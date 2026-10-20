import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Ejecutar el sync directamente sin Flask
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from app import app
with app.app_context():
    from routes.retroactivos import ejecutar_sincronizacion_y_calculos
    print("Iniciando sincronizacion...")
    ejecutar_sincronizacion_y_calculos()
    print("Sincronizacion completada.")

    # Verificar resultados en DB
    from db_conexion import obtener_conexion
    conn = obtener_conexion()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT CLAVE, CLIENTE, bicicletas_bold, notas_credito, garantias, importe_final
        FROM tabla_retroactivos
        WHERE bicicletas_bold > 0 OR CLAVE IN ('JE537','4E013','BF149','LD648','LD664','ND728','FA271','HA433','HF427')
        ORDER BY bicicletas_bold DESC
        LIMIT 30
    """)
    rows = cur.fetchall()
    print()
    print(f"{'CLAVE':<8} {'CLIENTE':<40} {'BICIS_BOLD':>12} {'NC':>12} {'GARANTIAS':>12} {'IMPORTE':>14}")
    print("-"*102)
    for r in rows:
        print(f"{str(r['CLAVE'] or ''):<8} {str(r['CLIENTE'] or '')[:39]:<40} {float(r['bicicletas_bold'] or 0):>12,.2f} {float(r['notas_credito'] or 0):>12,.2f} {float(r['garantias'] or 0):>12,.2f} {float(r['importe_final'] or 0):>14,.2f}")
    cur.close()
    conn.close()
