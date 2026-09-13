---
name: td:tax-intelligence:rfb-atos:pipeline
description: Executa pipeline completo (crawl → download_pdf → extract_content → categorize → embed) — uso diário
model: haiku
---

# td:tax-intelligence:rfb-atos:pipeline

Pipeline orquestrado para uso diário/agendado. Cada etapa é incremental.

## Uso

```bash
cd C:/td-rfb-atos/scripts

# Pipeline completo (incremental)
py run_pipeline.py

# Pular etapas
py run_pipeline.py --skip crawl

# Roda só uma etapa
py run_pipeline.py --only categorize

# Limite por etapa (útil para teste)
py run_pipeline.py --limit 10
```

## Schedule diário (Windows Task Scheduler)

1. Abrir **Task Scheduler** (Agendador de Tarefas)
2. Criar tarefa: "td-rfb-atos pipeline diário"
3. Trigger: diariamente, 06:00
4. Action: `cmd.exe /c "cd C:/td-rfb-atos/scripts && py run_pipeline.py >> ../logs/pipeline-%DATE%.log 2>&1"`
5. Run whether user logged on or not

## Custo operacional

~$2-5/mês em LLM + storage local (~10-50 MB/dia em PDFs e content).

## Pré-requisitos

- `/td:tax-intelligence:rfb-atos:setup` feito (banco em pé)
- `scripts/.env` com chaves Anthropic + Google
- Docker Desktop rodando

## Stats

Após cada run, ver:
```bash
py stats.py
```
