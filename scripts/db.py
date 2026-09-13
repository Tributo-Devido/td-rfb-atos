"""
db.py — Conexão Postgres + helpers de cursor para todos os scripts.

A base canônica é a NUVEM (`ratio.rfb_atos.*`). O Docker local segue como legado.

Resolução de DSN:
    1. env var `RFB_ATOS_DSN` explícita → usa esse (testes e CI).
    2. `--local` no sys.argv → Docker local (postgresql://td:td@localhost:5435/td_rfb_atos).
    3. Default: `credenciais.resolver_dsn(...)` — variável de ambiente → SSM, nunca arquivo:
         get_conn()              → leitura (`ratio_leitura`, /td/db/ratio-pg-dsn)
         get_conn(escrita=True)  → escrita (`rfb_writer`, /td/batch/rfb-writer-dsn)
       (até 13/09/2026 o default lia ~/.claude-tg-bot/ratio-pg-dsn.txt, com DSN de admin.)

Schema:
    - Nuvem: `rfb_atos.*` (search_path ajustado automaticamente).
    - Local: `public.*` (default).

Uso:
    from db import get_conn

    with get_conn() as conn:                 # leitura
        ...
    with get_conn(escrita=True) as conn:     # coleta / carga
        ...
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from credenciais import CredencialAusente, resolver_dsn

try:
    from pgvector.psycopg import register_vector
except ImportError:
    register_vector = None  # type: ignore

# Carrega .env do diretório do script
ENV_PATH = Path(__file__).resolve().parent / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)


DOCKER_LOCAL_DSN = "postgresql://td:td@localhost:5435/td_rfb_atos"


def _resolve_dsn(escrita: bool = False) -> tuple[str, str]:
    """Returns (dsn, mode) where mode is 'cloud' or 'local'."""
    # 1. Env var explícita
    dsn = os.environ.get("RFB_ATOS_DSN")
    if dsn:
        mode = "local" if "5435" in dsn else "cloud"
        return dsn, mode
    # 2. CLI flag --local
    if "--local" in sys.argv:
        return DOCKER_LOCAL_DSN, "local"
    # 3. Default: nuvem, pela credencial do papel pedido
    try:
        return resolver_dsn("escrita" if escrita else "leitura"), "cloud"
    except CredencialAusente as e:
        sys.exit(f"[erro] {e} Ou defina RFB_ATOS_DSN, ou use --local para o Docker legado.")


def get_conn(escrita: bool = False) -> psycopg.Connection:
    """Conexão ao banco canônico: leitura por padrão; `escrita=True` para coleta e carga."""
    dsn, mode = _resolve_dsn(escrita)
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
def cursor(escrita: bool = False):
    """Context manager: with cursor() as cur: ..."""
    with get_conn(escrita) as conn, conn.cursor() as cur:
        yield cur
        conn.commit()


if __name__ == "__main__":
    # Smoke test (leitura)
    dsn, mode = _resolve_dsn()
    print(f"DSN resolvido: modo={mode}")
    print("             : (DSN não exibido por segurança)")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT current_database(), current_user, version()")
        db, usuario, ver = cur.fetchone()
        print(f"  Database: {db}")
        print(f"  Usuário:  {usuario}")
        print(f"  Version:  {ver.split(',')[0]}")
        if mode == "cloud":
            cur.execute("SELECT COUNT(*) FROM rfb_atos.ato")
            n = cur.fetchone()[0]
            print(f"  rfb_atos.ato count: {n}")
        else:
            cur.execute("SELECT COUNT(*) FROM atos")
            n = cur.fetchone()[0]
            print(f"  atos count: {n}")
