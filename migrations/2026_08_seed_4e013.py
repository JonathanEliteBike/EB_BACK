"""
Seed script one-shot: registra 4E013 (Oaxaca, Christian Boccaletti) como cliente independiente.
Lee f_inicio y f_fin directamente de JE537 en la DB.
Es idempotente: verifica existencia antes de cada INSERT.

Uso:
    python migrations/2026_08_seed_4e013.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_conexion import obtener_conexion
from utils.seguridad import hash_password

CLAVE_NUEVA  = '4E013'
CLAVE_PADRE  = 'JE537'
NOMBRE_NUEVO = 'CHRISTIAN BOCCALETTI'
EMAIL_NUEVO  = 'zonabicioax@gmail.com'
CLAVE_TEMP   = 'temp1234'  # contraseña temporal; cambiar en el primer login


def run():
    conn = obtener_conexion()
    cur  = conn.cursor(dictionary=True)

    # ── 1. Leer datos del padre JE537 ─────────────────────────────────────────
    cur.execute(
        "SELECT id, evac, nivel, id_grupo, f_inicio, f_fin FROM clientes WHERE clave = %s",
        (CLAVE_PADRE,)
    )
    padre = cur.fetchone()
    if not padre:
        print(f"ERROR: No se encontró {CLAVE_PADRE} en clientes. Verifica la DB.")
        sys.exit(1)

    evac     = padre['evac']
    nivel    = padre['nivel']
    id_grupo = padre['id_grupo']
    f_inicio = padre['f_inicio']
    f_fin    = padre['f_fin']
    print(f"{CLAVE_PADRE} encontrado — evac={evac}, nivel={nivel}, f_inicio={f_inicio}, f_fin={f_fin}")

    # ── 2. UPSERT en clientes ──────────────────────────────────────────────────
    cur.execute("SELECT id, evac, nivel FROM clientes WHERE clave = %s", (CLAVE_NUEVA,))
    existing = cur.fetchone()
    if existing and existing['evac'] and existing['nivel']:
        print(f"[SKIP] clientes.{CLAVE_NUEVA} ya existe con datos completos.")
    elif existing:
        # Existe pero le faltan datos — actualizar
        cur.execute(
            """UPDATE clientes SET evac=%s, nombre_cliente=%s, nivel=%s,
               f_inicio=%s, f_fin=%s, id_grupo=%s WHERE clave=%s""",
            (evac, NOMBRE_NUEVO, nivel, f_inicio, f_fin, id_grupo, CLAVE_NUEVA)
        )
        conn.commit()
        print(f"[OK]   UPDATE clientes: evac={evac}, nivel={nivel} (fila existente estaba incompleta)")
    else:
        cur.execute(
            """INSERT INTO clientes
               (clave, evac, nombre_cliente, nivel, f_inicio, f_fin, id_grupo)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (CLAVE_NUEVA, evac, NOMBRE_NUEVO, nivel, f_inicio, f_fin, id_grupo)
        )
        conn.commit()
        print(f"[OK]   INSERT clientes: clave={CLAVE_NUEVA}, evac={evac}, nivel={nivel}")

    cur.execute("SELECT id FROM clientes WHERE clave = %s", (CLAVE_NUEVA,))
    id_nuevo = cur.fetchone()['id']

    # ── 3. INSERT en previo (copia de JE537, avances en 0) ────────────────────
    cur.execute("SELECT * FROM previo WHERE clave = %s LIMIT 1", (CLAVE_PADRE,))
    previo_padre = cur.fetchone()

    cur.execute("SELECT id, compromiso_scott, evac FROM previo WHERE clave = %s", (CLAVE_NUEVA,))
    prev_existing = cur.fetchone()
    if prev_existing and prev_existing['compromiso_scott'] and prev_existing['evac']:
        print(f"[SKIP] previo.{CLAVE_NUEVA} ya existe con datos completos.")
    elif prev_existing and previo_padre:
        # Existe pero vacía — actualizar compromisos desde padre
        cur.execute("""
            UPDATE previo SET
                evac=%s, nombre_cliente=%s, nivel=%s,
                compra_minima_anual=%s, compra_minima_inicial=%s,
                compromiso_scott=%s,
                compromiso_jul_ago=%s, compromiso_sep_oct=%s,
                compromiso_nov_dic=%s, compromiso_ene_feb=%s,
                compromiso_mar_abr=%s, compromiso_may_jun=%s,
                compromiso_apparel_syncros_vittoria=%s,
                compromiso_jul_ago_app=%s, compromiso_sep_oct_app=%s,
                compromiso_nov_dic_app=%s, compromiso_ene_feb_app=%s,
                compromiso_mar_abr_app=%s, compromiso_may_jun_app=%s
            WHERE clave=%s
        """, (
            previo_padre['evac'], NOMBRE_NUEVO, previo_padre['nivel'],
            previo_padre['compra_minima_anual'], previo_padre['compra_minima_inicial'],
            previo_padre['compromiso_scott'],
            previo_padre['compromiso_jul_ago'], previo_padre['compromiso_sep_oct'],
            previo_padre['compromiso_nov_dic'], previo_padre['compromiso_ene_feb'],
            previo_padre['compromiso_mar_abr'], previo_padre['compromiso_may_jun'],
            previo_padre['compromiso_apparel_syncros_vittoria'],
            previo_padre['compromiso_jul_ago_app'], previo_padre['compromiso_sep_oct_app'],
            previo_padre['compromiso_nov_dic_app'], previo_padre['compromiso_ene_feb_app'],
            previo_padre['compromiso_mar_abr_app'], previo_padre['compromiso_may_jun_app'],
            CLAVE_NUEVA
        ))
        conn.commit()
        print(f"[OK]   UPDATE previo.{CLAVE_NUEVA} (fila existente estaba vacía, compromisos copiados de {CLAVE_PADRE})")
    elif previo_padre:
        previo_padre.pop('id', None)
        previo_padre['clave']          = CLAVE_NUEVA
        previo_padre['nombre_cliente'] = NOMBRE_NUEVO
        # Resetear avances a 0 — 4E013 empieza desde cero esta temporada
        for col in list(previo_padre.keys()):
            if col.startswith('avance_') or col.startswith('compra_real') or col == 'total_avance':
                previo_padre[col] = 0
        cols         = ', '.join(f'`{k}`' for k in previo_padre.keys())
        placeholders = ', '.join(['%s'] * len(previo_padre))
        cur.execute(
            f"INSERT INTO previo ({cols}) VALUES ({placeholders})",
            list(previo_padre.values())
        )
        conn.commit()
        print(f"[OK]   INSERT previo.{CLAVE_NUEVA} (compromisos copiados de {CLAVE_PADRE}, avances en 0)")
    else:
        print(f"[WARN] No existe fila previo para {CLAVE_PADRE} — se omite previo de {CLAVE_NUEVA}")

    # ── 4. INSERT en tabla_retroactivos (una fila por categoría, copiada de JE537) ─
    cur.execute(
        "SELECT * FROM tabla_retroactivos WHERE UPPER(TRIM(CLAVE)) = %s",
        (CLAVE_PADRE.upper(),)
    )
    retros_padre = cur.fetchall()

    cur.execute(
        "SELECT id FROM tabla_retroactivos WHERE UPPER(TRIM(CLAVE)) = %s",
        (CLAVE_NUEVA.upper(),)
    )
    if cur.fetchone():
        print(f"[SKIP] tabla_retroactivos.{CLAVE_NUEVA} ya existe.")
    elif retros_padre:
        for row in retros_padre:
            row.pop('id', None)
            row['CLAVE']   = CLAVE_NUEVA
            row['CLIENTE'] = NOMBRE_NUEVO
            # Resetear compras/avances acumulados a 0
            for col in list(row.keys()):
                if any(col.upper().startswith(p) for p in ('COMPRA_REAL', 'AVANCE', 'TOTAL_COMP')):
                    row[col] = 0
            cols         = ', '.join(f'`{k}`' for k in row.keys())
            placeholders = ', '.join(['%s'] * len(row))
            cur.execute(
                f"INSERT INTO tabla_retroactivos ({cols}) VALUES ({placeholders})",
                list(row.values())
            )
        conn.commit()
        print(f"[OK]   INSERT tabla_retroactivos.{CLAVE_NUEVA} ({len(retros_padre)} filas copiadas de {CLAVE_PADRE})")
    else:
        print(f"[WARN] No existen filas retroactivos para {CLAVE_PADRE} — se omite")

    # ── 5. INSERT en usuarios ──────────────────────────────────────────────────
    cur.execute(
        "SELECT id FROM usuarios WHERE correo = %s OR usuario = %s",
        (EMAIL_NUEVO, CLAVE_NUEVA.lower())
    )
    if cur.fetchone():
        print(f"[SKIP] usuario {EMAIL_NUEVO} ya existe.")
    else:
        hashed = hash_password(CLAVE_TEMP)
        cur.execute(
            """INSERT INTO usuarios
               (usuario, contrasena, nombre, correo, rol_id, activo, cliente_id)
               VALUES (%s, %s, %s, %s, 2, 1, %s)""",
            (CLAVE_NUEVA.lower(), hashed, NOMBRE_NUEVO, EMAIL_NUEVO, id_nuevo)
        )
        conn.commit()
        print(f"[OK]   INSERT usuarios: login={CLAVE_NUEVA.lower()}, correo={EMAIL_NUEVO}")

    cur.close()
    conn.close()

    print()
    print("=" * 60)
    print(f"Seed completo para {CLAVE_NUEVA}.")
    print(f"Contraseña temporal de login: {CLAVE_TEMP}")
    print("Verificar con:")
    print(f"  SELECT id, clave, evac, nivel, f_inicio FROM clientes WHERE clave = '{CLAVE_NUEVA}';")
    print(f"  SELECT id, clave, nombre_cliente FROM previo WHERE clave = '{CLAVE_NUEVA}';")
    print(f"  SELECT CLAVE, CLIENTE, CATEGORIA FROM tabla_retroactivos WHERE CLAVE = '{CLAVE_NUEVA}';")
    print(f"  SELECT usuario, correo, rol_id, activo FROM usuarios WHERE usuario = '{CLAVE_NUEVA.lower()}';")
    print("=" * 60)


if __name__ == '__main__':
    run()
