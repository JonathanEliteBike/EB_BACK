-- Dashboard de Retroactivos: catálogo de acceso interno y endpoints.
-- Idempotente. No asigna permisos a usuarios ni modifica datos de negocio.

START TRANSACTION;

INSERT INTO modulos (nombre, identificador, ruta, activo)
SELECT 'Dashboard de Retroactivos', 'dashboard_retroactivos',
       '/dashboard-retroactivos', 1
WHERE NOT EXISTS (
    SELECT 1 FROM modulos WHERE identificador = 'dashboard_retroactivos'
);

UPDATE modulos
SET ruta = '/dashboard-retroactivos', activo = 1
WHERE identificador = 'dashboard_retroactivos';

INSERT IGNORE INTO modulo_acciones (modulo_id, accion_id)
SELECT modulo.id, accion.id
FROM modulos modulo
INNER JOIN acciones accion
    ON accion.identificador IN ('ver', 'editar')
   AND accion.activo = 1
WHERE modulo.identificador = 'dashboard_retroactivos'
  AND modulo.activo = 1;

-- Los dos endpoints de temporadas son compartidos con Carátula. La cabecera
-- X-Ruta-Interna permite elegir esta regla para el Dashboard sin alterar el
-- acceso actual de Carátula.
INSERT IGNORE INTO permisos_internos_endpoints
    (ruta_patron, metodo_http, modulo_id, accion_id, activo)
SELECT reglas.ruta_patron, reglas.metodo_http, modulo.id, accion.id, 1
FROM (
    SELECT '/retroactivos' AS ruta_patron, 'GET' AS metodo_http, 'ver' AS accion
    UNION ALL SELECT '/retroactivos_temporadas_disponibles', 'GET', 'ver'
    UNION ALL SELECT '/retroactivos_historico', 'GET', 'ver'
    UNION ALL SELECT '/sincronizar_notas', 'POST', 'editar'
) AS reglas
INNER JOIN modulos modulo
    ON modulo.identificador = 'dashboard_retroactivos' AND modulo.activo = 1
INNER JOIN acciones accion
    ON accion.identificador = reglas.accion AND accion.activo = 1;

COMMIT;
