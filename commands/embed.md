---
name: td:tax-intelligence:rfb-atos:embed
description: Gera embeddings Gemini text-embedding-004 (1024-dim) para ato_materia + ato_chunks
model: haiku
---

# td:tax-intelligence:rfb-atos:embed

Gera embeddings via **Gemini text-embedding-004** (1024 dimensões — mesma família do td-carf, permite busca cross-base).

## Uso

```bash
cd C:/td-rfb-atos/scripts

# Embedding completo (matérias + chunks)
py embed_chunks.py

# Limite
py embed_chunks.py --limit 100

# Só matérias (skip chunks granulares)
py embed_chunks.py --only-materias

# Só chunks (skip matérias — assume que já foram embeddadas)
py embed_chunks.py --only-chunks
```

## O que faz

### Para `ato_materia` (granularidade temática)
- Constrói texto a embeddar: `[tema_macro / tema_especifico] + tags + ementa_trecho + fato_consultado + solucao + fundamentacao_resumo`
- Atualiza `ato_materia.embedding` (vetor 1024-dim)

### Para `ato_chunks` (granularidade textual)
- Para atos com `content_disponivel = TRUE`: chunker simples (parágrafos até ~4000 chars)
- Embedda + tsvector PT + metadata desnormalizada (tributos, temas, eficácia, status — para filtro rápido no retrieval)
- Inclui sempre `ementa` como `seq=0` (chunk especial tipo `ementa`)

## Custo

Gemini text-embedding-004 é gratuito até quota generosa. Backfill ~157k matérias + ~50-80k chunks ≈ $0-15.

## Pré-requisitos

- `/td:tax-intelligence:rfb-atos:enrich` rodado (matérias criadas)
- `GOOGLE_API_KEY` em `scripts/.env`

## Próximo passo

`/td:tax-intelligence:rfb-atos:buscar` ou `/td:tax-intelligence:rfb-atos:pesquisar`.
