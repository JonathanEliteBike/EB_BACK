# services/modulos_service.py

from db_conexion import obtener_conexion

class ModulosService:

    @staticmethod
    def listar_modulos():
        """Lista todos los módulos y submódulos junto con sus acciones vinculadas."""
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("""
                SELECT id, padre_id, nombre, identificador, activo,
                       delegable_a_hijos, creado_en
                FROM modulos
                ORDER BY id ASC
            """)
            modulos = cur.fetchall()

            # Adjuntar las acciones asociadas a cada módulo
            for modulo in modulos:
                cur.execute("""
                    SELECT a.id, a.nombre, a.identificador
                    FROM modulo_acciones ma
                    INNER JOIN acciones a ON ma.accion_id = a.id
                    WHERE ma.modulo_id = %s AND a.activo = 1
                """, (modulo['id'],))
                modulo['acciones'] = cur.fetchall()

            return modulos
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def crear_modulo(datos):
        """Crea un módulo o submódulo y vincula sus acciones permitidas."""
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            # Validar si el identificador ya existe
            cur.execute("SELECT id FROM modulos WHERE identificador = %s", (datos['identificador'],))
            if cur.fetchone():
                raise Exception(f"El identificador '{datos['identificador']}' ya existe registrado.")

            sql = """
            INSERT INTO modulos
                (padre_id, nombre, identificador, activo, delegable_a_hijos)
            VALUES (%s, %s, %s, 1, %s)
            """
            cur.execute(sql, (
                datos.get('padre_id'),
                datos['nombre'],
                datos['identificador'],
                1 if datos.get('delegable_a_hijos', True) else 0,
            ))
            modulo_id = cur.lastrowid

            acciones = datos.get('acciones_ids', [])
            for accion_id in acciones:
                cur.execute("""
                    INSERT IGNORE INTO modulo_acciones (modulo_id, accion_id)
                    VALUES (%s, %s)
                """, (modulo_id, accion_id))

            conn.commit()
            return {"id": modulo_id, "mensaje": "Módulo creado correctamente."}
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def actualizar_modulo(modulo_id, datos):
        """Edita los datos base del módulo y reconfigura sus acciones vinculadas."""
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            campos = ["nombre = %s", "identificador = %s", "padre_id = %s"]
            valores = [
                datos['nombre'],
                datos['identificador'],
                datos.get('padre_id'),
            ]

            if 'delegable_a_hijos' in datos:
                campos.append("delegable_a_hijos = %s")
                valores.append(1 if datos['delegable_a_hijos'] else 0)

            valores.append(modulo_id)
            cur.execute(
                f"UPDATE modulos SET {', '.join(campos)} WHERE id = %s",
                tuple(valores),
            )

            # Actualizar acciones si vienen en la petición
            if 'acciones_ids' in datos:
                cur.execute("DELETE FROM modulo_acciones WHERE modulo_id = %s", (modulo_id,))
                for accion_id in datos['acciones_ids']:
                    cur.execute("""
                        INSERT INTO modulo_acciones (modulo_id, accion_id)
                        VALUES (%s, %s)
                    """, (modulo_id, accion_id))

            conn.commit()
            return {"mensaje": "Módulo actualizado correctamente."}
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def cambiar_estado_modulo(modulo_id, activo):
        """Activa (1) o desactiva (0) un módulo sin borrar datos."""
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            cur.execute("UPDATE modulos SET activo = %s WHERE id = %s", (activo, modulo_id))
            conn.commit()
            estado_texto = "activado" if activo == 1 else "desactivado"
            return {"mensaje": f"Módulo {estado_texto} correctamente."}
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def eliminar_modulo(modulo_id):
        """Elimina físicamente un módulo y limpia sus tablas vinculadas."""
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            cur.execute("DELETE FROM modulo_acciones WHERE modulo_id = %s", (modulo_id,))
            cur.execute("DELETE FROM permisos_delegables WHERE modulo_id = %s", (modulo_id,))
            cur.execute("DELETE FROM usuario_permisos WHERE modulo_id = %s", (modulo_id,))
            # Compatibilidad con la infraestructura paralela por módulo.
            cur.execute("DELETE FROM permisos_delegables_modulos WHERE modulo_id = %s", (modulo_id,))
            cur.execute("DELETE FROM usuario_modulos WHERE modulo_id = %s", (modulo_id,))
            cur.execute("DELETE FROM modulos_roles_acceso WHERE modulo_id = %s", (modulo_id,))
            cur.execute("DELETE FROM modulos WHERE id = %s", (modulo_id,))
            conn.commit()
            return {"mensaje": "Módulo eliminado permanentemente."}
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()
