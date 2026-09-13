"""
stats.py — Volumetria, frescor, cobertura.

Uso:
    py stats.py
"""
from __future__ import annotations

from psycopg.rows import dict_row

from db import get_conn


def main():
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            print("=== VOLUMETRIA ===\n")

            cur.execute("""
                SELECT tipo_ato, COUNT(*) AS n,
                       SUM(CASE WHEN pdf_disponivel THEN 1 ELSE 0 END) AS com_pdf,
                       SUM(CASE WHEN content_disponivel THEN 1 ELSE 0 END) AS com_content,
                       SUM(CASE WHEN analise_completa THEN 1 ELSE 0 END) AS analisados
                FROM atos
                GROUP BY tipo_ato
                ORDER BY n DESC
            """)
            print(f"{'tipo_ato':40s} {'total':>8} {'pdf':>8} {'content':>8} {'analise':>8}")
            for r in cur.fetchall():
                print(f"{r['tipo_ato'][:40]:40s} {r['n']:>8} {r['com_pdf']:>8} {r['com_content']:>8} {r['analisados']:>8}")

            print("\n=== FRESCOR (ano-mês mais recente) ===\n")
            cur.execute("""
                SELECT tipo_ato, MAX(data_publicacao) AS ultima
                FROM atos
                GROUP BY tipo_ato
                ORDER BY ultima DESC
            """)
            for r in cur.fetchall():
                print(f"  {r['tipo_ato']:40s}  {r['ultima']}")

            print("\n=== EMBEDDINGS ===\n")
            cur.execute("SELECT COUNT(*) AS n_materias, SUM(CASE WHEN embedding IS NOT NULL THEN 1 ELSE 0 END) AS embedded FROM ato_materia;")
            r = cur.fetchone()
            print(f"  matérias: {r['embedded']}/{r['n_materias']} embeddadas")
            cur.execute("SELECT COUNT(*) AS n_chunks FROM ato_chunks;")
            print(f"  chunks: {cur.fetchone()['n_chunks']}")

            print("\n=== GRAFO DE RELAÇÕES ===\n")
            cur.execute("SELECT tipo_relacao, COUNT(*) AS n FROM ato_relacao GROUP BY tipo_relacao ORDER BY n DESC;")
            for r in cur.fetchall():
                print(f"  {r['tipo_relacao']:30s} {r['n']:>6}")


if __name__ == "__main__":
    main()
