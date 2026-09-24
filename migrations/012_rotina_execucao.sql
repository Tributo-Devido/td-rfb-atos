-- Migration 012 -- historico da rotina noturna (rfb_atos)
--
-- Uma linha por rodada de scripts/rotina_noturna.py: o que foi coletado, categorizado, mandado
-- para revisao, os erros e o gasto. Serve para o dono e o time saberem se a base esta em dia
-- ("base atualizada ate") sem abrir os arquivos de log da maquina que roda a rotina.
--
-- Aditiva: nenhuma tabela existente muda. Idempotente. DDL: rodar como ratio_admin.
-- So ASCII neste arquivo (o psql no Windows le na codificacao do console).

SET search_path = rfb_atos, public;

CREATE TABLE IF NOT EXISTS rfb_atos.rotina_execucao (
    run_id               TEXT PRIMARY KEY,
    inicio               TIMESTAMPTZ NOT NULL,
    fim                  TIMESTAMPTZ,
    estado               TEXT NOT NULL,          -- ok | com_erro | falhou
    atos_coletados       INTEGER NOT NULL DEFAULT 0,
    coletados_por_tipo   JSONB,
    erros_coleta         INTEGER NOT NULL DEFAULT 0,
    atos_categorizados   INTEGER NOT NULL DEFAULT 0,
    materias_gravadas    INTEGER NOT NULL DEFAULT 0,
    para_revisao         INTEGER NOT NULL DEFAULT 0,
    erros_categorizacao  INTEGER NOT NULL DEFAULT 0,
    lotes_enviados       INTEGER NOT NULL DEFAULT 0,
    atos_enviados        INTEGER NOT NULL DEFAULT 0,
    custo_usd            NUMERIC(10, 2),
    base_atualizada_ate  DATE,                   -- maior data de publicacao na base no fim
    maquina              TEXT,
    resumo               JSONB,                  -- o resumo completo da rodada
    CONSTRAINT rotina_execucao_estado_check CHECK (estado IN ('ok', 'com_erro', 'falhou'))
);

CREATE INDEX IF NOT EXISTS ix_rotina_execucao_inicio ON rfb_atos.rotina_execucao (inicio DESC);

COMMENT ON TABLE rfb_atos.rotina_execucao IS
    'Uma linha por rodada da rotina noturna (scripts/rotina_noturna.py): coleta, categorizacao, '
    'revisao, erros, gasto e ate quando a base esta atualizada. Ver migration 012.';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ratio_leitura') THEN
        GRANT SELECT ON rfb_atos.rotina_execucao TO ratio_leitura;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rfb_writer') THEN
        GRANT SELECT, INSERT, UPDATE ON rfb_atos.rotina_execucao TO rfb_writer;
    END IF;
END $$;
