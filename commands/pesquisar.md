---
name: td:tax-intelligence:rfb-atos:pesquisar
description: Deep research em atos normativos RFB — planner + reflexão 6 eixos + relatório completo
model: opus
---

# td:tax-intelligence:rfb-atos:pesquisar

Deep research jurisprudencial-normativo. Espelha `/td:carf:pesquisar` mas adapta para a natureza de orientação vinculante (vs decisão colegiada).

## Pipeline (executado nesta sessão pelo Claude)

### 1. PLANNER
Decompõe a pergunta em 3-6 sub-questões com facetas (tributos, temas, tipos_ato, eficacia, datas).

### 2. EXECUTOR (loop por sub-questão)
Para cada sub-questão:
```bash
py scripts/retrieve.py search "..." --tributos ... --temas ... --tipos ... --limit 15 --format md
```

Reflexão sobre 6 EIXOS adaptados para orientação prévia:

1. **Cobertura por órgão** — tem Cosit + Disit das diferentes regiões? Cosit é vinculante geral (peso maior).
2. **Cobertura de vigência** — só vigentes ou inclui revogadas para histórico? SC revogada pode dar contexto da evolução do entendimento.
3. **Divergências entre SCs** — há SD que uniformizou? SCs Disit contraditórias? Cosit muda em SC posterior?
4. **Marco normativo** — a lei/IN que a SC interpretou foi alterada? Risco de caducidade.
5. **Conformidade STF/STJ** — Tema vinculante posterior contradiz a SC?
6. **Schema de metadata** — atos com mesmo tema_específico têm metadata_tematico comparáveis? (NCMs convergentes em CLASSIFICACAO_FISCAL.PRODUTO, mesmo critério em CREDITAMENTO.CONCEITO_INSUMO etc)

Se algum eixo lacunoso: refina query e roda de novo. Máx 2-3 iterações por sub-questão.

### 3. SYNTHESIZER (Claude nesta sessão)

Estrutura do relatório:

1. **Resumo executivo** (3-5 parágrafos densos)
2. **Panorama normativo** (leis/INs/decretos base citados)
3. **Hierarquia de eficácia das orientações** (Cosit vinculante geral × Disit local)
4. **Evolução por marco normativo** (linha do tempo da norma base + SCs vinculadas)
5. **Tese RFB consolidada vs casos divergentes** (quando há SD)
6. **Conformidade com judicial** (STF/STJ que confirmam/contradizem)
7. **Casos-chave anotados** (5-10 SCs/SDs com fichas)
8. **Marcos temporais** (tabela)
9. **Recomendação prática** (adaptada ao caso do usuário se fornecido)
10. **Lacunas e riscos da pesquisa** (vigência incerta, normas base alteradas)

## Regras de síntese

- Cite SEMPRE: tipo + número + órgão + data + link ao referenciar ato
- Use citação textual de ementa/dispositivo quando disponível
- NUNCA inventar SC/IN/lei/dispositivo — só usar o que o retrieval trouxe
- Sinalizar SC com norma base alterada (caducidade)
- Tamanho: 4000-8000 palavras

## Salvar relatório

`outputs/wip/relatorios/{slug-pergunta}-{YYYYMMDD}.md`

## Cross-base com CARF

Se a pergunta tiver dimensão contenciosa (litígio), invocar paralelamente:
```
/td:carf:pesquisar "<mesma pergunta>"
```
e consolidar os dois ângulos no relatório final (orientação prévia RFB + jurisprudência CARF).

## Pré-requisitos

- Embeddings gerados (`/td:tax-intelligence:rfb-atos:embed` rodado)
- `references/architecture.md` lido para entender modelo de dados antes de query SQL ad-hoc
