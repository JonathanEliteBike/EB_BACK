-- Ejecutar manualmente primero en elite_bike_pruebas2 (no aplicar automáticamente).
-- Permite conservar cualquier cantidad de históricos desactivados y limita a
-- un solo SKU activo por (importacion_id, sku_norm).
START TRANSACTION;

ALTER TABLE importacion_productos
    DROP INDEX uq_producto_embarque,
    ADD COLUMN sku_norm_activo VARCHAR(64)
        GENERATED ALWAYS AS (
            CASE WHEN activo = 1 THEN sku_norm ELSE NULL END
        ) STORED,
    ADD UNIQUE KEY uq_producto_embarque_activo (importacion_id, sku_norm_activo);

COMMIT;
