-- 008_sinal_materia.sql
-- Adiciona coluna `sinal` em ato_materia, populada por LLM (Haiku 4.5) em batch.
-- Substitui a heuristica keyword-based de detectar_conflitos.py:classificar() por
-- classificacao semantica robusta (AUTORIZA / VEDA / CONDICIONA / INDETERMINADO).

ALTER TABLE ato_materia
  ADD COLUMN IF NOT EXISTS sinal VARCHAR(15),
  ADD COLUMN IF NOT EXISTS sinal_classified_at TIMESTAMP,
  ADD COLUMN IF NOT EXISTS sinal_model VARCHAR(50);

ALTER TABLE ato_materia
  DROP CONSTRAINT IF EXISTS sinal_check;

ALTER TABLE ato_materia
  ADD CONSTRAINT sinal_check
  CHECK (sinal IS NULL OR sinal IN ('AUTORIZA','VEDA','CONDICIONA','INDETERMINADO'));

CREATE INDEX IF NOT EXISTS idx_materia_sinal ON ato_materia(sinal) WHERE sinal IS NOT NULL;

COMMENT ON COLUMN ato_materia.sinal IS
  'Classificacao semantica da posicao da materia: AUTORIZA (admite o direito/credito), VEDA (rejeita), CONDICIONA (admite com requisitos), INDETERMINADO (nao classificavel). Populado por LLM batch via classificar_sinal_haiku.py.';
