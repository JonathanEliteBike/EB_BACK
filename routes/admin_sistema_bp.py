# routes/admin_sistema_bp.py

from flask import Blueprint, request, jsonify
from services.admin_sistema_service import AdminSistemaService
from services.permisos_modulos_service import PermisosModulosService
from utils.auth_decorators import requiere_autenticacion, requiere_rol

admin_sistema_bp = Blueprint('admin_sistema', __name__, url_prefix='/api/admin-sistema')

@admin_sistema_bp.route('/administradores', methods=['GET'])
@requiere_autenticacion
@requiere_rol(1)
def listar_administradores():
    """Obtiene el listado de Administradores Cliente, su estado y cupo de usuarios."""
    try:
        admins = AdminSistemaService.listar_administradores()
        return jsonify({"administradores": admins}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@admin_sistema_bp.route('/administradores/<int:admin_id>/permisos-delegables', methods=['GET'])
@requiere_autenticacion
@requiere_rol(1)
def obtener_permisos_delegables_administrador(admin_id):
    """Obtiene la bolsa delegable del Administrador Cliente seleccionado por rol 1."""
    try:
        permisos = AdminSistemaService.obtener_permisos_delegables_administrador(admin_id)
        return jsonify({"permisos_delegables": permisos}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@admin_sistema_bp.route('/usuarios/<int:usuario_id>/estado', methods=['PATCH'])
def cambiar_estado_usuario(usuario_id):
    """Activa o desactiva a cualquier usuario o Administrador Cliente."""
    try:
        data = request.get_json() or {}
        activo = data.get('activo')
        if activo is None:
            return jsonify({"error": "El campo 'activo' (1 o 0) es obligatorio."}), 400

        resultado = AdminSistemaService.cambiar_estado_usuario(usuario_id, int(activo))
        return jsonify(resultado), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@admin_sistema_bp.route('/administradores/<int:admin_id>/cupo', methods=['PUT'])
@requiere_autenticacion
@requiere_rol(1)
def actualizar_cupo(admin_id):
    """Ajusta el límite máximo de usuarios hijos (max_hijos) para un Administrador Cliente."""
    try:
        data = request.get_json() or {}
        max_hijos = data.get('max_hijos')
        if isinstance(max_hijos, bool) or not isinstance(max_hijos, int) or max_hijos < 0:
            return jsonify({"error": "El campo 'max_hijos' debe ser un entero mayor o igual a 0."}), 400

        resultado = AdminSistemaService.actualizar_limite_cupo(admin_id, max_hijos)
        return jsonify(resultado), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@admin_sistema_bp.route('/permisos-delegables/asignar', methods=['POST'])
@requiere_autenticacion
@requiere_rol(1)
def asignar_permiso_delegable():
    """Asigna un permiso a la bolsa delegable de un Administrador Cliente."""
    try:
        data = request.get_json() or {}
        admin_id = data.get('administrador_id')
        modulo_id = data.get('modulo_id')
        accion_id = data.get('accion_id')

        if not all([admin_id, modulo_id, accion_id]):
            return jsonify({"error": "Los campos administrador_id, modulo_id y accion_id son requeridos."}), 400

        resultado = AdminSistemaService.asignar_permiso_delegable(admin_id, modulo_id, accion_id)
        return jsonify(resultado), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@admin_sistema_bp.route('/permisos-delegables/revocar', methods=['DELETE'])
@requiere_autenticacion
@requiere_rol(1)
def revocar_permiso_delegable():
    """Retira un permiso de la bolsa delegable de un Administrador Cliente."""
    try:
        data = request.get_json() or {}
        admin_id = data.get('administrador_id')
        modulo_id = data.get('modulo_id')
        accion_id = data.get('accion_id')

        if not all([admin_id, modulo_id, accion_id]):
            return jsonify({"error": "Los campos administrador_id, modulo_id y accion_id son requeridos."}), 400

        resultado = AdminSistemaService.revocar_permiso_delegable(admin_id, modulo_id, accion_id)
        return jsonify(resultado), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@admin_sistema_bp.route('/administradores/<int:admin_id>/modulos-delegables', methods=['GET'])
@requiere_autenticacion
@requiere_rol(1)
def obtener_modulos_delegables_administrador_nuevo(admin_id):
    """Bolsa nueva por módulo, independiente de Ver/Crear/Editar/Eliminar."""
    try:
        return jsonify({"modulos": PermisosModulosService.obtener_modulos_delegables(admin_id)}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@admin_sistema_bp.route('/modulos-delegables/asignar', methods=['POST'])
@requiere_autenticacion
@requiere_rol(1)
def asignar_modulo_delegable():
    try:
        data = request.get_json() or {}
        admin_id, modulo_id = data.get('administrador_id'), data.get('modulo_id')
        if not all([admin_id, modulo_id]):
            return jsonify({"error": "administrador_id y modulo_id son requeridos."}), 400
        return jsonify(PermisosModulosService.asignar_modulo_administrador(admin_id, modulo_id)), 201
    except (ValueError, PermissionError) as e:
        return jsonify({"error": str(e)}), 400


@admin_sistema_bp.route('/modulos-delegables/revocar', methods=['DELETE'])
@requiere_autenticacion
@requiere_rol(1)
def revocar_modulo_delegable():
    try:
        data = request.get_json() or {}
        admin_id, modulo_id = data.get('administrador_id'), data.get('modulo_id')
        if not all([admin_id, modulo_id]):
            return jsonify({"error": "administrador_id y modulo_id son requeridos."}), 400
        return jsonify(PermisosModulosService.revocar_modulo_administrador(admin_id, modulo_id)), 200
    except (ValueError, PermissionError) as e:
        return jsonify({"error": str(e)}), 400


@admin_sistema_bp.route('/administradores/<int:admin_id>/capacidades-delegables', methods=['GET'])
@requiere_autenticacion
@requiere_rol(1)
def obtener_capacidades_delegables_administrador(admin_id):
    try:
        return jsonify({"capacidades": PermisosModulosService.obtener_capacidades_delegables(admin_id)}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@admin_sistema_bp.route('/capacidades-delegables/asignar', methods=['POST'])
@requiere_autenticacion
@requiere_rol(1)
def asignar_capacidad_delegable():
    try:
        data = request.get_json() or {}
        admin_id, capacidad = data.get('administrador_id'), data.get('capacidad')
        if not admin_id or not capacidad:
            return jsonify({"error": "administrador_id y capacidad son requeridos."}), 400
        return jsonify(PermisosModulosService.asignar_capacidad_administrador(admin_id, capacidad)), 201
    except (ValueError, PermissionError) as e:
        return jsonify({"error": str(e)}), 400


@admin_sistema_bp.route('/capacidades-delegables/revocar', methods=['DELETE'])
@requiere_autenticacion
@requiere_rol(1)
def revocar_capacidad_delegable():
    try:
        data = request.get_json() or {}
        admin_id, capacidad = data.get('administrador_id'), data.get('capacidad')
        if not admin_id or not capacidad:
            return jsonify({"error": "administrador_id y capacidad son requeridos."}), 400
        return jsonify(PermisosModulosService.revocar_capacidad_administrador(admin_id, capacidad)), 200
    except (ValueError, PermissionError) as e:
        return jsonify({"error": str(e)}), 400
