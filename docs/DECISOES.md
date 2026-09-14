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

---

## 2026-09-13 · Conferir antes de gravar (`--gerar`), resposta validada, hash do teor, saída 1

**Decisão.** Revisão 4-LLM da versão 2 (Codex): como o `rfb_writer` não apaga, a conferência vem
**antes** da gravação, não depois.
- `--gerar` chama o modelo e escreve um relatório (matérias por parte, temas repetidos, artigos do
  ato citados pelas matérias, partes sem matéria, divergências entre partes) em
  `RFB_ATOS_DADOS/categorizacao/<ato>/`, **sem gravar**. O `--executar` depois reaproveita as
  respostas guardadas: as matérias não são pagas de novo.
- Resposta do modelo é validada (lista de matérias; cada uma com tema e com solução, trecho ou
  fundamentação); a reprovada não fica em disco e derruba o ato. Parte sem matéria e divergência de
  eficácia ficam registradas; eficácia divergente não é gravada.
- Na gravação, o hash do teor analisado é conferido com a linha do teor travada (`FOR SHARE`).
- As permissões do usuário são conferidas antes de chamar o modelo; os testes rodam como
  `rfb_writer`, com os mesmos GRANTs da nuvem.
- O script sai com código 1 se algum ato falhou ou ficou sem sinal/vetor.

**Consolidação das matérias — reaberta, na forma conservadora.** O Codex propôs remover só
duplicatas equivalentes (tema + tributo + regime, com os artigos como evidência) e manter matérias
diferentes. Não entra antes de medir: o relatório do `--gerar` da IN 2.121 (temas repetidos e
cobertura de artigos) decide. Isto substitui o "conferir depois da primeira execução" da entrada de
partição acima.

**Alternativa descartada.** Conferir depois de gravar (versões 1 e 2): um erro exigiria o admin
para apagar as matérias.

**O que derrubaria.** Relatório com cobertura de artigos baixa, partes sem matéria ou temas
repetidos com conteúdo equivalente — aí a reconciliação é feita antes do `--executar`.

---

## 2026-09-13 · `--executar` grava só o que o `--gerar` mostrou

**Decisão.** Revisão 4-LLM da versão 3 (Codex): o `--executar` não chama o modelo para as matérias
— só lê as respostas guardadas pelo `--gerar`. A chave de cada resposta é o hash de modelo + prompts
+ trecho + max_tokens; se o texto, o prompt ou o modelo mudaram depois do relatório, falta a resposta
e o ato para sem gravar ("rode --gerar"). A resposta cortada no limite de tokens também fica em disco,
para o `--executar` refazer a mesma divisão. Ato sem matéria nenhuma falha já no `--gerar`; `--ato`
inexistente sai com código 1. O sinal (uma palavra por matéria, Haiku) continua sendo pedido no
`--executar`: não entra no relatório.

**Alternativa descartada.** Manifesto separado com os hashes do texto, do modelo e dos prompts
(sugestão do Codex): a própria chave da resposta já é esse hash; um manifesto repetiria a mesma
conferência em outro arquivo.

**O que derrubaria.** Precisar gravar em lote sem conferência (massa de atos no Haiku) — aí um
`--sem-conferencia` explícito, decidido pelo dono.

---

## 2026-09-13 · Os 14 atos do marco entram direto em `rfb_atos` (etapa A), sem a área de espera

**Decisão.** Do dono, no fim do dia 13/09, depois que o td-analise-piscofins publicou o `vigente_em`
(atos_rfb 1.1.0, PR #235): os 14 atos que faltam para o marco dos manuais
(`references/atos_marco_2026-09-13.csv` — 13 não vigentes e a SC COSIT 168/2026) são coletados no
portal por `scripts/coletar_atos_portal.py` e gravados direto nas tabelas principais com ato, texto,
relações e situação do portal — **sem matérias nem vetores** (a etapa B depende do filtro de
vigência no td-analise-core, TI-7560). Substitui, para estes atos, a passagem pela área de espera do
plano v2.

**Regras do coletor.**
- Um número pode ter mais de um ato no portal (IN SRF 247/2002 e IN RFB 1.717/2017 têm, cada uma,
  uma retificação publicada como ato próprio): entram todos, ligados pela aresta `retifica`.
- Relações com fonte `normasinternet2_portal`, a mesma do extrator antigo (é a que o filtro do
  td-analise-piscofins aceita). Aresta cuja origem não está na base não cabe em `ato_relacao`
  (origem obrigatória): fica em `situacao_portal.relacoes_sem_origem`. Aresta já dita pelo modelo
  (llm-batch) que o portal confirma passa à fonte do portal, com antes/depois em `ato_mudanca`.
- `data_vigencia_fim` (fim exclusivo) do não vigente: data de efeito da revogação (REV) publicada
  pelo portal; na falta — o comum: nenhuma das revogações sondadas em 13/09 trazia a data —, o início
  de vigência do ato revogador segundo o portal; na falta dele, a publicação do revogador. Várias
  revogações: vale a mais antiga. Suspensão (SUS) e revogação parcial não fecham o ato. A origem da
  data fica em `situacao_portal.fim_vigencia`.
- Ato com trecho só em anexo PDF (IN 758/2007, 1.911/2019, 1.717/2017): grava o texto do JSON (a
  1.911 tem 800 mil caracteres) e registra em `ato_coleta` quantos segmentos ficaram só no anexo.

**Alternativas descartadas.**
- *Área de espera e promoção depois* (plano v2): o dono preferiu fechar o marco com os atos
  visíveis por identificador e no grafo, já carimbados pelo consumidor 1.1.0.
- *Deixar `data_vigencia_fim` vazio quando o portal não publica a data de efeito*: o filtro do
  consumidor não excluiria o ato revogado em nenhum período; o início de vigência do revogador é a
  melhor data que o portal oferece, com a origem registrada para auditoria.

**O que derrubaria.** Um ato revogado com cláusula de efeito diferida (revogação que só vale depois
da vigência do revogador) — a data gravada seria cedo demais. Conferir por amostra quando a recoleta
em massa rodar; se aparecer, ler a cláusula no texto do revogador.

---

## 2026-09-13 · Fim de vigência só com a data de efeito do portal (substitui a regra da entrada acima)

**Decisão.** *Supersede* a regra de `data_vigencia_fim` da entrada anterior. O coletor grava o fim
**só** quando o portal publica a data de efeito da revogação (`dataVigenciaPrimeiraAnotacao`; nos 14
atos, só a IN 1.717/2017 tem). Sem ela, o fim fica vazio e a estimativa (início de vigência do
revogador, ou a publicação dele) vai só para `situacao_portal.fim_vigencia.estimativa`, para
auditoria e leitura humana.

**Por quê.** Revisão 4-LLM (Gemini e Grok, 13/09): o custo do erro é assimétrico. Fechar cedo demais
(revogação com efeito diferido, "produz efeitos a partir de 1º de janeiro") tira da busca uma norma
que ainda rege os fatos daquele período; deixar vazio só faz o td-analise-piscofins carimbar "NÃO
VIGENTE (data de fim desconhecida)" sem excluir — o erro seguro. O próprio teste de falsificação da
entrada anterior era esse caso.

**Alternativas descartadas.** Início de vigência do revogador como fim (regra anterior); publicação
do revogador (mesmo problema); ler a cláusula de revogação no texto do revogador — é o caminho para
ter a data certa, mas é extração de texto a fazer na recoleta em massa, não nestes 14 atos.

**Ainda nesta revisão.** "Já na base" deixa de exigir a grafia do órgão (o legado varia); o coletor
informa as arestas externas que não conseguiu ligar. Suspensão (SUS) segue como `interrompe` — é a
classificação do portal (corSimbolo 3) e a da base —, com a sigla na observação. O Codex não revisou
esta rodada (login expirado).

**O que derrubaria.** O consumidor passar a precisar do fim para excluir revogados antigos em massa
— aí a extração da cláusula de revogação deixa de ser opcional.

---

## 2026-09-13 · Consolidação das matérias da IN 2.121: não é necessária (medido)

**Decisão.** Fecha a questão reaberta na entrada "Conferir antes de gravar": as 425 matérias da IN
RFB 2.121/2022 ficam como estão, sem passo de consolidação.

**Evidência.** Relatório do `--gerar` (13/09): 838 de 885 artigos citados (95%), 5 artigos citados
por mais de uma matéria, semelhança máxima 0,6 entre as soluções dessas matérias. Depois do
`--executar`, nos vetores: 90.100 pares, só 4 com semelhança ≥ 0,95 e 43 ≥ 0,90 (média 0,585); os
pares mais próximos são regras diferentes (alíquota zero na venda × na importação de mercadoria
equivalente; crédito presumido na exportação de café × na aquisição de café em grão).

**Alternativa descartada.** Deduplicação conservadora (tema + tributo + regime, proposta do Codex):
não há duplicata equivalente para remover.

**O que derrubaria.** Consulta do time devolvendo várias matérias da 2.121 com o mesmo conteúdo
no top-k — aí medir de novo com os pares acima de 0,90.
