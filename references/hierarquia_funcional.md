# Hierarquia funcional dos atos da Receita Federal

Documento de referencia para retrieval, ranking e regras de propagacao. Baseado em correcoes do usuario (sessao 2026-04-29).

## 1. Tres naturezas funcionais

Atos da RFB nao formam UMA hierarquia. Formam TRES sistemas funcionalmente diferentes que coexistem:

| Natureza | Tipos | Funcao | Quando o user busca |
|---|---|---|---|
| **Normativa** | IN, IN Conjunta, Decreto, Portaria Normativa, Resolucao, ADI, ADN, Norma de Execucao, Parecer Normativo | **Regula a materia de forma geral**. E o "manual" da RFB | "Como funciona o regime nao-cumulativo do PIS" |
| **Consultiva** | SC, SCI, SD | **Orientacao para caso concreto** trazido pelo consulente | "Como a RFB respondeu sobre tributacao de SaaS" |
| **Operacional** | AD, ADE, ADE Conjunto, Portaria, Portaria Conjunta, Ordem de Servico, Decisao, Despacho, Despacho Decisorio, Termo de Exclusao, Edital, Comunicado, Recomendacao, Nota, Nota Tecnica, Consulta Publica, Exposicao de Motivos, Orientacao Normativa | **Atos administrativos pontuais** (habilitacao, exclusao, nomeacao) | "Quem foi habilitado no PADIS em 2024" |

**Comparar peso entre naturezas nao faz sentido.** Uma SC respondendo "esse caso especifico se enquadra" nao substitui a IN que regulamenta a materia toda. E vice-versa.

## 2. Hierarquia DENTRO da natureza Consultiva

```
                Cosit (cupula tributaria)
                Coana (cupula aduaneira)
                       |
        emite ---------+----------
        |              |          |
        v              v          v
   SD Cosit       SC Cosit    Parecer
   uniformiza    vinculante   Normativo
   divergencias  geral        vinculante
                              geral
        |
        | reforma SCs anteriores
        v
   SC Disit/SRRF{NN}     SC Diana/SRRF{NN}
   vinculante LOCAL      vinculante LOCAL
   (regional + caso)     (aduaneira regional)
```

### Regras de propagacao

1. **SD Cosit reforma -> todas as SCs anteriores divergentes ficam superadas.** Reflete em `ato_relacao` com `tipo_relacao = uniformiza` ou `reforma`.
2. **SC Cosit nova sobre o mesmo assunto -> SCs Disit/Diana que contradigam ficam caducas** (mesmo sem revogacao formal). Hoje status_vigencia nao captura isso automaticamente; futuro: detectar via embedding similarity + diff temporal.
3. **Tema STF com Repercussao Geral / Tema STJ Repetitivo -> RFB e CARF tem que seguir** apos transito em julgado. Implementacao via Parecer SEI da PGFN (entra como `dispositivo` ou `fundamentacao_externa` na materia que cita o Tema).
4. **Sumula CARF Vinculante -> RFB segue.**

## 3. Hierarquia DENTRO da natureza Normativa

Nao ha hierarquia rigida — todos sao instrumentos diferentes:

- **Decreto** regulamenta lei (Poder Executivo, Presidencia)
- **Instrucao Normativa (IN)** regulamenta dispositivos da RFB. Sao os "manuais" da materia. Ex: IN 2.121/2022 trata "tudo sobre PIS/COFINS"
- **Portaria Normativa** estabelece normas internas
- **Resolucao** orgaos colegiados (CGSN, Gecex)
- **ADI (Ato Declaratorio Interpretativo)** interpreta dispositivo de outro ato sem alterar texto
- **ADN (Ato Declaratorio Normativo)** declaracao normativa
- **Parecer Normativo** orientacao normativa de Cosit/Coana

Dentro do mesmo tipo, **vence o mais recente** (decay temporal). Mas o estudo historico precisa da redacao da epoca — ver §5.

## 4. Modos de busca (`--modo`)

Como cada natureza atende necessidade diferente, o retrieval oferece 4 perfis:

| Modo | Quando usar | Pesos por natureza |
|---|---|---|
| **pratico** (default) | "Como faco / como e a regra atual" | Normativa **1.20**, Consultiva 1.05, Operacional 0.50 |
| **consultivo** | "Como a RFB respondeu casos parecidos" | Normativa 1.05, Consultiva **1.20**, Operacional 0.30 |
| **operacional** | "Quem foi habilitado / excluido / nomeado" | Normativa 0.70, Consultiva 0.70, Operacional **1.20** |
| **historico** | Estudo retrospectivo de evolucao da materia | Todos 1.00; obrigatorio `--vigente-em <data>` |

Dentro de cada natureza, boosts secundarios:

- Consultiva: SD (×1.15) > SC Cosit (×1.10) > SC Disit/Diana (×1.00)
- Normativa: ato vigente nunca alterado (×1.00) > ato alterado (×0.95) > nao vigente (×0.40)
- Operacional: sem hierarquia interna — depende do conteudo

## 5. Vigencia temporal — `--vigente-em <data>`

Mesma IN tem multiplas redacoes ao longo do tempo. Estudo historico precisa da redacao **vigente naquela data**, nao a atual.

### Funcao SQL `texto_vigente_em(ato_id, data)`

Retorna a versao de cada segmento que estava vigente na data solicitada, considerando:
- Versoes anteriores aa data sao preservadas se nenhuma alteracao posterior tinha entrado em vigor
- Segmentos `omitir=TRUE` (revogados) nao aparecem se a revogacao ja tinha ocorrido
- Segmentos posteriores ao crawl mas com `data_inicio_vigencia > data_solicitada` sao excluidos

Combinada com filtro temporal nas SCs (`data_publicacao <= data_solicitada`), permite reconstruir o estado da materia naquele momento.

## 6. Implementacao no retrieval

```python
score = (1/(60+pos_BM25) + 1/(60+pos_cosine))
      * peso_natureza[modo][natureza_do_ato]      # 0.30 a 1.20
      * peso_secundario_dentro_da_natureza        # 0.40 a 1.15
      * fator_recencia                            # decay com meia-vida 15 anos
      + boost_marco                               # +0.08 a +0.10 se cita Tema STF/STJ ou Sumula
```

Pesos sao configuraveis em `references/ranking_weights.json`. Ajustar conforme dados de uso real.

## 7. Decisoes abertas (revisar com dados de uso)

- **Reranker** (BGE-reranker / Cohere / Haiku-as-reranker) — adiciona 50-200ms de latencia. Vale se top-5 estiver errado >20% do tempo. Avaliar com queries reais.
- **Tuning de k** do RRF — k=60 e generico. Para direito tributario talvez k=40 ou k=80 funcionem melhor. Precisa conjunto de teste com "verdade" definida.
- **Auto-detect modo da query** — em vez de `--modo`, classificar via LLM se a pergunta e pratica/consultiva/operacional. Custa 1 chamada LLM por query, mas elimina parametro. Avaliar latencia/custo.
- **Boost por confiabilidade do consulente** — SC de empresa grande vs MEI tem mesmo peso. Provavelmente nao deveria mudar; mas se aparecer caso de viés, considerar.
- **Personalizacao por cliente** — empresa de varejo busca PIS/COFINS, empresa de servicos busca Simples. Dar leve boost no historico do user. Pos-uso real.
