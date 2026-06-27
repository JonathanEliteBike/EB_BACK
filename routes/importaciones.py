import json as _json
from flask import Blueprint, jsonify, request
from datetime import datetime, date, timedelta
from decimal import Decimal
from db_conexion import obtener_conexion

importaciones_bp = Blueprint("importaciones", __name__, url_prefix="/importaciones")


# ── helpers ──────────────────────────────────────────────────────────────────

def _serialize(row: dict) -> dict:
    for k, v in row.items():
        if isinstance(v, Decimal):
            row[k] = float(v)
        elif isinstance(v, (datetime, date)):
            row[k] = v.isoformat()
    # mysql-connector devuelve columnas JSON como string — parsear aquí
    if isinstance(row.get("borradores"), str):
        try:
            row["borradores"] = _json.loads(row["borradores"])
        except Exception:
            row["borradores"] = {}
    if isinstance(row.get("campos_na"), str):
        try:
            row["campos_na"] = _json.loads(row["campos_na"])
        except Exception:
            row["campos_na"] = []
    return row


def _calc_dias(fecha_desde, fecha_hasta):
    if fecha_desde and fecha_hasta:
        try:
            if isinstance(fecha_desde, str):
                fecha_desde = datetime.strptime(fecha_desde, "%Y-%m-%d").date()
            if isinstance(fecha_hasta, str):
                fecha_hasta = datetime.strptime(fecha_hasta, "%Y-%m-%d").date()
            return (fecha_hasta - fecha_desde).days
        except Exception:
            return None
    return None




# ── Porcentajes de avance por sección ────────────────────────────────────────
# Campos que cuentan para cada sección (valor no nulo = completado)

CAMPOS_LOGISTICA = [
    "log_fecha_notificacion", "log_fecha_entrega", "log_titulo_correo_salida",
    "log_confirmacion_enterado", "log_origen", "log_tipo_productos",
    "log_fecha_solicitud_cotizaciones", "log_confirmacion_cotizacion",
    "log_costo_flete", "log_fecha_shipping_instructions", "log_confirmacion_booking",
    "log_fecha_booking", "log_eta_puerto", "log_buque", "log_no_viaje",
    "log_puerto_salida", "log_contenedor", "log_recepcion_bl_co",
    "log_confirmacion_bl_co", "log_certificado_seguro",
    "log_envio_certificado",
    # log_recepcion_documentos moved to CAMPOS_ODOO
]

CAMPOS_IMPORTACION = [
    "imp_bl_guia", "imp_co", "imp_facturas", "imp_series",
    "imp_solicitud_pago_forwarder", "imp_llegada_contenedor_puerto",
    "imp_terminal", "imp_bl_endosado", "imp_bl_revalidado",
    "imp_entrega_facturas_aa", "imp_traduccion_aa",
    "imp_entrega_certificado_origen", "imp_relacion_numeros_serie",
    "imp_relacion_incrementables", "imp_recepcion_draft_pedimento",
    "imp_fecha_entrega_docs_aa", "imp_pedimento_revisado", "imp_pedimento",
    "imp_coves_aa", "imp_revision_coves", "imp_aplica_verificacion",
    "imp_layout_verificacion", "imp_envio_layout", "imp_carta_318",
    "imp_carta_incrementables", "imp_carta_no_previo",
    "imp_carta_declaracion_marca", "imp_carta_aplicacion_uva",
    "imp_articulos_verificar", "imp_liberacion_folios",
    "imp_fecha_pago_pedimento", "imp_fecha_traduccion",
    "imp_fecha_numeros_serie", "imp_carta_porte",
    "imp_fecha_limite_cruce",
]

CAMPOS_DESPACHO = [
    "des_solicitud_cita_cruce", "des_cita_cruce", "des_fecha_cruce_real",
    "des_solicitud_pase_maniobras", "des_carta_maniobras", "des_fecha_carta_porte",
    "des_fecha_entrega_almacen_prog", "des_lugar_destino", "des_llegada_almacen",
    "des_solicitud_carta_vacio", "des_fecha_lavado",
    "des_entrega_contenedor_naviera", "des_dias_sin_demoras", "des_fecha_limite_naviera",
    "des_recepcion_eir",
]

CAMPOS_ODOO = [
    "odoo_importador",
    "odoo_codificacion", "odoo_alta_catalogo", "odoo_alta_precios",
    "odoo_alta_orden_compra", "odoo_folio_orden",
    "log_recepcion_documentos",
]

CAMPOS_ALMACEN = [
    "alm_base_datos_etiquetas", "alm_base_datos_verificacion",
    "alm_liberacion_etiquetado", "alm_envio_info_uva",
    "alm_liberacion_uva", "alm_fecha_limite_etiquetado",
]

CAMPOS_RECEPCION = [
    "rec_cedula_costeo", "rec_recepcion_odoo",
    "rec_folio_compra", "rec_liberacion_verificacion",
    "rec_liberacion_final",
]

CAMPOS_CIERRE = [
    "cie_recepcion_cuenta_gastos", "cie_saldo_favor_elite",
    "cie_liquidado_elite", "cie_saldo_favor_aa", "cie_liquidado_aa",
    "cie_fecha_pago_elite",
    "cie_fecha_pago_aa",
]

CAMPOS_COSTOS = [
    "cos_tipo_cambio_pedimento", "cos_valor_factura", "cos_cantidad_bicicletas",
    "cos_flete_internacional_usd", "cos_gastos_forwarder_pesos",
    "cos_seguro_pesos", "cos_custodia_pesos", "cos_maniobras_pesos",
    "cos_cargos_adicionales_pesos", "cos_honorarios_pesos",
    "cos_flete_terrestre_usd", "cos_pernoctas_usd", "cos_paquetexpress_usd",
    "cos_demoras_usd", "cos_verificacion_pesos", "cos_lavado_contenedor_pesos",
    "cos_monitoreo_pesos", "cos_impuestos_pagados_pesos", "cos_reconocimiento_aduanero",
]


_COLS_PERMITIDAS: set = (
    set(CAMPOS_LOGISTICA)
    | set(CAMPOS_IMPORTACION)
    | set(CAMPOS_DESPACHO)
    | set(CAMPOS_ODOO)
    | set(CAMPOS_ALMACEN)
    | set(CAMPOS_RECEPCION)
    | set(CAMPOS_CIERRE)
    | set(CAMPOS_COSTOS)
    | {
        "referencia", "nombre", "estado", "via_transporte", "notas",
    }
)

_SECCIONES_VALIDAS = {
    "logistica", "importacion", "despacho", "odoo",
    "almacen", "recepcion", "cierre",
    "costos",
}

# Campos derivados que _recalcular_campos() inyecta en data — deben persistirse
# en INSERT y UPDATE aunque no estén en _COLS_PERMITIDAS (son cálculos, no input usuario).
_CAMPOS_CALC = [
    "log_dias_salida_tras_entrega", "log_dias_transito_maritimo",
    "imp_dias_libres_almacenaje", "imp_dias_despacho_aduanero",
    "des_dias_transito_terrestre",
    "des_fecha_limite_naviera",   # calculada: ETA Puerto + días sin demoras
]

_NO_CUENTA = {None, "", "NO"}

def _calcular_progreso(row: dict) -> dict:
    # Collect all N/A fields (officially saved + draft borradores)
    campos_na_db = set()
    raw_na = row.get("campos_na")
    if raw_na:
        if isinstance(raw_na, str):
            try: raw_na = _json.loads(raw_na)
            except: raw_na = []
        campos_na_db = set(raw_na if isinstance(raw_na, list) else [])

    borradores = row.get("borradores") or {}
    if isinstance(borradores, str):
        try: borradores = _json.loads(borradores)
        except: borradores = {}
    na_borradores = {
        campo
        for sec in borradores.values()
        for campo, val in (sec.items() if isinstance(sec, dict) else [])
        if val == "__NA__"
    }
    all_na = campos_na_db | na_borradores

    def pct(campos):
        total = len(campos)
        hechos = sum(
            1 for c in campos
            if row.get(c) not in _NO_CUENTA or c in all_na
        )
        return {"total": total, "completados": hechos, "pct": round(hechos / total * 100) if total else 0}

    return {
        "logistica":   pct(CAMPOS_LOGISTICA),
        "importacion": pct(CAMPOS_IMPORTACION),
        "despacho":    pct(CAMPOS_DESPACHO),
        "odoo":        pct(CAMPOS_ODOO),
        "almacen":     pct(CAMPOS_ALMACEN),
        "recepcion":   pct(CAMPOS_RECEPCION),
        "cierre":      pct(CAMPOS_CIERRE),
        "costos":      pct(CAMPOS_COSTOS),
    }


# ── Inicializar tablas ────────────────────────────────────────────────────────

@importaciones_bp.route("/inicializar-tablas", methods=["POST"])
def inicializar_tablas():
    conn = obtener_conexion()
    if not conn:
        return jsonify({"error": "Sin conexion a BD"}), 500
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS importaciones (
                id INT AUTO_INCREMENT PRIMARY KEY,

                -- Identificacion del embarque
                referencia       VARCHAR(50) NOT NULL,
                nombre           VARCHAR(500),
                estado           VARCHAR(30) DEFAULT 'activo',
                via_transporte   VARCHAR(10) NOT NULL DEFAULT 'MARITIMO',
                created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

                -- PROCESO DE LOGISTICA (22 items)
                log_fecha_notificacion          DATE,
                log_fecha_entrega               DATE,
                log_titulo_correo_salida        TEXT,
                log_confirmacion_enterado       DATE,
                log_origen                      VARCHAR(100),
                log_tipo_productos              TEXT,
                log_fecha_solicitud_cotizaciones DATE,
                log_confirmacion_cotizacion     TEXT,
                log_costo_flete                 VARCHAR(255),
                log_fecha_shipping_instructions DATE,
                log_confirmacion_booking        DATE,
                log_fecha_booking               DATE,
                log_eta_puerto                  DATE,
                log_buque                       VARCHAR(255),
                log_no_viaje                    VARCHAR(100),
                log_puerto_salida               VARCHAR(100),
                log_contenedor                  TEXT,
                log_recepcion_bl_co             DATE,
                log_confirmacion_bl_co          DATE,
                log_envio_certificado           DATE,
                log_certificado_seguro          VARCHAR(100),
                log_recepcion_documentos        DATE,
                -- Calculados logistica
                log_dias_salida_tras_entrega    INT,
                log_dias_transito_maritimo      INT,

                -- PROCESO DE IMPORTACION (35 items)
                imp_fecha_traduccion            DATE,
                imp_fecha_numeros_serie         DATE,
                imp_bl_guia                     VARCHAR(255),
                imp_co                          VARCHAR(10),
                imp_facturas                    TEXT,
                imp_series                      VARCHAR(10),
                imp_solicitud_pago_forwarder    DATE,
                imp_llegada_contenedor_puerto   DATE,
                imp_fecha_limite_cruce          DATE,
                imp_dias_libres_almacenaje      INT DEFAULT 17,
                imp_dias_despacho_aduanero      INT,
                imp_terminal                    VARCHAR(100),
                imp_bl_endosado                 DATE,
                imp_bl_revalidado               DATE,
                imp_carta_porte                 DATE,
                imp_entrega_facturas_aa         VARCHAR(10),
                imp_traduccion_aa               VARCHAR(10),
                imp_entrega_certificado_origen  VARCHAR(20),
                imp_relacion_numeros_serie      VARCHAR(10),
                imp_relacion_incrementables     VARCHAR(10),
                imp_recepcion_draft_pedimento   VARCHAR(10),
                imp_fecha_entrega_docs_aa       DATE,
                imp_pedimento_revisado          DATE,
                imp_pedimento                   VARCHAR(100),
                imp_coves_aa                    VARCHAR(10),
                imp_revision_coves              VARCHAR(10),
                imp_aplica_verificacion         VARCHAR(10),
                imp_layout_verificacion         VARCHAR(10),
                imp_envio_layout                DATE,
                imp_carta_318                   VARCHAR(10),
                imp_carta_incrementables        VARCHAR(10),
                imp_carta_no_previo             VARCHAR(10),
                imp_carta_declaracion_marca     VARCHAR(10),
                imp_carta_aplicacion_uva        VARCHAR(10),
                imp_articulos_verificar         TEXT,
                imp_liberacion_folios           DATE,
                imp_fecha_pago_pedimento        DATE,

                -- PROCESO DESPACHO Y REGRESO (14 items)
                des_solicitud_cita_cruce        DATE,
                des_cita_cruce                  DATE,
                des_fecha_cruce_real            DATE,
                des_solicitud_pase_maniobras    DATE,
                des_carta_maniobras             DATE,
                des_fecha_carta_porte           DATE,
                des_fecha_entrega_almacen_prog  DATE,
                des_lugar_destino               VARCHAR(100),
                des_llegada_almacen             DATE,
                des_dias_transito_terrestre     INT,
                des_solicitud_carta_vacio       DATE,
                des_fecha_lavado                DATE,
                des_entrega_contenedor_naviera  DATE,
                des_dias_sin_demoras            INT,
                des_fecha_limite_naviera        DATE,
                des_recepcion_eir               DATE,

                -- PROCESO ALTA ORDEN EN ODOO (7 items)
                odoo_importador                 VARCHAR(50),
                odoo_codificacion               VARCHAR(10),
                odoo_alta_catalogo              VARCHAR(10),
                odoo_alta_precios               VARCHAR(10),
                odoo_alta_orden_compra          DATE,
                odoo_folio_orden                VARCHAR(255),

                -- PROCESO CON ALMACEN (6 items)
                alm_base_datos_etiquetas        DATE,
                alm_base_datos_verificacion     DATE,
                alm_liberacion_etiquetado       DATE,
                alm_envio_info_uva              DATE,
                alm_liberacion_uva              DATE,
                alm_fecha_limite_etiquetado     DATE,

                -- PROCESO RECEPCION (4 items)
                rec_cedula_costeo               VARCHAR(10),
                rec_recepcion_odoo              DATE,
                rec_folio_compra                VARCHAR(100),
                rec_liberacion_verificacion     DATE,
                rec_liberacion_final            DATE,

                -- CIERRE DE CUENTAS (7 items)
                cie_recepcion_cuenta_gastos     VARCHAR(50),
                cie_saldo_favor_elite           VARCHAR(100),
                cie_liquidado_elite             VARCHAR(100),
                cie_fecha_pago_elite            DATE,
                cie_saldo_favor_aa              VARCHAR(100),
                cie_liquidado_aa                VARCHAR(100),
                cie_fecha_pago_aa               DATE,

                -- COSTOS POR IMPORTACION
                cos_tipo_cambio_pedimento       DECIMAL(10,4),
                cos_valor_factura               DECIMAL(15,2),
                cos_cantidad_bicicletas         INT,
                cos_flete_internacional_usd     DECIMAL(15,2),
                cos_gastos_forwarder_pesos      DECIMAL(15,2),
                cos_seguro_pesos                DECIMAL(15,2),
                cos_custodia_pesos              DECIMAL(15,2),
                cos_maniobras_pesos             DECIMAL(15,2),
                cos_cargos_adicionales_pesos    DECIMAL(15,2),
                cos_honorarios_pesos            DECIMAL(15,2),
                cos_flete_terrestre_usd         DECIMAL(15,2),
                cos_pernoctas_usd               DECIMAL(15,2),
                cos_paquetexpress_usd           DECIMAL(15,2),
                cos_demoras_usd                 DECIMAL(15,2),
                cos_verificacion_pesos          DECIMAL(15,2),
                cos_lavado_contenedor_pesos     DECIMAL(15,2),
                cos_monitoreo_pesos             DECIMAL(15,2),
                cos_impuestos_pagados_pesos     DECIMAL(15,2),
                cos_reconocimiento_aduanero     DECIMAL(15,2),

                -- Notas adicionales
                notas                           TEXT,

                -- Datos en edicion (no cuentan en porcentajes)
                borradores                      JSON,

                -- N/A fields (campos marcados como No Aplica)
                campos_na                       JSON
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)
        conn.commit()

        # Migraciones para instancias existentes
        migraciones = [
            "ALTER TABLE importaciones ADD COLUMN IF NOT EXISTS campos_na JSON AFTER borradores",
            "ALTER TABLE importaciones ADD COLUMN IF NOT EXISTS odoo_importador VARCHAR(50) AFTER odoo_folio_orden",
        ]
        for sql in migraciones:
            try:
                cursor.execute(sql)
            except Exception:
                pass
        conn.commit()

        return jsonify({"ok": True, "mensaje": "Tabla importaciones creada/verificada"}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


# ── GET /importaciones  →  lista con resumen de progreso ────────────────────

@importaciones_bp.route("", methods=["GET"])
def listar():
    conn = obtener_conexion()
    if not conn:
        return jsonify({"error": "Sin conexion a BD"}), 500
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT * FROM importaciones
            WHERE estado != 'eliminado'
            ORDER BY created_at DESC
        """)
        rows = cursor.fetchall()
        resultado = []
        for row in rows:
            row = _serialize(row)
            row["progreso"] = _calcular_progreso(row)
            resultado.append(row)
        return jsonify(resultado), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


# ── GET /importaciones/<id>  →  detalle completo ─────────────────────────────

@importaciones_bp.route("/<int:id_imp>", methods=["GET"])
def obtener(id_imp):
    conn = obtener_conexion()
    if not conn:
        return jsonify({"error": "Sin conexion a BD"}), 500
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM importaciones WHERE id = %s", (id_imp,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"error": "No encontrado"}), 404
        row = _serialize(row)
        row = _recalcular_campos(row)
        row["progreso"] = _calcular_progreso(row)
        return jsonify(row), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


# ── GET /importaciones/dashboard  →  datos analíticos agregados ──────────────

@importaciones_bp.route("/dashboard", methods=["GET"])
def dashboard():
    via    = request.args.get("via",    "").strip()
    estado = request.args.get("estado", "").strip()
    origen = request.args.get("origen", "").strip()
    anio   = request.args.get("anio",   "").strip()

    conn = obtener_conexion()
    if not conn:
        return jsonify({"error": "Sin conexion a BD"}), 500
    try:
        cursor = conn.cursor(dictionary=True)

        where  = ["estado != 'eliminado'"]
        params = []
        if via:
            where.append("via_transporte = %s")
            params.append(via)
        if estado:
            where.append("estado = %s")
            params.append(estado)
        if origen:
            where.append("log_origen LIKE %s")
            params.append(f"%{origen}%")
        if anio:
            where.append("YEAR(COALESCE(log_fecha_booking, created_at)) = %s")
            params.append(int(anio))

        w = " AND ".join(where)
        cursor.execute(
            f"SELECT * FROM importaciones WHERE {w} ORDER BY COALESCE(log_fecha_booking, created_at) DESC",
            params or []
        )
        rows = [_serialize(r) for r in cursor.fetchall()]

        # ── KPIs ──────────────────────────────────────────────────────────────
        total      = len(rows)
        activos    = sum(1 for r in rows if r.get("estado") == "activo")
        cerrados   = sum(1 for r in rows if r.get("estado") == "cerrado")
        cancelados = sum(1 for r in rows if r.get("estado") == "cancelado")

        fletes = [float(r["cos_flete_internacional_usd"]) for r in rows if r.get("cos_flete_internacional_usd")]
        flete_total = round(sum(fletes), 2)
        flete_prom  = round(flete_total / len(fletes), 2) if fletes else 0

        transitos    = [r["log_dias_transito_maritimo"] for r in rows if r.get("log_dias_transito_maritimo")]
        transito_prom = round(sum(transitos) / len(transitos), 1) if transitos else 0

        avances = []
        _prog_cache: dict = {}
        for r in rows:
            prog = _calcular_progreso(r)
            _prog_cache[r["id"]] = prog
            t = sum(s["total"] for s in prog.values())
            c = sum(s["completados"] for s in prog.values())
            avances.append(round(c / t * 100) if t else 0)
        pct_prom = round(sum(avances) / len(avances)) if avances else 0

        # ── Por vía ───────────────────────────────────────────────────────────
        por_via: dict = {}
        for r in rows:
            v = r.get("via_transporte") or "MARITIMO"
            por_via[v] = por_via.get(v, 0) + 1
        por_via_list = [{"via": k, "count": v} for k, v in sorted(por_via.items())]

        # ── Por origen (top 10) ───────────────────────────────────────────────
        por_origen: dict = {}
        for r in rows:
            o = (r.get("log_origen") or "Sin origen").strip().upper()
            por_origen[o] = por_origen.get(o, 0) + 1
        por_origen_list = sorted(
            [{"origen": k, "count": v} for k, v in por_origen.items()],
            key=lambda x: -x["count"]
        )[:10]

        # ── Por mes ───────────────────────────────────────────────────────────
        from collections import defaultdict
        por_mes: dict = defaultdict(lambda: {"maritimo": 0, "aereo": 0})
        meses_es = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]
        for r in rows:
            fecha = r.get("log_fecha_booking") or (r.get("created_at") or "")[:10]
            if fecha and len(str(fecha)) >= 7:
                mes_key = str(fecha)[:7]
                via_r   = r.get("via_transporte") or "MARITIMO"
                if via_r == "MARITIMO":
                    por_mes[mes_key]["maritimo"] += 1
                else:
                    por_mes[mes_key]["aereo"] += 1
        por_mes_list = []
        for mes_key in sorted(por_mes.keys()):
            try:
                y, m = mes_key.split("-")
                label = f"{meses_es[int(m)-1]} {y}"
            except Exception:
                label = mes_key
            por_mes_list.append({"mes": mes_key, "label": label, **por_mes[mes_key]})

        # ── Flete promedio por vía ────────────────────────────────────────────
        f_mar = [float(r["cos_flete_internacional_usd"]) for r in rows
                 if r.get("cos_flete_internacional_usd") and (r.get("via_transporte") or "MARITIMO") == "MARITIMO"]
        f_aer = [float(r["cos_flete_internacional_usd"]) for r in rows
                 if r.get("cos_flete_internacional_usd") and r.get("via_transporte") == "AEREO"]
        flete_por_via = {
            "maritimo_avg":   round(sum(f_mar) / len(f_mar), 2) if f_mar else 0,
            "maritimo_count": len(f_mar),
            "aereo_avg":      round(sum(f_aer) / len(f_aer), 2) if f_aer else 0,
            "aereo_count":    len(f_aer),
        }

        # ── Por estado ────────────────────────────────────────────────────────
        por_estado: dict = {}
        for r in rows:
            e = r.get("estado") or "activo"
            por_estado[e] = por_estado.get(e, 0) + 1
        por_estado_list = [{"estado": k, "count": v} for k, v in por_estado.items()]

        # ── Resumen por embarque ──────────────────────────────────────────────
        embarques_res = []
        for r in rows:
            prog = _prog_cache[r["id"]]
            t = sum(s["total"] for s in prog.values())
            c = sum(s["completados"] for s in prog.values())
            pct_global = round(c / t * 100) if t else 0

            tc = float(r.get("cos_tipo_cambio_pedimento") or 0)
            costo_total_pesos = round(
                ((float(r.get("cos_flete_internacional_usd") or 0) +
                  float(r.get("cos_flete_terrestre_usd")      or 0) +
                  float(r.get("cos_pernoctas_usd")            or 0) +
                  float(r.get("cos_demoras_usd")              or 0)) * tc) +
                float(r.get("cos_gastos_forwarder_pesos")   or 0) +
                float(r.get("cos_seguro_pesos")             or 0) +
                float(r.get("cos_custodia_pesos")           or 0) +
                float(r.get("cos_maniobras_pesos")          or 0) +
                float(r.get("cos_honorarios_pesos")         or 0) +
                float(r.get("cos_verificacion_pesos")       or 0) +
                float(r.get("cos_lavado_contenedor_pesos")  or 0) +
                float(r.get("cos_monitoreo_pesos")          or 0) +
                float(r.get("cos_impuestos_pagados_pesos")  or 0) +
                float(r.get("cos_reconocimiento_aduanero")  or 0) +
                float(r.get("cos_cargos_adicionales_pesos") or 0),
                2
            )

            nbici = r.get("cos_cantidad_bicicletas")
            embarques_res.append({
                "id":                          r["id"],
                "referencia":                  r["referencia"],
                "nombre":                      r.get("nombre") or "",
                "via_transporte":              r.get("via_transporte") or "MARITIMO",
                "log_origen":                  r.get("log_origen") or "",
                "log_tipo_productos":          r.get("log_tipo_productos") or "",
                "estado":                      r.get("estado") or "activo",
                "pct_global":                  pct_global,
                "des_llegada_almacen":         r.get("des_llegada_almacen"),
                "cos_flete_internacional_usd": float(r["cos_flete_internacional_usd"]) if r.get("cos_flete_internacional_usd") else None,
                "costo_total_pesos":           costo_total_pesos,
                "cos_cantidad_bicicletas":     int(nbici) if nbici else None,
                "costo_por_bicicleta":         round(costo_total_pesos / float(nbici), 2) if nbici and costo_total_pesos else None,
                "notas":                       r.get("notas") or "",
                "progreso":                    prog,
            })

        # ── Opciones para filtros ─────────────────────────────────────────────
        all_origenes = sorted({
            (r.get("log_origen") or "").strip().upper()
            for r in rows if r.get("log_origen")
        })
        all_anios = sorted({
            str(r.get("log_fecha_booking") or r.get("created_at", ""))[:4]
            for r in rows
            if str(r.get("log_fecha_booking") or r.get("created_at", ""))[:4].isdigit()
        }, reverse=True)

        return jsonify({
            "kpis": {
                "total":                           total,
                "activos":                         activos,
                "cerrados":                        cerrados,
                "cancelados":                      cancelados,
                "flete_total_usd":                 flete_total,
                "flete_promedio_usd":              flete_prom,
                "transito_maritimo_promedio_dias": transito_prom,
                "pct_avance_promedio":             pct_prom,
            },
            "por_via":       por_via_list,
            "por_origen":    por_origen_list,
            "por_mes":       por_mes_list,
            "flete_por_via": flete_por_via,
            "por_estado":    por_estado_list,
            "embarques":     embarques_res,
            "filtros": {
                "origenes": all_origenes,
                "anios":    all_anios,
            },
        }), 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


# ── POST /importaciones  →  crear nuevo embarque ─────────────────────────────

@importaciones_bp.route("", methods=["POST"])
def crear():
    data = request.get_json() or {}
    if not data.get("referencia"):
        return jsonify({"error": "El campo 'referencia' es obligatorio"}), 400

    conn = obtener_conexion()
    if not conn:
        return jsonify({"error": "Sin conexion a BD"}), 500
    try:
        # Calcular campos derivados
        data = _recalcular_campos(data)

        cols = list(dict.fromkeys(
            [k for k in data if k != "id" and k in _COLS_PERMITIDAS]
            + [c for c in _CAMPOS_CALC if data.get(c) is not None]
        ))
        if not cols:
            return jsonify({"error": "Sin campos válidos para insertar"}), 400
        placeholders = ", ".join(["%s"] * len(cols))
        col_names = ", ".join(cols)
        vals = [data[c] for c in cols]

        cursor = conn.cursor()
        cursor.execute(
            f"INSERT INTO importaciones ({col_names}) VALUES ({placeholders})",
            vals
        )
        conn.commit()
        new_id = cursor.lastrowid
        return jsonify({"ok": True, "id": new_id}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


# ── PUT /importaciones/<id>  →  actualizar campos ────────────────────────────

@importaciones_bp.route("/<int:id_imp>", methods=["PUT"])
def actualizar(id_imp):
    data = request.get_json() or {}
    data.pop("id", None)
    data.pop("created_at", None)

    borrador_seccion = data.pop("_borrador_seccion", None)
    seccion_oficial  = data.pop("_seccion_oficial",  None)

    if not data and borrador_seccion is None:
        return jsonify({"error": "Sin datos para actualizar"}), 400

    conn = obtener_conexion()
    if not conn:
        return jsonify({"error": "Sin conexion a BD"}), 500
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM importaciones WHERE id = %s", (id_imp,))
        existing = cursor.fetchone()
        if not existing:
            return jsonify({"error": "No encontrado"}), 404

        if borrador_seccion:
            if borrador_seccion not in _SECCIONES_VALIDAS:
                return jsonify({"error": "Sección de borrador no válida"}), 400
            if not data:
                return jsonify({"ok": True, "borrador": True}), 200  # no-op, nothing to save
            # JSON_MERGE_PATCH anidado: fusiona campos nuevos con borrador existente de la sección
            # en lugar de reemplazarlo — preserva campos editados en sesiones anteriores
            cursor.execute(
                f"""UPDATE importaciones SET borradores = JSON_MERGE_PATCH(
                    COALESCE(borradores, JSON_OBJECT()),
                    JSON_OBJECT('{borrador_seccion}', JSON_MERGE_PATCH(
                        COALESCE(JSON_EXTRACT(borradores, '$.{borrador_seccion}'), JSON_OBJECT()),
                        CAST(%s AS JSON)
                    ))
                ) WHERE id = %s""",
                (_json.dumps(data, ensure_ascii=False), id_imp),
            )
            conn.commit()
            return jsonify({"ok": True, "borrador": True}), 200

        # ── Guardar oficial: actualiza columnas reales y limpia borradores ──
        # Separar campos marcados como __NA__ → van a campos_na, se guardan NULL en columna real
        existing_campos_na_raw = existing.get("campos_na") or "[]"
        if isinstance(existing_campos_na_raw, str):
            try: existing_campos_na = set(_json.loads(existing_campos_na_raw))
            except: existing_campos_na = set()
        elif isinstance(existing_campos_na_raw, list):
            existing_campos_na = set(existing_campos_na_raw)
        else:
            existing_campos_na = set()

        for campo, valor in list(data.items()):
            if valor == "__NA__":
                existing_campos_na.add(campo)
                data[campo] = None  # Store NULL in the actual column
            elif campo in existing_campos_na:
                existing_campos_na.discard(campo)  # Real value overrides N/A

        merged = {**{k: v for k, v in existing.items()}, **data}
        merged = _recalcular_campos(merged)

        campos_a_actualizar = (
            [c for c in data.keys() if c in _COLS_PERMITIDAS]
            + [c for c in _CAMPOS_CALC if c not in data]
        )
        campos_a_actualizar = list(dict.fromkeys(campos_a_actualizar))
        if not campos_a_actualizar:
            return jsonify({"error": "Sin campos válidos para actualizar"}), 400

        set_clause = ", ".join([f"`{c}` = %s" for c in campos_a_actualizar])
        vals = [merged.get(c) for c in campos_a_actualizar]

        # Always persist campos_na
        set_clause += ", campos_na = %s"
        vals.append(_json.dumps(sorted(existing_campos_na), ensure_ascii=False))

        if seccion_oficial:
            borradores = _json.loads(existing.get("borradores") or "{}")
            borradores.pop(seccion_oficial, None)
            set_clause += ", borradores = %s"
            vals.append(_json.dumps(borradores, ensure_ascii=False))

        vals.append(id_imp)
        cursor.execute(f"UPDATE importaciones SET {set_clause} WHERE id = %s", vals)
        conn.commit()
        return jsonify({"ok": True}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


# ── DELETE /importaciones/<id>  →  soft delete ───────────────────────────────

@importaciones_bp.route("/<int:id_imp>", methods=["DELETE"])
def eliminar(id_imp):
    conn = obtener_conexion()
    if not conn:
        return jsonify({"error": "Sin conexion a BD"}), 500
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE importaciones SET estado = 'eliminado' WHERE id = %s",
            (id_imp,)
        )
        conn.commit()
        if cursor.rowcount == 0:
            return jsonify({"error": "No encontrado"}), 404
        return jsonify({"ok": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


# ── GET /importaciones/resumen  →  dashboard general ─────────────────────────

@importaciones_bp.route("/resumen", methods=["GET"])
def resumen():
    return listar()


# ── lógica de cálculos automáticos ───────────────────────────────────────────

def _recalcular_campos(data: dict) -> dict:
    # Días que tardó en salir (Booking - Fecha de entrega Scott)
    data["log_dias_salida_tras_entrega"] = _calc_dias(
        data.get("log_fecha_entrega"),
        data.get("log_fecha_booking")
    )

    # Días de tránsito marítimo (Llegada contenedor a puerto - Booking)
    data["log_dias_transito_maritimo"] = _calc_dias(
        data.get("log_fecha_booking"),
        data.get("imp_llegada_contenedor_puerto")
    )

    # Días libres en terminal (Fecha límite - Llegada a puerto)
    fecha_limite = data.get("imp_fecha_limite_cruce")
    llegada = data.get("imp_llegada_contenedor_puerto")
    if fecha_limite and llegada:
        try:
            fl = date.fromisoformat(str(fecha_limite)[:10])
            ll = date.fromisoformat(str(llegada)[:10])
            data["imp_dias_libres_almacenaje"] = (fl - ll).days
        except Exception:
            pass

    # Días de despacho aduanero (Fecha cruce real - Llegada a puerto)
    data["imp_dias_despacho_aduanero"] = _calc_dias(
        data.get("imp_llegada_contenedor_puerto"),
        data.get("des_fecha_cruce_real")
    )

    # Días de tránsito terrestre (Llegada almacen - Cruce real)
    data["des_dias_transito_terrestre"] = _calc_dias(
        data.get("des_fecha_cruce_real"),
        data.get("des_llegada_almacen")
    )

    # Fecha límite naviera = ETA Puerto + Días sin demoras para devolver contenedor
    eta_puerto = data.get("log_eta_puerto")
    dias_sin_demoras = data.get("des_dias_sin_demoras")
    if eta_puerto is not None and dias_sin_demoras is not None:
        try:
            eta = date.fromisoformat(str(eta_puerto)[:10])
            data["des_fecha_limite_naviera"] = (eta + timedelta(days=int(dias_sin_demoras))).isoformat()
        except Exception:
            data["des_fecha_limite_naviera"] = None
    else:
        data["des_fecha_limite_naviera"] = None

    return data
