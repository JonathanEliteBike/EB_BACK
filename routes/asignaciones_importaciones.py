import logging
from functools import wraps

from flask import Blueprint, request, jsonify

from db_conexion import obtener_conexion
from routes.clientes import token_required
from services import asignaciones_service as svc

asignaciones_bp = Blueprint("asignaciones_importaciones", __name__, url_prefix="/importaciones")


def _requiere_rol_importaciones(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        payload = getattr(request, "cliente_data", {}) or {}
        if payload.get("rol") not in (1, 3):
            return jsonify({
                "ok": False,
                "error": {"code": "NO_AUTORIZADO", "message": "No tienes permiso para acceder a este módulo"},
            }), 403
        return f(*args, **kwargs)
    return decorated


@asignaciones_bp.errorhandler(svc.AsignacionesError)
def _manejar_error_asignaciones(err):
    return jsonify({"ok": False, "error": {"code": err.code, "message": err.message}}), err.status


@asignaciones_bp.route("/asignaciones/inicializar-tablas", methods=["POST"])
@token_required
@_requiere_rol_importaciones
def inicializar_tablas():
    conn = obtener_conexion()
    if not conn:
        return jsonify({"ok": False, "error": {"code": "DB_NO_DISPONIBLE", "message": "Sin conexión a BD"}}), 500
    try:
        cursor = conn.cursor()
        for ddl in svc.TABLAS_SQL:
            cursor.execute(ddl)
        conn.commit()
        return jsonify({"ok": True, "mensaje": "Tablas de asignaciones creadas/verificadas"}), 201
    except Exception as e:
        logging.exception("Error creando tablas de asignaciones")
        return jsonify({"ok": False, "error": {"code": "DB_ERROR", "message": str(e)}}), 500
    finally:
        conn.close()
