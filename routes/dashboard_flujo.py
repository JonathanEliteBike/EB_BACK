from flask import Blueprint, jsonify, request
from db_conexion import obtener_conexion
from decimal import Decimal
from datetime import date, datetime
from collections import defaultdict
import calendar
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
from io import BytesIO
from flask import send_file
from openpyxl.utils import get_column_letter

# Importamos utilidades
from utils.jwt_utils import registrar_auditoria, verificar_token
import logging

try:
    from utils.odoo_utils import obtener_saldo_cuenta_odoo
except ImportError:
    from utils.odoo_utils import obtener_saldo_cuenta_odoo

dashboard_flujo_bp = Blueprint('dashboard_flujo_bp', __name__, url_prefix='/flujo')

# ==============================================================================
# 1. LECTURA: TABLERO MENSUAL (USANDO TABLAS UNIFICADAS)
# ==============================================================================
@dashboard_flujo_bp.route('/tablero-mensual', methods=['GET'])
def obtener_tablero_mensual():
    conexion = None
    try:
        fecha_reporte = request.args.get('fecha', '2026-01-01') 
        
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)
        
        # CAMBIO: Usamos cat_conceptos_unificados y flujo_valores_unificados
        sql = """
            SELECT 
                c.id_concepto,
                c.nombre_concepto,
                c.categoria,
                c.orden_reporte,
                COALESCE(v.monto_proyectado, 0) as proyectado,
                COALESCE(v.monto_real, 0) as real_val
            FROM cat_conceptos_unificados c
            LEFT JOIN flujo_valores_unificados v 
                ON c.id_concepto = v.id_concepto 
                AND v.fecha_reporte = %s
            ORDER BY c.orden_reporte ASC
        """
        
        cursor.execute(sql, (fecha_reporte,))
        registros = cursor.fetchall()
        
        data_final = []
        
        for row in registros:
            proyectado = float(row['proyectado']) if isinstance(row['proyectado'], Decimal) else float(row['proyectado'])
            real_val = float(row['real_val']) if isinstance(row['real_val'], Decimal) else float(row['real_val'])
            diferencia = real_val - proyectado
            
            data_final.append({
                "id": row['id_concepto'],
                "concepto": row['nombre_concepto'],
                "categoria": row['categoria'],
                "orden": row['orden_reporte'],
                "col_proyectado": proyectado,
                "col_real": real_val,
                "col_diferencia": diferencia,
                # Alerta visual simple: Negativo en Ingresos es malo
                "alerta": "negativa" if diferencia < 0 and 'Ingresos' in row['categoria'] else "normal"
            })

        return jsonify({
            "mes_reporte": fecha_reporte,
            "datos": data_final
        }), 200

    except Exception as e:
        logging.exception("Error en tablero mensual")
        return jsonify({'error': str(e)}), 500
    finally:
        if conexion:
            cursor.close()
            conexion.close()

# ==============================================================================
# 2. LECTURA: PROYECCIÓN ANUAL (USANDO TABLAS UNIFICADAS)
# ==============================================================================
@dashboard_flujo_bp.route('/proyeccion-anual', methods=['GET'])
def obtener_proyeccion_anual():
    conexion = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)

        # CAMBIO: Tabla unificada
        cursor.execute("SELECT DISTINCT fecha_reporte FROM flujo_valores_unificados ORDER BY fecha_reporte ASC")
        fechas_db = cursor.fetchall()
        columnas_fechas = [f['fecha_reporte'].isoformat() for f in fechas_db]

        # CAMBIO: Tablas unificadas
        sql = """
            SELECT 
                c.id_concepto, 
                c.nombre_concepto, 
                c.categoria,
                c.orden_reporte,
                v.fecha_reporte,
                v.monto_proyectado,
                v.monto_real
            FROM cat_conceptos_unificados c
            LEFT JOIN flujo_valores_unificados v ON c.id_concepto = v.id_concepto
            ORDER BY c.orden_reporte ASC, v.fecha_reporte ASC
        """
        cursor.execute(sql)
        data_raw = cursor.fetchall()

        filas_dict = defaultdict(lambda: {'meta': {}, 'valores': {}})
        
        for row in data_raw:
            id_c = row['id_concepto']
            if not filas_dict[id_c]['meta']:
                filas_dict[id_c]['meta'] = {
                    'concepto': row['nombre_concepto'],
                    'categoria': row['categoria'],
                    'orden': row['orden_reporte']
                }
            
            if row['fecha_reporte']:
                fecha_key = row['fecha_reporte'].isoformat()
                p = float(row['monto_proyectado'] or 0)
                r = float(row['monto_real'] or 0)
                
                filas_dict[id_c]['valores'][fecha_key] = {
                    'proyectado': p,
                    'real': r,
                    'diferencia': r - p
                }

        filas_finales = []
        for id_c in sorted(filas_dict.keys(), key=lambda k: filas_dict[k]['meta']['orden']):
            obj = filas_dict[id_c]
            datos_ordenados = []
            for fecha in columnas_fechas:
                val = obj['valores'].get(fecha, {'proyectado': 0.0, 'real': 0.0, 'diferencia': 0.0})
                datos_ordenados.append(val)

            filas_finales.append({
                'concepto': obj['meta']['concepto'],
                'categoria': obj['meta']['categoria'],
                'datos': datos_ordenados 
            })

        return jsonify({
            'columnas': columnas_fechas,
            'filas': filas_finales
        }), 200

    except Exception as e:
        logging.exception("Error en proyeccion anual")
        return jsonify({'error': str(e)}), 500
    finally:
        if conexion: conexion.close()

# ==============================================================================
# 3. ESCRITURA: GUARDAR VALOR Y RECALCULAR (LÓGICA CORREGIDA)
# ==============================================================================
@dashboard_flujo_bp.route('/guardar-valor', methods=['POST'])
def guardar_valor():
    auth_header = request.headers.get('Authorization')
    token = auth_header.split(" ")[1] if auth_header and " " in auth_header else None
    
    if not token or not verificar_token(token):
        return jsonify({'error': 'Tu sesión ha expirado.'}), 401

    data = request.get_json()
    conexion = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor()

        id_concepto = data['id_concepto']
        fecha = data['fecha']
        monto = data['monto']
        tipo = data.get('tipo', 'real') 

        # 1. Guardar el valor en la tabla UNIFICADA
        actualizar_valor_bd(cursor, id_concepto, fecha, monto, tipo)
        
        # 2. Obtener nombre para auditoría
        cursor.execute("SELECT nombre_concepto FROM cat_conceptos_unificados WHERE id_concepto = %s", (id_concepto,))
        res_nombre = cursor.fetchone()
        nombre_concepto = res_nombre[0] if res_nombre else f"Concepto {id_concepto}"
        registrar_auditoria(cursor, 'EDICION_CELDA', 'flujo_valores_unificados', id_concepto, f"Editó {nombre_concepto} a ${monto}")

        # 3. CORRECCIÓN: Automatización del recálculo propagado
        # Usamos la nueva función recalcular_formulas_flujo definida abajo
        f_obj = datetime.strptime(fecha, "%Y-%m-%d")
        
        # Recalcula desde el mes editado hasta diciembre para propagar saldos iniciales
        for m in range(f_obj.month, 13):
            recalcular_formulas_flujo(conexion, f_obj.year, m)

        conexion.commit()
        return jsonify({"mensaje": "Valor actualizado y saldos propagados correctamente"}), 200

    except Exception as e:
        if conexion: conexion.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        if conexion: conexion.close()

# ==============================================================================
# 4. SINCRONIZACIÓN CON ODOO (ACTUALIZADA CON CÁLCULO DE MES ANTERIOR)
# ==============================================================================
@dashboard_flujo_bp.route('/sincronizar-odoo', methods=['POST'])
def sincronizar_odoo():
    data = request.get_json()
    try:
        anio = int(data.get('anio'))
        mes = int(data.get('mes'))
    except:
        return jsonify({"mensaje": "Año o mes inválidos"}), 400

    # Configuración de fechas del mes actual
    fecha_inicio = f"{anio}-{mes:02d}-01"
    ultimo_dia = calendar.monthrange(anio, mes)[1]
    fecha_fin = f"{anio}-{mes:02d}-{ultimo_dia}"
    
    # 🚀 NUEVO: Configuración de fechas del mes anterior para el cálculo especial
    if mes == 1:
        mes_ant, anio_ant = 12, anio - 1
    else:
        mes_ant, anio_ant = mes - 1, anio
    ultimo_dia_ant = calendar.monthrange(anio_ant, mes_ant)[1]
    fecha_fin_ant = f"{anio_ant}-{mes_ant:02d}-{ultimo_dia_ant}"
    fecha_inicio_ant = f"{anio_ant}-{mes_ant:02d}-01"
    
    conexion = None
    try:
        conexion = obtener_conexion()
        cursor_read = conexion.cursor(dictionary=True)
        cursor_update = conexion.cursor()
        
        # 1. Traemos TODOS los conceptos (tengan o no código viejo)
        cursor_read.execute("SELECT id_concepto, categoria, codigo_cuenta_odoo FROM cat_conceptos_unificados")
        conceptos = cursor_read.fetchall()
        
        for c in conceptos:
            id_concepto = c['id_concepto']
            categoria = c['categoria']
            codigo_viejo = c['codigo_cuenta_odoo'] # Por si acaso
            
            total_real_concepto = 0.0
            se_proceso_informacion = False

            # -----------------------------------------------------------
            # PASO A: Buscar configuración en la NUEVA TABLA (Prioridad)
            # -----------------------------------------------------------
            cursor_read.execute("""
                SELECT codigo_cuenta_odoo, columna_saldo, palabras_excluidas, nomenclatura_ref, palabras_incluidas 
                FROM detalles_cuentas_odoo 
                WHERE id_concepto = %s
            """, (id_concepto,))
            
            detalles = cursor_read.fetchall()

            if detalles:
                se_proceso_informacion = True 
                
                for det in detalles:
                    codigo = det['codigo_cuenta_odoo']
                    columna = det['columna_saldo']
                    excluir = det['palabras_excluidas']
                    incluir = det['nomenclatura_ref'] 
                    palabras_req = det.get('palabras_incluidas')
                    
                    # 1. Obtenemos el saldo Odoo del mes actual
                    saldo_parcial = obtener_saldo_cuenta_odoo(
                        codigo_cuenta=codigo, 
                        fecha_inicio=fecha_inicio, 
                        fecha_fin=fecha_fin, 
                        columna_saldo=columna,
                        excluir_txt=excluir,
                        incluir_txt=incluir,
                        palabras_incluidas=palabras_req
                    )

                    # 🚀 2. CÁLCULO ESPECIAL DE RESTA (Solo para IDs 101 y 102)
                    if id_concepto in [101, 102]:
                        saldo_mes_anterior = obtener_saldo_cuenta_odoo(
                            codigo_cuenta=codigo, 
                            fecha_inicio=fecha_inicio_ant, 
                            fecha_fin=fecha_fin_ant, 
                            columna_saldo=columna,
                            excluir_txt=excluir,
                            incluir_txt=incluir,
                            palabras_incluidas=palabras_req
                        )
                        # Restamos el mes anterior al mes actual para tener el neto
                        saldo_parcial = saldo_parcial - saldo_mes_anterior

                    total_real_concepto += saldo_parcial

            # -----------------------------------------------------------
            # PASO B: Fallback (Lógica Vieja) si no hay tabla nueva
            # -----------------------------------------------------------
            elif codigo_viejo and codigo_viejo.strip() != '':
                se_proceso_informacion = True
                
                es_ingreso = not any(x in categoria.lower() for x in ['egreso', 'gasto', 'costo', 'pasivo', 'proveedor', 'importacion'])
                
                total_real_concepto = obtener_saldo_cuenta_odoo(
                    codigo_cuenta=codigo_viejo, 
                    fecha_inicio=fecha_inicio, 
                    fecha_fin=fecha_fin, 
                    es_ingreso=es_ingreso,
                    columna_saldo='Debe'
                )

            # -----------------------------------------------------------
            # PASO C: Guardar en Base de Datos
            # -----------------------------------------------------------
            if se_proceso_informacion:
                actualizar_valor_bd(cursor_update, id_concepto, fecha_inicio, total_real_concepto, 'real')

        # 2. AUTOMATIZACIÓN: Propagar recálculo
        for m in range(mes, 13):
            recalcular_formulas_flujo(conexion, anio, m)

        conexion.commit()
        return jsonify({"mensaje": "Sincronización Multi-Código finalizada correctamente"}), 200

    except Exception as e:
        if conexion: conexion.rollback()
        print(f"Error en sync: {e}") 
        return jsonify({'error': str(e)}), 500
    finally:
        if conexion: conexion.close()

# ==============================================================================
# 5. GENERACIÓN DE REPORTES EN EXCEL
# ==============================================================================
@dashboard_flujo_bp.route('/reporte-excel', methods=['GET'])
def exportar_excel():
    conexion = None
    try:
        fecha_inicio = request.args.get('inicio')
        fecha_fin = request.args.get('fin')

        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)
        
        sql = """
            SELECT 
                COALESCE(DATE_FORMAT(v.fecha_reporte, '%Y-%m-%d'), %s) AS Fecha,
                c.categoria AS Categoria,
                c.nombre_concepto AS Concepto,
                CAST(COALESCE(v.monto_proyectado, 0) AS DECIMAL(15,2)) AS Proyectado,
                CAST(COALESCE(v.monto_real, 0) AS DECIMAL(15,2)) AS Monto_Real,
                CAST((COALESCE(v.monto_real, 0) - COALESCE(v.monto_proyectado, 0)) AS DECIMAL(15,2)) AS Diferencia
            FROM cat_conceptos_unificados c
            LEFT JOIN flujo_valores_unificados v 
                ON c.id_concepto = v.id_concepto 
                AND v.fecha_reporte BETWEEN %s AND %s
            ORDER BY v.fecha_reporte DESC, c.orden_reporte ASC
        """
        
        cursor.execute(sql, (fecha_inicio, fecha_inicio, fecha_fin))
        data = cursor.fetchall()
        
        if not data:
            return jsonify({'error': 'No hay datos'}), 404

        df = pd.DataFrame(data)

        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Flujo de Efectivo')
            worksheet = writer.sheets['Flujo de Efectivo']

            currency_format = '$#,##0.00'
            
            for row in range(2, len(df) + 2):
                for col in ['D', 'E', 'F']:
                    cell = worksheet[f'{col}{row}']
                    cell.number_format = currency_format

            for idx, col in enumerate(df.columns):
                max_len = max(df[col].astype(str).map(len).max(), len(col)) + 2
                worksheet.column_dimensions[get_column_letter(idx + 1)].width = max_len

        output.seek(0)
        return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                         as_attachment=True, download_name=f"Reporte_Flujo_{fecha_inicio}.xlsx")
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conexion: conexion.close()

@dashboard_flujo_bp.route('/verificar-permiso/<int:id_usuario>', methods=['GET'])
def verificar_permiso_joker(id_usuario):
    conexion = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)
        sql = "SELECT flujo FROM usuarios WHERE id = %s"
        cursor.execute(sql, (id_usuario,))
        usuario = cursor.fetchone()
        
        if not usuario:
            return jsonify({"permiso": 0, "error": "Usuario no encontrado"}), 404
            
        return jsonify({"permiso": usuario['flujo']}), 200

    except Exception as e:
        logging.exception("Error verificando permiso")
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conexion: conexion.close()

# ==============================================================================
# LÓGICA DE CÁLCULO DE FÓRMULAS
# ==============================================================================

def actualizar_valor_bd(cursor, id_concepto, fecha, monto, tipo='real'):
    columna = 'monto_real' if tipo == 'real' else 'monto_proyectado'
    
    # 1. Verificar si existe
    check_sql = "SELECT id_valor FROM flujo_valores_unificados WHERE id_concepto = %s AND fecha_reporte = %s"
    cursor.execute(check_sql, (id_concepto, fecha))
    registro = cursor.fetchone()
    
    if registro:
        uid = registro['id_valor'] if isinstance(registro, dict) else registro[0]
        cursor.execute(f"UPDATE flujo_valores_unificados SET {columna} = %s WHERE id_valor = %s", (monto, uid))
    else:
        v_r = monto if tipo == 'real' else 0
        v_p = monto if tipo == 'proyectado' else 0
        sql_in = "INSERT INTO flujo_valores_unificados (id_concepto, fecha_reporte, monto_real, monto_proyectado) VALUES (%s, %s, %s, %s)"
        cursor.execute(sql_in, (id_concepto, fecha, v_r, v_p))

def recalcular_formulas_flujo(conexion, anio, mes):
    logging.info("Recalculando UNIFICADO para %s/%s", mes, anio)
    cursor = conexion.cursor(dictionary=True)
    
    fecha_actual = f"{anio}-{mes:02d}-01"
    
    # Calcular mes anterior
    if mes == 1:
        mes_ant, anio_ant = 12, anio - 1
    else:
        mes_ant, anio_ant = mes - 1, anio
    fecha_anterior = f"{anio_ant}-{mes_ant:02d}-01"

    try:
        # ==========================================
        # 1. DEFINICIÓN DE GRUPOS 
        # ==========================================
        
        # --- CONCEPTOS DE SALDO Y ENTRADAS ---
        ID_SALDO_INICIAL = 1
        ID_SALDO_FINAL = 99
        
        ID_VENTAS = 2
        ID_RECUPERACION = 3  # Espejo de ventas
        
        # Otros Ingresos
        IDS_OTROS_INGRESOS = [6, 7, 101, 102, 103, 104]
        
        ID_TOTAL_ENTRADAS = 8 # Suma de Saldo Inicial + Recuperacion + Otros

        # --- CONCEPTOS DE SALIDAS (LÓGICA DINÁMICA) ---
        
        # 🚨 SEPARAMOS LOS GASTOS PARA LA NUEVA REGLA
        # Base (Siempre se suman): Importaciones(24), Anticipos(25), Fijos(40), Nomina(41), PTU(42), Impuestos(43)
        IDS_OPERATIVOS_BASE = [24, 25, 40, 41, 42, 43, 105]
        
        # Los que cambian según la columna:
        ID_COMPRA_DIVISAS = 20
        IDS_PROVEEDORES = [21, 22, 23] 
        
        ID_RESUMEN_GASTOS_OP = 49

        # GRUPO 2: FINANCIEROS Y OTROS
        IDS_FINANCIEROS = [50, 52, 100]

        # GRUPO 3: MOVIMIENTOS / INVERSIONES
        IDS_MOVIMIENTOS = [60]
        
        ID_TOTAL_SALIDAS = 90 # Suma de todo lo anterior

        # ==========================================
        # 2. CARGA DE DATOS Y ARRASTRE
        # ==========================================
        hoy = datetime.now()
        mes_anterior_terminado = False
        
        if anio_ant < hoy.year:
            mes_anterior_terminado = True
        elif anio_ant == hoy.year and mes_ant < hoy.month:
            mes_anterior_terminado = True

        sql_ant = "SELECT monto_real, monto_proyectado FROM flujo_valores_unificados WHERE id_concepto = %s AND fecha_reporte = %s"
        cursor.execute(sql_ant, (ID_SALDO_FINAL, fecha_anterior))
        res_prev = cursor.fetchone()
        
        saldo_arrastre_real = 0.0
        saldo_arrastre_proyectado = 0.0
        
        if res_prev:
            if mes_anterior_terminado:
                saldo_arrastre_real = float(res_prev['monto_real'] or 0)
                saldo_arrastre_proyectado = float(res_prev['monto_real'] or 0)
            else:
                saldo_arrastre_real = float(res_prev['monto_real'] or 0)
                saldo_arrastre_proyectado = float(res_prev['monto_proyectado'] or 0)

        # Valores actuales del mes
        sql_fetch = "SELECT id_concepto, monto_real, monto_proyectado FROM flujo_valores_unificados WHERE fecha_reporte = %s"
        cursor.execute(sql_fetch, (fecha_actual,))
        rows = cursor.fetchall()
        
        val_r, val_p = defaultdict(float), defaultdict(float)
        for r in rows:
            val_r[r['id_concepto']] = float(r['monto_real'] or 0)
            val_p[r['id_concepto']] = float(r['monto_proyectado'] or 0)

        # Aplicar Arrastre
        if not (anio == 2026 and mes == 1):
            val_r[ID_SALDO_INICIAL] = saldo_arrastre_real
            val_p[ID_SALDO_INICIAL] = saldo_arrastre_proyectado
            
            actualizar_valor_bd(cursor, ID_SALDO_INICIAL, fecha_actual, saldo_arrastre_real, 'real')
            actualizar_valor_bd(cursor, ID_SALDO_INICIAL, fecha_actual, saldo_arrastre_proyectado, 'proyectado')

        # ==========================================
        # 3. CÁLCULOS MATEMÁTICOS
        # ==========================================
        for tipo in ['real', 'proyectado']:
            v_map = val_r if tipo == 'real' else val_p
            
            # --- A. INGRESOS ---
            actualizar_valor_bd(cursor, ID_RECUPERACION, fecha_actual, v_map[ID_VENTAS], tipo)
            v_map[ID_RECUPERACION] = v_map[ID_VENTAS]
            
            s_otros = sum(v_map[i] for i in IDS_OTROS_INGRESOS)
            total_entradas = v_map[ID_SALDO_INICIAL] + v_map[ID_RECUPERACION] + s_otros
            
            actualizar_valor_bd(cursor, ID_TOTAL_ENTRADAS, fecha_actual, total_entradas, tipo)
            v_map[ID_TOTAL_ENTRADAS] = total_entradas
            
            # --- B. SALIDAS ---
            
            # 🚀 AQUÍ APLICAMOS LA NUEVA REGLA MATEMÁTICA
            if tipo == 'proyectado':
                # Proyectado: Toma la base + PROVEEDORES (21, 22, 23). IGNORA Divisas (20).
                ids_gastos_operativos = IDS_OPERATIVOS_BASE + IDS_PROVEEDORES
            else:
                # Real: Toma la base + COMPRA DE DIVISAS (20). IGNORA Proveedores (21, 22, 23).
                ids_gastos_operativos = IDS_OPERATIVOS_BASE + [ID_COMPRA_DIVISAS]

            # Hacemos la suma con la lista que haya ganado el IF
            suma_operativos = sum(v_map[i] for i in ids_gastos_operativos)
            
            actualizar_valor_bd(cursor, ID_RESUMEN_GASTOS_OP, fecha_actual, suma_operativos, tipo)
            v_map[ID_RESUMEN_GASTOS_OP] = suma_operativos

            suma_financieros = sum(v_map[i] for i in IDS_FINANCIEROS)
            suma_movimientos = sum(v_map[i] for i in IDS_MOVIMIENTOS)
            
            gran_total_salidas = suma_operativos + suma_financieros + suma_movimientos
            actualizar_valor_bd(cursor, ID_TOTAL_SALIDAS, fecha_actual, gran_total_salidas, tipo)
            v_map[ID_TOTAL_SALIDAS] = gran_total_salidas
            
            # --- C. SALDO FINAL (ID 99) ---
            disponible_final = total_entradas - gran_total_salidas
            actualizar_valor_bd(cursor, ID_SALDO_FINAL, fecha_actual, disponible_final, tipo)

    except Exception as e:
        logging.exception("Error en recalcular_formulas_flujo para %s/%s", mes, anio)