from __future__ import annotations
from flask import Blueprint, jsonify, request
from utils.odoo_utils import get_odoo_models, ODOO_DB, ODOO_PASSWORD, ODOO_COMPANY_ID
from db_conexion import obtener_conexion
from datetime import date as _date, timedelta as _timedelta
import logging
import time

def _build_consolidation_map(partner_totals: dict, models, uid) -> dict:
    """
    Dado un dict {pid: {total, facturas, nombre}} de partners de Odoo, devuelve
    {pid: display_key} para TODOS los pids que deban re-mapearse:

    Prioridad (más alta primero):
      1. Grupo integral (nombre_grupo de MySQL)  → se llama igual para padre e hijo
      2. Cuenta hija con parent_id               → usa el nombre del padre
      3. Sin remap necesario                     → no aparece en el resultado

    Así "ALEXIS JASIEL CONCHA ESPIRITU" + "ALEXIS JASIEL CONCHA ESPIRITU, Alexis
    Jasiel B2B" se consolidan en una sola línea con el nombre del padre.
    """
    if not partner_totals:
        return {}
    all_pids = [pid for pid in partner_totals if pid]
    if not all_pids:
        return {}

    # ── 1. Leer ref, parent_id y name de todos los partners en una llamada ────
    try:
        partners_data = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD,
            'res.partner', 'read', [all_pids], {'fields': ['id', 'ref', 'parent_id', 'name']})
    except Exception:
        logging.warning('_build_consolidation_map: fallo llamada Odoo')
        return {}

    pid_ref:    dict = {}  # pid → clave (ref), puede ser None
    pid_parent: dict = {}  # pid → parent_pid, puede ser None
    pid_name:   dict = {}  # pid → name en Odoo
    for p in partners_data:
        ref = (p.get('ref') or '').strip().upper()
        pid_ref[p['id']]  = ref if ref else None
        parent = p.get('parent_id')
        pid_parent[p['id']] = parent[0] if parent and isinstance(parent, (list, tuple)) else None
        pid_name[p['id']]   = p.get('name') or ''

    def effective_ref(pid):
        """Ref propia o la del padre si no tiene."""
        ref = pid_ref.get(pid)
        if ref:
            return ref
        parent = pid_parent.get(pid)
        if parent:
            return pid_ref.get(parent)
        return None

    # ── 2. Consultar MySQL: claves que pertenecen a un grupo integral ──────────
    all_claves = {effective_ref(pid) for pid in all_pids if effective_ref(pid)}
    clave_to_grupo: dict = {}
    if all_claves:
        try:
            conn = obtener_conexion()
            cur  = conn.cursor(dictionary=True)
            placeholders = ','.join(['%s'] * len(all_claves))
            cur.execute(f"""
                SELECT c.clave, gc.nombre_grupo
                FROM clientes c
                JOIN grupo_clientes gc ON c.id_grupo = gc.id
                WHERE c.clave IN ({placeholders})
            """, list(all_claves))
            clave_to_grupo = {r['clave']: r['nombre_grupo'] for r in cur.fetchall()}
            cur.close()
            conn.close()
        except Exception:
            logging.warning('_build_consolidation_map: fallo MySQL, sin grupos integrales')

    # ── 3. Construir mapa final con prioridad: grupo > padre ──────────────────
    # Primero pre-calculamos la display_key de los pids "padre" (sin parent_id)
    # para que los hijos puedan heredar el nombre correcto.
    parent_display: dict = {}  # parent_pid → nombre que se usará
    for pid in all_pids:
        if pid_parent.get(pid):
            continue  # es hijo, se procesa después
        ref = pid_ref.get(pid)
        if ref and ref in clave_to_grupo:
            parent_display[pid] = clave_to_grupo[ref]
        else:
            parent_display[pid] = pid_name.get(pid, partner_totals[pid].get('nombre', 'Sin cliente'))

    result: dict = {}
    for pid in all_pids:
        ref    = effective_ref(pid)
        parent = pid_parent.get(pid)

        if ref and ref in clave_to_grupo:
            # Pertenece a grupo integral → máxima prioridad
            result[pid] = clave_to_grupo[ref]
        elif parent:
            # Cuenta hija → usar display_key del padre
            result[pid] = parent_display.get(parent, pid_name.get(parent, partner_totals[pid].get('nombre', 'Sin cliente')))
        # else: sin remap, se queda con su nombre actual → no se añade al dict

    return result



# ── Helpers para suplemento Odoo en integrales ────────────────────────────────

_monitor_start_cache = None


def _get_monitor_start():
    """Devuelve la fecha más antigua en la tabla monitor (cacheada)."""
    global _monitor_start_cache
    if _monitor_start_cache:
        return _monitor_start_cache
    try:
        conn = obtener_conexion()
        cur  = conn.cursor()
        cur.execute("SELECT MIN(fecha_factura) FROM monitor WHERE fecha_factura IS NOT NULL")
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row and row[0]:
            _monitor_start_cache = str(row[0])[:10]
            return _monitor_start_cache
    except Exception:
        pass
    return None


def _claves_para_grupo(grupo_id: int) -> list:
    """Devuelve las claves de cliente de un grupo integral."""
    try:
        conn = obtener_conexion()
        cur  = conn.cursor()
        cur.execute("SELECT clave FROM clientes WHERE id_grupo = %s AND clave IS NOT NULL", (grupo_id,))
        claves = [r[0] for r in cur.fetchall() if r[0]]
        cur.close()
        conn.close()
        return claves
    except Exception:
        return []


def _nombre_grupo(grupo_id: int) -> str:
    try:
        conn = obtener_conexion()
        cur  = conn.cursor()
        cur.execute("SELECT nombre_grupo FROM grupo_clientes WHERE id = %s", (grupo_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row[0] if row else ''
    except Exception:
        return ''


def _odoo_supplement_rows(uid, models, grupo_id: int, fi_str: str, ff_str: str) -> list:
    """
    Consulta Odoo para facturas del integral en el rango [fi_str, ff_str].
    Retorna filas con el mismo formato que el SELECT de la tabla monitor.
    """
    claves = _claves_para_grupo(grupo_id)
    if not claves:
        return []

    try:
        parents = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD,
            'res.partner', 'search_read',
            [[['ref', 'in', claves]]],
            {'fields': ['id']})
        all_pids = [p['id'] for p in parents]
        if all_pids:
            children = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD,
                'res.partner', 'search_read',
                [[['parent_id', 'in', all_pids]]],
                {'fields': ['id']})
            all_pids += [c['id'] for c in children]
    except Exception as e:
        logging.warning('_odoo_supplement_rows: error obteniendo partner IDs: %s', e)
        return []

    if not all_pids:
        return []

    nombre = _nombre_grupo(grupo_id)

    try:
        domain = [
            ['move_type', '=', 'out_invoice'],
            ['state', '=', 'posted'],
            PAYMENT_FILTER,
            ['invoice_date', '>=', fi_str],
            ['invoice_date', '<=', ff_str],
            ['company_id', '=', ODOO_COMPANY_ID],
            ['partner_id', 'in', all_pids],
        ]
        moves = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD,
            'account.move', 'search_read',
            [domain],
            {'fields': ['id', 'name', 'partner_id', 'invoice_date', 'amount_total']})

        if not moves:
            return []

        move_ids = [m['id'] for m in moves]
        lines = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD,
            'account.move.line', 'search_read',
            [[['move_id', 'in', move_ids], ['display_type', '=', 'product']]],
            {'fields': ['move_id', 'name', 'quantity'], 'order': 'id asc', 'limit': 0})

        move_product: dict = {}
        move_qty: dict = {}
        for l in lines:
            mid = l['move_id'][0] if isinstance(l['move_id'], (list, tuple)) else l['move_id']
            if mid not in move_product:
                raw = (l.get('name') or '').split('\n')[0].strip()
                if raw and 'FLETE' not in raw.upper():
                    move_product[mid] = raw
                    move_qty[mid] = int(l.get('quantity') or 0)

        rows = []
        for m in moves:
            pname = m['partner_id'][1] if isinstance(m['partner_id'], (list, tuple)) else 'Sin cliente'
            d_str = str(m.get('invoice_date') or '')[:10]
            if len(d_str) < 10:
                continue
            mid = m['id']
            rows.append({
                'numero_factura':      m.get('name', ''),
                'contacto_referencia': None,
                'contacto_nombre':     pname,
                'nombre_display':      nombre or pname,
                'fecha_factura':       None,
                'venta_total':         float(m.get('amount_total') or 0),
                'nombre_producto':     move_product.get(mid, ''),
                'cantidad':            move_qty.get(mid, 0),
                'anio':                int(d_str[:4]),
                'mes':                 int(d_str[5:7]),
            })
        return rows

    except Exception as e:
        logging.warning('_odoo_supplement_rows: error consultando Odoo: %s', e)
        return []


ventas_bp = Blueprint('ventas', __name__, url_prefix='/ventas')

MESES = {
    1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril',
    5: 'Mayo', 6: 'Junio', 7: 'Julio', 8: 'Agosto',
    9: 'Septiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre'
}

# Solo facturas confirmadas Y cobradas (total o parcialmente)
PAYMENT_FILTER = ['payment_state', 'in', ['paid', 'partial']]


def _ym_from_group(g: dict):
    """Extrae (anio, mes) de un resultado de read_group por invoice_date:month."""
    # Opción 1: usar __range que siempre da fecha ISO 'YYYY-MM-DD'
    rng = g.get('__range', {}).get('invoice_date:month', {})
    date_from = rng.get('from', '')
    if date_from and len(date_from) >= 7 and date_from[4] == '-':
        try:
            return int(date_from[:4]), int(date_from[5:7])
        except ValueError:
            pass
    # Opción 2: campo invoice_date con formato YYYY-MM-DD
    val = g.get('invoice_date')
    if val and val is not False:
        s = str(val)
        if len(s) >= 7 and s[4] == '-':
            try:
                return int(s[:4]), int(s[5:7])
            except ValueError:
                pass
    return None


def _group_count(g: dict, field: str) -> int:
    """Cuenta de registros en un grupo de read_group (compatible Odoo 14-17)."""
    return int(g.get('__count') or g.get(f'{field}_count') or 0)


# ── Cache en memoria para años disponibles ────────────────────────────────────
_anios_cache = None
_anios_cache_ts: float = 0.0
_ANIOS_TTL = 6 * 3600  # 6 horas — los años de facturación no cambian con frecuencia


# ── Años disponibles ──────────────────────────────────────────────────────────
@ventas_bp.route('/anios-disponibles', methods=['GET'])
def anios_disponibles():
    """Devuelve la lista de años con facturas de venta.

    Estrategia (de más rápido a más lento):
      1. Cache en memoria con TTL de 6 h  → respuesta inmediata
      2. Tabla `monitor` en MySQL local   → consulta local, sin red
      3. Fallback a Odoo vía XMLRPC       → sólo si monitor está vacío
    """
    global _anios_cache, _anios_cache_ts

    # ── 1. Responder desde cache si está vigente ──────────────────────────────
    if _anios_cache is not None and (time.time() - _anios_cache_ts) < _ANIOS_TTL:
        return jsonify(_anios_cache), 200

    # ── 2. Consultar la tabla monitor local (MySQL, sin llamada a red) ────────
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor()
        cursor.execute(
            "SELECT MIN(YEAR(fecha_factura)), MAX(YEAR(fecha_factura)) "
            "FROM monitor WHERE fecha_factura IS NOT NULL"
        )
        row = cursor.fetchone()
        cursor.close()
        if conexion.is_connected():
            conexion.close()

        if row and row[0] is not None and row[1] is not None:
            anio_min, anio_max = int(row[0]), int(row[1])
            anios = list(range(anio_min, anio_max + 1))
            _anios_cache = {'anios': anios}
            _anios_cache_ts = time.time()
            return jsonify(_anios_cache), 200
    except Exception:
        logging.warning('ventas.anios_disponibles: no se pudo consultar monitor local, usando Odoo')

    # ── 3. Fallback: consultar Odoo (lento — solo si monitor está vacío) ──────
    try:
        uid, models, err = get_odoo_models()
        if not uid:
            return jsonify({'error': 'No se pudo conectar a Odoo', 'detail': err}), 500

        primero = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD,
            'account.move', 'search_read',
            [[
                ['move_type', '=', 'out_invoice'],
                ['state', '=', 'posted'],
                PAYMENT_FILTER,
                ['company_id', '=', ODOO_COMPANY_ID],
            ]],
            {'fields': ['invoice_date'], 'order': 'invoice_date asc', 'limit': 1})

        ultimo = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD,
            'account.move', 'search_read',
            [[
                ['move_type', '=', 'out_invoice'],
                ['state', '=', 'posted'],
                PAYMENT_FILTER,
                ['company_id', '=', ODOO_COMPANY_ID],
            ]],
            {'fields': ['invoice_date'], 'order': 'invoice_date desc', 'limit': 1})

        if not primero or not ultimo:
            return jsonify({'anios': []}), 200

        anio_min = int(str(primero[0]['invoice_date'])[:4])
        anio_max = int(str(ultimo[0]['invoice_date'])[:4])
        anios = list(range(anio_min, anio_max + 1))
        _anios_cache = {'anios': anios}
        _anios_cache_ts = time.time()
        return jsonify({'anios': anios}), 200

    except Exception as e:
        logging.exception('ventas.anios_disponibles error')
        return jsonify({'error': str(e)}), 500


# ── Resumen de periodo ────────────────────────────────────────────────────────
@ventas_bp.route('/resumen', methods=['GET'])
def resumen():
    """
    Resumen de ventas para un rango de fechas.
    Devuelve: total, por_mes, top_clientes, top_productos, por_estado.
    Param: vista = 'cobranza' (default) | 'facturas'
    """
    fecha_inicio = request.args.get('fecha_inicio')
    fecha_fin    = request.args.get('fecha_fin')
    vista        = request.args.get('vista', 'cobranza')

    if not fecha_inicio or not fecha_fin:
        return jsonify({'error': 'Se requieren fecha_inicio y fecha_fin'}), 400

    try:
        uid, models, err = get_odoo_models()
        if not uid:
            return jsonify({'error': 'No se pudo conectar a Odoo', 'detail': err}), 500

        filtros_pago = [PAYMENT_FILTER] if vista == 'cobranza' else []

        domain_move = [
            ['move_type', '=', 'out_invoice'],
            ['state', '=', 'posted'],
            *filtros_pago,
            ['invoice_date', '>=', fecha_inicio],
            ['invoice_date', '<=', fecha_fin],
            ['company_id', '=', ODOO_COMPANY_ID],
        ]

        domain_line = [
            ['move_id.move_type', '=', 'out_invoice'],
            ['move_id.state', '=', 'posted'],
            *([['move_id.payment_state', 'in', ['paid', 'partial']]] if vista == 'cobranza' else []),
            ['move_id.invoice_date', '>=', fecha_inicio],
            ['move_id.invoice_date', '<=', fecha_fin],
            ['move_id.company_id', '=', ODOO_COMPANY_ID],
            ['display_type', '=', 'product'],
            ['product_id', '!=', False],
        ]

        # ── Llamada 1: desglose por mes ───────────────────────────────────────
        grupos_mes = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD,
            'account.move', 'read_group',
            [domain_move, ['amount_total'], ['invoice_date:month']],
            {'lazy': False})

        por_mes        = []
        total_global   = 0.0
        cantidad_total = 0

        for g in grupos_mes:
            ym = _ym_from_group(g)
            if not ym:
                continue
            anio_m, mes_m = ym
            monto = float(g.get('amount_total') or 0)
            cant  = _group_count(g, 'invoice_date')
            total_global   += monto
            cantidad_total += cant
            por_mes.append({
                'anio':              anio_m,
                'mes':               mes_m,
                'mes_nombre':        MESES[mes_m],
                'total':             round(monto, 2),
                'cantidad_facturas': cant,
            })

        por_mes.sort(key=lambda x: (x['anio'], x['mes']))

        # ── Llamada 2: clientes (reusado también para estados) ─────────────────
        grupos_partner = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD,
            'account.move', 'read_group',
            [domain_move, ['amount_total'], ['partner_id']],
            {'lazy': False})

        clientes_list  = []
        partner_totals = {}   # pid -> {total, facturas}

        for g in grupos_partner:
            pval = g.get('partner_id')
            if pval and isinstance(pval, (list, tuple)):
                pid    = pval[0]
                nombre = pval[1]
            else:
                pid    = 0
                nombre = 'Sin cliente'
            monto = float(g.get('amount_total') or 0)
            cant  = _group_count(g, 'partner_id')
            if monto > 0:
                clientes_list.append({'nombre': nombre, 'total': monto, 'cantidad': cant})
                partner_totals[pid] = {'total': monto, 'facturas': cant, 'nombre': nombre}

        clientes_list.sort(key=lambda x: x['total'], reverse=True)

        # ── Consolidar: cuentas hijas → padre, y grupos integrales → nombre_grupo ─
        consolidation_map = _build_consolidation_map(partner_totals, models, uid)
        if consolidation_map:
            merged: dict = {}
            for pid, info in partner_totals.items():
                key = consolidation_map.get(pid, info.get('nombre', 'Sin cliente'))
                if key not in merged:
                    merged[key] = {'nombre': key, 'total': 0.0, 'cantidad': 0}
                merged[key]['total']    += info['total']
                merged[key]['cantidad'] += info['facturas']
            clientes_list = sorted(merged.values(), key=lambda x: x['total'], reverse=True)

        total_cli = sum(c['total'] for c in clientes_list) or 1
        todos_clientes = [
            {'rank': i + 1, 'nombre': c['nombre'],
             'facturas': c['cantidad'], 'total': round(c['total'], 2),
             'participacion_pct': round(c['total'] / total_cli * 100, 1)}
            for i, c in enumerate(clientes_list)
        ]
        top_clientes = todos_clientes[:10]

        # ── Llamada 3: top 10 productos (por nombre de línea) ─────────────────
        # En esta instancia los account.move.line no tienen product_id;
        # el nombre del producto está en el campo 'name'. Se agrupa en Python.
        move_ids_period = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD,
            'account.move', 'search', [domain_move])

        lineas_prod: list = []
        if move_ids_period:
            lineas_prod = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD,
                'account.move.line', 'search_read',
                [[
                    ['move_id', 'in', move_ids_period],
                    ['display_type', '=', 'product'],
                    ['quantity', '>', 0],
                ]],
                {'fields': ['name', 'quantity', 'price_subtotal'], 'limit': 0})

        prod_dict: dict = {}
        for l in lineas_prod:
            nombre_raw = (l.get('name') or '').strip()
            if not nombre_raw:
                continue
            # Tomar solo la primera línea (quita números de serie, fechas, etc.)
            nombre = nombre_raw.split('\n')[0].strip()
            if not nombre or 'FLETE' in nombre.upper() or 'LEYENDA' in nombre.upper():
                continue
            monto = float(l.get('price_subtotal') or 0)
            cant  = float(l.get('quantity') or 0)
            if monto <= 0:
                continue
            if nombre not in prod_dict:
                prod_dict[nombre] = {'total': 0.0, 'cantidad': 0.0}
            prod_dict[nombre]['total']    += monto
            prod_dict[nombre]['cantidad'] += cant

        prod_sorted = sorted(prod_dict.items(), key=lambda x: x[1]['total'], reverse=True)
        total_prod = sum(v['total'] for _, v in prod_sorted) or 1
        todos_productos = [
            {'rank': i + 1, 'nombre': k,
             'cantidad': int(round(v['cantidad'])), 'total': round(v['total'], 2),
             'participacion_pct': round(v['total'] / total_prod * 100, 1)}
            for i, (k, v) in enumerate(prod_sorted)
        ]
        top_productos = todos_productos[:10]

        # ── Llamada 4: estados (via partner_id) ───────────────────────────────
        por_estado: list = []
        if partner_totals:
            all_pids      = list(partner_totals.keys())
            partners_data = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD,
                'res.partner', 'read',
                [all_pids],
                {'fields': ['id', 'state_id']})

            estado_dict: dict = {}
            for p in partners_data:
                state = p.get('state_id')
                if state and isinstance(state, (list, tuple)):
                    estado_name = state[1].replace(' (MX)', '').strip()
                else:
                    estado_name = 'Sin estado'
                montos      = partner_totals.get(p['id'], {'total': 0, 'facturas': 0})
                if estado_name not in estado_dict:
                    estado_dict[estado_name] = {'total': 0.0, 'facturas': 0}
                estado_dict[estado_name]['total']    += montos['total']
                estado_dict[estado_name]['facturas'] += montos['facturas']

            por_estado = sorted(
                [{'estado': k, 'total': round(v['total'], 2), 'facturas': v['facturas']}
                 for k, v in estado_dict.items() if v['total'] > 0],
                key=lambda x: x['total'], reverse=True
            )

        return jsonify({
            'total':             round(total_global, 2),
            'cantidad_facturas': cantidad_total,
            'por_mes':           por_mes,
            'top_clientes':      top_clientes,
            'todos_clientes':    todos_clientes,
            'top_productos':     top_productos,
            'todos_productos':   todos_productos,
            'por_estado':        por_estado,
        }), 200

    except Exception as e:
        logging.exception('ventas.resumen error')
        return jsonify({'error': str(e)}), 500


# ── Productos top por estado ──────────────────────────────────────────────────
@ventas_bp.route('/productos-por-estado', methods=['GET'])
def productos_por_estado():
    """
    Top de productos vendidos en un estado específico.
    Params: fecha_inicio, fecha_fin, estado (nombre del estado)
    """
    fecha_inicio = request.args.get('fecha_inicio')
    fecha_fin    = request.args.get('fecha_fin')
    estado_req   = request.args.get('estado', '').strip()
    vista        = request.args.get('vista', 'cobranza')

    if not fecha_inicio or not fecha_fin or not estado_req:
        return jsonify({'error': 'Se requieren fecha_inicio, fecha_fin y estado'}), 400

    try:
        uid, models, err = get_odoo_models()
        if not uid:
            return jsonify({'error': 'No se pudo conectar a Odoo', 'detail': err}), 500

        filtros_pago = [PAYMENT_FILTER] if vista == 'cobranza' else []

        domain_move = [
            ['move_type', '=', 'out_invoice'],
            ['state', '=', 'posted'],
            *filtros_pago,
            ['invoice_date', '>=', fecha_inicio],
            ['invoice_date', '<=', fecha_fin],
            ['company_id', '=', ODOO_COMPANY_ID],
        ]

        # Facturas del periodo con partner_id
        moves = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD,
            'account.move', 'search_read',
            [domain_move],
            {'fields': ['id', 'partner_id'], 'limit': 0})

        if not moves:
            return jsonify({'estado': estado_req, 'total': 0, 'facturas': 0, 'productos': []}), 200

        all_pids = list({
            m['partner_id'][0]
            for m in moves
            if m.get('partner_id') and isinstance(m['partner_id'], (list, tuple))
        })

        if not all_pids:
            return jsonify({'estado': estado_req, 'total': 0, 'facturas': 0, 'productos': []}), 200

        # Estado de cada partner
        partners_data = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD,
            'res.partner', 'read', [all_pids], {'fields': ['id', 'state_id']})

        pids_en_estado: set = set()
        for p in partners_data:
            state = p.get('state_id')
            if state and isinstance(state, (list, tuple)):
                nombre_estado = state[1].replace(' (MX)', '').strip()
            else:
                nombre_estado = 'Sin estado'
            if nombre_estado == estado_req:
                pids_en_estado.add(p['id'])

        if not pids_en_estado:
            return jsonify({'estado': estado_req, 'total': 0, 'facturas': 0, 'productos': []}), 200

        move_ids_estado = [
            m['id'] for m in moves
            if m.get('partner_id') and isinstance(m['partner_id'], (list, tuple))
            and m['partner_id'][0] in pids_en_estado
        ]

        if not move_ids_estado:
            return jsonify({'estado': estado_req, 'total': 0, 'facturas': 0, 'productos': []}), 200

        lines = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD,
            'account.move.line', 'search_read',
            [[
                ['move_id', 'in', move_ids_estado],
                ['display_type', '=', 'product'],
                ['quantity', '>', 0],
            ]],
            {'fields': ['name', 'quantity', 'price_subtotal'], 'limit': 0})

        prod_dict: dict = {}
        for l in lines:
            nombre_raw = (l.get('name') or '').split('\n')[0].strip()
            if not nombre_raw or 'FLETE' in nombre_raw.upper() or 'LEYENDA' in nombre_raw.upper():
                continue
            monto = float(l.get('price_subtotal') or 0)
            cant  = float(l.get('quantity') or 0)
            if monto <= 0:
                continue
            if nombre_raw not in prod_dict:
                prod_dict[nombre_raw] = {'total': 0.0, 'cantidad': 0.0}
            prod_dict[nombre_raw]['total']    += monto
            prod_dict[nombre_raw]['cantidad'] += cant

        total_estado = sum(v['total'] for v in prod_dict.values())
        prod_sorted = sorted(prod_dict.items(), key=lambda x: x[1]['total'], reverse=True)

        productos = [
            {
                'rank':              i + 1,
                'nombre':            k,
                'cantidad':          int(round(v['cantidad'])),
                'total':             round(v['total'], 2),
                'participacion_pct': round(v['total'] / total_estado * 100, 1) if total_estado > 0 else 0,
            }
            for i, (k, v) in enumerate(prod_sorted[:25])
        ]

        return jsonify({
            'estado':   estado_req,
            'total':    round(total_estado, 2),
            'facturas': len(move_ids_estado),
            'productos': productos,
        }), 200

    except Exception as e:
        logging.exception('ventas.productos_por_estado error')
        return jsonify({'error': str(e)}), 500


# ── Comparar dos años mes a mes ───────────────────────────────────────────────
@ventas_bp.route('/comparar-anual', methods=['GET'])
def comparar_anual():
    """
    Compara 12 meses de dos años en una sola llamada.

    Params:
        anio1   int
        anio2   int

    Respuesta:
        {
          anio1, anio2,
          total1, total2, delta, delta_pct,
          meses: [{ mes, mes_nombre, total1, cantidad1, total2, cantidad2, delta, delta_pct }]
        }
    """
    try:
        anio1 = request.args.get('anio1', type=int)
        anio2 = request.args.get('anio2', type=int)
        if not anio1 or not anio2:
            return jsonify({'error': 'Se requieren anio1 y anio2'}), 400

        uid, models, err = get_odoo_models()
        if not uid:
            return jsonify({'error': 'No se pudo conectar a Odoo', 'detail': err}), 500

        def _resumen_anual(anio: int) -> dict:
            grupos = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD,
                'account.move', 'read_group',
                [[
                    ['move_type', '=', 'out_invoice'],
                    ['state', '=', 'posted'],
                    PAYMENT_FILTER,
                    ['invoice_date', '>=', f'{anio}-01-01'],
                    ['invoice_date', '<=', f'{anio}-12-31'],
                    ['company_id', '=', ODOO_COMPANY_ID],
                ],
                ['amount_total'],
                ['invoice_date:month']],
                {'lazy': False})

            por_mes = {m: {'total': 0.0, 'cantidad': 0} for m in range(1, 13)}
            for g in grupos:
                ym = _ym_from_group(g)
                if not ym:
                    continue
                _, mes_num = ym
                por_mes[mes_num]['total']    += float(g.get('amount_total') or 0)
                por_mes[mes_num]['cantidad'] += _group_count(g, 'invoice_date')
            return por_mes

        pm1 = _resumen_anual(anio1)
        pm2 = _resumen_anual(anio2)

        meses_out = []
        for m in range(1, 13):
            t1 = round(pm1[m]['total'], 2)
            t2 = round(pm2[m]['total'], 2)
            delta     = round(t2 - t1, 2)
            delta_pct = round((delta / t1 * 100) if t1 > 0 else 0, 1)
            meses_out.append({
                'mes':        m,
                'mes_nombre': MESES[m],
                'total1':     t1,
                'cantidad1':  pm1[m]['cantidad'],
                'total2':     t2,
                'cantidad2':  pm2[m]['cantidad'],
                'delta':      delta,
                'delta_pct':  delta_pct,
            })

        total1 = round(sum(pm1[m]['total'] for m in range(1, 13)), 2)
        total2 = round(sum(pm2[m]['total'] for m in range(1, 13)), 2)
        delta_total = round(total2 - total1, 2)
        delta_total_pct = round((delta_total / total1 * 100) if total1 > 0 else 0, 1)

        return jsonify({
            'anio1':      anio1,
            'anio2':      anio2,
            'total1':     total1,
            'total2':     total2,
            'delta':      delta_total,
            'delta_pct':  delta_total_pct,
            'meses':      meses_out,
        }), 200

    except Exception as e:
        logging.exception('ventas.comparar_anual error')
        return jsonify({'error': str(e)}), 500


# ── Resumen por integral (fuente: Odoo, montos reales) ───────────────────────
@ventas_bp.route('/resumen-integral', methods=['GET'])
def resumen_integral():
    """
    Resumen de ventas de un grupo integral consultado directamente en Odoo.
    Usa amount_total real (no calculado) e incluye facturas paid y partial.
    Cubre el año natural completo sin límite de tabla local.
    """
    fecha_inicio = request.args.get('fecha_inicio')
    fecha_fin    = request.args.get('fecha_fin')
    grupo_id     = request.args.get('grupo_id', type=int)
    vista        = request.args.get('vista', 'cobranza')

    if not fecha_inicio or not fecha_fin:
        return jsonify({'error': 'Se requieren fecha_inicio y fecha_fin'}), 400
    if not grupo_id:
        return jsonify({'error': 'Se requiere grupo_id'}), 400

    try:
        filtros_pago = [PAYMENT_FILTER] if vista == 'cobranza' else []

        # ── 1. Claves del grupo ───────────────────────────────────────────────
        claves = _claves_para_grupo(grupo_id)
        if not claves:
            return jsonify({'error': 'Grupo no encontrado o sin clientes asignados'}), 404

        # ── 2. Conectar a Odoo ────────────────────────────────────────────────
        uid, models, err = get_odoo_models()
        if not uid:
            return jsonify({'error': 'No se pudo conectar a Odoo', 'detail': err}), 500

        # ── 3. Partner IDs (padres + cuentas hijas) ───────────────────────────
        parents = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD,
            'res.partner', 'search_read',
            [[['ref', 'in', claves]]],
            {'fields': ['id', 'name']})
        all_pids   = [p['id'] for p in parents]
        pid_name   = {p['id']: p['name'] for p in parents}
        if all_pids:
            children = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD,
                'res.partner', 'search_read',
                [[['parent_id', 'in', all_pids]]],
                {'fields': ['id', 'name', 'parent_id']})
            for c in children:
                cid    = c['id']
                parent = c.get('parent_id')
                pid    = parent[0] if isinstance(parent, (list, tuple)) else parent
                if cid not in all_pids:
                    all_pids.append(cid)
                # El nombre de la cuenta hija usa el nombre del padre para consolidar
                pid_name[cid] = pid_name.get(pid, c.get('name', 'Sin cliente'))

        if not all_pids:
            _empty = {'total': 0, 'cantidad_facturas': 0, 'participacion_total_pct': 0,
                      'global_total': 0, 'por_mes': [],
                      'top_clientes': [], 'todos_clientes': [],
                      'top_productos': [], 'todos_productos': [], 'por_estado': []}
            return jsonify(_empty), 200

        # ── 4a. Total global del período (todas las ventas, sin filtro de grupo) ─
        domain_global = [
            ['move_type', '=', 'out_invoice'],
            ['state', '=', 'posted'],
            *filtros_pago,
            ['invoice_date', '>=', fecha_inicio],
            ['invoice_date', '<=', fecha_fin],
            ['company_id', '=', ODOO_COMPANY_ID],
        ]
        global_agg = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD,
            'account.move', 'read_group',
            [domain_global, ['amount_total'], []],
            {'lazy': False})
        global_total = float((global_agg[0].get('amount_total') or 0) if global_agg else 0)

        # ── 4b. Facturas del grupo ────────────────────────────────────────────
        domain_move = [
            ['move_type', '=', 'out_invoice'],
            ['state', '=', 'posted'],
            *filtros_pago,
            ['invoice_date', '>=', fecha_inicio],
            ['invoice_date', '<=', fecha_fin],
            ['company_id', '=', ODOO_COMPANY_ID],
            ['partner_id', 'in', all_pids],
        ]
        moves = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD,
            'account.move', 'search_read',
            [domain_move],
            {'fields': ['id', 'name', 'partner_id', 'invoice_date', 'amount_total']})

        if not moves:
            _empty = {'total': 0, 'cantidad_facturas': 0, 'participacion_total_pct': 0,
                      'global_total': round(global_total, 2), 'por_mes': [],
                      'top_clientes': [], 'todos_clientes': [],
                      'top_productos': [], 'todos_productos': [], 'por_estado': []}
            return jsonify(_empty), 200

        # ── 5. Líneas de producto para el top de artículos ────────────────────
        move_ids = [m['id'] for m in moves]
        lines = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD,
            'account.move.line', 'search_read',
            [[['move_id', 'in', move_ids],
              ['display_type', '=', 'product'],
              ['quantity', '>', 0]]],
            {'fields': ['move_id', 'name', 'quantity', 'price_subtotal'], 'limit': 0})

        prod_dict: dict = {}
        for l in lines:
            nombre_raw = (l.get('name') or '').split('\n')[0].strip()
            if not nombre_raw or 'FLETE' in nombre_raw.upper() or 'LEYENDA' in nombre_raw.upper():
                continue
            monto = float(l.get('price_subtotal') or 0)
            cant  = float(l.get('quantity') or 0)
            if monto <= 0:
                continue
            if nombre_raw not in prod_dict:
                prod_dict[nombre_raw] = {'total': 0.0, 'cantidad': 0.0}
            prod_dict[nombre_raw]['total']    += monto
            prod_dict[nombre_raw]['cantidad'] += cant

        # ── 6. Agregación por mes y cliente ───────────────────────────────────
        total_global  = 0.0
        facturas_set  = set()
        por_mes_dict  = {}
        clientes_dict = {}

        for m in moves:
            monto   = float(m.get('amount_total') or 0)
            factura = m.get('name', '')
            d_str   = str(m.get('invoice_date') or '')[:10]
            if len(d_str) < 10 or monto <= 0:
                continue

            anio_r = int(d_str[:4])
            mes_r  = int(d_str[5:7])
            pid_m  = m['partner_id'][0] if isinstance(m['partner_id'], (list, tuple)) else None
            pname  = pid_name.get(pid_m, (m['partner_id'][1] if isinstance(m['partner_id'], (list, tuple)) else 'Sin cliente'))

            total_global += monto
            facturas_set.add(factura)

            ym = (anio_r, mes_r)
            if ym not in por_mes_dict:
                por_mes_dict[ym] = {'total': 0.0, 'facturas': set()}
            por_mes_dict[ym]['total']    += monto
            por_mes_dict[ym]['facturas'].add(factura)

            if pname not in clientes_dict:
                clientes_dict[pname] = {'total': 0.0, 'facturas': set()}
            clientes_dict[pname]['total']    += monto
            clientes_dict[pname]['facturas'].add(factura)

        # ── 7. Construir respuesta ────────────────────────────────────────────
        por_mes = sorted([
            {
                'anio':              ym[0],
                'mes':               ym[1],
                'mes_nombre':        MESES.get(ym[1], ''),
                'total':             round(v['total'], 2),
                'cantidad_facturas': len(v['facturas']),
            }
            for ym, v in por_mes_dict.items()
        ], key=lambda x: (x['anio'], x['mes']))

        denom = max(global_total, 1)

        clientes_sorted = sorted(clientes_dict.items(), key=lambda x: x[1]['total'], reverse=True)
        todos_clientes = [
            {
                'rank':              i + 1,
                'nombre':            nombre,
                'facturas':          len(v['facturas']),
                'total':             round(v['total'], 2),
                'participacion_pct': round(v['total'] / denom * 100, 1),
            }
            for i, (nombre, v) in enumerate(clientes_sorted)
        ]
        top_clientes = todos_clientes[:10]

        prod_sorted = sorted(prod_dict.items(), key=lambda x: x[1]['total'], reverse=True)
        todos_productos = [
            {
                'rank':              i + 1,
                'nombre':            nombre,
                'cantidad':          int(round(v['cantidad'])),
                'total':             round(v['total'], 2),
                'participacion_pct': round(v['total'] / denom * 100, 1),
            }
            for i, (nombre, v) in enumerate(prod_sorted)
        ]
        top_productos = todos_productos[:10]

        return jsonify({
            'total':                    round(total_global, 2),
            'cantidad_facturas':        len(facturas_set),
            'participacion_total_pct':  round(total_global / denom * 100, 1),
            'global_total':             round(global_total, 2),
            'por_mes':                  por_mes,
            'top_clientes':             top_clientes,
            'todos_clientes':           todos_clientes,
            'top_productos':            top_productos,
            'todos_productos':          todos_productos,
            'por_estado':               [],
        }), 200

    except Exception as e:
        logging.exception('ventas.resumen_integral error')
        return jsonify({'error': str(e)}), 500

