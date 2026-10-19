-- Proyecciones MY27: habilita la acción delegable ver_montos en el catálogo.
-- Idempotente. No asigna permisos a usuarios existentes.

START TRANSACTION;

INSERT INTO acciones (nombre, identificador, activo)
SELECT 'Ver montos', 'ver_montos', 1
WHERE NOT EXISTS (
    SELECT 1 FROM acciones WHERE identificador = 'ver_montos'
);

INSERT IGNORE INTO modulo_acciones (modulo_id, accion_id)
SELECT modulo.id, accion.id
FROM modulos modulo
INNER JOIN acciones accion
    ON accion.identificador = 'ver_montos' AND accion.activo = 1
WHERE modulo.identificador = 'proyecciones_my27'
  AND modulo.activo = 1;

COMMIT;
