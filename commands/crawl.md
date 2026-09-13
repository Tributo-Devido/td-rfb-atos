---
name: td:tax-intelligence:rfb-atos:crawl
description: Coleta atos do SIJUT2 RFB para a tabela atos (incremental por padrão)
model: haiku
---

# td:tax-intelligence:rfb-atos:crawl

Crawler do portal SIJUT2 da Receita Federal (`normas.receita.fazenda.gov.br/sijut2consulta/`).

## Modos

```bash
cd C:/td-rfb-atos/scripts

# Incremental (ano corrente + ano anterior, todos os tipos com sijut_value)
py extract_sijut.py

# Backfill completo (1983-atual, todos os tipos) — UMA VEZ
py extract_sijut.py --full

# Ano específico
py extract_sijut.py --ano 2025

# Múltiplos anos
py extract_sijut.py --anos 2023 2024 2025

# Subset de tipos (códigos da taxonomia.json)
py extract_sijut.py --tipos SOLUCAO_CONSULTA SOLUCAO_DIVERGENCIA
```

## O que faz

1. Carrega `references/taxonomia.json` para descobrir tipos com `sijut_value` mapeado
2. Para cada (tipo, ano), faz scraping HTML do SIJUT2
3. Parser BS4 extrai linhas da tabela
4. UPSERT na tabela `atos` pela chave natural `(tipo_ato, numero, orgao_emissor, data_publicacao)` — duplicatas são ignoradas, mudanças de ementa atualizam.

## Volumetria esperada

- Backfill completo: ~150-200k atos (vai depender do range histórico)
- Incremental diário (ano corrente + anterior): ~50-1500 novos atos por dia (volume real RFB ~50/dia)

## Logs

Stdout. Captura no Windows Task Scheduler para arquivo se quiser.

## Pré-requisitos

- `/td:tax-intelligence:rfb-atos:setup` rodado
- Conexão internet
- `scripts/.env` configurado (PG_DSN apontando para Postgres local)

## Próximo passo

`/td:tax-intelligence:rfb-atos:enrich` para baixar PDFs + extrair texto + categorizar via LLM.
