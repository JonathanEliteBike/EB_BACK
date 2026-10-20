import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from app import app
with app.app_context():
    # Paso 1: Recalcular previo (actualiza COMPRA_GLOBAL_BOLD con solo bikes)
    from routes.monitor_odoo import _recalcular_acumulados_previo
    from db_conexion import obtener_conexion
    print("=== PASO 1: Recalculando previo (COMPRA_GLOBAL_BOLD solo bikes) ===")
    conn1 = obtener_conexion()
    cur1  = conn1.cursor(dictionary=True)
    _recalcular_acumulados_previo(conn1, cur1)
    conn1.commit()
    cur1.close()
    conn1.close()
    print("Previo recalculado.")

    # Paso 2: Sync retroactivos (usa COMPRA_GLOBAL_BOLD ya corregido)
    from routes.retroactivos import ejecutar_sincronizacion_y_calculos
    print()
    print("=== PASO 2: Sync retroactivos ===")
    ejecutar_sincronizacion_y_calculos()
    print("Sync completado.")

    # Verificar resultados
    from db_conexion import obtener_conexion
    conn = obtener_conexion()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT CLAVE, CLIENTE, COMPRA_GLOBAL_BOLD, bicicletas_bold, notas_credito, importe_final
        FROM tabla_retroactivos
        WHERE bicicletas_bold > 0
           OR CLAVE IN ('JE537','4E013','BF149','LD648','LD664','ND728','FA271','HA433','HF427','LC662')
        ORDER BY bicicletas_bold DESC
        LIMIT 30
    """)
    rows = cur.fetchall()
    print()
    print(f"{'CLAVE':<10} {'CLIENTE':<38} {'GLOB_BOLD':>12} {'BICI_BOLD':>12} {'NC':>12} {'IMPORTE':>14}")
    print("-"*102)
    for r in rows:
        print(f"{str(r['CLAVE'] or ''):<10} {str(r['CLIENTE'] or '')[:37]:<38} {float(r['COMPRA_GLOBAL_BOLD'] or 0):>12,.2f} {float(r['bicicletas_bold'] or 0):>12,.2f} {float(r['notas_credito'] or 0):>12,.2f} {float(r['importe_final'] or 0):>14,.2f}")
    cur.close()
    conn.close()
