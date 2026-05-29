from db_conexion import obtener_conexion
conn = obtener_conexion()
cur = conn.cursor(dictionary=True)
cur.execute("""
    SELECT p.clave,
        ROUND(p.acumulado_anticipado,2)  AS p_acum,
        ROUND(p.acumulado_bold,2)        AS p_bold,
        ROUND(r.TOTAL_ACUMULADO,2)       AS r_total,
        ROUND(r.COMPRA_GLOBAL_BOLD,2)    AS r_bold,
        ROUND(r.TOTAL_ACUMULADO - p.acumulado_anticipado, 2) AS diferencia
    FROM previo p
    JOIN tabla_retroactivos r ON r.CLAVE = p.clave
    WHERE p.acumulado_bold > 0
    ORDER BY p.acumulado_bold DESC
    LIMIT 6
""")
rows = cur.fetchall()
print(f"{'CLAVE':<10} {'P_ACUM':>13} {'R_TOTAL':>13} {'DIFERENCIA':>12} {'BOLD_BICIS':>11}")
print("-" * 65)
for r in rows:
    print(f"{r['clave']:<10} {str(r['p_acum']):>13} {str(r['r_total']):>13} {str(r['diferencia']):>12} {str(r['p_bold']):>11}")
cur.close(); conn.close()
