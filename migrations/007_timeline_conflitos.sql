-- 007_timeline_conflitos.sql
-- Tabela para persistir pares de materias contraditorias detectadas por similaridade + sinal oposto
-- ("permitido" vs "vedado" sobre mesmo fato), ordenadas por data.

CREATE TABLE IF NOT EXISTS conflito_temporal (
  id BIGSERIAL PRIMARY KEY,
  tema_especifico VARCHAR(120) NOT NULL,
  materia_a_id BIGINT NOT NULL REFERENCES ato_materia(id) ON DELETE CASCADE,
  materia_b_id BIGINT NOT NULL REFERENCES ato_materia(id) ON DELETE CASCADE,
  similaridade NUMERIC(6,4) NOT NULL,
  sinal_a VARCHAR(20),                 -- AUTORIZADO | VEDADO | CONDICIONADO
  sinal_b VARCHAR(20),
  data_a DATE NOT NULL,
  data_b DATE NOT NULL,
  ato_b_supera BOOLEAN NOT NULL,       -- TRUE quando b e mais novo e tem sinal oposto
  formal_relacao_existe BOOLEAN DEFAULT FALSE,  -- TRUE se ja ha ato_relacao explicita
  observacao TEXT,
  created_at TIMESTAMP DEFAULT now(),
  CONSTRAINT conflito_uq UNIQUE (materia_a_id, materia_b_id)
);

CREATE INDEX IF NOT EXISTS idx_conflito_tema ON conflito_temporal(tema_especifico);
CREATE INDEX IF NOT EXISTS idx_conflito_data_b ON conflito_temporal(data_b DESC);
CREATE INDEX IF NOT EXISTS idx_conflito_supera ON conflito_temporal(ato_b_supera) WHERE ato_b_supera=TRUE;

COMMENT ON TABLE conflito_temporal IS
  'Pares de materias com mesmo fato (similaridade > limiar) e sinais opostos. A mais recente potencialmente supera a anterior dentro da janela de prescricao.';
