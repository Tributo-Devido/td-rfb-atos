"""Testes de backfill_pdf.py.

O contrato HTTP com o SIJUT nao pode ser testado aqui (e rede externa, e o
ambiente de escrita nao alcanca o portal). O que da para blindar -- e onde os
erros doem mais -- e a *classificacao*: decidir que um 404 e fato e um 503 e
ruido, e que 200 com HTML nao e um PDF. Essas sao funcoes puras.

A parte de rede e verificada a mao com `--probe`, que existe exatamente para isso.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

pytest.importorskip("psycopg")
import backfill_pdf as bf  # noqa: E402

DSN = os.environ.get("PGTEST_DSN") or os.environ.get("RFB_ATOS_DSN")
PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n"
HTML = b"<!DOCTYPE html><html><body>Consulta SIJUT</body></html>"


def alvo(**kw) -> bf.Alvo:
    base = dict(ato_id=1, tipo_ato="INSTRUCAO_NORMATIVA", numero="2121", ano=2022,
                url_pdf=None, url_html=None, link=None, id_portal=None)
    return bf.Alvo(**{**base, **kw})


# ----------------------------------------------------------------------
# resolver_url
# ----------------------------------------------------------------------
def test_url_gravada_ganha_da_url_montada():
    """O que o crawler viu vale mais que o que este script deduz."""
    a = alvo(url_pdf="https://x/a.pdf", id_portal=127905)
    assert bf.resolver_url(a) == "https://x/a.pdf"


def test_ordem_de_preferencia_url_pdf_link_html():
    assert bf.resolver_url(alvo(link="https://x/l", url_html="https://x/h")) \
        == "https://x/l"
    assert bf.resolver_url(alvo(url_html="https://x/h")) == "https://x/h"


def test_monta_do_id_portal_quando_nao_ha_url():
    u = bf.resolver_url(alvo(id_portal=127905))
    assert "idAto=127905" in u and u.startswith("https://normas.receita")


def test_sem_url_e_sem_id_portal_devolve_none():
    """Ato assim nao e falha de coleta -- e buraco de cadastro do crawler, e o
    plano precisa mostrar isso separado."""
    assert bf.resolver_url(alvo()) is None


def test_ignora_url_lixo_gravada_no_cadastro():
    assert bf.resolver_url(alvo(url_pdf="   ", link="n/a", id_portal=99)) \
        == f"{bf.BASE_SIJUT}/link.action?idAto=99&visao=original"


# ----------------------------------------------------------------------
# classificar_resposta -- o coracao do script
# ----------------------------------------------------------------------
def test_404_vira_fato_e_nao_volta_para_a_fila():
    """O portal respondeu e disse que nao ha. Insistir nao muda a resposta."""
    r = bf.classificar_resposta(404, "text/html", HTML)
    assert r.pdf_status == "sem_pdf_no_portal"
    assert r.retentavel is False


def test_503_e_ruido_e_volta_para_a_fila():
    r = bf.classificar_resposta(503, "text/html", b"")
    assert r.pdf_status == "erro_http"
    assert r.retentavel is True


def test_429_para_a_rodada():
    r = bf.classificar_resposta(429, "text/html", b"")
    assert r.pdf_status == "bloqueado"


def test_pdf_por_magic_bytes_mesmo_com_content_type_errado():
    """Servidor de orgao publico erra content-type com frequencia; os magic
    bytes nao mentem."""
    r = bf.classificar_resposta(200, "application/octet-stream", PDF)
    assert r.pdf_status == "baixado"
    assert r.sha256 and r.corpo == PDF


def test_200_com_html_nao_e_pdf():
    """O SIJUT devolve 200 + pagina quando nao ha o ato. Aceitar isso encheria o
    disco de HTML disfarcado de ato normativo."""
    r = bf.classificar_resposta(200, "text/html; charset=UTF-8", HTML)
    assert r.pdf_status == "sem_pdf_no_portal"
    assert r.corpo is None


def test_200_com_html_sem_content_type():
    r = bf.classificar_resposta(200, None, HTML)
    assert r.pdf_status == "sem_pdf_no_portal"


def test_200_vazio_e_erro_nao_ausencia():
    """Corpo vazio nao autoriza afirmar que o ato nao tem PDF."""
    r = bf.classificar_resposta(200, "application/pdf", b"")
    assert r.pdf_status == "erro_http"


def test_content_type_pdf_declarado_basta():
    r = bf.classificar_resposta(200, "application/pdf", b"conteudo qualquer")
    assert r.pdf_status == "baixado"


# ----------------------------------------------------------------------
# Auxiliares
# ----------------------------------------------------------------------
def test_pdf_espalhado_em_subpastas():
    """30k+ arquivos num diretorio so degrada listagem e backup."""
    assert bf.caminho_pdf(Path("/p"), 16630) == Path("/p/0016/16630.pdf")
    assert bf.caminho_pdf(Path("/p"), 7) == Path("/p/0000/7.pdf")


def test_backoff_cresce_com_a_tentativa():
    assert min(bf.backoff_s(1) for _ in range(50)) < max(
        bf.backoff_s(5) for _ in range(50)
    )


def test_backoff_respeita_o_teto_depois_do_jitter():
    """Aplicar o teto antes do jitter deixaria o valor final passar em ate 50% --
    foi assim que a primeira versao deste codigo errou."""
    assert all(bf.backoff_s(t) <= bf.TETO_BACKOFF_S for t in range(1, 40)
               for _ in range(10))


def test_backoff_tem_jitter():
    """Sem jitter, a fila inteira que tomou 503 volta ao portal toda junta e
    reproduz a sobrecarga que causou o 503."""
    assert len({round(bf.backoff_s(3), 6) for _ in range(20)}) > 1


# ----------------------------------------------------------------------
# Guarda de banco
# ----------------------------------------------------------------------
@pytest.mark.skipif(not DSN, reason="defina PGTEST_DSN")
def test_recusa_rodar_sem_a_migration_010():
    """Sem ato_coleta o resultado da coleta nao teria onde ser gravado e a rodada
    inteira -- que consome o portal -- se perderia."""
    import psycopg

    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS rfb_atos CASCADE")
        conn.execute(
            (RAIZ / "tests/fixtures/schema_rfb_atos.sql").read_text(encoding="utf-8")
        )
        with pytest.raises(SystemExit) as e:
            bf.exigir_010(conn)
        assert "010" in str(e.value)
        conn.execute("DROP SCHEMA IF EXISTS rfb_atos CASCADE")


# ----------------------------------------------------------------------
# Fila e gravacao -- SQL, exercitado contra Postgres real
# ----------------------------------------------------------------------
pgtest = pytest.mark.skipif(not DSN, reason="defina PGTEST_DSN")


@pytest.fixture()
def conn():
    import psycopg

    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS rfb_atos CASCADE")
        for f in ("tests/fixtures/schema_rfb_atos.sql",
                  "tests/fixtures/seed_cenarios.sql",
                  "migrations/010_ato_coleta.sql"):
            c.execute((RAIZ / f).read_text(encoding="utf-8"))
        c.execute("SET search_path = rfb_atos, public")
        yield c
        c.execute("DROP SCHEMA IF EXISTS rfb_atos CASCADE")


def args(**kw):
    from argparse import Namespace

    base = dict(limite=500, ato=None, tipo=None, ano_min=None)
    return Namespace(**{**base, **kw})


@pgtest
def test_fila_pega_exatamente_quem_nao_tem_texto(conn):
    ids = {a.ato_id for a in bf.montar_fila(conn, args())}
    assert ids == {4, 6, 7, 16630, 20}
    # 16707 tem texto e nenhuma materia: e trabalho da categorizacao, nao da
    # coleta. Se aparecer aqui, o backfill esta refazendo download a toa.
    assert 16707 not in ids


@pgtest
def test_fila_poe_instrucao_normativa_na_frente(conn):
    """INs consolidam materia inteira e sao as mais citadas pelo proprio acervo."""
    assert bf.montar_fila(conn, args())[0].ato_id == 16630


@pgtest
def test_fila_respeita_filtros(conn):
    assert [a.ato_id for a in bf.montar_fila(conn, args(ato="7"))] == [7]
    assert [a.ato_id for a in bf.montar_fila(conn, args(tipo="NOTA"))] == [7]
    assert 20 not in {a.ato_id for a in bf.montar_fila(conn, args(ano_min=2020))}


@pgtest
def test_fato_apurado_sai_da_fila_para_sempre(conn):
    alvo7 = next(a for a in bf.montar_fila(conn, args()) if a.ato_id == 7)
    bf.gravar(conn, alvo7, "https://x", bf.classificar_resposta(404, "text/html", HTML),
              "teste")

    assert 7 not in {a.ato_id for a in bf.montar_fila(conn, args())}
    status, proxima, tent = conn.execute(
        "SELECT pdf_status, proxima_tentativa, tentativas "
        "FROM rfb_atos.ato_coleta WHERE ato_id = 7"
    ).fetchone()
    assert (status, proxima, tent) == ("sem_pdf_no_portal", None, 1)


@pgtest
def test_erro_transitorio_volta_para_a_fila_mas_so_depois_do_backoff(conn):
    alvo = next(a for a in bf.montar_fila(conn, args()) if a.ato_id == 7)
    bf.gravar(conn, alvo, "https://x", bf.classificar_resposta(503, None, b""), "teste")

    # Fora da fila agora (proxima_tentativa no futuro), mas nao descartado.
    assert 7 not in {a.ato_id for a in bf.montar_fila(conn, args())}
    status, proxima = conn.execute(
        "SELECT pdf_status, proxima_tentativa > now() "
        "FROM rfb_atos.ato_coleta WHERE ato_id = 7"
    ).fetchone()
    assert (status, proxima) == ("erro_http", True)

    conn.execute("UPDATE rfb_atos.ato_coleta SET proxima_tentativa = now() - "
                 "interval '1 day' WHERE ato_id = 7")
    assert 7 in {a.ato_id for a in bf.montar_fila(conn, args())}


@pgtest
def test_desiste_depois_de_max_tentativas(conn):
    """Sem teto, um ato quebrado consome o portal indefinidamente."""
    conn.execute(
        "UPDATE rfb_atos.ato_coleta SET pdf_status='erro_http', tentativas=%s, "
        "proxima_tentativa=NULL WHERE ato_id=7", (bf.MAX_TENTATIVAS,)
    )
    assert 7 not in {a.ato_id for a in bf.montar_fila(conn, args())}


@pgtest
def test_gravar_e_idempotente_e_conta_tentativas(conn):
    alvo = next(a for a in bf.montar_fila(conn, args()) if a.ato_id == 7)
    for _ in range(3):
        bf.gravar(conn, alvo, "https://x",
                  bf.classificar_resposta(503, None, b""), "teste")
    linhas, tent = conn.execute(
        "SELECT count(*), max(tentativas) FROM rfb_atos.ato_coleta WHERE ato_id = 7"
    ).fetchone()
    assert (linhas, tent) == (1, 3)


@pgtest
def test_download_sincroniza_pdf_disponivel(conn):
    """Nao criar uma segunda verdade: o resto do ecossistema le pdf_disponivel."""
    alvo = next(a for a in bf.montar_fila(conn, args()) if a.ato_id == 16630)
    bf.gravar(conn, alvo, "https://x",
              bf.classificar_resposta(200, "application/pdf", PDF), "teste")
    status, disp = conn.execute(
        "SELECT cl.pdf_status, a.pdf_disponivel FROM rfb_atos.ato a "
        "JOIN rfb_atos.ato_coleta cl ON cl.ato_id = a.id WHERE a.id = 16630"
    ).fetchone()
    assert (status, disp) == ("baixado", True)


@pgtest
def test_grava_no_ato_que_o_invariante_protege(conn):
    """Regressao: o invariante da 010 e NOT VALID, entao tolera a linha legada
    parada mas rejeita UPDATE nela -- e as linhas legadas sao justamente as que
    este script existe para destravar. A IN 2.121 (16630) e uma delas.

    Sem o realinhamento de flags no gravar(), esta chamada estoura com
    CheckViolation e o backfill nao consegue tocar em nenhum dos 912 atos.
    """
    alvo = next(a for a in bf.montar_fila(conn, args()) if a.ato_id == 16630)
    assert conn.execute(
        "SELECT analise_completa FROM rfb_atos.ato WHERE id = 16630"
    ).fetchone()[0] is True

    rebaixou = bf.gravar(conn, alvo, "https://x",
                         bf.classificar_resposta(404, "text/html", HTML), "teste")

    assert rebaixou is True
    analise, content = conn.execute(
        "SELECT analise_completa, content_disponivel FROM rfb_atos.ato WHERE id = 16630"
    ).fetchone()
    assert (analise, content) == (False, False)


@pgtest
def test_nao_rebaixa_flag_de_analise_legitima(conn):
    """O realinhamento so pode agir onde o flag era falso. O ato 1 tem texto e
    analise de verdade -- passar pelo backfill nao pode apagar esse trabalho."""
    alvo1 = bf.Alvo(1, "SOLUCAO_CONSULTA", "1", 2023, None, None, "https://x", None, 0)

    rebaixou = bf.gravar(conn, alvo1, "https://x",
                         bf.classificar_resposta(404, "text/html", HTML), "teste")

    assert rebaixou is False
    assert conn.execute(
        "SELECT analise_completa, content_disponivel FROM rfb_atos.ato WHERE id = 1"
    ).fetchone() == (True, True)


@pgtest
def test_corrige_de_passagem_o_flag_que_mentia_contra(conn):
    """D3: o ato 5 tem texto e `content_disponivel = false` -- trabalho ja pago
    que a base esconde de toda consulta filtrada por esse flag."""
    alvo5 = bf.Alvo(5, "ATO_DECLARATORIO_EXECUTIVO", "5", 2021,
                    None, None, "https://x", None, 0)

    bf.gravar(conn, alvo5, "https://x",
              bf.classificar_resposta(404, "text/html", HTML), "teste")

    assert conn.execute(
        "SELECT content_disponivel FROM rfb_atos.ato WHERE id = 5"
    ).fetchone()[0] is True
