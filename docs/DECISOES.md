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
