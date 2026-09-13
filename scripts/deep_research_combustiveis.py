"""deep_research_combustiveis.py — Deep research SOMENTE com a base td-rfb-atos.

Para cada tema:
  1. Roda queries semanticas no retrieve.py
  2. Pega contexto consolidado (materias + dispositivos + relacoes + ementa) dos top atos
  3. Claude Opus 4.7 (adaptive thinking) sintetiza COM base APENAS nesse material
  4. Salva markdown em outputs/.../deep-research/

Sem web_search. Sem fontes externas. Apenas base RFB.
"""
from __future__ import annotations
from dotenv import load_dotenv; load_dotenv()
import json
import os
from pathlib import Path
import google.generativeai as genai
from anthropic import Anthropic
from psycopg.rows import dict_row
from db import get_conn

OUT = Path(__file__).resolve().parent.parent / "outputs" / "wip" / "combustiveis-alcool-creditos" / "v1" / "deep-research"
MODEL = "claude-opus-4-7"
EMBED_MODEL = "models/gemini-embedding-001"

genai.configure(api_key=os.environ["GOOGLE_API_KEY"])


def embed_query(q: str) -> list[float]:
    return genai.embed_content(
        model=EMBED_MODEL, content=q,
        task_type="RETRIEVAL_QUERY", output_dimensionality=1024,
    )["embedding"]


# ---------------------------------------------------------------------------
# Recuperacao da base
# ---------------------------------------------------------------------------

def buscar_atos_relevantes(queries: list[str], filtro_tipos: list[str] | None = None,
                           filtro_tributos: list[str] | None = None,
                           top_per_query: int = 12) -> list[int]:
    """Roda varias queries semanticas e retorna ato_ids unicos rankeados por max_score."""
    where = ["m.embedding IS NOT NULL"]
    params: list = []
    if filtro_tipos:
        where.append("a.tipo_ato = ANY(%s)")
        params.append(filtro_tipos)
    if filtro_tributos:
        where.append("EXISTS (SELECT 1 FROM materia_tributo mt WHERE mt.materia_id = m.id AND mt.tributo_codigo = ANY(%s))")
        params.append(filtro_tributos)
    where_sql = " AND ".join(where)

    scores: dict[int, float] = {}
    with get_conn() as conn:
        with conn.cursor() as cur:
            for q in queries:
                qvec = embed_query(q)
                cur.execute(f"""
                    SELECT a.id, 1.0 - (m.embedding <=> %s::vector) AS sim
                    FROM ato_materia m
                    JOIN atos a ON a.id = m.ato_id
                    WHERE {where_sql}
                    ORDER BY m.embedding <=> %s::vector
                    LIMIT %s
                """, [qvec, *params, qvec, top_per_query])
                for ato_id, sim in cur.fetchall():
                    if sim > scores.get(ato_id, 0):
                        scores[ato_id] = float(sim)
    return sorted(scores.keys(), key=lambda k: -scores[k])


def consolidar_ato(conn, ato_id: int) -> dict:
    """Retorna ato + todas as materias + dispositivos + relacoes + status_vigencia."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT id, tipo_ato, numero, orgao_emissor, data_publicacao::text,
                   ementa, eficacia, status_vigencia,
                   vigencia_inicio::text, vigencia_fim::text
            FROM atos WHERE id = %s
        """, [ato_id])
        ato = cur.fetchone()
        if not ato:
            return {}

        cur.execute("""
            SELECT id, tema_macro, tema_especifico, subtema,
                   solucao, fato_consultado, tese_adotada,
                   fundamentacao_resumo, ementa_trecho, resultado
            FROM ato_materia WHERE ato_id = %s
            ORDER BY ordem
        """, [ato_id])
        materias = [dict(r) for r in cur.fetchall()]

        if materias:
            mids = [m["id"] for m in materias]
            cur.execute("""
                SELECT materia_id, tributo_codigo, regime
                FROM materia_tributo WHERE materia_id = ANY(%s)
            """, [mids])
            trib = {}
            for r in cur.fetchall():
                regime_part = f" ({r['regime']})" if r["regime"] else ""
                trib.setdefault(r["materia_id"], []).append(f"{r['tributo_codigo']}{regime_part}")
            for m in materias:
                m["tributos"] = trib.get(m["id"], [])

            cur.execute("""
                SELECT materia_id, tipo_norma, referencia, dispositivo, tipo_uso
                FROM materia_dispositivo WHERE materia_id = ANY(%s)
                ORDER BY tipo_uso
            """, [mids])
            disp = {}
            for r in cur.fetchall():
                disp.setdefault(r["materia_id"], []).append(dict(r))
            for m in materias:
                m["dispositivos"] = disp.get(m["id"], [])

        cur.execute("""
            SELECT r.tipo_relacao, r.observacao, r.parcial,
                   ad.tipo_ato AS dest_tipo, ad.numero AS dest_numero,
                   ad.orgao_emissor AS dest_orgao
            FROM ato_relacao r
            LEFT JOIN atos ad ON ad.id = r.ato_destino_id
            WHERE r.ato_origem_id = %s
            LIMIT 8
        """, [ato_id])
        ato["relacoes"] = [dict(r) for r in cur.fetchall()]
        ato["materias"] = materias
        return dict(ato)


def renderizar_contexto(ato: dict, max_chars: int = 3000) -> str:
    if not ato:
        return ""
    sigla = {"SOLUCAO_CONSULTA": "SC", "SOLUCAO_DIVERGENCIA": "SD",
             "SOLUCAO_CONSULTA_INTERNA": "SCI"}.get(ato["tipo_ato"], ato["tipo_ato"])
    lines = [f"## {sigla} {ato['numero']}/{ato['data_publicacao'][:4]} ({ato['orgao_emissor']}) — id={ato['id']}"]
    lines.append(f"*eficacia:* {ato['eficacia']} · *vigencia:* {ato['status_vigencia']}")
    if ato["ementa"]:
        lines.append(f"\n**Ementa:** {ato['ementa'][:500].replace(chr(10), ' ')}")
    for m in ato.get("materias", [])[:6]:
        lines.append(f"\n### Materia: {m['tema_macro']} / {m['tema_especifico']}")
        if m["tributos"]:
            lines.append(f"*Tributos:* {', '.join(m['tributos'])}")
        if m.get("fato_consultado"):
            lines.append(f"**Fato:** {m['fato_consultado'][:400]}")
        if m.get("solucao"):
            lines.append(f"**Solucao:** {m['solucao'][:600]}")
        if m.get("tese_adotada"):
            lines.append(f"**Tese:** {m['tese_adotada'][:300]}")
        if m.get("fundamentacao_resumo"):
            lines.append(f"**Fundamentacao:** {m['fundamentacao_resumo'][:300]}")
        if m.get("dispositivos"):
            ds = "; ".join(f"{d['tipo_norma']} {d['referencia']}" + (f" {d['dispositivo']}" if d.get("dispositivo") else "")
                            for d in m["dispositivos"][:6])
            lines.append(f"**Dispositivos:** {ds}")
    if ato.get("relacoes"):
        rs = "; ".join(f"{r['tipo_relacao']} {r['dest_tipo']} {r['dest_numero']}/{r['dest_orgao']}"
                        for r in ato["relacoes"] if r.get("dest_tipo"))
        if rs:
            lines.append(f"\n**Relacoes:** {rs}")
    full = "\n".join(lines)
    return full[:max_chars]


# ---------------------------------------------------------------------------
# Temas
# ---------------------------------------------------------------------------

TEMAS = {
    "01-conceito-insumo-combustivel": {
        "titulo": "Conceito de insumo aplicado a combustíveis nas SCs/SDs da RFB",
        "queries": [
            "combustivel insumo essencialidade relevancia processo produtivo",
            "combustivel veiculo prestacao servico atividade fim",
            "oleo diesel atividade produtiva industrial credito PIS COFINS",
            "lubrificante manutencao maquina equipamento credito",
            "frete combustivel veiculo proprio empresa",
        ],
        "filtros": {"tipos": ["SOLUCAO_CONSULTA","SOLUCAO_DIVERGENCIA","SOLUCAO_CONSULTA_INTERNA"], "tributos": ["PIS","COFINS"]},
        "instrucao": "Sintetize a posicao da RFB sobre quando combustivel e insumo (gera credito) e quando nao e (nega credito), agrupando por hipotese de uso. Cite SC/SD por numero/ano/orgao. Diga se ha SD que pacificou divergencia.",
    },
    "02-monofasico-vedacao-credito": {
        "titulo": "Regime monofásico, vedação a créditos e exceções",
        "queries": [
            "monofasico aliquota concentrada credito vedado revenda",
            "produtor combustivel etanol aliquota especifica monofasica",
            "distribuidor atacadista combustivel insumo credito vedado",
            "manutencao credito saida nao tributada zero",
            "credito presumido produtor etanol cana",
        ],
        "filtros": {"tipos": ["SOLUCAO_CONSULTA","SOLUCAO_DIVERGENCIA","SOLUCAO_CONSULTA_INTERNA"], "tributos": ["PIS","COFINS"]},
        "instrucao": "Explique o regime monofasico para combustiveis e alcool com base nas SCs/SDs. Liste vedacoes pacificadas e excecoes. Identifique se a base traz Lei 11.033/2004 art.17, Lei 12.859/2013, LC 192/2022. Cite atos por numero.",
    },
    "03-lc-192-2022-oleo-diesel": {
        "titulo": "LC 192/2022 — vedação a créditos sobre óleo diesel para revenda",
        "queries": [
            "LC 192 2022 oleo diesel credito vedado revenda",
            "lei complementar 192 art 9 PIS COFINS combustivel",
            "manutencao credito anterior aquisicao oleo diesel",
            "estoque credito antes alteracao legal combustivel",
        ],
        "filtros": {"tipos": ["SOLUCAO_CONSULTA","SOLUCAO_DIVERGENCIA","SOLUCAO_CONSULTA_INTERNA","INSTRUCAO_NORMATIVA","ATO_DECLARATORIO_INTERPRETATIVO","DECRETO"]},
        "instrucao": "Descreva a posicao oficial da RFB sobre a vedacao introduzida pela LC 192/2022 com base nos atos da base. Quem foi atingido, quando vigorou, qual a interpretacao da RFB sobre manutencao de creditos anteriores. Cite atos por numero.",
    },
    "04-alcool-industrializacao": {
        "titulo": "Álcool/etanol como matéria-prima industrial (não combustível)",
        "queries": [
            "etanol alcool insumo industrial materia prima quimica",
            "alcool fabricacao bebida cosmetico farmaceutico credito",
            "produtor etanol insumo aquisicao bem credito percentual geral",
            "alcool desnaturado uso industrial nao combustivel tributacao",
            "credito presumido cana acucar etanol producao",
        ],
        "filtros": {"tributos": ["PIS","COFINS"]},
        "instrucao": "Explique como a RFB diferencia alcool/etanol usado como combustivel (monofasico) vs como materia-prima industrial (regime geral) com base nas SCs/SDs e INs. Cite atos por numero. Identifique riscos de glosa para o industrial que adquire etanol como insumo.",
    },
    "05-frete-combustivel-veiculo": {
        "titulo": "Combustível em veículos, frete e logística — quando há crédito",
        "queries": [
            "combustivel veiculo entrega mercadoria atacadista varejo",
            "frota propria deslocamento funcionario prestacao servico",
            "transporte materia prima entre estabelecimentos credito",
            "frete na aquisicao frete na revenda PIS COFINS",
            "transporte cargas terceiros TAC contratacao credito",
        ],
        "filtros": {"tipos": ["SOLUCAO_CONSULTA","SOLUCAO_DIVERGENCIA","SOLUCAO_CONSULTA_INTERNA"], "tributos": ["PIS","COFINS"]},
        "instrucao": "Sintetize a posicao da RFB sobre crédito de combustivel em veículos por hipotese: (a) veiculo industrial, (b) frota de servico, (c) entrega de mercadoria propria, (d) deslocamento de funcionarios, (e) transporte de materia-prima. Cite SCs por numero. Identifique a SD COSIT que pacifica e SCs revisitadas.",
    },
    "06-oportunidades-tese": {
        "titulo": "Oportunidades de tese — onde a RFB nega mas há fundamento no Tema 779",
        "queries": [
            "essencialidade relevancia tema 779 STJ insumo recurso especial",
            "atividade fim processo produtivo insumo conceito amplo",
            "judicial decisao TRF combustivel credito insumo essencial",
            "glosa fiscal credito combustivel autuacao revisao",
        ],
        "filtros": {"tributos": ["PIS","COFINS"]},
        "instrucao": "A partir das SCs/SDs/atos da base que NEGAM credito, identifique 3-5 hipoteses onde haveria fundamento para tese contra a posicao da RFB (com base no Tema 779 STJ ou ja superada por SC mais recente). Cite atos por numero e diga onde esta a divergencia.",
    },
}

PROMPT_BASE = """Voce e um analista tributario senior especializado em PIS/COFINS no regime nao cumulativo.

Sua tarefa e produzir um relatorio profundo SOMENTE com base nos atos abaixo extraidos da base td-rfb-atos da Tributo Devido (Solucoes de Consulta, Solucoes de Divergencia, Instrucoes Normativas, Decretos da Receita Federal).

REGRAS:
- NAO use conhecimento externo nao mencionado nos atos abaixo.
- NAO invente numeros de processo, decisoes do CARF ou STJ que nao estejam citados nos campos `dispositivos` ou `fundamentacao` das materias.
- Se algo nao estiver na base, diga "a base nao traz informacao sobre X".
- Cite SEMPRE pelo numero do ato (ex.: "SC COSIT 137/2025"), incluindo o orgao emissor.
- Estruture o relatorio em markdown com secoes claras.
- Seja factual e tecnico.

# Tema do relatorio
{titulo}

# Instrucao especifica
{instrucao}

# Atos da base (top relevantes para este tema)

{contexto}
"""


def pesquisar_tema(client: Anthropic, tema_id: str, config: dict) -> str:
    print(f"[start] {tema_id}: {config['titulo']}")
    ato_ids = buscar_atos_relevantes(
        config["queries"],
        filtro_tipos=config["filtros"].get("tipos"),
        filtro_tributos=config["filtros"].get("tributos"),
        top_per_query=10,
    )[:35]  # max 35 atos
    print(f"   {len(ato_ids)} atos relevantes")

    contextos = []
    with get_conn() as conn:
        for aid in ato_ids:
            ato = consolidar_ato(conn, aid)
            ctx = renderizar_contexto(ato, max_chars=2500)
            if ctx:
                contextos.append(ctx)

    contexto = "\n\n---\n\n".join(contextos)
    print(f"   {len(contexto):,} chars de contexto")

    prompt = PROMPT_BASE.format(titulo=config["titulo"], instrucao=config["instrucao"], contexto=contexto)

    response = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": prompt}],
    )
    text = "\n\n".join(b.text for b in response.content if b.type == "text")
    header = f"# {config['titulo']}\n\n*Modelo: {MODEL} (adaptive thinking) — base td-rfb-atos · {len(ato_ids)} atos analisados*\n\n---\n\n"
    return header + text


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    client = Anthropic()
    for tema_id, config in TEMAS.items():
        out_path = OUT / f"{tema_id}.md"
        if out_path.exists():
            print(f"[skip] {tema_id} ja existe")
            continue
        try:
            text = pesquisar_tema(client, tema_id, config)
            out_path.write_text(text, encoding="utf-8")
            print(f"[ok]   {tema_id} ({len(text):,} chars)")
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f"[ERR]  {tema_id}: {e}")


if __name__ == "__main__":
    main()
