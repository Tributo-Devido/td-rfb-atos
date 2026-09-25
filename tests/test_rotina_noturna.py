"""rotina_noturna.py — janela de coleta, trava, rodada inteira com portal e modelo falsos."""
from __future__ import annotations

import os
from datetime import date

import pytest

import rotina_noturna as rn
from test_categorizar_nuvem import FalsoModelo, _vetores
from test_coletar_atos_portal import IN, PortalFalso, _linha_html, _listagem
from test_coletar_atos_portal import conn  # noqa: F401  (fixture: banco como rfb_writer)
from test_lote_categorizacao import LoteFalso, _cliente

DSN = os.environ.get("PGTEST_DSN")
precisa_banco = pytest.mark.skipif(not DSN, reason="defina PGTEST_DSN (Postgres descartável)")


def test_janela_de_coleta_nunca_comeca_no_futuro():
    assert rn.janela_de_coleta(date(2026, 9, 20), hoje=date(2026, 9, 24)) == date(2026, 9, 10)
    assert rn.janela_de_coleta(date(2099, 1, 1), hoje=date(2026, 9, 24)) == date(2026, 9, 14)
    assert rn.janela_de_coleta(None, hoje=date(2026, 9, 24)) == date(2026, 9, 14)


def test_trava_impede_duas_rodadas(tmp_path):
    primeira = rn.travar(tmp_path)
    assert primeira is not None and rn.travar(tmp_path) is None
    primeira.unlink()
    assert rn.travar(tmp_path) is not None


def test_teve_erro():
    assert not rn.teve_erro({"coleta": {"erros": 0}, "categorizacao": {"gravado": 3}})
    assert rn.teve_erro({"coleta": {"erro": "portal fora"}})
    assert rn.teve_erro({"categorizacao": {"erro": "x"}, "coleta": {"erros": 0}})


@precisa_banco
def test_rodada_coleta_e_categoriza_so_os_tipos_liberados(conn, monkeypatch):  # noqa: F811
    import psycopg

    real = psycopg.connect

    def conectar_como_writer(*a, **kw):
        c = real(*a, **kw)
        c.execute("SET ROLE rfb_writer")
        return c

    monkeypatch.setattr(rn.psycopg, "connect", conectar_como_writer)
    monkeypatch.setattr(rn.col, "ultima_publicacao", lambda _c: date(2002, 11, 1))
    portal = PortalFalso()
    del portal.listagens[(IN, 2019)]
    portal.listagens[(IN, 2002)] = _listagem(
        _linha_html("247", "SRF", "26/11/2002", "Dispõe sobre a Contribuição", 15123))
    cfg = {"tipos_categorizar": ["SOLUCAO_CONSULTA"], "coletar_tipos": ["INSTRUCAO_NORMATIVA"],
           "teto_usd_por_noite": 5.0, "custo_max_ato": 3.0, "limite_atos_por_noite": 10,
           "paralelo": 2}
    lote = LoteFalso()
    resumo = rn.rodada(cfg, dsn=DSN, portal=portal, chamar=FalsoModelo(), embed=_vetores,
                       cliente_lote=_cliente(lote), log=lambda _t: None)
    assert resumo["coleta"]["gravados"] == 1 and resumo["coleta"]["erros"] == 0
    # só SOLUCAO_CONSULTA está liberada: a fila é a SC do seed (ato 2, texto curto demais: fica
    # fora do lote — o automático a manda para revisão sem modelo), e a IN coletada fica sem análise
    assert resumo["categorizacao"]["fila"] == 1 and resumo["categorizacao"]["lotes_enviados"] == []
    assert lote.lotes == {} and resumo["categorizacao"]["para_revisao_sem_modelo"] == 1
    assert conn.execute("SELECT metadados ? 'categorizacao_revisao' FROM rfb_atos.ato WHERE id = 2"
                        ).fetchone() == (True,)
    assert conn.execute("SELECT analise_completa FROM rfb_atos.ato WHERE id_portal = 15123"
                        ).fetchone() == (False,)
    assert conn.execute("SELECT count(*) FROM rfb_atos.ato WHERE id_portal = 15123"
                        ).fetchone() == (1,)


def test_reforco_das_4h_so_roda_se_a_rodada_de_hoje_nao_terminou_ok(tmp_path):
    import json
    dia = date.today().isoformat()
    hoje = tmp_path / f"{dia}.json"
    mudo = lambda _t: None  # noqa: E731
    assert rn.precisa_reforco(tmp_path, log=mudo) == (True, True)      # nenhuma rodada hoje
    hoje.write_text(json.dumps({"coleta": {"gravados": 2, "erros": 0},
                                "categorizacao": {"fila": 3}}), encoding="utf-8")
    assert rn.precisa_reforco(tmp_path, log=mudo) == (False, False)    # terminou ok
    # coleta com erro, mas o lote da noite saiu: roda de novo sem mandar outro lote
    hoje.write_text(json.dumps({"coleta": {"gravados": 2, "erros": 6},
                                "categorizacao": {"fila": 3, "lotes_enviados": ["b"]}}),
                    encoding="utf-8")
    assert rn.precisa_reforco(tmp_path, log=mudo) == (True, False)
    assert (tmp_path / f"{dia}-tentativa1.json").exists() and not hoje.exists()
    # a categorização falhou (25/09/2026): roda e manda o lote; a tentativa 1 não é sobrescrita
    hoje.write_text(json.dumps({"coleta": {"gravados": 2, "erros": 6},
                                "categorizacao": {"erro": "CredencialAusente"}}), encoding="utf-8")
    assert rn.precisa_reforco(tmp_path, log=mudo) == (True, True)
    assert (tmp_path / f"{dia}-tentativa2.json").exists()
    hoje.write_text("{trunc", encoding="utf-8")                          # resumo ilegível
    assert rn.precisa_reforco(tmp_path, log=mudo) == (True, True)
