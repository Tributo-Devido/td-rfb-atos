-- Migration 011 -- fundacao da recoleta (rfb_atos)
--
-- Plano: docs/PLANO-RECOLETA-2026-09-v2.md, F1.6. Tudo ADITIVO: nenhuma coluna ou
-- tabela que o td-analise-piscofins le muda de forma ou de conteudo.
--
--   1. ato_texto_visao   -- visoes original/multivigente do portal (ato_content segue
--                           com uma linha por ato: a vigente; o consumidor faz LEFT JOIN
--                           sem filtrar versao, entao outra linha la duplicaria resultados)
--   2. relacao externa   -- ato_relacao.destino_id_portal + indice unico parcial: a aresta
--                           para ato fora da base passa a ser guardada e idempotente
--                           (o UNIQUE atual trata NULLs como distintos e nao a protege)
--   3. base_analise      -- por materia: a categorizacao leu o teor ou so a ementa
--   4. situacao_portal   -- o que o portal disse (vigente, datas), bruto, sem inventar status
--   5. ato_mudanca       -- log antes/depois de toda alteracao em linha existente (rollback)
--   6. rfb_atos_staging  -- area de espera dos nao vigentes; SEM acesso do ratio_leitura,
--                           ou seja, invisivel ao time ate a promocao
--
-- Conferida contra o schema real da nuvem em 13/09/2026 (ato.id BIGINT, id_portal INTEGER,
-- dono de tudo = ratio_admin). Idempotente. DDL: rodar como ratio_admin.
--
-- So ASCII neste arquivo, como na 010: o psql no Windows le o arquivo na codificacao do
-- console (WIN1252) e um acento em UTF-8 quebra a migration (0x81) ou vira mojibake.

SET search_path = rfb_atos, public;

-- =====================================================================
-- 1. Visoes de texto do portal
-- =====================================================================

CREATE TABLE IF NOT EXISTS rfb_atos.ato_texto_visao (
    ato_id        BIGINT NOT NULL REFERENCES rfb_atos.ato(id) ON DELETE CASCADE,
    visao         TEXT   NOT NULL,
    sha256        TEXT   NOT NULL,          -- do texto: a mesma visao muda com o tempo
    texto         TEXT   NOT NULL,
    caminho_json  TEXT,                     -- JSON bruto fica em disco/S3, nao no Postgres
    fonte         TEXT,
    run_id        TEXT,
    coletado_em   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ato_id, visao, sha256),
    CONSTRAINT ato_texto_visao_visao_check CHECK (visao IN ('original', 'multivigente'))
);

COMMENT ON TABLE rfb_atos.ato_texto_visao IS
    'Fotos das visoes original e multivigente do portal (SIJUT). A vigente continua em '
    'ato_content (1 linha por ato). Ver migration 011.';

-- =====================================================================
-- 2. Relacao para ato fora da base
-- =====================================================================

ALTER TABLE rfb_atos.ato_relacao ADD COLUMN IF NOT EXISTS destino_id_portal INTEGER;

COMMENT ON COLUMN rfb_atos.ato_relacao.destino_id_portal IS
    'idAto do portal quando o destino ainda nao esta na base (ato_destino_id NULL). '
    'destino_externo segue como rotulo legivel. O resolvedor liga ao ato_destino_id quando '
    'o destino e coletado. Revogacao do portal = tipo_relacao interrompe. Ver migration 011.';

CREATE UNIQUE INDEX IF NOT EXISTS ux_ato_relacao_externa
    ON rfb_atos.ato_relacao (ato_origem_id, destino_id_portal, tipo_relacao)
 WHERE ato_destino_id IS NULL AND destino_id_portal IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_ato_relacao_destino_portal
    ON rfb_atos.ato_relacao (destino_id_portal)
 WHERE destino_id_portal IS NOT NULL;

-- =====================================================================
-- 3. Base da analise, por materia
-- =====================================================================
-- DEFAULT constante em ADD COLUMN nao reescreve a tabela (PostgreSQL 11+).

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
-- 4. Situacao bruta do portal
-- =====================================================================

ALTER TABLE rfb_atos.ato ADD COLUMN IF NOT EXISTS situacao_portal JSONB;

COMMENT ON COLUMN rfb_atos.ato.situacao_portal IS
    'O que o portal disse na ultima coleta (vigente, datas, visao). status_vigencia usa so '
    'o vocabulario do portal; "revogado" e derivado na leitura. Ver migration 011.';

-- =====================================================================
-- 5. Log de mudancas (rollback real)
-- =====================================================================

CREATE TABLE IF NOT EXISTS rfb_atos.ato_mudanca (
    id       BIGSERIAL PRIMARY KEY,
    run_id   TEXT   NOT NULL,
    ato_id   BIGINT NOT NULL,               -- sem FK de proposito: o log sobrevive ao ato
    tabela   TEXT   NOT NULL,
    campo    TEXT   NOT NULL,
    antes    JSONB,
    depois   JSONB,
    em       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_ato_mudanca_run ON rfb_atos.ato_mudanca (run_id);
CREATE INDEX IF NOT EXISTS ix_ato_mudanca_ato ON rfb_atos.ato_mudanca (ato_id);

COMMENT ON TABLE rfb_atos.ato_mudanca IS
    'Antes/depois de toda alteracao que a recoleta faz em linha existente. Reverter um '
    'run = reaplicar os "antes" do run_id. Ver migration 011.';

-- =====================================================================
-- 6. Area de espera
-- =====================================================================
-- LIKE ... INCLUDING ALL copia colunas (inclusive situacao_portal), defaults, CHECKs e
-- indices, mas nunca FKs. Com o default nextval da nuvem, o id do staging sai da mesma
-- sequencia do principal: a promocao mantem o id.

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
    'Area de espera da recoleta: atos nao vigentes ficam aqui ate o td-analise-piscofins '
    'ter vigente_em e o time atualizar. Sem acesso do ratio_leitura. Ver migration 011.';

-- =====================================================================
-- 7. Permissoes (so se os papeis existirem -- o CI nao tem esses papeis)
-- =====================================================================

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ratio_leitura') THEN
        GRANT SELECT ON rfb_atos.ato_texto_visao, rfb_atos.ato_mudanca TO ratio_leitura;
        -- de proposito, nenhum GRANT em rfb_atos_staging para o ratio_leitura
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
