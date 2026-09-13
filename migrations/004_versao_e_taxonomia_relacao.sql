-- ===========================================================================
-- 004_versao_e_taxonomia_relacao.sql
--
-- 1) ato_content: suportar múltiplas versões (vigente, original, multivigente)
-- 2) Nova tabela taxonomia_tipo_relacao + taxonomia_status_vigencia (vocabulário canônico do portal)
-- 3) Pronto para popular via seed_taxonomia.py (lê taxonomia.json)
-- ===========================================================================

-- 1) Ajustar ato_content: PK composta com tipo_versao
ALTER TABLE ato_content DROP CONSTRAINT IF EXISTS ato_content_pkey;
ALTER TABLE ato_content ADD COLUMN IF NOT EXISTS tipo_versao VARCHAR(20) NOT NULL DEFAULT 'vigente';
ALTER TABLE ato_content ADD CONSTRAINT ato_content_pk PRIMARY KEY (ato_id, tipo_versao);

-- Coluna para registrar referência ao PDF anexo (se o conteúdo veio de PDF)
ALTER TABLE ato_content ADD COLUMN IF NOT EXISTS id_arquivo_binario INTEGER;
ALTER TABLE ato_content ADD COLUMN IF NOT EXISTS pdf_path TEXT;

-- 2) Nova tabela: taxonomia_tipo_relacao (canônica do portal)
CREATE TABLE IF NOT EXISTS taxonomia_tipo_relacao (
  codigo VARCHAR(30) PRIMARY KEY,
  nome TEXT NOT NULL,
  cor_portal_rgb VARCHAR(20),
  origem_canonica BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS taxonomia_status_vigencia (
  codigo VARCHAR(30) PRIMARY KEY,
  nome TEXT NOT NULL,
  cor_portal_rgb VARCHAR(20),
  created_at TIMESTAMP DEFAULT now()
);

-- 3) Coluna em ato_relacao para preservar dados ricos do portal
ALTER TABLE ato_relacao ADD COLUMN IF NOT EXISTS data_efeito DATE;
ALTER TABLE ato_relacao ADD COLUMN IF NOT EXISTS dispositivo_afetado TEXT;
