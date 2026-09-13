-- ===========================================================================
-- td-rfb-atos — Initial schema
-- Banco: tais
-- Versão: v0.1.0
-- Data: 2026-04-27
--
-- IMPORTANTE: este DDL NÃO faz DROP de nada. Tabelas legadas (normas,
-- solucao_de_consulta*, orientacao_tributaria, assunto, tema, tributo,
-- dispositivo_legal, palavra_chave, classificacao_cnae, aplicacao_cnae,
-- ementa, acordaos_categorized, acordaos_chunks, etc.) ficam intocadas.
--
-- Migrações de DADOS são feitas em 003_migrate_from_normas.sql.
-- ===========================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ===========================================================================
-- TAXONOMIA (lookup tables)
-- ===========================================================================

CREATE TABLE IF NOT EXISTS taxonomia_tipo_ato (
  codigo VARCHAR(50) PRIMARY KEY,
  nome TEXT NOT NULL,
  sigla VARCHAR(20),
  sijut_value INTEGER,
  eficacia_default VARCHAR(30),
  ativo BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS taxonomia_orgao_emissor (
  codigo VARCHAR(50) PRIMARY KEY,
  nome TEXT NOT NULL,
  hierarquia INTEGER,
  eficacia_default VARCHAR(30),
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS taxonomia_tema_macro (
  codigo VARCHAR(50) PRIMARY KEY,
  descricao TEXT,
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS taxonomia_tema_especifico (
  codigo VARCHAR(100) PRIMARY KEY,
  tema_macro_codigo VARCHAR(50) REFERENCES taxonomia_tema_macro(codigo),
  descricao TEXT,
  schema_metadata JSONB,
  schema_path TEXT,
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS taxonomia_tributo (
  codigo VARCHAR(30) PRIMARY KEY,
  nome TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS taxonomia_setor_economico (
  codigo VARCHAR(50) PRIMARY KEY,
  nome TEXT,
  created_at TIMESTAMP DEFAULT now()
);

-- ===========================================================================
-- ATOS (tabela base unificada)
-- ===========================================================================

CREATE TABLE IF NOT EXISTS atos (
  id BIGSERIAL PRIMARY KEY,

  -- Identificação
  tipo_ato VARCHAR(50) NOT NULL,
  numero VARCHAR(50) NOT NULL,
  orgao_emissor VARCHAR(50) NOT NULL,
  data_publicacao DATE NOT NULL,

  -- Eficácia / vigência
  eficacia VARCHAR(30),
  status_vigencia VARCHAR(30) DEFAULT 'vigente',
  vigencia_inicio DATE,
  vigencia_fim DATE,

  -- Conteúdo
  ementa TEXT,
  link TEXT,
  pdf_disponivel BOOLEAN DEFAULT FALSE,
  content_disponivel BOOLEAN DEFAULT FALSE,
  analise_completa BOOLEAN DEFAULT FALSE,

  -- Metadata específica do tipo
  metadata JSONB DEFAULT '{}'::jsonb,

  -- Auditoria + rastreabilidade
  fonte_origem VARCHAR(30) DEFAULT 'sijut2_rfb',
  ato_legacy_id INTEGER,
  legacy_table VARCHAR(50),
  created_at TIMESTAMP DEFAULT now(),
  updated_at TIMESTAMP DEFAULT now(),

  CONSTRAINT atos_chave_natural UNIQUE (tipo_ato, numero, orgao_emissor, data_publicacao)
);

CREATE INDEX IF NOT EXISTS idx_atos_tipo ON atos(tipo_ato);
CREATE INDEX IF NOT EXISTS idx_atos_orgao ON atos(orgao_emissor);
CREATE INDEX IF NOT EXISTS idx_atos_publicacao ON atos(data_publicacao DESC);
CREATE INDEX IF NOT EXISTS idx_atos_status ON atos(status_vigencia);
CREATE INDEX IF NOT EXISTS idx_atos_metadata_gin ON atos USING GIN(metadata);
CREATE INDEX IF NOT EXISTS idx_atos_legacy ON atos(legacy_table, ato_legacy_id) WHERE ato_legacy_id IS NOT NULL;

-- ===========================================================================
-- ATO_CONTENT (texto extraído do PDF)
-- ===========================================================================

CREATE TABLE IF NOT EXISTS ato_content (
  ato_id BIGINT PRIMARY KEY REFERENCES atos(id) ON DELETE CASCADE,
  content TEXT NOT NULL,
  fonte_extracao VARCHAR(20),
  caracteres INTEGER,
  paginas INTEGER,
  processed_at TIMESTAMP DEFAULT now()
);

-- ===========================================================================
-- ATO_MATERIA (átomo de análise: 1+ por ato)
-- ===========================================================================

CREATE TABLE IF NOT EXISTS ato_materia (
  id BIGSERIAL PRIMARY KEY,
  ato_id BIGINT NOT NULL REFERENCES atos(id) ON DELETE CASCADE,
  ordem INTEGER NOT NULL,

  -- Categorização
  natureza VARCHAR(30),
  tema_macro VARCHAR(50) NOT NULL,
  tema_especifico VARCHAR(100) NOT NULL,
  subtema VARCHAR(150),
  tags TEXT[] DEFAULT '{}',

  -- Texto contextual
  ementa_trecho TEXT,

  -- Para CARF (decisão colegiada)
  tese_contribuinte TEXT,
  tese_fazenda TEXT,
  tese_adotada TEXT,
  resultado VARCHAR(50),

  -- Para RFB (orientação vinculante)
  fato_consultado TEXT,
  solucao TEXT,
  fundamentacao_resumo TEXT,

  -- Metadata específica do tema (validada contra references/schemas_metadata_tematico/)
  metadata_tematico JSONB DEFAULT '{}'::jsonb,

  -- Embedding consolidado da matéria
  embedding VECTOR(1024),
  embedding_source TEXT,
  embedded_at TIMESTAMP,

  -- Auditoria
  llm_model VARCHAR(50),
  llm_processed_at TIMESTAMP,
  schema_version VARCHAR(10) DEFAULT 'v1',

  CONSTRAINT materia_unica_por_ato UNIQUE (ato_id, ordem)
);

CREATE INDEX IF NOT EXISTS idx_materia_ato ON ato_materia(ato_id);
CREATE INDEX IF NOT EXISTS idx_materia_tema_macro ON ato_materia(tema_macro);
CREATE INDEX IF NOT EXISTS idx_materia_tema_especifico ON ato_materia(tema_especifico);
CREATE INDEX IF NOT EXISTS idx_materia_tags_gin ON ato_materia USING GIN(tags);
CREATE INDEX IF NOT EXISTS idx_materia_metadata_gin ON ato_materia USING GIN(metadata_tematico);
CREATE INDEX IF NOT EXISTS idx_materia_embedding_hnsw ON ato_materia USING hnsw (embedding vector_cosine_ops);

-- ===========================================================================
-- TABELAS N:N (tributos / dispositivos / CNAEs)
-- ===========================================================================

CREATE TABLE IF NOT EXISTS materia_tributo (
  materia_id BIGINT REFERENCES ato_materia(id) ON DELETE CASCADE,
  tributo_codigo VARCHAR(30) NOT NULL,
  regime VARCHAR(30),
  codigo_receita VARCHAR(20),
  PRIMARY KEY (materia_id, tributo_codigo)
);

CREATE INDEX IF NOT EXISTS idx_materia_tributo_codigo ON materia_tributo(tributo_codigo);

CREATE TABLE IF NOT EXISTS materia_dispositivo (
  id BIGSERIAL PRIMARY KEY,
  materia_id BIGINT REFERENCES ato_materia(id) ON DELETE CASCADE,
  tipo_norma VARCHAR(30),
  referencia VARCHAR(200),
  dispositivo VARCHAR(150),
  texto_resumido TEXT,
  tipo_uso VARCHAR(30) DEFAULT 'fundamento'
);

CREATE INDEX IF NOT EXISTS idx_materia_dispositivo_materia ON materia_dispositivo(materia_id);
CREATE INDEX IF NOT EXISTS idx_materia_dispositivo_referencia ON materia_dispositivo(referencia);

CREATE TABLE IF NOT EXISTS materia_cnae (
  materia_id BIGINT REFERENCES ato_materia(id) ON DELETE CASCADE,
  cnae_codigo VARCHAR(10) NOT NULL,
  descricao TEXT,
  relevancia VARCHAR(20),
  confianca VARCHAR(20),
  PRIMARY KEY (materia_id, cnae_codigo)
);

CREATE INDEX IF NOT EXISTS idx_materia_cnae_codigo ON materia_cnae(cnae_codigo);

-- ===========================================================================
-- ATO_RELACAO (grafo unificado entre atos)
-- ===========================================================================

CREATE TABLE IF NOT EXISTS ato_relacao (
  id BIGSERIAL PRIMARY KEY,
  ato_origem_id BIGINT NOT NULL REFERENCES atos(id),
  ato_destino_id BIGINT NOT NULL REFERENCES atos(id),
  tipo_relacao VARCHAR(30) NOT NULL,
  data_relacao DATE,
  parcial BOOLEAN DEFAULT FALSE,
  observacao TEXT,
  fonte VARCHAR(30) DEFAULT 'llm',
  created_at TIMESTAMP DEFAULT now(),
  CONSTRAINT relacao_unica UNIQUE (ato_origem_id, ato_destino_id, tipo_relacao)
);

CREATE INDEX IF NOT EXISTS idx_relacao_origem ON ato_relacao(ato_origem_id);
CREATE INDEX IF NOT EXISTS idx_relacao_destino ON ato_relacao(ato_destino_id);
CREATE INDEX IF NOT EXISTS idx_relacao_tipo ON ato_relacao(tipo_relacao);

-- ===========================================================================
-- ATO_CHUNKS (embeddings granulares — espelho de acordaos_chunks)
-- ===========================================================================

CREATE TABLE IF NOT EXISTS ato_chunks (
  id BIGSERIAL PRIMARY KEY,
  ato_id BIGINT NOT NULL REFERENCES atos(id) ON DELETE CASCADE,
  materia_id BIGINT REFERENCES ato_materia(id) ON DELETE SET NULL,

  tipo_chunk VARCHAR(30) NOT NULL,
  seq INTEGER NOT NULL,
  texto TEXT NOT NULL,
  caracteres INTEGER,

  embedding VECTOR(1024),
  tsvector_pt TSVECTOR,

  metadata JSONB DEFAULT '{}'::jsonb,

  embedded_at TIMESTAMP DEFAULT now(),

  CONSTRAINT chunk_unico UNIQUE (ato_id, tipo_chunk, seq)
);

CREATE INDEX IF NOT EXISTS idx_chunks_ato ON ato_chunks(ato_id);
CREATE INDEX IF NOT EXISTS idx_chunks_materia ON ato_chunks(materia_id);
CREATE INDEX IF NOT EXISTS idx_chunks_tsvector ON ato_chunks USING GIN(tsvector_pt);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw ON ato_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_chunks_metadata_gin ON ato_chunks USING GIN(metadata);

-- ===========================================================================
-- TRIGGER updated_at em atos
-- ===========================================================================

CREATE OR REPLACE FUNCTION trg_atos_updated_at() RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS atos_set_updated_at ON atos;
CREATE TRIGGER atos_set_updated_at
  BEFORE UPDATE ON atos
  FOR EACH ROW EXECUTE FUNCTION trg_atos_updated_at();

-- ===========================================================================
-- VIEW de conveniência: ato com matérias e contagens
-- ===========================================================================

CREATE OR REPLACE VIEW v_atos_resumo AS
SELECT
  a.id,
  a.tipo_ato,
  a.numero,
  a.orgao_emissor,
  a.data_publicacao,
  a.eficacia,
  a.status_vigencia,
  a.pdf_disponivel,
  a.content_disponivel,
  a.analise_completa,
  COUNT(DISTINCT m.id) AS qtd_materias,
  COUNT(DISTINCT m.id) FILTER (WHERE m.embedding IS NOT NULL) AS qtd_materias_embedded,
  COUNT(DISTINCT c.id) AS qtd_chunks,
  ARRAY_AGG(DISTINCT m.tema_macro) FILTER (WHERE m.tema_macro IS NOT NULL) AS temas_macro,
  ARRAY_AGG(DISTINCT m.tema_especifico) FILTER (WHERE m.tema_especifico IS NOT NULL) AS temas_especificos
FROM atos a
LEFT JOIN ato_materia m ON m.ato_id = a.id
LEFT JOIN ato_chunks c ON c.ato_id = a.id
GROUP BY a.id;

-- ===========================================================================
-- FIM
-- ===========================================================================
