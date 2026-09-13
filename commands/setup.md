---
name: td:tax-intelligence:rfb-atos:setup
description: Sobe Postgres local em Docker + aplica migrations (DDL + seed taxonomia)
model: haiku
---

# td:tax-intelligence:rfb-atos:setup

One-time. Sobe o Postgres local em Docker (porta 5435) com schema completo do `td-rfb-atos`.

## Instruções

1. **Verificar pré-requisitos:**
   - Docker Desktop rodando
   - Arquivo `scripts/.env` criado a partir de `scripts/.env.example` (com `ANTHROPIC_API_KEY` e `GOOGLE_API_KEY` preenchidos)

2. **Subir o banco:**
   ```bash
   cd C:/td-rfb-atos
   docker compose up -d
   ```
   As migrations em `migrations/*.sql` rodam automaticamente na primeira subida (volume `pgdata` vazio).

3. **Validar:**
   ```bash
   cd scripts
   pip install -r requirements.txt
   py stats.py
   ```
   Deve retornar `0` em todas as tabelas (banco vazio mas com schema correto).

4. **Confirmar pgvector:**
   ```bash
   docker exec td-rfb-atos-pg psql -U td -d td_rfb_atos -c "SELECT extversion FROM pg_extension WHERE extname='vector';"
   ```

## Em caso de erro

- `docker compose down -v` reseta TUDO (apaga volume).
- Migrations só rodam na primeira subida — para reaplicar, dropar volume.

## Próximo passo

`/td:tax-intelligence:rfb-atos:crawl --full` para backfill completo (todos os tipos × todos os anos).
