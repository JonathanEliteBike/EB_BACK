-- Base paralela para el modelo de acceso por módulo y capacidades globales.
-- No elimina ni modifica permisos_delegables, usuario_permisos, acciones ni
-- modulo_acciones: esos datos permanecen como compatibilidad temporal.

CREATE TABLE IF NOT EXISTS permisos_delegables_modulos (
    administrador_id INT NOT NULL,
    modulo_id INT NOT NULL,
    creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (administrador_id, modulo_id),
    KEY idx_pdm_modulo_id (modulo_id),
    CONSTRAINT fk_pdm_administrador
        FOREIGN KEY (administrador_id) REFERENCES usuarios (id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT fk_pdm_modulo
        FOREIGN KEY (modulo_id) REFERENCES modulos (id)
        ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS usuario_modulos (
    usuario_id INT NOT NULL,
    modulo_id INT NOT NULL,
    creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (usuario_id, modulo_id),
    KEY idx_um_modulo_id (modulo_id),
    CONSTRAINT fk_um_usuario
        FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT fk_um_modulo
        FOREIGN KEY (modulo_id) REFERENCES modulos (id)
        ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS permisos_delegables_capacidades (
    administrador_id INT NOT NULL,
    capacidad VARCHAR(100) NOT NULL,
    creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (administrador_id, capacidad),
    KEY idx_pdc_capacidad (capacidad),
    CONSTRAINT fk_pdc_administrador
        FOREIGN KEY (administrador_id) REFERENCES usuarios (id)
        ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS usuario_capacidades (
    usuario_id INT NOT NULL,
    capacidad VARCHAR(100) NOT NULL,
    creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (usuario_id, capacidad),
    KEY idx_uc_capacidad (capacidad),
    CONSTRAINT fk_uc_usuario
        FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
        ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- Backfill de módulos: una acción antigua vigente concede el módulo al rol 2.
INSERT IGNORE INTO permisos_delegables_modulos (administrador_id, modulo_id)
SELECT DISTINCT pd.administrador_id, pd.modulo_id
FROM permisos_delegables pd
INNER JOIN usuarios administrador ON administrador.id = pd.administrador_id AND administrador.rol_id = 2
INNER JOIN modulos m ON m.id = pd.modulo_id AND m.activo = 1
INNER JOIN acciones a ON a.id = pd.accion_id AND a.activo = 1
INNER JOIN modulo_acciones ma ON ma.modulo_id = pd.modulo_id AND ma.accion_id = pd.accion_id;

-- Backfill de hijos: conserva únicamente permisos históricos que aún tenían
-- respaldo equivalente del padre y una jerarquía válida.
INSERT IGNORE INTO usuario_modulos (usuario_id, modulo_id)
SELECT DISTINCT up.usuario_id, up.modulo_id
FROM usuario_permisos up
INNER JOIN usuarios hijo ON hijo.id = up.usuario_id AND hijo.rol_id = 3
INNER JOIN jerarquia_usuarios ju ON ju.hijo_id = up.usuario_id
INNER JOIN permisos_delegables pd
    ON pd.administrador_id = ju.padre_id
   AND pd.modulo_id = up.modulo_id
   AND pd.accion_id = up.accion_id
INNER JOIN modulos m ON m.id = up.modulo_id AND m.activo = 1
INNER JOIN acciones a ON a.id = up.accion_id AND a.activo = 1
INNER JOIN modulo_acciones ma ON ma.modulo_id = up.modulo_id AND ma.accion_id = up.accion_id;

-- La intención histórica de ver_montos pasa a la capacidad global mostrar_montos.
INSERT IGNORE INTO permisos_delegables_capacidades (administrador_id, capacidad)
SELECT DISTINCT pd.administrador_id, 'mostrar_montos'
FROM permisos_delegables pd
INNER JOIN usuarios administrador ON administrador.id = pd.administrador_id AND administrador.rol_id = 2
INNER JOIN modulos m ON m.id = pd.modulo_id AND m.activo = 1
INNER JOIN acciones a ON a.id = pd.accion_id AND a.activo = 1 AND a.identificador = 'ver_montos'
INNER JOIN modulo_acciones ma ON ma.modulo_id = pd.modulo_id AND ma.accion_id = pd.accion_id
WHERE m.identificador IN ('usuarios_caratula', 'usuarios_proyeccion_compras');

INSERT IGNORE INTO usuario_capacidades (usuario_id, capacidad)
SELECT DISTINCT up.usuario_id, 'mostrar_montos'
FROM usuario_permisos up
INNER JOIN usuarios hijo ON hijo.id = up.usuario_id AND hijo.rol_id = 3
INNER JOIN jerarquia_usuarios ju ON ju.hijo_id = up.usuario_id
INNER JOIN permisos_delegables pd
    ON pd.administrador_id = ju.padre_id
   AND pd.modulo_id = up.modulo_id
   AND pd.accion_id = up.accion_id
INNER JOIN modulos m ON m.id = up.modulo_id AND m.activo = 1
INNER JOIN acciones a ON a.id = up.accion_id AND a.activo = 1 AND a.identificador = 'ver_montos'
INNER JOIN modulo_acciones ma ON ma.modulo_id = up.modulo_id AND ma.accion_id = up.accion_id
WHERE m.identificador IN ('usuarios_caratula', 'usuarios_proyeccion_compras');
