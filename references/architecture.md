# Arquitetura — td-rfb-atos

Documento técnico de design. Ler ANTES de modificar DDL, prompts ou pipeline.

## 1. Princípios de design

### P1 — Modelo unificado para qualquer tipo de ato
Uma única tabela `atos` cobre Solução de Consulta, Solução de Divergência, Instrução Normativa, Decreto, Portaria, Parecer Normativo, Atos Declaratórios, etc. Discriminador via `tipo_ato`. Especificidades vão em `atos.metadata JSONB` ou em tabelas de extensão.

**Por quê:** o crawler SIJUT2 retorna 30+ tipos de ato com atributos comuns (numero, orgao, publicacao, ementa) + alguns específicos. Replicar a galáxia relacional atual (`assunto`, `tema`, `tributo`, `dispositivo_legal`, `palavra_chave`, `classificacao_cnae`, `aplicacao_cnae`) por tipo de ato seria explosão combinatória.

### P2 — Átomo de análise é a "matéria"
Uma SC tem 1+ orientações; um acórdão CARF tem 1+ matérias decididas. Cada matéria tem seu próprio `tema_macro`/`tema_especifico`/`tags`/`fundamentação`/`resultado`. Modelado como `ato_materia` (N por ato).

**Por quê:** uma SC pode tratar de "alíquota zero PIS" + "exclusão ICMS base de cálculo" — duas orientações distintas, cada uma com tema próprio. Forçar cardinalidade 1:1 com o ato perderia granularidade no retrieval.

### P3 — Schema tipado por `tema_especifico`
Para cada `tema_especifico` (ex: `CLASSIFICACAO_FISCAL.PRODUTO`), existe um JSON Schema em `references/schemas_metadata_tematico/{TEMA}.json` que define os campos esperados em `ato_materia.metadata_tematico JSONB`.

Exemplo `CLASSIFICACAO_FISCAL.PRODUTO`:
```json
{
  "ncm_pretendido_contribuinte": "2202.99.00",
  "ncm_defendido_fazenda": "2105.00.00",
  "ncm_definido": "2105.00.00",
  "produto_descricao": "sobremesa gelada à base de leite",
  "requisitos_tecnicos": ["composição lipídica", "processo de fabricação"],
  "norma_classificadora": "RDC ANVISA 266/2005"
}
```

**Por quê:** o usuário levantou que `CLASSIFICACAO_FISCAL.PRODUTO` exige `ncm_contribuinte`/`ncm_fazenda`/`ncm_definido` — mas cada `tema_especifico` tem categorização distinta. Forçar todas em colunas relacionais explodiria o DDL (60+ temas × 4-8 colunas cada). JSONB tipado preserva flexibilidade com validação por schema.

### P4 — Grafo unificado de relações entre atos
Tabela única `ato_relacao(origem_id, destino_id, tipo_relacao)` com tipos `revoga`, `altera`, `reforma`, `regulamenta`, `cita`, `contradiz`, `precedente_de`. Aplica-se a SC↔SC, SC↔IN, IN↔Lei, Acórdão↔SC, etc.

**Por quê:** o status_vigencia de uma SC depende de outra SC ter revogado, ou de a IN base ter mudado, ou de SD ter uniformizado. Tudo isso é o mesmo conceito — aresta entre dois atos.

### P5 — Embeddings na granularidade certa
- `ato_materia.embedding` (uma matéria = um vetor consolidado) para retrieval semântico no nível da orientação
- `ato_chunks` com chunks granulares de PDF para retrieval em texto longo (espelha td-carf `acordaos_chunks`)
- Mesmo modelo Gemini text-embedding-004 1024-dim do td-carf → busca cross-base (CARF + RFB) é viável

### P6 — Taxonomia controlada como única fonte de verdade
`references/taxonomia.json` lista os valores permitidos. LLM tem instrução rígida de usar APENAS valores listados. Mecanismo `__NOVO_*` para casos não cobertos (revisão humana periódica vira novos códigos).

## 2. Modelo de dados (canônico)

### Tabela `atos`
```sql
CREATE TABLE atos (
  id BIGSERIAL PRIMARY KEY,
  
  -- Identificação
  tipo_ato VARCHAR(50) NOT NULL,            -- FK lógico para taxonomia
  numero VARCHAR(50) NOT NULL,
  orgao_emissor VARCHAR(50) NOT NULL,
  data_publicacao DATE NOT NULL,
  
  -- Eficácia / vigência
  eficacia VARCHAR(30),                     -- vinculante_geral / vinculante_local / orientativo
  status_vigencia VARCHAR(30) DEFAULT 'vigente',
  vigencia_inicio DATE,
  vigencia_fim DATE,
  
  -- Conteúdo
  ementa TEXT,
  link TEXT,
  pdf_disponivel BOOLEAN DEFAULT FALSE,
  content_disponivel BOOLEAN DEFAULT FALSE,
  analise_completa BOOLEAN DEFAULT FALSE,
  
  -- Metadata específica do tipo (preenchido conforme tipo_ato)
  metadata JSONB DEFAULT '{}'::jsonb,
  
  -- Auditoria
  fonte_origem VARCHAR(30) DEFAULT 'sijut2_rfb',  -- sijut2_rfb, dou, ocr_manual
  ato_legacy_id INTEGER,                          -- FK para registro antigo (normas.id ou solucao_de_consulta.id) durante migração
  legacy_table VARCHAR(50),
  created_at TIMESTAMP DEFAULT now(),
  updated_at TIMESTAMP DEFAULT now(),
  
  CONSTRAINT atos_chave_natural UNIQUE (tipo_ato, numero, orgao_emissor, data_publicacao)
);
```

### Tabela `ato_content`
```sql
CREATE TABLE ato_content (
  ato_id BIGINT PRIMARY KEY REFERENCES atos(id) ON DELETE CASCADE,
  content TEXT NOT NULL,
  fonte_extracao VARCHAR(20),               -- markitdown, convertio_legacy, manual
  caracteres INTEGER,
  paginas INTEGER,
  processed_at TIMESTAMP DEFAULT now()
);
```

### Tabela `ato_materia` (átomo de análise)
```sql
CREATE TABLE ato_materia (
  id BIGSERIAL PRIMARY KEY,
  ato_id BIGINT NOT NULL REFERENCES atos(id) ON DELETE CASCADE,
  ordem INTEGER NOT NULL,                   -- 1, 2, 3 dentro do ato
  
  -- Categorização
  natureza VARCHAR(30),                     -- CARF: preliminar/merito/admissibilidade; RFB: orientacao
  tema_macro VARCHAR(50) NOT NULL,
  tema_especifico VARCHAR(100) NOT NULL,
  subtema VARCHAR(150),
  tags TEXT[] DEFAULT '{}',
  
  -- Texto contextual
  ementa_trecho TEXT,
  
  -- Para CARF (decisão)
  tese_contribuinte TEXT,
  tese_fazenda TEXT,
  tese_adotada TEXT,
  resultado VARCHAR(50),                    -- favoravel_contribuinte/fazenda etc
  
  -- Para RFB (orientação)
  fato_consultado TEXT,                     -- situação que o consulente apresentou
  solucao TEXT,                             -- resposta da RFB
  fundamentacao_resumo TEXT,
  
  -- Metadata específica do tema_especifico (validada contra schemas_metadata_tematico/)
  metadata_tematico JSONB DEFAULT '{}'::jsonb,
  
  -- Embedding consolidado da matéria
  embedding VECTOR(1024),
  embedding_source TEXT,                    -- texto que foi embeddado (ementa_trecho + tags + tese_adotada/solucao)
  embedded_at TIMESTAMP,
  
  -- Auditoria
  llm_model VARCHAR(50),                    -- claude-haiku-4-5
  llm_processed_at TIMESTAMP,
  schema_version VARCHAR(10) DEFAULT 'v1',
  
  CONSTRAINT materia_unica_por_ato UNIQUE (ato_id, ordem)
);

CREATE INDEX idx_materia_ato ON ato_materia(ato_id);
CREATE INDEX idx_materia_tema_macro ON ato_materia(tema_macro);
CREATE INDEX idx_materia_tema_especifico ON ato_materia(tema_especifico);
CREATE INDEX idx_materia_tags_gin ON ato_materia USING GIN(tags);
CREATE INDEX idx_materia_metadata_gin ON ato_materia USING GIN(metadata_tematico);
CREATE INDEX idx_materia_embedding_hnsw ON ato_materia USING hnsw (embedding vector_cosine_ops);
```

### Tabelas N:N para tributos / dispositivos / CNAEs / fundamentação
```sql
CREATE TABLE materia_tributo (
  materia_id BIGINT REFERENCES ato_materia(id) ON DELETE CASCADE,
  tributo_codigo VARCHAR(30) NOT NULL,      -- FK lógico para taxonomia.tributos
  regime VARCHAR(30),                       -- cumulativo/nao_cumulativo/lucro_real/...
  codigo_receita VARCHAR(20),
  PRIMARY KEY (materia_id, tributo_codigo)
);

CREATE TABLE materia_dispositivo (
  id BIGSERIAL PRIMARY KEY,
  materia_id BIGINT REFERENCES ato_materia(id) ON DELETE CASCADE,
  tipo_norma VARCHAR(30),                   -- lei, lei_complementar, decreto, instrucao_normativa, ...
  referencia VARCHAR(200),                  -- "Lei nº 10.833/2003"
  dispositivo VARCHAR(150),                 -- "art. 3º, §2º, inciso II"
  texto_resumido TEXT,
  tipo_uso VARCHAR(30) DEFAULT 'fundamento' -- fundamento_principal/acessorio/citacao
);

CREATE TABLE materia_cnae (
  materia_id BIGINT REFERENCES ato_materia(id) ON DELETE CASCADE,
  cnae_codigo VARCHAR(10) NOT NULL,
  descricao TEXT,
  relevancia VARCHAR(20),                   -- principal/secundaria/exemplificativa
  confianca VARCHAR(20),                    -- alta/media/baixa
  PRIMARY KEY (materia_id, cnae_codigo)
);
```

### Tabela `ato_relacao` (grafo unificado)
```sql
CREATE TABLE ato_relacao (
  id BIGSERIAL PRIMARY KEY,
  ato_origem_id BIGINT NOT NULL REFERENCES atos(id),
  ato_destino_id BIGINT NOT NULL REFERENCES atos(id),
  tipo_relacao VARCHAR(30) NOT NULL,        -- revoga/altera/reforma/regulamenta/cita/contradiz/precedente_de
  data_relacao DATE,
  parcial BOOLEAN DEFAULT FALSE,
  observacao TEXT,
  fonte VARCHAR(30) DEFAULT 'llm',          -- llm/manual/extracao_textual
  CONSTRAINT relacao_unica UNIQUE (ato_origem_id, ato_destino_id, tipo_relacao)
);

CREATE INDEX idx_relacao_origem ON ato_relacao(ato_origem_id);
CREATE INDEX idx_relacao_destino ON ato_relacao(ato_destino_id);
CREATE INDEX idx_relacao_tipo ON ato_relacao(tipo_relacao);
```

### Tabela `ato_chunks` (espelho de `acordaos_chunks` do td-carf)
```sql
CREATE TABLE ato_chunks (
  id BIGSERIAL PRIMARY KEY,
  ato_id BIGINT NOT NULL REFERENCES atos(id) ON DELETE CASCADE,
  materia_id BIGINT REFERENCES ato_materia(id) ON DELETE SET NULL,
  
  tipo_chunk VARCHAR(30) NOT NULL,          -- ementa, relatorio, voto, fundamentacao, body_section
  seq INTEGER NOT NULL,
  texto TEXT NOT NULL,
  caracteres INTEGER,
  
  embedding VECTOR(1024),
  tsvector_pt TSVECTOR,
  
  -- Metadata desnormalizada para filtro rápido (cópia de campos da matéria/ato)
  metadata JSONB DEFAULT '{}'::jsonb,       -- {tributos[], temas[], cnaes[], status_vigencia, eficacia, ...}
  
  embedded_at TIMESTAMP DEFAULT now(),
  
  CONSTRAINT chunk_unico UNIQUE (ato_id, tipo_chunk, seq)
);

CREATE INDEX idx_chunks_ato ON ato_chunks(ato_id);
CREATE INDEX idx_chunks_materia ON ato_chunks(materia_id);
CREATE INDEX idx_chunks_tsvector ON ato_chunks USING GIN(tsvector_pt);
CREATE INDEX idx_chunks_embedding_hnsw ON ato_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_chunks_metadata_gin ON ato_chunks USING GIN(metadata);
```

### Tabelas de taxonomia (lookup)
```sql
CREATE TABLE taxonomia_tipo_ato (
  codigo VARCHAR(50) PRIMARY KEY,           -- SOLUCAO_CONSULTA, SOLUCAO_DIVERGENCIA, INSTRUCAO_NORMATIVA, ...
  nome TEXT NOT NULL,
  sigla VARCHAR(20),                        -- SC, SD, IN, Decreto, ...
  sijut_value INTEGER,                      -- mapeamento para o crawler SIJUT2
  eficacia_default VARCHAR(30),
  ativo BOOLEAN DEFAULT TRUE
);

CREATE TABLE taxonomia_orgao_emissor (
  codigo VARCHAR(50) PRIMARY KEY,           -- COSIT, DISIT_SRRF08, DIANA_SRRF09, COANA, RFB
  nome TEXT NOT NULL,
  hierarquia INTEGER,                       -- ordem de relevância (1=mais alta)
  eficacia_default VARCHAR(30)
);

CREATE TABLE taxonomia_tema_macro (
  codigo VARCHAR(50) PRIMARY KEY,
  descricao TEXT
);

CREATE TABLE taxonomia_tema_especifico (
  codigo VARCHAR(100) PRIMARY KEY,
  tema_macro_codigo VARCHAR(50) REFERENCES taxonomia_tema_macro(codigo),
  descricao TEXT,
  schema_metadata JSONB,                    -- JSON Schema para validar ato_materia.metadata_tematico
  schema_path TEXT                          -- referência para references/schemas_metadata_tematico/{TEMA}.json
);

CREATE TABLE taxonomia_tributo (
  codigo VARCHAR(30) PRIMARY KEY,
  nome TEXT NOT NULL
);

CREATE TABLE taxonomia_setor_economico (
  codigo VARCHAR(50) PRIMARY KEY,
  nome TEXT
);
```

## 3. Fluxo de processamento

### 3.1 — Crawl (diário)
```
extract_normas_sijut.py
  → conecta SIJUT2
  → busca incremental (ano corrente + ano anterior se ato novo após data X)
  → parser HTML
  → UPSERT em atos (chave natural: tipo_ato + numero + orgao_emissor + data_publicacao)
  → registra novos ato_id em fila para enrich
```

### 3.2 — Enrich (diário, processa fila)
```
para cada ato novo (sem analise_completa):
  1. download_pdf.py → baixa PDF (se existe; marca pdf_disponivel)
  2. extract_content.py → MarkItDown → ato_content (marca content_disponivel)
  3. categorize_with_llm.py → Haiku 4.5 com taxonomia.json + schemas_metadata_tematico
       output: 1+ ato_materia (com tema_especifico, metadata_tematico, fundamentacao)
       popula materia_tributo / materia_dispositivo / materia_cnae
       extrai ato_relacao (revogações/citações)
  4. marca atos.analise_completa = TRUE
```

### 3.3 — Embed (diário, incremental)
```
para cada ato_materia sem embedding:
  embed_chunks.py → Gemini text-embedding-004
  → atualiza ato_materia.embedding
  → cria ato_chunks (ementa + content chunked) com embedding + tsvector_pt + metadata
```

### 3.4 — Schedule local
```
Windows Task Scheduler (etapa local-first):
  - Diário 06:00 → run_pipeline.py (crawl → enrich → embed)
  - Logs em scripts/logs/
  - Notificação por email/Slack se falhar (futuro)
```

## 4. Migração dos dados existentes

3 tabelas legadas + galáxia relacional:

```
normas (157.721)                   → atos (filtro tipo_ato='SOLUCAO_CONSULTA' etc)
solucao_de_consulta (14.055)       → atos (com pdf_disponivel correto)
solucao_de_consulta_content (8.309)→ ato_content
solucao_de_consulta_analysis       → ato_materia + materia_tributo + materia_cnae + ...
orientacao_tributaria              → (ignorada — redundante com analysis)
assunto/tema/tributo/...           → ato_materia.tags + materia_* tabelas
```

Estratégia:
1. Migration 003 cria os atos a partir de `normas` (todos os tipos) + dedupe contra `solucao_de_consulta` por chave natural
2. Migration 004 popula `ato_content` a partir de `solucao_de_consulta_content`
3. Migration 005 cria `ato_materia` a partir de `solucao_de_consulta_analysis` + galáxia, com mapeamento de `tema_especifico` (LLM auxiliar para classificar atos legados que não têm tema)
4. Migration 006 popula taxonomia
5. Tabelas legadas ficam em coexistência por 30 dias antes de drop (pode-se renomear para `*_legacy` no momento da promoção)

## 5. Custos estimados

### Backfill único (157k atos)
| Etapa | Quantidade | Modelo | Custo |
|---|---|---|---|
| Categorização | ~143k ementa-only | Haiku 4.5 (~800/300 tokens) | ~$185 |
| Categorização | ~14k com PDF | Haiku 4.5 (~3.500/800 tokens) | ~$70 |
| Embedding | ~157k matérias + chunks | Gemini text-embedding-004 | ~$15-25 |
| **Total backfill** | | | **~$270-280** |

### Operacional (mensal)
| Etapa | Volume diário | Custo/dia | Custo/mês |
|---|---|---|---|
| Crawl | ~50 atos novos | $0 | $0 |
| Categorização | ~50 × Haiku | ~$0.05 | ~$1.50 |
| Embedding | ~50 × Gemini | ~$0.01 | ~$0.30 |
| **Total operacional** | | | **~$2-5/mês** |

## 6. API normasinternet2 — endpoints descobertos

Portal Angular SPA: `https://normasinternet2.receita.fazenda.gov.br/`. Os endpoints REST que alimentam a SPA (descobertos via Playwright + Network):

```
GET /api/ambiente
GET /api/consulta-externa/ato/{idPortal}/visao/original
GET /api/consulta-externa/ato/{idPortal}/visao/vigente
GET /api/consulta-externa/ato/{idPortal}/visao/multivigente
GET /api/consulta-externa/ato/{idPortal}/visao-relacional   ← com hífen, não com barra
GET /api/consulta-externa/ato/{idPortal}/anexo/{idArquivoBinario}   ← PDF
```

Sem autenticação. User-Agent comum + Accept: `application/json` basta.

### Estrutura de `/visao/vigente`

```jsonc
{
  "idAto": 150754,
  "epigrafe": { "tipoAto": {...}, "numeroAto": "71", "dataAto": "2026-04-24", "orgaos": [...] },
  "epigrafeCompleta": "Solução de Consulta Cosit nº 71, de 24 de abril de 2026",
  "dadosPublicacao": "Publicado(a) no DOU de 27/04/2026, seção 1, página 38",
  "dataPublicacao": "2026-04-27",
  "dataVigenciaInicio": "2026-04-27",
  "vigente": true,
  "ementas": [
    { "textoIntegra": "Assunto: ... \n EMENTA ...", "arquivoBinario": null }
  ],
  "outrosSegmentos": [
    // CASO 1: corpo estruturado em JSON
    { "ordemSegmentoAto": 2, "textoIntegra": "RELATÓRIO ... ", "arquivoBinario": null },
    // CASO 2: corpo em PDF anexado
    { "ordemSegmentoAto": 2, "textoIntegra": "", "arquivoBinario": { "idArquivoBinario": 84241, ... } }
  ]
}
```

### Estrutura de `/visao-relacional`

```jsonc
{
  "atoCentral": { "idAto": 150754, "epigrafeBase": "SC Cosit nº 71", "vigencia": 1, ... },
  "impactosPoloAtivo": [    // outros atos que MODIFICAM o ato central
    { "idAto": 140232, "epigrafeBase": "ADE SRRF08 nº 53", "corSimbolo": 3, "sigla": "REV", "hint": "Revoga", "vigencia": 1, ... }
  ],
  "impactosPoloPassivo": [],  // o ato central modifica estes
  "anotacoesPassivasTemDataVigencia": true,
  "anotacoesAtivasTemDataVigencia": true
}
```

### Mapeamento `corSimbolo` → tipo_relacao canônico

Descoberto em amostra de 200 atos:

| corSimbolo | sigla(s) observadas | hint | Cor portal | tipo_relacao |
|---|---|---|---|---|
| 1 | (não observado na amostra) | (referência simples) | azul rgb(0,95,236) | `referencia` |
| 2 | ALT | Altera | amarelo rgb(249,197,44) | `altera` |
| 3 | REV, SEF | Revoga / Torna ou declara sem efeito | vermelho rgb(215,36,64) | `interrompe` |
| 4 | (não observado) | (recuperação de vigência) | verde rgb(52,189,89) | `recupera` |
| 5 | RET | Retifica | cinza rgb(126,126,126) | `retifica` |
| 6 | (não observado) | (anotação futura) | roxo rgb(163,73,164) | `anotacao_futura` |

`corSimbolo` desconhecido vira `corSimbolo_{N}` em `ato_relacao.tipo_relacao` para revisão manual.

### Padrão de conteúdo dos atos

Insight crítico: o **JSON sozinho NÃO é suficiente** para muitos atos:

- **Ementa** (curta, sumário): sempre presente em `ementas[].textoIntegra`
- **Corpo do ato** (relatório + fundamentos + conclusão): pode estar em
  - **`outrosSegmentos[].textoIntegra` estruturado** (atos sem PDF, ex: ADEs operacionais)
  - **PDF anexado** via `outrosSegmentos[].arquivoBinario.idArquivoBinario` (atos com peça assinada — Cosit, IN, Decreto, etc.)

Pipeline correto: SEMPRE JSON + PDF QUANDO HOUVER `arquivoBinario`. MarkItDown extrai texto do PDF e concatena com a ementa em `ato_content.content`.

Comparação numérica observada (SC Cosit 71/2026, idAto=150754):
- JSON `ementas[0].textoIntegra`: 1.464 chars
- PDF (`/anexo/84241`, 464KB): 33.951 chars de texto extraído
- Diferença: 23×, com o PDF contendo **RELATÓRIO + FUNDAMENTOS + CONCLUSÃO** que o JSON não tem

### Status de vigência canônico (cores de fundo do portal)

| Cor RGB | Codigo | Nome |
|---|---|---|
| 166,202,255 (azul claro) | `vigente_nunca_alterado` | Ato vigente que nunca foi alterado |
| 255,223,108 (amarelo claro) | `vigente_alterado` | Ato vigente que já foi alterado |
| 247,146,151 (rosa) | `nao_vigente` | Ato não vigente |
| 102,226,140 (verde claro) | `revigorado` | Ato revigorado |
| 201,201,201 (cinza) | `retificador` | Ato que promove retificação |

### `id_portal` (chave externa)

Cada ato no nosso banco local tem `atos.id_portal INTEGER`, populado por backfill do `link` capturado pelo crawler SIJUT2 antigo via regex `/consulta/externa/(\d+)/`. Cobertura: 100% dos 99.868 atos do crawl inicial.

## 7. Decisões abertas (revisar com usuário)

- **Drop tabelas legadas após X dias** — sugestão 30 dias após go-live com dual-write
- **`ato_legacy_id` / `legacy_table`** — manter para rastreabilidade ou dropar após validação?
- **Taxonomia v1 vs v2** — começar com porte direto do CARF + adições para SC; iterar depois
- **Schemas_metadata_tematico inicial** — quais 5-10 temas merecem schema rico no v0.1.0? Sugestão: CLASSIFICACAO_FISCAL.PRODUTO, CREDITAMENTO.CONCEITO_INSUMO, ALIQUOTA_E_BASE_CALCULO.EXCLUSAO_ICMS, IRPJ_CSLL.AMORTIZACAO_AGIO, CONTRIBUICOES_PREVIDENCIARIAS.REMUNERACAO. Demais temas usam metadata_tematico vazio até serem priorizados.
- **Schedule** — Windows Task Scheduler local primeiro (ele falou). Eventual move para Docker no servidor TDAX ou ECS Scheduled Task.
- **Hooks de validação** — adicionar hooks PreToolUse para validar JSONB de metadata_tematico contra schema do tema (futuro).
