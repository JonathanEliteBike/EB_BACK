"""Consulta del permiso propio: nunca se confía en un ID enviado por Angular."""

from flask import Blueprint, g, jsonify
from utils.inventario_auth import validar_acceso_inventario

inventario_acceso_bp = Blueprint(
    "inventario_acceso", __name__, url_prefix="/api/inventario/acceso"
)
inventario_acceso_bp.before_request(validar_acceso_inventario)


@inventario_acceso_bp.get("")
@inventario_acceso_bp.get("/")
def consultar_acceso():
    return jsonify({"autorizado": True, "usuario": g.inventario_usuario}), 200
