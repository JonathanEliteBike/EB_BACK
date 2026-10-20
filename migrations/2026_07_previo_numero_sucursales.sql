-- Cantidad de sucursales de un distribuidor individual (no Integral), para
-- aplicar el mismo umbral de porcentaje retroactivo por cantidad de tiendas
-- que ya existía solo para grupos Integral (ver ejecutar_sincronizacion_y_calculos
-- en routes/retroactivos.py). Default 1 = comportamiento igual que antes.
SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'previo' AND column_name = 'numero_sucursales'
);
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE previo ADD COLUMN numero_sucursales INT NOT NULL DEFAULT 1 COMMENT ''Para el umbral de porcentaje retroactivo por cantidad de tiendas''',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
