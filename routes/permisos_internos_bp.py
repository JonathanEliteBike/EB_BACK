"""Endpoints administrativos de permisos internos."""

from flask import Blueprint, jsonify, request

from services.permisos_internos_service import PermisosInternosService
from utils.auth_decorators import requiere_autenticacion, requiere_rol


permisos_internos_bp = Blueprint(
    "permisos_internos", __name__, url_prefix="/api/permisos-internos"
)


@permisos_internos_bp.route("/usuarios", methods=["GET"])
@requiere_autenticacion
@requiere_rol(1)
def listar_usuarios_internos():
    return jsonify({"usuarios": PermisosInternosService.listar_usuarios_internos()}), 200


@permisos_internos_bp.route("/usuario/<int:usuario_id>", methods=["GET"])
@requiere_autenticacion
@requiere_rol(1)
def obtener_permisos_usuario(usuario_id):
    try:
        permisos = PermisosInternosService.obtener_permisos_usuario(usuario_id)
        area = PermisosInternosService.obtener_area_usuario(usuario_id)
        return jsonify({"permisos": permisos, "area": area}), 200
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@permisos_internos_bp.route("/asignar", methods=["POST"])
@requiere_autenticacion
@requiere_rol(1)
def asignar_permiso():
    data = request.get_json(silent=True) or {}
    usuario_id = data.get("usuario_id")
    modulo_id = data.get("modulo_id")
    accion_id = data.get("accion_id")
    if not all([usuario_id, modulo_id, accion_id]):
        return jsonify({"error": "usuario_id, modulo_id y accion_id son requeridos."}), 400

    try:
        return jsonify(
            PermisosInternosService.asignar_permiso(usuario_id, modulo_id, accion_id)
        ), 200
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@permisos_internos_bp.route("/revocar", methods=["DELETE"])
@requiere_autenticacion
@requiere_rol(1)
def revocar_permiso():
    data = request.get_json(silent=True) or {}
    usuario_id = data.get("usuario_id")
    modulo_id = data.get("modulo_id")
    accion_id = data.get("accion_id")
    if not all([usuario_id, modulo_id, accion_id]):
        return jsonify({"error": "usuario_id, modulo_id y accion_id son requeridos."}), 400

    try:
        return jsonify(
            PermisosInternosService.revocar_permiso(usuario_id, modulo_id, accion_id)
        ), 200
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@permisos_internos_bp.route("/areas", methods=["GET"])
@requiere_autenticacion
@requiere_rol(1)
def listar_areas():
    return jsonify({"areas": PermisosInternosService.listar_areas()}), 200
