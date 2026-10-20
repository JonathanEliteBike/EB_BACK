-- Habilita exclusivamente en la matriz interna la limpieza del catálogo de
-- Proyección de Compras. No modifica permisos delegables ni roles 2/3.
-- La asignación continúa pasando por la misma relación módulo-área-acción.

START TRANSACTION;

SET @modulo := (
    SELECT id FROM modulos
    WHERE identificador = 'usuarios_proyeccion_compras'
    LIMIT 1
);
SET @accion := (
    SELECT id FROM acciones
    WHERE identificador = 'eliminar' AND activo = 1
    LIMIT 1
);

INSERT IGNORE INTO modulo_area_acciones (modulo_id, area_id, accion_id)
SELECT ma.modulo_id, ma.area_id, @accion
FROM modulo_areas ma
WHERE ma.modulo_id = @modulo AND @accion IS NOT NULL;

COMMIT;
