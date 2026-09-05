"""
reembed_cloud.py — Re-embed ato_materia da nuvem com OpenAI 3072.

Track A.3 da migração ratio-juris (2026-05-11).

Decisão de escopo (vide .planning/track-a-desvio.md):
    Re-embed alvo é `rfb_atos.ato_materia.embedding`, NÃO `ato_chunk.embedding`.
    Motivo: `ato_chunks` no Docker está vazio (0 linhas); todos os embeddings
    úteis vivem em `ato_materia.embedding` (41.164 vetores gemini 1024-dim).

Fonte do texto pra embedding (concatenado e truncado em ~6k chars):
    1. ementa_trecho (sempre presente: 41.159 de 41.164)
    2. + " || " + fato_consultado (presente em 20.388)
    3. + " || " + solucao (se tiver)

Modelo: text-embedding-3-large, dim 3072, halfvec.
Concorrência: 10 workers, batch 50.
Filtro: WHERE embedding IS NULL — idempotente / reentrante.

Uso:
    python reembed_cloud.py                  # roda tudo pendente
    python reembed_cloud.py --limit 100      # smoke test
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

import psycopg

# Add lib path
SCRIPTS_LIB = Path(r"c:/td-skills/td-creditos/scripts").resolve()
sys.path.insert(0, str(SCRIPTS_LIB))
from lib.embed_openai import embed_texts, vector_literal


CLOUD_DSN_PATH = Path(r"C:\Users\tribu\.claude-tg-bot\ratio-pg-dsn.txt")
CLOUD_DSN = CLOUD_DSN_PATH.read_text(encoding="utf-8").strip()

BATCH = 50           # textos por chamada OpenAI
CONCURRENCY = 10     # chamadas concorrentes
COMMIT_EVERY = 500   # commit a cada N matérias


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def build_text(ementa_trecho, fato_consultado, solucao, tema_macro, tema_especifico):
    """Concatena campos relevantes da matéria pra embedding, max ~6000 chars."""
    parts = []
    if tema_macro or tema_especifico:
        parts.append(f"[{tema_macro or ''} / {tema_especifico or ''}]")
    if ementa_trecho:
        parts.append(ementa_trecho.strip())
    if fato_consultado:
        parts.append("FATO: " + fato_consultado.strip())
    if solucao:
        parts.append("SOLUCAO: " + solucao.strip())
    txt = " || ".join(p for p in parts if p)
    if len(txt) > 6000:
        txt = txt[:6000]
    return txt or "(materia sem texto)"


async def reembed_chunk(rows: list[tuple], dst_dsn: str) -> int:
    """Embeda um lote e UPDATE na nuvem. rows = [(id, text), ...]."""
    ids = [r[0] for r in rows]
    texts = [r[1] for r in rows]
    vecs = await embed_texts(texts, batch_size=BATCH, concurrency=CONCURRENCY)
    # UPDATE em lote — usa executemany para reduzir round-trips no tunnel
    params = [
        (vector_literal(vec), "openai_text_embedding_3_large_3072", "text-embedding-3-large", mid)
        # strict=True: se embed_texts devolver menos vetores que textos, o zip
        # truncaria em silencio e as materias do fim do lote ficariam sem
        # embedding sem erro nenhum -- some da busca semantica e nada avisa.
        for mid, vec in zip(ids, vecs, strict=True)
    ]
    with psycopg.connect(dst_dsn) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                UPDATE rfb_atos.ato_materia
                   SET embedding = %s::halfvec,
                       embedded_at = now(),
                       embedding_source = %s,
                       llm_model = %s
                 WHERE id = %s
                """,
                params,
            )
        conn.commit()
    return len(rows)


async def main_async(limit: int | None):
    log("Conectando nuvem")
    conn = psycopg.connect(CLOUD_DSN)

    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM rfb_atos.ato_materia
             WHERE embedding IS NULL
        """)
        pending = cur.fetchone()[0]
        log(f"Materias pendentes de embedding: {pending}")
        if pending == 0:
            log("Nada a fazer.")
            return

        # Pega todas as matérias pendentes
        sql = """
            SELECT id, ementa_trecho, fato_consultado, solucao,
                   tema_macro, tema_especifico
              FROM rfb_atos.ato_materia
             WHERE embedding IS NULL
             ORDER BY id
        """
        if limit:
            sql += f"\n LIMIT {limit}"
        cur.execute(sql)
        rows_raw = cur.fetchall()

    conn.close()

    # Constroi textos
    work = []
    for (mid, et, fc, sol, tm, te) in rows_raw:
        txt = build_text(et, fc, sol, tm, te)
        work.append((mid, txt))

    log(f"Iniciando embedding de {len(work)} materias (batch={BATCH}, concurrency={CONCURRENCY})")
    t0 = time.time()
    done = 0
    # Particiona em macro-lotes de COMMIT_EVERY (= múltiplos de BATCH*CONCURRENCY)
    macro_size = BATCH * CONCURRENCY  # ~500
    for i in range(0, len(work), macro_size):
        macro = work[i:i+macro_size]
        n = await reembed_chunk(macro, CLOUD_DSN)
        done += n
        elapsed = time.time() - t0
        rate = done / elapsed if elapsed > 0 else 0
        eta = (len(work) - done) / rate if rate > 0 else 0
        log(f"  {done}/{len(work)} ({rate:.0f} mat/s, ETA {eta/60:.1f} min)")

    log(f"OK. Reembed completo em {(time.time()-t0)/60:.1f} min.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None, help="smoke test: só N materias")
    args = p.parse_args()
    asyncio.run(main_async(args.limit))


if __name__ == "__main__":
    main()
