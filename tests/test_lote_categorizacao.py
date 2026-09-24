"""lote_categorizacao.py — envio, download e aplicação com um cliente de lote falso."""
from __future__ import annotations

import os
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

    def __init__(self, terminado: bool = True):
        self.modelo = FalsoModelo()
        self.lotes: dict[str, list] = {}
        self.terminado = terminado

    def create(self, requests):
        lote_id = f"msgbatch_{len(self.lotes) + 1}"
        self.lotes[lote_id] = requests
        return SimpleNamespace(id=lote_id)

    def retrieve(self, lote_id):
        return SimpleNamespace(processing_status="ended" if self.terminado else "in_progress")

    def results(self, lote_id):
        for req in self.lotes[lote_id]:
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
