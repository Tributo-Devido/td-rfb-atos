"""compilar_estudo_combustiveis.py — Gera dossie de SCs/SDs sobre combustiveis e alcool.

Le da base td-rfb-atos e produz markdown estruturado por categoria (autorizado/vedado/condicionado).
"""
from __future__ import annotations
from dotenv import load_dotenv; load_dotenv()
import json
from pathlib import Path
from psycopg.rows import dict_row
from db import get_conn

OUT = Path(__file__).resolve().parent.parent / "outputs" / "wip" / "combustiveis-alcool-creditos" / "v1"

CATEGORIAS_AUTORIZADO = [
    "permite", "permitido", "autorizado", "é considerado insumo",
    "podem ser considerados insumos", "geram direito",
    "gera direito a crédito", "pode apurar", "pode descontar",
]
CATEGORIAS_VEDADO = [
    "não permite", "vedado", "não geram direito", "não pode apurar",
    "não constituem insumos", "não há direito", "impossibilidade de",
    "não se enquadram", "não permitida",
]


def classificar(solucao: str | None) -> str:
    if not solucao:
        return "?"
    s = solucao.lower()
    score_autoriza = sum(1 for k in CATEGORIAS_AUTORIZADO if k in s)
    score_veda = sum(1 for k in CATEGORIAS_VEDADO if k in s)
    if score_veda > score_autoriza:
        return "VEDADO"
    if score_autoriza > 0:
        return "AUTORIZADO"
    return "CONDICIONADO"


def ato_key(d: dict) -> tuple:
    return (d["tipo_ato"], d["numero"], d["orgao_emissor"])


def ato_label(d: dict) -> str:
    sigla = {"SOLUCAO_CONSULTA": "SC", "SOLUCAO_DIVERGENCIA": "SD",
             "SOLUCAO_CONSULTA_INTERNA": "SCI"}.get(d["tipo_ato"], d["tipo_ato"])
    return f"{sigla} {d['numero']}/{d['data_publicacao'][:4]} ({d['orgao_emissor']})"


def carregar(query: str) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query)
            return [dict(r) for r in cur.fetchall()]


def fundir_por_ato(materias: list[dict]) -> list[dict]:
    """Reduz materias_id duplicadas (PIS+COFINS = 2 materias) ao mesmo ato."""
    by_ato: dict[tuple, dict] = {}
    for m in materias:
        k = ato_key(m)
        if k not in by_ato:
            m["solucoes"] = [m["solucao"]] if m["solucao"] else []
            m["temas"] = {m["tema_especifico"]} if m["tema_especifico"] else set()
            by_ato[k] = m
        else:
            if m["solucao"] and m["solucao"] not in by_ato[k]["solucoes"]:
                by_ato[k]["solucoes"].append(m["solucao"])
            if m["tema_especifico"]:
                by_ato[k]["temas"].add(m["tema_especifico"])
    out = list(by_ato.values())
    out.sort(key=lambda d: d["data_publicacao"], reverse=True)
    return out


def render_ato(a: dict) -> str:
    label = ato_label(a)
    temas = " · ".join(sorted(a["temas"]))
    sol = "\n   ".join(s.replace("\n", " ").strip() for s in a["solucoes"][:2])
    out = [f"### {label}", f"*Temas:* {temas}", "", f"**Ementa:** {(a['ementa'] or '').replace(chr(10), ' ')[:300]}", ""]
    if a.get("fato_consultado"):
        out.append(f"**Fato consultado:** {a['fato_consultado'][:400]}")
        out.append("")
    out.append(f"**Solução:** {sol}")
    if a.get("fundamentacao_resumo"):
        out.append("")
        out.append(f"**Fundamentação:** {a['fundamentacao_resumo'][:300]}")
    return "\n".join(out)


def gerar_dossie(titulo: str, materias: list[dict], out_path: Path):
    atos = fundir_por_ato(materias)
    classes = {"AUTORIZADO": [], "VEDADO": [], "CONDICIONADO": [], "?": []}
    for a in atos:
        sol_concat = " ".join(a["solucoes"])
        classes[classificar(sol_concat)].append(a)

    lines = [f"# {titulo}", "", f"**Total:** {len(atos)} atos únicos · {len(materias)} matérias categorizadas (incluindo desdobramento PIS/COFINS).", ""]
    for cls in ("AUTORIZADO", "VEDADO", "CONDICIONADO", "?"):
        if not classes[cls]:
            continue
        emoji = {"AUTORIZADO": "✅", "VEDADO": "❌", "CONDICIONADO": "⚠️", "?": "❓"}[cls]
        lines.append(f"## {emoji} {cls}  ({len(classes[cls])} atos)")
        lines.append("")
        for a in classes[cls]:
            lines.append(render_ato(a))
            lines.append("")
            lines.append("---")
            lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[ok] {out_path.name}: {len(atos)} atos ({len(classes['AUTORIZADO'])} ✅ / {len(classes['VEDADO'])} ❌ / {len(classes['CONDICIONADO'])} ⚠️)")


def main():
    out_dir = OUT / "scs-da-base"
    out_dir.mkdir(parents=True, exist_ok=True)

    sql_combustiveis = """
        SELECT a.id AS ato_id, a.tipo_ato, a.numero, a.orgao_emissor, a.data_publicacao::text AS data_publicacao,
               a.ementa, m.tema_macro, m.tema_especifico, m.solucao, m.fato_consultado, m.fundamentacao_resumo
        FROM ato_materia m JOIN atos a ON a.id = m.ato_id
        WHERE (m.solucao ILIKE '%combust%' OR m.solucao ILIKE '%óleo diesel%' OR m.solucao ILIKE '%oleo diesel%'
               OR m.solucao ILIKE '%gasolina%' OR m.solucao ILIKE '%lubrificante%'
               OR m.fato_consultado ILIKE '%combust%')
          AND a.tipo_ato IN ('SOLUCAO_CONSULTA','SOLUCAO_DIVERGENCIA','SOLUCAO_CONSULTA_INTERNA')
          AND EXISTS (SELECT 1 FROM materia_tributo mt WHERE mt.materia_id = m.id AND mt.tributo_codigo IN ('PIS','COFINS'))
        ORDER BY a.data_publicacao DESC
    """
    sql_alcool = """
        SELECT a.id AS ato_id, a.tipo_ato, a.numero, a.orgao_emissor, a.data_publicacao::text AS data_publicacao,
               a.ementa, m.tema_macro, m.tema_especifico, m.solucao, m.fato_consultado, m.fundamentacao_resumo
        FROM ato_materia m JOIN atos a ON a.id = m.ato_id
        WHERE (m.solucao ILIKE '%etanol%' OR m.solucao ILIKE '%álcool%' OR m.solucao ILIKE '%alcool%'
               OR m.fato_consultado ILIKE '%etanol%' OR m.fato_consultado ILIKE '%álcool%'
               OR a.ementa ILIKE '%etanol%' OR a.ementa ILIKE '%álcool%')
          AND a.tipo_ato IN ('SOLUCAO_CONSULTA','SOLUCAO_DIVERGENCIA','SOLUCAO_CONSULTA_INTERNA')
          AND EXISTS (SELECT 1 FROM materia_tributo mt WHERE mt.materia_id = m.id AND mt.tributo_codigo IN ('PIS','COFINS'))
        ORDER BY a.data_publicacao DESC
    """

    gerar_dossie(
        "Dossiê SCs/SDs — Créditos de PIS/COFINS sobre Combustíveis",
        carregar(sql_combustiveis),
        out_dir / "01-combustiveis.md",
    )
    gerar_dossie(
        "Dossiê SCs/SDs — Créditos de PIS/COFINS sobre Álcool/Etanol",
        carregar(sql_alcool),
        out_dir / "02-alcool-etanol.md",
    )


if __name__ == "__main__":
    main()
