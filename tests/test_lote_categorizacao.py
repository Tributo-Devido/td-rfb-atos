"""lote_categorizacao.py — envio, download e aplicação com um cliente de lote falso."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import categorizar_nuvem as cn
import lote_categorizacao as lc
from test_categorizar_nuvem import ATO, CURTO, FalsoModelo, _ato, _conectar_como_writer, _vetores
from test_categorizar_nuvem import conn  # noqa: F401  (fixture: banco como rfb_writer)

DSN = os.environ.get("PGTEST_DSN")
precisa_banco = pytest.mark.skipif(not DSN, reason="defina PGTEST_DSN (Postgres descartável)")


class LoteFalso:
    """Faz as vezes de client.messages.batches: responde cada pedido com o FalsoModelo."""

    def __init__(self, terminado: bool = True, falhar: bool = False, falha_no_create=False):
        self.modelo = FalsoModelo()
        self.lotes: dict[str, list] = {}
        self.terminado = terminado
        self.falhar = falhar
        self.falha_no_create = falha_no_create

    def create(self, requests):
        if self.falha_no_create:
            raise ConnectionError("caiu no meio do envio")
        lote_id = f"msgbatch_{len(self.lotes) + 1}"
        self.lotes[lote_id] = requests
        return SimpleNamespace(id=lote_id)

    def retrieve(self, lote_id):
        return SimpleNamespace(processing_status="ended" if self.terminado else "in_progress")

    def results(self, lote_id):
        for req in self.lotes[lote_id]:
            if self.falhar:
                yield SimpleNamespace(custom_id=req["custom_id"],
                                      result=SimpleNamespace(type="expired"))
                continue
            prm = req["params"]
            r = self.modelo(prm["model"], prm["system"], prm["messages"][0]["content"],
                            prm["max_tokens"])
            uso = SimpleNamespace(input_tokens=100, output_tokens=50,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0)
            msg = SimpleNamespace(content=[SimpleNamespace(type="text", text=r.texto)],
                                  stop_reason=r.parada, usage=uso)
            yield SimpleNamespace(custom_id=req["custom_id"],
                                  result=SimpleNamespace(type="succeeded", message=msg))


def _cliente(lote):
    return SimpleNamespace(messages=SimpleNamespace(batches=lote))


@pytest.fixture(autouse=True)
def _dados(tmp_path, monkeypatch):
    monkeypatch.setenv("RFB_ATOS_DADOS", str(tmp_path))


def test_pedido_do_lote_usa_a_mesma_chave_da_chamada_direta():
    ato = {**_ato(), "texto_completo": CURTO}
    (p,), fora = lc.pedidos([ato])
    partes, sistema = cn.preparar(ato, CURTO)
    usuario = cn.mensagem_usuario(ato, partes[0], 1)
    assert p["arquivo"] == str(cn.arquivo_resposta(ATO, cn.MODELO_PADRAO, sistema, usuario,
                                                   cn.MAX_TOKENS))
    assert p["params"]["model"] == cn.MODELO_PADRAO and "temperature" not in p["params"]
    assert fora == []
    so_ementa = {**_ato(id=5), "texto_completo": "## EMENTA\nx"}
    assert lc.pedidos([so_ementa])[1][0]["ato_id"] == 5


def test_lote_ainda_rodando_nao_grava_nada():
    lote = LoteFalso(terminado=False)
    (bid,) = lc.enviar([{**_ato(), "texto_completo": CURTO}], _cliente(lote), saida=lambda _t: None)
    assert lc.baixar(bid, _cliente(lote), saida=lambda _t: None) is None
    assert lc.manifestos("enviado")[0]["batch_id"] == bid


@precisa_banco
def test_enviar_baixar_e_aplicar_sem_pagar_as_materias_de_novo(conn):  # noqa: F811
    conn.execute("UPDATE rfb_atos.ato_content SET texto_completo = %s WHERE ato_id = %s",
                 (CURTO, ATO))
    atos = cn.carregar_atos(conn, ids=[ATO])
    lote = LoteFalso()
    (bid,) = lc.enviar(atos, _cliente(lote), saida=lambda _t: None)
    assert len(lote.lotes[bid]) == 1
    direto = FalsoModelo()
    resultados = lc.aplicar(bid, _cliente(lote), conectar=_conectar_como_writer, chamar=direto,
                            embed=_vetores, paralelo=1, saida=lambda _t: None)
    assert resultados[0]["materias"] >= 1 and resultados[0]["sem_vetor"] == 0
    assert direto.mensagens == [] and direto.sinais == resultados[0]["materias"]
    m = lc.manifestos()[0]
    assert m["estado"] == "aplicado" and m["resultado"]["respostas"] == 1
    # enviar de novo: a resposta já está em disco, nada vai para o lote
    assert lc.pedidos(atos)[0] == []


# ---------------------------------------------------------------------------
# Testes de falsificação da revisão 4-LLM da PR #6
# ---------------------------------------------------------------------------

def _mudo(_t):
    return None


def test_ato_nao_e_dividido_entre_lotes():
    pedidos = ([{"ato_id": 1, "custom_id": f"a1-{i}"} for i in range(3)]
               + [{"ato_id": 2, "custom_id": "a2-0"}])
    fatias = lc._fatias(pedidos, 2)
    assert [sorted({p["ato_id"] for p in f}) for f in fatias] == [[1], [2]]
    assert len(fatias[0]) == 3            # o ato inteiro, mesmo passando do tamanho


def test_queda_depois_do_create_nao_reenvia(monkeypatch):
    lote = LoteFalso(falha_no_create=True)
    with pytest.raises(ConnectionError):
        lc.enviar([{**_ato(), "texto_completo": CURTO}], _cliente(lote), saida=_mudo)
    assert ATO in lc.em_voo()             # o manifesto provisório segura o ato por 24 h
    assert lc.manifestos("criando")[0]["atos"] == [ATO]


def test_teto_pula_o_ato_caro_e_manda_os_baratos(monkeypatch):
    caro = {**_ato(id=1), "texto_completo": "Art. 1º PIS e Cofins. " + "palavra " * 20000}
    barato = {**_ato(id=2), "texto_completo": CURTO}
    lote = LoteFalso()
    (bid,) = lc.enviar([caro, barato], _cliente(lote), teto_usd=lc.estimar_lote([barato]) + 0.001,
                       saida=_mudo)
    assert {r["custom_id"].split("-")[0] for r in lote.lotes[bid]} == {"a2"}


def test_resposta_cortada_em_disco_pede_as_metades():
    texto = "Art. 1º PIS e Cofins. " + "\n\n".join(f"Art. {i}. " + "texto " * 300
                                                   for i in range(2, 12))
    ato = {**_ato(), "texto_completo": texto}
    (p,), _ = lc.pedidos([ato])
    Path(p["arquivo"]).parent.mkdir(parents=True, exist_ok=True)
    Path(p["arquivo"]).write_text('{"texto": "{", "parada": "max_tokens", "uso": {}}',
                                  encoding="utf-8")
    metades, _ = lc.pedidos([ato])
    # a mesma divisão que o _categorizar_parte faria (pelos cabeçalhos, ~metade do trecho)
    assert len(metades) >= 2 and p["custom_id"] not in {x["custom_id"] for x in metades}


@precisa_banco
def test_lote_que_falha_inteiro_nao_vira_chamada_direta(conn):  # noqa: F811
    conn.execute("UPDATE rfb_atos.ato_content SET texto_completo = %s WHERE ato_id = %s",
                 (CURTO, ATO))
    lote = LoteFalso(falhar=True)
    (bid,) = lc.enviar(cn.carregar_atos(conn, ids=[ATO]), _cliente(lote), saida=_mudo)
    direto = FalsoModelo()
    (r,) = lc.aplicar(bid, _cliente(lote), conectar=_conectar_como_writer, chamar=direto,
                      embed=_vetores, paralelo=1, saida=_mudo)
    assert r.get("pendente") and direto.mensagens == []
    assert conn.execute("SELECT COALESCE(metadados, '{}'::jsonb) ? 'categorizacao_revisao' "
                        "FROM rfb_atos.ato WHERE id = %s", (ATO,)).fetchone() == (False,)
    assert ATO in {a["id"] for a in cn.carregar_atos(conn, pendentes=True, limit=100)}


@precisa_banco
def test_vetor_que_falha_depois_de_gravar_e_completado_no_reaplicar(conn):  # noqa: F811
    conn.execute("UPDATE rfb_atos.ato_content SET texto_completo = %s WHERE ato_id = %s",
                 (CURTO, ATO))
    lote = LoteFalso()
    (bid,) = lc.enviar(cn.carregar_atos(conn, ids=[ATO]), _cliente(lote), saida=_mudo)

    def vetor_fora_do_ar(_textos):
        raise RuntimeError("OpenAI fora do ar")

    (r,) = lc.aplicar(bid, _cliente(lote), conectar=_conectar_como_writer, chamar=FalsoModelo(),
                      embed=vetor_fora_do_ar, paralelo=1, saida=_mudo)
    assert r.get("erro") and lc.manifestos()[0]["estado"] == "baixado"
    antes = conn.execute("SELECT count(*) FROM rfb_atos.ato_materia WHERE ato_id = %s",
                         (ATO,)).fetchone()[0]
    direto = FalsoModelo()
    (r,) = lc.aplicar(bid, _cliente(lote), conectar=_conectar_como_writer, chamar=direto,
                      embed=_vetores, paralelo=1, saida=_mudo)
    assert r["completado"] and r["sem_vetor"] == 0 and direto.mensagens == []
    assert conn.execute("SELECT count(*) FROM rfb_atos.ato_materia WHERE ato_id = %s",
                        (ATO,)).fetchone()[0] == antes             # nada gravado de novo
    assert lc.manifestos()[0]["estado"] == "aplicado"
