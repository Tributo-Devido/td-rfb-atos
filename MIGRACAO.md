# Migração — a skill que vivia em `C:\td-skills\td-rfb-atos`

> **Status em 12/09/2026:** código trazido (branch `feat/completar-skill`), dados de
> execução movidos para `C:\td-rfb-atos-dados`. Este arquivo fica como registro do que
> veio, do que **não existia** na origem e do que continua pendente.

Este repo veio de um `git subtree split` do `Tributo-Devido/tdax-3.0`. A história foi
preservada (2 commits), mas o `tdax-3.0` só tinha o `SKILL.md` e quatro scripts de
migração para a nuvem. O resto da skill nunca tinha estado no git.

## 1. Referências que o `SKILL.md` cita

- [x] `references/taxonomia.json` — vocabulário controlado; **única fonte de verdade**
      de `tipos_ato`, `orgaos_emissores`, `eficacia`, `status_vigencia`, tributos, temas
- [x] `references/schemas_metadata_tematico/*.json` — JSON Schema por `tema_especifico`
- [x] `references/prompts/prompt_extrator_sc.md`
- [x] `references/prompts/prompt_extrator_normativos.md`
- [x] `references/architecture.md`

Vieram também `hierarquia_funcional.md`, `normalizacao_taxonomia.json`,
`ranking_weights.json`, `template-estudo.md`, `timeline-vinculancia.md` e
`exemplo-hipotese-h4.md`.

## 2. Migrations anteriores à 010

- [x] `migrations/001_initial_schema.sql`
- [x] `migrations/002_seed_taxonomia.sql` — a versão anterior deste checklist a chamava
      de `002_taxonomia_seed.sql`; o nome real é este
- [x] `migrations/003_add_id_portal.sql` — **não existe** `003_migrate_from_normas.sql`
      na origem; o nome previsto aqui era suposição
- [x] `migrations/004` a `008` (versão/relação, segmentos, natureza, timeline/conflitos, sinal)
- Não há `009` na origem.

> ⚠️ **A numeração está partida.** As 001–008 daqui são o schema do Postgres **local**
> (Docker 5435). As `004`/`005`/`006` que estendem `rfb_atos` **na nuvem** são outras e
> estão em `tdax-3.0/td-creditos/migrations/` (a 006, `006_rfb_atos_rico.sql`, define o
> modelo rico e o `halfvec(3072)`). A 010 deste repo pressupõe as da nuvem. Decidir se
> elas vêm para cá — e com que número — continua em aberto.

## 3. O pipeline diário

O `run_pipeline.py` real orquestra **cinco** etapas, não sete:

- [x] **CRAWL** — `extract_sijut.py` (o que o `run_pipeline.py` chama) e `fetch_normasinternet2.py`
- [ ] **PROMOTE** — sem script dedicado na origem
- [x] **DOWNLOAD PDF** — `download_pdf.py`
      *(o `backfill_pdf.py` cobre o caso de reparo, não a coleta corrente)*
- [x] **EXTRACT** — `extract_content.py`
- [x] **CATEGORIZE** — `categorize_with_llm.py` (diário) e `categorize_batch.py` (lote)
- [x] **EMBED** — `embed_chunks.py` (legado Gemini 1024 no Docker); o caminho atual,
      OpenAI 3072 na nuvem, é o `reembed_cloud.py`
- [ ] **RELATE** — sem script dedicado na origem

PROMOTE e RELATE apareciam na documentação, mas não havia script próprio para elas. A
conferir se acontecem dentro de `extract_sijut.py` / `fetch_normasinternet2.py`.

- [x] `docker-compose.yml` — Postgres local porta 5435 (legado do pipeline de coleta)

## 4. O que **não** subiu (e onde está)

| item | onde vive | por quê |
|---|---|---|
| `batches/` (4,6 GB de lotes Anthropic) | `C:\td-rfb-atos-dados\batches\` | dado de execução |
| `scripts/pdfs/` (30.527 PDFs) | `C:\td-rfb-atos-dados\scripts\pdfs\` | ~2,5 GB; git não é object store |
| `scripts/results_*.jsonl`, `test_*.jsonl`, `*.batch_id` | `C:\td-rfb-atos-dados\scripts\` | resultado de lote |
| `scripts/in2121_original.json` (3,6 MB) | `C:\td-rfb-atos-dados\scripts\` | dump do SIJUT, não é código |
| `logs/`, `scripts/logs/` | `C:\td-rfb-atos-dados\` | log de execução |
| `outputs/` (estudos por tema) | `C:\td-rfb-atos\outputs\` | consumidos por outras skills nesse caminho; ver `docs/DECISOES.md` |
| `scripts/.env` | `C:\td-rfb-atos\scripts\.env` | `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, DSN |
| `ratio-pg-dsn.txt` | máquina local / SSM | credencial de produção |

Todos estão no `.gitignore`. O `scripts/.env.example` teve o DSN do banco legado
trocado por marcadores antes do commit.

## 5. Como foi trazido

```powershell
# /XC /XN /XO: não sobrescreve nenhum arquivo que já existia no repo
robocopy C:\td-skills\td-rfb-atos C:\td-rfb-atos /E /XC /XN /XO `
  /XD pdfs batches logs outputs __pycache__ .venv venv pgdata .pytest_cache .git `
  /XF .env *_original.json *.jsonl *.batch_id *.pdf
```

67 arquivos novos. Os sete que já existiam nos dois lados (`.gitignore`, `README.md`,
`SKILL.md`, `db.py`, `migrate_to_cloud.py`, `reembed_cloud.py`,
`validate_cloud_parity.py`) ficaram na versão deste repo, que era a mais nova.

## 6. Pendências

- [x] Remover `td-rfb-atos/` do `tdax-3.0` — feito no `main` (commit `06da0fea`, 05/09/2026)
- [x] Ligar o CI: `.github/workflows/ci.yml` roda lint + 46 testes contra Postgres
- [x] Remover a cópia em `C:\td-skills\td-rfb-atos` — removida em 12/09/2026, depois de
      conferir por hash que todo arquivo tinha cópia idêntica no repo ou em
      `C:\td-rfb-atos-dados\_legado-td-skills\`. Os 9 arquivos que eram rastreados no git
      do td-skills (branch `feat/rfb-atos-embed`) aparecem lá como apagados, sem commit
- [ ] Decidir o destino das migrations `004`/`005`/`006` da nuvem (item 2)
- [x] `reembed_cloud.py` e `validate_cloud_parity.py` importavam `lib.embed_openai` do
      `td-creditos` — trazido para `scripts/lib/` em 13/09/2026, com credenciais via
      `scripts/credenciais.py` (sem o `ratio-pg-dsn.txt`)
- [ ] Aplicar a migration 010 no `ratio` (ainda não aplicada)
- [ ] A base parou em 06/07/2026 (último ato: 03/07/2026) — as correções do extrator
      (listagem sem filtro de vigentes, as quatro visões, relações para atos fora da
      base, timeout para atos grandes, PDF oficial pelo DOU) vêm em PR separado
- [ ] Os 26 scripts do pipeline de coleta sem teste (dívida declarada no `pyproject.toml`)
