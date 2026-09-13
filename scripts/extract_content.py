"""
extract_content.py — Converte PDF baixado em texto via MarkItDown.

Para cada ato com pdf_disponivel=TRUE e content_disponivel=FALSE, extrai texto e
insere em ato_content. Marca content_disponivel=TRUE.

Uso:
    py extract_content.py              # processa todos pendentes
    py extract_content.py --limit 50
"""
from __future__ import annotations

import argparse
from pathlib import Path

from loguru import logger
from markitdown import MarkItDown
from psycopg.rows import dict_row
from tqdm import tqdm

from db import get_conn


def processar(limit: int | None = None):
    md = MarkItDown(enable_plugins=False)

    sql = """
        SELECT a.id, a.tipo_ato, a.numero, a.metadata->>'pdf_path' AS pdf_path
        FROM atos a
        LEFT JOIN ato_content c ON c.ato_id = a.id
        WHERE a.pdf_disponivel = TRUE
          AND a.content_disponivel = FALSE
          AND c.ato_id IS NULL
          AND a.metadata->>'pdf_path' IS NOT NULL
        ORDER BY a.data_publicacao DESC
    """
    if limit:
        sql += f" LIMIT {limit}"

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql)
            atos = cur.fetchall()

        if not atos:
            logger.info("Nenhum ato pendente de extração de texto.")
            return

        logger.info(f"Extraindo texto de {len(atos)} PDFs...")

        for ato in tqdm(atos, desc="MarkItDown"):
            pdf_path = Path(ato["pdf_path"])
            if not pdf_path.exists():
                logger.warning(f"PDF não encontrado: {pdf_path}")
                continue

            try:
                result = md.convert(str(pdf_path))
                texto = (result.text_content or "").strip()
                if not texto:
                    logger.warning(f"Texto vazio para {pdf_path}")
                    continue

                with conn.cursor() as cur2:
                    cur2.execute(
                        """
                        INSERT INTO ato_content (ato_id, content, fonte_extracao, caracteres)
                        VALUES (%s, %s, 'markitdown', %s)
                        ON CONFLICT (ato_id) DO UPDATE SET
                            content = EXCLUDED.content,
                            fonte_extracao = EXCLUDED.fonte_extracao,
                            caracteres = EXCLUDED.caracteres,
                            processed_at = now()
                        """,
                        (ato["id"], texto, len(texto)),
                    )
                    cur2.execute(
                        "UPDATE atos SET content_disponivel = TRUE, updated_at = now() WHERE id = %s",
                        (ato["id"],),
                    )
                conn.commit()

            except Exception as e:
                logger.error(f"erro ato_id={ato['id']} pdf={pdf_path}: {e}")
                conn.rollback()
                continue

    logger.success("FIM extract_content.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    processar(limit=args.limit)
