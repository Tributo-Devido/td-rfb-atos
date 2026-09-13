# Exemplo de aplicação do template — H4 (combustíveis em prestação de serviços)

Demonstração de como uma hipótese da tabela mestra se desdobra em laudo. Baseado no estudo `combustiveis-alcool-creditos/v1`, aplicando o template `template-estudo.md`.

---

## Linha da tabela mestra (01-tabela-mestra.md)

| # | Hipótese | Status | Requisitos cumulativos | Fundamento legal | Atos-chave | Risco / Tensão |
|---|---|---|---|---|---|---|
| H4 | Combustível consumido em veículos/máquinas **diretamente afetados** à prestação de serviços | ✅ | 1) Atividade-fim = prestação de serviços (ou serviço dentro do processo produtivo); 2) Veículo/máquina é instrumento direto (não administrativo, não pré-produtivo, não revenda); 3) Combustível efetivamente consumido na prestação; 4) NF-e do combustível com CNPJ do tomador; 5) Controle interno de consumo por veículo/operação | Lei 10.637/2002 art. 3º II (PIS); Lei 10.833/2003 art. 3º II (COFINS); IN RFB 2.121/2022 art. 176; Tema 779/STJ (REsp 1.221.170/PR); PN Cosit 5/2018 | SC COSIT 91/2015; 80/2019; 32/2021; 18/2020; 293/2017; 28/2024 (caminhão betoneira); 148/2019 (transp. atividade-fim); 486/2017 (transp. atividade-fim) | Glosa frequente quando frota é mista (administrativa + serviço); SC 561/2017 e 211/2023 vedam frota administrativa/distribuição; precisa segregação |

---

## Laudo (02-laudo-por-hipotese.md)

## H4 — Combustível em veículos/máquinas afetados diretamente à prestação de serviços

**Status:** ✅ AUTORIZA
**Grau de fundamentação:** ★★★★★

### Cenário fatual

Pessoa jurídica não-cumulativa cuja atividade-fim é prestação de serviços (transporte rodoviário de cargas, montagem industrial, locação operada, terraplenagem, manutenção em campo, betoneira, etc.) consome combustível (diesel, gasolina, GLP) em veículos ou máquinas que são **instrumentos diretos** dessa prestação. O serviço é tributado por PIS/COFINS — não há operação de revenda de combustível.

Distinção crítica: o veículo/máquina deve ser **integrado ao processo de prestação**. Não basta ser "da empresa" — frota administrativa (deslocamento de funcionários, viagens comerciais) e frota de distribuição pós-venda (entrega de mercadoria já produzida) NÃO se qualificam — caem em hipótese específica vedada (H10 e H9 do estudo).

### Requisitos cumulativos para usufruir

1. **Atividade-fim** do contribuinte é prestação de serviços tributada (ou serviço **dentro** do processo produtivo do bem que vai ser vendido).
2. **Veículo/máquina é instrumento direto da prestação** — caminhão de transportadora, betoneira, retroescavadeira, máquina de terraplenagem, gerador a diesel acoplado a uma operação de campo. Não vale frota administrativa nem de distribuição pós-produção.
3. **Combustível efetivamente consumido na prestação** — controle por OS/operação, ficha de abastecimento, GPS/telemetria.
4. **NF-e do combustível em nome do contribuinte** (CNPJ do tomador). Não vale combustível adquirido em nome de outro estabelecimento ou pago por terceiro.
5. **Controle interno segregando** consumo da frota-serviço da frota-administrativa, se houver as duas (sob risco de glosa proporcional).

### Fundamento legal

- **Lei 10.637/2002, art. 3º, II** (PIS) — crédito sobre "bens e serviços, utilizados como insumo na prestação de serviços e na produção ou fabricação de bens ou produtos destinados à venda".
- **Lei 10.833/2003, art. 3º, II** (COFINS) — redação espelhada.
- **IN RFB 2.121/2022, art. 176** — regulamenta o conceito de insumo conforme Tema 779/STJ.
- **Tema 779/STJ (REsp 1.221.170/PR, j. 22/02/2018)** — insumo é o bem ou serviço de cuja **subtração** resulta a impossibilidade ou perda substancial de qualidade do produto/serviço (essencialidade + relevância).
- **PN Cosit 5/2018** — adoção administrativa do critério do STJ; supera IN SRF 247/2002 e IN SRF 404/2004.

### Posição da Receita Federal

**Atos que sustentam (✅ AUTORIZA):**
- **SC COSIT 91/2015** — combustível em veículo de prestação de serviço, geral.
- **SC COSIT 80/2019** — diesel em frota da prestação (antes de Tema 779 expressamente).
- **SC COSIT 32/2021** — pós-Tema 779.
- **SC COSIT 18/2020** — pós-Tema 779; reforça essencialidade.
- **SC COSIT 293/2017** — montagem industrial; combustível em transporte de partes/peças que serão montadas no estabelecimento do adquirente.
- **SC COSIT 28/2024** — caminhão betoneira (concreto produzido no trajeto até a obra).
- **SC COSIT 148/2019** — transportadora rodoviária; diesel/peças/manutenção/subcontratação como insumos da atividade-fim.
- **SC COSIT 486/2017** — transportadora rodoviária; idem.
- **SC DISIT/SRRF09 107/2005** — transportadora; aplicação local consistente.

**Atos que afastam / limitam:**
- **SC COSIT 561/2017; 490/2017; 275/2018; 35/2023; 290/2024** — combustível em frota própria de **entrega/distribuição pós-produção** (mercadoria revendida). Distinção crítica: aqui o veículo é da **distribuição**, não da **prestação**. Frota mista exige segregação.
- **SC COSIT 211/2023; 37/2021** — combustível em frota administrativa / deslocamento de funcionários. Vedado.
- **SD COSIT 7/2016; 10/2017; SC COSIT 213/2017** — combustível em **etapa pré-produtiva** (florestamento, corte/transporte de madeira). Doutrina do "insumo do insumo" — vedado, embora SC COSIT 32/2022 mostre evolução parcial.
- **SC COSIT 215/2025** — combustível em máquinas locadas a terceiros. Vedado pelo critério da inexistência de previsão legal para o locador (mas tese explorável — ver H{N} oportunidades).

**Conflitos / mudanças de posição (extraído de `conflito_temporal`, sim ≥ 0.92):**
- SC 101/2007 SRRF10 (VEDA) → SC 441/2011 SRRF08 (AUTORIZA) — sim 0.982. DISIT regional virou posição.
- SC 89/2012 SRRF08 (AUTORIZA) → SD COSIT 3/2017 (VEDA) — sim 0.980. SD vinculante posterior reverteu (escopo: frete revenda monofásico, não atividade-fim).
- SC 294/2008 SRRF09 (VEDA) → SC 207/2019 COSIT (AUTORIZA) — sim 0.972. Após Tema 779.
- *Lista completa no anexo 99-atualizacao-conflitos.md*.

### Riscos de glosa específicos

- **Frota mista sem segregação:** fiscalização tende a glosar 100% se contribuinte não comprovar a segregação entre frota-serviço (insumo) e frota-administrativa/distribuição (não-insumo).
- **Falta de controle de consumo por operação:** sem ficha de abastecimento por veículo/operação ou GPS/telemetria, presunção contra o contribuinte.
- **NF-e em nome de outro CNPJ:** combustível pago pelo motorista ou em nome de outro estabelecimento → glosa.
- **Atividade marginal vs. atividade-fim:** se o serviço com aquele veículo é minoritário (ex: locação ocasional dentro de empresa industrial), a Receita pode descaracterizar.
- **Extensão pré-produtiva:** transporte de matéria-prima própria entre estabelecimentos da mesma empresa cai em zona cinza (SD 7/2016 vs. SC COSIT 32/2022) — preferir caracterizar como insumo na coleta, não como movimentação interna.

### Tese de defesa (se houver autuação)

- **Argumento principal:** Tema 779/STJ + PN Cosit 5/2018 reconhecem combustível em veículo da prestação como insumo essencial. Interpretação restritiva da Receita ofende o art. 195 §12 da CF.
- **Comprovação:** apresentar contratos de prestação, OS, registros telemétricos, ficha de abastecimento, demonstração contábil de proporção da frota.
- **Acórdãos CARF favoráveis** (consulta `/td:carf:pesquisar` por CREDITAMENTO.COMBUSTIVEL + atividade-fim): rodar para produzir lista atualizada antes de defesa.
- **Defesa contra reclassificação para frota administrativa:** demonstrar que o serviço prestado **depende** daquele veículo (sem ele, perda substancial de qualidade ou impossibilidade — critério de essencialidade do Tema 779).

---

## O que mudou em relação ao formato atual do `99-sintese.md`

| Antes | Depois |
|---|---|
| Linha 21 do 99-sintese: "Combustível em veículos/máquinas afetados diretamente à prestação de serviço \| ✅ \| SC COSIT 91/2015; 80/2019; 32/2021; 18/2020; 293/2017" | Tabela mestra com **5 colunas adicionais**: requisitos, fundamento legal, atos negativos, risco/tensão, grau ★ |
| Fundamento legal mencionado en passant no texto narrativo | **Seção dedicada** com Lei + artigo + IN + Tema STJ |
| Requisitos diluídos no texto | **Lista numerada** de requisitos cumulativos verificáveis |
| Conflitos não mencionados | **Subsecção** "Conflitos detectados" com pares concretos da `conflito_temporal` |
| Sem tese de defesa | **Seção** com argumento + comprovação + integração com `td-carf` |
