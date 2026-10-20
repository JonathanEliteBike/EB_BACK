from flask import Blueprint, request, jsonify
from db_conexion import obtener_conexion
from utils.seguridad import hash_password, verificar_password
from utils.jwt_utils import generar_token, verificar_token_con_gracia
import logging
import sqlite3
import os
import re
import random
from utils.email import enviar_correo_activacion
from services.permisos_internos_service import PermisosInternosService
from datetime import datetime, timedelta
import uuid

auth = Blueprint('auth', __name__, url_prefix='')

def campo_vacio(campo):
    return campo is None or str(campo).strip() == ''

@auth.route('/registro', methods=['POST'])
def registrar_usuario():
    # 1. VALIDACIONES SIN BASE DE DATOS (Hazlas primero, es gratis)
    data = request.get_json()
    if not data:
        return jsonify({"error": "No se proporcionaron datos"}), 400

    usuario    = data.get('usuario')
    contrasena = data.get('contrasena')
    nombre     = data.get('nombre')
    correo     = data.get('correo')
    rol        = data.get('rol', 'Usuario')
    rol_id_recibido = data.get('rol_id')
    cliente_id = data.get('cliente_id')
    area_id    = data.get('area_id')

    # Validaciones básicas de campos
    campos_requeridos = {'usuario': usuario, 'contrasena': contrasena, 'nombre': nombre, 'correo': correo}
    for campo, valor in campos_requeridos.items():
        if not valor or (isinstance(valor, str) and valor.strip() == ''):
            return jsonify({"error": f"El campo '{campo}' es obligatorio"}), 400

    if not isinstance(usuario, str) or not isinstance(contrasena, str):
        return jsonify({"error": "Usuario y contraseña cadenas de texto"}), 400

    if not re.match(r"^[a-zA-Z0-9_.-]{3,20}$", usuario):
        return jsonify({"error": "Usuario: 3-20 caracteres alfanuméricos"}), 400

    if not re.match(r"[^@]+@[^@]+\.[^@]+", correo):
        return jsonify({"error": "Correo inválido"}), 400

    if len(contrasena) < 6:
        return jsonify({"error": "Contraseña mín. 6 caracteres"}), 400

    roles_validos = {"Administrador": 1, "Usuario": 2, "Usuario Interno": 4}
    if rol not in roles_validos:
        return jsonify({"error": "Rol inválido"}), 400
    rol_id = roles_validos[rol]
    if rol_id_recibido is not None:
        try:
            if int(rol_id_recibido) != rol_id:
                return jsonify({"error": "El rol y rol_id no coinciden"}), 400
        except (TypeError, ValueError):
            return jsonify({"error": "rol_id inválido"}), 400

    # 2. AHORA SÍ, ABRIMOS LA BASE DE DATOS
    conexion = obtener_conexion()
    cursor = None

    try:
        cursor = conexion.cursor(dictionary=True)

        # Validar cliente_id (requiere BD)
        if cliente_id not in [None, "", "null"]:
            try:
                cliente_id = int(cliente_id)
                cursor.execute("SELECT id FROM clientes WHERE id = %s", (cliente_id,))
                if not cursor.fetchone():
                    return jsonify({"error": "El cliente_id no existe"}), 400
            except ValueError:
                return jsonify({"error": "cliente_id inválido"}), 400
        else:
            cliente_id = None

        if rol_id == 4:
            if area_id in [None, "", "null"]:
                return jsonify({"error": "El área principal es obligatoria para un Usuario Interno"}), 400
            try:
                area_id = int(area_id)
            except (TypeError, ValueError):
                return jsonify({"error": "area_id inválido"}), 400

            cursor.execute("SELECT id FROM areas WHERE id = %s AND activo = 1", (area_id,))
            if not cursor.fetchone():
                return jsonify({"error": "El área principal no existe o está inactiva"}), 400
        else:
            area_id = None

        # Validar duplicados (requiere BD)
        cursor.execute("SELECT id FROM usuarios WHERE usuario = %s", (usuario,))
        if cursor.fetchone():
            return jsonify({"error": "El usuario ya existe"}), 400

        cursor.execute("SELECT id FROM usuarios WHERE correo = %s", (correo,))
        if cursor.fetchone():
            return jsonify({"error": "El correo ya existe"}), 400
        
        # Insertar
        contrasena_hash = hash_password(contrasena)
        # Registrar usuario sin id_grupo (se obtiene desde clientes table via JOIN)
        cursor.execute(
            """INSERT INTO usuarios (usuario, contrasena, nombre, correo, rol_id, activo, cliente_id, area_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (usuario, contrasena_hash, nombre, correo, rol_id, True, cliente_id, area_id)
        )
        nuevo_id = cursor.lastrowid
        permisos_base_asignados = 0
        if rol_id == 4:
            permisos_base_asignados = PermisosInternosService.asignar_permisos_base_area(
                cursor, nuevo_id, area_id
            )
        conexion.commit()

        # Obtener resultado
        cursor.execute("""
            SELECT u.id, u.usuario, u.nombre, u.correo, r.nombre AS rol, u.activo,
                   c.nombre_cliente, c.id AS cliente_id, c.id_grupo, u.area_id,
                   a.nombre AS area_nombre
            FROM usuarios u
            JOIN roles r ON u.rol_id = r.id
            LEFT JOIN clientes c ON u.cliente_id = c.id
            LEFT JOIN areas a ON u.area_id = a.id
            WHERE u.id = %s
        """, (nuevo_id,))
        usuario_creado = cursor.fetchone()

        mensaje = "Registrado"
        if rol_id == 4 and permisos_base_asignados == 0:
            mensaje = "Registrado. El área no tiene permisos base configurados."
        return jsonify({
            "mensaje": mensaje,
            "usuario": usuario_creado,
            "permisos_base_asignados": permisos_base_asignados,
        }), 201

    except Exception as e:
        if conexion.is_connected(): conexion.rollback()
        return jsonify({"error": str(e)}), 500
    
    finally:
        # 3. CERRAMOS PASE LO QUE PASE
        if cursor: cursor.close()
        if conexion and conexion.is_connected(): conexion.close()


@auth.route('/registro-integral', methods=['POST'])
def registrar_usuario_integral():
    """
    Registra un usuario vinculado a un cliente específico dentro de un grupo integral.

    Flujo:
      1. El administrador elige el grupo integral (id_grupo) y el cliente dentro del grupo (cliente_id).
      2. Se valida que ese cliente_id pertenezca realmente al grupo indicado.
      3. Se crea el usuario con ese cliente_id → el token JWT incluirá id_grupo
         automáticamente (vía la JOIN en /login).

    Body JSON requerido:
      usuario, contrasena, nombre, correo, id_grupo (int), cliente_id (int)
    Body JSON opcional:
      rol  (default: "Usuario")
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No se proporcionaron datos"}), 400

    usuario    = data.get('usuario')
    contrasena = data.get('contrasena')
    nombre     = data.get('nombre')
    correo     = data.get('correo')
    rol        = data.get('rol', 'Usuario')
    id_grupo   = data.get('id_grupo')
    cliente_id = data.get('cliente_id')

    # ── Validaciones sin BD ──────────────────────────────────────────────────
    campos_req = {'usuario': usuario, 'contrasena': contrasena,
                  'nombre': nombre, 'correo': correo}
    for campo, valor in campos_req.items():
        if not valor or (isinstance(valor, str) and valor.strip() == ''):
            return jsonify({"error": f"El campo '{campo}' es obligatorio"}), 400

    if not re.match(r"^[a-zA-Z0-9_.-]{3,20}$", usuario):
        return jsonify({"error": "Usuario: 3-20 caracteres alfanuméricos"}), 400

    if not re.match(r"[^@]+@[^@]+\.[^@]+", correo):
        return jsonify({"error": "Correo inválido"}), 400

    if len(contrasena) < 6:
        return jsonify({"error": "Contraseña mín. 6 caracteres"}), 400

    roles_validos = {"Administrador": 1, "Usuario": 2}
    if rol not in roles_validos:
        return jsonify({"error": "Rol inválido"}), 400
    rol_id = roles_validos[rol]

    if not id_grupo:
        return jsonify({"error": "id_grupo es obligatorio"}), 400

    try:
        id_grupo = int(id_grupo)
        cliente_id = int(cliente_id) if cliente_id else None
    except (ValueError, TypeError):
        return jsonify({"error": "id_grupo y cliente_id deben ser enteros"}), 400

    if not cliente_id:
        return jsonify({"error": "cliente_id es obligatorio para registro integral"}), 400

    # ── Validaciones con BD ──────────────────────────────────────────────────
    conexion = obtener_conexion()
    cursor   = None
    try:
        cursor = conexion.cursor(dictionary=True)

        # 1. Verificar que el grupo exista
        cursor.execute("SELECT id, nombre_grupo FROM grupo_clientes WHERE id = %s", (id_grupo,))
        grupo = cursor.fetchone()
        if not grupo:
            return jsonify({"error": "El grupo integral no existe"}), 404

        # 2. Verificar que el cliente pertenezca al grupo (solo si se proporciona cliente_id)
        cliente = None
        if cliente_id:
            cursor.execute(
                "SELECT id, nombre_cliente, clave FROM clientes WHERE id = %s AND id_grupo = %s",
                (cliente_id, id_grupo)
            )
            cliente = cursor.fetchone()
            if not cliente:
                return jsonify({"error": "El cliente no pertenece al grupo indicado"}), 400

        # 3. Verificar duplicados
        cursor.execute("SELECT id FROM usuarios WHERE usuario = %s", (usuario,))
        if cursor.fetchone():
            return jsonify({"error": "El usuario ya existe"}), 400

        cursor.execute("SELECT id FROM usuarios WHERE correo = %s", (correo,))
        if cursor.fetchone():
            return jsonify({"error": "El correo ya está registrado"}), 400

        # 4. Insertar usuario
        contrasena_hash = hash_password(contrasena)
        cursor.execute(
            """INSERT INTO usuarios (usuario, contrasena, nombre, correo, rol_id, activo, cliente_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (usuario, contrasena_hash, nombre, correo, rol_id, True, cliente_id)
        )
        conexion.commit()
        nuevo_id = cursor.lastrowid

        # 5. Devolver el usuario creado con contexto de grupo
        cursor.execute("""
            SELECT u.id, u.usuario, u.nombre, u.correo, r.nombre AS rol, u.activo,
                   c.nombre_cliente, c.clave AS clave_cliente, c.id AS cliente_id,
                   c.id_grupo AS id_grupo
            FROM usuarios u
            JOIN roles r ON u.rol_id = r.id
            LEFT JOIN clientes c ON u.cliente_id = c.id
            WHERE u.id = %s
        """, (nuevo_id,))
        usuario_creado = cursor.fetchone()

        return jsonify({
            "mensaje": f"Usuario registrado en el grupo '{grupo['nombre_grupo']}'",
            "usuario": usuario_creado
        }), 201

    except Exception as e:
        if conexion.is_connected(): conexion.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conexion and conexion.is_connected(): conexion.close()


def _sqlite_login_connection():
    return obtener_conexion()


@auth.route('/login', methods=['POST'])
def login():
    print("Login called")
    data = request.get_json()
    if not data: return jsonify({"error": "Sin datos"}), 400
    usuario = data.get('usuario')
    contrasena = data.get('contrasena')
    if campo_vacio(usuario) or campo_vacio(contrasena): return jsonify({"error": "Faltan datos"}), 400

    print(f"Attempting login for user: {usuario}")
    conexion = _sqlite_login_connection()
    if not conexion:
        print("Failed to get SQLite connection")
        return jsonify({"error": "Error de conexión a base de datos"}), 500

    cursor = None
    try:
        cursor = conexion.cursor()
        cursor.execute("""
            SELECT u.id, u.usuario, u.contrasena, u.nombre, u.correo, u.rol_id,
                   u.cliente_id, u.flujo,
                   c.clave AS clave_cliente, c.nombre_cliente, c.id_grupo
            FROM usuarios u
            LEFT JOIN clientes c ON u.cliente_id = c.id
            WHERE u.usuario = %s AND u.activo = 1
        """, (usuario,))
        user = cursor.fetchone()

        if user and verificar_password(contrasena, user[2]):
            print("Password verified, generating token")
            user_dict = {
                'id': user[0],
                'usuario': user[1],
                'contrasena': user[2],
                'nombre': user[3],
                'correo': user[4],
                'rol_id': user[5],
                'cliente_id': user[6],
                'flujo': user[7],
                'clave_cliente': user[8],
                'nombre_cliente': user[9],
                'id_grupo': user[10]
            }

            token = generar_token(
                user_dict['id'],
                user_dict['rol_id'],
                user_dict['usuario'],
                user_dict['nombre'],
                user_dict['cliente_id'],
                user_dict['clave_cliente'],
                user_dict['nombre_cliente'],
                user_dict['id_grupo'],
                user_dict['flujo']
            )
            print("Token generated successfully")
            return jsonify({"token": token}), 200

        print("Invalid credentials")
        return jsonify({"error": "Credenciales incorrectas"}), 401
    except Exception as e:
        print(f"Error in login: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conexion: conexion.close()

@auth.route('/renovar_token', methods=['POST'])
def renovar_token():
    """Renueva el JWT del usuario si aún es válido o expiró dentro de los últimos 60 minutos."""
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return jsonify({'error': 'Token requerido'}), 401

    token = auth_header.split(' ', 1)[1]
    payload = verificar_token_con_gracia(token, minutos_gracia=60)
    if not payload:
        return jsonify({'error': 'Token inválido o muy expirado. Inicia sesión nuevamente.'}), 401

    conexion = None
    cursor = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)
        cursor.execute("SELECT activo FROM usuarios WHERE id = %s", (payload['id'],))
        user = cursor.fetchone()
        if not user or not user['activo']:
            return jsonify({'error': 'Usuario inactivo'}), 403

        nuevo_token = generar_token(
            payload['id'], payload['rol'], payload['usuario'],
            payload['nombre'], payload.get('cliente_id'), payload.get('clave'),
            payload.get('nombre_cliente'), payload.get('id_grupo'), payload.get('flujo', 0)
        )
        return jsonify({'token': nuevo_token}), 200
    except Exception as e:
        logging.error("Error renovando token: %s", e)
        return jsonify({'error': 'Error interno al renovar sesión'}), 500
    finally:
        if cursor: cursor.close()
        if conexion: conexion.close()


@auth.route('/logout', methods=['POST'])
def logout():
    # Validación previa
    data = request.get_json()
    if not data or 'token' not in data: return jsonify({"error": "Falta token"}), 400
    token = data['token']

    # Abrir conexión
    conexion = obtener_conexion()
    cursor = None
    try:
        cursor = conexion.cursor()
        cursor.execute("UPDATE usuarios SET token = NULL WHERE token = %s", (token,))
        conexion.commit()
        return jsonify({"mensaje": "Sesión cerrada"}), 200
    except Exception as e:
        conexion.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conexion and conexion.is_connected(): conexion.close()


@auth.route('/enviar_codigo_activacion', methods=['POST'])
def enviar_codigo_activacion():
    data = request.get_json()
    correo = data.get('correo')
    if campo_vacio(correo): return jsonify({"error": "Correo obligatorio"}), 400

    conexion = obtener_conexion()
    cursor = None
    try:
        cursor = conexion.cursor(dictionary=True)
        cursor.execute("SELECT id FROM usuarios WHERE correo = %s", (correo,))
        if not cursor.fetchone():
            return jsonify({"error": "Usuario no encontrado"}), 404 # Aquí estaba el problema antes, retornabas sin cerrar

        codigo = str(random.randint(100000, 999999))
        cursor.execute("UPDATE usuarios SET codigo_activacion = %s WHERE correo = %s", (codigo, correo))
        conexion.commit()
        
        enviar_correo_activacion(correo, codigo)
        return jsonify({"mensaje": "Código enviado"}), 200
    except Exception as e:
        conexion.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conexion and conexion.is_connected(): conexion.close()


@auth.route('/verificar_codigo', methods=['POST'])
def verificar_codigo():
    data = request.get_json()
    codigo = data.get('codigo')
    if campo_vacio(codigo): return jsonify({"error": "Código obligatorio"}), 400

    conexion = obtener_conexion()
    cursor = None
    try:
        cursor = conexion.cursor(dictionary=True)
        cursor.execute("SELECT id FROM usuarios WHERE codigo_activacion = %s AND activo = TRUE", (codigo,))
        usuario = cursor.fetchone()

        if not usuario:
            return jsonify({"error": "Código inválido"}), 404

        token_temp = str(uuid.uuid4())
        expiracion = datetime.utcnow() + timedelta(minutes=15)
        cursor.execute("UPDATE usuarios SET codigo_activacion = NULL, token_correo = %s, token_expiracion = %s WHERE id = %s", (token_temp, expiracion, usuario['id']))
        conexion.commit()

        return jsonify({"mensaje": "Verificado", "token": token_temp}), 200
    except Exception as e:
        conexion.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conexion and conexion.is_connected(): conexion.close()


@auth.route('/cambiar_contrasena', methods=['POST'])    
def cambiar_contrasena():
    data = request.get_json()
    token = data.get('token')
    nueva = data.get('nueva_contrasena')

    if campo_vacio(token) or campo_vacio(nueva): return jsonify({"error": "Faltan datos"}), 400
    if len(nueva) < 6: return jsonify({"error": "Contraseña corta"}), 400

    conexion = obtener_conexion()
    cursor = None
    try:
        cursor = conexion.cursor(dictionary=True)
        cursor.execute("SELECT id, token_expiracion FROM usuarios WHERE token_correo = %s", (token,))
        usuario = cursor.fetchone()

        if not usuario: return jsonify({"error": "Token inválido"}), 400
        if usuario['token_expiracion'] < datetime.utcnow(): return jsonify({"error": "Token expirado"}), 400

        hash_nueva = hash_password(nueva)
        cursor.execute("UPDATE usuarios SET contrasena = %s, token_correo = NULL, token_expiracion = NULL WHERE id = %s", (hash_nueva, usuario['id']))
        conexion.commit()

        return jsonify({"mensaje": "Contraseña actualizada"}), 200
    except Exception as e:
        conexion.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conexion and conexion.is_connected(): conexion.close()
