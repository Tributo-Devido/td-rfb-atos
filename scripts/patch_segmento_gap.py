"""
patch_segmento_gap.py — Insere linhas faltantes de ato_segmento.

Compara Docker vs Cloud para ato_id específico e insere as diferenças.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

DOCKER_DSN = "postgresql://td:<<PG_PASSWORD>>@localhost:5435/td_rfb_atos"
CLOUD_DSN_PATH = Path(r"C:\Users\tribu\.claude-tg-bot\ratio-pg-dsn.txt")
CLOUD_DSN = CLOUD_DSN_PATH.read_text(encoding="utf-8").strip()


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    log("Conectando...")
    src = psycopg.connect(DOCKER_DSN)
    dst = psycopg.connect(CLOUD_DSN)

    # Find ato_ids with gap
    with src.cursor() as s, dst.cursor() as d:
        # Get all Docker ato_ids that might be incomplete in cloud
        # Check ato_id = 42907 specifically (the boundary)
        s.execute("""
            SELECT ato_id, id_segmento, versao_segmento, ordem,
                   id_tipo_segmento, id_assunto, texto_integra,
                   is_original, is_compilado, is_tachado, is_omitido,
                   is_agendado, raw, created_at
              FROM ato_segmento
             WHERE ato_id = 42907
        """)
        docker_rows = s.fetchall()
        log(f"Docker ato_id=42907: {len(docker_rows)} rows")

        d.execute("SELECT COUNT(*) FROM rfb_atos.ato_segmento WHERE ato_id = 42907")
        cloud_count = d.fetchone()[0]
        log(f"Cloud ato_id=42907: {cloud_count} rows")

        if len(docker_rows) == cloud_count:
            log("Already in sync for ato_id=42907!")
        else:
            batch = []
            for row in docker_rows:
                row_list = list(row)
                if row_list[12] is not None and isinstance(row_list[12], dict):
                    row_list[12] = Jsonb(row_list[12])
                batch.append(tuple(row_list))

            d.executemany(
                """
                INSERT INTO rfb_atos.ato_segmento (
                    ato_id, id_segmento, versao_segmento, ordem,
                    id_tipo_segmento, id_assunto, texto_integra,
                    is_original, is_compilado, is_tachado, is_omitido,
                    is_agendado, raw, criado_em
                )
                VALUES (%s,%s,%s,%s, %s,%s,%s, %s,%s,%s,%s, %s,%s,%s)
                ON CONFLICT (ato_id, id_segmento, versao_segmento) DO NOTHING
                """,
                batch,
            )
            dst.commit()
            log(f"Inserted {len(batch)} rows (ON CONFLICT DO NOTHING)")

        # Final count
        d.execute("SELECT COUNT(*) FROM rfb_atos.ato_segmento")
        total = d.fetchone()[0]
        log(f"Cloud total ato_segmento: {total}")

    src.close()
    dst.close()


if __name__ == "__main__":
    main()
