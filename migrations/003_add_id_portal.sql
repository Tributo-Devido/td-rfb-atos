-- ===========================================================================
-- 003_add_id_portal.sql
--
-- Adiciona id_portal (chave externa do normasinternet2.receita.fazenda.gov.br)
-- e backfill a partir do `link` capturado pelo crawler SIJUT2.
--
-- Padrão do link: ".../consulta/externa/{idPortal}/..."
-- ===========================================================================

ALTER TABLE atos ADD COLUMN IF NOT EXISTS id_portal INTEGER;

CREATE INDEX IF NOT EXISTS idx_atos_id_portal ON atos(id_portal) WHERE id_portal IS NOT NULL;

-- Backfill: extrai \d+ depois de "/consulta/externa/" no link
UPDATE atos
SET id_portal = CAST(SUBSTRING(link FROM 'consulta/externa/(\d+)') AS INTEGER)
WHERE id_portal IS NULL
  AND link IS NOT NULL
  AND link ~ 'consulta/externa/\d+';
