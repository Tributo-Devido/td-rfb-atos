"""Carregador de ato pelas visões do portal e reenfileiramento dos 912 — contra Postgres real.

Só roda com PGTEST_DSN (banco descartável): derruba os schemas rfb_atos e rfb_atos_staging.

O ato "analisado sem texto" é criado ANTES da migration 010, como aconteceu na nuvem: a trava
`ato_analise_exige_conteudo` entra NOT VALID e recusa esse estado em gravação nova.
Ids explícitos altos (900001+): o seed_cenarios.sql usa ids baixos explícitos, e a sequência
da identidade não sabe disso.
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest

import carregar_ato_portal as cap
import reenfileirar_sem_texto as rst
from test_visoes_portal import ORIGINAL, VIGENTE

RAIZ = Path(__file__).resolve().parent.parent
DSN = os.environ.get("PGTEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="defina PGTEST_DSN (Postgres descartável)")
ALVO, MODIFICADOR = 900001, 900002


def _limpar(conn) -> None:
    conn.execute("DROP SCHEMA IF EXISTS rfb_atos_staging CASCADE")
    conn.execute("DROP SCHEMA IF EXISTS rfb_atos CASCADE")


def _executar(conn, arquivo: str) -> None:
    conn.execute((RAIZ / arquivo).read_text(encoding="utf-8"))


@pytest.fixture
def conn():
    """Base com o ato alvo 'analisado sem texto' (id_portal 555) e o modificador (777)."""
    with psycopg.connect(DSN, autocommit=True) as c:
        _limpar(c)
        _executar(c, "tests/fixtures/schema_rfb_atos.sql")
        _executar(c, "tests/fixtures/seed_cenarios.sql")
        c.execute(
            "INSERT INTO rfb_atos.ato (id, tipo_ato, numero, ano, emissor, data_publicacao, "
            "id_portal, status_vigencia, content_disponivel, analise_completa) VALUES "
            "(%s, 'INSTRUCAO_NORMATIVA', '9999', 2022, 'RFB', '2022-12-20', 555, "
            "'nao_disponivel_portal', false, true)", (ALVO,))
        c.execute(
            "INSERT INTO rfb_atos.ato (id, tipo_ato, numero, ano, emissor, data_publicacao, "
            "id_portal) VALUES (%s, 'INSTRUCAO_NORMATIVA', '9998', 2022, 'RFB', '2022-12-29', "
            "777)", (MODIFICADOR,))
        _executar(c, "migrations/010_ato_coleta.sql")
        _executar(c, "migrations/011_recoleta.sql")
        yield c
        _limpar(c)


def _carregar(conn, run_id="t1"):
    return cap.aplicar(conn, ALVO, VIGENTE, ORIGINAL, run_id=run_id,
                       origem_vigente="teste://vigente", caminho_original="teste://original",
                       bytes_baixados=123)


def test_a_trava_da_010_recusa_analisado_sem_texto_novo(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO rfb_atos.ato (id, tipo_ato, numero, ano, content_disponivel, "
                     "analise_completa) VALUES (900003, 'IN', '1', 2020, false, true)")


def test_carrega_texto_segmentos_historico_e_flags(conn):
    resumo = _carregar(conn)
    assert resumo["segmentos"] == 5 and resumo["exibidos"] == 4
    texto = conn.execute("SELECT texto_completo FROM rfb_atos.ato_content WHERE ato_id = %s",
                         (ALVO,)).fetchone()[0]
    assert "Texto novo" in texto and "Texto antigo" not in texto
    original = conn.execute("SELECT texto FROM rfb_atos.ato_texto_visao WHERE ato_id = %s",
                            (ALVO,)).fetchone()[0]
    assert "Texto antigo" in original
    assert conn.execute("SELECT count(*) FROM rfb_atos.ato_segmento WHERE ato_id = %s",
                        (ALVO,)).fetchone()[0] == 5
    assert conn.execute("SELECT ato_modificador_id FROM rfb_atos.ato_alteracao_historico "
                        "WHERE ato_alvo_id = %s", (ALVO,)).fetchone()[0] == MODIFICADOR
    flags = conn.execute("SELECT content_disponivel, analise_completa, status_vigencia, "
                         "situacao_portal->>'idAto' FROM rfb_atos.ato WHERE id = %s",
                         (ALVO,)).fetchone()
    assert flags == (True, False, "vigente_alterado", "555")
    assert conn.execute("SELECT pdf_status, tentativas FROM rfb_atos.ato_coleta "
                        "WHERE ato_id = %s", (ALVO,)).fetchone() == ("baixado", 1)


def test_toda_alteracao_fica_no_log_com_antes_e_depois(conn):
    _carregar(conn, run_id="r1")
    campos = dict(conn.execute("SELECT campo, antes::text FROM rfb_atos.ato_mudanca "
                               "WHERE run_id = 'r1'").fetchall())
    assert campos["analise_completa"] == "true"
    assert campos["status_vigencia"] == '"nao_disponivel_portal"'
    assert "texto_completo" in campos


def test_recarregar_e_idempotente(conn):
    _carregar(conn, run_id="r1")
    _carregar(conn, run_id="r2")
    assert conn.execute("SELECT count(*) FROM rfb_atos.ato_segmento WHERE ato_id = %s",
                        (ALVO,)).fetchone()[0] == 5
    assert conn.execute("SELECT count(*) FROM rfb_atos.ato_texto_visao WHERE ato_id = %s",
                        (ALVO,)).fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM rfb_atos.ato_mudanca WHERE run_id = 'r2'"
                        ).fetchone()[0] == 0


def test_recusa_visao_de_outro_ato(conn):
    with pytest.raises(ValueError):
        cap.aplicar(conn, ALVO, {**VIGENTE, "idAto": 1}, None, run_id="x", origem_vigente="t")


def test_reenfileirar_devolve_a_fila_e_o_invariante_valida(conn):
    ids = rst.alvos(conn)
    assert ALVO in ids
    assert rst.aplicar(conn, ids, "fila1") == len(ids)
    assert rst.alvos(conn) == []
    assert conn.execute("SELECT count(*) FROM rfb_atos.ato_mudanca WHERE run_id = 'fila1'"
                        ).fetchone()[0] == len(ids)
    conn.execute("ALTER TABLE rfb_atos.ato VALIDATE CONSTRAINT ato_analise_exige_conteudo")


def test_reenfileirar_recusa_se_ha_materia(conn):
    conn.execute("INSERT INTO rfb_atos.ato_materia (id, ato_id, ordem) VALUES (900001, %s, 1)",
                 (ALVO,))
    with pytest.raises(RuntimeError):
        rst.aplicar(conn, rst.alvos(conn), "fila2")
