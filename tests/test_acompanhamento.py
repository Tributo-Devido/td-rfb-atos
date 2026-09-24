"""acompanhamento.py — mensagem, estado, registro no banco e conferência da manhã."""
from __future__ import annotations

import json
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

import acompanhamento as ac

RAIZ = Path(__file__).resolve().parent.parent
DSN = os.environ.get("PGTEST_DSN")
precisa_banco = pytest.mark.skipif(not DSN, reason="defina PGTEST_DSN (Postgres descartável)")

RESUMO = {
    "run_id": "rotina-20260925T020000", "inicio": "2026-09-25T02:00:00",
    "fim": "2026-09-25T02:40:00", "base_atualizada_ate": "2026-09-24",
    "coleta": {"gravados": 18, "erros": 0,
               "por_tipo": {"ATO_DECLARATORIO_EXECUTIVO": 12, "SOLUCAO_CONSULTA": 4,
                            "PORTARIA": 2}},
    "lotes_aplicados": [{"batch_id": "b1", "gravados": 31, "materias": 52, "revisar": 2,
                         "erros": 0, "custo_usd": 1.4}],
    "categorizacao": {"fila": 22, "lotes_enviados": ["b2"], "atos_enviados": 22,
                      "para_revisao_sem_modelo": 0},
}


def test_mensagem_da_noite_boa():
    texto = ac.mensagem(RESUMO)
    assert ":white_check_mark:" in texto and "25/09/2026" in texto
    assert "*18* atos novos (12 ADE, 4 SC, 2 Portaria) · 0 erros" in texto
    assert "*31* atos gravados (52 matérias) · 2 para revisão · US$ 1.40" in texto
    assert "Lote enviado: 22 atos" in texto and "Base atualizada até 24/09/2026" in texto


def test_estado_com_erro_e_falha():
    assert ac.numeros(RESUMO)["estado"] == "ok"
    com_erro = {**RESUMO, "coleta": {**RESUMO["coleta"], "erros": 3}}
    assert ac.numeros(com_erro)["estado"] == "com_erro"
    assert "3 recusados pelo portal" in ac.mensagem(com_erro)
    falhou = {"run_id": "x", "inicio": "2026-09-25T02:00:00", "falhou": "banco fora de alcance"}
    assert ac.numeros(falhou)["estado"] == "falhou"
    assert ":x:" in ac.mensagem(falhou) and "banco fora de alcance" in ac.mensagem(falhou)


def test_slack_sem_webhook_nao_quebra(monkeypatch):
    monkeypatch.delenv("RFB_ATOS_SLACK_WEBHOOK", raising=False)
    monkeypatch.setattr("credenciais._ssm", lambda *_a, **_k: None)
    avisos = []
    assert ac.enviar_slack("oi", log=avisos.append) is False and "sem webhook" in avisos[0]


def test_slack_posta_no_webhook(monkeypatch):
    monkeypatch.setenv("RFB_ATOS_SLACK_WEBHOOK", "https://hooks.exemplo/x")
    enviados = []
    assert ac.enviar_slack("oi", postar=lambda u, c: enviados.append((u, c)) or 200)
    assert enviados == [("https://hooks.exemplo/x", {"text": "oi"})]


def test_conferencia_da_manha(tmp_path, monkeypatch):
    monkeypatch.setenv("RFB_ATOS_SLACK_WEBHOOK", "https://hooks.exemplo/x")
    enviados = []
    postar = lambda u, c: enviados.append(c["text"]) or 200  # noqa: E731
    assert ac.conferir(tmp_path, postar=postar, log=lambda _t: None) == "atrasada"
    assert "não rodou" in enviados[0] and "nunca" in enviados[0]
    (tmp_path / "2026-09-25.json").write_text(json.dumps(RESUMO), encoding="utf-8")
    assert ac.conferir(tmp_path, agora=datetime(2026, 9, 25, 9, 0), postar=postar,
                       log=lambda _t: None) == "ok"
    assert ac.conferir(tmp_path, agora=datetime(2026, 9, 27, 9, 0), postar=postar,
                       log=lambda _t: None) == "atrasada"


@precisa_banco
def test_registro_no_banco_e_painel():
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS rfb_atos_staging CASCADE")
        c.execute("DROP SCHEMA IF EXISTS rfb_atos CASCADE")
        c.execute("CREATE SCHEMA rfb_atos")
        assert ac.registrar(c, RESUMO, log=lambda _t: None) is False   # sem a 012
        c.execute((RAIZ / "migrations/012_rotina_execucao.sql").read_text(encoding="utf-8"))
        c.execute((RAIZ / "migrations/012_rotina_execucao.sql").read_text(encoding="utf-8"))
        assert ac.registrar(c, RESUMO, log=lambda _t: None)
        assert ac.registrar(c, RESUMO, log=lambda _t: None)          # de novo: atualiza
        assert c.execute("SELECT estado, atos_coletados, atos_categorizados, materias_gravadas, "
                         "custo_usd, base_atualizada_ate::text FROM rfb_atos.rotina_execucao"
                         ).fetchall() == [("ok", 18, 31, 52, Decimal("1.40"), "2026-09-24")]
        assert "25/09/2026 02:00 ok" in ac.painel(c)
        c.execute("DROP SCHEMA rfb_atos CASCADE")
