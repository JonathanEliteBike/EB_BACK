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


def requiere_permiso(modulo_identificador, accion_identificador):
    """
    Decorador para proteger rutas. Valida el permiso especifico
    o concede acceso total si el rol_id es 1 (Administrador del Sistema).
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Obtener el ID del usuario desde el header HTTP 'X-Usuario-Id'
            usuario_id = request.headers.get('X-Usuario-Id', type=int)
            
            if not usuario_id:
                return jsonify({"error": "Header 'X-Usuario-Id' requerido para autenticación."}), 401

            conn = obtener_conexion()
            cur = conn.cursor(dictionary=True)
            try:
                # 1. Obtener usuario y verificar estado
                cur.execute("SELECT rol_id FROM usuarios WHERE id = %s AND activo = 1", (usuario_id,))
                usuario = cur.fetchone()

                if not usuario:
                    return jsonify({"error": "Usuario no encontrado o inactivo."}), 401

                # 2. Bypass para Administrador del Sistema (rol_id = 1)
                if usuario['rol_id'] == 1:
                    return f(*args, **kwargs)

                # 3. Validar permiso especifico en usuario_permisos
                cur.execute("""
                    SELECT 1 
                    FROM usuario_permisos up
                    INNER JOIN modulos m ON up.modulo_id = m.id
                    INNER JOIN acciones a ON up.accion_id = a.id
                    WHERE up.usuario_id = %s 
                      AND m.identificador = %s 
                      AND a.identificador = %s
                      AND m.activo = 1 
                      AND a.activo = 1
                """, (usuario_id, modulo_identificador, accion_identificador))
                
                if not cur.fetchone():
                    return jsonify({
                        "error": f"Acceso denegado: Sin permisos de '{accion_identificador}' en '{modulo_identificador}'."
                    }), 403

                return f(*args, **kwargs)

            finally:
                cur.close()
                conn.close()

        return decorated_function
    return decorator
