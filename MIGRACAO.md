# Migração — o que falta subir de `C:\td-skills\td-rfb-atos`

Este repo veio de um `git subtree split` do `Tributo-Devido/tdax-3.0`. A história foi
preservada (2 commits), mas o `tdax-3.0` só tinha o `SKILL.md` e quatro scripts de
migração para a nuvem. **O resto da skill nunca esteve no git.**

Checklist do que precisa vir da máquina do responsável.

## 1. Referências que o `SKILL.md` cita e não existem aqui

Sem estes arquivos a skill não roteia — o `SKILL.md` manda carregá-los e eles não estão.

- [ ] `references/taxonomia.json` — vocabulário controlado; **única fonte de verdade**
      de `tipos_ato`, `orgaos_emissores`, `eficacia`, `status_vigencia`, tributos, temas
- [ ] `references/schemas_metadata_tematico/*.json` — JSON Schema por `tema_especifico`
      (ex.: `CLASSIFICACAO_FISCAL.PRODUTO`)
- [ ] `references/prompts/prompt_extrator_sc.md` — prompt de extração de matéria de SC
- [ ] `references/prompts/prompt_extrator_normativos.md` — idem para IN/Decreto/Portaria
- [ ] `references/architecture.md` — decisões de design, trade-offs, modelo completo

## 2. Migrations anteriores à 010

A 010 pressupõe que `ato`, `ato_content` e `ato_materia` existam. Quem as cria não
está no git — hoje o único registro do schema real são as listas de `INSERT` do
`migrate_to_cloud.py` e o `tests/fixtures/schema_rfb_atos.sql`, que é uma reprodução
para teste, **não** a DDL canônica.

- [ ] `migrations/001_initial_schema.sql`
- [ ] `migrations/002_taxonomia_seed.sql`
- [ ] `migrations/003_migrate_from_normas.sql`

> Nota: as migrations `004`/`005`/`006` que estendem `rfb_atos` na nuvem estão hoje em
> `tdax-3.0/td-creditos/migrations/` — fora da skill. A 006 (`006_rfb_atos_rico.sql`)
> é a que define o modelo rico e o `halfvec(3072)`. Vale decidir se elas vêm para cá
> ou se ficam onde estão; hoje a numeração de migration do `rfb_atos` está partida
> entre dois lugares, e a 010 deste repo pressupõe as de lá.

## 3. O pipeline diário — as sete etapas

Nenhuma delas tem código no git. É a maior lacuna: sem isso o repo documenta um
pipeline que não pode rodar a partir dele.

- [ ] **CRAWL** — crawler SIJUT2 (portado de `marketing/normas/`, com UPSERT e incremental)
- [ ] **PROMOTE** — promoção de atos novos
- [ ] **DOWNLOAD PDF** — Selenium/requests
      *(o `scripts/backfill_pdf.py` cobre o caso de reparo, não a coleta corrente)*
- [ ] **EXTRACT** — PDF → texto via MarkItDown
- [ ] **CATEGORIZE** — Haiku 4.5 → matérias com `tema_macro`/`tema_especifico`
- [ ] **EMBED** — legado Gemini 1024 no Docker
      *(o caminho atual, OpenAI 3072 na nuvem, é o `reembed_cloud.py`, que já está aqui)*
- [ ] **RELATE** — extração do grafo revoga/altera/regulamenta/cita

- [ ] `docker-compose.yml` — Postgres local porta 5435 (legado do pipeline de coleta)

## 4. O que **não** deve subir

| item | onde vive | por quê |
|---|---|---|
| `scripts/pdfs/` (30.527 PDFs) | disco / S3 | ~GB; git não é object store |
| `scripts/in2121_original.json` (3,6 MB) | disco / S3 | dump do SIJUT, não é código |
| `scripts/.env` | máquina local / SSM | `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, DSN |
| `ratio-pg-dsn.txt` | máquina local / SSM | credencial de produção |

Todos já estão no `.gitignore`.

⚠️ **Conferir antes de commitar.** O `tdax-3.0` já teve senha versionada e removida
depois (`chore: remover senha do usuario tdax hardcoded do repo`, #5). Segredo que
entra no git não sai: some do HEAD e permanece no histórico. Vale um
`git diff --cached` de olho antes do primeiro push grande, e um `.env.example` com as
chaves em branco no lugar do `.env`.

## 5. Como subir

Do Windows, com o repo já clonado:

```powershell
cd C:\caminho\para\td-rfb-atos            # este repo
robocopy C:\td-skills\td-rfb-atos . /E /XD pdfs __pycache__ .venv /XF .env *_original.json

git status                                 # conferir que nada de segredo apareceu
git switch -c feat/completar-skill
git add -A
git diff --cached --stat                   # ultima olhada antes do commit
git commit -m "feat: pipeline de coleta, taxonomia e migrations 001-003"
git push -u origin feat/completar-skill
```

Depois disso, atualizar o `README.md` (remover o aviso de repo incompleto) e apagar
este arquivo.

## 6. Depois de completo

- [ ] Remover `td-rfb-atos/` do `tdax-3.0` com um ponteiro para este repo — enquanto as
      duas cópias existirem, há duas verdades e nada diz qual vale
- [ ] Decidir o destino das migrations `004`/`005`/`006` (item 2)
- [ ] Ligar o CI: `.github/workflows/ci.yml` já roda os 46 testes contra Postgres
