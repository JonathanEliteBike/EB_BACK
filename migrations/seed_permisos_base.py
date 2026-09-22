import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_conexion import obtener_conexion

def run():
    conn = obtener_conexion()
    cur = conn.cursor()

    try:
        print("Insertando rol: Administrador Cliente...")
        cur.execute("""
            INSERT IGNORE INTO roles (id, nombre)
            VALUES (4, 'Administrador Cliente')
        """)

        print("Insertando acciones base...")
        cur.execute("""
            INSERT INTO acciones (nombre, identificador, activo) VALUES
            ('Ver', 'ver', 1),
            ('Crear', 'crear', 1),
            ('Editar', 'editar', 1),
            ('Eliminar', 'eliminar', 1)
            ON DUPLICATE KEY UPDATE
                nombre = VALUES(nombre),
                activo = VALUES(activo)
        """)

        conn.commit()
        print("[OK] Base de datos poblada con éxito.")

    except Exception as e:
        conn.rollback()
        print(f"[ERROR] No se pudo ejecutar el seeder: {e}")
    finally:
        cur.close()
        conn.close()

if __name__ == '__main__':
    run()