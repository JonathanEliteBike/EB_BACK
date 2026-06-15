import sys
sys.path.insert(0, '.')
from db_conexion import obtener_conexion

conn = obtener_conexion()
cur = conn.cursor(dictionary=True)

# Grupos actuales con whitelist
cur.execute("""
    SELECT COUNT(DISTINCT oc.nombre_producto) as grupos
    FROM odoo_catalogo oc
    INNER JOIN forecast_sku_whitelist wl ON wl.sku = oc.referencia_interna
    WHERE oc.nombre_producto LIKE '%megamo%' OR oc.marca LIKE '%megamo%'
""")
print(f"Grupos con whitelist (local): {cur.fetchone()['grupos']}")

# Grupos TOTALES MEGAMO en catalogo (sin whitelist)
cur.execute("""
    SELECT COUNT(DISTINCT oc.nombre_producto) as grupos
    FROM odoo_catalogo oc
    WHERE oc.nombre_producto LIKE '%megamo%' OR oc.marca LIKE '%megamo%'
""")
print(f"Grupos totales MEGAMO en catalogo (local): {cur.fetchone()['grupos']}")

# Ver bicicletas MEGAMO en catalogo pero NO en whitelist
cur.execute("""
    SELECT DISTINCT oc.nombre_producto, MIN(oc.referencia_interna) as sku_ejemplo
    FROM odoo_catalogo oc
    WHERE (oc.nombre_producto LIKE '%megamo%' OR oc.marca LIKE '%megamo%')
      AND oc.nombre_producto LIKE '%BICICLETA%'
      AND oc.referencia_interna NOT IN (SELECT sku FROM forecast_sku_whitelist)
    GROUP BY oc.nombre_producto
    ORDER BY oc.nombre_producto
    LIMIT 30
""")
rows = cur.fetchall()
print(f"\nBicicletas MEGAMO fuera del whitelist ({len(rows)} grupos):")
for r in rows:
    print(f"  {r['sku_ejemplo']:25} {r['nombre_producto']}")

cur.close()
conn.close()
