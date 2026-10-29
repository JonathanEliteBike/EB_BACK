from datetime import date, datetime

from flask import Blueprint, g, jsonify, request

from db_conexion import obtener_conexion
from utils.inventario_actividad import registrar_evento, capturar_estado
from utils.inventario_auth import validar_acceso_inventario


asignaciones_bp = Blueprint(
    "asignaciones",
    __name__,
    url_prefix="/api/inventario/asignaciones"
)

# Aplica a todos los endpoints del blueprint (lectura y escritura).
asignaciones_bp.before_request(validar_acceso_inventario)


# ============================================================
# FUNCIONES AUXILIARES
# ============================================================

def limpiar_texto(valor):
    """
    Elimina espacios al inicio y al final.

    Si el texto queda vacío, devuelve None para guardar NULL
    en MySQL.
    """
    if valor is None:
        return None

    if isinstance(valor, str):
        valor = valor.strip()
        return valor if valor else None

    return valor


def convertir_entero(valor, nombre_campo):
    """
    Convierte un valor a entero positivo.
    """
    try:
        numero = int(valor)

        if numero <= 0:
            raise ValueError

        return numero

    except (TypeError, ValueError) as error:
        raise ValueError(
            f"El campo {nombre_campo} no es válido."
        ) from error


def normalizar_fecha_hora(valor, usar_fecha_actual=True):
    """
    Admite los formatos enviados normalmente por Angular:

    YYYY-MM-DD
    YYYY-MM-DDTHH:MM
    YYYY-MM-DD HH:MM:SS

    Si viene vacío y usar_fecha_actual es True, utiliza la
    fecha y hora actuales.
    """
    valor = limpiar_texto(valor)

    if not valor:
        return datetime.now() if usar_fecha_actual else None

    formatos = [
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d"
    ]

    for formato in formatos:
        try:
            return datetime.strptime(valor, formato)
        except ValueError:
            continue

    raise ValueError(
        "La fecha debe tener formato YYYY-MM-DD "
        "o YYYY-MM-DDTHH:MM."
    )


def fecha_devolucion_anterior_a_asignacion(fecha_devolucion, fecha_asignacion):
    """Valida el orden cronológico sin mezclar `date` con `datetime`.

    MySQL devuelve `date` para columnas DATE y `datetime` para DATETIME.
    Si la asignación solo almacena fecha, no inventamos una hora: comparamos
    fechas. Si la columna conserva hora, comparamos ambos instantes completos.
    """
    if fecha_asignacion is None:
        return False

    if isinstance(fecha_asignacion, datetime):
        return fecha_devolucion < fecha_asignacion

    if isinstance(fecha_asignacion, date):
        return fecha_devolucion.date() < fecha_asignacion

    raise TypeError(
        "El formato de la fecha de asignación almacenada no es válido."
    )


def fecha_texto(valor):
    if not valor:
        return ""

    return str(valor)


def nombre_completo_colaborador(row):
    return " ".join(
        parte
        for parte in [
            row.get("nombre"),
            row.get("apellido_paterno"),
            row.get("apellido_materno")
        ]
        if parte
    )



def generar_folio_responsiva(cursor):
    """
    Genera el siguiente folio de responsiva del año actual.

    La consulta se ejecuta dentro de la misma transacción de la
    asignación para evitar que la asignación quede guardada sin su
    responsiva correspondiente.
    """
    anio_actual = datetime.now().year
    prefijo = f"RESP-{anio_actual}-"

    cursor.execute(
        """
        SELECT folio
        FROM inventario_responsivas
        WHERE folio LIKE %s
        ORDER BY id DESC
        LIMIT 1
        FOR UPDATE
        """,
        (f"{prefijo}%",)
    )

    ultima = cursor.fetchone()
    consecutivo = 1

    if ultima and ultima.get("folio"):
        try:
            consecutivo = int(
                ultima["folio"].split("-")[-1]
            ) + 1
        except (TypeError, ValueError, IndexError):
            consecutivo = 1

    return f"{prefijo}{consecutivo:06d}"

def formatear_asignacion(row):
    nombre_colaborador = nombre_completo_colaborador(row)

    return {
        "id": row["id"],
        "equipoId": row["equipo_id"],
        "colaboradorId": row["colaborador_id"],

        "fechaAsignacion": fecha_texto(
            row.get("fecha_asignacion")
        ),

        "fechaDevolucion": fecha_texto(
            row.get("fecha_devolucion")
        ),

        "estado": row.get("estado") or "",
        "observacionesEntrega":
            row.get("observaciones_entrega") or "",

        "observacionesDevolucion":
            row.get("observaciones_devolucion") or "",

        "usuarioRegistro":
            row.get("usuario_registro") or "",

        "equipo": {
            "id": row["equipo_id"],
            "inventario":
                row.get("numero_inventario") or "",
            "nombre":
                row.get("descripcion_equipo") or "",
            "categoria":
                row.get("categoria_equipo") or "",
            "marca":
                row.get("marca_equipo") or "",
            "modelo":
                row.get("modelo_equipo") or "",
            "serie":
                row.get("numero_serie_equipo") or "",
            "funcionamiento":
                row.get("funcionamiento_equipo") or "",
            "estado":
                row.get("estado_equipo") or "",
            "ubicacion":
                row.get("ubicacion_equipo") or "",
            "empresa":
                row.get("empresa_equipo") or ""
        },

        "colaborador": {
            "id": row["colaborador_id"],
            "numeroEmpleado":
                row.get("numero_empleado") or "",
            "nombre":
                row.get("nombre") or "",
            "apellidoPaterno":
                row.get("apellido_paterno") or "",
            "apellidoMaterno":
                row.get("apellido_materno") or "",
            "nombreCompleto":
                nombre_colaborador,
            "empresa":
                row.get("empresa_colaborador") or "",
            "departamento":
                row.get("departamento_colaborador") or "",
            "puesto":
                row.get("puesto_colaborador") or "",
            "correo":
                row.get("correo_colaborador") or "",
            "estado":
                row.get("estado_colaborador") or ""
        }
    }


def consulta_base_asignaciones():
    return """
        SELECT
            a.id,
            a.equipo_id,
            a.colaborador_id,
            a.fecha_asignacion,
            a.fecha_devolucion,
            a.estado,
            a.observaciones_entrega,
            a.observaciones_devolucion,
            a.usuario_registro,
            a.fecha_creacion,
            a.fecha_actualizacion,

            e.numero_inventario,
            e.descripcion AS descripcion_equipo,
            e.categoria AS categoria_equipo,
            e.marca AS marca_equipo,
            e.modelo AS modelo_equipo,
            e.numero_serie AS numero_serie_equipo,
            e.funcionamiento AS funcionamiento_equipo,
            e.estado AS estado_equipo,
            e.ubicacion AS ubicacion_equipo,
            e.empresa AS empresa_equipo,

            c.numero_empleado,
            c.nombre,
            c.apellido_paterno,
            c.apellido_materno,
            c.empresa AS empresa_colaborador,
            c.departamento AS departamento_colaborador,
            c.puesto AS puesto_colaborador,
            c.correo AS correo_colaborador,
            c.estado AS estado_colaborador

        FROM inventario_asignaciones a

        INNER JOIN inventario_equipos e
            ON e.id = a.equipo_id

        INNER JOIN inventario_colaboradores c
            ON c.id = a.colaborador_id
    """


# ============================================================
# GET: LISTAR ASIGNACIONES
# ============================================================

@asignaciones_bp.route("", methods=["GET"])
@asignaciones_bp.route("/", methods=["GET"])
def listar_asignaciones():
    """
    Filtros opcionales:

    busqueda
    estado
    departamento
    empresa
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

    condiciones = []
    parametros = []

    if busqueda:
        termino = f"%{busqueda}%"

        condiciones.append("""
            (
                e.numero_inventario LIKE %s
                OR e.descripcion LIKE %s
                OR e.marca LIKE %s
                OR e.modelo LIKE %s
                OR e.numero_serie LIKE %s
                OR c.numero_empleado LIKE %s
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
            termino,
            termino
        ])

    if estado and estado != "Todas":
        condiciones.append(
            "a.estado = %s"
        )
        parametros.append(estado)

    if departamento and departamento != "Todos":
        condiciones.append(
            "c.departamento = %s"
        )
        parametros.append(departamento)

    if empresa and empresa != "Todas":
        condiciones.append("""
            (
                c.empresa = %s
                OR e.empresa = %s
            )
        """)

        parametros.extend([
            empresa,
            empresa
        ])

    where_sql = ""

    if condiciones:
        where_sql = (
            " WHERE "
            + " AND ".join(condiciones)
        )

    consulta = (
        consulta_base_asignaciones()
        + where_sql
        + """
            ORDER BY
                CASE a.estado
                    WHEN 'Activa' THEN 1
                    WHEN 'Finalizada' THEN 2
                    WHEN 'Cancelada' THEN 3
                    ELSE 4
                END,
                a.fecha_asignacion DESC,
                a.id DESC
        """
    )

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute(
            consulta,
            tuple(parametros)
        )

        registros = cursor.fetchall()

        return jsonify([
            formatear_asignacion(row)
            for row in registros
        ])

    except Exception as error:
        return jsonify({
            "error": (
                "No se pudieron obtener las asignaciones."
            ),
            "detalle": str(error)
        }), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# GET: ESTADÍSTICAS
# ============================================================

@asignaciones_bp.route(
    "/estadisticas",
    methods=["GET"]
)
def obtener_estadisticas():
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT
                SUM(
                    CASE
                        WHEN estado = 'Activa'
                        THEN 1
                        ELSE 0
                    END
                ) AS activas,

                SUM(
                    CASE
                        WHEN estado = 'Finalizada'
                        THEN 1
                        ELSE 0
                    END
                ) AS finalizadas,

                SUM(
                    CASE
                        WHEN estado = 'Cancelada'
                        THEN 1
                        ELSE 0
                    END
                ) AS canceladas,

                COUNT(*) AS total

            FROM inventario_asignaciones
        """)

        resumen = cursor.fetchone() or {}

        cursor.execute("""
            SELECT COUNT(*) AS disponibles
            FROM inventario_equipos
            WHERE estado = 'Disponible'
        """)

        disponibles = cursor.fetchone() or {}

        cursor.execute("""
            SELECT
                COUNT(DISTINCT colaborador_id)
                    AS colaboradores_con_equipo
            FROM inventario_asignaciones
            WHERE estado = 'Activa'
        """)

        colaboradores = cursor.fetchone() or {}

        return jsonify({
            "total": int(
                resumen.get("total") or 0
            ),
            "activas": int(
                resumen.get("activas") or 0
            ),
            "finalizadas": int(
                resumen.get("finalizadas") or 0
            ),
            "canceladas": int(
                resumen.get("canceladas") or 0
            ),
            "equiposDisponibles": int(
                disponibles.get("disponibles") or 0
            ),
            "colaboradoresConEquipo": int(
                colaboradores.get(
                    "colaboradores_con_equipo"
                ) or 0
            )
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
# GET: CATÁLOGOS PARA EL FORMULARIO
# ============================================================

@asignaciones_bp.route(
    "/catalogos",
    methods=["GET"]
)
def obtener_catalogos():
    """
    Devuelve únicamente:

    - colaboradores activos
    - equipos disponibles
    """

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

                (
                    SELECT COUNT(*)
                    FROM inventario_asignaciones a
                    WHERE a.colaborador_id = c.id
                      AND a.estado = 'Activa'
                ) AS equipos_asignados

            FROM inventario_colaboradores c

            WHERE c.estado = 'Activo'

            ORDER BY
                c.apellido_paterno,
                c.nombre
        """)

        colaboradores_registros = cursor.fetchall()

        colaboradores = []

        for row in colaboradores_registros:
            colaboradores.append({
                "id": row["id"],
                "numeroEmpleado":
                    row["numero_empleado"] or "",
                "nombreCompleto":
                    nombre_completo_colaborador(row),
                "empresa":
                    row["empresa"] or "",
                "departamento":
                    row["departamento"] or "",
                "puesto":
                    row["puesto"] or "",
                "correo":
                    row["correo"] or "",
                "equiposAsignados": int(
                    row.get("equipos_asignados") or 0
                )
            })

        cursor.execute("""
            SELECT
                id,
                numero_inventario,
                empresa,
                categoria,
                descripcion,
                marca,
                modelo,
                numero_serie,
                funcionamiento,
                estado,
                ubicacion,
                extras

            FROM inventario_equipos

            WHERE estado = 'Disponible'

              AND NOT EXISTS (
                  SELECT 1
                  FROM inventario_asignaciones a
                  WHERE a.equipo_id =
                        inventario_equipos.id
                    AND a.estado = 'Activa'
              )

            ORDER BY
                numero_inventario
        """)

        equipos_registros = cursor.fetchall()

        equipos = [
            {
                "id": row["id"],
                "inventario":
                    row["numero_inventario"] or "",
                "empresa":
                    row["empresa"] or "",
                "categoria":
                    row["categoria"] or "",
                "nombre":
                    row["descripcion"] or "",
                "marca":
                    row["marca"] or "",
                "modelo":
                    row["modelo"] or "",
                "serie":
                    row["numero_serie"] or "",
                "funcionamiento":
                    row["funcionamiento"] or "",
                "estado":
                    row["estado"] or "",
                "ubicacion":
                    row["ubicacion"] or "",
                "extras":
                    row["extras"] or ""
            }
            for row in equipos_registros
        ]

        return jsonify({
            "colaboradores": colaboradores,
            "equipos": equipos
        })

    except Exception as error:
        return jsonify({
            "error": (
                "No se pudieron obtener los catálogos."
            ),
            "detalle": str(error)
        }), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# GET: DETALLE DE UNA ASIGNACIÓN
# ============================================================

@asignaciones_bp.route(
    "/<int:asignacion_id>",
    methods=["GET"]
)
def obtener_asignacion(asignacion_id):
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        consulta = (
            consulta_base_asignaciones()
            + " WHERE a.id = %s"
        )

        cursor.execute(
            consulta,
            (asignacion_id,)
        )

        row = cursor.fetchone()

        if not row:
            return jsonify({
                "error": "Asignación no encontrada."
            }), 404

        return jsonify(
            formatear_asignacion(row)
        )

    except Exception as error:
        return jsonify({
            "error": (
                "No se pudo obtener la asignación."
            ),
            "detalle": str(error)
        }), 500

    finally:
        cursor.close()
        conexion.close()

# ============================================================
# POST: CREAR ASIGNACIÓN
# ============================================================

@asignaciones_bp.route("", methods=["POST"])
@asignaciones_bp.route("/", methods=["POST"])
def crear_asignacion():
    data = request.get_json(silent=True)

    if not data:
        return jsonify({
            "error": "No se recibieron datos."
        }), 400

    try:
        equipo_id = convertir_entero(
            data.get("equipoId"),
            "equipo"
        )

        colaborador_id = convertir_entero(
            data.get("colaboradorId"),
            "colaborador"
        )

        fecha_asignacion = normalizar_fecha_hora(
            data.get("fechaAsignacion")
        )

    except ValueError as error:
        return jsonify({
            "error": str(error)
        }), 400

    observaciones_entrega = limpiar_texto(
        data.get("observacionesEntrega")
    )

    usuario_registro = g.inventario_usuario["nombre"]

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        conexion.start_transaction()

        # ----------------------------------------------------
        # 1. BLOQUEAR Y VALIDAR AL COLABORADOR
        # ----------------------------------------------------

        cursor.execute(
            """
            SELECT
                id,
                numero_empleado,
                nombre,
                apellido_paterno,
                apellido_materno,
                empresa,
                departamento,
                puesto,
                correo,
                estado
            FROM inventario_colaboradores
            WHERE id = %s
            FOR UPDATE
            """,
            (colaborador_id,)
        )

        colaborador = cursor.fetchone()

        if not colaborador:
            conexion.rollback()

            return jsonify({
                "error": "Colaborador no encontrado."
            }), 404

        if colaborador.get("estado") != "Activo":
            conexion.rollback()

            return jsonify({
                "error": (
                    "El colaborador está inactivo y no puede "
                    "recibir equipos."
                )
            }), 409

        nombre_responsable = nombre_completo_colaborador(
            colaborador
        )

        if not nombre_responsable:
            conexion.rollback()

            return jsonify({
                "error": (
                    "El colaborador no tiene un nombre válido "
                    "para registrar la asignación."
                )
            }), 409

        # ----------------------------------------------------
        # 2. BLOQUEAR Y VALIDAR EL EQUIPO
        # ----------------------------------------------------

        cursor.execute(
            """
            SELECT
                id,
                numero_inventario,
                descripcion,
                estado,
                responsable,
                departamento,
                cargo,
                responsiva_estado
            FROM inventario_equipos
            WHERE id = %s
            FOR UPDATE
            """,
            (equipo_id,)
        )

        equipo = cursor.fetchone()

        if not equipo:
            conexion.rollback()

            return jsonify({
                "error": "Equipo no encontrado."
            }), 404

        if equipo.get("estado") == "Baja":
            conexion.rollback()

            return jsonify({
                "error": (
                    "El equipo está dado de baja y no puede "
                    "ser asignado."
                )
            }), 409

        if equipo.get("estado") != "Disponible":
            conexion.rollback()

            return jsonify({
                "error": (
                    "El equipo no está disponible para "
                    "asignación."
                )
            }), 409

        # ----------------------------------------------------
        # 3. COMPROBAR ASIGNACIÓN ACTIVA DUPLICADA
        # ----------------------------------------------------

        cursor.execute(
            """
            SELECT id
            FROM inventario_asignaciones
            WHERE equipo_id = %s
              AND estado = 'Activa'
            LIMIT 1
            FOR UPDATE
            """,
            (equipo_id,)
        )

        asignacion_existente = cursor.fetchone()

        if asignacion_existente:
            conexion.rollback()

            return jsonify({
                "error": (
                    "El equipo ya tiene una asignación activa."
                ),
                "asignacionId": asignacion_existente["id"]
            }), 409

        # ----------------------------------------------------
        # 4. CREAR ASIGNACIÓN
        # ----------------------------------------------------

        cursor.execute(
            """
            INSERT INTO inventario_asignaciones (
                equipo_id,
                colaborador_id,
                fecha_asignacion,
                estado,
                observaciones_entrega,
                usuario_registro
            )
            VALUES (
                %s,
                %s,
                %s,
                'Activa',
                %s,
                %s
            )
            """,
            (
                equipo_id,
                colaborador_id,
                fecha_asignacion,
                observaciones_entrega,
                usuario_registro
            )
        )

        asignacion_id = cursor.lastrowid

        # ----------------------------------------------------
        # 5. CREAR RESPONSIVA PENDIENTE
        # ----------------------------------------------------

        folio_responsiva = generar_folio_responsiva(
            cursor
        )

        cursor.execute(
            """
            INSERT INTO inventario_responsivas (
                asignacion_id,
                equipo_id,
                colaborador_id,
                folio,
                responsable,
                departamento,
                estado,
                fecha_generacion,
                observaciones
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                'Pendiente',
                CURRENT_TIMESTAMP,
                %s
            )
            """,
            (
                asignacion_id,
                equipo_id,
                colaborador_id,
                folio_responsiva,
                nombre_responsable,
                colaborador.get("departamento"),
                observaciones_entrega
            )
        )

        responsiva_id = cursor.lastrowid

        # ----------------------------------------------------
        # 6. ACTUALIZAR EL EQUIPO
        # ----------------------------------------------------

        cursor.execute(
            """
            UPDATE inventario_equipos
            SET
                responsable = %s,
                departamento = %s,
                cargo = %s,
                estado = 'Asignado',
                responsiva_estado = 'Pendiente'
            WHERE id = %s
              AND estado = 'Disponible'
            """,
            (
                nombre_responsable,
                colaborador.get("departamento"),
                colaborador.get("puesto"),
                equipo_id
            )
        )

        if cursor.rowcount != 1:
            raise RuntimeError(
                "No fue posible actualizar el estado del equipo."
            )

        # ----------------------------------------------------
        # 7. REGISTRAR MOVIMIENTO EN HISTORIAL
        # ----------------------------------------------------

        descripcion_movimiento = (
            f"Asignación del equipo "
            f"{equipo.get('numero_inventario') or equipo_id} "
            f"a {nombre_responsable}. "
            f"Asignación #{asignacion_id}. "
            f"Responsiva {folio_responsiva}."
        )

        cursor.execute(
            """
            INSERT INTO inventario_movimientos (
                equipo_id,
                tipo_movimiento,
                descripcion,
                responsable_anterior,
                responsable_nuevo,
                usuario_registro
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s
            )
            """,
            (
                equipo_id,
                "Asignación",
                descripcion_movimiento,
                equipo.get("responsable"),
                nombre_responsable,
                usuario_registro
            )
        )

        # ----------------------------------------------------
        # 8. CONFIRMAR TODA LA OPERACIÓN
        # ----------------------------------------------------

        registrar_evento(cursor, 'asignar', 'Asignación', asignacion_id)
        conexion.commit()

        return jsonify({
            "message": (
                "Equipo asignado correctamente. La responsiva "
                "se generó automáticamente."
            ),
            "id": asignacion_id,
            "equipoId": equipo_id,
            "colaboradorId": colaborador_id,
            "responsiva": {
                "id": responsiva_id,
                "folio": folio_responsiva,
                "estado": "Pendiente"
            },
            "folioResponsiva": folio_responsiva
        }), 201

    except Exception as error:
        conexion.rollback()

        print(
            "Error al crear asignación:",
            error
        )

        if getattr(error, "errno", None) == 1062:
            return jsonify({
                "error": (
                    "El equipo ya tiene una asignación activa "
                    "o la responsiva ya existe."
                ),
                "detalle": str(error)
            }), 409

        return jsonify({
            "error": "No se pudo crear la asignación.",
            "detalle": str(error)
        }), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# PUT: FINALIZAR ASIGNACIÓN / DEVOLVER EQUIPO
# ============================================================


# ============================================================
# EDITAR OBSERVACIONES SIN DEVOLVER EL EQUIPO
# ============================================================

@asignaciones_bp.route(
    "/<int:asignacion_id>/observaciones-entrega",
    methods=["PUT"]
)
def actualizar_observaciones_entrega(asignacion_id):
    data = request.get_json(silent=True)

    if not isinstance(data, dict) or "observacionesEntrega" not in data:
        return jsonify({
            "error": "Debes enviar las observaciones de entrega."
        }), 400

    if not isinstance(data["observacionesEntrega"], str):
        return jsonify({
            "error": "Las observaciones deben ser texto."
        }), 400

    observaciones = limpiar_texto(data["observacionesEntrega"])

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        conexion.start_transaction()

        cursor.execute(
            """
            SELECT id, estado, observaciones_entrega
            FROM inventario_asignaciones
            WHERE id = %s
            FOR UPDATE
            """,
            (asignacion_id,)
        )

        asignacion = cursor.fetchone()

        if not asignacion:
            conexion.rollback()
            return jsonify({
                "error": "Asignacion no encontrada."
            }), 404

        if asignacion["estado"] != "Activa":
            conexion.rollback()
            return jsonify({
                "error": "Solo pueden corregirse asignaciones activas."
            }), 409

        cursor.execute(
            """
            SELECT id, estado, observaciones
            FROM inventario_responsivas
            WHERE asignacion_id = %s
            ORDER BY id DESC
            FOR UPDATE
            """,
            (asignacion_id,)
        )

        responsivas = cursor.fetchall()

        if len(responsivas) != 1:
            conexion.rollback()
            return jsonify({
                "error": "La asignacion requiere revision de su responsiva."
            }), 409

        responsiva = responsivas[0]

        if responsiva["estado"] not in ("Pendiente", "Firmada"):
            conexion.rollback()
            return jsonify({
                "error": (
                    "La responsiva ya esta firmada o anulada. "
                    "Debe conservarse el documento original."
                )
            }), 409

        anterior_asignacion = (
            asignacion["observaciones_entrega"] or ""
        )
        anterior_responsiva = (
            responsiva["observaciones"] or ""
        )
        nuevo_texto = observaciones or ""

        if (
            anterior_asignacion == nuevo_texto
            and (
                responsiva["estado"] == "Firmada"
                or anterior_responsiva == nuevo_texto
            )
        ):
            conexion.rollback()
            return jsonify({
                "message": "Las observaciones ya estan actualizadas.",
                "id": asignacion_id,
                "observacionesEntrega": nuevo_texto
            }), 200

        estado_asignacion = capturar_estado(
            cursor, "Asignaci\u00f3n", asignacion_id
        )
        estado_responsiva = (
            capturar_estado(cursor, "Responsiva", responsiva["id"])
            if responsiva["estado"] == "Pendiente"
            else None
        )

        cursor.execute(
            """
            UPDATE inventario_asignaciones
            SET observaciones_entrega = %s
            WHERE id = %s AND estado = 'Activa'
            """,
            (observaciones, asignacion_id)
        )

        if responsiva["estado"] == "Pendiente":
            cursor.execute(
                """
                UPDATE inventario_responsivas
                SET observaciones = %s
                WHERE id = %s AND estado = 'Pendiente'
                """,
                (observaciones, responsiva["id"])
            )

        registrar_evento(
            cursor, "editar", "Asignaci\u00f3n",
            asignacion_id, anterior=estado_asignacion
        )

        if responsiva["estado"] == "Pendiente":
            registrar_evento(
                cursor, "editar", "Responsiva",
                responsiva["id"], anterior=estado_responsiva
            )

        conexion.commit()

        return jsonify({
            "message": (
                "Observaciones corregidas. La responsiva firmada "
                "original se conserva."
                if responsiva["estado"] == "Firmada"
                else "Observaciones corregidas correctamente."
            ),
            "id": asignacion_id,
            "observacionesEntrega": nuevo_texto
        }), 200

    except Exception:
        conexion.rollback()
        raise

    finally:
        cursor.close()
        conexion.close()


@asignaciones_bp.route(
    "/<int:asignacion_id>/finalizar",
    methods=["PUT"]
)
def finalizar_asignacion(asignacion_id):
    data = request.get_json(silent=True) or {}

    try:
        fecha_devolucion = normalizar_fecha_hora(
            data.get("fechaDevolucion")
        )

    except ValueError as error:
        return jsonify({
            "error": str(error)
        }), 400

    observaciones_devolucion = limpiar_texto(
        data.get("observacionesDevolucion")
    )

    usuario_registro = g.inventario_usuario["nombre"]

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        conexion.start_transaction()

        # ----------------------------------------------------
        # 1. BLOQUEAR ASIGNACIÓN, EQUIPO Y COLABORADOR
        # ----------------------------------------------------

        cursor.execute(
            """
            SELECT
                a.id,
                a.equipo_id,
                a.colaborador_id,
                a.estado,
                a.fecha_asignacion,
                a.fecha_devolucion,

                e.numero_inventario,
                e.descripcion AS descripcion_equipo,
                e.estado AS estado_equipo,
                e.responsable,

                c.nombre,
                c.apellido_paterno,
                c.apellido_materno

            FROM inventario_asignaciones a

            INNER JOIN inventario_equipos e
                ON e.id = a.equipo_id

            INNER JOIN inventario_colaboradores c
                ON c.id = a.colaborador_id

            WHERE a.id = %s

            FOR UPDATE
            """,
            (asignacion_id,)
        )

        asignacion = cursor.fetchone()

        if not asignacion:
            conexion.rollback()

            return jsonify({
                "error": "Asignación no encontrada."
            }), 404

        if asignacion.get("estado") != "Activa":
            conexion.rollback()

            return jsonify({
                "error": "La asignación ya no está activa."
            }), 409

        fecha_asignacion = asignacion.get(
            "fecha_asignacion"
        )

        if fecha_devolucion_anterior_a_asignacion(
            fecha_devolucion, fecha_asignacion
        ):
            conexion.rollback()

            return jsonify({
                "error": (
                    "La fecha de devolución no puede ser "
                    "anterior a la fecha de asignación."
                )
            }), 400

        equipo_id = asignacion["equipo_id"]

        nombre_responsable = nombre_completo_colaborador(
            asignacion
        )

        # ----------------------------------------------------
        # 2. FINALIZAR ASIGNACIÓN
        # ----------------------------------------------------

        estado_anterior_bitacora = capturar_estado(cursor, 'Asignación', asignacion_id)
        cursor.execute(
            """
            UPDATE inventario_asignaciones
            SET
                fecha_devolucion = %s,
                estado = 'Finalizada',
                observaciones_devolucion = %s
            WHERE id = %s
              AND estado = 'Activa'
            """,
            (
                fecha_devolucion,
                observaciones_devolucion,
                asignacion_id
            )
        )

        if cursor.rowcount != 1:
            raise RuntimeError(
                "No fue posible finalizar la asignación."
            )

        # ----------------------------------------------------
        # 3. ANULAR RESPONSIVA PENDIENTE
        # ----------------------------------------------------

        motivo_anulacion = (
            "Responsiva anulada automáticamente porque el "
            "equipo fue devuelto y la asignación finalizó."
        )

        cursor.execute(
            """
            UPDATE inventario_responsivas
            SET
                estado = 'Anulada',
                fecha_anulacion = CURRENT_TIMESTAMP,
                motivo_anulacion = %s
            WHERE asignacion_id = %s
              AND estado = 'Pendiente'
            """,
            (
                motivo_anulacion,
                asignacion_id
            )
        )

        responsiva_pendiente_anulada = (
            cursor.rowcount > 0
        )

        # ----------------------------------------------------
        # 4. LIBERAR EL EQUIPO
        # ----------------------------------------------------

        cursor.execute(
            """
            UPDATE inventario_equipos
            SET
                responsable = NULL,
                departamento = NULL,
                cargo = NULL,
                estado = 'Disponible',
                responsiva_estado = 'No aplica'
            WHERE id = %s
            """,
            (equipo_id,)
        )

        if cursor.rowcount != 1:
            raise RuntimeError(
                "No fue posible liberar el equipo."
            )

        # ----------------------------------------------------
        # 5. REGISTRAR DEVOLUCIÓN EN HISTORIAL
        # ----------------------------------------------------

        descripcion_movimiento = (
            f"Devolución del equipo "
            f"{asignacion.get('numero_inventario') or equipo_id} "
            f"por {nombre_responsable or 'Sin responsable'}. "
            f"Asignación #{asignacion_id} finalizada."
        )

        if observaciones_devolucion:
            descripcion_movimiento += (
                f" Observaciones: {observaciones_devolucion}"
            )

        cursor.execute(
            """
            INSERT INTO inventario_movimientos (
                equipo_id,
                tipo_movimiento,
                descripcion,
                responsable_anterior,
                responsable_nuevo,
                usuario_registro
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s
            )
            """,
            (
                equipo_id,
                "Devolución",
                descripcion_movimiento,
                nombre_responsable,
                None,
                usuario_registro
            )
        )

        # ----------------------------------------------------
        # 6. CONFIRMAR TODA LA OPERACIÓN
        # ----------------------------------------------------

        registrar_evento(cursor, 'devolver', 'Asignación', asignacion_id, anterior=estado_anterior_bitacora)
        conexion.commit()

        return jsonify({
            "message": (
                "La devolución se registró correctamente. "
                "El equipo volvió a estar disponible."
            ),
            "id": asignacion_id,
            "equipoId": equipo_id,
            "estado": "Finalizada",
            "equipoEstado": "Disponible",
            "responsivaPendienteAnulada": (
                responsiva_pendiente_anulada
            )
        })

    except Exception as error:
        conexion.rollback()

        print(
            "Error al finalizar asignación:",
            error
        )

        return jsonify({
            "error": "No se pudo finalizar la asignación.",
            "detalle": str(error)
        }), 500

    finally:
        cursor.close()
        conexion.close()