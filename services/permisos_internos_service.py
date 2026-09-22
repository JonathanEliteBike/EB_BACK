"""Permisos de usuarios internos (rol 4), aislados del flujo rol 2 -> rol 3."""

from db_conexion import obtener_conexion


class PermisosInternosService:
    ROL_USUARIO_INTERNO = 4

    @staticmethod
    def listar_usuarios_internos():
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """
                SELECT u.id, u.nombre, u.correo, u.usuario, u.activo,
                       u.area_id, a.nombre AS area_nombre
                FROM usuarios u
                LEFT JOIN areas a ON a.id = u.area_id
                WHERE u.rol_id = %s
                ORDER BY u.nombre, u.id
                """,
                (PermisosInternosService.ROL_USUARIO_INTERNO,),
            )
            return cur.fetchall()
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def listar_areas():
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """
                SELECT id, nombre, activo, creado_en, actualizado_en
                FROM areas
                ORDER BY nombre, id
                """
            )
            return cur.fetchall()
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def obtener_area_usuario(usuario_id):
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """
                SELECT a.id, a.nombre, a.activo
                FROM usuarios u
                LEFT JOIN areas a ON a.id = u.area_id
                WHERE u.id = %s AND u.rol_id = %s
                """,
                (usuario_id, PermisosInternosService.ROL_USUARIO_INTERNO),
            )
            fila = cur.fetchone()
            if fila is None:
                raise ValueError("El usuario seleccionado no corresponde a un usuario interno.")
            return fila
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def obtener_permisos_usuario(usuario_id):
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            PermisosInternosService._validar_usuario_interno(cur, usuario_id)
            cur.execute(
                """
                SELECT upi.usuario_id, upi.modulo_id, m.nombre AS modulo,
                       m.identificador AS modulo_identificador, m.padre_id,
                       upi.accion_id, a.nombre AS accion,
                       a.identificador AS accion_identificador, upi.creado_en
                FROM usuario_permisos_internos upi
                INNER JOIN modulos m ON m.id = upi.modulo_id
                INNER JOIN acciones a ON a.id = upi.accion_id
                WHERE upi.usuario_id = %s
                ORDER BY m.nombre, a.nombre
                """,
                (usuario_id,),
            )
            return cur.fetchall()
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def asignar_permiso(usuario_id, modulo_id, accion_id):
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            PermisosInternosService._validar_usuario_interno(cur, usuario_id, requiere_activo=True)
            PermisosInternosService._validar_modulo_accion(cur, modulo_id, accion_id)
            cur.execute(
                """
                INSERT IGNORE INTO usuario_permisos_internos (usuario_id, modulo_id, accion_id)
                VALUES (%s, %s, %s)
                """,
                (usuario_id, modulo_id, accion_id),
            )
            conn.commit()
            return {"mensaje": "Permiso interno asignado correctamente."}
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def revocar_permiso(usuario_id, modulo_id, accion_id):
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            PermisosInternosService._validar_usuario_interno(cur, usuario_id)
            cur.execute(
                """
                DELETE FROM usuario_permisos_internos
                WHERE usuario_id = %s AND modulo_id = %s AND accion_id = %s
                """,
                (usuario_id, modulo_id, accion_id),
            )
            conn.commit()
            return {"mensaje": "Permiso interno revocado correctamente."}
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def validar_permiso_usuario(usuario_id, modulo_identificador, accion_identificador):
        """Valida el permiso efectivo, incluyendo estado y relación módulo-acción."""
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT rol_id
                FROM usuarios
                WHERE id = %s AND activo = 1
                """,
                (usuario_id,),
            )
            usuario = cur.fetchone()
            if not usuario:
                return False

            rol_id = usuario[0]
            if rol_id == 1:
                return True
            if rol_id != PermisosInternosService.ROL_USUARIO_INTERNO:
                return False

            cur.execute(
                """
                SELECT 1
                FROM usuario_permisos_internos upi
                INNER JOIN modulos m
                    ON m.id = upi.modulo_id AND m.activo = 1
                INNER JOIN acciones a
                    ON a.id = upi.accion_id AND a.activo = 1
                INNER JOIN modulo_acciones ma
                    ON ma.modulo_id = upi.modulo_id
                   AND ma.accion_id = upi.accion_id
                WHERE upi.usuario_id = %s
                  AND m.identificador = %s
                  AND a.identificador = %s
                """,
                (usuario_id, modulo_identificador, accion_identificador),
            )
            return cur.fetchone() is not None
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def _validar_usuario_interno(cur, usuario_id, requiere_activo=False):
        sql = "SELECT id FROM usuarios WHERE id = %s AND rol_id = %s"
        parametros = [usuario_id, PermisosInternosService.ROL_USUARIO_INTERNO]
        if requiere_activo:
            sql += " AND activo = 1"
        cur.execute(sql, tuple(parametros))
        if not cur.fetchone():
            estado = " activo" if requiere_activo else ""
            raise ValueError(f"El usuario seleccionado no corresponde a un usuario interno{estado}.")

    @staticmethod
    def _validar_modulo_accion(cur, modulo_id, accion_id):
        cur.execute(
            """
            SELECT 1
            FROM modulo_acciones ma
            INNER JOIN modulos m ON m.id = ma.modulo_id AND m.activo = 1
            INNER JOIN acciones a ON a.id = ma.accion_id AND a.activo = 1
            WHERE ma.modulo_id = %s AND ma.accion_id = %s
            """,
            (modulo_id, accion_id),
        )
        if not cur.fetchone():
            raise ValueError(
                "La combinación de módulo y acción no existe o alguno de sus catálogos está inactivo."
            )
