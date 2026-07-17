from flask import Blueprint, jsonify, request
from db_conexion import obtener_conexion
from decimal import Decimal
import logging
import calendar
from datetime import date
from utils.jwt_utils import verificar_token

multimarcas_bp = Blueprint('multimarcas', __name__, url_prefix='')

_NOMBRES_MESES_TEMPORADA = [
    'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
    'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio'
]


def _rangos_meses_temporada(fecha_inicio_str: str) -> list:
    """A partir del inicio de una temporada (ej '2025-07-01'), regresa 12
    tuplas (nombre_mes, primer_dia, ultimo_dia) para julio..junio de ESA
    temporada -- el año se calcula dinamicamente a partir de fecha_inicio,
    nunca hardcodeado, para que sirva para cualquier temporada (MY26, MY27...)."""
    inicio = date.fromisoformat(str(fecha_inicio_str)[:10])
    anio, mes = inicio.year, inicio.month
    rangos = []
    for nombre in _NOMBRES_MESES_TEMPORADA:
        primer_dia = date(anio, mes, 1)
        ultimo_dia = date(anio, mes, calendar.monthrange(anio, mes)[1])
        rangos.append((nombre, primer_dia.isoformat(), ultimo_dia.isoformat()))
        mes += 1
        if mes > 12:
            mes = 1
            anio += 1
    return rangos

@multimarcas_bp.route('/actualizar_multimarcas', methods=['POST'])
def actualizar_multimarcas():
    conexion = None
    cursor = None
    
    try:
        # Verificar que se recibió data JSON
        if not request.is_json:
            return jsonify({'error': 'Se esperaba un JSON en el cuerpo de la solicitud'}), 400
        
        data = request.get_json()
        
        # El frontend envía { datos: [...] } o directamente la lista
        registros = data.get('datos', data) if isinstance(data, dict) else data
        
        # Validar estructura de los datos
        if not isinstance(registros, list):
            return jsonify({'error': 'Los datos deben ser una lista de registros'}), 400
            
        if len(registros) == 0:
            return jsonify({'error': 'No se recibieron registros para actualizar'}), 400
        
        print(f"Recibidos {len(registros)} registros para actualizar en multimarcas")
        
        conexion = obtener_conexion()
        cursor = conexion.cursor()
        
        # 1. Limpiar la tabla existente
        cursor.execute("TRUNCATE TABLE multimarcas")
        
        # 2. Insertar los nuevos registros
        registros_insertados = 0
        
        for registro in registros:
            try:
                # Validar campos mínimos requeridos
                if not all(key in registro for key in ['clave', 'evac', 'cliente_razon_social']):
                    print(f"Registro omitido: falta clave, evac o cliente_razon_social")
                    continue
                
                # Calcular avance_global si no viene en los datos
                avance_global = registro.get('avance_global') or sum(
                    Decimal(registro.get(field, 0) or 0)
                    for field in [
                        'avance_global_scott',
                        'avance_global_syncros',
                        'avance_global_apparel',
                        'avance_global_vittoria',
                        'avance_global_bold'
                    ]
                )
                
                cursor.execute("""
                    INSERT INTO multimarcas (
                        clave, evac, cliente_razon_social, avance_global,
                        avance_global_scott, avance_global_syncros, avance_global_apparel,
                        avance_global_vittoria, avance_global_bold,
                        total_facturas_julio, total_facturas_agosto, total_facturas_septiembre,
                        total_facturas_octubre, total_facturas_noviembre, total_facturas_diciembre,
                        total_facturas_enero, total_facturas_febrero, total_facturas_marzo,
                        total_facturas_abril, total_facturas_mayo, total_facturas_junio
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                """, (
                    registro['clave'],
                    registro['evac'],
                    registro['cliente_razon_social'],
                    avance_global,
                    registro.get('avance_global_scott', 0),
                    registro.get('avance_global_syncros', 0),
                    registro.get('avance_global_apparel', 0),
                    registro.get('avance_global_vittoria', 0),
                    registro.get('avance_global_bold', 0),
                    registro.get('total_facturas_julio', 0),
                    registro.get('total_facturas_agosto', 0),
                    registro.get('total_facturas_septiembre', 0),
                    registro.get('total_facturas_octubre', 0),
                    registro.get('total_facturas_noviembre', 0),
                    registro.get('total_facturas_diciembre', 0),
                    registro.get('total_facturas_enero', 0),
                    registro.get('total_facturas_febrero', 0),
                    registro.get('total_facturas_marzo', 0),
                    registro.get('total_facturas_abril', 0),
                    registro.get('total_facturas_mayo', 0),
                    registro.get('total_facturas_junio', 0)
                ))
                
                registros_insertados += 1
                
            except Exception as insert_error:
                print(f"Error insertando registro (clave: {registro.get('clave', 'N/A')}): {insert_error}")
                continue
        
        conexion.commit()
        return jsonify({
            'mensaje': f'Datos de multimarcas actualizados. {registros_insertados}/{len(registros)} registros insertados.',
            'success': True
        }), 200
    
    except Exception as e:
        print(f"Error general: {str(e)}")
        if conexion:
            conexion.rollback()
        return jsonify({'error': str(e), 'success': False}), 500
    
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@multimarcas_bp.route('/obtener_multimarcas', methods=['GET'])
def obtener_multimarcas():
    conexion = None
    cursor = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)
        cursor.execute("SELECT * FROM multimarcas")
        resultados = cursor.fetchall()
        return jsonify(resultados), 200
    except Exception as e:
        print(f"Error al obtener multimarcas: {str(e)}")
        return jsonify({'error': str(e), 'success': False}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@multimarcas_bp.route('/agregar_cliente', methods=['POST'])
def agregar_cliente():
    conexion = None
    cursor = None
    try:
        datos = request.get_json()
        clave = datos.get('clave')
        evac = datos.get('evac')
        cliente_razon_social = datos.get('cliente_razon_social')

        if not clave:
            return jsonify({'error': 'El campo clave es obligatorio'}), 400

        conexion = obtener_conexion()
        cursor = conexion.cursor()

        # Verificar si el cliente ya existe
        cursor.execute("SELECT id FROM clientes_multimarcas WHERE clave = %s", (clave,))
        if cursor.fetchone():
            return jsonify({'error': 'Ya existe un cliente con esta clave'}), 400

        # Insertar nuevo cliente
        cursor.execute(
            "INSERT INTO clientes_multimarcas (clave, evac, cliente_razon_social) VALUES (%s, %s, %s)",
            (clave, evac, cliente_razon_social)
        )
        conexion.commit()

        return jsonify({
            'mensaje': 'Cliente agregado correctamente',
            'id': cursor.lastrowid
        }), 201

    except Exception as e:
        if conexion:
            conexion.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@multimarcas_bp.route('/editar_cliente/<int:id>', methods=['PUT'])
def editar_cliente(id):
    conexion = None
    cursor = None
    try:
        datos = request.get_json()
        clave = datos.get('clave')
        evac = datos.get('evac')
        cliente_razon_social = datos.get('cliente_razon_social')

        if not clave:
            return jsonify({'error': 'El campo clave es obligatorio'}), 400

        conexion = obtener_conexion()
        cursor = conexion.cursor()

        # Verificar si el cliente existe
        cursor.execute("SELECT id FROM clientes_multimarcas WHERE id = %s", (id,))
        if not cursor.fetchone():
            return jsonify({'error': 'Cliente no encontrado'}), 404

        # Verificar si la nueva clave ya está en uso por otro cliente
        cursor.execute(
            "SELECT id FROM clientes_multimarcas WHERE clave = %s AND id != %s", 
            (clave, id)
        )
        if cursor.fetchone():
            return jsonify({'error': 'La clave ya está en uso por otro cliente'}), 400

        # Actualizar cliente
        cursor.execute(
            """UPDATE clientes_multimarcas 
            SET clave = %s, evac = %s, cliente_razon_social = %s 
            WHERE id = %s""",
            (clave, evac, cliente_razon_social, id)
        )
        conexion.commit()

        return jsonify({'mensaje': 'Cliente actualizado correctamente'}), 200

    except Exception as e:
        if conexion:
            conexion.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@multimarcas_bp.route('/eliminar_cliente/<int:id>', methods=['DELETE'])
def eliminar_cliente(id):
    conexion = None
    cursor = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor()

        # Verificar si el cliente existe
        cursor.execute("SELECT id FROM clientes_multimarcas WHERE id = %s", (id,))
        if not cursor.fetchone():
            return jsonify({'error': 'Cliente no encontrado'}), 404

        # Eliminar cliente
        cursor.execute("DELETE FROM clientes_multimarcas WHERE id = %s", (id,))
        conexion.commit()

        return jsonify({'mensaje': 'Cliente eliminado correctamente'}), 200

    except Exception as e:
        if conexion:
            conexion.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()


# ══════════════════════════════════════════════════════════════════════════
# HISTÓRICO POR TEMPORADA
# ══════════════════════════════════════════════════════════════════════════
# Igual que en routes/temporadas.py: funcion pura de solo lectura (nunca
# escribe en `multimarcas`, la tabla en vivo). Replica el mismo
# emparejamiento de dos pasos que usa multimarcas.component.ts para cada
# factura: primero por clave (monitor.contacto_referencia), y si no hay
# match, por nombre (monitor.contacto_nombre vs clientes_multimarcas.
# cliente_razon_social, exacto salvo mayusculas/espacios). Se calculan TODOS
# los clientes en una sola pasada sobre `monitor` (mas eficiente que
# reconsultar por cada uno, y necesario para que el fallback por nombre
# pueda comparar contra el nombre de CUALQUIER cliente del roster).

_CAMPOS_MULTIMARCAS_ACUMULABLES = [
    'avance_global_scott', 'avance_global_syncros', 'avance_global_apparel',
    'avance_global_vittoria', 'avance_global_bold',
] + [f'total_facturas_{n}' for n in _NOMBRES_MESES_TEMPORADA]


def _calcular_valores_multimarcas_todos(cursor, clientes: list, f_inicio: str, fecha_cierre: str) -> dict:
    """Calcula (sin escribir nada) los avances de Multimarcas para TODOS los
    clientes de `clientes_multimarcas` a la vez, acotados a
    [f_inicio, fecha_cierre]. Regresa {id_cliente: {campo: valor}}."""
    rangos = _rangos_meses_temporada(f_inicio)
    rangos_fecha = [(nombre, date.fromisoformat(ini), date.fromisoformat(fin)) for nombre, ini, fin in rangos]

    resultados = {
        c['id']: {campo: 0.0 for campo in _CAMPOS_MULTIMARCAS_ACUMULABLES}
        for c in clientes
    }

    por_clave = {c['clave']: c['id'] for c in clientes if c['clave']}
    por_nombre = {
        c['cliente_razon_social'].strip().lower(): c['id']
        for c in clientes if c.get('cliente_razon_social')
    }

    cursor.execute("""
        SELECT contacto_referencia, contacto_nombre, marca, apparel, venta_total, fecha_factura
        FROM monitor
        WHERE fecha_factura >= %s AND fecha_factura <= %s
    """, (f_inicio, fecha_cierre))

    for f in cursor.fetchall():
        clave_f = (f['contacto_referencia'] or '').strip()
        id_cliente = por_clave.get(clave_f)
        if id_cliente is None:
            nombre_f = (f['contacto_nombre'] or '').strip().lower()
            id_cliente = por_nombre.get(nombre_f)
        if id_cliente is None:
            continue

        monto = float(f['venta_total'] or 0)
        marca = (f['marca'] or '').strip().upper()
        apparel = (f['apparel'] or '').strip().upper()
        r = resultados[id_cliente]

        if marca == 'SCOTT' and apparel == 'NO':
            r['avance_global_scott'] += monto
        elif marca == 'SYNCROS':
            r['avance_global_syncros'] += monto
        elif marca == 'SCOTT' and apparel == 'SI':
            r['avance_global_apparel'] += monto
        elif marca == 'VITTORIA':
            r['avance_global_vittoria'] += monto
        elif marca == 'BOLD':
            r['avance_global_bold'] += monto

        fecha_f = f['fecha_factura']
        for nombre_mes, ini, fin in rangos_fecha:
            if ini <= fecha_f <= fin:
                r[f'total_facturas_{nombre_mes}'] += monto
                break

    for r in resultados.values():
        for campo in r:
            r[campo] = round(r[campo], 2)
        r['avance_global'] = round(
            r['avance_global_scott'] + r['avance_global_syncros'] + r['avance_global_apparel']
            + r['avance_global_vittoria'] + r['avance_global_bold'],
            2
        )

    return resultados


def cerrar_multimarcas_temporada(etiqueta: str, dry_run: bool = True) -> dict:
    """Cierra Multimarcas para una temporada: calcula cada cliente de
    `clientes_multimarcas` acotado a [fecha_inicio, fecha_fin] de esa
    temporada, y archiva en `multimarcas_historico` (dry_run=False). NUNCA
    escribe en `multimarcas` (la tabla en vivo) -- mismo cuidado que
    cerrar_temporada_completa en routes/temporadas.py."""
    conexion = obtener_conexion()
    cur_dict = conexion.cursor(dictionary=True)
    cur = conexion.cursor()

    procesados = 0
    preview = []

    try:
        cur_dict.execute("SELECT fecha_inicio, fecha_fin FROM temporadas WHERE etiqueta = %s", (etiqueta,))
        temporada_row = cur_dict.fetchone()
        if not temporada_row:
            raise ValueError(f"Temporada '{etiqueta}' no registrada en la tabla temporadas")

        if not dry_run:
            cur_dict.execute("SELECT COUNT(*) AS n FROM multimarcas_historico WHERE temporada = %s", (etiqueta,))
            if cur_dict.fetchone()['n'] > 0:
                raise ValueError(
                    f"Multimarcas de la temporada '{etiqueta}' ya fue archivada anteriormente. "
                    "No se puede volver a ejecutar el cierre real -- duplicaria las filas."
                )

        f_inicio = str(temporada_row['fecha_inicio'])
        f_fin = str(temporada_row['fecha_fin'])

        cur_dict.execute("SELECT id, clave, evac, cliente_razon_social FROM clientes_multimarcas")
        clientes = cur_dict.fetchall()

        valores_por_id = _calcular_valores_multimarcas_todos(cur_dict, clientes, f_inicio, f_fin)

        filas = []
        for c in clientes:
            valores = valores_por_id[c['id']]
            filas.append({**c, **valores})
            procesados += 1
            if len(preview) < 3:
                preview.append({'clave': c['clave'], 'avance_global': valores['avance_global']})

        if not dry_run:
            columnas_mes = [f'total_facturas_{n}' for n in _NOMBRES_MESES_TEMPORADA]
            columnas_fijas = [
                'temporada', 'id_multimarca', 'clave', 'evac', 'cliente_razon_social',
                'avance_global', 'avance_global_scott', 'avance_global_syncros',
                'avance_global_apparel', 'avance_global_vittoria', 'avance_global_bold',
            ]
            todas_columnas = columnas_fijas + columnas_mes

            cur.executemany(f"""
                INSERT INTO multimarcas_historico ({', '.join(todas_columnas)})
                VALUES ({', '.join(['%s'] * len(todas_columnas))})
            """, [
                (
                    etiqueta, f['id'], f['clave'], f['evac'], f['cliente_razon_social'],
                    f['avance_global'], f['avance_global_scott'], f['avance_global_syncros'],
                    f['avance_global_apparel'], f['avance_global_vittoria'], f['avance_global_bold'],
                    *(f[c] for c in columnas_mes)
                )
                for f in filas
            ])
            conexion.commit()
    except Exception:
        conexion.rollback()
        raise
    finally:
        cur_dict.close()
        cur.close()
        conexion.close()

    return {"clientes_procesados": procesados, "preview": preview}


def _requiere_admin_multimarcas(request) -> tuple:
    """Devuelve (payload, None) si el token es de admin, o (None, (body, status))
    con la respuesta de error a devolver si no."""
    auth_header = request.headers.get('Authorization', '')
    raw_token = auth_header.split(' ')[1] if ' ' in auth_header else None
    if not raw_token:
        return None, ({"error": "No autorizado"}, 401)

    payload = verificar_token(raw_token)
    if not payload:
        return None, ({"error": "Sesión expirada, por favor inicia sesión de nuevo"}, 401)

    rol = payload.get('rol')
    try:
        es_admin = int(rol) == 1
    except (TypeError, ValueError):
        es_admin = False
    if not es_admin:
        return None, ({"error": "Solo administradores pueden cerrar la temporada"}, 403)

    return payload, None


@multimarcas_bp.route('/cerrar-multimarcas-temporada', methods=['POST'])
def cerrar_multimarcas_temporada_endpoint():
    _payload, error = _requiere_admin_multimarcas(request)
    if error:
        body, status = error
        return jsonify(body), status

    data = request.get_json() or {}
    etiqueta = data.get('etiqueta')
    dry_run = data.get('dry_run', True)
    if not etiqueta:
        return jsonify({'error': 'Se requiere etiqueta (ej. "2025-2026")'}), 400
    try:
        resultado = cerrar_multimarcas_temporada(etiqueta, dry_run=dry_run)
        return jsonify(resultado), 200
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logging.exception('Error en cerrar_multimarcas_temporada_endpoint')
        return jsonify({'error': str(e)}), 500


@multimarcas_bp.route('/datos_multimarcas_historico', methods=['GET'])
def obtener_datos_multimarcas_historico():
    """Histórico de Multimarcas. Filtra por ?temporada=2025-2026 (opcional)."""
    temporada = request.args.get('temporada')
    conexion = None
    cursor = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)
        if temporada:
            cursor.execute(
                "SELECT * FROM multimarcas_historico WHERE temporada = %s ORDER BY fecha_snapshot DESC",
                (temporada,)
            )
        else:
            cursor.execute("SELECT * FROM multimarcas_historico ORDER BY fecha_snapshot DESC")
        resultados = cursor.fetchall()
        for fila in resultados:
            for key, value in fila.items():
                if isinstance(value, Decimal):
                    fila[key] = float(value)
        return jsonify(resultados), 200
    except Exception as e:
        logging.exception("Error en obtener_datos_multimarcas_historico")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()