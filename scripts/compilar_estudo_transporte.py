"""compilar_estudo_transporte.py — Dossie de SCs/SDs sobre transporte de cargas e frete."""
from __future__ import annotations
from dotenv import load_dotenv; load_dotenv()
from pathlib import Path
from psycopg.rows import dict_row
from db import get_conn

OUT = Path(__file__).resolve().parent.parent / "outputs" / "wip" / "transporte-cargas-creditos" / "v1"

CATEGORIAS_AUTORIZADO = [
    "permite", "permitido", "autorizado", "é considerado insumo",
    "podem ser considerados insumos", "geram direito",
    "gera direito a crédito", "pode apurar", "pode descontar", "é admitido",
]
CATEGORIAS_VEDADO = [
    "não permite", "vedado", "vedada", "não geram direito", "não pode apurar",
    "não constituem insumos", "não há direito", "impossibilidade",
    "não se enquadram", "não permitida", "não cabe", "não é admitido",
]


def classificar(s: str) -> str:
    if not s: return "?"
    s = s.lower()
    a = sum(1 for k in CATEGORIAS_AUTORIZADO if k in s)
    v = sum(1 for k in CATEGORIAS_VEDADO if k in s)
    if v > a: return "VEDADO"
    if a > 0: return "AUTORIZADO"
    return "CONDICIONADO"


def label(d: dict) -> str:
    sg = {"SOLUCAO_CONSULTA":"SC","SOLUCAO_DIVERGENCIA":"SD","SOLUCAO_CONSULTA_INTERNA":"SCI"}.get(d["tipo_ato"], d["tipo_ato"])
    return f"{sg} {d['numero']}/{d['data_publicacao'][:4]} ({d['orgao_emissor']})"


def carregar(query: str) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query)
            return [dict(r) for r in cur.fetchall()]


def fundir(materias: list[dict]) -> list[dict]:
    by_ato: dict[tuple, dict] = {}
    for m in materias:
        k = (m["tipo_ato"], m["numero"], m["orgao_emissor"])
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


def render(a: dict) -> str:
    temas = " · ".join(sorted(a["temas"]))
    sol = "\n   ".join(s.replace("\n", " ").strip() for s in a["solucoes"][:2])
    out = [f"### {label(a)}", f"*Temas:* {temas}", "",
           f"**Ementa:** {(a['ementa'] or '').replace(chr(10),' ')[:300]}", ""]
    if a.get("fato_consultado"):
        out.append(f"**Fato:** {a['fato_consultado'][:400]}"); out.append("")
    out.append(f"**Solução:** {sol}")
    if a.get("fundamentacao_resumo"):
        out.append(""); out.append(f"**Fundamentação:** {a['fundamentacao_resumo'][:300]}")
    return "\n".join(out)


def gerar_dossie(titulo: str, materias: list[dict], out_path: Path):
    atos = fundir(materias)
    classes = {"AUTORIZADO": [], "VEDADO": [], "CONDICIONADO": [], "?": []}
    for a in atos:
        classes[classificar(" ".join(a["solucoes"]))].append(a)
    lines = [f"# {titulo}", "",
             f"**Total:** {len(atos)} atos únicos · {len(materias)} matérias.", ""]
    for cls in ("AUTORIZADO","VEDADO","CONDICIONADO","?"):
        if not classes[cls]: continue
        emoji = {"AUTORIZADO":"✅","VEDADO":"❌","CONDICIONADO":"⚠️","?":"❓"}[cls]
        lines.append(f"## {emoji} {cls}  ({len(classes[cls])} atos)")
        lines.append("")
        for a in classes[cls]:
            lines.append(render(a)); lines.append(""); lines.append("---"); lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[ok] {out_path.name}: {len(atos)} ({len(classes['AUTORIZADO'])}✅ {len(classes['VEDADO'])}❌ {len(classes['CONDICIONADO'])}⚠️)")


def main():
    out_dir = OUT / "scs-da-base"
    out_dir.mkdir(parents=True, exist_ok=True)

    base_filter = """
        AND a.tipo_ato IN ('SOLUCAO_CONSULTA','SOLUCAO_DIVERGENCIA','SOLUCAO_CONSULTA_INTERNA')
        AND EXISTS (SELECT 1 FROM materia_tributo mt WHERE mt.materia_id = m.id AND mt.tributo_codigo IN ('PIS','COFINS'))
    """
    cols = """
        a.id AS ato_id, a.tipo_ato, a.numero, a.orgao_emissor, a.data_publicacao::text AS data_publicacao,
        a.ementa, m.tema_macro, m.tema_especifico, m.solucao, m.fato_consultado, m.fundamentacao_resumo
    """
    base_join = "FROM ato_materia m JOIN atos a ON a.id = m.ato_id"

    queries = {
        "01-frete-aquisicao": (
            "Frete na aquisição de insumos, ativo imobilizado e mercadoria",
            f"""SELECT {cols} {base_join} WHERE
                ((m.solucao ILIKE '%frete na aquisi%' OR m.solucao ILIKE '%frete de aquisi%'
                  OR m.solucao ILIKE '%frete na entrada%' OR m.solucao ILIKE '%frete da aquisi%'
                  OR m.fato_consultado ILIKE '%frete na aquisi%')) {base_filter}
                ORDER BY a.data_publicacao DESC""",
        ),
        "02-frete-venda-revenda": (
            "Frete na operação de venda e revenda",
            f"""SELECT {cols} {base_join} WHERE
                ((m.solucao ILIKE '%frete na venda%' OR m.solucao ILIKE '%frete na opera%'
                  OR m.solucao ILIKE '%frete na revenda%' OR m.solucao ILIKE '%frete de venda%'
                  OR m.solucao ILIKE '%frete pago%vendedor%' OR m.solucao ILIKE '%frete suportado%')) {base_filter}
                ORDER BY a.data_publicacao DESC""",
        ),
        "03-frota-propria": (
            "Frota própria — combustível, lubrificante, manutenção, pneus, peças",
            f"""SELECT {cols} {base_join} WHERE
                ((m.solucao ILIKE '%frota%' OR m.solucao ILIKE '%veículo%' OR m.solucao ILIKE '%veiculo%'
                  OR m.solucao ILIKE '%caminh%' OR m.solucao ILIKE '%pneu%' OR m.solucao ILIKE '%manuten%veículo%')
                 AND (m.solucao ILIKE '%transport%' OR m.solucao ILIKE '%frete%' OR a.ementa ILIKE '%transport%')) {base_filter}
                ORDER BY a.data_publicacao DESC""",
        ),
        "04-tac-subcontratacao": (
            "TAC e subcontratação de transportadores autônomos (Lei 11.442/2007)",
            f"""SELECT {cols} {base_join} WHERE
                ((m.solucao ILIKE '%TAC%' OR m.solucao ILIKE '%transportador autônomo%' OR m.solucao ILIKE '%transportador autonomo%'
                  OR m.solucao ILIKE '%subcontrat%' OR m.solucao ILIKE '%lei 11.442%' OR m.solucao ILIKE '%credito presumido%transport%'
                  OR m.solucao ILIKE '%crédito presumido%transport%'
                  OR m.fato_consultado ILIKE '%subcontrat%' OR m.fato_consultado ILIKE '%TAC%')) {base_filter}
                ORDER BY a.data_publicacao DESC""",
        ),
        "05-ativo-imobilizado": (
            "Ativo imobilizado — caminhões, carretas, semirreboques, depreciação",
            f"""SELECT {cols} {base_join} WHERE
                ((m.solucao ILIKE '%ativo imobiliz%' OR m.solucao ILIKE '%depreci%') AND
                 (m.solucao ILIKE '%caminh%' OR m.solucao ILIKE '%carreta%' OR m.solucao ILIKE '%semirreboque%'
                  OR m.solucao ILIKE '%transport%' OR a.ementa ILIKE '%transport%')) {base_filter}
                ORDER BY a.data_publicacao DESC""",
        ),
        "06-pedagio-seguro-armazenagem": (
            "Pedágio, seguro de cargas, armazenagem",
            f"""SELECT {cols} {base_join} WHERE
                ((m.solucao ILIKE '%pedágio%' OR m.solucao ILIKE '%pedagio%'
                  OR (m.solucao ILIKE '%seguro%' AND m.solucao ILIKE '%carga%')
                  OR m.solucao ILIKE '%armazenagem%')
                 AND (m.solucao ILIKE '%transport%' OR m.solucao ILIKE '%frete%' OR a.ementa ILIKE '%transport%')) {base_filter}
                ORDER BY a.data_publicacao DESC""",
        ),
        "07-cabotagem-multimodal": (
            "Cabotagem, multimodal, transporte internacional",
            f"""SELECT {cols} {base_join} WHERE
                ((m.solucao ILIKE '%cabotagem%' OR m.solucao ILIKE '%multimodal%'
                  OR m.solucao ILIKE '%transporte internacional%' OR m.solucao ILIKE '%aquaviário%' OR m.solucao ILIKE '%aquaviario%'
                  OR m.solucao ILIKE '%REPETRO%' OR m.solucao ILIKE '%navegação%transport%')) {base_filter}
                ORDER BY a.data_publicacao DESC""",
        ),
        "08-frete-monofasico-zero": (
            "Frete na cadeia de produtos zero, monofásicos e suspensos",
            f"""SELECT {cols} {base_join} WHERE
                ((m.solucao ILIKE '%frete%' OR m.solucao ILIKE '%transport%')
                 AND (m.solucao ILIKE '%monof%' OR m.solucao ILIKE '%alíquota zero%' OR m.solucao ILIKE '%aliquota zero%'
                      OR m.solucao ILIKE '%suspens%' OR m.solucao ILIKE '%não tributad%' OR m.solucao ILIKE '%isent%'
                      OR m.solucao ILIKE '%substituiç%')) {base_filter}
                ORDER BY a.data_publicacao DESC""",
        ),
    }

    for slug, (titulo, sql) in queries.items():
        gerar_dossie(titulo, carregar(sql), out_dir / f"{slug}.md")


if __name__ == "__main__":
    main()
