"""coletar_atos_portal.py — listagem, linha do ato, relações, fim de vigência e gravação.

As funções puras rodam sempre; as de banco só com PGTEST_DSN e como `rfb_writer` (GRANTs da nuvem).
O portal é falso (sem rede): as visões imitam as que o normasinternet2 devolveu em 13/09/2026 para a
IN RFB 1.911/2019 (idAto 104314, revogada pela 2.121, idAto 127905) e a IN SRF 247/2002 (15123)
com a retificação dela (15124). A 2.121 é a do seed (ato 16630, id_portal 127905).
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import psycopg
import pytest

import coletar_atos_portal as col
from test_categorizar_nuvem import PAPEIS_DA_NUVEM
from test_visoes_portal import seg

RAIZ = Path(__file__).resolve().parent.parent
DSN = os.environ.get("PGTEST_DSN")
precisa_banco = pytest.mark.skipif(not DSN, reason="defina PGTEST_DSN (Postgres descartável)")
IN = col.sijut_value("INSTRUCAO_NORMATIVA")
REVOGADOR = 16630             # a IN 2.121 do seed (id_portal 127905)
CONFIRMA_LLM = 900202          # ato qualquer já na base, para a aresta dita pelo modelo


def _linha_html(numero, orgao, data, ementa, id_ato, tipo="Instrução Normativa"):
    return (f"<tr><td>{tipo}</td><td>{numero}</td><td>{orgao}</td><td>{data}</td><td><a href=\""
            f"https://normasinternet2.receita.fazenda.gov.br/#/consulta/externa/{id_ato}/vs/XY\">"
            f"{ementa}</a></td></tr>")


def _listagem(*linhas):
    return ('<select id="p"><option value="1">1</option></select><table id="tabelaAtos">'
            "<tr><th>Tipo</th><th>Nº</th></tr>" + "".join(linhas) + "</table>")


LISTAGEM_2002 = _listagem(
    _linha_html("247", "SRF", "03/12/2002", "RetificaçãoRevogado(a) pela IN 1911", 15124),
    _linha_html("247", "SRF", "26/11/2002", "Dispõe sobre a Contribuição para o PIS", 15123),
    _linha_html("2.470", "SRF", "01/12/2002", "Outro ato", 99))
LISTAGEM_2019 = _listagem(
    _linha_html("1911", "RFB", "15/10/2019", "Regulamenta PIS/Cofins", 104314))


def _vigente(id_ato, numero, data_ato, publicacao, *, vigente=False, anexo=False, orgao="SRF"):
    outros = [seg(10, 1, 2, f"Art. 1º Texto do ato {numero}.", compilado=True)]
    if anexo:
        outros.append(seg(11, 1, 3, None, arquivoBinario={"idArquivoBinario": 5}))
    return {"idAto": id_ato, "vigente": vigente, "visao": "vigente", "dataPublicacao": publicacao,
            "dataVigenciaInicio": publicacao, "historico": [],
            "epigrafe": {"numeroAto": numero, "dataAto": data_ato,
                         "orgaos": [{"siglaOrgao": orgao}]},
            "ementas": [seg(1, 1, 1, f"Ementa do ato {numero}.", compilado=True)],
            "outrosSegmentos": outros}


HINT = {"REV": "Revoga", "ALT": "Altera", "RET": "Retifica", "SUS": "Suspende a execução"}
COR = {"REV": 3, "ALT": 2, "RET": 5, "SUS": 3}


def _imp(id_ato, sigla, *, pub="15/10/2019", efeito=None, epigrafe="IN X", hint=None):
    return {"idAto": id_ato, "sigla": sigla, "corSimbolo": COR[sigla], "hint": hint or HINT[sigla],
            "dataPublicacaoDMY": pub, "dataVigenciaPrimeiraAnotacao": efeito,
            "epigrafeBase": epigrafe}


VISOES = {
    (104314, "vigente"): _vigente(104314, "1911", "2019-10-11", "2019-10-15", anexo=True,
                                  orgao="RFB"),
    (104314, "relacional"): {
        "impactosPoloAtivo": [_imp(127905, "REV", pub="20/12/2022", epigrafe="IN RFB nº 2121"),
                              _imp(126434, "ALT", pub="01/11/2022", epigrafe="IN RFB nº 2112")],
        "impactosPoloPassivo": [_imp(15123, "REV", pub="26/11/2002", epigrafe="IN SRF nº 247")]},
    (127905, "vigente"): _vigente(127905, "2121", "2022-12-15", "2022-12-20", vigente=True,
                                  orgao="RFB"),
    (15123, "vigente"): _vigente(15123, "247", "2002-11-21", "2002-11-26"),
    (15123, "relacional"): {"impactosPoloAtivo": [_imp(104314, "REV", epigrafe="IN RFB nº 1911"),
                                                  _imp(15124, "RET", pub="03/12/2002")],
                            "impactosPoloPassivo": []},
    (15124, "vigente"): _vigente(15124, "247", "2002-11-21", "2002-12-03"),
    (15124, "relacional"): {"impactosPoloAtivo": [_imp(104314, "REV", epigrafe="IN RFB nº 1911")],
                            "impactosPoloPassivo": [_imp(15123, "RET", pub="26/11/2002")]},
}


class PortalFalso:
    def __init__(self):
        self.listagens = {(IN, 2002): LISTAGEM_2002, (IN, 2019): LISTAGEM_2019}
        self.pedidos: list = []

    def listagem(self, valor, ano):
        self.pedidos.append(("listagem", ano))
        return [self.listagens.get((valor, ano), _listagem())]

    def visao(self, id_portal, nome):
        self.pedidos.append((id_portal, nome))
        return VISOES.get((id_portal, nome))


def _mudo(_texto: str) -> None:
    return None


# ---------------------------------------------------------------------------
# Funções puras
# ---------------------------------------------------------------------------

def test_listagem_acha_o_ato_e_a_retificacao_pelo_numero():
    achados = col.linhas_da_listagem([LISTAGEM_2002], "247")
    assert [a["idAto"] for a in achados] == [15124, 15123]      # "2.470" não é "247"
    assert achados[1]["cols"][2:4] == ["SRF", "26/11/2002"]


def test_emissor_segue_a_regra_do_extrator_antigo():
    assert col.emissor_do_portal("Cosit") == "COSIT"
    assert col.emissor_do_portal("RFBSecex") == "RFBSECEX"
    assert col.emissor_do_portal("Disit/SRRF08") == "DISIT_SRRF08"
    assert col.emissor_do_portal(None) == "RFB"


def test_linha_do_ato_sai_da_epigrafe_do_portal():
    item = col.linhas_da_listagem([LISTAGEM_2002], "247")[1]
    linha = col.linha_ato("INSTRUCAO_NORMATIVA", item, VISOES[(15123, "vigente")])
    assert (linha["numero"], linha["ano"], linha["emissor"]) == ("247", 2002, "SRF")
    assert linha["identificador"] == "INSTRUCAO_NORMATIVA 247/2002"
    assert linha["data_publicacao"] == date(2002, 11, 26) and linha["id_portal"] == 15123
    assert linha["ementa"] == "Ementa do ato 247."
    assert linha["link"] == col.PREFIXO_LINK + item["href"] and not linha["pdf_disponivel"]
    assert linha["eficacia_atual"] == col.eficacia_padrao("INSTRUCAO_NORMATIVA")


def test_arestas_na_convencao_origem_age_sobre_destino():
    ativa, passiva = col.arestas_do_portal(104314, date(2019, 10, 15), {
        "impactosPoloAtivo": [_imp(127905, "REV", pub="20/12/2022")],
        "impactosPoloPassivo": [_imp(15123, "REV", pub="26/11/2002")]})
    assert (ativa["origem_portal"], ativa["destino_portal"]) == (127905, 104314)
    assert ativa["tipo_relacao"] == "interrompe" and ativa["observacao"] == "REV: Revoga"
    assert ativa["data_relacao"] == date(2022, 12, 20)        # publicação de quem revoga
    assert (passiva["origem_portal"], passiva["destino_portal"]) == (104314, 15123)
    assert passiva["data_relacao"] == date(2019, 10, 15)      # publicação deste ato


def test_fim_de_vigencia_so_com_a_data_de_efeito_do_portal():
    """Revisão 4-LLM (Gemini, Grok): sem a data de efeito, o fim fica vazio — fechar cedo demais
    esconderia norma aplicável; a estimativa fica só na auditoria."""
    rel = {"impactosPoloAtivo": [_imp(1, "REV", efeito="01/02/2020"),
                                 _imp(2, "REV", efeito="01/03/2019")]}
    data, auditoria = col.fim_vigencia(rel, {})
    assert data == date(2019, 3, 1) and auditoria["origem"] == "data_efeito_portal"
    assert auditoria["revogador_id_portal"] == 2
    sem_efeito = {"impactosPoloAtivo": [_imp(127905, "REV", pub="20/12/2022")]}
    data, auditoria = col.fim_vigencia(sem_efeito, {127905: date(2023, 1, 1)})
    assert data is None and auditoria["estimativa"] == "2023-01-01"
    assert auditoria["origem_estimativa"] == "inicio_vigencia_do_revogador"
    assert col.fim_vigencia(sem_efeito, {})[1]["origem_estimativa"] == "publicacao_do_revogador"


def test_fim_de_vigencia_vale_a_revogacao_mais_antiga_e_ignora_suspensao_e_parcial():
    rel = {"impactosPoloAtivo": [
        _imp(1, "REV", pub="20/12/2022"), _imp(2, "REV", pub="15/10/2019"),
        _imp(3, "SUS", pub="01/01/2010"), _imp(4, "ALT", pub="01/01/2011"),
        _imp(5, "REV", pub="01/01/2012", hint="Revoga parcialmente")]}
    data, auditoria = col.fim_vigencia(rel, {})
    assert data is None and auditoria["estimativa"] == "2019-10-15"
    assert auditoria["revogador_id_portal"] == 2
    assert col.fim_vigencia({"impactosPoloAtivo": [_imp(3, "SUS")]}, {}) == (None, None)


def test_suspensao_e_alteracao_nao_fecham_e_a_sigla_fica_na_observacao():
    """Revisão 4-LLM (Grok): SUS tem a mesma cor da revogação no portal (corSimbolo 3) e entra como
    `interrompe`, como na base; a sigla na observação separa as duas; nenhuma fecha o ato."""
    (a,) = col.arestas_do_portal(1, None, {"impactosPoloAtivo": [_imp(9, "SUS")]})
    assert a["tipo_relacao"] == "interrompe" and a["observacao"].startswith("SUS:")
    so_sus_e_alt = {"impactosPoloAtivo": [_imp(9, "SUS", efeito="01/01/2020"), _imp(4, "ALT")]}
    assert col.fim_vigencia(so_sus_e_alt, {}) == (None, None)


# ---------------------------------------------------------------------------
# Banco — como rfb_writer
# ---------------------------------------------------------------------------

def _limpar(conn) -> None:
    conn.execute("DROP SCHEMA IF EXISTS rfb_atos_staging CASCADE")
    conn.execute("DROP SCHEMA IF EXISTS rfb_atos CASCADE")


def _executar(conn, arquivo: str) -> None:
    conn.execute((RAIZ / arquivo).read_text(encoding="utf-8"))


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("RFB_ATOS_DADOS", str(tmp_path))
    with psycopg.connect(DSN, autocommit=True) as c:
        _limpar(c)
        _executar(c, "tests/fixtures/schema_rfb_atos.sql")
        _executar(c, "tests/fixtures/seed_cenarios.sql")
        c.execute("INSERT INTO rfb_atos.ato (id, tipo_ato, numero, ano, emissor, data_publicacao, "
                  "id_portal, status_vigencia, content_disponivel) VALUES "
                  "(%s, 'INSTRUCAO_NORMATIVA', '9876', 2020, 'RFB', '2020-01-01', 555001, "
                  "'vigente_nunca_alterado', false)", (CONFIRMA_LLM,))
        # a sequência da identidade não conhece os ids explícitos do seed: pula para longe deles
        c.execute("SELECT setval(pg_get_serial_sequence('rfb_atos.ato', 'id'), 1000000)")
        c.execute(PAPEIS_DA_NUVEM)
        _executar(c, "migrations/010_ato_coleta.sql")
        _executar(c, "migrations/011_recoleta.sql")
        c.execute("SET ROLE rfb_writer")
        try:
            yield c
        finally:
            c.execute("RESET ROLE")
            _limpar(c)


def _um(conn, sql: str, *params):
    return conn.execute(sql, params).fetchone()


def _id(conn, id_portal):
    return _um(conn, "SELECT id FROM rfb_atos.ato WHERE id_portal = %s", id_portal)[0]


@precisa_banco
def test_plano_nao_grava_nada(conn):
    resultado = col.coletar(conn, ("INSTRUCAO_NORMATIVA", "1911", 2019), PortalFalso(),
                            aplicar=False, run_id="p", saida=_mudo)
    assert resultado[0]["plano"] and _um(conn, "SELECT count(*) FROM rfb_atos.ato "
                                               "WHERE id_portal = 104314") == (0,)


@precisa_banco
def test_ato_nao_vigente_entra_com_texto_fim_e_a_aresta_do_revogador(conn):
    """O canário do td-analise-piscofins: IN 1.911 não vigente, revogada pela 2.121 (portal)."""
    (r,) = col.coletar(conn, ("INSTRUCAO_NORMATIVA", "1911", 2019), PortalFalso(), aplicar=True,
                       run_id="c1", saida=_mudo)
    ato = _id(conn, 104314)
    assert r["ato_id"] == ato and r["status_vigencia"] == "nao_vigente"
    # sem data de efeito no portal: fim vazio (o consumidor carimba "data de fim desconhecida")
    assert _um(conn, "SELECT identificador, emissor, status_vigencia, data_vigencia_inicio, "
                     "data_vigencia_fim, content_disponivel, analise_completa, "
                     "situacao_portal->'fim_vigencia'->>'estimativa' FROM rfb_atos.ato "
                     "WHERE id = %s", ato) == (
        "INSTRUCAO_NORMATIVA 1911/2019", "RFB", "nao_vigente", date(2019, 10, 15), None, True,
        False, "2022-12-20")
    assert _um(conn, "SELECT destino_id_portal FROM rfb_atos.ato_relacao WHERE "
                     "ato_origem_id = %s AND ato_destino_id = %s", REVOGADOR, ato) == (104314,)
    assert _um(conn, "SELECT tipo_relacao, fonte, observacao, parcial, data_relacao FROM "
                     "rfb_atos.ato_relacao WHERE ato_origem_id = %s AND ato_destino_id = %s",
               REVOGADOR, ato) == ("interrompe", col.FONTE, "REV: Revoga", False,
                                   date(2022, 12, 20))
    # a IN 2.112 (ALT) não está na base: a relação fica anotada no ato, não em ato_relacao
    assert _um(conn, "SELECT situacao_portal->'relacoes_sem_origem'->0->>'idAto' FROM "
                     "rfb_atos.ato WHERE id = %s", ato) == ("126434",)
    # destino fora da base (a IN 247) fica com o idAto, para ligar quando chegar
    assert _um(conn, "SELECT destino_id_portal, destino_externo FROM rfb_atos.ato_relacao "
                     "WHERE ato_origem_id = %s AND ato_destino_id IS NULL", ato) == (
        15123, "IN SRF nº 247")
    assert "anexo PDF" in _um(conn, "SELECT observacao FROM rfb_atos.ato_coleta WHERE ato_id = %s",
                              ato)[0]
    assert _um(conn, "SELECT count(*) FROM rfb_atos.ato_materia WHERE ato_id = %s", ato) == (0,)
    assert _um(conn, "SELECT count(*) FROM rfb_atos.ato_mudanca WHERE run_id = 'c1' AND "
                     "campo = 'criado'") == (1,)


@precisa_banco
def test_ato_que_chega_depois_liga_a_aresta_pendente_sem_duplicar(conn):
    portal = PortalFalso()
    col.coletar(conn, ("INSTRUCAO_NORMATIVA", "1911", 2019), portal, aplicar=True, run_id="c1",
                saida=_mudo)
    resultados = col.coletar(conn, ("INSTRUCAO_NORMATIVA", "247", 2002), portal, aplicar=True,
                             run_id="c2", saida=_mudo)
    assert [r["alvo"].split("idAto ")[1] for r in resultados] == ["15124)", "15123)"]
    in1911, in247, retif = _id(conn, 104314), _id(conn, 15123), _id(conn, 15124)
    assert _um(conn, "SELECT count(*) FROM rfb_atos.ato_relacao WHERE ato_origem_id = %s "
                     "AND tipo_relacao = 'interrompe' AND (ato_destino_id = %s OR "
                     "destino_id_portal = 15123)", in1911, in247) == (1,)
    assert _um(conn, "SELECT count(*) FROM rfb_atos.ato_relacao WHERE ato_destino_id IS NULL "
                     "AND destino_id_portal = 15123") == (0,)
    assert _um(conn, "SELECT tipo_relacao FROM rfb_atos.ato_relacao WHERE ato_origem_id = %s "
                     "AND ato_destino_id = %s", retif, in247) == ("retifica",)
    # a 247 é revogada pela 1.911 (portal sem data de efeito): fim vazio, estimativa na auditoria
    assert _um(conn, "SELECT data_vigencia_fim, situacao_portal->'fim_vigencia'->>'estimativa' "
                     "FROM rfb_atos.ato WHERE id = %s", in247) == (None, "2019-10-15")


@precisa_banco
def test_rodar_de_novo_nao_duplica_o_ato(conn):
    portal = PortalFalso()
    col.coletar(conn, ("INSTRUCAO_NORMATIVA", "1911", 2019), portal, aplicar=True, run_id="c1",
                saida=_mudo)
    (r,) = col.coletar(conn, ("INSTRUCAO_NORMATIVA", "1911", 2019), portal, aplicar=True,
                       run_id="c2", saida=_mudo)
    assert r["ja_existia"]
    assert _um(conn, "SELECT count(*) FROM rfb_atos.ato WHERE id_portal = 104314") == (1,)
    assert _um(conn, "SELECT count(*) FROM rfb_atos.ato_mudanca WHERE run_id = 'c2'") == (0,)


@precisa_banco
def test_aresta_dita_pelo_modelo_passa_a_fonte_do_portal_com_log(conn):
    conn.execute("INSERT INTO rfb_atos.ato_relacao (ato_origem_id, ato_destino_id, tipo_relacao, "
                 "fonte) VALUES (%s, %s, 'interrompe', 'llm-batch')", (REVOGADOR, CONFIRMA_LLM))
    aresta = {"destino_portal": 555001, "tipo_relacao": "interrompe", "observacao": "REV: Revoga",
              "data_relacao": date(2022, 12, 20), "data_efeito": None, "parcial": False,
              "rotulo_outro": "IN X"}
    assert col.gravar_aresta(conn, aresta, REVOGADOR, CONFIRMA_LLM, "m1") == "confirmada"
    assert _um(conn, "SELECT fonte, observacao FROM rfb_atos.ato_relacao WHERE ato_origem_id = %s "
                     "AND ato_destino_id = %s", REVOGADOR, CONFIRMA_LLM) == (col.FONTE,
                                                                           "REV: Revoga")
    assert _um(conn, "SELECT antes, depois FROM rfb_atos.ato_mudanca WHERE run_id = 'm1'") == (
        "llm-batch", col.FONTE)
    assert col.gravar_aresta(conn, aresta, REVOGADOR, CONFIRMA_LLM, "m2") == "ja_existe"


@precisa_banco
def test_numero_que_o_portal_nao_lista_vira_erro_sem_gravar(conn):
    (r,) = col.coletar(conn, ("INSTRUCAO_NORMATIVA", "1", 2019), PortalFalso(), aplicar=True,
                       run_id="x", saida=_mudo)
    assert r["erro"] == "ausente no portal"


@precisa_banco
def test_aresta_pendente_de_outro_ato_e_ligada_quando_o_destino_chega(conn):
    """Revisão 4-LLM (Grok): a aresta externa não pode ficar ao lado de uma interna igual. Com o ato
    recém-criado não há interna para ele; a pendente é ligada e nada fica para trás."""
    conn.execute("INSERT INTO rfb_atos.ato_relacao (ato_origem_id, destino_id_portal, "
                 "destino_externo, tipo_relacao, fonte) VALUES (%s, 104314, 'IN RFB nº 1911', "
                 "'altera', %s)", (CONFIRMA_LLM, col.FONTE))
    (r,) = col.coletar(conn, ("INSTRUCAO_NORMATIVA", "1911", 2019), PortalFalso(), aplicar=True,
                       run_id="c1", saida=_mudo)
    assert r["ligadas"] == 1 and r["pendentes_nao_ligadas"] == 0
    assert _um(conn, "SELECT ato_destino_id FROM rfb_atos.ato_relacao WHERE ato_origem_id = %s "
                     "AND tipo_relacao = 'altera'", CONFIRMA_LLM) == (r["ato_id"],)


@precisa_banco
def test_ato_ja_na_base_com_outra_grafia_de_orgao_nao_e_duplicado(conn):
    """Revisão 4-LLM (Grok): o legado tem id_portal vazio e o órgão grafado de outro jeito."""
    conn.execute("INSERT INTO rfb_atos.ato (tipo_ato, numero, ano, emissor, data_publicacao) "
                 "VALUES ('INSTRUCAO_NORMATIVA', '1911', 2019, 'SRF', '2019-10-15')")
    (r,) = col.coletar(conn, ("INSTRUCAO_NORMATIVA", "1911", 2019), PortalFalso(), aplicar=True,
                       run_id="c1", saida=_mudo)
    assert r["ja_existia"]
    assert _um(conn, "SELECT count(*) FROM rfb_atos.ato WHERE numero = '1911'") == (1,)


def test_listagem_sem_numero_traz_todas_as_linhas_com_a_data():
    todas = col.linhas_da_listagem([LISTAGEM_2002], None)
    assert [a["idAto"] for a in todas] == [15124, 15123, 99]
    assert todas[0]["publicacao"] == date(2002, 12, 3)


@precisa_banco
def test_novos_desde_uma_data_lista_so_o_que_falta_e_grava(conn):
    portal = PortalFalso()
    del portal.listagens[(IN, 2019)]      # a varredura vai de 2002 até hoje: só 2002 tem atos
    portal.listagens[(IN, 2002)] = _listagem(
        _linha_html("247", "SRF", "03/12/2002", "Retificação", 15124),
        _linha_html("247", "SRF", "26/11/2002", "Dispõe sobre a Contribuição", 15123),
        _linha_html("200", "SRF", "01/10/2002", "Antes da data de corte", 15000))
    plano = col.coletar_novos(conn, portal, desde=date(2002, 11, 1), aplicar=False, run_id="n0",
                              tipos=["INSTRUCAO_NORMATIVA"], saida=_mudo)
    assert [r["alvo"] for r in plano] == ["INSTRUCAO_NORMATIVA 247 (idAto 15123)",
                                          "INSTRUCAO_NORMATIVA 247 (idAto 15124)"]
    assert not any(p[1] == "vigente" for p in portal.pedidos if isinstance(p, tuple))
    gravados = col.coletar_novos(conn, portal, desde=date(2002, 11, 1), aplicar=True,
                                 run_id="n1", tipos=["INSTRUCAO_NORMATIVA"], saida=_mudo)
    assert [r.get("ato_id") is not None for r in gravados] == [True, True]
    # de novo: nada falta, nada é baixado
    portal.pedidos.clear()
    assert col.coletar_novos(conn, portal, desde=date(2002, 11, 1), aplicar=True, run_id="n2",
                             tipos=["INSTRUCAO_NORMATIVA"], saida=_mudo) == []
    assert all(p[0] == "listagem" for p in portal.pedidos)


@precisa_banco
def test_erro_num_ato_novo_nao_para_os_outros(conn):
    portal = PortalFalso()
    del portal.listagens[(IN, 2019)]
    portal.listagens[(IN, 2002)] = _listagem(
        _linha_html("247", "SRF", "26/11/2002", "Dispõe", 15123),
        _linha_html("999", "SRF", "27/11/2002", "Sem visão no portal", 424242))
    resultados = col.coletar_novos(conn, portal, desde=date(2002, 11, 1), aplicar=True,
                                   run_id="n3", tipos=["INSTRUCAO_NORMATIVA"], saida=_mudo)
    assert resultados[0].get("ato_id") and "erro" in resultados[1]
