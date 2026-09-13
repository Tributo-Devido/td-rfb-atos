"""
categorize_with_llm.py — Categorização estruturada via Claude Haiku 4.5.

Para cada ato sem analise_completa, envia ao LLM com:
  - Prompt apropriado (SC/SD vs Normativos vs CARF)
  - Taxonomia controlada (taxonomia.json — apenas seções relevantes)
  - Schema metadata_tematico se o tema_especifico for um dos prioritários

Output JSON é validado e desmembrado em ato_materia + materia_tributo + materia_dispositivo
+ materia_cnae + ato_relacao.

Uso:
    py categorize_with_llm.py                # processa todos pendentes
    py categorize_with_llm.py --limit 10
    py categorize_with_llm.py --tipo SOLUCAO_CONSULTA
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from anthropic import Anthropic
from loguru import logger
from psycopg.rows import dict_row
from tqdm import tqdm

from db import get_conn

REFERENCES_DIR = Path(__file__).resolve().parent.parent / "references"
TAXONOMIA_PATH = REFERENCES_DIR / "taxonomia.json"
SCHEMAS_DIR = REFERENCES_DIR / "schemas_metadata_tematico"
PROMPTS_DIR = REFERENCES_DIR / "prompts"

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 8192

TIPOS_SC = {"SOLUCAO_CONSULTA", "SOLUCAO_DIVERGENCIA", "SOLUCAO_CONSULTA_INTERNA"}
TIPOS_NORMATIVO = {
    "INSTRUCAO_NORMATIVA", "INSTRUCAO_NORMATIVA_CONJUNTA",
    "DECRETO", "PORTARIA", "PORTARIA_NORMATIVA", "PORTARIA_CONJUNTA", "PORTARIA_INTERMINISTERIAL",
    "ATO_DECLARATORIO_INTERPRETATIVO", "ATO_DECLARATORIO_NORMATIVO",
    "PARECER_NORMATIVO", "RESOLUCAO", "ORIENTACAO_NORMATIVA",
    "NORMA_EXECUCAO", "NORMA_EXECUCAO_CONJUNTA",
    "ORDEM_SERVICO", "ORDEM_SERVICO_CONJUNTA",
}


def carregar_taxonomia() -> dict:
    return json.loads(TAXONOMIA_PATH.read_text(encoding="utf-8"))


def carregar_prompt_sistema(tipo_ato: str) -> str:
    if tipo_ato in TIPOS_SC:
        path = PROMPTS_DIR / "prompt_extrator_sc.md"
    elif tipo_ato in TIPOS_NORMATIVO:
        path = PROMPTS_DIR / "prompt_extrator_normativos.md"
    else:
        # Fallback: SC (genérico bom o suficiente)
        path = PROMPTS_DIR / "prompt_extrator_sc.md"
    return path.read_text(encoding="utf-8")


def carregar_schema_tematico(tema_especifico: str) -> dict | None:
    p = SCHEMAS_DIR / f"{tema_especifico}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def montar_user_message(ato: dict, content: str | None) -> str:
    """Monta o bloco <ato_input>."""
    parts = [
        f"TIPO: {ato['tipo_ato']}",
        f"NÚMERO: {ato['numero']}",
        f"ÓRGÃO: {ato['orgao_emissor']}",
        f"PUBLICAÇÃO: {ato['data_publicacao']}",
        f"EMENTA: {ato['ementa'] or '(sem ementa)'}",
    ]
    if content:
        parts.append(f"\nCONTEÚDO COMPLETO:\n{content}")
    else:
        parts.append("\nCONTEÚDO COMPLETO: (não disponível — categorize com base apenas na ementa)")
    return "<ato_input>\n" + "\n".join(parts) + "\n</ato_input>"


def chamar_haiku(client: Anthropic, system: str, user: str) -> dict:
    msg = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        temperature=0,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = msg.content[0].text if msg.content else "{}"
    # Extrair JSON do texto (modelo às vezes envolve em ```json)
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


def persistir_resultado(conn, ato_id: int, llm_output: dict):
    """Insere matérias + relações + tributos + dispositivos + cnaes."""
    metadata_ato = llm_output.get("ato_metadata", {})
    materias = llm_output.get("materias", [])
    relacoes = llm_output.get("relacoes_com_outros_atos", [])
    vigencia = llm_output.get("vigencia", {})

    with conn.cursor() as cur:
        # Atualiza ato com metadata + eficácia + vigência
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
                vigencia.get("vigencia_inicio"),
                vigencia.get("vigencia_fim"),
                json.dumps({k: v for k, v in metadata_ato.items() if k not in ("eficacia",)}),
                ato_id,
            ),
        )

        # Limpa matérias antigas (idempotente reprocessing)
        cur.execute("DELETE FROM ato_materia WHERE ato_id = %s", (ato_id,))

        # Insere matérias
        for m in materias:
            cur.execute(
                """
                INSERT INTO ato_materia (
                  ato_id, ordem, natureza,
                  tema_macro, tema_especifico, subtema, tags,
                  ementa_trecho, fato_consultado, solucao, fundamentacao_resumo,
                  metadata_tematico,
                  resultado, llm_model, llm_processed_at, schema_version
                )
                VALUES (
                  %s, %s, %s,
                  %s, %s, %s, %s,
                  %s, %s, %s, %s,
                  %s,
                  %s, %s, now(), 'v1'
                )
                RETURNING id
                """,
                (
                    ato_id,
                    m.get("ordem", 1),
                    m.get("natureza", "orientacao"),
                    m["tema_macro"],
                    m["tema_especifico"],
                    m.get("subtema"),
                    m.get("tags", []),
                    m.get("ementa_trecho"),
                    m.get("fato_consultado"),
                    m.get("solucao"),
                    m.get("fundamentacao_resumo"),
                    json.dumps(m.get("metadata_tematico", {})),
                    m.get("resultado"),
                    MODEL,
                ),
            )
            materia_id = cur.fetchone()[0]

            # Tributos
            for t in m.get("tributos", []):
                cur.execute(
                    """
                    INSERT INTO materia_tributo (materia_id, tributo_codigo, regime)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (materia_id, tributo_codigo) DO UPDATE SET regime = EXCLUDED.regime
                    """,
                    (materia_id, t["codigo"], t.get("regime")),
                )

            # Dispositivos
            for d in m.get("dispositivos", []):
                cur.execute(
                    """
                    INSERT INTO materia_dispositivo (materia_id, tipo_norma, referencia, dispositivo, texto_resumido, tipo_uso)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        materia_id, d.get("tipo_norma"), d.get("referencia"),
                        d.get("dispositivo"), d.get("texto_resumido"),
                        d.get("tipo_uso", "fundamento"),
                    ),
                )

            # CNAEs
            for c in m.get("cnaes_aplicaveis", []):
                cur.execute(
                    """
                    INSERT INTO materia_cnae (materia_id, cnae_codigo, descricao, relevancia, confianca)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (materia_id, cnae_codigo) DO NOTHING
                    """,
                    (materia_id, c["codigo"], c.get("descricao"), c.get("relevancia"), c.get("confianca")),
                )

        # Relações (matchear por chave natural — ato destino pode não existir ainda; loga se não achar)
        for r in relacoes:
            destino = r.get("ato_destino", {})
            cur.execute(
                """
                SELECT id FROM atos
                WHERE tipo_ato = %s AND numero = %s AND orgao_emissor = %s
                """,
                (
                    destino.get("tipo_ato"),
                    str(destino.get("numero")),
                    destino.get("orgao", destino.get("orgao_emissor", "COSIT")),
                ),
            )
            row = cur.fetchone()
            if not row:
                # Não achado: registra como pending (próxima rodada pode resolver)
                logger.debug(f"relacao destino nao encontrada: {destino}")
                continue
            destino_id = row[0]
            cur.execute(
                """
                INSERT INTO ato_relacao (ato_origem_id, ato_destino_id, tipo_relacao, parcial, observacao, fonte)
                VALUES (%s, %s, %s, %s, %s, 'llm')
                ON CONFLICT (ato_origem_id, ato_destino_id, tipo_relacao) DO NOTHING
                """,
                (
                    ato_id, destino_id, r["tipo_relacao"],
                    r.get("parcial", False), r.get("observacao"),
                ),
            )


def processar(limit: int | None = None, tipo_filter: str | None = None):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("[erro] ANTHROPIC_API_KEY não definido em .env")

    client = Anthropic()

    where = "a.analise_completa = FALSE"
    params: list[Any] = []
    if tipo_filter:
        where += " AND a.tipo_ato = %s"
        params.append(tipo_filter)

    sql = f"""
        SELECT a.id, a.tipo_ato, a.numero, a.orgao_emissor, a.data_publicacao::text,
               a.ementa, c.content
        FROM atos a
        LEFT JOIN ato_content c ON c.ato_id = a.id
        WHERE {where}
        ORDER BY a.data_publicacao DESC
    """
    if limit:
        sql += f" LIMIT {limit}"

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            atos = cur.fetchall()

        if not atos:
            logger.info("Nenhum ato pendente de categorizacao.")
            return

        logger.info(f"Categorizando {len(atos)} atos via {MODEL}...")

        for ato in tqdm(atos, desc="LLM"):
            try:
                system_prompt = carregar_prompt_sistema(ato["tipo_ato"])
                user_msg = montar_user_message(ato, ato["content"])

                output = chamar_haiku(client, system_prompt, user_msg)
                persistir_resultado(conn, ato["id"], output)
                conn.commit()

            except json.JSONDecodeError as e:
                logger.error(f"JSON invalido para ato_id={ato['id']}: {e}")
                conn.rollback()
                continue
            except Exception as e:
                logger.error(f"erro ato_id={ato['id']}: {e}")
                conn.rollback()
                continue

    logger.success("FIM categorize_with_llm.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    parser.add_argument("--tipo", help="Filtra por tipo_ato (ex: SOLUCAO_CONSULTA)")
    args = parser.parse_args()
    processar(limit=args.limit, tipo_filter=args.tipo)
