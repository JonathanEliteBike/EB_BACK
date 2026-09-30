-- Regulariza el módulo histórico del flujo Distribuidor → Usuario hijo.
-- No reutiliza ni modifica el módulo interno "garantias", áreas ni permisos internos.
-- Es idempotente: conserva la fila técnica si ya existe y sólo normaliza sus datos.

START TRANSACTION;

INSERT INTO modulos (
    padre_id, area_id, nombre, identificador, ruta, activo, delegable_a_hijos
)
SELECT NULL, NULL, 'Garantías', 'usuarios_garantias', '/usuarios/garantias', 1, 1
WHERE NOT EXISTS (
    SELECT 1 FROM modulos WHERE identificador = 'usuarios_garantias'
);

UPDATE modulos
SET nombre = 'Garantías',
    ruta = '/usuarios/garantias',
    activo = 1,
    delegable_a_hijos = 1
WHERE identificador = 'usuarios_garantias';

-- El distribuidor tiene acceso propio al portal; esta relación mantiene el
-- catálogo técnico coherente sin asignar nada a usuarios hijo.
INSERT INTO modulos_roles_acceso (modulo_id, rol_id, activo)
SELECT m.id, 2, 1
FROM modulos m
WHERE m.identificador = 'usuarios_garantias'
ON DUPLICATE KEY UPDATE activo = 1;

-- La bolsa se crea para distribuidores activos. No se insertan filas en
-- usuario_modulos ni usuario_permisos: ningún usuario hijo recibe acceso.
INSERT IGNORE INTO permisos_delegables_modulos (administrador_id, modulo_id)
SELECT u.id, m.id
FROM usuarios u
INNER JOIN modulos m ON m.identificador = 'usuarios_garantias'
WHERE u.rol_id = 2
  AND u.activo = 1;

COMMIT;
