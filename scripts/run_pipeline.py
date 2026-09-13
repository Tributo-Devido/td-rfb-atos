"""
run_pipeline.py — Orquestra o pipeline diário: crawl -> download_pdf -> extract_content -> categorize -> embed.

Use este script para schedule diário. Cada etapa é incremental (só processa pendentes).

Uso:
    py run_pipeline.py                    # pipeline completo (incremental)
    py run_pipeline.py --skip crawl       # pula etapa
    py run_pipeline.py --only categorize  # roda só uma etapa
    py run_pipeline.py --limit 50         # limita por etapa
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from loguru import logger

SCRIPTS_DIR = Path(__file__).resolve().parent
ETAPAS = ["crawl", "download_pdf", "extract_content", "categorize", "embed"]

ETAPA_TO_SCRIPT = {
    "crawl": "extract_sijut.py",
    "download_pdf": "download_pdf.py",
    "extract_content": "extract_content.py",
    "categorize": "categorize_with_llm.py",
    "embed": "embed_chunks.py",
}


def rodar(script: str, extra_args: list[str]):
    cmd = [sys.executable, str(SCRIPTS_DIR / script)] + extra_args
    logger.info("$ " + " ".join(cmd))
    rc = subprocess.call(cmd)
    if rc != 0:
        logger.error(f"{script} retornou {rc}")
        return False
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip", nargs="*", default=[], choices=ETAPAS)
    parser.add_argument("--only", choices=ETAPAS)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    extra_args = []
    if args.limit:
        extra_args = ["--limit", str(args.limit)]

    etapas = [args.only] if args.only else [e for e in ETAPAS if e not in args.skip]

    for etapa in etapas:
        script = ETAPA_TO_SCRIPT[etapa]
        logger.info(f"=== ETAPA: {etapa} ({script}) ===")
        ok = rodar(script, extra_args if etapa != "crawl" else [])
        if not ok:
            logger.error(f"FALHA em {etapa}. Abortando.")
            sys.exit(1)

    logger.success("Pipeline completo.")


if __name__ == "__main__":
    main()
