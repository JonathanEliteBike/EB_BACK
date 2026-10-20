from flask import Blueprint, jsonify, request
from db_conexion import obtener_conexion
from utils.jwt_utils import registrar_auditoria

ingresos_bp = Blueprint('ingresos_bp', __name__, url_prefix='/flujo')

@ingresos_bp.route('/ingresos', methods=['POST'])
def crear_ingreso():
    data = request.get_json()
    conexion = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor()
        query = """
            INSERT INTO ingresos_cobranza 
            (folio_factura, cliente, fecha_promesa_pago, monto_cobro, probabilidad, cuenta_destino, estatus)
            VALUES (%s, %s, %s, %s, %s, %s, 'PENDIENTE')
        """
        cursor.execute(query, (
            data.get('folio_factura'),
            data.get('cliente'),
            data.get('fecha_promesa_pago'),
            data.get('monto_cobro'),
            data.get('probabilidad', 'ALTA'),
            data.get('cuenta_destino')
        ))
        registrar_auditoria(
            cursor, 'INSERT_INGRESO', 'ingresos_cobranza', cursor.lastrowid,
            f"Cobranza {data.get('folio_factura') or 'sin folio'} registrada para {data.get('cliente') or 'cliente sin especificar'}"
        )
        conexion.commit()
        return jsonify({"mensaje": "Cobranza proyectada registrada"}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conexion: conexion.close()
