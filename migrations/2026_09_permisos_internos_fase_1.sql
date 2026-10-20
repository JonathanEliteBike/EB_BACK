-- Fase 1: permisos independientes para usuarios internos.
--
-- Esta migracion no modifica usuarios existentes ni las tablas de delegacion
-- de distribuidores/usuarios hijo. El rol 4 se reserva para Usuario Interno.
-- No ejecutar automaticamente: aplicar en una sesion controlada de MySQL.

START TRANSACTION;

-- El rol 4 no aparece usado en el codigo ni en las migraciones actuales.
-- Si ya existiera en una base concreta, no se sobrescribe ni se reasigna.
INSERT INTO roles (id, nombre)
SELECT 4, 'Usuario Interno'
WHERE NOT EXISTS (
    SELECT 1 FROM roles WHERE id = 4
);

CREATE TABLE IF NOT EXISTS areas (
    id INT NOT NULL AUTO_INCREMENT,
    nombre VARCHAR(100) NOT NULL,
    activo TINYINT(1) NOT NULL DEFAULT 1,
    creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_areas_nombre (nombre)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- MySQL no soporta ADD COLUMN IF NOT EXISTS en todas las versiones usadas
-- por el proyecto; se consulta el catalogo para mantener la migracion repetible.
SET @area_id_en_usuarios := (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'usuarios'
      AND COLUMN_NAME = 'area_id'
);
SET @sql_agregar_area_usuarios := IF(
    @area_id_en_usuarios = 0,
    'ALTER TABLE usuarios ADD COLUMN area_id INT NULL AFTER cliente_id',
    'SELECT 1'
);
PREPARE stmt_agregar_area_usuarios FROM @sql_agregar_area_usuarios;
EXECUTE stmt_agregar_area_usuarios;
DEALLOCATE PREPARE stmt_agregar_area_usuarios;

SET @area_id_en_modulos := (
    SELECT COUNT(*)
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'modulos'
      AND COLUMN_NAME = 'area_id'
);
SET @sql_agregar_area_modulos := IF(
    @area_id_en_modulos = 0,
    'ALTER TABLE modulos ADD COLUMN area_id INT NULL AFTER padre_id',
    'SELECT 1'
);
PREPARE stmt_agregar_area_modulos FROM @sql_agregar_area_modulos;
EXECUTE stmt_agregar_area_modulos;
DEALLOCATE PREPARE stmt_agregar_area_modulos;

SET @fk_usuarios_area_existe := (
    SELECT COUNT(*)
    FROM information_schema.TABLE_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE()
      AND TABLE_NAME = 'usuarios'
      AND CONSTRAINT_NAME = 'fk_usuarios_area'
);
SET @sql_fk_usuarios_area := IF(
    @fk_usuarios_area_existe = 0,
    'ALTER TABLE usuarios ADD KEY idx_usuarios_area_id (area_id), ADD CONSTRAINT fk_usuarios_area FOREIGN KEY (area_id) REFERENCES areas (id) ON UPDATE CASCADE ON DELETE SET NULL',
    'SELECT 1'
);
PREPARE stmt_fk_usuarios_area FROM @sql_fk_usuarios_area;
EXECUTE stmt_fk_usuarios_area;
DEALLOCATE PREPARE stmt_fk_usuarios_area;

SET @fk_modulos_area_existe := (
    SELECT COUNT(*)
    FROM information_schema.TABLE_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE()
      AND TABLE_NAME = 'modulos'
      AND CONSTRAINT_NAME = 'fk_modulos_area'
);
SET @sql_fk_modulos_area := IF(
    @fk_modulos_area_existe = 0,
    'ALTER TABLE modulos ADD KEY idx_modulos_area_id (area_id), ADD CONSTRAINT fk_modulos_area FOREIGN KEY (area_id) REFERENCES areas (id) ON UPDATE CASCADE ON DELETE SET NULL',
    'SELECT 1'
);
PREPARE stmt_fk_modulos_area FROM @sql_fk_modulos_area;
EXECUTE stmt_fk_modulos_area;
DEALLOCATE PREPARE stmt_fk_modulos_area;

CREATE TABLE IF NOT EXISTS usuario_permisos_internos (
    usuario_id INT NOT NULL,
    modulo_id INT NOT NULL,
    accion_id INT NOT NULL,
    creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (usuario_id, modulo_id, accion_id),
    KEY idx_upi_modulo_id (modulo_id),
    KEY idx_upi_accion_id (accion_id),
    CONSTRAINT fk_upi_usuario
        FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT fk_upi_modulo
        FOREIGN KEY (modulo_id) REFERENCES modulos (id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT fk_upi_accion
        FOREIGN KEY (accion_id) REFERENCES acciones (id)
        ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

COMMIT;
