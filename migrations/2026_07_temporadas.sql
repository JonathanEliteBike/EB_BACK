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

INSERT INTO temporadas (etiqueta, fecha_inicio, fecha_fin, estado, fecha_cierre)
VALUES ('2025-2026', '2025-07-01', '2026-06-30', 'cerrada', NOW())
ON DUPLICATE KEY UPDATE etiqueta = etiqueta;

INSERT INTO temporadas (etiqueta, fecha_inicio, fecha_fin, estado)
VALUES ('2026-2027', '2026-07-01', '2027-06-30', 'abierta')
ON DUPLICATE KEY UPDATE etiqueta = etiqueta;

-- Add dia_inicio_temporada column to clientes table for early-starter distributors
-- Note: MySQL 8.0.43 does not support IF NOT EXISTS in ALTER TABLE, so using direct ALTER
-- Idempotency: This migration should be run only once; re-running will fail if column exists
ALTER TABLE clientes
    ADD COLUMN dia_inicio_temporada VARCHAR(5) NULL COMMENT 'MM-DD; NULL = usa el default 07-01';
