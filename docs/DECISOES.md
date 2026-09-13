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

---

## 2026-09-13 · Texto de uma visão do portal = segmentos com `omitir` falso

**Decisão.** `scripts/visoes_portal.py`: o texto de uma visão (vigente ou original) é a
concatenação, na ordem do ato, dos segmentos com `omitir = false`. Todas as versões continuam
guardadas em `ato_segmento`, com as marcas.

**Evidência.** IN RFB 2.121/2022 (idAto 127905), visões baixadas do portal em 13/09: com a regra,
0 dispositivos com mais de uma versão nas duas visões e o art. 171 só na redação atual.

**Alternativas descartadas.**
- *Concatenar todos os segmentos* (extrator antigo): repete redações antigas (art. 171 três vezes,
  +16% de texto) — é o D12 do plano.
- *Filtrar por `compilado`*: perde 237 segmentos que o portal exibe na vigente e o art. 171 inteiro
  na original.
- *Reconstruir pela visão "exclusiva"* (o `in2121_original.json` do disco): 1.169 segmentos a menos
  que a visão vigente real — o arquivo não serve de fonte.

**O que derrubaria.** Um ato em que a visão exibida pelo site difira do texto montado pela regra —
conferir por amostra na F2 (atos alterados de tipos diferentes).

---

## 2026-09-13 · Categorização na nuvem: ato longo em partes pelos cabeçalhos, com o sumário do ato

**Decisão.** `scripts/categorizar_nuvem.py` tira o HTML do portal do texto enviado ao modelo, divide
o ato em partes de até 60 mil caracteres cortando nos cabeçalhos (livro, título, capítulo, seção,
subseção, anexo) e manda em cada chamada o sumário do ato inteiro (cabeçalhos com nome e primeiro
artigo), em cache. IN RFB 2.121/2022: 844 mil caracteres sem HTML (1,24 milhão com), 15 partes,
sumário de 741 linhas; estimativa de ~US$ 1,65 com Sonnet.

**Alternativas descartadas.**
- *Truncar em 100 mil caracteres* (o `categorize_batch.py`): deixaria 88% da IN 2.121 sem matéria.
- *Uma chamada com o ato inteiro:* a resposta (16 mil tokens) comporta umas 20 matérias para 811
  artigos — granularidade de livro, e o time cita artigo.
- *Consolidar as matérias no fim (map-reduce, proposta do Gemini na revisão 4-LLM):* fundiria regras
  diferentes do mesmo tema (crédito presumido da agroindústria × o da ZFM) numa matéria vaga; a base
  já trabalha com várias matérias por ato.

**Por que o sumário.** Crítica do Gemini: sem ele, cada parte não sabe onde está no ato e uma
remissão ("de que trata o art. 171") fica no vácuo. O modelo é orientado a citar o artigo de outra
parte sem presumir o conteúdo.

**O que derrubaria.** Na conferência depois da primeira execução, perguntas de regra geral +
exceção (ex.: insumo em geral × insumo na agroindústria) devolvendo matérias que se contradizem sem
citar o artigo da outra parte.

---

## 2026-09-13 · A categorização não grava relação, não mexe na vigência e não recategoriza

**Decisão.**
- As relações que o modelo sugere são descartadas: relação vem do portal (visão relacional), com o
  vocabulário do portal. O prompt do extrator pede `revoga`, que a base não usa.
- Vigência é do portal; `eficacia_atual` só é preenchida se estiver vazia. O que o modelo disse do
  ato fica em `ato.metadados->'categorizacao'`.
- Ato que já tem matéria não é recategorizado: o `rfb_writer` não apaga, e recategorizar (por
  exemplo, atos com mais de 100 mil caracteres que o `categorize_batch.py` categorizou só pelo
  começo — contar na F2) é decisão à parte, com o admin.

**Alternativa descartada.** Gravar as relações do modelo normalizadas: a base já tem dezenas de
rótulos de relação vindos do modelo (`regulamentacao`, `vinculada_parcialmente_a`, ...) — é o ruído
que a F2c existe para parar de produzir.

**O que derrubaria.** O consumidor precisar de relação que o portal não publica.

---

## 2026-09-13 · Artigos do próprio ato viram dispositivo; sinal também sem `solucao`

**Decisão.**
- `dispositivos_do_ato` (os artigos da própria IN que cada matéria cobre, que o prompt já pedia e o
  `categorize_batch.py` jogava fora) vira linha de `materia_dispositivo` com `tipo_uso =
  'dispositivo_do_ato'` e a referência do próprio ato ("Instrução Normativa RFB nº 2.121/2022",
  número com ponto de milhar — o `normalizar_norma` do td-analise-piscofins lê "2121" como 212).
  Efeito no time: `por_dispositivo("IN RFB 2.121/2022, art. 171")` passa a devolver também a matéria
  da própria IN, com `nivel_match = 'artigo'` e o `tipo_uso` à vista.
- O sinal é classificado quando a matéria tem `solucao` **ou** `ementa_trecho` (o script antigo
  exigia `solucao`; em norma o extrator às vezes só preenche o trecho, e sem sinal a matéria some da
  busca filtrada por sinal).

**Alternativa descartada.** Manter a paridade com o `categorize_batch.py` (primeira versão deste PR):
a revisão 4-LLM (Grok) apontou que, justamente na IN 2.121, o analista precisa dos artigos *desta* IN,
e `ementa_trecho` não os substitui.

**O que derrubaria.** Consulta do time por dispositivo de lei (ex.: Lei 10.833, art. 3º) sendo
poluída — não acontece: a linha nova tem a referência da própria IN, não a da lei.
