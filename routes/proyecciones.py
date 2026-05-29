from flask import Blueprint, jsonify
from db_conexion import obtener_conexion
from flask import request
import pymysql as mysql_connector_compat
import jwt
import json
from werkzeug.utils import secure_filename
import os
from datetime import datetime
from utils.tiempo import ahora_mx, ahora_str
pd = None
PANDAS_OK = False

def _get_pandas():
    global pd, PANDAS_OK
    if pd is not None:
        return pd
    try:
        import pandas as _pd
        pd = _pd
        PANDAS_OK = True
    except Exception:
        pd = None
        PANDAS_OK = False
    return pd
import traceback
try:
    import numpy as np
except Exception:
    np = None  # type: ignore

SECRET_KEY = "123456"

proyecciones_bp = Blueprint('proyecciones', __name__, url_prefix='')

@proyecciones_bp.route('/proyecciones', methods=['GET'])
def listar_proyecciones():
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("SELECT * FROM proyecciones_ventas ORDER BY id")
        resultados = cursor.fetchall()

        if resultados:
            return jsonify(resultados), 200
        else:
            return jsonify({"mensaje": "No hay proyecciones registradas"}), 404
    except Exception as e:
        print("Error al obtener proyecciones:", str(e))
        return jsonify({"error": "Error al obtener proyecciones"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@proyecciones_bp.route('/proyecciones-limpias', methods=['GET'])
def listar_proyecciones_limpias():
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT 
                pv.id,
                pv.referencia,
                pv.clave_factura,
                pv.clave_6_digitos,
                pv.ean,
                pv.clave_odoo,
                pv.descripcion,
                pv.modelo,
                pv.spec,

                -- Precios
                pv.precio_elite_plus_sin_iva,
                pv.precio_elite_sin_iva,
                pv.precio_partner_sin_iva,
                pv.precio_distribuidor_sin_iva,
                pv.precio_publico_sin_iva,
                pv.precio_publico_con_iva_my26,
                pv.precio_elite_plus_con_iva,
                pv.precio_elite_con_iva,
                pv.precio_partner_con_iva,
                pv.precio_distribuidor_con_iva,
                pv.precio_publico_con_iva,

                -- Quincenas
                pv.q1_sep_2025,
                pv.q2_sep_2025,
                pv.q1_oct_2025,
                pv.q2_oct_2025,
                pv.q1_nov_2025,
                pv.q2_nov_2025,
                pv.q1_dic_2025,
                pv.q2_dic_2025,
                pv.q1_mar_2026,
                pv.q2_mar_2026,
                pv.q1_abr_2026,
                pv.q2_abr_2026,
                pv.q1_may_2026,
                pv.q2_may_2026,

                -- Totales
                pv.orden_total_cant,
                pv.orden_total_importe,

                -- Disponibilidad (opcional)
                dp.q1_sep_2025 as disp_q1_sep_2025,
                dp.q2_sep_2025 as disp_q2_sep_2025,
                dp.q1_oct_2025 as disp_q1_oct_2025,
                dp.q2_oct_2025 as disp_q2_oct_2025,
                dp.q1_nov_2025 as disp_q1_nov_2025,
                dp.q2_nov_2025 as disp_q2_nov_2025,
                dp.q1_dic_2025 as disp_q1_dic_2025,
                dp.q2_dic_2025 as disp_q2_dic_2025,
                dp.q1_mar_2026 as disp_q1_mar_2026,
                dp.q2_mar_2026 as disp_q2_mar_2026,
                dp.q1_abr_2026 as disp_q1_abr_2026,
                dp.q2_abr_2026 as disp_q2_abr_2026,
                dp.q1_may_2026 as disp_q1_may_2026,
                dp.q2_may_2026 as disp_q2_may_2026

            FROM proyecciones_ventas pv
            LEFT JOIN disponibilidad_proyeccion dp ON pv.id_disponibilidad = dp.id
            ORDER BY pv.id
        """)
        resultados = cursor.fetchall()
        return jsonify(resultados), 200 if resultados else 404

    except Exception as e:
        print("Error al obtener proyecciones limpias:", str(e))
        return jsonify({"error": "Error al obtener proyecciones limpias"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@proyecciones_bp.route('/proyecciones/agregar', methods=['POST'])
def agregar_proyecciones_cliente():
    data = request.get_json()
    auth_header = request.headers.get('Authorization')

    if not auth_header:
        return jsonify({"error": "No se proporcionó token"}), 401

    try:
        token = auth_header.split(" ")[1]
        decoded = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        id_usuario = decoded.get("id")
    except Exception as e:
        print("Error al decodificar token:", str(e))
        return jsonify({"error": "Token inválido"}), 401

    if not id_usuario:
        return jsonify({"error": "No se proporcionó id_usuario en headers"}), 400

    if not isinstance(data, list):
        return jsonify({"error": "Se esperaba una lista de proyecciones"}), 400

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        # Obtener el cliente asociado al usuario
        cursor.execute("SELECT cliente_id FROM usuarios WHERE id = %s", (id_usuario,))
        cliente = cursor.fetchone()
        if not cliente or not cliente['cliente_id']:
            return jsonify({"error": "Cliente no encontrado para este usuario"}), 404
        id_cliente = cliente['cliente_id']

        # Obtener el nivel del cliente
        cursor.execute("SELECT nivel FROM clientes WHERE id = %s", (id_cliente,))
        cliente_info = cursor.fetchone()
        nivel_cliente = cliente_info['nivel'] if cliente_info else None

        # Obtener folio único para esta transacción
        folio = f"FOL-{ahora_str('%Y%m%d%H%M%S')}-{id_cliente}"
        total_proyeccion = 0

        for proyeccion in data:
            id_proyeccion = proyeccion.get('id_proyeccion')

            # Obtener el precio según el nivel del cliente
            cursor.execute("""
                SELECT 
                    precio_elite_plus_con_iva,
                    precio_elite_con_iva,
                    precio_partner_con_iva,
                    precio_distribuidor_con_iva,
                    precio_publico_con_iva
                FROM proyecciones_ventas 
                WHERE id = %s
            """, (id_proyeccion,))
            precios = cursor.fetchone()
            
            # Determinar el precio aplicado según el nivel
            precio_aplicado = 0
            if nivel_cliente == 'Partner Elite Plus':
                precio_aplicado = precios['precio_elite_plus_con_iva']
            elif nivel_cliente == 'Partner Elite':
                precio_aplicado = precios['precio_elite_con_iva']
            elif nivel_cliente == 'Partner':
                precio_aplicado = precios['precio_partner_con_iva']
            elif nivel_cliente == 'Distribuidor':
                precio_aplicado = precios['precio_distribuidor_con_iva']
            else:
                precio_aplicado = precios['precio_publico_con_iva']

            cantidades = {
                'q1_sep_2025': proyeccion.get('q1_sep_2025', 0),
                'q2_sep_2025': proyeccion.get('q2_sep_2025', 0),
                'q1_oct_2025': proyeccion.get('q1_oct_2025', 0),
                'q2_oct_2025': proyeccion.get('q2_oct_2025', 0),
                'q1_nov_2025': proyeccion.get('q1_nov_2025', 0),
                'q2_nov_2025': proyeccion.get('q2_nov_2025', 0),
                'q1_dic_2025': proyeccion.get('q1_dic_2025', 0),
                'q2_dic_2025': proyeccion.get('q2_dic_2025', 0),
                'q1_mar_2026': proyeccion.get('q1_mar_2026', 0),
                'q2_mar_2026': proyeccion.get('q2_mar_2026', 0),
                'q1_abr_2026': proyeccion.get('q1_abr_2026', 0),
                'q2_abr_2026': proyeccion.get('q2_abr_2026', 0),
                'q1_may_2026': proyeccion.get('q1_may_2026', 0),
                'q2_may_2026': proyeccion.get('q2_may_2026', 0)
            }

            # Calcular subtotal para esta proyección
            cantidad_total = sum(cantidades.values())
            subtotal = cantidad_total * precio_aplicado
            total_proyeccion += subtotal

            # Eliminar registro previo del mismo cliente+producto para hacer upsert
            cursor.execute("""
                DELETE FROM proyecciones_cliente
                WHERE id_cliente = %s AND id_proyeccion = %s
            """, (id_cliente, id_proyeccion))

            cursor.execute("""
                INSERT INTO proyecciones_cliente (
                    id_cliente, id_proyeccion, precio_aplicado, folio,
                    q1_sep_2025, q2_sep_2025, q1_oct_2025, q2_oct_2025,
                    q1_nov_2025, q2_nov_2025, q1_dic_2025, q2_dic_2025,
                    q1_mar_2026, q2_mar_2026, q1_abr_2026, q2_abr_2026,
                    q1_may_2026, q2_may_2026
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                id_cliente, id_proyeccion, precio_aplicado, folio,
                cantidades['q1_sep_2025'], cantidades['q2_sep_2025'],
                cantidades['q1_oct_2025'], cantidades['q2_oct_2025'],
                cantidades['q1_nov_2025'], cantidades['q2_nov_2025'],
                cantidades['q1_dic_2025'], cantidades['q2_dic_2025'],
                cantidades['q1_mar_2026'], cantidades['q2_mar_2026'],
                cantidades['q1_abr_2026'], cantidades['q2_abr_2026'],
                cantidades['q1_may_2026'], cantidades['q2_may_2026']
            ))

        conexion.commit()
        return jsonify({
            "mensaje": "Proyecciones registradas correctamente",
            "folio": folio,
            "total_proyeccion": total_proyeccion,
            "total_bicicletas": sum(sum(p.get(q, 0) for q in [
                'q1_sep_2025', 'q2_sep_2025', 
                'q1_oct_2025', 'q2_oct_2025',
                'q1_nov_2025', 'q2_nov_2025',
                'q1_dic_2025', 'q2_dic_2025',
                'q1_mar_2026', 'q2_mar_2026',
                'q1_abr_2026', 'q2_abr_2026',
                'q1_may_2026', 'q2_may_2026'
            ]) for p in data)
        }), 201

    except mysql_connector_compat.Error as err:
        print("Error al insertar proyecciones:", str(err))
        conexion.rollback()
        return jsonify({"error": "Error al insertar proyecciones"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@proyecciones_bp.route('/proyecciones/historial', methods=['GET'])
def historial_proyecciones_cliente():
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return jsonify({"error": "No se proporcionó token"}), 401

    try:
        token = auth_header.split(" ")[1]
        decoded = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        id_usuario = decoded.get("id")
    except Exception as e:
        print("Error al decodificar token:", str(e))
        return jsonify({"error": "Token inválido"}), 401

    try:
        # Buscar el ID del cliente asociado al usuario autenticado
        cursor.execute("SELECT cliente_id FROM usuarios WHERE id = %s", (id_usuario,))
        cliente = cursor.fetchone()

        if not cliente or not cliente['cliente_id']:
            return jsonify({"error": "Cliente no encontrado"}), 404

        id_cliente = cliente['cliente_id']

        # Buscar historial de proyecciones del cliente
        cursor.execute("""
            SELECT 
                pc.id,
                pc.id_cliente,
                pc.id_proyeccion,
                pc.fecha_registro,
                pc.q1_sep_2025,
                pc.q2_sep_2025,
                pc.q1_oct_2025,
                pc.q2_oct_2025,
                pc.q1_nov_2025,
                pc.q2_nov_2025,
                pc.q1_dic_2025,
                pc.q2_dic_2025,
                pc.q1_mar_2026,
                pc.q2_mar_2026,
                pc.q1_abr_2026,
                pc.q2_abr_2026,
                pc.q1_may_2026,
                pc.q2_may_2026,
                pc.precio_aplicado,
                pv.id AS id_producto,
                pv.referencia,
                pv.clave_factura,
                pv.clave_6_digitos,
                pv.ean,
                pv.clave_odoo,
                pv.descripcion, 
                pv.modelo,
                pv.spec,
                pv.precio_elite_plus_sin_iva,
                pv.precio_elite_sin_iva,
                pv.precio_partner_sin_iva,
                pv.precio_distribuidor_sin_iva,
                pv.precio_publico_sin_iva,
                pv.precio_publico_con_iva_my26,
                pv.precio_elite_plus_con_iva,
                pv.precio_elite_con_iva,
                pv.precio_partner_con_iva,
                pv.precio_distribuidor_con_iva,
                pv.precio_publico_con_iva,
                pv.q1_sep_2025 AS producto_q1_sep_2025,
                pv.q2_sep_2025 AS producto_q2_sep_2025,
                pv.q1_oct_2025 AS producto_q1_oct_2025,
                pv.q2_oct_2025 AS producto_q2_oct_2025,
                pv.q1_nov_2025 AS producto_q1_nov_2025,
                pv.q2_nov_2025 AS producto_q2_nov_2025,
                pv.q1_dic_2025 AS producto_q1_dic_2025,
                pv.q2_dic_2025 AS producto_q2_dic_2025,
                pv.q1_mar_2026 AS producto_q1_mar_2026,
                pv.q2_mar_2026 AS producto_q2_mar_2026,
                pv.q1_abr_2026 AS producto_q1_abr_2026,
                pv.q2_abr_2026 AS producto_q2_abr_2026,
                pv.q1_may_2026 AS producto_q1_may_2026,
                pv.q2_may_2026 AS producto_q2_may_2026,
                pv.orden_total_cant,
                pv.orden_total_importe,
                pv.id_disponibilidad
            FROM proyecciones_cliente pc
            JOIN proyecciones_ventas pv ON pc.id_proyeccion = pv.id
            WHERE pc.id_cliente = %s
            ORDER BY pc.fecha_registro DESC
        """, (id_cliente,))
        proyecciones = cursor.fetchall()

        if not proyecciones:
            return jsonify({"mensaje": "Este cliente no tiene historial"}), 404

        return jsonify(proyecciones), 200

    except Exception as e:
        print("Error al obtener historial:", str(e))
        return jsonify({"error": "Error al obtener historial"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@proyecciones_bp.route('/proyecciones/detalles/<int:id_proyeccion>', methods=['GET'])
def detalles_proyeccion(id_proyeccion):
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        # Debug: Verificar el ID recibido
        print(f"Recibido ID Proyección: {id_proyeccion} (Tipo: {type(id_proyeccion)})")
        
        # Consulta SQL modificada con parámetros correctos
        query = """
            SELECT 
                pv.id,
                pv.clave_factura,
                pv.clave_6_digitos,
                pv.clave_odoo,
                pv.descripcion,
                pv.modelo,
                pv.spec,
                pv.ean,
                pv.referencia,
                pv.precio_elite_plus_sin_iva,
                pv.precio_elite_sin_iva,
                pv.precio_partner_sin_iva,
                pv.precio_distribuidor_sin_iva,
                pv.precio_publico_sin_iva,
                pv.precio_elite_plus_con_iva,
                pv.precio_elite_con_iva,
                pv.precio_partner_con_iva,
                pv.precio_distribuidor_con_iva,
                pv.precio_publico_con_iva,
                pv.precio_publico_con_iva_my26,
                pv.q1_sep_2025,
                pv.q2_sep_2025,
                pv.q1_oct_2025,
                pv.q2_oct_2025,
                pv.q1_nov_2025,
                pv.q2_nov_2025,
                pv.q1_dic_2025,
                pv.q2_dic_2025,
                pv.q1_mar_2026,
                pv.q2_mar_2026,
                pv.q1_abr_2026,
                pv.q2_abr_2026,
                pv.q1_may_2026,
                pv.q2_may_2026,
                pv.orden_total_cant,
                pv.orden_total_importe,
                JSON_ARRAYAGG(JSON_OBJECT(
                    'nombre_cliente', c.nombre_cliente,
                    'fecha_registro', DATE_FORMAT(pc.fecha_registro, '%%Y-%%m-%%d %%H:%%i:%%s'),
                    'precio_aplicado', pc.precio_aplicado,
                    'folio', pc.folio,
                    'q1_sep_2025', pc.q1_sep_2025,
                    'q2_sep_2025', pc.q2_sep_2025,
                    'q1_oct_2025', pc.q1_oct_2025,
                    'q2_oct_2025', pc.q2_oct_2025,
                    'q1_nov_2025', pc.q1_nov_2025,
                    'q2_nov_2025', pc.q2_nov_2025,
                    'q1_dic_2025', pc.q1_dic_2025,
                    'q2_dic_2025', pc.q2_dic_2025,
                    'q1_mar_2026', pc.q1_mar_2026,
                    'q2_mar_2026', pc.q2_mar_2026,
                    'q1_abr_2026', pc.q1_abr_2026,
                    'q2_abr_2026', pc.q2_abr_2026,
                    'q1_may_2026', pc.q1_may_2026,
                    'q2_may_2026', pc.q2_may_2026
                )) AS historial_clientes
            FROM proyecciones_ventas pv
            LEFT JOIN proyecciones_cliente pc ON pv.id = pc.id_proyeccion
            LEFT JOIN clientes c ON pc.id_cliente = c.id
            WHERE pv.id = %s
            GROUP BY pv.id
        """
        
        # Ejecutar con parámetros como tupla
        cursor.execute(query, (id_proyeccion,))
        
        resultado = cursor.fetchone()
        if not resultado:
            return jsonify({"mensaje": "Proyección no encontrada"}), 404

        # Parsear el historial de clientes
        if resultado.get("historial_clientes"):
            try:
                resultado["historial_clientes"] = json.loads(resultado["historial_clientes"])
                resultado["historial_clientes"] = [h for h in resultado["historial_clientes"] if h.get('nombre_cliente')]
            except Exception as e:
                print("Error al parsear historial_clientes:", str(e))
                resultado["historial_clientes"] = []
        else:
            resultado["historial_clientes"] = []

        return jsonify(resultado), 200

    except Exception as e:
        print("Error completo al obtener detalles:", str(e))
        return jsonify({"error": "Error al obtener detalles de proyección", "detalles": str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

# Disponibilidades
@proyecciones_bp.route('/disponibilidades', methods=['GET'])
def listar_disponibilidades():
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("SELECT * FROM disponibilidad_proyeccion ORDER BY id")
        resultados = cursor.fetchall()
        return jsonify(resultados), 200
    except Exception as e:
        print("Error al obtener disponibilidades:", str(e))
        return jsonify({"error": "Error al obtener disponibilidades"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@proyecciones_bp.route('/proyecciones/buscar/<int:id>', methods=['GET'])
def buscar_proyeccion_por_id(id):
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("SELECT * FROM proyecciones_ventas WHERE id = %s", (id,))
        resultado = cursor.fetchone()

        if resultado:
            return jsonify(resultado), 200
        else:
            return jsonify({"mensaje": "Proyección no encontrada"}), 404
    except Exception as e:
        print("Error al buscar proyección por ID:", str(e))
        return jsonify({"error": "Error al buscar proyección"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@proyecciones_bp.route('/proyecciones/nueva', methods=['POST'])
def agregar_proyeccion():
    data = request.get_json()

    campos_obligatorios = [
        'referencia', 'clave_factura', 'clave_6_digitos', 'ean',
        'clave_odoo', 'descripcion', 'modelo', 'spec', 'id_disponibilidad',
        'precio_distribuidor_sin_iva', 'precio_elite_plus_sin_iva',
        'precio_elite_sin_iva', 'precio_partner_sin_iva',
        'precio_publico_sin_iva'
    ]

    for campo in campos_obligatorios:
        if campo not in data:
            return jsonify({"error": f"El campo '{campo}' es obligatorio"}), 400

    try:
        # Calcular precios con IVA (16%)
        precio_distribuidor_con_iva = round(float(data['precio_distribuidor_sin_iva']) * 1.16, 2)
        precio_elite_plus_con_iva = round(float(data['precio_elite_plus_sin_iva']) * 1.16, 2)
        precio_elite_con_iva = round(float(data['precio_elite_sin_iva']) * 1.16, 2)
        precio_partner_con_iva = round(float(data['precio_partner_sin_iva']) * 1.16, 2)
        precio_publico_con_iva = round(float(data['precio_publico_sin_iva']) * 1.16, 2)

        conexion = obtener_conexion()
        cursor = conexion.cursor()

        cursor.execute("""
            INSERT INTO proyecciones_ventas (
                referencia, clave_factura, clave_6_digitos, ean, clave_odoo,
                descripcion, modelo, spec,
                precio_distribuidor_sin_iva, precio_distribuidor_con_iva,
                precio_elite_plus_sin_iva, precio_elite_plus_con_iva,
                precio_elite_sin_iva, precio_elite_con_iva,
                precio_partner_sin_iva, precio_partner_con_iva,
                precio_publico_sin_iva, precio_publico_con_iva, precio_publico_con_iva_my26,
                id_disponibilidad
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            data['referencia'],
            data['clave_factura'],
            data['clave_6_digitos'],
            data['ean'],
            data['clave_odoo'],
            data['descripcion'],
            data['modelo'],
            data['spec'],
            # Precios distribuidor
            data['precio_distribuidor_sin_iva'],
            precio_distribuidor_con_iva,
            # Precios elite plus
            data['precio_elite_plus_sin_iva'],
            precio_elite_plus_con_iva,
            # Precios elite
            data['precio_elite_sin_iva'],
            precio_elite_con_iva,
            # Precios partner
            data['precio_partner_sin_iva'],
            precio_partner_con_iva,
            # Precios público
            data['precio_publico_sin_iva'],
            precio_publico_con_iva,
            precio_publico_con_iva,  # precio_publico_con_iva_my26 es igual
            data['id_disponibilidad']
        ))

        conexion.commit()
        return jsonify({
            "mensaje": "Proyección agregada exitosamente",
            "precios_calculados": {
                "distribuidor": {
                    "sin_iva": data['precio_distribuidor_sin_iva'],
                    "con_iva": precio_distribuidor_con_iva
                },
                "elite_plus": {
                    "sin_iva": data['precio_elite_plus_sin_iva'],
                    "con_iva": precio_elite_plus_con_iva
                },
                "elite": {
                    "sin_iva": data['precio_elite_sin_iva'],
                    "con_iva": precio_elite_con_iva
                },
                "partner": {
                    "sin_iva": data['precio_partner_sin_iva'],
                    "con_iva": precio_partner_con_iva
                },
                "publico": {
                    "sin_iva": data['precio_publico_sin_iva'],
                    "con_iva": precio_publico_con_iva
                }
            }
        }), 201

    except ValueError as ve:
        print("Error en formato de precios:", str(ve))
        return jsonify({"error": "Los precios deben ser valores numéricos válidos"}), 400
    except Exception as e:
        print("Error al agregar proyección:", str(e))
        return jsonify({"error": "Error interno al agregar proyección"}), 500
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conexion' in locals():
            conexion.close()

@proyecciones_bp.route('/proyecciones/editar/<int:id>', methods=['PUT'])
def editar_proyeccion(id):
    data = request.get_json()

    campos_obligatorios = [
        'referencia', 'clave_factura', 'clave_6_digitos', 'ean',
        'clave_odoo', 'descripcion', 'modelo', 'spec', 'id_disponibilidad',
        'precio_distribuidor_sin_iva', 'precio_elite_plus_sin_iva',
        'precio_elite_sin_iva', 'precio_partner_sin_iva',
        'precio_publico_sin_iva'
    ]

    for campo in campos_obligatorios:
        if campo not in data:
            return jsonify({"error": f"El campo '{campo}' es obligatorio"}), 400

    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor()

        cursor.execute("""
            UPDATE proyecciones_ventas SET
                referencia = %s,
                clave_factura = %s,
                clave_6_digitos = %s,
                ean = %s,
                clave_odoo = %s,
                descripcion = %s,
                modelo = %s,
                spec = %s,
                precio_distribuidor_sin_iva = %s,
                precio_elite_plus_sin_iva = %s,
                precio_elite_sin_iva = %s,
                precio_partner_sin_iva = %s,
                precio_publico_sin_iva = %s,
                id_disponibilidad = %s
            WHERE id = %s
        """, (
            data['referencia'],
            data['clave_factura'],
            data['clave_6_digitos'],
            data['ean'],
            data['clave_odoo'],
            data['descripcion'],
            data['modelo'],
            data['spec'],
            data['precio_distribuidor_sin_iva'],
            data['precio_elite_plus_sin_iva'],
            data['precio_elite_sin_iva'],
            data['precio_partner_sin_iva'],
            data['precio_publico_sin_iva'],
            data['id_disponibilidad'],
            id
        ))

        conexion.commit()

        if cursor.rowcount == 0:
            return jsonify({"error": "No se encontró la proyección para actualizar"}), 404

        return jsonify({"mensaje": "Proyección actualizada exitosamente"}), 200

    except Exception as e:
        print("Error al editar proyección:", str(e))
        return jsonify({"error": "Error interno al editar proyección"}), 500

    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@proyecciones_bp.route('/proyecciones/eliminar/<int:id>', methods=['DELETE'])
def eliminar_proyeccion(id):
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor()

        cursor.execute("DELETE FROM proyecciones_ventas WHERE id = %s", (id,))

        conexion.commit()

        if cursor.rowcount == 0:
            return jsonify({"error": "No se encontró la proyección para eliminar"}), 404

        return jsonify({"mensaje": "Proyección eliminada exitosamente"}), 200

    except Exception as e:
        print("Error al eliminar proyección:", str(e))
        return jsonify({"error": "Error interno al eliminar proyección"}), 500

    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@proyecciones_bp.route('/proyecciones/ya-enviada', methods=['GET'])
def verificar_proyeccion_enviada():
    auth_header = request.headers.get('Authorization')
    
    if not auth_header:
        return jsonify({"error": "No se proporcionó token"}), 401

    try:
        token = auth_header.split(" ")[1]
        decoded = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        id_usuario = decoded.get("id")
    except Exception as e:
        print("Error al decodificar token:", str(e))
        return jsonify({"error": "Token inválido"}), 401

    if not id_usuario:
        return jsonify({"error": "No se proporcionó id_usuario"}), 400

    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)

        # Buscar el cliente vinculado al usuario
        cursor.execute("SELECT cliente_id FROM usuarios WHERE id = %s", (id_usuario,))
        cliente = cursor.fetchone()

        if not cliente or not cliente['cliente_id']:
            return jsonify({"error": "Cliente no encontrado"}), 404

        id_cliente = cliente['cliente_id']

        # Verificar si hay registros de proyección para este cliente
        cursor.execute("""
            SELECT COUNT(*) AS total FROM proyecciones_cliente WHERE id_cliente = %s
        """, (id_cliente,))
        resultado = cursor.fetchone()

        ya_enviada = resultado['total'] > 0
        return jsonify({"yaEnviada": ya_enviada}), 200

    except Exception as e:
        print("Error al verificar proyección:", str(e))
        return jsonify({"error": "Error interno"}), 500

    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@proyecciones_bp.route('/proyecciones/resumen-global', methods=['GET'])
def resumen_global_proyecciones():
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT
                c.id AS id_cliente,
                c.clave AS clave_cliente,
                c.nombre_cliente,
                c.zona,
                c.nivel,

                pv.referencia,
                pv.clave_factura,
                pv.clave_6_digitos,
                pv.ean,
                pv.clave_odoo,
                pv.descripcion,
                pv.modelo,
                pv.spec,
                pc.precio_aplicado,
                pv.precio_publico_con_iva,

                pc.q1_sep_2025,
                pc.q2_sep_2025,
                pc.q1_oct_2025,
                pc.q2_oct_2025,
                pc.q1_nov_2025,
                pc.q2_nov_2025,
                pc.q1_dic_2025,
                pc.q2_dic_2025,
                pc.q1_mar_2026,
                pc.q2_mar_2026,
                pc.q1_abr_2026,
                pc.q2_abr_2026,
                pc.q1_may_2026,
                pc.q2_may_2026,

                (pc.q1_sep_2025 + pc.q2_sep_2025 + pc.q1_oct_2025 + pc.q2_oct_2025 + 
                 pc.q1_nov_2025 + pc.q2_nov_2025 + pc.q1_dic_2025 + pc.q2_dic_2025 +
                 pc.q1_mar_2026 + pc.q2_mar_2026 + pc.q1_abr_2026 + pc.q2_abr_2026 +
                 pc.q1_may_2026 + pc.q2_may_2026) AS orden_total_cant,

                (pc.precio_aplicado * 
                 (pc.q1_sep_2025 + pc.q2_sep_2025 + pc.q1_oct_2025 + pc.q2_oct_2025 + 
                  pc.q1_nov_2025 + pc.q2_nov_2025 + pc.q1_dic_2025 + pc.q2_dic_2025 +
                  pc.q1_mar_2026 + pc.q2_mar_2026 + pc.q1_abr_2026 + pc.q2_abr_2026 +
                  pc.q1_may_2026 + pc.q2_may_2026)
                ) AS orden_total_importe,

                pc.fecha_registro,
                pc.folio

            FROM proyecciones_cliente pc
            JOIN proyecciones_ventas pv ON pc.id_proyeccion = pv.id
            JOIN clientes c ON pc.id_cliente = c.id

            ORDER BY c.nombre_cliente, pc.fecha_registro DESC, pv.clave_factura
        """)

        rows = cursor.fetchall()
        agrupado = {}

        for row in rows:
            id_cliente = row["id_cliente"]
            if id_cliente not in agrupado:
                agrupado[id_cliente] = {
                    "clave_cliente": row["clave_cliente"],
                    "nombre_cliente": row["nombre_cliente"],
                    "zona": row["zona"],
                    "nivel": row["nivel"],
                    "productos": []
                }

            producto = {
                "referencia": row["referencia"],
                "clave_factura": row["clave_factura"],
                "clave_6_digitos": row["clave_6_digitos"],
                "ean": row["ean"],
                "clave_odoo": row["clave_odoo"],
                "descripcion": row["descripcion"],
                "modelo": row["modelo"],
                "spec": row["spec"],
                "precio_aplicado": float(row["precio_aplicado"]) if row["precio_aplicado"] is not None else None,
                "precio_publico_con_iva": float(row["precio_publico_con_iva"]) if row["precio_publico_con_iva"] is not None else None,
                "q1_sep_2025": row["q1_sep_2025"],
                "q2_sep_2025": row["q2_sep_2025"],
                "q1_oct_2025": row["q1_oct_2025"],
                "q2_oct_2025": row["q2_oct_2025"],
                "q1_nov_2025": row["q1_nov_2025"],
                "q2_nov_2025": row["q2_nov_2025"],
                "q1_dic_2025": row["q1_dic_2025"],
                "q2_dic_2025": row["q2_dic_2025"],
                "q1_mar_2026": row["q1_mar_2026"],
                "q2_mar_2026": row["q2_mar_2026"],
                "q1_abr_2026": row["q1_abr_2026"],
                "q2_abr_2026": row["q2_abr_2026"],
                "q1_may_2026": row["q1_may_2026"],
                "q2_may_2026": row["q2_may_2026"],
                "orden_total_cant": row["orden_total_cant"],
                "orden_total_importe": float(row["orden_total_importe"]) if row["orden_total_importe"] is not None else None,
                "fecha_registro": row["fecha_registro"].strftime('%Y-%m-%d %H:%M:%S') if row["fecha_registro"] else None,
                "folio": row["folio"]
            }

            agrupado[id_cliente]["productos"].append(producto)

        return jsonify(list(agrupado.values())), 200

    except Exception as e:
        print("Error al obtener el resumen global:", str(e))
        return jsonify({"error": "Error interno al obtener el resumen"}), 500

    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@proyecciones_bp.route('/importar_proyecciones', methods=['POST'])
def importar_proyecciones():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No se proporcionó archivo'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'Nombre de archivo vacío'}), 400

    try:
        # Guardar archivo temporalmente
        UPLOAD_FOLDER = 'temp_uploads'
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)

        filename = secure_filename(f"proyecciones_{ahora_str('%Y%m%d_%H%M%S')}.xlsx")
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)

        # Funciones de limpieza (se mantienen igual)
        def to_decimal(val):
            try:
                if val is None or pd.isna(val):
                    return None
                if isinstance(val, str) and val.strip().lower() in ['nan', 'none', '']:
                    return None
                return float(str(val).replace(',', '').strip())
            except:
                return None

        def to_int(val):
            try:
                if val is None or pd.isna(val):
                    return 0
                if isinstance(val, str) and val.strip().lower() in ['nan', 'none', '']:
                    return 0
                return int(float(val))
            except:
                return 0

        # Leer el archivo Excel
        df = pd.read_excel(filepath)
        df = df.replace([np.nan, pd.NaT], None)

        # Verificar columnas requeridas (agregando las columnas de totales)
        columnas_requeridas = [
            'referencia', 'clave_factura', 'clave_6_digitos', 'clave_odoo',
            'descripcion', 'modelo', 'spec', 'ean',
            'precio_elite_plus_sin_iva', 'precio_elite_sin_iva', 'precio_partner_sin_iva',
            'precio_distribuidor_sin_iva', 'precio_publico_sin_iva', 'precio_publico_con_iva_my26',
            'precio_elite_plus_con_iva', 'precio_elite_con_iva', 'precio_partner_con_iva',
            'precio_distribuidor_con_iva', 'precio_publico_con_iva',
            'q1_sep_2025', 'q2_sep_2025', 'q1_oct_2025', 'q2_oct_2025',
            'q1_nov_2025', 'q2_nov_2025', 'q1_dic_2025', 'q2_dic_2025',
            'q1_mar_2026', 'q2_mar_2026', 'q1_abr_2026', 'q2_abr_2026',
            'q1_may_2026', 'q2_may_2026',
            'orden_total_cant', 'orden_total_importe'  # Asegúrate de que estas columnas existen
        ]

        columnas_faltantes = [col for col in columnas_requeridas if col not in df.columns]
        if columnas_faltantes:
            return jsonify({'success': False, 'error': f'Faltan columnas: {", ".join(columnas_faltantes)}'}), 400

        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)

        total_insertados = 0
        sql = """
            INSERT INTO proyecciones_ventas (
                referencia, clave_factura, clave_6_digitos, ean, clave_odoo, descripcion, modelo, spec,
                precio_elite_plus_sin_iva, precio_elite_sin_iva, precio_partner_sin_iva,
                precio_distribuidor_sin_iva, precio_publico_sin_iva, precio_publico_con_iva_my26,
                precio_elite_plus_con_iva, precio_elite_con_iva, precio_partner_con_iva,
                precio_distribuidor_con_iva, precio_publico_con_iva,
                q1_sep_2025, q2_sep_2025, q1_oct_2025, q2_oct_2025,
                q1_nov_2025, q2_nov_2025, q1_dic_2025, q2_dic_2025,
                q1_mar_2026, q2_mar_2026, q1_abr_2026, q2_abr_2026,
                q1_may_2026, q2_may_2026,
                orden_total_cant, orden_total_importe, id_disponibilidad
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """

        for _, fila in df.iterrows():
            # Usamos DIRECTAMENTE los valores del archivo sin calcular
            total_cant = to_int(fila['orden_total_cant'])  # Asume que la columna existe
            total_importe = to_decimal(fila['orden_total_importe'])  # Asume que la columna existe

            valores = (
                fila['referencia'], 
                str(fila['clave_factura']) if pd.notna(fila['clave_factura']) else None,  # Modificado
                str(fila['clave_6_digitos']) if pd.notna(fila['clave_6_digitos']) else None,  # Modificado
                str(fila['ean']) if pd.notna(fila['ean']) else None,  # Modificado
                fila['clave_odoo'], 
                fila['descripcion'], 
                fila['modelo'],
                fila['spec'],
                to_decimal(fila['precio_elite_plus_sin_iva']),
                to_decimal(fila['precio_elite_sin_iva']),
                to_decimal(fila['precio_partner_sin_iva']),
                to_decimal(fila['precio_distribuidor_sin_iva']),
                to_decimal(fila['precio_publico_sin_iva']),
                to_decimal(fila['precio_publico_con_iva_my26']),
                to_decimal(fila['precio_elite_plus_con_iva']),
                to_decimal(fila['precio_elite_con_iva']),
                to_decimal(fila['precio_partner_con_iva']),
                to_decimal(fila['precio_distribuidor_con_iva']),
                to_decimal(fila['precio_publico_con_iva']),
                to_int(fila['q1_sep_2025']),
                to_int(fila['q2_sep_2025']),
                to_int(fila['q1_oct_2025']),
                to_int(fila['q2_oct_2025']),
                to_int(fila['q1_nov_2025']),
                to_int(fila['q2_nov_2025']),
                to_int(fila['q1_dic_2025']),
                to_int(fila['q2_dic_2025']),
                to_int(fila['q1_mar_2026']),
                to_int(fila['q2_mar_2026']),
                to_int(fila['q1_abr_2026']),
                to_int(fila['q2_abr_2026']),
                to_int(fila['q1_may_2026']),
                to_int(fila['q2_may_2026']),
                total_cant,
                total_importe,
                1  # Valor por defecto para id_disponibilidad
            )

            try:
                cursor.execute(sql, valores)
                total_insertados += 1
            except Exception as e:
                print(f"Error al insertar fila: {fila['referencia']}")
                print(f"Error: {str(e)}")
                continue
            
        conexion.commit()
        cursor.close()
        conexion.close()
        os.remove(filepath)

        return jsonify({
            'success': True, 
            'message': f'Se importaron {total_insertados} registros', 
            'count': total_insertados
        })

    except Exception as e:
        print("Error:", e)
        traceback.print_exc()
        if 'conexion' in locals():
            conexion.rollback()
            conexion.close()
        if 'filepath' in locals() and os.path.exists(filepath):
            os.remove(filepath)
        return jsonify({
            'success': False, 
            'error': str(e),
            'details': 'Verifica que el archivo tenga el formato correcto y todas las columnas requeridas'
        }), 500
    
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@proyecciones_bp.route('/proyecciones/autoguardado', methods=['POST'])
def manejar_autoguardado():
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return jsonify({"error": "No se proporcionó token"}), 401

    try:
        token = auth_header.split(" ")[1]
        decoded = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        id_usuario = decoded.get("id")
    except Exception as e:
        print("Error al decodificar token:", str(e))
        return jsonify({"error": "Token inválido"}), 401

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        # Obtener el cliente asociado al usuario
        cursor.execute("SELECT cliente_id FROM usuarios WHERE id = %s", (id_usuario,))
        cliente = cursor.fetchone()
        if not cliente or not cliente['cliente_id']:
            return jsonify({"error": "Cliente no encontrado para este usuario"}), 404
        id_cliente = cliente['cliente_id']

        data = request.get_json()
        
        # Validar estructura de datos
        if not isinstance(data, dict):
            return jsonify({"error": "Se esperaba un objeto con acción y datos"}), 400
            
        accion = data.get('accion')
        proyecciones = data.get('proyecciones', [])
        
        # 1. Guardar datos (acción por defecto)
        if accion in (None, 'guardar'):
            if not isinstance(proyecciones, list):
                return jsonify({"error": "Se esperaba una lista de proyecciones"}), 400

            for proyeccion in proyecciones:
                id_proyeccion = proyeccion.get('id_proyeccion')
                if not id_proyeccion:
                    continue  # O podrías devolver error

                cursor.execute("""
                    INSERT INTO proyecciones_autoguardado (
                        id_cliente, id_proyeccion,
                        q1_sep_2025, q2_sep_2025, q1_oct_2025, q2_oct_2025,
                        q1_nov_2025, q2_nov_2025, q1_dic_2025, q2_dic_2025,
                        q1_mar_2026, q2_mar_2026, q1_abr_2026, q2_abr_2026,
                        q1_may_2026, q2_may_2026
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        q1_sep_2025 = VALUES(q1_sep_2025),
                        q2_sep_2025 = VALUES(q2_sep_2025),
                        q1_oct_2025 = VALUES(q1_oct_2025),
                        q2_oct_2025 = VALUES(q2_oct_2025),
                        q1_nov_2025 = VALUES(q1_nov_2025),
                        q2_nov_2025 = VALUES(q2_nov_2025),
                        q1_dic_2025 = VALUES(q1_dic_2025),
                        q2_dic_2025 = VALUES(q2_dic_2025),
                        q1_mar_2026 = VALUES(q1_mar_2026),
                        q2_mar_2026 = VALUES(q2_mar_2026),
                        q1_abr_2026 = VALUES(q1_abr_2026),
                        q2_abr_2026 = VALUES(q2_abr_2026),
                        q1_may_2026 = VALUES(q1_may_2026),
                        q2_may_2026 = VALUES(q2_may_2026),
                        fecha_actualizacion = CURRENT_TIMESTAMP
                """, (
                    id_cliente, id_proyeccion,
                    proyeccion.get('q1_sep_2025', 0),
                    proyeccion.get('q2_sep_2025', 0),
                    proyeccion.get('q1_oct_2025', 0),
                    proyeccion.get('q2_oct_2025', 0),
                    proyeccion.get('q1_nov_2025', 0),
                    proyeccion.get('q2_nov_2025', 0),
                    proyeccion.get('q1_dic_2025', 0),
                    proyeccion.get('q2_dic_2025', 0),
                    proyeccion.get('q1_mar_2026', 0),
                    proyeccion.get('q2_mar_2026', 0),
                    proyeccion.get('q1_abr_2026', 0),
                    proyeccion.get('q2_abr_2026', 0),
                    proyeccion.get('q1_may_2026', 0),
                    proyeccion.get('q2_may_2026', 0)
                ))

            conexion.commit()
            return jsonify({
                "mensaje": "Autoguardado realizado correctamente",
                "accion": "guardado",
                "proyecciones_actualizadas": len(proyecciones)
            }), 200

        # 2. Cargar datos existentes
        elif accion == 'cargar':
            cursor.execute("""
                SELECT * FROM proyecciones_autoguardado 
                WHERE id_cliente = %s
            """, (id_cliente,))
            return jsonify({
                "mensaje": "Datos de autoguardado cargados",
                "accion": "cargar",
                "data": cursor.fetchall()
            }), 200

        # 3. Limpiar autoguardado
        elif accion == 'limpiar':
            cursor.execute("DELETE FROM proyecciones_autoguardado WHERE id_cliente = %s", (id_cliente,))
            conexion.commit()
            return jsonify({
                "mensaje": "Autoguardado eliminado correctamente",
                "accion": "limpiar"
            }), 200

        else:
            return jsonify({"error": "Acción no válida"}), 400

    except mysql_connector_compat.Error as err:
        print("Error en autoguardado:", str(err))
        conexion.rollback()
        return jsonify({"error": "Error en el servidor"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()