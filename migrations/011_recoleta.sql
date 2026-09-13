-- Migration 011 — fundação da recoleta (rfb_atos)
--
-- Plano: docs/PLANO-RECOLETA-2026-09-v2.md, F1.6. Tudo ADITIVO: nenhuma coluna ou
-- tabela que o td-analise-piscofins lê muda de forma ou de conteúdo.
--
--   1. ato_texto_visao   — visões original/multivigente do portal (ato_content segue
--                          com uma linha por ato: a vigente; o consumidor faz LEFT JOIN
--                          sem filtrar versão, então outra linha lá duplicaria resultados)
--   2. relação externa   — ato_relacao.destino_id_portal + índice único parcial: a aresta
--                          para ato fora da base passa a ser guardada e idempotente
--                          (o UNIQUE atual trata NULLs como distintos e não a protege)
--   3. base_analise      — por matéria: a categorização leu o teor ou só a ementa
--   4. situacao_portal   — o que o portal disse (vigente, datas), bruto, sem inventar status
--   5. ato_mudanca       — log antes/depois de toda alteração em linha existente (rollback)
--   6. rfb_atos_staging  — área de espera dos não vigentes; SEM acesso do ratio_leitura,
--                          ou seja, invisível ao time até a promoção
--
-- Conferida contra o schema real da nuvem em 13/09/2026 (ato.id BIGINT, id_portal INTEGER,
-- dono de tudo = ratio_admin). Idempotente. DDL: rodar como ratio_admin.

SET search_path = rfb_atos, public;

-- =====================================================================
-- 1. Visões de texto do portal
-- =====================================================================

CREATE TABLE IF NOT EXISTS rfb_atos.ato_texto_visao (
    ato_id        BIGINT NOT NULL REFERENCES rfb_atos.ato(id) ON DELETE CASCADE,
    visao         TEXT   NOT NULL,
    sha256        TEXT   NOT NULL,          -- do texto: a mesma visão muda com o tempo
    texto         TEXT   NOT NULL,
    caminho_json  TEXT,                     -- JSON bruto fica em disco/S3, não no Postgres
    fonte         TEXT,
    run_id        TEXT,
    coletado_em   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ato_id, visao, sha256),
    CONSTRAINT ato_texto_visao_visao_check CHECK (visao IN ('original', 'multivigente'))
);

COMMENT ON TABLE rfb_atos.ato_texto_visao IS
    'Fotos das visões original e multivigente do portal (SIJUT). A vigente continua em '
    'ato_content (1 linha por ato). Ver migration 011.';

-- =====================================================================
-- 2. Relação para ato fora da base
-- =====================================================================

ALTER TABLE rfb_atos.ato_relacao ADD COLUMN IF NOT EXISTS destino_id_portal INTEGER;

COMMENT ON COLUMN rfb_atos.ato_relacao.destino_id_portal IS
    'idAto do portal quando o destino ainda não está na base (ato_destino_id NULL). '
    'destino_externo segue como rótulo legível. O resolvedor liga ao ato_destino_id quando '
    'o destino é coletado. Revogação do portal = tipo_relacao interrompe. Ver migration 011.';

CREATE UNIQUE INDEX IF NOT EXISTS ux_ato_relacao_externa
    ON rfb_atos.ato_relacao (ato_origem_id, destino_id_portal, tipo_relacao)
 WHERE ato_destino_id IS NULL AND destino_id_portal IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_ato_relacao_destino_portal
    ON rfb_atos.ato_relacao (destino_id_portal)
 WHERE destino_id_portal IS NOT NULL;

-- =====================================================================
-- 3. Base da análise, por matéria
-- =====================================================================
-- DEFAULT constante em ADD COLUMN não reescreve a tabela (PostgreSQL 11+).

ALTER TABLE rfb_atos.ato_materia
    ADD COLUMN IF NOT EXISTS base_analise TEXT NOT NULL DEFAULT 'texto';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'ato_materia_base_analise_check') THEN
        ALTER TABLE rfb_atos.ato_materia
            ADD CONSTRAINT ato_materia_base_analise_check
            CHECK (base_analise IN ('texto', 'ementa'));
    END IF;
END $$;

-- =====================================================================
-- 4. Situação bruta do portal
-- =====================================================================

ALTER TABLE rfb_atos.ato ADD COLUMN IF NOT EXISTS situacao_portal JSONB;

COMMENT ON COLUMN rfb_atos.ato.situacao_portal IS
    'O que o portal disse na última coleta (vigente, datas, visão). status_vigencia usa só '
    'o vocabulário do portal; "revogado" é derivado na leitura. Ver migration 011.';

-- =====================================================================
-- 5. Log de mudanças (rollback real)
-- =====================================================================

CREATE TABLE IF NOT EXISTS rfb_atos.ato_mudanca (
    id       BIGSERIAL PRIMARY KEY,
    run_id   TEXT   NOT NULL,
    ato_id   BIGINT NOT NULL,               -- sem FK de propósito: o log sobrevive ao ato
    tabela   TEXT   NOT NULL,
    campo    TEXT   NOT NULL,
    antes    JSONB,
    depois   JSONB,
    em       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_ato_mudanca_run ON rfb_atos.ato_mudanca (run_id);
CREATE INDEX IF NOT EXISTS ix_ato_mudanca_ato ON rfb_atos.ato_mudanca (ato_id);

COMMENT ON TABLE rfb_atos.ato_mudanca IS
    'Antes/depois de toda alteração que a recoleta faz em linha existente. Reverter um '
    'run = reaplicar os "antes" do run_id. Ver migration 011.';

-- =====================================================================
-- 6. Área de espera
-- =====================================================================
-- LIKE ... INCLUDING ALL copia colunas (inclusive situacao_portal), defaults, CHECKs e
-- índices, mas nunca FKs. Com o default nextval da nuvem, o id do staging sai da mesma
-- sequência do principal: a promoção mantém o id.

CREATE SCHEMA IF NOT EXISTS rfb_atos_staging;

CREATE TABLE IF NOT EXISTS rfb_atos_staging.ato
    (LIKE rfb_atos.ato INCLUDING ALL);
CREATE TABLE IF NOT EXISTS rfb_atos_staging.ato_content
    (LIKE rfb_atos.ato_content INCLUDING ALL);
CREATE TABLE IF NOT EXISTS rfb_atos_staging.ato_texto_visao
    (LIKE rfb_atos.ato_texto_visao INCLUDING ALL);
CREATE TABLE IF NOT EXISTS rfb_atos_staging.ato_relacao
    (LIKE rfb_atos.ato_relacao INCLUDING ALL);
CREATE TABLE IF NOT EXISTS rfb_atos_staging.ato_segmento
    (LIKE rfb_atos.ato_segmento INCLUDING ALL);
CREATE TABLE IF NOT EXISTS rfb_atos_staging.ato_alteracao_historico
    (LIKE rfb_atos.ato_alteracao_historico INCLUDING ALL);

COMMENT ON SCHEMA rfb_atos_staging IS
    'Área de espera da recoleta: atos não vigentes ficam aqui até o td-analise-piscofins '
    'ter vigente_em e o time atualizar. Sem acesso do ratio_leitura. Ver migration 011.';

-- =====================================================================
-- 7. Permissões (só se os papéis existirem — o CI não tem esses papéis)
-- =====================================================================

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ratio_leitura') THEN
        GRANT SELECT ON rfb_atos.ato_texto_visao, rfb_atos.ato_mudanca TO ratio_leitura;
        -- de propósito, nenhum GRANT em rfb_atos_staging para o ratio_leitura
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rfb_writer') THEN
        GRANT SELECT, INSERT, UPDATE ON rfb_atos.ato_texto_visao TO rfb_writer;
        GRANT SELECT, INSERT ON rfb_atos.ato_mudanca TO rfb_writer;
        GRANT USAGE, SELECT ON SEQUENCE rfb_atos.ato_mudanca_id_seq TO rfb_writer;
        GRANT USAGE ON SCHEMA rfb_atos_staging TO rfb_writer;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA rfb_atos_staging
            TO rfb_writer;
    END IF;
END $$;
