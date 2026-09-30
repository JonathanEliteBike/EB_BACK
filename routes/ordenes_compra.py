from datetime import date, datetime
from decimal import Decimal
from flask import Blueprint, jsonify, request
from db_conexion import obtener_conexion
from utils.jwt_utils import registrar_auditoria

ordenes_compra_bp = Blueprint('ordenes_compra_bp', __name__, url_prefix='/flujo')

@ordenes_compra_bp.route('/ordenes', methods=['POST'])
def crear_orden():
    data = request.get_json()
    conexion = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor()
        
        # Si no mandan importe final, es igual al original
        imp_orig = data.get('importe_original')
        imp_final = data.get('importe_final', imp_orig)

        query = """
            INSERT INTO ordenes_compra 
            (codigo_po, proveedor, fecha_po, moneda, importe_original, importe_final, fecha_vencimiento, estatus)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'PRODUCCION')
        """
        cursor.execute(query, (
            data.get('codigo_po'),
            data.get('proveedor'),
            data.get('fecha_po'),
            data.get('moneda', 'MXN'),
            imp_orig,
            imp_final,
            data.get('fecha_vencimiento')
        ))
        registrar_auditoria(
            cursor, 'INSERT_ORDEN_COMPRA', 'ordenes_compra', cursor.lastrowid,
            f"Orden de compra {data.get('codigo_po') or 'sin código'} registrada"
        )
        conexion.commit()
        return jsonify({"mensaje": "Orden registrada correctamente"}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conexion: conexion.close()

@ordenes_compra_bp.route('/ordenes', methods=['GET'])
def obtener_ordenes():
    conexion = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)
        
        # Traemos las órdenes ordenadas por fecha (las más nuevas primero)
        query = "SELECT * FROM ordenes_compra ORDER BY created_at DESC"
        cursor.execute(query)
        registros = cursor.fetchall()
        
        # Limpieza de datos para JSON
        for row in registros:
            for k, v in row.items():
                if isinstance(v, Decimal):
                    row[k] = float(v)
                elif isinstance(v, (datetime, date)):
                    row[k] = v.isoformat()
                    
        return jsonify(registros), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conexion: conexion.close()

@ordenes_compra_bp.route('/ordenes/<int:id_orden>', methods=['PUT'])
def actualizar_orden(id_orden):
    data = request.get_json()
    conexion = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor()
        
        query = """
            UPDATE ordenes_compra
            SET codigo_po = %s, proveedor = %s, fecha_po = %s, 
                moneda = %s, importe_original = %s, estatus = %s
            WHERE id_orden = %s
        """
        cursor.execute(query, (
            data.get('codigo_po'),
            data.get('proveedor'),
            data.get('fecha_po'),
            data.get('moneda'),
            data.get('importe_original'),
            data.get('estatus'), # Para cambiar a 'TRANSITO', 'CERRADO', etc.
            id_orden
        ))
        registrar_auditoria(
            cursor, 'UPDATE_ORDEN_COMPRA', 'ordenes_compra', id_orden,
            f"Orden de compra {data.get('codigo_po') or id_orden} actualizada"
        )
        conexion.commit()
        return jsonify({"mensaje": "Orden actualizada"}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conexion: conexion.close()

# 4. ELIMINAR (DELETE)
@ordenes_compra_bp.route('/ordenes/<int:id_orden>', methods=['DELETE'])
def eliminar_orden(id_orden):
    conexion = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor()
        # Nota: Esto fallará si la orden ya tiene embarques (por seguridad de la BD)
        cursor.execute("DELETE FROM ordenes_compra WHERE id_orden = %s", (id_orden,))
        registrar_auditoria(
            cursor, 'DELETE_ORDEN_COMPRA', 'ordenes_compra', id_orden,
            f"Orden de compra {id_orden} eliminada"
        )
        conexion.commit()
        return jsonify({"mensaje": "Orden eliminada"}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conexion: conexion.close()
