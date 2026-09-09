"""
Servicio compartido de Proyecciones.
Centraliza la prioridad de clientes, la normalización de SKU y la deducción
de órdenes Odoo ya confirmadas, para que routes/proyecciones_my27.py y
routes/asignaciones_importaciones.py usen la misma fuente sin duplicar lógica.
"""
import logging
import re
import time

from db_conexion import obtener_conexion
from utils.odoo_utils import get_odoo_models, ODOO_DB, ODOO_PASSWORD

# Lista de prioridad para distribución de inventario.
# Los clientes no en esta lista reciben stock después de la prioridad 27.
PRIORIDAD_CLIENTES = [
    (1,  'LC657', 'Víctor Hugo Villanueva Guzman'),
    (2,  'MC677', 'BICICLETAS SCJM'),
    (3,  'MC679', 'Adventure Bike Rider S. A. DE C. V. (GPE)'),
    (4,  'GC411', 'Adventure Bike Rider S. A. DE C. V.'),
    (5,  'HE420', 'Xavier James Lord Santos'),
    (6,  'EC216', 'Marco Tulio (Morelia)'),
    (7,  'JC539', 'Marco Tulio Andrade Navarro (León)'),
    (8,  'MD670', 'LIVING FOR BIKES'),
    (9,  'GD380', 'Cycling Riding de Mexico SA de CV (Metepec)'),
    (10, 'HA433', 'Lucia Salazar Lopez'),
    (11, 'ID506', 'Angelica Osorio Gasperin'),
    (12, '4E013', 'CHRISTIAN BOCCALETTI.'),
    (13, 'JE537', 'Christian Boccaletti.'),
    (14, 'LC625', 'Naruco S. A. de C. V. Arcos'),
    (15, 'LC626', 'Naruco S. A. de C. V. SJR'),
    (16, 'LC627', 'Naruco S. A. de C. V. (Jurica)'),
    (17, '84920', 'Naruco Corregidora'),
    (18, 'MD697', 'Fernando Pontón Rocha'),
    (19, 'EA219', 'Victor Alejandro Garnier Morga'),
    (20, 'HF427', 'Opciones Creativas SA de CV'),
    (21, 'FA271', 'Juan Manuel Ruacho Rangel'),
    (22, 'AG873', 'Alta Gama 87'),
    (23, 'LD664', 'Bikes 95 Cycling Club S. A. De C. V.'),
    (24, '5GEG6', 'FELIPE ENRIQUEZ ROJAS'),
    (25, 'IA500', 'Jesus Manuel Medrano Velarde'),
    (26, 'DC192', 'ANA CECILIA LOPEZ LOPEZ'),
    (27, 'JC554', 'ZIRANDA MADRIGAL EUGENA'),
]
# Lookup rápido: clave_cliente → (prioridad, nombre_canonical)
_PRIORIDAD_MAP: dict = {clave.strip().upper(): (prio, nombre)
                        for prio, clave, nombre in PRIORIDAD_CLIENTES}

_ORDENES_CACHE: dict = {'data': {}, 'periodo': '', 'ts': 0.0}
_ORDENES_TTL = 180  # 3 minutos


def _norm_sku(s: str) -> str:
    return re.sub(r'[\-\s]', '', str(s or '')).upper()


def obtener_prioridad_clientes() -> list:
    """[{clave, nombre, prioridad}] ordenado por prioridad — para GET /clientes/prioridad."""
    return [
        {"clave": clave, "nombre": nombre, "prioridad": prioridad}
        for prioridad, clave, nombre in PRIORIDAD_CLIENTES
    ]


def _get_ordenes_my27(periodo: str) -> dict:
    """
    Retorna {clave_cliente: {sku_norm: total_pedido}} con las unidades ya
    confirmadas en Odoo (sale.order state=sale/done) para el periodo MY27.
    Caché de 3 minutos para no saturar Odoo en cada recarga.
    """
    now = time.time()
    if (now - _ORDENES_CACHE['ts'] < _ORDENES_TTL and
            _ORDENES_CACHE['periodo'] == periodo):
        return _ORDENES_CACHE['data']

    try:
        m = re.match(r'^(\d{4})-(\d{4})$', periodo)
        if not m:
            return {}
        year1, year2 = int(m.group(1)), int(m.group(2))
        fecha_inicio = f'{year1 - 1}-07-01'
        fecha_fin    = f'{year2}-04-30'

        uid, models, err = get_odoo_models()
        if err or not uid:
            return _ORDENES_CACHE['data']

        orders = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'sale.order', 'search_read',
            [[['state', 'in', ['sale', 'done']],
              ['date_order', '>=', fecha_inicio],
              ['date_order', '<=', fecha_fin + ' 23:59:59']]],
            {'fields': ['id', 'partner_id'], 'limit': 0}
        )
        if not orders:
            _ORDENES_CACHE.update({'data': {}, 'periodo': periodo, 'ts': now})
            return {}

        partner_ids = list({o['partner_id'][0] for o in orders if o.get('partner_id')})
        partners = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'res.partner', 'search_read',
            [[['id', 'in', partner_ids]]],
            {'fields': ['id', 'ref', 'parent_id'], 'limit': 0}
        )
        ref_map: dict = {}
        sin_ref: list = []
        sin_ref_ni_padre: list = []
        for p in partners:
            ref = (p.get('ref') or '').strip()
            if ref:
                ref_map[p['id']] = ref
            elif p.get('parent_id'):
                sin_ref.append((p['id'], p['parent_id'][0]))
            else:
                sin_ref_ni_padre.append(p['id'])
        logging.info('[ordenes_my27] partners con ref: %d, hijos sin ref: %d, sin ref ni padre: %d',
                     len(ref_map), len(sin_ref), len(sin_ref_ni_padre))
        if sin_ref:
            parent_ids_lookup = list({pid for _, pid in sin_ref})
            parents = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                'res.partner', 'search_read',
                [[['id', 'in', parent_ids_lookup]]],
                {'fields': ['id', 'ref', 'name'], 'limit': 0}
            )
            parent_ref_map = {p['id']: (p.get('ref') or '').strip() for p in parents}
            logging.info('[ordenes_my27] padres encontrados: %s',
                         {p['id']: (p.get('ref'), p.get('name')) for p in parents})
            for child_id, parent_id in sin_ref:
                pref = parent_ref_map.get(parent_id, '')
                logging.info('[ordenes_my27] hijo %s → padre %s ref=%r', child_id, parent_id, pref)
                if pref:
                    ref_map[child_id] = pref
        logging.info('[ordenes_my27] ref_map final: %d claves; EC216 presente: %s',
                     len(ref_map), 'EC216' in ref_map.values())

        order_clave = {
            o['id']: ref_map[o['partner_id'][0]]
            for o in orders
            if o.get('partner_id') and ref_map.get(o['partner_id'][0])
        }
        logging.info('[ordenes_my27] ordenes con clave: %d / %d totales', len(order_clave), len(orders))

        sol = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'sale.order.line', 'search_read',
            [[['order_id', 'in', list(order_clave.keys())],
              ['state', 'not in', ['cancel']]]],
            {'fields': ['order_id', 'product_id', 'product_uom_qty'], 'limit': 0}
        )

        prod_ids = list({l['product_id'][0] for l in sol if l.get('product_id')})
        prods = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'product.product', 'search_read',
            [[['id', 'in', prod_ids]]],
            {'fields': ['id', 'default_code'], 'limit': 0}
        )
        prod_code = {p['id']: (p.get('default_code') or '').strip() for p in prods}

        result: dict = {}
        for l in sol:
            oid = l['order_id'][0] if isinstance(l.get('order_id'), list) else l.get('order_id')
            clave = order_clave.get(oid, '')
            if not clave:
                continue
            pid = l['product_id'][0] if l.get('product_id') else None
            if not pid:
                continue
            sn = _norm_sku(prod_code.get(pid, ''))
            if not sn:
                continue
            qty = int(l.get('product_uom_qty') or 0)
            if clave not in result:
                result[clave] = {}
            result[clave][sn] = result[clave].get(sn, 0) + qty

        _ORDENES_CACHE.update({'data': result, 'periodo': periodo, 'ts': now})
        logging.info('[ordenes_my27] %d clientes con pedidos en %s', len(result), periodo)
        if 'EC216' in result:
            logging.info('[ordenes_my27] EC216 pedidos: %s', result['EC216'])
        else:
            logging.info('[ordenes_my27] EC216 NO está en result — no se descontará')
        return result

    except Exception as e:
        logging.exception('[ordenes_my27] error: %s', e)
        return _ORDENES_CACHE['data']


def demanda_neta_por_cliente(periodo: str, skus_norm: list) -> dict:
    """
    {sku_norm: {clave_cliente: cantidad_neta_pendiente}} — proyección total
    del periodo por cliente, menos lo que Odoo ya tiene confirmado (sale.order).
    Lanza RuntimeError si no hay conexión a BD (el caller decide cómo degradar).
    """
    if not skus_norm:
        return {}
    skus_norm_set = set(skus_norm)

    conn = obtener_conexion()
    if not conn:
        raise RuntimeError("Sin conexión a BD para consultar forecast_proyecciones")
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT clave_cliente, sku, "
            "(mayo+junio+julio+agosto+septiembre+octubre+noviembre+diciembre"
            "+enero+febrero+marzo+abril) AS total "
            "FROM forecast_proyecciones WHERE periodo = %s",
            (periodo,),
        )
        crudo: dict = {}
        for row in cursor.fetchall():
            sku_n = _norm_sku(row["sku"])
            if sku_n not in skus_norm_set:
                continue
            crudo.setdefault(sku_n, {})
            crudo[sku_n][row["clave_cliente"]] = crudo[sku_n].get(row["clave_cliente"], 0) + (row["total"] or 0)
    finally:
        conn.close()

    try:
        ordenes = _get_ordenes_my27(periodo)
    except Exception:
        logging.exception("No se pudo deducir órdenes Odoo para %s", periodo)
        ordenes = {}

    resultado: dict = {}
    for sku_n, por_cliente in crudo.items():
        resultado[sku_n] = {}
        for clave, cantidad in por_cliente.items():
            ya_en_odoo = ordenes.get(clave, {}).get(sku_n, 0)
            neta = max(cantidad - ya_en_odoo, 0)
            if neta > 0:
                resultado[sku_n][clave] = neta
    return resultado
