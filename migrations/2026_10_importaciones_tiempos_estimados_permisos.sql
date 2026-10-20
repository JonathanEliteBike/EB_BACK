-- Tiempos Estimados de Importaciones: catálogo y reglas para usuarios internos.
-- Idempotente. No concede permisos ni cambia asignaciones existentes.

START TRANSACTION;

-- La ruta Angular requiere un módulo catalogado; hereda las acciones del
-- módulo padre Importaciones y no crea un permiso adicional que asignar.
INSERT INTO modulos (padre_id, nombre, identificador, ruta, activo)
SELECT padre.id, 'Tiempos Estimados', 'importaciones_tiempos_estimados',
       '/importaciones/tiempos-estimados', 1
FROM modulos padre
WHERE padre.identificador = 'importaciones'
  AND padre.activo = 1
  AND NOT EXISTS (
      SELECT 1 FROM modulos hijo
      WHERE hijo.identificador = 'importaciones_tiempos_estimados'
  );

UPDATE modulos hijo
INNER JOIN modulos padre
    ON padre.identificador = 'importaciones' AND padre.activo = 1
SET hijo.padre_id = padre.id,
    hijo.ruta = '/importaciones/tiempos-estimados',
    hijo.activo = 1
WHERE hijo.identificador = 'importaciones_tiempos_estimados';

-- Asegura que el módulo padre soporte las cuatro operaciones ya expuestas por
-- la API. No agrega permisos a usuario alguno.
INSERT IGNORE INTO modulo_acciones (modulo_id, accion_id)
SELECT modulo.id, accion.id
FROM modulos modulo
INNER JOIN acciones accion
    ON accion.identificador IN ('ver', 'crear', 'editar', 'eliminar')
   AND accion.activo = 1
WHERE modulo.identificador = 'importaciones'
  AND modulo.activo = 1;

INSERT IGNORE INTO permisos_internos_endpoints
    (ruta_patron, metodo_http, modulo_id, accion_id, activo)
SELECT reglas.ruta_patron, reglas.metodo_http, modulo.id, accion.id, 1
FROM (
    SELECT '/importaciones/tiempos-estimados' AS ruta_patron, 'GET' AS metodo_http, 'ver' AS accion
    UNION ALL SELECT '/importaciones/tiempos-estimados', 'POST', 'crear'
    UNION ALL SELECT '/importaciones/tiempos-estimados/<int:id_regla>', 'PUT', 'editar'
    UNION ALL SELECT '/importaciones/tiempos-estimados/<int:id_regla>', 'DELETE', 'eliminar'
) AS reglas
INNER JOIN modulos modulo
    ON modulo.identificador = 'importaciones' AND modulo.activo = 1
INNER JOIN acciones accion
    ON accion.identificador = reglas.accion AND accion.activo = 1;

COMMIT;
