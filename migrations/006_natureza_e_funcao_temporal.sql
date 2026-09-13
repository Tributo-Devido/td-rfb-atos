-- 006_natureza_e_funcao_temporal.sql
-- Adiciona coluna natureza em taxonomia_tipo_ato (normativa | consultiva | operacional)
-- Cria funcao texto_vigente_em(ato_id, data) para reconstrucao temporal.

-- 1) coluna natureza
ALTER TABLE taxonomia_tipo_ato
  ADD COLUMN IF NOT EXISTS natureza VARCHAR(20);

COMMENT ON COLUMN taxonomia_tipo_ato.natureza IS
  'Natureza funcional do ato: normativa (regula materia geral), consultiva (orientacao caso concreto), operacional (ato administrativo pontual). Ver references/hierarquia_funcional.md.';

CREATE INDEX IF NOT EXISTS idx_taxonomia_tipo_ato_natureza
  ON taxonomia_tipo_ato(natureza);

-- 2) funcao auxiliar: data de inicio de vigencia de cada versao de cada segmento
-- versao 1 = data de publicacao do ato; versao N>=2 = data_inicio_vigencia da N-1 esima alteracao do mesmo segmento.
CREATE OR REPLACE VIEW v_ato_segmento_vigencia AS
WITH alteracoes_ranked AS (
  SELECT
    h.ato_alvo_id,
    h.id_segmento_alvo,
    h.data_inicio_vigencia,
    ROW_NUMBER() OVER (
      PARTITION BY h.ato_alvo_id, h.id_segmento_alvo
      ORDER BY h.data_inicio_vigencia NULLS LAST, h.id
    ) AS n_alteracao
  FROM ato_alteracao_historico h
)
SELECT
  s.ato_id,
  s.id_segmento,
  s.versao_segmento,
  s.ordem,
  s.texto_integra,
  s.is_compilado,
  s.is_omitido,
  CASE
    WHEN s.versao_segmento = 1 THEN a.data_publicacao
    ELSE COALESCE(ar.data_inicio_vigencia, a.data_publicacao)
  END AS data_inicio_vigencia
FROM ato_segmento s
JOIN atos a ON a.id = s.ato_id
LEFT JOIN alteracoes_ranked ar
  ON ar.ato_alvo_id = s.ato_id
 AND ar.id_segmento_alvo = s.id_segmento
 AND ar.n_alteracao = s.versao_segmento - 1;

COMMENT ON VIEW v_ato_segmento_vigencia IS
  'Mapeia cada versao de segmento a data em que entrou em vigor. Versao 1 = publicacao; versao N>=2 = data_inicio_vigencia da (N-1)-esima alteracao.';

-- 3) funcao texto_vigente_em(ato_id, data)
CREATE OR REPLACE FUNCTION texto_vigente_em(p_ato_id BIGINT, p_data DATE)
RETURNS TABLE (
  ordem INT,
  id_segmento BIGINT,
  versao_segmento INT,
  texto_integra TEXT,
  data_inicio_vigencia DATE
)
LANGUAGE sql STABLE AS $$
  WITH versoes_validas AS (
    SELECT
      v.ato_id, v.id_segmento,
      MAX(v.versao_segmento) AS versao_aplicavel
    FROM v_ato_segmento_vigencia v
    WHERE v.ato_id = p_ato_id
      AND (v.data_inicio_vigencia IS NULL OR v.data_inicio_vigencia <= p_data)
      AND v.is_omitido = FALSE
    GROUP BY v.ato_id, v.id_segmento
  )
  SELECT v.ordem, v.id_segmento, v.versao_segmento, v.texto_integra, v.data_inicio_vigencia
  FROM v_ato_segmento_vigencia v
  JOIN versoes_validas vv
    ON vv.ato_id = v.ato_id
   AND vv.id_segmento = v.id_segmento
   AND vv.versao_aplicavel = v.versao_segmento
  ORDER BY v.ordem;
$$;

COMMENT ON FUNCTION texto_vigente_em(BIGINT, DATE) IS
  'Reconstrucao temporal: retorna segmentos do ato vigentes na data solicitada (exclui omitidos e versoes futuras).';
