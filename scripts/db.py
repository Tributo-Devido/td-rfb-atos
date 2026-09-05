"""
db.py — Conexão Postgres + helpers de cursor para todos os scripts.

A partir de 2026-05-11, a base canônica é a NUVEM (ratio.rfb_atos.*), não mais
o Docker local. O Docker local segue funcional como histórico/legado mas o
ponto-de-verdade do produto é a nuvem.

Resolução de DSN:
    1. env var RFB_ATOS_DSN explícita → usa esse.
    2. Se --local CLI flag estiver no sys.argv → usa Docker local
       (postgresql://td:td@localhost:5435/td_rfb_atos).
    3. Default: lê DSN da nuvem de C:\\Users\\tribu\\.claude-tg-bot\\ratio-pg-dsn.txt.

Schema:
    - Nuvem: `rfb_atos.*` (set search_path automaticamente).
    - Local: `public.*` (default).

Uso:
    from db import get_conn, get_legacy_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

import psycopg
from dotenv import load_dotenv

try:
    from pgvector.psycopg import register_vector
except ImportError:
    register_vector = None  # type: ignore

# Carrega .env do diretório do script
ENV_PATH = Path(__file__).resolve().parent / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)


DOCKER_LOCAL_DSN = "postgresql://td:td@localhost:5435/td_rfb_atos"
CLOUD_DSN_FILE = Path(r"C:\Users\tribu\.claude-tg-bot\ratio-pg-dsn.txt")


def _resolve_dsn() -> tuple[str, str]:
    """Returns (dsn, mode) where mode is 'cloud' or 'local'."""
    # 1. Env var explícita
    dsn = os.environ.get("RFB_ATOS_DSN")
    if dsn:
        mode = "local" if "5435" in dsn else "cloud"
        return dsn, mode
    # 2. CLI flag --local
    if "--local" in sys.argv:
        return DOCKER_LOCAL_DSN, "local"
    # 3. Default: nuvem
    if CLOUD_DSN_FILE.exists():
        return CLOUD_DSN_FILE.read_text(encoding="utf-8").strip(), "cloud"
    # 4. Fallback: legacy .env PG_DSN
    legacy = os.environ.get("PG_DSN")
    if legacy:
        mode = "local" if "5435" in legacy else "cloud"
        return legacy, mode
    sys.exit("[erro] Sem DSN: defina RFB_ATOS_DSN, use --local, ou crie ratio-pg-dsn.txt")


def get_conn() -> psycopg.Connection:
    """Conexão ao banco canônico (nuvem por default; --local pra Docker)."""
    dsn, mode = _resolve_dsn()
    conn = psycopg.connect(dsn)
    # Schema search_path: nuvem usa rfb_atos.*, local usa public.*
    if mode == "cloud":
        with conn.cursor() as cur:
            cur.execute("SET search_path TO rfb_atos, public")
        conn.commit()
    if register_vector is not None:
        try:
            register_vector(conn)
        except Exception:
            # pgvector pode não estar instalado se a migração ainda não rodou
            pass
    return conn


def get_local_conn() -> psycopg.Connection:
    """Conexão explícita ao Docker local (td_rfb_atos:5435), independente de RFB_ATOS_DSN."""
    conn = psycopg.connect(DOCKER_LOCAL_DSN)
    if register_vector is not None:
        try:
            register_vector(conn)
        except Exception:
            pass
    return conn


def get_legacy_conn() -> psycopg.Connection:
    """[DEPRECATED] Conexão ao Postgres legado (tais em db.tdax.com.br).

    Mantido por compatibilidade; rfb_atos agora vive em ratio (nuvem).
    """
    dsn = os.environ.get("PG_DSN_LEGACY")
    if not dsn:
        sys.exit("[erro] PG_DSN_LEGACY não definido (deprecated — use get_conn() para a nuvem)")
    return psycopg.connect(dsn)


@contextmanager
def cursor():
    """Context manager: with cursor() as cur: ..."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            yield cur
            conn.commit()


if __name__ == "__main__":
    # Smoke test
    dsn, mode = _resolve_dsn()
    print(f"DSN resolvido: modo={mode}")
    print("             : (DSN não exibido por segurança)")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_schema, version()")
            db, schema, ver = cur.fetchone()
            print(f"  Database: {db}")
            print(f"  Schema:   {schema}")
            print(f"  Version:  {ver.split(',')[0]}")
            if mode == "cloud":
                cur.execute("SELECT COUNT(*) FROM rfb_atos.ato")
                n = cur.fetchone()[0]
                print(f"  rfb_atos.ato count: {n}")
            else:
                cur.execute("SELECT COUNT(*) FROM atos")
                n = cur.fetchone()[0]
                print(f"  atos count: {n}")
