# Revisão de cobertura do `rfb_atos` — runbook

> Como medir quanto do acervo chegou ao fim do funil, e como fechar o que faltar.

## O problema que isto resolve

A regra é simples de enunciar: **todo ato que tem PDF no portal deve estar baixado,
categorizado e com embedding.** Ato que o portal não publica é fato da vida — não é
pendência.

O que impedia cumprir a regra não era falta de esforço: era não haver como
distinguir os dois casos. A base guarda três booleanos em `rfb_atos.ato` —
`pdf_disponivel`, `content_disponivel`, `analise_completa` — e `pdf_disponivel = false`
significa, ao mesmo tempo:

| o que aconteceu de verdade | o que a base registra | é pendência? |
|---|---|---|
| o portal não publica PDF para este ato | `pdf_disponivel = false` | **não** |
| nunca tentamos baixar | `pdf_disponivel = false` | **sim** |
| tentamos e falhou (404, timeout, 503) | `pdf_disponivel = false` | **sim** |

Com os três colapsados num único `false`, não existe fila de trabalho: não dá para
saber o que ainda tem conserto. Foi assim que a **IN RFB 2.121/2022** (`ato_id`
16630) — a IN que consolidou toda a regulamentação de PIS/COFINS — ficou parada com
`status_vigencia = 'nao_disponivel_portal'`, sem uma linha em `ato_content` e,
mesmo assim, com `analise_completa = true`. A base sabia que a coleta tinha falhado
e o registro disso não levava a lugar nenhum.

O custo apareceu na ponta: os arts. 171, 179 e 185 dela — que decidem dinheiro em
crédito de imobilizado — tiveram de ser lidos por **transcrição dentro de Solução de
Consulta** (SC COSIT 110/2025 e 267/2023). Proveniência indireta, e que não escala.

## O funil

```
ato  ──▶  [1] PDF  ──▶  [2] texto  ──▶  [3] matéria  ──▶  [4] embedding
          portal        ato_content     ato_materia       ato_materia.embedding
                        (MarkItDown)    (Haiku)           (OpenAI 3072)
```

Cada etapa é pré-requisito da seguinte, e cada uma tem dono diferente. A queda entre
duas linhas do relatório é o trabalho pendente **daquela** etapa — e só dela. Confundir
as etapas é o que faz uma falha de OCR parecer falha de coleta.

| etapa | quem executa | situação |
|---|---|---|
| 1. ato → PDF | `scripts/backfill_pdf.py` | **novo** |
| 2. PDF → `ato_content` | etapa EXTRACT do pipeline (MarkItDown) | já existe |
| 3. `ato_content` → `ato_materia` | etapa CATEGORIZE (Haiku 4.5) | já existe |
| 4. `ato_materia` → embedding | `scripts/reembed_cloud.py` | já existe, idempotente |

Vale registrar: a etapa 4 **já é idempotente** (`WHERE embedding IS NULL`). Se houver
matéria sem vetor, basta rodar. O gargalo real está nas etapas 1 a 3.

## Os defeitos que a revisão nomeia

| # | defeito | o que fazer |
|---|---|---|
| D1 | `analise_completa = true` sem uma linha de texto | rebaixar o flag e reenfileirar a coleta |
| D2 | `content_disponivel = true` sem texto (mente **a favor**) | rebaixar o flag e reenfileirar |
| D3 | `content_disponivel = false` **com** texto (mente **contra**) | promover o flag; não há coleta a refazer |
| D4 | PDF indicado, texto ausente | reextrair do arquivo local — sem rede, custo baixo |
| D5 | texto presente, zero matéria | enfileirar na categorização (Haiku) |
| D6 | matéria sem embedding | rodar `reembed_cloud.py` |
| D7 | sem texto e **sem nenhuma tentativa registrada** | probar no portal para separar fato de defeito |
| D8 | `status_vigencia = 'nao_disponivel_portal'` nunca reprocessado | reenfileirar com prioridade |

**D7 é o que carrega a revisão.** Enquanto ele não for a zero, "este ato não tem PDF"
é suposição, não medição. D2 é mais perigoso que a ausência pura: o ato entra em toda
consulta como se tivesse teor, volta vazio e não aparece em fila nenhuma.

## Como rodar

Tudo exige o tunnel SSM aberto (`localhost:15432`) ou um DSN em `RFB_ATOS_DSN`.

### 1. Medir (não escreve nada)

```bash
python scripts/auditar_cobertura.py --out ./relatorio
```

Somente leitura — e não por convenção: a conexão abre com
`default_transaction_read_only = on`, então o próprio Postgres recusa escrita vinda
dali. Gera `REVISAO-COBERTURA.md` mais um CSV por recorte.

Recortes úteis:

```bash
python scripts/auditar_cobertura.py --tipo INSTRUCAO_NORMATIVA   # só as INs
python scripts/auditar_cobertura.py --foco 16630                 # a IN 2.121
python scripts/auditar_cobertura.py --amostra 50                 # mais exemplos
```

### 2. Criar o estado de coleta (uma vez)

```bash
psql "$DSN" -v ON_ERROR_STOP=1 -f migrations/010_ato_coleta.sql
```

Cria `rfb_atos.ato_coleta` (uma linha por ato), a view `v_cobertura_ato` e o
invariante `ato_analise_exige_conteudo`. É idempotente e **exige papel com DDL** —
`legislacao_writer` e afins não têm.

Duas decisões de desenho que merecem atenção antes de aplicar:

- **O backfill da 010 não inventa ausência de PDF.** Ato com texto vira `baixado`;
  todo o resto vira `nao_tentado`. Marcar `pdf_disponivel = false` como
  `sem_pdf_no_portal` transformaria a dúvida em fato — exatamente o defeito que a
  migration existe para corrigir. Só o `backfill_pdf.py`, contra o portal, promove
  `nao_tentado → sem_pdf_no_portal`.
- **O invariante entra `NOT VALID`.** Passa a valer para toda escrita nova
  imediatamente, sem travar nas ~912 linhas legadas que já o violam. Depois de
  limpá-las: `ALTER TABLE rfb_atos.ato VALIDATE CONSTRAINT ato_analise_exige_conteudo;`
  Uma limpeza em massa dessas linhas não está em nenhum script — é decisão do dono da
  base, não de uma migration.

### 3. Conferir o contrato do portal (não escreve nada)

```bash
python scripts/backfill_pdf.py                  # o plano: fila e URLs, sem rede
python scripts/backfill_pdf.py --probe 5        # busca 5 e mostra o resultado cru
```

> ⚠️ **O contrato HTTP com o SIJUT não foi verificado contra o portal.** O ambiente
> onde estes scripts foram escritos não tem saída para
> `normas.receita.fazenda.gov.br`. O formato da URL, como o portal sinaliza "sem PDF"
> e o content-type devolvido são **suposições** até alguém rodar `--probe`. Confira se
> a linha `-> status` bate com a realidade de cada caso antes de qualquer `--aplicar`
> em lote. Se não bater, o ajuste está em `classificar_resposta()` e `resolver_url()`,
> que são funções puras e têm teste.

### 4. Coletar

```bash
python scripts/backfill_pdf.py --aplicar --ato 16630                    # a IN 2.121
python scripts/backfill_pdf.py --aplicar --tipo INSTRUCAO_NORMATIVA     # as INs
python scripts/backfill_pdf.py --aplicar --limite 500                   # lote geral
```

A fila é resumível: se o portal aplicar rate limit, o script para e nada se perde.
Erro transitório volta com backoff exponencial e jitter; `sem_pdf_no_portal` sai da
fila **para sempre**, porque insistir num 404 não muda a resposta — muda só o consumo
do portal.

### 5. Seguir o funil

Depois do download, as etapas 2 a 4 são o pipeline que já existe: extração →
categorização → `reembed_cloud.py`. Rodar a auditoria de novo fecha o ciclo e mostra
o que a rodada moveu.

## Ordem de prioridade

A fila do `backfill_pdf.py` põe **Instrução Normativa na frente**, depois publicação
mais recente. Não é preferência estética: IN consolida matéria inteira, e a medição
feita pelo td-legislacao mostrou que **59,4% das citações a IN dentro do próprio
acervo apontam para IN que a base não tem** (30.625 de 51.525 citações, nas 60 INs
mais citadas). A base cita massivamente atos que ela não guarda.

Duas ausências que essa medição destaca, ambas centrais para PIS/COFINS e nenhuma
resolvida por este trabalho — elas são de **coleta de ato revogado**, um problema
diferente:

- **IN SRF 457/2004** — rege o crédito de imobilizado no período 2015–2019; o art. 7º
  decide se um cliente pode ou não refazer a base do crédito (opção irretratável).
- **IN RFB 1.911/2019** — regeu PIS/COFINS entre 2019 e 2022; 2.063 citações no acervo.

O acervo é o **vigente hoje**, e análise de crédito trabalha sobre **período
fiscalizado**. Um cliente com período de 2015 a 2019 é regido pela 457/2004. Fechar
essa lacuna é outro escopo, e está descrito em
`td-legislacao/docs/PROMPT-para-sessao-rfb-atos.md` (Problema 2).

## O que este trabalho não faz

- **Não corrige as 912 linhas legadas em massa.** O invariante entra `NOT VALID`; a
  limpeza é decisão do dono. O `backfill_pdf.py` realinha os flags **de um ato por
  vez**, no momento em que tem evidência fresca sobre ele — o que é muito mais fácil
  de defender que um `UPDATE` de 912 linhas.
- **Não coleta ato revogado.** Escopo diferente (Problema 2 acima).
- **Não extrai texto nem categoriza.** Essas etapas já existem no pipeline; misturá-las
  com o download faria falha de OCR parecer falha de coleta.
- **Não foi exercitado contra o portal.** Ver o aviso da etapa 3.

## Testes

```bash
initdb -D /tmp/pgdata -U postgres --auth=trust
pg_ctl -D /tmp/pgdata -o '-p 15999 -k /tmp' start
createdb -h /tmp -p 15999 -U postgres rfbtest
PGTEST_DSN='postgresql://postgres@/rfbtest?host=/tmp&port=15999' pytest tests/ -q
```

46 testes, contra Postgres real — sem mock de banco. O que estes scripts têm de
difícil é SQL (`FILTER`, `EXISTS` correlacionado, `NOT VALID`, `UPDATE ... FROM` com
CTE); um mock validaria a montagem da string e deixaria passar exatamente a classe de
erro que importa. `tests/fixtures/` reproduz a forma do schema da nuvem e um ato por
cenário que a revisão precisa distinguir — incluindo a IN 2.121 e, como controle
negativo, a **IN 2.152/2023**, que tem texto e `analise_completa = false`: estado
coerente ("tem teor, ainda não analisado") que não pode aparecer como defeito.
