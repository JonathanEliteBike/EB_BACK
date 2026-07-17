-- migrations/2026_07_temporadas.sql
CREATE TABLE IF NOT EXISTS temporadas (
    id INT NOT NULL AUTO_INCREMENT,
    etiqueta VARCHAR(20) NOT NULL,
    fecha_inicio DATE NOT NULL,
    fecha_fin DATE NOT NULL,
    estado ENUM('abierta','cerrada') NOT NULL DEFAULT 'abierta',
    fecha_cierre DATETIME NULL,
    cerrada_por VARCHAR(100) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_etiqueta (etiqueta)
) ENGINE=InnoDB DEFAULT CHARSET=latin1;

-- IMPORTANTE: '2025-2026' se inserta como 'abierta', no 'cerrada'. Solo
-- representa el rango de fechas de esa temporada; el cierre GLOBAL real se
-- marca aparte, cuando cerrar_temporada_completa() se ejecute de verdad
-- (via cerrar_temporada_masiva_pendientes, no como parte de esta migracion).
-- No confundir con clientes.temporada_cerrada, que es el cierre INDIVIDUAL
-- por cliente y ya existe independientemente de esta tabla.
INSERT INTO temporadas (etiqueta, fecha_inicio, fecha_fin, estado)
VALUES ('2025-2026', '2025-07-01', '2026-06-30', 'abierta')
ON DUPLICATE KEY UPDATE etiqueta = etiqueta;

INSERT INTO temporadas (etiqueta, fecha_inicio, fecha_fin, estado)
VALUES ('2026-2027', '2026-07-01', '2027-06-30', 'abierta')
ON DUPLICATE KEY UPDATE etiqueta = etiqueta;

-- Add dia_inicio_temporada column to clientes table for early-starter distributors
-- Note: MySQL's ALTER TABLE grammar does not support "ADD COLUMN IF NOT EXISTS"
-- (that is a MariaDB-only extension, not a MySQL version limitation), so we guard
-- the ALTER with information_schema + dynamic SQL to keep this file safe to re-run.
SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'clientes' AND column_name = 'dia_inicio_temporada'
);
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE clientes ADD COLUMN dia_inicio_temporada VARCHAR(5) NULL COMMENT ''MM-DD; NULL = usa el default 07-01''',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
