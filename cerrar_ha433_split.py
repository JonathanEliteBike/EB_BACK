"""
Cierre especial HA433 (Lucia Salazar) con dos fechas de corte:
  - Scott / bicicletas / NC / garantias / ofertados  ->  31/05/2026
  - Apparel / Syncros / Vittoria                     ->  13/06/2026
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from db_conexion import obtener_conexion

CLAVE        = 'HA433'
F_INICIO     = '2025-06-11'
FECHA_SCOTT  = '2026-05-31'   # cierre general + NC/garantias/ofertados
FECHA_APP    = '2026-06-13'   # cierre apparel/syncros/vittoria

SCOTT_COND = """(
    (
        UPPER(TRIM(m.marca)) IN ('SCOTT','MEGAMO')
        AND (UPPER(TRIM(m.subcategoria))='BICICLETA' OR UPPER(m.nombre_producto) LIKE '%%BICICLETA%%')
        AND (UPPER(TRIM(m.apparel))='NO'             OR UPPER(m.nombre_producto) LIKE '%%BICICLETA%%')
    )
    OR (UPPER(TRIM(m.marca))='BOLD' AND UPPER(TRIM(m.subcategoria))='BICICLETA')
)"""

APP_COND  = "(m.apparel='SI' OR (m.marca='BOLD' AND UPPER(TRIM(COALESCE(m.subcategoria,'')))!='BICICLETA'))"
SYN_COND  = "m.marca='SYNCROS'"
VIT_COND  = "m.marca='VITTORIA'"

PERIODS = ['jul_ago','sep_oct','nov_dic','ene_feb','mar_abr','may_jun']
PERIOD_RANGES = {
    'jul_ago': (F_INICIO,    '2025-08-31'),
    'sep_oct': ('2025-09-01','2025-10-31'),
    'nov_dic': ('2025-11-01','2025-12-31'),
    'ene_feb': ('2026-01-01','2026-02-28'),
    'mar_abr': ('2026-03-01','2026-04-30'),
    'may_jun': ('2026-05-01','2026-06-30'),
}

def flt(v): return float(v or 0)
def cap(fecha, limite): return min(fecha, limite)

def query_acum(cur, cond, fecha_desde, fecha_hasta):
    cur.execute(f"""
        SELECT COALESCE(SUM(m.venta_total), 0) AS total
        FROM monitor m
        WHERE m.contacto_referencia = %s
          AND m.fecha_factura >= %s
          AND m.fecha_factura <= %s
          AND {cond}
    """, (CLAVE, fecha_desde, fecha_hasta))
    return flt(cur.fetchone()['total'])


def main():
    conn = obtener_conexion()
    cur  = conn.cursor(dictionary=True)
    cur2 = conn.cursor()

    print(f"[HA433] Calculando cierre especial  Scott<={FECHA_SCOTT}  App<={FECHA_APP}")

    # ── 1. Acumulados globales ──────────────────────────────────────────────────

    # Total bruto = todo hasta mayo31 + apparel/syncros/vittoria de junio 1-13
    cur.execute(f"""
        SELECT COALESCE(SUM(m.venta_total), 0) AS base
        FROM monitor m
        WHERE m.contacto_referencia = %s
          AND m.fecha_factura >= %s AND m.fecha_factura <= %s
    """, (CLAVE, F_INICIO, FECHA_SCOTT))
    base = flt(cur.fetchone()['base'])

    cur.execute(f"""
        SELECT COALESCE(SUM(m.venta_total), 0) AS extra
        FROM monitor m
        WHERE m.contacto_referencia = %s
          AND m.fecha_factura > %s AND m.fecha_factura <= %s
          AND ({APP_COND} OR {SYN_COND} OR {VIT_COND})
    """, (CLAVE, FECHA_SCOTT, FECHA_APP))
    extra = flt(cur.fetchone()['extra'])

    total_bruto = round(base + extra, 2)

    scott    = query_acum(cur, SCOTT_COND, F_INICIO, FECHA_SCOTT)
    apparel  = query_acum(cur, APP_COND,   F_INICIO, FECHA_APP)
    syncros  = query_acum(cur, SYN_COND,   F_INICIO, FECHA_APP)
    vittoria = query_acum(cur, VIT_COND,   F_INICIO, FECHA_APP)

    cur.execute(f"""
        SELECT COALESCE(SUM(m.venta_total), 0) AS bold
        FROM monitor m
        WHERE m.contacto_referencia=%s
          AND m.fecha_factura>=%s AND m.fecha_factura<=%s
          AND UPPER(TRIM(m.marca))='BOLD' AND UPPER(TRIM(m.subcategoria))='BICICLETA'
    """, (CLAVE, F_INICIO, FECHA_SCOTT))
    bold = flt(cur.fetchone()['bold'])

    app_global = round(apparel + syncros + vittoria, 2)

    print(f"  total_bruto  = {total_bruto:,.2f}")
    print(f"  scott        = {scott:,.2f}")
    print(f"  apparel      = {apparel:,.2f}")
    print(f"  syncros      = {syncros:,.2f}")
    print(f"  vittoria     = {vittoria:,.2f}")
    print(f"  app_global   = {app_global:,.2f}")

    # ── 2. Acumulados por periodo ───────────────────────────────────────────────
    p_scott   = {}
    p_syncros = {}
    p_apparel = {}
    p_vittoria= {}

    for p, (p_ini, p_fin) in PERIOD_RANGES.items():
        desde_s = max(p_ini, F_INICIO)
        hasta_s = cap(p_fin, FECHA_SCOTT)
        hasta_a = cap(p_fin, FECHA_APP)

        p_scott[p]    = query_acum(cur, SCOTT_COND, desde_s, hasta_s) if hasta_s >= desde_s else 0.0
        p_syncros[p]  = query_acum(cur, SYN_COND,   desde_s, hasta_a) if hasta_a >= desde_s else 0.0
        p_apparel[p]  = query_acum(cur, APP_COND,   desde_s, hasta_a) if hasta_a >= desde_s else 0.0
        p_vittoria[p] = query_acum(cur, VIT_COND,   desde_s, hasta_a) if hasta_a >= desde_s else 0.0

    p_app = {p: round(p_syncros[p] + p_apparel[p] + p_vittoria[p], 2) for p in PERIODS}

    # ── 3. Leer compromisos de previo ───────────────────────────────────────────
    cur.execute("""
        SELECT id, compromiso_scott, compromiso_apparel_syncros_vittoria,
               compromiso_jul_ago, compromiso_sep_oct, compromiso_nov_dic,
               compromiso_ene_feb, compromiso_mar_abr, compromiso_may_jun,
               compromiso_jul_ago_app, compromiso_sep_oct_app, compromiso_nov_dic_app,
               compromiso_ene_feb_app, compromiso_mar_abr_app, compromiso_may_jun_app,
               compra_minima_inicial, compra_minima_anual
        FROM previo
        WHERE UPPER(TRIM(clave)) = %s AND (es_integral IS NULL OR es_integral = 0)
        LIMIT 1
    """, (CLAVE,))
    previo = cur.fetchone()
    if not previo:
        print("[ERROR] No se encontró fila en previo para HA433")
        return

    def pct(avance, comp): return int(round(avance / comp * 100)) if comp > 0 else 0

    cm_ini = flt(previo['compra_minima_inicial'])
    cm_anu = flt(previo['compra_minima_anual'])

    # ── 4. Actualizar previo ────────────────────────────────────────────────────
    cur2.execute("""
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
        total_bruto, syncros, apparel, vittoria, bold,
        total_bruto, scott, app_global,
        pct(total_bruto, cm_ini), pct(total_bruto, cm_anu),
        pct(scott,     flt(previo['compromiso_scott'])),
        pct(app_global,flt(previo['compromiso_apparel_syncros_vittoria'])),
        p_scott['jul_ago'], pct(p_scott['jul_ago'], flt(previo['compromiso_jul_ago'])),
        p_scott['sep_oct'], pct(p_scott['sep_oct'], flt(previo['compromiso_sep_oct'])),
        p_scott['nov_dic'], pct(p_scott['nov_dic'], flt(previo['compromiso_nov_dic'])),
        p_scott['ene_feb'], pct(p_scott['ene_feb'], flt(previo['compromiso_ene_feb'])),
        p_scott['mar_abr'], pct(p_scott['mar_abr'], flt(previo['compromiso_mar_abr'])),
        p_scott['may_jun'], pct(p_scott['may_jun'], flt(previo['compromiso_may_jun'])),
        p_app['jul_ago'],   pct(p_app['jul_ago'],   flt(previo['compromiso_jul_ago_app'])),
        p_app['sep_oct'],   pct(p_app['sep_oct'],   flt(previo['compromiso_sep_oct_app'])),
        p_app['nov_dic'],   pct(p_app['nov_dic'],   flt(previo['compromiso_nov_dic_app'])),
        p_app['ene_feb'],   pct(p_app['ene_feb'],   flt(previo['compromiso_ene_feb_app'])),
        p_app['mar_abr'],   pct(p_app['mar_abr'],   flt(previo['compromiso_mar_abr_app'])),
        p_app['may_jun'],   pct(p_app['may_jun'],   flt(previo['compromiso_may_jun_app'])),
        previo['id']
    ))

    # ── 5. Cerrar HA433 — f_fin=31/05 (NC/garantias/ofertados usan esta fecha) ─
    cur2.execute("""
        UPDATE clientes
        SET temporada_cerrada      = 1,
            fecha_cierre_temporada = %s,
            f_fin                  = %s
        WHERE UPPER(TRIM(clave)) = %s
    """, (FECHA_SCOTT, FECHA_SCOTT, CLAVE))

    conn.commit()
    print("[OK] previo y clientes actualizados")

    # ── 6. Copiar previo -> tabla_retroactivos ──────────────────────────────────
    cur2.execute("""
        UPDATE tabla_retroactivos tr
        JOIN previo p ON UPPER(TRIM(tr.CLAVE)) = UPPER(TRIM(p.clave))
        SET
            tr.COMPRAS_TOTALES_CRUDO = COALESCE(p.acumulado_anticipado, 0),
            tr.COMPRA_GLOBAL_SCOTT   = COALESCE(p.avance_global_scott, 0),
            tr.COMPRA_GLOBAL_APPAREL = COALESCE(p.avance_global_apparel_syncros_vittoria, 0),
            tr.COMPRA_GLOBAL_BOLD    = COALESCE(p.acumulado_bold, 0)
        WHERE tr.CLAVE = %s
    """, (CLAVE,))
    conn.commit()
    print(f"[OK] tabla_retroactivos actualizada ({cur2.rowcount} filas)")

    cur.close(); cur2.close(); conn.close()


if __name__ == '__main__':
    main()
