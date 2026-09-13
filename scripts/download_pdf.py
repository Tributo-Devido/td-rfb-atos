"""
download_pdf.py — Baixa PDFs dos atos que têm link e ainda não foram baixados.

A página individual do ato (atos.link) é consultada via HTTP. Se houver botão de
download de PDF, baixa para PDF_DIR e marca atos.pdf_disponivel = TRUE.
Caso não tenha PDF, marca como FALSE (orientação só com ementa publicada).

Uso:
    py download_pdf.py             # baixa PDFs de atos com pdf_disponivel IS NULL
    py download_pdf.py --limit 100 # processa só 100
    py download_pdf.py --redo      # tenta de novo os marcados como FALSE (caso página tenha PDF agora)
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential
from tqdm import tqdm

from db import get_conn

PDF_DIR = Path(os.environ.get("PDF_DIR", "./pdfs")).resolve()
PDF_DIR.mkdir(parents=True, exist_ok=True)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def fetch(url: str) -> requests.Response:
    r = requests.get(url, timeout=60, headers={"User-Agent": UA})
    r.raise_for_status()
    return r


def descobrir_pdf_url(html_pagina_ato: str, url_pagina: str) -> str | None:
    """Procura link de PDF na página do ato (botão 'Baixar PDF' ou anchor com .pdf)."""
    soup = BeautifulSoup(html_pagina_ato, "html.parser")
    # Heurística 1: anchor com texto "PDF" ou href terminando em .pdf
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text().strip().lower()
        if href.lower().endswith(".pdf") or "pdf" in text:
            return urljoin(url_pagina, href)
    return None


def slugify_filename(ato: dict) -> str:
    """Nome do arquivo: TIPO_NUMERO_ORGAO_AAAA-MM-DD.pdf"""
    safe_num = re.sub(r"[^a-zA-Z0-9_-]", "_", ato["numero"])
    return f"{ato['tipo_ato']}_{safe_num}_{ato['orgao_emissor']}_{ato['data_publicacao']}.pdf"


def processar(redo: bool = False, limit: int | None = None):
    where = "pdf_disponivel IS NULL"
    if redo:
        where = "pdf_disponivel = FALSE"

    sql = f"""
        SELECT id, tipo_ato, numero, orgao_emissor, data_publicacao::text, link
        FROM atos
        WHERE {where}
          AND link IS NOT NULL
        ORDER BY data_publicacao DESC
    """
    if limit:
        sql += f" LIMIT {limit}"

    with get_conn() as conn:
        with conn.cursor(row_factory=psycopg_dict_row()) as cur:
            cur.execute(sql)
            atos = cur.fetchall()

        if not atos:
            logger.info("Nenhum ato pendente de download de PDF.")
            return

        logger.info(f"Processando {len(atos)} atos...")

        for ato in tqdm(atos, desc="download PDFs"):
            try:
                resp = fetch(ato["link"])
                pdf_url = descobrir_pdf_url(resp.text, ato["link"])

                if not pdf_url:
                    with conn.cursor() as cur2:
                        cur2.execute("UPDATE atos SET pdf_disponivel = FALSE WHERE id = %s", (ato["id"],))
                    conn.commit()
                    continue

                pdf_resp = fetch(pdf_url)
                pdf_path = PDF_DIR / slugify_filename(ato)
                pdf_path.write_bytes(pdf_resp.content)

                with conn.cursor() as cur2:
                    cur2.execute("UPDATE atos SET pdf_disponivel = TRUE, metadata = jsonb_set(COALESCE(metadata, '{}'::jsonb), '{pdf_path}', to_jsonb(%s::text)) WHERE id = %s", (str(pdf_path), ato["id"]))
                conn.commit()

            except Exception as e:
                logger.error(f"erro ato_id={ato['id']} {ato['link']}: {e}")
                conn.rollback()
                continue

    logger.success("FIM download_pdf.")


def psycopg_dict_row():
    """Retorna factory de row dict (compatível psycopg 3)."""
    from psycopg.rows import dict_row
    return dict_row


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    parser.add_argument("--redo", action="store_true")
    args = parser.parse_args()
    processar(redo=args.redo, limit=args.limit)
