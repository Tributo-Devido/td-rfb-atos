"""
validate_cloud_parity.py — Validação pós-ETL Docker vs Nuvem.

Track A.4 da migração ratio-juris (2026-05-11).

Checks:
    1. Contagem por tabela: Docker vs Nuvem.
    2. Cobertura embedding em ato_materia (3072) = 100%.
    3. Sample query semântica: "transportadora pode creditar pneu".
    4. Integridade FK (nenhum órfão).
    5. Distribuição de sinal: AUTORIZA/VEDA/CONDICIONA/INDETERMINADO.

Saída: relatório markdown em
    c:/td-skills/td-rfb-atos/outputs/migration-validation-2026-05-11.md
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import psycopg

SCRIPTS_LIB = Path(r"c:/td-skills/td-creditos/scripts").resolve()
sys.path.insert(0, str(SCRIPTS_LIB))
from lib.embed_openai import embed_texts_sync, vector_literal  # noqa: E402


DOCKER_DSN = "postgresql://td:td@localhost:5435/td_rfb_atos"
CLOUD_DSN = Path(r"C:\Users\tribu\.claude-tg-bot\ratio-pg-dsn.txt").read_text(encoding="utf-8").strip()

OUTPUT_PATH = Path(r"c:/td-skills/td-rfb-atos/outputs/migration-validation-2026-05-11.md")


PAIRS = [
    ("atos",                    "ato"),
    ("ato_content",             "ato_content"),
    ("ato_segmento",            "ato_segmento"),
    ("ato_materia",             "ato_materia"),
    ("materia_tributo",         "materia_tributo"),
    ("materia_cnae",            "materia_cnae"),
    ("materia_dispositivo",     "materia_dispositivo"),
    ("ato_relacao",             "ato_relacao"),
    ("conflito_temporal",       "conflito_temporal"),
    ("ato_alteracao_historico", "ato_alteracao_historico"),
]


def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def check_counts(src, dst, report: list[str]):
    report.append("## 1. Contagem por tabela (Docker vs Nuvem)\n")
    report.append("| Tabela Docker | Tabela Nuvem | Docker | Nuvem | Match |")
    report.append("|---|---|---:|---:|:---:|")
    all_ok = True
    for d_tbl, c_tbl in PAIRS:
        with src.cursor() as s:
            s.execute(f"SELECT COUNT(*) FROM {d_tbl}")
            dc = s.fetchone()[0]
        with dst.cursor() as c:
            c.execute(f"SELECT COUNT(*) FROM rfb_atos.{c_tbl}")
            cc = c.fetchone()[0]
        ok = (dc == cc)
        all_ok = all_ok and ok
        report.append(f"| {d_tbl} | rfb_atos.{c_tbl} | {dc:,} | {cc:,} | {'OK' if ok else 'FAIL'} |")
        log(f"  {'OK' if ok else 'FAIL'} {d_tbl:30s} docker={dc:>8} cloud={cc:>8}")
    report.append("")
    return all_ok


def check_embedding_coverage(dst, report: list[str]):
    report.append("## 2. Cobertura de embedding em rfb_atos.ato_materia\n")
    with dst.cursor() as c:
        c.execute("""
            SELECT COUNT(*) FILTER (WHERE embedding IS NOT NULL) AS com_emb,
                   COUNT(*) AS total
              FROM rfb_atos.ato_materia
        """)
        com, total = c.fetchone()
    pct = (com / total * 100) if total else 0
    report.append(f"- com embedding 3072 (halfvec): **{com:,}** / {total:,} ({pct:.2f}%)")
    if com == total:
        report.append("- **OK: cobertura 100%**")
    else:
        report.append(f"- **FAIL**: {total - com:,} matérias sem embedding")
    report.append("")
    return com == total


def check_sinal_distribution(dst, report: list[str]):
    report.append("## 3. Distribuição de `sinal` em rfb_atos.ato_materia\n")
    with dst.cursor() as c:
        c.execute("""
            SELECT sinal, COUNT(*)
              FROM rfb_atos.ato_materia
             GROUP BY sinal
             ORDER BY COUNT(*) DESC
        """)
        rows = c.fetchall()
    expected = {"AUTORIZA": 12545, "VEDA": 3801, "CONDICIONA": 7190, "INDETERMINADO": 17628}
    report.append("| Sinal | Esperado | Obtido | Match |")
    report.append("|---|---:|---:|:---:|")
    obtained = {s: n for (s, n) in rows}
    all_ok = True
    for sinal in ["AUTORIZA", "VEDA", "CONDICIONA", "INDETERMINADO"]:
        got = obtained.get(sinal, 0)
        exp = expected[sinal]
        ok = (got == exp)
        all_ok = all_ok and ok
        report.append(f"| {sinal} | {exp:,} | {got:,} | {'OK' if ok else 'FAIL'} |")
    null_count = obtained.get(None, 0)
    if null_count:
        report.append(f"| (NULL) | 0 | {null_count:,} | INFO |")
    report.append("")
    return all_ok


def check_fk_integrity(dst, report: list[str]):
    report.append("## 4. Integridade FK\n")
    checks = [
        ("ato_materia.ato_id sem ato",
         "SELECT COUNT(*) FROM rfb_atos.ato_materia m LEFT JOIN rfb_atos.ato a ON m.ato_id = a.id WHERE a.id IS NULL"),
        ("ato_segmento.ato_id sem ato",
         "SELECT COUNT(*) FROM rfb_atos.ato_segmento s LEFT JOIN rfb_atos.ato a ON s.ato_id = a.id WHERE a.id IS NULL"),
        ("materia_tributo.materia_id sem materia",
         "SELECT COUNT(*) FROM rfb_atos.materia_tributo mt LEFT JOIN rfb_atos.ato_materia m ON mt.materia_id = m.id WHERE m.id IS NULL"),
        ("materia_dispositivo.materia_id sem materia",
         "SELECT COUNT(*) FROM rfb_atos.materia_dispositivo md LEFT JOIN rfb_atos.ato_materia m ON md.materia_id = m.id WHERE m.id IS NULL"),
        ("conflito_temporal.materia_a sem materia",
         "SELECT COUNT(*) FROM rfb_atos.conflito_temporal ct LEFT JOIN rfb_atos.ato_materia m ON ct.materia_a_id = m.id WHERE m.id IS NULL"),
        ("conflito_temporal.materia_b sem materia",
         "SELECT COUNT(*) FROM rfb_atos.conflito_temporal ct LEFT JOIN rfb_atos.ato_materia m ON ct.materia_b_id = m.id WHERE m.id IS NULL"),
    ]
    report.append("| Check | Orfãos | Status |")
    report.append("|---|---:|:---:|")
    all_ok = True
    with dst.cursor() as c:
        for name, sql in checks:
            c.execute(sql)
            n = c.fetchone()[0]
            ok = (n == 0)
            all_ok = all_ok and ok
            report.append(f"| {name} | {n:,} | {'OK' if ok else 'FAIL'} |")
    report.append("")
    return all_ok


def check_semantic_query(dst, report: list[str]):
    report.append("## 5. Sample query semântica\n")
    query = "transportadora pode creditar PIS COFINS sobre pneu autopeca insumo"
    report.append(f"Query: `\"{query}\"`\n")
    vec = embed_texts_sync([query])[0]
    vec_lit = vector_literal(vec)

    with dst.cursor() as c:
        c.execute("""
            SELECT m.id, m.sinal, m.tema_macro, m.tema_especifico,
                   LEFT(m.ementa_trecho, 120) AS preview,
                   m.embedding <=> %s::halfvec AS distancia
              FROM rfb_atos.ato_materia m
             WHERE m.embedding IS NOT NULL
             ORDER BY m.embedding <=> %s::halfvec
             LIMIT 10
        """, (vec_lit, vec_lit))
        rows = c.fetchall()

    report.append("| # | id | sinal | tema | distância | preview |")
    report.append("|---:|---:|---|---|---:|---|")
    for i, (mid, sinal, tm, te, prev, dist) in enumerate(rows, 1):
        prev_safe = (prev or "").replace("|", "\\|").replace("\n", " ")[:120]
        tema = f"{tm or ''} / {te or ''}"
        report.append(f"| {i} | {mid} | {sinal or '-'} | {tema} | {dist:.4f} | {prev_safe} |")
    report.append("")
    return len(rows) >= 5


def main():
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    report: list[str] = []
    report.append("# Migration Validation Report — rfb_atos Docker -> Nuvem")
    report.append("")
    report.append(f"**Gerado em:** {time.strftime('%Y-%m-%d %H:%M:%S')}")
    report.append("**Track:** A (rfb-atos modelo rico)")
    report.append("**Plano:** `.planning/migracao-ratio-juris-execucao-2026-05-11.md`")
    report.append("")

    log("Conectando Docker e Nuvem")
    src = psycopg.connect(DOCKER_DSN)
    dst = psycopg.connect(CLOUD_DSN)

    log("1. Contagens por tabela")
    ok_counts = check_counts(src, dst, report)

    log("2. Cobertura embedding")
    ok_emb = check_embedding_coverage(dst, report)

    log("3. Distribuição de sinal")
    ok_sinal = check_sinal_distribution(dst, report)

    log("4. Integridade FK")
    ok_fk = check_fk_integrity(dst, report)

    log("5. Query semântica")
    ok_sem = check_semantic_query(dst, report)

    report.append("## Resumo final\n")
    overall = ok_counts and ok_emb and ok_sinal and ok_fk and ok_sem
    report.append(f"- 1. Contagens: **{'OK' if ok_counts else 'FAIL'}**")
    report.append(f"- 2. Embedding 100%: **{'OK' if ok_emb else 'FAIL'}**")
    report.append(f"- 3. Distribuição sinal: **{'OK' if ok_sinal else 'FAIL'}**")
    report.append(f"- 4. Integridade FK: **{'OK' if ok_fk else 'FAIL'}**")
    report.append(f"- 5. Query semântica: **{'OK' if ok_sem else 'FAIL'}**")
    report.append("")
    report.append(f"## **Status global: {'OK (todos os checks passaram)' if overall else 'FAIL (revisar items acima)'}**")
    report.append("")

    OUTPUT_PATH.write_text("\n".join(report), encoding="utf-8")
    log(f"Relatorio salvo em {OUTPUT_PATH}")
    log(f"Status global: {'OK' if overall else 'FAIL'}")
    sys.exit(0 if overall else 1)


if __name__ == "__main__":
    main()
