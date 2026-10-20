-- Solicitud de Retroactivos: la nota de crédito ahora tiene su propio
-- estatus, separado del estatus general del ticket (que sigue dependiendo
-- solo de validacion_docs_json).
--
-- Flujo real del negocio: Banca y Pagos (BCYP) captura el número de nota
-- de crédito, pero Auditoría es quien la valida (con un código numérico,
-- ver utils/auditoria_utils.py). Sin esta columna no había forma de saber,
-- a simple vista, si una NC ya capturada estaba realmente validada.
--
-- NULL      = todavía no se ha capturado ninguna nota de crédito.
-- pendiente = capturada, esperando validación de Auditoría.
-- validada  = Auditoría la validó con el código correcto.
SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'solicitud_retroactivo_venta' AND column_name = 'nota_credito_estatus'
);
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE solicitud_retroactivo_venta ADD COLUMN nota_credito_estatus VARCHAR(20) DEFAULT NULL AFTER nota_credito',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Backfill: las notas de crédito que ya existan se marcan 'pendiente' (no
-- se puede saber retroactivamente si Auditoría ya las había revisado por
-- fuera del sistema, así que se piden confirmar una vez más).
UPDATE solicitud_retroactivo_venta
SET nota_credito_estatus = 'pendiente'
WHERE nota_credito IS NOT NULL AND nota_credito <> '' AND nota_credito_estatus IS NULL;
