"""
Migración: arquitectura de usuarios, jerarquía y permisos.

Crea las tablas:
- modulos
- acciones
- modulo_acciones
- jerarquia_usuarios
- limites_usuario
- permisos_delegables
- usuario_permisos

Las tablas usuarios y roles ya existen y NO se modifican.

Uso:
    python migrations/2026_08_usuarios_permisos.py
"""

import sys
import os

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from db_conexion import obtener_conexion


DDL = [
    """
    CREATE TABLE IF NOT EXISTS modulos (
        id INT NOT NULL AUTO_INCREMENT,
        padre_id INT NULL,
        nombre VARCHAR(100) NOT NULL,
        identificador VARCHAR(100) NOT NULL,
        activo TINYINT(1) NOT NULL DEFAULT 1,
        creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            ON UPDATE CURRENT_TIMESTAMP,

        PRIMARY KEY (id),

        UNIQUE KEY uq_modulos_identificador (identificador),

        KEY idx_modulos_padre_id (padre_id),

        CONSTRAINT fk_modulos_padre
            FOREIGN KEY (padre_id)
            REFERENCES modulos (id)
            ON UPDATE CASCADE
            ON DELETE RESTRICT
    ) ENGINE=InnoDB
      DEFAULT CHARSET=utf8mb4
      COLLATE=utf8mb4_general_ci;
    """,

    """
    CREATE TABLE IF NOT EXISTS acciones (
        id INT NOT NULL AUTO_INCREMENT,
        nombre VARCHAR(100) NOT NULL,
        identificador VARCHAR(100) NOT NULL,
        activo TINYINT(1) NOT NULL DEFAULT 1,
        creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            ON UPDATE CURRENT_TIMESTAMP,

        PRIMARY KEY (id),

        UNIQUE KEY uq_acciones_identificador (identificador)
    ) ENGINE=InnoDB
      DEFAULT CHARSET=utf8mb4
      COLLATE=utf8mb4_general_ci;
    """,

    """
    CREATE TABLE IF NOT EXISTS modulo_acciones (
        modulo_id INT NOT NULL,
        accion_id INT NOT NULL,

        PRIMARY KEY (modulo_id, accion_id),

        KEY idx_modulo_acciones_accion_id (accion_id),

        CONSTRAINT fk_modulo_acciones_modulo
            FOREIGN KEY (modulo_id)
            REFERENCES modulos (id)
            ON UPDATE CASCADE
            ON DELETE RESTRICT,

        CONSTRAINT fk_modulo_acciones_accion
            FOREIGN KEY (accion_id)
            REFERENCES acciones (id)
            ON UPDATE CASCADE
            ON DELETE RESTRICT
    ) ENGINE=InnoDB
      DEFAULT CHARSET=utf8mb4
      COLLATE=utf8mb4_general_ci;
    """,

    """
    CREATE TABLE IF NOT EXISTS jerarquia_usuarios (
        id INT NOT NULL AUTO_INCREMENT,
        padre_id INT NOT NULL,
        hijo_id INT NOT NULL,
        creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

        PRIMARY KEY (id),

        UNIQUE KEY uq_jerarquia_hijo (hijo_id),

        KEY idx_jerarquia_padre_id (padre_id),

        CONSTRAINT fk_jerarquia_padre
            FOREIGN KEY (padre_id)
            REFERENCES usuarios (id)
            ON UPDATE CASCADE
            ON DELETE RESTRICT,

        CONSTRAINT fk_jerarquia_hijo
            FOREIGN KEY (hijo_id)
            REFERENCES usuarios (id)
            ON UPDATE CASCADE
            ON DELETE RESTRICT
    ) ENGINE=InnoDB
      DEFAULT CHARSET=utf8mb4
      COLLATE=utf8mb4_general_ci;
    """,

    """
    CREATE TABLE IF NOT EXISTS limites_usuario (
        padre_id INT NOT NULL,
        max_hijos INT NOT NULL DEFAULT 3,
        creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            ON UPDATE CURRENT_TIMESTAMP,

        PRIMARY KEY (padre_id),

        CONSTRAINT fk_limites_usuario_padre
            FOREIGN KEY (padre_id)
            REFERENCES usuarios (id)
            ON UPDATE CASCADE
            ON DELETE RESTRICT,

        CONSTRAINT chk_limites_usuario_max_hijos
            CHECK (max_hijos >= 0)
    ) ENGINE=InnoDB
      DEFAULT CHARSET=utf8mb4
      COLLATE=utf8mb4_general_ci;
    """,

    """
    CREATE TABLE IF NOT EXISTS permisos_delegables (
        administrador_id INT NOT NULL,
        modulo_id INT NOT NULL,
        accion_id INT NOT NULL,

        PRIMARY KEY (
            administrador_id,
            modulo_id,
            accion_id
        ),

        KEY idx_permisos_delegables_modulo_id (modulo_id),
        KEY idx_permisos_delegables_accion_id (accion_id),

        CONSTRAINT fk_permisos_delegables_administrador
            FOREIGN KEY (administrador_id)
            REFERENCES usuarios (id)
            ON UPDATE CASCADE
            ON DELETE RESTRICT,

        CONSTRAINT fk_permisos_delegables_modulo
            FOREIGN KEY (modulo_id)
            REFERENCES modulos (id)
            ON UPDATE CASCADE
            ON DELETE RESTRICT,

        CONSTRAINT fk_permisos_delegables_accion
            FOREIGN KEY (accion_id)
            REFERENCES acciones (id)
            ON UPDATE CASCADE
            ON DELETE RESTRICT
    ) ENGINE=InnoDB
      DEFAULT CHARSET=utf8mb4
      COLLATE=utf8mb4_general_ci;
    """,

    """
    CREATE TABLE IF NOT EXISTS usuario_permisos (
        usuario_id INT NOT NULL,
        modulo_id INT NOT NULL,
        accion_id INT NOT NULL,

        PRIMARY KEY (
            usuario_id,
            modulo_id,
            accion_id
        ),

        KEY idx_usuario_permisos_modulo_id (modulo_id),
        KEY idx_usuario_permisos_accion_id (accion_id),

        CONSTRAINT fk_usuario_permisos_usuario
            FOREIGN KEY (usuario_id)
            REFERENCES usuarios (id)
            ON UPDATE CASCADE
            ON DELETE RESTRICT,

        CONSTRAINT fk_usuario_permisos_modulo
            FOREIGN KEY (modulo_id)
            REFERENCES modulos (id)
            ON UPDATE CASCADE
            ON DELETE RESTRICT,

        CONSTRAINT fk_usuario_permisos_accion
            FOREIGN KEY (accion_id)
            REFERENCES acciones (id)
            ON UPDATE CASCADE
            ON DELETE RESTRICT
    ) ENGINE=InnoDB
      DEFAULT CHARSET=utf8mb4
      COLLATE=utf8mb4_general_ci;
    """
]


def run():
    conn = obtener_conexion()

    if conn is None:
        raise RuntimeError("No fue posible obtener la conexión a MySQL.")

    cur = conn.cursor()

    try:
        for statement in DDL:
            cur.execute(statement)

        conn.commit()

        print(
            "[OK] Tablas de usuarios, jerarquía y permisos "
            "creadas o ya existentes."
        )

    except Exception as e:
        conn.rollback()
        print(f"[ERROR] Error ejecutando la migración: {e}")
        raise

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    run()