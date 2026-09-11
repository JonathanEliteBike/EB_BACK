-- Control monetario exclusivo para usuarios hijo (rol 3).
-- Esta migracion es aditiva, idempotente y no modifica la infraestructura
-- existente de modulos ni las capacidades temporales de mostrar_montos.

CREATE TABLE IF NOT EXISTS ambitos_montos (
    id INT NOT NULL AUTO_INCREMENT,
    identificador VARCHAR(100) NOT NULL,
    nombre VARCHAR(150) NOT NULL,
    modulo_identificador VARCHAR(100) NULL,
    activo TINYINT(1) NOT NULL DEFAULT 1,
    creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ambitos_montos_identificador (identificador)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS configuracion_montos_usuario (
    usuario_id INT NOT NULL,
    ocultar_montos_global TINYINT(1) NOT NULL DEFAULT 1,
    creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (usuario_id),
    CONSTRAINT fk_configuracion_montos_usuario
        FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
        ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS configuracion_montos_usuario_ambito (
    usuario_id INT NOT NULL,
    ambito_id INT NOT NULL,
    ocultar_montos TINYINT(1) NOT NULL DEFAULT 0,
    creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (usuario_id, ambito_id),
    CONSTRAINT fk_configuracion_montos_ambito_usuario
        FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT fk_configuracion_montos_ambito
        FOREIGN KEY (ambito_id) REFERENCES ambitos_montos (id)
        ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- Retroactivos agrupa su caratula, solicitudes y calculadora: son parte del
-- mismo flujo comercial y se evita duplicar configuraciones para negocio.
INSERT IGNORE INTO ambitos_montos (identificador, nombre, modulo_identificador) VALUES
    ('proyeccion_compras', 'Proyeccion Compras', 'usuarios_proyeccion_compras'),
    ('detalle_compras', 'Detalles de Compra', 'usuarios_caratula'),
    ('caratula_distribuidor', 'Caratula de Distribuidor', 'usuarios_caratula'),
    ('retroactivos', 'Retroactivos', 'usuarios_caratula_retroactivos');

-- Todo rol 3 existente inicia bloqueado globalmente. INSERT IGNORE permite
-- reejecutar sin sobrescribir una configuracion que ya haya sido administrada.
INSERT IGNORE INTO configuracion_montos_usuario (usuario_id, ocultar_montos_global)
SELECT id, 1
FROM usuarios
WHERE rol_id = 3;
