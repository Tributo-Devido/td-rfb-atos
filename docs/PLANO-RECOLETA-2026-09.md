# Plano — recoleta do `rfb_atos`: revogados, versões, carga pendente e atualização

> v1 · 2026-09-13 · **proposta para revisão 4-LLM (GR-3)**. Nada foi gravado no banco.
> Diagnóstico medido nesta data (fase 0): código do extrator, portal SIJUT e o banco
> `ratio` como `ratio_leitura`. Consumidor principal: `td-analise-piscofins/libs/atos_rfb.py`,
> usado pelo time inteiro — **nenhuma fase pode quebrá-lo**.

## 1. Resumo

A base `ratio.rfb_atos` tem 101.026 atos, **todos vigentes**. Está **parada desde 03/07/2026**,
tem **912 atos marcados "analisados" sem uma linha de texto**, **nenhuma relação "revoga"** e só
guarda a **visão vigente** do texto. As causas estão no extrator e em uma baixa manual que nunca
foi versionada. A proposta é consertar em 5 fases aditivas, carregar primeiro o que já está no
disco (a IN RFB 2.121/2022 inteira) e só então recoletar ~10 mil atos revogados, em lotes, com
o consumidor do time preparado antes.

## 2. Diagnóstico (fase 0 — feita)

| # | Achado | Evidência |
|---|---|---|
| D1 | **A coleta lista só vigentes** | `extract_sijut.py:81` usa `somente_atos_vigentes=on`. Portal, IN com filtro × sem: 2004 **23 × 117**; 2006 25 × 116; 2013 41 × 132; 2019 31 × 60; 2022 63 × 75. A base tem 24 INs em 2004 — é exatamente o resultado filtrado |
| D2 | O buraco se concentra em IN e Portaria | Portaria 2010 102 × 146, 2018 529 × 726; ADE 2010 184 × 210, 2018 5.296 × 5.591; SC 2010 142 × 159, 2018 1.068 × 1.071; ADI sem diferença |
| D3 | **Relações para ato fora da base são descartadas** | `fetch_normasinternet2.py:255-258` (`continue` se o destino não existe). Resultado: o grafo tem 0 arestas `revoga`; do portal só `altera` (2.405), `retifica` (2.265) e cauda |
| D4 | **Só a visão vigente** | `ato_content`: 100.113 linhas, todas `vigente`. Na nuvem `ato_content.ato_id` é **UNIQUE** (td-creditos 004:294) — não cabe outra versão na mesma tabela. `extract_segmentos.py` tenta original → vigente; a multivigente nunca é coletada |
| D5 | **912 "analisados" sem texto, nenhuma matéria** | 912 atos `analise_completa = true` e `content_disponivel = false`; **0** têm `ato_materia`; 721 estão `nao_disponivel_portal`; 0 vieram de migração (`legacy_table`). **Nenhum script versionado grava `nao_disponivel_portal`** → baixa avulsa (SQL manual ou script perdido). O `categorize_with_llm.py` (não exige texto; categoriza "pela ementa" e marca `analise_completa = TRUE`) é um segundo caminho possível para o mesmo sintoma |
| D6 | Timeout curto | `get_json` com 30 s; ato grande (IN 2.121: JSON de 3,6 MB) falha e fica sem texto |
| D7 | **Base parada** | último ato 03/07/2026; ritmo ~5,5 mil atos/ano → ~1,5 mil atos novos faltando |
| D8 | **Carga que ficou para trás** | `C:\td-rfb-atos-dados\scripts\in2121_original.json` (`idAto` 127905 = `id_portal` do ato 16630), 4.238 segmentos com flags `original/compilado/tachado/agendado` |
| D9 | Vigência quase vazia | `data_vigencia_fim` em 1.802 de 101.026; nenhum status de revogado |
| D10 | Sem controle de coleta e sem credencial | migration 010 não aplicada; `db.py` lia o `ratio-pg-dsn.txt` (apagado); não há usuário de escrita para `rfb_atos` |
| D11 | Consumidor | `atos_rfb.py`: **não** usa `analise_completa` como trava (L1024-1026); **já trata** `destino_externo` (L893); faz `LEFT JOIN ato_content` sem filtrar versão (L546, L1034); as buscas **não filtram vigência**, mas projetam status e datas (`_PROJECAO`) |

Volume a recoletar (estimativa por amostra, a medir por tipo × ano na F2): **~10 mil atos** revogados
+ ~1,5 mil novos + os 721 `nao_disponivel_portal`.

## 3. Princípios

- **P1 — Não quebrar o consumidor do time.** Só mudanças aditivas: `ato_content` continua com uma
  linha por ato (vigente); versões vão para tabela nova; relação externa via `destino_externo`
  (que o consumidor já lê); status novo é só um valor novo em `status_vigencia`.
- **P2 — Um modelo de embedding** (OpenAI `text-embedding-3-large`) — a lição do CARF.
- **P3 — Uma credencial por vertical** (runbook de chaves): `rfb_writer`.
- **P4 — Idempotente, retomável e marcado** (`fonte`/`run_id` = `recoleta-2026-09`) para rollback.
- **P5 — GR-6:** cada escolha entre caminhos viáveis vai para `docs/DECISOES.md`.

## 4. Fases

### F1 — Fundação (sem tocar no portal)

1. **Credencial:** `CREATE ROLE rfb_writer` herdando `ratio_leitura`, com `INSERT/UPDATE/DELETE` em
   `rfb_atos.*` e uso das sequences; DSN em SSM `/td/batch/rfb-writer-dsn`. `db.py` passa a
   resolver **variável de ambiente → SSM** (leitura `/td/db/ratio-pg-dsn`, escrita
   `/td/batch/rfb-writer-dsn`), sem arquivo. *Precisa do admin (dono).*
2. **Embedding local:** trazer `lib.embed_openai` para `scripts/lib/` (com teste); `reembed_cloud.py`
   e `validate_cloud_parity.py` deixam de depender do td-creditos.
3. **Migration 010** (`ato_coleta`, `v_cobertura_ato`, invariante `NOT VALID`) — conferir antes
   contra o schema real da nuvem (foi escrita sem acesso ao banco).
4. **Migration 011 (aditiva):**
   - `rfb_atos.ato_texto_versao (ato_id, visao, texto, json_bruto, fonte, sha256, coletado_em,
     PRIMARY KEY (ato_id, visao))`, `visao ∈ {original, multivigente}` — **sem tocar em `ato_content`**;
   - `ato_relacao.destino_id_portal BIGINT` + índice único parcial
     `(ato_origem_id, destino_externo, tipo_relacao) WHERE ato_destino_id IS NULL` (idempotência
     da relação externa; hoje o UNIQUE não pega linhas com destino nulo);
   - `ato.analise_base TEXT CHECK (analise_base IN ('texto','ementa'))` — torna explícito se a
     categorização leu o teor ou só a ementa;
   - valores novos de `status_vigencia`: `revogado`, `nao_vigente`.
5. **Os 912:** voltar para a fila (`analise_completa = false` — nenhuma matéria se perde), registrar a
   falha em `ato_coleta` com motivo, e `VALIDATE CONSTRAINT` do invariante.
6. **Carga da IN 2.121/2022 do JSON íntegro:** vigente → `ato_content`; original →
   `ato_texto_versao`; 4.238 segmentos → `ato_segmento` com as flags; depois categorizar e vetorizar.
   **Primeiro ganho visível para o time**, sem portal.

### F2 — Extrator corrigido (código + testes, sem carga em massa)

- `extract_sijut.py`: `https`, **sem** `somente_atos_vigentes`.
- `fetch_normasinternet2.py`: timeout adaptativo (até ~180 s, streaming); falha vai para
  `ato_coleta`, não para `status_vigencia`; visões: vigente → `ato_content` (como hoje),
  original e multivigente → `ato_texto_versao`, relacional → `ato_relacao` mesmo com destino fora
  da base (`destino_id_portal` + `destino_externo` legível).
- **Status:** `vigente = false` no JSON → `nao_vigente`; com impacto de revogação no relacional →
  `revogado` + `data_vigencia_fim` pela data de efeito.
- **Propagação:** ao processar um ato novo, os impactos que ele causa (revoga/altera) atualizam o
  ato antigo — resolve "revogado depois da coleta" no incremental diário.
- **Resolvedor:** quando o destino é coletado, `destino_id_portal` vira `ato_destino_id`.
- Testes com as respostas reais já no repo (`api_discovery.json`, `sc150754_*.json`) + Postgres real
  no CI; categorizador passa a exigir texto (ou gravar `analise_base = 'ementa'`).

### F3 — Recoleta em lotes (gate entre lotes)

1. **2026 inteiro** (vigentes + revogados) — fecha o buraco desde 03/07.
2. **Cadeia de PIS/COFINS**: IN SRF 247/2002, 457/2004, 660/2006, RFB 1.717/2017, 1.911/2019 (lista
   conferida no portal antes).
3. **As 33 INs ausentes mais citadas pela própria base** (59% das citações a IN;
   `td-legislacao/docs/consulta-citacoes-in-orfas.sql`).
4. **Backlog de revogados por tipo × ano**: IN → Portaria → ADE → SC → demais (~10 mil), ~1 req/s.
5. **Os 721 `nao_disponivel_portal`** com o timeout novo.

### F4 — Pós-coleta

`extract_segmentos` (original + vigente), categorização (Haiku 4.5 em lote, como hoje), vetores
OpenAI nas matérias, `detectar_conflitos`/`timeline`, `auditar_cobertura.py`.

### F5 — Atualização contínua

O agendamento que o dono vai montar para todas as bases já nasce com a listagem sem filtro, a
propagação de revogação e o controle em `ato_coleta`.

## 5. Impacto no time

- **O que o time passa a ver:** atos revogados nas buscas do `atos_rfb.py` (as vias semântica, textual
  e por tributo não filtram vigência). Status e datas já vêm no resultado e a `vigencia()` já
  descreve "vigente desde X até Y" — mas um analista pode citar ato revogado como vigente.
- **Sequência proposta:** (1) release do td-analise-piscofins com parâmetro `vigente_em` (padrão: o
  período do case) e marca "REVOGADO em dd/mm/aaaa" na citação; (2) time atualiza
  (`/td-analise-piscofins:atualizar`); (3) só então a F3 carrega revogados. Quem não atualizar não
  quebra — vê mais resultados, com o status à vista.
- **Não muda:** `ato_content` (uma linha por ato), relações (o consumidor já trata `destino_externo`),
  `analise_completa` (não é trava de leitura), credenciais do time (`ratio_leitura`).

## 6. Custo e tempo (estimativas, a confirmar)

- **Portal:** ~12 mil atos × ~4 requisições ≈ 48 mil requisições a 1 req/s ≈ **13 h** (+ anexos).
- **Categorização:** histórico de 30.501 atos ≈ US$ 130 → ~US$ 0,004/ato → **~US$ 55**.
- **Vetores:** ~1,35 matéria/ato × 13 mil × ~300 tokens ≈ 5 mi tokens ≈ **US$ 1**.
- **Banco:** `ato_texto_versao` na ordem de centenas de MB (hoje o schema inteiro tem ~1,6 GB).

## 7. Validação e teste de falsificação

- **Canários:** IN 2.121 com texto e segmentos; IN SRF 457/2004 e IN RFB 1.911/2019 presentes, com
  status revogado e `data_vigencia_fim`.
- **A pergunta do td-legislacao:** "qual IN regia o crédito de imobilizado em 2016?" → a base
  responde IN SRF 457/2004 vigente naquele ano. Se não responder, o plano falhou.
- **Contagem:** base × portal sem filtro, por tipo × ano, dentro de tolerância.
- **Grafo:** arestas `revoga` > 0 e coerentes com `data_vigencia_fim`.
- **Invariante 010:** 0 violações após `VALIDATE`.
- **Time:** `tests/test_atos_rfb.py` do td-analise-piscofins verde contra o banco pós-carga + um case
  conhecido sem regressão.

## 8. Rollback

Tudo marcado com `fonte`/`run_id`; tabelas e colunas novas são aditivas; os estados anteriores dos
912 ficam registrados em `ato_coleta`. Reverter um lote = apagar pelas marcas.

## 9. Perguntas para a revisão

1. `analise_base` + invariante estrito × redefinir `analise_completa`?
2. Versões em tabela nova × mudar a chave de `ato_content` e o consumidor?
3. Carregar revogados antes ou depois do release do consumidor?
4. A propagação pelo relacional do ato novo basta para "revogado depois da coleta"?
5. Categorizar com Haiku 4.5 (como a base atual) ou Sonnet (como a v6 do CARF)?
6. `revogado` × `nao_vigente`: o portal distingue com segurança?
