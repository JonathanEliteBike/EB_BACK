# services/modulos_service.py

from db_conexion import obtener_conexion


class ModuloEliminacionBloqueada(ValueError):
    """Describe una dependencia que debe resolverse antes de eliminar un módulo."""

    def __init__(self, mensaje, codigo, **detalles):
        super().__init__(mensaje)
        self.codigo = codigo
        self.detalles = detalles


class ModuloAreaDuplicada(ValueError):
    """Indica que la relación módulo-área ya existe."""


class ModulosService:

    @staticmethod
    def listar_modulos():
        """Lista todos los módulos y submódulos junto con sus acciones vinculadas."""
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("""
                SELECT m.id, m.padre_id, padre.identificador AS padre_identificador,
                       m.nombre, m.identificador, m.ruta, m.activo,
                       m.delegable_a_hijos, m.area_id, a.nombre AS area_nombre,
                       m.creado_en
                FROM modulos m
                LEFT JOIN areas a ON a.id = m.area_id
                LEFT JOIN modulos padre ON padre.id = m.padre_id
                ORDER BY m.id ASC
            """)
            modulos = cur.fetchall()

            # Adjuntar las acciones asociadas a cada módulo
            for modulo in modulos:
                cur.execute("""
                    SELECT a.id, a.nombre
                    FROM modulo_areas ma
                    INNER JOIN areas a ON a.id = ma.area_id
                    WHERE ma.modulo_id = %s
                    ORDER BY a.nombre, a.id
                """, (modulo['id'],))
                modulo['areas'] = cur.fetchall()
                for area in modulo['areas']:
                    cur.execute("""SELECT a.id, a.nombre, a.identificador FROM modulo_area_acciones maa
                        INNER JOIN acciones a ON a.id=maa.accion_id
                        WHERE maa.modulo_id=%s AND maa.area_id=%s AND a.activo=1 ORDER BY a.nombre""", (modulo['id'], area['id']))
                    area['acciones'] = cur.fetchall()

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
            areas_ids = ModulosService._obtener_areas_ids(datos)
            areas_ids = ModulosService._validar_areas_activas(cur, areas_ids)
            area_id = areas_ids[0] if areas_ids else None

            # Una ruta o identificador ya registrados representan el mismo
            # módulo técnico. En vez de duplicarlo, se configura únicamente
            # su relación con el área solicitada.
            cur.execute(
                "SELECT id FROM modulos WHERE identificador = %s",
                (datos['identificador'],),
            )
            modulo_por_identificador = cur.fetchone()
            modulo_por_ruta = None
            if datos.get('ruta'):
                cur.execute(
                    "SELECT id FROM modulos WHERE ruta = %s",
                    (datos['ruta'],),
                )
                modulo_por_ruta = cur.fetchone()

            if (modulo_por_identificador and modulo_por_ruta
                    and modulo_por_identificador['id'] != modulo_por_ruta['id']):
                raise ValueError(
                    "La ruta y el identificador pertenecen a módulos técnicos distintos."
                )

            modulo_existente = modulo_por_identificador or modulo_por_ruta
            if modulo_existente:
                if len(areas_ids) != 1:
                    raise ValueError(
                        "Para reutilizar un módulo existente selecciona una sola área."
                    )
                resultado = ModulosService.configurar_area_modulo(
                    modulo_existente['id'],
                    {
                        'area_id': area_id,
                        'acciones_ids': datos.get('acciones_ids', []),
                        'crear_asociacion': True,
                    },
                )
                resultado['reutilizado'] = True
                return resultado

            ModulosService._validar_datos_globales_modulo(cur, None, datos)

            sql = """
            INSERT INTO modulos
                (padre_id, area_id, nombre, identificador, ruta, activo, delegable_a_hijos)
            VALUES (%s, %s, %s, %s, %s, 1, %s)
            """
            cur.execute(sql, (
                datos.get('padre_id'),
                area_id,
                datos['nombre'],
                datos['identificador'],
                datos.get('ruta'),
                1 if datos.get('delegable_a_hijos', False) else 0,
            ))
            modulo_id = cur.lastrowid

            for area_id in areas_ids:
                cur.execute("""
                    INSERT IGNORE INTO modulo_areas (modulo_id, area_id)
                    VALUES (%s, %s)
                """, (modulo_id, area_id))

            for area_id, acciones_area in ModulosService._obtener_acciones_por_area(datos, areas_ids).items():
                for accion_id in ModulosService._validar_acciones_activas(cur, acciones_area):
                    cur.execute("INSERT IGNORE INTO modulo_area_acciones (modulo_id, area_id, accion_id) VALUES (%s,%s,%s)", (modulo_id, area_id, accion_id))

            if datos.get('catalogo_historico_distribuidores'):
                cur.execute("""
                    INSERT IGNORE INTO modulos_roles_acceso (modulo_id, rol_id, activo)
                    VALUES (%s, 2, 1)
                """, (modulo_id,))

            acciones = ModulosService._validar_acciones_activas(cur, datos.get('acciones_ids', []))
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
        if datos.get('configurar_area'):
            return ModulosService.configurar_area_modulo(modulo_id, datos)

        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            campos = ["nombre = %s", "identificador = %s", "padre_id = %s"]
            valores = [
                datos['nombre'],
                datos['identificador'],
                datos.get('padre_id'),
            ]

            if 'ruta' in datos:
                campos.append("ruta = %s")
                valores.append(datos.get('ruta'))

            if 'delegable_a_hijos' in datos:
                campos.append("delegable_a_hijos = %s")
                valores.append(1 if datos['delegable_a_hijos'] else 0)

            if 'areas_ids' in datos or 'area_id' in datos:
                areas_ids = ModulosService._validar_areas_activas(
                    cur, ModulosService._obtener_areas_ids(datos)
                )
                area_id = areas_ids[0] if areas_ids else None
                campos.append("area_id = %s")
                valores.append(area_id)

            valores.append(modulo_id)
            cur.execute(
                f"UPDATE modulos SET {', '.join(campos)} WHERE id = %s",
                tuple(valores),
            )

            if 'areas_ids' in datos or 'area_id' in datos:
                cur.execute("DELETE FROM modulo_areas WHERE modulo_id = %s", (modulo_id,))
                for area_id in areas_ids:
                    cur.execute("""
                        INSERT IGNORE INTO modulo_areas (modulo_id, area_id)
                        VALUES (%s, %s)
                    """, (modulo_id, area_id))
                cur.execute("DELETE FROM modulo_area_acciones WHERE modulo_id = %s", (modulo_id,))
                for area_id, acciones_area in ModulosService._obtener_acciones_por_area(datos, areas_ids).items():
                    for accion_id in ModulosService._validar_acciones_activas(cur, acciones_area):
                        cur.execute("INSERT IGNORE INTO modulo_area_acciones (modulo_id, area_id, accion_id) VALUES (%s,%s,%s)", (modulo_id, area_id, accion_id))

            # Actualizar acciones si vienen en la petición
            if 'acciones_ids' in datos:
                acciones = ModulosService._validar_acciones_activas(cur, datos['acciones_ids'])
                # El formulario solo permite administrar acciones activas. Las
                # asociaciones históricas con acciones inactivas se preservan.
                cur.execute("""
                    DELETE ma
                    FROM modulo_acciones ma
                    INNER JOIN acciones a ON a.id = ma.accion_id
                    WHERE ma.modulo_id = %s AND a.activo = 1
                """, (modulo_id,))
                for accion_id in acciones:
                    cur.execute("""
                        INSERT IGNORE INTO modulo_acciones (modulo_id, accion_id)
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
    def eliminar_modulo_de_area(modulo_id, area_id, eliminar_permisos_asignados=False):
        """Desasocia un módulo de un área sin modificar su configuración global."""
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            conn.start_transaction()
            cur.execute(
                """SELECT ar.nombre AS area_nombre
                   FROM modulo_areas ma
                   INNER JOIN areas ar ON ar.id = ma.area_id
                   WHERE ma.modulo_id = %s AND ma.area_id = %s FOR UPDATE""",
                (modulo_id, area_id),
            )
            relacion = cur.fetchone()
            if not relacion:
                raise ValueError("El módulo no está asociado al área seleccionada.")

            nombre_area = relacion['area_nombre']
            if eliminar_permisos_asignados and nombre_area.strip().casefold() != 'contabilidad':
                raise ValueError(
                    "La limpieza confirmada de permisos está habilitada únicamente para Contabilidad."
                )

            cur.execute(
                """SELECT COUNT(*) AS total
                   FROM modulos hijo
                   INNER JOIN modulo_areas ma ON ma.modulo_id = hijo.id
                   WHERE hijo.padre_id = %s AND ma.area_id = %s""",
                (modulo_id, area_id),
            )
            submodulos = cur.fetchone()['total']
            if submodulos:
                raise ModuloEliminacionBloqueada(
                    f"Este módulo tiene submódulos asociados en {nombre_area}. Elimínalos primero.",
                    "submodulos_area_asociados",
                    submodulos=submodulos,
                )

            cur.execute(
                """SELECT COUNT(*) AS permisos, COUNT(DISTINCT usuario_id) AS usuarios
                   FROM usuario_permisos_internos
                   WHERE modulo_id = %s AND area_id = %s""",
                (modulo_id, area_id),
            )
            permisos = cur.fetchone()
            if permisos['permisos'] and not eliminar_permisos_asignados:
                raise ModuloEliminacionBloqueada(
                    f"No se puede eliminar este módulo de {nombre_area} porque tiene "
                    f"{permisos['permisos']} permisos asignados a "
                    f"{permisos['usuarios']} usuario(s). Retíralos primero.",
                    "permisos_internos_area_asignados",
                    usuarios=permisos['usuarios'],
                    permisos=permisos['permisos'],
                )

            if permisos['permisos']:
                cur.execute(
                    "DELETE FROM usuario_permisos_internos WHERE modulo_id = %s AND area_id = %s",
                    (modulo_id, area_id),
                )

            cur.execute(
                "DELETE FROM modulo_area_acciones WHERE modulo_id = %s AND area_id = %s",
                (modulo_id, area_id),
            )
            cur.execute(
                "DELETE FROM modulo_areas WHERE modulo_id = %s AND area_id = %s",
                (modulo_id, area_id),
            )
            conn.commit()
            return {
                "mensaje": "Módulo quitado del área correctamente.",
                "permisos_eliminados": permisos['permisos'],
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def eliminar_modulo(modulo_id):
        """Elimina un módulo sin dejar dependencias ni borrar permisos internos."""
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            conn.start_transaction()
            cur.execute("SELECT id FROM modulos WHERE id = %s FOR UPDATE", (modulo_id,))
            if not cur.fetchone():
                raise ValueError("El módulo no existe.")

            cur.execute("SELECT COUNT(*) AS total FROM modulos WHERE padre_id = %s", (modulo_id,))
            submodulos = cur.fetchone()['total']
            if submodulos:
                raise ModuloEliminacionBloqueada(
                    "No se puede eliminar porque tiene submódulos asociados. Elimínalos primero.",
                    "submodulos_asociados",
                    submodulos=submodulos,
                )

            cur.execute("""
                SELECT COUNT(*) AS permisos, COUNT(DISTINCT usuario_id) AS usuarios
                FROM usuario_permisos_internos
                WHERE modulo_id = %s
            """, (modulo_id,))
            permisos_internos = cur.fetchone()
            if permisos_internos['permisos']:
                raise ModuloEliminacionBloqueada(
                    "No se puede eliminar porque tiene "
                    f"{permisos_internos['permisos']} permisos asignados a "
                    f"{permisos_internos['usuarios']} usuarios internos.",
                    "permisos_internos_asignados",
                    usuarios=permisos_internos['usuarios'],
                    permisos=permisos_internos['permisos'],
                )

            cur.execute("DELETE FROM permisos_internos_endpoints WHERE modulo_id = %s", (modulo_id,))
            cur.execute("DELETE FROM modulo_area_acciones WHERE modulo_id = %s", (modulo_id,))
            cur.execute("DELETE FROM modulo_areas WHERE modulo_id = %s", (modulo_id,))
            cur.execute("DELETE FROM modulo_acciones WHERE modulo_id = %s", (modulo_id,))
            cur.execute("DELETE FROM permisos_delegables WHERE modulo_id = %s", (modulo_id,))
            cur.execute("DELETE FROM usuario_permisos WHERE modulo_id = %s", (modulo_id,))
            # Compatibilidad con la infraestructura paralela por módulo.
            cur.execute("DELETE FROM permisos_delegables_modulos WHERE modulo_id = %s", (modulo_id,))
            cur.execute("DELETE FROM usuario_modulos WHERE modulo_id = %s", (modulo_id,))
            cur.execute("DELETE FROM modulos_roles_acceso WHERE modulo_id = %s", (modulo_id,))
            cur.execute("DELETE FROM modulos WHERE id = %s", (modulo_id,))
            if cur.rowcount == 0:
                raise ValueError("El módulo no existe.")
            conn.commit()
            return {"mensaje": "Módulo eliminado permanentemente."}
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def configurar_area_modulo(modulo_id, datos):
        """Configura una única relación módulo-área sin tocar las demás áreas."""
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            area_id = datos.get('area_id')
            if isinstance(area_id, bool) or not isinstance(area_id, int):
                raise ValueError("area_id es obligatorio y debe ser numérico.")

            ModulosService._validar_areas_activas(cur, [area_id])
            acciones = ModulosService._validar_acciones_activas(
                cur, datos.get('acciones_ids', [])
            )

            cur.execute("SELECT id FROM modulos WHERE id = %s", (modulo_id,))
            if not cur.fetchone():
                raise ValueError("El módulo seleccionado no existe.")

            if datos.get('actualizar_modulo_global'):
                ModulosService._validar_datos_globales_modulo(cur, modulo_id, datos)
                cur.execute(
                    """UPDATE modulos
                       SET nombre = %s, identificador = %s, ruta = %s, padre_id = %s
                       WHERE id = %s""",
                    (
                        datos['nombre'].strip(),
                        datos['identificador'].strip(),
                        datos.get('ruta'),
                        datos.get('padre_id'),
                        modulo_id,
                    ),
                )

            cur.execute(
                "SELECT 1 FROM modulo_areas WHERE modulo_id = %s AND area_id = %s",
                (modulo_id, area_id),
            )
            asociacion_existe = cur.fetchone() is not None
            creando_asociacion = bool(datos.get('crear_asociacion'))

            if creando_asociacion and asociacion_existe:
                raise ModuloAreaDuplicada("El módulo ya está agregado al área seleccionada.")
            if not creando_asociacion and not asociacion_existe:
                raise ValueError("El módulo no está asociado al área seleccionada.")

            if not asociacion_existe:
                cur.execute(
                    "INSERT INTO modulo_areas (modulo_id, area_id) VALUES (%s, %s)",
                    (modulo_id, area_id),
                )

            cur.execute(
                "DELETE FROM modulo_area_acciones WHERE modulo_id = %s AND area_id = %s",
                (modulo_id, area_id),
            )
            for accion_id in acciones:
                cur.execute(
                    """INSERT INTO modulo_area_acciones (modulo_id, area_id, accion_id)
                       VALUES (%s, %s, %s)""",
                    (modulo_id, area_id, accion_id),
                )

            # Compatibilidad: el flujo distribuidor → usuario hijo continúa
            # consultando modulo_acciones. Sólo se agregan acciones; no se
            # retiran asociaciones históricas de otras áreas.
            for accion_id in acciones:
                cur.execute(
                    "INSERT IGNORE INTO modulo_acciones (modulo_id, accion_id) VALUES (%s, %s)",
                    (modulo_id, accion_id),
                )

            conn.commit()
            mensaje = "Módulo asociado al área correctamente." if creando_asociacion else "Acciones del área actualizadas correctamente."
            return {"mensaje": mensaje, "id": modulo_id}
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def _obtener_areas_ids(datos):
        if 'areas_ids' in datos:
            areas_ids = datos['areas_ids']
            if not isinstance(areas_ids, list):
                raise ValueError("areas_ids debe ser una lista.")
            return list(dict.fromkeys(areas_ids))

        area_id = datos.get('area_id')
        return [] if area_id is None else [area_id]

    @staticmethod
    def _validar_areas_activas(cur, areas_ids):
        if any(isinstance(area_id, bool) or not isinstance(area_id, int) for area_id in areas_ids):
            raise ValueError("areas_ids debe contener IDs numéricos válidos.")
        if not areas_ids:
            return []

        marcadores = ', '.join(['%s'] * len(areas_ids))
        cur.execute(
            f"SELECT id FROM areas WHERE activo = 1 AND id IN ({marcadores})",
            tuple(areas_ids),
        )
        encontrados = {
            fila['id'] if isinstance(fila, dict) else fila[0]
            for fila in cur.fetchall()
        }
        if len(encontrados) != len(areas_ids):
            raise ValueError("Una o más áreas seleccionadas no existen o están inactivas.")
        return areas_ids

    @staticmethod
    def _obtener_acciones_por_area(datos, areas_ids):
        valores = datos.get('areas_acciones')
        if valores is None:
            return {area_id: datos.get('acciones_ids', []) for area_id in areas_ids}
        if not isinstance(valores, dict):
            raise ValueError("areas_acciones debe ser un objeto.")
        resultado = {}
        for area_id in areas_ids:
            acciones = valores.get(str(area_id), valores.get(area_id, []))
            if not isinstance(acciones, list):
                raise ValueError("Las acciones de cada área deben ser una lista.")
            resultado[area_id] = acciones
        return resultado

    @staticmethod
    def _validar_acciones_activas(cur, acciones_ids):
        if not isinstance(acciones_ids, list):
            raise ValueError("acciones_ids debe ser una lista.")

        ids = list(dict.fromkeys(acciones_ids))
        if not ids:
            return []

        if any(isinstance(accion_id, bool) or not isinstance(accion_id, int) for accion_id in ids):
            raise ValueError("acciones_ids debe contener IDs numéricos válidos.")

        marcadores = ', '.join(['%s'] * len(ids))
        cur.execute(
            f"SELECT id FROM acciones WHERE activo = 1 AND id IN ({marcadores})",
            tuple(ids),
        )
        encontrados = {
            fila['id'] if isinstance(fila, dict) else fila[0]
            for fila in cur.fetchall()
        }
        if len(encontrados) != len(ids):
            raise ValueError("Una o más acciones seleccionadas no existen o están inactivas.")
        return ids

    @staticmethod
    def _validar_datos_globales_modulo(cur, modulo_id, datos):
        nombre = datos.get('nombre')
        identificador = datos.get('identificador')
        if not isinstance(nombre, str) or not nombre.strip() or not isinstance(identificador, str) or not identificador.strip():
            raise ValueError("Nombre e identificador son obligatorios.")

        cur.execute(
            "SELECT id FROM modulos WHERE identificador = %s AND id != %s",
            (identificador.strip(), modulo_id if modulo_id is not None else 0),
        )
        if cur.fetchone():
            raise ValueError(f"El identificador '{identificador}' ya existe registrado.")

        ruta = datos.get('ruta')
        if ruta:
            cur.execute(
                "SELECT id FROM modulos WHERE ruta = %s AND id != %s",
                (ruta, modulo_id if modulo_id is not None else 0),
            )
            if cur.fetchone():
                raise ValueError("La ruta seleccionada ya pertenece a otro módulo técnico.")

        padre_id = datos.get('padre_id')
        if padre_id is None:
            return
        if isinstance(padre_id, bool) or not isinstance(padre_id, int):
            raise ValueError("padre_id debe ser numérico o null.")
        if padre_id == modulo_id:
            raise ValueError("Un módulo no puede ser su propio padre.")

        cur.execute("SELECT padre_id FROM modulos WHERE id = %s", (padre_id,))
        padre = cur.fetchone()
        if not padre:
            raise ValueError("El módulo padre seleccionado no existe.")

        visitados = set()
        actual_id = padre_id
        while actual_id is not None:
            if actual_id == modulo_id:
                raise ValueError("El módulo padre seleccionado crearía un ciclo.")
            if actual_id in visitados:
                raise ValueError("La jerarquía actual de módulos contiene un ciclo.")
            visitados.add(actual_id)
            cur.execute("SELECT padre_id FROM modulos WHERE id = %s", (actual_id,))
            actual = cur.fetchone()
            actual_id = actual['padre_id'] if isinstance(actual, dict) else actual[0]
