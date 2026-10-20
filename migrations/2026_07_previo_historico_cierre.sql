-- Agrega a previo_historico el estado de cierre individual del cliente,
-- capturado al momento del snapshot -- mismo patron que se agrego a
-- tabla_retroactivos_historico, para que la carta/banner de "temporada
-- cerrada" pueda mostrarse tambien sobre el historico de previo/evac.
-- Idempotente: seguro de re-correr.
SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'previo_historico' AND column_name = 'temporada_cerrada'
);
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE previo_historico ADD COLUMN temporada_cerrada TINYINT(1) DEFAULT 0',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'previo_historico' AND column_name = 'fecha_cierre_temporada'
);
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE previo_historico ADD COLUMN fecha_cierre_temporada DATETIME NULL',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'previo_historico' AND column_name = 'fecha_cierre_apparel'
);
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE previo_historico ADD COLUMN fecha_cierre_apparel DATE NULL',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
