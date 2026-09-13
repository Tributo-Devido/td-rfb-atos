---
name: td:tax-intelligence:rfb-atos:ajuda
description: Guia completo de td-rfb-atos — pipeline + pesquisa de atos normativos da Receita Federal
model: haiku
---

# td:tax-intelligence:rfb-atos:ajuda

Guia da skill **td-rfb-atos** (área: tax-intelligence).

## O que faz

Coleta, enriquece, categoriza, indexa e pesquisa todos os atos normativos da Receita Federal:

- Soluções de Consulta (SC) — orientações vinculantes
- Soluções de Divergência (SD)
- Instruções Normativas, Decretos, Portarias
- Atos Declaratórios (ADI, ADE, ADN), Pareceres Normativos
- Notas Técnicas, Resoluções, Ordens de Serviço

## Comandos disponíveis

| Comando | Ação |
|---|---|
| `/td:tax-intelligence:rfb-atos:setup` | Sobe Postgres local (Docker) e roda migrations |
| `/td:tax-intelligence:rfb-atos:crawl` | Coleta atos do SIJUT2 RFB (incremental por padrão) |
| `/td:tax-intelligence:rfb-atos:enrich` | Pipeline de enriquecimento: download PDF → extração → categorização Haiku |
| `/td:tax-intelligence:rfb-atos:embed` | Gera embeddings Gemini (matérias + chunks) |
| `/td:tax-intelligence:rfb-atos:pipeline` | Executa crawl → enrich → embed em sequência (uso diário) |
| `/td:tax-intelligence:rfb-atos:buscar` | Busca simples: query + facetas → lista de hits |
| `/td:tax-intelligence:rfb-atos:pesquisar` | Deep research: planner + reflexão + relatório |
| `/td:tax-intelligence:rfb-atos:stats` | Volumetria, frescor, cobertura |

## Quando usar

- Pesquisa de orientação RFB sobre tributo/dispositivo
- Antes de redigir tese: validar se há SC vinculante recente sobre o ponto
- Ao analisar dossiê de empresa: verificar SC do CNAE/setor do cliente
- Cross-base com CARF: `td:carf:pesquisar` (contencioso) + `td:tax-intelligence:rfb-atos:pesquisar` (orientação prévia)

## Arquitetura

Banco LOCAL (Postgres em Docker, porta 5435). Schema unificado:

- `atos` (todos os tipos) → `ato_content` → `ato_materia` (1+ por ato) → `ato_chunks`
- `ato_relacao` (grafo: revoga / altera / regulamenta / cita / uniformiza / interpretado_por)
- `materia_tributo`, `materia_dispositivo`, `materia_cnae`
- Taxonomia controlada em `taxonomia.json` (única fonte de verdade)
- Schemas tipados de `metadata_tematico` por `tema_especifico`

## Diferença vs td-carf

| | CARF | RFB-atos |
|---|---|---|
| Natureza | Decisão colegiada (litígio) | Orientação vinculante (consulta prévia) |
| Eficácia | Inter partes | Cosit = vinculante geral; Disit = local |
| Reforma | Não se reforma | Pode ser reformada/revogada/uniformizada por SD |
| Vigência | Definitiva | Crítica — pode ficar caduca se norma base mudar |

## Estado atual

v0.1.0 — design + scaffolding completo. Próximo passo: aplicar migrations no banco local + rodar primeiro crawl.

Ver `SKILL.md` e `references/architecture.md` para detalhes.
