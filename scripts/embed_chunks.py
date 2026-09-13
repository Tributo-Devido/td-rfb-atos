"""
embed_chunks.py — Geração de embeddings via Gemini text-embedding-004 (1024-dim).

Espelha o pattern do td-carf (mesma família de embedding → busca cross-base viável).

Para cada ato_materia sem embedding, gera embedding consolidado da matéria.
Para cada ato com content, cria ato_chunks (chunks de ~1000 tokens) com embedding + tsvector.

Uso:
    py embed_chunks.py                # processa todos pendentes
    py embed_chunks.py --limit 100
    py embed_chunks.py --only-materias  # só matérias (skip chunks)
    py embed_chunks.py --only-chunks    # só chunks (skip matérias)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Iterable

import google.generativeai as genai
from loguru import logger
from psycopg.rows import dict_row
from tenacity import retry, stop_after_attempt, wait_exponential
from tqdm import tqdm

from db import get_conn

EMBED_MODEL = "models/gemini-embedding-001"
EMBED_DIMS = 1024
CHUNK_MAX_CHARS = 4000  # ~1000 tokens

if os.environ.get("GOOGLE_API_KEY"):
    genai.configure(api_key=os.environ["GOOGLE_API_KEY"])


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def embed_one(texto: str, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float]:
    result = genai.embed_content(
        model=EMBED_MODEL,
        content=texto,
        task_type=task_type,
        output_dimensionality=EMBED_DIMS,
    )
    return result["embedding"]


def embedar_materias(conn, limit: int | None):
    sql = """
        SELECT m.id, m.tema_macro, m.tema_especifico, m.tags,
               m.ementa_trecho, m.solucao, m.fato_consultado,
               m.tese_adotada, m.fundamentacao_resumo, a.ementa
        FROM ato_materia m
        JOIN atos a ON a.id = m.ato_id
        WHERE m.embedding IS NULL
        ORDER BY m.id
    """
    if limit:
        sql += f" LIMIT {limit}"

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql)
        materias = cur.fetchall()

    if not materias:
        logger.info("Nenhuma materia pendente de embedding.")
        return

    logger.info(f"Embedando {len(materias)} matérias...")

    for m in tqdm(materias, desc="materias"):
        # Construir texto a embeddar: tema + tags + ementa_trecho + solucao + tese_adotada
        partes = [
            f"[{m['tema_macro']} / {m['tema_especifico']}]",
        ]
        if m["tags"]:
            partes.append("Tags: " + ", ".join(m["tags"]))
        if m["ementa_trecho"]:
            partes.append(m["ementa_trecho"])
        if m["fato_consultado"]:
            partes.append("Fato: " + m["fato_consultado"])
        if m["solucao"]:
            partes.append("Solução: " + m["solucao"])
        if m["tese_adotada"]:
            partes.append("Tese: " + m["tese_adotada"])
        if m["fundamentacao_resumo"]:
            partes.append("Fundamentação: " + m["fundamentacao_resumo"])
        if not partes[1:]:  # só tema, sem conteúdo
            partes.append(m["ementa"] or "")

        texto = "\n".join(p for p in partes if p)
        try:
            emb = embed_one(texto)
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ato_materia SET embedding = %s, embedding_source = %s, embedded_at = now() WHERE id = %s",
                    (emb, texto[:500], m["id"]),
                )
            conn.commit()
        except Exception as e:
            logger.error(f"erro materia_id={m['id']}: {e}")
            conn.rollback()
            continue

    logger.success("FIM embedding materias.")


def chunkar_texto(texto: str, max_chars: int = CHUNK_MAX_CHARS) -> list[str]:
    """Chunker simples por parágrafo. Mantém parágrafos contíguos até max_chars."""
    paragrafos = [p.strip() for p in texto.split("\n\n") if p.strip()]
    chunks, atual = [], ""
    for p in paragrafos:
        if len(atual) + len(p) + 2 > max_chars and atual:
            chunks.append(atual.strip())
            atual = p
        else:
            atual = (atual + "\n\n" + p) if atual else p
    if atual:
        chunks.append(atual.strip())
    return chunks


def embedar_chunks(conn, limit: int | None):
    """Para cada ato com content e sem chunks, cria ato_chunks."""
    sql = """
        SELECT a.id, a.tipo_ato, a.tema_macros, a.ementa, c.content,
               a.metadata_jsonb_for_chunk
        FROM (
          SELECT a.id, a.tipo_ato, a.ementa,
                 ARRAY_AGG(DISTINCT m.tema_macro) FILTER (WHERE m.tema_macro IS NOT NULL) AS tema_macros,
                 jsonb_build_object(
                   'tipo_ato', a.tipo_ato,
                   'orgao_emissor', a.orgao_emissor,
                   'eficacia', a.eficacia,
                   'status_vigencia', a.status_vigencia,
                   'data_publicacao', a.data_publicacao::text,
                   'temas_macro', ARRAY_AGG(DISTINCT m.tema_macro) FILTER (WHERE m.tema_macro IS NOT NULL),
                   'temas_especificos', ARRAY_AGG(DISTINCT m.tema_especifico) FILTER (WHERE m.tema_especifico IS NOT NULL),
                   'tributos', (
                     SELECT ARRAY_AGG(DISTINCT mt.tributo_codigo)
                     FROM ato_materia mm JOIN materia_tributo mt ON mt.materia_id = mm.id
                     WHERE mm.ato_id = a.id
                   )
                 ) AS metadata_jsonb_for_chunk
          FROM atos a
          LEFT JOIN ato_materia m ON m.ato_id = a.id
          WHERE a.content_disponivel = TRUE
            AND NOT EXISTS (SELECT 1 FROM ato_chunks ch WHERE ch.ato_id = a.id)
          GROUP BY a.id, a.tipo_ato, a.orgao_emissor, a.eficacia, a.status_vigencia, a.data_publicacao, a.ementa
        ) a
        JOIN ato_content c ON c.ato_id = a.id
        ORDER BY a.id
    """
    if limit:
        sql += f" LIMIT {limit}"

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql)
        atos = cur.fetchall()

    if not atos:
        logger.info("Nenhum ato pendente de chunking.")
        return

    logger.info(f"Chunkando + embedando {len(atos)} atos...")

    for a in tqdm(atos, desc="chunks"):
        chunks = chunkar_texto(a["content"])
        # Adiciona ementa como chunk[0] sempre
        chunks = [a["ementa"]] + chunks if a["ementa"] else chunks
        metadata = a["metadata_jsonb_for_chunk"] or {}

        for seq, chunk_texto in enumerate(chunks):
            if not chunk_texto or len(chunk_texto) < 30:
                continue
            try:
                emb = embed_one(chunk_texto)
                tipo_chunk = "ementa" if seq == 0 else "body_section"
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO ato_chunks (ato_id, tipo_chunk, seq, texto, caracteres, embedding, tsvector_pt, metadata)
                        VALUES (%s, %s, %s, %s, %s, %s, to_tsvector('portuguese', %s), %s)
                        ON CONFLICT (ato_id, tipo_chunk, seq) DO NOTHING
                        """,
                        (
                            a["id"], tipo_chunk, seq, chunk_texto, len(chunk_texto),
                            emb, chunk_texto, json.dumps(metadata),
                        ),
                    )
                conn.commit()
            except Exception as e:
                logger.error(f"erro ato_id={a['id']} chunk={seq}: {e}")
                conn.rollback()
                continue

    logger.success("FIM embedding chunks.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    parser.add_argument("--only-materias", action="store_true")
    parser.add_argument("--only-chunks", action="store_true")
    args = parser.parse_args()

    if not os.environ.get("GOOGLE_API_KEY"):
        sys.exit("[erro] GOOGLE_API_KEY não definido em .env")

    with get_conn() as conn:
        if not args.only_chunks:
            embedar_materias(conn, args.limit)
        if not args.only_materias:
            embedar_chunks(conn, args.limit)


if __name__ == "__main__":
    main()
