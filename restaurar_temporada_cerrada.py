"""
Script para restaurar el previo de distribuidores cerrados cuyo acumulado
quedó en 0 debido al bug en _recalcular_acumulados_previo.

Uso: python restaurar_temporada_cerrada.py [CLAVE1 CLAVE2 ...]
     Sin argumentos: restaura TODOS los distribuidores cerrados.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from db_conexion import obtener_conexion
from routes.retroactivos import _recalcular_previo_clave_cierre

def restaurar(claves_a_restaurar=None):
    conn = obtener_conexion()
    cur_dict = conn.cursor(dictionary=True)
    cur = conn.cursor()

    try:
        if claves_a_restaurar:
            fmt = ','.join(['%s'] * len(claves_a_restaurar))
            cur_dict.execute(f"""
                SELECT clave, f_inicio, fecha_cierre_temporada
                FROM clientes
                WHERE temporada_cerrada = 1
                  AND UPPER(TRIM(clave)) IN ({fmt})
            """, [c.upper() for c in claves_a_restaurar])
        else:
            cur_dict.execute("""
                SELECT clave, f_inicio, fecha_cierre_temporada
                FROM clientes
                WHERE temporada_cerrada = 1
            """)

        distribuidores = cur_dict.fetchall()
        if not distribuidores:
            print("No se encontraron distribuidores cerrados.")
            return

        for d in distribuidores:
            clave = d['clave'].strip().upper()
            f_ini = d['f_inicio']
            f_cie = d['fecha_cierre_temporada']

            if hasattr(f_ini, 'strftime'):
                f_ini = f_ini.strftime('%Y-%m-%d')
            if hasattr(f_cie, 'strftime'):
                f_cie = f_cie.strftime('%Y-%m-%d')

            f_ini = f_ini or '2025-07-01'
            if not f_cie:
                print(f"[SKIP] {clave}: sin fecha_cierre_temporada")
                continue

            print(f"[RESTAURANDO] {clave}  f_inicio={f_ini}  fecha_cierre={f_cie}")
            _recalcular_previo_clave_cierre(conn, cur_dict, cur, clave, f_ini, f_cie)
            conn.commit()
            print(f"[OK] {clave}")

        # Actualizar tabla_retroactivos con los nuevos valores de previo
        cur.execute("""
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
        conn.commit()
        print(f"\n[OK] tabla_retroactivos actualizada ({cur.rowcount} filas).")

    finally:
        cur_dict.close()
        cur.close()
        conn.close()

if __name__ == '__main__':
    claves = sys.argv[1:] if len(sys.argv) > 1 else None
    restaurar(claves)
