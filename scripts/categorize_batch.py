"""
categorize_batch.py — Categorização em lote via Anthropic Message Batches API.

Modelo padrao: claude-opus-4-7 (override com --model claude-sonnet-4-6 ou claude-haiku-4-5).
Usa prompt caching nas partes estaveis (taxonomia + schemas + prompt).
Custo: ~50% off batch + ~90% off em prefixo cacheado.

Workflow:
    1. py categorize_batch.py prepare --tipos SOLUCAO_CONSULTA --orgao COSIT --output b1.jsonl
    2. py categorize_batch.py submit b1.jsonl
    3. py categorize_batch.py status <batch_id>     (poll ate 'ended')
    4. py categorize_batch.py apply <batch_id>

Saida:
    - b1.jsonl                  -> requests prontas pra batch
    - b1.jsonl.batch_id         -> id do batch submetido
    - b1.jsonl.results.jsonl    -> resultados brutos (apos apply)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterator

from anthropic import Anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request
from loguru import logger
from psycopg.rows import dict_row

from db import get_conn
from normalize import (
    normalizar_tributo, normalizar_tema_macro, normalizar_tema_especifico,
    normalizar_regime, normalizar_tipo_norma, normalizar_tipo_uso,
    normalizar_tipo_relacao, normalizar_natureza, normalizar_resultado,
    decompor_tributo_composto,
)

REFS = Path(__file__).resolve().parent.parent / "references"
TAXONOMIA = REFS / "taxonomia.json"
SCHEMAS_DIR = REFS / "schemas_metadata_tematico"
PROMPT_SC = REFS / "prompts" / "prompt_extrator_sc.md"
PROMPT_NORM = REFS / "prompts" / "prompt_extrator_normativos.md"

DEFAULT_MODEL = "claude-opus-4-7"
MAX_CONTENT_CHARS = 100_000  # truncar atos absurdamente longos (~30k tokens)

TIPOS_SC = {"SOLUCAO_CONSULTA", "SOLUCAO_DIVERGENCIA", "SOLUCAO_CONSULTA_INTERNA"}


# ---------------------------------------------------------------------------
# Carrega blocos cacheaveis do system prompt
# ---------------------------------------------------------------------------

def carregar_schemas_tematicos() -> dict:
    """Le todos os schemas em references/schemas_metadata_tematico/*.json"""
    out = {}
    for f in SCHEMAS_DIR.glob("*.json"):
        if f.stem.startswith("_"):
            continue
        out[f.stem] = json.loads(f.read_text(encoding="utf-8"))
    return out


def system_blocks_para(tipo_ato: str) -> list[dict]:
    """
    Monta system prompt em blocos cacheaveis (cache_control ephemeral).
    A ordem importa: tudo estavel ANTES do cache_control = cacheado.
    Conteudo variavel (ato_input) vai como user message DEPOIS.
    """
    if tipo_ato in TIPOS_SC:
        prompt_path = PROMPT_SC
    else:
        prompt_path = PROMPT_NORM

    prompt_text = prompt_path.read_text(encoding="utf-8")
    taxonomia_text = TAXONOMIA.read_text(encoding="utf-8")
    schemas_json = json.dumps(carregar_schemas_tematicos(), ensure_ascii=False, indent=2)

    return [
        # Bloco 1 — instrucoes do extrator
        {
            "type": "text",
            "text": prompt_text,
        },
        # Bloco 2 — taxonomia controlada (cacheada)
        {
            "type": "text",
            "text": f"<taxonomia>\n{taxonomia_text}\n</taxonomia>",
        },
        # Bloco 3 — schemas tematicos (cacheada — ULTIMO bloco com cache_control)
        {
            "type": "text",
            "text": f"<schemas_metadata_tematico>\n{schemas_json}\n</schemas_metadata_tematico>\n\n"
                    "REGRAS DE OUTPUT:\n"
                    "- Responda APENAS um JSON valido conforme o schema do prompt acima.\n"
                    "- NAO inclua texto antes ou depois do JSON.\n"
                    "- NAO use markdown code fences (```).\n"
                    "- Comece a resposta diretamente com '{' e termine com '}'.",
            "cache_control": {"type": "ephemeral"},
        },
    ]


# ---------------------------------------------------------------------------
# prepare — gera JSONL
# ---------------------------------------------------------------------------

def montar_user_content(ato: dict) -> str:
    """Monta o bloco <ato_input> com dados do ato."""
    content = ato["content"] or ""
    if len(content) > MAX_CONTENT_CHARS:
        content = content[:MAX_CONTENT_CHARS] + "\n\n[...TRUNCADO]"

    return (
        "<ato_input>\n"
        f"TIPO: {ato['tipo_ato']}\n"
        f"NUMERO: {ato['numero']}\n"
        f"ORGAO: {ato['orgao_emissor']}\n"
        f"PUBLICACAO: {ato['data_publicacao']}\n"
        f"EMENTA: {ato['ementa'] or '(sem ementa)'}\n"
        f"\nCONTEUDO COMPLETO:\n{content}\n"
        "</ato_input>"
    )


def cmd_prepare(args):
    where = ["a.analise_completa = FALSE", "a.content_disponivel = TRUE"]
    if getattr(args, "so_pdf", False):
        where.append("a.pdf_disponivel = TRUE")
    params: list[Any] = []
    if args.tipos:
        where.append("a.tipo_ato = ANY(%s)")
        params.append(args.tipos)
    if args.orgao:
        where.append("a.orgao_emissor = %s")
        params.append(args.orgao)
    if args.ano:
        where.append("EXTRACT(YEAR FROM a.data_publicacao) = %s")
        params.append(args.ano)

    sql = f"""
        SELECT a.id, a.tipo_ato, a.numero, a.orgao_emissor, a.data_publicacao::text,
               a.ementa, c.content
        FROM atos a
        JOIN ato_content c ON c.ato_id=a.id AND c.tipo_versao='vigente'
        WHERE {' AND '.join(where)}
        ORDER BY a.data_publicacao DESC
    """
    if args.limit:
        sql += f" LIMIT {args.limit}"

    output = Path(args.output)
    n = 0
    total_input_chars = 0
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            with output.open("w", encoding="utf-8") as f:
                for ato in cur:
                    sys_blocks = system_blocks_para(ato["tipo_ato"])
                    user_msg = montar_user_content(ato)
                    req = {
                        "custom_id": f"ato_{ato['id']}",
                        "params": {
                            "model": args.model,
                            "max_tokens": args.max_tokens,
                            "system": sys_blocks,
                            "messages": [{"role": "user", "content": user_msg}],
                        },
                    }
                    f.write(json.dumps(req, ensure_ascii=False) + "\n")
                    n += 1
                    total_input_chars += len(user_msg)

    logger.success(f"{n} requests gravados em {output}")
    logger.info(f"  variavel total: {total_input_chars:,} chars (~{total_input_chars//4:,} tokens)")
    logger.info(f"  cacheado por request: ~30k tokens (taxonomia + schemas + prompt)")
    logger.info(f"  proximo passo: py categorize_batch.py submit {output}")


# ---------------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------------

def cmd_submit(args):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("[erro] ANTHROPIC_API_KEY nao definido em .env")

    client = Anthropic()
    requests_list = []
    with open(args.jsonl, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            requests_list.append(Request(
                custom_id=d["custom_id"],
                params=MessageCreateParamsNonStreaming(**d["params"]),
            ))

    logger.info(f"Submetendo {len(requests_list)} requests...")
    batch = client.messages.batches.create(requests=requests_list)
    logger.success(f"batch_id: {batch.id}")
    logger.info(f"status: {batch.processing_status}")

    Path(f"{args.jsonl}.batch_id").write_text(batch.id)
    logger.info(f"  proximo passo: py categorize_batch.py status {batch.id}")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def cmd_status(args):
    client = Anthropic()
    batch = client.messages.batches.retrieve(args.batch_id)
    rc = batch.request_counts
    print(f"batch_id:     {batch.id}")
    print(f"status:       {batch.processing_status}")
    print(f"created_at:   {batch.created_at}")
    print(f"ended_at:     {batch.ended_at}")
    print(f"counts:")
    print(f"  processing:  {rc.processing}")
    print(f"  succeeded:   {rc.succeeded}")
    print(f"  errored:     {rc.errored}")
    print(f"  canceled:    {rc.canceled}")
    print(f"  expired:     {rc.expired}")
    if batch.processing_status == "ended":
        print(f"\n>> proximo passo: py categorize_batch.py apply {batch.id}")


# ---------------------------------------------------------------------------
# apply — processa resultados e aplica no banco
# ---------------------------------------------------------------------------

def parse_json_response(text: str) -> dict:
    """Extrai JSON do output do LLM."""
    text = text.strip()
    # Remove fences se vieram
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


def aplicar_resultado_no_banco(conn, ato_id: int, llm_output: dict):
    """Aplica matérias + tributos + dispositivos + cnaes + relacoes no banco.
    Aplica normalizacao canonica em TODOS os valores categoricos antes do INSERT."""
    metadata_ato = llm_output.get("ato_metadata", {})
    materias = llm_output.get("materias", [])
    relacoes = llm_output.get("relacoes_com_outros_atos", [])
    vigencia = llm_output.get("vigencia", {})

    with conn.cursor() as cur:
        # UPDATE atos com metadata + eficacia + vigencia
        meta_extra = {k: v for k, v in metadata_ato.items() if k != "eficacia" and v is not None}
        cur.execute(
            """
            UPDATE atos SET
              eficacia = COALESCE(%s, eficacia),
              vigencia_inicio = COALESCE(%s::date, vigencia_inicio),
              vigencia_fim = COALESCE(%s::date, vigencia_fim),
              metadata = metadata || %s::jsonb,
              analise_completa = TRUE,
              updated_at = now()
            WHERE id = %s
            """,
            (
                metadata_ato.get("eficacia"),
                vigencia.get("vigencia_inicio") if vigencia else None,
                vigencia.get("vigencia_fim") if vigencia else None,
                json.dumps(meta_extra) if meta_extra else "{}",
                ato_id,
            ),
        )

        # Idempotente: limpa materias antigas
        cur.execute("DELETE FROM ato_materia WHERE ato_id = %s", (ato_id,))

        for i, m in enumerate(materias, 1):
            tema_macro_raw = m.get("tema_macro", "__NOVO_TEMA")
            tema_macro = normalizar_tema_macro(tema_macro_raw) or "__NOVO_TEMA"
            tema_esp_raw = m.get("tema_especifico", f"{tema_macro}.OUTRO")
            tema_esp = normalizar_tema_especifico(tema_esp_raw, tema_macro) or f"{tema_macro}.OUTRO"
            natureza = normalizar_natureza(m.get("natureza")) or "orientacao"
            resultado = normalizar_resultado(m.get("resultado"))
            cur.execute(
                """
                INSERT INTO ato_materia (
                  ato_id, ordem, natureza,
                  tema_macro, tema_especifico, subtema, tags,
                  ementa_trecho, fato_consultado, solucao, fundamentacao_resumo,
                  metadata_tematico,
                  resultado, llm_model, llm_processed_at, schema_version,
                  tese_contribuinte, tese_fazenda, tese_adotada
                ) VALUES (
                  %s, %s, %s,
                  %s, %s, %s, %s,
                  %s, %s, %s, %s,
                  %s,
                  %s, %s, now(), 'v1',
                  %s, %s, %s
                )
                RETURNING id
                """,
                (
                    ato_id,
                    m.get("ordem", i),
                    natureza,
                    tema_macro, tema_esp, m.get("subtema"),
                    m.get("tags") or [],
                    m.get("ementa_trecho"),
                    m.get("fato_consultado"),
                    m.get("solucao"),
                    m.get("fundamentacao_resumo"),
                    json.dumps(m.get("metadata_tematico") or {}),
                    resultado,
                    "batch-api",
                    m.get("tese_contribuinte"),
                    m.get("tese_fazenda"),
                    m.get("tese_adotada"),
                ),
            )
            materia_id = cur.fetchone()[0]

            # Tributos: normaliza + decompoe compostos (PIS/COFINS -> 2 linhas)
            tributos_canonicos: set[tuple[str, str | None]] = set()
            for t in m.get("tributos") or []:
                if isinstance(t, str):
                    t = {"codigo": t}
                elif not isinstance(t, dict):
                    continue
                codigo_raw = t.get("codigo") or ""
                regime = normalizar_regime(t.get("regime"))
                # Tenta canonico direto; se nao, decompoe (PIS/COFINS -> [PIS, COFINS])
                codigo_canonico = normalizar_tributo(codigo_raw)
                if codigo_canonico:
                    tributos_canonicos.add((codigo_canonico, regime))
                else:
                    for c in decompor_tributo_composto(codigo_raw):
                        tributos_canonicos.add((c, regime))
            for codigo, regime in tributos_canonicos:
                cur.execute(
                    """
                    INSERT INTO materia_tributo (materia_id, tributo_codigo, regime)
                    VALUES (%s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (materia_id, codigo, regime),
                )

            # Dispositivos legais + fundamentacao externa (jurisprudencia/pareceres)
            for d in m.get("dispositivos") or []:
                if not isinstance(d, dict):
                    continue
                tipo_norma = normalizar_tipo_norma(d.get("tipo_norma")) or d.get("tipo_norma")
                tipo_uso = normalizar_tipo_uso(d.get("tipo_uso")) or "fundamento_principal"
                cur.execute(
                    """
                    INSERT INTO materia_dispositivo (materia_id, tipo_norma, referencia, dispositivo, texto_resumido, tipo_uso)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (materia_id, tipo_norma, d.get("referencia"),
                     d.get("dispositivo"), d.get("texto_resumido"), tipo_uso),
                )
            for f in m.get("fundamentacao_externa") or []:
                if not isinstance(f, dict):
                    continue
                tipo_norma = normalizar_tipo_norma(f.get("tipo_fonte")) or f.get("tipo_fonte")
                cur.execute(
                    """
                    INSERT INTO materia_dispositivo (materia_id, tipo_norma, referencia, dispositivo, texto_resumido, tipo_uso)
                    VALUES (%s, %s, %s, %s, %s, 'fundamento_externo')
                    """,
                    (materia_id, tipo_norma, f.get("referencia"),
                     None, f.get("texto_resumido")),
                )

            # CNAEs
            for c in m.get("cnaes_aplicaveis") or []:
                if isinstance(c, str):
                    # Haiku as vezes retorna lista de strings (nome do setor) ao inves de dict com codigo;
                    # sem codigo nao da pra inserir, pula.
                    continue
                if not isinstance(c, dict) or not c.get("codigo"):
                    continue
                cur.execute(
                    """
                    INSERT INTO materia_cnae (materia_id, cnae_codigo, descricao, relevancia, confianca)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (materia_id, c["codigo"], c.get("descricao"),
                     c.get("relevancia"), c.get("confianca")),
                )

        # Relacoes — resolve idAto destino para ato_id local
        for r in relacoes:
            destino = r.get("ato_destino") or {}
            tipo_dest = destino.get("tipo_ato")
            num_dest = str(destino.get("numero") or "")
            ano_dest = destino.get("ano")
            org_dest = destino.get("orgao") or destino.get("orgao_emissor") or "COSIT"
            if not (tipo_dest and num_dest and ano_dest):
                continue
            cur.execute(
                """
                SELECT id FROM atos
                WHERE tipo_ato = %s AND numero = %s AND orgao_emissor = %s
                  AND EXTRACT(YEAR FROM data_publicacao) = %s
                LIMIT 1
                """,
                (tipo_dest, num_dest, org_dest, ano_dest),
            )
            row = cur.fetchone()
            if not row:
                continue
            tipo_rel = normalizar_tipo_relacao(r.get("tipo_relacao")) or r.get("tipo_relacao")
            cur.execute(
                """
                INSERT INTO ato_relacao (ato_origem_id, ato_destino_id, tipo_relacao, observacao, fonte, parcial)
                VALUES (%s, %s, %s, %s, 'llm-batch', %s)
                ON CONFLICT (ato_origem_id, ato_destino_id, tipo_relacao) DO UPDATE SET observacao = EXCLUDED.observacao
                """,
                (ato_id, row[0], tipo_rel,
                 r.get("observacao"), r.get("parcial", False)),
            )


def cmd_apply(args):
    client = Anthropic()
    out_path = Path(args.results_out or f"results_{args.batch_id}.jsonl")

    ok, fail, json_err, db_err = 0, 0, 0, 0
    with get_conn() as conn:
        with out_path.open("w", encoding="utf-8") as out_f:
            for result in client.messages.batches.results(args.batch_id):
                custom_id = result.custom_id
                ato_id = int(custom_id.replace("ato_", ""))
                # Salva raw
                out_f.write(json.dumps({
                    "custom_id": custom_id,
                    "type": result.result.type,
                }, ensure_ascii=False) + "\n")

                if result.result.type != "succeeded":
                    fail += 1
                    logger.warning(f"{custom_id}: {result.result.type}")
                    continue

                msg = result.result.message
                # extrai texto
                text = ""
                for block in msg.content:
                    if block.type == "text":
                        text += block.text

                try:
                    output = parse_json_response(text)
                except json.JSONDecodeError as e:
                    json_err += 1
                    logger.error(f"{custom_id}: JSON invalido: {e}")
                    continue

                try:
                    aplicar_resultado_no_banco(conn, ato_id, output)
                    conn.commit()
                    ok += 1
                except Exception as e:
                    db_err += 1
                    logger.error(f"{custom_id}: erro DB: {e}")
                    conn.rollback()
                    continue

    logger.success(f"FIM: {ok} ok | {fail} batch failed | {json_err} json invalido | {db_err} erro db")
    logger.info(f"raw: {out_path}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prepare", help="Gera JSONL de requests pra batch")
    p.add_argument("--output", required=True, help="caminho do .jsonl de saida")
    p.add_argument("--tipos", nargs="+", help="ex: SOLUCAO_CONSULTA SOLUCAO_DIVERGENCIA")
    p.add_argument("--orgao", help="ex: COSIT")
    p.add_argument("--ano", type=int)
    p.add_argument("--limit", type=int)
    p.add_argument("--model", default=DEFAULT_MODEL,
                   help=f"default {DEFAULT_MODEL}; opcoes: claude-sonnet-4-6, claude-haiku-4-5")
    p.add_argument("--so-pdf", dest="so_pdf", action="store_true",
                   help="So atos com pdf_disponivel=TRUE (ignora os que so tem HTML do portal)")
    p.add_argument("--max-tokens", dest="max_tokens", type=int, default=8000,
                   help="max_tokens por request (default 8000; 16000 para INs/Decretos longos)")

    p = sub.add_parser("submit", help="Submete batch JSONL")
    p.add_argument("jsonl")

    p = sub.add_parser("status", help="Checa status de um batch")
    p.add_argument("batch_id")

    p = sub.add_parser("apply", help="Busca resultados e aplica no banco")
    p.add_argument("batch_id")
    p.add_argument("--results-out", help="caminho pra salvar raw results (.jsonl)")

    args = parser.parse_args()
    {"prepare": cmd_prepare, "submit": cmd_submit,
     "status": cmd_status, "apply": cmd_apply}[args.cmd](args)


if __name__ == "__main__":
    main()
