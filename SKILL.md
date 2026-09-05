---
name: td-rfb-atos
description: Pipeline + base de pesquisa unificada para todos os atos da Receita Federal — Soluções de Consulta, Soluções de Divergência, Instruções Normativas, Decretos, Portarias, ADIs, Pareceres Normativos, etc. Modelo de dados unificado (atos + matérias + grafo de relações), análise estruturada por tema_específico via Haiku, embeddings via Gemini, mesma família arquitetural de td-carf.
metadata:
  version: "0.3.0"
  area_dona: tax-intelligence
  dominio: pesquisa-normativa
  responsavel: guga
  governed: true
---

# td-rfb-atos — Atos normativos da Receita Federal (pipeline + pesquisa)

## O que é

Skill que **coleta, enriquece, categoriza, indexa e pesquisa** todos os atos normativos da Receita Federal:

- Soluções de Consulta (SC) — orientações vinculantes
- Soluções de Divergência (SD) — uniformização entre SCs divergentes
- Instruções Normativas (IN), Decretos, Portarias, Pareceres Normativos
- Atos Declaratórios Interpretativos (ADI), Executivos (ADE), Normativos (ADN)
- Notas Técnicas, Resoluções, Ordens de Serviço
- Demais atos do SIJUT2 (Sistema de Informações Jurídico-Tributárias da RFB)

**Diferenças do td-carf** (decisão colegiada) **vs td-rfb-atos** (orientação vinculante):
- CARF resolve litígio entre contribuinte e fazenda (tese contribuinte × tese fazenda → tese adotada)
- RFB-atos é orientação prévia (fato consultado → solução; sem disputa)
- CARF tem voto de qualidade, eras temporais, divergência entre câmaras
- RFB-atos tem hierarquia de eficácia (Cosit vinculante geral, Disit local), vigência (revogação/reforma), grafo de relações com leis/INs base
- CARF é jurisprudência reativa; RFB-atos é interpretação proativa que vincula o fisco

## Arquitetura

```
Pipeline diário (local, com schedule):
  1. CRAWL          (SIJUT2 RFB → tabela `atos`)
  2. PROMOTE        (atos novos com tipo='SC' ganham flag pdf, etc)
  3. DOWNLOAD PDF   (Selenium/requests; nem todo ato tem PDF)
  4. EXTRACT        (PDF → texto via MarkItDown)
  5. CATEGORIZE     (Haiku 4.5 → matérias com tema_macro/tema_específico/metadata_tematico)
  6. EMBED          (Gemini → ato_chunks com embedding 1024-dim)
  7. RELATE         (extrai grafo: revogação/alteração/citação entre atos)

Banco (Postgres ratio.rfb_atos.* — nuvem; ratio-pg-prod via SSM tunnel localhost:15432):
  rfb_atos.ato                    ← base unificada (todos os tipos de ato)
  rfb_atos.ato_content            ← texto extraído do PDF
  rfb_atos.ato_segmento           ← versões temporais segmentadas (888k)
  rfb_atos.ato_materia            ← N matérias por ato (átomo de análise, com tema_especifico)
                                    + embedding halfvec(3072) OpenAI text-embedding-3-large
                                    + sinal AUTORIZA/VEDA/CONDICIONA/INDETERMINADO (Haiku)
  rfb_atos.materia_tributo        ← N:N matéria↔tributo
  rfb_atos.materia_cnae           ← N:N matéria↔CNAE
  rfb_atos.materia_dispositivo    ← N:N matéria↔dispositivo legal
  rfb_atos.ato_relacao            ← grafo unificado revoga/altera/regulamenta/cita
  rfb_atos.conflito_temporal      ← pares de matérias com mesmo fato + sinais opostos (74k)
  rfb_atos.ato_alteracao_historico ← histórico de modificações por segmento

  rfb_atos.ato_coleta             ← estado da coleta por ato (migration 010)
                                    distingue "o portal não tem PDF" (fato) de
                                    "nunca tentamos" e "tentamos e falhou" (defeitos)
  rfb_atos.v_cobertura_ato        ← view do funil, uma linha por ato

  rfb_atos.taxonomia_*            ← lookups controlados (tributos, temas, setores, tipos_ato)

Banco legado (Docker local, porta 5435): mantido como histórico operacional do pipeline
de coleta/enriquecimento. NÃO é o ponto-de-verdade do produto desde 2026-05-11.

Retrieval (espelha td-carf):
  Python scripts/    ← apenas retrieval (FTS + vetor + RRF + filtros estruturados)
  Claude Code (skill) ← planner / reflexor / synthesizer
```

## Comandos

| Comando | Ação |
|---|---|
| `td:rfb-atos:setup` | One-time: cria schema, migra dados de `normas`/`solucao_de_consulta` para modelo unificado |
| `td:rfb-atos:crawl` | Roda crawler SIJUT2 (incremental por padrão; `--full` para rodar tudo) |
| `td:rfb-atos:enrich` | Pipeline de enriquecimento: PDF → MarkItDown → Haiku → grafo de relações |
| `td:rfb-atos:embed` | Gera embeddings Gemini para `ato_chunks` (incremental) |
| `td:rfb-atos:pipeline` | Executa crawl → enrich → embed em sequência (uso diário) |
| `td:rfb-atos:buscar` | Busca simples: query + facetas → lista de hits |
| `td:rfb-atos:pesquisar` | Deep research: planner + reflexão + relatório (espelha td:carf:pesquisar) |
| `td:rfb-atos:stats` | Volumetria, frescor, cobertura (% com PDF, % categorizado, % embeddado) |
| `td:rfb-atos:auditar` | **Revisão de cobertura** — funil ato→PDF→texto→matéria→embedding, defeitos D1-D8 e fila de trabalho. Somente leitura (`scripts/auditar_cobertura.py`) |
| `td:rfb-atos:backfill` | **Coleta do que falta** — proba o portal e registra o resultado, separando "não tem PDF" de "nunca tentamos". Dry-run por padrão (`scripts/backfill_pdf.py`) |

Runbook completo dos dois: [`docs/REVISAO-COBERTURA.md`](docs/REVISAO-COBERTURA.md).

## Vocabulário controlado

Carregado de `references/taxonomia.json`. Lista canônica de:

- **tipos_ato:** SOLUCAO_CONSULTA, SOLUCAO_DIVERGENCIA, INSTRUCAO_NORMATIVA, DECRETO, PORTARIA, ADI, ADE, ADN, PARECER_NORMATIVO, NOTA_TECNICA, RESOLUCAO, ORDEM_SERVICO, …
- **orgaos_emissores:** COSIT, DISIT_SRRF01..10, DIANA_SRRF01..10, COANA, RFB
- **eficacia:** vinculante_geral (Cosit), vinculante_local (Disit/Diana), orientativo
- **status_vigencia:** vigente, revogada, reformada, caduca, em_divergencia
- **tributos / temas / regimes / setores / dispositivos / fundamentação:** cobertura completa (porte do CARF + adições para orientação prévia)

**Importante:** `tema_especifico` tem **schema próprio de metadata** definido em `references/schemas_metadata_tematico/{TEMA}.json`.

Exemplo: `CLASSIFICACAO_FISCAL.PRODUTO` exige `metadata_tematico = { ncm_pretendido_contribuinte, ncm_defendido_fazenda, ncm_definido, requisitos_tecnicos[], norma_classificadora }`.

## Modelo de dados (resumo)

```
atos                    ← uma linha por ato (qualquer tipo)
   ├── id, tipo_ato, numero, orgao_emissor, data_publicacao
   ├── ementa, link, pdf_disponivel, content_disponivel
   ├── status_vigencia, vigencia_inicio, vigencia_fim
   ├── eficacia (vinculante_geral / vinculante_local / orientativo)
   └── metadata JSONB (campos específicos do tipo)
        ↓ 1:1
   ato_content (texto bruto)
        ↓ 1:N
   ato_materia                         ← ÁTOMO DE ANÁLISE
       ├── ordem, natureza
       ├── tema_macro, tema_especifico, subtema, tags[]
       ├── (CARF) tese_contribuinte / tese_fazenda / tese_adotada / resultado
       ├── (RFB)  fato_consultado / solucao / fundamentacao
       ├── ementa_trecho
       └── metadata_tematico JSONB (schema definido por tema_especifico)
            ↓ N:N
       materia_tributo / materia_dispositivo / materia_cnae / materia_fundamentacao
        ↓ 1:N
   ato_chunks (embedding + tsvector + metadata para filtro)

ato_relacao              ← grafo unificado entre atos
   (origem_id, destino_id, tipo_relacao, data, parcial)
   tipo_relacao: revoga, altera, reforma, regulamenta, cita, contradiz, precedente_de
```

## Fluxo `td:rfb-atos:pesquisar` (deep research)

Mesmo pipeline do td:carf:pesquisar:

1. **PLANNER** (Claude) — decompõe pergunta em sub-questões com facetas
2. **EXECUTOR** (loop) — busca híbrida + reflexão 6 eixos (cobertura, divergências, vigência, etc)
3. **SYNTHESIZER** (Claude) — relatório estruturado com citações textuais

Eixos de reflexão **adaptados para SC** (vs CARF):
1. Cobertura por **órgão** (tem Cosit + Disit?)
2. Cobertura por **vigência** (só vigentes ou inclui revogadas para histórico?)
3. **Divergências entre SCs** (tem SD que uniformizou?)
4. **Marco normativo** (a lei/IN base mudou após a SC?)
5. **Conformidade STF/STJ** (Tema vinculante posterior contradiz a SC?)
6. **Schema de metadata** (atos do mesmo tema_específico devem ter metadata_tematico comparável)

## Configuração

**Banco canônico (NUVEM):** `ratio.rfb_atos.*` em `ratio-pg-prod`, acessado via SSM tunnel em `localhost:15432`.

```
DSN nuvem: lido de C:\Users\tribu\.claude-tg-bot\ratio-pg-dsn.txt (formato postgresql://...:15432/ratio)
Watchdog do tunnel: ~/.claude-tg-bot/subir-tunnel-watchdog.ps1 (reabre automaticamente)
```

Em código Python, use `db.py`:

```python
from db import get_conn  # default: nuvem com search_path = rfb_atos, public
with get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM rfb_atos.ato_materia WHERE sinal = 'AUTORIZA'")
```

Para forçar Docker local (legado, históricos), use uma das opções:

```bash
# CLI flag
python script.py --local

# Env var explícita
export RFB_ATOS_DSN=postgresql://td:td@localhost:5435/td_rfb_atos
python script.py
```

**Embeddings:** OpenAI `text-embedding-3-large` (3072-dim, halfvec). Helper compartilhado em `c:/td-skills/td-creditos/scripts/lib/embed_openai.py`. API key vem do `.env` do td-carf.

**Banco LOCAL Docker (legado para pipeline de coleta):**

```bash
docker compose up -d    # sobe Postgres local porta 5435 (apenas para crawl/enrich/embed do pipeline)
docker compose down
```

`.env` em `scripts/` (legado):
```
PG_DSN=postgresql://td:td@localhost:5435/td_rfb_atos    # legado — só pipeline de coleta
GOOGLE_API_KEY=...                                       # embeddings legados gemini 1024 (Docker)
ANTHROPIC_API_KEY=sk-...                                 # categorização via Haiku 4.5
```

## Quando usar

- Pesquisa de orientação RFB sobre tributo/dispositivo (`td:rfb-atos:pesquisar`)
- Antes de redigir tese: validar se há SC vinculante recente sobre o ponto
- Ao analisar dossiê de empresa: verificar SC do CNAE/setor do cliente
- Cross-base com CARF: usar `td:carf:pesquisar` para contencioso + `td:rfb-atos:pesquisar` para orientação prévia (mesma dim de embedding 1024 permite busca cross-skill)

## Referências (carregar quando necessário)

- `references/architecture.md` — decisões de design, trade-offs, modelo de dados completo
- `references/taxonomia.json` — vocabulário controlado (única fonte de verdade)
- `references/schemas_metadata_tematico/*.json` — schemas JSON Schema por tema_específico
- `references/prompts/prompt_extrator_sc.md` — prompt LLM para extrair matérias de SC
- `references/prompts/prompt_extrator_normativos.md` — prompt LLM para IN/Decreto/Portaria
- `migrations/001_initial_schema.sql` — DDL completo
- `migrations/002_taxonomia_seed.sql` — seed de taxonomia
- `migrations/003_migrate_from_normas.sql` — migração dos dados existentes

## Status atual

- v0.3.0 (2026-09-05) — **revisao de cobertura + coleta governada**
  - `scripts/auditar_cobertura.py` (somente leitura): funil ato -> PDF -> texto ->
    materia -> embedding, defeitos D1-D8, recorte por tipo/ano, holofote nos atos
    nomeados no diagnostico do td-legislacao (D-19/D-20).
  - `migrations/010_ato_coleta.sql`: `ato_coleta` (estado da coleta por ato),
    `v_cobertura_ato` e o invariante `ato_analise_exige_conteudo` (NOT VALID).
  - `scripts/backfill_pdf.py`: proba o portal e registra o resultado. Dry-run por
    padrao; `--probe` confere o contrato antes de qualquer escrita.
  - 46 testes contra Postgres real (`tests/`). Runbook em `docs/REVISAO-COBERTURA.md`.
  - ⚠️ o contrato HTTP com o SIJUT ainda **nao** foi exercitado contra o portal --
    rodar `--probe 5` antes do primeiro `--aplicar` em lote.
- v0.2.0 (2026-05-11) — **base operacional, migrada para a nuvem `ratio.rfb_atos.*`**
  - 99.868 atos, 41.164 matérias com embedding OpenAI 3072 + sinal classificado (Haiku),
    888.132 segmentos temporais, 74.578 conflitos temporais, 142.812 ligações
    matéria↔dispositivo. Schema rico (Track A da migração ratio-juris 2026-05-11).
  - `db.py` agora default → nuvem; `--local` para Docker.
  - Pipeline de coleta/enriquecimento continua no Docker local; sync para nuvem é manual
    (rodar `scripts/migrate_to_cloud.py` é idempotente).
- v0.1.0 — design + DDL + scaffolding (era no Docker local).
- Crawler SIJUT2 portado de `marketing/normas/` com correções (UPSERT, incremental, todos os tipos de ato).
