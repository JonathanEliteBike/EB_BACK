"""
Script: find_missing_megamo.py
Corre en el servidor para encontrar productos MEGAMO en odoo_catalogo
que NO están en forecast_sku_whitelist.
Estos son los productos que aparecen en Odoo pero no en la búsqueda.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db_conexion import obtener_conexion

conn = obtener_conexion()
cur  = conn.cursor(dictionary=True)

# 1. Productos MEGAMO en catalogo pero NO en whitelist
print("=== MEGAMO en odoo_catalogo pero NO en whitelist ===")
cur.execute("""
    SELECT oc.referencia_interna, oc.nombre_producto, oc.marca, oc.color, oc.talla
    FROM odoo_catalogo oc
    WHERE (oc.marca LIKE '%MEGAMO%' OR oc.nombre_producto LIKE '%MEGAMO%')
      AND oc.referencia_interna NOT IN (SELECT sku FROM forecast_sku_whitelist)
    ORDER BY oc.nombre_producto, oc.referencia_interna
""")
rows = cur.fetchall()
print(f"Total: {len(rows)} SKUs fuera del whitelist")
print()

# Agrupar por nombre de producto para ver cuántos grupos únicos son
nombres = {}
for r in rows:
    n = ' '.join((r.get('nombre_producto') or '').split()).upper()
    nombres.setdefault(n, []).append(r['referencia_interna'])

print(f"Grupos únicos (templates): {len(nombres)}")
print()
for nombre, skus in sorted(nombres.items()):
    print(f"  [{len(skus)} SKUs] {nombre}")
    for s in skus[:5]:
        print(f"    - {s}")
    if len(skus) > 5:
        print(f"    ... y {len(skus)-5} más")

# 2. Cuántos grupos hay actualmente en whitelist × catalogo
print()
print("=== Grupos actuales en whitelist + catalogo ===")
cur.execute("""
    SELECT COUNT(DISTINCT oc.nombre_producto) as grupos
    FROM odoo_catalogo oc
    INNER JOIN forecast_sku_whitelist wl ON wl.sku = oc.referencia_interna
    WHERE oc.nombre_producto LIKE '%megamo%' OR oc.marca LIKE '%megamo%'
""")
print(f"Grupos con whitelist: {cur.fetchone()['grupos']}")

cur.close()
conn.close()
