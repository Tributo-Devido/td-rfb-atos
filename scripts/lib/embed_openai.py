"""embed_openai.py — vetores OpenAI (`text-embedding-3-large`, 3072) para o rfb_atos.

Trazido de `td-skills/td-creditos/scripts/lib/embed_openai.py` em 13/09/2026, para o repositório
não depender de outro checkout. Mesma API: `embed_texts` (async), `embed_texts_sync`,
`vector_literal`. Diferenças:
    - a chave vem de `credenciais.resolver("openai")` (variável de ambiente → SSM
      `/td/llm/openai-api-key`) e só é buscada na hora do uso — importar não exige chave;
    - o SDK `openai` é importado só na hora do uso e o retry é local (sem `tenacity`);
    - `cliente=` permite injetar um cliente (testes).

Um só modelo de embedding no ecossistema — a lição do CARF: mesma dimensão não é mesmo modelo.
NUNCA logue a chave.
"""
from __future__ import annotations

import asyncio
import time

from credenciais import resolver

DEFAULT_MODEL = "text-embedding-3-large"
DEFAULT_DIM = 3072
TENTATIVAS = 5


def _esperas() -> list[int]:
    """Backoff exponencial entre tentativas: 2, 4, 8, 16 s (teto 30)."""
    return [min(30, 2 * 2 ** i) for i in range(TENTATIVAS - 1)]


def _normalizar(textos: list[str]) -> list[str]:
    # a API rejeita string vazia
    return [t if (t and t.strip()) else "(vazio)" for t in textos]


def _cliente_sync():
    chave = resolver("openai")  # antes do import: falta de chave dá erro claro mesmo sem o SDK
    from openai import OpenAI
    return OpenAI(api_key=chave)


def _cliente_async():
    chave = resolver("openai")
    from openai import AsyncOpenAI
    return AsyncOpenAI(api_key=chave)


def _com_retry(chamar, *, dormir=None):
    dormir = dormir or time.sleep
    for espera in _esperas():
        try:
            return chamar()
        except Exception:
            dormir(espera)
    return chamar()


async def _com_retry_async(chamar, *, dormir=None):
    dormir = dormir or asyncio.sleep
    for espera in _esperas():
        try:
            return await chamar()
        except Exception:
            await dormir(espera)
    return await chamar()


def embed_texts_sync(
    texts: list[str],
    model: str = DEFAULT_MODEL,
    dimensions: int = DEFAULT_DIM,
    batch_size: int = 50,
    *,
    cliente=None,
) -> list[list[float]]:
    """Vetoriza em lotes, sem concorrência. Bom para menos de ~1 mil textos."""
    cliente = cliente or _cliente_sync()
    saida: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        lote = _normalizar(texts[i:i + batch_size])
        resp = _com_retry(lambda lote=lote: cliente.embeddings.create(
            input=lote, model=model, dimensions=dimensions))
        saida.extend(d.embedding for d in resp.data)
    return saida


async def embed_texts(
    texts: list[str],
    model: str = DEFAULT_MODEL,
    dimensions: int = DEFAULT_DIM,
    batch_size: int = 50,
    concurrency: int = 10,
    *,
    cliente=None,
) -> list[list[float]]:
    """Vetoriza em lotes concorrentes; devolve os vetores na mesma ordem dos textos."""
    cliente = cliente or _cliente_async()
    sem = asyncio.Semaphore(concurrency)
    norm = _normalizar(texts)
    lotes = [norm[i:i + batch_size] for i in range(0, len(norm), batch_size)]
    resultados: list[list[list[float]] | None] = [None] * len(lotes)

    async def trabalhar(idx: int, lote: list[str]) -> None:
        async with sem:
            resp = await _com_retry_async(lambda: cliente.embeddings.create(
                input=lote, model=model, dimensions=dimensions))
            resultados[idx] = [d.embedding for d in resp.data]

    await asyncio.gather(*[trabalhar(i, lote) for i, lote in enumerate(lotes)])
    saida: list[list[float]] = []
    for r in resultados:
        assert r is not None
        saida.extend(r)
    return saida


def vector_literal(vec: list[float]) -> str:
    """Literal `[v1,v2,...]` aceito por `vector`/`halfvec` do pgvector (6 casas)."""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
