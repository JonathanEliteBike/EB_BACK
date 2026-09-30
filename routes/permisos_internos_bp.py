"""Endpoints administrativos de permisos internos."""

from flask import Blueprint, jsonify, request, g

from services.permisos_internos_service import PermisosInternosService
from utils.auth_decorators import (
    permite_acceso_interno_sin_regla,
    requiere_autenticacion,
    requiere_rol,
)


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


@permisos_internos_bp.route("/mis-permisos", methods=["GET"])
@requiere_autenticacion
@permite_acceso_interno_sin_regla
def obtener_mis_permisos_internos():
    """Devuelve la matriz propia del usuario interno autenticado."""
    try:
        permisos = PermisosInternosService.obtener_permisos_usuario(g.usuario_actual["id"])
        return jsonify({"permisos": permisos}), 200
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@permisos_internos_bp.route("/asignar", methods=["POST"])
@requiere_autenticacion
@requiere_rol(1)
def asignar_permiso():
    data = request.get_json(silent=True) or {}
    usuario_id = data.get("usuario_id")
    modulo_id = data.get("modulo_id")
    area_id = data.get("area_id")
    accion_id = data.get("accion_id")
    if not all([usuario_id, modulo_id, area_id, accion_id]):
        return jsonify({"error": "usuario_id, modulo_id, area_id y accion_id son requeridos."}), 400

    try:
        return jsonify(
            PermisosInternosService.asignar_permiso(usuario_id, modulo_id, area_id, accion_id)
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
    area_id = data.get("area_id")
    accion_id = data.get("accion_id")
    if not all([usuario_id, modulo_id, area_id, accion_id]):
        return jsonify({"error": "usuario_id, modulo_id, area_id y accion_id son requeridos."}), 400

    try:
        return jsonify(
            PermisosInternosService.revocar_permiso(usuario_id, modulo_id, area_id, accion_id)
        ), 200
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@permisos_internos_bp.route("/areas", methods=["GET"])
@requiere_autenticacion
@requiere_rol(1)
def listar_areas():
    return jsonify({"areas": PermisosInternosService.listar_areas()}), 200


@permisos_internos_bp.route("/areas", methods=["POST"])
@requiere_autenticacion
@requiere_rol(1)
def crear_area():
    try:
        return jsonify(PermisosInternosService.crear_area((request.get_json(silent=True) or {}).get("nombre"))), 201
    except ValueError as error:
        estado = 409 if str(error).startswith("Ya existe") else 400
        return jsonify({"error": str(error)}), estado


@permisos_internos_bp.route("/areas/<int:area_id>", methods=["PUT"])
@requiere_autenticacion
@requiere_rol(1)
def actualizar_area(area_id):
    try:
        return jsonify(PermisosInternosService.actualizar_area(
            area_id, (request.get_json(silent=True) or {}).get("nombre")
        )), 200
    except ValueError as error:
        estado = 409 if str(error).startswith("Ya existe") else 400
        return jsonify({"error": str(error)}), estado


@permisos_internos_bp.route("/areas/<int:area_id>/estado", methods=["PUT"])
@requiere_autenticacion
@requiere_rol(1)
def cambiar_estado_area(area_id):
    try:
        return jsonify(PermisosInternosService.cambiar_estado_area(
            area_id, (request.get_json(silent=True) or {}).get("activo")
        )), 200
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@permisos_internos_bp.route("/endpoints", methods=["GET"])
@requiere_autenticacion
@requiere_rol(1)
def listar_reglas_endpoint():
    modulo_id = request.args.get("modulo_id", type=int)
    return jsonify({"endpoints": PermisosInternosService.listar_reglas_endpoint(modulo_id)}), 200


@permisos_internos_bp.route("/endpoints", methods=["POST"])
@requiere_autenticacion
@requiere_rol(1)
def crear_regla_endpoint():
    try:
        return jsonify(PermisosInternosService.crear_regla_endpoint(request.get_json(silent=True) or {})), 201
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@permisos_internos_bp.route("/endpoints/<int:regla_id>", methods=["PUT"])
@requiere_autenticacion
@requiere_rol(1)
def actualizar_regla_endpoint(regla_id):
    try:
        return jsonify(PermisosInternosService.actualizar_regla_endpoint(regla_id, request.get_json(silent=True) or {})), 200
    except ValueError as error:
        return jsonify({"error": str(error)}), 400


@permisos_internos_bp.route("/endpoints/<int:regla_id>", methods=["DELETE"])
@requiere_autenticacion
@requiere_rol(1)
def eliminar_regla_endpoint(regla_id):
    try:
        return jsonify(PermisosInternosService.eliminar_regla_endpoint(regla_id)), 200
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
