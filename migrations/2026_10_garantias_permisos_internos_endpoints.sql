-- Garantías: catálogo interno por acción para las pantallas y operaciones
-- existentes. Es repetible y no toca usuarios_garantias, usuarios, áreas ni
-- asignaciones de permisos ya existentes.
--
-- Precondición: 2026_09_permisos_internos_endpoints_multiregla.sql, porque
-- /garantias/archivo/subir tiene reglas distintas para crear y editar.

START TRANSACTION;

INSERT IGNORE INTO acciones (nombre, identificador, activo) VALUES
    ('Ver', 'ver', 1),
    ('Crear', 'crear', 1),
    ('Editar', 'editar', 1),
    ('Eliminar', 'eliminar', 1);

INSERT INTO modulos (nombre, identificador, ruta, activo)
SELECT 'Garantías', 'garantias', '/garantias', 1
WHERE NOT EXISTS (
    SELECT 1 FROM modulos WHERE identificador = 'garantias'
);

-- Sólo completa una ruta ausente; nunca reemplaza una ruta ya configurada.
UPDATE modulos
SET ruta = '/garantias'
WHERE identificador = 'garantias'
  AND (ruta IS NULL OR ruta = '');

INSERT IGNORE INTO modulo_acciones (modulo_id, accion_id)
SELECT modulo.id, accion.id
FROM modulos modulo
INNER JOIN acciones accion
    ON accion.identificador IN ('ver', 'crear', 'editar', 'eliminar')
WHERE modulo.identificador = 'garantias'
  AND modulo.activo = 1
  AND accion.activo = 1;

-- /garantias/inicializar-tablas se mantiene deliberadamente fuera del
-- catálogo: continúa limitado exclusivamente por requiere_rol(1).
INSERT IGNORE INTO permisos_internos_endpoints
    (ruta_patron, metodo_http, modulo_id, accion_id, activo)
SELECT reglas.ruta_patron, reglas.metodo_http, modulo.id, accion.id, 1
FROM (
    SELECT '/garantias/dashboard' AS ruta_patron, 'GET' AS metodo_http, 'ver' AS accion
    UNION ALL SELECT '/garantias/exportar', 'GET', 'ver'
    UNION ALL SELECT '/garantias/refrescar', 'POST', 'editar'
    UNION ALL SELECT '/garantias/formulario/enviar', 'POST', 'crear'
    UNION ALL SELECT '/garantias/formulario/lista', 'GET', 'ver'
    UNION ALL SELECT '/garantias/formulario/<int:form_id>/actualizar-dato', 'PUT', 'editar'
    UNION ALL SELECT '/garantias/mis-tickets', 'GET', 'ver'
    UNION ALL SELECT '/garantias/formulario/<int:form_id>', 'GET', 'ver'
    UNION ALL SELECT '/garantias/formulario/<int:form_id>', 'DELETE', 'eliminar'
    UNION ALL SELECT '/garantias/formulario/<int:form_id>/estatus', 'PUT', 'editar'
    UNION ALL SELECT '/garantias/formulario/<int:form_id>/fecha-estatus', 'PUT', 'editar'
    UNION ALL SELECT '/garantias/formulario/<int:form_id>/fecha-creacion', 'PUT', 'editar'
    UNION ALL SELECT '/garantias/formulario/<int:form_id>/pieza-reemplazo', 'PUT', 'editar'
    UNION ALL SELECT '/garantias/formulario/<int:form_id>/pieza', 'PUT', 'editar'
    UNION ALL SELECT '/garantias/formulario/<int:form_id>/fecha-pieza', 'PUT', 'editar'
    UNION ALL SELECT '/garantias/formulario/<int:form_id>/validacion-doc', 'PUT', 'editar'
    UNION ALL SELECT '/garantias/formulario/<int:form_id>/validacion', 'PUT', 'editar'
    UNION ALL SELECT '/garantias/estructura', 'GET', 'ver'
    UNION ALL SELECT '/garantias/estructura', 'POST', 'editar'
    UNION ALL SELECT '/garantias/stats', 'GET', 'ver'
    UNION ALL SELECT '/garantias/ticket/<int:formulario_id>/comentarios', 'GET', 'ver'
    UNION ALL SELECT '/garantias/ticket/<int:formulario_id>/comentarios', 'POST', 'crear'
    UNION ALL SELECT '/garantias/latencias', 'GET', 'ver'
    UNION ALL SELECT '/garantias/piezas', 'GET', 'ver'
    UNION ALL SELECT '/garantias/piezas', 'POST', 'crear'
    UNION ALL SELECT '/garantias/piezas/uso', 'GET', 'ver'
    UNION ALL SELECT '/garantias/piezas', 'DELETE', 'eliminar'
    UNION ALL SELECT '/garantias/importar/plantilla', 'GET', 'crear'
    UNION ALL SELECT '/garantias/importar', 'POST', 'crear'
    UNION ALL SELECT '/garantias/archivo/subir', 'POST', 'crear'
    UNION ALL SELECT '/garantias/archivo/subir', 'POST', 'editar'
    UNION ALL SELECT '/garantias/archivo/<path:nombre>', 'GET', 'ver'
    UNION ALL SELECT '/temporadas', 'GET', 'ver'
) AS reglas
INNER JOIN modulos modulo
    ON modulo.identificador = 'garantias' AND modulo.activo = 1
INNER JOIN acciones accion
    ON accion.identificador = reglas.accion AND accion.activo = 1
WHERE EXISTS (
    SELECT 1
    FROM modulo_acciones modulo_accion
    WHERE modulo_accion.modulo_id = modulo.id
      AND modulo_accion.accion_id = accion.id
);

COMMIT;
