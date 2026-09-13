---
name: td:tax-intelligence:rfb-atos:buscar
description: Busca simples — query + facetas → lista de hits (sem deep research)
model: haiku
---

# td:tax-intelligence:rfb-atos:buscar

Busca **híbrida** (FTS BM25 + vetorial cosine + Reciprocal Rank Fusion) com filtros estruturados.

Para deep research com planner/reflexão/relatório, use `/td:tax-intelligence:rfb-atos:pesquisar`.

## Uso

```bash
cd C:/td-rfb-atos/scripts

# Busca livre
py retrieve.py search "exclusao do ICMS PIS COFINS"

# Com filtros
py retrieve.py search "credito presumido agroindustria" --tributos PIS COFINS --temas CREDITAMENTO --tipos SOLUCAO_CONSULTA --eficacia vinculante_geral --limit 20

# Output JSON
py retrieve.py search "..." --format json

# Listar facetas disponíveis
py retrieve.py facetas
```

## Filtros

- `--tributos PIS COFINS IRPJ ...` — códigos taxonomia.tributos
- `--temas CREDITAMENTO IRPJ_CSLL ...` — códigos tema_macro
- `--tipos SOLUCAO_CONSULTA SOLUCAO_DIVERGENCIA INSTRUCAO_NORMATIVA ...` — tipos de ato
- `--eficacia vinculante_geral vinculante_local orientativo` — alcance
- `--data-ini 2024-01-01 --data-fim 2025-12-31` — janela temporal
- `--limit N` — top N (default 15)

## Output

Para cada hit:
- `tipo_ato + numero + orgao_emissor + data_publicacao`
- eficácia + status_vigencia + score RRF
- ementa (200 chars)
- trecho do chunk (400 chars)

## Pré-requisitos

- `/td:tax-intelligence:rfb-atos:embed` rodado (chunks com embedding)
