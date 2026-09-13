# Plano v2 — recoleta do `rfb_atos`: revogados, versões, carga pendente e atualização

> v2 · 2026-09-13 · pós-revisão 4-LLM (Codex, Gemini, Grok — `PLANO-RECOLETA-2026-09-ANEXO-4LLM.md`).
> A v1 (`PLANO-RECOLETA-2026-09.md`) teve a **F1 aceita com correções** e a **F3 rejeitada** pelos três:
> carregar revogados no banco compartilhado muda o que o time vê. **Aguardando decisão do dono.**
> Nada foi gravado no banco.

## 1. Resumo

A base `ratio.rfb_atos` (101.026 atos) só tem vigentes, parou em 03/07/2026, tem 912 atos
"analisados" sem texto e nenhuma aresta de revogação vinda do portal. A v2 conserta em camadas e
**isola os revogados numa área de espera** até o consumidor do time (`td-analise-piscofins/libs/atos_rfb.py`)
saber lidar com vigência por data. Antes de carregar qualquer coisa, mede o portal inteiro (censo).

## 2. Diagnóstico (fase 0) — com a correção da revisão

Igual à v1, §2, com duas correções:
- **D3 corrigido:** no portal a revogação é o `corSimbolo = 3`, gravado como **`interrompe`**
  ("comando que interrompe a vigência — revoga", `taxonomia.json:108`; `fetch_normasinternet2.py:57`).
  O grafo tem 291 `interrompe`, **todas inferidas por LLM**; nenhuma veio do portal, porque o destino
  (o ato revogado) nunca esteve na base e a aresta era descartada (`:255-258`). O consumidor já tem a
  família `interrompe` e declara `revoga` como ausente (`atos_rfb.py:840-847`) — **não gravar `revoga`**.
- **D7 corrigido:** ~1,1 mil atos novos desde 03/07 (5,5 mil/ano × ~72 dias), não 1,5 mil.
- **D3, número exato (conferência do schema, 13/09):** o grafo tem **5** arestas `interrompe`
  vindas do portal (revogações entre dois atos que já estavam na base) — não zero.
- **D12 (novo, medido na IN 2.121 contra o portal):** em cada visão o portal manda todos os
  segmentos de todas as versões e marca com `omitir` os que não aparecem; **mostrar = `omitir`
  falso** dá uma versão por dispositivo. O extrator antigo (`consolidar_texto_vigente`)
  concatena **todos** — no texto guardado dos atos alterados (~3,6 mil `vigente_alterado`)
  **redações antigas estão misturadas com as atuais** (na IN 2.121: art. 171 três vezes, +16%).
  Vai para a F2c (extrator com a regra) e para a F3 (reprocessar o texto vigente dos alterados,
  com `ato_mudanca`) — é correção do que o time lê hoje; comunicar.
- **Timeout não explica a IN 2.121:** a visão vigente (3,45 MB) respondeu em 2 s; a falha dela
  segue atribuída à baixa avulsa (D5).

## 3. Princípios

- **P1 — Nada muda para o time sem o consumidor pronto.** Aditivo no schema **e** no comportamento:
  revogados ficam em `rfb_atos_staging` até o release com `vigente_em` estar adotado.
- **P2 — Vocabulário do portal, sem invenção.** `status_vigencia` usa os valores do portal
  (`nao_vigente`, `anulada`, `revigorado`…); revogação é aresta `interrompe`; "revogado" é derivado
  na leitura. A situação bruta do portal fica guardada.
- **P3 — Um modelo de embedding** (OpenAI `text-embedding-3-large`).
- **P4 — Rollback real:** toda mudança em linha existente vai para um log com `run_id`; o usuário de
  escrita não apaga nas tabelas principais.
- **P5 — Medir antes de estimar:** censo do portal e ensaio de carga definem volume, ritmo e custo.

## 4. Fases

### F1 — Fundação (sem portal, sem expor nada novo)

1. **Credencial** `rfb_writer` (herda `ratio_leitura`; `INSERT/UPDATE` em `rfb_atos.*`, **sem `DELETE`**;
   `DELETE` só em `rfb_atos_staging`), DSN em `/td/batch/rfb-writer-dsn`. `db.py` resolve variável de
   ambiente → SSM (leitura `/td/db/ratio-pg-dsn`, escrita `/td/batch/rfb-writer-dsn`). *Precisa do admin.*
2. **Embedding local:** `lib.embed_openai` para `scripts/lib/` (com teste).
3. **Categorizador corrigido ANTES da trava:** `categorize_with_llm.py` passa a exigir texto (ou marca a
   matéria como feita pela ementa). Motivo: um `CHECK ... NOT VALID` já vale para gravações novas.
4. **Conferir o schema real** (`pg_catalog`): nulidade de `ato_relacao.ato_destino_id`, chaves de
   `ato_content`, colunas que a 010 pressupõe.
5. **Migration 010** (`ato_coleta`, `v_cobertura_ato`, invariante `NOT VALID`).
6. **Migration 011 (aditiva):**
   - `ato_texto_visao (ato_id, visao, sha256, coletado_em, texto, caminho_json)`, PK `(ato_id, visao, sha256)`
     — guarda fotos das visões `original`/`multivigente` ao longo do tempo; **o JSON bruto fica em disco/S3**
     (`C:\td-rfb-atos-dados`), não no Postgres. `ato_content` não muda.
   - `ato_relacao.destino_id_portal BIGINT` (identidade da aresta externa; `destino_externo` segue como
     rótulo legível, que o consumidor mostra) + índice único parcial
     `(ato_origem_id, destino_id_portal, tipo_relacao) WHERE ato_destino_id IS NULL`.
   - `ato_materia.base_analise TEXT NOT NULL DEFAULT 'texto' CHECK (base_analise IN ('texto','ementa'))`
     — a base é da matéria, não do ato.
   - `ato.situacao_portal JSONB` (vigente, cor, datas brutas do portal).
   - `rfb_atos.ato_mudanca (run_id, ato_id, campo, antes, depois, em)` — log de toda alteração em linha existente.
   - schema **`rfb_atos_staging`** espelhando `ato`, `ato_content`, `ato_texto_visao`, `ato_relacao`.
7. **Os 912:** voltam para a fila (`analise_completa = false`; nenhuma matéria se perde), com o estado
   anterior no `ato_mudanca` e a falha em `ato_coleta`; depois `VALIDATE CONSTRAINT`.
8. **IN RFB 2.121/2022 do JSON íntegro** (é vigente → vai direto para as tabelas principais): vigente →
   `ato_content`, original → `ato_texto_visao`, 4.238 segmentos → `ato_segmento`; categorizar e vetorizar.

### F2 — Medir e consertar o extrator (sem carga em massa)

- **F2a — Censo do portal:** listagem SIJUT **sem** o filtro de vigentes, todos os tipos × anos, só a
  listagem (sem baixar teor) → inventário em `rfb_atos_staging`. Sai o delta **exato** por tipo × ano × situação.
- **F2b — Ensaio de carga no portal:** 500 atos em taxas crescentes, registrando 429/403/5xx, latência e
  bytes; define ritmo e o disjuntor (pausa automática em 429/403, backoff).
- **F2c — Extrator:** `https`; sem filtro de vigentes; timeout adaptativo com download **fora** de
  transação; visões vigente → `ato_content`, original/multivigente → `ato_texto_visao`, relacional →
  `ato_relacao` (inclusive destino fora da base, por `destino_id_portal`); `vigente = false` →
  `nao_vigente` + `situacao_portal`; `data_vigencia_fim` pela data de efeito de uma `interrompe`
  **total** (revogação parcial não encerra o ato).
- **F2d — Propagação e reconciliação:** (i) ao processar um ato, os efeitos que ele causa atualizam os
  atos afetados; (ii) o resolvedor liga `destino_id_portal → ato_destino_id` **fundindo** com aresta
  interna existente e **reaplicando** os efeitos; (iii) passada de reconciliação ao fim de cada lote;
  (iv) **listagem dupla diária** (com e sem filtro) acha vigentes que deixaram de ser vigentes.
- Testes com respostas reais do portal já no repo + Postgres real no CI.

### F3 — Carga em camadas (gate entre camadas)

1. **Novos desde 03/07** (vigentes e não vigentes): vigentes → tabelas principais; não vigentes → staging.
2. **Cadeia de PIS/COFINS** (IN SRF 247/2002, 457/2004, 660/2006, RFB 1.717/2017, 1.911/2019 — conferida no censo),
   **as 33 INs ausentes mais citadas** e o **backlog** por tipo × ano → **staging**.
3. **Os 721 `nao_disponivel_portal`** com o timeout novo.
4. **Promoção em duas etapas**, só depois do release do consumidor (§5) adotado pelo time:
   - **etapa A:** `ato`, texto e relações sobem para as tabelas principais — aparecem na busca por
     identificador e no grafo (`snowball`), com o status à vista, mas **não** na busca por matéria;
   - **etapa B:** categorização e vetores dos não vigentes — só então entram nas buscas semântica,
     textual e por tributo.

### F4 — Pós-coleta

`extract_segmentos` (original + histórico), categorização (Haiku 4.5 na massa; Sonnet na cadeia de
PIS/COFINS se o dono aprovar — `llm_model` já audita), vetores OpenAI, conflitos/timeline, `auditar_cobertura.py`.

### F5 — Atualização contínua

O agendamento de todas as bases já nasce com censo incremental, listagem dupla, propagação, disjuntor e `ato_coleta`.

## 5. Impacto no time

- **Release do td-analise-piscofins antes da promoção:** parâmetro `vigente_em` (padrão: o período do
  case) nas buscas; citação de ato não vigente sai marcada "NÃO VIGENTE desde dd/mm/aaaa (interrompido
  por X)"; `tests/test_atos_rfb.py` ampliado (incluindo o teste de famílias ausentes).
- **Roteiro do analista:** `/td-analise-piscofins:atualizar` (+ `git -C C:\td-analise-core pull` se o
  core mudar). Nenhuma credencial nova.
- **Até a promoção, nada muda para ninguém.** Depois da etapa A, quem não atualizou vê atos não vigentes
  só por identificador e grafo, com o status; a etapa B só roda com o time atualizado (prazo comunicado).

## 6. Custo e tempo

Definidos pela F2a (censo) e pela F2b (ensaio). Ordem de grandeza da v1, a confirmar: categorização
~US$ 0,004/ato em Haiku; vetores ~US$ 0,13 por milhão de tokens; portal no ritmo que o ensaio permitir.

## 7. Validação e teste de falsificação

- **Canários:** IN 2.121 com texto e segmentos; IN SRF 457/2004 e IN RFB 1.911/2019 com `nao_vigente`,
  `data_vigencia_fim` e aresta `interrompe` a partir do ato revogador.
- **A pergunta do td-legislacao** ("qual IN regia o crédito de imobilizado em 2016?") respondida pelos
  **segmentos com vigência** (texto vigente naquela data), não pela visão vigente atual.
- **Consumidor:** saídas de consultas fixas do `atos_rfb.py` idênticas antes e depois de cada camada até
  a promoção; `tests/test_atos_rfb.py` verde; um case real sem regressão.
- **Censo × base** por tipo × ano; arestas `interrompe` do portal > 0; invariante 010 sem violação;
  relação externa idempotente (repetir o lote não duplica); disjuntor testado.

## 8. Rollback

Staging é descartável; promoção é por `run_id`; toda alteração em linha existente está no `ato_mudanca`
(antes/depois) e é revertida por ele.

## 9. Decisões do dono

1. Aprovar a v2 (staging + promoção em duas etapas).
2. Sonnet na cadeia de PIS/COFINS (sim/não).
3. Criar o `rfb_writer` (admin) — pré-requisito da F1.
