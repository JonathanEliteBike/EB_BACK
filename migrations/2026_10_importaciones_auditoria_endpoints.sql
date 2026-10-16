-- Registra únicamente Hitos y Auditoría de Importaciones para la capa interna.
-- Repetible; no modifica usuarios ni asignaciones de permisos.
-- Requiere la llave multiregla de 2026_09_permisos_internos_endpoints_multiregla.sql.
-- Las pantallas heredan el permiso importaciones/ver del módulo padre, igual
-- que el detalle /importaciones/:id. No requieren asignaciones propias.
UPDATE modulos hijo
INNER JOIN modulos padre
    ON padre.identificador = 'importaciones' AND padre.activo = 1
SET hijo.padre_id = padre.id
WHERE hijo.identificador IN ('importaciones_hitos_auditoria', 'importaciones_auditoria')
  AND hijo.activo = 1
  AND (hijo.padre_id IS NULL OR hijo.padre_id <> padre.id);

INSERT IGNORE INTO permisos_internos_endpoints
    (ruta_patron, metodo_http, modulo_id, accion_id, activo)
SELECT reglas.ruta_patron, reglas.metodo_http, modulo.id, accion.id, 1
FROM (
    SELECT '/importaciones/hitos-auditoria' AS ruta_patron, 'GET' AS metodo_http, 'ver' AS accion
    UNION ALL SELECT '/importaciones/hitos-auditoria', 'POST', 'crear'
    UNION ALL SELECT '/importaciones/hitos-auditoria/<int:id_hito>', 'PUT', 'editar'
    UNION ALL SELECT '/importaciones/hitos-auditoria/<int:id_hito>', 'DELETE', 'eliminar'
    UNION ALL SELECT '/importaciones/<int:id_imp>/auditoria', 'GET', 'ver'
    UNION ALL SELECT '/importaciones/auditoria-resumen', 'GET', 'ver'
) AS reglas
INNER JOIN modulos modulo
    ON modulo.identificador = 'importaciones' AND modulo.activo = 1
INNER JOIN acciones accion
    ON accion.identificador = reglas.accion AND accion.activo = 1
WHERE EXISTS (
    SELECT 1 FROM modulo_acciones ma
    WHERE ma.modulo_id = modulo.id AND ma.accion_id = accion.id
);
