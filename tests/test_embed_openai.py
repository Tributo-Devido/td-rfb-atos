"""Testes offline do `lib/embed_openai.py` — lotes, ordem, retry e falta de chave, sem rede."""
from __future__ import annotations

import asyncio

import pytest

import credenciais
from lib import embed_openai as eo


class _Vetor:
    def __init__(self, v):
        self.embedding = v


class _Resposta:
    def __init__(self, textos):
        # vetor identifica o texto (pelo tamanho) para conferir a ordem
        self.data = [_Vetor([float(len(t))]) for t in textos]


class _Embeddings:
    def __init__(self, falhas=0):
        self.chamadas = []
        self.falhas = falhas

    def create(self, *, input, model, dimensions):
        self.chamadas.append((list(input), model, dimensions))
        if self.falhas:
            self.falhas -= 1
            raise RuntimeError("429 simulado")
        return _Resposta(input)


class _EmbeddingsAsync(_Embeddings):
    async def create(self, *, input, model, dimensions):
        return super().create(input=input, model=model, dimensions=dimensions)


class _Cliente:
    def __init__(self, embeddings):
        self.embeddings = embeddings


def test_vector_literal():
    assert eo.vector_literal([0.1, -2]) == "[0.100000,-2.000000]"


def test_sync_em_lotes_troca_vazio_e_usa_o_modelo_do_ecossistema():
    emb = _Embeddings()
    vetores = eo.embed_texts_sync(["a", "", "  ", "bb", "ccc"], batch_size=2, cliente=_Cliente(emb))
    assert len(vetores) == 5
    assert [len(c[0]) for c in emb.chamadas] == [2, 2, 1]
    assert emb.chamadas[0][0] == ["a", "(vazio)"]
    assert emb.chamadas[0][1:] == ("text-embedding-3-large", 3072)


def test_retry_recupera_falha_transitoria(monkeypatch):
    dormidas = []
    monkeypatch.setattr(eo.time, "sleep", dormidas.append)
    emb = _Embeddings(falhas=2)
    assert eo.embed_texts_sync(["x"], cliente=_Cliente(emb)) == [[1.0]]
    assert dormidas == [2, 4]


def test_retry_desiste_depois_das_tentativas(monkeypatch):
    monkeypatch.setattr(eo.time, "sleep", lambda s: None)
    emb = _Embeddings(falhas=eo.TENTATIVAS)
    with pytest.raises(RuntimeError):
        eo.embed_texts_sync(["x"], cliente=_Cliente(emb))
    assert len(emb.chamadas) == eo.TENTATIVAS


def test_async_preserva_a_ordem_dos_textos():
    textos = ["a", "bb", "ccc", "dddd", "eeeee"]
    vetores = asyncio.run(eo.embed_texts(textos, batch_size=2, concurrency=2,
                                         cliente=_Cliente(_EmbeddingsAsync())))
    assert vetores == [[1.0], [2.0], [3.0], [4.0], [5.0]]


def test_sem_chave_da_erro_claro_antes_de_importar_o_sdk(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(credenciais, "_ssm", lambda p, perfil: None)
    with pytest.raises(credenciais.CredencialAusente):
        eo.embed_texts_sync(["x"])
