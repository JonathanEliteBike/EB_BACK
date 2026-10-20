-- Catalogo base de permisos: revision de elite_bike_db y del codigo actual.
-- Archivo preparado solamente; NO ejecutado por su autor.
--
-- PRECONDICIONES:
-- 1. Tablas InnoDB modulos, acciones y modulo_acciones creadas por
--    2026_08_usuarios_permisos.py, con sus claves unicas y foraneas.
-- 2. Seleccionar explicitamente la BD destino en el cliente al aplicar.
--    Ejecutar en una sesion dedicada, sin --force: detenerse ante cualquier
--    error y hacer ROLLBACK; no continuar hasta COMMIT tras un error.
-- No requiere delegable_a_hijos: usa exclusivamente columnas de agosto.
-- La creacion de esa columna y la configuracion de delegacion pertenecen
-- exclusivamente a 2026_09_acceso_propio_rol2_y_delegacion.sql.
--
-- ORDEN DE APLICACION (no ejecutar desde este archivo):
-- 2026_08_usuarios_permisos.py
-- -> este catalogo
-- -> migraciones especificas de acciones
-- -> 2026_09_permisos_modulos_capacidades.sql
-- -> 2026_09_acceso_propio_rol2_y_delegacion.sql
-- -> 2026_10_control_montos_rol3.sql
-- -> 2026_11_jerarquia_retroactivos_modulos.sql
--
-- Alcance: solo INSERT en las tres tablas del catalogo. No concede permisos,
-- no modifica roles, usuarios, capacidades, ni modulos_roles_acceso.
-- Los accesos efectivos requieren su configuracion independiente.
-- Todos los IDs se resuelven localmente mediante identificador; no se copian
-- IDs, secuencias AUTO_INCREMENT ni asignaciones de desarrollo.
-- Repetible: inserta solo registros ausentes y conserva los existentes.
-- No pretende corregir jerarquias o estados preexistentes en otra BD.
--
-- EVIDENCIA Y DECISIONES (consulta de desarrollo del 2026-09-18):
-- Desarrollo: 8 modulos, 5 acciones, 22 relaciones modulo/accion.
-- Excluidos creacion_usuarios_dis (padre circular, apunta a si mismo) y
-- usuarios_hijos: services/permisos_modulos_service.py los excluye.
-- No se identificaron otros registros de prueba en estas tres tablas.
-- usuarios_creacion_usuarios falta en desarrollo, pero se usa en
-- EB_FRONT/src/app/views/usuarios/creacion-usuarios/creacion-usuarios.component.ts
-- (tieneModulo y permisoNombre usuarios_creacion_usuarios/ver).
-- Su delegacion se configurara despues, en la migracion de acceso propio
-- de rol 2; este catalogo no establece valores de delegacion.
-- usuarios_caratula_retroactivos falta en desarrollo y esta oculto en la
-- gestion actual. Se incluye SOLO por compatibilidad solicitada y porque
-- gestion-clientes.component.ts y 2026_11_jerarquia_retroactivos_modulos.sql
-- aun lo reconocen. No es el permiso que abre la pantalla de retroactivos:
-- esa pantalla exige usuarios_retroactivos en app.routes.ts.
-- Los otros seis modulos existen en desarrollo y tienen referencias activas
-- en frontend y decoradores de routes/{caratulas,proyecciones,forecast,
-- garantias,retroactivos,solicitud_retroactivo}.py.
-- La jerarquia de calculadora y solicitudes coincide con desarrollo y los
-- guards. La de caratula retroactivos procede de la migracion de jerarquia.
--
-- Relaciones minimas de compatibilidad por accion:
-- * Creacion de usuarios: ver, referencia explicita del frontend.
-- * Proyeccion: ver/crear/ver_montos, services/permisos_proyeccion_compras.py.
--   No editar (2026_09_proyeccion_compras_permisos.sql) ni eliminar:
--   editar/eliminar el catalogo en routes/proyecciones.py exige rol 1.
-- * Garantias: ver/crear/editar, coincide con el catalogo de desarrollo salvo
--   eliminar, descartado por 2026_09_garantias_permisos_por_accion.sql.
-- * Caratula: ver/ver_montos, lectura y compatibilidad con
--   2026_09_caratula_ver_montos.sql. No reproducir crear/editar/eliminar de
--   desarrollo: no hay comprobacion de esas acciones en el flujo actual.
-- * Retroactivos y sus hijos: sin acciones, como los modulos vigentes de
--   desarrollo; se autorizan por modulo (requiere_modulo/requiere_modulos).
-- eliminar se conserva como accion global solicitada, sin vinculos
-- delegables. La existencia de una accion no concede su ejecucion.
-- ver_montos conserva compatibilidad; no sustituye el control actual de
-- montos por capacidades/configuracion de ocultamiento.

START TRANSACTION;

-- Raices primero: cinco registros, sin configurar delegacion.
INSERT INTO modulos (padre_id, nombre, identificador, activo)
SELECT NULL, catalogo.nombre, catalogo.identificador, 1
FROM (
    SELECT 'Gestion de usuarios' AS nombre,
           'usuarios_creacion_usuarios' AS identificador
    UNION ALL SELECT 'Proyeccion Compras', 'usuarios_proyeccion_compras'
    UNION ALL SELECT 'Garantias', 'usuarios_garantias'
    UNION ALL SELECT 'Caratula', 'usuarios_caratula'
    UNION ALL SELECT 'Retroactivos', 'usuarios_retroactivos'
) AS catalogo
WHERE NOT EXISTS (
    SELECT 1 FROM modulos existente
    WHERE existente.identificador = catalogo.identificador
);

-- Tres hijos de Retroactivos; el padre se resuelve por clave logica.
INSERT INTO modulos (padre_id, nombre, identificador, activo)
SELECT padre.id, catalogo.nombre, catalogo.identificador, 1
FROM (
    SELECT 'Caratula de Retroactivos' AS nombre,
           'usuarios_caratula_retroactivos' AS identificador
    UNION ALL SELECT 'Calculadora de Retroactivos', 'usuarios_calculadora_retroactivos'
    UNION ALL SELECT 'Solicitudes de Retroactivos', 'usuarios_solicitudes_retroactivos'
) AS catalogo
INNER JOIN modulos padre ON padre.identificador = 'usuarios_retroactivos'
WHERE NOT EXISTS (
    SELECT 1 FROM modulos existente
    WHERE existente.identificador = catalogo.identificador
);

-- Las cinco acciones globales verificadas en desarrollo.
INSERT INTO acciones (nombre, identificador, activo)
SELECT catalogo.nombre, catalogo.identificador, 1
FROM (
    SELECT 'Ver' AS nombre, 'ver' AS identificador
    UNION ALL SELECT 'Crear', 'crear'
    UNION ALL SELECT 'Editar', 'editar'
    UNION ALL SELECT 'Eliminar', 'eliminar'
    UNION ALL SELECT 'Ver montos', 'ver_montos'
) AS catalogo
WHERE NOT EXISTS (
    SELECT 1 FROM acciones existente
    WHERE existente.identificador = catalogo.identificador
);

-- Nueve combinaciones explicitas; no generar un producto cartesiano CRUD.
INSERT INTO modulo_acciones (modulo_id, accion_id)
SELECT modulo.id, accion.id
FROM (
    SELECT 'usuarios_creacion_usuarios' AS modulo, 'ver' AS accion
    UNION ALL SELECT 'usuarios_proyeccion_compras', 'ver'
    UNION ALL SELECT 'usuarios_proyeccion_compras', 'crear'
    UNION ALL SELECT 'usuarios_proyeccion_compras', 'ver_montos'
    UNION ALL SELECT 'usuarios_garantias', 'ver'
    UNION ALL SELECT 'usuarios_garantias', 'crear'
    UNION ALL SELECT 'usuarios_garantias', 'editar'
    UNION ALL SELECT 'usuarios_caratula', 'ver'
    UNION ALL SELECT 'usuarios_caratula', 'ver_montos'
) AS catalogo
INNER JOIN modulos modulo ON modulo.identificador = catalogo.modulo
INNER JOIN acciones accion ON accion.identificador = catalogo.accion
WHERE NOT EXISTS (
    SELECT 1 FROM modulo_acciones existente
    WHERE existente.modulo_id = modulo.id
      AND existente.accion_id = accion.id
);

COMMIT;
