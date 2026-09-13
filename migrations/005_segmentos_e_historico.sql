-- ============================================================
-- 005 — Segmentos versionados + historico de alteracoes (sem LLM)
-- Popula a partir do endpoint /visao/original do normasinternet2
-- ============================================================

-- Segmentos do ato (artigos, paragrafos, incisos, alineas, ementa)
-- versionado: cada alteracao gera nova linha, identificada por (id_segmento, versao_segmento)
CREATE TABLE IF NOT EXISTS ato_segmento (
  ato_id BIGINT NOT NULL REFERENCES atos(id) ON DELETE CASCADE,
  id_segmento BIGINT NOT NULL,                    -- chave externa do portal
  versao_segmento INT NOT NULL DEFAULT 1,         -- 1=original, 2=primeira alteracao, ...
  ordem INT,                                       -- ordemSegmentoAto
  id_tipo_segmento INT,                            -- tipo (1=ementa, etc; mapear depois)
  id_assunto INT,
  texto_integra TEXT,
  is_original BOOLEAN DEFAULT FALSE,               -- se faz parte da redacao publicada
  is_compilado BOOLEAN DEFAULT FALSE,              -- TRUE = vigente HOJE (apos alteracoes)
  is_tachado BOOLEAN DEFAULT FALSE,
  is_omitido BOOLEAN DEFAULT FALSE,                -- TRUE = revogado/suprimido
  is_agendado BOOLEAN DEFAULT FALSE,
  raw JSONB,                                       -- payload original do portal pra audit
  created_at TIMESTAMP DEFAULT now(),
  PRIMARY KEY (ato_id, id_segmento, versao_segmento)
);

CREATE INDEX IF NOT EXISTS idx_ato_segmento_ato ON ato_segmento(ato_id);
CREATE INDEX IF NOT EXISTS idx_ato_segmento_compilado ON ato_segmento(ato_id, ordem) WHERE is_compilado=TRUE;
CREATE INDEX IF NOT EXISTS idx_ato_segmento_id_segmento ON ato_segmento(id_segmento);
CREATE INDEX IF NOT EXISTS idx_ato_segmento_tsv ON ato_segmento USING GIN(to_tsvector('portuguese', texto_integra));

-- Historico de alteracoes: cada anotacao do tipo "Alterado(a) pelo(a)..." vira uma linha
CREATE TABLE IF NOT EXISTS ato_alteracao_historico (
  id BIGSERIAL PRIMARY KEY,
  ato_alvo_id BIGINT NOT NULL REFERENCES atos(id) ON DELETE CASCADE,
  id_segmento_alvo BIGINT NOT NULL,                  -- segmento que foi alterado
  ato_modificador_id BIGINT REFERENCES atos(id),     -- resolvido via id_portal (NULL se desconhecido)
  id_ato_modificador_portal INT NOT NULL,            -- fallback / chave externa
  data_inicio_vigencia DATE,                         -- dataInicioVigencia (data REAL do efeito)
  data_republicacao DATE,
  texto_anotacao TEXT,                               -- "[Alterado(a) pelo(a)...]"
  raw_anotacao_id INT,                               -- idAnotacao do portal
  eh_agendamento BOOLEAN DEFAULT FALSE,
  texto_agendamento TEXT,
  created_at TIMESTAMP DEFAULT now(),
  CONSTRAINT alteracao_uq UNIQUE (ato_alvo_id, id_segmento_alvo, raw_anotacao_id)
);

CREATE INDEX IF NOT EXISTS idx_alteracao_alvo ON ato_alteracao_historico(ato_alvo_id);
CREATE INDEX IF NOT EXISTS idx_alteracao_modif ON ato_alteracao_historico(ato_modificador_id);
CREATE INDEX IF NOT EXISTS idx_alteracao_data ON ato_alteracao_historico(data_inicio_vigencia);
CREATE INDEX IF NOT EXISTS idx_alteracao_segmento ON ato_alteracao_historico(id_segmento_alvo);

-- View: estado atual (vigente) de cada artigo de um ato
CREATE OR REPLACE VIEW v_ato_vigente_segmentos AS
SELECT a.id AS ato_id, a.tipo_ato, a.numero, a.orgao_emissor,
       s.id_segmento, s.versao_segmento, s.ordem,
       s.texto_integra, s.is_omitido,
       (SELECT COUNT(*) FROM ato_alteracao_historico h
        WHERE h.ato_alvo_id=a.id AND h.id_segmento_alvo=s.id_segmento) AS qtd_alteracoes
FROM atos a
JOIN ato_segmento s ON s.ato_id=a.id
WHERE s.is_compilado = TRUE;

-- View: linha do tempo de alteracoes em um ato
CREATE OR REPLACE VIEW v_ato_evolucao AS
SELECT a_alvo.id AS ato_alvo_id, a_alvo.tipo_ato, a_alvo.numero AS numero_alvo,
       h.id_segmento_alvo,
       h.data_inicio_vigencia,
       h.texto_anotacao,
       a_mod.id AS ato_modificador_id, a_mod.tipo_ato AS modif_tipo, a_mod.numero AS modif_numero,
       a_mod.data_publicacao AS modif_publicacao
FROM ato_alteracao_historico h
JOIN atos a_alvo ON a_alvo.id = h.ato_alvo_id
LEFT JOIN atos a_mod ON a_mod.id = h.ato_modificador_id
ORDER BY a_alvo.id, h.id_segmento_alvo, h.data_inicio_vigencia;
