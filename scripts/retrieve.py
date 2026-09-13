"""
retrieve.py — Retrieval hibrido (FTS + vetor + filtros) com ranking por natureza funcional.

Modos (--modo):
  pratico (default) — "como funciona / como e a regra atual" — prioriza atos normativos
  consultivo        — "como a RFB respondeu casos parecidos" — prioriza SCs e SDs
  operacional       — atos administrativos pontuais (habilitacao, exclusao, nomeacao)
  historico         — estudo retrospectivo (use com --vigente-em <data>)

Pesos configuraveis em references/ranking_weights.json.
Ver references/hierarquia_funcional.md.

Comandos:
    py retrieve.py search "exclusao do ICMS PIS COFINS" --modo pratico --tributos PIS COFINS --limit 10
    py retrieve.py search "tributacao SaaS" --modo consultivo
    py retrieve.py search "PIS nao cumulativo" --modo historico --vigente-em 2018-06-01
    py retrieve.py facetas
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path

from loguru import logger
from psycopg.rows import dict_row

from db import get_conn

EMBED_MODEL = "models/gemini-embedding-001"
EMBED_DIMS = 1024

WEIGHTS_PATH = Path(__file__).resolve().parent.parent / "references" / "ranking_weights.json"
WEIGHTS = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))


def embed_query(q: str) -> list[float]:
    import google.generativeai as genai
    if os.environ.get("GOOGLE_API_KEY"):
        genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
    result = genai.embed_content(
        model=EMBED_MODEL, content=q,
        task_type="RETRIEVAL_QUERY", output_dimensionality=EMBED_DIMS,
    )
    return result["embedding"]


def _peso_secundario(natureza: str | None, tipo_ato: str | None, orgao: str | None) -> float:
    """Boost secundario dentro da natureza (SD > SC Cosit > SC Disit; IN > ADI; etc.)."""
    boost = WEIGHTS.get("boost_secundario", {}).get(natureza or "", {})
    if not boost:
        return 1.0
    val = boost.get(tipo_ato or "")
    if isinstance(val, dict):
        return float(val.get(orgao or "", val.get("_default", 1.0)))
    if isinstance(val, (int, float)):
        return float(val)
    return 1.0


def _peso_status(status: str | None) -> float:
    pesos = WEIGHTS.get("peso_status_vigencia", {})
    return float(pesos.get(status or "", pesos.get("_default", 1.0)))


def _fator_recencia(data_pub) -> float:
    """Decay exponencial com meia-vida configuravel (default 15 anos)."""
    if not data_pub:
        return 1.0
    if isinstance(data_pub, str):
        try:
            data_pub = date.fromisoformat(data_pub[:10])
        except Exception:
            return 1.0
    anos = (date.today() - data_pub).days / 365.25
    meia_vida = float(WEIGHTS.get("decay_recencia_anos_meia_vida", 15))
    return 0.5 ** (anos / meia_vida) if meia_vida > 0 else 1.0


def buscar(query: str, modo: str = "pratico",
           tributos: list[str] | None = None, temas: list[str] | None = None,
           tipos_ato: list[str] | None = None, eficacia: list[str] | None = None,
           data_ini: str | None = None, data_fim: str | None = None,
           vigente_em: str | None = None,
           vinculante_em: str | None = None,
           limit: int = 15, formato: str = "md"):
    """
    Busca hibrida: BM25 (tsvector_pt) + cosine (embedding) com Reciprocal Rank Fusion,
    re-ranqueada por peso de natureza funcional + boost secundario + recencia + boost de marco.
    """
    if modo not in WEIGHTS["modos"]:
        raise ValueError(f"modo invalido: {modo}. Use: {list(WEIGHTS['modos'].keys())}")

    pesos_natureza = WEIGHTS["modos"][modo]["natureza"]
    rrf_k = int(WEIGHTS.get("rrf_k", 60))

    # Modo historico recomenda --vigente-em
    if modo == "historico" and not vigente_em:
        logger.warning("modo=historico recomendado com --vigente-em <data> para reconstrucao temporal")

    # Filtro temporal: vigente_em vira data_fim teto (so atos publicados ate a data)
    if vigente_em and not data_fim:
        data_fim = vigente_em

    qvec = embed_query(query)

    where = ["1=1"]
    params: list = []
    if tributos:
        where.append("EXISTS (SELECT 1 FROM materia_tributo mt WHERE mt.materia_id = m.id AND mt.tributo_codigo = ANY(%s))")
        params.append(tributos)
    if temas:
        where.append("m.tema_macro = ANY(%s)")
        params.append(temas)
    if tipos_ato:
        where.append("a.tipo_ato = ANY(%s)")
        params.append(tipos_ato)
    if eficacia:
        where.append("a.eficacia = ANY(%s)")
        params.append(eficacia)
    if data_ini:
        where.append("a.data_publicacao >= %s")
        params.append(data_ini)
    if data_fim:
        where.append("a.data_publicacao <= %s")
        params.append(data_fim)
    if vinculante_em:
        # Janela de 60 meses retroativa: SC publicada nos 60 meses anteriores a <data>
        # ainda esta vinculante para fato gerador na <data> (prescricao tributaria).
        # Filtra so atos com eficacia vinculante (Cosit/Disit/Diana/Coana).
        where.append("a.data_publicacao <= %s")
        params.append(vinculante_em)
        where.append("a.data_publicacao >= (%s::date - INTERVAL '60 months')")
        params.append(vinculante_em)
        where.append("(a.eficacia IS NULL OR a.eficacia LIKE 'vinculante%%')")

    where_sql = " AND ".join(where)

    pool = max(limit * 6, 80)

    # Concatena campos textuais da materia para FTS on-the-fly
    materia_text_expr = (
        "COALESCE(m.ementa_trecho,'') || ' ' || COALESCE(m.solucao,'') || ' ' || "
        "COALESCE(m.fato_consultado,'') || ' ' || COALESCE(m.fundamentacao_resumo,'') || ' ' || "
        "COALESCE(m.tese_adotada,'')"
    )

    sql = f"""
    WITH fts AS (
      SELECT m.id AS materia_id, m.ato_id,
             ROW_NUMBER() OVER (
               ORDER BY ts_rank(to_tsvector('portuguese', {materia_text_expr}), plainto_tsquery('portuguese', %s)) DESC
             ) AS pos
      FROM ato_materia m
      JOIN atos a ON a.id = m.ato_id
      WHERE to_tsvector('portuguese', {materia_text_expr}) @@ plainto_tsquery('portuguese', %s)
        AND {where_sql}
      LIMIT {pool}
    ),
    vec AS (
      SELECT m.id AS materia_id, m.ato_id,
             ROW_NUMBER() OVER (ORDER BY m.embedding <=> %s::vector) AS pos
      FROM ato_materia m
      JOIN atos a ON a.id = m.ato_id
      WHERE m.embedding IS NOT NULL AND {where_sql}
      ORDER BY m.embedding <=> %s::vector
      LIMIT {pool}
    ),
    combined AS (
      SELECT COALESCE(f.materia_id, v.materia_id) AS materia_id,
             COALESCE(f.ato_id, v.ato_id) AS ato_id,
             COALESCE(1.0 / ({rrf_k} + f.pos), 0) + COALESCE(1.0 / ({rrf_k} + v.pos), 0) AS rrf_score
      FROM fts f FULL OUTER JOIN vec v USING (materia_id, ato_id)
    )
    SELECT a.id, a.tipo_ato, a.numero, a.orgao_emissor, a.data_publicacao,
           a.eficacia, a.status_vigencia, a.ementa,
           tt.natureza,
           m.tema_macro, m.tema_especifico, m.solucao, m.tese_adotada, m.ementa_trecho,
           comb.rrf_score,
           (SELECT COALESCE(SUM(
              CASE fe.tipo_norma
                WHEN 'tema_stf' THEN %s
                WHEN 'tema_stj' THEN %s
                WHEN 'sumula_carf' THEN %s
                WHEN 'sumula_vinculante' THEN %s
                WHEN 'sumula_stf' THEN %s
                WHEN 'sumula_stj' THEN %s
                ELSE 0
              END), 0)
            FROM materia_dispositivo fe
            WHERE fe.materia_id = m.id) AS boost_marco
    FROM combined comb
    JOIN ato_materia m ON m.id = comb.materia_id
    JOIN atos a ON a.id = comb.ato_id
    LEFT JOIN taxonomia_tipo_ato tt ON tt.codigo = a.tipo_ato
    ORDER BY comb.rrf_score DESC
    LIMIT {pool}
    """

    bm = WEIGHTS.get("boost_marco", {})
    boost_params = [
        bm.get("tema_stf", 0), bm.get("tema_stj", 0),
        bm.get("sumula_carf", 0), bm.get("sumula_vinculante", 0),
        bm.get("sumula_stf", 0), bm.get("sumula_stj", 0),
    ]

    full_params = [query, query] + params + [qvec] + params + [qvec] + boost_params

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, full_params)
            hits = cur.fetchall()

    # Re-ranking aplicacional
    for h in hits:
        peso_nat = float(pesos_natureza.get(h.get("natureza") or "", 1.0))
        peso_sec = _peso_secundario(h.get("natureza"), h.get("tipo_ato"), h.get("orgao_emissor"))
        peso_st = _peso_status(h.get("status_vigencia"))
        rec = _fator_recencia(h.get("data_publicacao"))
        h["score_final"] = float(h["rrf_score"]) * peso_nat * peso_sec * peso_st * rec + float(h["boost_marco"] or 0)
        h["_pesos"] = {"nat": peso_nat, "sec": peso_sec, "status": peso_st, "rec": round(rec, 3)}

    hits.sort(key=lambda x: x["score_final"], reverse=True)
    hits = hits[:limit]

    if formato == "json":
        print(json.dumps([dict(h, data_publicacao=str(h["data_publicacao"])) for h in hits],
                         indent=2, ensure_ascii=False, default=str))
    else:
        suffix = ""
        if vigente_em: suffix += f"  vigente_em={vigente_em}"
        if vinculante_em: suffix += f"  vinculante_em={vinculante_em} (janela 60 meses)"
        print(f"# busca: {query!r}  modo={modo}{suffix}")
        for h in hits:
            print(f"\n## {h['tipo_ato']} {h['numero']} — {h['orgao_emissor']} ({h['data_publicacao']})")
            print(f"   natureza: {h['natureza']} | eficacia: {h['eficacia']} | vigencia: {h['status_vigencia']}")
            print(f"   score: {h['score_final']:.4f}  (rrf={float(h['rrf_score']):.4f}  pesos={h['_pesos']}  marco={float(h['boost_marco'] or 0):.2f})")
            print(f"   tema: {h['tema_macro']} / {h['tema_especifico']}")
            print(f"   ementa: {(h['ementa'] or '')[:200]}")
            if h.get("solucao"):
                print(f"   solucao: {h['solucao'][:300]}")
            elif h.get("tese_adotada"):
                print(f"   tese: {h['tese_adotada'][:300]}")
            elif h.get("ementa_trecho"):
                print(f"   trecho: {h['ementa_trecho'][:300]}")


def facetas():
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT codigo, nome, natureza FROM taxonomia_tipo_ato ORDER BY natureza, codigo;")
            print("=== TIPOS DE ATO (por natureza) ===")
            for r in cur.fetchall():
                print(f"  [{r['natureza'] or '???':12s}] {r['codigo']:42s} {r['nome']}")
            cur.execute("SELECT DISTINCT tema_macro FROM ato_materia ORDER BY tema_macro;")
            print("\n=== TEMAS MACRO COM DADOS ===")
            for r in cur.fetchall():
                print(f"  {r['tema_macro']}")
            cur.execute("SELECT tributo_codigo, COUNT(*) AS n FROM materia_tributo GROUP BY tributo_codigo ORDER BY n DESC;")
            print("\n=== TRIBUTOS COM DADOS ===")
            for r in cur.fetchall():
                print(f"  {r['tributo_codigo']:30s} {r['n']:>6}")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--modo", default="pratico", choices=list(WEIGHTS["modos"].keys()))
    p.add_argument("--vigente-em", dest="vigente_em",
                   help="Reconstrucao temporal (YYYY-MM-DD). Filtra atos publicados ate a data.")
    p.add_argument("--vinculante-em", dest="vinculante_em",
                   help="Janela de prescricao (YYYY-MM-DD): retorna so atos vinculantes na janela [data-60meses, data].")
    p.add_argument("--tributos", nargs="+")
    p.add_argument("--temas", nargs="+")
    p.add_argument("--tipos", nargs="+")
    p.add_argument("--eficacia", nargs="+")
    p.add_argument("--data-ini")
    p.add_argument("--data-fim")
    p.add_argument("--limit", type=int, default=15)
    p.add_argument("--format", default="md", choices=["md", "json"])

    sub.add_parser("facetas")

    args = parser.parse_args()

    if args.cmd == "search":
        buscar(args.query, args.modo, args.tributos, args.temas, args.tipos, args.eficacia,
               args.data_ini, args.data_fim, args.vigente_em, args.vinculante_em,
               args.limit, args.format)
    elif args.cmd == "facetas":
        facetas()


if __name__ == "__main__":
    main()
