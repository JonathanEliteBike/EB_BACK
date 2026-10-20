-- Solicitud de Retroactivos: columnas que el código actual
-- (routes/solicitud_retroactivo.py, services/solicitud_retroactivo_service.py)
-- da por hechas y que en algunos entornos locales no estaban presentes.
--
-- 1) validacion_docs_json: estatus de cada uno de los 4 documentos
--    ('valido' | 'rechazado' | ausente = pendiente) -- ver _calcular_estatus.
-- 2) historial_json: bitácora de auditoría (creación, validación por
--    documento, corrección de precio, corrección de nota de crédito,
--    reenvío del cliente) -- ver _entrada_historial.
-- Ambas como columna JSON en vez de tabla aparte -- no hay volumen que
-- justifique una tabla nueva.
SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'solicitud_retroactivo_venta' AND column_name = 'validacion_docs_json'
);
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE solicitud_retroactivo_venta ADD COLUMN validacion_docs_json JSON DEFAULT NULL AFTER validado',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'solicitud_retroactivo_venta' AND column_name = 'historial_json'
);
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE solicitud_retroactivo_venta ADD COLUMN historial_json JSON DEFAULT NULL AFTER validacion_docs_json',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 3) factura_pdf_key / factura_xml_key ahora son opcionales en el flujo de
--    captura (registrar_venta/editar_venta ya mandan NULL cuando el cliente
--    no las adjunta) -- el esquema debe permitirlo.
ALTER TABLE solicitud_retroactivo_venta MODIFY COLUMN factura_pdf_key VARCHAR(255) NULL;
ALTER TABLE solicitud_retroactivo_venta MODIFY COLUMN factura_xml_key VARCHAR(255) NULL;
