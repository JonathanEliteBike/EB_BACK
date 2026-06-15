import unicodedata
import os
import re
from flask import Blueprint, jsonify, request
from db_conexion import obtener_conexion
import decimal
import traceback
from datetime import date, datetime
from utils.odoo_utils import get_odoo_models, ODOO_DB, ODOO_PASSWORD
import openpyxl

retroactivos_bp = Blueprint('retroactivos', __name__, url_prefix='')

from utils.jwt_utils import verificar_token


# ==============================================================================
# CONFIGURACIÓN GENERAL
# ==============================================================================
FECHA_MINIMA_RETROACTIVOS = '2025-05-01'
FECHA_MINIMA_PRODUCTOS_OFERTADOS = '2025-06-01'
FECHA_CORTE = '2026-06-30'

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

REFERENCIAS_SYNCROS_2X1_TUBELESS_2026 = {
    # SKU: 275469
    # Referencia interna en Odoo:
    'KIT7',
    '275469-0001927'

    # Lo dejo también por seguridad, por si en Odoo alguna variante trae el SKU como referencia.
    '275469',
}

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
# PRODUCTOS OFERTADOS POR CAMPAÑA (REFERENCIA INTERNA + FECHA DE FACTURA)
# ==============================================================================
# Nueva regla:
# Ya no dependemos de etiquetas actuales de Odoo/sale.report.
# Se replica la base validada en Odoo desde:
# Contabilidad -> Apuntes contables
#   - Publicado
#   - Asiento contable / Tipo = Factura de cliente
#   - Cuenta = 401.01.01 Ventas y/o servicios gravados a la tasa general
#   - Producto establecido
#   - Fecha de factura dentro del rango
#
# El monto tomado para productos_ofertados es price_total de account.move.line,
# equivalente a la columna Total exportada desde Odoo, es decir, total con IVA.

REFERENCIAS_SPRING_SALE_2026 = {
    '286383-704',
    '286383-706',
    '290178-006',
    '290186-008',
    '290186-010',
    '290187-006',
    '290187-008',
    '290187-010',
    '290187-012',
    '290187-014',
    '290188-014',
    '290189-008',
    '290189-010',
    '290194-008',
    '290194-010',
    '290310-704',
    '290310-706',
    '290310-908',
    '290373-047',
    '290373-049',
    '290373-052',
    '290373-054',
    '290373-056',
    '290383-047',
    '290583-008',
    '290584-006',
    '290584-008',
    '290584-010',
    '290586-006',
    '290586-008',
    '290586-010',
    '291328-010',
    '291328-012',
    '291329-006',
    '291329-008',
    '293026-008',
    '293180-049',
    '293180-052',
    '293180-054',
    '293180-056',
    '293251-052',
    '293251-054',
    '293251-056',
    '293252-049',
    '293268-054',
    '293268-056',
    '293268-058',
    '293272-047',
    '293272-049',
    '293272-052',
    '293272-054',
    '293272-056',
    '293290-006',
    '420650-010',
    '423116-3020006',
    '423116-3020008',
    '423116-3020010',
    '423116-7961006',
    '423116-7961008',
    '423116-7961010',
    '423116-8086006',
    '423116-8086008',
    '423116-8086010',
    '423120-7960006',
    '423120-7960008',
    '423120-7960010',
    '423120-7960012',
    '423128-7960006',
    '423128-7960008',
    '423128-7960010',
    '423132-3020006',
    '423132-3020008',
    '423132-3020010',
    '423132-3020012',
    '423132-8085006',
    '423132-8085008',
    '423132-8085010',
    '423132-8085012',
    '423226-7315004',
    '423226-7315006',
    '423226-7315008',
    '423226-7315010',
    '423234-7241004',
    '423234-7241006',
    '423234-7241008',
    '423234-7241010',
    '423234-8102004',
    '423234-8102006',
    '423234-8102008',
    '423234-8102010',
    '423246-8096004',
    '423246-8096006',
    '423246-8096008',
    '423246-8096010',
    '423256-7959002',
    '423256-7959004',
    '423256-7959006',
    '423256-7959008',
    '423256-7959010',
    '423256-7959012',
    '423256-7959014',
    '423283-3020004',
    '423283-3020006',
    '423283-3020008',
    '423283-3020010',
    '423283-8099004',
    '423283-8099006',
    '423283-8099008',
    '423283-8099010',
    '423365-8086008',
    '423378-6922006',
    '423380-6985004',
    '423380-6985006',
    '423380-6985008',
    '423380-6985010',
    '423380-8097002',
    '423380-8097004',
    '423380-8097006',
    '423384-7965002',
    '423384-7965004',
    '423384-7965006',
    '423384-7965008',
    '423384-7965010',
    '423384-8089002',
    '423384-8089004',
    '423384-8089006',
    '423384-8089008',
    '423384-8089010',
    '423384-8098004',
    '423384-8098006',
    '423384-8098008',
    '423384-8098010',
    '423387-6985004',
    '423387-6985006',
    '423387-6985008',
    '423387-6985010',
    '423387-7241002',
    '423387-7241004',
    '423387-7241006',
    '423387-7241008',
    '423387-7241010',
    '423424-3020006',
    '423424-3020008',
    '423424-3020010',
    '423460-0001222',
    'SBI20BI274747001',
    'SBI22BI280645024',
    'SBI22BI286274008',
    'SBI22BI286274010',
    'SBI22BI286276006',
    'SBI22BI286276008',
    'SBI22BI286276010',
    'SBI22BI286276012',
    'SBI22BI286277006',
    'SBI22BI286277008',
    'SBI22BI286277010',
    'SBI22BI286278008',
    'SBI22BI286335010',
    'SBI22BI286392910',
    'SBI23BI290116006',
    'SBI23BI290116008',
    'SBI23BI290116010',
    'SBI23BI290116012',
    'SBI23BI290118006',
    'SBI23BI290118008',
    'SBI23BI290118010',
    'SBI23BI290118012',
    'SBI23BI290119006',
    'SBI23BI290119008',
    'SBI23BI290119010',
    'SBI23BI290119012',
    'SBI23BI290120006',
    'SBI23BI290120008',
    'SBI23BI290120010',
    'SBI23BI290121006',
    'SBI23BI290121008',
    'SBI23BI290121010',
    'SBI23BI290121012',
    'SBI23BI290139008',
    'SBI23BI290139010',
    'SBI23BI290139012',
    'SBI23BI290140006',
    'SBI23BI290140008',
    'SBI23BI290140010',
    'SBI23BI290140012',
    'SBI23BI290141006',
    'SBI23BI290141008',
    'SBI23BI290141010',
    'SBI23BI290172006',
    'SBI23BI290172008',
    'SBI23BI290172010',
    'SBI23BI290172012',
    'SBI23BI290173006',
    'SBI23BI290173008',
    'SBI23BI290173010',
    'SBI23BI290173012',
    'SBI23BI290301006',
    'SBI23BI290301008',
    'SBI23BI290341054',
    'SBI23BI290349049',
    'SBI23BI290349052',
    'SBI23BI290349054',
    'SBI23BI290349056',
    'SBI23BI290351049',
    'SBI23BI290351052',
    'SBI23BI290353049',
    'SBI23BI290353052',
    'SBI23BI290353054',
    'SBI23BI290353056',
    'SBI23BI290353058',
    'SBI23BI290354049',
    'SBI23BI290354052',
    'SBI23BI290354054',
    'SBI23BI290354056',
    'SBI23BI290364049',
    'SBI23BI290364052',
    'SBI23BI290364054',
    'SBI23BI290364056',
    'SBI23BI290364058',
    'SBI23BI290366052',
    'SBI23BI290366054',
    'SBI23BI290366056',
    'SBI23BI290367052',
    'SBI23BI290367054',
    'SBI23BI290368047',
    'SBI23BI290368058',
    'SBI23BI290369047',
    'SBI23BI290369052',
    'SBI23BI290369058',
    'SBI23BI290370047',
    'SBI23BI290370049',
    'SBI23BI290370052',
    'SBI23BI290370054',
    'SBI23BI290370056',
    'SBI23BI290383052',
    'SBI23BI290383054',
    'SBI23BI290383056',
    'SBI23BI290384052',
    'SBI23BI290384054',
    'SBI23BI290384056',
    'SBI23BI290384058',
    'SBI23BI290524049',
    'SBI23BI290524052',
    'SBI23BI290524054',
    'SBI23BI290524056',
    'SBI23BI290525049',
    'SBI23BI290525052',
    'SBI23BI290525054',
    'SBI23BI290525056',
    'SBI23BI290546008',
    'SBI23BI290546010',
    'SBI23BI293025008',
    'SBI23BI293025010',
    'SBI23BI293026006',
    'SBI23BI293026010',
    'SBI23BI293182049',
    'SBI23BI293182052',
    'SBI23BI293182054',
    'SBI23BI293182056',
    'SBI23BI293251049',
    'SBI23BI293268052',
    'SBI23BI293269049',
    'SBI23BI293269052',
    'SBI23BI293269054',
    'SBI23BI293290008',
    'SBI23BI293290010',
    'SBI23BI293291006',
    'SBI23BI293291008',
    'SBI23BI293291010',
    'SBI23BI293292008',
    'SBI23BI293292010',
    'SBI24BI293290008',
}

REFERENCIAS_DEAL_IRRESISTIBLE_2025 = {
    '286383-704',
    '290187-008',
    '290187-010',
    '290187-012',
    '290187-014',
    '290188-014',
    '290189-008',
    '290189-010',
    '290310-704',
    '290310-706',
    '290310-908',
    '290564-006',
    '290568-006',
    '290583-008',
    '290584-006',
    '290584-008',
    '290586-006',
    '290586-008',
    '291328-010',
    '291328-012',
    '291329-008',
    '293026-008',
    '293180-054',
    '293180-056',
    '293251-052',
    '293251-054',
    '293251-056',
    '293252-049',
    '293268-054',
    '293268-056',
    '293268-058',
    '293272-047',
    '293272-049',
    '293272-052',
    '293272-056',
    '293290-006',
    '420650-010',
    '423226-7315004',
    '423226-7315006',
    '423226-7315008',
    '423226-7315010',
    '423234-7241004',
    '423234-7241006',
    '423234-7241010',
    '423234-8102006',
    '423234-8102008',
    '423246-8096006',
    '423246-8096010',
    '423256-7959002',
    '423256-7959004',
    '423256-7959006',
    '423256-7959008',
    '423256-7959010',
    '423283-3020004',
    '423283-3020006',
    '423283-3020008',
    '423283-3020010',
    '423283-8099004',
    '423283-8099008',
    '423283-8099010',
    '423365-8086008',
    '423378-6922006',
    '423380-6985004',
    '423380-6985006',
    '423380-6985008',
    '423380-6985010',
    '423380-8097002',
    '423380-8097004',
    '423384-7965004',
    '423384-7965006',
    '423384-7965008',
    '423384-7965010',
    '423384-8089002',
    '423384-8089004',
    '423384-8089006',
    '423384-8089008',
    '423384-8089010',
    '423384-8098004',
    '423384-8098006',
    '423384-8098008',
    '423384-8098010',
    '423424-3020006',
    '423424-3020008',
    '423424-3020010',
    '423460-0001222',
    '423580-2308222',
    '423580-8133222',
    '423581-7963222',
    'SBI22BI280645024',
    'SBI22BI286335010',
    'SBI22BI286392910',
    'SBI23BI290096010',
    'SBI23BI290098012',
    'SBI23BI290116006',
    'SBI23BI290116008',
    'SBI23BI290116010',
    'SBI23BI290118006',
    'SBI23BI290118008',
    'SBI23BI290118010',
    'SBI23BI290119006',
    'SBI23BI290119008',
    'SBI23BI290119010',
    'SBI23BI290120006',
    'SBI23BI290120008',
    'SBI23BI290120010',
    'SBI23BI290121006',
    'SBI23BI290121008',
    'SBI23BI290121010',
    'SBI23BI290121012',
    'SBI23BI290139008',
    'SBI23BI290139012',
    'SBI23BI290140006',
    'SBI23BI290140008',
    'SBI23BI290141006',
    'SBI23BI290141008',
    'SBI23BI290149008',
    'SBI23BI290149010',
    'SBI23BI290155008',
    'SBI23BI290172012',
    'SBI23BI290173006',
    'SBI23BI290173008',
    'SBI23BI290173010',
    'SBI23BI290173012',
    'SBI23BI290179006',
    'SBI23BI290301006',
    'SBI23BI290341054',
    'SBI23BI290349049',
    'SBI23BI290349052',
    'SBI23BI290349054',
    'SBI23BI290349056',
    'SBI23BI290351049',
    'SBI23BI290351052',
    'SBI23BI290353049',
    'SBI23BI290353052',
    'SBI23BI290353054',
    'SBI23BI290353056',
    'SBI23BI290353058',
    'SBI23BI290354052',
    'SBI23BI290354054',
    'SBI23BI290354056',
    'SBI23BI290364052',
    'SBI23BI290364054',
    'SBI23BI290364056',
    'SBI23BI290364058',
    'SBI23BI290366052',
    'SBI23BI290366054',
    'SBI23BI290366056',
    'SBI23BI290524054',
    'SBI23BI290524056',
    'SBI23BI290546006',
    'SBI23BI290546008',
    'SBI23BI290546010',
    'SBI23BI290568008',
    'SBI23BI290731004',
    'SBI23BI293025008',
    'SBI23BI293025010',
    'SBI23BI293026006',
    'SBI23BI293026010',
    'SBI23BI293182049',
    'SBI23BI293182052',
    'SBI23BI293182054',
    'SBI23BI293182056',
    'SBI23BI293251049',
    'SBI23BI293268052',
    'SBI23BI293269049',
    'SBI23BI293269052',
    'SBI23BI293269054',
    'SBI23BI293290008',
    'SBI23BI293290010',
    'SBI23BI293291006',
    'SBI23BI293291008',
    'SBI23BI293291010',
    'SBI23BI293292008',
    'SBI23BI293292010',
    'SBI24BI293290008',
    'SBI24BI420647006',
    'SBI24BI420647010',
}

REFERENCIAS_DEAL_NAVIDAD_KIDS_2025 = {
    '286383-704',
    '290310-704',
    '290310-706',
    '290310-908',
    '423580-2308222',
    '423580-8133222',
    '425790-3761222',
    '425790-8269222',
    '425791-3028222',
    '425791-8268222',
    '425792-2308222',
    '425792-4173222',
    'SBI23BI290330704',
}

REFERENCIAS_VITTORIA_OFF_SEASON_2026 = {
    '1113442432111TG',
    '1113442442111BK',
    '1113442442111TG',
    '1113S32355111BK',
    '1113S42355111BK',
    '1113S42355111TG',
    '11A00194',
    '11A00195',
    '11A00304',
    '11A00307',
    '11A00389',
    '11A00416',
    '11A00438',
    '11A00446',
    '11A00518',
    '11A00556',
    '11A00557',
    '11A00559',
    '11A00560',
    '11A00629',
    '11A00730',
    '11A00734',
    '11A00736',
    '11E00263',
    '11E00282',
    '11E00304',
    '11E00306',
    '11E00307',
    '11E00323',
    '11E00416',
    'VIT13LL06ND30047',
    'VIT13SE01NSPS',
    'VITBAR00213',
    'VITBAR00249',
    'VITBAR00453',
    'VITBAR00454',
    'VITCOR00393',
    'VITCOR00394',
    'VITCOR00399',
    'VITCOR00400',
    'VITCOR00413',
    'VITCOR00414',
    'VITCOR00432',
    'VITCOR00434',
    'VITCOR00455',
    'VITCOR00484',
    'VITEVO00043',
    'VITMAR00341',
    'VITMAR00415',
    'VITMAZ00313',
    'VITMAZ00318',
    'VITMAZ00337',
    'VITMEZ00229',
    'VITMEZ00252',
    'VITMEZ00478',
    'VITPIT00416',
    'VITPIT00470',
    'VITPIT00471',
    'VITRID00428',
    'VITRID00429',
    'VITRID00430',
    'VITRID00452',
    'VITRUB00243',
    'VITRUB00256',
    'VITRUB00257',
    'VITSAG00324',
    'VITSEL00451',
    'VITSYE00361',
    'VITTER000401',
    'VITTER00076',
    'VITTER00260',
    'VITTER00265',
    'VITTER00406',
    'VITTER00407',
    'VITTER00409',
    'VITTER00410',
    'VITTER00437',
    'VITTER00439',
    'VITTER00441',
    'VITTER00442',
    'VITTER00445',
    'VITVAL00468',
    'VITVAL00469',
    'VITZAF00050',
    'VITZAF00305',
    'VITZAF00316',
    'VITZAF00318',
    'VITZAF00328',
    'VITZAF00466',
    'VITZAF00467',
}

REFERENCIAS_DEAL_ZAPATOS_2025 = {
    '296549-1007430',
    'SCO20ZA596622408',
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
    'SCO21ZA208101712',
    'SCO21ZA208101720',
    'SCO21ZA208101722',
    'SCO21ZA603101740',
    'SCO21ZA603101750',
    'SCO21ZA603101760',
    'SCO21ZA603101770',
    'SCO21ZA605502460',
    'SCO21ZA950656822',
    'SCO22ZA595656520',
    'SCO22ZA599656512',
    'SCO22ZA599656514',
    'SCO22ZA814165930',
    'SCO22ZA814165950',
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
    'SCO22ZA818101950',
    'SCO22ZA826200640',
    'SCO22ZA826200670',
    'SCO22ZA828727310',
    'SCO22ZA828727330',
    'SCO22ZA836727510',
    'SCO22ZA836727520',
    'SCO22ZA836727530',
    'SCO22ZA836727540',
    'SCO22ZA836727550',
    'SCO22ZA836727560',
    'SCO22ZA836727580',
    'SCO22ZA905101910',
    'SCO22ZA905101912',
    'SCO22ZA905101914',
    'SCO22ZA905101916',
    'SCO22ZA905101918',
    'SCO22ZA905101920',
    'SCOZA5627552410',
    'SCOZA5627552420',
    'SCOZA5627552430',
    'SCOZA5627552440',
    'SCOZA605-5544340',
    'SCOZA605-5544350',
    'SCOZA605-5544360',
    'SCOZA8127663400',
    'SCOZA8127663410',
    'SCOZA8127663430',
    'SCOZA8127663440',
    'SCOZA8127663460',
}

REFERENCIAS_DEAL_CASCOS_2025 = {
    '288584-1035010',
    'SCO20CA192651306',
    'SCO20CA192651307',
    'SCO20CA192651308',
    'SCO20CA192651606',
    'SCO20CA192651607',
    'SCO20CA195000106',
    'SCO20CA195651806',
    'SCO20CA195651906',
    'SCO20CA195651907',
    'SCO20CA205000106',
    'SCO20CA205103506',
    'SCO20CA205103507',
    'SCO21CA195009606',
    'SCO21CA218424422',
    'SCO21CA405691706',
    'SCO22CA195726006',
    'SCO22CA195726007',
    'SCO22CA195726008',
    'SCO22CA584651306',
    'SCO22CA584692206',
    'SCO2752080002015',
    'SCO2752080091015',
    'SCO2752080091017',
    'SCO2752080096015',
    'SCO2752080135015',
    'SCO2752086519015',
    'SCO2752086909015',
    'SCO2752086909017',
    'SCO2752120001222',
    'SCO2752124310222',
    'SCO2752126505222',
    'SCO2752126867222',
    'SCO2752126927222',
    'SCO2752126928222',
    'SCO2752127017222',
    'SCO2752180135222',
    'SCO2752184244222',
    'SCO2752326530222',
    'SCO2752326823222',
    'SCO2752356909222',
    'SCOCA205-6322006',
    'SCOCA218-0096222',
    'SCOCA2326522222',
}

REFERENCIAS_VOLTAGE_ERIDE = {
    '293210-006',
    '293210-008',
    '293210-010',
    '293290-006',
    '293290-06',
    '293292-008',
    '293292-010',
    'SBI23BI29321006',
    'SBI23BI29321008',
    'SBI23BI29321010',
    'SBI23BI29329008',
    'SBI23BI29329010',
    'SBI23BI293292008',
    'SBI23BI293292010',
    'SBI24BI29329008',
}

CAMPANAS_PRODUCTOS_OFERTADOS = [
    {
        'nombre': 'SPRING_SALE_2026',
        'inicio': '2026-02-16',
        'fin': '2026-04-30',
        'referencias': REFERENCIAS_SPRING_SALE_2026,
        'porcentaje': 1.0
    },
    {
        'nombre': 'DEAL_IRRESISTIBLE_NAVIDAD_2025',
        'inicio': '2025-10-15',
        'fin': '2025-12-31',
        'referencias': (
            REFERENCIAS_DEAL_IRRESISTIBLE_2025 |
            REFERENCIAS_DEAL_NAVIDAD_KIDS_2025 |
            REFERENCIAS_DEAL_ZAPATOS_2025 |
            REFERENCIAS_DEAL_CASCOS_2025
        ),
        'porcentaje': 1.0
    },
    {
        'nombre': 'VITTORIA_OFF_SEASON_2026',
        'inicio': '2026-04-01',
        'fin': '2026-05-30',
        'referencias': REFERENCIAS_VITTORIA_OFF_SEASON_2026,
        'porcentaje': 1.0
    },
    {
        'nombre': 'SYNCROS_2X1_TUBELESS_2026',
        'inicio': '2026-02-16',
        'fin': '2026-03-31',
        'referencias': REFERENCIAS_SYNCROS_2X1_TUBELESS_2026,
        'porcentaje': 1.0
    },
    {
        'nombre': 'VOLTAGE_ERIDE_MIX_PROMOS',
        'inicio': '2025-11-28',
        'fin': '2026-06-30',
        'referencias': REFERENCIAS_VOLTAGE_ERIDE,
        'porcentaje': 1.0
    }
]


def normalizar_referencia_producto(ref):
    """
    Normaliza referencia interna para comparación:
    - mayúsculas
    - quita espacios
    - conserva guiones para mostrar, pero genera variantes para comparar
    """
    return str(ref or '').strip().upper().replace(' ', '')


def variantes_referencia_producto(ref):
    """
    Genera variantes para casos como:
    293290-006 vs 293290-06
    SBI23BI29329008 vs SBI23BI29329008
    """
    ref = normalizar_referencia_producto(ref)

    if not ref:
        return set()

    variantes = {ref, ref.replace('-', '')}

    if '-' in ref:
        base, sufijo = ref.split('-', 1)

        if sufijo.isdigit():
            variantes.add(f"{base}-{sufijo.zfill(3)}")
            variantes.add(f"{base}-{str(int(sufijo))}")
            variantes.add(f"{base}{sufijo.zfill(3)}")
            variantes.add(f"{base}{str(int(sufijo))}")

    return {v for v in variantes if v}


def construir_indice_campanas_productos():
    """
    Convierte las referencias de campañas en un índice por variante normalizada.
    Así una referencia puede compararse aunque venga con/sin guion o con cero distinto.
    """
    indice = {}

    for campana in CAMPANAS_PRODUCTOS_OFERTADOS:
        for ref in campana.get('referencias', set()):
            for variante in variantes_referencia_producto(ref):
                if variante not in indice:
                    indice[variante] = []

                indice[variante].append(campana)

    return indice


INDICE_CAMPANAS_PRODUCTOS = construir_indice_campanas_productos()


def encontrar_campana_producto_ofertado(referencia, fecha_factura):
    """
    Devuelve la primera campaña aplicable para referencia + fecha.
    Si una referencia cae en dos campañas, se toma una sola vez para no duplicar.
    """
    if not referencia or not fecha_factura:
        return None

    fecha_factura = str(fecha_factura)[:10]

    campanas_posibles = []

    for variante in variantes_referencia_producto(referencia):
        campanas_posibles.extend(INDICE_CAMPANAS_PRODUCTOS.get(variante, []))

    for campana in campanas_posibles:
        if campana['inicio'] <= fecha_factura <= campana['fin']:
            return campana

    return None


def obtener_lineas_factura_productos_ofertados(models, uid, lista_ids_validos, min_date, max_date):
    """
    Replica la exportación validada en Odoo:
    Contabilidad -> Apuntes contables
      Publicado
      Factura de cliente
      Cuenta 401.01.01
      Producto establecido
      Fecha de factura en rango
    """
    fecha_inicio = max(min_date, FECHA_MINIMA_PRODUCTOS_OFERTADOS)

    domain = [
        ('move_id.move_type', '=', 'out_invoice'),
        ('move_id.state', '=', 'posted'),
        ('move_id.invoice_date', '>=', fecha_inicio),
        ('move_id.invoice_date', '<=', max_date),
        ('account_id.code', '=', '401.01.01'),
        ('product_id', '!=', False),
        ('partner_id', 'in', lista_ids_validos)
    ]

    return fetch_all_odoo(
        models,
        uid,
        'account.move.line',
        domain,
        [
            'id',
            'date',
            'move_id',
            'partner_id',
            'account_id',
            'name',
            'product_id',
            'quantity',
            'price_unit',
            'price_subtotal',
            'price_total'
        ],
        order='date asc'
    )


def calcular_productos_ofertados_por_campanas(models, uid, lista_ids_validos, min_date, max_date, odoo_id_to_clave, fechas_por_clave):
    """
    Calcula productos ofertados usando facturas reales de Odoo por referencia interna y fecha.
    Retorna un diccionario con total con IVA tomado por clave.
    """
    lineas = obtener_lineas_factura_productos_ofertados(
        models,
        uid,
        lista_ids_validos,
        min_date,
        max_date
    )

    product_ids = list({
        obtener_id_m2o(linea.get('product_id'))
        for linea in lineas
        if obtener_id_m2o(linea.get('product_id'))
    })

    productos = obtener_productos_por_ids(models, uid, product_ids)
    producto_por_id = {p['id']: p for p in productos}

    totales_por_clave = {}
    detalle = []

    for linea in lineas:
        partner = linea.get('partner_id')

        if not partner:
            continue

        partner_id = partner[0]
        clave = odoo_id_to_clave.get(partner_id)

        if not clave:
            continue

        fecha_factura = str(linea.get('date') or '')[:10]

        if not fecha_factura:
            continue

        if fecha_factura < FECHA_MINIMA_PRODUCTOS_OFERTADOS:
            continue

        rango = fechas_por_clave.get(clave)

        if rango and rango.get('inicio') and rango.get('fin'):
            fecha_inicio_real = max(rango['inicio'], FECHA_MINIMA_PRODUCTOS_OFERTADOS)

            if not (fecha_inicio_real <= fecha_factura <= rango['fin']):
                continue

        product_id = obtener_id_m2o(linea.get('product_id'))
        producto_info = producto_por_id.get(product_id, {})
        referencia = normalizar_referencia_producto(producto_info.get('default_code'))

        campana = encontrar_campana_producto_ofertado(referencia, fecha_factura)

        if not campana:
            continue

        total_con_iva = float(linea.get('price_total') or 0)
        porcentaje = float(campana.get('porcentaje') or 1.0)
        monto_tomado = total_con_iva * porcentaje

        if monto_tomado <= 0:
            continue

        totales_por_clave[clave] = totales_por_clave.get(clave, 0.0) + monto_tomado

        detalle.append({
            'clave': clave,
            'cliente': partner[1] if len(partner) > 1 else '',
            'fecha_factura': fecha_factura,
            'factura': obtener_nombre_m2o(linea.get('move_id')),
            'referencia_interna': referencia,
            'producto': obtener_nombre_m2o(linea.get('product_id')),
            'cantidad': float(linea.get('quantity') or 0),
            'precio_unitario_sin_iva': round(float(linea.get('price_unit') or 0), 2),
            'subtotal_sin_iva': round(float(linea.get('price_subtotal') or 0), 2),
            'total_con_iva': round(total_con_iva, 2),
            'campana': campana['nombre'],
            'porcentaje_tomado': porcentaje,
            'monto_tomado': round(monto_tomado, 2)
        })

    return totales_por_clave, detalle


def calcular_productos_ofertados_por_etiqueta_sale_report(
    models,
    uid,
    lista_ids_validos,
    min_date,
    max_date,
    odoo_id_to_clave,
    fechas_por_clave,
    llaves_ya_tomadas=None
):
    """
    Calcula productos ofertados desde:
    Ventas -> Reportes -> Análisis de ventas

    Filtros equivalentes en Odoo:
    - Fecha de la orden dentro del rango
    - Producto / Etiquetas de la plantilla del producto = Producto Ofertado
    - Estado de factura = Facturado por completo
    - Cliente dentro de los partners válidos

    IMPORTANTE:
    También respeta la fecha de inicio y fin del distribuidor desde clientes.f_inicio / clientes.f_fin.

    Evita duplicar ventas ya tomadas por campañas/referencias usando:
    clave + fecha + referencia + total_con_iva
    """

    llaves_ya_tomadas = llaves_ya_tomadas or set()

    fecha_inicio_global = max(min_date, FECHA_MINIMA_PRODUCTOS_OFERTADOS)

    tags_ofertados = obtener_tags_por_nombres(
        models,
        uid,
        ['PRODUCTO OFERTADO']
    )

    tag_ids = [tag['id'] for tag in tags_ofertados]

    if not tag_ids:
        return {}, []

    domain = [
        ('date', '>=', f'{fecha_inicio_global} 00:00:00'),
        ('date', '<=', f'{max_date} 23:59:59'),
        ('partner_id', 'in', lista_ids_validos),
        ('product_tmpl_id.product_tag_ids', 'in', tag_ids),
        ('invoice_status', '=', 'invoiced')
    ]

    registros = fetch_all_odoo(
        models,
        uid,
        'sale.report',
        domain,
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
            'product_uom_qty',
            'price_subtotal',
            'price_total',
            'discount_amount',
            'invoice_status'
        ],
        order='date asc'
    )

    product_ids = list({
        obtener_id_m2o(r.get('product_id'))
        for r in registros
        if obtener_id_m2o(r.get('product_id'))
    })

    productos = obtener_productos_por_ids(models, uid, product_ids)
    producto_por_id = {
        p['id']: p
        for p in productos
    }

    totales_por_clave = {}
    detalle = []

    for registro in registros:
        partner = registro.get('partner_id')
        commercial_partner = registro.get('commercial_partner_id')

        partner_id = obtener_id_m2o(partner)
        commercial_partner_id = obtener_id_m2o(commercial_partner)

        clave = None

        # Primero intentamos con el contacto de la venta.
        if partner_id:
            clave = odoo_id_to_clave.get(partner_id)

        # Si no se encontró, intentamos con el commercial_partner_id.
        # Esto ayuda cuando Odoo muestra contactos hijos tipo "Cycling riding B2B".
        if not clave and commercial_partner_id:
            clave = odoo_id_to_clave.get(commercial_partner_id)

        if not clave:
            continue

        fecha_orden = str(registro.get('date') or '')[:10]

        if not fecha_orden:
            continue

        if fecha_orden < FECHA_MINIMA_PRODUCTOS_OFERTADOS:
            continue

        # ==========================================================
        # VALIDACIÓN DE FECHA DEL DISTRIBUIDOR
        # ==========================================================
        rango = fechas_por_clave.get(clave)

        if rango and rango.get('inicio') and rango.get('fin'):
            fecha_inicio_real = max(
                rango['inicio'],
                FECHA_MINIMA_PRODUCTOS_OFERTADOS
            )

            if not (fecha_inicio_real <= fecha_orden <= rango['fin']):
                continue

        product_id = obtener_id_m2o(registro.get('product_id'))
        producto_info = producto_por_id.get(product_id, {})

        referencia = normalizar_referencia_producto(
            producto_info.get('default_code')
        )

        total_con_iva = float(registro.get('price_total') or 0)

        if total_con_iva <= 0:
            continue

        # Evita duplicar contra lo ya tomado por campañas/referencias.
        llave_dedupe = (
            str(clave).strip().upper(),
            fecha_orden,
            referencia,
            round(total_con_iva, 2)
        )

        if llave_dedupe in llaves_ya_tomadas:
            continue

        totales_por_clave[clave] = (
            totales_por_clave.get(clave, 0.0) + total_con_iva
        )

        detalle.append({
            'clave': clave,
            'cliente': obtener_nombre_m2o(partner),
            'fecha_orden': fecha_orden,
            'orden': obtener_nombre_m2o(registro.get('order_reference')),
            'referencia_interna': referencia,
            'producto': obtener_nombre_m2o(registro.get('product_id')),
            'cantidad': float(registro.get('product_uom_qty') or 0),
            'subtotal_sin_iva': round(float(registro.get('price_subtotal') or 0), 2),
            'total_con_iva': round(total_con_iva, 2),
            'fuente': 'SALE_REPORT_ETIQUETA_PRODUCTO_OFERTADO',
            'etiqueta': 'Producto Ofertado',
            'monto_tomado': round(total_con_iva, 2)
        })

    return totales_por_clave, detalle


# ==============================================================================
# METODOLOGÍA POR ETIQUETAS — campañas y productos ofertados
# Cada entrada define el nombre de la etiqueta en Odoo y su ventana de fechas.
# inicio/fin = None → sin restricción de fecha (aplica todo el periodo del dist.)
# ==============================================================================
CAMPANAS_ETIQUETAS = [
    # id=8  "Producto Ofertado" — sin restricción de fecha
    {'nombre': 'Producto Ofertado', 'tag_id': 8,  'inicio': None,         'fin': None},
    # id=23 "DEAL IRRESISTIBLE" (= DEAL NAVIDEÑO en los reportes)
    {'nombre': 'DEAL NAVIDENO',     'tag_id': 23, 'inicio': '2025-10-15', 'fin': '2025-12-31'},
    # id=30 "SPRING SALE 26´"
    {'nombre': 'SPRING SALE 26',    'tag_id': 30, 'inicio': '2026-02-16', 'fin': '2026-04-30'},
    # id=31 "Season Off!"
    {'nombre': 'Season Off',        'tag_id': 31, 'inicio': '2026-04-01', 'fin': '2026-05-30'},
    # id=36 "Scott Sale"
    {'nombre': 'Scott Sale',        'tag_id': 36, 'inicio': '2026-04-29', 'fin': '2026-06-30'},
]

# Ruta al Excel de referencia — exportación directa de Odoo con todos los productos
# etiquetados con campañas (Scott Sale, DEAL NAVIDENO, SPRING SALE 26, Season Off,
# Producto Ofertado). Una sola hoja, col A=Referencia, col B=Nombre, col C=Etiqueta.
CAMPANAS_EXCEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'CAMAPÑAS Y PRODUCTO OFERTADO.xlsx'
)


def cargar_refs_por_etiqueta(models, uid):
    """
    Consulta Odoo y retorna un dict {ref: [lista_de_campanas]}.

    Un producto puede tener múltiples tags (ej. DEAL NAVIDENO + SPRING SALE 26).
    Se guardan TODOS los tags por ref; la función de cálculo usa la fecha de
    factura para determinar en cuál campaña aplica cada línea.
    """
    from utils.odoo_utils import ODOO_DB, ODOO_PASSWORD

    refs_por_etiqueta = {}   # {ref: [{'nombre': ..., 'inicio': ..., 'fin': ...}, ...]}

    for campana in CAMPANAS_ETIQUETAS:
        tag_id = campana['tag_id']
        try:
            tmpl_ids = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                'product.template', 'search',
                [[('product_tag_ids', 'in', [tag_id])]],
                {'limit': 10000}
            )
            if not tmpl_ids:
                print(f"[AVISO] Tag '{campana['nombre']}' (id={tag_id}): 0 plantillas en Odoo.")
                continue

            variants = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                'product.product', 'search_read',
                [[('product_tmpl_id', 'in', tmpl_ids),
                  ('default_code', '!=', False)]],
                {'fields': ['default_code'], 'limit': 50000}
            )

            info_camp = {
                'nombre': campana['nombre'],
                'inicio': campana['inicio'],
                'fin':    campana['fin'],
            }
            agregadas = 0
            for v in variants:
                ref = str(v['default_code']).strip()
                if not ref:
                    continue
                if ref not in refs_por_etiqueta:
                    refs_por_etiqueta[ref] = []
                # Evitar duplicar la misma campaña
                if not any(c['nombre'] == campana['nombre'] for c in refs_por_etiqueta[ref]):
                    refs_por_etiqueta[ref].append(info_camp)
                    agregadas += 1
            print(f"[OK] Tag '{campana['nombre']}': {len(tmpl_ids)} plantillas, {len(variants)} variantes, {agregadas} refs.")

        except Exception as e:
            print(f"[ERROR] Tag '{campana['nombre']}': {e}")

    print(f"[OK] Total refs con al menos un tag: {len(refs_por_etiqueta)}")
    return refs_por_etiqueta


def _extract_template_num(ref):
    """Extrae el número de template (5-6 dígitos) de una referencia de variante."""
    if not ref:
        return None
    m = re.match(r'^(\d{5,6})', ref)
    if m:
        return m.group(1)
    m = re.search(r'BI(\d{6})\d+$', ref)
    if m:
        return m.group(1)
    m = re.search(r'(\d{6})', ref)
    if m:
        return m.group(1)
    return None


def _norm_nombre(s):
    """Normaliza nombre para comparación: minúsculas y sin espacios (ej. '22SPORT' == '22 SPORT')."""
    return re.sub(r'\s+', '', s.lower())


def cargar_campanas_desde_excel():
    """
    Lee CAMPAÑAS Y PRODUCTO OFERTADO.xlsx — exportación directa de Odoo.
    Estructura: col A=Referencia interna, col B=Nombre, col C=Etiqueta.
    Cuando un producto tiene múltiples etiquetas, A y B solo aparecen en la
    primera fila del grupo; las siguientes filas tienen A y B vacíos.

    Retorna (refs_exactos, tmpl_por_num, nombre_list):
      - refs_exactos: {ref_variante: [lista_campanas]}
      - tmpl_por_num: {num_template: [lista_campanas]}
      - nombre_list:  [{nombre_lower, camps}, ...]  para fallback por nombre
    """
    CAMPANA_INFO = {
        'DEAL NAVIDENO':     {'inicio': '2025-10-15', 'fin': '2025-12-31'},
        'SPRING SALE 26':    {'inicio': '2026-02-16', 'fin': '2026-04-30'},
        'Season Off':        {'inicio': '2026-04-01', 'fin': '2026-05-30'},
        'Scott Sale':        {'inicio': '2026-04-29', 'fin': '2026-06-30'},
        'Producto Ofertado': {'inicio': None,         'fin': None},
    }
    CAMP_TAGS = set(CAMPANA_INFO.keys())

    refs_exactos = {}
    tmpl_por_num = {}
    nombre_list  = []
    seen_nombres = set()

    def _add(d, key, camp_list):
        if not key:
            return
        existing = d.setdefault(key, [])
        for c in camp_list:
            if not any(x['nombre'] == c['nombre'] for x in existing):
                existing.append(c)

    try:
        wb = openpyxl.load_workbook(CAMPANAS_EXCEL_PATH, read_only=True, data_only=True)
    except Exception as e:
        print(f"[ERROR] No se pudo leer Excel de campañas ({CAMPANAS_EXCEL_PATH}): {e}")
        return {}, {}, []

    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))[1:]  # saltar encabezado

    # Agrupar filas por producto (forward-fill de ref y nombre)
    cur_ref, cur_nom, cur_tags = None, None, []
    groups = []  # [(ref, nombre, [tags])]

    for row in rows:
        # Estructura actual del export: col0=ID repetido, col1=Referencia, col2=Nombre, col3=Etiqueta
        ref = str(row[1]).strip() if row[1] else None
        nom = str(row[2]).strip() if row[2] else None
        tag = str(row[3]).strip() if row[3] else None

        if ref:
            # Nueva variante con referencia
            if cur_nom is not None:
                groups.append((cur_ref, cur_nom, cur_tags))
            cur_ref, cur_nom, cur_tags = ref, nom, ([tag] if tag else [])
        elif nom and not ref:
            # Plantilla sin código de variante
            if cur_nom is not None:
                groups.append((cur_ref, cur_nom, cur_tags))
            cur_ref, cur_nom, cur_tags = None, nom, ([tag] if tag else [])
        elif tag:
            cur_tags.append(tag)

    if cur_nom is not None:
        groups.append((cur_ref, cur_nom, cur_tags))

    wb.close()

    # Construir los tres índices
    for ref, nombre, tags in groups:
        camps = [
            {'nombre': t, 'inicio': CAMPANA_INFO[t]['inicio'], 'fin': CAMPANA_INFO[t]['fin']}
            for t in tags if t in CAMP_TAGS
        ]
        if not camps:
            continue

        if ref:
            _add(refs_exactos, ref, camps)

        m = re.match(r'^(\d{5,6})', nombre or '')
        if m:
            _add(tmpl_por_num, m.group(1), camps)

        # nombre_list solo para campañas con fecha (no Producto Ofertado)
        camps_con_fecha = [c for c in camps if c['nombre'] != 'Producto Ofertado']
        if camps_con_fecha:
            nombre_stripped = re.sub(r'^\d+\s*', '', nombre or '').strip()
            key_n = nombre_stripped.lower()
            if nombre_stripped and key_n not in seen_nombres:
                nombre_list.append({'nombre_lower': key_n, 'camps': camps_con_fecha})
                seen_nombres.add(key_n)

    print(f"[OK] CAMPAÑAS+PO: {len(refs_exactos)} refs exactas, "
          f"{len(tmpl_por_num)} templates, {len(nombre_list)} nombres.")
    return refs_exactos, tmpl_por_num, nombre_list


def calcular_productos_ofertados_etiquetas(cursor_dict, fechas_por_clave, refs_exactos, tmpl_por_num, nombre_list=None, refs_con_tag_odoo=None):
    """
    Calcula productos_ofertados usando las hojas CAMPAÑAS y PO del Excel de referencia.

    Para cada línea de factura del distribuidor en su periodo:
      1. Busca match exacto de ref en refs_exactos
      2. Si no encuentra, extrae el número de template de la ref y busca en tmpl_por_num
      3. Si la fecha de factura está dentro de la ventana de la campaña → suma venta_total

    venta_total es CON IVA (price_total de Odoo), consistente con COMPRAS_TOTALES_CRUDO
    y con los totales CON IVA que muestra el Excel de referencia.

    Retorna {clave: monto_CON_IVA}.
    """
    if not refs_exactos and not tmpl_por_num:
        print("[AVISO] Sin datos de campañas — productos_ofertados = 0 para todos.")
        return {}

    PRIORITY = ['Producto Ofertado', 'DEAL NAVIDENO', 'SPRING SALE 26', 'Season Off', 'Scott Sale']
    _nombre_list = nombre_list or []

    totales = {}

    for clave, rango in fechas_por_clave.items():
        fecha_inicio_ef = max(str(rango.get('inicio') or ''), FECHA_MINIMA_PRODUCTOS_OFERTADOS)
        fecha_fin_ef    = str(rango.get('fin') or FECHA_CORTE)

        if fecha_inicio_ef > fecha_fin_ef:
            continue

        cursor_dict.execute("""
            SELECT referencia_interna, nombre_producto, fecha_factura, venta_total
            FROM monitor
            WHERE contacto_referencia = %s
              AND fecha_factura >= %s
              AND fecha_factura <= %s
              AND referencia_interna IS NOT NULL
              AND venta_total > 0
              AND cantidad > 0
        """, (clave, fecha_inicio_ef, fecha_fin_ef))

        for linea in cursor_dict.fetchall():
            ref = str(linea['referencia_interna'] or '').strip()
            if not ref:
                continue

            # 1. Match exacto por referencia de variante
            campanas_ref = refs_exactos.get(ref)

            # 2. Match por número de template extraído de la ref
            # Para refs con formato numérico "NNNNNN-XXXXXXX" (ej. 275894-5547022) se aplica
            # el mismo gate Odoo que el paso 3: el template "275894" proviene de
            # SCO20ZA894588xxx (NG/AM, con DEAL NAVIDENO) pero no debe aplicarse a
            # 275894-5547xxx (NEGRO MATE/GRIS, sin etiqueta de campaña).
            if not campanas_ref:
                tmpl = _extract_template_num(ref)
                if tmpl:
                    _is_num_ref = bool(re.match(r'^\d{5,6}-', ref))
                    if not _is_num_ref or refs_con_tag_odoo is None or ref in refs_con_tag_odoo:
                        campanas_ref = tmpl_por_num.get(tmpl)

            # 3. Fallback por nombre (igual que Excel: LEFT(nombre,20) wildcard en CAMPAÑAS!G)
            # Para refs con formato numérico "NNNNNN-XXXXXXX" (ej. 281217-1659014) se aplica
            # el gate Odoo: solo se cuentan si tienen etiqueta de campaña. Esto evita falsos
            # positivos donde dos productos distintos comparten el mismo prefijo de 20 chars
            # (ej. "ZAPATOS SCOTT 22 SPO" → CRUS-R y TRAIL EVO BOA ambos coinciden).
            # Refs con letra (SBI23..., BLD23..., SCO22...) no necesitan gate porque son
            # coincidencias más específicas y están explícitamente en CAMPAÑAS Excel.
            if not campanas_ref and _nombre_list:
                _is_num_ref = bool(re.match(r'^\d{5,6}-', ref))
                _tiene_tag = not _is_num_ref or refs_con_tag_odoo is None or ref in refs_con_tag_odoo
                if _tiene_tag:
                    nombre_prod = str(linea['nombre_producto'] or '').strip()
                    if nombre_prod:
                        prefix_norm = _norm_nombre(nombre_prod[:20])
                        for entry in _nombre_list:
                            nombre_norm = _norm_nombre(entry['nombre_lower'])
                            if prefix_norm in nombre_norm or nombre_norm in prefix_norm:
                                campanas_ref = entry['camps']
                                break

            if not campanas_ref:
                continue

            fecha = str(linea['fecha_factura'])[:10]

            # Seleccionar la primera campaña activa según prioridad y ventana de fecha
            campana_activa = None
            for pname in PRIORITY:
                for campana in campanas_ref:
                    if campana['nombre'] != pname:
                        continue
                    if campana['inicio'] and fecha < campana['inicio']:
                        continue
                    if campana['fin'] and fecha > campana['fin']:
                        continue
                    campana_activa = campana
                    break
                if campana_activa:
                    break

            if not campana_activa:
                continue

            # venta_total es CON IVA — mismo criterio que COMPRAS_TOTALES_CRUDO
            venta_total = float(linea['venta_total'] or 0)
            totales[clave] = totales.get(clave, 0.0) + venta_total

    return totales




# ==============================================================================
# 1. FUNCIÓN MAESTRA: OBTENER DEDUCCIONES DESDE ODOO
# ==============================================================================
def obtener_deducciones_odoo(claves_db, fechas_por_clave):
    resultado_odoo = get_odoo_models()
    uid = resultado_odoo[0]
    models = resultado_odoo[1]

    print("Mapeando IDs internos de Odoo con las Claves de la Base de Datos...")
    partners_odoo = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'res.partner', 'search_read',
        [[]], {'fields': ['id', 'name', 'ref', 'parent_id']})

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
        'MARCO TULIO ANDRADE NAVARRO': 'JC539',
        'NARUCO': 'LC625'
    }

    for p in partners_odoo:
        name_odoo = str(p.get('name', '')).strip().upper()

        if name_odoo in redirecciones:
            clave_redirect = redirecciones[name_odoo]

            if clave_redirect in claves_db:
                odoo_id_to_clave[p['id']] = clave_redirect
                print(
                    f"DEBUG REDIRECCION FORZADA: partner_id={p['id']} "
                    f"name={name_odoo} -> clave={clave_redirect}"
                )

    # Segunda pasada: mapear cuentas hijas (contactos secundarios) al mismo CLAVE del padre.
    # Esto cubre casos como "Cycling riding B2B" que es hija de CYCLING RIDING DE MEXICO (GD380).
    for p in partners_odoo:
        if p['id'] not in odoo_id_to_clave and p.get('parent_id'):
            parent_id = p['parent_id'][0] if isinstance(p['parent_id'], (list, tuple)) else p['parent_id']
            if parent_id and parent_id in odoo_id_to_clave:
                odoo_id_to_clave[p['id']] = odoo_id_to_clave[parent_id]

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

        # ==========================================================================
        # B. PRODUCTOS OFERTADOS
        # ==========================================================================
        # La metodología de campañas y etiqueta fue reemplazada.
        # Los productos ofertados se calculan en el paso siguiente desde monitor
        # (lista de precios), por lo que aquí el valor se inicializa en 0.

        # ==========================================================================
        # B2. BICICLETAS DEMO
        # ==========================================================================
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

        for orden in ordenes_demo:
            partner = orden.get('partner_id')

            if not partner:
                continue

            partner_id = partner[0]
            fecha_orden = str(orden.get('date_order') or '')[:10]
            monto_demo = float(orden.get('amount_total') or 0)

            agregar_valor(
                partner_id,
                'demo',
                monto_demo,
                fecha_orden
            )

        # ==========================================================================
        # B3. BICICLETAS BOLD
        # ==========================================================================
        domain_bold = [
            ('move_id.move_type', '=', 'out_invoice'),
            ('move_id.state', '=', 'posted'),
            ('move_id.invoice_date', '>=', min_date),
            ('move_id.invoice_date', '<=', max_date),
            ('quantity', '!=', 0),
            ('partner_id', 'in', lista_ids_validos),
            ('display_type', '=', 'product'),
            ('name', 'ilike', 'BOLD'),
            ('name', 'ilike', 'BICICLETA'),
        ]

        lineas_bold = fetch_all_odoo(
            models,
            uid,
            'account.move.line',
            domain_bold,
            [
                'partner_id',
                'price_total',
                'date',
                'name',
                'move_id'
            ]
        )

        for linea in lineas_bold:
            partner = linea.get('partner_id')

            if not partner:
                continue

            agregar_valor(
                partner[0],
                'bold',
                linea.get('price_total', 0),
                linea.get('date')
            )

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

            agregar_valor(
                pid,
                'nc',
                nc['amount_total'],
                nc.get('invoice_date')
            )

        if 'GD380' in resultados_por_clave:
            print("DEBUG FINAL SINCRONIZAR GD380:", resultados_por_clave['GD380'])

        return resultados_por_clave
    except Exception as e:
        print(f"[ERROR Odoo] {e}")
        traceback.print_exc()
        return {}


# ==============================================================================
# 2. FUNCIÓN DE SINCRONIZACIÓN AUTOMÁTICA
# ==============================================================================

def _recalcular_previo_clave_cierre(conexion, cursor_dict, cursor, clave, f_inicio, fecha_cierre):
    """
    Recalcula las columnas de avance en previo para un distribuidor individual,
    usando fecha_cierre como tope de fecha. Se llama al cerrar la temporada
    para asegurar que el previo no incluya compras posteriores al cierre.
    """
    SCOTT_COND = """(
        (
            UPPER(TRIM(m.marca)) IN ('SCOTT', 'MEGAMO')
            AND (UPPER(TRIM(m.subcategoria)) = 'BICICLETA' OR UPPER(m.nombre_producto) LIKE '%%BICICLETA%%')
            AND (UPPER(TRIM(m.apparel)) = 'NO' OR UPPER(m.nombre_producto) LIKE '%%BICICLETA%%')
        )
        OR (UPPER(TRIM(m.marca)) = 'BOLD' AND UPPER(TRIM(m.subcategoria)) = 'BICICLETA')
    )"""

    APP_COND = "(m.apparel = 'SI' OR (m.marca = 'BOLD' AND UPPER(TRIM(COALESCE(m.subcategoria,''))) != 'BICICLETA'))"

    cursor_dict.execute(f"""
        SELECT
            COALESCE(SUM(m.venta_total), 0) AS total_bruto,
            COALESCE(SUM(CASE WHEN m.marca = 'SYNCROS' THEN m.venta_total ELSE 0 END), 0) AS syncros,
            COALESCE(SUM(CASE WHEN {APP_COND} THEN m.venta_total ELSE 0 END), 0) AS apparel,
            COALESCE(SUM(CASE WHEN m.marca = 'VITTORIA' THEN m.venta_total ELSE 0 END), 0) AS vittoria,
            COALESCE(SUM(CASE WHEN UPPER(TRIM(m.marca)) = 'BOLD' AND UPPER(TRIM(m.subcategoria)) = 'BICICLETA' THEN m.venta_total ELSE 0 END), 0) AS bold,
            COALESCE(SUM(CASE WHEN {SCOTT_COND} THEN m.venta_total ELSE 0 END), 0) AS scott,
            COALESCE(SUM(CASE WHEN m.fecha_factura <= '2025-08-31' AND {SCOTT_COND} THEN m.venta_total ELSE 0 END), 0) AS scott_jul_ago,
            COALESCE(SUM(CASE WHEN m.fecha_factura BETWEEN '2025-09-01' AND '2025-10-31' AND {SCOTT_COND} THEN m.venta_total ELSE 0 END), 0) AS scott_sep_oct,
            COALESCE(SUM(CASE WHEN m.fecha_factura BETWEEN '2025-11-01' AND '2025-12-31' AND {SCOTT_COND} THEN m.venta_total ELSE 0 END), 0) AS scott_nov_dic,
            COALESCE(SUM(CASE WHEN m.fecha_factura BETWEEN '2026-01-01' AND '2026-02-28' AND {SCOTT_COND} THEN m.venta_total ELSE 0 END), 0) AS scott_ene_feb,
            COALESCE(SUM(CASE WHEN m.fecha_factura BETWEEN '2026-03-01' AND '2026-04-30' AND {SCOTT_COND} THEN m.venta_total ELSE 0 END), 0) AS scott_mar_abr,
            COALESCE(SUM(CASE WHEN m.fecha_factura BETWEEN '2026-05-01' AND '2026-06-30' AND {SCOTT_COND} THEN m.venta_total ELSE 0 END), 0) AS scott_may_jun,
            COALESCE(SUM(CASE WHEN m.fecha_factura <= '2025-08-31' AND m.marca = 'SYNCROS' THEN m.venta_total ELSE 0 END), 0) AS syncros_jul_ago,
            COALESCE(SUM(CASE WHEN m.fecha_factura BETWEEN '2025-09-01' AND '2025-10-31' AND m.marca = 'SYNCROS' THEN m.venta_total ELSE 0 END), 0) AS syncros_sep_oct,
            COALESCE(SUM(CASE WHEN m.fecha_factura BETWEEN '2025-11-01' AND '2025-12-31' AND m.marca = 'SYNCROS' THEN m.venta_total ELSE 0 END), 0) AS syncros_nov_dic,
            COALESCE(SUM(CASE WHEN m.fecha_factura BETWEEN '2026-01-01' AND '2026-02-28' AND m.marca = 'SYNCROS' THEN m.venta_total ELSE 0 END), 0) AS syncros_ene_feb,
            COALESCE(SUM(CASE WHEN m.fecha_factura BETWEEN '2026-03-01' AND '2026-04-30' AND m.marca = 'SYNCROS' THEN m.venta_total ELSE 0 END), 0) AS syncros_mar_abr,
            COALESCE(SUM(CASE WHEN m.fecha_factura BETWEEN '2026-05-01' AND '2026-06-30' AND m.marca = 'SYNCROS' THEN m.venta_total ELSE 0 END), 0) AS syncros_may_jun,
            COALESCE(SUM(CASE WHEN m.fecha_factura <= '2025-08-31' AND {APP_COND} THEN m.venta_total ELSE 0 END), 0) AS apparel_jul_ago,
            COALESCE(SUM(CASE WHEN m.fecha_factura BETWEEN '2025-09-01' AND '2025-10-31' AND {APP_COND} THEN m.venta_total ELSE 0 END), 0) AS apparel_sep_oct,
            COALESCE(SUM(CASE WHEN m.fecha_factura BETWEEN '2025-11-01' AND '2025-12-31' AND {APP_COND} THEN m.venta_total ELSE 0 END), 0) AS apparel_nov_dic,
            COALESCE(SUM(CASE WHEN m.fecha_factura BETWEEN '2026-01-01' AND '2026-02-28' AND {APP_COND} THEN m.venta_total ELSE 0 END), 0) AS apparel_ene_feb,
            COALESCE(SUM(CASE WHEN m.fecha_factura BETWEEN '2026-03-01' AND '2026-04-30' AND {APP_COND} THEN m.venta_total ELSE 0 END), 0) AS apparel_mar_abr,
            COALESCE(SUM(CASE WHEN m.fecha_factura BETWEEN '2026-05-01' AND '2026-06-30' AND {APP_COND} THEN m.venta_total ELSE 0 END), 0) AS apparel_may_jun,
            COALESCE(SUM(CASE WHEN m.fecha_factura <= '2025-08-31' AND m.marca = 'VITTORIA' THEN m.venta_total ELSE 0 END), 0) AS vittoria_jul_ago,
            COALESCE(SUM(CASE WHEN m.fecha_factura BETWEEN '2025-09-01' AND '2025-10-31' AND m.marca = 'VITTORIA' THEN m.venta_total ELSE 0 END), 0) AS vittoria_sep_oct,
            COALESCE(SUM(CASE WHEN m.fecha_factura BETWEEN '2025-11-01' AND '2025-12-31' AND m.marca = 'VITTORIA' THEN m.venta_total ELSE 0 END), 0) AS vittoria_nov_dic,
            COALESCE(SUM(CASE WHEN m.fecha_factura BETWEEN '2026-01-01' AND '2026-02-28' AND m.marca = 'VITTORIA' THEN m.venta_total ELSE 0 END), 0) AS vittoria_ene_feb,
            COALESCE(SUM(CASE WHEN m.fecha_factura BETWEEN '2026-03-01' AND '2026-04-30' AND m.marca = 'VITTORIA' THEN m.venta_total ELSE 0 END), 0) AS vittoria_mar_abr,
            COALESCE(SUM(CASE WHEN m.fecha_factura BETWEEN '2026-05-01' AND '2026-06-30' AND m.marca = 'VITTORIA' THEN m.venta_total ELSE 0 END), 0) AS vittoria_may_jun
        FROM monitor m
        WHERE m.contacto_referencia = %s
          AND m.fecha_factura >= %s
          AND m.fecha_factura <= %s
    """, (clave, f_inicio, fecha_cierre))

    row = cursor_dict.fetchone() or {}

    cursor_dict.execute("""
        SELECT id, compromiso_scott, compromiso_apparel_syncros_vittoria,
               compromiso_jul_ago, compromiso_sep_oct, compromiso_nov_dic,
               compromiso_ene_feb, compromiso_mar_abr, compromiso_may_jun,
               compromiso_jul_ago_app, compromiso_sep_oct_app, compromiso_nov_dic_app,
               compromiso_ene_feb_app, compromiso_mar_abr_app, compromiso_may_jun_app,
               compra_minima_inicial, compra_minima_anual
        FROM previo
        WHERE UPPER(TRIM(clave)) = %s AND (es_integral IS NULL OR es_integral = 0)
        LIMIT 1
    """, (clave,))
    previo_row = cursor_dict.fetchone()
    if not previo_row:
        return

    def flt(v): return float(v or 0)
    def pct(avance, compromiso): return int(round(avance / compromiso * 100)) if compromiso > 0 else 0

    PERIODS = ['jul_ago', 'sep_oct', 'nov_dic', 'ene_feb', 'mar_abr', 'may_jun']
    syncros  = flt(row.get('syncros'))
    apparel  = flt(row.get('apparel'))
    vittoria = flt(row.get('vittoria'))
    bold     = flt(row.get('bold'))
    scott    = flt(row.get('scott'))
    acum_total = round(flt(row.get('total_bruto')), 2)

    p_syncros  = {p: flt(row.get(f'syncros_{p}'))  for p in PERIODS}
    p_apparel  = {p: flt(row.get(f'apparel_{p}'))  for p in PERIODS}
    p_vittoria = {p: flt(row.get(f'vittoria_{p}')) for p in PERIODS}
    p_scott    = {p: flt(row.get(f'scott_{p}'))    for p in PERIODS}
    app_global = round(syncros + apparel + vittoria, 2)
    p_app = {p: round(p_syncros[p] + p_apparel[p] + p_vittoria[p], 2) for p in PERIODS}

    cm_ini = flt(previo_row.get('compra_minima_inicial'))
    cm_anu = flt(previo_row.get('compra_minima_anual'))

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
            avance_jul_ago     = %s, porcentaje_jul_ago     = %s,
            avance_sep_oct     = %s, porcentaje_sep_oct     = %s,
            avance_nov_dic     = %s, porcentaje_nov_dic     = %s,
            avance_ene_feb     = %s, porcentaje_ene_feb     = %s,
            avance_mar_abr     = %s, porcentaje_mar_abr     = %s,
            avance_may_jun     = %s, porcentaje_may_jun     = %s,
            avance_jul_ago_app = %s, porcentaje_jul_ago_app = %s,
            avance_sep_oct_app = %s, porcentaje_sep_oct_app = %s,
            avance_nov_dic_app = %s, porcentaje_nov_dic_app = %s,
            avance_ene_feb_app = %s, porcentaje_ene_feb_app = %s,
            avance_mar_abr_app = %s, porcentaje_mar_abr_app = %s,
            avance_may_jun_app = %s, porcentaje_may_jun_app = %s
        WHERE id = %s
    """, (
        acum_total, syncros, apparel, vittoria, bold,
        acum_total, scott, app_global,
        pct(acum_total, cm_ini), pct(acum_total, cm_anu),
        pct(scott,      flt(previo_row.get('compromiso_scott'))),
        pct(app_global, flt(previo_row.get('compromiso_apparel_syncros_vittoria'))),
        p_scott['jul_ago'], pct(p_scott['jul_ago'], flt(previo_row.get('compromiso_jul_ago'))),
        p_scott['sep_oct'], pct(p_scott['sep_oct'], flt(previo_row.get('compromiso_sep_oct'))),
        p_scott['nov_dic'], pct(p_scott['nov_dic'], flt(previo_row.get('compromiso_nov_dic'))),
        p_scott['ene_feb'], pct(p_scott['ene_feb'], flt(previo_row.get('compromiso_ene_feb'))),
        p_scott['mar_abr'], pct(p_scott['mar_abr'], flt(previo_row.get('compromiso_mar_abr'))),
        p_scott['may_jun'], pct(p_scott['may_jun'], flt(previo_row.get('compromiso_may_jun'))),
        p_app['jul_ago'],   pct(p_app['jul_ago'],   flt(previo_row.get('compromiso_jul_ago_app'))),
        p_app['sep_oct'],   pct(p_app['sep_oct'],   flt(previo_row.get('compromiso_sep_oct_app'))),
        p_app['nov_dic'],   pct(p_app['nov_dic'],   flt(previo_row.get('compromiso_nov_dic_app'))),
        p_app['ene_feb'],   pct(p_app['ene_feb'],   flt(previo_row.get('compromiso_ene_feb_app'))),
        p_app['mar_abr'],   pct(p_app['mar_abr'],   flt(previo_row.get('compromiso_mar_abr_app'))),
        p_app['may_jun'],   pct(p_app['may_jun'],   flt(previo_row.get('compromiso_may_jun_app'))),
        previo_row['id']
    ))


def ejecutar_sincronizacion_y_calculos():
    conexion = obtener_conexion()
    cursor_dict = conexion.cursor(dictionary=True)
    cursor = conexion.cursor()

    try:
        print("[INFO] Auto-sincronizando Odoo y calculando matematicas...")

        # ══════════════════════════════════════════════════════════════════════
        # PASO 0: Sync previo → tabla_retroactivos
        # previo ya tiene los acumulados correctos (calculados desde monitor,
        # que a su vez viene de Odoo con price_total real).
        # Aquí los copiamos a tabla_retroactivos para que el resto del cálculo
        # use montos correctos en COMPRAS_TOTALES_CRUDO, COMPRA_GLOBAL_SCOTT,
        # COMPRA_GLOBAL_APPAREL y COMPRA_GLOBAL_BOLD.
        # ══════════════════════════════════════════════════════════════════════
        cursor.execute("""
            UPDATE tabla_retroactivos tr
            JOIN previo p ON UPPER(TRIM(tr.CLAVE)) = UPPER(TRIM(p.clave))
            SET
                tr.COMPRAS_TOTALES_CRUDO = COALESCE(p.acumulado_anticipado, 0),
                tr.COMPRA_GLOBAL_SCOTT   = COALESCE(p.avance_global_scott, 0),
                tr.COMPRA_GLOBAL_APPAREL = COALESCE(p.avance_global_apparel_syncros_vittoria, 0),
                tr.COMPRA_GLOBAL_BOLD    = COALESCE(p.acumulado_bold, 0)
            WHERE tr.CLAVE NOT LIKE 'Integral%%'
              AND COALESCE(p.es_integral, 0) = 0
        """)

        # Integrales: suma de sus claves hijas (ya actualizadas arriba)
        _integrales_sync = {
            'Integral 1': ['EC216', 'JC539'],
            'Integral 2': ['GC411', 'MC679', 'MC677', 'LC657'],
            'Integral 3': ['LC625', 'LC627', 'LC626'],
        }
        for clave_int, hijas in _integrales_sync.items():
            fmt = ','.join(['%s'] * len(hijas))
            cursor.execute(f"""
                UPDATE tabla_retroactivos tr_i
                JOIN (
                    SELECT
                        COALESCE(SUM(COMPRAS_TOTALES_CRUDO), 0) AS s_crudo,
                        COALESCE(SUM(COMPRA_GLOBAL_SCOTT),   0) AS s_scott,
                        COALESCE(SUM(COMPRA_GLOBAL_APPAREL), 0) AS s_apparel,
                        COALESCE(SUM(COMPRA_GLOBAL_BOLD),    0) AS s_bold
                    FROM tabla_retroactivos
                    WHERE CLAVE IN ({fmt})
                ) sub
                SET
                    tr_i.COMPRAS_TOTALES_CRUDO = sub.s_crudo,
                    tr_i.COMPRA_GLOBAL_SCOTT   = sub.s_scott,
                    tr_i.COMPRA_GLOBAL_APPAREL = sub.s_apparel,
                    tr_i.COMPRA_GLOBAL_BOLD    = sub.s_bold
                WHERE tr_i.CLAVE = %s
            """, tuple(hijas) + (clave_int,))

        print("[OK] Acumulados previo -> tabla_retroactivos sincronizados.")

        cursor_dict.execute("""
            SELECT
                tr.CLAVE,
                tr.CATEGORIA,
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
        nivel_por_clave = {}

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
                'fin': min(fin, FECHA_CORTE)
            }
            nivel_por_clave[clave] = str(row.get('CATEGORIA') or '')

        datos_por_clave = obtener_deducciones_odoo(claves_db, fechas_por_clave)

        if 'LC657' in datos_por_clave:
            print("DEBUG SINCRONIZAR LC657 datos_por_clave:", datos_por_clave['LC657'])
        else:
            print("DEBUG SINCRONIZAR LC657 NO EXISTE EN datos_por_clave")

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
            cursor.execute("""
                UPDATE tabla_retroactivos 
                SET 
                    notas_credito = %s,
                    garantias = %s,
                    productos_ofertados = %s,
                    bicicleta_demo = %s,
                    bicicletas_bold = %s
                WHERE UPPER(TRIM(CLAVE)) = UPPER(TRIM(%s))
            """, (
                float(valores.get('nc') or 0),
                float(valores.get('garantia') or 0),
                float(valores.get('ofertado') or 0),
                float(valores.get('demo') or 0),
                float(valores.get('bold') or 0),
                clave
            ))

            if clave == 'LC657':
                print("DEBUG UPDATE LC657 rowcount:", cursor.rowcount)
                print("DEBUG UPDATE LC657 productos_ofertados:", float(valores.get('ofertado') or 0))

        # ==========================================================================
        # PRODUCTOS OFERTADOS: METODOLOGÍA POR EXCEL (hojas CAMPAÑAS y PO)
        # Fuente de verdad: snapshot de etiquetas del Excel de referencia.
        # Monto almacenado: venta_total CON IVA (consistente con COMPRAS_TOTALES_CRUDO).
        # ==========================================================================
        try:
            _refs_exactos, _tmpl_por_num, _nombre_list = cargar_campanas_desde_excel()
        except Exception as _e:
            print(f"[ERROR] No se pudo cargar campañas desde Excel: {_e}")
            _refs_exactos, _tmpl_por_num, _nombre_list = {}, {}, []

        # Gate de etiquetas Odoo: solo el paso 3 (name-match) requiere confirmación.
        # Si el producto no tiene etiqueta de campaña en Odoo, no se contabiliza
        # aunque su nombre coincida con CAMPAÑAS (evita falsos positivos).
        _refs_con_tag_odoo = None
        try:
            _r_odoo = get_odoo_models()
            if _r_odoo and _r_odoo[0]:
                _refs_por_etiqueta_odoo = cargar_refs_por_etiqueta(_r_odoo[1], _r_odoo[0])
                _refs_con_tag_odoo = set(_refs_por_etiqueta_odoo.keys())
                print(f"[OK] Gate Odoo name-match: {len(_refs_con_tag_odoo)} refs con etiqueta.")
        except Exception as _e_odoo:
            print(f"[AVISO] Gate Odoo no disponible, name-match sin restricción: {_e_odoo}")

        totales_etiquetas = calcular_productos_ofertados_etiquetas(
            cursor_dict,
            fechas_por_clave,
            _refs_exactos,
            _tmpl_por_num,
            _nombre_list,
            _refs_con_tag_odoo,
        )
        for clave_et, monto_et in totales_etiquetas.items():
            cursor.execute("""
                UPDATE tabla_retroactivos
                SET productos_ofertados = %s
                WHERE UPPER(TRIM(CLAVE)) = UPPER(TRIM(%s))
            """, (float(monto_et), clave_et))
        print(f"[OK] Productos ofertados (Excel): {len(totales_etiquetas)} clientes actualizados.")

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
        # importe_final es columna GENERADA en MySQL:
        # CRUDO − notas_credito − garantias − productos_ofertados − bicicleta_demo − bicicletas_bold
        # Se recalcula automáticamente; no se puede ni se necesita hacer UPDATE.

        # ==========================================================================
        # CÁLCULOS BASE
        # ==========================================================================
        cursor.execute("""
            UPDATE tabla_retroactivos
            SET
                TOTAL_ACUMULADO = COALESCE(COMPRAS_TOTALES_CRUDO, 0),

                compra_anual_crudo = (
                    COALESCE(COMPRAS_TOTALES_CRUDO, 0) -
                    COALESCE(notas_credito, 0) -
                    COALESCE(garantias, 0)
                ),

                compra_adicional = (
                    (
                        COALESCE(COMPRAS_TOTALES_CRUDO, 0) -
                        COALESCE(notas_credito, 0) -
                        COALESCE(garantias, 0)
                    ) - COALESCE(COMPRA_MINIMA_ANUAL, 0)
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
        print("[OK] Sincronizacion y calculos terminados correctamente.")

        cursor_dict.execute("""
            SELECT 
                CLAVE,
                CLIENTE,
                productos_ofertados,
                notas_credito,
                garantias
            FROM tabla_retroactivos
            WHERE UPPER(TRIM(CLAVE)) = 'LC657'
            LIMIT 1
        """)

        debug_lc657_db = cursor_dict.fetchone()
        print("DEBUG DB LC657 DESPUÉS DE COMMIT:", debug_lc657_db)

    except Exception as e:
        if conexion:
            conexion.rollback()

        print(f"[ERROR auto-sync] {e}")
        traceback.print_exc()

        raise

    finally:
        if cursor_dict:
            cursor_dict.close()

        if cursor:
            cursor.close()

        if conexion:
            conexion.close()


# ==============================================================================
# 3. ENDPOINT CLAVES (autocomplete sin filtro de categoría)
# ==============================================================================
@retroactivos_bp.route('/retroactivos/claves', methods=['GET'])
def obtener_claves_retroactivos():
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT CLAVE, CLIENTE FROM tabla_retroactivos
            WHERE CLAVE IS NOT NULL
            ORDER BY CLIENTE
        """)
        return jsonify(cursor.fetchall()), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conexion.close()


# 3B. ENDPOINT GET GLOBAL
# ==============================================================================
@retroactivos_bp.route('/retroactivos', methods=['GET'])
def obtener_retroactivos():

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
                (
                    fila.get('COMPRA_GLOBAL_SCOTT', 0) +
                    fila.get('COMPRA_GLOBAL_APPAREL', 0) +
                    fila.get('COMPRA_GLOBAL_BOLD', 0)
                ) -
                fila.get('notas_credito', 0) -
                fila.get('garantias', 0)
            )

        return jsonify(resultados), 200

    except Exception as e:
        print("[ERROR] Error al obtener retroactivos:", str(e))
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
    try:
        ejecutar_sincronizacion_y_calculos()

        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)

        cursor.execute("""
            SELECT 
                CLAVE,
                CLIENTE,
                productos_ofertados,
                notas_credito,
                garantias,
                importe_final
            FROM tabla_retroactivos
            WHERE UPPER(TRIM(CLAVE)) = 'LC657'
            LIMIT 1
        """)

        debug_lc657 = cursor.fetchone()

        cursor.close()
        conexion.close()

        return jsonify({
            "success": True,
            "mensaje": "Sincronización ejecutada correctamente.",
            "debug_lc657": serializar_fila(debug_lc657) if debug_lc657 else None
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

# ==============================================================================
# 5. ENDPOINT CERRAR TEMPORADA (admin only, irreversible)
# ==============================================================================
@retroactivos_bp.route('/cerrar-temporada', methods=['POST'])
def cerrar_temporada():
    auth_header = request.headers.get('Authorization', '')
    raw_token = auth_header.split(' ')[1] if ' ' in auth_header else None
    if not raw_token:
        return jsonify({"error": "No autorizado"}), 401

    payload = verificar_token(raw_token)
    rol = payload.get('rol') if payload else None
    try:
        es_admin = int(rol) == 1
    except (TypeError, ValueError):
        es_admin = False
    if not es_admin:
        return jsonify({"error": "Solo administradores pueden cerrar la temporada"}), 403

    data = request.get_json() or {}
    clave = (data.get('clave') or '').strip().upper()
    fecha_cierre = (data.get('fecha_cierre') or '').strip()

    if not clave:
        return jsonify({"error": "Se requiere la clave del distribuidor"}), 400
    if not fecha_cierre:
        return jsonify({"error": "Se requiere la fecha de cierre"}), 400

    try:
        datetime.strptime(fecha_cierre, '%Y-%m-%d')
    except ValueError:
        return jsonify({"error": "Formato de fecha inválido. Use YYYY-MM-DD"}), 400

    conexion = obtener_conexion()
    cursor     = conexion.cursor()
    cursor_dict = conexion.cursor(dictionary=True)

    try:
        cursor_dict.execute("""
            SELECT clave, f_inicio, temporada_cerrada
            FROM clientes
            WHERE UPPER(TRIM(clave)) = %s
            LIMIT 1
        """, (clave,))
        cliente = cursor_dict.fetchone()

        if not cliente:
            return jsonify({"error": f"No se encontró el distribuidor {clave}"}), 404

        if cliente.get('temporada_cerrada'):
            return jsonify({"error": f"La temporada de {clave} ya fue cerrada previamente"}), 409

        f_inicio = cliente.get('f_inicio')
        if hasattr(f_inicio, 'strftime'):
            f_inicio = f_inicio.strftime('%Y-%m-%d')
        f_inicio = f_inicio or '2025-07-01'

        # Recalcular previo con tope en fecha_cierre ANTES de marcar como cerrado
        _recalcular_previo_clave_cierre(conexion, cursor_dict, cursor, clave, f_inicio, fecha_cierre)

        # Actualizar f_fin y marcar como cerrado
        cursor.execute("""
            UPDATE clientes
            SET temporada_cerrada      = 1,
                fecha_cierre_temporada = %s,
                f_fin                  = %s
            WHERE UPPER(TRIM(clave)) = %s
        """, (fecha_cierre, fecha_cierre, clave))

        conexion.commit()
        print(f"[CIERRE] Temporada cerrada para {clave} al {fecha_cierre}")

    except Exception as e:
        conexion.rollback()
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        cursor_dict.close()
        cursor.close()
        conexion.close()

    # Recalcular tabla_retroactivos con los nuevos topes ya aplicados
    try:
        ejecutar_sincronizacion_y_calculos()
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": f"Cierre marcado pero falló el recálculo: {str(e)}"
        }), 500

    return jsonify({
        "success": True,
        "mensaje": f"Temporada cerrada para {clave} al {fecha_cierre}",
        "clave": clave,
        "fecha_cierre": fecha_cierre
    }), 200


# ==============================================================================
# 6. ENDPOINT GET INDIVIDUAL
# ==============================================================================
@retroactivos_bp.route('/retroactivo_cliente/<string:identificador>', methods=['GET'])
def obtener_retroactivo_individual(identificador):

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        query = """
            SELECT
                tr.CLAVE, tr.ZONA, tr.CLIENTE, tr.CATEGORIA,
                tr.COMPRA_MINIMA_ANUAL, tr.COMPRA_GLOBAL_SCOTT,
                tr.COMPRA_MINIMA_APPAREL, tr.COMPRA_GLOBAL_APPAREL,
                tr.COMPRAS_TOTALES_CRUDO, tr.notas_credito, tr.garantias,
                tr.productos_ofertados, tr.bicicleta_demo, tr.bicicletas_bold,
                tr.importe_final, tr.porcentaje_retroactivo, tr.porcentaje_retroactivo_apparel,
                tr.compra_adicional, tr.retroactivo_total, tr.importe, tr.estatus, tr.NC, tr.FACT,
                COALESCE(c.temporada_cerrada, 0)   AS temporada_cerrada,
                c.fecha_cierre_temporada
            FROM tabla_retroactivos tr
            LEFT JOIN clientes c ON UPPER(TRIM(tr.CLAVE)) = UPPER(TRIM(c.clave))
            WHERE tr.CLAVE = %s OR tr.CLIENTE LIKE %s
            LIMIT 1
        """

        cursor.execute(query, (identificador, f'%{identificador}%'))
        cliente_data = cursor.fetchone()

        if not cliente_data:
            return jsonify({"mensaje": "Cliente no encontrado"}), 404

        # Guardar campos de cierre antes de la serialización genérica
        _tc  = cliente_data.get('temporada_cerrada')
        _fct = cliente_data.get('fecha_cierre_temporada')

        for clave, valor in cliente_data.items():
            cliente_data[clave] = convertir_decimal_y_fecha(valor)

            if cliente_data[clave] is None:
                if clave in ['CLAVE', 'ZONA', 'CLIENTE', 'CATEGORIA', 'estatus', 'NC', 'FACT',
                             'fecha_cierre_temporada']:
                    cliente_data[clave] = ''
                else:
                    cliente_data[clave] = 0.0

        # Restaurar campos de cierre con tipos correctos
        cliente_data['temporada_cerrada'] = bool(_tc)
        if _fct:
            cliente_data['fecha_cierre_temporada'] = (
                _fct.strftime('%Y-%m-%d') if hasattr(_fct, 'strftime') else str(_fct)
            )
        else:
            cliente_data['fecha_cierre_temporada'] = None

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
            (
                cliente_data.get('COMPRA_GLOBAL_SCOTT', 0) +
                cliente_data.get('COMPRA_GLOBAL_APPAREL', 0) +
                cliente_data.get('COMPRA_GLOBAL_BOLD', 0)
            ) -
            cliente_data.get('notas_credito', 0) -
            cliente_data.get('garantias', 0)
        )

        return jsonify(cliente_data), 200

    except Exception as e:
        print("[ERROR] Error al obtener cliente:", str(e))
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
                "fin": min(f_fin or fecha_fin, FECHA_CORTE)
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

@retroactivos_bp.route('/debug_nc_garantias', methods=['GET'])
def debug_nc_garantias():
    """
    Debug de notas de crédito y garantías por cliente.
    NO actualiza BD. Solo consulta Odoo y devuelve totales + detalle.
    Uso:
      /debug_nc_garantias
      /debug_nc_garantias?clave=GD380
      /debug_nc_garantias?inicio=2025-05-01&fin=2026-06-30
    """
    resultado_odoo = get_odoo_models()
    uid = resultado_odoo[0]
    models = resultado_odoo[1]

    if not uid:
        return jsonify({"error": "No se pudo conectar a Odoo"}), 500

    conexion = None
    cursor = None

    try:
        fecha_inicio = request.args.get('inicio', FECHA_MINIMA_RETROACTIVOS)
        fecha_fin = request.args.get('fin', '2026-06-30')
        clave_filtro = (request.args.get('clave', '') or '').strip().upper()

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

        claves_db = []
        fechas_por_clave = {}
        cliente_por_clave = {}

        for c in clientes_db:
            clave = str(c.get('clave') or '').strip().upper()

            if not clave:
                continue

            if clave_filtro and clave != clave_filtro:
                continue

            f_inicio = c.get('f_inicio')
            f_fin = c.get('f_fin')

            if isinstance(f_inicio, (date, datetime)):
                f_inicio = f_inicio.strftime('%Y-%m-%d')

            if isinstance(f_fin, (date, datetime)):
                f_fin = f_fin.strftime('%Y-%m-%d')

            claves_db.append(clave)

            fechas_por_clave[clave] = {
                'inicio': f_inicio or fecha_inicio,
                'fin': min(f_fin or fecha_fin, FECHA_CORTE)
            }

            cliente_por_clave[clave] = c.get('nombre_cliente') or ''

        if not claves_db:
            return jsonify({
                "mensaje": "No se encontraron clientes para el filtro.",
                "clave_filtro": clave_filtro
            }), 200

        # ==========================================================
        # MAPEAR PARTNERS ODOO -> CLAVE
        # ==========================================================
        partners_odoo = models.execute_kw(
            ODOO_DB,
            uid,
            ODOO_PASSWORD,
            'res.partner',
            'search_read',
            [[]],
            {
                'fields': ['id', 'name', 'ref', 'parent_id'],
                'limit': 10000
            }
        )

        odoo_id_to_clave = {}

        for p in partners_odoo:
            ref_odoo = str(p.get('ref') or '').strip().upper()
            name_odoo = str(p.get('name') or '').strip().upper()

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
            name_odoo = str(p.get('name') or '').strip().upper()

            if p['id'] not in odoo_id_to_clave and name_odoo in redirecciones:
                clave_redirect = redirecciones[name_odoo]

                if clave_redirect in claves_db:
                    odoo_id_to_clave[p['id']] = clave_redirect

        # Mapear contactos hijos al padre
        for p in partners_odoo:
            if p['id'] not in odoo_id_to_clave and p.get('parent_id'):
                parent_id = p['parent_id'][0] if isinstance(p['parent_id'], (list, tuple)) else p['parent_id']

                if parent_id in odoo_id_to_clave:
                    odoo_id_to_clave[p['id']] = odoo_id_to_clave[parent_id]

        lista_ids_validos = list(odoo_id_to_clave.keys())

        if not lista_ids_validos:
            return jsonify({
                "mensaje": "No se encontraron partners Odoo relacionados a las claves.",
                "claves": claves_db
            }), 200

        resumen = {}

        for clave in claves_db:
            resumen[clave] = {
                "clave": clave,
                "cliente": cliente_por_clave.get(clave, ''),
                "notas_credito": 0.0,
                "garantias": 0.0,
                "total_deducciones": 0.0,
                "detalle_notas_credito": [],
                "detalle_garantias": []
            }

        def fecha_valida_para_clave(clave, fecha_linea):
            if not fecha_linea:
                return False

            fecha_linea = str(fecha_linea)[:10]

            if fecha_linea < FECHA_MINIMA_RETROACTIVOS:
                return False

            rango = fechas_por_clave.get(clave)

            if rango and rango.get('inicio') and rango.get('fin'):
                inicio_real = max(rango['inicio'], FECHA_MINIMA_RETROACTIVOS)

                if not (inicio_real <= fecha_linea <= rango['fin']):
                    return False

            if not (fecha_inicio <= fecha_linea <= fecha_fin):
                return False

            return True

        # ==========================================================
        # GARANTÍAS
        # ==========================================================
        ids_cuenta_garantia = models.execute_kw(
            ODOO_DB,
            uid,
            ODOO_PASSWORD,
            'account.account',
            'search',
            [[('code', '=', '402.01.05')]]
        )

        lineas_garantia_raw = models.execute_kw(
            ODOO_DB,
            uid,
            ODOO_PASSWORD,
            'account.move.line',
            'search_read',
            [[
                ('move_id.move_type', '=', 'out_refund'),
                ('move_id.state', '=', 'posted'),
                ('move_id.invoice_date', '>=', fecha_inicio),
                ('move_id.invoice_date', '<=', fecha_fin),
                ('partner_id', 'in', lista_ids_validos),
                ('account_id', 'in', ids_cuenta_garantia)
            ]],
            {
                'fields': ['move_id', 'partner_id', 'date', 'account_id', 'name'],
                'limit': 10000
            }
        )

        move_ids_garantia = set()
        move_meta_garantia = {}

        for linea in lineas_garantia_raw:
            move = linea.get('move_id')
            partner = linea.get('partner_id')

            if not move or not partner:
                continue

            move_id = move[0]
            partner_id = partner[0]
            clave = odoo_id_to_clave.get(partner_id)
            fecha = str(linea.get('date') or '')[:10]

            if not clave or clave not in resumen:
                continue

            if not fecha_valida_para_clave(clave, fecha):
                continue

            move_ids_garantia.add(move_id)
            move_meta_garantia[move_id] = {
                "partner_id": partner_id,
                "partner_nombre": partner[1],
                "clave": clave,
                "fecha": fecha,
                "linea": linea.get('name'),
                "cuenta": linea.get('account_id')
            }

        if move_ids_garantia:
            facturas_garantia = models.execute_kw(
                ODOO_DB,
                uid,
                ODOO_PASSWORD,
                'account.move',
                'read',
                [list(move_ids_garantia)],
                {
                    'fields': ['id', 'name', 'ref', 'amount_total', 'invoice_date']
                }
            )

            for factura in facturas_garantia:
                meta = move_meta_garantia.get(factura['id'])

                if not meta:
                    continue

                clave = meta['clave']
                monto = abs(float(factura.get('amount_total') or 0))

                resumen[clave]["garantias"] += monto
                resumen[clave]["detalle_garantias"].append({
                    "move_id": factura.get('id'),
                    "numero": factura.get('name'),
                    "referencia": factura.get('ref'),
                    "fecha": factura.get('invoice_date') or meta.get('fecha'),
                    "partner_id": meta.get('partner_id'),
                    "partner_nombre": meta.get('partner_nombre'),
                    "cuenta": meta.get('cuenta'),
                    "linea": meta.get('linea'),
                    "monto": round(monto, 2)
                })

        # ==========================================================
        # NOTAS DE CRÉDITO
        # ==========================================================
        todas_nc_moves = models.execute_kw(
            ODOO_DB,
            uid,
            ODOO_PASSWORD,
            'account.move',
            'search_read',
            [[
                ('move_type', '=', 'out_refund'),
                ('state', '=', 'posted'),
                ('invoice_date', '>=', fecha_inicio),
                ('invoice_date', '<=', fecha_fin),
                ('partner_id', 'in', lista_ids_validos)
            ]],
            {
                'fields': ['id', 'partner_id', 'amount_total', 'invoice_date', 'name', 'ref'],
                'limit': 10000
            }
        )

        for nc in todas_nc_moves:
            if nc['id'] in move_ids_garantia:
                continue

            partner = nc.get('partner_id')

            if not partner:
                continue

            partner_id = partner[0]
            clave = odoo_id_to_clave.get(partner_id)

            if not clave or clave not in resumen:
                continue

            fecha_nc = str(nc.get('invoice_date') or '')[:10]

            if not fecha_valida_para_clave(clave, fecha_nc):
                continue

            ref_texto = (str(nc.get('name') or '') + ' ' + str(nc.get('ref') or '')).upper()

            if 'APLANT' in ref_texto or 'ANTICIPO' in ref_texto:
                continue

            monto = abs(float(nc.get('amount_total') or 0))

            resumen[clave]["notas_credito"] += monto
            resumen[clave]["detalle_notas_credito"].append({
                "move_id": nc.get('id'),
                "numero": nc.get('name'),
                "referencia": nc.get('ref'),
                "fecha": nc.get('invoice_date'),
                "partner_id": partner_id,
                "partner_nombre": partner[1],
                "monto": round(monto, 2)
            })

        respuesta = []

        for item in resumen.values():
            item["notas_credito"] = round(item["notas_credito"], 2)
            item["garantias"] = round(item["garantias"], 2)
            item["total_deducciones"] = round(item["notas_credito"] + item["garantias"], 2)
            respuesta.append(item)

        respuesta = sorted(
            respuesta,
            key=lambda x: x["total_deducciones"],
            reverse=True
        )

        return jsonify({
            "periodo": {
                "inicio": fecha_inicio,
                "fin": fecha_fin,
                "clave": clave_filtro or None
            },
            "resumen": respuesta,
            "totales_generales": {
                "notas_credito": round(sum(x["notas_credito"] for x in respuesta), 2),
                "garantias": round(sum(x["garantias"] for x in respuesta), 2),
                "total_deducciones": round(sum(x["total_deducciones"] for x in respuesta), 2)
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

@retroactivos_bp.route('/historial_facturas_productos_odoo', methods=['GET'])
def historial_facturas_productos_odoo():
    """
    Historial de productos vendidos en facturas de cliente.
    Replica la ruta de Odoo:
    Contabilidad → Apuntes contables

    Filtros:
    - Publicado
    - Tipo = Factura de cliente
    - Cuenta = 401.01.01
    - Producto establecido
    - Fecha de factura entre inicio y fin

    Uso:
      /historial_facturas_productos_odoo
      /historial_facturas_productos_odoo?inicio=2025-06-01&fin=2026-06-30
      /historial_facturas_productos_odoo?inicio=2025-06-01&fin=2026-06-30&clave=GD380
    """
    resultado_odoo = get_odoo_models()
    uid = resultado_odoo[0]
    models = resultado_odoo[1]

    if not uid:
        return jsonify({"error": "No se pudo conectar a Odoo"}), 500

    try:
        fecha_inicio = request.args.get('inicio', '2025-06-01')
        fecha_fin = request.args.get('fin', '2026-06-30')
        clave = (request.args.get('clave', '') or '').strip().upper()

        domain = [
            ('move_id.move_type', '=', 'out_invoice'),
            ('move_id.state', '=', 'posted'),
            ('move_id.invoice_date', '>=', fecha_inicio),
            ('move_id.invoice_date', '<=', fecha_fin),
            ('account_id.code', '=', '401.01.01'),
            ('product_id', '!=', False) 
        ]

        if clave:
            domain += [
                '|',
                    ('partner_id.ref', 'ilike', clave),
                    ('partner_id.name', 'ilike', clave)
            ]

        lineas = fetch_all_odoo(
            models,
            uid,
            'account.move.line',
            domain,
            [
                'id',
                'date',
                'move_id',
                'partner_id',
                'account_id',
                'name',
                'product_id',
                'quantity',
                'price_unit',
                'price_subtotal',
                'price_total'
            ],
            order='date asc'
        )

        product_ids = []

        for linea in lineas:
            product_id = obtener_id_m2o(linea.get('product_id'))

            if product_id:
                product_ids.append(product_id)

        productos_por_id = {}

        if product_ids:
            productos = fetch_all_odoo(
                models,
                uid,
                'product.product',
                [('id', 'in', list(set(product_ids)))],
                [
                    'id',
                    'name',
                    'display_name',
                    'default_code',
                    'categ_id',
                    'product_tmpl_id'
                ],
                batch_size=500
            )

            productos_por_id = {
                p['id']: p
                for p in productos
            }

        resultado = []

        total_sin_iva = 0.0
        total_con_iva = 0.0

        for linea in lineas:
            factura = linea.get('move_id') or []
            cliente = linea.get('partner_id') or []
            producto = linea.get('product_id') or []
            cuenta = linea.get('account_id') or []

            product_id = obtener_id_m2o(producto)
            producto_info = productos_por_id.get(product_id, {})

            cantidad = float(linea.get('quantity') or 0)
            precio_unitario_sin_iva = float(linea.get('price_unit') or 0)
            subtotal_sin_iva = float(linea.get('price_subtotal') or 0)
            total_linea_con_iva = float(linea.get('price_total') or 0)

            precio_unitario_con_iva = (
                total_linea_con_iva / cantidad
            ) if cantidad else 0.0

            total_sin_iva += subtotal_sin_iva
            total_con_iva += total_linea_con_iva

            resultado.append({
                "id_linea_odoo": linea.get('id'),

                "numero_factura": factura[1] if len(factura) > 1 else "",
                "fecha_factura": linea.get('date'),

                "cliente": cliente[1] if len(cliente) > 1 else "",
                "cliente_odoo_id": cliente[0] if cliente else None,

                "producto": producto[1] if len(producto) > 1 else linea.get('name'),
                "producto_odoo_id": product_id,
                "referencia_interna": producto_info.get('default_code') or "",
                "categoria_producto": obtener_nombre_m2o(producto_info.get('categ_id')),

                "cantidad": cantidad,

                "precio_unitario_sin_iva": round(precio_unitario_sin_iva, 2),
                "precio_unitario_con_iva": round(precio_unitario_con_iva, 2),

                "subtotal_sin_iva": round(subtotal_sin_iva, 2),
                "total_con_iva": round(total_linea_con_iva, 2),

                "cuenta": cuenta[1] if len(cuenta) > 1 else "",
                "etiqueta": linea.get('name') or ""
            })

        return jsonify({
            "periodo": {
                "inicio": fecha_inicio,
                "fin": fecha_fin,
                "clave": clave or None
            },
            "filtros_replicados_odoo": {
                "ruta": "Contabilidad → Apuntes contables",
                "estado": "Publicado",
                "tipo": "Factura de cliente",
                "cuenta": "401.01.01 Ventas y/o servicios gravados a la tasa general",
                "producto": "Producto establecido"
            },
            "registros": len(resultado),
            "total_sin_iva": round(total_sin_iva, 2),
            "total_con_iva": round(total_con_iva, 2),
            "data": resultado
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@retroactivos_bp.route('/debug_productos_ofertados_campanas', methods=['GET'])
def debug_productos_ofertados_campanas():
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
        clave_filtro = (request.args.get('clave', '') or '').strip().upper()
        respetar_fechas_cliente = request.args.get('respetar_fechas_cliente', '1') == '1'

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
        claves_db = []
        fechas_por_clave = {}

        for c in clientes_db:
            clave = str(c.get('clave') or '').strip().upper()

            if not clave:
                continue

            if clave_filtro and clave != clave_filtro:
                continue

            claves_db.append(clave)

            f_inicio = c.get('f_inicio')
            f_fin = c.get('f_fin')

            if isinstance(f_inicio, (date, datetime)):
                f_inicio = f_inicio.strftime('%Y-%m-%d')

            if isinstance(f_fin, (date, datetime)):
                f_fin = f_fin.strftime('%Y-%m-%d')

            fechas_por_clave[clave] = {
                'inicio': f_inicio or fecha_inicio,
                'fin': f_fin or fecha_fin
            }

        partners_odoo = models.execute_kw(
            ODOO_DB,
            uid,
            ODOO_PASSWORD,
            'res.partner',
            'search_read',
            [[]],
            {'fields': ['id', 'name', 'ref', 'parent_id'], 'limit': 20000}
        )

        odoo_id_to_clave = {}

        for p in partners_odoo:
            ref_odoo = str(p.get('ref') or '').strip().upper()
            name_odoo = str(p.get('name') or '').strip().upper()

            for clave in claves_db:
                if ref_odoo == clave or ref_odoo == f"{clave}-CA" or clave in name_odoo:
                    odoo_id_to_clave[p['id']] = clave
                    break

        for p in partners_odoo:
            if p['id'] not in odoo_id_to_clave and p.get('parent_id'):
                parent_id = p['parent_id'][0] if isinstance(p['parent_id'], (list, tuple)) else p['parent_id']

                if parent_id in odoo_id_to_clave:
                    odoo_id_to_clave[p['id']] = odoo_id_to_clave[parent_id]

        lista_ids_validos = list(odoo_id_to_clave.keys())

        if not lista_ids_validos:
            return jsonify({
                "mensaje": "No se encontraron partners Odoo relacionados.",
                "clave": clave_filtro or None
            }), 200

        if not respetar_fechas_cliente:
            for clave in fechas_por_clave:
                fechas_por_clave[clave] = {
                    'inicio': fecha_inicio,
                    'fin': fecha_fin
                }

        totales, detalle = calcular_productos_ofertados_por_campanas(
            models,
            uid,
            lista_ids_validos,
            fecha_inicio,
            fecha_fin,
            odoo_id_to_clave,
            fechas_por_clave
        )

        resumen = []

        for clave, total in totales.items():
            resumen.append({
                "clave": clave,
                "total_productos_ofertados": round(float(total or 0), 2),
                "registros": len([d for d in detalle if d.get('clave') == clave])
            })

        resumen = sorted(resumen, key=lambda x: x['total_productos_ofertados'], reverse=True)

        return jsonify({
            "periodo": {
                "inicio": fecha_inicio,
                "fin": fecha_fin,
                "clave": clave_filtro or None,
                "respetar_fechas_cliente": respetar_fechas_cliente
            },
            "fuente_odoo": {
                "modelo": "account.move.line",
                "ruta": "Contabilidad -> Apuntes contables",
                "filtros": [
                    "Publicado",
                    "Asiento contable / Tipo = Factura de cliente",
                    "Cuenta = 401.01.01",
                    "Producto establecido",
                    "Fecha de factura en rango"
                ],
                "monto": "price_total / Total con IVA"
            },
            "campanas": [
                {
                    "nombre": c['nombre'],
                    "inicio": c['inicio'],
                    "fin": c['fin'],
                    "referencias": len(c.get('referencias', []))
                }
                for c in CAMPANAS_PRODUCTOS_OFERTADOS
            ],
            "total_general": round(sum(float(v or 0) for v in totales.values()), 2),
            "resumen_por_cliente": resumen,
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

