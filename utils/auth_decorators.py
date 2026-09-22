# utils/auth_decorators.py

from functools import wraps
from flask import request, jsonify, g
from db_conexion import obtener_conexion
from utils.jwt_utils import verificar_token


def requiere_autenticacion(f):
    """Valida un JWT Bearer y deja su payload disponible en ``g.usuario_actual``."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')

        if not auth_header.startswith('Bearer '):
            return jsonify({"error": "Token de autenticación requerido."}), 401

        token = auth_header[7:].strip()
        if not token:
            return jsonify({"error": "Token de autenticación requerido."}), 401

        payload = verificar_token(token)
        if not payload or not payload.get('id'):
            return jsonify({"error": "Token inválido o expirado."}), 401

        g.usuario_actual = payload
        return f(*args, **kwargs)

    return decorated_function


def requiere_rol(*roles_permitidos):
    """Restringe una ruta a los roles presentes en el payload JWT autenticado."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            try:
                rol_usuario = int(g.usuario_actual.get('rol'))
            except (AttributeError, TypeError, ValueError):
                return jsonify({"error": "Rol no autorizado."}), 403

            if rol_usuario not in roles_permitidos:
                return jsonify({"error": "Rol no autorizado."}), 403

            return f(*args, **kwargs)

        return decorated_function
    return decorator


def usuario_actual_tiene_permiso(modulo_identificador, accion_identificador):
    """Comprueba el permiso efectivo del usuario autenticado en ``g``.

    Los distribuidores usan su bolsa delegable y los usuarios hijo requieren,
    además, que el permiso asignado continúe presente en la bolsa de su padre.
    """
    payload = getattr(g, "usuario_actual", None)
    if not payload or not payload.get("id"):
        return False, (jsonify({"error": "Token de autenticación requerido."}), 401)

    conn = obtener_conexion()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT id, rol_id FROM usuarios WHERE id = %s AND activo = 1",
            (payload["id"],),
        )
        usuario = cur.fetchone()
        if not usuario:
            return False, (jsonify({"error": "Usuario no encontrado o inactivo."}), 401)

        rol_id = usuario["rol_id"]
        if rol_id == 1:
            return True, None

        if rol_id == 2:
            cur.execute("""
                SELECT 1
                FROM modulos_roles_acceso mra
                INNER JOIN modulos m ON m.id = mra.modulo_id AND m.activo = 1
                WHERE mra.rol_id = 2
                  AND mra.activo = 1
                  AND m.identificador = %s
            """, (modulo_identificador,))
        elif rol_id == 3:
            cur.execute("""
                SELECT 1
                FROM usuario_permisos up
                INNER JOIN jerarquia_usuarios ju ON ju.hijo_id = up.usuario_id
                INNER JOIN permisos_delegables pd
                    ON pd.administrador_id = ju.padre_id
                   AND pd.modulo_id = up.modulo_id
                   AND pd.accion_id = up.accion_id
                INNER JOIN modulo_acciones ma
                    ON ma.modulo_id = up.modulo_id AND ma.accion_id = up.accion_id
                INNER JOIN modulos m ON m.id = up.modulo_id AND m.activo = 1
                INNER JOIN acciones a ON a.id = up.accion_id AND a.activo = 1
                WHERE up.usuario_id = %s
                  AND m.identificador = %s
                  AND a.identificador = %s
            """, (usuario["id"], modulo_identificador, accion_identificador))
        else:
            return False, (jsonify({"error": "Rol no autorizado."}), 403)

        if not cur.fetchone():
            return False, (jsonify({
                "error": f"Acceso denegado: Sin permisos de '{accion_identificador}' en '{modulo_identificador}'."
            }), 403)
        return True, None
    finally:
        cur.close()
        conn.close()


def requiere_permiso(modulo_identificador, accion_identificador):
    """Exige un JWT previamente validado y el permiso efectivo indicado."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            permitido, error = usuario_actual_tiene_permiso(
                modulo_identificador, accion_identificador
            )
            if not permitido:
                return error
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def _usuario_actual_activo():
    """Obtiene el usuario activo respaldado por el JWT, sin confiar en su rol."""
    payload = getattr(g, "usuario_actual", None)
    if not payload or not payload.get("id"):
        return None, (jsonify({"error": "Token de autenticación requerido."}), 401)

    conn = obtener_conexion()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT id, rol_id FROM usuarios WHERE id = %s AND activo = 1",
            (payload["id"],),
        )
        usuario = cur.fetchone()
        if not usuario:
            return None, (jsonify({"error": "Usuario no encontrado o inactivo."}), 401)
        return usuario, None
    finally:
        cur.close()
        conn.close()


def usuario_actual_tiene_modulo(modulo_identificador):
    """Comprueba el acceso efectivo al módulo en el modelo paralelo nuevo.

    El rol 2 tiene acceso propio al portal; la bolsa sólo decide lo que puede
    delegar a sus usuarios hijo (rol 3).
    """
    usuario, error = _usuario_actual_activo()
    if error:
        return False, error
    if usuario["rol_id"] == 1:
        return True, None
    if usuario["rol_id"] == 2:
        return True, None

    conn = obtener_conexion()
    cur = conn.cursor()
    try:
        if usuario["rol_id"] == 3:
            cur.execute(
                """
                SELECT 1
                FROM usuario_modulos um
                INNER JOIN jerarquia_usuarios ju ON ju.hijo_id = um.usuario_id
                INNER JOIN permisos_delegables_modulos pdm
                    ON pdm.administrador_id = ju.padre_id AND pdm.modulo_id = um.modulo_id
                INNER JOIN modulos m ON m.id = um.modulo_id AND m.activo = 1
                WHERE um.usuario_id = %s
                  AND m.identificador = %s
                  AND m.delegable_a_hijos = 1
                """,
                (usuario["id"], modulo_identificador),
            )
        else:
            return False, (jsonify({"error": "Rol no autorizado."}), 403)

        if cur.fetchone():
            return True, None
        return False, (jsonify({"error": f"Acceso denegado al módulo '{modulo_identificador}'."}), 403)
    finally:
        cur.close()
        conn.close()


def requiere_modulo(modulo_identificador):
    """Exige que el usuario autenticado tenga acceso efectivo al módulo."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            permitido, error = usuario_actual_tiene_modulo(modulo_identificador)
            if not permitido:
                return error
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def requiere_modulos(*modulos_identificador):
    """Exige acceso efectivo a todos los módulos indicados.

    Se utiliza cuando una pantalla hija depende de su área padre. No altera
    las asignaciones existentes: una asignación hija puede permanecer guardada
    aunque el acceso padre sea retirado, pero no resulta utilizable hasta que
    ambos módulos vuelvan a estar habilitados.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            for modulo_identificador in modulos_identificador:
                permitido, error = usuario_actual_tiene_modulo(modulo_identificador)
                if not permitido:
                    return error
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def usuario_actual_tiene_capacidad(capacidad):
    """Comprueba una capacidad global sin conceder acceso a módulos.

    Las capacidades son propias para rol 2 y delegables únicamente hacia
    rol 3. Por ejemplo, un distribuidor siempre puede ver sus montos, pero un
    hijo requiere tanto la bolsa del padre como su asignación individual.
    """
    usuario, error = _usuario_actual_activo()
    if error:
        return False, error
    if usuario["rol_id"] in (1, 2):
        return True, None

    conn = obtener_conexion()
    cur = conn.cursor()
    try:
        if usuario["rol_id"] == 3:
            cur.execute(
                """
                SELECT 1
                FROM usuario_capacidades uc
                INNER JOIN jerarquia_usuarios ju ON ju.hijo_id = uc.usuario_id
                INNER JOIN permisos_delegables_capacidades pdc
                    ON pdc.administrador_id = ju.padre_id AND pdc.capacidad = uc.capacidad
                WHERE uc.usuario_id = %s AND uc.capacidad = %s
                """,
                (usuario["id"], capacidad),
            )
        else:
            return False, (jsonify({"error": "Rol no autorizado."}), 403)

        if cur.fetchone():
            return True, None
        return False, (jsonify({"error": f"Capacidad no autorizada: '{capacidad}'."}), 403)
    finally:
        cur.close()
        conn.close()


def requiere_capacidad(capacidad):
    """Exige una capacidad global efectiva; no sustituye requiere_modulo."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            permitido, error = usuario_actual_tiene_capacidad(capacidad)
            if not permitido:
                return error
            return f(*args, **kwargs)
        return decorated_function
    return decorator
