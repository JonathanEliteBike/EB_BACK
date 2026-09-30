from flask import Blueprint, jsonify, request
from datetime import datetime, timedelta, date
from decimal import Decimal
from db_conexion import obtener_conexion
from utils.jwt_utils import registrar_auditoria
from datetime import datetime, timedelta

logistica_bp = Blueprint('logistica_bp', __name__, url_prefix='/flujo')

@logistica_bp.route('/embarques', methods=['POST'])
def crear_embarque():
    data = request.get_json()
    conexion = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor()

        # LÓGICA AUTOMÁTICA DE FECHAS
        fecha_eta_str = data.get('fecha_eta')
        fecha_eta = datetime.strptime(fecha_eta_str, '%Y-%m-%d')
        # Regla: Se pagan impuestos 3 días antes de llegar
        fecha_pago_impuestos = fecha_eta - timedelta(days=3)

        # LÓGICA AUTOMÁTICA DE IVA
        valor = float(data.get('valor_aduana_mxn', 0))
        igi = float(data.get('pago_igi', 0))
        dta = float(data.get('pago_dta', 0))
        # Regla: IVA = (Valor + IGI + DTA) * 16%
        iva_calc = (valor + igi + dta) * 0.16

        query = """
            INSERT INTO embarques_logistica 
            (codigo_embarque, orden_compra_id, contenedor, fecha_eta, tipo_cambio_proy, 
             valor_aduana_mxn, pago_igi, pago_dta, pago_iva_impo, gasto_flete_mxn, fecha_pago_impuestos)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        cursor.execute(query, (
            data.get('codigo_embarque'),
            data.get('orden_compra_id'), # ID de la tabla ordenes_compra
            data.get('contenedor'),
            fecha_eta_str,
            data.get('tipo_cambio_proy', 19.50),
            valor,
            igi,
            dta,
            iva_calc,   # Dato calculado
            data.get('gasto_flete_mxn', 0),
            fecha_pago_impuestos.strftime('%Y-%m-%d') # Dato calculado
        ))
        registrar_auditoria(
            cursor, 'INSERT_EMBARQUE', 'embarques_logistica', cursor.lastrowid,
            f"Embarque {data.get('codigo_embarque') or 'sin código'} registrado"
        )
        conexion.commit()
        return jsonify({"mensaje": "Embarque e Impuestos calculados exitosamente"}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conexion: conexion.close()

@logistica_bp.route('/embarques', methods=['GET'])
def obtener_embarques():
    conexion = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)
        
        # Hacemos JOIN con ordenes_compra para saber de quién es el embarque
        query = """
            SELECT e.*, o.proveedor, o.codigo_po 
            FROM embarques_logistica e
            LEFT JOIN ordenes_compra o ON e.orden_compra_id = o.id_orden
            ORDER BY e.fecha_eta DESC
        """
        cursor.execute(query)
        registros = cursor.fetchall()

        # Convertimos decimales y fechas a string para JSON
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

# -----------------------------------------------------------------------------
# 3. ACTUALIZAR (PUT) - Recalculando impuestos si cambian los valores
# -----------------------------------------------------------------------------
@logistica_bp.route('/embarques/<int:id_embarque>', methods=['PUT'])
def actualizar_embarque(id_embarque):
    data = request.get_json()
    conexion = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor()

        # RE-CALCULAMOS TODO (Igual que en el POST)
        fecha_eta_str = data.get('fecha_eta')
        fecha_eta = datetime.strptime(fecha_eta_str, '%Y-%m-%d')
        fecha_pago_impuestos = fecha_eta - timedelta(days=3)

        valor = float(data.get('valor_aduana_mxn', 0))
        igi = float(data.get('pago_igi', 0))
        dta = float(data.get('pago_dta', 0))
        iva_calc = (valor + igi + dta) * 0.16

        query = """
            UPDATE embarques_logistica
            SET codigo_embarque=%s, orden_compra_id=%s, contenedor=%s, 
                fecha_eta=%s, valor_aduana_mxn=%s, pago_igi=%s, 
                pago_dta=%s, pago_iva_impo=%s, gasto_flete_mxn=%s, 
                fecha_pago_impuestos=%s
            WHERE id_embarque=%s
        """
        cursor.execute(query, (
            data.get('codigo_embarque'),
            data.get('orden_compra_id'),
            data.get('contenedor'),
            fecha_eta_str,
            valor, igi, dta, iva_calc,
            data.get('gasto_flete_mxn', 0),
            fecha_pago_impuestos.strftime('%Y-%m-%d'),
            id_embarque
        ))
        registrar_auditoria(
            cursor, 'UPDATE_EMBARQUE', 'embarques_logistica', id_embarque,
            f"Embarque {data.get('codigo_embarque') or id_embarque} actualizado"
        )
        conexion.commit()
        return jsonify({"mensaje": "Embarque actualizado y recalculado"}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conexion: conexion.close()

# -----------------------------------------------------------------------------
# 4. ELIMINAR (DELETE)
# -----------------------------------------------------------------------------
@logistica_bp.route('/embarques/<int:id_embarque>', methods=['DELETE'])
def eliminar_embarque(id_embarque):
    conexion = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor()
        cursor.execute("DELETE FROM embarques_logistica WHERE id_embarque = %s", (id_embarque,))
        registrar_auditoria(
            cursor, 'DELETE_EMBARQUE', 'embarques_logistica', id_embarque,
            f"Embarque {id_embarque} eliminado"
        )
        conexion.commit()
        return jsonify({"mensaje": "Embarque eliminado correctamente"}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conexion: conexion.close()
