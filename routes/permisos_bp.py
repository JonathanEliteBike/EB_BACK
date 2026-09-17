# routes/permisos_bp.py

from flask import Blueprint, request, jsonify, g
from services.permisos_service import PermisosService
from services.permisos_modulos_service import PermisosModulosService
from services.politica_montos_service import PoliticaMontosService
from utils.auth_decorators import requiere_autenticacion, requiere_rol

permisos_bp = Blueprint('permisos', __name__, url_prefix='/api/permisos')


# Configuracion monetaria exclusiva de rol 2 sobre sus propios hijos rol 3.
# No interviene en los modulos, acciones ni capacidades heredadas.
@permisos_bp.route('/montos/usuario/<int:hijo_id>', methods=['GET'])
@requiere_autenticacion
@requiere_rol(2)
def obtener_configuracion_montos_hijo(hijo_id):
    try:
        return jsonify(PoliticaMontosService.obtener_configuracion_hijo(g.usuario_actual['id'], hijo_id)), 200
    except PermissionError as e:
        return jsonify({'error': str(e)}), 403


@permisos_bp.route('/montos/usuario/<int:hijo_id>/global', methods=['PUT'])
@requiere_autenticacion
@requiere_rol(2)
def actualizar_configuracion_montos_global(hijo_id):
    try:
        data = request.get_json(silent=True) or {}
        if 'ocultar_montos_global' not in data:
            return jsonify({'error': 'ocultar_montos_global es requerido.'}), 400
        return jsonify(PoliticaMontosService.actualizar_global_hijo(
            g.usuario_actual['id'], hijo_id, data['ocultar_montos_global']
        )), 200
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except PermissionError as e:
        return jsonify({'error': str(e)}), 403


@permisos_bp.route('/montos/usuario/<int:hijo_id>/ambito', methods=['PUT'])
@requiere_autenticacion
@requiere_rol(2)
def actualizar_configuracion_montos_ambito(hijo_id):
    try:
        data = request.get_json(silent=True) or {}
        if 'ambito_identificador' not in data or 'ocultar_montos' not in data:
            return jsonify({'error': 'ambito_identificador y ocultar_montos son requeridos.'}), 400
        return jsonify(PoliticaMontosService.actualizar_ambito_hijo(
            g.usuario_actual['id'], hijo_id,
            data['ambito_identificador'], data['ocultar_montos']
        )), 200
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except PermissionError as e:
        return jsonify({'error': str(e)}), 403


@permisos_bp.route('/montos/mis-politicas', methods=['GET'])
@requiere_autenticacion
def obtener_mis_politicas_montos():
    try:
        return jsonify(PoliticaMontosService.obtener_politica_propia(g.usuario_actual['id'])), 200
    except PermissionError as e:
        return jsonify({'error': str(e)}), 403

@permisos_bp.route('/mis-permisos', methods=['GET'])
@requiere_autenticacion
@requiere_rol(3)
def obtener_mis_permisos():
    """Obtiene los permisos efectivos del usuario hijo autenticado."""
    try:
        hijo_id = g.usuario_actual['id']
        permisos = PermisosService.obtener_permisos_propios_hijo(hijo_id)
        return jsonify({"permisos": permisos}), 200
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@permisos_bp.route('/delegables', methods=['GET'])
@requiere_autenticacion
@requiere_rol(2)
def obtener_delegables():
    """Obtiene los módulos y acciones que un administrador puede delegar a sus hijos."""
    try:
        padre_id = g.usuario_actual['id']
            
        permisos = PermisosService.obtener_permisos_delegables(padre_id)
        return jsonify({"permisos_delegables": permisos}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@permisos_bp.route('/usuario/<int:hijo_id>', methods=['GET'])
@requiere_autenticacion
@requiere_rol(2)
def obtener_permisos_usuario(hijo_id):
    """Obtiene la lista de permisos asignados actualmente a un usuario hijo."""
    try:
        padre_id = g.usuario_actual['id']
        permisos = PermisosService.obtener_permisos_usuario(padre_id, hijo_id)
        return jsonify({"permisos": permisos}), 200
    except Exception as e:
        msg = str(e)
        status_code = 403 if "Acceso denegado" in msg else 500
        return jsonify({"error": msg}), status_code


@permisos_bp.route('/asignar', methods=['POST'])
@requiere_autenticacion
@requiere_rol(2)
def asignar_permiso():
    """Asigna un permiso específico a un usuario hijo aplicando las reglas de seguridad."""
    try:
        data = request.get_json() or {}
        padre_id = g.usuario_actual['id']
        hijo_id = data.get('hijo_id')
        modulo_id = data.get('modulo_id')
        accion_id = data.get('accion_id')
        
        if not all([hijo_id, modulo_id, accion_id]):
            return jsonify({"error": "Faltan parámetros requeridos (hijo_id, modulo_id, accion_id)."}), 400
            
        resultado = PermisosService.asignar_permiso_hijo(padre_id, hijo_id, modulo_id, accion_id)
        return jsonify(resultado), 200
        
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 403


@permisos_bp.route('/revocar', methods=['DELETE'])
@requiere_autenticacion
@requiere_rol(2)
def revocar_permiso():
    """Revoca un permiso asignado a un usuario hijo."""
    try:
        data = request.get_json() or {}
        padre_id = g.usuario_actual['id']
        hijo_id = data.get('hijo_id')
        modulo_id = data.get('modulo_id')
        accion_id = data.get('accion_id')

        if not all([hijo_id, modulo_id, accion_id]):
            return jsonify({"error": "Faltan parámetros requeridos (hijo_id, modulo_id, accion_id)."}), 400

        resultado = PermisosService.revocar_permiso_hijo(padre_id, hijo_id, modulo_id, accion_id)
        return jsonify(resultado), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 403


# Modelo paralelo por módulo. Las rutas anteriores por acción permanecen sin cambios.
@permisos_bp.route('/mis-modulos', methods=['GET'])
@requiere_autenticacion
def obtener_mis_modulos():
    try:
        usuario_id = g.usuario_actual['id']
        rol_id = int(g.usuario_actual['rol'])
        if rol_id == 1:
            return jsonify({"modulos": [{"identificador": "*"}]}), 200
        if rol_id == 2:
            modulos = PermisosModulosService.obtener_modulos_propios_rol2(usuario_id)
        elif rol_id == 3:
            modulos = PermisosModulosService.obtener_modulos_propios_hijo(usuario_id)
        else:
            return jsonify({"error": "Rol no autorizado."}), 403
        return jsonify({"modulos": modulos}), 200
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403


@permisos_bp.route('/modulos-delegables', methods=['GET'])
@requiere_autenticacion
@requiere_rol(2)
def obtener_modulos_delegables():
    return jsonify({"modulos": PermisosModulosService.obtener_modulos_delegables(g.usuario_actual['id'])}), 200


@permisos_bp.route('/modulos/usuario/<int:hijo_id>', methods=['GET'])
@requiere_autenticacion
@requiere_rol(2)
def obtener_modulos_usuario(hijo_id):
    try:
        return jsonify({"modulos": PermisosModulosService.obtener_modulos_hijo(g.usuario_actual['id'], hijo_id)}), 200
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403


@permisos_bp.route('/modulos/asignar', methods=['POST'])
@requiere_autenticacion
@requiere_rol(2)
def asignar_modulo_hijo():
    try:
        data = request.get_json() or {}
        hijo_id, modulo_id = data.get('hijo_id'), data.get('modulo_id')
        if not all([hijo_id, modulo_id]):
            return jsonify({"error": "hijo_id y modulo_id son requeridos."}), 400
        return jsonify(PermisosModulosService.asignar_modulo_hijo(g.usuario_actual['id'], hijo_id, modulo_id)), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403


@permisos_bp.route('/modulos/revocar', methods=['DELETE'])
@requiere_autenticacion
@requiere_rol(2)
def revocar_modulo_hijo():
    try:
        data = request.get_json() or {}
        hijo_id, modulo_id = data.get('hijo_id'), data.get('modulo_id')
        if not all([hijo_id, modulo_id]):
            return jsonify({"error": "hijo_id y modulo_id son requeridos."}), 400
        return jsonify(PermisosModulosService.revocar_modulo_hijo(g.usuario_actual['id'], hijo_id, modulo_id)), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403


@permisos_bp.route('/mis-capacidades', methods=['GET'])
@requiere_autenticacion
@requiere_rol(3)
def obtener_mis_capacidades():
    try:
        return jsonify({"capacidades": PermisosModulosService.obtener_capacidades_propias_hijo(g.usuario_actual['id'])}), 200
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403


@permisos_bp.route('/capacidades-delegables', methods=['GET'])
@requiere_autenticacion
@requiere_rol(2)
def obtener_capacidades_delegables():
    return jsonify({"capacidades": PermisosModulosService.obtener_capacidades_delegables(g.usuario_actual['id'])}), 200


@permisos_bp.route('/capacidades/usuario/<int:hijo_id>', methods=['GET'])
@requiere_autenticacion
@requiere_rol(2)
def obtener_capacidades_usuario(hijo_id):
    try:
        return jsonify({"capacidades": PermisosModulosService.obtener_capacidades_hijo(g.usuario_actual['id'], hijo_id)}), 200
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403


@permisos_bp.route('/capacidades/asignar', methods=['POST'])
@requiere_autenticacion
@requiere_rol(2)
def asignar_capacidad_hijo():
    try:
        data = request.get_json() or {}
        hijo_id, capacidad = data.get('hijo_id'), data.get('capacidad')
        if not hijo_id or not capacidad:
            return jsonify({"error": "hijo_id y capacidad son requeridos."}), 400
        return jsonify(PermisosModulosService.asignar_capacidad_hijo(g.usuario_actual['id'], hijo_id, capacidad)), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403


@permisos_bp.route('/capacidades/revocar', methods=['DELETE'])
@requiere_autenticacion
@requiere_rol(2)
def revocar_capacidad_hijo():
    try:
        data = request.get_json() or {}
        hijo_id, capacidad = data.get('hijo_id'), data.get('capacidad')
        if not hijo_id or not capacidad:
            return jsonify({"error": "hijo_id y capacidad son requeridos."}), 400
        return jsonify(PermisosModulosService.revocar_capacidad_hijo(g.usuario_actual['id'], hijo_id, capacidad)), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
