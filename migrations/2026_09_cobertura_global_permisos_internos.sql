-- Cobertura base global de endpoints para rol 4.
-- No depende de areas: resuelve el modulo técnico por su identificador y la
-- acción por su identificador. INSERT IGNORE permite ejecutarla varias veces.
-- Requiere la llave única de 2026_09_permisos_internos_endpoints_multiregla.sql.

INSERT IGNORE INTO permisos_internos_endpoints
    (ruta_patron, metodo_http, modulo_id, accion_id, activo)
SELECT reglas.ruta_patron, reglas.metodo_http, modulo.id, accion.id, 1
FROM (
    SELECT '/metas' ruta_patron, 'GET' metodo_http, 'metas' modulo_identificador, 'ver' accion
    UNION ALL SELECT '/metas/agregar', 'POST', 'metas', 'crear'
    UNION ALL SELECT '/metas/editar/<int:id_meta>', 'PUT', 'metas', 'editar'
    UNION ALL SELECT '/metas/eliminar/<int:id_meta>', 'DELETE', 'metas', 'eliminar'
    UNION ALL SELECT '/caratula_evac', 'GET', 'caratulas', 'ver'
    UNION ALL SELECT '/nombres_caratula', 'GET', 'caratulas', 'ver'
    UNION ALL SELECT '/clientes_a', 'GET', 'caratulas', 'ver'
    UNION ALL SELECT '/clientes_b', 'GET', 'caratulas', 'ver'
    UNION ALL SELECT '/clientes_go', 'GET', 'caratulas', 'ver'
    UNION ALL SELECT '/datos_evac_a', 'GET', 'caratulas', 'ver'
    UNION ALL SELECT '/datos_evac_b', 'GET', 'caratulas', 'ver'
    UNION ALL SELECT '/datos_previo', 'GET', 'caratulas', 'ver'
    UNION ALL SELECT '/resumen_caratulas_my27', 'GET', 'caratulas', 'ver'
    UNION ALL SELECT '/detalle-compras-odoo', 'GET', 'caratulas', 'ver'
    UNION ALL SELECT '/detalle-compras-odoo', 'GET', 'monitor_pedidos', 'ver'
    UNION ALL SELECT '/temporadas_disponibles', 'GET', 'caratulas', 'ver'
    UNION ALL SELECT '/temporadas_disponibles', 'GET', 'monitor_pedidos', 'ver'
    UNION ALL SELECT '/usuarios/para-monitor', 'GET', 'monitor_pedidos', 'ver'
    UNION ALL SELECT '/integrales/grupos', 'GET', 'monitor_pedidos', 'ver'
    UNION ALL SELECT '/retroactivos_temporadas_disponibles', 'GET', 'caratula_retroactivos', 'ver'
    UNION ALL SELECT '/retroactivos_historico', 'GET', 'caratula_retroactivos', 'ver'
    UNION ALL SELECT '/retroactivo_cliente/<string:identificador>', 'GET', 'caratula_retroactivos', 'ver'
    UNION ALL SELECT '/api/solicitud-retroactivo/msi', 'GET', 'usuarios_solicitud_retroactivo', 'ver'
    UNION ALL SELECT '/api/solicitud-retroactivo/marca', 'GET', 'usuarios_solicitud_retroactivo', 'ver'
    UNION ALL SELECT '/api/solicitud-retroactivo/formulario', 'GET', 'usuarios_solicitud_retroactivo', 'ver'
    UNION ALL SELECT '/api/solicitud-retroactivo/campania/<int:id_campania>/msi', 'GET', 'usuarios_solicitud_retroactivo', 'ver'
    UNION ALL SELECT '/api/solicitud-retroactivo/campania/<int:id_campania>/productos', 'GET', 'usuarios_solicitud_retroactivo', 'ver'
    UNION ALL SELECT '/api/solicitud-retroactivo/campania/<int:id_campania>/marcas', 'GET', 'usuarios_solicitud_retroactivo', 'ver'
    UNION ALL SELECT '/api/solicitud-retroactivo/razones-sociales', 'GET', 'usuarios_solicitud_retroactivo', 'ver'
    UNION ALL SELECT '/api/solicitud-retroactivo/tiendas/<int:cliente_id>', 'GET', 'usuarios_solicitud_retroactivo', 'ver'
    UNION ALL SELECT '/api/solicitud-retroactivo/mis-solicitudes', 'GET', 'usuarios_solicitud_retroactivo', 'ver'
    UNION ALL SELECT '/api/solicitud-retroactivo/registrar/venta', 'POST', 'usuarios_solicitud_retroactivo', 'crear'
    UNION ALL SELECT '/api/solicitud-retroactivo/venta/<int:id_venta>', 'PUT', 'usuarios_solicitud_retroactivo', 'editar'
    UNION ALL SELECT '/forecast', 'GET', 'usuarios_proyeccion_compras', 'ver'
    UNION ALL SELECT '/forecast/avance', 'GET', 'usuarios_proyeccion_compras', 'ver'
    UNION ALL SELECT '/forecast/precios-catalogo', 'GET', 'usuarios_proyeccion_compras', 'ver'
    UNION ALL SELECT '/forecast/catalogo-excel', 'GET', 'usuarios_proyeccion_compras', 'ver'
    UNION ALL SELECT '/forecast/catalogo-excel/lista', 'GET', 'usuarios_proyeccion_compras', 'ver'
    UNION ALL SELECT '/forecast/catalogo-excel', 'POST', 'usuarios_proyeccion_compras', 'crear'
    UNION ALL SELECT '/forecast/importar-csv-apparel', 'POST', 'usuarios_proyeccion_compras', 'crear'
    UNION ALL SELECT '/forecast/catalogo-excel', 'DELETE', 'usuarios_proyeccion_compras', 'eliminar'
    UNION ALL SELECT '/ventas/anios-disponibles', 'GET', 'ventas_monitor', 'ver'
    UNION ALL SELECT '/ventas/resumen', 'GET', 'ventas_monitor', 'ver'
    UNION ALL SELECT '/ventas/productos-por-estado', 'GET', 'ventas_monitor', 'ver'
    UNION ALL SELECT '/ventas/comparar-anual', 'GET', 'ventas_monitor', 'ver'
    UNION ALL SELECT '/ventas/resumen-integral', 'GET', 'ventas_monitor', 'ver'
) reglas
INNER JOIN modulos modulo
    ON modulo.identificador = reglas.modulo_identificador AND modulo.activo = 1
INNER JOIN acciones accion
    ON accion.identificador = reglas.accion AND accion.activo = 1
WHERE EXISTS (
    SELECT 1
    FROM modulo_acciones ma
    WHERE ma.modulo_id = modulo.id AND ma.accion_id = accion.id
) OR EXISTS (
    SELECT 1
    FROM modulo_area_acciones maa
    WHERE maa.modulo_id = modulo.id AND maa.accion_id = accion.id
);
