"""Politica de informacion monetaria exclusiva para usuarios hijo (rol 3)."""

from db_conexion import obtener_conexion


class PoliticaMontosService:
    """Centraliza la decision de redaccion de montos antes de serializar datos."""

    @staticmethod
    def debe_ocultar_montos(usuario_id, ambito_identificador):
        """Devuelve True solo si un rol 3 debe recibir el ambito redactado.

        Roles 1 y 2 nunca usan configuracion monetaria y siempre ven sus datos.
        Para rol 3, una configuracion ausente o un ambito desconocido se trata
        como oculto para evitar una exposicion accidental durante la migracion.
        """
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT rol_id FROM usuarios WHERE id = %s AND activo = 1", (usuario_id,))
            usuario = cur.fetchone()
            if not usuario:
                return True
            if usuario['rol_id'] in (1, 2):
                return False
            if usuario['rol_id'] != 3:
                return True

            cur.execute(
                "SELECT ocultar_montos_global FROM configuracion_montos_usuario WHERE usuario_id = %s",
                (usuario_id,),
            )
            configuracion = cur.fetchone()
            if not configuracion or bool(configuracion['ocultar_montos_global']):
                return True

            cur.execute("SELECT id FROM ambitos_montos WHERE identificador = %s AND activo = 1", (ambito_identificador,))
            ambito = cur.fetchone()
            if not ambito:
                return True

            cur.execute(
                """
                SELECT ocultar_montos
                FROM configuracion_montos_usuario_ambito
                WHERE usuario_id = %s AND ambito_id = %s
                """,
                (usuario_id, ambito['id']),
            )
            configuracion_ambito = cur.fetchone()
            return bool(configuracion_ambito and configuracion_ambito['ocultar_montos'])
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def _validar_hijo_del_padre(cur, padre_id, hijo_id):
        cur.execute(
            """
            SELECT 1
            FROM jerarquia_usuarios ju
            INNER JOIN usuarios padre ON padre.id = ju.padre_id AND padre.rol_id = 2 AND padre.activo = 1
            INNER JOIN usuarios hijo ON hijo.id = ju.hijo_id AND hijo.rol_id = 3
            WHERE ju.padre_id = %s
              AND ju.hijo_id = %s
              AND hijo.cliente_id <=> padre.cliente_id
            """,
            (padre_id, hijo_id),
        )
        if not cur.fetchone():
            raise PermissionError('Acceso denegado: el usuario hijo no pertenece a su ambito de administracion.')

    @staticmethod
    def obtener_configuracion_hijo(padre_id, hijo_id):
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            PoliticaMontosService._validar_hijo_del_padre(cur, padre_id, hijo_id)
            cur.execute(
                "SELECT ocultar_montos_global FROM configuracion_montos_usuario WHERE usuario_id = %s",
                (hijo_id,),
            )
            global_config = cur.fetchone()
            # Ausencia se representa de forma segura para que la UI nunca sugiera
            # que un hijo sin configuracion puede ver montos.
            ocultar_global = True if not global_config else bool(global_config['ocultar_montos_global'])
            cur.execute(
                """
                SELECT a.id, a.identificador, a.nombre,
                       COALESCE(c.ocultar_montos, 0) AS ocultar_montos
                FROM ambitos_montos a
                LEFT JOIN configuracion_montos_usuario_ambito c
                    ON c.ambito_id = a.id AND c.usuario_id = %s
                WHERE a.activo = 1
                ORDER BY a.id
                """,
                (hijo_id,),
            )
            return {
                'ocultar_montos_global': ocultar_global,
                'ambitos': cur.fetchall(),
            }
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def obtener_politica_propia(usuario_id):
        """Politica efectiva para que Angular oculte controles sin ser autoridad."""
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT rol_id FROM usuarios WHERE id = %s AND activo = 1", (usuario_id,))
            usuario = cur.fetchone()
            if not usuario:
                raise PermissionError('Usuario no encontrado o inactivo.')
            if usuario['rol_id'] in (1, 2):
                return {'ocultar_montos_global': False, 'ambitos': []}
            if usuario['rol_id'] != 3:
                raise PermissionError('Rol no autorizado.')
            cur.execute(
                "SELECT ocultar_montos_global FROM configuracion_montos_usuario WHERE usuario_id = %s",
                (usuario_id,),
            )
            configuracion = cur.fetchone()
            cur.execute(
                """
                SELECT a.identificador, COALESCE(c.ocultar_montos, 0) AS ocultar_montos
                FROM ambitos_montos a
                LEFT JOIN configuracion_montos_usuario_ambito c
                    ON c.ambito_id = a.id AND c.usuario_id = %s
                WHERE a.activo = 1
                """,
                (usuario_id,),
            )
            return {
                'ocultar_montos_global': True if not configuracion else bool(configuracion['ocultar_montos_global']),
                'ambitos': cur.fetchall(),
            }
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def actualizar_global_hijo(padre_id, hijo_id, ocultar_montos_global):
        if not isinstance(ocultar_montos_global, bool):
            raise ValueError('ocultar_montos_global debe ser booleano.')
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            PoliticaMontosService._validar_hijo_del_padre(cur, padre_id, hijo_id)
            cur.execute(
                """
                INSERT INTO configuracion_montos_usuario (usuario_id, ocultar_montos_global)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE ocultar_montos_global = VALUES(ocultar_montos_global)
                """,
                (hijo_id, int(ocultar_montos_global)),
            )
            conn.commit()
            return {'mensaje': 'Configuracion global de montos actualizada.'}
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def actualizar_ambito_hijo(padre_id, hijo_id, ambito_identificador, ocultar_montos):
        if not isinstance(ocultar_montos, bool):
            raise ValueError('ocultar_montos debe ser booleano.')
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            PoliticaMontosService._validar_hijo_del_padre(cur, padre_id, hijo_id)
            cur.execute(
                "SELECT id FROM ambitos_montos WHERE identificador = %s AND activo = 1",
                (ambito_identificador,),
            )
            ambito = cur.fetchone()
            if not ambito:
                raise ValueError('El ambito monetario no existe o esta inactivo.')
            cur.execute(
                """
                INSERT INTO configuracion_montos_usuario_ambito (usuario_id, ambito_id, ocultar_montos)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE ocultar_montos = VALUES(ocultar_montos)
                """,
                (hijo_id, ambito['id'], int(ocultar_montos)),
            )
            conn.commit()
            return {'mensaje': 'Configuracion monetaria del ambito actualizada.'}
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
