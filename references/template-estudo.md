# Template canônico — Estudo td-rfb-atos

Estrutura padronizada para todo estudo gerado por `td-rfb-atos` sobre uma frente tributária (créditos de PIS/COFINS, IRPJ/CSLL, ICMS, etc.).

A estrutura é progressiva: **tabela** dá visão consolidada para decisão rápida → **laudo** justifica cada linha com requisitos, fundamentos, posição da Receita e riscos.

## Diretório padrão

```
{estudo}/v{N}/
├── 00-objetivo-e-metodologia.md          ← escopo, fonte, premissa jurídica, modo de busca
├── 01-tabela-mestra.md                   ← TABELA: 1 linha por hipótese
├── 02-laudo-por-hipotese.md              ← LAUDO: 1 seção H{N} por hipótese da tabela
├── 03-oportunidades-de-tese.md           ← Top N oportunidades + grau de fundamentação ★
├── 04-riscos-de-glosa.md                 ← Riscos consolidados (com fundamento da Receita)
├── 05-lacunas-da-base.md                 ← Pontos abertos / lacunas
├── 99-atualizacao-conflitos.md           ← Anexo automático (gerado por classificador v2)
├── 99-sintese.md                         ← Síntese executiva (1–2 páginas)
├── scs-da-base/                          ← SCs/SDs catalogadas (input bruto)
└── deep-research/                        ← Detalhamento Opus por sub-tema
```

## Vocabulário fixo

- **Status:** ✅ AUTORIZA · ❌ VEDA · ⚠️ CONDICIONA · 🔍 LACUNA (sem ato)
- **Grau de fundamentação:** ★★★★★ (consolidado) · ★★★★☆ (forte) · ★★★☆☆ (médio) · ★★☆☆☆ (frágil) · ★☆☆☆☆ (frontal contra a RFB)
- Numeração de hipóteses: H1, H2, H3... (sequencial, mantém-se entre versões para facilitar referência cruzada)

## 01-tabela-mestra.md

Tabela única, 1 linha por hipótese de uso, com TODAS as colunas abaixo. Tabela é a "tela de decisão" — quem só lê isso já sabe se pode ou não tomar o crédito.

| # | Hipótese | Status | Requisitos cumulativos | Fundamento legal | Atos-chave | Risco / Tensão |
|---|---|---|---|---|---|---|
| H1 | (cenário fatual concreto) | ✅/❌/⚠️/🔍 | 1) … 2) … 3) … (obrigatórios e cumulativos) | Lei X art. Y; Tema Z/STJ; IN A art. B | SC NN/AAAA (ÓRGÃO) | (riscos de glosa, divergência DISIT × COSIT, prazos modulados) |

Regras:
- "Requisitos cumulativos" são os requisitos do contribuinte para usufruir, em linguagem operacional (não jurídica).
- "Fundamento legal" cita a base normativa SEMPRE — Lei + artigo, IN + artigo, Tema STF/STJ aplicável.
- "Atos-chave" cita 3-5 atos representativos. Detalhe completo vai no laudo.
- "Risco / Tensão" é o que pode minar a tese (glosa, conflito formal, modulação temporal).

## 02-laudo-por-hipotese.md

Para cada linha da tabela mestra, uma seção com a estrutura abaixo. Seções numeradas H1, H2, H3... casando com a tabela.

```markdown
## H{N} — {hipótese curta}

**Status:** ✅ AUTORIZA (ou outro)
**Grau de fundamentação:** ★★★★☆

### Cenário fatual
{1-2 parágrafos descrevendo a situação concreta. Quem é o contribuinte, o que adquire, como usa, qual é a operação.}

### Requisitos cumulativos para usufruir
1. {requisito operacional 1 — verificável}
2. {requisito operacional 2}
3. {requisito documental — NF, contrato, controle interno}
4. ...

### Fundamento legal
- **Lei 10.637/2002, art. 3º, II** — texto/redação relevante (1 linha)
- **Lei 10.833/2003, art. 3º, II** — idem para COFINS
- **IN RFB 2.121/2022, art. 176** — regulamento administrativo
- **Tema 779/STJ (REsp 1.221.170/PR)** — insumo = essencialidade + relevância
- **PN Cosit 5/2018** — adoção administrativa do critério do STJ
- {citações adicionais conforme o caso}

### Posição da Receita Federal
**Atos que sustentam ({status}):**
- SC COSIT NN/AAAA — fundamento descrito (1 linha)
- SC DISIT/SRRF0X NN/AAAA — fundamento descrito
- ...

**Atos que afastam / limitam (se houver):**
- SC COSIT NN/AAAA — fundamento contrário descrito
- ...

**Conflitos / mudanças de posição detectados (`conflito_temporal`):**
- {auto-extraído do anexo 99-atualizacao-conflitos.md filtrado pela hipótese, com sim ≥ 0.92}
- Ex.: SC 101/2007 (VEDA) → SC 441/2011 (AUTORIZA) sim 0.982 — DISIT regional inverteu
- ...

### Riscos de glosa específicos
- {risco prático 1 — o que a fiscalização tipicamente faz}
- {risco prático 2}
- {requisito documental crítico}

### Tese de defesa (se houver autuação)
- {linha de argumentação jurídica}
- {acórdãos CARF e STJ relevantes — vem da skill td-carf}
```

## 03-oportunidades-de-tese.md

Top N (3-7) oportunidades com argumento, atos sustentando, atos negando, e grau de fundamentação ★. Já segue o padrão atual do estudo de combustíveis (linhas 65-93 do 99-sintese.md). Mantém aqui.

## 04-riscos-de-glosa.md

Tabela: Risco | Hipótese da tabela mestra (H{N}) | Atos da Receita que justificam glosa | Frequência observada | Cuidado documental

## 05-lacunas-da-base.md

Aspectos onde a base td-rfb-atos não traz solução de consulta — sinaliza buracos para Deep Research externo (Gemini), pesquisa CARF (`/td:carf:pesquisar`), ou doutrina.

## 99-atualizacao-conflitos.md

**Gerado automaticamente** pelo script `gerar_anexo_conflitos.py` consumindo a tabela `conflito_temporal`. Não editar manualmente. Já segue padrão atual.

## 99-sintese.md

Síntese executiva de 1-2 páginas. Estrutura fixa:

```markdown
# Síntese Executiva — {tema do estudo}

## 1. Em uma frase
{Resposta direta: o que pode, o que não pode, o que depende.}

## 2. Hipóteses por status
- ✅ Autorizadas: H1, H2, H4, H7 (N hipóteses)
- ❌ Vedadas: H3, H5 (N)
- ⚠️ Condicionadas: H6 (N)
- 🔍 Lacunas: H8 (N)

## 3. Top 3 oportunidades (resumo do 03-)

## 4. Top 3 riscos (resumo do 04-)

## 5. Próximos passos
- Pesquisa adicional necessária (lacunas)
- SCs em vigor a monitorar
- Eventual consulta formal à Receita
```

---

## Quando aplicar este template

Todo NOVO estudo gerado por `td-rfb-atos` segue obrigatoriamente este template.

Para os 2 estudos legados (combustíveis-alcool-creditos/v1 e transporte-cargas-creditos/v1), refatoração é **opcional** — o conteúdo está sólido, apenas o formato não bate. Se refatorar, gerar uma `v2/` mantendo a `v1/` original.
