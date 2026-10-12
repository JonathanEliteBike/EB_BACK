from __future__ import annotations
from flask import Blueprint, jsonify, request, current_app, g
from utils.odoo_utils import get_odoo_models, ODOO_DB, ODOO_PASSWORD, ODOO_COMPANY_ID
from db_conexion import obtener_conexion
from utils.auth_decorators import requiere_autenticacion, requiere_permiso_interno
from utils.dashboard_clasificacion_comercial import (
    obtener_clasificacion_comercial,
    obtener_submodelo_aprobado,
)
from services.politica_montos_service import PoliticaMontosService
from datetime import date as _date, timedelta as _timedelta
import logging
import time
import re
import unicodedata
import threading

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


@ventas_bp.route('/listar-clientes', methods=['GET'])
@requiere_autenticacion
@requiere_permiso_interno('ventas_monitor', 'ver')
def listar_clientes():
    """Sugerencias de clientes para el buscador del Monitor de Ventas."""
    termino = (request.args.get('q') or '').strip()
    if len(termino) < 2:
        return jsonify({'clientes': []}), 200

    try:
        if current_app.config.get('ODOO_TRACE_VENTAS', False):
            logging.info('ventas.listar_clientes odoo_inicio caracteres_consulta=%d', len(termino))
        uid, models, error = get_odoo_models()
        if not uid:
            if current_app.config.get('ODOO_TRACE_VENTAS', False):
                logging.warning('ventas.listar_clientes odoo_no_disponible error=%s', str(error)[:200])
            return jsonify({'error': 'No se pudo conectar a Odoo', 'detail': error}), 500
        clientes = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD, 'res.partner', 'search_read',
            [[['name', 'ilike', termino]]],
            {'fields': ['id', 'name'], 'limit': 20, 'order': 'name'}
        )
        if current_app.config.get('ODOO_TRACE_VENTAS', False):
            logging.info('ventas.listar_clientes odoo_resultado registros=%d', len(clientes))
        return jsonify({
            'clientes': [
                {'id': cliente['id'], 'nombre': cliente.get('name') or ''}
                for cliente in clientes
            ]
        }), 200
    except Exception as error:
        logging.exception('ventas.listar_clientes error')
        return jsonify({'error': str(error)}), 500

MESES = {
    1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril',
    5: 'Mayo', 6: 'Junio', 7: 'Julio', 8: 'Agosto',
    9: 'Septiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre'
}

# Solo facturas confirmadas Y cobradas (total o parcialmente)
PAYMENT_FILTER = ['payment_state', 'in', ['paid', 'partial']]


def _debe_ocultar_montos_ventas() -> bool:
    return PoliticaMontosService.debe_ocultar_montos(g.usuario_actual['id'], 'ventas_monitor')


# ── Consulta dinámica del Dashboard de Ventas ───────────────────────────────
_DASHBOARD_DIMENSIONES_SOPORTADAS = (
    'marca', 'producto', 'categoria', 'subcategoria', 'talla', 'color',
    'modelo', 'submodelo', 'estado', 'cliente', 'integral',
)
_DASHBOARD_DIMENSIONES_PENDIENTES = ('tienda',)
_DASHBOARD_TODAS_DIMENSIONES = (
    *_DASHBOARD_DIMENSIONES_SOPORTADAS,
    *_DASHBOARD_DIMENSIONES_PENDIENTES,
)
_DASHBOARD_ORDENES = ('pzas', 'venta', 'participacion')
_DASHBOARD_LABELS = {
    'marca': 'Marca', 'producto': 'Producto', 'categoria': 'Categoría',
    'subcategoria': 'Subcategoría', 'modelo': 'Modelo', 'submodelo': 'Submodelo',
    'talla': 'Talla', 'color': 'Color',
    'estado': 'Estado', 'cliente': 'Cliente', 'integral': 'Integral',
}
# Los catálogos libres sirven para armar comparaciones y sólo dependen del
# universo temporal. Las dimensiones comerciales detalladas conservan un
# contexto acotado para que sus listas sigan siendo útiles y manejables.
_DASHBOARD_CONTEXTO_FILTROS = {
    'marca': (), 'producto': (), 'categoria': ('producto',),
    'subcategoria': ('producto', 'categoria'),
    'estado': (), 'cliente': (), 'integral': (),
    'modelo': ('marca', 'producto', 'categoria', 'subcategoria'),
    'submodelo': ('marca', 'producto', 'categoria', 'subcategoria', 'modelo'),
    'talla': ('marca', 'producto', 'categoria', 'subcategoria', 'modelo', 'submodelo'),
    'color': ('marca', 'producto', 'categoria', 'subcategoria', 'modelo', 'submodelo'),
}

# Familias observadas en el catálogo comercial de Odoo. La llave se normaliza
# sólo para comparar; el valor es la etiqueta que se expone al dashboard.
_DASHBOARD_FAMILIAS_PRODUCTO = {
    'BICICLETA': 'BICICLETA',
    'APPAREL': 'ROPA',
    'ACCESORIOS': 'ACCESORIOS',
    'ACCESORIOS VARIOS': 'ACCESORIOS',
    'COMPONENTES': 'COMPONENTES',
    'REEMPLAZO': 'REFACCIONES',
    'REEMPLAZOS': 'REFACCIONES',
    'CUADRO': 'CUADRO',
    'HERRAMIENTAS': 'HERRAMIENTAS',
    'PERIFERICOS': 'PERIFERICOS',
    'SERVICIOS': 'SERVICIOS',
}
_DASHBOARD_SUBCATEGORIAS_BICICLETA = {'MONTANA', 'ELECTRICA'}
# Odoo no conserva el tipo comercial en las rutas JUNIOR de estos modelos.
# Este mapeo sólo cubre modelos confirmados del catálogo comercial; los que no
# estén aquí permanecen sin subcategoría para no inferir un tipo incorrecto.
_DASHBOARD_SUBCATEGORIAS_JUNIOR_POR_MODELO = {
    'CONTRAIL 200': 'MONTAÑA',
    'CONTRAIL 400': 'MONTAÑA',
    'ROXTER': 'MONTAÑA',
    'SCALE 24': 'MONTAÑA',
}
_DASHBOARD_UNIVERSO_TTL = 180
_DASHBOARD_CLASIFICACION_COMERCIAL_VERSION = 2
_dashboard_universo_cache: dict[tuple, tuple[float, list[dict]]] = {}
_dashboard_universo_lock = threading.RLock()
_DASHBOARD_MESES_CORTOS = ('Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic')


def _dashboard_lotes(items: list, tamanio: int = 500):
    """Genera lotes para las lecturas XML-RPC; evita una llamada por registro."""
    for inicio in range(0, len(items), tamanio):
        yield items[inicio:inicio + tamanio]


def _dashboard_ruta_categoria(valor) -> list[str]:
    """Normaliza complete_name sin asumir posiciones absolutas en la ruta."""
    segmentos = []
    for indice, segmento in enumerate(str(valor or '').split('/')):
        limpio = segmento.strip()
        # Algunas rutas de Odoo incluyen un identificador numérico de compañía
        # como primer nivel. Los números posteriores sí pueden ser una versión
        # comercial (p. ej. FACTORY / 15), por lo que deben conservarse.
        if not limpio or limpio.upper() == 'ALL' or (indice == 0 and limpio.isdigit()):
            continue
        segmentos.append(limpio)
    return segmentos


def _dashboard_marcas_por_ruta(rutas) -> dict[str, str]:
    """Detecta raíces comerciales por estructura, sin catálogo fijo de marcas.

    Una raíz con descendientes es una candidata a marca siempre que no sea una
    familia operativa conocida (por ejemplo, APPAREL o SERVICIOS). Esto permite
    incorporar marcas nuevas que tengan su propia rama de categorías.
    """
    marcas = {}
    for ruta in rutas:
        if len(ruta) < 2:
            continue
        raiz = ruta[0]
        clave = _dashboard_clave_texto(raiz)
        if clave not in _DASHBOARD_FAMILIAS_PRODUCTO:
            marcas.setdefault(clave, raiz)
    return marcas


def _dashboard_clave_texto(valor) -> str:
    texto = unicodedata.normalize('NFD', str(valor or ''))
    return ''.join(c for c in texto if unicodedata.category(c) != 'Mn').strip().upper()


def _dashboard_submodelo_desde_template(nombre_template, modelo: str | None) -> str | None:
    """Obtiene una versión sólo si el template conserva el modelo como prefijo comercial."""
    if not nombre_template or not modelo:
        return None
    texto = ' '.join(str(nombre_template).split())
    coincidencia = re.search(re.escape(modelo), texto, flags=re.IGNORECASE)
    if not coincidencia:
        return None
    candidato = texto[coincidencia.start():]
    candidato = re.split(r'\s+(?:MY\s*\d{2,4}|20\d{2})\b', candidato, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    return candidato if candidato and _dashboard_clave_texto(candidato) != _dashboard_clave_texto(modelo) else None


def _dashboard_subcategoria_junior_por_modelo(modelo, nombre_template) -> str | None:
    """Resuelve únicamente modelos JUNIOR verificados fuera de la jerarquía."""
    candidatos = [_dashboard_clave_texto(modelo), _dashboard_clave_texto(nombre_template)]
    for modelo_mapeado, subcategoria in _DASHBOARD_SUBCATEGORIAS_JUNIOR_POR_MODELO.items():
        clave_modelo = _dashboard_clave_texto(modelo_mapeado)
        if any(clave_modelo in candidato for candidato in candidatos if candidato):
            return subcategoria
    return None


def _dashboard_dimensiones_producto(
    ruta: list[str], atributos: list[dict], nombre_template=None, marcas_por_ruta=None
) -> dict:
    """Clasifica catálogo por familia comercial, sin asumir posiciones absolutas.

    Una marca sólo existe cuando hay una familia de producto reconocida después
    de ella; las ramas raíz operativas (por ejemplo, servicios) no se inventan
    como marcas. Modelo/submodelo se habilitan únicamente para bicicletas,
    donde la taxonomía del catálogo sí contiene esos niveles de forma estable.
    """
    marcas_por_ruta = marcas_por_ruta or {}
    familia_indice = next(
        (i for i, segmento in enumerate(ruta)
         if _dashboard_clave_texto(segmento) in _DASHBOARD_FAMILIAS_PRODUCTO),
        None,
    )
    marca = marcas_por_ruta.get(_dashboard_clave_texto(ruta[0])) if ruta else None
    if familia_indice is not None:
        producto = _DASHBOARD_FAMILIAS_PRODUCTO[_dashboard_clave_texto(ruta[familia_indice])]
        niveles = ruta[familia_indice + 1:]
    elif marca and len(ruta) > 1:
        # Marcas como VITTORIA organizan su primer nivel comercial con tipos
        # propios (LLANTAS, CÁMARAS, etc.), sin una familia raíz intermedia.
        producto = ruta[1]
        niveles = ruta[2:]
    else:
        producto = None
        niveles = []
    categoria = niveles[0] if niveles else None
    subcategoria = None
    indice_modelo = 1
    if producto == 'BICICLETA' and _dashboard_clave_texto(categoria) in ('JUNIOR', 'INFANTIL') and len(niveles) > 1:
        candidato = niveles[1]
        if _dashboard_clave_texto(candidato) in _DASHBOARD_SUBCATEGORIAS_BICICLETA:
            subcategoria = candidato
            indice_modelo = 2
    elif producto != 'BICICLETA' and len(niveles) > 1:
        # En las familias no-bicicleta el segundo nivel del catálogo es una
        # subdivisión comercial (p. ej. LLANTAS → DE MONTAÑA → TRAIL). No se
        # usa como Modelo, porque esa dimensión sólo es confiable en bicicletas.
        subcategoria = niveles[1]

    modelo = None
    submodelo = None
    if producto == 'BICICLETA' and len(niveles) > indice_modelo:
        modelo = niveles[indice_modelo]
        if len(niveles) > indice_modelo + 1:
            submodelo = niveles[indice_modelo + 1]
        else:
            submodelo = _dashboard_submodelo_desde_template(nombre_template, modelo)

    if producto == 'BICICLETA' and _dashboard_clave_texto(categoria) == 'JUNIOR' and not subcategoria:
        subcategoria = _dashboard_subcategoria_junior_por_modelo(modelo, nombre_template)

    talla = None
    color = None
    for atributo in atributos:
        nombre = str(atributo.get('atributo') or '').upper()
        valor = (atributo.get('valor') or '').strip() or None
        if not valor:
            continue
        if any(clave in nombre for clave in ('COLOR', 'COLO', 'COLOUR')):
            color = valor
        elif any(clave in nombre for clave in ('TALLA', 'TAMAÑO', 'TAMANO', 'SIZE')):
            talla = valor

    return {
        'marca': marca, 'producto': producto, 'categoria': categoria,
        'subcategoria': subcategoria, 'modelo': modelo, 'submodelo': submodelo,
        'talla': talla, 'color': color,
    }


def _dashboard_aplicar_clasificacion_comercial(product_id, dimensiones: dict) -> str | None:
    """Aplica sólo la consolidación validada de SCOTT / ROPA por product_id.

    La referencia comercial es informativa: nunca participa como filtro ni
    desglose. Modelo conserva únicamente valores confirmados.
    """
    clasificacion = obtener_clasificacion_comercial(product_id)
    if not clasificacion:
        return None

    estado = clasificacion.get('c')
    if estado == 'MODELO_CONFIRMADO':
        dimensiones['modelo'] = clasificacion.get('m') or None
        submodelo_aprobado = obtener_submodelo_aprobado(product_id)
        if submodelo_aprobado:
            dimensiones['submodelo'] = submodelo_aprobado
        return None
    if estado == 'LINEA_CONFIRMADA':
        dimensiones['modelo'] = None
        return f"{clasificacion.get('r')} · Línea"
    if estado == 'POR_VALIDAR':
        dimensiones['modelo'] = None
        return f"{clasificacion.get('r')} · Por validar"
    if estado == 'NO_ESPECIFICADO':
        dimensiones['modelo'] = None
        return 'No especificado'
    if estado == 'RECHAZADO':
        dimensiones['modelo'] = None
    return None


def _dashboard_referencia_comercial_agregada(referencias: set[str]) -> list[str]:
    """Devuelve referencias únicas y deterministas para una fila agregada."""
    return sorted(
        referencias,
        key=lambda referencia: (referencia == 'No especificado', referencia.casefold()),
    )


def _dashboard_dimension(valor, etiqueta_sin_dato: str) -> dict:
    """Mantiene un contrato uniforme para dimensiones presentes o ausentes."""
    if valor is None or str(valor).strip() == '':
        return {'id': None, 'label': etiqueta_sin_dato}
    return {'id': str(valor), 'label': str(valor)}


def _dashboard_coincide_filtro(dimension: dict, valores: list[str]) -> bool:
    if not valores:
        return True
    comparables = {
        str(dimension.get('id') or '').strip().casefold(),
        str(dimension.get('label') or '').strip().casefold(),
    }
    return any(str(valor).strip().casefold() in comparables for valor in valores)


def _dashboard_integrales_por_clave(claves: set[str]) -> dict[str, dict]:
    """Resuelve la relación existente clientes → grupo_clientes en una consulta."""
    if not claves:
        return {}
    conn = obtener_conexion()
    cur = conn.cursor(dictionary=True)
    try:
        placeholders = ','.join(['%s'] * len(claves))
        cur.execute(f"""
            SELECT c.clave, gc.id AS grupo_id, gc.nombre_grupo
            FROM clientes c
            LEFT JOIN grupo_clientes gc ON gc.id = c.id_grupo
            WHERE UPPER(c.clave) IN ({placeholders})
        """, list(claves))
        return {
            str(fila['clave']).strip().upper(): {
                'id': fila['grupo_id'], 'label': fila['nombre_grupo']
            }
            for fila in cur.fetchall()
            if fila.get('grupo_id') and fila.get('nombre_grupo')
        }
    finally:
        cur.close()
        conn.close()


def _dashboard_universo_enriquecido(consulta: dict) -> list[dict]:
    """Obtiene y enriquece una vez las líneas del universo del dashboard.

    La caché sólo contiene dimensiones y métricas sin transformar permisos. El
    ocultamiento de montos se sigue resolviendo al construir cada respuesta.
    """
    clave_cache = (
        consulta['fecha_inicio'], consulta['fecha_fin'], consulta['vista'], ODOO_COMPANY_ID,
        _DASHBOARD_CLASIFICACION_COMERCIAL_VERSION,
    )
    ahora = time.time()
    with _dashboard_universo_lock:
        vigente = _dashboard_universo_cache.get(clave_cache)
        if vigente and ahora - vigente[0] < _DASHBOARD_UNIVERSO_TTL:
            # Las entradas previas a la tendencia no incluían fecha; se
            # reconstruyen una vez para que la serie mensual sea consistente.
            if all('fecha' in fila for fila in vigente[1]):
                return vigente[1]
            _dashboard_universo_cache.pop(clave_cache, None)

        uid, models, err = get_odoo_models()
        if not uid:
            raise RuntimeError(err or 'No se pudo conectar a Odoo')

        domain_line = [
            ['move_id.move_type', '=', 'out_invoice'],
            ['move_id.state', '=', 'posted'],
            *([['move_id.payment_state', 'in', ['paid', 'partial']]]
              if consulta['vista'] == 'cobranza' else []),
            ['move_id.invoice_date', '>=', consulta['fecha_inicio']],
            ['move_id.invoice_date', '<=', consulta['fecha_fin']],
            ['move_id.company_id', '=', ODOO_COMPANY_ID],
            ['display_type', '=', 'product'],
            ['product_id', '!=', False],
        ]
        lineas = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD, 'account.move.line', 'search_read',
            [domain_line], {'fields': ['product_id', 'quantity', 'price_total', 'move_id'], 'limit': 0}
        )
        product_ids = sorted({linea['product_id'][0] for linea in lineas if linea.get('product_id')})
        move_ids = sorted({linea['move_id'][0] for linea in lineas if linea.get('move_id')})

        productos = []
        for ids in _dashboard_lotes(product_ids):
            productos.extend(models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD, 'product.product', 'read', [ids],
                {'fields': ['id', 'categ_id', 'product_tmpl_id', 'product_template_attribute_value_ids']}
            ))
        productos_por_id = {producto['id']: producto for producto in productos}
        template_ids = sorted({p['product_tmpl_id'][0] for p in productos if p.get('product_tmpl_id')})
        templates = []
        for ids in _dashboard_lotes(template_ids):
            templates.extend(models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD, 'product.template', 'read', [ids], {'fields': ['id', 'name']}
            ))
        templates_por_id = {template['id']: template for template in templates}
        categ_ids = sorted({p['categ_id'][0] for p in productos if p.get('categ_id')})
        categorias = []
        for ids in _dashboard_lotes(categ_ids):
            categorias.extend(models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD, 'product.category', 'read', [ids],
                {'fields': ['id', 'complete_name']}
            ))
        rutas_categoria = {
            categoria['id']: _dashboard_ruta_categoria(categoria.get('complete_name'))
            for categoria in categorias
        }
        marcas_por_ruta = _dashboard_marcas_por_ruta(rutas_categoria.values())
        attr_ids = sorted({
            atributo_id for producto in productos
            for atributo_id in (producto.get('product_template_attribute_value_ids') or [])
        })
        atributos = []
        for ids in _dashboard_lotes(attr_ids):
            atributos.extend(models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD, 'product.template.attribute.value', 'read', [ids],
                {'fields': ['id', 'attribute_id', 'name']}
            ))
        atributos_por_id = {
            atributo['id']: {
                'atributo': (atributo.get('attribute_id') or [None, ''])[1],
                'valor': atributo.get('name'),
            }
            for atributo in atributos
        }

        moves = []
        for ids in _dashboard_lotes(move_ids):
            moves.extend(models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD, 'account.move', 'read', [ids],
                {'fields': ['id', 'partner_id', 'commercial_partner_id', 'invoice_date']}
            ))
        move_partner = {
            move['id']: (move.get('commercial_partner_id') or move.get('partner_id') or [None])[0]
            for move in moves
        }
        move_fechas = {move['id']: move.get('invoice_date') for move in moves}
        partner_ids = sorted({partner_id for partner_id in move_partner.values() if partner_id})
        partners = []
        for ids in _dashboard_lotes(partner_ids):
            partners.extend(models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD, 'res.partner', 'read', [ids],
                {'fields': ['id', 'name', 'ref', 'parent_id', 'state_id']}
            ))
        partners_por_id = {partner['id']: partner for partner in partners}
        parent_ids = sorted({
            (partner.get('parent_id') or [None])[0] for partner in partners
            if partner.get('parent_id') and (partner.get('parent_id') or [None])[0] not in partners_por_id
        })
        for ids in _dashboard_lotes(parent_ids):
            for partner in models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD, 'res.partner', 'read', [ids],
                {'fields': ['id', 'name', 'ref', 'parent_id', 'state_id']}
            ):
                partners_por_id[partner['id']] = partner
        claves = set()
        for partner in partners_por_id.values():
            ref = (partner.get('ref') or '').strip().upper()
            if not ref and partner.get('parent_id'):
                ref = (partners_por_id.get((partner.get('parent_id') or [None])[0], {}).get('ref') or '').strip().upper()
            if ref:
                claves.add(ref)
        integrales_por_clave = _dashboard_integrales_por_clave(claves)

        universo = []
        for linea in lineas:
            producto = productos_por_id.get((linea.get('product_id') or [None])[0])
            if not producto:
                continue
            atributos_producto = [
                atributos_por_id[atributo_id]
                for atributo_id in (producto.get('product_template_attribute_value_ids') or [])
                if atributo_id in atributos_por_id
            ]
            dimensiones_producto = _dashboard_dimensiones_producto(
                rutas_categoria.get((producto.get('categ_id') or [None])[0], []), atributos_producto,
                templates_por_id.get((producto.get('product_tmpl_id') or [None])[0], {}).get('name'),
                marcas_por_ruta,
            )
            referencia_comercial = _dashboard_aplicar_clasificacion_comercial(
                producto.get('id'), dimensiones_producto,
            )
            dimensiones = {
                nombre: _dashboard_dimension(valor, f'Sin {_DASHBOARD_LABELS[nombre].lower()}')
                for nombre, valor in dimensiones_producto.items()
            }
            partner = partners_por_id.get(move_partner.get((linea.get('move_id') or [None])[0]), {})
            parent = partners_por_id.get((partner.get('parent_id') or [None])[0], {}) if partner.get('parent_id') else {}
            clave_cliente = (partner.get('ref') or parent.get('ref') or '').strip().upper()
            dimensiones['cliente'] = {
                'id': clave_cliente or (f"odoo:{partner['id']}" if partner.get('id') else None),
                'label': (partner.get('name') or parent.get('name') or '').strip() or 'Sin cliente',
                'extra': {'clave': clave_cliente} if clave_cliente else {},
            }
            estado = partner.get('state_id')
            dimensiones['estado'] = _dashboard_dimension(
                estado[1] if isinstance(estado, (list, tuple)) and len(estado) > 1 else None, 'Sin estado'
            )
            integral = integrales_por_clave.get(clave_cliente)
            dimensiones['integral'] = {
                'id': str(integral['id']) if integral else None,
                'label': integral['label'] if integral else 'Sin integral',
            }
            universo.append({
                'dimensiones': dimensiones,
                'pzas': float(linea.get('quantity') or 0),
                'venta': float(linea.get('price_total') or 0),
                'fecha': move_fechas.get((linea.get('move_id') or [None])[0]),
                'referencia_comercial': referencia_comercial,
            })
        _dashboard_universo_cache[clave_cache] = (time.time(), universo)
        return universo


def _dashboard_filtrar_universo(universo: list[dict], filtros: dict) -> list[dict]:
    return [
        fila for fila in universo
        if all(
            _dashboard_coincide_filtro(fila['dimensiones'].get(clave, {}), valores)
            for clave, valores in filtros.items()
            if clave in _DASHBOARD_DIMENSIONES_SOPORTADAS and valores
        )
    ]


def _dashboard_periodos_mensuales(fecha_inicio: str, fecha_fin: str) -> list[dict]:
    """Construye la serie completa, incluidos meses sin movimientos."""
    inicio = _date.fromisoformat(fecha_inicio).replace(day=1)
    fin = _date.fromisoformat(fecha_fin).replace(day=1)
    incluye_varios_anios = inicio.year != fin.year
    periodos = []
    actual = inicio
    while actual <= fin:
        etiqueta = _DASHBOARD_MESES_CORTOS[actual.month - 1]
        if incluye_varios_anios:
            etiqueta = f'{etiqueta} {str(actual.year)[-2:]}'
        periodos.append({
            'periodo': actual.strftime('%Y-%m'), 'label': etiqueta,
            'pzas': 0.0, 'venta': 0.0,
        })
        actual = _date(actual.year + (actual.month == 12), 1 if actual.month == 12 else actual.month + 1, 1)
    return periodos


def _validar_consulta_dashboard(data: dict):
    if not isinstance(data, dict):
        return None, (jsonify({'error': 'El cuerpo JSON debe ser un objeto.'}), 400)

    fecha_inicio = data.get('fecha_inicio')
    fecha_fin = data.get('fecha_fin')
    if not fecha_inicio or not fecha_fin:
        return None, (jsonify({'error': 'Se requieren fecha_inicio y fecha_fin.'}), 400)
    try:
        inicio = _date.fromisoformat(str(fecha_inicio))
        fin = _date.fromisoformat(str(fecha_fin))
    except (TypeError, ValueError):
        return None, (jsonify({'error': 'Las fechas deben usar formato YYYY-MM-DD.'}), 400)
    if inicio > fin:
        return None, (jsonify({'error': 'fecha_inicio no puede ser mayor que fecha_fin.'}), 400)

    vista = data.get('vista', 'cobranza')
    if vista not in ('facturas', 'cobranza'):
        return None, (jsonify({'error': 'Vista no válida.'}), 400)

    filtros_entrada = data.get('filtros', {})
    if not isinstance(filtros_entrada, dict):
        return None, (jsonify({'error': 'filtros debe ser un objeto.'}), 400)
    filtros_desconocidos = set(filtros_entrada) - set(_DASHBOARD_TODAS_DIMENSIONES)
    if filtros_desconocidos:
        return None, (jsonify({
            'error': f"Filtro no válido: {sorted(filtros_desconocidos)[0]}."
        }), 400)
    filtros = {}
    for dimension in _DASHBOARD_TODAS_DIMENSIONES:
        valores = filtros_entrada.get(dimension, [])
        if not isinstance(valores, list):
            return None, (jsonify({'error': f'El filtro {dimension} debe ser una lista.'}), 400)
        if any(valor is None or isinstance(valor, (dict, list)) for valor in valores):
            return None, (jsonify({'error': f'El filtro {dimension} contiene valores inválidos.'}), 400)
        filtros[dimension] = [str(valor).strip() for valor in valores if str(valor).strip()]
        if dimension in _DASHBOARD_DIMENSIONES_PENDIENTES and filtros[dimension]:
            return None, (jsonify({
                'error': f'La dimensión {dimension} aún no está soportada en el dashboard.',
                'dimensiones_pendientes': list(_DASHBOARD_DIMENSIONES_PENDIENTES),
            }), 400)

    desgloses_entrada = data.get('desgloses')
    if desgloses_entrada is None:
        # Compatibilidad temporal con el contrato de un solo desglose.
        desglose_legacy = data.get('desglose')
        desgloses_entrada = [] if desglose_legacy is None else [desglose_legacy]
    if not isinstance(desgloses_entrada, list):
        return None, (jsonify({'error': 'desgloses debe ser una lista.'}), 400)
    desgloses = []
    for dimension in desgloses_entrada:
        if dimension not in _DASHBOARD_TODAS_DIMENSIONES:
            return None, (jsonify({'error': 'desgloses contiene una dimensión no válida.'}), 400)
        if dimension in _DASHBOARD_DIMENSIONES_PENDIENTES:
            return None, (jsonify({
                'error': f'La dimensión {dimension} aún no está soportada en el dashboard.',
                'dimensiones_pendientes': list(_DASHBOARD_DIMENSIONES_PENDIENTES),
            }), 400)
        if dimension not in desgloses:
            desgloses.append(dimension)

    orden = data.get('orden') or {'campo': 'venta', 'direccion': 'desc'}
    if not isinstance(orden, dict):
        return None, (jsonify({'error': 'orden debe ser un objeto.'}), 400)
    campo_orden = orden.get('campo', 'venta')
    direccion_orden = orden.get('direccion', 'desc')
    if campo_orden not in _DASHBOARD_ORDENES:
        return None, (jsonify({'error': 'orden.campo no es válido.'}), 400)
    if direccion_orden not in ('asc', 'desc'):
        return None, (jsonify({'error': 'orden.direccion debe ser asc o desc.'}), 400)

    return {
        'fecha_inicio': inicio.isoformat(), 'fecha_fin': fin.isoformat(),
        'vista': vista, 'filtros': filtros, 'desgloses': desgloses,
        'desglose': desgloses[0] if len(desgloses) == 1 else None,
        'orden': {'campo': campo_orden, 'direccion': direccion_orden},
    }, None


@ventas_bp.route('/dashboard/consulta', methods=['POST'])
@requiere_autenticacion
@requiere_permiso_interno('ventas_monitor', 'ver')
def dashboard_consulta():
    """Consulta dinámica base del Dashboard de Ventas.

    Odoo conserva la fuente financiera. Las dimensiones se enriquecen por
    lotes desde producto, categoría, atributos y partner, sin depender de SKU
    como identificador técnico ni realizar consultas XML-RPC por línea.
    """
    consulta, error = _validar_consulta_dashboard(request.get_json(silent=True))
    if error:
        return error

    try:
        universo = _dashboard_universo_enriquecido(consulta)
        filtradas = _dashboard_filtrar_universo(universo, consulta['filtros'])
        columnas_dimensiones = [
            clave for clave in _DASHBOARD_DIMENSIONES_SOPORTADAS if consulta['filtros'][clave]
        ]
        for desglose in consulta['desgloses']:
            if desglose not in columnas_dimensiones:
                columnas_dimensiones.append(desglose)
        agrupado = {}
        for fila in filtradas:
            llave = tuple(fila['dimensiones'][clave].get('id') for clave in columnas_dimensiones)
            acumulado = agrupado.setdefault(llave, {
                'dimensiones': {clave: fila['dimensiones'][clave] for clave in columnas_dimensiones},
                'pzas': 0.0, 'venta': 0.0, 'referencias_comerciales': set(),
            })
            acumulado['pzas'] += fila['pzas']
            acumulado['venta'] += fila['venta']
            if fila.get('referencia_comercial'):
                acumulado['referencias_comerciales'].add(fila['referencia_comercial'])
        total_pzas = sum(fila['pzas'] for fila in agrupado.values())
        total_venta = sum(fila['venta'] for fila in agrupado.values())
        filas = [{
            'dimensiones': fila['dimensiones'], 'pzas': round(fila['pzas'], 2),
            'venta': round(fila['venta'], 2),
            'participacion': round((fila['venta'] / total_venta * 100) if total_venta else 0, 2),
            'referencia_comercial': _dashboard_referencia_comercial_agregada(fila['referencias_comerciales']),
        } for fila in agrupado.values()]
        filas.sort(key=lambda fila: fila[consulta['orden']['campo']], reverse=consulta['orden']['direccion'] == 'desc')
        ocultar_montos = _debe_ocultar_montos_ventas()
        columnas = [{'key': clave, 'label': _DASHBOARD_LABELS[clave], 'tipo': 'dimension'} for clave in columnas_dimensiones]
        if any(fila['referencia_comercial'] for fila in filas):
            columnas.append({'key': 'referencia_comercial', 'label': 'Referencia comercial', 'tipo': 'informativa'})
        columnas.append({'key': 'pzas', 'label': 'Pzas', 'tipo': 'metrica'})
        if not ocultar_montos:
            columnas.append({'key': 'venta', 'label': 'Venta', 'tipo': 'metrica'})
        columnas.append({'key': 'participacion', 'label': 'Participación', 'tipo': 'metrica'})
        respuesta = {
            'consulta': consulta, 'columnas': columnas, 'filas': filas,
            'totales': {'pzas': round(total_pzas, 2), 'venta': round(total_venta, 2)},
            'permisos': {'ocultar_montos': ocultar_montos},
            'dimensiones_soportadas': list(_DASHBOARD_DIMENSIONES_SOPORTADAS),
            'dimensiones_pendientes': list(_DASHBOARD_DIMENSIONES_PENDIENTES),
        }
        if ocultar_montos:
            respuesta['totales'].pop('venta', None)
            for fila in respuesta['filas']:
                fila.pop('venta', None)
        return jsonify(respuesta), 200

        uid, models, err = get_odoo_models()
        if not uid:
            return jsonify({'error': 'No se pudo conectar a Odoo', 'detail': err}), 500

        domain_line = [
            ['move_id.move_type', '=', 'out_invoice'],
            ['move_id.state', '=', 'posted'],
            *([['move_id.payment_state', 'in', ['paid', 'partial']]]
              if consulta['vista'] == 'cobranza' else []),
            ['move_id.invoice_date', '>=', consulta['fecha_inicio']],
            ['move_id.invoice_date', '<=', consulta['fecha_fin']],
            ['move_id.company_id', '=', ODOO_COMPANY_ID],
            ['display_type', '=', 'product'],
            ['product_id', '!=', False],
        ]
        lineas = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD, 'account.move.line', 'search_read',
            [domain_line],
            {'fields': ['product_id', 'quantity', 'price_total', 'move_id'], 'limit': 0}
        )

        product_ids = sorted({linea['product_id'][0] for linea in lineas if linea.get('product_id')})
        move_ids = sorted({linea['move_id'][0] for linea in lineas if linea.get('move_id')})

        productos = []
        for ids in _dashboard_lotes(product_ids):
            productos.extend(models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD, 'product.product', 'read', [ids],
                {'fields': ['id', 'default_code', 'categ_id', 'product_tmpl_id',
                            'product_template_attribute_value_ids']}
            ))
        productos_por_id = {producto['id']: producto for producto in productos}

        template_ids = sorted({
            producto['product_tmpl_id'][0] for producto in productos
            if producto.get('product_tmpl_id')
        })
        templates = []
        for ids in _dashboard_lotes(template_ids):
            templates.extend(models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD, 'product.template', 'read', [ids],
                {'fields': ['id', 'name']}
            ))
        templates_por_id = {template['id']: template for template in templates}

        categ_ids = sorted({p['categ_id'][0] for p in productos if p.get('categ_id')})
        categorias = []
        for ids in _dashboard_lotes(categ_ids):
            categorias.extend(models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD, 'product.category', 'read', [ids],
                {'fields': ['id', 'complete_name']}
            ))
        rutas_categoria = {
            categoria['id']: _dashboard_ruta_categoria(categoria.get('complete_name'))
            for categoria in categorias
        }
        marcas_por_ruta = _dashboard_marcas_por_ruta(rutas_categoria.values())

        attr_ids = sorted({
            atributo_id for producto in productos
            for atributo_id in (producto.get('product_template_attribute_value_ids') or [])
        })
        atributos = []
        for ids in _dashboard_lotes(attr_ids):
            atributos.extend(models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD, 'product.template.attribute.value', 'read', [ids],
                {'fields': ['id', 'attribute_id', 'name']}
            ))
        atributos_por_id = {
            atributo['id']: {
                'atributo': (atributo.get('attribute_id') or [None, ''])[1],
                'valor': atributo.get('name'),
            }
            for atributo in atributos
        }

        moves = []
        for ids in _dashboard_lotes(move_ids):
            moves.extend(models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD, 'account.move', 'read', [ids],
                {'fields': ['id', 'partner_id', 'commercial_partner_id']}
            ))
        move_partner = {
            move['id']: (
                (move.get('commercial_partner_id') or move.get('partner_id') or [None])[0]
            )
            for move in moves
        }
        partner_ids = sorted({partner_id for partner_id in move_partner.values() if partner_id})
        partners = []
        for ids in _dashboard_lotes(partner_ids):
            partners.extend(models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD, 'res.partner', 'read', [ids],
                {'fields': ['id', 'name', 'ref', 'parent_id', 'state_id']}
            ))
        partners_por_id = {partner['id']: partner for partner in partners}

        parent_ids = sorted({
            (partner.get('parent_id') or [None])[0]
            for partner in partners
            if partner.get('parent_id')
            and (partner.get('parent_id') or [None])[0] not in partners_por_id
        })
        for ids in _dashboard_lotes(parent_ids):
            for partner in models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD, 'res.partner', 'read', [ids],
                {'fields': ['id', 'name', 'ref', 'parent_id', 'state_id']}
            ):
                partners_por_id[partner['id']] = partner

        claves = set()
        for partner in partners_por_id.values():
            ref = (partner.get('ref') or '').strip().upper()
            if not ref and partner.get('parent_id'):
                padre = partners_por_id.get((partner.get('parent_id') or [None])[0], {})
                ref = (padre.get('ref') or '').strip().upper()
            if ref:
                claves.add(ref)
        integrales_por_clave = _dashboard_integrales_por_clave(claves)

        filtradas = []
        for linea in lineas:
            producto_id = (linea.get('product_id') or [None])[0]
            producto = productos_por_id.get(producto_id)
            if not producto:
                continue
            atributos_producto = [
                atributos_por_id[atributo_id]
                for atributo_id in (producto.get('product_template_attribute_value_ids') or [])
                if atributo_id in atributos_por_id
            ]
            categ_id = (producto.get('categ_id') or [None])[0]
            dimensiones = {
                clave: _dashboard_dimension(valor, f'Sin {_DASHBOARD_LABELS[clave].lower()}')
                for clave, valor in _dashboard_dimensiones_producto(
                    rutas_categoria.get(categ_id, []), atributos_producto,
                    templates_por_id.get((producto.get('product_tmpl_id') or [None])[0], {}).get('name'),
                    marcas_por_ruta,
                ).items()
            }

            move_id = (linea.get('move_id') or [None])[0]
            partner = partners_por_id.get(move_partner.get(move_id), {})
            parent = partners_por_id.get((partner.get('parent_id') or [None])[0], {}) if partner.get('parent_id') else {}
            clave_cliente = (partner.get('ref') or parent.get('ref') or '').strip().upper()
            nombre_cliente = (partner.get('name') or parent.get('name') or '').strip() or None
            estado = partner.get('state_id')
            dimensiones['cliente'] = {
                'id': clave_cliente or (f"odoo:{partner['id']}" if partner.get('id') else None),
                'label': nombre_cliente or 'Sin cliente',
                'extra': {'clave': clave_cliente} if clave_cliente else {},
            }
            dimensiones['estado'] = _dashboard_dimension(
                estado[1] if isinstance(estado, (list, tuple)) and len(estado) > 1 else None,
                'Sin estado'
            )
            integral = integrales_por_clave.get(clave_cliente)
            dimensiones['integral'] = {
                'id': str(integral['id']) if integral else None,
                'label': integral['label'] if integral else 'Sin integral',
            }

            if not all(_dashboard_coincide_filtro(dimensiones[clave], valores)
                       for clave, valores in consulta['filtros'].items()
                       if clave in _DASHBOARD_DIMENSIONES_SOPORTADAS):
                continue
            filtradas.append({
                'dimensiones': dimensiones,
                'pzas': float(linea.get('quantity') or 0),
                # price_total es el importe de línea con impuestos incluidos.
                'venta': float(linea.get('price_total') or 0),
            })

        columnas_dimensiones = [
            clave for clave in _DASHBOARD_DIMENSIONES_SOPORTADAS
            if consulta['filtros'][clave]
        ]
        if consulta['desglose'] and consulta['desglose'] not in columnas_dimensiones:
            columnas_dimensiones.append(consulta['desglose'])

        agrupado = {}
        for fila in filtradas:
            llave = tuple(
                fila['dimensiones'][clave].get('id')
                for clave in columnas_dimensiones
            )
            acumulado = agrupado.setdefault(llave, {
                'dimensiones': {clave: fila['dimensiones'][clave] for clave in columnas_dimensiones},
                'pzas': 0.0, 'venta': 0.0,
            })
            acumulado['pzas'] += fila['pzas']
            acumulado['venta'] += fila['venta']

        total_pzas = sum(fila['pzas'] for fila in agrupado.values())
        total_venta = sum(fila['venta'] for fila in agrupado.values())
        filas = []
        for fila in agrupado.values():
            venta = round(fila['venta'], 2)
            filas.append({
                'dimensiones': fila['dimensiones'],
                'pzas': round(fila['pzas'], 2),
                'venta': venta,
                'participacion': round((fila['venta'] / total_venta * 100) if total_venta else 0, 2),
            })

        campo_orden = consulta['orden']['campo']
        filas.sort(
            key=lambda fila: fila[campo_orden],
            reverse=consulta['orden']['direccion'] == 'desc'
        )

        ocultar_montos = _debe_ocultar_montos_ventas()
        columnas = [
            {'key': clave, 'label': _DASHBOARD_LABELS[clave], 'tipo': 'dimension'}
            for clave in columnas_dimensiones
        ]
        columnas.append({'key': 'pzas', 'label': 'Pzas', 'tipo': 'metrica'})
        if not ocultar_montos:
            columnas.append({'key': 'venta', 'label': 'Venta', 'tipo': 'metrica'})
        columnas.append({'key': 'participacion', 'label': 'Participación', 'tipo': 'metrica'})

        respuesta = {
            'consulta': consulta,
            'columnas': columnas,
            'filas': filas,
            'totales': {'pzas': round(total_pzas, 2), 'venta': round(total_venta, 2)},
            'permisos': {'ocultar_montos': ocultar_montos},
            'dimensiones_soportadas': list(_DASHBOARD_DIMENSIONES_SOPORTADAS),
            'dimensiones_pendientes': list(_DASHBOARD_DIMENSIONES_PENDIENTES),
        }
        if ocultar_montos:
            respuesta['totales'].pop('venta', None)
            for fila in respuesta['filas']:
                fila.pop('venta', None)
        return jsonify(respuesta), 200
    except Exception as exc:
        logging.exception('ventas.dashboard_consulta error')
        return jsonify({'error': 'No se pudo cargar la consulta del dashboard'}), 500


@ventas_bp.route('/dashboard/tendencia', methods=['POST'])
@requiere_autenticacion
@requiere_permiso_interno('ventas_monitor', 'ver')
def dashboard_tendencia():
    """Serie mensual del mismo universo enriquecido usado por la consulta."""
    consulta, error = _validar_consulta_dashboard(request.get_json(silent=True))
    if error:
        return error

    try:
        periodos = _dashboard_periodos_mensuales(consulta['fecha_inicio'], consulta['fecha_fin'])
        por_periodo = {periodo['periodo']: periodo for periodo in periodos}
        universo = _dashboard_universo_enriquecido(consulta)
        for fila in _dashboard_filtrar_universo(universo, consulta['filtros']):
            fecha = str(fila.get('fecha') or '')
            periodo = por_periodo.get(fecha[:7])
            if not periodo:
                continue
            periodo['pzas'] += fila['pzas']
            periodo['venta'] += fila['venta']

        for periodo in periodos:
            periodo['pzas'] = round(periodo['pzas'], 2)
            periodo['venta'] = round(periodo['venta'], 2)

        ocultar_montos = _debe_ocultar_montos_ventas()
        respuesta = {
            'periodos': periodos,
            'permisos': {'ocultar_montos': ocultar_montos},
        }
        if ocultar_montos:
            for periodo in respuesta['periodos']:
                periodo.pop('venta', None)
        return jsonify(respuesta), 200
    except Exception:
        logging.exception('ventas.dashboard_tendencia error')
        return jsonify({'error': 'No se pudo cargar la tendencia de ventas'}), 500


@ventas_bp.route('/dashboard/filtros', methods=['POST'])
@requiere_autenticacion
@requiere_permiso_interno('ventas_monitor', 'ver')
def dashboard_filtros():
    """Opciones buscables y dependientes para los filtros del dashboard.

    El filtro de la dimensión que se está consultando se omite temporalmente;
    así el usuario puede ampliar una multiselección sin quedar bloqueado por su
    propia selección actual.
    """
    data = request.get_json(silent=True)
    consulta, error = _validar_consulta_dashboard(data)
    if error:
        return error

    dimension = data.get('dimension')
    if dimension in _DASHBOARD_DIMENSIONES_PENDIENTES:
        return jsonify({
            'error': f'La dimensión {dimension} aún no está soportada en el dashboard.',
            'dimensiones_pendientes': list(_DASHBOARD_DIMENSIONES_PENDIENTES),
        }), 400
    if dimension not in _DASHBOARD_DIMENSIONES_SOPORTADAS:
        return jsonify({'error': 'dimension no es válida.'}), 400

    buscar = data.get('buscar', '')
    if not isinstance(buscar, str):
        return jsonify({'error': 'buscar debe ser un texto.'}), 400
    buscar_normalizado = buscar.strip().casefold()

    limite = data.get('limite', 50)
    if isinstance(limite, bool) or not isinstance(limite, int) or limite < 1:
        return jsonify({'error': 'limite debe ser un entero mayor que cero.'}), 400
    limite = min(limite, 100)

    filtros_contexto = {
        clave: consulta['filtros'][clave]
        for clave in _DASHBOARD_CONTEXTO_FILTROS[dimension]
        if consulta['filtros'][clave]
    }

    try:
        universo = _dashboard_universo_enriquecido(consulta)
        opciones_por_llave = {}
        for fila in _dashboard_filtrar_universo(universo, filtros_contexto):
            opcion = fila['dimensiones'].get(dimension)
            if not opcion or opcion.get('id') is None or not str(opcion.get('label') or '').strip():
                continue
            etiqueta = str(opcion['label']).strip()
            identificador = str(opcion['id']).strip()
            if buscar_normalizado and buscar_normalizado not in etiqueta.casefold() and buscar_normalizado not in identificador.casefold():
                continue
            llave = (identificador, etiqueta.casefold())
            opciones_por_llave.setdefault(llave, {
                'id': identificador, 'label': etiqueta,
                **({'extra': opcion['extra']} if opcion.get('extra') else {}),
            })
        opciones = sorted(opciones_por_llave.values(), key=lambda opcion: (opcion['label'].casefold(), opcion['id'].casefold()))
        return jsonify({
            'dimension': dimension, 'buscar': buscar, 'opciones': opciones[:limite],
            'total': len(opciones), 'limit': limite,
        }), 200

        uid, models, err = get_odoo_models()
        if not uid:
            return jsonify({'error': 'No se pudo conectar a Odoo', 'detail': err}), 500

        domain_line = [
            ['move_id.move_type', '=', 'out_invoice'],
            ['move_id.state', '=', 'posted'],
            *([['move_id.payment_state', 'in', ['paid', 'partial']]]
              if consulta['vista'] == 'cobranza' else []),
            ['move_id.invoice_date', '>=', consulta['fecha_inicio']],
            ['move_id.invoice_date', '<=', consulta['fecha_fin']],
            ['move_id.company_id', '=', ODOO_COMPANY_ID],
            ['display_type', '=', 'product'],
            ['product_id', '!=', False],
        ]
        lineas = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD, 'account.move.line', 'search_read',
            [domain_line], {'fields': ['product_id', 'move_id'], 'limit': 0}
        )

        productos_por_id = {}
        rutas_categoria = {}
        atributos_por_id = {}
        templates_por_id = {}
        marcas_por_ruta = {}
        if requiere_producto:
            product_ids = sorted({
                linea['product_id'][0] for linea in lineas if linea.get('product_id')
            })
            productos = []
            for ids in _dashboard_lotes(product_ids):
                productos.extend(models.execute_kw(
                    ODOO_DB, uid, ODOO_PASSWORD, 'product.product', 'read', [ids],
                    {'fields': ['id', 'categ_id', 'product_tmpl_id', 'product_template_attribute_value_ids']}
                ))
            productos_por_id = {producto['id']: producto for producto in productos}

            template_ids = sorted({
                producto['product_tmpl_id'][0] for producto in productos
                if producto.get('product_tmpl_id')
            })
            templates = []
            for ids in _dashboard_lotes(template_ids):
                templates.extend(models.execute_kw(
                    ODOO_DB, uid, ODOO_PASSWORD, 'product.template', 'read', [ids],
                    {'fields': ['id', 'name']}
                ))
            templates_por_id = {template['id']: template for template in templates}

            categ_ids = sorted({p['categ_id'][0] for p in productos if p.get('categ_id')})
            categorias = []
            for ids in _dashboard_lotes(categ_ids):
                categorias.extend(models.execute_kw(
                    ODOO_DB, uid, ODOO_PASSWORD, 'product.category', 'read', [ids],
                    {'fields': ['id', 'complete_name']}
                ))
            rutas_categoria = {
                categoria['id']: _dashboard_ruta_categoria(categoria.get('complete_name'))
                for categoria in categorias
            }
            marcas_por_ruta = _dashboard_marcas_por_ruta(rutas_categoria.values())

            attr_ids = sorted({
                atributo_id for producto in productos
                for atributo_id in (producto.get('product_template_attribute_value_ids') or [])
            })
            atributos = []
            for ids in _dashboard_lotes(attr_ids):
                atributos.extend(models.execute_kw(
                    ODOO_DB, uid, ODOO_PASSWORD,
                    'product.template.attribute.value', 'read', [ids],
                    {'fields': ['id', 'attribute_id', 'name']}
                ))
            atributos_por_id = {
                atributo['id']: {
                    'atributo': (atributo.get('attribute_id') or [None, ''])[1],
                    'valor': atributo.get('name'),
                }
                for atributo in atributos
            }

        partners_por_id = {}
        move_partner = {}
        integrales_por_clave = {}
        if requiere_partner:
            move_ids = sorted({linea['move_id'][0] for linea in lineas if linea.get('move_id')})
            moves = []
            for ids in _dashboard_lotes(move_ids):
                moves.extend(models.execute_kw(
                    ODOO_DB, uid, ODOO_PASSWORD, 'account.move', 'read', [ids],
                    {'fields': ['id', 'partner_id', 'commercial_partner_id']}
                ))
            move_partner = {
                move['id']: (
                    (move.get('commercial_partner_id') or move.get('partner_id') or [None])[0]
                )
                for move in moves
            }
            partner_ids = sorted({partner_id for partner_id in move_partner.values() if partner_id})
            partners = []
            for ids in _dashboard_lotes(partner_ids):
                partners.extend(models.execute_kw(
                    ODOO_DB, uid, ODOO_PASSWORD, 'res.partner', 'read', [ids],
                    {'fields': ['id', 'name', 'ref', 'parent_id', 'state_id']}
                ))
            partners_por_id = {partner['id']: partner for partner in partners}

            parent_ids = sorted({
                (partner.get('parent_id') or [None])[0]
                for partner in partners
                if partner.get('parent_id')
                and (partner.get('parent_id') or [None])[0] not in partners_por_id
            })
            for ids in _dashboard_lotes(parent_ids):
                for partner in models.execute_kw(
                    ODOO_DB, uid, ODOO_PASSWORD, 'res.partner', 'read', [ids],
                    {'fields': ['id', 'name', 'ref', 'parent_id', 'state_id']}
                ):
                    partners_por_id[partner['id']] = partner

            if dimension == 'integral' or 'integral' in filtros_contexto:
                claves = set()
                for partner in partners_por_id.values():
                    ref = (partner.get('ref') or '').strip().upper()
                    if not ref and partner.get('parent_id'):
                        padre = partners_por_id.get((partner.get('parent_id') or [None])[0], {})
                        ref = (padre.get('ref') or '').strip().upper()
                    if ref:
                        claves.add(ref)
                integrales_por_clave = _dashboard_integrales_por_clave(claves)

        opciones_por_llave = {}
        for linea in lineas:
            dimensiones = {}
            if requiere_producto:
                producto_id = (linea.get('product_id') or [None])[0]
                producto = productos_por_id.get(producto_id)
                if not producto:
                    continue
                atributos_producto = [
                    atributos_por_id[atributo_id]
                    for atributo_id in (producto.get('product_template_attribute_value_ids') or [])
                    if atributo_id in atributos_por_id
                ]
                categ_id = (producto.get('categ_id') or [None])[0]
                dimensiones.update({
                    clave: _dashboard_dimension(valor, f'Sin {_DASHBOARD_LABELS[clave].lower()}')
                    for clave, valor in _dashboard_dimensiones_producto(
                        rutas_categoria.get(categ_id, []), atributos_producto,
                        templates_por_id.get((producto.get('product_tmpl_id') or [None])[0], {}).get('name'),
                        marcas_por_ruta,
                    ).items()
                })

            if requiere_partner:
                move_id = (linea.get('move_id') or [None])[0]
                partner = partners_por_id.get(move_partner.get(move_id), {})
                parent = (partners_por_id.get((partner.get('parent_id') or [None])[0], {})
                          if partner.get('parent_id') else {})
                clave_cliente = (partner.get('ref') or parent.get('ref') or '').strip().upper()
                nombre_cliente = (partner.get('name') or parent.get('name') or '').strip() or None
                estado = partner.get('state_id')
                dimensiones['cliente'] = {
                    'id': clave_cliente or (f"odoo:{partner['id']}" if partner.get('id') else None),
                    'label': nombre_cliente or 'Sin cliente',
                    'extra': {'clave': clave_cliente} if clave_cliente else {},
                }
                dimensiones['estado'] = _dashboard_dimension(
                    estado[1] if isinstance(estado, (list, tuple)) and len(estado) > 1 else None,
                    'Sin estado'
                )
                integral = integrales_por_clave.get(clave_cliente)
                dimensiones['integral'] = {
                    'id': str(integral['id']) if integral else None,
                    'label': integral['label'] if integral else 'Sin integral',
                }

            # Se aplican todos los filtros activos salvo el de la dimensión
            # consultada: eso permite ampliar su propia multiselección.
            if not all(
                _dashboard_coincide_filtro(dimensiones.get(clave, {}), valores)
                for clave, valores in filtros_contexto.items()
            ):
                continue

            opcion = dimensiones.get(dimension)
            if not opcion or opcion.get('id') is None or not str(opcion.get('label') or '').strip():
                continue
            etiqueta = str(opcion['label']).strip()
            identificador = str(opcion['id']).strip()
            if buscar_normalizado and (
                buscar_normalizado not in etiqueta.casefold()
                and buscar_normalizado not in identificador.casefold()
            ):
                continue
            llave = (identificador, etiqueta.casefold())
            opciones_por_llave.setdefault(llave, {
                'id': identificador,
                'label': etiqueta,
                **({'extra': opcion['extra']} if opcion.get('extra') else {}),
            })

        opciones = sorted(
            opciones_por_llave.values(),
            key=lambda opcion: (opcion['label'].casefold(), opcion['id'].casefold())
        )
        total = len(opciones)
        return jsonify({
            'dimension': dimension,
            'buscar': buscar,
            'opciones': opciones[:limite],
            'total': total,
            'limit': limite,
        }), 200
    except Exception:
        logging.exception('ventas.dashboard_filtros error')
        return jsonify({'error': 'No se pudieron cargar las opciones de filtro'}), 500


@ventas_bp.route('/dashboard/categorias', methods=['GET'])
@requiere_autenticacion
@requiere_permiso_interno('ventas_monitor', 'ver')
def dashboard_categorias():
    """Ventas por categoría de producto para el nuevo Dashboard.

    Usa líneas de factura de Odoo para conservar el mismo periodo y filtro de
    cobranza del Monitor. Los importes son ``price_subtotal`` (sin IVA), por lo
    que nunca se mezclan con ``account.move.amount_total`` (con IVA).
    """
    fecha_inicio = request.args.get('fecha_inicio')
    fecha_fin = request.args.get('fecha_fin')
    vista = request.args.get('vista', 'cobranza')
    categoria_filtro = (request.args.get('categoria') or '').strip()

    if not fecha_inicio or not fecha_fin:
        return jsonify({'error': 'Se requieren fecha_inicio y fecha_fin'}), 400
    if vista not in ('facturas', 'cobranza'):
        return jsonify({'error': 'Vista no válida'}), 400

    try:
        uid, models, err = get_odoo_models()
        if not uid:
            return jsonify({'error': 'No se pudo conectar a Odoo', 'detail': err}), 500

        domain_line = [
            ['move_id.move_type', '=', 'out_invoice'],
            ['move_id.state', '=', 'posted'],
            *([['move_id.payment_state', 'in', ['paid', 'partial']]] if vista == 'cobranza' else []),
            ['move_id.invoice_date', '>=', fecha_inicio],
            ['move_id.invoice_date', '<=', fecha_fin],
            ['move_id.company_id', '=', ODOO_COMPANY_ID],
            ['display_type', '=', 'product'],
            ['product_id', '!=', False],
            ['quantity', '>', 0],
        ]
        lineas = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD,
            'account.move.line', 'search_read', [domain_line],
            {'fields': ['product_id', 'quantity', 'price_subtotal'], 'limit': 0})

        product_ids = list({l['product_id'][0] for l in lineas if l.get('product_id')})
        product_categ = {}
        # sale.report evita depender del acceso directo a producto; se conserva
        # el respaldo de product.product que usa la sincronización de monitor.
        for inicio in range(0, len(product_ids), 500):
            ids = product_ids[inicio:inicio + 500]
            for fila in models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'sale.report', 'search_read',
                [[['product_id', 'in', ids]]], {'fields': ['product_id', 'categ_id'], 'limit': 0}):
                if fila.get('product_id') and fila.get('categ_id'):
                    product_categ.setdefault(fila['product_id'][0], fila['categ_id'][0])
        faltantes = [pid for pid in product_ids if pid not in product_categ]
        if faltantes:
            for inicio in range(0, len(faltantes), 500):
                for prod in models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'product.product', 'read',
                    [faltantes[inicio:inicio + 500]], {'fields': ['id', 'categ_id']}):
                    if prod.get('categ_id'):
                        product_categ[prod['id']] = prod['categ_id'][0]

        categoria_nombre = {}
        categ_ids = list(set(product_categ.values()))
        for inicio in range(0, len(categ_ids), 500):
            for categ in models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'product.category', 'read',
                [categ_ids[inicio:inicio + 500]], {'fields': ['id', 'complete_name']}):
                nombre = (categ.get('complete_name') or '').strip()
                if nombre.startswith('All / '): nombre = nombre[6:].strip()
                elif nombre.startswith('All/'): nombre = nombre[4:].strip()
                categoria_nombre[categ['id']] = nombre or 'Sin categoría'

        acumulado = {}
        for linea in lineas:
            product = linea.get('product_id')
            if not product:
                continue
            nombre = categoria_nombre.get(product_categ.get(product[0]), 'Sin categoría')
            if categoria_filtro and nombre != categoria_filtro:
                continue
            dato = acumulado.setdefault(nombre, {'unidades': 0.0, 'venta': 0.0})
            dato['unidades'] += float(linea.get('quantity') or 0)
            dato['venta'] += float(linea.get('price_subtotal') or 0)

        total_venta = sum(d['venta'] for d in acumulado.values())
        categorias = sorted([
            {'categoria': nombre, 'unidades': round(d['unidades'], 2), 'venta': round(d['venta'], 2),
             'participacion': round((d['venta'] / total_venta * 100) if total_venta else 0, 2)}
            for nombre, d in acumulado.items()], key=lambda d: d['venta'], reverse=True)
        respuesta = {'total_venta': round(total_venta, 2),
                     'total_unidades': round(sum(d['unidades'] for d in acumulado.values()), 2),
                     'categorias': categorias}
        if _debe_ocultar_montos_ventas():
            respuesta.pop('total_venta', None)
            for fila in respuesta['categorias']:
                fila.pop('venta', None)
        return jsonify(respuesta), 200
    except Exception as e:
        logging.exception('ventas.dashboard_categorias error')
        return jsonify({'error': 'No se pudo cargar la venta por categoría'}), 500


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
@requiere_autenticacion
@requiere_permiso_interno('ventas_monitor', 'ver')
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
@requiere_autenticacion
@requiere_permiso_interno('ventas_monitor', 'ver')
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
@requiere_autenticacion
@requiere_permiso_interno('ventas_monitor', 'ver')
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
@requiere_autenticacion
@requiere_permiso_interno('ventas_monitor', 'ver')
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
@requiere_autenticacion
@requiere_permiso_interno('ventas_monitor', 'ver')
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

