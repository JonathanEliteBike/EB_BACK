"""
Diagnostica qué grupos MEGAMO están en el whitelist pero no en odoo_catalogo.
Corre en el SERVIDOR: python check_grupos_vs_wl.py
"""
import sys
sys.path.insert(0, '.')
from db_conexion import obtener_conexion

conn = obtener_conexion()
cur = conn.cursor(dictionary=True)

# 1. Grupos visibles en búsqueda (INNER JOIN whitelist × catalogo)
cur.execute("""
    SELECT DISTINCT oc.nombre_producto AS nombre
    FROM odoo_catalogo oc
    INNER JOIN forecast_sku_whitelist wl ON wl.sku = oc.referencia_interna
    WHERE oc.nombre_producto LIKE '%megamo%' OR oc.marca LIKE '%megamo%'
    ORDER BY oc.nombre_producto
""")
grupos_con_wl = [r['nombre'] for r in cur.fetchall()]
print(f"Grupos visibles en busqueda: {len(grupos_con_wl)}")
for g in grupos_con_wl:
    print(f"  {g}")

# 2. SKUs del whitelist NO en odoo_catalogo (estos productos no se pueden buscar)
cur.execute("""
    SELECT wl.sku
    FROM forecast_sku_whitelist wl
    WHERE wl.sku LIKE 'MH%'
      AND wl.sku NOT IN (SELECT referencia_interna FROM odoo_catalogo)
    ORDER BY wl.sku
""")
missing_in_cat = [r['sku'] for r in cur.fetchall()]
print(f"\nSKUs MH del whitelist que NO estan en odoo_catalogo: {len(missing_in_cat)}")
if missing_in_cat:
    prefijos = {}
    for sku in missing_in_cat:
        p = sku[:7]
        prefijos.setdefault(p, []).append(sku)
    for p, skus in sorted(prefijos.items()):
        print(f"  {p} ({len(skus)} SKUs): ejemplo {skus[0]}")

# 3. Total MEGAMO en catalogo
cur.execute("SELECT COUNT(*) as cnt FROM odoo_catalogo WHERE nombre_producto LIKE '%megamo%' OR marca LIKE '%megamo%'")
print(f"\nTotal filas MEGAMO en odoo_catalogo: {cur.fetchone()['cnt']}")

cur.close()
conn.close()
