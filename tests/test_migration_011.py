"""Migration 011 contra Postgres real: aditiva, idempotente e com as garantias que promete.

Só roda com PGTEST_DSN (banco descartável): os testes derrubam os schemas rfb_atos e
rfb_atos_staging. Nunca aponte para a nuvem.
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest

RAIZ = Path(__file__).resolve().parent.parent
DSN = os.environ.get("PGTEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="defina PGTEST_DSN (Postgres descartável)")

ARQUIVOS = (
    "tests/fixtures/schema_rfb_atos.sql",
    "tests/fixtures/seed_cenarios.sql",
    "migrations/010_ato_coleta.sql",
    "migrations/011_recoleta.sql",
)


def _limpar(conn) -> None:
    conn.execute("DROP SCHEMA IF EXISTS rfb_atos_staging CASCADE")
    conn.execute("DROP SCHEMA IF EXISTS rfb_atos CASCADE")


@pytest.fixture
def db():
    with psycopg.connect(DSN, autocommit=True) as conn:
        _limpar(conn)
        for arquivo in ARQUIVOS:
            conn.execute((RAIZ / arquivo).read_text(encoding="utf-8"))
        conn.execute("SET search_path = rfb_atos, public")
        yield conn
        _limpar(conn)


def _um_ato(db) -> int:
    return db.execute("SELECT id FROM rfb_atos.ato ORDER BY id LIMIT 1").fetchone()[0]


def _colunas(db, schema: str, tabela: str) -> list[tuple[str, str]]:
    return db.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
        (schema, tabela)).fetchall()


def test_011_e_idempotente(db):
    db.execute((RAIZ / "migrations/011_recoleta.sql").read_text(encoding="utf-8"))
    db.execute((RAIZ / "migrations/010_ato_coleta.sql").read_text(encoding="utf-8"))


def test_relacao_externa_nao_duplica(db):
    ato = _um_ato(db)
    sql = ("INSERT INTO rfb_atos.ato_relacao (ato_origem_id, destino_id_portal, destino_externo, "
           "tipo_relacao, fonte) VALUES (%s, 999, 'IN SRF 457/2004', 'interrompe', 'teste')")
    db.execute(sql, (ato,))
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute(sql, (ato,))


def test_relacao_externa_com_outro_destino_ou_tipo_convive(db):
    ato = _um_ato(db)
    sql = ("INSERT INTO rfb_atos.ato_relacao (ato_origem_id, destino_id_portal, tipo_relacao) "
           "VALUES (%s, %s, %s)")
    db.execute(sql, (ato, 1, "interrompe"))
    db.execute(sql, (ato, 2, "interrompe"))
    db.execute(sql, (ato, 1, "altera"))
    n = db.execute("SELECT count(*) FROM rfb_atos.ato_relacao WHERE ato_origem_id = %s",
                   (ato,)).fetchone()[0]
    assert n == 3


def test_base_analise_default_texto_e_vocabulario_fechado(db):
    ato = _um_ato(db)
    mid = db.execute("INSERT INTO rfb_atos.ato_materia (ato_id, ordem) VALUES (%s, 99) "
                     "RETURNING id", (ato,)).fetchone()[0]
    assert db.execute("SELECT base_analise FROM rfb_atos.ato_materia WHERE id = %s",
                      (mid,)).fetchone()[0] == "texto"
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("UPDATE rfb_atos.ato_materia SET base_analise = 'pdf' WHERE id = %s", (mid,))


def test_texto_visao_guarda_fotos_e_recusa_visao_vigente(db):
    ato = _um_ato(db)
    sql = ("INSERT INTO rfb_atos.ato_texto_visao (ato_id, visao, sha256, texto) "
           "VALUES (%s, %s, %s, 'x')")
    db.execute(sql, (ato, "original", "h1"))
    db.execute(sql, (ato, "original", "h2"))       # outra foto da mesma visão
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(sql, (ato, "vigente", "h3"))    # a vigente mora em ato_content


def test_staging_espelha_as_tabelas_principais(db):
    for tabela in ("ato", "ato_content", "ato_texto_visao", "ato_relacao", "ato_segmento",
                   "ato_alteracao_historico"):
        assert _colunas(db, "rfb_atos_staging", tabela) == _colunas(db, "rfb_atos", tabela), tabela
    assert ("situacao_portal", "jsonb") in _colunas(db, "rfb_atos_staging", "ato")


def test_log_de_mudanca_aceita_registro(db):
    db.execute("INSERT INTO rfb_atos.ato_mudanca (run_id, ato_id, tabela, campo, antes, depois) "
               "VALUES ('t', 1, 'ato', 'analise_completa', 'true', 'false')")
    assert db.execute("SELECT count(*) FROM rfb_atos.ato_mudanca").fetchone()[0] == 1


def test_011_nao_altera_colunas_que_ja_existiam(db):
    antes = {"ato_content": ["ato_id", "texto_completo", "origem_extracao", "processed_at",
                             "fonte_extracao", "caracteres", "paginas", "tipo_versao",
                             "id_arquivo_binario", "pdf_path"]}
    atual = [c for c, _ in _colunas(db, "rfb_atos", "ato_content")]
    assert atual == antes["ato_content"]
