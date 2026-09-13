"""
migrate_segmento_fast.py — ETL rápido para ato_segmento usando COPY.

Retoma de onde parou: usa MAX(ato_id) da nuvem como ponto de partida.
Usa copy_records_to_table para throughput ~10x vs executemany.

Uso:
    python migrate_segmento_fast.py
    python migrate_segmento_fast.py --from-ato-id 42907  # override
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

DOCKER_DSN = "postgresql://td:<<PG_PASSWORD>>@localhost:5435/td_rfb_atos"
CLOUD_DSN_PATH = Path(r"C:\Users\tribu\.claude-tg-bot\ratio-pg-dsn.txt")
if not CLOUD_DSN_PATH.exists():
    sys.exit(f"[erro] DSN nuvem não encontrado: {CLOUD_DSN_PATH}")
CLOUD_DSN = CLOUD_DSN_PATH.read_text(encoding="utf-8").strip()

FETCH_BATCH = 5000   # tamanho do cursor server-side
COPY_BATCH  = 10000  # linhas por COPY
COMMIT_EVERY = 50000


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def get_resume_point(dst_conn) -> int:
    """Retorna o maior ato_id já migrado para nuvem."""
    with dst_conn.cursor() as c:
        c.execute("SELECT COALESCE(MAX(ato_id), 0) FROM rfb_atos.ato_segmento")
        return c.fetchone()[0]


def run(from_ato_id: int | None = None):
    log(f"Conectando Docker: {DOCKER_DSN}")
    src = psycopg.connect(DOCKER_DSN)
    log("Conectando Nuvem (ratio)")
    dst = psycopg.connect(CLOUD_DSN)

    if from_ato_id is None:
        from_ato_id = get_resume_point(dst)
    log(f"Retomando a partir de ato_id > {from_ato_id}")

    # Count total to migrate
    with src.cursor() as c:
        c.execute("SELECT COUNT(*) FROM ato_segmento WHERE ato_id > %s", (from_ato_id,))
        total_pending = c.fetchone()[0]
    log(f"Pendentes no Docker: {total_pending}")

    # Use server-side named cursor to stream from Docker
    cols = [
        "ato_id", "id_segmento", "versao_segmento", "ordem",
        "id_tipo_segmento", "id_assunto", "texto_integra",
        "is_original", "is_compilado", "is_tachado", "is_omitido",
        "is_agendado", "raw", "created_at",
    ]

    cloud_cols = (
        "ato_id", "id_segmento", "versao_segmento", "ordem",
        "id_tipo_segmento", "id_assunto", "texto_integra",
        "is_original", "is_compilado", "is_tachado", "is_omitido",
        "is_agendado", "raw", "criado_em",
    )

    total = 0
    since_commit = 0
    t0 = time.time()

    with src.cursor(name="seg_fast_cur") as s:
        s.itersize = FETCH_BATCH
        s.execute(
            """
            SELECT ato_id, id_segmento, versao_segmento, ordem,
                   id_tipo_segmento, id_assunto, texto_integra,
                   is_original, is_compilado, is_tachado, is_omitido,
                   is_agendado, raw, created_at
              FROM ato_segmento
             WHERE ato_id > %s
             ORDER BY ato_id, id_segmento, versao_segmento
            """,
            (from_ato_id,),
        )

        copy_buf = []

        def flush_copy():
            if not copy_buf:
                return
            with dst.cursor() as d:
                with d.copy(
                    """
                    COPY rfb_atos.ato_segmento (
                        ato_id, id_segmento, versao_segmento, ordem,
                        id_tipo_segmento, id_assunto, texto_integra,
                        is_original, is_compilado, is_tachado, is_omitido,
                        is_agendado, raw, criado_em
                    ) FROM STDIN
                    """
                ) as cp:
                    for row in copy_buf:
                        cp.write_row(row)
            copy_buf.clear()

        while True:
            rows = s.fetchmany(FETCH_BATCH)
            if not rows:
                break

            for row in rows:
                row_list = list(row)
                # raw (index 12) is jsonb dict — serialize to JSON string for COPY
                if row_list[12] is not None and isinstance(row_list[12], dict):
                    row_list[12] = json.dumps(row_list[12], ensure_ascii=False)
                copy_buf.append(tuple(row_list))

            total += len(rows)
            since_commit += len(rows)

            if len(copy_buf) >= COPY_BATCH:
                flush_copy()

            if since_commit >= COMMIT_EVERY:
                dst.commit()
                since_commit = 0
                elapsed = time.time() - t0
                rate = total / elapsed if elapsed > 0 else 0
                eta = (total_pending - total) / rate if rate > 0 else 0
                log(f"  ato_segmento: {total}/{total_pending} ({rate:.0f} rows/s, ETA {eta/60:.1f} min)")

        # flush remainder
        flush_copy()
        dst.commit()

    elapsed = time.time() - t0
    rate = total / elapsed if elapsed > 0 else 0
    log(f"  ato_segmento DONE: {total} linhas em {elapsed/60:.1f} min ({rate:.0f} rows/s)")

    # Final count
    with dst.cursor() as d:
        d.execute("SELECT COUNT(*) FROM rfb_atos.ato_segmento")
        cloud_total = d.fetchone()[0]
    log(f"  Cloud total: {cloud_total}")

    src.close()
    dst.close()
    return cloud_total


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--from-ato-id", type=int, default=None)
    args = p.parse_args()
    run(from_ato_id=args.from_ato_id)


if __name__ == "__main__":
    main()
