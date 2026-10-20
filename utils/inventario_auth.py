"""Validación centralizada para los siete blueprints de Inventario IT."""

from flask import g, jsonify, request

from db_conexion import obtener_conexion
from utils.jwt_utils import verificar_token


def validar_acceso_inventario():
    # Flask puede gestionar preflight CORS sin exigir un JWT.
    if request.method == "OPTIONS":
        return None

    encabezado = request.headers.get("Authorization", "")
    partes = encabezado.split()
    if len(partes) != 2 or partes[0].lower() != "bearer":
        return jsonify({"error": "Autenticación requerida"}), 401

    datos = verificar_token(partes[1])
    if not datos:
        return jsonify({"error": "Sesión inválida o expirada"}), 401

    try:
        usuario_id = int(datos.get("id"))
        rol = int(datos.get("rol"))
    except (TypeError, ValueError):
        return jsonify({"error": "Token sin identidad válida"}), 401

    if usuario_id <= 0:
        return jsonify({"error": "Token sin identidad válida"}), 401

    conexion = None
    cursor = None
    try:
        conexion = obtener_conexion()
        if conexion is None:
            return jsonify({"error": "No se pudo verificar la sesión"}), 503
        cursor = conexion.cursor(dictionary=True)
        cursor.execute(
            "SELECT u.id, u.nombre, u.usuario FROM usuarios AS u "
            "INNER JOIN inventario_usuarios_autorizados AS p "
            "ON p.usuario_id = u.id AND p.habilitado = 1 "
            "WHERE u.id = %s AND u.rol_id = %s AND u.activo = 1",
            (usuario_id, rol),
        )
        cuenta = cursor.fetchone()
    except Exception:
        # Errores de conexión o migración ausente fallan cerrados: sin acceso.
        return jsonify({"error": "No se pudo verificar el permiso de Inventario IT"}), 503
    finally:
        if cursor is not None:
            cursor.close()
        if conexion is not None:
            conexion.close()

    if not cuenta:
        return jsonify({"error": "Cuenta no autorizada para Inventario IT"}), 403

    # Nunca confiar en un usuario_registro enviado por Angular.
    g.inventario_usuario = {
        "id": usuario_id,
        "nombre": cuenta.get("nombre") or cuenta.get("usuario") or str(usuario_id),
    }
    return None
