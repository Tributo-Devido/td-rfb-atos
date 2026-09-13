# td-rfb-atos

Pipeline e base de pesquisa dos **atos normativos da Receita Federal** — Soluções de
Consulta, Soluções de Divergência, Instruções Normativas, Portarias, ADIs, Pareceres
Normativos e demais atos do SIJUT2.

Base canônica: `ratio.rfb_atos.*` (Postgres `ratio-pg-prod`, via tunnel SSM em
`localhost:15432`). ~101 mil atos, ~41 mil matérias com embedding OpenAI 3072.

O roteador da skill é o [`SKILL.md`](SKILL.md).

---

## De onde veio

O repositório nasceu de um `git subtree split` do `Tributo-Devido/tdax-3.0`, que só tinha
o `SKILL.md` e quatro scripts de migração para a nuvem. O resto da skill — pipeline de
coleta, taxonomia, prompts, comandos e as migrations do schema local — vivia apenas em
`C:\td-skills\td-rfb-atos`, fora do git. Foi trazido para cá em 12/09/2026. O que veio,
o que não existia na origem e o que ficou pendente está em [`MIGRACAO.md`](MIGRACAO.md).

## O que está aqui

| caminho | o que é |
|---|---|
| `SKILL.md` | roteador da skill: arquitetura, modelo de dados, comandos |
| `commands/` | os comandos `/td:rfb-atos:*` (setup, crawl, enrich, embed, buscar, pesquisar, pipeline, stats) |
| `references/` | taxonomia (única fonte de verdade), prompts de extração, schemas por tema, arquitetura |
| `scripts/run_pipeline.py` | pipeline diário: crawl → download → extract → categorize → embed |
| `scripts/extract_sijut.py`, `fetch_normasinternet2.py` | coleta no SIJUT2 |
| `scripts/download_pdf.py`, `extract_content.py` | PDF do ato e texto (MarkItDown) |
| `scripts/categorize_with_llm.py`, `categorize_batch.py` | matérias via Haiku 4.5 (online e em lote) |
| `scripts/embed_chunks.py`, `retrieve.py` | embeddings legados (Gemini 1024) e busca híbrida |
| `scripts/timeline.py`, `detectar_conflitos.py`, `classificar_sinal_haiku.py` | linha do tempo, conflitos e sinal AUTORIZA/VEDA |
| `scripts/db.py` | resolução de DSN + conexão (nuvem por padrão, `--local` para Docker) |
| `scripts/migrate_to_cloud.py`, `reembed_cloud.py`, `validate_cloud_parity.py` | Docker local → `ratio.rfb_atos`, embeddings OpenAI 3072, paridade |
| `scripts/auditar_cobertura.py` | **revisão de cobertura** — o funil e os defeitos D1-D8 |
| `scripts/backfill_pdf.py` | **coleta do que falta** — proba o portal e registra o resultado |
| `migrations/001` … `008` | schema do Postgres local (Docker 5435) |
| `migrations/010_ato_coleta.sql` | nuvem: estado de coleta por ato + view do funil + invariante |
| `docker-compose.yml` | Postgres local porta 5435 (legado do pipeline de coleta) |
| `docs/REVISAO-COBERTURA.md` | runbook da revisão e da coleta |
| `docs/DECISOES.md` | decisões tomadas e o porquê |
| `tests/` | 46 testes contra Postgres real |

Os scripts do pipeline de coleta não têm teste e estão dispensados só das regras
cosméticas do lint (bloco "dívida declarada" no `pyproject.toml`). `reembed_cloud.py` e
`validate_cloud_parity.py` ainda importam `lib.embed_openai`, que mora em `td-creditos` —
não rodam sozinhos a partir deste repositório.

## Dados fora do git

| o quê | onde |
|---|---|
| lotes Anthropic (`batches/`, 4,6 GB) | `C:\td-rfb-atos-dados\batches\` |
| acervo de PDFs (30.527 arquivos) | `C:\td-rfb-atos-dados\scripts\pdfs\` — `PDF_DIR` no `.env` |
| resultados de lote (`results_*.jsonl`, `test_*.jsonl`), `in2121_original.json` | `C:\td-rfb-atos-dados\scripts\` |
| logs | `C:\td-rfb-atos-dados\logs\` e `C:\td-rfb-atos-dados\scripts\logs\` — `LOG_DIR` |
| estudos (`outputs/wip/<tema>/v1`, `v2`) | `C:\td-rfb-atos\outputs\` — dentro do repo, ignorado pelo git |
| `scripts/.env` (chaves e DSN) | `C:\td-rfb-atos\scripts\.env` — ignorado pelo git |

Os estudos ficam em `outputs/`, e não na pasta de dados, porque o td-creditos, o
td-mapeamento-fiscal e o `/td:mapeamento:aplicar` os procuram em
`td-rfb-atos/outputs/wip/<tema>/`. Se devem ser versionados é decisão em aberto — ver
[`docs/DECISOES.md`](docs/DECISOES.md).

## Revisão de cobertura — o problema central

A regra do produto é simples: **todo ato que tem PDF no portal deve estar baixado,
categorizado e com embedding.** Ato que o portal não publica é fato da vida, não
pendência.

O que impedia cumprir isso não era falta de esforço — era não haver como distinguir
os casos. Até a migration 010, três situações diferentes eram gravadas como
`pdf_disponivel = false`:

| o que aconteceu | é pendência? |
|---|---|
| o portal não publica PDF para o ato | **não** |
| nunca tentamos baixar | **sim** |
| tentamos e falhou (404, timeout, 503) | **sim** |

Colapsadas num único `false`, não existe fila de trabalho. Foi assim que a
**IN RFB 2.121/2022** (`ato_id` 16630) — a IN que consolidou toda a regulamentação de
PIS/COFINS — ficou parada com `status_vigencia = 'nao_disponivel_portal'`, sem uma
linha em `ato_content` e ainda assim com `analise_completa = true`. Consequência na
ponta: os arts. 171, 179 e 185 dela, que decidem dinheiro em crédito de imobilizado,
tiveram de ser lidos por transcrição dentro de Solução de Consulta.

O funil, e quem executa cada etapa:

```
ato ──▶ [1] PDF ──▶ [2] texto ──▶ [3] matéria ──▶ [4] embedding
        portal      ato_content   ato_materia     ato_materia.embedding
```

| etapa | executa |
|---|---|
| 1. ato → PDF | `scripts/backfill_pdf.py` (reparo) e `scripts/download_pdf.py` (coleta corrente) |
| 2. PDF → `ato_content` | `scripts/extract_content.py` (MarkItDown) |
| 3. `ato_content` → `ato_materia` | `scripts/categorize_with_llm.py` / `categorize_batch.py` (Haiku 4.5) |
| 4. `ato_materia` → embedding | `scripts/reembed_cloud.py` |

Detalhes, defeitos D1-D8 e ordem de prioridade em
[`docs/REVISAO-COBERTURA.md`](docs/REVISAO-COBERTURA.md).

## Como rodar

```bash
export RFB_ATOS_DSN='postgresql://...@localhost:15432/ratio'   # tunnel SSM aberto

python scripts/auditar_cobertura.py --out ./relatorio    # medir (somente leitura)
psql "$RFB_ATOS_DSN" -v ON_ERROR_STOP=1 -f migrations/010_ato_coleta.sql
python scripts/backfill_pdf.py                           # o plano, sem rede
python scripts/backfill_pdf.py --probe 5                 # confere o portal, sem gravar
python scripts/backfill_pdf.py --aplicar --ato 16630     # a IN 2.121 primeiro
```

`auditar_cobertura.py` é somente leitura de verdade: abre a conexão com
`default_transaction_read_only = on`, então o Postgres recusa escrita vinda dali.
`backfill_pdf.py` é dry-run por padrão.

> O contrato HTTP com o SIJUT (formato da URL, como o portal sinaliza "sem PDF")
> **ainda não foi exercitado contra o portal** — foi escrito num ambiente sem saída
> para `normas.receita.fazenda.gov.br`. Rodar `--probe 5` e conferir a saída antes do
> primeiro `--aplicar` em lote. O ajuste, se preciso, é em `classificar_resposta()` e
> `resolver_url()`, ambas puras e cobertas por teste.

## Testes

```bash
initdb -D /tmp/pgdata -U postgres --auth=trust
pg_ctl -D /tmp/pgdata -o '-p 15999 -k /tmp' start
createdb -h /tmp -p 15999 -U postgres rfbtest
PGTEST_DSN='postgresql://postgres@/rfbtest?host=/tmp&port=15999' pytest tests/ -q
```

Sem mock de banco, de propósito: o que estes scripts têm de difícil é SQL (`FILTER`,
`EXISTS` correlacionado, `NOT VALID`, `UPDATE ... FROM` com CTE). Um mock validaria a
montagem da string e deixaria passar exatamente a classe de erro que importa.

## Segredos

`.env`, DSNs e certificados estão no `.gitignore` e não entram aqui. O repositório de
origem já teve senha commitada e removida depois — o que sai do HEAD continua no
histórico. Credenciais ficam em SSM Parameter Store ou em arquivo local fora do repo.
