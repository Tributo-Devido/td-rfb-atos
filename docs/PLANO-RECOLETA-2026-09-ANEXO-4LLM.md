# Anexo 4-LLM — revisão do plano de recoleta do `rfb_atos` (v1 → v2)

> 2026-09-13 · GR-3 + GR-5. Revisores: **Codex** (CLI 0.154.0, modelo padrão da conta), **Gemini**
> (wrapper `gemini-review.ps1`), **Grok** (wrapper `grok-review.ps1`). Síntese e adjudicação: Claude.
> Observação: o Codex relatou que "o quórum 4-LLM não foi concluído" porque tentou chamar os outros
> modelos de dentro do próprio sandbox; os três rodaram separadamente aqui — o quórum está completo.

## Veredictos

| Revisor | F1 | F3 | Caminho proposto |
|---|---|---|---|
| Codex | aceitar com correções | rejeitar | inventário/staging primeiro; consumidor e categorizador antes da trava; modelo de situação; rollback real |
| Gemini | aceitar | rejeitar | view de compatibilidade filtrando vigentes; reconciliação pós-lote; Sonnet |
| Grok | aceitar com correções | não aprovar | censo antes; manter `interrompe`/`nao_vigente`; não vetorizar revogados antes do release; listagem dupla |

## Pontos e o que a v2 fez

| # | Ponto | Quem | Veredicto | Na v2 |
|---|---|---|---|---|
| 1 | Revogados no banco compartilhado mudam o top-k de quem não atualizou ("não quebra" é falso) | os três | acatado | schema `rfb_atos_staging` + promoção em 2 etapas após o release |
| 2 | D3 errado: a revogação do portal é `interrompe` (corSimbolo 3), e o consumidor declara `revoga` ausente | Grok | **acatado e verificado** (`taxonomia.json:108`, `fetch_normasinternet2.py:57`, `atos_rfb.py:840-847`) | D3 corrigido; nada de gravar `revoga`; canário por `interrompe` |
| 3 | Não existe status `revogado` no portal; `nao_vigente` é amplo (revogação, anulação, perda de efeito) | os três | acatado | vocabulário do portal + `situacao_portal` bruta; "revogado" derivado na leitura |
| 4 | `CHECK ... NOT VALID` já vale para gravações novas — corrigir o categorizador só na F2 é tarde | Codex, Grok | acatado | F1.3 antes da migration 010 |
| 5 | Identidade da aresta externa deve ser o id do portal, não o texto; resolvedor precisa fundir e reaplicar efeitos | os três | acatado | `destino_id_portal` + índice parcial; F2d |
| 6 | `ato_destino_id` é `NOT NULL` | Codex | **rejeitado para a nuvem** (td-creditos 004:327 já é nulável; o Codex leu a 001 local) — mas conferir no `pg_catalog` | F1.4 |
| 7 | `analise_base` pertence à matéria | Codex | acatado | `ato_materia.base_analise` |
| 8 | Visão multivigente muda no tempo; JSON bruto não deve ir ao Postgres | Codex, Grok | acatado | `ato_texto_visao` com `sha256` na chave; JSON em disco/S3 |
| 9 | Rollback descrito não existia (só o último estado) | Codex | acatado | `ato_mudanca` + writer sem `DELETE` |
| 10 | Volume, custo e tempo eram extrapolação | os três | acatado | F2a (censo) e F2b (ensaio com disjuntor) |
| 11 | Propagação só no ato novo não pega revogação de atos já na base | Grok, Gemini | acatado | reconciliação por lote + listagem dupla diária |
| 12 | Canário de 2016 precisa do texto vigente naquela data, não da visão atual | Grok | acatado | validação por segmentos com vigência |
| 13 | View de compatibilidade renomeando tabelas | Gemini | **rejeitado**: exige renomear tabelas que o consumidor lê pelo nome — mais invasivo que a área de espera | — |
| 14 | Timeout longo segurando transação | Gemini | acatado | download fora de transação |
| 15 | Sonnet × Haiku | Gemini (Sonnet), Grok (Haiku na massa, Sonnet na cadeia) | decisão do dono | §9 |
| 16 | ~1,1 mil atos novos, não 1,5 mil | Grok | acatado | D7 corrigido |

Parecer integral do Codex: scratchpad da sessão td-rfb-atos, `rev-codex-recoleta.md`.
