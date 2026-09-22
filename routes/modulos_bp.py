# routes/modulos_bp.py

from flask import Blueprint, request, jsonify
from services.modulos_service import ModulosService

modulos_bp = Blueprint('modulos', __name__, url_prefix='/api/modulos')

@modulos_bp.route('', methods=['GET'])
def listar_modulos():
    """Obtiene el catálogo completo de módulos, submódulos y sus acciones."""
    try:
        modulos = ModulosService.listar_modulos()
        return jsonify({"modulos": modulos}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@modulos_bp.route('', methods=['POST'])
def crear_modulo():
    """Crea un módulo o submódulo y le asigna sus acciones base."""
    try:
        data = request.get_json() or {}
        if not data.get('nombre') or not data.get('identificador'):
            return jsonify({"error": "Los campos 'nombre' e 'identificador' son obligatorios."}), 400

        resultado = ModulosService.crear_modulo(data)
        return jsonify(resultado), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@modulos_bp.route('/<int:modulo_id>', methods=['PUT'])
def actualizar_modulo(modulo_id):
    """Actualiza un módulo y sus acciones asociadas."""
    try:
        data = request.get_json() or {}
        if not data.get('nombre') or not data.get('identificador'):
            return jsonify({"error": "Los campos 'nombre' e 'identificador' son obligatorios."}), 400

        resultado = ModulosService.actualizar_modulo(modulo_id, data)
        return jsonify(resultado), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@modulos_bp.route('/<int:modulo_id>/estado', methods=['PUT'])
def cambiar_estado(modulo_id):
    """Activa o desactiva un módulo."""
    try:
        data = request.get_json() or {}
        activo = data.get('activo')
        if activo is None:
            return jsonify({"error": "El campo 'activo' (1 o 0) es obligatorio."}), 400

        resultado = ModulosService.cambiar_estado_modulo(modulo_id, int(activo))
        return jsonify(resultado), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@modulos_bp.route('/<int:modulo_id>', methods=['DELETE'])
def eliminar_modulo(modulo_id):
    """Elimina físicamente un módulo."""
    try:
        resultado = ModulosService.eliminar_modulo(modulo_id)
        return jsonify(resultado), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400