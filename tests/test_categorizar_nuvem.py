"""categorizar_nuvem.py — partição do texto, matérias, gravação, sinal e vetor.

As funções puras rodam sempre. As de banco só com PGTEST_DSN (banco descartável: derrubam os schemas
rfb_atos e rfb_atos_staging) e rodam como `rfb_writer`, com os mesmos GRANTs da nuvem. O modelo e os
vetores são falsos, injetados: nenhum teste chama API paga.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest

import categorizar_nuvem as cn

RAIZ = Path(__file__).resolve().parent.parent
DSN = os.environ.get("PGTEST_DSN")
precisa_banco = pytest.mark.skipif(not DSN, reason="defina PGTEST_DSN (Postgres descartável)")
ATO = 900101
REFERENCIA_IN = "Instrução Normativa RFB nº 2.121/2022"
# ato de uma parte só, com corpo além da ementa (abaixo de MINIMO_CORPO o ato não é categorizado)
CURTO = "Art. 1º PIS e Cofins, Lei nº 10.833/2003. " + "Texto curto do ato. " * 12


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


def _materia(n: int, *, com_solucao: bool = True) -> dict:
    m = {"ordem": 1, "natureza": "dispositivo", "tema_macro": "CREDITAMENTO",
         "tema_especifico": "CREDITAMENTO.CONCEITO_INSUMO", "tags": "insumo",
         "tributos": [{"codigo": "PIS/COFINS", "regime": "não-cumulativo"}],
         "dispositivos": [{"tipo_norma": "Lei", "referencia": "Lei nº 10.833/2003",
                           "dispositivo": "art. 3º, II", "tipo_uso": "regulamentacao"}],
         "dispositivos_do_ato": [{"artigo": str(n), "paragrafo": "único"}],
         "cnaes_aplicaveis": [{"codigo": "1011-2/01", "confianca": 0.9}, "setor sem código"],
         "ementa_trecho": f"trecho {n}"}
    if com_solucao:
        m["solucao"] = f"solução da parte {n}"
    return m


class FalsoModelo:
    """Faz as vezes da API: uma matéria por chamada (a matéria k cita o art. k do ato); o sinal
    responde VEDA. `corpo(n)` troca a resposta da chamada n (None = a padrão)."""

    def __init__(self, *, falhar_na: int | None = None, cortar_acima: int | None = None,
                 com_solucao: bool = True, corpo=None):
        self.falhar_na, self.cortar_acima, self.com_solucao = falhar_na, cortar_acima, com_solucao
        self.corpo = corpo
        self.mensagens: list[str] = []
        self.sistemas: list[list[dict]] = []
        self.sinais = 0

    def __call__(self, modelo, sistema, usuario, max_tokens):
        if max_tokens == cn.MAX_TOKENS_SINAL:
            self.sinais += 1
            return cn.Resposta("VEDA", "end_turn", {"entrada": 50, "saida": 1})
        self.mensagens.append(usuario)
        self.sistemas.append(sistema)
        n = len(self.mensagens)
        if n == self.falhar_na:
            return cn.Resposta("desculpe, não consigo", "end_turn", {})
        if self.cortar_acima and len(usuario) > self.cortar_acima:
            return cn.Resposta('{"materias": [', "max_tokens", {"saida": cn.MAX_TOKENS})
        if self.corpo is not None and (outro := self.corpo(n)) is not None:
            return cn.Resposta(json.dumps(outro, ensure_ascii=False), "end_turn", {"saida": 5})
        corpo = {"ato_metadata": {"eficacia": "vinculante_geral",
                                  "norma_base_regulamentada": [{"referencia": "Lei 10.833/2003"}]},
                 "materias": [_materia(n, com_solucao=self.com_solucao)],
                 "relacoes_com_outros_atos": [{"tipo_relacao": "revoga", "ato_destino": {
                     "tipo_ato": "INSTRUCAO_NORMATIVA", "numero": "1911", "ano": 2019}}]}
        return cn.Resposta("```json\n" + json.dumps(corpo, ensure_ascii=False) + "\n```",
                           "end_turn", {"entrada": 1000, "saida": 200})


def _vetores(textos: list[str]) -> list[list[float]]:
    return [[0.001] * cn.DEFAULT_DIM for _ in textos]


def _mudo(_texto: str) -> None:
    return None


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


def test_sumario_pega_o_nome_no_bloco_seguinte_e_o_primeiro_artigo():
    texto = ("LIVRO I\n\nDISPOSIÇÕES GERAIS\n\nTÍTULO I\n\nDO FATO GERADOR\n\nArt. 1º Texto."
             "\n\nArt. 2º Mais.\n\nTÍTULO II<br>DA BASE\n\nArt. 3º-A Outro.\n\nANEXO I\n\nTabela")
    assert cn.sumario(cn.texto_para_llm(texto)).split("\n") == [
        "LIVRO I (DISPOSIÇÕES GERAIS) — art. 1º",
        "  TÍTULO I (DO FATO GERADOR) — art. 1º",
        "  TÍTULO II (DA BASE) — art. 3º-A",
        "ANEXO I (Tabela)",
    ]


def test_cada_parte_leva_o_sumario_do_ato_inteiro():
    modelo = FalsoModelo()
    cn.categorizar(_ato(), _texto_longo(), modelo, modelo=cn.MODELO_CADEIA, uso=cn.Uso(),
                   limite=5000)
    ultimos = {s[-1]["text"] for s in modelo.sistemas}
    assert len(ultimos) == 1 and "TÍTULO 6 (DO TEMA 6) — art. 31" in ultimos.pop()
    curto = FalsoModelo()
    cn.categorizar(_ato(), CURTO, curto, modelo=cn.MODELO_MASSA, uso=cn.Uso())
    assert len(curto.sistemas[0]) == 3      # ato de uma parte só não precisa de sumário


@pytest.mark.parametrize(("tipo", "numero", "ano"), [
    ("INSTRUCAO_NORMATIVA", "2.121", 2022), ("INSTRUCAO_NORMATIVA", "2.121", 2021),
    ("SOLUCAO_CONSULTA", "2121", 2022), ("PORTARIA", "1", 2026),
])
def test_sonnet_em_todos_os_tipos(tipo, numero, ano):
    """Decisão do dono em 24/09/2026: categorização com Sonnet no mínimo, sinal inclusive."""
    assert cn.escolher_modelo(tipo, numero, ano) == cn.MODELO_CADEIA == cn.MODELO_SINAL


def test_interpreta_json_com_cercas_e_recusa_texto_solto():
    assert cn.interpretar('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ValueError):
        cn.interpretar("sem json aqui")


def test_resposta_mal_formada_derruba_o_ato_e_nao_fica_em_disco():
    """Revisão 4-LLM (Codex): JSON válido não basta — lista de matérias, tema e conteúdo."""
    ruins = [{"materias": "erro"}, {"materias": [{"solucao": "sem tema"}]},
             {"materias": [{"tema_macro": "CREDITAMENTO"}]}, {"materias": ["texto solto"]},
             {"materias": [], "ato_metadata": "texto"}]
    for ruim in ruins:
        modelo = FalsoModelo(corpo=lambda _n, r=ruim: r)
        for _ in range(2):
            with pytest.raises(cn.FalhaCategorizacao):
                cn.categorizar(_ato(), CURTO, modelo, modelo=cn.MODELO_MASSA,
                               uso=cn.Uso())
        assert len(modelo.mensagens) == 2, ruim     # não ficou em disco: pediu de novo


def test_parte_sem_materia_fica_registrada():
    modelo = FalsoModelo(corpo=lambda n: {"materias": []} if n == 2 else None)
    materias, meta = cn.categorizar(_ato(), _texto_longo(), modelo, modelo=cn.MODELO_MASSA,
                                    uso=cn.Uso(), limite=5000)
    assert meta["partes_sem_materia"] == ["2"] and len(materias) == meta["partes"] - 1


def test_divergencia_entre_partes_fica_registrada():
    saidas = [{"_parte": "1", "materias": [], "ato_metadata": {"eficacia": "vinculante_geral"}},
              {"_parte": "2", "materias": [], "ato_metadata": {"eficacia": "normativa"}}]
    _, meta = cn.juntar(saidas)
    assert meta["divergencias"] == {"eficacia": ["vinculante_geral", "normativa"]}
    assert meta["partes_sem_materia"] == ["1", "2"]


def test_materia_normalizada_com_tributo_composto_separado():
    linha = cn.linha_materia(_materia(1), 7)
    assert linha["ordem"] == 7
    assert linha["tributos"] == ["COFINS", "PIS"] and linha["regimes"] == ["nao_cumulativo"]
    assert set(linha["_tributos"]) == {("PIS", "nao_cumulativo"), ("COFINS", "nao_cumulativo")}
    assert linha["tags"] == ["insumo"]
    assert linha["_cnaes"] == {"1011-2/01": (None, None, "0.9")}   # o "setor sem código" sai
    # sem a norma do ato, os dispositivos_do_ato não viram linha
    assert linha["_dispositivos"] == [("lei", "Lei nº 10.833/2003", "art. 3º, II", None,
                                       "regulamentacao")]


def test_tributo_sem_codigo_ou_rejeitado_nao_vira_linha():
    """Revisão 4-LLM (Grok) temia NULL em materia_tributo.tributo_codigo (NOT NULL): o
    decompositor só devolve código canônico."""
    m = {"tributos": [{"codigo": None}, {"codigo": "IN"}, "6912", {"regime": "lucro_real"}, 7]}
    linha = cn.linha_materia(m, 1)
    assert linha["_tributos"] == [] and linha["tributos"] == [] and linha["regimes"] == []


def test_artigos_do_proprio_ato_viram_dispositivo_casavel():
    # formato conferido contra o atos_rfb.normalizar_norma/normalizar_dispositivo do
    # td-analise-piscofins em 13/09/2026: IN 2121/2022 e art:171|par:2|inc:iv|ali:a
    norma = cn.norma_do_ato(_ato())
    assert norma == ("instrucao_normativa", REFERENCIA_IN)   # "2.121": sem o ponto vira 212
    m = {**_materia(1), "dispositivos_do_ato": [
        {"artigo": "171", "paragrafo": "2º", "inciso": "IV", "alinea": "a"}, {"artigo": None}]}
    proprios = [d for d in cn.linha_materia(m, 1, norma)["_dispositivos"]
                if d[4] == "dispositivo_do_ato"]
    assert proprios == [("instrucao_normativa", REFERENCIA_IN,
                         "art. 171, § 2º, inciso IV, alínea a", None, "dispositivo_do_ato")]


def test_varias_partes_numeradas_e_metadados_juntos():
    modelo = FalsoModelo()
    materias, meta = cn.categorizar(_ato(), _texto_longo(), modelo, modelo=cn.MODELO_CADEIA,
                                    uso=cn.Uso(), limite=5000)
    assert meta["partes"] == meta["chamadas"] == len(modelo.mensagens) == len(materias) > 1
    assert "PARTE 2 de" in modelo.mensagens[1] and "<br>" not in modelo.mensagens[1]
    assert "CONTEUDO COMPLETO" not in modelo.mensagens[1]
    assert meta["eficacia"] == "vinculante_geral" and "divergencias" not in meta
    assert meta["norma_base_regulamentada"] == [{"referencia": "Lei 10.833/2003"}]
    assert [m["_parte"] for m in materias] == [str(i) for i in range(1, len(materias) + 1)]


def test_resposta_cortada_divide_a_parte_em_duas():
    texto = _texto_longo(titulos=1, artigos=40, preambulo=False)      # ~26 mil: uma parte só
    modelo = FalsoModelo(cortar_acima=18_000)
    materias, meta = cn.categorizar(_ato(), texto, modelo, modelo=cn.MODELO_MASSA, uso=cn.Uso())
    assert meta["partes"] == 1 and meta["chamadas"] == len(materias) >= 2
    assert "PARTE 1.1 de 1" in modelo.mensagens[1]


def test_parte_curta_cortada_tambem_divide():
    """Revisão 4-LLM (Grok): com o mínimo antigo (8 mil x 2), um trecho denso de ~10 mil
    caracteres cortado no limite de tokens derrubava o ato inteiro."""
    texto = _texto_longo(titulos=1, artigos=15, preambulo=False)      # ~9,5 mil
    modelo = FalsoModelo(cortar_acima=6_000)
    _, meta = cn.categorizar(_ato(), texto, modelo, modelo=cn.MODELO_MASSA, uso=cn.Uso())
    assert meta["chamadas"] >= 2


def test_falha_numa_parte_derruba_o_ato_inteiro():
    with pytest.raises(cn.FalhaCategorizacao):
        cn.categorizar(_ato(), _texto_longo(), FalsoModelo(falhar_na=2), modelo=cn.MODELO_MASSA,
                       uso=cn.Uso(), limite=5000)


def test_resposta_guardada_nao_e_paga_de_novo():
    modelo = FalsoModelo()
    usos = [cn.Uso(), cn.Uso()]
    for uso in usos:
        cn.categorizar(_ato(), CURTO, modelo, modelo=cn.MODELO_MASSA, uso=uso)
    assert len(modelo.mensagens) == 1
    assert usos[1].por_modelo[cn.MODELO_MASSA]["do_disco"] == 1 and usos[1].custo() == 0


def test_tipo_da_coluna_de_vetor_so_passa_o_esperado():
    assert cn._cast_embedding("halfvec(3072)") == "halfvec(3072)"
    assert cn._cast_embedding("text") == "text"
    for ruim in ("halfvec", "text; DROP TABLE x", None):
        with pytest.raises(RuntimeError):
            cn._cast_embedding(ruim)


def test_codigo_de_saida_nao_deixa_lote_com_falha_parecer_sucesso():
    """Revisão 4-LLM (Codex): o lote terminava com saída 0 mesmo com erro."""
    ok = {"ato_id": 1, "sem_sinal": 0, "sem_vetor": 0}
    assert cn.codigo_saida([ok]) == 0
    assert cn.codigo_saida([ok, {"ato_id": 2, "erro": "x"}]) == 1
    assert cn.codigo_saida([{**ok, "sem_vetor": 2}]) == 1
    assert cn.codigo_saida([{**ok, "sem_vetor": 2}], vetor=False) == 0


def test_sonnet_5_vai_sem_temperature_e_haiku_com_zero():
    """Revisão 4-LLM (Codex v4): o Sonnet 5 recusa temperature diferente do padrão (HTTP 400)."""
    pedidos: list[dict] = []

    class Fluxo:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def get_final_message(self):
            uso = SimpleNamespace(input_tokens=10, output_tokens=2,
                                  cache_creation_input_tokens=None, cache_read_input_tokens=5)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="{}")],
                                   stop_reason="end_turn", usage=uso)

    class Mensagens:
        def stream(self, **kwargs):
            pedidos.append(kwargs)
            return Fluxo()

    chamar = cn.chamador_anthropic(SimpleNamespace(messages=Mensagens()))
    r = chamar(cn.MODELO_CADEIA, [{"type": "text", "text": "s"}], "u", 100)
    chamar(cn.MODELO_MASSA, [], "u", 20)
    assert "temperature" not in pedidos[0] and pedidos[1]["temperature"] == 0
    assert pedidos[0]["model"] == "claude-sonnet-5"
    assert pedidos[0]["messages"] == [{"role": "user", "content": "u"}]
    assert r == cn.Resposta("{}", "end_turn",
                            {"entrada": 10, "saida": 2, "cache_criado": 0, "cache_lido": 5})


def test_plano_estima_partes_e_custo():
    e = cn.estimar({**_ato(), "texto_completo": _texto_longo(titulos=40, artigos=30)},
                   cn.MODELO_CADEIA)
    assert e["partes"] > 1 and e["custo"] > 0


# ---------------------------------------------------------------------------
# Banco — como rfb_writer
# ---------------------------------------------------------------------------

# Os mesmos GRANTs que criaram o rfb_writer na nuvem em 13/09/2026, na mesma ordem (antes das
# migrations 010 e 011, que concedem as tabelas novas). Papéis valem para o cluster todo.
PAPEIS_DA_NUVEM = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ratio_leitura') THEN
        CREATE ROLE ratio_leitura NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rfb_writer') THEN
        CREATE ROLE rfb_writer NOLOGIN;
    END IF;
END $$;
GRANT ratio_leitura TO rfb_writer;
GRANT USAGE ON SCHEMA rfb_atos TO ratio_leitura, rfb_writer;
GRANT SELECT ON ALL TABLES IN SCHEMA rfb_atos TO ratio_leitura;
GRANT INSERT, UPDATE ON ALL TABLES IN SCHEMA rfb_atos TO rfb_writer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA rfb_atos TO rfb_writer;
"""


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
        c.execute(PAPEIS_DA_NUVEM)
        _executar(c, "migrations/010_ato_coleta.sql")
        _executar(c, "migrations/011_recoleta.sql")
        c.execute("SET ROLE rfb_writer")
        try:
            yield c
        finally:
            c.execute("RESET ROLE")
            _limpar(c)


def _processar(conn, modelo=None, run_id="t1", embed=_vetores):
    ato = cn.carregar_atos(conn, ids=[ATO])[0]
    return cn.processar_ato(conn, ato, modelo or FalsoModelo(), modelo=cn.MODELO_CADEIA,
                            run_id=run_id, uso=cn.Uso(), embed=embed, limite=5000)


def _um(conn, sql: str, *params):
    return conn.execute(sql, params).fetchone()


@precisa_banco
def test_permissoes_do_rfb_writer_bastam_e_as_do_leitor_nao(conn):
    cn.exigir_permissoes(conn)                     # rfb_writer: passa
    conn.execute("SET ROLE ratio_leitura")
    with pytest.raises(cn.SemPermissao, match=r"INSERT em rfb_atos\.ato_materia"):
        cn.exigir_permissoes(conn)


@precisa_banco
def test_grava_materias_sinal_e_vetor_e_nenhuma_relacao(conn):
    modelo = FalsoModelo()
    resumo = _processar(conn, modelo)
    n = len(modelo.mensagens)
    assert resumo["materias"] == resumo["vetores"] == n > 1 and resumo["sinal"] == {"VEDA": n}
    assert resumo["sem_sinal"] == resumo["sem_vetor"] == 0

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
    usos = dict(conn.execute(
        "SELECT d.tipo_uso, count(*) FROM rfb_atos.materia_dispositivo d JOIN "
        "rfb_atos.ato_materia m ON m.id = d.materia_id WHERE m.ato_id = %s GROUP BY 1",
        (ATO,)).fetchall())
    assert usos == {"regulamentacao": n, "dispositivo_do_ato": n}
    assert _um(conn, "SELECT d.referencia, d.dispositivo FROM rfb_atos.materia_dispositivo d "
                     "JOIN rfb_atos.ato_materia m ON m.id = d.materia_id WHERE m.ato_id = %s "
                     "AND m.ordem = 1 AND d.tipo_uso = 'dispositivo_do_ato'", ATO) == (
        REFERENCIA_IN, "art. 1, § único")
    assert _um(conn, "SELECT c.confianca FROM rfb_atos.materia_cnae c JOIN rfb_atos.ato_materia m "
                     "ON m.id = c.materia_id WHERE m.ato_id = %s AND m.ordem = 1", ATO) == ("0.9",)

    # o "revoga" sugerido pelo modelo não entra: relação vem do portal
    assert _um(conn, "SELECT count(*) FROM rfb_atos.ato_relacao WHERE ato_origem_id = %s",
               ATO) == (0,)
    assert _um(conn, "SELECT analise_completa, eficacia_atual, "
                     "metadados->'categorizacao'->>'modelo' FROM rfb_atos.ato WHERE id = %s",
               ATO) == (True, "vinculante_geral", cn.MODELO_CADEIA)
    texto = _um(conn, "SELECT texto_completo FROM rfb_atos.ato_content WHERE ato_id = %s", ATO)[0]
    assert _um(conn, "SELECT metadados->'categorizacao'->>'texto_sha256' FROM rfb_atos.ato "
                     "WHERE id = %s", ATO) == (cn._sha256(texto),)
    assert {r[0] for r in conn.execute(
        "SELECT campo FROM rfb_atos.ato_mudanca WHERE run_id = 't1'")} == {
        "analise_completa", "eficacia_atual", "metadados.categorizacao", "ids"}
    # a matéria antiga sem vetor (seed, ato 3) não é tocada: o vetor é só das matérias do ato
    assert _um(conn, "SELECT embedding FROM rfb_atos.ato_materia WHERE ato_id = 3 AND ordem = 2"
               ) == (None,)


@precisa_banco
def test_gerar_faz_relatorio_sem_gravar_e_executar_reaproveita(conn):
    """Revisão 4-LLM (Codex): o rfb_writer não apaga — conferir antes de gravar."""
    modelo = FalsoModelo()
    resultado = cn.gerar(cn.carregar_atos(conn, ids=[ATO]), modelo, run_id="g1", uso=cn.Uso(),
                         limite=5000, saida=_mudo)[0]
    n = len(modelo.mensagens)
    assert resultado["materias"] == n and resultado["artigos_no_texto"] == 36
    assert resultado["artigos_citados"] == n          # a matéria k cita o art. k
    assert resultado["temas_repetidos"] == {"CREDITAMENTO.CONCEITO_INSUMO": n}
    relatorio = Path(resultado["relatorio"])
    assert "nada foi gravado no banco" in relatorio.read_text(encoding="utf-8")
    assert relatorio.with_suffix(".json").exists()
    assert _um(conn, "SELECT count(*) FROM rfb_atos.ato_materia WHERE ato_id = %s", ATO) == (0,)
    assert _um(conn, "SELECT analise_completa FROM rfb_atos.ato WHERE id = %s", ATO) == (False,)

    sem_api = FalsoModelo()        # o --executar não pode pedir matéria ao modelo
    gravado = cn.executar_lote(conn, cn.carregar_atos(conn, ids=[ATO]), sem_api, run_id="e1",
                               uso=cn.Uso(), embed=_vetores, limite=5000, so_disco=True,
                               saida=_mudo)[0]
    assert sem_api.mensagens == [] and gravado["materias"] == n


@precisa_banco
def test_executar_sem_resposta_conferida_nao_chama_o_modelo_nem_grava(conn):
    """Revisão 4-LLM (Codex v3): o --executar só grava o que o --gerar mostrou. Texto mudado depois
    do relatório = pedido diferente = sem resposta conferida."""
    cn.gerar(cn.carregar_atos(conn, ids=[ATO]), FalsoModelo(), run_id="g1", uso=cn.Uso(),
             limite=5000, saida=_mudo)
    conn.execute("UPDATE rfb_atos.ato_content SET texto_completo = texto_completo || "
                 "' Mais uma frase.' WHERE ato_id = %s", (ATO,))
    sem_api = FalsoModelo()
    resultado = cn.executar_lote(conn, cn.carregar_atos(conn, ids=[ATO]), sem_api, run_id="e2",
                                 uso=cn.Uso(), embed=_vetores, limite=5000, so_disco=True,
                                 saida=_mudo)[0]
    assert "rode --gerar" in resultado["erro"] and sem_api.mensagens == []
    assert _um(conn, "SELECT count(*) FROM rfb_atos.ato_materia WHERE ato_id = %s", ATO) == (0,)
    assert _um(conn, "SELECT analise_completa FROM rfb_atos.ato WHERE id = %s", ATO) == (False,)


def test_parte_cortada_no_gerar_tambem_serve_ao_executar():
    texto = _texto_longo(titulos=1, artigos=40, preambulo=False)
    cn.categorizar(_ato(), texto, FalsoModelo(cortar_acima=18_000), modelo=cn.MODELO_MASSA,
                   uso=cn.Uso())
    sem_api = FalsoModelo()
    materias, meta = cn.categorizar(_ato(), texto, sem_api, modelo=cn.MODELO_MASSA, uso=cn.Uso(),
                                    so_disco=True)
    assert sem_api.mensagens == [] and meta["chamadas"] == len(materias) >= 2


def test_nenhuma_parte_com_materia_derruba_o_ato():
    vazio = FalsoModelo(corpo=lambda _n: {"materias": []})
    with pytest.raises(cn.FalhaCategorizacao, match="nenhuma parte"):
        cn.categorizar(_ato(), CURTO, vazio, modelo=cn.MODELO_MASSA, uso=cn.Uso())


@precisa_banco
def test_materia_sem_solucao_recebe_sinal_pelo_trecho(conn):
    resumo = _processar(conn, FalsoModelo(com_solucao=False))
    assert resumo["sem_sinal"] == 0 and sum(resumo["sinal"].values()) == resumo["materias"]


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
def test_texto_trocado_durante_a_categorizacao_aborta(conn):
    """Revisão 4-LLM (Codex): o teor analisado tem que ser o que está no banco na gravação."""
    with pytest.raises(cn.AtoInapto, match="texto mudou"):
        cn.persistir(conn, ATO, [cn.linha_materia(_materia(1), 1)], {"texto_sha256": "0" * 64},
                     modelo="m", run_id="h")
    assert _um(conn, "SELECT count(*) FROM rfb_atos.ato_materia WHERE ato_id = %s", ATO) == (0,)


@precisa_banco
def test_erro_inesperado_num_ato_nao_derruba_o_lote(conn):
    """Revisão 4-LLM (Grok): uma falha fora de AtoInapto/FalhaCategorizacao (API de vetor fora do
    ar, por exemplo) parava o lote inteiro."""
    chamadas = []

    def vetor_instavel(textos):
        chamadas.append(1)
        if len(chamadas) == 1:
            raise RuntimeError("OpenAI fora do ar")
        return _vetores(textos)

    conn.execute("UPDATE rfb_atos.ato_content SET texto_completo = %s WHERE ato_id = 2",
                 (CURTO,))
    atos = cn.carregar_atos(conn, ids=[2, ATO])        # ato 2 (seed) vem primeiro e falha
    resultados = cn.executar_lote(conn, atos, FalsoModelo(), run_id="lote", uso=cn.Uso(),
                                  embed=vetor_instavel, limite=5000, saida=_mudo)
    assert resultados[0]["erro"].startswith("RuntimeError") and resultados[1]["materias"] > 1
    assert cn.codigo_saida(resultados) == 1
    # as matérias do ato 2 ficaram, sem vetor; rodar de novo completa sem pedir matéria de novo
    de_novo = cn.executar_lote(conn, cn.carregar_atos(conn, ids=[2]), FalsoModelo(),
                               run_id="lote2", uso=cn.Uso(), embed=_vetores, saida=_mudo)
    assert de_novo[0]["categorizado_agora"] is False and de_novo[0]["sem_vetor"] == 0
    assert cn.codigo_saida(de_novo) == 0


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
def test_eficacia_divergente_entre_partes_nao_e_gravada(conn):
    meta = {"eficacia": "vinculante_geral", "divergencias": {"eficacia": ["vinculante_geral", "x"]}}
    cn.persistir(conn, ATO, [cn.linha_materia(_materia(1), 1)], meta, modelo="m", run_id="d")
    assert _um(conn, "SELECT eficacia_atual FROM rfb_atos.ato WHERE id = %s", ATO) == (None,)


@precisa_banco
def test_fila_de_pendentes_so_com_texto_e_sem_materia(conn):
    ids = {a["id"] for a in cn.carregar_atos(conn, pendentes=True, limit=100)}
    assert ATO in ids and 2 in ids
    assert not ids & {1, 3, 4, 6}   # analisados, sem texto ou texto nulo


def test_so_a_ementa_nao_e_categorizada():
    with pytest.raises(cn.FalhaCategorizacao, match="só a ementa"):
        cn.categorizar(_ato(), "## EMENTA\nDispõe sobre X.\n\nArt. 1º Curto.", FalsoModelo(),
                       modelo=cn.MODELO_CADEIA, uso=cn.Uso())


def test_portao_de_qualidade():
    ok = {"materias": 4, "partes_sem_materia": [], "tema_fora_da_taxonomia": 0, "sem_solucao": 0,
          "artigos_no_texto": 10, "cobertura_artigos": 0.1}
    assert cn.portao(ok) == []                     # ato curto: cobertura de artigos não conta
    assert cn.portao({**ok, "artigos_no_texto": 40, "cobertura_artigos": 0.5})
    assert cn.portao({**ok, "tema_fora_da_taxonomia": 2})
    assert cn.portao({**ok, "sem_solucao": 2})
    assert cn.portao({**ok, "partes_sem_materia": ["3"]})


def test_estimativa_calibrada_pela_in_2121():
    e = cn.estimar({**_ato(), "texto_completo": "Art. 1º " + "palavra " * 5000}, cn.MODELO_CADEIA)
    assert e["saida"] >= e["caracteres"] * cn.SAIDA_POR_CARACTERE


def _conectar_como_writer():
    c = psycopg.connect(DSN, autocommit=True)
    c.execute("SET ROLE rfb_writer")
    return c


def _automatico(conn, **kw):
    atos = cn.carregar_atos(conn, ids=[ATO])
    opcoes = {"conectar": _conectar_como_writer, "run_id": "auto1", "uso": cn.Uso(),
              "embed": _vetores, "limite": 5000, "paralelo": 2, "saida": _mudo,
              "custo_max_ato": None}
    return cn.automatico(atos, kw.pop("modelo_falso", FalsoModelo()), **{**opcoes, **kw})


@precisa_banco
def test_automatico_grava_o_que_passa_no_portao(conn):
    # 12 artigos: a regra de cobertura não se aplica; o preâmbulo ancora PIS, Cofins e a lei
    curto = ("Dispõe sobre PIS e Cofins (Lei nº 10.833/2003).\n\n"
             + _texto_longo(titulos=3, artigos=4))
    conn.execute("UPDATE rfb_atos.ato_content SET texto_completo = %s WHERE ato_id = %s",
                 (curto, ATO))
    (r,) = _automatico(conn)
    assert r["materias"] >= 1 and r["sem_sinal"] == r["sem_vetor"] == 0
    assert _um(conn, "SELECT analise_completa FROM rfb_atos.ato WHERE id = %s", ATO) == (True,)
    rodada = Path(os.environ["RFB_ATOS_DADOS"]) / "categorizacao" / "rodadas" / "auto1.jsonl"
    assert json.loads(rodada.read_text(encoding="utf-8").splitlines()[0])["ato_id"] == ATO


@precisa_banco
def test_automatico_manda_para_revisao_e_tira_da_fila(conn):
    """36 artigos no texto, o modelo falso cita poucos: cobertura baixa reprova no portão."""
    (r,) = _automatico(conn)
    assert any("cobertura" in m for m in r["revisar"]) and Path(r["relatorio"]).exists()
    assert _um(conn, "SELECT count(*) FROM rfb_atos.ato_materia WHERE ato_id = %s", ATO) == (0,)
    assert _um(conn, "SELECT metadados->'categorizacao_revisao'->>'run_id', analise_completa "
                     "FROM rfb_atos.ato WHERE id = %s", ATO) == ("auto1", False)
    assert ATO not in {a["id"] for a in cn.carregar_atos(conn, pendentes=True, limit=100)}


@precisa_banco
def test_automatico_respeita_o_teto_e_o_custo_por_ato(conn):
    modelo = FalsoModelo()
    (r,) = _automatico(conn, modelo_falso=modelo, teto_usd=0.0)
    assert r["pulado"] and modelo.mensagens == []
    (r,) = _automatico(conn, modelo_falso=modelo, custo_max_ato=0.000001)
    assert "ato grande" in r["revisar"][0] and modelo.mensagens == []


@precisa_banco
def test_automatico_so_ementa_vai_para_revisao_sem_chamar(conn):
    conn.execute("UPDATE rfb_atos.ato_content SET texto_completo = %s WHERE ato_id = %s",
                 ("## EMENTA\nDispõe sobre X.", ATO))
    modelo = FalsoModelo()
    (r,) = _automatico(conn, modelo_falso=modelo)
    assert "só a ementa" in r["revisar"][0] and modelo.mensagens == []


@precisa_banco
def test_fila_deixa_de_fora_nao_vigente_alterado_antigo_e_tipo_excluido(conn):
    assert ATO in {a["id"] for a in cn.carregar_atos(conn, pendentes=True, limit=100,
                                                   tipos=["INSTRUCAO_NORMATIVA"])}
    assert ATO not in {a["id"] for a in cn.carregar_atos(
        conn, pendentes=True, limit=100, excluir_tipos=["INSTRUCAO_NORMATIVA"])}
    conn.execute("UPDATE rfb_atos.ato SET status_vigencia = 'vigente_alterado' WHERE id = %s",
                 (ATO,))
    assert ATO not in {a["id"] for a in cn.carregar_atos(conn, pendentes=True, limit=100)}
    conn.execute("UPDATE rfb_atos.ato_content SET fonte_extracao = 'json_portal' "
                 "WHERE ato_id = %s", (ATO,))
    assert ATO in {a["id"] for a in cn.carregar_atos(conn, pendentes=True, limit=100)}
    conn.execute("UPDATE rfb_atos.ato SET status_vigencia = 'nao_vigente' WHERE id = %s", (ATO,))
    assert ATO not in {a["id"] for a in cn.carregar_atos(conn, pendentes=True, limit=100)}


def test_sc_so_com_a_ementa_publicada_vai_como_base_ementa():
    sc = {**_ato(), "tipo_ato": "SOLUCAO_CONSULTA", "texto_completo": "## EMENTA\nASSUNTO: PIS."}
    assert cn.base_da_analise(sc) == "ementa"
    assert cn.base_da_analise({**sc, "texto_completo": "Relatório\n\nA consulente..."}) == "texto"
    assert cn.base_da_analise(_ato(texto_completo="## EMENTA\nx")) == "texto"   # IN: é a norma
    msg = cn.mensagem_usuario(sc, cn.Parte("1", "x", ""), 1)
    assert "a íntegra não é publicada" in msg


@precisa_banco
def test_materia_de_sc_so_ementa_grava_base_ementa(conn):
    conn.execute("UPDATE rfb_atos.ato SET tipo_ato = 'SOLUCAO_CONSULTA' WHERE id = %s", (ATO,))
    conn.execute("UPDATE rfb_atos.ato_content SET texto_completo = %s WHERE ato_id = %s",
                 ("## EMENTA\nASSUNTO: PIS\n\n" + CURTO, ATO))
    (r,) = _automatico(conn)
    assert r["materias"] >= 1
    assert {x[0] for x in conn.execute("SELECT base_analise FROM rfb_atos.ato_materia "
                                       "WHERE ato_id = %s", (ATO,))} == {"ementa"}


def test_ancoragem_pega_tributo_e_lei_que_nao_estao_no_texto():
    texto = "Assunto: Cofins. Nos termos da Lei nº 10.833, de 2003, art. 3º, II, o crédito..."
    boa = {"tributos": ["COFINS"],
           "_dispositivos": [("lei", "Lei nº 10.833/2003", "art. 3º", None, "fundamento")]}
    assert cn.ancoragem(texto, boa) == []
    tributo_trocado = {**boa, "tributos": ["IPI"]}
    assert cn.ancoragem(texto, tributo_trocado) == ["tributo IPI não aparece no texto"]
    lei_inventada = {**boa, "_dispositivos": [
        ("lei", "Lei nº 9.718/1998", "art. 3º", None, "fundamento"),
        ("lei", "Lei nº 12.973/2014", "art. 1º", None, "fundamento")]}
    assert "2 de 2 normas citadas" in cn.ancoragem(texto, lei_inventada)[0]
    assert cn.ancoragem(texto, {"tributos": ["TRIBUTO_SEM_LISTA"], "_dispositivos": []}) == []


def test_portao_reprova_ato_com_muitas_materias_sem_ancora():
    ok = {"materias": 5, "partes_sem_materia": [], "tema_fora_da_taxonomia": 0, "sem_solucao": 0,
          "artigos_no_texto": 0, "cobertura_artigos": None, "desancoradas": ["matéria 1: x"]}
    assert cn.portao(ok) == []                                  # 1 de 5 = 20%: passa
    assert "sem âncora" in cn.portao({**ok, "desancoradas": ["m1: x", "m2: y"]})[0]   # 40%
