# routes/acciones_bp.py

from flask import Blueprint, request, jsonify
from services.acciones_service import AccionesService

acciones_bp = Blueprint('acciones', __name__, url_prefix='/api/acciones')

@acciones_bp.route('', methods=['GET'])
def listar_acciones():
    """Obtiene la lista de acciones base globales."""
    try:
        acciones = AccionesService.listar_acciones()
        return jsonify({"acciones": acciones}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@acciones_bp.route('', methods=['POST'])
def crear_accion():
    """Crea una nueva acción base (ej. Aprobar, Exportar)."""
    try:
        data = request.get_json() or {}
        if not data.get('nombre') or not data.get('identificador'):
            return jsonify({"error": "Los campos 'nombre' e 'identificador' son obligatorios."}), 400

        resultado = AccionesService.crear_accion(data)
        return jsonify(resultado), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@acciones_bp.route('/<int:accion_id>/estado', methods=['PUT'])
def cambiar_estado(accion_id):
    """Activa o desactiva una acción base."""
    try:
        data = request.get_json() or {}
        activo = data.get('activo')
        if activo is None:
            return jsonify({"error": "El campo 'activo' (1 o 0) es obligatorio."}), 400

        resultado = AccionesService.cambiar_estado_accion(accion_id, int(activo))
        return jsonify(resultado), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@acciones_bp.route('/<int:accion_id>', methods=['DELETE'])
def eliminar_accion(accion_id):
    """Elimina físicamente una acción base."""
    try:
        resultado = AccionesService.eliminar_accion(accion_id)
        return jsonify(resultado), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400