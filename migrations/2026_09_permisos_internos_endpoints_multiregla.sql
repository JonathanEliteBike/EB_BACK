-- Permite varias reglas de permisos internos para el mismo endpoint HTTP.
-- No ejecutar automáticamente: esta migración sólo prepara la estructura.
-- Es idempotente y no modifica ni elimina reglas existentes.

SET @tabla_existe := (
    SELECT COUNT(*)
    FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'permisos_internos_endpoints'
);

-- Elimina únicamente índices UNIQUE compuestos exactamente por
-- (ruta_patron, metodo_http). No toca PRIMARY KEY ni índices de FKs.
SET @indices_legacy := (
    SELECT GROUP_CONCAT(CONCAT('DROP INDEX `', indice, '`') SEPARATOR ', ')
    FROM (
        SELECT INDEX_NAME AS indice
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'permisos_internos_endpoints'
          AND NON_UNIQUE = 0
          AND INDEX_NAME <> 'PRIMARY'
        GROUP BY INDEX_NAME
        HAVING COUNT(*) = 2
           AND GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) = 'ruta_patron,metodo_http'
    ) AS indices_encontrados
);

SET @sql_eliminar_indices_legacy := IF(
    @tabla_existe = 0 OR @indices_legacy IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `permisos_internos_endpoints` ', @indices_legacy)
);
PREPARE stmt_eliminar_indices_legacy FROM @sql_eliminar_indices_legacy;
EXECUTE stmt_eliminar_indices_legacy;
DEALLOCATE PREPARE stmt_eliminar_indices_legacy;

-- Evita duplicar una regla exacta, pero permite el mismo endpoint+método
-- para distintos módulos y/o acciones.
SET @indice_nuevo_existe := (
    SELECT COUNT(*)
    FROM (
        SELECT INDEX_NAME
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'permisos_internos_endpoints'
          AND NON_UNIQUE = 0
        GROUP BY INDEX_NAME
        HAVING COUNT(*) = 4
           AND GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) =
               'ruta_patron,metodo_http,modulo_id,accion_id'
    ) AS indices_nuevos
);

SET @sql_crear_indice_nuevo := IF(
    @tabla_existe = 0 OR @indice_nuevo_existe > 0,
    'SELECT 1',
    'ALTER TABLE `permisos_internos_endpoints` ADD UNIQUE KEY `uq_pie_ruta_metodo_modulo_accion` (`ruta_patron`, `metodo_http`, `modulo_id`, `accion_id`)'
);
PREPARE stmt_crear_indice_nuevo FROM @sql_crear_indice_nuevo;
EXECUTE stmt_crear_indice_nuevo;
DEALLOCATE PREPARE stmt_crear_indice_nuevo;
