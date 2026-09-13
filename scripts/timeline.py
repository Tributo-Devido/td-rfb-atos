"""timeline.py — Renderiza linha temporal de SCs/SDs sobre um tema.

Uso:
  py timeline.py tema CREDITAMENTO.COMBUSTIVEL --tributos PIS COFINS
  py timeline.py tema CREDITAMENTO.FRETE --vinculante-em 2022-08-15
  py timeline.py conflitos --top 30  # top conflitos a revisar
"""
from __future__ import annotations
from dotenv import load_dotenv; load_dotenv()
import argparse
from psycopg.rows import dict_row
from db import get_conn


def sigla(tipo: str) -> str:
    return {"SOLUCAO_CONSULTA":"SC","SOLUCAO_DIVERGENCIA":"SD","SOLUCAO_CONSULTA_INTERNA":"SCI"}.get(tipo, tipo)


def cmd_tema(args):
    where = ["m.tema_especifico = %s", "a.tipo_ato IN ('SOLUCAO_CONSULTA','SOLUCAO_DIVERGENCIA','SOLUCAO_CONSULTA_INTERNA')"]
    params: list = [args.tema]
    if args.tributos:
        where.append("EXISTS (SELECT 1 FROM materia_tributo mt WHERE mt.materia_id = m.id AND mt.tributo_codigo = ANY(%s))")
        params.append(args.tributos)
    if args.vinculante_em:
        where.append("a.data_publicacao <= %s")
        params.append(args.vinculante_em)
        where.append("a.data_publicacao >= (%s::date - INTERVAL '60 months')")
        params.append(args.vinculante_em)
        where.append("(a.eficacia LIKE 'vinculante%%')")

    sql = f"""
        SELECT a.id AS ato_id, m.id AS materia_id, a.tipo_ato, a.numero, a.orgao_emissor,
               a.data_publicacao::text AS data, a.eficacia, a.status_vigencia,
               m.solucao, m.fato_consultado, m.tema_especifico
        FROM ato_materia m
        JOIN atos a ON a.id = m.ato_id
        WHERE {' AND '.join(where)}
        ORDER BY a.data_publicacao
    """
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            mats = cur.fetchall()

        if not mats:
            print(f"Nenhum ato encontrado para tema={args.tema}")
            return

        # Pega conflitos relevantes
        mat_ids = [m["id"] for m in mats] if False else [m["materia_id"] for m in mats]
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT * FROM conflito_temporal
                WHERE tema_especifico = %s
                  AND (materia_a_id = ANY(%s) OR materia_b_id = ANY(%s))
                ORDER BY similaridade DESC LIMIT 30
            """, [args.tema, mat_ids, mat_ids])
            conflitos = cur.fetchall()

    print(f"# Timeline: {args.tema}")
    if args.tributos:
        print(f"*Tributos:* {', '.join(args.tributos)}")
    if args.vinculante_em:
        print(f"*Janela vinculante:* atos publicados em [{args.vinculante_em} - 60 meses, {args.vinculante_em}]")
    print(f"\n**{len(mats)} matérias** ({len(set(m['ato_id'] for m in mats))} atos únicos)\n")

    # Agrupa por ano
    por_ano: dict[str, list] = {}
    for m in mats:
        por_ano.setdefault(m["data"][:4], []).append(m)
    for ano in sorted(por_ano.keys()):
        print(f"## {ano}")
        for m in por_ano[ano]:
            sg = sigla(m["tipo_ato"])
            sol = (m["solucao"] or "")[:300].replace("\n", " ")
            print(f"\n- **{sg} {m['numero']}/{ano} ({m['orgao_emissor']})** · {m['data']} · {m['eficacia']}")
            print(f"  {sol}")

    if conflitos:
        print(f"\n## ⚠️ Possíveis Supersedências (top {len(conflitos)})\n")
        # Mapa id->label
        lbl = {m["materia_id"]: f"{sigla(m['tipo_ato'])} {m['numero']}/{m['data'][:4]} ({m['orgao_emissor']})" for m in mats}
        for c in conflitos:
            la = lbl.get(c["materia_a_id"], f"materia#{c['materia_a_id']}")
            lb = lbl.get(c["materia_b_id"], f"materia#{c['materia_b_id']}")
            arrow = "→ POTENCIALMENTE SUPERA →" if c["ato_b_supera"] else "↔"
            star = " 🔗" if c["formal_relacao_existe"] else ""
            print(f"- [{c['similaridade']:.3f}] **{la}** ({c['sinal_a']}) {arrow} **{lb}** ({c['sinal_b']}){star}")


def cmd_conflitos(args):
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT c.*,
                       a_a.tipo_ato AS a_tipo, a_a.numero AS a_num, a_a.orgao_emissor AS a_org,
                       a_b.tipo_ato AS b_tipo, a_b.numero AS b_num, a_b.orgao_emissor AS b_org
                FROM conflito_temporal c
                JOIN ato_materia ma_a ON ma_a.id = c.materia_a_id
                JOIN atos a_a ON a_a.id = ma_a.ato_id
                JOIN ato_materia ma_b ON ma_b.id = c.materia_b_id
                JOIN atos a_b ON a_b.id = ma_b.ato_id
                WHERE c.ato_b_supera = TRUE
                  AND NOT c.formal_relacao_existe
                ORDER BY c.similaridade DESC, c.data_b DESC
                LIMIT %s
            """, [args.top])
            for c in cur.fetchall():
                a_label = f"{sigla(c['a_tipo'])} {c['a_num']}/{str(c['data_a'])[:4]} {c['a_org']}"
                b_label = f"{sigla(c['b_tipo'])} {c['b_num']}/{str(c['data_b'])[:4]} {c['b_org']}"
                print(f"[{c['similaridade']:.3f}] {c['tema_especifico']:40s} | {a_label} ({c['sinal_a']}) -> {b_label} ({c['sinal_b']})")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    pt = sub.add_parser("tema", help="Linha temporal de um tema_especifico")
    pt.add_argument("tema")
    pt.add_argument("--tributos", nargs="+")
    pt.add_argument("--vinculante-em", dest="vinculante_em")
    pc = sub.add_parser("conflitos", help="Top conflitos a revisar")
    pc.add_argument("--top", type=int, default=30)
    args = p.parse_args()
    {"tema": cmd_tema, "conflitos": cmd_conflitos}[args.cmd](args)


if __name__ == "__main__":
    main()
