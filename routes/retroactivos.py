import unicodedata
from flask import Blueprint, jsonify, request
from db_conexion import obtener_conexion
import decimal
import traceback
from datetime import date, datetime
from utils.odoo_utils import get_odoo_models, ODOO_DB, ODOO_PASSWORD

retroactivos_bp = Blueprint('retroactivos', __name__, url_prefix='')


# ==============================================================================
# CONFIGURACIÓN GENERAL
# ==============================================================================
FECHA_MINIMA_RETROACTIVOS = '2025-05-01'
FECHA_MINIMA_PRODUCTOS_OFERTADOS = '2025-06-01'

FECHA_INICIO_CASCOS_PROMO = '2025-10-15'
FECHA_FIN_CASCOS_PROMO = '2025-12-31'

FECHA_INICIO_ZAPATOS_PROMO = '2025-10-15'
FECHA_FIN_ZAPATOS_PROMO = '2025-12-31'

# ==============================================================================
# PROMOCIONES / ETIQUETAS DE PRODUCTO OFERTADO
# ==============================================================================
# IMPORTANTE:
# Odoo muestra las etiquetas que el producto tiene HOY. Si una bicicleta hoy tiene
# "Spring Sale 26", sale.report también la puede mostrar en ventas antiguas.
# Para no inflar productos_ofertados, cada etiqueta debe tener vigencia.
#
# Ajusta las fechas si negocio te confirma una vigencia distinta.
PROMOCIONES_PRODUCTO_OFERTADO = {
    'BOLD ON FIRE': {
        'inicio': '2025-06-01',
        'fin': '2026-06-30',
        'porcentaje': 1.0
    },
    'DEAL NAVIDENO': {
        'inicio': '2025-06-01',
        'fin': '2026-06-30',
        'porcentaje': 1.0
    },
    'MEGAMO ON FIRE': {
        'inicio': '2025-06-01',
        'fin': '2026-06-30',
        'porcentaje': 1.0
    },
    'PRODUCTO OFERTADO': {
        'inicio': '2025-06-01',
        'fin': '2026-06-30',
        'porcentaje': 1.0
    },

    # Spring Sale 26 no debe afectar ventas viejas si la promoción no estaba vigente.
    # Si en tu Odoo sigue apareciendo como "SPRING SALE 26´", la normalización lo iguala a SPRING SALE 26.
    'SPRING SALE 26': {
        'inicio': '2026-03-01',
        'fin': '2026-06-30',
        'porcentaje': 1.0
    },

    # Si estas promociones tienen fechas específicas, ajusta aquí.
    'SCOTT SALE': {
        'inicio': '2025-06-01',
        'fin': '2026-06-30',
        'porcentaje': 1.0
    },
    'SEASON OFF': {
        'inicio': '2025-06-01',
        'fin': '2026-06-30',
        'porcentaje': 1.0
    }
}

# Lista derivada automáticamente. Se usa para buscar las etiquetas reales en Odoo.
ETIQUETAS_PRODUCTO_OFERTADO = list(PROMOCIONES_PRODUCTO_OFERTADO.keys())

# Referencias tomadas de CASCOS-OFERTADO.pdf.
# Regla: cascos de esta lista vendidos del 2025-10-15 al 2025-12-31 se descuentan al 50%.
REFERENCIAS_CASCOS_50 = {
    'SCO20CA192651307',
    'SCO20CA192651306',
    'SCO20CA192651606',
    'SCO20CA192651308',
    'SCO20CA192651607',
    'SCO2752326823222',
    'SCO2752326530222',
    'SCO21CA405691706',
    '288584-1035010',
    'SCO22CA584651306',
    'SCO22CA584692206',
    'SCO20CA195651906',
    'SCO20CA195000106',
    'SCO20CA195651907',
    'SCO22CA195726006',
    'SCO20CA195651806',
    'SCO22CA195726008',
    'SCO22CA195726007',
    'SCO21CA195009606',
    'SCO2752086909015',
    'SCO2752086519015',
    'SCO2752080091015',
    'SCO2752080135015',
    'SCO2752080091017',
    'SCO2752080002015',
    'SCO2752080096015',
    'SCO2752086909017',
    'SCOCA2326522222',
    'SCO2752356909222',
    'SCO2752184244222',
    'SCO2752180135222',
    'SCO2752124310222',
    'SCO2752126927222',
    'SCO2752126867222',
    'SCO2752120001222',
    'SCO2752126505222',
    'SCO2752127017222',
    'SCO2752126928222',
    'SCO21CA218424422',
    'SCOCA218-0096222',
    'SCO20CA205103506',
    'SCO20CA205000106',
    'SCO20CA205103507',
    'SCOCA205-6322006'
}

# Referencias tomadas de ZAPATOS-OFERTADO.pdf.
# Regla: zapatos con check en 50% se descuentan al 50%.
REFERENCIAS_ZAPATOS_50 = {
    '296549-1007430',
    'SCO20ZA596622408',
    'SCO21ZA208101712',
    'SCO21ZA208101720',
    'SCO21ZA208101722',
    'SCO21ZA605502460',
    'SCO21ZA950656822',
    'SCO22ZA595656520',
    'SCO22ZA814165930',
    'SCO22ZA814165950',
    'SCO22ZA818101950',
    'SCO22ZA828727310',
    'SCO22ZA828727330',
    'SCO22ZA836727510',
    'SCO22ZA836727520',
    'SCO22ZA836727530',
    'SCO22ZA836727540',
    'SCO22ZA836727550',
    'SCO22ZA836727560',
    'SCO22ZA836727580',
    'SCOZA5627552410',
    'SCOZA5627552420',
    'SCOZA5627552430',
    'SCOZA5627552440',
    'SCOZA605-5544340',
    'SCOZA605-5544350',
    'SCOZA605-5544360'
}

# Regla: zapatos con check en 30% se descuentan al 30%.
REFERENCIAS_ZAPATOS_30 = {
    'SCO20ZA834554712',
    'SCO20ZA834554714',
    'SCO20ZA834554716',
    'SCO20ZA834554720',
    'SCO20ZA834554722',
    'SCO20ZA885104212',
    'SCO20ZA885104214',
    'SCO20ZA885104216',
    'SCO20ZA885104218',
    'SCO20ZA885104220',
    'SCO20ZA885104222',
    'SCO20ZA894588910',
    'SCO20ZA894588912',
    'SCO20ZA894588914',
    'SCO20ZA894588916',
    'SCO20ZA894588918',
    'SCO20ZA894588920',
    'SCO20ZA894588922',
    'SCO21ZA603101740',
    'SCO21ZA603101750',
    'SCO21ZA603101760',
    'SCO21ZA603101770',
    'SCO22ZA599656512',
    'SCO22ZA599656514',
    'SCO22ZA817100012',
    'SCO22ZA817100014',
    'SCO22ZA817100016',
    'SCO22ZA817100018',
    'SCO22ZA817100020',
    'SCO22ZA817100022',
    'SCO22ZA817209810',
    'SCO22ZA817209812',
    'SCO22ZA817209814',
    'SCO22ZA817209816',
    'SCO22ZA817209818',
    'SCO22ZA817209820',
    'SCO22ZA817209822',
    'SCO22ZA826200640',
    'SCO22ZA826200670',
    'SCO22ZA905101910',
    'SCO22ZA905101912',
    'SCO22ZA905101914',
    'SCO22ZA905101916',
    'SCO22ZA905101918',
    'SCO22ZA905101920',
    'SCOZA8127663400',
    'SCOZA8127663410',
    'SCOZA8127663430',
    'SCOZA8127663440',
    'SCOZA8127663460'
}



# ==============================================================================
# HELPERS GENERALES
# ==============================================================================
def normalizar_texto_etiqueta(texto):
    """
    Normaliza textos para comparar etiquetas de Odoo sin fallar por:
    - acentos / Ñ
    - comillas ´ ` ' ’ ‘
    - mayúsculas/minúsculas
    - espacios extra
    - signos como !
    """
    texto = str(texto or '').strip().upper()
    texto = unicodedata.normalize('NFKD', texto)
    texto = ''.join(c for c in texto if not unicodedata.combining(c))
    texto = texto.replace('´', "'")
    texto = texto.replace('`', "'")
    texto = texto.replace('’', "'")
    texto = texto.replace('‘', "'")
    texto = texto.replace("'", "")
    texto = texto.replace("!", "")
    texto = ' '.join(texto.split())
    return texto


def convertir_decimal_y_fecha(valor):
    if isinstance(valor, decimal.Decimal):
        return float(valor)

    if isinstance(valor, (datetime, date)):
        return valor.strftime('%Y-%m-%d')

    return valor


def obtener_nombre_m2o(valor):
    if isinstance(valor, list) and len(valor) > 1:
        return str(valor[1] or '')
    return ''


def obtener_id_m2o(valor):
    if isinstance(valor, list) and valor:
        return valor[0]
    return None


def fetch_all_odoo(models, uid, modelo, domain, fields, order=None, batch_size=5000):
    todos = []
    offset = 0

    while True:
        params = {
            'fields': fields,
            'limit': batch_size,
            'offset': offset
        }

        if order:
            params['order'] = order

        lote = models.execute_kw(
            ODOO_DB,
            uid,
            ODOO_PASSWORD,
            modelo,
            'search_read',
            [domain],
            params
        )

        if not lote:
            break

        todos.extend(lote)

        if len(lote) < batch_size:
            break

        offset += batch_size

    return todos


def obtener_tags_por_nombres(models, uid, etiquetas_objetivo):
    """
    Trae todas las etiquetas de product.tag desde Odoo y filtra localmente.
    Esto evita problemas con ilike cuando hay Ñ, acentos o caracteres raros.
    """
    tags_odoo = models.execute_kw(
        ODOO_DB,
        uid,
        ODOO_PASSWORD,
        'product.tag',
        'search_read',
        [[]],
        {
            'fields': ['id', 'name'],
            'limit': 3000
        }
    )

    objetivos_normalizados = {
        normalizar_texto_etiqueta(etiqueta)
        for etiqueta in etiquetas_objetivo
    }

    tags_filtradas = []

    for tag in tags_odoo:
        nombre_normalizado = normalizar_texto_etiqueta(tag.get('name'))

        if nombre_normalizado in objetivos_normalizados:
            tags_filtradas.append(tag)

    return tags_filtradas


def obtener_productos_por_referencias(models, uid, referencias):
    """
    Busca variantes product.product por referencia interna/default_code.
    """
    referencias_limpias = sorted({str(r).strip() for r in referencias if str(r).strip()})

    if not referencias_limpias:
        return []

    productos = []

    for i in range(0, len(referencias_limpias), 200):
        lote_refs = referencias_limpias[i:i + 200]

        encontrados = models.execute_kw(
            ODOO_DB,
            uid,
            ODOO_PASSWORD,
            'product.product',
            'search_read',
            [[('default_code', 'in', lote_refs)]],
            {
                'fields': ['id', 'name', 'display_name', 'default_code', 'product_tmpl_id', 'categ_id'],
                'limit': 1000
            }
        )

        productos.extend(encontrados)

    return productos


def obtener_productos_por_ids(models, uid, product_ids):
    ids = sorted({int(x) for x in product_ids if x})

    if not ids:
        return []

    productos = []

    for i in range(0, len(ids), 500):
        lote_ids = ids[i:i + 500]

        encontrados = models.execute_kw(
            ODOO_DB,
            uid,
            ODOO_PASSWORD,
            'product.product',
            'search_read',
            [[('id', 'in', lote_ids)]],
            {
                'fields': ['id', 'name', 'display_name', 'default_code', 'product_tmpl_id', 'categ_id'],
                'limit': 1000
            }
        )

        productos.extend(encontrados)

    return productos


def serializar_fila(fila):
    return {
        clave: convertir_decimal_y_fecha(valor)
        for clave, valor in fila.items()
    }


# ==============================================================================
# LÓGICA DE PRODUCTOS OFERTADOS
# ==============================================================================
def clasificar_producto_ofertado(registro, producto_info, etiquetas_producto):
    """
    Regla corregida:
    - El producto debe tener una etiqueta configurada en PROMOCIONES_PRODUCTO_OFERTADO.
    - La etiqueta debe estar vigente en la fecha de venta.
    - Si el producto tiene varias etiquetas válidas, se descuenta una sola vez con el mayor porcentaje.
    - Cascos y zapatos solo aplican si también vienen dentro de una etiqueta promocional válida.
    """
    fecha_registro = str(registro.get('date') or '')[:10]
    total_con_impuestos = float(registro.get('price_total') or 0)

    default_code = str(producto_info.get('default_code') or '').strip()
    display_name = str(producto_info.get('display_name') or producto_info.get('name') or '').upper()
    categoria = obtener_nombre_m2o(producto_info.get('categ_id')).upper()
    texto_producto = f"{default_code} {display_name} {categoria}".upper()

    etiquetas_norm = {
        normalizar_texto_etiqueta(etiqueta)
        for etiqueta in etiquetas_producto
    }

    promociones_aplicables = []
    promociones_fuera_de_fecha = []

    for etiqueta_norm in etiquetas_norm:
        config = PROMOCIONES_PRODUCTO_OFERTADO.get(etiqueta_norm)

        if not config:
            continue

        inicio = config.get('inicio')
        fin = config.get('fin')
        porcentaje = float(config.get('porcentaje') or 1.0)

        if inicio <= fecha_registro <= fin:
            promociones_aplicables.append({
                'etiqueta': etiqueta_norm,
                'inicio': inicio,
                'fin': fin,
                'porcentaje': porcentaje
            })
        else:
            promociones_fuera_de_fecha.append({
                'etiqueta': etiqueta_norm,
                'inicio': inicio,
                'fin': fin,
                'fecha_venta': fecha_registro
            })

    if not promociones_aplicables:
        return {
            'monto_descuento': 0.0,
            'tipo_calculo': 'ETIQUETA_FUERA_DE_VIGENCIA',
            'porcentaje_descuento': 0.0,
            'motivo_ignorado': f'Ninguna etiqueta aplica para la fecha {fecha_registro}.',
            'etiquetas_aplicadas': [],
            'etiquetas_fuera_de_fecha': promociones_fuera_de_fecha
        }

    # Si tiene más de una etiqueta válida, no duplicamos; gana el porcentaje mayor.
    promo_ganadora = max(
        promociones_aplicables,
        key=lambda x: x['porcentaje']
    )

    porcentaje = float(promo_ganadora.get('porcentaje') or 1.0)
    tipo = f"PRODUCTO_OFERTADO_{promo_ganadora['etiqueta']}"

    es_casco_por_ref = default_code in REFERENCIAS_CASCOS_50
    es_zapato_50_por_ref = default_code in REFERENCIAS_ZAPATOS_50
    es_zapato_30_por_ref = default_code in REFERENCIAS_ZAPATOS_30

    es_casco_por_texto = 'CASCO' in texto_producto
    es_zapato_por_texto = 'ZAPATO' in texto_producto or 'ZAPATOS' in texto_producto

    # Cascos: 50%, pero solo si esta línea ya tenía una etiqueta promocional válida.
    if (
        (es_casco_por_ref or es_casco_por_texto) and
        FECHA_INICIO_CASCOS_PROMO <= fecha_registro <= FECHA_FIN_CASCOS_PROMO
    ):
        porcentaje = 0.50
        tipo = 'CASCO_PROMO_50'

    # Zapatos 50%: solo si esta línea ya tenía una etiqueta promocional válida.
    elif (
        es_zapato_50_por_ref and
        FECHA_INICIO_ZAPATOS_PROMO <= fecha_registro <= FECHA_FIN_ZAPATOS_PROMO
    ):
        porcentaje = 0.50
        tipo = 'ZAPATO_PROMO_50'

    # Zapatos 30%: solo si esta línea ya tenía una etiqueta promocional válida.
    elif (
        es_zapato_30_por_ref and
        FECHA_INICIO_ZAPATOS_PROMO <= fecha_registro <= FECHA_FIN_ZAPATOS_PROMO
    ):
        porcentaje = 0.30
        tipo = 'ZAPATO_PROMO_30'

    monto = total_con_impuestos * porcentaje

    return {
        'monto_descuento': monto,
        'tipo_calculo': tipo,
        'porcentaje_descuento': porcentaje,
        'motivo_ignorado': '',
        'etiquetas_aplicadas': promociones_aplicables,
        'etiquetas_fuera_de_fecha': promociones_fuera_de_fecha
    }


def obtener_registros_productos_ofertados(models, uid, lista_ids_validos, min_date, max_date):
    """
    Devuelve registros de sale.report que deben revisarse para productos ofertados:
    1. Productos con etiqueta promocional en plantilla.
    2. Cascos del archivo dentro del periodo.
    3. Zapatos del archivo dentro del periodo.
    """
    fecha_inicio_ofertados = max(min_date, FECHA_MINIMA_PRODUCTOS_OFERTADOS)

    tags_ofertados = obtener_tags_por_nombres(
        models,
        uid,
        ETIQUETAS_PRODUCTO_OFERTADO
    )

    tag_ids_ofertados = [tag['id'] for tag in tags_ofertados]

    referencias_especiales = set()
    productos_especiales = []
    product_ids_especiales = []

    registros_por_id = {}

    if tag_ids_ofertados:
        domain_tagged = [
            ('date', '>=', f'{fecha_inicio_ofertados} 00:00:00'),
            ('date', '<=', f'{max_date} 23:59:59'),
            ('partner_id', 'in', lista_ids_validos),
            ('product_tmpl_id.product_tag_ids', 'in', tag_ids_ofertados)
        ]

        registros_tagged = fetch_all_odoo(
            models,
            uid,
            'sale.report',
            domain_tagged,
            [
                'id',
                'date',
                'name',
                'order_reference',
                'partner_id',
                'commercial_partner_id',
                'product_id',
                'product_tmpl_id',
                'categ_id',
                'user_id',
                'team_id',
                'company_id',
                'product_uom_qty',
                'price_subtotal',
                'price_total',
                'discount_amount'
            ],
            order='date asc'
        )

        for r in registros_tagged:
            registros_por_id[r['id']] = r

    registros = sorted(
        registros_por_id.values(),
        key=lambda x: str(x.get('date') or '')
    )

    return {
        'tags_ofertados': tags_ofertados,
        'tag_ids_ofertados': tag_ids_ofertados,
        'productos_especiales': productos_especiales,
        'product_ids_especiales': product_ids_especiales,
        'registros': registros
    }


def preparar_productos_y_etiquetas(models, uid, registros, tags):
    product_ids = list({
        obtener_id_m2o(r.get('product_id'))
        for r in registros
        if obtener_id_m2o(r.get('product_id'))
    })

    productos = obtener_productos_por_ids(models, uid, product_ids)
    producto_por_id = {p['id']: p for p in productos}

    product_tmpl_ids = list({
        obtener_id_m2o(r.get('product_tmpl_id'))
        for r in registros
        if obtener_id_m2o(r.get('product_tmpl_id'))
    })

    plantillas = []

    if product_tmpl_ids:
        plantillas = fetch_all_odoo(
            models,
            uid,
            'product.template',
            [('id', 'in', product_tmpl_ids)],
            ['id', 'name', 'product_tag_ids'],
            batch_size=500
        )

    plantilla_por_id = {p['id']: p for p in plantillas}
    tag_por_id = {t['id']: t['name'] for t in tags}

    return producto_por_id, plantilla_por_id, tag_por_id


# ==============================================================================
# 1. FUNCIÓN MAESTRA: OBTENER DEDUCCIONES DESDE ODOO
# ==============================================================================
def obtener_deducciones_odoo(claves_db, fechas_por_clave):
    resultado_odoo = get_odoo_models()
    uid = resultado_odoo[0]
    models = resultado_odoo[1]

    if not uid:
        return {}

    print("🔎 Mapeando IDs internos de Odoo con las claves de la base de datos...")

    partners_odoo = models.execute_kw(
        ODOO_DB,
        uid,
        ODOO_PASSWORD,
        'res.partner',
        'search_read',
        [[]],
        {
            'fields': ['id', 'name', 'ref'],
            'limit': 20000
        }
    )

    odoo_id_to_clave = {}

    for p in partners_odoo:
        ref_odoo = str(p.get('ref', '')).strip().upper()
        name_odoo = str(p.get('name', '')).strip().upper()

        for clave in claves_db:
            clave_limpia = str(clave).strip().upper()

            if (
                ref_odoo == clave_limpia or
                ref_odoo == f"{clave_limpia}-CA" or
                clave_limpia in name_odoo
            ):
                odoo_id_to_clave[p['id']] = clave_limpia
                break

    redirecciones = {
        'VICTOR HUGO VILLANUEVA GUZMAN': 'LC657',
        'BICICLETAS SCJM': 'LC657',
        'MARCO TULIO ANDRADE NAVARRO': 'JC539',
        'NARUCO': 'LC625'
    }

    for p in partners_odoo:
        name_odoo = str(p.get('name', '')).strip().upper()

        if p['id'] not in odoo_id_to_clave and name_odoo in redirecciones:
            odoo_id_to_clave[p['id']] = redirecciones[name_odoo]

    clave_por_partner_id = dict(odoo_id_to_clave)

    resultados_por_clave = {
        clave: {
            'nc': 0.0,
            'garantia': 0.0,
            'ofertado': 0.0,
            'demo': 0.0,
            'bold': 0.0
        }
        for clave in claves_db
    }

    def agregar_valor(partner_id_odoo, tipo, valor, fecha_linea):
        clave_encontrada = odoo_id_to_clave.get(partner_id_odoo)

        if not clave_encontrada or clave_encontrada not in resultados_por_clave:
            return

        if not fecha_linea:
            return

        if fecha_linea < FECHA_MINIMA_RETROACTIVOS:
            return

        rango = fechas_por_clave.get(clave_encontrada)

        if rango and rango.get('inicio') and rango.get('fin'):
            fecha_inicio_real = max(rango['inicio'], FECHA_MINIMA_RETROACTIVOS)

            if not (fecha_inicio_real <= fecha_linea <= rango['fin']):
                return

        monto = float(valor or 0)

        if tipo in ['nc', 'garantia']:
            monto = abs(monto)

        resultados_por_clave[clave_encontrada][tipo] += monto

    lista_ids_validos = list(odoo_id_to_clave.keys())

    if not lista_ids_validos:
        return resultados_por_clave

    try:
        todas_inicios = [
            f['inicio']
            for f in fechas_por_clave.values()
            if f.get('inicio')
        ]

        todas_fines = [
            f['fin']
            for f in fechas_por_clave.values()
            if f.get('fin')
        ]

        min_date = max(min(todas_inicios), FECHA_MINIMA_RETROACTIVOS) if todas_inicios else FECHA_MINIMA_RETROACTIVOS
        max_date = max(todas_fines) if todas_fines else '2026-06-30'

        print(f"📅 Consultando Odoo desde {min_date} hasta {max_date}")

        # ==========================================================================
        # A. GARANTÍAS
        # ==========================================================================
        domain_garantia = [
            ('move_id.move_type', '=', 'out_refund'),
            ('move_id.state', '=', 'posted'),
            ('move_id.invoice_date', '>=', min_date),
            ('move_id.invoice_date', '<=', max_date),
            ('quantity', '=', 1),
            ('partner_id', 'in', lista_ids_validos),
            '|',
                ('product_id.default_code', 'ilike', 'DESCGARANTIA'),
                ('name', 'ilike', 'DESCGARANTIA')
        ]

        lineas_garantia = fetch_all_odoo(
            models,
            uid,
            'account.move.line',
            domain_garantia,
            ['partner_id', 'price_subtotal', 'name', 'date']
        )

        for linea in lineas_garantia:
            if linea.get('partner_id'):
                agregar_valor(
                    linea['partner_id'][0],
                    'garantia',
                    linea.get('price_subtotal', 0),
                    linea.get('date')
                )

        # ==========================================================================
        # B. PRODUCTOS OFERTADOS
        # ==========================================================================
        datos_ofertados = obtener_registros_productos_ofertados(
            models,
            uid,
            lista_ids_validos,
            min_date,
            max_date
        )

        registros_ofertados = datos_ofertados['registros']
        tags_ofertados = datos_ofertados['tags_ofertados']
        tag_ids_ofertados = datos_ofertados['tag_ids_ofertados']

        print(f"🏷️ Tags productos ofertados encontradas: {tags_ofertados}")
        print(f"🧾 Productos especiales encontrados por referencia: {len(datos_ofertados['productos_especiales'])}")
        print(f"🟧 Registros productos ofertados encontrados: {len(registros_ofertados)}")

        producto_por_id, plantilla_por_id, tag_por_id = preparar_productos_y_etiquetas(
            models,
            uid,
            registros_ofertados,
            tags_ofertados
        )

        for registro in registros_ofertados:
            partner = registro.get('partner_id')

            if not partner:
                continue

            partner_id = partner[0]
            clave_encontrada = clave_por_partner_id.get(partner_id)

            if not clave_encontrada or clave_encontrada not in resultados_por_clave:
                continue

            fecha_registro = str(registro.get('date', ''))[:10]

            if not fecha_registro:
                continue

            if fecha_registro < FECHA_MINIMA_PRODUCTOS_OFERTADOS:
                continue

            rango = fechas_por_clave.get(clave_encontrada)

            if rango and rango.get('inicio') and rango.get('fin'):
                fecha_inicio_real = max(rango['inicio'], FECHA_MINIMA_PRODUCTOS_OFERTADOS)

                if not (fecha_inicio_real <= fecha_registro <= rango['fin']):
                    continue

            product_id = obtener_id_m2o(registro.get('product_id'))
            producto_info = producto_por_id.get(product_id, {})

            plantilla_id = obtener_id_m2o(registro.get('product_tmpl_id'))
            plantilla_info = plantilla_por_id.get(plantilla_id, {})

            etiquetas_producto = [
                tag_por_id.get(tag_id, str(tag_id))
                for tag_id in plantilla_info.get('product_tag_ids') or []
                if tag_id in tag_ids_ofertados
            ]

            calculo = clasificar_producto_ofertado(
                registro,
                producto_info,
                etiquetas_producto
            )

            monto = float(calculo['monto_descuento'] or 0)

            if monto <= 0:
                print(
                    f"⚠️ Ofertado ignorado | Tipo: {calculo['tipo_calculo']} | "
                    f"Clave: {clave_encontrada} | Orden: {registro.get('name')} | "
                    f"Producto: {obtener_nombre_m2o(registro.get('product_id'))} | "
                    f"Motivo: {calculo['motivo_ignorado']}"
                )
                continue

            resultados_por_clave[clave_encontrada]['ofertado'] += monto

            print(
                f"🟧 Ofertado tomado | Tipo: {calculo['tipo_calculo']} | "
                f"Clave: {clave_encontrada} | Cliente Odoo: {partner[1]} | "
                f"Orden: {registro.get('name')} | "
                f"Producto: {obtener_nombre_m2o(registro.get('product_id'))} | "
                f"Etiquetas aplicadas: {calculo.get('etiquetas_aplicadas')} | "
                f"Etiquetas fuera de fecha: {calculo.get('etiquetas_fuera_de_fecha')} | "
                f"Total venta: {float(registro.get('price_total') or 0)} | "
                f"Monto descuento tomado: {monto}"
            )

        # ==========================================================================
        # B2. BICICLETAS DEMO
        # ==========================================================================
        # DEMO se busca desde sale.order porque la etiqueta está en la orden de venta.
        # Se toma amount_total para que cuadre con el total mostrado en Odoo.
        domain_ordenes_demo = [
            ('state', 'in', ['sale', 'done']),
            ('date_order', '>=', f'{min_date} 00:00:00'),
            ('date_order', '<=', f'{max_date} 23:59:59'),
            ('partner_id', 'in', lista_ids_validos),
            ('tag_ids.name', 'ilike', 'DEMO'),
            ('invoice_status', '=', 'invoiced')
        ]

        ordenes_demo = fetch_all_odoo(
            models,
            uid,
            'sale.order',
            domain_ordenes_demo,
            [
                'id',
                'name',
                'partner_id',
                'date_order',
                'amount_total',
                'invoice_status',
                'tag_ids'
            ],
            order='date_order asc'
        )

        print(f"🟦 Órdenes DEMO encontradas: {len(ordenes_demo)}")

        for orden in ordenes_demo:
            partner = orden.get('partner_id')

            if not partner:
                continue

            partner_id = partner[0]
            fecha_orden = str(orden.get('date_order', ''))[:10]
            monto_demo = float(orden.get('amount_total') or 0)

            print(
                f"🟦 DEMO tomado | Orden: {orden.get('name')} | "
                f"Cliente: {partner} | "
                f"Monto total orden con impuestos: {monto_demo}"
            )

            agregar_valor(
                partner_id,
                'demo',
                monto_demo,
                fecha_orden
            )

        # ==========================================================================
        # B3. BICICLETAS BOLD
        # ==========================================================================
        # Se conserva por compatibilidad, aunque después se reemplaza por COMPRA_GLOBAL_BOLD.
        domain_bold = [
            ('move_id.move_type', '=', 'out_invoice'),
            ('move_id.state', '=', 'posted'),
            ('move_id.invoice_date', '>=', min_date),
            ('move_id.invoice_date', '<=', max_date),
            ('quantity', '!=', 0),
            ('partner_id', 'in', lista_ids_validos),
            ('display_type', '=', False),

            '|',
                ('product_id.product_tmpl_id.name', 'ilike', 'BOLD'),
                ('name', 'ilike', 'BOLD'),

            '|',
                ('product_id.product_tmpl_id.categ_id.complete_name', 'ilike', 'BICICLETA'),
                ('name', 'ilike', 'BICICLETA')
        ]

        lineas_bold = fetch_all_odoo(
            models,
            uid,
            'account.move.line',
            domain_bold,
            [
                'partner_id',
                'price_subtotal',
                'date',
                'name',
                'move_id'
            ]
        )

        for linea in lineas_bold:
            if linea.get('partner_id'):
                agregar_valor(
                    linea['partner_id'][0],
                    'bold',
                    linea.get('price_subtotal', 0),
                    linea.get('date')
                )

        # ==========================================================================
        # C. NOTAS DE CRÉDITO
        # ==========================================================================
        domain_nc = [
            ('move_id.move_type', '=', 'out_refund'),
            ('move_id.state', '=', 'posted'),
            ('move_id.invoice_date', '>=', min_date),
            ('move_id.invoice_date', '<=', max_date),
            ('move_id.l10n_mx_edi_usage', '=', 'G02'),
            ('partner_id', 'in', lista_ids_validos),
            ('quantity', '=', 1)
        ]

        lineas_nc = fetch_all_odoo(
            models,
            uid,
            'account.move.line',
            domain_nc,
            ['partner_id', 'price_subtotal', 'name', 'date']
        )

        for linea in lineas_nc:
            if not linea.get('partner_id'):
                continue

            monto = float(linea.get('price_subtotal', 0))
            nombre_producto = str(linea.get('name') or '').upper()
            fecha_nc = linea.get('date')

            es_garantia = 'GARANTIA' in nombre_producto or 'DESCGARANTIA' in nombre_producto
            es_aplant = 'APLANT' in nombre_producto or 'ANTICIPO' in nombre_producto
            es_descuento_valido = (
                'DESC' in nombre_producto or
                'DESCESPECIAL' in nombre_producto or
                'DESCPAGO' in nombre_producto
            )

            if not es_garantia and not es_aplant and es_descuento_valido:
                agregar_valor(
                    linea['partner_id'][0],
                    'nc',
                    monto,
                    fecha_nc
                )

        return resultados_por_clave

    except Exception as e:
        print(f"❌ Error Odoo: {e}")
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
        print("🔵 Auto-sincronizando Odoo y calculando matemáticas...")

        cursor_dict.execute("""
            SELECT 
                tr.CLAVE, 
                c.f_inicio, 
                c.f_fin 
            FROM tabla_retroactivos tr
            LEFT JOIN clientes c 
                ON UPPER(TRIM(tr.CLAVE)) = UPPER(TRIM(c.clave))
            WHERE tr.CLAVE NOT LIKE 'Integral%' 
              AND tr.CLAVE IS NOT NULL
        """)

        resultados_db = cursor_dict.fetchall()

        claves_db = []
        fechas_por_clave = {}

        for row in resultados_db:
            clave = str(row['CLAVE']).strip().upper()
            claves_db.append(clave)

            ini = (
                row['f_inicio'].strftime('%Y-%m-%d')
                if isinstance(row['f_inicio'], (date, datetime))
                else (row['f_inicio'] or '2025-06-01')
            )

            fin = (
                row['f_fin'].strftime('%Y-%m-%d')
                if isinstance(row['f_fin'], (date, datetime))
                else (row['f_fin'] or '2026-06-30')
            )

            fechas_por_clave[clave] = {
                'inicio': ini,
                'fin': fin
            }

        datos_por_clave = obtener_deducciones_odoo(claves_db, fechas_por_clave)

        cursor.execute("""
            UPDATE tabla_retroactivos 
            SET 
                notas_credito = 0,
                garantias = 0,
                productos_ofertados = 0,
                bicicleta_demo = 0,
                bicicletas_bold = 0,
                NC = '',
                FACT = '',
                estatus = 'Pendiente'
        """)

        for clave, valores in datos_por_clave.items():
            if (
                valores['nc'] != 0 or
                valores['garantia'] != 0 or
                valores['ofertado'] != 0 or
                valores['demo'] != 0 or
                valores['bold'] != 0
            ):
                cursor.execute("""
                    UPDATE tabla_retroactivos 
                    SET 
                        notas_credito = %s,
                        garantias = %s,
                        productos_ofertados = %s,
                        bicicleta_demo = %s,
                        bicicletas_bold = %s
                    WHERE CLAVE = %s
                """, (
                    valores['nc'],
                    valores['garantia'],
                    valores['ofertado'],
                    valores['demo'],
                    valores['bold'],
                    clave
                ))

        # ==========================================================================
        # BOLD
        # ==========================================================================
        # Las bicicletas BOLD se toman desde COMPRA_GLOBAL_BOLD.
        # Esto se hace antes de calcular integrales para que las integrales sumen
        # correctamente el BOLD de sus claves hijas.
        cursor.execute("""
            UPDATE tabla_retroactivos
            SET bicicletas_bold = COALESCE(COMPRA_GLOBAL_BOLD, 0)
            WHERE CLAVE NOT LIKE 'Integral%'
        """)

        # ==========================================================================
        # INTEGRALES
        # ==========================================================================
        integrales_map = {
            'Integral 1': ['EC216', 'JC539'],
            'Integral 2': ['GC411', 'MC679', 'MC677', 'LC657'],
            'Integral 3': ['LC625', 'LC627', 'LC626']
        }

        for clave_padre, claves_hijas in integrales_map.items():
            format_strings = ','.join(['%s'] * len(claves_hijas))

            query_suma = f"""
                SELECT 
                    COALESCE(SUM(notas_credito), 0),
                    COALESCE(SUM(garantias), 0),
                    COALESCE(SUM(productos_ofertados), 0),
                    COALESCE(SUM(bicicleta_demo), 0),
                    COALESCE(SUM(bicicletas_bold), 0)
                FROM tabla_retroactivos 
                WHERE CLAVE IN ({format_strings})
            """

            cursor.execute(query_suma, tuple(claves_hijas))
            suma = cursor.fetchone()

            if suma:
                cursor.execute("""
                    UPDATE tabla_retroactivos 
                    SET 
                        notas_credito = %s,
                        garantias = %s,
                        productos_ofertados = %s,
                        bicicleta_demo = %s,
                        bicicletas_bold = %s
                    WHERE CLAVE = %s
                """, (
                    suma[0],
                    suma[1],
                    suma[2],
                    suma[3],
                    suma[4],
                    clave_padre
                ))

        # ==========================================================================
        # CÁLCULOS BASE
        # ==========================================================================
        cursor.execute("""
            UPDATE tabla_retroactivos
            SET 
                TOTAL_ACUMULADO = (
                    COALESCE(COMPRA_GLOBAL_SCOTT, 0) + 
                    COALESCE(COMPRA_GLOBAL_APPAREL, 0) + 
                    COALESCE(COMPRA_GLOBAL_BOLD, 0)
                ),

                compra_anual_crudo = (
                    COALESCE(COMPRAS_TOTALES_CRUDO, 0) - 
                    COALESCE(notas_credito, 0) - 
                    COALESCE(garantias, 0)
                ),

                compra_adicional = (
                    COALESCE(COMPRAS_TOTALES_CRUDO, 0) - 
                    COALESCE(notas_credito, 0) - 
                    COALESCE(garantias, 0) - 
                    COALESCE(COMPRA_MINIMA_ANUAL, 0)
                )
        """)

        # ==========================================================================
        # PORCENTAJES POR CANTIDAD DE TIENDAS
        # ==========================================================================
        umbrales_por_tiendas = {
            1: [(5000000, 0.045), (2000000, 0.02), (800000, 0.01)],
            2: [(7500000, 0.045), (3000000, 0.02), (1200000, 0.01)],
            3: [(11250000, 0.045), (4500000, 0.02), (1800000, 0.01)],
            4: [(15000000, 0.045), (6000000, 0.02), (2400000, 0.01)],
            5: [(18750000, 0.045), (7500000, 0.02), (3000000, 0.01)],
            6: [(22500000, 0.045), (9000000, 0.02), (3600000, 0.01)],
        }

        casos_integrales = []

        for clave_integral, tiendas in integrales_map.items():
            num_tiendas = len(tiendas)

            if num_tiendas in umbrales_por_tiendas:
                u = umbrales_por_tiendas[num_tiendas]

                caso = f"""
                    WHEN CLAVE = '{clave_integral}' 
                         AND CATEGORIA IN ('Partner Elite', 'Partner Elite Plus') 
                    THEN
                        CASE
                            WHEN compra_adicional >= {u[0][0]} THEN {u[0][1]}
                            WHEN compra_adicional >= {u[1][0]} THEN {u[1][1]}
                            WHEN compra_adicional >= {u[2][0]} THEN {u[2][1]}
                            ELSE 0.00
                        END
                """

                casos_integrales.append(caso)

        casos_sql = " ".join(casos_integrales)

        query_porcentajes = f"""
            UPDATE tabla_retroactivos
            SET 
                porcentaje_retroactivo = CASE
                    {casos_sql}
                    ELSE
                        CASE
                            WHEN compra_adicional >= 5000000 THEN 0.045
                            WHEN compra_adicional >= 2000000 THEN 0.02
                            WHEN compra_adicional >= 800000 THEN 0.01
                            ELSE 0.00
                        END
                END,

                porcentaje_retroactivo_apparel = CASE
                    WHEN COALESCE(COMPRA_GLOBAL_APPAREL, 0) >= COALESCE(COMPRA_MINIMA_APPAREL, 0)
                         AND COALESCE(COMPRA_MINIMA_APPAREL, 0) > 0
                    THEN
                        CASE 
                            WHEN CATEGORIA LIKE '%Partner Elite%' THEN 0.025 
                            WHEN CATEGORIA = 'Partner' THEN 0.015 
                            ELSE 0.00 
                        END
                    ELSE 0.00
                END
        """

        cursor.execute(query_porcentajes)

        # ==========================================================================
        # IMPORTE FINAL A PAGAR
        # ==========================================================================
        cursor.execute("""
            UPDATE tabla_retroactivos
            SET
                retroactivo_total = (
                    COALESCE(porcentaje_retroactivo, 0) + 
                    COALESCE(porcentaje_retroactivo_apparel, 0)
                ),

                importe = (
                    COALESCE(importe_final, 0) * 
                    (
                        COALESCE(porcentaje_retroactivo, 0) + 
                        COALESCE(porcentaje_retroactivo_apparel, 0)
                    )
                )
        """)

        conexion.commit()
        print("✅ Sincronización y cálculos terminados correctamente.")

    except Exception as e:
        if conexion:
            conexion.rollback()

        print(f"❌ Error en auto-sync: {e}")
        traceback.print_exc()

    finally:
        if cursor_dict:
            cursor_dict.close()

        if cursor:
            cursor.close()

        if conexion:
            conexion.close()


# ==============================================================================
# 3. ENDPOINT GET GLOBAL
# ==============================================================================
@retroactivos_bp.route('/retroactivos', methods=['GET'])
def obtener_retroactivos():
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
            ORDER BY 
                CASE 
                    WHEN ZONA = 'A' THEN 1 
                    WHEN ZONA = 'B' THEN 2 
                    WHEN ZONA = 'GO' THEN 3 
                    ELSE 4 
                END, 
                CLIENTE ASC
        """)

        resultados = cursor.fetchall()

        for fila in resultados:
            for clave, valor in fila.items():
                fila[clave] = convertir_decimal_y_fecha(valor)

            m_anual = fila.get('COMPRA_MINIMA_ANUAL', 0) or 0
            m_apparel = fila.get('COMPRA_MINIMA_APPAREL', 0) or 0

            fila['porcentaje_avance_general'] = (
                fila.get('COMPRAS_TOTALES_CRUDO', 0) / m_anual
            ) if m_anual > 0 else 0.0

            fila['porcentaje_avance_scott'] = (
                fila.get('COMPRA_GLOBAL_SCOTT', 0) / m_anual
            ) if m_anual > 0 else 0.0

            fila['porcentaje_avance_apparel'] = (
                fila.get('COMPRA_GLOBAL_APPAREL', 0) / m_apparel
            ) if m_apparel > 0 else 0.0

            fila['total_bicis_deduccion'] = (
                fila.get('bicicleta_demo', 0) +
                fila.get('bicicletas_bold', 0)
            )

            fila['acumulado_global_calculado'] = (
                fila.get('COMPRAS_TOTALES_CRUDO', 0) -
                fila.get('notas_credito', 0) -
                fila.get('garantias', 0)
            )

        return jsonify(resultados), 200

    except Exception as e:
        print("❌ Error al obtener retroactivos:", str(e))
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    finally:
        if cursor:
            cursor.close()

        if conexion:
            conexion.close()


# ==============================================================================
# 4. ENDPOINT POST MANUAL
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
            cliente_data[clave] = convertir_decimal_y_fecha(valor)

            if cliente_data[clave] is None:
                if clave in ['CLAVE', 'ZONA', 'CLIENTE', 'CATEGORIA', 'estatus', 'NC', 'FACT']:
                    cliente_data[clave] = ''
                else:
                    cliente_data[clave] = 0.0

        minima_anual = cliente_data.get('COMPRA_MINIMA_ANUAL', 0) or 0
        minima_apparel = cliente_data.get('COMPRA_MINIMA_APPAREL', 0) or 0

        cliente_data['porcentaje_avance_general'] = (
            cliente_data.get('COMPRAS_TOTALES_CRUDO', 0) / minima_anual
        ) if minima_anual > 0 else 0.0

        cliente_data['porcentaje_avance_scott'] = (
            cliente_data.get('COMPRA_GLOBAL_SCOTT', 0) / minima_anual
        ) if minima_anual > 0 else 0.0

        cliente_data['porcentaje_avance_apparel'] = (
            cliente_data.get('COMPRA_GLOBAL_APPAREL', 0) / minima_apparel
        ) if minima_apparel > 0 else 0.0

        cliente_data['total_bicis_deduccion'] = (
            cliente_data.get('bicicleta_demo', 0) +
            cliente_data.get('bicicletas_bold', 0)
        )

        cliente_data['acumulado_global_calculado'] = (
            cliente_data.get('COMPRAS_TOTALES_CRUDO', 0) -
            cliente_data.get('notas_credito', 0) -
            cliente_data.get('garantias', 0)
        )

        return jsonify(cliente_data), 200

    except Exception as e:
        print("❌ Error al obtener cliente:", str(e))
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    finally:
        if cursor:
            cursor.close()

        if conexion:
            conexion.close()


# ==============================================================================
# ENDPOINTS DE DEBUG
# ==============================================================================
@retroactivos_bp.route('/debug_tags_productos_ofertados', methods=['GET'])
def debug_tags_productos_ofertados():
    resultado_odoo = get_odoo_models()
    uid = resultado_odoo[0]
    models = resultado_odoo[1]

    if not uid:
        return jsonify({"error": "No se pudo conectar a Odoo"}), 500

    try:
        tags = obtener_tags_por_nombres(
            models,
            uid,
            ETIQUETAS_PRODUCTO_OFERTADO
        )

        productos_especiales = obtener_productos_por_referencias(
            models,
            uid,
            set(REFERENCIAS_CASCOS_50) | set(REFERENCIAS_ZAPATOS_50) | set(REFERENCIAS_ZAPATOS_30)
        )

        return jsonify({
            "etiquetas_buscadas": ETIQUETAS_PRODUCTO_OFERTADO,
            "tags_encontradas": tags,
            "ids_encontrados": [t['id'] for t in tags],
            "referencias_cascos_50": len(REFERENCIAS_CASCOS_50),
            "referencias_zapatos_50": len(REFERENCIAS_ZAPATOS_50),
            "referencias_zapatos_30": len(REFERENCIAS_ZAPATOS_30),
            "productos_especiales_encontrados_en_odoo": len(productos_especiales),
            "nota": "La comparación de etiquetas se hace normalizando acentos, Ñ, comillas y espacios."
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@retroactivos_bp.route('/test_demo_odoo', methods=['GET'])
def test_demo_odoo():
    resultado_odoo = get_odoo_models()
    uid = resultado_odoo[0]
    models = resultado_odoo[1]

    if not uid:
        return jsonify({"error": "No se pudo conectar a Odoo"}), 500

    try:
        ordenes = models.execute_kw(
            ODOO_DB,
            uid,
            ODOO_PASSWORD,
            'sale.order',
            'search_read',
            [[
                ('tag_ids.name', 'ilike', 'DEMO'),
                ('state', 'in', ['sale', 'done'])
            ]],
            {
                'fields': [
                    'name',
                    'partner_id',
                    'date_order',
                    'amount_total',
                    'invoice_status',
                    'tag_ids'
                ],
                'limit': 20,
                'order': 'date_order desc'
            }
        )

        return jsonify(ordenes), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@retroactivos_bp.route('/test_bold_odoo/<string:clave>', methods=['GET'])
def test_bold_odoo(clave):
    resultado_odoo = get_odoo_models()
    uid = resultado_odoo[0]
    models = resultado_odoo[1]

    if not uid:
        return jsonify({"error": "No se pudo conectar a Odoo"}), 500

    try:
        clave = clave.strip().upper()

        partners = models.execute_kw(
            ODOO_DB,
            uid,
            ODOO_PASSWORD,
            'res.partner',
            'search_read',
            [[
                '|',
                    ('ref', 'ilike', clave),
                    ('name', 'ilike', clave)
            ]],
            {
                'fields': ['id', 'name', 'ref'],
                'limit': 50
            }
        )

        partner_ids = [p['id'] for p in partners]

        if not partner_ids:
            return jsonify({
                "clave_buscada": clave,
                "mensaje": "No se encontró partner en Odoo",
                "partners": []
            }), 200

        lineas_factura = models.execute_kw(
            ODOO_DB,
            uid,
            ODOO_PASSWORD,
            'account.move.line',
            'search_read',
            [[
                ('move_id.move_type', '=', 'out_invoice'),
                ('move_id.state', '=', 'posted'),
                ('move_id.invoice_date', '>=', '2025-05-01'),
                ('move_id.invoice_date', '<=', '2026-06-30'),
                ('partner_id', 'in', partner_ids),
                ('display_type', '=', False),
                ('quantity', '!=', 0),
                '|',
                    ('name', 'ilike', 'BOLD'),
                    ('product_id.product_tmpl_id.name', 'ilike', 'BOLD')
            ]],
            {
                'fields': [
                    'id',
                    'move_id',
                    'partner_id',
                    'date',
                    'name',
                    'product_id',
                    'quantity',
                    'price_unit',
                    'price_subtotal',
                    'price_total'
                ],
                'limit': 500
            }
        )

        return jsonify({
            "clave_buscada": clave,
            "partners_encontrados": partners,
            "lineas_bold_factura": lineas_factura
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@retroactivos_bp.route('/debug_productos_ofertados_detalle', methods=['GET'])
def debug_productos_ofertados_detalle(clave_param=None):
    resultado_odoo = get_odoo_models()
    uid = resultado_odoo[0]
    models = resultado_odoo[1]

    if not uid:
        return jsonify({"error": "No se pudo conectar a Odoo"}), 500

    conexion = None
    cursor = None

    try:
        fecha_inicio = request.args.get('inicio', '2025-06-01')
        fecha_fin = request.args.get('fin', '2026-06-30')
        clave_filtro = (clave_param or request.args.get('clave', '')).strip().upper()
        respetar_fechas_cliente = request.args.get('respetar_fechas_cliente', '0') == '1'

        datos_ofertados = obtener_registros_productos_ofertados(
            models,
            uid,
            [],
            fecha_inicio,
            fecha_fin
        )

        # Si lista_ids_validos viene vacío, el helper anterior no sirve para consulta global.
        # Por eso aquí se rehace global sin partner_id.
        tags = obtener_tags_por_nombres(models, uid, ETIQUETAS_PRODUCTO_OFERTADO)
        tag_ids = [t['id'] for t in tags]

        productos_especiales = []
        product_ids_especiales = []

        registros_por_id = {}

        if tag_ids:
            for r in fetch_all_odoo(
                models,
                uid,
                'sale.report',
                [
                    ('date', '>=', f'{fecha_inicio} 00:00:00'),
                    ('date', '<=', f'{fecha_fin} 23:59:59'),
                    ('product_tmpl_id.product_tag_ids', 'in', tag_ids)
                ],
                [
                    'id',
                    'date',
                    'name',
                    'order_reference',
                    'partner_id',
                    'commercial_partner_id',
                    'product_id',
                    'product_tmpl_id',
                    'categ_id',
                    'user_id',
                    'team_id',
                    'company_id',
                    'product_uom_qty',
                    'price_subtotal',
                    'price_total',
                    'discount_amount'
                ],
                order='date asc'
            ):
                registros_por_id[r['id']] = r

        registros = sorted(
            registros_por_id.values(),
            key=lambda x: str(x.get('date') or '')
        )

        fechas_por_clave = {}

        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)

        cursor.execute("""
            SELECT 
                clave,
                nombre_cliente,
                f_inicio,
                f_fin
            FROM clientes
            WHERE clave IS NOT NULL
        """)

        clientes_db = cursor.fetchall()

        for c in clientes_db:
            clave_db = str(c.get('clave') or '').strip().upper()

            if not clave_db:
                continue

            f_inicio = c.get('f_inicio')
            f_fin = c.get('f_fin')

            if isinstance(f_inicio, (date, datetime)):
                f_inicio = f_inicio.strftime('%Y-%m-%d')

            if isinstance(f_fin, (date, datetime)):
                f_fin = f_fin.strftime('%Y-%m-%d')

            fechas_por_clave[clave_db] = {
                "nombre_cliente": c.get('nombre_cliente'),
                "inicio": f_inicio or fecha_inicio,
                "fin": f_fin or fecha_fin
            }

        partner_ids = list({
            obtener_id_m2o(r.get('partner_id'))
            for r in registros
            if obtener_id_m2o(r.get('partner_id'))
        })

        partners = []

        if partner_ids:
            partners = fetch_all_odoo(
                models,
                uid,
                'res.partner',
                [('id', 'in', partner_ids)],
                ['id', 'name', 'ref'],
                batch_size=500
            )

        partner_por_id = {p['id']: p for p in partners}

        producto_por_id, plantilla_por_id, tag_por_id = preparar_productos_y_etiquetas(
            models,
            uid,
            registros,
            tags
        )

        total_venta_general = 0.0
        total_descuento_general = 0.0
        total_sin_impuestos_general = 0.0
        resumen_por_clave = {}
        resumen_por_tipo = {}
        detalle = []

        for r in registros:
            partner = r.get('partner_id')

            if not partner:
                continue

            partner_id = partner[0]
            partner_info = partner_por_id.get(partner_id, {})
            clave = str(partner_info.get('ref') or '').strip().upper()
            cliente = partner_info.get('name') or partner[1]

            if clave_filtro and clave != clave_filtro:
                continue

            fecha_registro = str(r.get('date') or '')[:10]

            if respetar_fechas_cliente and clave:
                rango = fechas_por_clave.get(clave)

                if rango:
                    inicio_real = max(fecha_inicio, rango.get('inicio') or fecha_inicio)
                    fin_real = rango.get('fin') or fecha_fin

                    if not (inicio_real <= fecha_registro <= fin_real):
                        continue

            product_id = obtener_id_m2o(r.get('product_id'))
            producto_info = producto_por_id.get(product_id, {})

            plantilla_id = obtener_id_m2o(r.get('product_tmpl_id'))
            plantilla_info = plantilla_por_id.get(plantilla_id, {})

            etiquetas_producto = [
                tag_por_id.get(tag_id, str(tag_id))
                for tag_id in plantilla_info.get('product_tag_ids') or []
                if tag_id in tag_ids
            ]

            calculo = clasificar_producto_ofertado(
                r,
                producto_info,
                etiquetas_producto
            )

            total_venta = float(r.get('price_total') or 0)
            subtotal = float(r.get('price_subtotal') or 0)
            descuento_tomado = float(calculo['monto_descuento'] or 0)
            descuento_odoo = float(r.get('discount_amount') or 0)

            if descuento_tomado <= 0:
                continue

            total_venta_general += total_venta
            total_descuento_general += descuento_tomado
            total_sin_impuestos_general += subtotal

            key_cliente = clave if clave else f"ODOO_{partner_id}"

            if key_cliente not in resumen_por_clave:
                resumen_por_clave[key_cliente] = {
                    "clave": clave,
                    "cliente": cliente,
                    "total_venta_con_impuestos": 0.0,
                    "monto_descuento_tomado": 0.0,
                    "subtotal_sin_impuestos": 0.0,
                    "descuento_odoo": 0.0,
                    "registros": 0,
                    "ordenes": set(),
                    "partners": set()
                }

            resumen_por_clave[key_cliente]["total_venta_con_impuestos"] += total_venta
            resumen_por_clave[key_cliente]["monto_descuento_tomado"] += descuento_tomado
            resumen_por_clave[key_cliente]["subtotal_sin_impuestos"] += subtotal
            resumen_por_clave[key_cliente]["descuento_odoo"] += descuento_odoo
            resumen_por_clave[key_cliente]["registros"] += 1
            resumen_por_clave[key_cliente]["ordenes"].add(str(r.get('name') or ''))
            resumen_por_clave[key_cliente]["partners"].add(f"{partner_id} - {cliente}")

            tipo = calculo['tipo_calculo']

            if tipo not in resumen_por_tipo:
                resumen_por_tipo[tipo] = {
                    "tipo_calculo": tipo,
                    "total_venta_con_impuestos": 0.0,
                    "monto_descuento_tomado": 0.0,
                    "registros": 0
                }

            resumen_por_tipo[tipo]["total_venta_con_impuestos"] += total_venta
            resumen_por_tipo[tipo]["monto_descuento_tomado"] += descuento_tomado
            resumen_por_tipo[tipo]["registros"] += 1

            detalle.append({
                "fecha_de_la_orden": r.get('date'),
                "orden_relacionada": r.get('name'),
                "order_reference": r.get('order_reference'),

                "clave": clave,
                "cliente": cliente,
                "partner_id": partner_id,

                "referencia_interna": producto_info.get('default_code'),
                "producto": r.get('product_id'),
                "plantilla_producto": r.get('product_tmpl_id'),
                "categoria": r.get('categ_id'),
                "etiquetas_producto": etiquetas_producto,
                "etiquetas_aplicadas": calculo.get('etiquetas_aplicadas', []),
                "etiquetas_fuera_de_fecha": calculo.get('etiquetas_fuera_de_fecha', []),

                "vendedor": r.get('user_id'),
                "equipo_de_ventas": r.get('team_id'),
                "empresa": r.get('company_id'),

                "cantidad": r.get('product_uom_qty'),
                "subtotal_sin_impuestos": round(subtotal, 2),
                "total_venta_con_impuestos": round(total_venta, 2),
                "porcentaje_descuento_tomado": calculo['porcentaje_descuento'],
                "monto_descuento_tomado": round(descuento_tomado, 2),
                "tipo_calculo": tipo,
                "descuento_odoo": round(descuento_odoo, 2)
            })

        resumen_clientes = []

        for item in resumen_por_clave.values():
            resumen_clientes.append({
                "clave": item["clave"],
                "cliente": item["cliente"],
                "total_venta_con_impuestos": round(item["total_venta_con_impuestos"], 2),
                "monto_descuento_tomado": round(item["monto_descuento_tomado"], 2),
                "subtotal_sin_impuestos": round(item["subtotal_sin_impuestos"], 2),
                "descuento_odoo": round(item["descuento_odoo"], 2),
                "registros": item["registros"],
                "ordenes": sorted(list(item["ordenes"])),
                "partners_unificados": sorted(list(item["partners"]))
            })

        resumen_clientes = sorted(
            resumen_clientes,
            key=lambda x: x["monto_descuento_tomado"],
            reverse=True
        )

        resumen_tipos = []

        for item in resumen_por_tipo.values():
            resumen_tipos.append({
                "tipo_calculo": item["tipo_calculo"],
                "total_venta_con_impuestos": round(item["total_venta_con_impuestos"], 2),
                "monto_descuento_tomado": round(item["monto_descuento_tomado"], 2),
                "registros": item["registros"]
            })

        resumen_tipos = sorted(
            resumen_tipos,
            key=lambda x: x["monto_descuento_tomado"],
            reverse=True
        )

        return jsonify({
            "periodo": {
                "inicio": fecha_inicio,
                "fin": fecha_fin,
                "respetar_fechas_cliente": respetar_fechas_cliente
            },
            "filtros_odoo_replicados": {
                "fuente": "sale.report / Análisis de ventas",
                "fecha": f"{fecha_inicio} a {fecha_fin}",
                "producto_etiquetas_plantilla": ETIQUETAS_PRODUCTO_OFERTADO,
                "vigencias_por_etiqueta": PROMOCIONES_PRODUCTO_OFERTADO,
                "monto_normal": "price_total / total con impuestos",
                "cascos": "referencias del archivo CASCOS-OFERTADO, 50% del total con impuestos entre 2025-10-15 y 2025-12-31",
                "zapatos": "referencias del archivo ZAPATOS-OFERTADO, 50% o 30% del total con impuestos entre 2025-10-15 y 2025-12-31"
            },
            "clave_filtrada": clave_filtro or None,
            "tags_encontradas": tags,
            "registros_encontrados": len(detalle),
            "total_venta_con_impuestos": round(total_venta_general, 2),
            "monto_descuento_tomado": round(total_descuento_general, 2),
            "total_sin_impuestos": round(total_sin_impuestos_general, 2),
            "resumen_por_cliente": resumen_clientes,
            "resumen_por_tipo_calculo": resumen_tipos,
            "detalle": detalle
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    finally:
        if cursor:
            cursor.close()

        if conexion:
            conexion.close()


@retroactivos_bp.route('/test_productos_ofertados', methods=['GET'])
def test_productos_ofertados():
    # Alias más corto para el debug global.
    return debug_productos_ofertados_detalle()


@retroactivos_bp.route('/debug_productos_ofertados_cliente/<string:clave>', methods=['GET'])
def debug_productos_ofertados_cliente(clave):
    # Reutiliza el debug detallado filtrando por clave.
    return debug_productos_ofertados_detalle(clave)


@retroactivos_bp.route('/debug_demo_bold_cliente/<string:clave>', methods=['GET'])
def debug_demo_bold_cliente(clave):
    resultado_odoo = get_odoo_models()
    uid = resultado_odoo[0]
    models = resultado_odoo[1]

    if not uid:
        return jsonify({"error": "No se pudo conectar a Odoo"}), 500

    conexion = None
    cursor = None

    try:
        clave = clave.strip().upper()

        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)

        cursor.execute("""
            SELECT 
                tr.CLAVE,
                tr.CLIENTE,
                tr.COMPRA_GLOBAL_BOLD,
                tr.bicicleta_demo,
                tr.bicicletas_bold,
                tr.importe_final,
                c.f_inicio,
                c.f_fin
            FROM tabla_retroactivos tr
            LEFT JOIN clientes c
                ON UPPER(TRIM(tr.CLAVE)) = UPPER(TRIM(c.clave))
            WHERE UPPER(TRIM(tr.CLAVE)) = %s
            LIMIT 1
        """, (clave,))

        cliente_db = cursor.fetchone()

        if not cliente_db:
            return jsonify({"error": f"No encontré la clave {clave}"}), 404

        f_inicio = cliente_db.get('f_inicio')
        f_fin = cliente_db.get('f_fin')

        if isinstance(f_inicio, (date, datetime)):
            f_inicio = f_inicio.strftime('%Y-%m-%d')

        if isinstance(f_fin, (date, datetime)):
            f_fin = f_fin.strftime('%Y-%m-%d')

        fecha_inicio = f_inicio or '2025-05-01'
        fecha_fin = f_fin or '2026-06-30'

        partners = models.execute_kw(
            ODOO_DB,
            uid,
            ODOO_PASSWORD,
            'res.partner',
            'search_read',
            [[
                '|',
                    ('ref', 'ilike', clave),
                    ('name', 'ilike', clave)
            ]],
            {
                'fields': ['id', 'name', 'ref'],
                'limit': 50
            }
        )

        partner_ids = [p['id'] for p in partners]

        ordenes_demo = fetch_all_odoo(
            models,
            uid,
            'sale.order',
            [
                ('partner_id', 'in', partner_ids),
                ('state', 'in', ['sale', 'done']),
                ('date_order', '>=', f'{fecha_inicio} 00:00:00'),
                ('date_order', '<=', f'{fecha_fin} 23:59:59'),
                ('tag_ids.name', 'ilike', 'DEMO'),
                ('invoice_status', '=', 'invoiced')
            ],
            [
                'id',
                'name',
                'partner_id',
                'date_order',
                'amount_untaxed',
                'amount_total',
                'invoice_status',
                'tag_ids'
            ],
            order='date_order asc'
        )

        total_demo_con_impuestos = sum(float(o.get('amount_total') or 0) for o in ordenes_demo)
        total_demo_sin_impuestos = sum(float(o.get('amount_untaxed') or 0) for o in ordenes_demo)

        compra_global_bold = float(cliente_db.get('COMPRA_GLOBAL_BOLD') or 0)
        bicicleta_demo_db = float(cliente_db.get('bicicleta_demo') or 0)
        bicicletas_bold_db = float(cliente_db.get('bicicletas_bold') or 0)

        return jsonify({
            "clave": clave,
            "cliente_db": {
                "cliente": cliente_db.get('CLIENTE'),
                "f_inicio": f_inicio,
                "f_fin": f_fin,
                "COMPRA_GLOBAL_BOLD": compra_global_bold,
                "bicicleta_demo_guardado_db": bicicleta_demo_db,
                "bicicletas_bold_guardado_db": bicicletas_bold_db,
                "total_demo_bold_db": round(bicicleta_demo_db + bicicletas_bold_db, 2),
                "importe_final_db": float(cliente_db.get('importe_final') or 0)
            },
            "partners_odoo": partners,
            "ordenes_demo_encontradas": ordenes_demo,
            "calculo_demo_desde_odoo": {
                "total_demo_sin_impuestos": round(total_demo_sin_impuestos, 2),
                "total_demo_con_impuestos": round(total_demo_con_impuestos, 2),
                "nota": "La lógica actual toma amount_total completo de la orden DEMO."
            },
            "calculo_si_usamos_con_impuestos": {
                "demo_con_impuestos": round(total_demo_con_impuestos, 2),
                "bold_desde_compra_global_bold": round(compra_global_bold, 2),
                "total_demo_bold_con_impuestos": round(total_demo_con_impuestos + compra_global_bold, 2)
            }
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    finally:
        if cursor:
            cursor.close()

        if conexion:
            conexion.close()


@retroactivos_bp.route('/debug_buscar_deal_navideno', methods=['GET'])
def debug_buscar_deal_navideno():
    resultado_odoo = get_odoo_models()
    uid = resultado_odoo[0]
    models = resultado_odoo[1]

    if not uid:
        return jsonify({"error": "No se pudo conectar a Odoo"}), 500

    try:
        def buscar(modelo, domain, fields, limit=100):
            return models.execute_kw(
                ODOO_DB,
                uid,
                ODOO_PASSWORD,
                modelo,
                'search_read',
                [domain],
                {
                    'fields': fields,
                    'limit': limit
                }
            )

        resultados = {
            "product_tag_deal": buscar('product.tag', [('name', 'ilike', 'DEAL')], ['id', 'name'], 200),
            "product_tag_nav": buscar('product.tag', [('name', 'ilike', 'NAV')], ['id', 'name'], 200),
            "product_template_tag_deal": buscar('product.template', [('product_tag_ids.name', 'ilike', 'DEAL')], ['id', 'name', 'product_tag_ids'], 50),
            "sale_report_tag_deal": buscar(
                'sale.report',
                [
                    ('date', '>=', '2025-06-01 00:00:00'),
                    ('date', '<=', '2026-06-30 23:59:59'),
                    ('product_tmpl_id.product_tag_ids.name', 'ilike', 'DEAL')
                ],
                [
                    'date',
                    'name',
                    'partner_id',
                    'product_id',
                    'product_tmpl_id',
                    'price_total'
                ],
                50
            )
        }

        return jsonify(resultados), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
