"""categorizar_nuvem.py — partição do texto, matérias, gravação, sinal e vetor.

As funções puras rodam sempre. As de banco só com PGTEST_DSN (banco descartável: derrubam os schemas
rfb_atos e rfb_atos_staging). O modelo e os vetores são falsos, injetados: nenhum teste chama API
paga.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg
import pytest

import categorizar_nuvem as cn

RAIZ = Path(__file__).resolve().parent.parent
DSN = os.environ.get("PGTEST_DSN")
precisa_banco = pytest.mark.skipif(not DSN, reason="defina PGTEST_DSN (Postgres descartável)")
ATO = 900101


def _ato(**kw) -> dict:
    ato = {"id": ATO, "tipo_ato": "INSTRUCAO_NORMATIVA", "numero": "2121", "ano": 2022,
           "emissor": "RFB", "data_publicacao": "2022-12-15", "ementa": "Consolida PIS/COFINS."}
    ato.update(kw)
    return ato


def _texto_longo(titulos: int = 6, artigos: int = 6, preambulo: bool = True) -> str:
    """Texto no formato do portal: um segmento por bloco, HTML nas quebras dos cabeçalhos."""
    blocos = ["Preâmbulo da IN."] if preambulo else []
    n = 1
    for t in range(1, titulos + 1):
        blocos.append(f"TÍTULO {t}<br>DO TEMA {t}")
        for _ in range(artigos):
            blocos.append(f"Art. {n}. " + "palavra " * 50)
            blocos.append("§ 1º " + "detalhe " * 25)
            n += 1
    return "\n\n".join(blocos)


def _materia(n: int) -> dict:
    return {"ordem": 1, "natureza": "dispositivo", "tema_macro": "CREDITAMENTO",
            "tema_especifico": "CREDITAMENTO.CONCEITO_INSUMO", "tags": "insumo",
            "tributos": [{"codigo": "PIS/COFINS", "regime": "não-cumulativo"}],
            "dispositivos": [{"tipo_norma": "Lei", "referencia": "Lei nº 10.833/2003",
                              "dispositivo": "art. 3º, II", "tipo_uso": "regulamentacao"}],
            "cnaes_aplicaveis": [{"codigo": "1011-2/01", "confianca": 0.9}, "setor sem código"],
            "ementa_trecho": f"trecho {n}", "solucao": f"solução da parte {n}"}


class FalsoModelo:
    """Faz as vezes da API: uma matéria por chamada; o sinal responde VEDA."""

    def __init__(self, *, falhar_na: int | None = None, cortar_acima: int | None = None):
        self.falhar_na, self.cortar_acima = falhar_na, cortar_acima
        self.mensagens: list[str] = []
        self.sinais = 0

    def __call__(self, modelo, sistema, usuario, max_tokens):
        if max_tokens == cn.MAX_TOKENS_SINAL:
            self.sinais += 1
            return cn.Resposta("VEDA", "end_turn", {"entrada": 50, "saida": 1})
        self.mensagens.append(usuario)
        n = len(self.mensagens)
        if n == self.falhar_na:
            return cn.Resposta("desculpe, não consigo", "end_turn", {})
        if self.cortar_acima and len(usuario) > self.cortar_acima:
            return cn.Resposta('{"materias": [', "max_tokens", {"saida": cn.MAX_TOKENS})
        corpo = {"ato_metadata": {"eficacia": "vinculante_geral",
                                  "norma_base_regulamentada": [{"referencia": "Lei 10.833/2003"}]},
                 "materias": [_materia(n)],
                 "relacoes_com_outros_atos": [{"tipo_relacao": "revoga", "ato_destino": {
                     "tipo_ato": "INSTRUCAO_NORMATIVA", "numero": "1911", "ano": 2019}}]}
        return cn.Resposta("```json\n" + json.dumps(corpo, ensure_ascii=False) + "\n```",
                           "end_turn", {"entrada": 1000, "saida": 200})


def _vetores(textos: list[str]) -> list[list[float]]:
    return [[0.001] * cn.DEFAULT_DIM for _ in textos]


@pytest.fixture(autouse=True)
def _respostas_em_pasta_temporaria(tmp_path, monkeypatch):
    monkeypatch.setenv("RFB_ATOS_DADOS", str(tmp_path))


# ---------------------------------------------------------------------------
# Funções puras
# ---------------------------------------------------------------------------

def test_texto_para_llm_tira_html_e_mantem_paragrafos():
    t = cn.texto_para_llm('TÍTULO I<br>DO FATO<br/>\n\nArt. 1º A <a href="x">Lei</a> &amp; '
                          'o&nbsp;resto.')
    assert t == "TÍTULO I\nDO FATO\n\nArt. 1º A Lei & o resto."


def test_ato_curto_vai_inteiro_numa_parte():
    partes = cn.particionar("Art. 1º Curto.", 1000)
    assert [(p.rotulo, p.texto, p.caminho) for p in partes] == [("1", "Art. 1º Curto.", "")]


def test_particao_corta_nos_cabecalhos_sem_perder_texto():
    limpo = cn.texto_para_llm(_texto_longo())
    partes = cn.particionar(limpo, 5000)
    assert len(partes) > 1
    assert "\n\n".join(p.texto for p in partes) == limpo      # nada some, nada repete
    assert all(len(p.texto) <= 5000 for p in partes)
    assert all(p.texto.startswith(("Preâmbulo", "TÍTULO")) for p in partes)
    assert partes[1].caminho.startswith("TÍTULO") and "(DO TEMA" in partes[1].caminho


def test_titulo_maior_que_o_limite_corta_antes_de_artigo():
    limpo = cn.texto_para_llm(_texto_longo(titulos=1, artigos=30, preambulo=False))
    partes = cn.particionar(limpo, 3000)
    assert len(partes) > 3
    assert "\n\n".join(p.texto for p in partes) == limpo
    assert all(len(p.texto) <= 3000 for p in partes)
    assert all(p.texto.startswith(("TÍTULO", "Art.")) for p in partes)   # nunca num "§"
    assert {p.caminho for p in partes} == {"TÍTULO 1 (DO TEMA 1)"}


def test_bloco_gigante_sem_cabecalho_corta_entre_linhas():
    bloco = "\n".join(f"linha {i} " + "x" * 90 for i in range(200))
    partes = cn.particionar(bloco, 5000)
    assert all(len(p.texto) <= 5000 for p in partes)
    assert sum(len(p.texto) for p in partes) == len(bloco) - (len(partes) - 1)


def test_palavra_no_inicio_de_paragrafo_nao_vira_cabecalho():
    assert cn._CABECALHO.match("Livro Registro de Inventário deve ser escriturado") is None
    assert cn._CABECALHO.match("Subseção IV\nDas Agências") is not None
    assert cn._CABECALHO.match("ANEXO ÚNICO") is not None


@pytest.mark.parametrize(("tipo", "numero", "ano", "esperado"), [
    ("INSTRUCAO_NORMATIVA", "2.121", 2022, cn.MODELO_CADEIA),
    ("INSTRUCAO_NORMATIVA", "247", 2002, cn.MODELO_CADEIA),
    ("INSTRUCAO_NORMATIVA", "2.121", 2021, cn.MODELO_MASSA),
    ("SOLUCAO_CONSULTA", "2121", 2022, cn.MODELO_MASSA),
])
def test_sonnet_so_na_cadeia_de_piscofins(tipo, numero, ano, esperado):
    assert cn.escolher_modelo(tipo, numero, ano) == esperado


def test_interpreta_json_com_cercas_e_recusa_texto_solto():
    assert cn.interpretar('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ValueError):
        cn.interpretar("sem json aqui")


def test_materia_normalizada_com_tributo_composto_separado():
    linha = cn.linha_materia(_materia(1), 7)
    assert linha["ordem"] == 7
    assert linha["tributos"] == ["COFINS", "PIS"] and linha["regimes"] == ["nao_cumulativo"]
    assert set(linha["_tributos"]) == {("PIS", "nao_cumulativo"), ("COFINS", "nao_cumulativo")}
    assert linha["tags"] == ["insumo"]
    assert linha["_cnaes"] == {"1011-2/01": (None, None, "0.9")}   # o "setor sem código" sai
    assert linha["_dispositivos"] == [("lei", "Lei nº 10.833/2003", "art. 3º, II", None,
                                       "regulamentacao")]


def test_varias_partes_numeradas_e_metadados_juntos():
    modelo = FalsoModelo()
    materias, meta = cn.categorizar(_ato(), _texto_longo(), modelo, modelo=cn.MODELO_CADEIA,
                                    uso=cn.Uso(), limite=5000)
    assert meta["partes"] == len(modelo.mensagens) == len(materias) > 1
    assert "PARTE 2 de" in modelo.mensagens[1] and "<br>" not in modelo.mensagens[1]
    assert "CONTEUDO COMPLETO" not in modelo.mensagens[1]
    assert meta["eficacia"] == "vinculante_geral"
    assert meta["norma_base_regulamentada"] == [{"referencia": "Lei 10.833/2003"}]


def test_resposta_cortada_divide_a_parte_em_duas():
    texto = _texto_longo(titulos=1, artigos=40, preambulo=False)      # ~26 mil: uma parte só
    modelo = FalsoModelo(cortar_acima=18_000)
    materias, meta = cn.categorizar(_ato(), texto, modelo, modelo=cn.MODELO_MASSA, uso=cn.Uso())
    assert meta["partes"] >= 2 and len(materias) == meta["partes"]
    assert "PARTE 1.1 de 1" in modelo.mensagens[1]


def test_falha_numa_parte_derruba_o_ato_inteiro():
    with pytest.raises(cn.FalhaCategorizacao):
        cn.categorizar(_ato(), _texto_longo(), FalsoModelo(falhar_na=2), modelo=cn.MODELO_MASSA,
                       uso=cn.Uso(), limite=5000)


def test_resposta_guardada_nao_e_paga_de_novo():
    modelo = FalsoModelo()
    usos = [cn.Uso(), cn.Uso()]
    for uso in usos:
        cn.categorizar(_ato(), "Art. 1º Texto curto.", modelo, modelo=cn.MODELO_MASSA, uso=uso)
    assert len(modelo.mensagens) == 1
    assert usos[1].por_modelo[cn.MODELO_MASSA]["do_disco"] == 1 and usos[1].custo() == 0


def test_plano_estima_partes_e_custo():
    e = cn.estimar({**_ato(), "texto_completo": _texto_longo(titulos=40, artigos=30)},
                   cn.MODELO_CADEIA)
    assert e["partes"] > 1 and e["custo"] > 0


# ---------------------------------------------------------------------------
# Banco
# ---------------------------------------------------------------------------

def _limpar(conn) -> None:
    conn.execute("DROP SCHEMA IF EXISTS rfb_atos_staging CASCADE")
    conn.execute("DROP SCHEMA IF EXISTS rfb_atos CASCADE")


def _executar(conn, arquivo: str) -> None:
    conn.execute((RAIZ / arquivo).read_text(encoding="utf-8"))


@pytest.fixture
def conn():
    texto = _texto_longo()
    with psycopg.connect(DSN, autocommit=True) as c:
        _limpar(c)
        _executar(c, "tests/fixtures/schema_rfb_atos.sql")
        _executar(c, "tests/fixtures/seed_cenarios.sql")
        c.execute("INSERT INTO rfb_atos.ato (id, tipo_ato, numero, ano, emissor, "
                  "data_publicacao, ementa, content_disponivel, analise_completa) VALUES "
                  # data diferente da IN 2.121 do seed (ato_chave_natural); o modelo sai do
                  # número e do ano, que continuam os da cadeia de PIS/COFINS
                  "(%s, 'INSTRUCAO_NORMATIVA', '2121', 2022, 'RFB', '2022-12-19', "
                  "'Consolida PIS/COFINS.', true, false)", (ATO,))
        c.execute("INSERT INTO rfb_atos.ato_content (ato_id, texto_completo, caracteres) "
                  "VALUES (%s, %s, %s)", (ATO, texto, len(texto)))
        _executar(c, "migrations/010_ato_coleta.sql")
        _executar(c, "migrations/011_recoleta.sql")
        yield c
        _limpar(c)


def _processar(conn, modelo=None, run_id="t1", embed=_vetores):
    ato = cn.carregar_atos(conn, ids=[ATO])[0]
    return cn.processar_ato(conn, ato, modelo or FalsoModelo(), modelo=cn.MODELO_CADEIA,
                            run_id=run_id, uso=cn.Uso(), embed=embed, limite=5000)


def _um(conn, sql: str, *params):
    return conn.execute(sql, params).fetchone()


@precisa_banco
def test_grava_materias_sinal_e_vetor_e_nenhuma_relacao(conn):
    modelo = FalsoModelo()
    resumo = _processar(conn, modelo)
    n = len(modelo.mensagens)
    assert resumo["materias"] == resumo["vetores"] == n > 1 and resumo["sinal"] == {"VEDA": n}

    ordens = [r[0] for r in conn.execute(
        "SELECT ordem FROM rfb_atos.ato_materia WHERE ato_id = %s ORDER BY ordem", (ATO,))]
    assert ordens == list(range(1, n + 1))
    assert _um(conn, "SELECT tributos, regimes, llm_model, sinal, embedding IS NOT NULL, "
                     "embedding_source, base_analise FROM rfb_atos.ato_materia "
                     "WHERE ato_id = %s AND ordem = 1", ATO) == (
        ["COFINS", "PIS"], ["nao_cumulativo"], cn.MODELO_CADEIA, "VEDA", True,
        cn.FONTE_EMBEDDING, "texto")
    assert conn.execute(
        "SELECT t.tributo_codigo, t.regime FROM rfb_atos.materia_tributo t JOIN "
        "rfb_atos.ato_materia m ON m.id = t.materia_id WHERE m.ato_id = %s AND m.ordem = 1 "
        "ORDER BY 1", (ATO,)).fetchall() == [("COFINS", "nao_cumulativo"),
                                            ("PIS", "nao_cumulativo")]
    assert _um(conn, "SELECT count(*) FROM rfb_atos.materia_dispositivo d JOIN "
                     "rfb_atos.ato_materia m ON m.id = d.materia_id WHERE m.ato_id = %s",
               ATO) == (n,)
    assert _um(conn, "SELECT c.confianca FROM rfb_atos.materia_cnae c JOIN rfb_atos.ato_materia m "
                     "ON m.id = c.materia_id WHERE m.ato_id = %s AND m.ordem = 1", ATO) == ("0.9",)

    # o "revoga" sugerido pelo modelo não entra: relação vem do portal
    assert _um(conn, "SELECT count(*) FROM rfb_atos.ato_relacao WHERE ato_origem_id = %s",
               ATO) == (0,)
    assert _um(conn, "SELECT analise_completa, eficacia_atual, "
                     "metadados->'categorizacao'->>'modelo' FROM rfb_atos.ato WHERE id = %s",
               ATO) == (True, "vinculante_geral", cn.MODELO_CADEIA)
    assert {r[0] for r in conn.execute(
        "SELECT campo FROM rfb_atos.ato_mudanca WHERE run_id = 't1'")} == {
        "analise_completa", "eficacia_atual", "metadados.categorizacao", "ids"}
    # a matéria antiga sem vetor (seed, ato 3) não é tocada: o vetor é só das matérias do ato
    assert _um(conn, "SELECT embedding FROM rfb_atos.ato_materia WHERE ato_id = 3 AND ordem = 2"
               ) == (None,)


@precisa_banco
def test_rodar_de_novo_nao_chama_modelo_nem_vetor(conn):
    _processar(conn)
    modelo, vetorizados = FalsoModelo(), []
    resumo = _processar(conn, modelo, run_id="t2",
                        embed=lambda t: vetorizados.append(t) or _vetores(t))
    assert resumo["categorizado_agora"] is False
    assert modelo.mensagens == [] and modelo.sinais == 0 and vetorizados == []
    assert _um(conn, "SELECT count(*) FROM rfb_atos.ato_mudanca WHERE run_id = 't2'") == (0,)


@precisa_banco
def test_falha_numa_parte_nao_grava_nada(conn):
    with pytest.raises(cn.FalhaCategorizacao):
        _processar(conn, FalsoModelo(falhar_na=2))
    assert _um(conn, "SELECT count(*) FROM rfb_atos.ato_materia WHERE ato_id = %s", ATO) == (0,)
    assert _um(conn, "SELECT analise_completa FROM rfb_atos.ato WHERE id = %s", ATO) == (False,)


@precisa_banco
def test_recusa_ato_sem_texto_ou_ja_analisado(conn):
    linha = cn.linha_materia(_materia(1), 1)
    with pytest.raises(cn.AtoInapto, match="sem texto"):
        cn.persistir(conn, 4, [linha], {}, modelo="m", run_id="x")   # seed: PDF sem texto
    with pytest.raises(cn.AtoInapto, match="analise_completa"):
        cn.persistir(conn, 1, [linha], {}, modelo="m", run_id="x")   # seed: analisado
    assert _um(conn, "SELECT count(*) FROM rfb_atos.ato_mudanca WHERE run_id = 'x'") == (0,)


@precisa_banco
def test_recusa_ato_que_ja_tem_materia(conn):
    conn.execute("INSERT INTO rfb_atos.ato_materia (ato_id, ordem) VALUES (2, 1)")
    with pytest.raises(cn.AtoInapto, match="rfb_writer não apaga"):
        cn.persistir(conn, 2, [cn.linha_materia(_materia(1), 1)], {}, modelo="m", run_id="x")


@precisa_banco
def test_eficacia_existente_nao_e_sobrescrita(conn):
    conn.execute("UPDATE rfb_atos.ato SET eficacia_atual = 'normativa' WHERE id = %s", (ATO,))
    _processar(conn)
    assert _um(conn, "SELECT eficacia_atual FROM rfb_atos.ato WHERE id = %s", ATO) == (
        "normativa",)
    assert _um(conn, "SELECT count(*) FROM rfb_atos.ato_mudanca WHERE campo = 'eficacia_atual'"
               ) == (0,)


@precisa_banco
def test_fila_de_pendentes_so_com_texto_e_sem_materia(conn):
    ids = {a["id"] for a in cn.carregar_atos(conn, pendentes=True, limit=100)}
    assert ATO in ids and 2 in ids
    assert not ids & {1, 3, 4, 6}   # analisados, sem texto ou texto nulo
