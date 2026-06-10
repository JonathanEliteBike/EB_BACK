from flask import Blueprint, jsonify, request
from db_conexion import obtener_conexion
import jwt
from datetime import date, datetime

from utils.jwt_utils import verificar_token
from functools import wraps

SECRET_KEY = "123456"

clientes_bp = Blueprint('clientes', __name__, url_prefix='')

@clientes_bp.route('/clientes', methods=['GET'])
def obtener_detalles_clientes():
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("""
                SELECT 
                    c.clave,
                    c.evac,
                    c.nombre_cliente,
                    c.nivel,
                    c.f_inicio,
                    c.f_fin
                FROM clientes c
                WHERE c.nombre_cliente NOT IN ('Alberto Garcia', 'Andre Padilla Goray', 'Andre Vittoria')
                ORDER BY 
                    CASE 
                        WHEN c.evac = 'A' THEN 1
                        WHEN c.evac = 'B' THEN 2
                        WHEN c.evac = 'GO' THEN 3
                        ELSE 4
                    END,
                    c.nombre_cliente ASC
        """)
        resultados = cursor.fetchall()
        
        # Convertir objetos date/datetime a strings en formato YYYY-MM-DD
        for cliente in resultados:
            if cliente['f_inicio'] and isinstance(cliente['f_inicio'], (datetime, date)):
                cliente['f_inicio'] = cliente['f_inicio'].strftime('%Y-%m-%d')
            if cliente['f_fin'] and isinstance(cliente['f_fin'], (datetime, date)):
                cliente['f_fin'] = cliente['f_fin'].strftime('%Y-%m-%d')
        
        return jsonify(resultados), 200
    except Exception as e:
        print("Error al obtener los detalles de los clientes:", str(e))
        return jsonify({"error": "Error en la consulta"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@clientes_bp.route('/clientes/nombres', methods=['GET'])
def obtener_nombres_clientes():
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT 
                c.id,
                COALESCE(g.nombre_grupo, c.clave) AS clave,
                c.nombre_cliente
            FROM clientes c
            LEFT JOIN grupo_clientes g ON c.id_grupo = g.id
            ORDER BY c.nombre_cliente
        """)
        resultados = cursor.fetchall()
        return jsonify([
            {"id": row["id"], "clave": row["clave"], "nombre_cliente": row["nombre_cliente"]}
            for row in resultados
        ]), 200
    except Exception as e:
        print("Error al obtener los nombres de los clientes:", str(e))
        return jsonify({"error": "Error en la consulta"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@clientes_bp.route('/clientes/por-grupo/<int:id_grupo>', methods=['GET'])
def obtener_clientes_por_grupo(id_grupo):
    """Devuelve los clientes que pertenecen a un grupo integral específico."""
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT c.id, c.clave, c.nombre_cliente
            FROM clientes c
            WHERE c.id_grupo = %s
            ORDER BY c.nombre_cliente
        """, (id_grupo,))
        resultados = cursor.fetchall()
        return jsonify(resultados), 200
    except Exception as e:
        print("Error al obtener clientes por grupo:", str(e))
        return jsonify({"error": "Error en la consulta"}), 500
    finally:
        if cursor: cursor.close()
        if conexion and conexion.is_connected(): conexion.close()

@clientes_bp.route('/clientes/buscar', methods=['POST'])
def buscar_cliente():
    data = request.get_json()
    valor = data.get('valor')

    if not valor:
        return jsonify({"error": "Falta el valor"}), 400

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        query = """
            SELECT id, clave, evac, nombre_cliente, nivel, f_inicio, f_fin
            FROM clientes
            WHERE clave = %s OR nombre_cliente LIKE %s
            LIMIT 1
        """
        cursor.execute(query, (valor, f'%{valor}%'))
        cliente = cursor.fetchone()

        if cliente:
            return jsonify(cliente), 200
        else:
            return jsonify({"mensaje": "Cliente no encontrado"}), 404
    except Exception as e:
        print("Error al buscar cliente:", str(e))
        return jsonify({"error": "Error al buscar cliente"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@clientes_bp.route('/clientes/agregar', methods=['POST'])
def agregar_cliente():
    data = request.get_json()

    clave = data.get('clave')
    evac = data.get('evac')  # Changed from zona to evac
    nombre_cliente = data.get('nombre_cliente')
    nivel = data.get('nivel')
    f_inicio = data.get('f_inicio')
    f_fin = data.get('f_fin')

    # Validar que todos los campos estén presentes
    if not all([clave, evac, nombre_cliente, nivel, f_inicio, f_fin]):
        return jsonify({"error": "Todos los campos son obligatorios"}), 400

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:
        # Validar que no exista la clave o el nombre ya registrados
        cursor.execute("SELECT id FROM clientes WHERE clave = %s OR nombre_cliente = %s", (clave, nombre_cliente))
        existente = cursor.fetchone()

        if existente:
            return jsonify({"error": "Ya existe un cliente con esa clave o nombre"}), 409

        # Insertar el nuevo cliente
        query = """
            INSERT INTO clientes (clave, evac, nombre_cliente, nivel, f_inicio, f_fin)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        cursor.execute(query, (clave, evac, nombre_cliente, nivel, f_inicio, f_fin))
        conexion.commit()

        return jsonify({"mensaje": "Cliente agregado exitosamente"}), 201
    except Exception as e:
        print("Error al agregar cliente:", str(e))
        return jsonify({"error": "Error al agregar cliente"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@clientes_bp.route('/clientes/editar/<int:id_cliente>', methods=['PUT'])
def editar_cliente(id_cliente):
    data = request.get_json()

    clave = data.get('clave')
    evac = data.get('evac')  # Changed from zona to evac
    nombre_cliente = data.get('nombre_cliente')
    nivel = data.get('nivel')
    f_inicio = data.get('f_inicio')
    f_fin = data.get('f_fin')

    if not all([clave, evac, nombre_cliente, nivel, f_inicio, f_fin]):
        return jsonify({"error": "Todos los campos son obligatorios"}), 400

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:
        # Verificar que el cliente exista
        cursor.execute("SELECT id FROM clientes WHERE id = %s", (id_cliente,))
        if cursor.fetchone() is None:
            return jsonify({"error": "Cliente no encontrado"}), 404

        # Actualizar
        query = """
            UPDATE clientes
            SET clave = %s,
                evac = %s,  # Changed zona to evac
                nombre_cliente = %s,
                nivel = %s,
                f_inicio = %s,
                f_fin = %s
            WHERE id = %s
        """
        cursor.execute(query, (clave, evac, nombre_cliente, nivel, f_inicio, f_fin, id_cliente))
        conexion.commit()

        return jsonify({"mensaje": "Cliente actualizado exitosamente"}), 200
    except Exception as e:
        print("Error al editar cliente:", str(e))
        return jsonify({"error": "Error al editar cliente"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@clientes_bp.route('/clientes/eliminar/<int:id_cliente>', methods=['DELETE'])
def eliminar_cliente(id_cliente):
    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:
        cursor.execute("SELECT id FROM clientes WHERE id = %s", (id_cliente,))
        if cursor.fetchone() is None:
            return jsonify({"error": "Cliente no encontrado"}), 404

        cursor.execute("DELETE FROM clientes WHERE id = %s", (id_cliente,))
        conexion.commit()

        return jsonify({"mensaje": "Cliente eliminado correctamente"}), 200
    except Exception as e:
        print("Error al eliminar cliente:", str(e))
        return jsonify({"error": "Error al eliminar cliente"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@clientes_bp.route('/clientes/nivel', methods=['GET'])
def obtener_nivel_cliente_actual():
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({"error": "Token no proporcionado"}), 401

    token = auth_header.split(' ')[1]

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=['HS256'])
        usuario_id = payload['id']
    except jwt.ExpiredSignatureError:
        return jsonify({"error": "Token expirado"}), 401
    except jwt.InvalidTokenError:
        return jsonify({"error": "Token inválido"}), 401

    try:
        # Obtener cliente_id del usuario
        cursor.execute("SELECT cliente_id FROM usuarios WHERE id = %s", (usuario_id,))
        usuario = cursor.fetchone()

        if not usuario or not usuario['cliente_id']:
            return jsonify({"error": "El usuario no tiene cliente asociado"}), 404

        cliente_id = usuario['cliente_id']

        # Obtener nivel y compromiso_scott del cliente
        cursor.execute("""
            SELECT c.nivel, n.compromiso_scott
            FROM clientes c
            JOIN niveles_distribuidor n ON c.nivel = n.nivel
            WHERE c.id = %s
        """, (cliente_id,))
        cliente = cursor.fetchone()

        if not cliente:
            return jsonify({"error": "Cliente no encontrado"}), 404

        return jsonify({
            "nivel": cliente['nivel'],
            "compromiso": cliente['compromiso_scott']
        }), 200

    except Exception as e:
        print("Error al obtener nivel del cliente:", str(e))
        return jsonify({"error": "Error en la consulta"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@clientes_bp.route('/clientes/info', methods=['GET'])
def obtener_info_cliente_actual():
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({"error": "Token no proporcionado"}), 401

    token = auth_header.split(' ')[1]

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=['HS256'])
        usuario_id = payload['id']
    except jwt.ExpiredSignatureError:
        return jsonify({"error": "Token expirado"}), 401
    except jwt.InvalidTokenError:
        return jsonify({"error": "Token inválido"}), 401

    try:
        # Obtener cliente_id del usuario
        cursor.execute("SELECT cliente_id FROM usuarios WHERE id = %s", (usuario_id,))
        usuario = cursor.fetchone()

        if not usuario or not usuario['cliente_id']:
            return jsonify({"error": "El usuario no tiene cliente asociado"}), 404

        cliente_id = usuario['cliente_id']

        # Obtener la información completa del cliente
        cursor.execute("""
            SELECT id, clave, zona, nombre_cliente, nivel, id_grupo
            FROM clientes
            WHERE id = %s
        """, (cliente_id,))
        cliente = cursor.fetchone()

        if not cliente:
            return jsonify({"error": "Cliente no encontrado"}), 404

        return jsonify(cliente), 200

    except Exception as e:
        print("Error al obtener la información del cliente:", str(e))
        return jsonify({"error": "Error en la consulta"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@clientes_bp.route('/clientes_multimarcas', methods=['GET'])
def obtener_clientes_multimarcas():
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("SELECT * FROM clientes_multimarcas")
        resultados = cursor.fetchall()
        return jsonify(resultados), 200
    except Exception as e:
        print("Error al obtener clientes multimarcas:", str(e))
        return jsonify({"error": "Error en la consulta"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@clientes_bp.route('/clientes_multimarcas_claves', methods=['GET'])
def obtener_clientes_multimarcas_claves():
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)
    
    clave = request.args.get('clave')  # Obtener parámetro de consulta 'clave'

    try:
        if clave:
            # Buscar cliente específico por clave
            cursor.execute(
                "SELECT id, clave, cliente_razon_social FROM clientes_multimarcas WHERE clave = %s", 
                (clave,)
            )
            resultado = cursor.fetchone()
            return jsonify(resultado) if resultado else jsonify({"error": "Cliente no encontrado"}), 404
        else:
            # Obtener todos los clientes
            cursor.execute("SELECT id, clave, cliente_razon_social FROM clientes_multimarcas")
            return jsonify(cursor.fetchall()), 200
            
    except Exception as e:
        print("Error al obtener clientes multimarcas:", str(e))
        return jsonify({"error": "Error en la consulta"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@clientes_bp.route('/clientes_multimarcas_buscar', methods=['GET'])
def buscar_cliente_multimarcas():
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)
    
    busqueda = request.args.get('q')  # Parámetro de búsqueda

    try:
        if busqueda:
            # Buscar por clave o razón social (insensible a mayúsculas)
            query = """
                SELECT id, clave, cliente_razon_social, evac 
                FROM clientes_multimarcas 
                WHERE clave LIKE %s OR cliente_razon_social LIKE %s
            """
            parametro_busqueda = f"%{busqueda}%"
            cursor.execute(query, (parametro_busqueda, parametro_busqueda))
            
            resultados = cursor.fetchall()
            return jsonify(resultados), 200
        else:
            return jsonify({"error": "Parámetro de búsqueda requerido"}), 400
            
    except Exception as e:
        print("Error al buscar cliente:", str(e))
        return jsonify({"error": "Error en la consulta"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()
            
@clientes_bp.route('/clientes_fechas', methods=['GET'])
def obtener_fechas_clientes():
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("""
                SELECT 
                    c.nombre_cliente,
                    c.f_inicio,
                    c.f_fin
                FROM clientes c
                WHERE c.nombre_cliente NOT IN ('Alberto Garcia', 'Andre Padilla Goray', 'Andre Vittoria')
                ORDER BY 
                    c.nombre_cliente ASC
        """)
        resultados = cursor.fetchall()
        
        # Convertir objetos date/datetime a strings en formato YYYY-MM-DD
        for cliente in resultados:
            if cliente['f_inicio'] and isinstance(cliente['f_inicio'], (datetime, date)):
                cliente['f_inicio'] = cliente['f_inicio'].strftime('%Y-%m-%d')
            if cliente['f_fin'] and isinstance(cliente['f_fin'], (datetime, date)):
                cliente['f_fin'] = cliente['f_fin'].strftime('%Y-%m-%d')
        
        return jsonify(resultados), 200
    except Exception as e:
        print("Error al obtener las fechas de los clientes:", str(e))
        return jsonify({"error": "Error en la consulta"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        
        if not token:
            return jsonify({'error': 'Token es requerido'}), 401
        
        try:
            # Remover 'Bearer ' si está presente
            if token.startswith('Bearer '):
                token = token[7:]
            
            # Usar tu función de verificación
            decoded_token = verificar_token(token)
            if not decoded_token:
                return jsonify({'error': 'Token inválido o expirado'}), 401
            
            request.cliente_data = decoded_token  # Almacenar datos del cliente
        except Exception as e:
            print("Error al procesar token:", str(e))
            return jsonify({'error': 'Error al procesar token'}), 401
        
        return f(*args, **kwargs)
    return decorated

@clientes_bp.route('/facturas-cliente', methods=['GET'])
@token_required
def obtener_facturas_cliente():
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cliente_data = getattr(request, 'cliente_data', None)
        if not cliente_data:
            return jsonify({"error": "Datos del cliente no encontrados"}), 400

        clave_cliente = cliente_data.get('clave')
        nombre_cliente = cliente_data.get('nombre_cliente')

        if not clave_cliente and not nombre_cliente:
            return jsonify({"error": "No se encontró información del cliente en el token"}), 400

        cursor.execute("SELECT f_inicio FROM clientes WHERE clave = %s", (clave_cliente,))
        cliente_info = cursor.fetchone()

        if not cliente_info or not cliente_info['f_inicio']:
            return jsonify({
                "success": True,
                "mensaje": "El cliente no tiene una fecha de inicio de temporada configurada.",
                "data": []
            }), 200
        
        fecha_inicio_temporada = cliente_info['f_inicio']
        
        query = """
            SELECT 
                id, numero_factura, referencia_interna, nombre_producto,
                contacto_referencia, contacto_nombre, fecha_factura,
                precio_unitario, cantidad, venta_total, marca,
                subcategoria, apparel, eride, evac,
                categoria_producto, estado_factura
            FROM monitor
            WHERE (contacto_referencia = %s OR contacto_nombre = %s)
            AND fecha_factura >= %s  -- <-- NUEVA CONDICIÓN
            AND numero_factura IS NOT NULL 
            AND numero_factura != '/'
            AND fecha_factura IS NOT NULL 
            AND fecha_factura != '0001-01-01 00:00:00'
            ORDER BY 
                CASE 
                    WHEN contacto_referencia = %s THEN 1
                    WHEN contacto_nombre = %s THEN 2
                    ELSE 3
                END,
                fecha_factura DESC
        """

        cursor.execute(query, (clave_cliente, nombre_cliente, fecha_inicio_temporada, clave_cliente, nombre_cliente))
        facturas = cursor.fetchall()

        for factura in facturas:
            if factura['fecha_factura'] and isinstance(factura['fecha_factura'], (datetime, date)):
                factura['fecha_factura'] = factura['fecha_factura'].strftime('%Y-%m-%d %H:%M:%S')

        return jsonify({
            "success": True,
            "cliente": {
                "clave": clave_cliente,
                "nombre": nombre_cliente
            },
            "total_facturas": len(facturas),
            "data": facturas
        }), 200

    except Exception as e:
        print("Error al obtener las facturas del cliente:", str(e))
        return jsonify({"error": "Error en la consulta de facturas"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@clientes_bp.route('/facturas-grupo/<int:id_grupo>', methods=['GET'])
@token_required
def obtener_facturas_grupo(id_grupo):
    conexion = None
    cursor = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)

        ## NUEVO: Consulta optimizada con JOIN para filtrar por fecha en una sola operación
        query_facturas = """
            SELECT 
                m.* -- Seleccionamos todas las columnas de la tabla monitor
            FROM 
                monitor m
            JOIN 
                clientes c ON m.contacto_referencia = c.clave -- Unimos con clientes por la clave
            WHERE 
                c.id_grupo = %s -- Filtramos por el ID del grupo
                AND m.fecha_factura >= c.f_inicio -- Condición: la fecha de factura debe ser mayor o igual a la f_inicio del cliente
                AND m.numero_factura IS NOT NULL 
                AND m.numero_factura != '/'
            ORDER BY 
                m.fecha_factura DESC
        """
        
        cursor.execute(query_facturas, (id_grupo,))
        facturas = cursor.fetchall()
        
        for factura in facturas:
            if factura.get('fecha_factura') and isinstance(factura['fecha_factura'], (datetime, date)):
                factura['fecha_factura'] = factura['fecha_factura'].strftime('%Y-%m-%d %H:%M:%S')

        return jsonify({"success": True, "data": facturas}), 200

    except Exception as e:
        print(f"Error al obtener facturas del grupo: {str(e)}")
        return jsonify({"error": "Error en la consulta de facturas del grupo"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()