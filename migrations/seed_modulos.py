# migrations/seed_modulos.py

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_conexion import obtener_conexion

def run():
    conn = obtener_conexion()
    cur = conn.cursor(dictionary=True)

    try:
        print("Obteniendo catálogo de acciones base...")
        cur.execute("SELECT id, identificador FROM acciones")
        acciones_db = cur.fetchall()

        # Crear un diccionario para acceder rápido al ID de cada acción: {'ver': 1, 'crear': 2, ...}
        acciones = {acc['identificador']: acc['id'] for acc in acciones_db}

        if not acciones:
            raise Exception("No se encontraron acciones. Corre seed_permisos_base.py primero.")

        print("Creando árbol jerárquico de Módulos...")

        # 1. MÓDULO RAÍZ: Garantías
        cur.execute("""
            INSERT INTO modulos (nombre, identificador, activo)
            VALUES ('Garantías', 'garantias', 1)
        """)
        garantias_id = cur.lastrowid

        # 2. SUBMÓDULOS de Garantías (Solicitudes y Reportes)
        cur.execute("""
            INSERT INTO modulos (padre_id, nombre, identificador, activo)
            VALUES (%s, 'Solicitudes', 'garantias_solicitudes', 1)
        """, (garantias_id,))
        solicitudes_id = cur.lastrowid

        cur.execute("""
            INSERT INTO modulos (padre_id, nombre, identificador, activo)
            VALUES (%s, 'Reportes', 'garantias_reportes', 1)
        """, (garantias_id,))
        reportes_id = cur.lastrowid

        print("Asignando acciones a los submódulos...")

        # 3. VINCULAR MÓDULOS CON ACCIONES (Tabla pivote: modulo_acciones)
        # A 'Solicitudes' le damos Ver, Crear, Editar, Eliminar
        acciones_solicitudes = ['ver', 'crear', 'editar', 'eliminar']
        for accion in acciones_solicitudes:
            cur.execute("""
                INSERT IGNORE INTO modulo_acciones (modulo_id, accion_id)
                VALUES (%s, %s)
            """, (solicitudes_id, acciones[accion]))

        # A 'Reportes' le damos solo Ver
        cur.execute("""
            INSERT IGNORE INTO modulo_acciones (modulo_id, accion_id)
            VALUES (%s, %s)
        """, (reportes_id, acciones['ver']))

        conn.commit()
        print("[OK] Módulos y sus acciones fueron sembrados correctamente.")

    except Exception as e:
        conn.rollback()
        print(f"[ERROR] Ocurrió un error al ejecutar el seeder de módulos: {e}")
    finally:
        cur.close()
        conn.close()

if __name__ == '__main__':
    run()