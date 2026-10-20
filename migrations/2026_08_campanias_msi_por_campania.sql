-- Integra el módulo nuevo de Campañas de Retroactivos con el formulario de
-- venta real (antes eran dos sistemas separados: el formulario leía de
-- solicitud_retroactivo_formulario, campañas vivía en
-- solicitud_retroactivo_campanias y nunca se conectaban).
--
-- Cambios:
-- 1) Nueva tabla solicitud_retroactivo_campania_msi: cada campaña puede ligar
--    varios plazos MSI, cada uno con su propio % retroactivo (antes una
--    campaña solo tenía UN msi_id con el % fijo del catálogo global).
-- 2) solicitud_retroactivo_campanias pierde la columna msi_id (se reemplaza
--    por la tabla anterior).
-- 3) Se migran las 2 campañas que ya usaba el formulario de venta
--    (solicitud_retroactivo_formulario: SCOTT id=1, SPRING SALE id=2) hacia
--    solicitud_retroactivo_campanias CONSERVANDO SUS IDs -- así las ventas
--    ya registradas (solicitud_retroactivo_venta.id_formulario) no quedan
--    huérfanas ni cambian de significado. Se les liga a los 3 plazos MSI
--    existentes con el mismo % que tenían en el catálogo global, para que el
--    monto de ventas nuevas sobre esas campañas no cambie.
-- 4) Se borra la campaña de prueba "SCOTT PRUEBA" (id=1, creada al probar la
--    UI) para dejar libre el id=1 y poder reinsertar SCOTT ahí.

-- ── 1) Tabla campaña-msi ──────────────────────────────────────────────────
SET @tbl_exists = (
    SELECT COUNT(*) FROM information_schema.tables
    WHERE table_schema = DATABASE() AND table_name = 'solicitud_retroactivo_campania_msi'
);
SET @sql = IF(@tbl_exists = 0,
    'CREATE TABLE solicitud_retroactivo_campania_msi (
        id INT NOT NULL AUTO_INCREMENT,
        campania_id INT NOT NULL,
        msi_id INT NOT NULL,
        porcentaje DECIMAL(10,2) NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_campania_msi (campania_id, msi_id),
        INDEX idx_campania_msi_campania (campania_id),
        INDEX idx_campania_msi_msi (msi_id),
        CONSTRAINT fk_campania_msi_campania FOREIGN KEY (campania_id)
            REFERENCES solicitud_retroactivo_campanias(id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        CONSTRAINT fk_campania_msi_msi FOREIGN KEY (msi_id)
            REFERENCES solicitud_retroactivo_msi(id)
            ON UPDATE CASCADE
    )',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ── 2) Quitar msi_id de campanias (ahora vive en la tabla anterior) ────────
SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'solicitud_retroactivo_campanias' AND column_name = 'msi_id'
);
SET @sql = IF(@col_exists > 0,
    'ALTER TABLE solicitud_retroactivo_campanias DROP INDEX idx_campanias_msi_id, DROP COLUMN msi_id',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ── 3) Borrar campaña de prueba y migrar SCOTT / SPRING SALE ───────────────
DELETE FROM solicitud_retroactivo_campanias WHERE nombre = 'SCOTT PRUEBA';

INSERT INTO solicitud_retroactivo_campanias (id, nombre, fecha_inicio, fecha_fin, activa)
SELECT id, nombre, '2025-01-01', '2030-12-31', 1
FROM solicitud_retroactivo_formulario
WHERE id IN (1, 2)
  AND NOT EXISTS (
      SELECT 1 FROM solicitud_retroactivo_campanias c WHERE c.id = solicitud_retroactivo_formulario.id
  );

INSERT INTO solicitud_retroactivo_campania_msi (campania_id, msi_id, porcentaje)
SELECT c.id, m.id, m.porcentaje
FROM solicitud_retroactivo_campanias c
JOIN solicitud_retroactivo_msi m
WHERE c.id IN (1, 2)
  AND NOT EXISTS (
      SELECT 1 FROM solicitud_retroactivo_campania_msi cm
      WHERE cm.campania_id = c.id AND cm.msi_id = m.id
  );
