# routes/usuarios_hijos_bp.py

from flask import Blueprint, request, jsonify, g
from services.usuarios_hijos_service import UsuariosHijosService, ErrorSecuenciaUsuario
from utils.auth_decorators import requiere_autenticacion, requiere_rol

usuarios_hijos_bp = Blueprint('usuarios_hijos', __name__, url_prefix='/api/usuarios-hijos')

@usuarios_hijos_bp.route('/cupo', methods=['GET'])
@requiere_autenticacion
@requiere_rol(2)
def obtener_cupo():
    """Obtiene el cupo actual de usuarios permitidos para el administrador."""
    try:
        padre_id = g.usuario_actual['id']

        cupo = UsuariosHijosService.obtener_cupo_padre(padre_id)
        return jsonify(cupo), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@usuarios_hijos_bp.route('', methods=['GET'])
@requiere_autenticacion
@requiere_rol(2)
def listar_hijos():
    """Lista todos los usuarios hijos pertenecientes al padre."""
    try:
        padre_id = g.usuario_actual['id']

        hijos = UsuariosHijosService.listar_hijos(padre_id)
        return jsonify({"usuarios": hijos}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@usuarios_hijos_bp.route('', methods=['POST'])
@requiere_autenticacion
@requiere_rol(2)
def crear_hijo():
    """Crea un usuario hijo verificando disponibilidad de cupo."""
    try:
        data = request.get_json() or {}
        padre_id = g.usuario_actual['id']

        campos_requeridos = ['nombre', 'correo', 'contrasena']
        for campo in campos_requeridos:
            if not data.get(campo):
                return jsonify({"error": f"El campo {campo} es obligatorio."}), 400

        resultado = UsuariosHijosService.crear_usuario_hijo(padre_id, data)
        return jsonify(resultado), 201

    except ValueError as e:
        return jsonify({"error": str(e)}), getattr(e, 'codigo_http', 400)
    except Exception:
        return jsonify({"error": "Error interno al crear el usuario hijo."}), 500


@usuarios_hijos_bp.route('/siguiente-usuario', methods=['GET'])
@requiere_autenticacion
@requiere_rol(2)
def siguiente_usuario_hijo():
    """Previsualización no reservada; el POST siempre vuelve a calcularla."""
    try:
        padre_id = g.usuario_actual['id']
        return jsonify({"usuario": UsuariosHijosService.siguiente_username_hijo(padre_id)}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), getattr(e, 'codigo_http', 400)
    except Exception:
        return jsonify({"error": "No fue posible calcular el siguiente usuario."}), 500


@usuarios_hijos_bp.route('/<int:hijo_id>/contrasena', methods=['PUT'])
@requiere_autenticacion
@requiere_rol(2)
def cambiar_contrasena(hijo_id):
    """Cambia la contraseña de un usuario hijo."""
    try:
        data = request.get_json() or {}
        padre_id = g.usuario_actual['id']
        nueva_contrasena = data.get('contrasena')

        if not nueva_contrasena:
            return jsonify({"error": "El campo contrasena es obligatorio."}), 400

        resultado = UsuariosHijosService.cambiar_contrasena_hijo(padre_id, hijo_id, nueva_contrasena)
        return jsonify(resultado), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@usuarios_hijos_bp.route('/<int:hijo_id>/estado', methods=['PUT'])
@requiere_autenticacion
@requiere_rol(2)
def cambiar_estado(hijo_id):
    """Activa o desactiva un usuario hijo."""
    try:
        data = request.get_json() or {}
        padre_id = g.usuario_actual['id']
        nuevo_estado = data.get('activo')

        if nuevo_estado is None:
            return jsonify({"error": "El campo activo (1 o 0) es obligatorio."}), 400

        resultado = UsuariosHijosService.cambiar_estado_hijo(padre_id, hijo_id, int(nuevo_estado))
        return jsonify(resultado), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    
@usuarios_hijos_bp.route('/<int:hijo_id>', methods=['DELETE'])
@requiere_autenticacion
@requiere_rol(2)
def eliminar_hijo(hijo_id):
    """Elimina físicamente un usuario hijo previa validación del padre."""
    try:
        padre_id = g.usuario_actual['id']

        resultado = UsuariosHijosService.eliminar_usuario_hijo(padre_id, hijo_id)
        return jsonify(resultado), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    
@usuarios_hijos_bp.route('/correo-padre/<int:_padre_id>', methods=['GET'])
@requiere_autenticacion
@requiere_rol(2)
def obtener_correo_padre(_padre_id):
    """Endpoint dedicado a devolver el correo del administrador."""
    try:
        # Se conserva el segmento de URL temporalmente por compatibilidad con Angular.
        # El valor recibido no participa en la autorización ni en la consulta.
        padre_id = g.usuario_actual['id']
        resultado = UsuariosHijosService.obtener_correo_padre(padre_id)
        return jsonify(resultado), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
