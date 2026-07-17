from datetime import datetime

from flask import Blueprint, jsonify, request

from db_conexion import obtener_conexion


colaboradores_bp = Blueprint(
    "colaboradores",
    __name__,
    url_prefix="/api/inventario/colaboradores"
)


# ============================================================
# FUNCIONES AUXILIARES
# ============================================================

def limpiar_texto(valor):
    """
    Elimina espacios al inicio y al final.

    Si el valor queda vacío, devuelve None para almacenarlo
    como NULL en MySQL.
    """
    if valor is None:
        return None

    if isinstance(valor, str):
        valor = valor.strip()
        return valor if valor else None

    return valor


def normalizar_fecha(valor):
    """
    Valida fechas con formato YYYY-MM-DD.

    Devuelve None cuando la fecha viene vacía.
    Lanza ValueError cuando el formato no es correcto.
    """
    valor = limpiar_texto(valor)

    if not valor:
        return None

    if not isinstance(valor, str):
        raise ValueError(
            "La fecha de ingreso debe tener formato YYYY-MM-DD"
        )

    try:
        return datetime.strptime(
            valor,
            "%Y-%m-%d"
        ).date()

    except ValueError as error:
        raise ValueError(
            "La fecha de ingreso debe tener formato YYYY-MM-DD"
        ) from error


def formatear_colaborador(row):
    """
    Convierte los nombres de las columnas de MySQL
    al formato camelCase utilizado por Angular.
    """
    nombre_completo = " ".join(
        parte
        for parte in [
            row.get("nombre"),
            row.get("apellido_paterno"),
            row.get("apellido_materno")
        ]
        if parte
    )

    return {
        "id": row["id"],
        "numeroEmpleado": row["numero_empleado"] or "",
        "nombre": row["nombre"] or "",
        "apellidoPaterno": row["apellido_paterno"] or "",
        "apellidoMaterno": row["apellido_materno"] or "",
        "nombreCompleto": nombre_completo,
        "empresa": row["empresa"] or "ELITE BIKE",
        "departamento": row["departamento"] or "",
        "puesto": row["puesto"] or "",
        "correo": row["correo"] or "",
        "telefono": row["telefono"] or "",
        "extension": row["extension"] or "",
        "ubicacion": row["ubicacion"] or "",
        "fechaIngreso": (
            str(row["fecha_ingreso"])
            if row.get("fecha_ingreso")
            else ""
        ),
        "estado": row["estado"] or "Activo",
        "comentarios": row["comentarios"] or "",
        "equiposAsignados": int(
            row.get("equipos_asignados") or 0
        ),
        "fechaCreacion": (
            str(row["fecha_creacion"])
            if row.get("fecha_creacion")
            else ""
        ),
        "fechaActualizacion": (
            str(row["fecha_actualizacion"])
            if row.get("fecha_actualizacion")
            else ""
        )
    }


def formatear_equipo_asignado(row):
    """
    Convierte un equipo asignado al formato utilizado
    por la vista de detalle del colaborador.
    """
    return {
        "asignacionId": row["asignacion_id"],
        "equipoId": row["equipo_id"],
        "inventario": row["numero_inventario"] or "",
        "nombre": row["descripcion"] or "",
        "categoria": row["categoria"] or "",
        "marca": row["marca"] or "",
        "modelo": row["modelo"] or "",
        "serie": row["numero_serie"] or "",
        "estado": row["estado_equipo"] or "",
        "funcionamiento": row["funcionamiento"] or "",
        "ubicacion": row["ubicacion"] or "",
        "fechaAsignacion": (
            str(row["fecha_asignacion"])
            if row.get("fecha_asignacion")
            else ""
        )
    }


def colaborador_existe(cursor, colaborador_id):
    cursor.execute("""
        SELECT id
        FROM inventario_colaboradores
        WHERE id = %s
    """, (colaborador_id,))

    return cursor.fetchone() is not None


def contar_asignaciones_activas(cursor, colaborador_id):
    cursor.execute("""
        SELECT COUNT(*) AS total
        FROM inventario_asignaciones
        WHERE colaborador_id = %s
          AND estado = 'Activa'
    """, (colaborador_id,))

    resultado = cursor.fetchone()

    if isinstance(resultado, dict):
        return int(resultado.get("total") or 0)

    return int(resultado[0] or 0)


def validar_campos_obligatorios(data):
    """
    Devuelve un mensaje de error si falta un campo obligatorio.
    Si todos están presentes, devuelve None.
    """
    campos = {
        "numeroEmpleado": "Número de empleado",
        "nombre": "Nombre",
        "apellidoPaterno": "Apellido paterno",
        "departamento": "Departamento",
        "puesto": "Puesto"
    }

    faltantes = []

    for campo, etiqueta in campos.items():
        valor = limpiar_texto(data.get(campo))

        if not valor:
            faltantes.append(etiqueta)

    if faltantes:
        return (
            "Completa los campos obligatorios: "
            + ", ".join(faltantes)
            + "."
        )

    return None


def respuesta_error_base_datos(error, accion):
    """
    Convierte algunos errores comunes de MySQL
    en respuestas entendibles para el frontend.
    """
    numero_error = getattr(error, "errno", None)
    mensaje_error = str(error)

    if numero_error == 1062:
        if "numero_empleado" in mensaje_error:
            return jsonify({
                "error": (
                    "Ya existe un colaborador con ese "
                    "número de empleado."
                )
            }), 409

        if "correo" in mensaje_error:
            return jsonify({
                "error": (
                    "Ya existe un colaborador registrado "
                    "con ese correo."
                )
            }), 409

        return jsonify({
            "error": "Ya existe un registro con esos datos."
        }), 409

    return jsonify({
        "error": f"No se pudo {accion} el colaborador.",
        "detalle": mensaje_error
    }), 500


# ============================================================
# GET: LISTAR COLABORADORES
# ============================================================

@colaboradores_bp.route("", methods=["GET"])
@colaboradores_bp.route("/", methods=["GET"])
def listar_colaboradores():
    """
    Lista todos los colaboradores.

    Filtros opcionales:
    - busqueda
    - estado
    - departamento
    - empresa
    - asignacion: Con equipo | Sin equipo
    """

    busqueda = limpiar_texto(
        request.args.get("busqueda")
    )

    estado = limpiar_texto(
        request.args.get("estado")
    )

    departamento = limpiar_texto(
        request.args.get("departamento")
    )

    empresa = limpiar_texto(
        request.args.get("empresa")
    )

    asignacion = limpiar_texto(
        request.args.get("asignacion")
    )

    condiciones = []
    parametros = []

    if busqueda:
        termino = f"%{busqueda}%"

        condiciones.append("""
            (
                c.numero_empleado LIKE %s
                OR c.nombre LIKE %s
                OR c.apellido_paterno LIKE %s
                OR c.apellido_materno LIKE %s
                OR CONCAT_WS(
                    ' ',
                    c.nombre,
                    c.apellido_paterno,
                    c.apellido_materno
                ) LIKE %s
                OR c.departamento LIKE %s
                OR c.puesto LIKE %s
                OR c.correo LIKE %s
                OR c.telefono LIKE %s
                OR c.extension LIKE %s
                OR c.ubicacion LIKE %s
            )
        """)

        parametros.extend([
            termino,
            termino,
            termino,
            termino,
            termino,
            termino,
            termino,
            termino,
            termino,
            termino,
            termino
        ])

    if estado and estado != "Todos":
        condiciones.append(
            "c.estado = %s"
        )
        parametros.append(estado)

    if departamento and departamento != "Todos":
        condiciones.append(
            "c.departamento = %s"
        )
        parametros.append(departamento)

    if empresa and empresa != "Todas":
        condiciones.append(
            "c.empresa = %s"
        )
        parametros.append(empresa)

    if asignacion == "Con equipo":
        condiciones.append("""
            EXISTS (
                SELECT 1
                FROM inventario_asignaciones asignacion_activa
                WHERE asignacion_activa.colaborador_id = c.id
                  AND asignacion_activa.estado = 'Activa'
            )
        """)

    elif asignacion == "Sin equipo":
        condiciones.append("""
            NOT EXISTS (
                SELECT 1
                FROM inventario_asignaciones asignacion_activa
                WHERE asignacion_activa.colaborador_id = c.id
                  AND asignacion_activa.estado = 'Activa'
            )
        """)

    where_sql = ""

    if condiciones:
        where_sql = (
            "WHERE "
            + " AND ".join(condiciones)
        )

    consulta = f"""
        SELECT
            c.id,
            c.numero_empleado,
            c.nombre,
            c.apellido_paterno,
            c.apellido_materno,
            c.empresa,
            c.departamento,
            c.puesto,
            c.correo,
            c.telefono,
            c.extension,
            c.ubicacion,
            c.fecha_ingreso,
            c.estado,
            c.comentarios,
            c.fecha_creacion,
            c.fecha_actualizacion,

            (
                SELECT COUNT(*)
                FROM inventario_asignaciones a
                WHERE a.colaborador_id = c.id
                  AND a.estado = 'Activa'
            ) AS equipos_asignados

        FROM inventario_colaboradores c

        {where_sql}

        ORDER BY
            c.estado ASC,
            c.apellido_paterno ASC,
            c.nombre ASC
    """

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute(
            consulta,
            tuple(parametros)
        )

        registros = cursor.fetchall()

        colaboradores = [
            formatear_colaborador(row)
            for row in registros
        ]

        return jsonify(colaboradores)

    except Exception as error:
        return jsonify({
            "error": (
                "No se pudieron obtener los colaboradores."
            ),
            "detalle": str(error)
        }), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# GET: ESTADÍSTICAS
# ============================================================

@colaboradores_bp.route("/estadisticas", methods=["GET"])
def obtener_estadisticas():
    """
    Devuelve los contadores principales del módulo.
    """

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT
                COUNT(*) AS total,

                SUM(
                    CASE
                        WHEN estado = 'Activo'
                        THEN 1
                        ELSE 0
                    END
                ) AS activos,

                SUM(
                    CASE
                        WHEN estado = 'Inactivo'
                        THEN 1
                        ELSE 0
                    END
                ) AS inactivos

            FROM inventario_colaboradores
        """)

        generales = cursor.fetchone()

        cursor.execute("""
            SELECT
                COUNT(DISTINCT colaborador_id) AS con_equipos
            FROM inventario_asignaciones
            WHERE estado = 'Activa'
        """)

        asignados = cursor.fetchone()

        total = int(
            generales.get("total") or 0
        )

        activos = int(
            generales.get("activos") or 0
        )

        inactivos = int(
            generales.get("inactivos") or 0
        )

        con_equipos = int(
            asignados.get("con_equipos") or 0
        )

        cursor.execute("""
            SELECT COUNT(*) AS sin_equipos
            FROM inventario_colaboradores c
            WHERE c.estado = 'Activo'
              AND NOT EXISTS (
                  SELECT 1
                  FROM inventario_asignaciones a
                  WHERE a.colaborador_id = c.id
                    AND a.estado = 'Activa'
              )
        """)

        sin_asignar = cursor.fetchone()

        sin_equipos = int(
            sin_asignar.get("sin_equipos") or 0
        )

        return jsonify({
            "total": total,
            "activos": activos,
            "inactivos": inactivos,
            "conEquipos": con_equipos,
            "sinEquipos": sin_equipos
        })

    except Exception as error:
        return jsonify({
            "error": (
                "No se pudieron obtener las estadísticas."
            ),
            "detalle": str(error)
        }), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# GET: DEPARTAMENTOS DISPONIBLES
# ============================================================

@colaboradores_bp.route("/departamentos", methods=["GET"])
def listar_departamentos():
    """
    Devuelve los departamentos registrados para construir
    los filtros del frontend.
    """

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT DISTINCT departamento
            FROM inventario_colaboradores
            WHERE departamento IS NOT NULL
              AND TRIM(departamento) <> ''
            ORDER BY departamento
        """)

        registros = cursor.fetchall()

        departamentos = [
            row["departamento"]
            for row in registros
        ]

        return jsonify(departamentos)

    except Exception as error:
        return jsonify({
            "error": (
                "No se pudieron obtener los departamentos."
            ),
            "detalle": str(error)
        }), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# GET: OBTENER UN COLABORADOR
# ============================================================

@colaboradores_bp.route(
    "/<int:colaborador_id>",
    methods=["GET"]
)
def obtener_colaborador(colaborador_id):
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT
                c.id,
                c.numero_empleado,
                c.nombre,
                c.apellido_paterno,
                c.apellido_materno,
                c.empresa,
                c.departamento,
                c.puesto,
                c.correo,
                c.telefono,
                c.extension,
                c.ubicacion,
                c.fecha_ingreso,
                c.estado,
                c.comentarios,
                c.fecha_creacion,
                c.fecha_actualizacion,

                (
                    SELECT COUNT(*)
                    FROM inventario_asignaciones a
                    WHERE a.colaborador_id = c.id
                      AND a.estado = 'Activa'
                ) AS equipos_asignados

            FROM inventario_colaboradores c
            WHERE c.id = %s
        """, (colaborador_id,))

        row = cursor.fetchone()

        if not row:
            return jsonify({
                "error": "Colaborador no encontrado."
            }), 404

        colaborador = formatear_colaborador(row)

        cursor.execute("""
            SELECT
                a.id AS asignacion_id,
                a.equipo_id,
                a.fecha_asignacion,

                e.numero_inventario,
                e.descripcion,
                e.categoria,
                e.marca,
                e.modelo,
                e.numero_serie,
                e.estado AS estado_equipo,
                e.funcionamiento,
                e.ubicacion

            FROM inventario_asignaciones a

            INNER JOIN inventario_equipos e
                ON e.id = a.equipo_id

            WHERE a.colaborador_id = %s
              AND a.estado = 'Activa'

            ORDER BY a.fecha_asignacion DESC
        """, (colaborador_id,))

        equipos_registrados = cursor.fetchall()

        colaborador["equipos"] = [
            formatear_equipo_asignado(equipo)
            for equipo in equipos_registrados
        ]

        return jsonify(colaborador)

    except Exception as error:
        return jsonify({
            "error": (
                "No se pudo obtener el colaborador."
            ),
            "detalle": str(error)
        }), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# POST: CREAR COLABORADOR
# ============================================================

@colaboradores_bp.route("", methods=["POST"])
@colaboradores_bp.route("/", methods=["POST"])
def crear_colaborador():
    data = request.get_json(silent=True)

    if not data:
        return jsonify({
            "error": "No se recibieron datos."
        }), 400

    error_validacion = validar_campos_obligatorios(
        data
    )

    if error_validacion:
        return jsonify({
            "error": error_validacion
        }), 400

    estado = limpiar_texto(
        data.get("estado")
    ) or "Activo"

    if estado not in [
        "Activo",
        "Inactivo"
    ]:
        return jsonify({
            "error": (
                "El estado debe ser Activo o Inactivo."
            )
        }), 400

    try:
        fecha_ingreso = normalizar_fecha(
            data.get("fechaIngreso")
        )

    except ValueError as error:
        return jsonify({
            "error": str(error)
        }), 400

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:
        cursor.execute("""
            INSERT INTO inventario_colaboradores (
                numero_empleado,
                nombre,
                apellido_paterno,
                apellido_materno,
                empresa,
                departamento,
                puesto,
                correo,
                telefono,
                extension,
                ubicacion,
                fecha_ingreso,
                estado,
                comentarios
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s
            )
        """, (
            limpiar_texto(
                data.get("numeroEmpleado")
            ),
            limpiar_texto(
                data.get("nombre")
            ),
            limpiar_texto(
                data.get("apellidoPaterno")
            ),
            limpiar_texto(
                data.get("apellidoMaterno")
            ),
            limpiar_texto(
                data.get("empresa")
            ) or "ELITE BIKE",
            limpiar_texto(
                data.get("departamento")
            ),
            limpiar_texto(
                data.get("puesto")
            ),
            limpiar_texto(
                data.get("correo")
            ),
            limpiar_texto(
                data.get("telefono")
            ),
            limpiar_texto(
                data.get("extension")
            ),
            limpiar_texto(
                data.get("ubicacion")
            ),
            fecha_ingreso,
            estado,
            limpiar_texto(
                data.get("comentarios")
            )
        ))

        conexion.commit()

        nuevo_id = cursor.lastrowid

        return jsonify({
            "message": (
                "Colaborador registrado correctamente."
            ),
            "id": nuevo_id
        }), 201

    except Exception as error:
        conexion.rollback()

        return respuesta_error_base_datos(
            error,
            "registrar"
        )

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# PUT: ACTUALIZAR COLABORADOR
# ============================================================

@colaboradores_bp.route(
    "/<int:colaborador_id>",
    methods=["PUT"]
)
def actualizar_colaborador(colaborador_id):
    data = request.get_json(silent=True)

    if not data:
        return jsonify({
            "error": "No se recibieron datos."
        }), 400

    error_validacion = validar_campos_obligatorios(
        data
    )

    if error_validacion:
        return jsonify({
            "error": error_validacion
        }), 400

    estado = limpiar_texto(
        data.get("estado")
    ) or "Activo"

    if estado not in [
        "Activo",
        "Inactivo"
    ]:
        return jsonify({
            "error": (
                "El estado debe ser Activo o Inactivo."
            )
        }), 400

    try:
        fecha_ingreso = normalizar_fecha(
            data.get("fechaIngreso")
        )

    except ValueError as error:
        return jsonify({
            "error": str(error)
        }), 400

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        if not colaborador_existe(
            cursor,
            colaborador_id
        ):
            return jsonify({
                "error": "Colaborador no encontrado."
            }), 404

        if estado == "Inactivo":
            asignaciones_activas = (
                contar_asignaciones_activas(
                    cursor,
                    colaborador_id
                )
            )

            if asignaciones_activas > 0:
                return jsonify({
                    "error": (
                        "No se puede marcar al colaborador "
                        "como inactivo porque todavía tiene "
                        f"{asignaciones_activas} equipo(s) "
                        "asignado(s)."
                    )
                }), 409

        cursor.execute("""
            UPDATE inventario_colaboradores
            SET
                numero_empleado = %s,
                nombre = %s,
                apellido_paterno = %s,
                apellido_materno = %s,
                empresa = %s,
                departamento = %s,
                puesto = %s,
                correo = %s,
                telefono = %s,
                extension = %s,
                ubicacion = %s,
                fecha_ingreso = %s,
                estado = %s,
                comentarios = %s
            WHERE id = %s
        """, (
            limpiar_texto(
                data.get("numeroEmpleado")
            ),
            limpiar_texto(
                data.get("nombre")
            ),
            limpiar_texto(
                data.get("apellidoPaterno")
            ),
            limpiar_texto(
                data.get("apellidoMaterno")
            ),
            limpiar_texto(
                data.get("empresa")
            ) or "ELITE BIKE",
            limpiar_texto(
                data.get("departamento")
            ),
            limpiar_texto(
                data.get("puesto")
            ),
            limpiar_texto(
                data.get("correo")
            ),
            limpiar_texto(
                data.get("telefono")
            ),
            limpiar_texto(
                data.get("extension")
            ),
            limpiar_texto(
                data.get("ubicacion")
            ),
            fecha_ingreso,
            estado,
            limpiar_texto(
                data.get("comentarios")
            ),
            colaborador_id
        ))

        conexion.commit()

        return jsonify({
            "message": (
                "Colaborador actualizado correctamente."
            ),
            "id": colaborador_id
        })

    except Exception as error:
        conexion.rollback()

        return respuesta_error_base_datos(
            error,
            "actualizar"
        )

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# PATCH: CAMBIAR ESTADO
# ============================================================

@colaboradores_bp.route(
    "/<int:colaborador_id>/estado",
    methods=["PATCH"]
)
def cambiar_estado_colaborador(colaborador_id):
    data = request.get_json(silent=True)

    if not data:
        return jsonify({
            "error": "No se recibieron datos."
        }), 400

    nuevo_estado = limpiar_texto(
        data.get("estado")
    )

    if nuevo_estado not in [
        "Activo",
        "Inactivo"
    ]:
        return jsonify({
            "error": (
                "El estado debe ser Activo o Inactivo."
            )
        }), 400

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        if not colaborador_existe(
            cursor,
            colaborador_id
        ):
            return jsonify({
                "error": "Colaborador no encontrado."
            }), 404

        if nuevo_estado == "Inactivo":
            asignaciones_activas = (
                contar_asignaciones_activas(
                    cursor,
                    colaborador_id
                )
            )

            if asignaciones_activas > 0:
                return jsonify({
                    "error": (
                        "No se puede desactivar al colaborador "
                        "porque todavía tiene "
                        f"{asignaciones_activas} equipo(s) "
                        "asignado(s)."
                    )
                }), 409

        cursor.execute("""
            UPDATE inventario_colaboradores
            SET estado = %s
            WHERE id = %s
        """, (
            nuevo_estado,
            colaborador_id
        ))

        conexion.commit()

        return jsonify({
            "message": (
                f"Colaborador marcado como {nuevo_estado}."
            ),
            "id": colaborador_id,
            "estado": nuevo_estado
        })

    except Exception as error:
        conexion.rollback()

        return jsonify({
            "error": (
                "No se pudo cambiar el estado "
                "del colaborador."
            ),
            "detalle": str(error)
        }), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# DELETE: BAJA LÓGICA
# ============================================================

@colaboradores_bp.route(
    "/<int:colaborador_id>",
    methods=["DELETE"]
)
def desactivar_colaborador(colaborador_id):
    """
    No elimina físicamente el colaborador.

    Cambia su estado a Inactivo para conservar:
    - asignaciones
    - responsivas
    - movimientos
    - historial
    """

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        if not colaborador_existe(
            cursor,
            colaborador_id
        ):
            return jsonify({
                "error": "Colaborador no encontrado."
            }), 404

        asignaciones_activas = (
            contar_asignaciones_activas(
                cursor,
                colaborador_id
            )
        )

        if asignaciones_activas > 0:
            return jsonify({
                "error": (
                    "No se puede desactivar al colaborador "
                    "porque todavía tiene "
                    f"{asignaciones_activas} equipo(s) "
                    "asignado(s)."
                )
            }), 409

        cursor.execute("""
            UPDATE inventario_colaboradores
            SET estado = 'Inactivo'
            WHERE id = %s
        """, (colaborador_id,))

        conexion.commit()

        return jsonify({
            "message": (
                "Colaborador desactivado correctamente."
            ),
            "id": colaborador_id,
            "estado": "Inactivo"
        })

    except Exception as error:
        conexion.rollback()

        return jsonify({
            "error": (
                "No se pudo desactivar el colaborador."
            ),
            "detalle": str(error)
        }), 500

    finally:
        cursor.close()
        conexion.close()