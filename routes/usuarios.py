from flask import Blueprint, jsonify, request, g
from models.user_model import obtener_usuarios
import re
import logging
from utils.seguridad import hash_password, verificar_password, generar_token
from utils.odoo_utils import get_odoo_models, ODOO_DB, ODOO_PASSWORD, ODOO_COMPANY_ID
from db_conexion import obtener_conexion
from utils.auth_decorators import (
    requiere_autenticacion,
    requiere_rol,
    requiere_permiso_interno_dinamico,
)
from services.permisos_internos_service import PermisosInternosService

def campo_vacio(valor):
    return valor is None or (isinstance(valor, str) and valor.strip() == "")


def nombre_rol(rol_id):
    if rol_id == 1:
        return "Administrador"
    if rol_id == 4:
        return "Usuario Interno"
    return "Usuario"


def resolver_rol(data, rol_actual=None):
    roles_validos = {"Administrador": 1, "Usuario": 2, "Usuario Interno": 4}
    rol_recibido = data.get('rol')
    rol_id_recibido = data.get('rol_id')

    if rol_recibido is None and rol_id_recibido is None:
        return rol_actual, None

    if rol_recibido is not None:
        if not isinstance(rol_recibido, str) or rol_recibido.strip() not in roles_validos:
            return None, "Rol inválido"
        rol_id = roles_validos[rol_recibido.strip()]
    else:
        try:
            rol_id = int(rol_id_recibido)
        except (TypeError, ValueError):
            return None, "rol_id inválido"
        if rol_id not in roles_validos.values():
            return None, "Rol inválido"

    if rol_id_recibido is not None:
        try:
            if int(rol_id_recibido) != rol_id:
                return None, "El rol y rol_id no coinciden"
        except (TypeError, ValueError):
            return None, "rol_id inválido"

    return rol_id, None

usuarios_bp = Blueprint('usuarios', __name__, url_prefix='/usuarios')

@usuarios_bp.route('/para-monitor', methods=['GET'])
@requiere_autenticacion
@requiere_permiso_interno_dinamico
def usuarios_para_monitor():
    """Devuelve los clientes dados de alta correctamente en el monitor (tienen evac,
    nivel y f_inicio configurados) junto con su grupo. Los registros importados
    automáticamente desde Odoo que solo tienen clave y nombre se excluyen."""
    conexion = None
    cursor = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)

        # Solo clientes con datos completos (dados de alta manualmente).
        # Los auto-importados de Odoo tienen evac, nivel y f_inicio en NULL.
        cursor.execute("""
            SELECT
                c.id AS id_cliente,
                u.id AS id_usuario,
                COALESCE(u.nombre, c.nombre_cliente) AS nombre,
                u.usuario,
                u.rol_id,
                u.activo,
                c.clave,
                c.id_grupo AS id_grupo,
                g.nombre_grupo,
                CASE WHEN fp.clave_cliente IS NOT NULL THEN 1 ELSE 0 END AS tiene_proyeccion
            FROM clientes c
            LEFT JOIN usuarios u ON u.cliente_id = c.id
            LEFT JOIN grupo_clientes g ON c.id_grupo = g.id
            LEFT JOIN (SELECT DISTINCT clave_cliente FROM forecast_proyecciones) fp ON fp.clave_cliente = c.clave
            WHERE c.evac IS NOT NULL AND c.evac != ''
              AND c.nivel IS NOT NULL AND c.nivel != ''
              AND c.f_inicio IS NOT NULL

            UNION

            SELECT
                NULL AS id_cliente,
                u.id AS id_usuario,
                u.nombre,
                u.usuario,
                u.rol_id,
                u.activo,
                NULL AS clave,
                NULL AS id_grupo,
                NULL AS nombre_grupo,
                0 AS tiene_proyeccion
            FROM usuarios u
            WHERE u.cliente_id IS NULL

            ORDER BY nombre
        """)
        
        filas = cursor.fetchall()
        resultado = []
        
        for f in filas:
            # Manejamos el rol de forma segura para clientes sin usuario
            if f["rol_id"] == 1:
                rol = "Administrador"
            elif f["rol_id"] is not None:
                rol = "Usuario"
            else:
                rol = "Sin Usuario" # Identificador visual útil para tu monitor

            resultado.append({
                "id": f["id_usuario"],
                "id_cliente": f["id_cliente"],
                "nombre": f["nombre"],
                "usuario": f["usuario"],
                "rol": rol,
                "activo": bool(f["activo"]) if f["activo"] is not None else False,
                "clave": f["clave"],
                "id_grupo": f["id_grupo"],
                "nombre_grupo": f["nombre_grupo"],
                "tiene_proyeccion": bool(f["tiene_proyeccion"]),
            })
            
        return jsonify(resultado), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500
        
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@usuarios_bp.route('/sync-odoo', methods=['POST'])
def sync_clientes_desde_odoo():
    """
    Verifica qué clientes del monitor están enlazados con Odoo por clave (ref).
    NO agrega ni modifica clientes. Solo reporta el estado del enlace.
    Para agregar un cliente nuevo: registrarlo manualmente en el monitor
    con la clave que tenga asignada en Odoo; el siguiente sync lo detectará.
    Retorna { enlazados, sin_match, total_monitor }.
    """
    try:
        uid, models, err = get_odoo_models()
        if not uid:
            return jsonify({'error': 'No se pudo conectar a Odoo', 'detail': err}), 500

        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)

        # Clientes registrados en el monitor
        cursor.execute("SELECT clave FROM clientes WHERE clave IS NOT NULL AND clave != ''")
        claves_monitor = [r['clave'].strip().upper() for r in cursor.fetchall()]
        cursor.close()
        conexion.close()

        if not claves_monitor:
            return jsonify({'enlazados': 0, 'sin_match': 0, 'total_monitor': 0}), 200

        # Buscar en Odoo solo los partners cuya ref coincida con alguna clave del monitor
        partners = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD,
            'res.partner', 'search_read',
            [[['ref', 'in', claves_monitor]]],
            {'fields': ['ref'], 'limit': 0})

        refs_en_odoo = {(p.get('ref') or '').strip().upper() for p in partners}

        enlazados  = sum(1 for c in claves_monitor if c in refs_en_odoo)
        sin_match  = len(claves_monitor) - enlazados

        logging.info('sync-odoo: %d/%d clientes enlazados con Odoo', enlazados, len(claves_monitor))
        return jsonify({
            'enlazados':     enlazados,
            'sin_match':     sin_match,
            'total_monitor': len(claves_monitor),
        }), 200

    except Exception as e:
        logging.exception('sync_clientes_desde_odoo error')
        return jsonify({'error': str(e)}), 500


@usuarios_bp.route('', methods=['GET'])
def listar_usuarios():
    conexion = None
    cursor = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)

        consulta = """
        SELECT 
            u.id, u.nombre, u.correo, u.usuario, u.contrasena, 
            u.activo, u.rol_id, u.cliente_id, c.nombre_cliente, u.area_id,
            a.nombre AS area_nombre
        FROM 
            usuarios u
        LEFT JOIN 
            clientes c ON u.cliente_id = c.id
        LEFT JOIN
            areas a ON u.area_id = a.id
        """
        cursor.execute(consulta)
        usuarios = cursor.fetchall()
        usuarios_filtrados = []

        for u in usuarios:
            usuario_filtrado = {
                "id": u["id"],
                "nombre": u["nombre"],
                "correo": u["correo"],
                "usuario": u["usuario"],
                # "contrasena": u["contrasena"],  # opcional ocultar
                "activo": u["activo"],
                "rol": nombre_rol(u["rol_id"]),
                "cliente_id": u.get("cliente_id"),
                "cliente_nombre": u["nombre_cliente"],
                "area_id": u.get("area_id"),
                "area_nombre": u.get("area_nombre")
            }
            usuarios_filtrados.append(usuario_filtrado)

        return jsonify(usuarios_filtrados), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()
    
def obtener_usuarios():
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)

        consulta = """
        SELECT 
            u.id, u.nombre, u.correo, u.usuario, u.contrasena, 
            u.activo, u.rol_id, u.cliente_id, c.nombre_cliente
        FROM 
            usuarios u
        LEFT JOIN 
            clientes c ON u.cliente_id = c.id
        """
        cursor.execute(consulta)
        usuarios = cursor.fetchall()

        cursor.close()
        conexion.close()

        return usuarios
    except Exception as e:
        print(f"Error al obtener usuarios: {e}")
        return []
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@usuarios_bp.route('/<int:usuario_id>', methods=['PUT'])
def actualizar_usuario(usuario_id):
    data = request.get_json()

    if not data:
        return jsonify({"error": "No se proporcionaron datos"}), 400

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        # Obtener usuario existente
        cursor.execute("SELECT * FROM usuarios WHERE id = %s", (usuario_id,))
        usuario_existente = cursor.fetchone()

        if not usuario_existente:
            return jsonify({"error": "Usuario no encontrado"}), 404

        campos_actualizar = {}
        errores = []
        reemplazar_permisos_area = False
        asignar_permisos_base = False
        limpiar_permisos_internos = False
        permisos_base_asignados = 0

        # Validación y actualización del nombre
        if 'nombre' in data:
            nombre = data['nombre'].strip() if data['nombre'] else ""
            if campo_vacio(nombre):
                errores.append("El nombre es obligatorio")
            else:
                if nombre != usuario_existente['nombre']:
                    cursor.execute("SELECT id FROM usuarios WHERE nombre = %s AND id != %s", (nombre, usuario_id))
                    if cursor.fetchone():
                        errores.append("El nombre ya está en uso")
                    else:
                        campos_actualizar['nombre'] = nombre

        # Validación y actualización del correo
        if 'correo' in data:
            correo = data['correo'].strip() if data['correo'] else ""
            if campo_vacio(correo):
                errores.append("El correo es obligatorio")
            elif not re.match(r"[^@]+@[^@]+\.[^@]+", correo):
                errores.append("El correo electrónico no es válido")
            else:
                if correo != usuario_existente['correo']:
                    cursor.execute("SELECT id FROM usuarios WHERE correo = %s AND id != %s", (correo, usuario_id))
                    if cursor.fetchone():
                        errores.append("El correo electrónico ya está en uso")
                    else:
                        campos_actualizar['correo'] = correo

        # Validación y actualización del usuario
        if 'usuario' in data:
            usuario = data['usuario'].strip() if data['usuario'] else ""
            if campo_vacio(usuario):
                errores.append("El nombre de usuario es obligatorio")
            elif not re.match(r"^[a-zA-Z0-9_.-]{3,20}$", usuario):
                errores.append("El nombre de usuario debe tener entre 3 y 20 caracteres alfanuméricos")
            else:
                if usuario != usuario_existente['usuario']:
                    cursor.execute("SELECT id FROM usuarios WHERE usuario = %s AND id != %s", (usuario, usuario_id))
                    if cursor.fetchone():
                        errores.append("El nombre de usuario ya está en uso")
                    else:
                        campos_actualizar['usuario'] = usuario

        # El rol 4 tiene un área principal activa; los demás roles no la conservan.
        rol_id, error_rol = resolver_rol(data, usuario_existente['rol_id'])
        if error_rol:
            errores.append(error_rol)
        elif rol_id != usuario_existente['rol_id']:
            campos_actualizar['rol_id'] = rol_id

        if not error_rol:
            if rol_id == 4:
                area_id = data.get('area_id', usuario_existente.get('area_id'))
                if area_id in [None, "", "null"]:
                    errores.append("El área principal es obligatoria para un Usuario Interno")
                else:
                    try:
                        area_id = int(area_id)
                        cursor.execute("SELECT id FROM areas WHERE id = %s AND activo = 1", (area_id,))
                        if not cursor.fetchone():
                            errores.append("El área principal no existe o está inactiva")
                        elif area_id != usuario_existente.get('area_id'):
                            cambio_area_usuario_interno = usuario_existente['rol_id'] == 4
                            if cambio_area_usuario_interno and data.get('confirmar_cambio_area') is not True:
                                errores.append(
                                    "Confirma el cambio de área para reemplazar los permisos internos actuales."
                                )
                            else:
                                # También al volver al rol interno se descarta
                                # cualquier matriz histórica antes de sembrar
                                # la base del área seleccionada.
                                reemplazar_permisos_area = True
                                asignar_permisos_base = True
                            campos_actualizar['area_id'] = area_id
                    except (TypeError, ValueError):
                        errores.append("area_id inválido")
            else:
                if usuario_existente.get('area_id') is not None:
                    campos_actualizar['area_id'] = None
                if usuario_existente['rol_id'] == 4:
                    limpiar_permisos_internos = True

        # Validación y actualización de la contraseña
        if 'contrasena' in data:
            contrasena = data['contrasena']
            if contrasena and isinstance(contrasena, str):
                if len(contrasena) < 6:
                    errores.append("La contraseña debe tener al menos 6 caracteres")
                else:
                    # Siempre actualizar la contraseña si se proporciona y es válida
                    campos_actualizar['contrasena'] = hash_password(contrasena)
            else:
                errores.append("La contraseña debe ser una cadena de texto no vacía")

        # Validación y actualización del cliente_id (opcional)
        if 'cliente_id' in data:
            cliente_id = data['cliente_id']

            if cliente_id in [None, "", "null"]:
                campos_actualizar['cliente_id'] = None
            else:
                try:
                    cliente_id = int(cliente_id)
                    cursor.execute("SELECT id FROM clientes WHERE id = %s", (cliente_id,))
                    if not cursor.fetchone():
                        errores.append("El cliente_id proporcionado no existe")
                    else:
                        campos_actualizar['cliente_id'] = cliente_id
                except ValueError:
                    errores.append("El cliente_id debe ser un número entero válido")

        # Si hay errores, retornarlos todos juntos
        if errores:
            return jsonify({"errores": errores}), 400

        # Si no hay campos para actualizar ni permisos que sembrar.
        if not campos_actualizar and not asignar_permisos_base:
            return jsonify({"mensaje": "No se detectaron cambios para actualizar"}), 200

        # Construir y ejecutar la consulta de actualización
        set_clause = ', '.join([f"{key} = %s" for key in campos_actualizar.keys()])
        valores = list(campos_actualizar.values())
        valores.append(usuario_id)

        if campos_actualizar:
            query = f"UPDATE usuarios SET {set_clause} WHERE id = %s"
            cursor.execute(query, valores)
        if limpiar_permisos_internos:
            cursor.execute(
                "DELETE FROM usuario_permisos_internos WHERE usuario_id = %s",
                (usuario_id,),
            )
        if asignar_permisos_base:
            permisos_base_asignados = PermisosInternosService.asignar_permisos_base_area(
                cursor, usuario_id, campos_actualizar['area_id'], reemplazar_permisos_area
            )
        conexion.commit()

        # Obtener los nuevos valores para emitir el evento
        campos_emitir = {
            "id": usuario_id,
            "nombre": campos_actualizar.get('nombre', usuario_existente['nombre']),
            "correo": campos_actualizar.get('correo', usuario_existente['correo']),
            "usuario": campos_actualizar.get('usuario', usuario_existente['usuario']),
            "rol": nombre_rol(campos_actualizar.get('rol_id', usuario_existente['rol_id'])),
            "cliente_id": campos_actualizar.get('cliente_id', usuario_existente.get('cliente_id')),
            "area_id": campos_actualizar.get('area_id', usuario_existente.get('area_id')),
        }

        mensaje = "Usuario actualizado con éxito"
        if asignar_permisos_base and permisos_base_asignados == 0:
            mensaje += ". El área no tiene permisos base configurados."
        return jsonify({
            "mensaje": mensaje,
            "campos_actualizados": list(campos_actualizar.keys()),
            "permisos_base_asignados": permisos_base_asignados,
        }), 200

    except Exception as e:
        conexion.rollback()
        return jsonify({"error": f"Error al actualizar el usuario: {str(e)}"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()
    
@usuarios_bp.route('/<int:usuario_id>', methods=['DELETE'])
@requiere_autenticacion
@requiere_rol(1)
def eliminar_usuario(usuario_id):
    conexion = obtener_conexion()
    if not conexion:
        return jsonify({"error": "No fue posible eliminar el usuario."}), 500
    cursor = conexion.cursor(dictionary=True)

    try:
        # Validar primero el objetivo y las reglas de negocio, sin modificar datos.
        cursor.execute("SELECT id, usuario, rol_id FROM usuarios WHERE id = %s", (usuario_id,))
        usuario = cursor.fetchone()

        if not usuario:
            return jsonify({"error": "Usuario no encontrado."}), 404

        # Un administrador autenticado nunca puede invalidar la sesión actual
        # eliminando su propia cuenta.
        if usuario_id == g.usuario_actual['id']:
            return jsonify({
                "error": "No puedes eliminar tu propia cuenta de administrador."
            }), 409

        # 2. Validación: No permitir eliminar el último administrador
        if usuario['rol_id'] == 1:  # Si es administrador
            cursor.execute("""
                SELECT COUNT(*) as total_admins 
                FROM usuarios 
                WHERE rol_id = 1 AND id != %s
            """, (usuario_id,))
            
            otros_admins = cursor.fetchone()['total_admins']
            
            if otros_admins == 0:
                return jsonify({
                    "error": "No se puede eliminar al último administrador."
                }), 400

        # Las solicitudes de retroactivo son historial financiero. No se borran
        # como efecto colateral de eliminar un usuario.
        cursor.execute(
            "SELECT COUNT(*) AS total FROM solicitud_retroactivo_venta WHERE id_usuario = %s",
            (usuario_id,),
        )
        if cursor.fetchone()['total'] > 0:
            return jsonify({
                "error": "El usuario tiene solicitudes de retroactivo asociadas y no puede eliminarse porque existe información histórica relacionada."
            }), 409

        # No se eliminan ni reasignan hijos automáticamente: un distribuidor no
        # puede dejar usuarios hijo huérfanos.
        if usuario['rol_id'] == 2:
            cursor.execute(
                "SELECT COUNT(*) AS total FROM jerarquia_usuarios WHERE padre_id = %s",
                (usuario_id,),
            )
            if cursor.fetchone()['total'] > 0:
                return jsonify({
                    "error": "El distribuidor tiene usuarios hijos asociados. Elimine o reasigne sus usuarios hijos antes de eliminarlo."
                }), 409

        # obtener_conexion() trabaja con autocommit desactivado. Las consultas
        # de validación anteriores ya abrieron la transacción implícita; volver
        # a llamar start_transaction() provoca "Transaction already in progress".
        # Los DELETE siguientes, commit y rollback permanecen en esa misma
        # transacción atómica.

        # Dependencias individuales (modelo nuevo y compatibilidad antigua).
        cursor.execute("DELETE FROM usuario_capacidades WHERE usuario_id = %s", (usuario_id,))
        cursor.execute("DELETE FROM usuario_modulos WHERE usuario_id = %s", (usuario_id,))
        cursor.execute("DELETE FROM usuario_permisos WHERE usuario_id = %s", (usuario_id,))
        cursor.execute("DELETE FROM configuracion_montos_usuario_ambito WHERE usuario_id = %s", (usuario_id,))
        cursor.execute("DELETE FROM configuracion_montos_usuario WHERE usuario_id = %s", (usuario_id,))

        # Bolsa delegable y límite del distribuidor. En otro rol son operaciones
        # inocuas que también limpian posibles residuos inconsistentes.
        cursor.execute("DELETE FROM permisos_delegables_capacidades WHERE administrador_id = %s", (usuario_id,))
        cursor.execute("DELETE FROM permisos_delegables_modulos WHERE administrador_id = %s", (usuario_id,))
        cursor.execute("DELETE FROM permisos_delegables WHERE administrador_id = %s", (usuario_id,))
        cursor.execute("DELETE FROM limites_usuario WHERE padre_id = %s", (usuario_id,))

        # Rol 3: borra su relación como hijo. Rol 2: el prechequeo anterior
        # garantiza que no se eliminará una relación que deje hijos huérfanos.
        cursor.execute("DELETE FROM jerarquia_usuarios WHERE hijo_id = %s", (usuario_id,))
        cursor.execute("DELETE FROM jerarquia_usuarios WHERE padre_id = %s", (usuario_id,))

        # Esta relación usa usuarios.usuario (no usuarios.id), pero su FK también
        # impide la eliminación física si quedan historiales asociados.
        cursor.execute("DELETE FROM historial_caratulas WHERE usuario_envio = %s", (usuario['usuario'],))

        # Eliminación física final, después de todas las relaciones dependientes.
        cursor.execute("DELETE FROM usuarios WHERE id = %s", (usuario_id,))
        if cursor.rowcount != 1:
            raise RuntimeError("No se pudo eliminar el usuario objetivo.")
        conexion.commit()

        return jsonify({
            "mensaje": "Usuario eliminado permanentemente de la base de datos",
        }), 200

    except Exception as e:
        conexion.rollback()
        logging.exception("Error al eliminar usuario id=%s", usuario_id)
        # Defensa ante una dependencia futura no incluida en la limpieza. El
        # detalle SQL se conserva en el log, nunca se expone al frontend.
        if getattr(e, 'errno', None) == 1451:
            return jsonify({
                "error": "El usuario tiene información relacionada y no puede eliminarse."
            }), 409
        return jsonify({
            "error": "No fue posible eliminar el usuario. Intenta nuevamente o contacta al administrador."
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()
