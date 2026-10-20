# services/acciones_service.py

from db_conexion import obtener_conexion

class AccionesService:

    @staticmethod
    def listar_acciones():
        """Lista todas las acciones base disponibles en el sistema."""
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT id, nombre, identificador, activo FROM acciones ORDER BY id ASC")
            return cur.fetchall()
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def crear_accion(datos):
        """Crea una nueva acción base global."""
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT id FROM acciones WHERE identificador = %s", (datos['identificador'],))
            if cur.fetchone():
                raise Exception(f"La acción '{datos['identificador']}' ya se encuentra registrada.")

            sql = "INSERT INTO acciones (nombre, identificador, activo) VALUES (%s, %s, 1)"
            cur.execute(sql, (datos['nombre'], datos['identificador']))
            conn.commit()
            return {"id": cur.lastrowid, "mensaje": "Acción creada correctamente."}
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def cambiar_estado_accion(accion_id, activo):
        """Activa (1) o desactiva (0) una acción sin borrar datos."""
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            cur.execute("UPDATE acciones SET activo = %s WHERE id = %s", (activo, accion_id))
            conn.commit()
            estado_texto = "activada" if activo == 1 else "desactivada"
            return {"mensaje": f"Acción {estado_texto} correctamente."}
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def eliminar_accion(accion_id):
        """Elimina físicamente una acción y sus tablas vinculadas."""
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            cur.execute("DELETE FROM modulo_acciones WHERE accion_id = %s", (accion_id,))
            cur.execute("DELETE FROM permisos_delegables WHERE accion_id = %s", (accion_id,))
            cur.execute("DELETE FROM usuario_permisos WHERE accion_id = %s", (accion_id,))
            cur.execute("DELETE FROM acciones WHERE id = %s", (accion_id,))
            conn.commit()
            return {"mensaje": "Acción eliminada permanentemente."}
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()