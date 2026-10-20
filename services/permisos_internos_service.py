"""Permisos de usuarios internos (rol 4), aislados del flujo rol 2 -> rol 3."""

import re

from db_conexion import obtener_conexion


class PermisosInternosService:
    ROL_USUARIO_INTERNO = 4
    METODOS_HTTP_VALIDOS = {"GET", "POST", "PUT", "PATCH", "DELETE"}

    @staticmethod
    def asignar_permisos_base_area(cur, usuario_id, area_id, reemplazar=False):
        """Siembra la matriz módulo + acción configurada para un área.

        El cursor pertenece a la transacción del alta o edición del usuario,
        para que el área y sus permisos nunca queden en estados distintos.
        """
        if reemplazar:
            cur.execute("DELETE FROM usuario_permisos_internos WHERE usuario_id = %s", (usuario_id,))

        cur.execute(
            """
            INSERT IGNORE INTO usuario_permisos_internos
                (usuario_id, modulo_id, area_id, accion_id)
            SELECT %s, ma.modulo_id, ma.area_id, maa.accion_id
            FROM modulo_areas ma
            INNER JOIN modulo_area_acciones maa
                ON maa.modulo_id = ma.modulo_id AND maa.area_id = ma.area_id
            INNER JOIN modulos m ON m.id = ma.modulo_id AND m.activo = 1
            INNER JOIN acciones a ON a.id = maa.accion_id AND a.activo = 1
            WHERE ma.area_id = %s
            """,
            (usuario_id, area_id),
        )
        return cur.rowcount

    @staticmethod
    def listar_reglas_endpoint(modulo_id=None):
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            consulta = """
                SELECT pie.id, pie.ruta_patron, pie.metodo_http, pie.modulo_id,
                       pie.accion_id, pie.activo, pie.creado_en, pie.actualizado_en,
                       m.nombre AS modulo_nombre, m.identificador AS modulo_identificador,
                       a.nombre AS accion_nombre, a.identificador AS accion_identificador
                FROM permisos_internos_endpoints pie
                INNER JOIN modulos m ON m.id = pie.modulo_id
                INNER JOIN acciones a ON a.id = pie.accion_id
            """
            parametros = []
            if modulo_id is not None:
                consulta += " WHERE pie.modulo_id = %s"
                parametros.append(modulo_id)
            consulta += " ORDER BY pie.ruta_patron, pie.metodo_http, pie.id"
            cur.execute(consulta, tuple(parametros))
            return cur.fetchall()
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def crear_regla_endpoint(datos):
        ruta_patron, metodo_http, modulo_id, accion_id, activo = PermisosInternosService._normalizar_regla_endpoint(datos)
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            PermisosInternosService._validar_modulo_accion(cur, modulo_id, accion_id)
            cur.execute(
                """SELECT 1 FROM permisos_internos_endpoints
                   WHERE ruta_patron = %s AND metodo_http = %s
                     AND modulo_id = %s AND accion_id = %s""",
                (ruta_patron, metodo_http, modulo_id, accion_id),
            )
            if cur.fetchone():
                raise ValueError("Ya existe esa regla exacta para la ruta, método, módulo y acción.")
            cur.execute(
                """INSERT INTO permisos_internos_endpoints
                   (ruta_patron, metodo_http, modulo_id, accion_id, activo)
                   VALUES (%s, %s, %s, %s, %s)""",
                (ruta_patron, metodo_http, modulo_id, accion_id, activo),
            )
            conn.commit()
            return {"id": cur.lastrowid, "mensaje": "Regla de backend creada correctamente."}
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def actualizar_regla_endpoint(regla_id, datos):
        ruta_patron, metodo_http, modulo_id, accion_id, activo = PermisosInternosService._normalizar_regla_endpoint(datos)
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            cur.execute("SELECT id FROM permisos_internos_endpoints WHERE id = %s", (regla_id,))
            if not cur.fetchone():
                raise ValueError("La regla de backend no existe.")
            PermisosInternosService._validar_modulo_accion(cur, modulo_id, accion_id)
            cur.execute(
                """SELECT 1 FROM permisos_internos_endpoints
                   WHERE ruta_patron = %s AND metodo_http = %s
                     AND modulo_id = %s AND accion_id = %s AND id != %s""",
                (ruta_patron, metodo_http, modulo_id, accion_id, regla_id),
            )
            if cur.fetchone():
                raise ValueError("Ya existe esa regla exacta para la ruta, método, módulo y acción.")
            cur.execute(
                """UPDATE permisos_internos_endpoints
                   SET ruta_patron = %s, metodo_http = %s, modulo_id = %s,
                       accion_id = %s, activo = %s
                   WHERE id = %s""",
                (ruta_patron, metodo_http, modulo_id, accion_id, activo, regla_id),
            )
            conn.commit()
            return {"mensaje": "Regla de backend actualizada correctamente."}
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def eliminar_regla_endpoint(regla_id):
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            cur.execute("DELETE FROM permisos_internos_endpoints WHERE id = %s", (regla_id,))
            if cur.rowcount == 0:
                raise ValueError("La regla de backend no existe.")
            conn.commit()
            return {"mensaje": "Regla de backend eliminada correctamente."}
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def validar_permiso_endpoint(usuario_id, ruta, metodo_http, ruta_interna=None):
        """Valida las reglas activas de un endpoint para un usuario interno.

        Las reglas del mismo patrón representan alternativas válidas: basta
        con que el usuario posea una. Cuando coinciden varios patrones, se
        evalúa el más específico para no confundir una ruta exacta con una
        plantilla parametrizada más amplia.
        """
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT pie.ruta_patron, m.identificador AS modulo_identificador, m.ruta AS modulo_ruta,
                          a.identificador AS accion_identificador
                   FROM permisos_internos_endpoints pie
                   INNER JOIN modulos m ON m.id = pie.modulo_id AND m.activo = 1
                   INNER JOIN acciones a ON a.id = pie.accion_id AND a.activo = 1
                   WHERE pie.activo = 1 AND pie.metodo_http = %s
                     AND (
                         EXISTS (
                             SELECT 1 FROM modulo_acciones ma
                             WHERE ma.modulo_id = m.id AND ma.accion_id = a.id
                         )
                         OR EXISTS (
                             SELECT 1 FROM modulo_area_acciones maa
                             WHERE maa.modulo_id = m.id AND maa.accion_id = a.id
                         )
                     )""",
                (metodo_http.upper(),),
            )
            reglas = [
                regla for regla in cur.fetchall()
                if PermisosInternosService._coincide_patron_endpoint(regla['ruta_patron'], ruta)
            ]
            if not reglas:
                return None

            # Las reglas del mismo patrón son alternativas. Si un patrón
            # exacto y uno parametrizado coinciden, se evalúa el más
            # específico en vez de fallar por ambigüedad.
            def prioridad(regla):
                patron = regla['ruta_patron'].rstrip('/') or '/'
                segmentos = [segmento for segmento in patron.split('/') if segmento]
                parametros = sum(1 for segmento in segmentos if segmento.startswith('<'))
                return (len(segmentos) - parametros, -parametros, len(patron))

            mejor_prioridad = max(prioridad(regla) for regla in reglas)
            reglas = [regla for regla in reglas if prioridad(regla) == mejor_prioridad]

            from flask import g, has_request_context
            from services.contexto_permisos_internos import coincide_ruta
            if ruta_interna:
                reglas = [r for r in reglas if coincide_ruta(r, ruta_interna)]
            elif len({r['modulo_identificador'] for r in reglas}) > 1:
                # Los endpoints compartidos requieren el contexto de pantalla;
                # sin él no se puede escoger arbitrariamente otro módulo.
                return False
            autorizados = set()
            for regla in reglas:
                modulo = regla['modulo_identificador']
                accion = regla['accion_identificador']
                # Ver montos complementa lectura; jamás autoriza una operación.
                if accion == 'ver_montos':
                    continue
                if (PermisosInternosService.validar_permiso_usuario(usuario_id, modulo, 'ver')
                        and (accion == 'ver' or PermisosInternosService.validar_permiso_usuario(usuario_id, modulo, accion))):
                    autorizados.add(modulo)
            if has_request_context():
                g._modulos_internos_autorizados = autorizados
            return bool(autorizados)
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def diagnosticar_permiso_endpoint(usuario_id, ruta, metodo_http, ruta_interna=None):
        """Devuelve datos seguros para diagnosticar un 403 de rol 4.

        Sólo se invoca cuando el registro local está habilitado. No incluye
        JWT, cabeceras de autenticación ni información personal del usuario.
        """
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT pie.ruta_patron, m.identificador AS modulo_identificador,
                          m.ruta AS modulo_ruta, a.identificador AS accion_identificador
                   FROM permisos_internos_endpoints pie
                   INNER JOIN modulos m ON m.id = pie.modulo_id AND m.activo = 1
                   INNER JOIN acciones a ON a.id = pie.accion_id AND a.activo = 1
                   WHERE pie.activo = 1 AND pie.metodo_http = %s""",
                (metodo_http.upper(),),
            )
            from services.contexto_permisos_internos import coincide_ruta
            coincidentes = [
                regla for regla in cur.fetchall()
                if PermisosInternosService._coincide_patron_endpoint(regla['ruta_patron'], ruta)
            ]
            reglas = []
            for regla in coincidentes:
                contexto_compatible = bool(ruta_interna and coincide_ruta(regla, ruta_interna))
                tiene_ver = PermisosInternosService.validar_permiso_usuario(
                    usuario_id, regla['modulo_identificador'], 'ver'
                )
                tiene_accion = (
                    regla['accion_identificador'] == 'ver'
                    or PermisosInternosService.validar_permiso_usuario(
                        usuario_id, regla['modulo_identificador'], regla['accion_identificador']
                    )
                )
                reglas.append({
                    'ruta_patron': regla['ruta_patron'],
                    'modulo': regla['modulo_identificador'],
                    'accion': regla['accion_identificador'],
                    'contexto_compatible': contexto_compatible,
                    'tiene_ver': tiene_ver,
                    'tiene_accion': tiene_accion,
                })
            return {
                'ruta': ruta,
                'metodo': metodo_http.upper(),
                'ruta_interna': ruta_interna or None,
                'reglas_coincidentes': reglas,
            }
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def _normalizar_regla_endpoint(datos):
        ruta_patron = str(datos.get('ruta_patron') or '').strip()
        metodo_http = str(datos.get('metodo_http') or '').strip().upper()
        if not ruta_patron.startswith('/'):
            raise ValueError("ruta_patron debe iniciar con '/'.")
        if not ruta_patron or '//' in ruta_patron:
            raise ValueError("ruta_patron no es válida.")
        if metodo_http not in PermisosInternosService.METODOS_HTTP_VALIDOS:
            raise ValueError("metodo_http no es válido.")
        try:
            modulo_id = int(datos.get('modulo_id'))
            accion_id = int(datos.get('accion_id'))
        except (TypeError, ValueError):
            raise ValueError("modulo_id y accion_id deben ser numéricos.")
        return ruta_patron.rstrip('/') or '/', metodo_http, modulo_id, accion_id, 1 if datos.get('activo', True) else 0

    @staticmethod
    def _coincide_patron_endpoint(patron, ruta):
        patron_normalizado = (patron or '/').rstrip('/') or '/'
        ruta_normalizada = (ruta or '/').rstrip('/') or '/'

        partes = re.split(
            r'(<(?:[^:<>]+:)?[^<>]+>)',
            patron_normalizado
        )

        def convertir_parte(parte):
            parametro = re.fullmatch(
                r'<(?:(?P<tipo>[^:<>]+):)?(?P<nombre>[^<>]+)>',
                parte
            )

            if not parametro:
                return re.escape(parte)

            tipo = parametro.group('tipo')

            if tipo == 'int':
                return r'\d+'

            if tipo == 'path':
                return r'.+'

            return r'[^/]+'

        expresion = ''.join(convertir_parte(parte) for parte in partes)

        return re.fullmatch(expresion, ruta_normalizada) is not None

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
    def crear_area(nombre):
        nombre_normalizado = PermisosInternosService._normalizar_nombre_area(nombre)
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            PermisosInternosService._validar_nombre_area_disponible(cur, nombre_normalizado)
            cur.execute("INSERT INTO areas (nombre, activo) VALUES (%s, 1)", (nombre_normalizado,))
            conn.commit()
            return {"mensaje": "Área creada correctamente.", "id": cur.lastrowid}
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def actualizar_area(area_id, nombre):
        nombre_normalizado = PermisosInternosService._normalizar_nombre_area(nombre)
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT id FROM areas WHERE id = %s", (area_id,))
            if not cur.fetchone():
                raise ValueError("El área no existe.")
            PermisosInternosService._validar_nombre_area_disponible(cur, nombre_normalizado, area_id)
            cur.execute("UPDATE areas SET nombre = %s WHERE id = %s", (nombre_normalizado, area_id))
            conn.commit()
            return {"mensaje": "Área actualizada correctamente."}
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def cambiar_estado_area(area_id, activo):
        if isinstance(activo, bool):
            activo = int(activo)
        if activo not in (0, 1):
            raise ValueError("activo debe ser 0 o 1.")

        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("UPDATE areas SET activo = %s WHERE id = %s", (activo, area_id))
            if cur.rowcount == 0:
                raise ValueError("El área no existe.")
            conn.commit()
            return {"mensaje": f"Área {'activada' if activo else 'desactivada'} correctamente."}
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def _normalizar_nombre_area(nombre):
        if not isinstance(nombre, str) or not nombre.strip():
            raise ValueError("El nombre del área es obligatorio.")
        nombre_normalizado = nombre.strip()
        if len(nombre_normalizado) > 100:
            raise ValueError("El nombre del área no puede exceder 100 caracteres.")
        return nombre_normalizado

    @staticmethod
    def _validar_nombre_area_disponible(cur, nombre, area_id=None):
        consulta = "SELECT id FROM areas WHERE nombre = %s"
        parametros = [nombre]
        if area_id is not None:
            consulta += " AND id <> %s"
            parametros.append(area_id)
        cur.execute(consulta, tuple(parametros))
        if cur.fetchone():
            raise ValueError("Ya existe un área con ese nombre.")

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
                       upi.area_id, ar.nombre AS area_nombre, upi.accion_id, a.nombre AS accion,
                       a.identificador AS accion_identificador, upi.creado_en
                FROM usuario_permisos_internos upi
                INNER JOIN modulos m ON m.id = upi.modulo_id
                INNER JOIN acciones a ON a.id = upi.accion_id
                LEFT JOIN areas ar ON ar.id = upi.area_id
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
    def asignar_permiso(usuario_id, modulo_id, area_id, accion_id):
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            PermisosInternosService._validar_usuario_interno(cur, usuario_id, requiere_activo=True)
            PermisosInternosService._validar_modulo_area_accion(cur, modulo_id, area_id, accion_id)
            cur.execute(
                """
                INSERT IGNORE INTO usuario_permisos_internos (usuario_id, modulo_id, area_id, accion_id)
                VALUES (%s, %s, %s, %s)
                """,
                (usuario_id, modulo_id, area_id, accion_id),
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
    def revocar_permiso(usuario_id, modulo_id, area_id, accion_id):
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            PermisosInternosService._validar_usuario_interno(cur, usuario_id)
            cur.execute(
                """
                DELETE FROM usuario_permisos_internos
                WHERE usuario_id = %s AND modulo_id = %s AND area_id = %s AND accion_id = %s
                """,
                (usuario_id, modulo_id, area_id, accion_id),
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
                WHERE upi.usuario_id = %s
                  AND m.identificador = %s
                  AND a.identificador = %s
                  AND ((upi.area_id IS NULL AND EXISTS (SELECT 1 FROM modulo_acciones ma WHERE ma.modulo_id=upi.modulo_id AND ma.accion_id=upi.accion_id))
                    OR (upi.area_id IS NOT NULL AND EXISTS (SELECT 1 FROM modulo_area_acciones maa WHERE maa.modulo_id=upi.modulo_id AND maa.area_id=upi.area_id AND maa.accion_id=upi.accion_id)))
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
            FROM modulos m
            INNER JOIN acciones a ON a.id = %s AND a.activo = 1
            WHERE m.id = %s AND m.activo = 1
              AND (
                  EXISTS (
                      SELECT 1 FROM modulo_acciones ma
                      WHERE ma.modulo_id = m.id AND ma.accion_id = a.id
                  )
                  OR EXISTS (
                      SELECT 1 FROM modulo_area_acciones maa
                      WHERE maa.modulo_id = m.id AND maa.accion_id = a.id
                  )
              )
            """,
            (accion_id, modulo_id),
        )
        if not cur.fetchone():
            raise ValueError(
                "La combinación de módulo y acción no existe o alguno de sus catálogos está inactivo."
            )

    @staticmethod
    def _validar_modulo_area_accion(cur, modulo_id, area_id, accion_id):
        cur.execute("""SELECT 1 FROM modulo_area_acciones maa
            INNER JOIN modulos m ON m.id=maa.modulo_id AND m.activo=1
            INNER JOIN areas ar ON ar.id=maa.area_id AND ar.activo=1
            INNER JOIN acciones a ON a.id=maa.accion_id AND a.activo=1
            WHERE maa.modulo_id=%s AND maa.area_id=%s AND maa.accion_id=%s""", (modulo_id, area_id, accion_id))
        if not cur.fetchone():
            raise ValueError("La acción no está disponible para el módulo y área seleccionados.")
