"""Testes offline do `credenciais.py` — ordem das fontes e mensagens, sem AWS nem rede."""
from __future__ import annotations

import pytest

import credenciais as cr


@pytest.fixture(autouse=True)
def consultas_ssm(monkeypatch):
    """Ambiente limpo e SSM falso que só registra o que foi pedido (e não entrega nada)."""
    for variavel, _, _ in cr.REGISTRO.values():
        monkeypatch.delenv(variavel, raising=False)
    monkeypatch.delenv(cr.OVERRIDE_DSN, raising=False)
    monkeypatch.delenv("RFB_ATOS_AWS_PROFILE", raising=False)
    pedidos: list[tuple[str, str | None]] = []

    def falso(parametro, perfil):
        pedidos.append((parametro, perfil))

    monkeypatch.setattr(cr, "_ssm", falso)
    return pedidos


def test_variavel_de_ambiente_vence_e_nao_consulta_ssm(monkeypatch, consultas_ssm):
    monkeypatch.setenv("OPENAI_API_KEY", "k-env")
    assert cr.resolver("openai") == "k-env"
    assert cr.fonte("openai") == "env"
    assert consultas_ssm == []


def test_ssm_quando_nao_ha_variavel(monkeypatch):
    def ssm(parametro, perfil):
        return "dsn-ssm" if parametro == "/td/db/ratio-pg-dsn" else None

    monkeypatch.setattr(cr, "_ssm", ssm)
    assert cr.resolver_dsn("leitura") == "dsn-ssm"
    assert cr.fonte("leitura") == "ssm"


def test_padrao_do_resolver_dsn_e_leitura(monkeypatch):
    monkeypatch.setenv("RFB_ATOS_DSN_LEITURA", "dsn-leitura")
    monkeypatch.setenv("RFB_ATOS_DSN_ESCRITA", "dsn-escrita")
    assert cr.resolver_dsn() == "dsn-leitura"
    assert cr.resolver_dsn("escrita") == "dsn-escrita"


def test_rfb_atos_dsn_vale_para_dsn_mas_nao_para_chave(monkeypatch):
    monkeypatch.setenv("RFB_ATOS_DSN", "postgresql://teste")
    assert cr.resolver_dsn("escrita") == "postgresql://teste"
    assert cr.resolver_dsn("admin") == "postgresql://teste"
    with pytest.raises(cr.CredencialAusente):
        cr.resolver("openai")


def test_admin_consulta_com_perfil_executive(consultas_ssm):
    with pytest.raises(cr.CredencialAusente):
        cr.resolver_dsn("admin")
    assert consultas_ssm == [("/td/admin/ratio-pg-dsn-admin", "executive")]


def test_perfil_aws_trocado_por_variavel(monkeypatch, consultas_ssm):
    monkeypatch.setenv("RFB_ATOS_AWS_PROFILE", "outro")
    with pytest.raises(cr.CredencialAusente):
        cr.resolver("leitura")
    assert consultas_ssm[-1] == ("/td/db/ratio-pg-dsn", "outro")


def test_mensagem_orienta_sem_vazar_valor():
    with pytest.raises(cr.CredencialAusente) as erro:
        cr.resolver_dsn("escrita")
    mensagem = str(erro.value)
    assert "RFB_ATOS_DSN_ESCRITA" in mensagem
    assert "/td/batch/rfb-writer-dsn" in mensagem


def test_nome_desconhecido_e_erro_de_programacao():
    with pytest.raises(KeyError):
        cr.resolver("nada")
    with pytest.raises(KeyError):
        cr.resolver_dsn("openai")
