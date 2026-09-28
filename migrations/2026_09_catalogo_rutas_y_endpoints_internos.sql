-- Catálogo técnico único para las pantallas internas de rol 4.
--
-- Precondiciones:
--   2026_09_permisos_internos_endpoints_multiregla.sql
--   2026_09_cobertura_global_permisos_internos.sql
--   2026_09_proyeccion_compras_catalogo_eliminar_interno.sql
--
-- Repetible: no cambia áreas, usuarios, permisos delegables, usuarios hijo
-- ni el módulo histórico usuarios_garantias.

START TRANSACTION;

-- modulos.ruta es el único origen de verdad de guard, Home, encabezado HTTP
-- y evaluación de montos. Sólo se actualizan pantallas Angular reales.
UPDATE modulos
SET ruta = CASE identificador
    WHEN 'flujo_dashboard' THEN '/flujo-dashboard'
    WHEN 'flujo_tablero' THEN '/flujo-tablero'
    WHEN 'flujo_tablero_anual' THEN '/flujo-tablero-anual'
    WHEN 'flujo_auditoria' THEN '/flujo-auditoria'
    WHEN 'ventas_monitor' THEN '/ventas-monitor'
    WHEN 'garantias' THEN '/garantias'
    WHEN 'usuarios_proyeccion_compras' THEN '/usuarios/proyeccion-compras'
    WHEN 'caratulas' THEN '/caratulas'
    WHEN 'caratula_evacs' THEN '/caratula-evacs'
    WHEN 'caratula_evac_a' THEN '/caratula-evac-a'
    WHEN 'caratula_evac_b' THEN '/caratula-evac-b'
    WHEN 'caratula_retroactivos' THEN '/caratula-retroactivos'
    WHEN 'monitor_pedidos' THEN '/monitor-pedidos'
    WHEN 'usuarios_solicitud_retroactivo' THEN '/usuarios/solicitud-retroactivo'
    WHEN 'usuarios_solicitud_retroactivo_dashboard' THEN '/usuarios/solicitud-retroactivo/dashboard'
    WHEN 'usuarios_solicitud_retroactivo_gestor' THEN '/usuarios/solicitud-retroactivo/gestor'
    ELSE ruta
END
WHERE activo = 1
  AND identificador IN (
      'flujo_dashboard', 'flujo_tablero', 'flujo_tablero_anual', 'flujo_auditoria',
      'ventas_monitor', 'garantias', 'usuarios_proyeccion_compras', 'caratulas',
      'caratula_evacs', 'caratula_evac_a', 'caratula_evac_b',
      'caratula_retroactivos', 'monitor_pedidos', 'usuarios_solicitud_retroactivo',
      'usuarios_solicitud_retroactivo_dashboard', 'usuarios_solicitud_retroactivo_gestor'
  );

-- La vista de detalle usa el permiso del módulo padre Importaciones. El
-- registro propio se conserva para instalaciones que ya lo tengan asignado.
UPDATE modulos detalle
INNER JOIN modulos padre ON padre.identificador = 'importaciones' AND padre.activo = 1
SET detalle.padre_id = padre.id
WHERE detalle.identificador = 'importaciones_:id'
  AND detalle.activo = 1
  AND (detalle.padre_id IS NULL OR detalle.padre_id <> padre.id);

-- Las primeras ejecuciones de esta migración registraron rutas de Flujo sin
-- el prefijo del Blueprint. Se eliminan sólo esas reglas incorrectas antes de
-- insertar sus equivalentes reales, manteniendo el script repetible.
DELETE pie
FROM permisos_internos_endpoints pie
INNER JOIN modulos modulo ON modulo.id = pie.modulo_id
WHERE modulo.identificador IN (
    'flujo_dashboard', 'flujo_tablero', 'flujo_tablero_anual', 'flujo_auditoria'
)
  AND pie.ruta_patron IN (
    '/tablero-mensual', '/proyeccion-anual', '/auditoria', '/reporte-excel',
    '/guardar-valor', '/sincronizar-odoo', '/verificar-permiso/<int:id_usuario>'
  );

-- La pantalla Tablero es quien consume esta operación; elimina únicamente la
-- asociación errónea creada por una ejecución previa de esta misma corrección.
DELETE pie
FROM permisos_internos_endpoints pie
INNER JOIN modulos modulo ON modulo.id = pie.modulo_id
INNER JOIN acciones accion ON accion.id = pie.accion_id
WHERE pie.ruta_patron = '/sincronizar-odoo'
  AND pie.metodo_http = 'POST'
  AND modulo.identificador = 'flujo_dashboard'
  AND accion.identificador = 'editar';

-- INSERT IGNORE conserva reglas existentes y permite varias reglas para el
-- mismo endpoint cuando X-Ruta-Interna identifica una pantalla distinta.
INSERT IGNORE INTO permisos_internos_endpoints
    (ruta_patron, metodo_http, modulo_id, accion_id, activo)
SELECT reglas.ruta_patron, reglas.metodo_http, modulo.id, accion.id, 1
FROM (
    -- Flujo de Efectivo.
    SELECT '/flujo/tablero-mensual' ruta_patron, 'GET' metodo_http, 'flujo_dashboard' modulo, 'ver' accion
    UNION ALL SELECT '/flujo/proyeccion-anual', 'GET', 'flujo_tablero_anual', 'ver'
    UNION ALL SELECT '/flujo/auditoria', 'GET', 'flujo_auditoria', 'ver'
    UNION ALL SELECT '/flujo/reporte-excel', 'GET', 'flujo_tablero', 'ver'
    UNION ALL SELECT '/flujo/guardar-valor', 'POST', 'flujo_tablero', 'editar'
    UNION ALL SELECT '/flujo/sincronizar-odoo', 'POST', 'flujo_tablero', 'editar'
    UNION ALL SELECT '/flujo/verificar-permiso/<int:id_usuario>', 'GET', 'flujo_dashboard', 'ver'

    -- Gastos Operativos e Ingresos: sólo rutas implementadas actualmente.
    UNION ALL SELECT '/flujo/conceptos', 'POST', 'gastos_operativos', 'crear'
    UNION ALL SELECT '/flujo/gastos-operativos', 'GET', 'gastos_operativos', 'ver'
    UNION ALL SELECT '/flujo/gastos-operativos', 'POST', 'gastos_operativos', 'crear'
    UNION ALL SELECT '/flujo/ingresos', 'POST', 'ingresos', 'crear'

    -- Dependencias compartidas.
    UNION ALL SELECT '/integrales/grupos', 'GET', 'ventas_monitor', 'ver'
    UNION ALL SELECT '/detalle-compras-odoo', 'GET', 'ventas_monitor', 'ver'
    UNION ALL SELECT '/temporadas', 'GET', 'caratulas', 'ver'
    UNION ALL SELECT '/temporadas', 'GET', 'caratula_retroactivos', 'ver'
    UNION ALL SELECT '/temporadas', 'GET', 'garantias', 'ver'

    -- Carátulas generales.
    UNION ALL SELECT '/caratula_evac_a', 'POST', 'caratulas', 'crear'
    UNION ALL SELECT '/caratula_evac_b', 'POST', 'caratulas', 'crear'
    UNION ALL SELECT '/datos_previo_historico', 'GET', 'caratulas', 'ver'
    UNION ALL SELECT '/datos_evac_a_historico', 'GET', 'caratulas', 'ver'
    UNION ALL SELECT '/datos_evac_b_historico', 'GET', 'caratulas', 'ver'
    UNION ALL SELECT '/generar-pdf', 'POST', 'caratulas', 'crear'
    UNION ALL SELECT '/verificar_grupo_cliente', 'GET', 'caratulas', 'ver'
    UNION ALL SELECT '/ventas_no_registradas', 'GET', 'caratulas', 'ver'
    UNION ALL SELECT '/ultima_actualizacion', 'GET', 'caratulas', 'ver'

    -- Carátula Global reutiliza el resumen de Carátulas, pero su contexto
    -- técnico es independiente.
    UNION ALL SELECT '/datos_previo', 'GET', 'caratula_global', 'ver'
    UNION ALL SELECT '/resumen_caratulas_my27', 'GET', 'caratula_global', 'ver'

    -- Historial de envíos: consulta local de historial_caratulas.
    UNION ALL SELECT '/email/historial-caratulas', 'GET', 'historial_caratulas', 'ver'

    -- Carátulas EVAC comparte datos con sus pantallas individuales. Cada
    -- regla se selecciona por X-Ruta-Interna, nunca por un bypass.
    UNION ALL SELECT '/clientes_a', 'GET', 'caratula_evacs', 'ver'
    UNION ALL SELECT '/clientes_b', 'GET', 'caratula_evacs', 'ver'
    UNION ALL SELECT '/clientes_a', 'GET', 'caratula_evac_a', 'ver'
    UNION ALL SELECT '/clientes_b', 'GET', 'caratula_evac_b', 'ver'
    UNION ALL SELECT '/monitor_odoo', 'GET', 'caratula_evacs', 'ver'
    UNION ALL SELECT '/temporadas', 'GET', 'caratula_evacs', 'ver'
    UNION ALL SELECT '/resumen_caratulas_my27', 'GET', 'caratula_evacs', 'ver'
    UNION ALL SELECT '/resumen_caratulas_my27', 'GET', 'caratula_evac_a', 'ver'
    UNION ALL SELECT '/resumen_caratulas_my27', 'GET', 'caratula_evac_b', 'ver'

    -- Pantallas que comparten la lectura del monitor Odoo.
    UNION ALL SELECT '/monitor_odoo', 'GET', 'monitor', 'ver'
    UNION ALL SELECT '/monitor_odoo', 'GET', 'multimarcas', 'ver'
    UNION ALL SELECT '/ultima_actualizacion', 'GET', 'monitor', 'ver'
    UNION ALL SELECT '/importar_facturas', 'POST', 'monitor', 'editar'

    -- Previo consume estos datos y dispara su recálculo desde su propia ruta.
    UNION ALL SELECT '/obtener_previo', 'GET', 'previo', 'ver'
    UNION ALL SELECT '/obtener_previo_int', 'GET', 'previo', 'ver'
    UNION ALL SELECT '/obtener_previo_fecha', 'GET', 'previo', 'ver'
    UNION ALL SELECT '/actualizar_previo', 'POST', 'previo', 'editar'
    UNION ALL SELECT '/recalcular_previo', 'POST', 'previo', 'editar'
    UNION ALL SELECT '/ultima_actualizacion', 'GET', 'previo', 'ver'

    -- Carátula de Retroactivos.
    UNION ALL SELECT '/retroactivos/claves', 'GET', 'caratula_retroactivos', 'ver'
    UNION ALL SELECT '/retroactivo_cliente/<string:identificador>', 'GET', 'caratula_retroactivos', 'ver'
    UNION ALL SELECT '/retroactivos_temporadas_disponibles', 'GET', 'caratula_retroactivos', 'ver'
    UNION ALL SELECT '/retroactivos_historico', 'GET', 'caratula_retroactivos', 'ver'

    -- Dashboard de Retroactivos: la sincronización recalcula datos en BD.
    UNION ALL SELECT '/sincronizar_notas', 'POST', 'dashboard_retroactivos', 'editar'

    -- Campañas tiene catálogo técnico propio y conserva su contexto aislado.
    UNION ALL SELECT '/api/solicitud-retroactivo-campanias', 'GET', 'solicitud_retroactivo_campanias', 'ver'
    UNION ALL SELECT '/api/solicitud-retroactivo-campanias/msi', 'GET', 'solicitud_retroactivo_campanias', 'ver'
    UNION ALL SELECT '/api/solicitud-retroactivo-campanias/catalogo-productos', 'GET', 'solicitud_retroactivo_campanias', 'ver'
    UNION ALL SELECT '/api/solicitud-retroactivo-campanias/marcas', 'GET', 'solicitud_retroactivo_campanias', 'ver'
    UNION ALL SELECT '/api/solicitud-retroactivo-campanias/<int:id_campania>', 'GET', 'solicitud_retroactivo_campanias', 'ver'
    UNION ALL SELECT '/api/solicitud-retroactivo-campanias/productos/buscar-por-sku', 'POST', 'solicitud_retroactivo_campanias', 'ver'
    UNION ALL SELECT '/api/solicitud-retroactivo-campanias', 'POST', 'solicitud_retroactivo_campanias', 'crear'
    UNION ALL SELECT '/api/solicitud-retroactivo-campanias/msi', 'POST', 'solicitud_retroactivo_campanias', 'crear'
    UNION ALL SELECT '/api/solicitud-retroactivo-campanias/<int:id_campania>', 'PUT', 'solicitud_retroactivo_campanias', 'editar'
    UNION ALL SELECT '/api/solicitud-retroactivo-campanias/<int:id_campania>', 'DELETE', 'solicitud_retroactivo_campanias', 'eliminar'

    -- Tablero mensual se consume desde la pantalla hija Flujo Tablero.
    UNION ALL SELECT '/flujo/tablero-mensual', 'GET', 'flujo_tablero', 'ver'

    -- Solicitud, Gestor y Dashboard son módulos independientes.
    UNION ALL SELECT '/api/solicitud-retroactivo/listar', 'GET', 'usuarios_solicitud_retroactivo_gestor', 'ver'
    UNION ALL SELECT '/api/solicitud-retroactivo/dashboard', 'GET', 'usuarios_solicitud_retroactivo_dashboard', 'ver'
    UNION ALL SELECT '/api/solicitud-retroactivo/validar-documento/<int:id_venta>', 'POST', 'usuarios_solicitud_retroactivo_gestor', 'editar'
    UNION ALL SELECT '/api/solicitud-retroactivo/nota-credito/<int:id_venta>', 'POST', 'usuarios_solicitud_retroactivo_gestor', 'editar'
    UNION ALL SELECT '/api/solicitud-retroactivo/nota-credito/<int:id_venta>/validar', 'POST', 'usuarios_solicitud_retroactivo_gestor', 'editar'
    UNION ALL SELECT '/api/solicitud-retroactivo/precio/<int:id_venta>', 'POST', 'usuarios_solicitud_retroactivo_gestor', 'editar'

    -- Proyección de Compras.
    UNION ALL SELECT '/clientes/info', 'GET', 'usuarios_proyeccion_compras', 'ver'
    UNION ALL SELECT '/forecast/catalogo-excel', 'DELETE', 'usuarios_proyeccion_compras', 'eliminar'

    -- Monitor de Ventas.
    UNION ALL SELECT '/ventas/listar-clientes', 'GET', 'ventas_monitor', 'ver'

    -- Monitor de Pedidos: la pantalla consulta el estado de sincronización.
    UNION ALL SELECT '/usuarios/sync-odoo', 'POST', 'monitor_pedidos', 'editar'

    -- Multimarcas: la pantalla calcula desde el monitor local y persiste el
    -- resumen de la temporada en curso bajo su propio contexto.
    UNION ALL SELECT '/clientes_multimarcas', 'GET', 'multimarcas', 'ver'
    UNION ALL SELECT '/obtener_multimarcas', 'GET', 'multimarcas', 'ver'
    UNION ALL SELECT '/actualizar_multimarcas', 'POST', 'multimarcas', 'editar'
    UNION ALL SELECT '/temporadas', 'GET', 'multimarcas', 'ver'
    UNION ALL SELECT '/temporadas_disponibles', 'GET', 'multimarcas', 'ver'
    UNION ALL SELECT '/datos_multimarcas_historico', 'GET', 'multimarcas', 'ver'

    -- Gestión de distribuidores: consultas y mantenimiento de clientes.
    UNION ALL SELECT '/metas', 'GET', 'distribuidores', 'ver'
    UNION ALL SELECT '/clientes/nombres', 'GET', 'distribuidores', 'ver'
    UNION ALL SELECT '/clientes/buscar', 'POST', 'distribuidores', 'ver'
    UNION ALL SELECT '/clientes/agregar', 'POST', 'distribuidores', 'crear'
    UNION ALL SELECT '/clientes/editar/<int:id_cliente>', 'PUT', 'distribuidores', 'editar'
    UNION ALL SELECT '/clientes/eliminar/<int:id_cliente>', 'DELETE', 'distribuidores', 'eliminar'

    -- Distribuidores Multimarcas mantiene su catálogo independiente.
    UNION ALL SELECT '/clientes_multimarcas_claves', 'GET', 'distribuidores_multimarcas', 'ver'
    UNION ALL SELECT '/clientes_multimarcas_buscar', 'GET', 'distribuidores_multimarcas', 'ver'
    UNION ALL SELECT '/agregar_cliente', 'POST', 'distribuidores_multimarcas', 'crear'
    UNION ALL SELECT '/editar_cliente/<int:id>', 'PUT', 'distribuidores_multimarcas', 'editar'
    UNION ALL SELECT '/eliminar_cliente/<int:id>', 'DELETE', 'distribuidores_multimarcas', 'eliminar'

    -- Integrales y el formulario Nuevo Integral comparten el módulo técnico.
    UNION ALL SELECT '/integrales/grupos', 'GET', 'integrales', 'ver'
    UNION ALL SELECT '/integrales/agregar', 'POST', 'integrales', 'crear'
    UNION ALL SELECT '/integrales/grupos/editar/<int:id_grupo>', 'PUT', 'integrales', 'editar'
    UNION ALL SELECT '/integrales/grupos/eliminar/<int:id_grupo>', 'DELETE', 'integrales', 'eliminar'
    UNION ALL SELECT '/integrales/clientes/grupo/<int:id_grupo>', 'GET', 'integrales', 'ver'
    UNION ALL SELECT '/integrales/clientes/asignar-grupo', 'POST', 'integrales', 'editar'
    UNION ALL SELECT '/clientes/nombres', 'GET', 'integrales', 'ver'
    UNION ALL SELECT '/clientes/buscar', 'POST', 'integrales', 'ver'
) reglas
INNER JOIN modulos modulo
    ON modulo.identificador = reglas.modulo AND modulo.activo = 1
INNER JOIN acciones accion
    ON accion.identificador = reglas.accion AND accion.activo = 1
WHERE EXISTS (
    SELECT 1 FROM modulo_acciones ma
    WHERE ma.modulo_id = modulo.id AND ma.accion_id = accion.id
) OR EXISTS (
    SELECT 1 FROM modulo_area_acciones maa
    WHERE maa.modulo_id = modulo.id AND maa.accion_id = accion.id
);

-- Contabilidad consulta los acumulados de Carátula Global y EVAC A. La
-- capacidad de montos es complementaria a lectura; no habilita endpoints.
INSERT IGNORE INTO modulo_area_acciones (modulo_id, area_id, accion_id)
SELECT modulo.id, area.id, accion.id
FROM modulos modulo
INNER JOIN areas area
    ON LOWER(TRIM(area.nombre)) = 'contabilidad' AND area.activo = 1
INNER JOIN acciones accion ON accion.identificador = 'ver_montos' AND accion.activo = 1
WHERE modulo.identificador = 'caratula_evac_a' AND modulo.activo = 1;

-- Completa únicamente esa capacidad ya configurada para los usuarios activos
-- de Contabilidad, sin modificar otras áreas ni roles.
INSERT IGNORE INTO usuario_permisos_internos (usuario_id, modulo_id, area_id, accion_id)
SELECT usuario.id, modulo.id, usuario.area_id, accion.id
FROM usuarios usuario
INNER JOIN areas area
    ON area.id = usuario.area_id
   AND LOWER(TRIM(area.nombre)) = 'contabilidad'
   AND area.activo = 1
INNER JOIN modulos modulo ON modulo.identificador IN ('caratula_global', 'caratula_evac_a') AND modulo.activo = 1
INNER JOIN acciones accion ON accion.identificador = 'ver_montos' AND accion.activo = 1
WHERE usuario.rol_id = 4 AND usuario.activo = 1
  AND EXISTS (
      SELECT 1 FROM modulo_area_acciones maa
      WHERE maa.modulo_id = modulo.id
        AND maa.area_id = usuario.area_id
        AND maa.accion_id = accion.id
  );

-- Nueva Solicitud pertenece a Garantías interno. Contabilidad conserva Ver y
-- recibe Crear sólo para ese formulario; no involucra usuarios_garantias.
INSERT IGNORE INTO modulo_area_acciones (modulo_id, area_id, accion_id)
SELECT modulo.id, area.id, accion.id
FROM modulos modulo
INNER JOIN areas area
    ON LOWER(TRIM(area.nombre)) = 'contabilidad' AND area.activo = 1
INNER JOIN acciones accion ON accion.identificador = 'crear' AND accion.activo = 1
WHERE modulo.identificador = 'garantias' AND modulo.activo = 1;

INSERT IGNORE INTO usuario_permisos_internos (usuario_id, modulo_id, area_id, accion_id)
SELECT usuario.id, modulo.id, usuario.area_id, accion.id
FROM usuarios usuario
INNER JOIN areas area
    ON area.id = usuario.area_id
   AND LOWER(TRIM(area.nombre)) = 'contabilidad'
   AND area.activo = 1
INNER JOIN modulos modulo ON modulo.identificador = 'garantias' AND modulo.activo = 1
INNER JOIN acciones accion ON accion.identificador = 'crear' AND accion.activo = 1
WHERE usuario.rol_id = 4 AND usuario.activo = 1
  AND EXISTS (
      SELECT 1 FROM modulo_area_acciones maa
      WHERE maa.modulo_id = modulo.id
        AND maa.area_id = usuario.area_id
        AND maa.accion_id = accion.id
  );

COMMIT;
