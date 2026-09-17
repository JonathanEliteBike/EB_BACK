"""Infraestructura paralela para accesos por módulo y capacidades globales.

Las tablas de acciones continúan siendo compatibles durante la migración. Este
servicio no las modifica ni las utiliza para decidir los accesos nuevos.
"""

from db_conexion import obtener_conexion
from services.usuarios_hijos_service import UsuariosHijosService


class PermisosModulosService:
    CAPACIDAD_MOSTRAR_MONTOS = "mostrar_montos"

    @staticmethod
    def _validar_administrador(cur, administrador_id):
        cur.execute(
            "SELECT 1 FROM usuarios WHERE id = %s AND rol_id = 2",
            (administrador_id,),
        )
        if not cur.fetchone():
            raise ValueError("El usuario seleccionado no corresponde al rol 2.")

    @staticmethod
    def _validar_modulo(cur, modulo_id):
        cur.execute(
            "SELECT 1 FROM modulos WHERE id = %s AND activo = 1",
            (modulo_id,),
        )
        if not cur.fetchone():
            raise ValueError("El módulo seleccionado no existe o está inactivo.")

    @staticmethod
    def _validar_modulo_delegable(cur, modulo_id):
        cur.execute(
            """
            SELECT padre_id, nombre FROM modulos
            WHERE id = %s
              AND activo = 1
              AND delegable_a_hijos = 1
              AND identificador NOT IN ('creacion_usuarios_dis', 'usuarios_hijos')
            """,
            (modulo_id,),
        )
        modulo = cur.fetchone()
        if not modulo:
            raise PermissionError("El módulo seleccionado no puede delegarse a usuarios hijo.")
        return modulo

    @staticmethod
    def _validar_capacidad(capacidad):
        if capacidad != PermisosModulosService.CAPACIDAD_MOSTRAR_MONTOS:
            raise ValueError("La capacidad solicitada no está disponible.")

    @staticmethod
    def _listar_modulos(cur, tabla, usuario_id):
        columna_usuario = "administrador_id" if tabla == "permisos_delegables_modulos" else "usuario_id"
        cur.execute(
            f"""
            SELECT m.id AS modulo_id, m.nombre AS modulo, m.identificador,
                   m.padre_id, p.identificador AS padre_identificador
            FROM {tabla} pm
            INNER JOIN modulos m ON m.id = pm.modulo_id AND m.activo = 1
            LEFT JOIN modulos p ON p.id = m.padre_id
            LEFT JOIN {tabla} permiso_padre
                ON permiso_padre.{columna_usuario} = pm.{columna_usuario}
                AND permiso_padre.modulo_id = m.padre_id
            WHERE pm.{columna_usuario} = %s
              AND m.delegable_a_hijos = 1
              AND m.identificador NOT IN ('creacion_usuarios_dis', 'usuarios_hijos')
              AND (m.padre_id IS NULL OR permiso_padre.modulo_id IS NOT NULL)
            ORDER BY m.nombre
            """,
            (usuario_id,),
        )
        return cur.fetchall()

    @staticmethod
    def obtener_modulos_delegables(administrador_id):
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            PermisosModulosService._validar_administrador(cur, administrador_id)
            return PermisosModulosService._listar_modulos(
                cur, "permisos_delegables_modulos", administrador_id
            )
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def obtener_modulos_hijo(padre_id, hijo_id):
        if not UsuariosHijosService.validar_pertenencia_hijo(padre_id, hijo_id):
            raise PermissionError("Acceso denegado: Este usuario no pertenece a su ámbito de administración.")

        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """
                SELECT m.id AS modulo_id, m.nombre AS modulo, m.identificador,
                       m.padre_id, p.identificador AS padre_identificador
                FROM usuario_modulos um
                INNER JOIN permisos_delegables_modulos pdm
                    ON pdm.modulo_id = um.modulo_id AND pdm.administrador_id = %s
                INNER JOIN modulos m ON m.id = um.modulo_id AND m.activo = 1
                LEFT JOIN modulos p ON p.id = m.padre_id
                LEFT JOIN permisos_delegables_modulos pdm_padre
                    ON pdm_padre.administrador_id = %s AND pdm_padre.modulo_id = m.padre_id
                LEFT JOIN usuario_modulos um_padre
                    ON um_padre.usuario_id = um.usuario_id AND um_padre.modulo_id = m.padre_id
                WHERE um.usuario_id = %s
                  AND m.delegable_a_hijos = 1
                  AND m.identificador NOT IN ('creacion_usuarios_dis', 'usuarios_hijos')
                  AND (m.padre_id IS NULL OR (pdm_padre.modulo_id IS NOT NULL AND um_padre.modulo_id IS NOT NULL))
                ORDER BY m.nombre
                """,
                (padre_id, padre_id, hijo_id),
            )
            return cur.fetchall()
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def obtener_modulos_propios_hijo(hijo_id):
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT padre_id FROM jerarquia_usuarios WHERE hijo_id = %s", (hijo_id,))
            jerarquia = cur.fetchone()
            if not jerarquia:
                raise PermissionError("Acceso denegado: El usuario no tiene una relación padre-hijo válida.")
        finally:
            cur.close()
            conn.close()
        return PermisosModulosService.obtener_modulos_hijo(jerarquia["padre_id"], hijo_id)

    @staticmethod
    def obtener_modulos_propios_rol2(usuario_id):
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            PermisosModulosService._validar_administrador(cur, usuario_id)
            cur.execute(
                """
                SELECT m.id AS modulo_id, m.nombre AS modulo, m.identificador,
                       m.padre_id, p.identificador AS padre_identificador
                FROM modulos_roles_acceso mra
                INNER JOIN modulos m ON m.id = mra.modulo_id AND m.activo = 1
                LEFT JOIN modulos p ON p.id = m.padre_id
                WHERE mra.rol_id = 2 AND mra.activo = 1
                ORDER BY m.nombre
                """
            )
            return cur.fetchall()
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def asignar_modulo_administrador(administrador_id, modulo_id):
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            PermisosModulosService._validar_administrador(cur, administrador_id)
            PermisosModulosService._validar_modulo_delegable(cur, modulo_id)
            cur.execute(
                "INSERT IGNORE INTO permisos_delegables_modulos (administrador_id, modulo_id) VALUES (%s, %s)",
                (administrador_id, modulo_id),
            )
            conn.commit()
            return {"mensaje": "Acceso al módulo otorgado al distribuidor."}
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def revocar_modulo_administrador(administrador_id, modulo_id):
        """Retira el acceso del padre; las filas de hijos quedan inefectivas."""
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            PermisosModulosService._validar_administrador(cur, administrador_id)
            cur.execute(
                "DELETE FROM permisos_delegables_modulos WHERE administrador_id = %s AND modulo_id = %s",
                (administrador_id, modulo_id),
            )
            conn.commit()
            return {"mensaje": "Acceso al módulo retirado. Los accesos de hijos quedan inefectivos."}
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def asignar_modulo_hijo(padre_id, hijo_id, modulo_id):
        if not UsuariosHijosService.validar_pertenencia_hijo(padre_id, hijo_id):
            raise PermissionError("Acceso denegado: Este usuario no pertenece a su ámbito de administración.")
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            modulo = PermisosModulosService._validar_modulo_delegable(cur, modulo_id)
            cur.execute(
                "SELECT 1 FROM permisos_delegables_modulos WHERE administrador_id = %s AND modulo_id = %s",
                (padre_id, modulo_id),
            )
            if not cur.fetchone():
                raise PermissionError("No puede delegar un módulo que no está en su bolsa.")

            padre_modulo_id = modulo[0]
            if padre_modulo_id:
                cur.execute(
                    """
                    SELECT nombre
                    FROM modulos
                    WHERE id = %s AND activo = 1 AND delegable_a_hijos = 1
                    """,
                    (padre_modulo_id,),
                )
                padre = cur.fetchone()
                if not padre:
                    raise ValueError("El módulo padre no existe, está inactivo o no puede delegarse.")

                cur.execute(
                    """
                    SELECT 1
                    FROM permisos_delegables_modulos pdm
                    INNER JOIN usuario_modulos um
                        ON um.modulo_id = pdm.modulo_id AND um.usuario_id = %s
                    WHERE pdm.administrador_id = %s AND pdm.modulo_id = %s
                    """,
                    (hijo_id, padre_id, padre_modulo_id),
                )
                if not cur.fetchone():
                    raise ValueError(
                        f"El módulo padre '{padre[0]}' debe estar habilitado antes de asignar este submódulo."
                    )
            cur.execute(
                "INSERT IGNORE INTO usuario_modulos (usuario_id, modulo_id) VALUES (%s, %s)",
                (hijo_id, modulo_id),
            )
            conn.commit()
            return {"mensaje": "Acceso al módulo asignado al usuario hijo."}
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def revocar_modulo_hijo(padre_id, hijo_id, modulo_id):
        if not UsuariosHijosService.validar_pertenencia_hijo(padre_id, hijo_id):
            raise PermissionError("Acceso denegado: Este usuario no pertenece a su ámbito de administración.")
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            PermisosModulosService._validar_modulo(cur, modulo_id)
            cur.execute("SELECT id, padre_id FROM modulos")
            relaciones = cur.fetchall()
            descendientes = {modulo_id}
            pendientes = [modulo_id]
            while pendientes:
                padre_actual = pendientes.pop()
                hijos = [fila[0] for fila in relaciones if fila[1] == padre_actual]
                for hijo in hijos:
                    if hijo not in descendientes:
                        descendientes.add(hijo)
                        pendientes.append(hijo)

            placeholders = ", ".join(["%s"] * len(descendientes))
            cur.execute(
                f"DELETE FROM usuario_modulos WHERE usuario_id = %s AND modulo_id IN ({placeholders})",
                (hijo_id, *descendientes),
            )
            conn.commit()
            return {"mensaje": "Acceso al módulo y sus submódulos retirado del usuario hijo."}
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def obtener_capacidades_delegables(administrador_id):
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            PermisosModulosService._validar_administrador(cur, administrador_id)
            cur.execute(
                "SELECT capacidad FROM permisos_delegables_capacidades WHERE administrador_id = %s ORDER BY capacidad",
                (administrador_id,),
            )
            return cur.fetchall()
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def obtener_capacidades_hijo(padre_id, hijo_id):
        if not UsuariosHijosService.validar_pertenencia_hijo(padre_id, hijo_id):
            raise PermissionError("Acceso denegado: Este usuario no pertenece a su ámbito de administración.")
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """
                SELECT uc.capacidad
                FROM usuario_capacidades uc
                INNER JOIN permisos_delegables_capacidades pdc
                    ON pdc.capacidad = uc.capacidad AND pdc.administrador_id = %s
                WHERE uc.usuario_id = %s
                ORDER BY uc.capacidad
                """,
                (padre_id, hijo_id),
            )
            return cur.fetchall()
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def obtener_capacidades_propias_hijo(hijo_id):
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT padre_id FROM jerarquia_usuarios WHERE hijo_id = %s", (hijo_id,))
            jerarquia = cur.fetchone()
            if not jerarquia:
                raise PermissionError("Acceso denegado: El usuario no tiene una relación padre-hijo válida.")
        finally:
            cur.close()
            conn.close()
        return PermisosModulosService.obtener_capacidades_hijo(jerarquia["padre_id"], hijo_id)

    @staticmethod
    def asignar_capacidad_administrador(administrador_id, capacidad):
        PermisosModulosService._validar_capacidad(capacidad)
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            PermisosModulosService._validar_administrador(cur, administrador_id)
            cur.execute(
                "INSERT IGNORE INTO permisos_delegables_capacidades (administrador_id, capacidad) VALUES (%s, %s)",
                (administrador_id, capacidad),
            )
            conn.commit()
            return {"mensaje": "Capacidad otorgada al distribuidor."}
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def revocar_capacidad_administrador(administrador_id, capacidad):
        PermisosModulosService._validar_capacidad(capacidad)
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            PermisosModulosService._validar_administrador(cur, administrador_id)
            cur.execute(
                "DELETE FROM permisos_delegables_capacidades WHERE administrador_id = %s AND capacidad = %s",
                (administrador_id, capacidad),
            )
            conn.commit()
            return {"mensaje": "Capacidad retirada. Las capacidades de hijos quedan inefectivas."}
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def asignar_capacidad_hijo(padre_id, hijo_id, capacidad):
        PermisosModulosService._validar_capacidad(capacidad)
        if not UsuariosHijosService.validar_pertenencia_hijo(padre_id, hijo_id):
            raise PermissionError("Acceso denegado: Este usuario no pertenece a su ámbito de administración.")
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT 1 FROM permisos_delegables_capacidades WHERE administrador_id = %s AND capacidad = %s",
                (padre_id, capacidad),
            )
            if not cur.fetchone():
                raise PermissionError("No puede delegar una capacidad que no está en su bolsa.")
            cur.execute(
                "INSERT IGNORE INTO usuario_capacidades (usuario_id, capacidad) VALUES (%s, %s)",
                (hijo_id, capacidad),
            )
            conn.commit()
            return {"mensaje": "Capacidad asignada al usuario hijo."}
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def revocar_capacidad_hijo(padre_id, hijo_id, capacidad):
        PermisosModulosService._validar_capacidad(capacidad)
        if not UsuariosHijosService.validar_pertenencia_hijo(padre_id, hijo_id):
            raise PermissionError("Acceso denegado: Este usuario no pertenece a su ámbito de administración.")
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            cur.execute(
                "DELETE FROM usuario_capacidades WHERE usuario_id = %s AND capacidad = %s",
                (hijo_id, capacidad),
            )
            conn.commit()
            return {"mensaje": "Capacidad retirada del usuario hijo."}
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
