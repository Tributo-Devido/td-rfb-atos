# Decisões

Registro append-only: decisão, alternativas descartadas, porquê e o que a derrubaria.
Para corrigir uma decisão, adicionar entrada nova marcando a anterior como *superseded*.

---

## 2026-09-12 · Estudos por tema ficam em `outputs/` do repo, fora do git

**Decisão.** Os estudos (`outputs/wip/combustiveis-alcool-creditos/`,
`outputs/wip/transporte-cargas-creditos/`, v1 e v2, ~2,3 MB de markdown) e os SQLs de
categorização de lotes COSIT foram de `C:\td-skills\td-rfb-atos\outputs\` para
`C:\td-rfb-atos\outputs\`, que continua no `.gitignore`.

**Alternativas descartadas.**
- *Pasta de dados (`C:\td-rfb-atos-dados`)*, como o roteiro de separação sugeria:
  quebraria quem lê os estudos — `td-creditos/references/contract-4-camadas.md`,
  `contract-7-buscas.md`, `td-mapeamento-fiscal/references/*` e o comando
  `/td:mapeamento:aplicar` apontam para `td-rfb-atos/outputs/wip/<tema>/`.
- *Versionar no git agora*: é o destino provável (é produto analítico, não dado bruto),
  mas muda o `.gitignore` de propósito e mistura duas decisões num PR de mudança de
  lugar. Fica para o dono decidir.

**Porquê.** Mantém a convenção `td-rfb-atos/outputs/...` de quem consome, e os scripts
que gravam em `Path(__file__).parent.parent / "outputs"` (compilar/sintese/deep research)
passam a gravar no mesmo lugar.

**O que derrubaria.** Se os estudos passarem a ser citados em entregável de cliente, eles
precisam de versão — aí migram para o git (ou para um repo de estudos) e esta entrada é
superseded.

---

## 2026-09-12 · Lint dos scripts herdados: dívida declarada por arquivo

**Decisão.** Os 26 scripts do pipeline de coleta entraram com 304 achados de ruff.
Cada um ganhou, no `pyproject.toml`, uma entrada de `per-file-ignores` com os códigos que
tinha naquele dia — e só esses.

**Alternativas descartadas.**
- *Corrigir os 304*: sem teste nenhum nesses scripts, é mexer em ETL que grava em banco
  sem nada que pegue um caractere trocado. Nenhum achado era de bug (zero F821, zero B).
- *Excluir os scripts do lint (`extend-exclude`)*: esconderia também achados novos de
  correção que alguém introduza depois.
- *Um ignore global com a união dos códigos*: dispensaria em todos os arquivos códigos
  que só alguns tinham.

**Porquê.** Mantém o CI verde sem esconder regressão: código novo nesses arquivos com
outra classe de achado continua reprovando.

**O que derrubaria.** Achado de ruff que seja bug real escondido por um desses códigos
(ex.: `F401` mascarando import com efeito colateral). Ao dar teste a um script, limpar e
tirar a entrada.

---

## 2026-09-12 · Pasta de dados espelha os caminhos originais

**Decisão.** `C:\td-rfb-atos-dados\` reproduz os caminhos relativos da pasta antiga
(`batches\`, `logs\`, `scripts\pdfs\`, `scripts\logs\`, `scripts\*.jsonl`…).

**Alternativa descartada.** Reorganizar por tipo (`pdfs\`, `resultados\`). Mais bonito,
mas perde a correspondência 1:1 com o que os scripts e os handoffs antigos citam.

**Porquê.** A conferência "nada se perdeu" antes de apagar a cópia antiga é por caminho
relativo; com espelho, ela é mecânica.

**O que derrubaria.** Mudança dos dados para S3 — aí o layout é o do bucket.

---

## 2026-09-13 · Recoleta: revogados entram por área de espera e só sobem depois do consumidor

**Decisão.** Aprovada pelo dono a v2 do plano (`docs/PLANO-RECOLETA-2026-09-v2.md`): atos não
vigentes recoletados vão para o schema `rfb_atos_staging` e só são promovidos às tabelas que o time
lê depois que o td-analise-piscofins ganhar o parâmetro `vigente_em` e o time atualizar; a promoção é
em duas etapas (ato/texto/relações primeiro; matérias e vetores depois).

**Alternativas descartadas.**
- *Carregar direto nas tabelas principais* (v1): reprovado pelos três revisores — as buscas do
  `atos_rfb.py` não filtram vigência; ato revogado ocupa o top-k e é citado como vigente por quem não atualizou.
- *View de compatibilidade renomeando as tabelas* (Gemini): exige renomear tabelas que o consumidor
  lê pelo nome; mais invasivo que a área de espera.

**Porquê.** O banco é compartilhado pelo time inteiro; "aditivo no schema" não é "aditivo no comportamento".

**O que derrubaria.** Se o release com `vigente_em` não for adotado, os não vigentes ficam presos na
área de espera — aceitável: é o estado de hoje, com o dado já coletado.

---

## 2026-09-13 · Vocabulário do portal para vigência; revogação é `interrompe`

**Decisão.** `status_vigencia` usa só valores do portal (`nao_vigente`, `anulada`, `revigorado`…); a
revogação é a aresta `interrompe` (corSimbolo 3); "revogado" é derivado na leitura. A situação bruta
do portal fica em `ato.situacao_portal`.

**Alternativa descartada.** Status novo `revogado` e aresta `revoga` (v1): o portal não os tem, e o
consumidor declara `revoga` como família ausente (`atos_rfb.py:847`) — gravar `revoga` mudaria o
comportamento e quebraria teste do time.

**O que derrubaria.** Documentação oficial do SIJUT distinguindo revogação de outras perdas de efeito
num campo próprio — aí o campo passa a ser gravado.

---

## 2026-09-13 · Categorização: Sonnet na cadeia de PIS/COFINS, Haiku na massa

**Decisão.** O dono escolheu Sonnet. Aplicado às INs centrais de PIS/COFINS (247, 457, 660, 1.717,
1.911, 2.121); a massa segue no Haiku 4.5, como a base atual. `llm_model` registra o modelo por matéria.

**Alternativa em aberto.** Sonnet em tudo (proposta do Gemini) — custo de centenas de dólares em vez
de dezenas; o dono pode estender.

---

## 2026-09-13 · Credencial de escrita própria: `rfb_writer`, sem DELETE nas tabelas principais

**Decisão.** Aprovado pelo dono: usuário `rfb_writer` herdando `ratio_leitura`, com `INSERT/UPDATE`
em `rfb_atos.*` e `DELETE` só na área de espera; DSN no SSM `/td/batch/rfb-writer-dsn`. O `db.py`
passa a resolver variável de ambiente → SSM, sem arquivo local.

**Alternativa descartada.** Escrever como `ratio_admin` (`/td/admin/...`): contraria o padrão "uma
credencial por vertical" do runbook e dá privilégio de DDL a scripts de coleta.
