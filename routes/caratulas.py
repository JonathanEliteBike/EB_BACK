from __future__ import annotations
from flask import Blueprint, jsonify, request, Response, g
from db_conexion import obtener_conexion
from decimal import Decimal
from datetime import datetime
import json
import os
import re
import redis as _redis_lib
from utils.email_utils import crear_cuerpo_email
from utils.odoo_utils import get_odoo_models, ODOO_DB, ODOO_PASSWORD
from utils.temporada_utils import etiqueta_temporada
import logging
import traceback
from copy import deepcopy
from utils.auth_decorators import (
    requiere_autenticacion,
    requiere_modulo,
)
from services.politica_montos_service import PoliticaMontosService

caratulas_bp = Blueprint('caratulas', __name__, url_prefix='')

# ── Caché Redis para detalle-compras-odoo (TTL = 30 min) ─────────────────────
_ODOO_PEDIDOS_TTL = 1800         # segundos (30 min)
_REDIS_URL = os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')
try:
    _redis = _redis_lib.from_url(_REDIS_URL, decode_responses=True, socket_connect_timeout=2)
    _redis.ping()
    logging.info('Redis cache activo: %s', _REDIS_URL)
except Exception as _re:
    _redis = None
    logging.warning('Redis no disponible, cache desactivado: %s', _re)


_WARM_WORKERS = 4   # peticiones paralelas a Odoo (no subir de 5 para no saturar)


def _contexto_cliente_autenticado(cursor):
    """Obtiene el cliente real del portal para roles 2 y 3.

    El cliente y su grupo son datos de la cuenta autenticada; nunca se toman
    de los parámetros de consulta. El rol 1 conserva las consultas de
    administración global.
    """
    usuario_id = g.usuario_actual["id"]
    cursor.execute(
        """
        SELECT u.rol_id, c.id, c.clave, c.nombre_cliente, c.id_grupo
        FROM usuarios u
        LEFT JOIN clientes c ON c.id = u.cliente_id
        WHERE u.id = %s AND u.activo = 1
        """,
        (usuario_id,),
    )
    contexto = cursor.fetchone()
    if not contexto:
        raise PermissionError("Usuario no encontrado o inactivo.")
    if contexto["rol_id"] == 1:
        return None
    if contexto["rol_id"] not in (2, 3) or not contexto["id"] or not contexto["clave"]:
        raise PermissionError("El usuario no tiene un cliente válido asignado.")
    return contexto


def _clave_resumen_contexto(contexto):
    """Devuelve la clave segura de la carátula: grupo integral o cliente."""
    if contexto and contexto.get("id_grupo"):
        return f"Integral {contexto['id_grupo']}"
    return contexto["clave"] if contexto else None


def _redactar_montos_detalle_compras(resultado, filas, meta):
    """Copia la respuesta sin montos internos para usuarios sin ``ver_montos``.

    Redis conserva los datos completos; la redacción se aplica por respuesta,
    después de recuperar o construir el dato bruto y antes de ``jsonify``.
    """
    resultado_limpio = deepcopy(resultado)
    filas_limpias = deepcopy(filas)
    meta_limpia = deepcopy(meta)

    for pedido in resultado_limpio:
        pedido.pop('monto_total', None)
        for linea in pedido.get('lineas') or []:
            linea.pop('precio_unitario', None)
            linea.pop('total_linea', None)
            linea.pop('total_entregado_linea', None)

    for fila in filas_limpias:
        fila.pop('precio_unitario', None)
        fila.pop('total', None)
        fila.pop('total_entregado', None)

    meta_limpia.pop('avance_previo', None)
    return resultado_limpio, filas_limpias, meta_limpia


_CAMPOS_MONETARIOS_CARATULA = {
    'compra_minima_anual', 'compra_minima_inicial', 'acumulado_anticipado',
    'compromiso_scott', 'avance_global_scott',
    'compromiso_apparel_syncros_vittoria', 'avance_global_apparel_syncros_vittoria',
    'compromiso_jul_ago', 'avance_jul_ago',
    'compromiso_sep_oct', 'avance_sep_oct',
    'compromiso_nov_dic', 'avance_nov_dic',
    'compromiso_ene_feb', 'avance_ene_feb',
    'compromiso_mar_abr', 'avance_mar_abr',
    'compromiso_may_jun', 'avance_may_jun',
    'compromiso_jul_ago_app', 'avance_jul_ago_app',
    'compromiso_sep_oct_app', 'avance_sep_oct_app',
    'compromiso_nov_dic_app', 'avance_nov_dic_app',
    'compromiso_ene_feb_app', 'avance_ene_feb_app',
    'compromiso_mar_abr_app', 'avance_mar_abr_app',
    'compromiso_may_jun_app', 'avance_may_jun_app',
}


def _redactar_montos_caratula(filas):
    """Elimina importes de Carátula para un usuario hijo sin capacidad.

    Los porcentajes se conservan porque no exponen los valores monetarios y la
    pantalla puede seguir mostrando el avance sin recibir los montos brutos.
    """
    filas_limpias = deepcopy(filas)
    for fila in filas_limpias:
        for campo in _CAMPOS_MONETARIOS_CARATULA:
            fila.pop(campo, None)
    return filas_limpias


def _debe_ocultar_montos_caratula():
    return PoliticaMontosService.debe_ocultar_montos(
        g.usuario_actual['id'], 'caratula_distribuidor'
    )


def _debe_ocultar_montos_detalle_compras():
    return PoliticaMontosService.debe_ocultar_montos(
        g.usuario_actual['id'], 'detalle_compras'
    )

# ─────────────────────────────────────────────────────────────────────────────
# CARÁTULAS MY27 — cálculo maestro
# ─────────────────────────────────────────────────────────────────────────────
#
# Fuente de verdad para la temporada vigente:
# - previo: universo de carátula, metas y compromisos.
# - clientes: estado vigente, EVAC, nivel, grupo y ventana f_inicio/f_fin.
# - monitor: ventas/acumulados reales.
# - clientes_multimarcas: catálogo Multimarcas vigente.
#
# Reglas:
# 1) Cliente normal activo: cuenta una vez.
# 2) Integral completo en un solo EVAC:
#       Global -> fila Integral N una vez.
#       EVAC   -> fila Integral N una vez.
# 3) Integral repartido entre A y B:
#       Global -> fila Integral N una vez.
#       EVAC   -> sucursales reales del EVAC con su meta individual.
# 4) Apparel del integral multi-EVAC se reparte proporcionalmente a la
#    meta anual asignada a cada EVAC y luego a sus sucursales.
# 5) Bicicletas = Meta General - Apparel/Syncros/Vittoria.
#
# No se hardcodean claves de sucursales ni números de integrales.
_NIVELES_CARATULA = {
    "Distribuidor",
    "Partner",
    "Partner Elite",
    "Partner Elite Plus",
}
_NIVELES_CATEGORIA = {
    "Partner",
    "Partner Elite",
    "Partner Elite Plus",
}


def _caratula_decimal(valor) -> Decimal:
    if valor in (None, ""):
        return Decimal("0")
    if isinstance(valor, Decimal):
        return valor
    return Decimal(str(valor))


def _caratula_money(valor: Decimal) -> Decimal:
    return valor.quantize(Decimal("0.01"))


def _caratula_json_fila(fila: dict) -> dict:
    """Copia serializable manteniendo el mismo shape que previo."""
    salida = {}
    for key, value in fila.items():
        if key.startswith("_"):
            continue
        if isinstance(value, Decimal):
            salida[key] = float(value)
        elif hasattr(value, "strftime"):
            salida[key] = value.strftime("%Y-%m-%d")
        else:
            salida[key] = value
    return salida


def _caratula_cargar_base_my27(cursor):
    """
    Carga previo y el estado vigente de clientes.
    Las filas integrales no tienen cliente equivalente y se conservan por
    es_integral/grupo_integral.
    """
    cursor.execute("""
        SELECT
            p.*,
            c.id       AS _cliente_id_actual,
            c.activo   AS _activo_actual,
            c.evac     AS _evac_actual,
            c.nivel    AS _nivel_actual,
            c.id_grupo AS _id_grupo_actual
        FROM previo p
        LEFT JOIN clientes c
          ON c.clave = p.clave
         AND COALESCE(p.es_integral, 0) = 0
    """)
    return cursor.fetchall()


def _caratula_repartir_proporcional(total: Decimal, items: list[dict], campo_peso: str):
    """
    Reparte un monto a centavos y garantiza que la suma sea EXACTAMENTE total.
    El último registro absorbe únicamente el residuo de redondeo.
    """
    if not items:
        return {}

    total = _caratula_money(total)
    pesos = [_caratula_decimal(item.get(campo_peso)) for item in items]
    suma_pesos = sum(pesos, Decimal("0"))

    if suma_pesos <= 0:
        return {id(item): Decimal("0.00") for item in items}

    asignaciones = {}
    acumulado = Decimal("0.00")

    for index, (item, peso) in enumerate(zip(items, pesos)):
        if index == len(items) - 1:
            monto = total - acumulado
        else:
            monto = _caratula_money(total * peso / suma_pesos)
            acumulado += monto
        asignaciones[id(item)] = monto

    return asignaciones


def _caratula_construir_contribuciones_my27(filas):
    """
    Construye las tres vistas DESDE LA MISMA BASE:
      - global
      - A
      - B

    Devuelve filas compatibles con los componentes actuales.
    """
    integrales_por_grupo = {}
    miembros_por_grupo = {}
    normales = []

    for original in filas:
        fila = dict(original)
        es_integral = int(fila.get("es_integral") or 0) == 1

        if es_integral:
            grupo = fila.get("grupo_integral")
            nivel = fila.get("nivel")
            if grupo is not None and nivel in _NIVELES_CARATULA:
                integrales_por_grupo[int(grupo)] = fila
            continue

        # MY27 actual: solo clientes existentes y activos.
        if not fila.get("_cliente_id_actual"):
            continue
        if int(fila.get("_activo_actual") or 0) != 1:
            continue

        evac_actual = fila.get("_evac_actual")
        nivel_actual = fila.get("_nivel_actual") or fila.get("nivel")

        # Multimarcas / registros sin nivel o sin EVAC normal no entran aquí.
        # Global los sigue manejando por su flujo Multimarcas de producción.
        if evac_actual not in ("A", "B"):
            continue
        if nivel_actual not in _NIVELES_CARATULA:
            continue

        fila["evac"] = evac_actual
        fila["nivel"] = nivel_actual

        grupo_actual = fila.get("_id_grupo_actual")
        if grupo_actual is None:
            normales.append(fila)
        else:
            miembros_por_grupo.setdefault(int(grupo_actual), []).append(fila)

    contrib_global = [dict(fila) for fila in normales]
    contrib_a = [dict(fila) for fila in normales if fila.get("evac") == "A"]
    contrib_b = [dict(fila) for fila in normales if fila.get("evac") == "B"]

    grupos_ids = set(miembros_por_grupo) | set(integrales_por_grupo)

    for grupo_id in sorted(grupos_ids):
        miembros = miembros_por_grupo.get(grupo_id, [])
        integral = integrales_por_grupo.get(grupo_id)

        # Si ya no hay ningún miembro activo del grupo, no aporta a MY27.
        if not miembros:
            continue

        # Fallback conservador si faltara la fila Integral N.
        if integral is None:
            contrib_global.extend(dict(m) for m in miembros)
            contrib_a.extend(dict(m) for m in miembros if m.get("evac") == "A")
            contrib_b.extend(dict(m) for m in miembros if m.get("evac") == "B")
            continue

        # GLOBAL: el integral siempre cuenta una sola vez.
        fila_integral_global = dict(integral)
        contrib_global.append(fila_integral_global)

        miembros_a = [m for m in miembros if m.get("evac") == "A"]
        miembros_b = [m for m in miembros if m.get("evac") == "B"]
        evacs_presentes = int(bool(miembros_a)) + int(bool(miembros_b))

        # Integral completo dentro de un solo EVAC:
        # ese EVAC recibe la fila integral una sola vez.
        if evacs_presentes == 1:
            evac = "A" if miembros_a else "B"
            fila_integral_evac = dict(integral)
            fila_integral_evac["evac"] = evac

            if evac == "A":
                contrib_a.append(fila_integral_evac)
            else:
                contrib_b.append(fila_integral_evac)
            continue

        # Integral multi-EVAC:
        # primero se reparte Apparel entre EVAC A/B según la suma de metas
        # individuales del escenario vigente.
        apparel_integral = _caratula_decimal(
            integral.get("compromiso_apparel_syncros_vittoria")
        )

        grupos_evac = []
        if miembros_a:
            grupos_evac.append({
                "evac": "A",
                "miembros": miembros_a,
                "peso": sum(
                    (_caratula_decimal(m.get("compra_minima_anual")) for m in miembros_a),
                    Decimal("0"),
                ),
            })
        if miembros_b:
            grupos_evac.append({
                "evac": "B",
                "miembros": miembros_b,
                "peso": sum(
                    (_caratula_decimal(m.get("compra_minima_anual")) for m in miembros_b),
                    Decimal("0"),
                ),
            })

        # Reparto a centavos exacto entre EVACs.
        total_peso_evacs = sum((g["peso"] for g in grupos_evac), Decimal("0"))
        apparel_por_evac = {}
        acumulado_apparel = Decimal("0.00")

        for index, grupo_evac in enumerate(grupos_evac):
            if index == len(grupos_evac) - 1:
                monto_evac = _caratula_money(apparel_integral) - acumulado_apparel
            elif total_peso_evacs > 0:
                monto_evac = _caratula_money(
                    apparel_integral * grupo_evac["peso"] / total_peso_evacs
                )
                acumulado_apparel += monto_evac
            else:
                monto_evac = Decimal("0.00")
            apparel_por_evac[grupo_evac["evac"]] = monto_evac

        # Luego se reparte el monto del EVAC entre sus sucursales, también exacto.
        for grupo_evac in grupos_evac:
            evac = grupo_evac["evac"]
            miembros_evac = sorted(
                grupo_evac["miembros"],
                key=lambda x: str(x.get("clave") or ""),
            )

            reparto_miembros = _caratula_repartir_proporcional(
                apparel_por_evac.get(evac, Decimal("0.00")),
                miembros_evac,
                "compra_minima_anual",
            )

            for miembro in miembros_evac:
                fila_evac = dict(miembro)
                meta_miembro = _caratula_decimal(
                    fila_evac.get("compra_minima_anual")
                )
                apparel_miembro = reparto_miembros.get(
                    id(miembro), Decimal("0.00")
                )

                # Solo se derivan estos dos campos en la respuesta.
                # NO se altera la BD.
                fila_evac["compromiso_apparel_syncros_vittoria"] = float(
                    _caratula_money(apparel_miembro)
                )
                fila_evac["compromiso_scott"] = float(
                    _caratula_money(meta_miembro - apparel_miembro)
                )

                if evac == "A":
                    contrib_a.append(fila_evac)
                else:
                    contrib_b.append(fila_evac)

    return {
        "global": contrib_global,
        "A": contrib_a,
        "B": contrib_b,
    }



def _caratula_obtener_contribuciones_my27(
    fecha_desde=None,
    fecha_hasta=None,
):
    """
    Construye Global/A/B y reemplaza SOLO los acumulados de las filas actuales
    por ventas reales de monitor.

    Sin fechas solicitadas conserva exactamente la lógica MY27 vigente:
      - f_inicio individual puede ser anterior al 01/jul;
      - f_fin individual limita el acumulado;
      - NULL usa el rango general MY27.

    Con fechas solicitadas aplica la intersección:
      inicio = MAX(fecha_desde, f_inicio o inicio MY27)
      fin    = MIN(fecha_hasta, f_fin o fin MY27, hoy)

    No modifica previo ni ninguna tabla.
    """
    conexion = None
    cursor = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)

        filas = _caratula_cargar_base_my27(cursor)
        contribuciones = _caratula_construir_contribuciones_my27(filas)

        fecha_inicio_my27, fecha_fin_my27 = _caratula_rango_my27(cursor)

        acumulados, grupos = _caratula_acumulados_normales_desde_monitor(
            cursor,
            fecha_inicio_my27,
            fecha_fin_my27,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
        )

        _caratula_aplicar_acumulados_reales(
            contribuciones,
            acumulados,
            grupos,
        )

        return contribuciones
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

def _caratula_resumen_acumulado_lineas(filas):
    """
    Resume únicamente acumulados que ya existen en previo.
    No consulta Odoo ni modifica la BD.

    BICICLETAS base = SCOTT + BOLD.
    La parte MEGAMO se agrega después desde monitor para poder validarla
    por separado antes de tocar el frontend.
    """
    general = Decimal("0")
    bicicletas_base = Decimal("0")
    apparel_syncros_vittoria = Decimal("0")

    for fila in filas:
        general += _caratula_decimal(fila.get("acumulado_anticipado"))
        bicicletas_base += (
            _caratula_decimal(fila.get("avance_global_scott"))
            + _caratula_decimal(fila.get("acumulado_bold"))
        )
        apparel_syncros_vittoria += _caratula_decimal(
            fila.get("avance_global_apparel_syncros_vittoria")
        )

    return {
        "general": _caratula_money(general),
        "bicicletas_base": _caratula_money(bicicletas_base),
        "apparel_syncros_vittoria": _caratula_money(apparel_syncros_vittoria),
    }


def _caratula_rango_my27(cursor):
    """
    MY27 actual: julio -> junio.
    Se calcula con la fecha del servidor MySQL para no depender del reloj
    del navegador ni de una fecha hardcodeada.
    """
    cursor.execute("""
        SELECT
            CASE
                WHEN MONTH(CURDATE()) >= 7
                    THEN STR_TO_DATE(CONCAT(YEAR(CURDATE()), '-07-01'), '%Y-%m-%d')
                ELSE STR_TO_DATE(CONCAT(YEAR(CURDATE()) - 1, '-07-01'), '%Y-%m-%d')
            END AS fecha_inicio,
            CASE
                WHEN MONTH(CURDATE()) >= 7
                    THEN STR_TO_DATE(CONCAT(YEAR(CURDATE()) + 1, '-06-30'), '%Y-%m-%d')
                ELSE STR_TO_DATE(CONCAT(YEAR(CURDATE()), '-06-30'), '%Y-%m-%d')
            END AS fecha_fin
    """)
    row = cursor.fetchone() or {}
    return row.get("fecha_inicio"), row.get("fecha_fin")


def _caratula_parse_fecha_opcional(valor, nombre):
    """Convierte YYYY-MM-DD a date. Vacío/None se interpreta como no enviado."""
    if valor in (None, ""):
        return None

    valor = str(valor).strip()
    if not valor:
        return None

    try:
        return datetime.strptime(valor, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(
            f"{nombre} debe tener formato YYYY-MM-DD."
        ) from exc


def _caratula_leer_rango_solicitado():
    """
    Lee fecha_desde/fecha_hasta del query string.

    - Sin parámetros: devuelve (None, None) y conserva el comportamiento MY27 actual.
    - Con parámetros: exige ambos y valida que desde <= hasta.
    """
    desde_raw = request.args.get("fecha_desde")
    hasta_raw = request.args.get("fecha_hasta")

    fecha_desde = _caratula_parse_fecha_opcional(desde_raw, "fecha_desde")
    fecha_hasta = _caratula_parse_fecha_opcional(hasta_raw, "fecha_hasta")

    if (fecha_desde is None) != (fecha_hasta is None):
        raise ValueError(
            "Debes enviar fecha_desde y fecha_hasta juntos."
        )

    if fecha_desde and fecha_hasta and fecha_desde > fecha_hasta:
        raise ValueError(
            "fecha_desde no puede ser posterior a fecha_hasta."
        )

    return fecha_desde, fecha_hasta


def _caratula_rango_general_efectivo(
    fecha_inicio_my27,
    fecha_fin_my27,
    fecha_desde=None,
    fecha_hasta=None,
):
    """
    Rango para ventas SIN ventana individual (Multimarcas/no registradas).
    Nunca sale del calendario MY27.
    """
    inicio = fecha_inicio_my27
    fin = fecha_fin_my27

    if fecha_desde is not None:
        inicio = max(inicio, fecha_desde)

    if fecha_hasta is not None:
        fin = min(fin, fecha_hasta)

    return inicio, fin


def _caratula_condicion_apparel_sql(alias="m"):
    """
    Clasificación exclusiva de APPAREL / SYNCROS / VITTORIA.
    Una línea que entra aquí NO debe volver a entrar en BICICLETAS.
    """
    return f"""
        (
            UPPER(TRIM(COALESCE({alias}.apparel, ''))) IN ('SI', 'YES')
            OR UPPER(TRIM(COALESCE({alias}.marca, ''))) IN ('SYNCROS', 'VITTORIA')
        )
    """


def _caratula_condicion_megamo_sql(alias="m"):
    return f"""
        (
            UPPER(TRIM(COALESCE({alias}.marca, ''))) = 'MEGAMO'
            OR UPPER(TRIM(COALESCE({alias}.subcategoria, ''))) = 'MEGAMO'
            OR UPPER(COALESCE({alias}.categoria_producto, '')) LIKE '%MEGAMO%'
        )
    """



def _caratula_normal_en_previo_sql(cliente_alias="c"):
    """Universo operativo de carátula: la clave DEBE existir en previo.

    `clientes` solo aporta el estado vigente (activo, EVAC, nivel, grupo y fechas).
    Esto evita que clientes de prueba/auxiliares que existan en `clientes` pero no
    estén dados de alta en la carátula entren por accidente al acumulado.
    """
    return f"""
        EXISTS (
            SELECT 1
            FROM previo p_scope
            WHERE COALESCE(p_scope.es_integral, 0) = 0
              AND TRIM(UPPER(p_scope.clave)) = TRIM(UPPER({cliente_alias}.clave))
              AND COALESCE(
                    NULLIF(TRIM({cliente_alias}.nivel), ''),
                    p_scope.nivel
                  ) IN ('Distribuidor', 'Partner', 'Partner Elite', 'Partner Elite Plus')
        )
    """


def _caratula_clasificacion_sql(alias="m"):
    """Clasificación EXCLUSIVA de una venta; cada línea cae en una sola categoría."""
    return f"""
        CASE
            WHEN UPPER(TRIM(COALESCE({alias}.marca, ''))) = 'VITTORIA'
              OR UPPER(COALESCE({alias}.categoria_producto, '')) LIKE 'VITTORIA%'
                THEN 'VITTORIA'

            WHEN UPPER(TRIM(COALESCE({alias}.marca, ''))) = 'SYNCROS'
              OR UPPER(COALESCE({alias}.categoria_producto, '')) LIKE 'SYNCROS%'
                THEN 'SYNCROS'

            WHEN UPPER(TRIM(COALESCE({alias}.marca, ''))) = 'BOLD'
              OR UPPER(COALESCE({alias}.categoria_producto, '')) LIKE 'BOLD%'
                THEN 'BOLD'

            WHEN UPPER(TRIM(COALESCE({alias}.apparel, ''))) IN ('SI', 'YES')
              OR UPPER(COALESCE({alias}.categoria_producto, '')) LIKE 'SCOTT / APPAREL%'
                THEN 'APPAREL'

            WHEN UPPER(TRIM(COALESCE({alias}.marca, ''))) = 'MEGAMO'
              OR UPPER(TRIM(COALESCE({alias}.subcategoria, ''))) = 'MEGAMO'
              OR UPPER(COALESCE({alias}.categoria_producto, '')) LIKE '%MEGAMO%'
                THEN 'MEGAMO'

            WHEN UPPER(TRIM(COALESCE({alias}.marca, ''))) = 'SCOTT'
              OR UPPER(COALESCE({alias}.categoria_producto, '')) LIKE 'SCOTT%'
                THEN 'SCOTT'

            ELSE 'OTROS'
        END
    """

def _caratula_acumulados_normales_desde_monitor(
    cursor,
    fecha_inicio_my27,
    fecha_fin_my27,
    fecha_desde=None,
    fecha_hasta=None,
):
    """Ventas reales de CLIENTES NORMALES que pertenecen a la carátula.

    Reglas:
      - cliente activo;
      - EVAC vigente A/B;
      - la clave debe existir como fila normal en previo;
      - sin filtro manual conserva f_inicio/f_fin actual;
      - con filtro manual intersecta rango solicitado con ventana individual;
      - nunca incluye facturas posteriores a hoy;
      - clasificación exclusiva para evitar dobles conteos.
    """
    scope = _caratula_normal_en_previo_sql("c")
    clasificacion = _caratula_clasificacion_sql("m")

    cursor.execute(f"""
        SELECT
            x.clave,
            x.evac,
            x.nivel,
            x.id_grupo,
            ROUND(SUM(x.venta_total), 2) AS general,
            ROUND(SUM(CASE WHEN x.clasificacion = 'SCOTT' THEN x.venta_total ELSE 0 END), 2) AS scott,
            ROUND(SUM(CASE WHEN x.clasificacion = 'BOLD' THEN x.venta_total ELSE 0 END), 2) AS bold,
            ROUND(SUM(CASE WHEN x.clasificacion = 'MEGAMO' THEN x.venta_total ELSE 0 END), 2) AS megamo,
            ROUND(SUM(CASE WHEN x.clasificacion = 'APPAREL' THEN x.venta_total ELSE 0 END), 2) AS apparel,
            ROUND(SUM(CASE WHEN x.clasificacion = 'SYNCROS' THEN x.venta_total ELSE 0 END), 2) AS syncros,
            ROUND(SUM(CASE WHEN x.clasificacion = 'VITTORIA' THEN x.venta_total ELSE 0 END), 2) AS vittoria,
            ROUND(SUM(CASE WHEN x.clasificacion IN ('APPAREL','SYNCROS','VITTORIA') THEN x.venta_total ELSE 0 END), 2) AS apparel_syncros_vittoria,
            ROUND(SUM(CASE WHEN x.clasificacion = 'OTROS' THEN x.venta_total ELSE 0 END), 2) AS otros
        FROM (
            SELECT
                c.clave,
                c.evac,
                c.nivel AS nivel,
                c.id_grupo,
                COALESCE(m.venta_total, 0) AS venta_total,
                {clasificacion} AS clasificacion
            FROM monitor m
            INNER JOIN clientes c
                ON TRIM(UPPER(c.clave)) = TRIM(UPPER(m.contacto_referencia))
            WHERE c.activo = 1
              AND c.evac IN ('A', 'B')
              AND {scope}
              AND m.fecha_factura >= CASE
                    WHEN %s IS NULL
                        THEN COALESCE(c.f_inicio, %s)
                    ELSE GREATEST(
                        %s,
                        COALESCE(c.f_inicio, %s)
                    )
                  END
              AND m.fecha_factura < DATE_ADD(
                    LEAST(
                        COALESCE(c.f_fin, %s),
                        COALESCE(%s, %s),
                        CURDATE()
                    ),
                    INTERVAL 1 DAY
                  )
        ) x
        GROUP BY x.clave, x.evac, x.nivel, x.id_grupo
    """, (
        fecha_desde,
        fecha_inicio_my27,
        fecha_desde,
        fecha_inicio_my27,
        fecha_fin_my27,
        fecha_hasta,
        fecha_fin_my27,
    ))

    acumulados = {}
    for row in cursor.fetchall():
        clave = str(row.get("clave") or "").strip().upper()
        evac = str(row.get("evac") or "").strip().upper()
        if not clave or evac not in ("A", "B"):
            continue

        acumulados[clave] = {
            "general": _caratula_money(_caratula_decimal(row.get("general"))),
            "scott": _caratula_money(_caratula_decimal(row.get("scott"))),
            "bold": _caratula_money(_caratula_decimal(row.get("bold"))),
            "megamo": _caratula_money(_caratula_decimal(row.get("megamo"))),
            "apparel": _caratula_money(_caratula_decimal(row.get("apparel"))),
            "syncros": _caratula_money(_caratula_decimal(row.get("syncros"))),
            "vittoria": _caratula_money(_caratula_decimal(row.get("vittoria"))),
            "apparel_syncros_vittoria": _caratula_money(
                _caratula_decimal(row.get("apparel_syncros_vittoria"))
            ),
            "otros": _caratula_money(_caratula_decimal(row.get("otros"))),
            "evac": evac,
            "nivel": row.get("nivel"),
            "id_grupo": row.get("id_grupo"),
        }

    # La membresía del grupo NO depende de que el cliente haya vendido.
    cursor.execute(f"""
        SELECT DISTINCT c.clave, c.id_grupo
        FROM clientes c
        WHERE c.activo = 1
          AND c.evac IN ('A', 'B')
          AND c.id_grupo IS NOT NULL
          AND {scope}
    """)

    grupos = {}
    for row in cursor.fetchall():
        clave = str(row.get("clave") or "").strip().upper()
        grupo = row.get("id_grupo")
        if clave and grupo is not None:
            grupos.setdefault(int(grupo), []).append(clave)

    return acumulados, grupos

def _caratula_sumar_acumulados_claves(acumulados, claves):
    total = {
        "general": Decimal("0"),
        "scott": Decimal("0"),
        "bold": Decimal("0"),
        "megamo": Decimal("0"),
        "apparel": Decimal("0"),
        "syncros": Decimal("0"),
        "vittoria": Decimal("0"),
        "apparel_syncros_vittoria": Decimal("0"),
        "otros": Decimal("0"),
    }

    for clave in claves:
        dato = acumulados.get(str(clave or "").strip().upper())
        if not dato:
            continue
        for campo in ("general", "scott", "bold", "megamo", "apparel", "syncros", "vittoria", "apparel_syncros_vittoria", "otros"):
            total[campo] += _caratula_decimal(dato.get(campo))

    for key in total:
        total[key] = _caratula_money(total[key])
    return total

def _caratula_aplicar_acumulados_reales(contribuciones, acumulados, grupos):
    """Sustituye EN MEMORIA los acumulados de las filas vigentes de carátula.

    No escribe en `previo`. Las integrales toman la suma de sus sucursales reales.
    """
    for vista in ("global", "A", "B"):
        for fila in contribuciones[vista]:
            es_integral = int(fila.get("es_integral") or 0) == 1

            if es_integral:
                grupo = fila.get("grupo_integral")
                claves = list(grupos.get(int(grupo), [])) if grupo is not None else []
                if vista in ("A", "B"):
                    claves = [
                        clave for clave in claves
                        if acumulados.get(clave, {}).get("evac") == vista
                    ]
            else:
                clave = str(fila.get("clave") or "").strip().upper()
                claves = [clave] if clave else []

            total = _caratula_sumar_acumulados_claves(acumulados, claves)

            fila["acumulado_anticipado"] = float(total["general"])
            fila["avance_global_scott"] = float(total["scott"])
            fila["acumulado_bold"] = float(total["bold"])
            fila["acumulado_apparel"] = float(total["apparel"])
            fila["acumulado_syncros"] = float(total["syncros"])
            fila["acumulado_vittoria"] = float(total["vittoria"])
            fila["avance_global_apparel_syncros_vittoria"] = float(
                total["apparel_syncros_vittoria"]
            )
            # Campos adicionales compatibles hacia atrás: frontends viejos los ignoran.
            fila["acumulado_megamo"] = float(total["megamo"])
            fila["acumulado_otros"] = float(total["otros"])

def _caratula_megamo_desde_monitor(
    cursor,
    fecha_inicio_my27,
    fecha_fin_my27,
    fecha_desde=None,
    fecha_hasta=None,
):
    """MEGAMO válido: normal PREVIO + Multimarcas vigente, respetando el filtro."""
    scope = _caratula_normal_en_previo_sql("c")
    clasificacion = _caratula_clasificacion_sql("m")

    cursor.execute(f"""
        SELECT c.evac, ROUND(SUM(COALESCE(m.venta_total, 0)), 2) AS total
        FROM monitor m
        INNER JOIN clientes c
            ON TRIM(UPPER(c.clave)) = TRIM(UPPER(m.contacto_referencia))
        WHERE c.activo = 1
          AND c.evac IN ('A', 'B')
          AND {scope}
          AND m.fecha_factura >= CASE
                WHEN %s IS NULL
                    THEN COALESCE(c.f_inicio, %s)
                ELSE GREATEST(
                    %s,
                    COALESCE(c.f_inicio, %s)
                )
              END
          AND m.fecha_factura < DATE_ADD(
                LEAST(
                    COALESCE(c.f_fin, %s),
                    COALESCE(%s, %s),
                    CURDATE()
                ),
                INTERVAL 1 DAY
              )
          AND ({clasificacion}) = 'MEGAMO'
        GROUP BY c.evac
    """, (
        fecha_desde,
        fecha_inicio_my27,
        fecha_desde,
        fecha_inicio_my27,
        fecha_fin_my27,
        fecha_hasta,
        fecha_fin_my27,
    ))

    normal = {"A": Decimal("0"), "B": Decimal("0")}
    for row in cursor.fetchall():
        evac = row.get("evac")
        if evac in normal:
            normal[evac] = _caratula_money(_caratula_decimal(row.get("total")))

    inicio_multi, fin_multi = _caratula_rango_general_efectivo(
        fecha_inicio_my27,
        fecha_fin_my27,
        fecha_desde,
        fecha_hasta,
    )

    cursor.execute(f"""
        SELECT cm.evac, ROUND(SUM(COALESCE(m.venta_total, 0)), 2) AS total
        FROM monitor m
        INNER JOIN clientes_multimarcas cm
          ON (
                (TRIM(COALESCE(cm.clave, '')) <> ''
                 AND TRIM(UPPER(cm.clave)) = TRIM(UPPER(m.contacto_referencia)))
                OR
                ((m.contacto_referencia IS NULL OR TRIM(m.contacto_referencia) = '')
                 AND TRIM(COALESCE(cm.cliente_razon_social, '')) <> ''
                 AND TRIM(UPPER(cm.cliente_razon_social)) = TRIM(UPPER(m.contacto_nombre)))
             )
        WHERE cm.activo = 1
          AND cm.evac IN ('A', 'B')
          AND UPPER(TRIM(COALESCE(m.evac, ''))) = UPPER(CONCAT(TRIM(cm.evac), ' MULTIMARCAS'))
          AND m.fecha_factura >= %s
          AND m.fecha_factura < DATE_ADD(LEAST(%s, CURDATE()), INTERVAL 1 DAY)
          AND ({clasificacion}) = 'MEGAMO'
          AND NOT EXISTS (
                SELECT 1
                FROM clientes c
                WHERE c.activo = 1
                  AND c.evac IN ('A', 'B')
                  AND TRIM(UPPER(c.clave)) = TRIM(UPPER(cm.clave))
                  AND {_caratula_normal_en_previo_sql('c')}
          )
        GROUP BY cm.evac
    """, (inicio_multi, fin_multi))

    multimarcas = {"A": Decimal("0"), "B": Decimal("0")}
    for row in cursor.fetchall():
        evac = row.get("evac")
        if evac in multimarcas:
            multimarcas[evac] = _caratula_money(_caratula_decimal(row.get("total")))

    return {
        "normal": normal,
        "multimarcas": multimarcas,
        "total": {
            "A": _caratula_money(normal["A"] + multimarcas["A"]),
            "B": _caratula_money(normal["B"] + multimarcas["B"]),
        },
    }


def _caratula_multimarcas_acumulados(
    cursor,
    fecha_inicio_my27=None,
    fecha_fin_my27=None,
    fecha_desde=None,
    fecha_hasta=None,
):
    """Acumulado Multimarcas activo dentro del rango efectivo solicitado."""
    if fecha_inicio_my27 is None or fecha_fin_my27 is None:
        fecha_inicio_my27, fecha_fin_my27 = _caratula_rango_my27(cursor)

    fecha_inicio, fecha_fin = _caratula_rango_general_efectivo(
        fecha_inicio_my27,
        fecha_fin_my27,
        fecha_desde,
        fecha_hasta,
    )

    clasificacion = _caratula_clasificacion_sql("m")
    scope_normal = _caratula_normal_en_previo_sql("c_scope")

    cursor.execute(f"""
        SELECT
            x.evac,
            ROUND(SUM(x.venta_total), 2) AS general,
            ROUND(SUM(CASE WHEN x.clasificacion IN ('SCOTT','BOLD') THEN x.venta_total ELSE 0 END), 2) AS bicicletas_base,
            ROUND(SUM(CASE WHEN x.clasificacion = 'SCOTT' THEN x.venta_total ELSE 0 END), 2) AS scott,
            ROUND(SUM(CASE WHEN x.clasificacion = 'BOLD' THEN x.venta_total ELSE 0 END), 2) AS bold,
            ROUND(SUM(CASE WHEN x.clasificacion = 'MEGAMO' THEN x.venta_total ELSE 0 END), 2) AS megamo,
            ROUND(SUM(CASE WHEN x.clasificacion = 'APPAREL' THEN x.venta_total ELSE 0 END), 2) AS apparel,
            ROUND(SUM(CASE WHEN x.clasificacion = 'SYNCROS' THEN x.venta_total ELSE 0 END), 2) AS syncros,
            ROUND(SUM(CASE WHEN x.clasificacion = 'VITTORIA' THEN x.venta_total ELSE 0 END), 2) AS vittoria,
            ROUND(SUM(CASE WHEN x.clasificacion IN ('APPAREL','SYNCROS','VITTORIA') THEN x.venta_total ELSE 0 END), 2) AS apparel_syncros_vittoria,
            ROUND(SUM(CASE WHEN x.clasificacion = 'OTROS' THEN x.venta_total ELSE 0 END), 2) AS otros
        FROM (
            SELECT
                cm.evac,
                COALESCE(m.venta_total, 0) AS venta_total,
                {clasificacion} AS clasificacion
            FROM monitor m
            INNER JOIN clientes_multimarcas cm
              ON (
                    (TRIM(COALESCE(cm.clave, '')) <> ''
                     AND TRIM(UPPER(cm.clave)) = TRIM(UPPER(m.contacto_referencia)))
                    OR
                    ((m.contacto_referencia IS NULL OR TRIM(m.contacto_referencia) = '')
                     AND TRIM(COALESCE(cm.cliente_razon_social, '')) <> ''
                     AND TRIM(UPPER(cm.cliente_razon_social)) = TRIM(UPPER(m.contacto_nombre)))
                 )
            WHERE cm.activo = 1
              AND cm.evac IN ('A', 'B')
              AND UPPER(TRIM(COALESCE(m.evac, ''))) = UPPER(CONCAT(TRIM(cm.evac), ' MULTIMARCAS'))
              AND m.fecha_factura >= %s
              AND m.fecha_factura < DATE_ADD(LEAST(%s, CURDATE()), INTERVAL 1 DAY)
              AND NOT EXISTS (
                    SELECT 1
                    FROM clientes c_scope
                    WHERE c_scope.activo = 1
                      AND c_scope.evac IN ('A', 'B')
                      AND TRIM(UPPER(c_scope.clave)) = TRIM(UPPER(cm.clave))
                      AND {scope_normal}
              )
        ) x
        GROUP BY x.evac
    """, (fecha_inicio, fecha_fin))

    resultado = {
        "A": {
            "general": Decimal("0"), "bicicletas_base": Decimal("0"),
            "scott": Decimal("0"), "bold": Decimal("0"), "megamo": Decimal("0"),
            "apparel": Decimal("0"), "syncros": Decimal("0"), "vittoria": Decimal("0"),
            "apparel_syncros_vittoria": Decimal("0"), "otros": Decimal("0")
        },
        "B": {
            "general": Decimal("0"), "bicicletas_base": Decimal("0"),
            "scott": Decimal("0"), "bold": Decimal("0"), "megamo": Decimal("0"),
            "apparel": Decimal("0"), "syncros": Decimal("0"), "vittoria": Decimal("0"),
            "apparel_syncros_vittoria": Decimal("0"), "otros": Decimal("0")
        },
    }

    for row in cursor.fetchall():
        evac = row.get("evac")
        if evac not in resultado:
            continue
        resultado[evac] = {
            "general": _caratula_money(_caratula_decimal(row.get("general"))),
            "bicicletas_base": _caratula_money(_caratula_decimal(row.get("bicicletas_base"))),
            "scott": _caratula_money(_caratula_decimal(row.get("scott"))),
            "bold": _caratula_money(_caratula_decimal(row.get("bold"))),
            "megamo": _caratula_money(_caratula_decimal(row.get("megamo"))),
            "apparel": _caratula_money(_caratula_decimal(row.get("apparel"))),
            "syncros": _caratula_money(_caratula_decimal(row.get("syncros"))),
            "vittoria": _caratula_money(_caratula_decimal(row.get("vittoria"))),
            "apparel_syncros_vittoria": _caratula_money(_caratula_decimal(row.get("apparel_syncros_vittoria"))),
            "otros": _caratula_money(_caratula_decimal(row.get("otros"))),
        }

    return resultado

def _caratula_ventas_no_registradas_resumen(cursor, fecha_inicio, fecha_fin):
    """Ventas sin cliente registrado. Solo pertenecen a Global, nunca a EVAC A/B."""
    clasificacion = _caratula_clasificacion_sql("m")
    cursor.execute(f"""
        SELECT
            ROUND(SUM(x.venta_total), 2) AS total,
            COUNT(*) AS filas,
            ROUND(SUM(CASE WHEN x.clasificacion = 'SCOTT' THEN x.venta_total ELSE 0 END), 2) AS scott,
            ROUND(SUM(CASE WHEN x.clasificacion = 'BOLD' THEN x.venta_total ELSE 0 END), 2) AS bold,
            ROUND(SUM(CASE WHEN x.clasificacion = 'MEGAMO' THEN x.venta_total ELSE 0 END), 2) AS megamo,
            ROUND(SUM(CASE WHEN x.clasificacion = 'APPAREL' THEN x.venta_total ELSE 0 END), 2) AS apparel,
            ROUND(SUM(CASE WHEN x.clasificacion = 'SYNCROS' THEN x.venta_total ELSE 0 END), 2) AS syncros,
            ROUND(SUM(CASE WHEN x.clasificacion = 'VITTORIA' THEN x.venta_total ELSE 0 END), 2) AS vittoria,
            ROUND(SUM(CASE WHEN x.clasificacion = 'OTROS' THEN x.venta_total ELSE 0 END), 2) AS otros
        FROM (
            SELECT
                COALESCE(m.venta_total, 0) AS venta_total,
                {clasificacion} AS clasificacion
            FROM monitor m
            WHERE NOT EXISTS (
                SELECT 1 FROM clientes c
                WHERE UPPER(TRIM(c.clave)) = UPPER(TRIM(m.contacto_referencia))
                  AND TRIM(COALESCE(c.clave, '')) <> ''
            )
            AND NOT EXISTS (
                SELECT 1 FROM clientes_multimarcas cm
                WHERE UPPER(TRIM(cm.clave)) = UPPER(TRIM(m.contacto_referencia))
                  AND TRIM(COALESCE(cm.clave, '')) <> ''
            )
            AND NOT EXISTS (
                SELECT 1 FROM clientes_multimarcas cm2
                WHERE UPPER(TRIM(cm2.cliente_razon_social)) = UPPER(TRIM(m.contacto_nombre))
                  AND TRIM(COALESCE(cm2.cliente_razon_social, '')) <> ''
            )
            AND m.fecha_factura >= %s
            AND m.fecha_factura < DATE_ADD(LEAST(%s, CURDATE()), INTERVAL 1 DAY)
        ) x
    """, (fecha_inicio, fecha_fin))
    row = cursor.fetchone() or {}
    salida = {campo: _caratula_money(_caratula_decimal(row.get(campo))) for campo in (
        "total", "scott", "bold", "megamo", "apparel", "syncros", "vittoria", "otros"
    )}
    salida["filas"] = int(row.get("filas") or 0)
    salida["bicicletas"] = _caratula_money(salida["scott"] + salida["bold"] + salida["megamo"])
    salida["apparel_syncros_vittoria"] = _caratula_money(salida["apparel"] + salida["syncros"] + salida["vittoria"])
    return salida


def _caratula_desglose_contribuciones(filas):
    """Desglose exclusivo de ventas normales ya filtradas por el universo de carátula."""
    campos = {
        "scott": "avance_global_scott",
        "bold": "acumulado_bold",
        "megamo": "acumulado_megamo",
        "apparel": "acumulado_apparel",
        "syncros": "acumulado_syncros",
        "vittoria": "acumulado_vittoria",
        "otros": "acumulado_otros",
    }
    resultado = {clave: Decimal("0") for clave in campos}
    for fila in filas:
        for clave, campo in campos.items():
            resultado[clave] += _caratula_decimal(fila.get(campo))
    return {clave: _caratula_money(valor) for clave, valor in resultado.items()}


def _caratula_sumar_desglose(base, multi=None, extra=None):
    """Suma desgloses exclusivos y devuelve también agrupaciones comerciales."""
    campos = ("scott", "bold", "megamo", "apparel", "syncros", "vittoria", "otros")
    multi = multi or {}
    extra = extra or {}
    salida = {}
    for campo in campos:
        salida[campo] = _caratula_money(
            _caratula_decimal(base.get(campo))
            + _caratula_decimal(multi.get(campo))
            + _caratula_decimal(extra.get(campo))
        )

    salida["bicicletas"] = _caratula_money(
        salida["scott"] + salida["bold"] + salida["megamo"]
    )
    salida["apparel_syncros_vittoria"] = _caratula_money(
        salida["apparel"] + salida["syncros"] + salida["vittoria"]
    )
    salida["general"] = _caratula_money(
        salida["bicicletas"]
        + salida["apparel_syncros_vittoria"]
        + salida["otros"]
    )
    return salida


def _caratula_armar_validacion_lineas(base, multi, megamo):
    general = _caratula_money(base["general"] + multi["general"])
    bicicletas_base = _caratula_money(base["bicicletas_base"] + multi["bicicletas_base"])
    apparel = _caratula_money(base["apparel_syncros_vittoria"] + multi["apparel_syncros_vittoria"])
    megamo = _caratula_money(megamo)
    bicicletas = _caratula_money(bicicletas_base + megamo)
    otros = _caratula_money(general - bicicletas - apparel)
    return {
        "acumulado_general": float(general),
        "bicicletas_antes_megamo": float(bicicletas_base),
        "megamo_my27": float(megamo),
        "bicicletas_con_megamo": float(bicicletas),
        "apparel_syncros_vittoria": float(apparel),
        "otros_despues_megamo": float(otros),
    }

def _caratula_resumen_meta(filas):
    meta = Decimal("0")
    categoria = Decimal("0")
    distribuidor = Decimal("0")
    apparel = Decimal("0")

    for fila in filas:
        nivel = fila.get("nivel")
        meta_fila = _caratula_decimal(fila.get("compra_minima_anual"))

        meta += meta_fila
        apparel += _caratula_decimal(
            fila.get("compromiso_apparel_syncros_vittoria")
        )

        if nivel in _NIVELES_CATEGORIA:
            categoria += meta_fila
        elif nivel == "Distribuidor":
            distribuidor += meta_fila

    meta = _caratula_money(meta)
    categoria = _caratula_money(categoria)
    distribuidor = _caratula_money(distribuidor)
    apparel = _caratula_money(apparel)
    bicicletas = _caratula_money(meta - apparel)

    return {
        "meta": float(meta),
        "categoria": float(categoria),
        "distribuidor": float(distribuidor),
        "bicicletas": float(bicicletas),
        "apparel": float(apparel),
    }



def _precalentar_claves(claves: list[str], host: str = 'http://localhost:5000') -> None:
    """Carga todos los clientes en Redis usando un pool de threads paralelos."""
    import requests as _req
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _cargar_uno(clave: str) -> str:
        try:
            _req.get(
                f'{host}/detalle-compras-odoo',
                params={'cliente': clave, 'ref_exacta': '1'},
                timeout=120,
            )
            return f'OK:{clave}'
        except Exception as _e:
            return f'ERR:{clave}:{_e}'

    logging.info('Precalentamiento: %d clientes, %d workers paralelos', len(claves), _WARM_WORKERS)
    with ThreadPoolExecutor(max_workers=_WARM_WORKERS) as pool:
        futuros = {pool.submit(_cargar_uno, c): c for c in claves}
        ok = err = 0
        for fut in as_completed(futuros):
            res = fut.result()
            if res.startswith('OK'):
                ok += 1
            else:
                err += 1
                logging.warning('Precalentamiento error: %s', res)
    logging.info('Precalentamiento terminado: %d OK, %d errores', ok, err)


def iniciar_precalentamiento(host: str = 'http://localhost:5000') -> int:
    """Lanza un thread daemon que precalienta Redis para todos los clientes activos."""
    import threading
    try:
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT clave FROM clientes "
            "WHERE clave IS NOT NULL AND clave != '' AND f_inicio IS NOT NULL"
        )
        claves = [r['clave'] for r in cur.fetchall()]
        cur.close()
        conn.close()
    except Exception as _e:
        logging.warning('iniciar_precalentamiento: no se pudo leer clientes: %s', _e)
        return 0

    t = threading.Thread(target=_precalentar_claves, args=(claves, host), daemon=True)
    t.start()
    logging.info('Precalentamiento iniciado para %d clientes', len(claves))
    return len(claves)

@caratulas_bp.route('/precalentar-monitor', methods=['POST'])
def precalentar_monitor():
    """Dispara el pre-calentamiento del cache Redis para todos los clientes."""
    total = iniciar_precalentamiento()
    return jsonify({'status': 'iniciado', 'clientes': total}), 202


@caratulas_bp.route('/caratula_evac', methods=['GET'])
@requiere_autenticacion
@requiere_modulo('usuarios_caratula')
def buscar_caratula_evac():
    try:
        clave = request.args.get('clave')
        nombre_cliente = request.args.get('nombre_cliente')
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)
        contexto = _contexto_cliente_autenticado(cursor)
        if contexto:
            # Roles de portal: ignorar cualquier clave o nombre manipulados.
            clave = _clave_resumen_contexto(contexto)
            nombre_cliente = None

        if not clave and not nombre_cliente:
            return jsonify({'error': 'Se requiere clave o nombre_cliente'}), 400
        
        nombre_a_buscar = nombre_cliente
        columna_a_buscar = "nombre_cliente" # Por defecto buscamos en nombre_cliente
        
        import re as _re_evac

        # Si la búsqueda es por nombre y contiene "Integral", es un grupo.
        if nombre_cliente and "integral" in nombre_cliente.lower():
            cursor.execute("SELECT id FROM grupo_clientes WHERE nombre_grupo = %s", (nombre_cliente,))
            grupo = cursor.fetchone()

            if grupo:
                # Usar grupo_integral (id real de grupo_clientes) en lugar de
                # la clave ordinal "Integral N", que no coincide con el id del grupo.
                nombre_a_buscar = str(grupo['id'])
                columna_a_buscar = "grupo_integral"
                logging.info("Búsqueda de GRUPO: usando grupo_integral = %s para '%s'", grupo['id'], nombre_cliente)
            else:
                # Búsqueda directa por clave de integral (ej. "Integral 4")
                nombre_a_buscar = nombre_cliente
                columna_a_buscar = "clave"
                logging.info("Búsqueda de INTEGRAL por clave directa: '%s'", nombre_cliente)

        # Construir consulta dinámica
        query = "SELECT * FROM previo WHERE "
        params = []
        conditions = []

        if clave:
            # Si la clave es "Integral {id}" (enviada por el frontend usando id_grupo del token),
            # usar la columna grupo_integral (id real) en lugar de clave (nombre ordinal).
            # La clave ordinal "Integral 4" no coincide con el id de grupo_clientes (ej: 12).
            _m_integral = _re_evac.match(r'^integral\s+(\d+)$', clave.strip(), _re_evac.IGNORECASE)
            if _m_integral:
                # Buscar por clave ordinal ("Integral 4") O por id real de grupo (grupo_integral=12).
                # El admin busca por nombre ordinal; el token del usuario trae el id real.
                conditions.append("(LOWER(clave) = LOWER(%s) OR grupo_integral = %s) AND es_integral = 1")
                params.append(clave.strip())
                params.append(int(_m_integral.group(1)))
            else:
                conditions.append("clave = %s")
                params.append(clave)
        elif nombre_a_buscar:
            if columna_a_buscar == "grupo_integral":
                conditions.append("grupo_integral = %s AND es_integral = 1")
                params.append(int(nombre_a_buscar))
            else:
                conditions.append(f"{columna_a_buscar} LIKE %s")
                params.append(f"%{nombre_a_buscar}%")

        query += " AND ".join(conditions)
        
        cursor.execute(query, tuple(params))
        resultados = cursor.fetchall()

        if not resultados:
            return jsonify({'error': 'No se encontraron registros'}), 404

        # Convertir Decimal a float
        for fila in resultados:
            for key, value in fila.items():
                if isinstance(value, Decimal):
                    fila[key] = float(value)

        if _debe_ocultar_montos_caratula():
            resultados = _redactar_montos_caratula(resultados)
        
        return jsonify(resultados), 200

    except PermissionError as e:
        return jsonify({'error': str(e)}), 403
    except Exception as e:
        logging.exception("Error en buscar_caratula_evac")
        return jsonify({'error': 'Error al procesar la solicitud'}), 500
        
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conexion' in locals() and conexion.is_connected():
            conexion.close()

@caratulas_bp.route('/nombres_caratula', methods=['GET'])
def obtener_nombres():
    try:
        # Conexión a BD
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)

        # Cuando previo.nombre_cliente == previo.clave (ej: "4E013"), usa el nombre real de clientes.
        # COALESCE(p.grupo_integral, c.id_grupo) unifica el grupo para individuales e integrales.
        query = """
        SELECT p.clave,
               CASE WHEN p.nombre_cliente = p.clave
                    THEN COALESCE(c.nombre_cliente, p.nombre_cliente)
                    ELSE p.nombre_cliente
               END AS nombre_cliente,
               p.es_integral, p.grupo_integral,
               COALESCE(p.grupo_integral, c.id_grupo) AS id_grupo
        FROM previo p
        LEFT JOIN clientes c ON p.clave = c.clave AND p.es_integral = 0
        """
        cursor.execute(query)
        resultados = cursor.fetchall()

        if not resultados:
            return jsonify({'error': 'No se encontraron registros'}), 404

        return jsonify(resultados), 200

    except Exception as e:
        logging.exception("Error en obtener_nombres")
        return jsonify({'error': 'Error al procesar la solicitud'}), 500

    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@caratulas_bp.route('/clientes_a', methods=['GET'])
def obtener_previo_evac_a():
    try:
        fecha_desde, fecha_hasta = _caratula_leer_rango_solicitado()
        contribuciones = _caratula_obtener_contribuciones_my27(
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
        )
        resultados = [
            _caratula_json_fila(fila)
            for fila in contribuciones["A"]
        ]
        return jsonify(resultados), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logging.exception("Error en obtener_previo_evac_a MY27")
        return jsonify({"error": str(e)}), 500


@caratulas_bp.route('/clientes_b', methods=['GET'])
def obtener_previo_evac_b():
    try:
        fecha_desde, fecha_hasta = _caratula_leer_rango_solicitado()
        contribuciones = _caratula_obtener_contribuciones_my27(
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
        )
        resultados = [
            _caratula_json_fila(fila)
            for fila in contribuciones["B"]
        ]
        return jsonify(resultados), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logging.exception("Error en obtener_previo_evac_b MY27")
        return jsonify({"error": str(e)}), 500

@caratulas_bp.route('/clientes_go', methods=['GET'])
def obtener_previo_evac_go():
    try:
        conexion = obtener_conexion()
        with conexion.cursor(dictionary=True) as cursor:
            query = "SELECT * FROM previo WHERE evac = %s"
            cursor.execute(query, ("GO",))
            resultados = cursor.fetchall()
        
        # Convertir valores Decimal a float para JSON
        for fila in resultados:
            for key, value in fila.items():
                if isinstance(value, Decimal):
                    fila[key] = float(value)

        return jsonify(resultados), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@caratulas_bp.route('/caratula_evac_a', methods=['POST'])
def actualizar_caratula_evac_a():
    try:
        datos = request.get_json()
        
        # CORRECCIÓN: El frontend envía {datos: [...]} no directamente [...]
        datos_array = datos.get('datos') if isinstance(datos, dict) else datos
        
        if not datos_array or not isinstance(datos_array, list):
            return jsonify({'error': 'Datos no proporcionados correctamente'}), 400
        
        conexion = obtener_conexion()
        with conexion.cursor() as cursor:
            # Snapshot: preservar el estado actual en caratula_evac_a_historico antes de
            # truncar, para poder consultar temporadas anteriores.
            cursor.execute("""
                INSERT INTO caratula_evac_a_historico
                    (temporada, fecha_snapshot, id_original, categoria, meta, acumulado_real, avance_proyectado, porcentaje)
                SELECT %s, NOW(), id, categoria, meta, acumulado_real, avance_proyectado, porcentaje
                FROM caratula_evac_a
            """, (etiqueta_temporada(),))

            cursor.execute("TRUNCATE TABLE caratula_evac_a")
            for i, item in enumerate(datos_array):
                cursor.execute("""
                    INSERT INTO caratula_evac_a 
                    (categoria, meta, acumulado_real, avance_proyectado, porcentaje)
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    item.get('categoria'),
                    item.get('meta', 0),
                    item.get('acumulado_real', 0),
                    item.get('avance_proyectado', 0),
                    item.get('porcentaje', 0)
                ))
            
            conexion.commit()
            return jsonify({'success': True, 'message': 'Datos actualizados'}), 200
            
    except Exception as e:
        if 'conexion' in locals():
            conexion.rollback()
            logging.exception("Error en actualizar_caratula_evac_a")
        return jsonify({'error': str(e)}), 500
    
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@caratulas_bp.route('/caratula_evac_b', methods=['POST'])
def actualizar_caratula_evac_b():
    try:
        datos = request.get_json()
        
        # CORRECCIÓN: El frontend envía {datos: [...]} no directamente [...]
        datos_array = datos.get('datos') if isinstance(datos, dict) else datos
        
        if not datos_array or not isinstance(datos_array, list):
            return jsonify({'error': 'Datos no proporcionados correctamente'}), 400
        
        conexion = obtener_conexion()
        with conexion.cursor() as cursor:
            # Snapshot: preservar el estado actual en caratula_evac_b_historico antes de
            # truncar, para poder consultar temporadas anteriores.
            cursor.execute("""
                INSERT INTO caratula_evac_b_historico
                    (temporada, fecha_snapshot, id_original, categoria, meta, acumulado_real, avance_proyectado, porcentaje)
                SELECT %s, NOW(), id, categoria, meta, acumulado_real, avance_proyectado, porcentaje
                FROM caratula_evac_b
            """, (etiqueta_temporada(),))

            cursor.execute("TRUNCATE TABLE caratula_evac_b")
            for i, item in enumerate(datos_array):
                cursor.execute("""
                    INSERT INTO caratula_evac_b
                    (categoria, meta, acumulado_real, avance_proyectado, porcentaje)
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    item.get('categoria'),
                    item.get('meta', 0),
                    item.get('acumulado_real', 0),
                    item.get('avance_proyectado', 0),
                    item.get('porcentaje', 0)
                ))
            
            conexion.commit()
            return jsonify({'success': True, 'message': 'Datos actualizados'}), 200
            
    except Exception as e:
        if 'conexion' in locals():
            conexion.rollback()
        logging.exception("Error en actualizar_caratula_evac_b")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@caratulas_bp.route('/datos_evac_a', methods=['GET'])
def obtener_caratula_evac_a():
        try:
            conexion = obtener_conexion()
            with conexion.cursor(dictionary=True) as cursor:
                cursor.execute("SELECT * FROM caratula_evac_a")
                resultados = cursor.fetchall()
                # Convertir Decimal a float si es necesario
                for fila in resultados:
                    for key, value in fila.items():
                        if isinstance(value, Decimal):
                            fila[key] = float(value)
            return jsonify(resultados), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500
        finally:
            if cursor:
                cursor.close()
            if conexion and conexion.is_connected():
                conexion.close()

@caratulas_bp.route('/datos_evac_b', methods=['GET'])
def obtener_caratula_evac_b():
        try:
            conexion = obtener_conexion()
            with conexion.cursor(dictionary=True) as cursor:
                cursor.execute("SELECT * FROM caratula_evac_b")
                resultados = cursor.fetchall()
                # Convertir Decimal a float si es necesario
                for fila in resultados:
                    for key, value in fila.items():
                        if isinstance(value, Decimal):
                            fila[key] = float(value)
            return jsonify(resultados), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500
        finally:
            if cursor:
                cursor.close()
            if conexion and conexion.is_connected():
                conexion.close()

@caratulas_bp.route('/datos_previo', methods=['GET'])
def obtener_datos_previo():
    try:
        contribuciones = _caratula_obtener_contribuciones_my27()
        resultados = [
            _caratula_json_fila(fila)
            for fila in contribuciones["global"]
        ]
        return jsonify(resultados), 200
    except Exception as e:
        logging.exception("Error en obtener_datos_previo MY27")
        return jsonify({'error': str(e)}), 500


@caratulas_bp.route('/resumen_caratulas_my27', methods=['GET'])
def obtener_resumen_caratulas_my27():
    """
    Fuente maestra MY27 para Global, EVAC A y EVAC B.

    Sin query params conserva el cálculo actual.
    Con fecha_desde/fecha_hasta usa exactamente la misma lógica y solo limita
    el acumulado al rango solicitado, respetando ventanas individuales.
    """
    conexion = None
    cursor = None
    try:
        fecha_desde, fecha_hasta = _caratula_leer_rango_solicitado()

        contribuciones = _caratula_obtener_contribuciones_my27(
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
        )

        global_resumen = _caratula_resumen_meta(contribuciones["global"])
        a_resumen = _caratula_resumen_meta(contribuciones["A"])
        b_resumen = _caratula_resumen_meta(contribuciones["B"])

        base_global = _caratula_resumen_acumulado_lineas(contribuciones["global"])
        base_a = _caratula_resumen_acumulado_lineas(contribuciones["A"])
        base_b = _caratula_resumen_acumulado_lineas(contribuciones["B"])

        detalle_normal_a = _caratula_desglose_contribuciones(contribuciones["A"])
        detalle_normal_b = _caratula_desglose_contribuciones(contribuciones["B"])

        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)

        fecha_inicio_my27, fecha_fin_my27 = _caratula_rango_my27(cursor)
        fecha_inicio_efectiva, fecha_fin_efectiva = _caratula_rango_general_efectivo(
            fecha_inicio_my27,
            fecha_fin_my27,
            fecha_desde,
            fecha_hasta,
        )

        multimarcas = _caratula_multimarcas_acumulados(
            cursor,
            fecha_inicio_my27,
            fecha_fin_my27,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
        )

        megamo = _caratula_megamo_desde_monitor(
            cursor,
            fecha_inicio_my27,
            fecha_fin_my27,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
        )

        no_reg = _caratula_ventas_no_registradas_resumen(
            cursor,
            fecha_inicio_efectiva,
            fecha_fin_efectiva,
        )

        detalle_a = _caratula_sumar_desglose(detalle_normal_a, multimarcas["A"])
        detalle_b = _caratula_sumar_desglose(detalle_normal_b, multimarcas["B"])
        detalle_global_operativo = _caratula_sumar_desglose(detalle_a, detalle_b)
        detalle_global = _caratula_sumar_desglose(
            detalle_global_operativo,
            extra=no_reg,
        )

        acumulado_a = _caratula_armar_validacion_lineas(
            base_a, multimarcas["A"], megamo["total"]["A"]
        )
        acumulado_b = _caratula_armar_validacion_lineas(
            base_b, multimarcas["B"], megamo["total"]["B"]
        )

        multi_global = {
            "general": multimarcas["A"]["general"] + multimarcas["B"]["general"],
            "bicicletas_base": multimarcas["A"]["bicicletas_base"] + multimarcas["B"]["bicicletas_base"],
            "apparel_syncros_vittoria": multimarcas["A"]["apparel_syncros_vittoria"] + multimarcas["B"]["apparel_syncros_vittoria"],
        }

        operativo_global = _caratula_armar_validacion_lineas(
            base_global,
            multi_global,
            megamo["total"]["A"] + megamo["total"]["B"],
        )

        global_final = {
            "acumulado_general": round(operativo_global["acumulado_general"] + float(no_reg["total"]), 2),
            "acumulado_bicicletas": round(operativo_global["bicicletas_con_megamo"] + float(no_reg["bicicletas"]), 2),
            "acumulado_apparel": round(operativo_global["apparel_syncros_vittoria"] + float(no_reg["apparel_syncros_vittoria"]), 2),
            "acumulado_otros": round(operativo_global["otros_despues_megamo"] + float(no_reg["otros"]), 2),
            "acumulado_megamo": round(operativo_global["megamo_my27"] + float(no_reg["megamo"]), 2),
        }

        global_resumen.update(global_final)

        a_resumen.update({
            "acumulado_general": acumulado_a["acumulado_general"],
            "acumulado_bicicletas": acumulado_a["bicicletas_con_megamo"],
            "acumulado_apparel": acumulado_a["apparel_syncros_vittoria"],
            "acumulado_otros": acumulado_a["otros_despues_megamo"],
            "acumulado_megamo": acumulado_a["megamo_my27"],
        })

        b_resumen.update({
            "acumulado_general": acumulado_b["acumulado_general"],
            "acumulado_bicicletas": acumulado_b["bicicletas_con_megamo"],
            "acumulado_apparel": acumulado_b["apparel_syncros_vittoria"],
            "acumulado_otros": acumulado_b["otros_despues_megamo"],
            "acumulado_megamo": acumulado_b["megamo_my27"],
        })

        a_resumen["desglose"] = {k: float(v) for k, v in detalle_a.items()}
        b_resumen["desglose"] = {k: float(v) for k, v in detalle_b.items()}
        global_resumen["desglose"] = {k: float(v) for k, v in detalle_global.items()}

        a_mas_b_general = round(
            a_resumen["acumulado_general"] + b_resumen["acumulado_general"],
            2,
        )
        a_mas_b_bicicletas = round(
            a_resumen["acumulado_bicicletas"] + b_resumen["acumulado_bicicletas"],
            2,
        )
        a_mas_b_apparel = round(
            a_resumen["acumulado_apparel"] + b_resumen["acumulado_apparel"],
            2,
        )
        a_mas_b_otros = round(
            a_resumen["acumulado_otros"] + b_resumen["acumulado_otros"],
            2,
        )

        return jsonify({
            "global": global_resumen,
            "evac_a": a_resumen,
            "evac_b": b_resumen,
            "rango_my27": {
                "fecha_inicio": fecha_inicio_my27.strftime("%Y-%m-%d") if hasattr(fecha_inicio_my27, "strftime") else str(fecha_inicio_my27),
                "fecha_fin": fecha_fin_my27.strftime("%Y-%m-%d") if hasattr(fecha_fin_my27, "strftime") else str(fecha_fin_my27),
            },
            "rango_solicitado": {
                "fecha_desde": fecha_desde.strftime("%Y-%m-%d") if fecha_desde else None,
                "fecha_hasta": fecha_hasta.strftime("%Y-%m-%d") if fecha_hasta else None,
                "fecha_inicio_general_efectiva": fecha_inicio_efectiva.strftime("%Y-%m-%d") if hasattr(fecha_inicio_efectiva, "strftime") else str(fecha_inicio_efectiva),
                "fecha_fin_general_efectiva": fecha_fin_efectiva.strftime("%Y-%m-%d") if hasattr(fecha_fin_efectiva, "strftime") else str(fecha_fin_efectiva),
            },
            "ventas_no_registradas": {
                k: (float(v) if isinstance(v, Decimal) else v)
                for k, v in no_reg.items()
            },
            "validacion": {
                "meta_a_mas_b": round(a_resumen["meta"] + b_resumen["meta"], 2),
                "meta_global": round(global_resumen["meta"], 2),
                "acumulado_general_a_mas_b": a_mas_b_general,
                "acumulado_general_global": round(global_resumen["acumulado_general"], 2),
                "diferencia_global_vs_a_b": round(global_resumen["acumulado_general"] - a_mas_b_general, 2),
                "ventas_no_registradas_total": float(no_reg["total"]),
                "acumulado_bicicletas_a_mas_b": a_mas_b_bicicletas,
                "acumulado_bicicletas_global": round(global_resumen["acumulado_bicicletas"], 2),
                "acumulado_apparel_a_mas_b": a_mas_b_apparel,
                "acumulado_apparel_global": round(global_resumen["acumulado_apparel"], 2),
                "acumulado_otros_a_mas_b": a_mas_b_otros,
                "acumulado_otros_global": round(global_resumen["acumulado_otros"], 2),
                "cuadre_global_categorias": round(
                    global_resumen["acumulado_general"]
                    - global_resumen["acumulado_bicicletas"]
                    - global_resumen["acumulado_apparel"]
                    - global_resumen["acumulado_otros"],
                    2,
                ),
                "cuadre_desglose_global": round(
                    global_resumen["acumulado_general"] - float(detalle_global["general"]),
                    2,
                ),
                "cuadre_desglose_a": round(
                    a_resumen["acumulado_general"] - float(detalle_a["general"]),
                    2,
                ),
                "cuadre_desglose_b": round(
                    b_resumen["acumulado_general"] - float(detalle_b["general"]),
                    2,
                ),
            },
        }), 200

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logging.exception("Error en resumen_caratulas_my27")
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@caratulas_bp.route('/temporadas_disponibles', methods=['GET'])
@requiere_autenticacion
@requiere_modulo('usuarios_caratula')
def temporadas_disponibles():
    """Devuelve las temporadas (ej. '2025-2026') que tienen snapshots guardados
    en cualquiera de las tablas de histórico, para poblar un selector en el frontend.

    Excluye la etiqueta de la temporada actual (por calendario): previo/evac se
    auto-archivan en cada guardado usando etiqueta_temporada(), que solo mira la
    fecha de hoy -- sin importar si la temporada realmente cerro. Eso deja
    snapshots intermedios "ruidosos" bajo la etiqueta vigente que no representan
    un cierre real y confunden en el selector de "temporadas cerradas".
    """
    try:
        conexion = obtener_conexion()
        with conexion.cursor() as cursor:
            cursor.execute("""
                SELECT temporada FROM previo_historico
                UNION
                SELECT temporada FROM caratula_evac_a_historico
                UNION
                SELECT temporada FROM caratula_evac_b_historico
                ORDER BY temporada DESC
            """)
            temporada_actual = etiqueta_temporada()
            temporadas = [row[0] for row in cursor.fetchall() if row[0] != temporada_actual]
        return jsonify(temporadas), 200
    except Exception as e:
        logging.exception("Error en temporadas_disponibles")
        return jsonify({'error': str(e)}), 500
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conexion' in locals() and conexion and conexion.is_connected():
            conexion.close()


@caratulas_bp.route('/datos_previo_historico', methods=['GET'])
@requiere_autenticacion
@requiere_modulo('usuarios_caratula')
def obtener_datos_previo_historico():
    """Histórico de la tabla `previo`. Filtra por ?temporada=2025-2026 (opcional).
    Si no se especifica temporada, devuelve todos los snapshots guardados.
    """
    temporada = request.args.get('temporada')
    try:
        conexion = obtener_conexion()
        with conexion.cursor(dictionary=True) as cursor:
            contexto = _contexto_cliente_autenticado(cursor)
            if contexto and contexto.get('id_grupo'):
                filtro_cliente = "(grupo_integral = %s AND es_integral = 1)"
                parametros_cliente = (contexto['id_grupo'],)
            elif contexto:
                filtro_cliente = "LOWER(clave) = LOWER(%s)"
                parametros_cliente = (contexto['clave'],)
            else:
                filtro_cliente = "1 = 1"
                parametros_cliente = ()

            if temporada:
                cursor.execute(
                    f"SELECT * FROM previo_historico WHERE temporada = %s AND {filtro_cliente} "
                    "ORDER BY fecha_snapshot DESC",
                    (temporada, *parametros_cliente)
                )
            else:
                cursor.execute(
                    f"SELECT * FROM previo_historico WHERE {filtro_cliente} ORDER BY fecha_snapshot DESC",
                    parametros_cliente,
                )
            resultados = cursor.fetchall()
            for fila in resultados:
                for key, value in fila.items():
                    if isinstance(value, Decimal):
                        fila[key] = float(value)
                    elif hasattr(value, 'strftime'):
                        fila[key] = value.strftime('%Y-%m-%d')
            if _debe_ocultar_montos_caratula():
                resultados = _redactar_montos_caratula(resultados)
        return jsonify(resultados), 200
    except PermissionError as e:
        return jsonify({'error': str(e)}), 403
    except Exception as e:
        logging.exception("Error en obtener_datos_previo_historico")
        return jsonify({'error': str(e)}), 500
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conexion' in locals() and conexion and conexion.is_connected():
            conexion.close()


@caratulas_bp.route('/datos_evac_a_historico', methods=['GET'])
def obtener_datos_evac_a_historico():
    """Histórico de la tabla `caratula_evac_a`. Filtra por ?temporada=2025-2026 (opcional)."""
    temporada = request.args.get('temporada')
    try:
        conexion = obtener_conexion()
        with conexion.cursor(dictionary=True) as cursor:
            if temporada:
                cursor.execute(
                    "SELECT * FROM caratula_evac_a_historico WHERE temporada = %s ORDER BY fecha_snapshot DESC",
                    (temporada,)
                )
            else:
                cursor.execute("SELECT * FROM caratula_evac_a_historico ORDER BY fecha_snapshot DESC")
            resultados = cursor.fetchall()
            for fila in resultados:
                for key, value in fila.items():
                    if isinstance(value, Decimal):
                        fila[key] = float(value)
                    elif hasattr(value, 'strftime'):
                        fila[key] = value.strftime('%Y-%m-%d')
        return jsonify(resultados), 200
    except Exception as e:
        logging.exception("Error en obtener_datos_evac_a_historico")
        return jsonify({'error': str(e)}), 500
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conexion' in locals() and conexion and conexion.is_connected():
            conexion.close()


@caratulas_bp.route('/datos_evac_b_historico', methods=['GET'])
def obtener_datos_evac_b_historico():
    """Histórico de la tabla `caratula_evac_b`. Filtra por ?temporada=2025-2026 (opcional)."""
    temporada = request.args.get('temporada')
    try:
        conexion = obtener_conexion()
        with conexion.cursor(dictionary=True) as cursor:
            if temporada:
                cursor.execute(
                    "SELECT * FROM caratula_evac_b_historico WHERE temporada = %s ORDER BY fecha_snapshot DESC",
                    (temporada,)
                )
            else:
                cursor.execute("SELECT * FROM caratula_evac_b_historico ORDER BY fecha_snapshot DESC")
            resultados = cursor.fetchall()
            for fila in resultados:
                for key, value in fila.items():
                    if isinstance(value, Decimal):
                        fila[key] = float(value)
                    elif hasattr(value, 'strftime'):
                        fila[key] = value.strftime('%Y-%m-%d')
        return jsonify(resultados), 200
    except Exception as e:
        logging.exception("Error en obtener_datos_evac_b_historico")
        return jsonify({'error': str(e)}), 500
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conexion' in locals() and conexion and conexion.is_connected():
            conexion.close()



@caratulas_bp.route('/validar-bicicletas-megamo-my27', methods=['GET'])
def validar_bicicletas_megamo_my27():
    """
    Diagnóstico NO destructivo.

    Valida cuánto MEGAMO debe moverse de "Otros productos" a BICICLETAS
    para Global, EVAC A y EVAC B.

    NO hace UPDATE, INSERT, DELETE ni TRUNCATE.
    """
    conexion = None
    cursor = None

    try:
        contribuciones = _caratula_obtener_contribuciones_my27()

        base_global = _caratula_resumen_acumulado_lineas(
            contribuciones["global"]
        )
        base_a = _caratula_resumen_acumulado_lineas(
            contribuciones["A"]
        )
        base_b = _caratula_resumen_acumulado_lineas(
            contribuciones["B"]
        )

        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)

        fecha_inicio, fecha_fin = _caratula_rango_my27(cursor)
        megamo = _caratula_megamo_desde_monitor(
            cursor, fecha_inicio, fecha_fin
        )
        multimarcas = _caratula_multimarcas_acumulados(cursor)

        resultado_a = _caratula_armar_validacion_lineas(
            base_a,
            multimarcas["A"],
            megamo["total"]["A"],
        )
        resultado_b = _caratula_armar_validacion_lineas(
            base_b,
            multimarcas["B"],
            megamo["total"]["B"],
        )

        base_global_con_multi = {
            "general": base_global["general"],
            "bicicletas_base": base_global["bicicletas_base"],
            "apparel_syncros_vittoria": base_global[
                "apparel_syncros_vittoria"
            ],
        }
        multi_global = {
            "general": (
                multimarcas["A"]["general"]
                + multimarcas["B"]["general"]
            ),
            "bicicletas_base": (
                multimarcas["A"]["bicicletas_base"]
                + multimarcas["B"]["bicicletas_base"]
            ),
            "apparel_syncros_vittoria": (
                multimarcas["A"]["apparel_syncros_vittoria"]
                + multimarcas["B"]["apparel_syncros_vittoria"]
            ),
        }
        megamo_global = (
            megamo["total"]["A"] + megamo["total"]["B"]
        )

        resultado_global = _caratula_armar_validacion_lineas(
            base_global_con_multi,
            multi_global,
            megamo_global,
        )

        return jsonify({
            "regla": (
                "BICICLETAS = SCOTT + BOLD + MEGAMO; "
                "APPAREL/SYNCROS/VITTORIA se mantiene separado."
            ),
            "rango_my27": {
                "fecha_inicio": (
                    fecha_inicio.strftime("%Y-%m-%d")
                    if hasattr(fecha_inicio, "strftime")
                    else str(fecha_inicio)
                ),
                "fecha_fin": (
                    fecha_fin.strftime("%Y-%m-%d")
                    if hasattr(fecha_fin, "strftime")
                    else str(fecha_fin)
                ),
            },
            "global": resultado_global,
            "evac_a": resultado_a,
            "evac_b": resultado_b,
            "detalle_megamo": {
                "clientes_normales": {
                    "A": float(_caratula_money(megamo["normal"]["A"])),
                    "B": float(_caratula_money(megamo["normal"]["B"])),
                },
                "multimarcas": {
                    "A": float(
                        _caratula_money(megamo["multimarcas"]["A"])
                    ),
                    "B": float(
                        _caratula_money(megamo["multimarcas"]["B"])
                    ),
                },
            },
            "validacion": {
                "megamo_a_mas_b": round(
                    resultado_a["megamo_my27"]
                    + resultado_b["megamo_my27"],
                    2,
                ),
                "megamo_global": round(
                    resultado_global["megamo_my27"], 2
                ),
                "general_a_mas_b": round(
                    resultado_a["acumulado_general"]
                    + resultado_b["acumulado_general"],
                    2,
                ),
                "general_global": round(
                    resultado_global["acumulado_general"], 2
                ),
                "otros_a_mas_b": round(
                    resultado_a["otros_despues_megamo"]
                    + resultado_b["otros_despues_megamo"],
                    2,
                ),
                "otros_global": round(
                    resultado_global["otros_despues_megamo"], 2
                ),
            },
        }), 200

    except Exception as e:
        logging.exception("Error en validar_bicicletas_megamo_my27")
        return jsonify({"error": str(e)}), 500

    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()


@caratulas_bp.route('/debug-caratula-global-otros', methods=['GET'])
def debug_caratula_global_otros():
    conexion = None
    cursor = None

    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)

        claves_excluidas = (
            'JC539','EC216','LC657',
            'GC411','MC679','MC677',
            'LC625','LC626','LC627',
            'LD653','MD680','ID492',
            'LD660','NA718','7C042'
        )

        placeholders = ",".join(["%s"] * len(claves_excluidas))

        # 1) Resumen por cliente desde previo
        cursor.execute(f"""
            SELECT
                clave,
                nombre_cliente,
                acumulado_anticipado,
                avance_global_scott,
                acumulado_bold,
                avance_global_apparel_syncros_vittoria,
                (
                    COALESCE(avance_global_scott, 0)
                    + COALESCE(acumulado_bold, 0)
                    + COALESCE(avance_global_apparel_syncros_vittoria, 0)
                ) AS suma_categorias,
                (
                    COALESCE(acumulado_anticipado, 0)
                    -
                    (
                        COALESCE(avance_global_scott, 0)
                        + COALESCE(acumulado_bold, 0)
                        + COALESCE(avance_global_apparel_syncros_vittoria, 0)
                    )
                ) AS diferencia
            FROM previo
            WHERE clave NOT IN ({placeholders})
            AND nombre_cliente IS NOT NULL
            AND nombre_cliente <> ''
            AND nivel IS NOT NULL
            AND nivel <> ''
            AND clave NOT LIKE 'ODOO%'
            HAVING ABS(diferencia) > 1
            ORDER BY diferencia DESC
        """, claves_excluidas)

        clientes_con_diferencia = cursor.fetchall()

        claves_con_diferencia = [
            row["clave"] for row in clientes_con_diferencia
            if row.get("clave") and not str(row.get("clave")).startswith("Integral")
        ]

        # 2) Si no hay claves normales, devolver solo resumen
        if not claves_con_diferencia:
            return jsonify({
                "mensaje": "No se encontraron clientes normales con diferencia. Solo hay integrales o no hay diferencia.",
                "clientes_con_diferencia": clientes_con_diferencia
            }), 200

        placeholders_clientes = ",".join(["%s"] * len(claves_con_diferencia))

        # 3) Clasificación por marca/categoría desde monitor_odoo
        cursor.execute(f"""
            SELECT
                CASE
                    WHEN UPPER(COALESCE(marca, '')) = 'SCOTT'
                         AND UPPER(COALESCE(apparel, '')) = 'SI'
                        THEN 'APPAREL'

                    WHEN UPPER(COALESCE(marca, '')) = 'SCOTT'
                         AND UPPER(COALESCE(apparel, '')) <> 'SI'
                        THEN 'SCOTT'

                    WHEN UPPER(COALESCE(marca, '')) = 'BOLD'
                        THEN 'BOLD'

                    WHEN UPPER(COALESCE(marca, '')) = 'SYNCROS'
                        THEN 'SYNCROS'

                    WHEN UPPER(COALESCE(marca, '')) = 'VITTORIA'
                        THEN 'VITTORIA'

                    WHEN UPPER(COALESCE(categoria_producto, '')) LIKE 'SCOTT / APPAREL%%'
                        THEN 'APPAREL_CATEGORIA_SIN_MARCA'

                    WHEN UPPER(COALESCE(categoria_producto, '')) LIKE 'SCOTT%%'
                        THEN 'SCOTT_CATEGORIA_SIN_MARCA'

                    WHEN UPPER(COALESCE(categoria_producto, '')) LIKE 'BOLD%%'
                        THEN 'BOLD_CATEGORIA_SIN_MARCA'

                    WHEN UPPER(COALESCE(categoria_producto, '')) LIKE 'SYNCROS%%'
                        THEN 'SYNCROS_CATEGORIA_SIN_MARCA'

                    WHEN UPPER(COALESCE(categoria_producto, '')) LIKE 'VITTORIA%%'
                        THEN 'VITTORIA_CATEGORIA_SIN_MARCA'

                    WHEN UPPER(COALESCE(categoria_producto, '')) LIKE 'SERVICIOS%%'
                        THEN 'SERVICIOS'

                    WHEN COALESCE(marca, '') = ''
                         AND COALESCE(categoria_producto, '') = ''
                        THEN 'SIN_MARCA_SIN_CATEGORIA'

                    ELSE 'OTROS'
                END AS clasificacion_debug,

                COALESCE(marca, '') AS marca,
                COALESCE(subcategoria, '') AS subcategoria,
                COALESCE(apparel, '') AS apparel,
                COALESCE(eride, '') AS eride,
                COALESCE(categoria_producto, '') AS categoria_producto,

                COUNT(*) AS registros,
                ROUND(SUM(COALESCE(venta_total, 0)), 2) AS total
            FROM monitor
            WHERE contacto_referencia IN ({placeholders_clientes})
            GROUP BY
                clasificacion_debug,
                marca,
                subcategoria,
                apparel,
                eride,
                categoria_producto
            HAVING total <> 0
            ORDER BY
                clasificacion_debug,
                total DESC
        """, claves_con_diferencia)

        detalle_categorias = cursor.fetchall()

        # 4) Resumen solo de categorías sospechosas
        cursor.execute(f"""
            SELECT
                CASE
                    WHEN UPPER(COALESCE(marca, '')) = 'SCOTT'
                         AND UPPER(COALESCE(apparel, '')) = 'SI'
                        THEN 'APPAREL'

                    WHEN UPPER(COALESCE(marca, '')) = 'SCOTT'
                         AND UPPER(COALESCE(apparel, '')) <> 'SI'
                        THEN 'SCOTT'

                    WHEN UPPER(COALESCE(marca, '')) = 'BOLD'
                        THEN 'BOLD'

                    WHEN UPPER(COALESCE(marca, '')) = 'SYNCROS'
                        THEN 'SYNCROS'

                    WHEN UPPER(COALESCE(marca, '')) = 'VITTORIA'
                        THEN 'VITTORIA'

                    WHEN UPPER(COALESCE(categoria_producto, '')) LIKE 'SCOTT / APPAREL%%'
                        THEN 'APPAREL_CATEGORIA_SIN_MARCA'

                    WHEN UPPER(COALESCE(categoria_producto, '')) LIKE 'SCOTT%%'
                        THEN 'SCOTT_CATEGORIA_SIN_MARCA'

                    WHEN UPPER(COALESCE(categoria_producto, '')) LIKE 'BOLD%%'
                        THEN 'BOLD_CATEGORIA_SIN_MARCA'

                    WHEN UPPER(COALESCE(categoria_producto, '')) LIKE 'SYNCROS%%'
                        THEN 'SYNCROS_CATEGORIA_SIN_MARCA'

                    WHEN UPPER(COALESCE(categoria_producto, '')) LIKE 'VITTORIA%%'
                        THEN 'VITTORIA_CATEGORIA_SIN_MARCA'

                    WHEN UPPER(COALESCE(categoria_producto, '')) LIKE 'SERVICIOS%%'
                        THEN 'SERVICIOS'

                    WHEN COALESCE(marca, '') = ''
                         AND COALESCE(categoria_producto, '') = ''
                        THEN 'SIN_MARCA_SIN_CATEGORIA'

                    ELSE 'OTROS'
                END AS clasificacion_debug,

                COUNT(*) AS registros,
                ROUND(SUM(COALESCE(venta_total, 0)), 2) AS total
            FROM monitor
            WHERE contacto_referencia IN ({placeholders_clientes})
            GROUP BY clasificacion_debug
            HAVING total <> 0
            ORDER BY total DESC
        """, claves_con_diferencia)

        resumen_clasificacion = cursor.fetchall()

        total_diferencia_previo = sum(float(row["diferencia"] or 0) for row in clientes_con_diferencia)

        return jsonify({
            "objetivo": "Detectar qué categorías/marcas explican la diferencia entre acumulado general y SCOTT + BOLD + APPAREL/SYNCROS/VITTORIA",
            "total_diferencia_previo": round(total_diferencia_previo, 2),
            "clientes_con_diferencia": clientes_con_diferencia,
            "resumen_clasificacion_monitor_odoo": resumen_clasificacion,
            "detalle_categorias_monitor_odoo": detalle_categorias
        }), 200

    except Exception as e:
        logging.exception("Error en debug_caratula_global_otros")
        return jsonify({"error": str(e)}), 500

    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@caratulas_bp.route('/generar-pdf', methods=['POST'])
@requiere_autenticacion
@requiere_modulo('usuarios_caratula')
def generar_caratula_pdf():
    """
    Endpoint para generar un PDF de la carátula en el servidor y devolverlo.
    """
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)
    
    try:
        contexto = _contexto_cliente_autenticado(cursor)
        if _debe_ocultar_montos_caratula():
            return jsonify({"error": "No tienes autorización para exportar la carátula con montos."}), 403
        # 1. Obtener los datos del cliente enviados desde Angular
        data = request.get_json()
        if not data or 'datos_caratula' not in data:
            return jsonify({"error": "No se proporcionaron datos de la carátula"}), 400

        if contexto:
            clave_recibida = str(data.get('datos_caratula', {}).get('clave') or '').strip().lower()
            if contexto.get('id_grupo'):
                # La clave visible del grupo puede ser "Integral N" con un N
                # distinto al id técnico de grupo_clientes. Se valida contra
                # la fila real que pertenece al grupo autenticado.
                cursor.execute(
                    """
                    SELECT 1 FROM previo
                    WHERE grupo_integral = %s AND es_integral = 1
                      AND LOWER(clave) = %s
                    LIMIT 1
                    """,
                    (contexto['id_grupo'], clave_recibida),
                )
                clave_valida = cursor.fetchone() is not None
            else:
                clave_valida = clave_recibida == contexto['clave'].strip().lower()
            if not clave_valida:
                return jsonify({"error": "No puedes exportar la carátula de otro cliente."}), 403

        # 2. Reutilizar la lógica para crear el HTML del PDF
        # La función crear_cuerpo_email devuelve un dict con 'html_caratula_pdf'
        htmls = crear_cuerpo_email(data)
        html_para_pdf = htmls['html_caratula_pdf']

        # 3. Generar el PDF en memoria usando WeasyPrint (import dinámico)
        try:
            from weasyprint import HTML
        except Exception as e:
            return jsonify({
                "error": (
                    "WeasyPrint no disponible en el entorno. "
                    "Instale las dependencias del sistema (p.ej. libgobject, pango) "
                    "o ejecute en un entorno donde WeasyPrint esté instalado. Detalle: " + str(e)
                )
            }), 500

        pdf_bytes = HTML(string=html_para_pdf).write_pdf()

        # 4. Preparar el nombre del archivo
        clave_cliente = data.get('datos_caratula', {}).get('clave', 'SIN_CLAVE')
        filename = f"Caratula_{clave_cliente}.pdf"

        # 5. Crear una respuesta de Flask con el contenido del PDF
        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={"Content-Disposition": f"attachment;filename={filename}"}
        )

    except PermissionError as e:
            return jsonify({"error": str(e)}), 403
    except Exception as e:
            logging.exception("Error al generar PDF")
            return jsonify({"error": f"Error interno al generar el PDF: {str(e)}"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()
    
@caratulas_bp.route('/verificar_grupo_cliente', methods=['GET'])
@requiere_autenticacion
@requiere_modulo('usuarios_caratula')
def verificar_grupo_cliente():
    """
    Verifica si un cliente, basado en su clave, pertenece a un grupo.
    Si pertenece, devuelve el ID y el nombre del grupo.
    """
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)
        contexto = _contexto_cliente_autenticado(cursor)
        clave = contexto['clave'] if contexto else request.args.get('clave')
        if not clave:
            return jsonify({'error': 'Se requiere la clave del cliente'}), 400
        
        query = """
            SELECT
                c.id_grupo,
                g.nombre_grupo
            FROM clientes c
            JOIN grupo_clientes g ON c.id_grupo = g.id
            WHERE c.clave = %s AND c.id_grupo IS NOT NULL;
        """
        cursor.execute(query, (clave,))
        resultado = cursor.fetchone()

        if resultado:
            # ¡Éxito! El cliente tiene un grupo.
            return jsonify({
                'tiene_grupo': True,
                'id_grupo': resultado['id_grupo'],
                'nombre_grupo': resultado['nombre_grupo']
            })
        else:
            # El cliente no pertenece a ningún grupo.
            return jsonify({'tiene_grupo': False})

    except PermissionError as e:
        return jsonify({'error': str(e)}), 403
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conexion' in locals() and conexion.is_connected():
            conexion.close()


@caratulas_bp.route('/debug-odoo', methods=['GET'])
def debug_odoo():
    """Debug helper (dev only): intenta conectarse a Odoo y buscar partners para un cliente dado.
    Devuelve uid y número de partners encontrados o el error.
    """
    cliente = request.args.get('cliente')
    try:
        uid, models = get_odoo_models()
        if not uid or not models:
            return jsonify({'ok': False, 'error': 'No se pudo autenticar en Odoo', 'uid': uid}), 500

        if not cliente:
            return jsonify({'ok': True, 'uid': uid})

        try:
            partners = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'res.partner', 'search_read', [[['name', 'ilike', cliente]]], {'fields': ['id', 'name']})
            return jsonify({'ok': True, 'uid': uid, 'partners_count': len(partners), 'sample': partners[:5]}), 200
        except Exception as ex:
            logging.exception('debug-odoo: error buscando partners')
            return jsonify({'ok': False, 'error': str(ex)}), 500

    except Exception as e:
        logging.exception('debug-odoo: excepción inesperada')
        return jsonify({'ok': False, 'error': str(e)}), 500


@caratulas_bp.route('/detalle-compras-odoo', methods=['GET'])
@requiere_autenticacion
@requiere_modulo('usuarios_caratula')
def detalle_compras_odoo():
    """
    Devuelve el historial completo de órdenes de venta de un cliente desde Odoo.
    - Incluye el estado de la orden (Cotización, Confirmada, Bloqueada, Cancelada).
    - Excluye productos con clave FLE o nombre "Standard delivery".
    - Todos los pickings, moves y move_lines se leen en batch (una sola llamada por modelo)
      para minimizar la latencia.
    """
    cliente = request.args.get('cliente')
    estado_filtro = request.args.get('estado')  # opcional
    # grupo_odoo: Vista Global de integral → consulta DB por claves del grupo, luego Odoo con ref IN
    grupo_odoo = request.args.get('grupo')
    # Cuando ref_exacta=1 la búsqueda es solo por ref exact en res.partner
    # (usado en "Mis Pedidos" de usuarios integrales para evitar matches parciales)
    ref_exacta    = request.args.get('ref_exacta') in ('1', 'true', 'True')
    force_refresh = request.args.get('force_refresh') in ('1', 'true', 'True')
    # temporada: etiqueta histórica opcional (ej. "2025-2026"). Cuando se manda,
    # se acotan las órdenes al rango fijo de esa temporada en vez del f_inicio
    # actual del cliente (que ya se reseteó a la temporada abierta).
    temporada_param = request.args.get('temporada')

    # Los usuarios de portal sólo pueden consultar su propio cliente o su
    # grupo integral. Los parámetros cliente/grupo son sólo funcionales para
    # administración (rol 1).
    _conexion_contexto = None
    _cursor_contexto = None
    try:
        _conexion_contexto = obtener_conexion()
        _cursor_contexto = _conexion_contexto.cursor(dictionary=True)
        contexto = _contexto_cliente_autenticado(_cursor_contexto)
        if contexto:
            if grupo_odoo:
                if not contexto.get('id_grupo'):
                    return jsonify({'error': 'Tu cuenta no pertenece a un grupo integral.'}), 403
                grupo_odoo = str(contexto['id_grupo'])
                cliente = None
            else:
                cliente = contexto['clave']
                grupo_odoo = None
                ref_exacta = True
    except PermissionError as e:
        return jsonify({'error': str(e)}), 403
    finally:
        if _cursor_contexto:
            _cursor_contexto.close()
        if _conexion_contexto and _conexion_contexto.is_connected():
            _conexion_contexto.close()
    try:
        _limit_raw = request.args.get('limit')
        limit = int(_limit_raw) if _limit_raw is not None else None
        if limit is not None and limit <= 0:
            limit = None  # 0 o negativo → sin límite, devolver todo
    except Exception:
        limit = None
    try:
        offset = int(request.args.get('offset')) if request.args.get('offset') is not None else 0
    except Exception:
        offset = 0

    if not cliente and not grupo_odoo:
        return jsonify({'error': 'Se requiere parámetro cliente o grupo'}), 400

    # "Mostrar montos" se administra como capacidad delegable, no como la
    # acción heredada usuarios_caratula/ver_montos.
    debe_ocultar_montos = _debe_ocultar_montos_detalle_compras()

    # ── Caché Redis (5 min TTL) ───────────────────────────────────────────────
    # La clave NO incluye limit/offset/estado: esos parámetros se aplican
    # en caliente sobre los datos cacheados, evitando entradas duplicadas por página.
    # v2 invalida entradas creadas antes del respaldo de subtotal monetario.
    _cache_key = f"monitor_pedidos:v2:{cliente or ''}:{int(bool(ref_exacta))}:{grupo_odoo or ''}:{temporada_param or ''}"
    if _redis and force_refresh:
        try:
            _redis.delete(_cache_key)
            logging.info('Cache invalidado por force_refresh: %s', _cache_key)
        except Exception as _de:
            logging.warning('Redis delete error: %s', _de)
    if _redis and not force_refresh:
        try:
            _raw = _redis.get(_cache_key)
            if _raw:
                _cached = json.loads(_raw)
                _c_resultado, _c_filas, _c_meta_base = _cached
                # Si el caché es de una versión anterior sin etiquetas, descartarlo
                if _c_filas and 'etiquetas' not in _c_filas[0]:
                    _redis.delete(_cache_key)
                    logging.info('Cache obsoleto (sin etiquetas), forzando reconsulta: %s', _cache_key)
                else:
                    if debe_ocultar_montos:
                        _c_resultado, _c_filas, _c_meta_base = _redactar_montos_detalle_compras(
                            _c_resultado, _c_filas, _c_meta_base
                        )
                    _c_filas_fil = [f for f in _c_filas if f.get('estatus_out') == estado_filtro] if estado_filtro else _c_filas
                    _c_total = len(_c_filas_fil)
                    _c_pag = _c_filas_fil[offset: offset + limit] if limit is not None else _c_filas_fil[offset:]
                    _c_nombre = _c_resultado[0].get('cliente') if _c_resultado else None
                    return jsonify({
                        'data': _c_resultado,
                        'rows': _c_pag,
                        'meta': {**_c_meta_base, 'total': _c_total, 'limit': limit, 'offset': offset, 'returned': len(_c_pag)},
                        'cliente': {'nombre_cliente': _c_nombre, 'clave': cliente},
                    }), 200
        except Exception as _ce:
            logging.warning('Redis cache hit error: %s', _ce)

    # ── Fecha de inicio de temporada por cliente / grupo ──────────────────────
    # En lugar del hard-code '2025-07-01' usamos f_inicio de la tabla clientes,
    # lo que permite incluir pedidos de clientes con temporada anticipada.
    FECHA_INICIO_DEFAULT = '2025-07-01'
    fecha_inicio_temporada = FECHA_INICIO_DEFAULT
    fecha_fin_temporada = None  # solo se acota cuando se pide una temporada histórica
    try:
        _conn_fi = obtener_conexion()
        _cur_fi = _conn_fi.cursor(dictionary=True)
        if temporada_param:
            # Temporada histórica: usar el rango fijo de esa temporada, sin
            # importar el f_inicio individual actual del cliente (ya reseteado
            # a la temporada abierta).
            _cur_fi.execute(
                "SELECT fecha_inicio, fecha_fin FROM temporadas WHERE etiqueta = %s",
                (temporada_param,)
            )
            _row_temp = _cur_fi.fetchone()
            if _row_temp:
                fecha_inicio_temporada = str(_row_temp['fecha_inicio'])
                fecha_fin_temporada = str(_row_temp['fecha_fin'])
        elif grupo_odoo:
            _cur_fi.execute(
                "SELECT MIN(f_inicio) AS fi FROM clientes "
                "WHERE id_grupo = %s AND f_inicio IS NOT NULL",
                (grupo_odoo,)
            )
            _row_fi = _cur_fi.fetchone()
            if _row_fi and _row_fi.get('fi'):
                fecha_inicio_temporada = str(_row_fi['fi'])
        elif cliente:
            # Primero buscar por clave exacta, luego por nombre LIKE
            _cur_fi.execute(
                "SELECT f_inicio FROM clientes WHERE clave = %s",
                (cliente,)
            )
            _row_fi = _cur_fi.fetchone()
            if _row_fi and _row_fi.get('f_inicio'):
                fecha_inicio_temporada = str(_row_fi['f_inicio'])
            else:
                _cur_fi.execute(
                    "SELECT MIN(f_inicio) AS fi FROM clientes "
                    "WHERE nombre_cliente LIKE %s AND f_inicio IS NOT NULL",
                    (f'%{cliente}%',)
                )
                _row_fi = _cur_fi.fetchone()
                if _row_fi and _row_fi.get('fi'):
                    fecha_inicio_temporada = str(_row_fi['fi'])
        _cur_fi.close()
        _conn_fi.close()
    except Exception:
        fecha_inicio_temporada = FECHA_INICIO_DEFAULT

    uid, models, odoo_err = get_odoo_models()
    if not uid or not models:
        logging.error('detalle_compras_odoo: no se pudo conectar a Odoo')
        return jsonify({'error': 'No se pudo conectar a Odoo', 'detail': odoo_err}), 500

    # Etiquetas legibles para el estado de la orden de venta
    SALE_STATE_LABELS = {
        'draft':  'Cotización',
        'sent':   'Cotización Enviada',
        'sale':   'Orden Confirmada',
        'done':   'Bloqueada',
        'cancel': 'Cancelada',
    }

    def map_estado_picking(state):
        if state == 'assigned':
            return 'Almacén EB'
        if state == 'done':
            return 'Entregado'
        if state == 'waiting':
            return 'Falta de confirmación'
        if state in ('confirmed', 'partially_available'):
            return 'En tránsito'
        if state == 'cancel':
            return 'Cancelado'
        return state or ''

    def es_producto_excluido(prod):
        """True si el producto es FLE, Standard delivery, Descuento o línea sin SKU de ese tipo."""
        if not prod:
            return False
        code = (prod.get('default_code') or '').strip().upper()
        name = (prod.get('name') or '').strip().lower()
        return (
            code.startswith('FLE')
            or 'standard delivery' in name
            or 'descuento' in name
        )

    try:
        # ── 1) Determinar el dominio de partners según el modo de búsqueda ──────────────
        try:
            if grupo_odoo:
                # Vista Global de integral: obtener todas las claves Y nombres del grupo
                # desde DB, luego buscar en Odoo por ref. Si algún miembro del grupo
                # no tiene ref en Odoo (distribuidor nuevo), se busca también por nombre.
                try:
                    _conn = obtener_conexion()
                    _cur = _conn.cursor(dictionary=True)
                    _cur.execute(
                        "SELECT clave, nombre_cliente FROM clientes "
                        "WHERE id_grupo = %s AND clave IS NOT NULL AND clave != ''",
                        (grupo_odoo,)
                    )
                    _grupo_rows = _cur.fetchall()
                    _claves = [r['clave'] for r in _grupo_rows]
                    _cur.close()
                    _conn.close()
                except Exception as db_ex:
                    return jsonify({'error': f'Error consultando claves del grupo: {str(db_ex)}'}), 500

                if not _claves:
                    return jsonify({'data': [], 'rows': [], 'meta': {'total': 0}}), 200

                partner_domain = [['ref', 'in', _claves]]
            elif ref_exacta:
                # Modo "Mis Pedidos" de integral: match exacto por ref
                partner_domain = [['ref', '=', cliente]]
            else:
                # Modo global/normal: busca por nombre o ref con ilike
                partner_domain = ['|', ['name', 'ilike', cliente], ['ref', 'ilike', cliente]]

            partners = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                'res.partner', 'search_read',
                [partner_domain],
                {'fields': ['id', 'name', 'ref', 'child_ids'], 'limit': 0}
            )

            # ── Fallback por nombre para distribuidores sin ref en Odoo ─────────────
            # Si ref_exacta y no encontró nada: el distribuidor existe en nuestra DB
            # pero no tiene clave/ref asignada en Odoo → buscar por nombre_cliente.
            if not partners and ref_exacta:
                try:
                    _conn_fb = obtener_conexion()
                    _cur_fb = _conn_fb.cursor(dictionary=True)
                    _cur_fb.execute(
                        "SELECT nombre_cliente FROM clientes WHERE clave = %s",
                        (cliente,)
                    )
                    _row_fb = _cur_fb.fetchone()
                    _cur_fb.close()
                    _conn_fb.close()
                    if _row_fb and _row_fb.get('nombre_cliente'):
                        partners = models.execute_kw(
                            ODOO_DB, uid, ODOO_PASSWORD,
                            'res.partner', 'search_read',
                            [[['name', 'ilike', _row_fb['nombre_cliente']]]],
                            {'fields': ['id', 'name', 'ref', 'child_ids'], 'limit': 0}
                        )
                except Exception:
                    pass

            # ── Fallback por nombre para miembros de grupo sin ref en Odoo ──────────
            # Algunos distribuidores del grupo pueden no tener ref en Odoo.
            # Detectamos cuáles faltan comparando los refs devueltos vs los esperados,
            # y hacemos una búsqueda adicional por nombre para cada uno.
            if grupo_odoo and _grupo_rows:
                refs_encontradas = {(p.get('ref') or '').strip() for p in partners}
                claves_sin_match = [
                    r for r in _grupo_rows
                    if r['clave'].strip() not in refs_encontradas and r.get('nombre_cliente')
                ]
                if claves_sin_match:
                    nombres_faltantes = [r['nombre_cliente'] for r in claves_sin_match]
                    try:
                        # Construir dominio OR con todos los nombres faltantes
                        _name_domain: list = []
                        for _nm in nombres_faltantes:
                            _name_domain.extend(['|', ['name', 'ilike', _nm]])
                        # El último '|' sobra; re-construir correctamente con OR apilado
                        _name_domain_clean: list = []
                        for i, _nm in enumerate(nombres_faltantes):
                            if i < len(nombres_faltantes) - 1:
                                _name_domain_clean.append('|')
                            _name_domain_clean.append(['name', 'ilike', _nm])
                        extra_partners = models.execute_kw(
                            ODOO_DB, uid, ODOO_PASSWORD,
                            'res.partner', 'search_read',
                            [_name_domain_clean],
                            {'fields': ['id', 'name', 'ref', 'child_ids'], 'limit': 0}
                        )
                        # Mergear evitando duplicados por id
                        existing_ids = {p['id'] for p in partners}
                        partners = list(partners) + [
                            p for p in extra_partners if p['id'] not in existing_ids
                        ]
                    except Exception:
                        pass

        except Exception as ex:
            return jsonify({'error': f'Error consultando res.partner: {str(ex)}'}), 500

        if not partners:
            return jsonify({'data': [], 'rows': [], 'meta': {'total': 0}}), 200

        # Expandimos child_ids excluyendo hijos que tienen su propio ref registrado
        # como cliente independiente en nuestra DB (evita doble conteo entre sucursales).
        _clientes_registrados: set = set()
        try:
            _conn_reg = obtener_conexion()
            _cur_reg = _conn_reg.cursor()
            _cur_reg.execute(
                "SELECT UPPER(TRIM(clave)) FROM clientes "
                "WHERE clave IS NOT NULL AND clave != ''"
            )
            _clientes_registrados = {row[0] for row in _cur_reg.fetchall()}
            _cur_reg.close()
            _conn_reg.close()
        except Exception:
            pass  # fallback: incluye todos los hijos (comportamiento anterior)

        all_partner_ids = set()
        for p in partners:
            all_partner_ids.add(p['id'])
            child_ids_list = p.get('child_ids') or []
            if child_ids_list:
                try:
                    children_data = models.execute_kw(
                        ODOO_DB, uid, ODOO_PASSWORD,
                        'res.partner', 'read',
                        [child_ids_list],
                        {'fields': ['id', 'ref']}
                    )
                    for child in children_data:
                        child_ref = (child.get('ref') or '').strip().upper()
                        # Solo incluir hijos que NO son clientes independientes registrados
                        if not child_ref or child_ref not in _clientes_registrados:
                            all_partner_ids.add(child['id'])
                except Exception:
                    # Fallback: incluir todos los hijos del partner
                    for cid in child_ids_list:
                        all_partner_ids.add(cid)
        partner_ids = list(all_partner_ids)

        # ── 2) Traer órdenes de venta desde la fecha de inicio de temporada del cliente ──
        # Excluimos únicamente borradores (state='draft') — órdenes que aún no han
        # sido confirmadas y no deben aparecer en el monitor.
        # Las órdenes canceladas (state='cancel') SÍ se muestran con estatus "Cancelado".
        #
        # Para incluir "anticipos de temporada" (órdenes creadas antes del inicio pero
        # facturadas dentro de la temporada), extendemos el rango 90 días hacia atrás.
        # 90 días cubre el periodo típico de anticipos (mayo-junio para temporada jul).
        # Usar el año completo (ene) trae demasiadas órdenes y dispara cientos de
        # llamadas extra a Odoo para verificar facturas, causando timeouts de 5+ min.
        try:
            from datetime import datetime as _dt, timedelta as _td
            _fecha_inicio_dt = _dt.strptime(fecha_inicio_temporada, '%Y-%m-%d')
            _fecha_inicio_anticipos = (_fecha_inicio_dt - _td(days=90)).strftime('%Y-%m-%d')
            _domain_orders = [['partner_id', 'in', partner_ids],
                               ['date_order', '>=', _fecha_inicio_anticipos],
                               ['state', '!=', 'draft']]
            if fecha_fin_temporada:
                _domain_orders.append(['date_order', '<=', fecha_fin_temporada])
            orders = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                'sale.order', 'search_read',
                [_domain_orders],
                {'fields': ['id', 'name', 'date_order', 'partner_id', 'order_line',
                            'amount_total', 'state', 'tag_ids', 'invoice_ids'],
                 'order': 'date_order desc', 'limit': 0}
            )
        except Exception as ex:
            return jsonify({'error': f'Error consultando sale.order: {str(ex)}'}), 500

        if not orders:
            return jsonify({'data': [], 'rows': [], 'meta': {'total': 0}}), 200

        # Filtrar anticipos: de las órdenes pre-temporada, conservar solo las que
        # tienen TODAS sus facturas dentro de la temporada (ninguna antes de f_inicio).
        # Esto distingue anticipos MY27 (creados en mayo-jun, facturados en jul+)
        # de carryovers MY26 (órdenes antiguas con una pequeña factura residual en jul).
        _presea_invoice_ids: list[int] = []
        _presea_by_inv: dict[int, int] = {}  # invoice_id → order_id
        for _o in orders:
            if (_o.get('date_order') or '')[:10] < fecha_inicio_temporada:
                for _inv_id in (_o.get('invoice_ids') or []):
                    _presea_invoice_ids.append(_inv_id)
                    _presea_by_inv[_inv_id] = _o['id']

        _valid_presea_ids: set[int] = set()
        if _presea_invoice_ids:
            try:
                _inv_rows = models.execute_kw(
                    ODOO_DB, uid, ODOO_PASSWORD,
                    'account.move', 'read',
                    [list(set(_presea_invoice_ids))],
                    {'fields': ['id', 'invoice_date', 'state']}
                )
                # Agrupar facturas por orden para evaluar el conjunto completo
                _inv_by_order: dict[int, list] = {}
                for _inv in _inv_rows:
                    _oid = _presea_by_inv[_inv['id']]
                    _inv_by_order.setdefault(_oid, []).append(_inv)

                for _oid, _invs in _inv_by_order.items():
                    posted = [i for i in _invs if i.get('state') == 'posted']
                    if not posted:
                        continue
                    # Antipo MY27: TODAS las facturas publicadas son de la temporada actual.
                    # Si alguna es anterior, es un carryover MY26 → excluir.
                    if all((i.get('invoice_date') or '') >= fecha_inicio_temporada for i in posted):
                        _valid_presea_ids.add(_oid)
            except Exception:
                pass  # si falla, excluimos las pre-temporada (comportamiento conservador)

        orders = [
            _o for _o in orders
            if (_o.get('date_order') or '')[:10] >= fecha_inicio_temporada
            or _o['id'] in _valid_presea_ids
        ]

        if not orders:
            return jsonify({'data': [], 'rows': [], 'meta': {'total': 0}}), 200

        # ── 2.5) Batch-leer nombres de etiquetas ─────────────────────────────────
        all_tag_ids = set()
        for o in orders:
            for tid in (o.get('tag_ids') or []):
                all_tag_ids.add(tid)

        tags_map: dict = {}
        if all_tag_ids:
            for _tag_model in ('crm.tag', 'sale.order.tag'):
                try:
                    _tag_rows = models.execute_kw(
                        ODOO_DB, uid, ODOO_PASSWORD,
                        _tag_model, 'read',
                        [list(all_tag_ids)],
                        {'fields': ['id', 'name']}
                    )
                    tags_map = {t['id']: t['name'] for t in _tag_rows}
                    break
                except Exception:
                    continue

        # ── 3) Leer líneas en batch ───────────────────────────────────────────────
        all_line_ids = []
        for o in orders:
            all_line_ids.extend(o.get('order_line') or [])

        lines_map = {}
        if all_line_ids:
            try:
                sol_want = ['id', 'order_id', 'product_id', 'name', 'product_uom_qty',
                             'qty_delivered', 'price_unit', 'discount', 'price_total',
                             'price_subtotal']
                # forecast_expected_date: fecha en que Odoo pronostica disponibilidad
                # is_mto: True si la línea usa ruta MTO (pedido a proveedor bajo demanda)
                sol_all_keys = set()
                try:
                    _sf = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD,
                        'sale.order.line', 'fields_get', [], {'attributes': ['string']})
                    sol_all_keys = set(_sf.keys())
                except Exception:
                    pass
                if 'forecast_expected_date' in sol_all_keys:
                    sol_want.append('forecast_expected_date')
                if 'is_mto' in sol_all_keys:
                    sol_want.append('is_mto')
                # search_read falla cuando el usuario no tiene acceso a product.product.
                # read() con IDs explícitos funciona, PERO algunos campos computados
                # (como is_mto) requieren product.product y también fallan.
                # Estrategia: intentar con todos los campos; si falla, quitar is_mto y reintentar.
                _SOL_BATCH = 500

                def _leer_lineas(fields):
                    result = {}
                    for _i in range(0, len(all_line_ids), _SOL_BATCH):
                        _chunk = all_line_ids[_i:_i + _SOL_BATCH]
                        for l in models.execute_kw(
                            ODOO_DB, uid, ODOO_PASSWORD,
                            'sale.order.line', 'read',
                            [_chunk],
                            {'fields': fields}
                        ):
                            result[l['id']] = l
                    return result

                try:
                    lines_map = _leer_lineas(sol_want)
                except Exception:
                    # is_mto (u otro campo computado) puede requerir product.product; omitirlo.
                    sol_want_fallback = [f for f in sol_want if f != 'is_mto']
                    try:
                        lines_map = _leer_lineas(sol_want_fallback)
                    except Exception:
                        pass
            except Exception:
                pass

        # ── 4) Construir products_map desde display_name de las líneas ────────────
        # product.product.search_read requiere permisos que el usuario puede no tener.
        # El display_name en sale.order.line ya incluye el código "[SKU] Nombre",
        # así que extraemos la info directamente sin llamada adicional a Odoo.
        _CODE_RE_SOL = re.compile(r'^\[([^\]]+)\]\s*(.*)')
        products_map = {}
        for l in lines_map.values():
            pid_raw = l.get('product_id')
            if not pid_raw:
                continue
            pid = pid_raw[0]
            if pid in products_map:
                continue
            display_name = pid_raw[1] if isinstance(pid_raw, (list, tuple)) and len(pid_raw) > 1 else ''
            m_code = _CODE_RE_SOL.match(display_name)
            if m_code:
                code = m_code.group(1).strip()
                pname = m_code.group(2).strip()
            else:
                code = ''
                pname = display_name.strip()
            products_map[pid] = {
                'id': pid,
                'default_code': code,
                'name': pname,
                'display_name': display_name,
            }


        # ── 5) Leer facturas en batch ─────────────────────────────────────────────
        order_names = [o['name'] for o in orders if o.get('name')]
        invoices_map_by_origin = {}
        try:
            inv_rows = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                'account.move', 'search_read',
                [[['origin', 'in', order_names], ['move_type', '=', 'out_invoice']]],
                {'fields': ['id', 'name', 'invoice_date', 'origin', 'state', 'amount_total'], 'limit': 0}
            )
            for m in inv_rows:
                invoices_map_by_origin.setdefault(m.get('origin'), []).append(m)
        except Exception:
            pass

        # ── 6) Determinar campos disponibles en stock.* UNA SOLA VEZ ─────────────
        picking_keys = set()
        try:
            pf = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'stock.picking', 'fields_get', [], {})
            picking_keys = set(pf.keys()) if isinstance(pf, dict) else set()
        except Exception:
            pass

        move_keys = set()
        try:
            mf = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'stock.move', 'fields_get', [], {})
            move_keys = set(mf.keys()) if isinstance(mf, dict) else set()
        except Exception:
            pass

        mline_keys = set()
        try:
            mlf = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'stock.move.line', 'fields_get', [], {})
            mline_keys = set(mlf.keys()) if isinstance(mlf, dict) else set()
        except Exception:
            pass

        # ── 7) Leer TODOS los pickings en un solo batch ───────────────────────────
        picking_want_fields = ['name', 'state', 'picking_type_id', 'picking_type_code', 'scheduled_date', 'origin']
        if 'move_ids' in picking_keys:
            picking_want_fields.append('move_ids')
        if 'move_line_ids' in picking_keys:
            picking_want_fields.append('move_line_ids')

        all_pickings = []
        try:
            all_pickings = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                'stock.picking', 'search_read',
                [[['origin', 'in', order_names]]],
                {'fields': picking_want_fields, 'limit': 0}
            )
        except Exception:
            pass

        pickings_by_origin = {}
        for p in all_pickings:
            pickings_by_origin.setdefault(p.get('origin'), []).append(p)

        # ── 8) Leer TODOS los stock.move en un solo batch ─────────────────────────
        all_move_ids = []
        for p in all_pickings:
            all_move_ids.extend(p.get('move_ids') or [])

        m_fields = ['product_id', 'product_uom_qty', 'state', 'picking_id']
        if 'quantity_done' in move_keys:
            m_fields.append('quantity_done')
        elif 'qty_done' in move_keys:
            m_fields.append('qty_done')
        if 'purchase_line_id' in move_keys:
            m_fields.append('purchase_line_id')
        # move_orig_ids: IDs de los moves de los que depende este move (cadena MTO).
        # Un outgoing 'waiting' depende del incoming de la OC → accedemos a su purchase_line_id.
        if 'move_orig_ids' in move_keys:
            m_fields.append('move_orig_ids')

        moves_by_picking = {}
        move_orig_map: dict = {}  # move_id → list[orig_move_id]
        if all_move_ids:
            try:
                move_rows = models.execute_kw(
                    ODOO_DB, uid, ODOO_PASSWORD,
                    'stock.move', 'search_read',
                    [[['id', 'in', all_move_ids]]],
                    {'fields': m_fields, 'limit': 0}
                )
                for m in move_rows:
                    p_id = m.get('picking_id') and m['picking_id'][0]
                    if p_id:
                        moves_by_picking.setdefault(p_id, []).append(m)
                    orig_ids = m.get('move_orig_ids') or []
                    if orig_ids:
                        move_orig_map[m['id']] = orig_ids
            except Exception:
                pass

        # ── 8.5) Leer purchase.order.line → fecha esperada de entrega ──────────────
        # Hay dos rutas para encontrar la OC ligada a un outgoing move:
        #   A) Directa: el propio outgoing move tiene purchase_line_id (raro en v15+)
        #   B) Indirecta (MTO): outgoing 'waiting' → move_orig_ids → incoming move → purchase_line_id
        pol_fecha_map: dict = {}          # pol_id → {date_planned, po_name}
        upstream_move_pol: dict = {}      # orig_move_id → pol_id  (para resolución en paso 10)
        try:
            pol_ids_set: set = set()

            # Ruta A — POLs directos en los moves que ya leímos
            for moves_list in moves_by_picking.values():
                for m in moves_list:
                    pol_ref = m.get('purchase_line_id')
                    if pol_ref:
                        pol_id_val = pol_ref[0] if isinstance(pol_ref, (list, tuple)) else pol_ref
                        if isinstance(pol_id_val, int) and pol_id_val > 0:
                            pol_ids_set.add(pol_id_val)

            # Ruta B — Leer upstream moves (move_orig_ids de los outgoing waiting)
            # para alcanzar el incoming que tiene purchase_line_id
            all_orig_ids: set = set()
            for orig_list in move_orig_map.values():
                all_orig_ids.update(orig_list)
            if all_orig_ids:
                try:
                    upstream_rows = models.execute_kw(
                        ODOO_DB, uid, ODOO_PASSWORD,
                        'stock.move', 'search_read',
                        [[['id', 'in', list(all_orig_ids)]]],
                        {'fields': ['id', 'purchase_line_id', 'state'], 'limit': 0}
                    )
                    for um in upstream_rows:
                        pol_ref = um.get('purchase_line_id')
                        if pol_ref:
                            pol_id_val = pol_ref[0] if isinstance(pol_ref, (list, tuple)) else pol_ref
                            if isinstance(pol_id_val, int) and pol_id_val > 0:
                                pol_ids_set.add(pol_id_val)
                                upstream_move_pol[um['id']] = pol_id_val
                except Exception as _ex_up:
                    logging.warning('detalle_compras_odoo: error al leer upstream moves: %s', _ex_up)

            # Leer todas las POL en un solo batch
            if pol_ids_set:
                pol_rows = models.execute_kw(
                    ODOO_DB, uid, ODOO_PASSWORD,
                    'purchase.order.line', 'search_read',
                    [[['id', 'in', list(pol_ids_set)]]],
                    {'fields': ['id', 'date_planned', 'product_id', 'order_id'], 'limit': 0}
                )
                for pol in pol_rows:
                    dp = pol.get('date_planned')
                    if dp:
                        pol_fecha_map[pol['id']] = {
                            'date_planned': str(dp),
                            'po_name': pol['order_id'][1] if pol.get('order_id') else None
                        }
        except Exception as _ex_pol:
            logging.warning('detalle_compras_odoo: error al leer purchase.order.line: %s', _ex_pol)

        # ── 9) Leer TODOS los stock.move.line en un solo batch ───────────────────
        all_mline_ids = []
        for p in all_pickings:
            all_mline_ids.extend(p.get('move_line_ids') or [])

        ml_fields = ['product_id', 'product_uom_qty', 'state', 'picking_id']
        if 'qty_done' in mline_keys:
            ml_fields.append('qty_done')
        elif 'quantity_done' in mline_keys:
            ml_fields.append('quantity_done')

        mlines_by_picking = {}
        if all_mline_ids:
            try:
                ml_rows = models.execute_kw(
                    ODOO_DB, uid, ODOO_PASSWORD,
                    'stock.move.line', 'search_read',
                    [[['id', 'in', all_mline_ids]]],
                    {'fields': ml_fields, 'limit': 0}
                )
                for ml in ml_rows:
                    p_id = ml.get('picking_id') and ml['picking_id'][0]
                    if p_id:
                        mlines_by_picking.setdefault(p_id, []).append(ml)
            except Exception:
                pass

        # ── 10) Mapa de entrega por (orden, product_id) ─────────────────────────
        # Procesamos outgoing (entrega final) E internal (PICK) para poder
        # desambiguar el estado 'waiting':
        #   - outgoing waiting + internal assigned/done  → mercancía EN bodega (Almacén EB)
        #   - outgoing waiting + internal sin reserva    → mercancía en tránsito del proveedor
        # ────────────────────────────────────────────────────────────────────────────────────
        # Clave = (nombre_orden, product_id_odoo)  ← combinación única por producto por orden
        entrega_por_prod = {}
        for p in all_pickings:
            ptype = p.get('picking_type_code') or ''
            # Ignoramos recepciones de proveedor (incoming) — su origin es la OC, no la OV
            if ptype not in ('outgoing', 'internal'):
                continue
            is_outgoing = (ptype == 'outgoing')
            origin = p.get('origin') or ''
            p_id = p['id']
            for m in (moves_by_picking.get(p_id) or []):
                prod_id = m.get('product_id') and m['product_id'][0]
                if not prod_id or not origin:
                    continue
                key = (origin, prod_id)
                if key not in entrega_por_prod:
                    entrega_por_prod[key] = {
                        'qty': 0.0, 'done': 0.0,
                        'estados_out': set(),   # estados de stock.move outgoing
                        'estados_int': set(),   # estados de stock.move interno (PICK)
                        'has_purchase': False,  # True si algún move apunta a una OC
                        'fecha_esperada': None, 'po_name': None
                    }
                raw_state = m.get('state') or ''
                if is_outgoing:
                    entrega_por_prod[key]['qty'] += float(m.get('product_uom_qty') or 0)
                    done_qty = m.get('quantity_done') or m.get('qty_done') or 0
                    entrega_por_prod[key]['done'] += float(done_qty)
                    if raw_state:
                        entrega_por_prod[key]['estados_out'].add(raw_state)
                else:
                    if raw_state:
                        entrega_por_prod[key]['estados_int'].add(raw_state)
                # ── Fecha esperada / vínculo OC ─────────────────────────────────────
                # Ruta A: purchase_line_id directo en este move
                pol_ref = m.get('purchase_line_id')
                pol_id_direct = None
                if pol_ref:
                    pol_id_direct = pol_ref[0] if isinstance(pol_ref, (list, tuple)) else pol_ref
                    entrega_por_prod[key]['has_purchase'] = True
                    if isinstance(pol_id_direct, int) and pol_id_direct in pol_fecha_map:
                        if entrega_por_prod[key]['fecha_esperada'] is None:
                            entrega_por_prod[key]['fecha_esperada'] = pol_fecha_map[pol_id_direct]['date_planned']
                            entrega_por_prod[key]['po_name'] = pol_fecha_map[pol_id_direct].get('po_name')

                # Ruta B: buscar en los upstream moves (cadena MTO)
                if not entrega_por_prod[key]['has_purchase']:
                    for orig_id in (move_orig_map.get(m['id']) or []):
                        pol_id_up = upstream_move_pol.get(orig_id)
                        if pol_id_up:
                            entrega_por_prod[key]['has_purchase'] = True
                            if entrega_por_prod[key]['fecha_esperada'] is None and pol_id_up in pol_fecha_map:
                                entrega_por_prod[key]['fecha_esperada'] = pol_fecha_map[pol_id_up]['date_planned']
                                entrega_por_prod[key]['po_name'] = pol_fecha_map[pol_id_up].get('po_name')
                            break

        # ── 10.5) Fecha esperada via POL directo al producto (sin cadena MTO directa) ──
        # Cubre dos escenarios sin enlace directo a OC:
        #   A) outgoing en confirmed/partially_available  (move directo sin PICK intermedio)
        #   B) outgoing waiting con PICK interno en confirmed (flujo multi-paso sin cadena MTO)
        # IMPORTANTE: para evitar mostrar fechas anteriores a la orden de venta
        # (que pertenecen a otras OCs), filtramos los POLs por fecha >= fecha_orden.
        order_date_map = {o['name']: str(o.get('date_order') or '')[:10] for o in orders}

        pending_items = {
            (order_name, prod_id): order_date_map.get(order_name, '')
            for (order_name, prod_id), info in entrega_por_prod.items()
            if not info['has_purchase'] and (
                info['estados_out'] & {'confirmed', 'partially_available'}
                or 'confirmed' in info.get('estados_int', set())
            )
        }
        pending_prod_ids = {pid for (_, pid) in pending_items}

        if pending_prod_ids:
            try:
                pol_fallback_rows = models.execute_kw(
                    ODOO_DB, uid, ODOO_PASSWORD,
                    'purchase.order.line', 'search_read',
                    [[['product_id', 'in', list(pending_prod_ids)],
                      ['order_id.state', 'in', ['purchase', 'done']]]],
                    {'fields': ['id', 'product_id', 'date_planned', 'order_id',
                                'qty_received', 'product_qty'], 'limit': 0}
                )
                # Filtrar en Python: sólo líneas con pendiente de recibir
                pol_fallback_rows = [
                    p for p in pol_fallback_rows
                    if float(p.get('qty_received') or 0) < float(p.get('product_qty') or 0)
                ]

                # Agrupar todos los POLs disponibles por product_id, ordenados por fecha
                pols_by_prod: dict = {}  # prod_id → [{date_planned, po_name}, ...]
                for pol in pol_fallback_rows:
                    dp = pol.get('date_planned')
                    if not dp:
                        continue
                    pid = pol['product_id'][0] if pol.get('product_id') else None
                    if not pid:
                        continue
                    pols_by_prod.setdefault(pid, []).append({
                        'date_planned': str(dp)[:10],
                        'po_name': pol['order_id'][1] if pol.get('order_id') else None
                    })
                for pid in pols_by_prod:
                    pols_by_prod[pid].sort(key=lambda x: x['date_planned'])

                # Por cada (orden, producto) elegir el POL más cercano
                # cuya fecha sea >= fecha de la orden de venta.
                # Si ninguno cumple esa condición (entregas tardías), tomar la más reciente.
                for (order_name, prod_id), order_date in pending_items.items():
                    info = entrega_por_prod.get((order_name, prod_id))
                    if not info or info['has_purchase']:
                        continue
                    cands = pols_by_prod.get(prod_id, [])
                    if not cands:
                        continue
                    # Buscar el primer POL con fecha >= fecha de la OV
                    chosen = next(
                        (c for c in cands if c['date_planned'] >= order_date),
                        None
                    )
                    # Siempre marcar has_purchase=True si existe cualquier POL
                    # (para que el producto aparezca como "En tránsito")
                    info['has_purchase'] = True
                    # Solo asignar fecha si es igual o posterior a la OV:
                    # fechas del pasado no aportan información útil al usuario
                    if chosen is not None and info['fecha_esperada'] is None:
                        info['fecha_esperada'] = chosen['date_planned']
                        info['po_name'] = chosen.get('po_name')
            except Exception as _ex_fb:
                logging.warning('detalle_compras_odoo: error al leer POL fallback: %s', _ex_fb)


        def estatus_por_producto(order_name: str, product_id):
            """Devuelve el estatus de entrega de un producto específico en una orden.

            Prioridad revisada para almacenes multi-paso (Pick + Ship):
            1. Entregado / Entregado Parcial
            2. En tránsito  — 'confirmed'/'partially_available' en outgoing,
               incluso si coexiste con 'assigned' (parte en ruta, parte lista)
            3. Almacén EB   — 'assigned' en outgoing  O  'waiting' en outgoing
               con PICK interno reservado (mercancía ya en bodega, falta mover)
            4. Falta de confirmación — 'waiting' sin stock en bodega y sin OC
            5. Cancelado
            """
            info = entrega_por_prod.get((order_name, product_id))
            if not info or not info['estados_out']:
                return None
            estados_out = info['estados_out']
            estados_int = info.get('estados_int', set())
            has_purchase = info.get('has_purchase', False)
            qty  = info['qty']
            done = info['done']

            # 1. Entregado
            if 'done' in estados_out:
                if qty > 0 and done >= qty:
                    return 'Entregado'
                elif done > 0:
                    return 'Entregado Parcial'
                return 'Entregado'

            # 2. En tránsito — prioridad sobre Almacén EB
            #    Si alguna unidad aún no está disponible es la info más urgente
            if estados_out & {'confirmed', 'partially_available'}:
                return 'En tránsito'

            # 3. Almacén EB — out reservado en zona de salida
            if 'assigned' in estados_out:
                return 'Almacén EB'

            # 4. Waiting — desambiguar con move interno (PICK)
            if 'waiting' in estados_out:
                # PICK interno con reserva → mercancía físicamente en bodega
                if estados_int & {'assigned', 'done'}:
                    return 'Almacén EB'
                # Hay vínculo a OC (directo o via fallback) → en tránsito del proveedor
                if has_purchase:
                    return 'En tránsito'
                # Sin stock y sin OC → falta confirmar abasto
                return 'Falta de confirmación'

            # 5. Cancelado
            if 'cancel' in estados_out:
                return 'Cancelado'

            return map_estado_picking(next(iter(estados_out)))

        # ── 11) Construir resultado ───────────────────────────────────────────────
        resultado = []
        filas_planas = []

        # Periodo activo (MY27 = 2026-2027): deriva del mes actual
        from datetime import date as _date
        _hoy = _date.today()
        _py1 = _hoy.year if _hoy.month >= 7 else _hoy.year - 1
        _periodo_activo = f'{_py1}-{_py1 + 1}'
        _periodo_start  = f'{_py1}-07-01'   # inicio MY27: 2026-07-01
        _periodo_end    = f'{_py1 + 1}-06-30'  # fin MY27: 2027-06-30

        # Pre-cargar SKUs del forecast (solo periodo activo) para marcar líneas con de_proyeccion
        def _norm_fc(s): return re.sub(r'[\-\s]', '', str(s or '')).upper()
        _forecast_skus: set = set()
        try:
            _conn_fc = obtener_conexion()
            _cur_fc = _conn_fc.cursor(dictionary=True)
            if grupo_odoo:
                _cur_fc.execute(
                    "SELECT sku FROM forecast_proyecciones "
                    "WHERE periodo = %s AND clave_cliente IN "
                    "  (SELECT clave FROM clientes WHERE id_grupo = %s)",
                    (_periodo_activo, grupo_odoo)
                )
            else:
                _cur_fc.execute(
                    "SELECT sku FROM forecast_proyecciones "
                    "WHERE periodo = %s AND clave_cliente = %s",
                    (_periodo_activo, cliente)
                )
            _forecast_skus = {_norm_fc(r['sku']) for r in _cur_fc.fetchall()}
            _cur_fc.close()
            _conn_fc.close()
        except Exception as _ex_fc:
            logging.warning('detalle_compras_odoo: error al leer forecast SKUs: %s', _ex_fc)

        for o in orders:
            estado_orden_raw = o.get('state') or ''
            estado_orden = SALE_STATE_LABELS.get(estado_orden_raw, estado_orden_raw)

            _etiquetas = [tags_map[tid] for tid in (o.get('tag_ids') or []) if tid in tags_map]
            order_obj = {
                'orden': o.get('name'),
                'fecha': o.get('date_order'),
                'cliente': o['partner_id'][1] if o.get('partner_id') else None,
                'monto_total': float(o.get('amount_total') or 0),
                'estado_orden': estado_orden,
                'estado_orden_raw': estado_orden_raw,
                'etiquetas': _etiquetas,
                'lineas': [],
                'pickings': []
            }

            # Líneas — filtrando FLE / Standard delivery
            for lid in (o.get('order_line') or []):
                l = lines_map.get(lid)
                if not l:
                    continue
                pid = l.get('product_id') and l['product_id'][0]
                prod = products_map.get(pid) if pid else None
                if es_producto_excluido(prod):
                    continue
                clave = prod.get('default_code') if prod else None
                if prod:
                    dn = prod.get('display_name') or prod.get('name') or ''
                    dc = prod.get('default_code') or ''
                    producto_nombre = dn[len(f'[{dc}] '):] if (dc and dn.startswith(f'[{dc}] ')) else dn
                else:
                    producto_nombre = l['product_id'][1] if l.get('product_id') else None
                cantidad = float(l.get('product_uom_qty') or 0)
                # Omitir líneas con cantidad 0 (producto cancelado/removido sin borrar la línea)
                if cantidad == 0:
                    continue
                qty_entregada = float(l.get('qty_delivered') or 0)
                # Usar price_total de Odoo (incluye el IVA real de cada producto).
                # Algunas líneas históricas no exponen ese campo aunque sí entregan
                # price_subtotal; en ese caso aplicamos el mismo respaldo que ya
                # utilizaba este endpoint para evitar convertir un importe real en 0.
                price_total_odoo = float(l.get('price_total') or 0)
                if price_total_odoo <= 0 and cantidad > 0:
                    subtotal_odoo = float(l.get('price_subtotal') or 0)
                    descuento = float(l.get('discount') or 0)
                    precio_unitario = float(l.get('price_unit') or 0)
                    if subtotal_odoo > 0:
                        price_total_odoo = round(subtotal_odoo * 1.16, 2)
                    elif precio_unitario > 0:
                        price_total_odoo = round(precio_unitario * (1 - descuento / 100) * 1.16 * cantidad, 2)
                precio = round(price_total_odoo / cantidad, 4) if cantidad > 0 else 0
                total_entregado_linea = round((qty_entregada / cantidad) * price_total_odoo, 2) if cantidad > 0 else 0
                order_obj['lineas'].append({
                    'id': l['id'],
                    'product_id_odoo': pid,   # guardamos el ID para cruzar con moves
                    'producto': producto_nombre,
                    'clave_producto': clave,
                    'descripcion': l.get('name'),
                    'cantidad_pedida': cantidad,
                    'cantidad_entregada': qty_entregada,
                    'precio_unitario': precio,
                    'total_linea': round(price_total_odoo, 2),
                    'total_entregado_linea': total_entregado_linea,
                    'forecast_expected_date': l.get('forecast_expected_date') or None,
                    'is_mto': bool(l.get('is_mto')),
                })

            # Pickings (todos leídos en batch, solo se indexan aquí)
            for p in (pickings_by_origin.get(o.get('name')) or []):
                estado_mapeado = map_estado_picking(p.get('state'))
                ptype_code = p.get('picking_type_code') or ''
                p_id = p['id']
                moves_result = []

                for m in (moves_by_picking.get(p_id) or []):
                    cantidad_hecha = m.get('quantity_done') or m.get('qty_done') or 0
                    moves_result.append({
                        'producto': m['product_id'][1] if m.get('product_id') else None,
                        'cantidad': float(m.get('product_uom_qty') or 0),
                        'cantidad_hecha': float(cantidad_hecha),
                        'state': m.get('state')
                    })

                for ml in (mlines_by_picking.get(p_id) or []):
                    cantidad_hecha_ml = ml.get('qty_done') or ml.get('quantity_done') or 0
                    moves_result.append({
                        'producto': ml['product_id'][1] if ml.get('product_id') else None,
                        'cantidad': float(ml.get('product_uom_qty') or 0),
                        'cantidad_hecha': float(cantidad_hecha_ml),
                        'state': ml.get('state')
                    })

                order_obj['pickings'].append({
                    'picking': p.get('name'),
                    'estado': estado_mapeado,
                    'picking_type_code': ptype_code,
                    'scheduled_date': p.get('scheduled_date'),
                    'moves': moves_result
                })

            # Filas planas para la tabla del frontend
            facturas_rel = invoices_map_by_origin.get(o.get('name'), [])
            factura_nombre = facturas_rel[0]['name'] if facturas_rel else None
            fecha_factura = facturas_rel[0].get('invoice_date') if facturas_rel else None
            order_name = o.get('name')

            for lin in order_obj['lineas']:
                # ── Estatus por picking (cruce con moves)
                estatus_out_lin = estatus_por_producto(order_name, lin.get('product_id_odoo'))
                # Fallback: si no hay moves en pickings outgoing, usar el primer picking outgoing de la orden
                if estatus_out_lin is None:
                    pickings_out = [p for p in order_obj['pickings'] if (p.get('picking_type_code') or '') == 'outgoing']
                    if pickings_out:
                        estatus_out_lin = pickings_out[0]['estado']
                    elif order_obj['pickings']:
                        estatus_out_lin = order_obj['pickings'][0]['estado']
                # Último fallback: si la orden de venta está cancelada y no hay pickings
                # (se canceló antes de crear movimientos), reflejar "Cancelado" directamente.
                if estatus_out_lin is None and estado_orden_raw == 'cancel':
                    estatus_out_lin = 'Cancelado'

                # ── Override con qty_delivered de Odoo (campo autoritativo)
                # qty_delivered es el campo que Odoo calcula directamente;
                # evita que movimientos multi-paso pasen desapercibidos.
                qty_ped = lin.get('cantidad_pedida', 0)
                qty_del = lin.get('cantidad_entregada', 0)
                if qty_ped > 0 and estatus_out_lin != 'Cancelado':
                    if qty_del >= qty_ped:
                        estatus_out_lin = 'Entregado'
                    elif qty_del > 0 and estatus_out_lin not in ('Entregado',):
                        estatus_out_lin = 'Entregado Parcial'

                # ── Override adicional: si el pedido tiene factura posted → entregado+facturado
                facturas_orden = invoices_map_by_origin.get(order_name, [])
                if facturas_orden and any(f.get('state') == 'posted' for f in facturas_orden):
                    if estatus_out_lin not in ('Cancelado', 'Entregado Parcial', 'Entregado'):
                        estatus_out_lin = 'Entregado'

                _ep_info = entrega_por_prod.get((order_name, lin.get('product_id_odoo'))) or {}
                # Fuente primaria: forecast_expected_date de sale.order.line (Odoo calcula esto
                # considerando toda la cadena de abasto; es el mismo dato del tooltip rojo).
                # Fallback: fecha obtenida via cadena de OC en pasos 8.5 / 10.5.
                raw_forecast = lin.get('forecast_expected_date')
                fecha_esp_final = (str(raw_forecast)[:10] if raw_forecast else None) \
                    or _ep_info.get('fecha_esperada')
                po_name_final = _ep_info.get('po_name')
                filas_planas.append({
                    'numero_factura': factura_nombre or order_name,
                    'clave_producto': lin.get('clave_producto'),
                    'producto': lin.get('producto'),
                    'descripcion': lin.get('descripcion'),
                    'fecha': fecha_factura or o.get('date_order'),
                    'precio_unitario': lin.get('precio_unitario'),
                    'cantidad': lin.get('cantidad_pedida'),
                    'cantidad_entregada': lin.get('cantidad_entregada', 0),
                    'total': lin.get('total_linea'),
                    'total_entregado': lin.get('total_entregado_linea', 0),
                    'orden': order_name,
                    'estado_orden': estado_orden,
                    'estado_orden_raw': estado_orden_raw,
                    'cliente': order_obj['cliente'],
                    'pickings': order_obj['pickings'],
                    'estatus_out': estatus_out_lin,
                    'fecha_esperada': fecha_esp_final,
                    'po_name': po_name_final,
                    'de_proyeccion': (
                        _norm_fc(lin.get('clave_producto')) in _forecast_skus
                        and _periodo_start <= (o.get('date_order') or '')[:10] <= _periodo_end
                    ),
                    'etiquetas': order_obj['etiquetas'],
                })

            resultado.append(order_obj)

        # ── 12) Leer acumulado_anticipado desde previo ────────────────────────────
        # Para grupos: suma las claves individuales de los miembros del grupo.
        # Usar "Integral {id}" era incorrecto porque el id de grupo_clientes (ej: 12)
        # no coincide con el número ordinal del integral en previo (ej: "Integral 4").
        # Para clientes individuales: lee la fila por clave directamente.
        avance_previo = None
        try:
            _conn_ap = obtener_conexion()
            _cur_ap = _conn_ap.cursor(dictionary=True)
            if temporada_param:
                # Temporada histórica: snapshot por claves individuales
                if grupo_odoo and _claves:
                    _ph_ap = ','.join(['%s'] * len(_claves))
                    _cur_ap.execute(
                        f"SELECT COALESCE(SUM(acumulado_anticipado), 0) AS total "
                        f"FROM previo_historico "
                        f"WHERE clave IN ({_ph_ap}) AND temporada = %s",
                        (*_claves, temporada_param)
                    )
                else:
                    _cur_ap.execute(
                        "SELECT acumulado_anticipado AS total FROM previo_historico "
                        "WHERE clave = %s AND temporada = %s "
                        "ORDER BY fecha_snapshot DESC LIMIT 1",
                        (cliente, temporada_param)
                    )
            elif grupo_odoo and _claves:
                # Grupo: suma previo de todos los miembros (evita depender del nombre ordinal)
                _ph_ap = ','.join(['%s'] * len(_claves))
                _cur_ap.execute(
                    f"SELECT COALESCE(SUM(acumulado_anticipado), 0) AS total "
                    f"FROM previo WHERE clave IN ({_ph_ap})",
                    tuple(_claves)
                )
            else:
                _cur_ap.execute(
                    "SELECT acumulado_anticipado AS total FROM previo "
                    "WHERE clave = %s AND (es_integral = 0 OR es_integral IS NULL) LIMIT 1",
                    (cliente,)
                )
            _row_ap = _cur_ap.fetchone()
            if _row_ap and _row_ap.get('total') is not None:
                avance_previo = float(_row_ap['total'])
            _cur_ap.close()
            _conn_ap.close()
        except Exception as _ex_ap:
            logging.warning('detalle_compras_odoo: error al leer acumulado_anticipado: %s', _ex_ap)
            avance_previo = None

        # ── Guardar en caché los datos crudos (sin filtro ni paginación) ─────────
        _meta_base = {
            'fecha_inicio_temporada': fecha_inicio_temporada,
            'avance_previo': avance_previo,
            'temporada': temporada_param,
        }
        if _redis:
            try:
                _redis.setex(_cache_key, _ODOO_PEDIDOS_TTL, json.dumps([resultado, filas_planas, _meta_base]))
            except Exception as _ce:
                logging.warning('Redis cache store error: %s', _ce)

        if debe_ocultar_montos:
            resultado, filas_planas, _meta_base = _redactar_montos_detalle_compras(
                resultado, filas_planas, _meta_base
            )

        # ── Filtro opcional por estado de picking
        filas_fil = [f for f in filas_planas if f.get('estatus_out') == estado_filtro] if estado_filtro else filas_planas
        total = len(filas_fil)
        filas_pag = filas_fil[offset: offset + limit] if limit is not None else filas_fil[offset:]

        _nombre_partner = partners[0]['name'] if partners else cliente
        _clave_partner  = (partners[0].get('ref') or '').strip() if partners else cliente
        return jsonify({
            'data': resultado,
            'rows': filas_pag,
            'meta': {
                **_meta_base,
                'total': total,
                'limit': limit,
                'offset': offset,
                'returned': len(filas_pag),
            },
            'cliente': {
                'nombre_cliente': _nombre_partner,
                'clave': _clave_partner,
            },
        }), 200

    except Exception as e:
        tb = traceback.format_exc()
        logging.exception('detalle_compras_odoo: excepción inesperada')
        return jsonify({'error': str(e), 'trace': tb}), 500


@caratulas_bp.route('/ventas_no_registradas', methods=['GET'])
def ventas_no_registradas():
    """Ventas sin cliente registrado para Carátula Global.

    Si no se mandan fechas, usa automáticamente el rango MY27 vigente y hoy como
    tope efectivo. Mantiene `total` y `filas` y agrega desglose por categoría.
    """
    conexion = None
    cursor = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)
        rango_inicio, rango_fin = _caratula_rango_my27(cursor)

        fecha_desde = request.args.get('fecha_desde') or rango_inicio
        fecha_hasta = request.args.get('fecha_hasta') or rango_fin
        resumen = _caratula_ventas_no_registradas_resumen(cursor, fecha_desde, fecha_hasta)

        return jsonify({
            k: (float(v) if isinstance(v, Decimal) else v)
            for k, v in resumen.items()
        }), 200
    except Exception as e:
        logging.exception('ventas_no_registradas: error')
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()
