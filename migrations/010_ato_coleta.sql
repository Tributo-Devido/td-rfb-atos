-- Migration 010 — estado de coleta por ato (rfb_atos)
--
-- Problema que resolve
-- --------------------
-- Hoje a base tem tres booleanos soltos em `rfb_atos.ato` -- `pdf_disponivel`,
-- `content_disponivel` e `analise_completa` -- e nenhum deles distingue as tres
-- situacoes que decidem o que fazer com o ato:
--
--   (a) o portal NAO publica PDF para este ato   -> nada a fazer, e um fato
--   (b) o portal publica, mas nunca tentamos     -> defeito de cobertura
--   (c) tentamos e falhou (404 / timeout / etc)  -> defeito, com causa
--
-- Sem essa distincao, `pdf_disponivel = false` significa as tres coisas ao mesmo
-- tempo, e um ato como a IN RFB 2.121/2022 (ato_id 16630) fica indefinidamente em
-- `status_vigencia = 'nao_disponivel_portal'` sem nunca ser reprocessado: a base
-- sabe que falhou e nao guarda nada que permita agir sobre isso.
--
-- Esta migration cria `rfb_atos.ato_coleta` -- uma linha por ato, com o resultado
-- da ultima tentativa e o historico agregado -- e o invariante que impede a
-- regressao apontada no diagnostico do td-legislacao (D-19/D-20, 01/09/2026):
-- nenhum ato pode ficar `analise_completa = true` sem conteudo.
--
-- Idempotente: pode rodar 2x sem efeito colateral.
-- DDL: requer papel com permissao de DDL no schema rfb_atos (nao o writer DML).

SET search_path = rfb_atos, public;

-- =====================================================================
-- 1. Estado de coleta por ato
-- =====================================================================

CREATE TABLE IF NOT EXISTS rfb_atos.ato_coleta (
    ato_id              INTEGER PRIMARY KEY
                        REFERENCES rfb_atos.ato(id) ON DELETE CASCADE,

    -- Resultado da ultima tentativa de obter o PDF/teor no portal.
    --   nao_tentado         : nunca foi probado (default) -- NAO e "nao tem PDF"
    --   baixado             : arquivo obtido e persistido
    --   sem_pdf_no_portal   : o portal respondeu e nao ha PDF para este ato
    --   erro_http           : o portal respondeu erro (ver http_status)
    --   erro_rede           : timeout / conexao / TLS
    --   erro_extracao       : arquivo obtido, mas a extracao de texto falhou
    --   bloqueado           : recusa deliberada (robots, captcha, rate limit duro)
    pdf_status          TEXT NOT NULL DEFAULT 'nao_tentado',

    url_tentada         TEXT,
    http_status         INTEGER,
    content_type        TEXT,
    bytes_baixados      BIGINT,
    sha256              TEXT,

    tentativas          INTEGER NOT NULL DEFAULT 0,
    primeira_tentativa  TIMESTAMPTZ,
    ultima_tentativa    TIMESTAMPTZ,
    proxima_tentativa   TIMESTAMPTZ,   -- backoff; NULL = elegivel agora

    erro                TEXT,
    origem              TEXT,          -- script/rodada que gravou (auditoria)
    observacao          TEXT,

    criado_em           TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ato_coleta_pdf_status_check CHECK (pdf_status IN (
        'nao_tentado', 'baixado', 'sem_pdf_no_portal',
        'erro_http', 'erro_rede', 'erro_extracao', 'bloqueado'
    )),

    -- "sem PDF no portal" so vale como fato se alguem de fato perguntou ao portal.
    CONSTRAINT ato_coleta_sem_pdf_exige_tentativa CHECK (
        pdf_status <> 'sem_pdf_no_portal' OR tentativas > 0
    )
);

COMMENT ON TABLE rfb_atos.ato_coleta IS
    'Estado da coleta do PDF/teor por ato. Distingue "o portal nao tem" (fato) de '
    '"nunca tentamos" e de "tentamos e falhou" (defeitos). Ver migration 010.';

COMMENT ON COLUMN rfb_atos.ato_coleta.pdf_status IS
    'nao_tentado e o default e NAO significa ausencia de PDF -- significa ausencia '
    'de informacao. So sem_pdf_no_portal, apos tentativa registrada, afirma ausencia.';

CREATE INDEX IF NOT EXISTS ix_ato_coleta_status
    ON rfb_atos.ato_coleta(pdf_status);

-- Fila de trabalho: o que ainda pode ser tentado, em ordem de elegibilidade.
CREATE INDEX IF NOT EXISTS ix_ato_coleta_fila
    ON rfb_atos.ato_coleta(proxima_tentativa NULLS FIRST)
    WHERE pdf_status IN ('nao_tentado', 'erro_http', 'erro_rede', 'erro_extracao');

-- =====================================================================
-- 2. Trigger de atualizado_em
-- =====================================================================

CREATE OR REPLACE FUNCTION rfb_atos.ato_coleta_touch() RETURNS trigger AS $$
BEGIN
    NEW.atualizado_em := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_ato_coleta_touch ON rfb_atos.ato_coleta;
CREATE TRIGGER trg_ato_coleta_touch
    BEFORE UPDATE ON rfb_atos.ato_coleta
    FOR EACH ROW EXECUTE FUNCTION rfb_atos.ato_coleta_touch();

-- =====================================================================
-- 3. Backfill: uma linha por ato, inferindo o que ja da para inferir
-- =====================================================================
--
-- Regra de inferencia, conservadora de proposito:
--   - ato com texto em ato_content        -> 'baixado'   (o teor chegou de algum jeito)
--   - ato sem texto                       -> 'nao_tentado'
--
-- NAO inferimos 'sem_pdf_no_portal' de `pdf_disponivel = false`: essa e exatamente
-- a informacao que a base nao tem hoje. Marcar aqui seria transformar a duvida em
-- fato -- que e o defeito que esta migration existe para corrigir. O
-- backfill_pdf.py e quem promove 'nao_tentado' -> 'sem_pdf_no_portal' contra o portal.

INSERT INTO rfb_atos.ato_coleta (ato_id, pdf_status, tentativas, origem, observacao)
SELECT a.id,
       CASE WHEN c.ato_id IS NOT NULL THEN 'baixado' ELSE 'nao_tentado' END,
       CASE WHEN c.ato_id IS NOT NULL THEN 1 ELSE 0 END,
       'migration_010',
       CASE WHEN c.ato_id IS NOT NULL
            THEN 'inferido do ato_content preexistente'
            ELSE 'sem informacao de coleta antes da 010' END
  FROM rfb_atos.ato a
  LEFT JOIN rfb_atos.ato_content c
         ON c.ato_id = a.id
        AND c.texto_completo IS NOT NULL
        AND length(btrim(c.texto_completo)) > 0
 ON CONFLICT (ato_id) DO NOTHING;

-- =====================================================================
-- 4. Invariante anti-regressao
-- =====================================================================
--
-- Pedido explicito do diagnostico D-19: nenhum ato pode ficar
-- `analise_completa = true` com `content_disponivel = false`.
--
-- Entra como NOT VALID: a constraint passa a valer para toda escrita NOVA
-- imediatamente, sem travar a migration nas 912 linhas legadas que ja violam.
-- Depois de limpar o legado, rodar:
--     ALTER TABLE rfb_atos.ato VALIDATE CONSTRAINT ato_analise_exige_conteudo;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ato_analise_exige_conteudo'
    ) THEN
        ALTER TABLE rfb_atos.ato
            ADD CONSTRAINT ato_analise_exige_conteudo
            CHECK (NOT (analise_completa AND NOT content_disponivel))
            NOT VALID;
    END IF;
END $$;

-- =====================================================================
-- 5. View de cobertura -- o funil em uma linha por ato
-- =====================================================================

CREATE OR REPLACE VIEW rfb_atos.v_cobertura_ato AS
SELECT a.id                                   AS ato_id,
       a.tipo_ato,
       a.numero,
       a.ano,
       a.emissor,
       a.data_publicacao,
       a.status_vigencia,
       a.pdf_disponivel,
       a.content_disponivel,
       a.analise_completa,
       COALESCE(cl.pdf_status, 'nao_tentado') AS pdf_status,
       cl.tentativas,
       cl.ultima_tentativa,
       (ct.ato_id IS NOT NULL)                AS tem_texto,
       ct.caracteres,
       COALESCE(m.n_materias, 0)              AS n_materias,
       COALESCE(m.n_embeddings, 0)            AS n_embeddings,
       -- Estagio alcancado no funil: ato -> pdf -> texto -> materia -> embedding
       CASE
           WHEN COALESCE(m.n_materias, 0) > 0
                AND COALESCE(m.n_embeddings, 0) = COALESCE(m.n_materias, 0)
                                              THEN '5_embeddado'
           WHEN COALESCE(m.n_materias, 0) > 0 THEN '4_categorizado'
           WHEN ct.ato_id IS NOT NULL         THEN '3_com_texto'
           WHEN COALESCE(cl.pdf_status, 'nao_tentado') = 'baixado'
                                              THEN '2_pdf_baixado'
           WHEN COALESCE(cl.pdf_status, 'nao_tentado') = 'sem_pdf_no_portal'
                                              THEN '1_sem_pdf_no_portal'
           ELSE                                    '0_pendente'
       END                                    AS estagio
  FROM rfb_atos.ato a
  LEFT JOIN rfb_atos.ato_coleta cl ON cl.ato_id = a.id
  LEFT JOIN rfb_atos.ato_content ct
         ON ct.ato_id = a.id
        AND ct.texto_completo IS NOT NULL
        AND length(btrim(ct.texto_completo)) > 0
  LEFT JOIN (
        SELECT ato_id,
               count(*)                                    AS n_materias,
               count(*) FILTER (WHERE embedding IS NOT NULL) AS n_embeddings
          FROM rfb_atos.ato_materia
         GROUP BY ato_id
  ) m ON m.ato_id = a.id;

COMMENT ON VIEW rfb_atos.v_cobertura_ato IS
    'Funil de cobertura por ato: ato -> pdf -> texto -> materia -> embedding. '
    'Base do auditar_cobertura.py. Ver migration 010.';
