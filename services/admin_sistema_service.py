# services/admin_sistema_service.py

from db_conexion import obtener_conexion

class AdminSistemaService:

    @staticmethod
    def validar_administrador_cliente(cur, admin_id):
        """Confirma que el destinatario de una bolsa sea un Administrador Cliente."""
        cur.execute(
            "SELECT 1 FROM usuarios WHERE id = %s AND rol_id = 2",
            (admin_id,)
        )
        return cur.fetchone() is not None

    @staticmethod
    def _obtener_columna_limite(cur):
        """Detecta automáticamente el nombre de la columna en limites_usuario."""
        cur.execute("SHOW COLUMNS FROM limites_usuario")
        columnas = [col['Field'] for col in cur.fetchall()]
        for col in ['administrador_id', 'usuario_id', 'padre_id', 'admin_id', 'id']:
            if col in columnas:
                return col
        return 'id'

    @staticmethod
    def listar_administradores():
        """Lista todos los Administradores Cliente junto con su estado y cupo asignado."""
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            col_fk = AdminSistemaService._obtener_columna_limite(cur)
            sql = f"""
                SELECT u.id, u.nombre, u.correo, u.usuario, u.activo, u.cliente_id,
                       COALESCE(l.max_hijos, 0) as max_hijos,
                       (SELECT COUNT(*)
                        FROM jerarquia_usuarios ju
                        INNER JOIN usuarios h ON h.id = ju.hijo_id
                        WHERE ju.padre_id = u.id AND h.activo = 1) as hijos_activos
                FROM usuarios u
                LEFT JOIN limites_usuario l ON u.id = l.{col_fk}
                WHERE u.rol_id = 2
                ORDER BY u.id DESC
            """
            cur.execute(sql)
            return cur.fetchall()
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def cambiar_estado_usuario(usuario_id, activo):
        """Activa (1) o desactiva (0) cualquier usuario o administrador del sistema."""
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            cur.execute("UPDATE usuarios SET activo = %s WHERE id = %s", (activo, usuario_id))
            conn.commit()
            estado_texto = "activado" if activo == 1 else "desactivado"
            return {"mensaje": f"Usuario ID {usuario_id} {estado_texto} correctamente."}
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def actualizar_limite_cupo(admin_id, max_hijos):
        """Asigna o actualiza el límite máximo de usuarios hijos para un Administrador Cliente."""
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            if not AdminSistemaService.validar_administrador_cliente(cur, admin_id):
                raise ValueError("El usuario seleccionado no corresponde a un distribuidor activo para este flujo.")

            col_fk = AdminSistemaService._obtener_columna_limite(cur)
            sql = f"""
                INSERT INTO limites_usuario ({col_fk}, max_hijos)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE max_hijos = VALUES(max_hijos)
            """
            cur.execute(sql, (admin_id, max_hijos))
            conn.commit()
            return {"mensaje": f"Cupo máximo de {max_hijos} usuarios actualizado para el Administrador ID {admin_id}."}
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def asignar_permiso_delegable(admin_id, modulo_id, accion_id):
        """Otorga un permiso a la bolsa delegable de un Administrador Cliente."""
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            if not AdminSistemaService.validar_administrador_cliente(cur, admin_id):
                raise ValueError("El administrador seleccionado no existe o no corresponde al rol 2.")

            cur.execute("""
                SELECT 1
                FROM modulo_acciones ma
                INNER JOIN modulos m ON m.id = ma.modulo_id AND m.activo = 1
                INNER JOIN acciones a ON a.id = ma.accion_id AND a.activo = 1
                WHERE ma.modulo_id = %s AND ma.accion_id = %s
            """, (modulo_id, accion_id))
            if not cur.fetchone():
                raise ValueError("La acción no está disponible para el módulo seleccionado.")

            sql = """
                INSERT IGNORE INTO permisos_delegables (administrador_id, modulo_id, accion_id)
                VALUES (%s, %s, %s)
            """
            cur.execute(sql, (admin_id, modulo_id, accion_id))
            conn.commit()
            return {"mensaje": "Permiso delegable otorgado al administrador correctamente."}
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def revocar_permiso_delegable(admin_id, modulo_id, accion_id):
        """Retira un permiso de la bolsa delegable de un Administrador Cliente 
           y lo elimina en cascada de todos sus usuarios hijos."""
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            if not AdminSistemaService.validar_administrador_cliente(cur, admin_id):
                raise ValueError("El administrador seleccionado no existe o no corresponde al rol 2.")

            # 1. Borrar físicamente el permiso de TODOS los usuarios hijos (Rol 3) que pertenezcan a este administrador
            sql_hijos = """
                DELETE up FROM usuario_permisos up
                INNER JOIN jerarquia_usuarios ju ON up.usuario_id = ju.hijo_id
                WHERE ju.padre_id = %s AND up.modulo_id = %s AND up.accion_id = %s
            """
            cur.execute(sql_hijos, (admin_id, modulo_id, accion_id))

            # 2. Borrar el permiso de la bolsa delegable del Administrador (Rol 2)
            sql_admin = """
                DELETE FROM permisos_delegables
                WHERE administrador_id = %s AND modulo_id = %s AND accion_id = %s
            """
            cur.execute(sql_admin, (admin_id, modulo_id, accion_id))

            conn.commit()
            return {"mensaje": "Permiso delegable retirado al administrador y a sus usuarios dependientes."}
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def obtener_permisos_delegables_administrador(admin_id):
        """Obtiene la bolsa vigente de un Administrador Cliente para su gestión por rol 1."""
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            if not AdminSistemaService.validar_administrador_cliente(cur, admin_id):
                raise ValueError("El administrador seleccionado no existe o no corresponde al rol 2.")
        finally:
            cur.close()
            conn.close()

        # Reutiliza la consulta que alimenta la bolsa real del distribuidor.
        from services.permisos_service import PermisosService
        return PermisosService.obtener_permisos_delegables(admin_id)
