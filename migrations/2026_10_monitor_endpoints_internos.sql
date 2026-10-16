-- Monitor: operaciones existentes que faltaban en el catálogo interno.
-- Repetible; no modifica usuarios ni permisos asignados.
INSERT IGNORE INTO permisos_internos_endpoints
    (ruta_patron, metodo_http, modulo_id, accion_id, activo)
SELECT reglas.ruta_patron, reglas.metodo_http, modulo.id, accion.id, 1
FROM (
    SELECT '/sync-monitor-odoo' AS ruta_patron, 'POST' AS metodo_http, 'editar' AS accion
    UNION ALL SELECT '/recalcular-previo-desde-monitor', 'POST', 'editar'
) AS reglas
INNER JOIN modulos modulo ON modulo.identificador = 'monitor' AND modulo.activo = 1
INNER JOIN acciones accion ON accion.identificador = reglas.accion AND accion.activo = 1
WHERE EXISTS (
    SELECT 1 FROM modulo_acciones ma
    WHERE ma.modulo_id = modulo.id AND ma.accion_id = accion.id
);
