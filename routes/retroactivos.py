from flask import Blueprint, jsonify
from db_conexion import obtener_conexion
import decimal
import traceback
from datetime import date, datetime
from utils.odoo_utils import get_odoo_models, ODOO_DB, ODOO_PASSWORD 

retroactivos_bp = Blueprint('retroactivos', __name__, url_prefix='')

# ==============================================================================
# 1. FUNCIÓN MAESTRA: OBTENER DEDUCCIONES (MOTOR BLINDADO Y CON FECHAS DINÁMICAS)
# ==============================================================================
def obtener_deducciones_odoo(claves_db, fechas_por_clave):
    resultado_odoo = get_odoo_models()
    uid = resultado_odoo[0]
    models = resultado_odoo[1]
    if not uid: return {}

    print("Mapeando IDs internos de Odoo con las Claves de la Base de Datos...")
    partners_odoo = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'res.partner', 'search_read',
        [[]], {'fields': ['id', 'name', 'ref', 'parent_id']})

    odoo_id_to_clave = {}
    
    for p in partners_odoo:
        ref_odoo = str(p.get('ref', '')).strip().upper()
        name_odoo = str(p.get('name', '')).strip().upper()

        for clave in claves_db:
            if ref_odoo == clave or ref_odoo == f"{clave}-CA" or clave in name_odoo:
                odoo_id_to_clave[p['id']] = clave 
                break

    redirecciones = {
        'VICTOR HUGO VILLANUEVA GUZMAN': 'LC657',
        'MARCO TULIO ANDRADE NAVARRO': 'JC539',
        'NARUCO': 'LC625'
    }
    for p in partners_odoo:
        name_odoo = str(p.get('name', '')).strip().upper()
        if p['id'] not in odoo_id_to_clave and name_odoo in redirecciones:
            odoo_id_to_clave[p['id']] = redirecciones[name_odoo]

    # Segunda pasada: mapear cuentas hijas (contactos secundarios) al mismo CLAVE del padre.
    # Esto cubre casos como "Cycling riding B2B" que es hija de CYCLING RIDING DE MEXICO (GD380).
    for p in partners_odoo:
        if p['id'] not in odoo_id_to_clave and p.get('parent_id'):
            parent_id = p['parent_id'][0] if isinstance(p['parent_id'], (list, tuple)) else p['parent_id']
            if parent_id and parent_id in odoo_id_to_clave:
                odoo_id_to_clave[p['id']] = odoo_id_to_clave[parent_id]

    resultados_por_clave = {clave: {'nc': 0.0, 'garantia': 0.0, 'ofertado': 0.0} for clave in claves_db}

    # FUNCIÓN INTERNA PARA AGREGAR Y VALIDAR LA FECHA EXACTA DEL CLIENTE
    def agregar_valor(partner_id_odoo, tipo, valor, fecha_linea):
        clave_encontrada = odoo_id_to_clave.get(partner_id_odoo)
        if not clave_encontrada or clave_encontrada not in resultados_por_clave: 
            return

        # Validación de rango de fechas personalizado por cliente
        rango = fechas_por_clave.get(clave_encontrada)
        if rango and rango['inicio'] and rango['fin'] and fecha_linea:
            if not (rango['inicio'] <= fecha_linea <= rango['fin']):
                return # La factura/NC está fuera del periodo de este cliente, se ignora

        resultados_por_clave[clave_encontrada][tipo] += valor

    lista_ids_validos = list(odoo_id_to_clave.keys())
    if not lista_ids_validos:
        return resultados_por_clave

    try:
        # Calculamos la fecha mínima y máxima global para no pedirle a Odoo toda su historia
        todas_inicios = [f['inicio'] for f in fechas_por_clave.values() if f['inicio']]
        todas_fines = [f['fin'] for f in fechas_por_clave.values() if f['fin']]
        
        # Si por alguna razón no hay fechas, ponemos un fallback por defecto
        min_date = min(todas_inicios) if todas_inicios else '2025-07-01'
        max_date = max(todas_fines) if todas_fines else '2026-06-30'

        # Pre-cargamos los IDs de la cuenta garantía (402.01.05)
        ids_cuenta_garantia = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD, 'account.account', 'search',
            [[('code', '=', '402.01.05')]]
        )

        # A. GARANTÍAS — identificamos qué NC tienen línea en cuenta 402.01.05,
        # luego leemos amount_total de account.move (incluye IVA correctamente).
        lineas_garantia_raw = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'account.move.line', 'search_read',
            [[
                ('move_id.move_type', '=', 'out_refund'),
                ('move_id.state', '=', 'posted'),
                ('move_id.invoice_date', '>=', min_date),
                ('move_id.invoice_date', '<=', max_date),
                ('partner_id', 'in', lista_ids_validos),
                ('account_id', 'in', ids_cuenta_garantia)
            ]],
            {'fields': ['move_id', 'partner_id', 'date']}
        )

        move_ids_garantia = set()
        move_meta_garantia = {}  # move_id -> (partner_id, date)
        for l in lineas_garantia_raw:
            mid = l['move_id'][0]
            move_ids_garantia.add(mid)
            if mid not in move_meta_garantia:
                pid = l['partner_id'][0] if l['partner_id'] else None
                move_meta_garantia[mid] = (pid, l.get('date'))

        if move_ids_garantia:
            facturas_garantia = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'account.move', 'read',
                [list(move_ids_garantia)], {'fields': ['id', 'amount_total']}
            )
            for f in facturas_garantia:
                pid, fecha = move_meta_garantia.get(f['id'], (None, None))
                if pid:
                    agregar_valor(pid, 'garantia', f['amount_total'], fecha)

        # B. PRODUCTOS OFERTADOS (incluye etiqueta DEMO)
        domain_ofertado = [
            ('move_id.move_type', '=', 'out_invoice'),
            ('move_id.state', '=', 'posted'),
            ('move_id.invoice_date', '>=', min_date),
            ('move_id.invoice_date', '<=', max_date),
            ('quantity', '!=', 0),
            ('partner_id', 'in', lista_ids_validos),
            '|',
                ('product_id.product_tmpl_id.product_tag_ids.name', 'ilike', 'Producto Ofertado'),
                ('product_id.product_tmpl_id.product_tag_ids.name', 'ilike', 'DEMO')
        ]
        lineas_ofertado = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'account.move.line', 'search_read',
            [domain_ofertado], {'fields': ['partner_id', 'price_subtotal', 'date']})

        for linea in lineas_ofertado:
            if linea['partner_id']:
                agregar_valor(linea['partner_id'][0], 'ofertado', float(linea['price_subtotal']), linea.get('date'))

        # C. NOTAS DE CRÉDITO — leemos amount_total de account.move (con IVA).
        # Se excluyen las NC ya clasificadas como Garantías y las de APLANT/ANTICIPO.
        todas_nc_moves = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'account.move', 'search_read',
            [[
                ('move_type', '=', 'out_refund'),
                ('state', '=', 'posted'),
                ('invoice_date', '>=', min_date),
                ('invoice_date', '<=', max_date),
                ('partner_id', 'in', lista_ids_validos)
            ]],
            {'fields': ['id', 'partner_id', 'amount_total', 'invoice_date', 'name', 'ref']}
        )

        for nc in todas_nc_moves:
            if nc['id'] in move_ids_garantia:
                continue  # Es garantía, ya contada arriba
            pid = nc['partner_id'][0] if nc['partner_id'] else None
            if not pid:
                continue
            ref_texto = (str(nc.get('name') or '') + ' ' + str(nc.get('ref') or '')).upper()
            if 'APLANT' in ref_texto or 'ANTICIPO' in ref_texto:
                continue
            agregar_valor(pid, 'nc', nc['amount_total'], nc.get('invoice_date'))

        return resultados_por_clave

    except Exception as e:
        print(f"Error Odoo: {e}")
        traceback.print_exc()
        return {}


# ==============================================================================
# 2. FUNCIÓN DE SINCRONIZACIÓN AUTOMÁTICA
# ==============================================================================
def ejecutar_sincronizacion_y_calculos():
    conexion = obtener_conexion()
    cursor_dict = conexion.cursor(dictionary=True)
    cursor = conexion.cursor() 
    try:
        print("Auto-sincronizando Odoo y calculando matematicas...")
        
        # OBTENEMOS LAS CLAVES Y SUS FECHAS ESPECÍFICAS HACIENDO UN JOIN
        cursor_dict.execute("""
            SELECT 
                tr.CLAVE, 
                c.f_inicio, 
                c.f_fin 
            FROM tabla_retroactivos tr
            LEFT JOIN clientes c ON tr.CLIENTE = c.nombre_cliente
            WHERE tr.CLAVE NOT LIKE 'Integral%' AND tr.CLAVE IS NOT NULL
        """)
        resultados_db = cursor_dict.fetchall()
        
        claves_db = []
        fechas_por_clave = {}

        for row in resultados_db:
            clave = row['CLAVE'].strip().upper()
            claves_db.append(clave)
            
            # Formateamos las fechas
            ini = row['f_inicio'].strftime('%Y-%m-%d') if isinstance(row['f_inicio'], (date, datetime)) else (row['f_inicio'] or '2025-07-01')
            fin = row['f_fin'].strftime('%Y-%m-%d') if isinstance(row['f_fin'], (date, datetime)) else (row['f_fin'] or '2026-06-30')
            
            fechas_por_clave[clave] = {
                'inicio': ini,
                'fin': fin
            }

        datos_por_clave = obtener_deducciones_odoo(claves_db, fechas_por_clave)
        
        cursor.execute("UPDATE tabla_retroactivos SET notas_credito=0, garantias=0, productos_ofertados=0, NC='', FACT='', estatus='Pendiente'")

        for clave, valores in datos_por_clave.items():
            if valores['nc'] != 0 or valores['garantia'] != 0 or valores['ofertado'] != 0:
                cursor.execute("""
                    UPDATE tabla_retroactivos 
                    SET notas_credito = %s, garantias = %s, productos_ofertados = %s WHERE CLAVE = %s
                """, (valores['nc'], valores['garantia'], valores['ofertado'], clave))

        # === DICCIONARIO DE INTEGRALES ===
        integrales_map = {
            'Integral 1': ['EC216', 'JC539'],
            'Integral 2': ['GC411', 'MC679', 'MC677', 'LC657'],
            'Integral 3': ['LC625', 'LC627', 'LC626']
        }
        
        for clave_padre, claves_hijas in integrales_map.items():
            format_strings = ','.join(['%s'] * len(claves_hijas))
            query_suma = f"SELECT COALESCE(SUM(notas_credito), 0), COALESCE(SUM(garantias), 0), COALESCE(SUM(productos_ofertados), 0) FROM tabla_retroactivos WHERE CLAVE IN ({format_strings})"
            cursor.execute(query_suma, tuple(claves_hijas))
            suma = cursor.fetchone()
            if suma:
                cursor.execute("UPDATE tabla_retroactivos SET notas_credito = %s, garantias = %s, productos_ofertados = %s WHERE CLAVE = %s", (suma[0], suma[1], suma[2], clave_padre))

        cursor.execute("""
            UPDATE tabla_retroactivos
            SET 
                TOTAL_ACUMULADO = (COALESCE(COMPRA_GLOBAL_SCOTT, 0) + COALESCE(COMPRA_GLOBAL_APPAREL, 0) + COALESCE(COMPRA_GLOBAL_BOLD, 0)),
                compra_anual_crudo = (COALESCE(COMPRAS_TOTALES_CRUDO, 0) - COALESCE(notas_credito, 0) - COALESCE(garantias, 0)),
                compra_adicional = (COALESCE(COMPRAS_TOTALES_CRUDO, 0) - COALESCE(notas_credito, 0) - COALESCE(garantias, 0) - COALESCE(COMPRA_MINIMA_ANUAL, 0))
        """)
        
        # ==============================================================================
        # LÓGICA DINÁMICA DE PORCENTAJES POR CANTIDAD DE TIENDAS
        # ==============================================================================
        umbrales_por_tiendas = {
            1: [(5000000, 0.045), (2000000, 0.02), (800000, 0.01)],
            2: [(7500000, 0.045), (3000000, 0.02), (1200000, 0.01)],
            3: [(11250000, 0.045), (4500000, 0.02), (1800000, 0.01)],
            4: [(15000000, 0.045), (6000000, 0.02), (2400000, 0.01)],
            5: [(18750000, 0.045), (7500000, 0.02), (3000000, 0.01)],
            6: [(22500000, 0.045), (9000000, 0.02), (3600000, 0.01)],
        }

        casos_integrales = []
        # Leemos integrales_map para saber cuántas tiendas tiene cada Integral
        for clave_integral, tiendas in integrales_map.items():
            num_tiendas = len(tiendas)
            if num_tiendas in umbrales_por_tiendas:
                u = umbrales_por_tiendas[num_tiendas]
                caso = f"""
                    WHEN CLAVE = '{clave_integral}' AND CATEGORIA IN ('Partner Elite', 'Partner Elite Plus') THEN
                        CASE
                            WHEN compra_adicional >= {u[0][0]} THEN {u[0][1]}
                            WHEN compra_adicional >= {u[1][0]} THEN {u[1][1]}
                            WHEN compra_adicional >= {u[2][0]} THEN {u[2][1]}
                            ELSE 0.00
                        END
                """
                casos_integrales.append(caso)
        
        casos_sql = " ".join(casos_integrales)

        # Inyectamos los casos generados dinámicamente en el UPDATE principal
        query_porcentajes = f"""
            UPDATE tabla_retroactivos
            SET 
                porcentaje_retroactivo = CASE
                    {casos_sql}
                    -- Caso por defecto (1 tienda o sucursales normales)
                    ELSE
                        CASE
                            WHEN compra_adicional >= 5000000 THEN 0.045
                            WHEN compra_adicional >= 2000000 THEN 0.02
                            WHEN compra_adicional >= 800000 THEN 0.01
                            ELSE 0.00
                        END
                END,
                porcentaje_retroactivo_apparel = CASE
                    WHEN COALESCE(COMPRA_GLOBAL_APPAREL, 0) >= COALESCE(COMPRA_MINIMA_APPAREL, 0) AND COALESCE(COMPRA_MINIMA_APPAREL, 0) > 0 THEN
                        CASE WHEN CATEGORIA LIKE '%Partner Elite%' THEN 0.025 WHEN CATEGORIA = 'Partner' THEN 0.015 ELSE 0.00 END
                    ELSE 0.00
                END
        """
        cursor.execute(query_porcentajes)
        # ==============================================================================

        cursor.execute("""
            UPDATE tabla_retroactivos
            SET
                retroactivo_total = (COALESCE(porcentaje_retroactivo, 0) + COALESCE(porcentaje_retroactivo_apparel, 0)),
                importe = (COALESCE(importe_final, 0) * (COALESCE(porcentaje_retroactivo, 0) + COALESCE(porcentaje_retroactivo_apparel, 0)))
        """)

        conexion.commit()
    except Exception as e:
        if conexion: conexion.rollback()
        print(f"Error en auto-sync: {e}")
    finally:
        if cursor_dict: cursor_dict.close()
        if cursor: cursor.close()
        if conexion: conexion.close()

# ==============================================================================
# 3. ENDPOINT GET GLOBALES (Este es el que llama Angular)
# ==============================================================================
@retroactivos_bp.route('/retroactivos', methods=['GET'])
def obtener_retroactivos():
    # --- LA MAGIA ESTÁ AQUÍ ---
    # Llamamos a la función ANTES de hacer la consulta SELECT
    ejecutar_sincronizacion_y_calculos()

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT 
                id, id_previo, CLAVE, ZONA, CLIENTE, CATEGORIA,
                COMPRA_MINIMA_ANUAL, COMPRA_MINIMA_APPAREL,
                COMPRAS_TOTALES_CRUDO, META_MY26_CUMPLIDA,
                COMPRA_GLOBAL_SCOTT, COMPRA_GLOBAL_APPAREL, COMPRA_GLOBAL_BOLD,
                TOTAL_ACUMULADO, compra_anual_crudo, compra_adicional,
                notas_credito, garantias, productos_ofertados,
                bicicleta_demo, bicicletas_bold, importe_final,
                porcentaje_retroactivo, porcentaje_retroactivo_apparel,
                retroactivo_total, importe, estatus, fecha_aplicacion, NC, FACT
            FROM tabla_retroactivos
            WHERE COALESCE(CATEGORIA, '') != 'Distribuidor'
            ORDER BY CASE WHEN ZONA = 'A' THEN 1 WHEN ZONA = 'B' THEN 2 WHEN ZONA = 'GO' THEN 3 ELSE 4 END, CLIENTE ASC
        """)
        resultados = cursor.fetchall()

        for fila in resultados:
            for clave, valor in fila.items():
                if isinstance(valor, decimal.Decimal):
                    fila[clave] = float(valor)
                elif isinstance(valor, (datetime, date)):
                    fila[clave] = valor.strftime('%Y-%m-%d')
            
            m_anual = fila.get('COMPRA_MINIMA_ANUAL', 0)
            m_apparel = fila.get('COMPRA_MINIMA_APPAREL', 0)
            
            fila['porcentaje_avance_general'] = (fila.get('COMPRAS_TOTALES_CRUDO', 0) / m_anual) if m_anual > 0 else 0.0
            fila['porcentaje_avance_scott'] = (fila.get('COMPRA_GLOBAL_SCOTT', 0) / m_anual) if m_anual > 0 else 0.0
            fila['porcentaje_avance_apparel'] = (fila.get('COMPRA_GLOBAL_APPAREL', 0) / m_apparel) if m_apparel > 0 else 0.0
            
            fila['total_bicis_deduccion'] = fila.get('bicicleta_demo', 0) + fila.get('bicicletas_bold', 0)
            fila['acumulado_global_calculado'] = fila.get('COMPRAS_TOTALES_CRUDO', 0) - fila.get('notas_credito', 0) - fila.get('garantias', 0)
        
        return jsonify(resultados), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conexion: conexion.close()


# ==============================================================================
# 4. ENDPOINT POST (Por si mantienes el botón manual en Angular)
# ==============================================================================
@retroactivos_bp.route('/sincronizar_notas', methods=['POST'])
def sincronizar_notas_odoo():
    ejecutar_sincronizacion_y_calculos()
    return jsonify({"mensaje": "Sincronización exitosa"}), 200


# ==============================================================================
# 5. ENDPOINT GET INDIVIDUAL
# ==============================================================================
@retroactivos_bp.route('/retroactivo_cliente/<string:identificador>', methods=['GET'])
def obtener_retroactivo_individual(identificador):

    ejecutar_sincronizacion_y_calculos()

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)
    try:
        query = """
            SELECT 
                CLAVE, ZONA, CLIENTE, CATEGORIA,
                COMPRA_MINIMA_ANUAL, COMPRA_GLOBAL_SCOTT,
                COMPRA_MINIMA_APPAREL, COMPRA_GLOBAL_APPAREL,
                COMPRAS_TOTALES_CRUDO, notas_credito, garantias,
                productos_ofertados, bicicleta_demo, bicicletas_bold,
                importe_final, porcentaje_retroactivo, porcentaje_retroactivo_apparel,
                compra_adicional, retroactivo_total, importe, estatus, NC, FACT
            FROM tabla_retroactivos
            WHERE CLAVE = %s OR CLIENTE = %s
            LIMIT 1
        """
        cursor.execute(query, (identificador, identificador))
        cliente_data = cursor.fetchone()

        if not cliente_data:
            return jsonify({"mensaje": "Cliente no encontrado"}), 404

        for clave, valor in cliente_data.items():
            if isinstance(valor, decimal.Decimal):
                cliente_data[clave] = float(valor)
            elif valor is None:
                cliente_data[clave] = 0.0 if clave not in ['CLAVE','ZONA','CLIENTE','CATEGORIA','estatus','NC','FACT'] else ''

        minima_anual = cliente_data['COMPRA_MINIMA_ANUAL']
        minima_apparel = cliente_data['COMPRA_MINIMA_APPAREL']
        
        cliente_data['porcentaje_avance_general'] = (cliente_data['COMPRAS_TOTALES_CRUDO'] / minima_anual) if minima_anual > 0 else 0.0
        cliente_data['porcentaje_avance_scott'] = (cliente_data['COMPRA_GLOBAL_SCOTT'] / minima_anual) if minima_anual > 0 else 0.0
        cliente_data['porcentaje_avance_apparel'] = (cliente_data['COMPRA_GLOBAL_APPAREL'] / minima_apparel) if minima_apparel > 0 else 0.0
        
        cliente_data['total_bicis_deduccion'] = cliente_data['bicicleta_demo'] + cliente_data['bicicletas_bold']
        cliente_data['acumulado_global_calculado'] = cliente_data['COMPRAS_TOTALES_CRUDO'] - cliente_data['notas_credito'] - cliente_data['garantias']

        return jsonify(cliente_data), 200

    except Exception as e:
        print("Error al obtener cliente:", str(e))
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conexion: conexion.close()