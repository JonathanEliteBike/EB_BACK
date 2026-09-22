"""Reglas de permisos exclusivas del módulo Proyección Compras."""

MODULO_PROYECCION_COMPRAS = 'usuarios_proyeccion_compras'
ACCION_VER = 'ver'
ACCION_CREAR = 'crear'
ACCION_VER_MONTOS = 'ver_montos'
MENSAJE_ACCION_REQUIERE_VER = (
    'Para asignar esta acción en Proyección Compras también debe asignar Ver.'
)


def es_accion_proyeccion_compras(cur, modulo_id, accion_id, accion_identificador):
    """Indica si los IDs corresponden a una acción concreta de este módulo."""
    cur.execute(
        """
        SELECT 1
        FROM modulos m
        INNER JOIN acciones a ON a.id = %s
        WHERE m.id = %s
          AND m.identificador = %s
          AND a.identificador = %s
        """,
        (accion_id, modulo_id, MODULO_PROYECCION_COMPRAS, accion_identificador),
    )
    return cur.fetchone() is not None


def obtener_ids_ver_proyeccion_compras(cur):
    """Obtiene los IDs técnicos de Ver para validar su prerequisito."""
    cur.execute(
        """
        SELECT m.id AS modulo_id, a.id AS accion_id
        FROM modulos m
        INNER JOIN acciones a ON a.identificador = %s
        WHERE m.identificador = %s
        LIMIT 1
        """,
        (ACCION_VER, MODULO_PROYECCION_COMPRAS),
    )
    return cur.fetchone()


def accion_proyeccion_requiere_ver(cur, modulo_id, accion_id):
    """Crear y Ver montos no son útiles ni válidos sin acceso base de lectura."""
    return any(
        es_accion_proyeccion_compras(cur, modulo_id, accion_id, accion)
        for accion in (ACCION_CREAR, ACCION_VER_MONTOS)
    )
