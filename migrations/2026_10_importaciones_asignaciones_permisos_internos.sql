-- Cobertura de Importaciones y su submódulo Asignaciones para rol interno 4.
-- Repetible: sólo registra reglas de autorización; no modifica usuarios,
-- asignaciones existentes ni el comportamiento de los roles 1, 2, 3 o 99.
-- Requiere que el módulo padre `importaciones` tenga las acciones indicadas.

START TRANSACTION;

INSERT IGNORE INTO permisos_internos_endpoints
    (ruta_patron, metodo_http, modulo_id, accion_id, activo)
SELECT reglas.ruta_patron, reglas.metodo_http, modulo.id, accion.id, 1
FROM (
    -- Pantalla principal, detalle y tablero de Importaciones.
    SELECT '/importaciones' AS ruta_patron, 'GET' AS metodo_http, 'ver' AS accion
    UNION ALL SELECT '/importaciones/resumen', 'GET', 'ver'
    UNION ALL SELECT '/importaciones/dashboard', 'GET', 'ver'
    UNION ALL SELECT '/importaciones/<int:id_imp>', 'GET', 'ver'
    UNION ALL SELECT '/importaciones', 'POST', 'crear'
    UNION ALL SELECT '/importaciones/<int:id_imp>', 'PUT', 'editar'
    UNION ALL SELECT '/importaciones/<int:id_imp>', 'DELETE', 'eliminar'

    -- Consultas de Asignaciones.
    UNION ALL SELECT '/importaciones/asignaciones/periodos', 'GET', 'ver'
    UNION ALL SELECT '/importaciones/asignaciones/embarques', 'GET', 'ver'
    UNION ALL SELECT '/importaciones/asignaciones/productos', 'GET', 'ver'
    UNION ALL SELECT '/importaciones/<int:importacion_id>/asignaciones', 'GET', 'ver'
    UNION ALL SELECT '/importaciones/<int:importacion_id>/asignaciones/productos', 'GET', 'ver'
    UNION ALL SELECT '/importaciones/<int:importacion_id>/asignaciones/productos/<int:producto_id>/detalle', 'GET', 'ver'
    UNION ALL SELECT '/importaciones/<int:importacion_id>/asignaciones/movimientos', 'GET', 'ver'
    UNION ALL SELECT '/importaciones/<int:importacion_id>/asignaciones/reservas', 'GET', 'ver'
    UNION ALL SELECT '/importaciones/<int:importacion_id>/asignaciones/reasignar', 'POST', 'ver'

    -- Altas de productos, inventario y ventas sobrantes.
    UNION ALL SELECT '/importaciones/asignaciones/periodos/siguiente', 'POST', 'crear'
    UNION ALL SELECT '/importaciones/<int:importacion_id>/asignaciones/productos', 'POST', 'crear'
    UNION ALL SELECT '/importaciones/<int:importacion_id>/asignaciones/productos/importar', 'POST', 'crear'
    UNION ALL SELECT '/importaciones/<int:importacion_id>/asignaciones/productos/<int:producto_id>/venta-sobrante', 'POST', 'crear'

    -- Cambios de cantidades, reservas, reasignaciones y validación Odoo.
    UNION ALL SELECT '/importaciones/<int:importacion_id>/asignaciones/productos/<int:producto_id>', 'PUT', 'editar'
    UNION ALL SELECT '/importaciones/<int:importacion_id>/asignaciones/recalcular', 'POST', 'editar'
    UNION ALL SELECT '/importaciones/<int:importacion_id>/asignaciones/productos/<int:producto_id>/asignar', 'POST', 'editar'
    UNION ALL SELECT '/importaciones/<int:importacion_id>/asignaciones/productos/<int:producto_id>/reservar', 'POST', 'editar'
    UNION ALL SELECT '/importaciones/<int:importacion_id>/asignaciones/productos/<int:producto_id>/reasignar', 'POST', 'editar'
    UNION ALL SELECT '/importaciones/<int:importacion_id>/asignaciones/reservas/<int:reserva_id>/resolver', 'POST', 'editar'
    UNION ALL SELECT '/importaciones/<int:importacion_id>/asignaciones/ventas/<int:venta_id>/validar-odoo', 'POST', 'editar'

    -- Cancelar revierte la entidad de negocio, por lo que requiere eliminar.
    UNION ALL SELECT '/importaciones/<int:importacion_id>/asignaciones/productos/<int:producto_id>/asignaciones/<int:asignacion_id>/cancelar', 'POST', 'eliminar'
    UNION ALL SELECT '/importaciones/<int:importacion_id>/asignaciones/ventas/<int:venta_id>/cancelar', 'POST', 'eliminar'
) AS reglas
INNER JOIN modulos modulo
    ON modulo.identificador = 'importaciones' AND modulo.activo = 1
INNER JOIN acciones accion
    ON accion.identificador = reglas.accion AND accion.activo = 1
WHERE EXISTS (
    SELECT 1 FROM modulo_acciones ma
    WHERE ma.modulo_id = modulo.id AND ma.accion_id = accion.id
) OR EXISTS (
    SELECT 1 FROM modulo_area_acciones maa
    WHERE maa.modulo_id = modulo.id AND maa.accion_id = accion.id
);

COMMIT;
