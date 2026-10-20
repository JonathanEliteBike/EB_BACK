# utils/auth_decorators.py

from functools import wraps
import logging
from flask import request, jsonify, g, current_app
from db_conexion import obtener_conexion
from utils.jwt_utils import verificar_token


def permite_acceso_interno_sin_regla(f):
    """Marca un endpoint técnico que el rol 4 necesita antes de evaluar reglas.

    Esta excepción no otorga acceso a operaciones de negocio; sólo evita que
    la carga de la propia matriz de permisos se bloquee a sí misma.
    """
    f._permite_acceso_interno_sin_regla = True
    return f


def _resultado_permiso_interno_endpoint():
    """Obtiene y memoriza la decisión dinámica del endpoint actual para rol 4.

    ``True`` significa que alguna regla coincidente autorizó al usuario,
    ``False`` que existen reglas pero ninguna aplica, y ``None`` que no hay
    regla activa o que los patrones configurados son ambiguos.
    """
    payload = getattr(g, "usuario_actual", None)
    try:
        es_usuario_interno = int((payload or {}).get("rol")) == 4
    except (TypeError, ValueError):
        es_usuario_interno = False
    if not es_usuario_interno:
        return None

    if hasattr(g, "_resultado_permiso_interno_endpoint"):
        return g._resultado_permiso_interno_endpoint

    from services.permisos_internos_service import PermisosInternosService

    resultado = PermisosInternosService.validar_permiso_endpoint(
        payload["id"], request.path, request.method, request.headers.get('X-Ruta-Interna')
    )
    # Los decoradores explícitos siguen siendo reglas válidas cuando el
    # catálogo no define ninguna. Una regla configurada denegada nunca cae aquí.
    if resultado is None:
        vista = current_app.view_functions.get(request.endpoint)
        permiso = getattr(vista, '_permiso_interno', None)
        if permiso:
            modulo, accion = permiso
            resultado = (accion != 'ver_montos'
                         and PermisosInternosService.validar_permiso_usuario(payload['id'], modulo, 'ver')
                         and PermisosInternosService.validar_permiso_usuario(payload['id'], modulo, accion))
            if resultado:
                g._modulos_internos_autorizados = {modulo}
    if resultado is not True and current_app.config.get('PERMISOS_INTERNOS_LOG_403', False):
        diagnostico = PermisosInternosService.diagnosticar_permiso_endpoint(
            payload['id'], request.path, request.method, request.headers.get('X-Ruta-Interna')
        )
        logging.getLogger(__name__).warning('permiso_interno_403 %s', diagnostico)
    g._resultado_permiso_interno_endpoint = resultado
    return resultado


def _error_permiso_interno_endpoint(resultado):
    if resultado is False:
        return jsonify({
            "error": "Acceso denegado: no cuenta con ningún permiso interno válido para este endpoint."
        }), 403
    return jsonify({
        "error": "Acceso denegado: el endpoint no tiene una regla interna activa o su configuración es ambigua."
    }), 403


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

        # El rol 4 se autoriza únicamente con las reglas configuradas para
        # endpoint+método. Los endpoints técnicos que cargan la matriz se
        # marcan de forma explícita; no se abren endpoints de negocio por
        # ausencia de regla.
        try:
            es_usuario_interno = int(payload.get("rol")) == 4
        except (TypeError, ValueError):
            es_usuario_interno = False
        requiere_regla_dinamica = not any((
            getattr(f, "_permite_acceso_interno_sin_regla", False),
            getattr(f, "_permiso_interno", None),
            getattr(f, "_permiso_interno_dinamico", False),
        ))
        if es_usuario_interno and requiere_regla_dinamica:
            resultado = _resultado_permiso_interno_endpoint()
            if resultado is not True:
                return _error_permiso_interno_endpoint(resultado)

        return f(*args, **kwargs)

    return decorated_function


def requiere_rol(*roles_permitidos):
    """Restringe una ruta a los roles presentes en el payload JWT autenticado."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Algunas rutas históricas de administración conservan su control
            # por rol. Cuando además se marcan explícitamente con la capa de
            # permisos internos, el rol 4 llega a ese decorador para que la
            # comprobación módulo + acción decida su acceso. Ninguna ruta sin
            # esta marca cambia de comportamiento.
            permiso_interno = getattr(f, "_permiso_interno", None) or getattr(f, "_permiso_interno_dinamico", None)
            try:
                rol_usuario = int(g.usuario_actual.get('rol'))
            except (AttributeError, TypeError, ValueError):
                return jsonify({"error": "Rol no autorizado."}), 403

            if rol_usuario == 4:
                resultado = _resultado_permiso_interno_endpoint()
                if resultado is True:
                    return f(*args, **kwargs)
                if permiso_interno:
                    return f(*args, **kwargs)
                return _error_permiso_interno_endpoint(resultado)

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
            if _resultado_permiso_interno_endpoint() is True:
                return f(*args, **kwargs)
            permitido, error = usuario_actual_tiene_permiso(
                modulo_identificador, accion_identificador
            )
            if not permitido:
                return error
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def requiere_permiso_interno(modulo_identificador, accion_identificador):
    """Exige un permiso de la capa aislada para usuarios internos.

    El rol 1 conserva su bypass total. El rol 4 se valida exclusivamente en
    ``usuario_permisos_internos``; los demás roles no usan esta capa.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            payload = getattr(g, "usuario_actual", None)
            if not payload or not payload.get("id"):
                return jsonify({"error": "Token de autenticación requerido."}), 401

            try:
                rol_id = int(payload.get("rol"))
            except (TypeError, ValueError):
                return jsonify({"error": "Rol no autorizado."}), 403

            # Esta capa sólo restringe al usuario interno. Los roles existentes
            # continúan con la autorización que ya tenía la ruta protegida.
            if rol_id != 4:
                return f(*args, **kwargs)

            resultado = _resultado_permiso_interno_endpoint()
            if resultado is not None:
                if resultado is True:
                    return f(*args, **kwargs)
                return _error_permiso_interno_endpoint(resultado)

            from services.permisos_internos_service import PermisosInternosService

            permitido = (
                accion_identificador != 'ver_montos'
                and PermisosInternosService.validar_permiso_usuario(
                    payload["id"], modulo_identificador, 'ver'
                )
                and PermisosInternosService.validar_permiso_usuario(
                    payload["id"], modulo_identificador, accion_identificador
                )
            )
            if permitido:
                return f(*args, **kwargs)

            return jsonify({
                "error": (
                    f"Acceso denegado: Sin permiso interno de "
                    f"'{accion_identificador}' en '{modulo_identificador}'."
                )
            }), 403
        # Permite que los decoradores históricos de rol o módulo detecten que
        # esta ruta optó expresamente por la coexistencia con el rol interno.
        decorated_function._permiso_interno = (
            modulo_identificador,
            accion_identificador,
        )
        return decorated_function
    return decorator


def requiere_permiso_interno_dinamico(f):
    """Aplica una regla activa de ``permisos_internos_endpoints`` al rol 4.

    No afecta a roles existentes. Las rutas que optan por este decorador fallan
    cerradas para rol 4 si no hay reglas compatibles o si hay patrones ambiguos.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        payload = getattr(g, "usuario_actual", None)
        if not payload or not payload.get("id"):
            return jsonify({"error": "Token de autenticación requerido."}), 401
        try:
            rol_id = int(payload.get("rol"))
        except (TypeError, ValueError):
            return jsonify({"error": "Rol no autorizado."}), 403
        if rol_id != 4:
            return f(*args, **kwargs)

        permitido = _resultado_permiso_interno_endpoint()
        if permitido is True:
            return f(*args, **kwargs)
        return _error_permiso_interno_endpoint(permitido)

    decorated_function._permiso_interno_dinamico = True
    return decorated_function


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
            # El flujo distribuidor → hijo se conserva intacto. Únicamente una
            # ruta marcada con requiere_permiso_interno puede continuar hasta
            # su validación aislada para el rol 4.
            try:
                es_usuario_interno = int(g.usuario_actual.get("rol")) == 4
            except (AttributeError, TypeError, ValueError):
                es_usuario_interno = False
            if es_usuario_interno:
                resultado = _resultado_permiso_interno_endpoint()
                if resultado is True:
                    return f(*args, **kwargs)
                if getattr(f, "_permiso_interno", None):
                    return f(*args, **kwargs)
                return _error_permiso_interno_endpoint(resultado)

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
            try:
                es_usuario_interno = int(g.usuario_actual.get("rol")) == 4
            except (AttributeError, TypeError, ValueError):
                es_usuario_interno = False
            if es_usuario_interno:
                resultado = _resultado_permiso_interno_endpoint()
                if resultado is True:
                    return f(*args, **kwargs)
                if getattr(f, "_permiso_interno", None):
                    return f(*args, **kwargs)
                return _error_permiso_interno_endpoint(resultado)
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
            if _resultado_permiso_interno_endpoint() is True:
                return f(*args, **kwargs)
            permitido, error = usuario_actual_tiene_capacidad(capacidad)
            if not permitido:
                return error
            return f(*args, **kwargs)
        return decorated_function
    return decorator
