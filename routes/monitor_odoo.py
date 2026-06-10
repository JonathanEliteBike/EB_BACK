from flask import Blueprint, request, jsonify
try:
    import pandas as pd
    PANDAS_OK = True
except Exception:
    pd = None  # type: ignore
    PANDAS_OK = False
from db_conexion import obtener_conexion
from werkzeug.utils import secure_filename
import os
from datetime import datetime
from models.monitor_odoo_model import obtener_todos_los_registros
import re
from zoneinfo import ZoneInfo
import time
import logging
from utils.odoo_utils import get_odoo_models, ODOO_DB, ODOO_PASSWORD

monitor_odoo_bp = Blueprint('monitor_odoo', __name__, url_prefix='')

@monitor_odoo_bp.route('/monitor_odoo', methods=['GET'])
def obtener_monitor():
    conexion = None
    cursor = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)
        consulta = """
        SELECT 
            id,
            numero_factura,
            referencia_interna,
            nombre_producto,
            contacto_referencia,
            contacto_nombre,
            fecha_factura,
            precio_unitario,
            cantidad,
            venta_total,
            marca,
            subcategoria,
            apparel,
            eride,
            evac,
            categoria_producto,
            estado_factura
        FROM monitor
        """
        cursor.execute(consulta)
        resultados = cursor.fetchall()
        return jsonify(resultados)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@monitor_odoo_bp.route('/ultima_actualizacion', methods=['GET'])
def obtener_ultima_actualizacion():
    t_inicio = time.time() # ⏱️ INICIO
    
    conexion = None
    cursor = None
    try:
        # Paso 1: Conexión
        t1 = time.time()
        conexion = obtener_conexion()
        t2 = time.time()
        print(f"Tiempo Conexion: {t2 - t1:.4f} seg")

        cursor = conexion.cursor(dictionary=True)
        
        # Paso 2: Ejecución
        consulta = "SELECT ultima_fecha FROM cache_ultima_actualizacion WHERE id = 1"
        
        t3 = time.time()
        cursor.execute(consulta)
        resultado = cursor.fetchone()
        t4 = time.time()
        print(f"Tiempo SQL: {t4 - t3:.4f} seg")
        
        t_total = time.time() - t_inicio
        print(f"TIEMPO TOTAL API: {t_total:.4f} seg")

        if resultado and resultado['ultima_fecha']:
            return jsonify({
                'success': True,
                'ultima_fecha_actualizacion': resultado['ultima_fecha'].isoformat()
            })
        else:
            return jsonify({
                'success': True,
                'ultima_fecha_actualizacion': None
            })
            
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
        
    finally:
        # IMPORTANTE: Al usar Pool, .close() no cierra, devuelve al pool.
        # Quitamos el chequeo is_connected() que a veces añade latencia innecesaria
        if cursor: cursor.close()
        if conexion: conexion.close()

@monitor_odoo_bp.route('/importar_facturas', methods=['POST'])
def importar_facturas():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No se proporcionó archivo'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'Nombre de archivo vacío'}), 400

    try:
        UPLOAD_FOLDER = 'temp_uploads'
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        filename = secure_filename(f"temp_import_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)

        df = pd.read_excel(filepath)

        # Validación de columnas requeridas
        columnas_requeridas = [
            'Líneas de factura/Número',
            'Líneas de factura/Producto/Referencia interna',
            'Líneas de factura/Producto/Nombre',
            'Líneas de factura/Contacto/Referencia',
            'Líneas de factura/Contacto/Nombre',
            'Líneas de factura/Fecha de factura',
            'Líneas de factura/Precio unitario',
            'Líneas de factura/Cantidad',
            'Líneas de factura/Producto/Categoría del producto',
            'Líneas de factura/Estado'
        ]

        columnas_faltantes = [col for col in columnas_requeridas if col not in df.columns]
        if columnas_faltantes:
            return jsonify({
                'success': False,
                'error': f'Faltan columnas requeridas: {", ".join(columnas_faltantes)}'
            }), 400

        # Renombrar columnas
        rename_map = {
            'Líneas de factura/Número': 'numero_factura',
            'Líneas de factura/Producto/Referencia interna': 'referencia_interna',
            'Líneas de factura/Producto/Nombre': 'nombre_producto',
            'Líneas de factura/Contacto/Referencia': 'contacto_referencia',
            'Líneas de factura/Contacto/Nombre': 'contacto_nombre',
            'Líneas de factura/Fecha de factura': 'fecha_factura',
            'Líneas de factura/Precio unitario': 'precio_unitario',
            'Líneas de factura/Cantidad': 'cantidad',
            'Líneas de factura/Producto/Categoría del producto': 'categoria_producto',
            'Líneas de factura/Estado': 'estado_factura',
        }
        # Columna opcional: total con IVA ya calculado por Odoo
        EXCEL_TOTAL_COL = 'Líneas de factura/Total'
        tiene_total_col = EXCEL_TOTAL_COL in df.columns
        if tiene_total_col:
            rename_map[EXCEL_TOTAL_COL] = 'total_con_iva'
        df = df.rename(columns=rename_map)

        df = df.where(pd.notna(df), None)

        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)
        
        # Le decimos a esta sesión de MySQL que trabaje en la zona horaria de México
        # cursor.execute("SET time_zone = 'America/Mexico_City'")

        # Obtener todos los clientes de la base de datos
        cursor.execute("SELECT clave, nombre_cliente, evac FROM clientes")
        clientes_db = cursor.fetchall()

        # Preparar estructuras para búsqueda rápida
        clientes_por_clave = {str(cliente['clave']).strip().upper(): cliente for cliente in clientes_db}
        clientes_por_nombre = {}

        # Diccionario de nombres para Multimarcas A y B
        MULTIMARCAS_GROUPS = {
            'A': {
                'DOMESTIQUE 310188',
                'WE SPORTS GROUP',
                'HUMBERTO GONZALO GUERRA FLORES',
                'ADRIAN ELIAS BONILLAS',
                'ANGELA KARINA VILLEGAS CERVANTES',
                'BLANCA ESTELA CHAVEZ VELAZQUEZ',
                'COMERCIALIZADORA MEFRUP BIKE',
                'ALDO CARLOS MALDONADO MONTOYA',
                'RC PARTNERS',
                'HUGO ENRIQUE MONDRAGON VERGARA',
                'LAURA CRISTINA GUTIERREZ CRUZ',
                'LUIS FERNANDO DE VILLACAÑA LEMUS',
                'GUILLERMO FERNANDEZ GARDUÑO',
                'FRANCISCO ALBERTO FERNANDO LUARCA JARQUIN',
                'ALAN FERNANDO RINCON MALDONADO',
                'HECTOR ENRIQUE SANCHEZ GALLO',
                'SANDRA REYES SANCHEZ',
                'MARIANO VERDUZCO MENDEZ',
                'EDUARDO DANIEL CRUZ AZCOITIA',
                'CESAR MARTINEZ MARTINEZ',
                'LUIS AUGUSTO BAAS DZIB',
                'SERGIO ORTEGA OLVERA',
                'MELQUIADES GRANDE GARCIA',
                'JORGE ALBERTO ORTIZ CUERVO'
            },
            'B': {
                'TOMAS LUNA CHAVEZ',
                'DAVID ESCUDERO CHAVEZ',
                'AARON HOSAI TORRES ESTRADA',
                'CARLOS ALBERTO TORRES ALANIS',
                'MAURICIO OLIVEROS TORRES',
                'FERNANDO JAVIER RUIZ GONZALEZ',
                'PATRICIA DEL VIVAR MONTIEL',
                'COMERCIALIZADORA CONAGUINET',
                'EDUARDO NOEL RODRIGUEZ BRAY',
                'OSCAR MAURICIO CUEVAS TELLEZ',
                'GEORGINA ZAMUDIO PANTOJA',
                'AURORA JAASIEL YEBRA SANCHEZ',
                'EDNA GRACIELA PEÑA ZARATE',
                'CICLISTAS DE SANTA FE',
                'MARCOS ANTONIO CRUZ LOPEZ',
                'ARMANDO MARIN MUÑOZ',
                'RODOLFO MARTINEZ ARIETA',
                'HECTOR GERARDO ROSAS GONZALEZ',
                'EMMANUEL HERRERA FOSTER Y NOPHAL',
                'GUILLERMO FERNANDEZ GARDUÑO',
                'JOSE EDUARDO LOPEZ BAUTISTA',
                'JAVIER RIVERA SPECIA',
                'RICARDO VALENZUELA RODRIGUEZ',
                'REINHARD STEGE RENK',
                'EDGAR ENRIQUE OSEGUERA ESPERON',
                'KASAT SERVICIOS INDUSTRIALES Y DE CONSTRUCCION'
            }
        }

        # Función de normalización de nombres
        def normalizar_nombre(nombre):
            if not nombre:
                return ""
            nombre = str(nombre).strip().upper()
            # Reemplazar variaciones comunes
            reemplazos = {
                'S. A. DE C. V.': 'SA DE CV',
                'S.A. DE C.V.': 'SA DE CV',
                'S. DE R. L. DE C. V.': 'S DE RL DE CV',
                'SAPI DE C. V.': 'SAPI DE CV',
                '&': 'Y',
                ',': '',
                '.': '',
                '-': ' ',
                '  ': ' '
            }
            for original, reemplazo in reemplazos.items():
                nombre = nombre.replace(original, reemplazo)
            # Eliminar caracteres especiales
            nombre = ''.join(c if c.isalnum() or c.isspace() else ' ' for c in nombre)
            return ' '.join(nombre.split())

        # Función para normalizar categorías (agregar espacios después de diagonales)
        def normalizar_categoria(categoria):
            if not categoria:
                return None
            categoria = str(categoria).strip()
            # Agregar espacio después de cada diagonal si no lo tiene
            categoria = re.sub(r'/(?=\S)', '/ ', categoria)
            # También corregir casos donde pueda haber múltiples espacios
            categoria = re.sub(r'\s+', ' ', categoria)
            return categoria

        # Preprocesar nombres para búsqueda
        for cliente in clientes_db:
            nombre_normalizado = normalizar_nombre(cliente['nombre_cliente'])
            if nombre_normalizado:
                if nombre_normalizado not in clientes_por_nombre:
                    clientes_por_nombre[nombre_normalizado] = cliente
                
                # También almacenar versión sin espacios
                nombre_sin_espacios = nombre_normalizado.replace(' ', '')
                if nombre_sin_espacios and nombre_sin_espacios not in clientes_por_nombre:
                    clientes_por_nombre[nombre_sin_espacios] = cliente

        # Función para buscar EVAC
        def buscar_evac(contacto_referencia, contacto_nombre):
            # 0. Primero verificar si es un cliente Multimarcas A o B
            if contacto_nombre:
                nombre_normalizado = normalizar_nombre(contacto_nombre)
                
                # Verificar grupo A
                if nombre_normalizado in MULTIMARCAS_GROUPS['A']:
                    return "A Multimarcas"
                
                # Verificar grupo B
                if nombre_normalizado in MULTIMARCAS_GROUPS['B']:
                    return "B Multimarcas"
            
            # 1. Buscar por clave (contacto_referencia)
            if contacto_referencia:
                clave_normalizada = str(contacto_referencia).strip().upper()
                if clave_normalizada in clientes_por_clave:
                    return clientes_por_clave[clave_normalizada]['evac']
            
            # 2. Buscar por nombre si no se encontró por clave
            if contacto_nombre:
                # 2.1 Coincidencia exacta
                if nombre_normalizado in clientes_por_nombre:
                    return clientes_por_nombre[nombre_normalizado]['evac']
                
                # 2.2 Versión sin espacios
                nombre_sin_espacios = nombre_normalizado.replace(' ', '')
                if nombre_sin_espacios in clientes_por_nombre:
                    return clientes_por_nombre[nombre_sin_espacios]['evac']
                
                # 2.3 Búsqueda por palabras clave
                palabras_nombre = set(nombre_normalizado.split())
                for nombre_db, cliente_db in clientes_por_nombre.items():
                    palabras_db = set(nombre_db.split())
                    if len(palabras_nombre & palabras_db) >= 2:
                        return cliente_db['evac']
            
            return None

        # Limpiar tabla antes de insertar
        cursor.execute("TRUNCATE TABLE monitor")
        conexion.commit()

        total_insertados = 0

        for _, fila in df.iterrows():
            # Validación de fecha
            try:
                fecha = pd.to_datetime(fila['fecha_factura'], errors='coerce')
                if pd.isna(fecha) or fecha < pd.to_datetime('2025-06-10'):
                    continue
            except:
                continue

            # Filtrar facturas canceladas
            estado = str(fila['estado_factura']).strip().lower() if fila['estado_factura'] else ''
            if 'cancel' in estado or 'draft' in estado:
                continue

            # Validar categoría y normalizarla
            categoria_raw = fila['categoria_producto']
            categoria = normalizar_categoria(categoria_raw) if categoria_raw else None
            if not categoria or categoria == 'SERVICIOS':
                continue

            # Calcular valores numéricos
            try:
                precio = float(fila['precio_unitario']) if fila['precio_unitario'] else 0.0
                cantidad = int(fila['cantidad']) if fila['cantidad'] else 0
                total_odoo = fila.get('total_con_iva') if tiene_total_col else None
                if total_odoo is not None and pd.notna(total_odoo) and float(total_odoo) > 0:
                    venta_total = round(float(total_odoo), 2)
                else:
                    venta_total = round((precio * cantidad) * 1.16, 2)
            except (ValueError, TypeError):
                precio, cantidad, venta_total = 0.0, 0, 0.0

            # Extraer marca y subcategoría (usando la categoría normalizada)
            marca = categoria.split('/')[0].strip() if categoria else None
            subcategoria = categoria.split('/')[1].strip() if categoria and len(categoria.split('/')) > 1 else None
            apparel = 'SI' if subcategoria and subcategoria.upper() == 'APPAREL' else 'NO'
            eride = 'SI' if categoria and 'ERIDE' in categoria.upper() else 'NO'

            # Asignar EVAC
            evac = buscar_evac(fila['contacto_referencia'], fila['contacto_nombre'])

            # Preparar valores para inserción
            valores = (
                fila['numero_factura'],
                fila['referencia_interna'],
                fila['nombre_producto'],
                fila['contacto_referencia'],
                fila['contacto_nombre'],
                fecha.strftime('%Y-%m-%d') if not pd.isna(fecha) else None,
                precio,
                cantidad,
                venta_total,
                marca,
                subcategoria,
                apparel,
                eride,
                evac,
                categoria,
                fila['estado_factura']
            )

            # Insertar en la base de datos
            sql = """
                INSERT INTO monitor (
                    numero_factura, referencia_interna, nombre_producto,
                    contacto_referencia, contacto_nombre, fecha_factura,
                    precio_unitario, cantidad, venta_total,
                    marca, subcategoria, apparel, eride, evac,
                    categoria_producto, estado_factura
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            cursor.execute(sql, valores)
            total_insertados += 1

        conexion.commit()
        
        try:
            zona_horaria_mexico = ZoneInfo("America/Mexico_City")
            fecha_actual = datetime.now(zona_horaria_mexico)
            cursor.execute(
                "INSERT INTO historial_actualizaciones (fecha_actualizacion) VALUES (%s)",
                (fecha_actual,)
            )
            conexion.commit() 
        except Exception as e:
            print(f"No se pudo guardar la fecha de actualización: {e}")

        cursor.close()
        os.remove(filepath)

        return jsonify({
            'success': True,
            'message': f'Se importaron {total_insertados} registros correctamente',
            'count': total_insertados
        })

    except Exception as e:
        if 'cursor' in locals():
            cursor.close()
        if 'filepath' in locals() and os.path.exists(filepath):
            os.remove(filepath)

        return jsonify({
            'success': False,
            'error': f'Ocurrió un error durante la importación: {str(e)}'
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()


# ─────────────────────────────────────────────────────────────────────────────
# Funciones auxiliares compartidas por sync y recalcular
# ─────────────────────────────────────────────────────────────────────────────

MULTIMARCAS_GROUPS_SHARED = {
    'A': {
        'DOMESTIQUE 310188', 'WE SPORTS GROUP', 'HUMBERTO GONZALO GUERRA FLORES',
        'ADRIAN ELIAS BONILLAS', 'ANGELA KARINA VILLEGAS CERVANTES',
        'BLANCA ESTELA CHAVEZ VELAZQUEZ', 'COMERCIALIZADORA MEFRUP BIKE',
        'ALDO CARLOS MALDONADO MONTOYA', 'RC PARTNERS',
        'HUGO ENRIQUE MONDRAGON VERGARA', 'LAURA CRISTINA GUTIERREZ CRUZ',
        'LUIS FERNANDO DE VILLACAÑA LEMUS', 'GUILLERMO FERNANDEZ GARDUÑO',
        'FRANCISCO ALBERTO FERNANDO LUARCA JARQUIN',
        'ALAN FERNANDO RINCON MALDONADO', 'HECTOR ENRIQUE SANCHEZ GALLO',
        'SANDRA REYES SANCHEZ', 'MARIANO VERDUZCO MENDEZ',
        'EDUARDO DANIEL CRUZ AZCOITIA', 'CESAR MARTINEZ MARTINEZ',
        'LUIS AUGUSTO BAAS DZIB', 'SERGIO ORTEGA OLVERA',
        'MELQUIADES GRANDE GARCIA', 'JORGE ALBERTO ORTIZ CUERVO',
    },
    'B': {
        'TOMAS LUNA CHAVEZ', 'DAVID ESCUDERO CHAVEZ', 'AARON HOSAI TORRES ESTRADA',
        'CARLOS ALBERTO TORRES ALANIS', 'MAURICIO OLIVEROS TORRES',
        'FERNANDO JAVIER RUIZ GONZALEZ', 'PATRICIA DEL VIVAR MONTIEL',
        'COMERCIALIZADORA CONAGUINET', 'EDUARDO NOEL RODRIGUEZ BRAY',
        'OSCAR MAURICIO CUEVAS TELLEZ', 'GEORGINA ZAMUDIO PANTOJA',
        'AURORA JAASIEL YEBRA SANCHEZ', 'EDNA GRACIELA PEÑA ZARATE',
        'CICLISTAS DE SANTA FE', 'MARCOS ANTONIO CRUZ LOPEZ',
        'ARMANDO MARIN MUÑOZ', 'RODOLFO MARTINEZ ARIETA',
        'HECTOR GERARDO ROSAS GONZALEZ', 'EMMANUEL HERRERA FOSTER Y NOPHAL',
        'GUILLERMO FERNANDEZ GARDUÑO', 'JOSE EDUARDO LOPEZ BAUTISTA',
        'JAVIER RIVERA SPECIA', 'RICARDO VALENZUELA RODRIGUEZ',
        'REINHARD STEGE RENK', 'EDGAR ENRIQUE OSEGUERA ESPERON',
        'KASAT SERVICIOS INDUSTRIALES Y DE CONSTRUCCION',
    }
}


def _normalizar_nombre_shared(nombre):
    if not nombre:
        return ""
    nombre = str(nombre).strip().upper()
    reemplazos = {
        'S. A. DE C. V.': 'SA DE CV', 'S.A. DE C.V.': 'SA DE CV',
        'S. DE R. L. DE C. V.': 'S DE RL DE CV', 'SAPI DE C. V.': 'SAPI DE CV',
        '&': 'Y', ',': '', '.': '', '-': ' ', '  ': ' ',
    }
    for k, v in reemplazos.items():
        nombre = nombre.replace(k, v)
    nombre = ''.join(c if c.isalnum() or c.isspace() else ' ' for c in nombre)
    return ' '.join(nombre.split())


def _normalizar_categoria_shared(categoria):
    if not categoria:
        return None
    categoria = str(categoria).strip()
    categoria = re.sub(r'/(?=\S)', '/ ', categoria)
    categoria = re.sub(r'\s+', ' ', categoria)
    return categoria


def _construir_buscar_evac(clientes_db):
    """Devuelve la función buscar_evac con el contexto de clientes precargado."""
    clientes_por_clave = {str(c['clave']).strip().upper(): c for c in clientes_db}
    clientes_por_nombre = {}
    for cliente in clientes_db:
        nom = _normalizar_nombre_shared(cliente['nombre_cliente'])
        if nom and nom not in clientes_por_nombre:
            clientes_por_nombre[nom] = cliente
        nom_sin = nom.replace(' ', '')
        if nom_sin and nom_sin not in clientes_por_nombre:
            clientes_por_nombre[nom_sin] = cliente

    def buscar_evac(contacto_referencia, contacto_nombre):
        if contacto_nombre:
            nom = _normalizar_nombre_shared(contacto_nombre)
            if nom in MULTIMARCAS_GROUPS_SHARED['A']:
                return "A Multimarcas"
            if nom in MULTIMARCAS_GROUPS_SHARED['B']:
                return "B Multimarcas"
        if contacto_referencia:
            clave = str(contacto_referencia).strip().upper()
            if clave in clientes_por_clave:
                return clientes_por_clave[clave]['evac']
        if contacto_nombre:
            nom = _normalizar_nombre_shared(contacto_nombre)
            if nom in clientes_por_nombre:
                return clientes_por_nombre[nom]['evac']
            nom_sin = nom.replace(' ', '')
            if nom_sin in clientes_por_nombre:
                return clientes_por_nombre[nom_sin]['evac']
            palabras = set(nom.split())
            for nom_db, cli in clientes_por_nombre.items():
                if len(palabras & set(nom_db.split())) >= 2:
                    return cli['evac']
        return None

    return buscar_evac


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint: Sincronizar monitor directamente desde Odoo (sin Excel)
# ─────────────────────────────────────────────────────────────────────────────

@monitor_odoo_bp.route('/sync-monitor-odoo', methods=['POST'])
def sync_monitor_odoo():
    """
    Reemplaza el flujo manual de exportar Excel desde Odoo y subirlo.
    Consulta account.move (facturas posted) + líneas + productos + categorías
    + partners en batch, aplica la misma lógica que importar_facturas y
    actualiza la tabla monitor.

    Acepta parámetro JSON opcional: { "recalcular_previo": true }
    para también recalcular acumulado_anticipado en previo.
    """
    FECHA_INICIO = '2025-06-10'
    body = request.get_json(silent=True) or {}
    recalcular_previo = body.get('recalcular_previo', False)

    conexion = None
    cursor = None
    try:
        uid, models, odoo_err = get_odoo_models()
        if not uid or not models:
            return jsonify({'success': False, 'error': f'No se pudo conectar a Odoo: {odoo_err}'}), 500

        # ── 1. Facturas posted desde la temporada ─────────────────────────────
        facturas = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'account.move', 'search_read',
            [[
                ['move_type', '=', 'out_invoice'],
                ['state', '=', 'posted'],
                ['invoice_date', '>=', FECHA_INICIO],
            ]],
            {'fields': ['id', 'name', 'invoice_date', 'partner_id', 'invoice_line_ids'], 'limit': 0}
        )

        if not facturas:
            return jsonify({'success': True, 'message': 'No hay facturas en el periodo', 'count': 0})

        # ── 2. Mapear contexto por línea ──────────────────────────────────────
        all_line_ids = []
        line_context = {}
        for f in facturas:
            for lid in (f.get('invoice_line_ids') or []):
                all_line_ids.append(lid)
                line_context[lid] = {
                    'invoice_name': f['name'],
                    'invoice_date': f['invoice_date'],
                    'partner_id': f['partner_id'][0] if f.get('partner_id') else None,
                }

        if not all_line_ids:
            return jsonify({'success': True, 'message': 'No hay líneas de factura', 'count': 0})

        # ── 3. Líneas en batches de 2000 (evita timeout con 27k+ IDs) ───────────
        _LINE_BATCH = 2000
        lines_raw = []
        for _i in range(0, len(all_line_ids), _LINE_BATCH):
            lines_raw.extend(models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                'account.move.line', 'read',
                [all_line_ids[_i:_i + _LINE_BATCH]],
                {'fields': ['id', 'product_id', 'price_unit', 'price_total', 'quantity', 'display_type']}
            ))
        # Solo líneas de producto (excluir secciones y notas)
        lines = [l for l in lines_raw if l.get('product_id') and l.get('display_type') not in ('line_section', 'line_note')]

        # ── 4. Categorías vía sale.report (product.product está restringido) ──
        # sale.report tiene product_id → categ_id sin necesitar acceso a product.product
        product_ids = list({l['product_id'][0] for l in lines})
        product_categ_map = {}  # product_id → categ_id
        _SR_BATCH = 500
        for _i in range(0, len(product_ids), _SR_BATCH):
            chunk = product_ids[_i:_i + _SR_BATCH]
            sr = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                'sale.report', 'search_read',
                [[['product_id', 'in', chunk]]],
                {'fields': ['product_id', 'categ_id']}
            )
            for r in sr:
                pid = r['product_id'][0]
                if pid not in product_categ_map and r.get('categ_id'):
                    product_categ_map[pid] = r['categ_id'][0]

        # ── 5. Categorías en batch ────────────────────────────────────────────
        categ_ids = list(set(product_categ_map.values()))
        categs_raw = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'product.category', 'read',
            [categ_ids],
            {'fields': ['id', 'complete_name']}
        )
        # complete_name puede venir como "All / SCOTT / BICICLETA / ..." — quitamos el prefijo
        categs_map = {}
        for c in categs_raw:
            nombre = c['complete_name'] or ''
            if nombre.startswith('All / '):
                nombre = nombre[6:]
            elif nombre.startswith('All/'):
                nombre = nombre[4:]
            categs_map[c['id']] = nombre.strip()

        # ── 6. Partners en batch ──────────────────────────────────────────────
        # Mapeo manual para partners sin ref en Odoo.
        # Agregar aquí cuando un partner tenga facturas pero no tenga ref configurada en Odoo.
        # La solución permanente es configurar la ref directamente en Odoo.
        _NOMBRE_A_CLAVE = {
            'RAUL INFANTE MIRANDA':               '00002',
            'BROTHERS BIKE':                      'KA578',
            'FELIPE ENRIQUEZ ROJAS':              '5GEG6',
            'MANUEL ALEJANDRO NAVARRO GONZALEZ':  'KC612',
            'JOSE ANGEL DIAZ CORTES':             'FD324',
            'PAULIN FIGUEROA DURAN':              '35754',
            'FRANCISCO CUADRADO ALARCON':         '34241',
            'JONATHAN SANDOVAL ULLOA':            '56445',
            'GO LEMON':                           'GA378',
        }

        partner_ids = list({ctx['partner_id'] for ctx in line_context.values() if ctx['partner_id']})
        partners_raw = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'res.partner', 'read',
            [partner_ids],
            {'fields': ['id', 'ref', 'name', 'parent_id']}
        )
        partners_map = {p['id']: p for p in partners_raw}

        # Si ref vacío: heredar ref del padre (cubre cuentas B2B/portal sin ref propia)
        for p in partners_raw:
            if not p.get('ref') and p.get('parent_id'):
                parent_id = p['parent_id'][0] if isinstance(p['parent_id'], (list, tuple)) else p['parent_id']
                parent_ref = (partners_map.get(parent_id) or {}).get('ref') or ''
                if parent_ref:
                    p['ref'] = parent_ref

        # ── 7. Preparar lógica EVAC ───────────────────────────────────────────
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)
        cursor.execute("SELECT clave, nombre_cliente, evac FROM clientes")
        buscar_evac = _construir_buscar_evac(cursor.fetchall())

        # ── 8. Truncar e insertar ─────────────────────────────────────────────
        # El código del producto se extrae del display_name: "[CODE] Nombre producto"
        _CODE_RE = re.compile(r'^\[([^\]]+)\]\s*(.*)')
        _PREFIJOS_EXCLUIDOS = ('FLE', 'SERV', 'APLANT', 'ANTI', 'DESC', 'GARANT', 'LEYENDA')
        _PALABRAS_EXCLUIDAS = (
            'standard delivery', 'descuento', 'garantia', 'garantía',
            'anticipo', 'aplant', 'flete', 'felet', 'servicio',
            ' desc ', 'cargo por', 'bonificacion', 'bonificación',
        )

        # Pre-paso: acumular totales negativos por factura.
        # Cuando en Odoo se "avanza" un descuento, queda como línea separada con
        # price_total < 0. Estos montos se distribuirán proporcionalmente entre los
        # productos de la misma factura para reflejar el precio efectivo real.
        invoice_neg_totals = {}
        for _ln in lines:
            _pt = float(_ln.get('price_total') or 0)
            if _pt >= 0:
                continue
            _ctx = line_context.get(_ln['id'])
            if _ctx:
                _inv = _ctx['invoice_name']
                invoice_neg_totals[_inv] = invoice_neg_totals.get(_inv, 0) + _pt

        cursor.execute("TRUNCATE TABLE monitor")
        total_insertados = 0
        invoice_pos_totals = {}  # factura → suma de venta_total insertada

        for line in lines:
            ctx = line_context.get(line['id'])
            if not ctx:
                continue

            fecha_str = ctx['invoice_date']
            if not fecha_str or str(fecha_str) < FECHA_INICIO:
                continue

            categ_id = product_categ_map.get(line['product_id'][0])
            categoria = _normalizar_categoria_shared(categs_map.get(categ_id, '') if categ_id else '')

            if not categoria or 'SERVICIOS' in categoria.upper():
                continue

            # Extraer código y nombre del display_name del producto
            prod_display = line['product_id'][1] if line.get('product_id') else ''
            m = _CODE_RE.match(prod_display)
            code = m.group(1).strip().upper() if m else ''
            name_prod_display = m.group(2).strip() if m else prod_display.strip()
            name_prod_lower = name_prod_display.lower()

            if any(code.startswith(p) for p in _PREFIJOS_EXCLUIDOS):
                continue

            if any(kw in name_prod_lower for kw in _PALABRAS_EXCLUIDAS):
                continue

            # Líneas sin monto real (gratuitas, canceladas o de cantidad 0)
            venta_total = round(float(line.get('price_total') or 0), 2)
            if venta_total <= 0:
                continue

            partner = partners_map.get(ctx['partner_id'], {})
            contacto_referencia = (partner.get('ref') or '').strip().upper()
            contacto_nombre = (partner.get('name') or '').strip()
            if not contacto_referencia:
                contacto_referencia = _NOMBRE_A_CLAVE.get(contacto_nombre.upper().strip(), '')

            precio = float(line.get('price_unit') or 0)
            cantidad = int(float(line.get('quantity') or 0))

            partes = [p.strip() for p in categoria.split('/')]
            marca = partes[0] if partes else None
            subcategoria = partes[1] if len(partes) > 1 else None
            apparel = 'SI' if subcategoria and subcategoria.upper() == 'APPAREL' else 'NO'
            eride = 'SI' if 'ERIDE' in categoria.upper() else 'NO'

            evac = buscar_evac(contacto_referencia, contacto_nombre)

            inv_name = ctx['invoice_name']
            invoice_pos_totals[inv_name] = invoice_pos_totals.get(inv_name, 0) + venta_total

            cursor.execute("""
                INSERT INTO monitor (
                    numero_factura, referencia_interna, nombre_producto,
                    contacto_referencia, contacto_nombre, fecha_factura,
                    precio_unitario, cantidad, venta_total,
                    marca, subcategoria, apparel, eride, evac,
                    categoria_producto, estado_factura
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                inv_name,
                code or name_prod_display,
                name_prod_display,
                contacto_referencia,
                contacto_nombre,
                str(fecha_str),
                precio,
                cantidad,
                venta_total,
                marca,
                subcategoria,
                apparel,
                eride,
                evac,
                categoria,
                'posted',
            ))
            total_insertados += 1

        # Distribuir descuentos de líneas negativas proporcionalmente por factura.
        # Cubre el caso donde en Odoo se aplica un descuento como línea adicional
        # en lugar de directo en el producto (precio_total < 0 en esa línea).
        desc_facturas = 0
        for inv_name, neg_total in invoice_neg_totals.items():
            pos_total = invoice_pos_totals.get(inv_name, 0)
            if pos_total <= 0 or neg_total >= 0:
                continue
            cursor.execute("""
                UPDATE monitor
                SET descuento_distribuido = ROUND(venta_total / %s * %s, 2)
                WHERE numero_factura = %s
            """, (pos_total, neg_total, inv_name))
            desc_facturas += 1
        if desc_facturas:
            print(f'[INFO] Descuentos de línea distribuidos en {desc_facturas} facturas')

        conexion.commit()

        # ── 9. Timestamp ──────────────────────────────────────────────────────
        try:
            zona_mx = ZoneInfo("America/Mexico_City")
            cursor.execute(
                "INSERT INTO historial_actualizaciones (fecha_actualizacion) VALUES (%s)",
                (datetime.now(zona_mx),)
            )
            conexion.commit()
        except Exception as _e:
            logging.warning('sync_monitor_odoo: no se pudo guardar timestamp: %s', _e)

        result = {
            'success': True,
            'message': f'Se sincronizaron {total_insertados} registros desde Odoo',
            'count': total_insertados,
        }

        # ── 10. Recalcular previo si se solicitó ──────────────────────────────
        if recalcular_previo:
            recalc = _recalcular_acumulados_previo(conexion, cursor)
            result['previo_actualizado'] = recalc

        return jsonify(result)

    except Exception as e:
        logging.exception('sync_monitor_odoo: error inesperado')
        if conexion:
            try:
                conexion.rollback()
            except Exception:
                pass
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint: Recalcular acumulados en previo desde monitor (puede llamarse solo)
# ─────────────────────────────────────────────────────────────────────────────

def _recalcular_acumulados_previo(conexion, cursor):
    """
    Actualiza previo con los avances de cumplimiento calculados desde monitor.

    Solo cuentan para cumplimiento:
      - SCOTT: bicicletas SCOTT / MEGAMO / BOLD (subcategoria=BICICLETA o nombre contiene BICICLETA)
      - APP:   SYNCROS (marca) + APPAREL (apparel=SI) + VITTORIA (marca)  — NO total-scott

    El campo acumulado_anticipado = SCOTT + APP (no el total bruto del monitor).
    Aplica f_inicio por cliente desde la tabla clientes (default 2025-07-01).
    El primer periodo (jul_ago) arranca desde f_inicio, no desde el 1 de julio,
    lo que permite contabilizar correctamente a distribuidores con inicio anticipado.
    Para filas integrales suma los miembros del grupo.
    Retorna el número de filas actualizadas.
    """
    DEFAULT_INICIO = '2025-07-01'
    FECHA_CORTE    = '2026-06-03'   # corte temporal para alinear con hoja demo

    SCOTT_COND = """
        (
            (
                UPPER(TRIM(m.marca)) IN ('SCOTT', 'MEGAMO')
                AND (
                    UPPER(TRIM(m.subcategoria)) = 'BICICLETA'
                    OR UPPER(m.nombre_producto) LIKE '%%BICICLETA%%'
                )
                AND (
                    UPPER(TRIM(m.apparel)) = 'NO'
                    OR UPPER(m.nombre_producto) LIKE '%%BICICLETA%%'
                )
            )
            OR
            (
                UPPER(TRIM(m.marca)) = 'BOLD'
                AND UPPER(TRIM(m.subcategoria)) = 'BICICLETA'
            )
        )
    """

    MARCAS_VALIDAS_COND = """
        UPPER(TRIM(COALESCE(m.marca, ''))) IN (
            'SCOTT',
            'MEGAMO',
            'BOLD',
            'SYNCROS',
            'VITTORIA'
        )
    """

    cursor.execute(f"""
        SELECT
            m.contacto_referencia AS clave,
            -- Acumulados de sub-marcas (sin filtro de fecha mas alla de f_inicio)
            SUM(m.venta_total)                                                          AS total_bruto,
            SUM(CASE WHEN m.marca = 'SYNCROS'  THEN m.venta_total ELSE 0 END)          AS syncros,
            SUM(CASE WHEN m.apparel = 'SI'
                          OR (m.marca = 'BOLD' AND UPPER(TRIM(COALESCE(m.subcategoria,''))) != 'BICICLETA')
                     THEN m.venta_total ELSE 0 END)                                     AS apparel,
            SUM(CASE WHEN m.marca = 'VITTORIA' THEN m.venta_total ELSE 0 END)          AS vittoria,
            SUM(
                CASE
                    WHEN UPPER(TRIM(COALESCE(m.marca, ''))) = 'BOLD'
                    AND UPPER(TRIM(COALESCE(m.subcategoria, ''))) = 'BICICLETA'
                    THEN COALESCE(m.venta_total, 0)
                    ELSE 0
                END
            ) AS bold,
            SUM(
                CASE 
                    WHEN {MARCAS_VALIDAS_COND}
                    THEN COALESCE(m.venta_total, 0)
                    ELSE 0
                END
            ) AS avance_marcas_validas,
            -- SCOTT bicicletas: total y por periodo
            SUM(CASE WHEN {SCOTT_COND} THEN m.venta_total ELSE 0 END)                  AS scott,
            SUM(CASE WHEN m.fecha_factura <= '2025-08-31'
                          AND {SCOTT_COND} THEN m.venta_total ELSE 0 END)              AS scott_jul_ago,
            SUM(CASE WHEN m.fecha_factura BETWEEN '2025-09-01' AND '2025-10-31'
                          AND {SCOTT_COND} THEN m.venta_total ELSE 0 END)              AS scott_sep_oct,
            SUM(CASE WHEN m.fecha_factura BETWEEN '2025-11-01' AND '2025-12-31'
                          AND {SCOTT_COND} THEN m.venta_total ELSE 0 END)              AS scott_nov_dic,
            SUM(CASE WHEN m.fecha_factura BETWEEN '2026-01-01' AND '2026-02-28'
                          AND {SCOTT_COND} THEN m.venta_total ELSE 0 END)              AS scott_ene_feb,
            SUM(CASE WHEN m.fecha_factura BETWEEN '2026-03-01' AND '2026-04-30'
                          AND {SCOTT_COND} THEN m.venta_total ELSE 0 END)              AS scott_mar_abr,
            SUM(CASE WHEN m.fecha_factura BETWEEN '2026-05-01' AND '2026-06-30'
                          AND {SCOTT_COND} THEN m.venta_total ELSE 0 END)              AS scott_may_jun,
            -- SYNCROS por periodo
            SUM(CASE WHEN m.fecha_factura <= '2025-08-31'
                          AND m.marca = 'SYNCROS' THEN m.venta_total ELSE 0 END)       AS syncros_jul_ago,
            SUM(CASE WHEN m.fecha_factura BETWEEN '2025-09-01' AND '2025-10-31'
                          AND m.marca = 'SYNCROS' THEN m.venta_total ELSE 0 END)       AS syncros_sep_oct,
            SUM(CASE WHEN m.fecha_factura BETWEEN '2025-11-01' AND '2025-12-31'
                          AND m.marca = 'SYNCROS' THEN m.venta_total ELSE 0 END)       AS syncros_nov_dic,
            SUM(CASE WHEN m.fecha_factura BETWEEN '2026-01-01' AND '2026-02-28'
                          AND m.marca = 'SYNCROS' THEN m.venta_total ELSE 0 END)       AS syncros_ene_feb,
            SUM(CASE WHEN m.fecha_factura BETWEEN '2026-03-01' AND '2026-04-30'
                          AND m.marca = 'SYNCROS' THEN m.venta_total ELSE 0 END)       AS syncros_mar_abr,
            SUM(CASE WHEN m.fecha_factura BETWEEN '2026-05-01' AND '2026-06-30'
                          AND m.marca = 'SYNCROS' THEN m.venta_total ELSE 0 END)       AS syncros_may_jun,
            -- APPAREL (apparel=SI + BOLD no-bicicleta) por periodo
            SUM(CASE WHEN m.fecha_factura <= '2025-08-31'
                          AND (m.apparel = 'SI' OR (m.marca = 'BOLD' AND UPPER(TRIM(COALESCE(m.subcategoria,''))) != 'BICICLETA'))
                     THEN m.venta_total ELSE 0 END)                                     AS apparel_jul_ago,
            SUM(CASE WHEN m.fecha_factura BETWEEN '2025-09-01' AND '2025-10-31'
                          AND (m.apparel = 'SI' OR (m.marca = 'BOLD' AND UPPER(TRIM(COALESCE(m.subcategoria,''))) != 'BICICLETA'))
                     THEN m.venta_total ELSE 0 END)                                     AS apparel_sep_oct,
            SUM(CASE WHEN m.fecha_factura BETWEEN '2025-11-01' AND '2025-12-31'
                          AND (m.apparel = 'SI' OR (m.marca = 'BOLD' AND UPPER(TRIM(COALESCE(m.subcategoria,''))) != 'BICICLETA'))
                     THEN m.venta_total ELSE 0 END)                                     AS apparel_nov_dic,
            SUM(CASE WHEN m.fecha_factura BETWEEN '2026-01-01' AND '2026-02-28'
                          AND (m.apparel = 'SI' OR (m.marca = 'BOLD' AND UPPER(TRIM(COALESCE(m.subcategoria,''))) != 'BICICLETA'))
                     THEN m.venta_total ELSE 0 END)                                     AS apparel_ene_feb,
            SUM(CASE WHEN m.fecha_factura BETWEEN '2026-03-01' AND '2026-04-30'
                          AND (m.apparel = 'SI' OR (m.marca = 'BOLD' AND UPPER(TRIM(COALESCE(m.subcategoria,''))) != 'BICICLETA'))
                     THEN m.venta_total ELSE 0 END)                                     AS apparel_mar_abr,
            SUM(CASE WHEN m.fecha_factura BETWEEN '2026-05-01' AND '2026-06-30'
                          AND (m.apparel = 'SI' OR (m.marca = 'BOLD' AND UPPER(TRIM(COALESCE(m.subcategoria,''))) != 'BICICLETA'))
                     THEN m.venta_total ELSE 0 END)                                     AS apparel_may_jun,
            -- VITTORIA por periodo
            SUM(CASE WHEN m.fecha_factura <= '2025-08-31'
                          AND m.marca = 'VITTORIA' THEN m.venta_total ELSE 0 END)      AS vittoria_jul_ago,
            SUM(CASE WHEN m.fecha_factura BETWEEN '2025-09-01' AND '2025-10-31'
                          AND m.marca = 'VITTORIA' THEN m.venta_total ELSE 0 END)      AS vittoria_sep_oct,
            SUM(CASE WHEN m.fecha_factura BETWEEN '2025-11-01' AND '2025-12-31'
                          AND m.marca = 'VITTORIA' THEN m.venta_total ELSE 0 END)      AS vittoria_nov_dic,
            SUM(CASE WHEN m.fecha_factura BETWEEN '2026-01-01' AND '2026-02-28'
                          AND m.marca = 'VITTORIA' THEN m.venta_total ELSE 0 END)      AS vittoria_ene_feb,
            SUM(CASE WHEN m.fecha_factura BETWEEN '2026-03-01' AND '2026-04-30'
                          AND m.marca = 'VITTORIA' THEN m.venta_total ELSE 0 END)      AS vittoria_mar_abr,
            SUM(CASE WHEN m.fecha_factura BETWEEN '2026-05-01' AND '2026-06-30'
                          AND m.marca = 'VITTORIA' THEN m.venta_total ELSE 0 END)      AS vittoria_may_jun
        FROM monitor m
        LEFT JOIN clientes c ON m.contacto_referencia = c.clave
        WHERE m.contacto_referencia IS NOT NULL
          AND m.contacto_referencia != ''
          AND m.fecha_factura >= COALESCE(c.f_inicio, %s)
          AND m.fecha_factura <= %s
        GROUP BY m.contacto_referencia
    """, (DEFAULT_INICIO, FECHA_CORTE))
    totales = {row['clave']: row for row in cursor.fetchall()}

    cursor.execute("SELECT clave, id_grupo FROM clientes WHERE id_grupo IS NOT NULL")
    miembros_grupo = {}
    for row in cursor.fetchall():
        miembros_grupo.setdefault(row['id_grupo'], []).append(row['clave'])

    cursor.execute("""
        SELECT id, clave, es_integral, grupo_integral,
               compromiso_scott, compromiso_apparel_syncros_vittoria,
               compromiso_jul_ago,     compromiso_sep_oct,     compromiso_nov_dic,
               compromiso_ene_feb,     compromiso_mar_abr,     compromiso_may_jun,
               compromiso_jul_ago_app, compromiso_sep_oct_app, compromiso_nov_dic_app,
               compromiso_ene_feb_app, compromiso_mar_abr_app, compromiso_may_jun_app,
               compra_minima_inicial,  compra_minima_anual
        FROM previo
    """)
    filas = cursor.fetchall()

    PERIODS = ['jul_ago', 'sep_oct', 'nov_dic', 'ene_feb', 'mar_abr', 'may_jun']

    def flt(v):
        return float(v or 0)

    def pct(avance, compromiso):
        return int(round(avance / compromiso * 100)) if compromiso > 0 else 0

    def sf(claves, field):
        return sum(flt(totales.get(c, {}).get(field, 0)) for c in claves)

    actualizados = 0
    for fila in filas:
        if fila['es_integral']:
            grupo_id = fila['grupo_integral']
            claves   = miembros_grupo.get(grupo_id, [])
            syncros  = sf(claves, 'syncros')
            apparel  = sf(claves, 'apparel')
            vittoria = sf(claves, 'vittoria')
            bold     = sf(claves, 'bold')
            scott    = sf(claves, 'scott')
            total_bruto = sf(claves, 'total_bruto')
            p_syncros  = {p: sf(claves, f'syncros_{p}')  for p in PERIODS}
            p_apparel  = {p: sf(claves, f'apparel_{p}')  for p in PERIODS}
            p_vittoria = {p: sf(claves, f'vittoria_{p}') for p in PERIODS}
            p_scott    = {p: sf(claves, f'scott_{p}')    for p in PERIODS}
        else:
            row      = totales.get(fila['clave']) or {}
            syncros  = flt(row.get('syncros'))
            apparel  = flt(row.get('apparel'))
            vittoria = flt(row.get('vittoria'))
            bold     = flt(row.get('bold'))
            scott    = flt(row.get('scott'))
            total_bruto = flt(row.get('total_bruto'))
            p_syncros  = {p: flt(row.get(f'syncros_{p}'))  for p in PERIODS}
            p_apparel  = {p: flt(row.get(f'apparel_{p}'))  for p in PERIODS}
            p_vittoria = {p: flt(row.get(f'vittoria_{p}')) for p in PERIODS}
            p_scott    = {p: flt(row.get(f'scott_{p}'))    for p in PERIODS}


        app_global = round(syncros + apparel + vittoria, 2)
        acum_total = round(total_bruto, 2)
        p_app = {p: round(p_syncros[p] + p_apparel[p] + p_vittoria[p], 2) for p in PERIODS}

        cm_ini = flt(fila.get('compra_minima_inicial'))
        cm_anu = flt(fila.get('compra_minima_anual'))

        cursor.execute("""
            UPDATE previo SET
                acumulado_anticipado                   = %s,
                acumulado_syncros                      = %s,
                acumulado_apparel                      = %s,
                acumulado_vittoria                     = %s,
                acumulado_bold                         = %s,
                avance_global                          = %s,
                avance_global_scott                    = %s,
                avance_global_apparel_syncros_vittoria = %s,
                porcentaje_global                      = %s,
                porcentaje_anual                       = %s,
                porcentaje_scott                       = %s,
                porcentaje_apparel_syncros_vittoria    = %s,
                avance_jul_ago     = %s,  porcentaje_jul_ago     = %s,
                avance_sep_oct     = %s,  porcentaje_sep_oct     = %s,
                avance_nov_dic     = %s,  porcentaje_nov_dic     = %s,
                avance_ene_feb     = %s,  porcentaje_ene_feb     = %s,
                avance_mar_abr     = %s,  porcentaje_mar_abr     = %s,
                avance_may_jun     = %s,  porcentaje_may_jun     = %s,
                avance_jul_ago_app = %s,  porcentaje_jul_ago_app = %s,
                avance_sep_oct_app = %s,  porcentaje_sep_oct_app = %s,
                avance_nov_dic_app = %s,  porcentaje_nov_dic_app = %s,
                avance_ene_feb_app = %s,  porcentaje_ene_feb_app = %s,
                avance_mar_abr_app = %s,  porcentaje_mar_abr_app = %s,
                avance_may_jun_app = %s,  porcentaje_may_jun_app = %s
            WHERE id = %s
        """, (
            acum_total, syncros, apparel, vittoria, bold,
            acum_total, scott, app_global,
            pct(acum_total, cm_ini), pct(acum_total, cm_anu),
            pct(scott,      flt(fila.get('compromiso_scott'))),
            pct(app_global, flt(fila.get('compromiso_apparel_syncros_vittoria'))),
            p_scott['jul_ago'], pct(p_scott['jul_ago'], flt(fila.get('compromiso_jul_ago'))),
            p_scott['sep_oct'], pct(p_scott['sep_oct'], flt(fila.get('compromiso_sep_oct'))),
            p_scott['nov_dic'], pct(p_scott['nov_dic'], flt(fila.get('compromiso_nov_dic'))),
            p_scott['ene_feb'], pct(p_scott['ene_feb'], flt(fila.get('compromiso_ene_feb'))),
            p_scott['mar_abr'], pct(p_scott['mar_abr'], flt(fila.get('compromiso_mar_abr'))),
            p_scott['may_jun'], pct(p_scott['may_jun'], flt(fila.get('compromiso_may_jun'))),
            p_app['jul_ago'],   pct(p_app['jul_ago'],   flt(fila.get('compromiso_jul_ago_app'))),
            p_app['sep_oct'],   pct(p_app['sep_oct'],   flt(fila.get('compromiso_sep_oct_app'))),
            p_app['nov_dic'],   pct(p_app['nov_dic'],   flt(fila.get('compromiso_nov_dic_app'))),
            p_app['ene_feb'],   pct(p_app['ene_feb'],   flt(fila.get('compromiso_ene_feb_app'))),
            p_app['mar_abr'],   pct(p_app['mar_abr'],   flt(fila.get('compromiso_mar_abr_app'))),
            p_app['may_jun'],   pct(p_app['may_jun'],   flt(fila.get('compromiso_may_jun_app'))),
            fila['id']
        ))
        actualizados += 1

    conexion.commit()
    return actualizados


@monitor_odoo_bp.route('/recalcular-previo-desde-monitor', methods=['POST'])
def recalcular_previo_desde_monitor():
    """
    Recalcula acumulado_anticipado y sub-marcas en previo sumando desde monitor.
    Se puede llamar de forma independiente después de cualquier importación de Excel.
    """
    conexion = None
    cursor = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)
        actualizados = _recalcular_acumulados_previo(conexion, cursor)
        return jsonify({
            'success': True,
            'message': f'{actualizados} filas de previo actualizadas desde monitor',
            'updated': actualizados,
        })
    except Exception as e:
        logging.exception('recalcular_previo_desde_monitor: error')
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()