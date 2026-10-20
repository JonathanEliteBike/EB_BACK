-- Separa el acceso propio del distribuidor (rol 2) de su bolsa delegable
-- hacia usuarios hijo (rol 3). Conserva todas las tablas heredadas.

-- MySQL no admite ADD COLUMN IF NOT EXISTS en todas las versiones usadas por
-- el proyecto; se consulta information_schema para mantener la migración
-- repetible.
SET @columna_delegable_existe := (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'modulos'
      AND COLUMN_NAME = 'delegable_a_hijos'
);
SET @sql_agregar_delegable := IF(
    @columna_delegable_existe = 0,
    'ALTER TABLE modulos ADD COLUMN delegable_a_hijos TINYINT(1) NOT NULL DEFAULT 1 AFTER activo',
    'SELECT 1'
);
PREPARE stmt_agregar_delegable FROM @sql_agregar_delegable;
EXECUTE stmt_agregar_delegable;
DEALLOCATE PREPARE stmt_agregar_delegable;

CREATE TABLE IF NOT EXISTS modulos_roles_acceso (
    modulo_id INT NOT NULL,
    rol_id INT NOT NULL,
    activo TINYINT(1) NOT NULL DEFAULT 1,
    creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (modulo_id, rol_id),
    CONSTRAINT fk_mra_modulo
        FOREIGN KEY (modulo_id) REFERENCES modulos (id)
        ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- Los ocho módulos son accesos propios del distribuidor (rol 2).
-- Todos pueden delegarse a rol 3 excepto usuarios_creacion_usuarios.
UPDATE modulos
SET delegable_a_hijos = CASE identificador
    WHEN 'usuarios_creacion_usuarios' THEN 0
    WHEN 'usuarios_proyeccion_compras' THEN 1
    WHEN 'usuarios_garantias' THEN 1
    WHEN 'usuarios_caratula' THEN 1
    WHEN 'usuarios_retroactivos' THEN 1
    WHEN 'usuarios_caratula_retroactivos' THEN 1
    WHEN 'usuarios_calculadora_retroactivos' THEN 1
    WHEN 'usuarios_solicitudes_retroactivos' THEN 1
    ELSE delegable_a_hijos
END
WHERE identificador IN (
    'usuarios_proyeccion_compras',
    'usuarios_garantias',
    'usuarios_caratula',
    'usuarios_retroactivos',
    'usuarios_caratula_retroactivos',
    'usuarios_calculadora_retroactivos',
    'usuarios_solicitudes_retroactivos',
    'usuarios_creacion_usuarios'
);

INSERT INTO modulos_roles_acceso (modulo_id, rol_id, activo)
SELECT m.id, 2, 1
FROM modulos m
WHERE m.identificador IN (
    'usuarios_proyeccion_compras',
    'usuarios_garantias',
    'usuarios_caratula',
    'usuarios_retroactivos',
    'usuarios_caratula_retroactivos',
    'usuarios_calculadora_retroactivos',
    'usuarios_solicitudes_retroactivos',
    'usuarios_creacion_usuarios'
)
  AND m.activo = 1
ON DUPLICATE KEY UPDATE activo = VALUES(activo);
