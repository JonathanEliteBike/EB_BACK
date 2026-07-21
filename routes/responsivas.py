import os
from datetime import datetime
from html import escape
from io import BytesIO

from flask import Blueprint, jsonify, request, send_file

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from db_conexion import obtener_conexion


responsivas_bp = Blueprint(
    "responsivas",
    __name__,
    url_prefix="/api/inventario/responsivas",
)


# ============================================================
# FUNCIONES AUXILIARES
# ============================================================

def limpiar_texto(valor):
    if valor is None:
        return None

    if isinstance(valor, str):
        valor = valor.strip()
        return valor if valor else None

    return valor


def convertir_entero(valor, nombre_campo):
    try:
        numero = int(valor)

        if numero <= 0:
            raise ValueError

        return numero

    except (TypeError, ValueError) as error:
        raise ValueError(
            f"El campo {nombre_campo} no es válido."
        ) from error


def fecha_a_texto(valor):
    if not valor:
        return ""

    return str(valor)


def nombre_completo(row):
    return " ".join(
        str(parte).strip()
        for parte in [
            row.get("nombre"),
            row.get("apellido_paterno"),
            row.get("apellido_materno"),
        ]
        if parte and str(parte).strip()
    )


def generar_folio(cursor):
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
        (f"{prefijo}%",),
    )

    ultimo = cursor.fetchone()
    consecutivo = 1

    if ultimo and ultimo.get("folio"):
        try:
            consecutivo = int(
                ultimo["folio"].split("-")[-1]
            ) + 1
        except (TypeError, ValueError, IndexError):
            consecutivo = 1

    return f"{prefijo}{consecutivo:06d}"


def formatear_responsiva(row):
    return {
        "id": row["id"],
        "asignacionId": row.get("asignacion_id"),
        "equipoId": row["equipo_id"],
        "colaboradorId": row.get("colaborador_id"),
        "folio": row.get("folio") or "",
        "estado": row.get("estado") or "Pendiente",
        "fechaGeneracion": fecha_a_texto(
            row.get("fecha_generacion")
        ),
        "fechaFirma": fecha_a_texto(
            row.get("fecha_firma")
        ),
        "fechaAnulacion": fecha_a_texto(
            row.get("fecha_anulacion")
        ),
        "motivoAnulacion": (
            row.get("motivo_anulacion") or ""
        ),
        "observaciones": row.get("observaciones") or "",
        "archivoPdf": row.get("archivo_pdf") or "",
        "responsable": row.get("responsable") or "",
        "departamento": row.get("departamento") or "",
        "equipo": {
            "id": row["equipo_id"],
            "inventario": (
                row.get("numero_inventario") or ""
            ),
            "nombre": (
                row.get("descripcion_equipo") or ""
            ),
            "categoria": (
                row.get("categoria_equipo") or ""
            ),
            "marca": row.get("marca_equipo") or "",
            "modelo": row.get("modelo_equipo") or "",
            "serie": (
                row.get("numero_serie_equipo") or ""
            ),
            "funcionamiento": (
                row.get("funcionamiento_equipo") or ""
            ),
            "extras": row.get("extras_equipo") or "",
            "empresa": row.get("empresa_equipo") or "",
            "responsivaEstado": (
                row.get("responsiva_estado_equipo")
                or "No aplica"
            ),
        },
        "colaborador": {
            "id": row.get("colaborador_id"),
            "numeroEmpleado": (
                row.get("numero_empleado") or ""
            ),
            "nombreCompleto": nombre_completo(row),
            "puesto": (
                row.get("puesto_colaborador") or ""
            ),
            "empresa": (
                row.get("empresa_colaborador") or ""
            ),
            "departamento": (
                row.get("departamento_colaborador") or ""
            ),
        },
        "asignacion": {
            "id": row.get("asignacion_id"),
            "estado": row.get("estado_asignacion") or "",
            "fechaAsignacion": fecha_a_texto(
                row.get("fecha_asignacion")
            ),
            "fechaDevolucion": fecha_a_texto(
                row.get("fecha_devolucion")
            ),
        },
    }


def consulta_base():
    return """
        SELECT
            r.id,
            r.asignacion_id,
            r.equipo_id,
            r.colaborador_id,
            r.folio,
            r.responsable,
            r.departamento,
            r.estado,
            r.fecha_generacion,
            r.fecha_firma,
            r.fecha_anulacion,
            r.motivo_anulacion,
            r.observaciones,
            r.archivo_pdf,
            r.fecha_creacion,

            e.numero_inventario,
            e.descripcion AS descripcion_equipo,
            e.categoria AS categoria_equipo,
            e.marca AS marca_equipo,
            e.modelo AS modelo_equipo,
            e.numero_serie AS numero_serie_equipo,
            e.funcionamiento AS funcionamiento_equipo,
            e.extras AS extras_equipo,
            e.empresa AS empresa_equipo,
            e.responsiva_estado
                AS responsiva_estado_equipo,

            c.numero_empleado,
            c.nombre,
            c.apellido_paterno,
            c.apellido_materno,
            c.puesto AS puesto_colaborador,
            c.empresa AS empresa_colaborador,
            c.departamento
                AS departamento_colaborador,

            a.estado AS estado_asignacion,
            a.fecha_asignacion,
            a.fecha_devolucion

        FROM inventario_responsivas r

        INNER JOIN inventario_equipos e
            ON e.id = r.equipo_id

        LEFT JOIN inventario_colaboradores c
            ON c.id = r.colaborador_id

        LEFT JOIN inventario_asignaciones a
            ON a.id = r.asignacion_id
    """


def aplicar_estilo_tabla(tabla):
    tabla.setStyle(
        TableStyle(
            [
                (
                    "GRID",
                    (0, 0),
                    (-1, -1),
                    0.5,
                    colors.HexColor("#d6d6d6"),
                ),
                (
                    "BACKGROUND",
                    (0, 0),
                    (0, -1),
                    colors.HexColor("#f3f3f3"),
                ),
                (
                    "VALIGN",
                    (0, 0),
                    (-1, -1),
                    "TOP",
                ),
                (
                    "LEFTPADDING",
                    (0, 0),
                    (-1, -1),
                    8,
                ),
                (
                    "RIGHTPADDING",
                    (0, 0),
                    (-1, -1),
                    8,
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    5,
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    5,
                ),
            ]
        )
    )


# ============================================================
# GET: LISTAR RESPONSIVAS
# ============================================================

@responsivas_bp.route("", methods=["GET"])
@responsivas_bp.route("/", methods=["GET"])
def listar_responsivas():
    busqueda = limpiar_texto(
        request.args.get("busqueda")
    )
    estado = limpiar_texto(
        request.args.get("estado")
    )
    empresa = limpiar_texto(
        request.args.get("empresa")
    )
    departamento = limpiar_texto(
        request.args.get("departamento")
    )

    condiciones = []
    parametros = []

    if busqueda:
        termino = f"%{busqueda}%"

        condiciones.append(
            """
            (
                r.folio LIKE %s
                OR r.responsable LIKE %s
                OR e.numero_inventario LIKE %s
                OR e.descripcion LIKE %s
                OR e.marca LIKE %s
                OR e.modelo LIKE %s
                OR e.numero_serie LIKE %s
                OR c.numero_empleado LIKE %s
                OR CONCAT_WS(
                    ' ',
                    c.nombre,
                    c.apellido_paterno,
                    c.apellido_materno
                ) LIKE %s
                OR c.puesto LIKE %s
            )
            """
        )

        parametros.extend([termino] * 10)

    if estado and estado != "Todos":
        condiciones.append("r.estado = %s")
        parametros.append(estado)

    if empresa and empresa != "Todas":
        condiciones.append(
            "(c.empresa = %s OR e.empresa = %s)"
        )
        parametros.extend([empresa, empresa])

    if departamento and departamento != "Todos":
        condiciones.append("c.departamento = %s")
        parametros.append(departamento)

    consulta = consulta_base()

    if condiciones:
        consulta += (
            " WHERE "
            + " AND ".join(condiciones)
        )

    consulta += """
        ORDER BY
            CASE r.estado
                WHEN 'Pendiente' THEN 1
                WHEN 'Firmada' THEN 2
                WHEN 'Anulada' THEN 3
                ELSE 4
            END,
            r.fecha_generacion DESC,
            r.id DESC
    """

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute(consulta, tuple(parametros))
        registros = cursor.fetchall()

        return jsonify(
            [
                formatear_responsiva(registro)
                for registro in registros
            ]
        )

    except Exception as error:
        print("Error al listar responsivas:", error)

        return jsonify(
            {
                "error": (
                    "No se pudieron obtener las responsivas."
                ),
                "detalle": str(error),
            }
        ), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# GET: ESTADÍSTICAS
# ============================================================

@responsivas_bp.route(
    "/estadisticas",
    methods=["GET"],
)
def obtener_estadisticas():
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(
                    CASE
                        WHEN estado = 'Pendiente'
                        THEN 1
                        ELSE 0
                    END
                ) AS pendientes,
                SUM(
                    CASE
                        WHEN estado = 'Firmada'
                        THEN 1
                        ELSE 0
                    END
                ) AS firmadas,
                SUM(
                    CASE
                        WHEN estado = 'Anulada'
                        THEN 1
                        ELSE 0
                    END
                ) AS anuladas
            FROM inventario_responsivas
            """
        )

        resultado = cursor.fetchone() or {}

        return jsonify(
            {
                "total": int(
                    resultado.get("total") or 0
                ),
                "pendientes": int(
                    resultado.get("pendientes") or 0
                ),
                "firmadas": int(
                    resultado.get("firmadas") or 0
                ),
                "anuladas": int(
                    resultado.get("anuladas") or 0
                ),
            }
        )

    except Exception as error:
        print(
            "Error al obtener estadísticas de responsivas:",
            error,
        )

        return jsonify(
            {
                "error": (
                    "No se pudieron obtener las estadísticas."
                ),
                "detalle": str(error),
            }
        ), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# GET: DETALLE DE RESPONSIVA
# ============================================================

@responsivas_bp.route(
    "/<int:responsiva_id>",
    methods=["GET"],
)
def obtener_responsiva(responsiva_id):
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        consulta = (
            consulta_base()
            + " WHERE r.id = %s"
        )

        cursor.execute(
            consulta,
            (responsiva_id,),
        )

        registro = cursor.fetchone()

        if not registro:
            return jsonify(
                {
                    "error": (
                        "Responsiva no encontrada."
                    )
                }
            ), 404

        return jsonify(
            formatear_responsiva(registro)
        )

    except Exception as error:
        print("Error al obtener responsiva:", error)

        return jsonify(
            {
                "error": (
                    "No se pudo obtener la responsiva."
                ),
                "detalle": str(error),
            }
        ), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# POST: CREAR RESPONSIVA MANUALMENTE
# ============================================================

@responsivas_bp.route("", methods=["POST"])
@responsivas_bp.route("/", methods=["POST"])
def crear_responsiva():
    data = request.get_json(silent=True)

    if not data:
        return jsonify(
            {"error": "No se recibieron datos."}
        ), 400

    try:
        asignacion_id = convertir_entero(
            data.get("asignacionId"),
            "asignación",
        )
    except ValueError as error:
        return jsonify(
            {"error": str(error)}
        ), 400

    observaciones = limpiar_texto(
        data.get("observaciones")
    )

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        conexion.start_transaction()

        cursor.execute(
            """
            SELECT
                a.id,
                a.equipo_id,
                a.colaborador_id,
                a.estado AS estado_asignacion,

                c.nombre,
                c.apellido_paterno,
                c.apellido_materno,
                c.departamento

            FROM inventario_asignaciones a

            INNER JOIN inventario_colaboradores c
                ON c.id = a.colaborador_id

            WHERE a.id = %s
            FOR UPDATE
            """,
            (asignacion_id,),
        )

        asignacion = cursor.fetchone()

        if not asignacion:
            conexion.rollback()

            return jsonify(
                {"error": "Asignación no encontrada."}
            ), 404

        cursor.execute(
            """
            SELECT id
            FROM inventario_responsivas
            WHERE asignacion_id = %s
            LIMIT 1
            FOR UPDATE
            """,
            (asignacion_id,),
        )

        existente = cursor.fetchone()

        if existente:
            conexion.rollback()

            return jsonify(
                {
                    "error": (
                        "La asignación ya tiene una responsiva."
                    ),
                    "responsivaId": existente["id"],
                }
            ), 409

        folio = generar_folio(cursor)
        responsable = nombre_completo(asignacion)

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
                asignacion["equipo_id"],
                asignacion["colaborador_id"],
                folio,
                responsable,
                asignacion.get("departamento"),
                observaciones,
            ),
        )

        responsiva_id = cursor.lastrowid

        if (
            asignacion.get("estado_asignacion")
            == "Activa"
        ):
            cursor.execute(
                """
                UPDATE inventario_equipos
                SET responsiva_estado = 'Pendiente'
                WHERE id = %s
                """,
                (asignacion["equipo_id"],),
            )

        conexion.commit()

        return jsonify(
            {
                "message": (
                    "Responsiva creada correctamente."
                ),
                "id": responsiva_id,
                "folio": folio,
                "estado": "Pendiente",
            }
        ), 201

    except Exception as error:
        conexion.rollback()
        print("Error al crear responsiva:", error)

        return jsonify(
            {
                "error": (
                    "No se pudo crear la responsiva."
                ),
                "detalle": str(error),
            }
        ), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# PUT: ACTUALIZAR DATOS GENERALES
# ============================================================

@responsivas_bp.route(
    "/<int:responsiva_id>",
    methods=["PUT"],
)
def actualizar_responsiva(responsiva_id):
    data = request.get_json(silent=True)

    if not data:
        return jsonify(
            {"error": "No se recibieron datos."}
        ), 400

    observaciones = limpiar_texto(
        data.get("observaciones")
    )
    archivo_pdf = limpiar_texto(
        data.get("archivoPdf")
    )

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        conexion.start_transaction()

        cursor.execute(
            """
            SELECT id, estado
            FROM inventario_responsivas
            WHERE id = %s
            FOR UPDATE
            """,
            (responsiva_id,),
        )

        responsiva = cursor.fetchone()

        if not responsiva:
            conexion.rollback()

            return jsonify(
                {"error": "Responsiva no encontrada."}
            ), 404

        if responsiva["estado"] == "Anulada":
            conexion.rollback()

            return jsonify(
                {
                    "error": (
                        "Una responsiva anulada no puede editarse."
                    )
                }
            ), 409

        cursor.execute(
            """
            UPDATE inventario_responsivas
            SET
                observaciones = %s,
                archivo_pdf = %s
            WHERE id = %s
            """,
            (
                observaciones,
                archivo_pdf,
                responsiva_id,
            ),
        )

        conexion.commit()

        return jsonify(
            {
                "message": (
                    "Responsiva actualizada correctamente."
                ),
                "id": responsiva_id,
            }
        )

    except Exception as error:
        conexion.rollback()
        print("Error al actualizar responsiva:", error)

        return jsonify(
            {
                "error": (
                    "No se pudo actualizar la responsiva."
                ),
                "detalle": str(error),
            }
        ), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# PUT: FIRMAR RESPONSIVA
# ============================================================

@responsivas_bp.route(
    "/<int:responsiva_id>/firmar",
    methods=["PUT"],
)
def firmar_responsiva(responsiva_id):
    data = request.get_json(silent=True) or {}

    archivo_pdf = limpiar_texto(
        data.get("archivoPdf")
    )
    observaciones = limpiar_texto(
        data.get("observaciones")
    )

    if not archivo_pdf:
        return jsonify(
            {
                "error": (
                    "Debes registrar la URL del documento "
                    "firmado en Drive."
                )
            }
        ), 400

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        conexion.start_transaction()

        cursor.execute(
            """
            SELECT
                r.id,
                r.equipo_id,
                r.estado,
                a.estado AS estado_asignacion

            FROM inventario_responsivas r

            LEFT JOIN inventario_asignaciones a
                ON a.id = r.asignacion_id

            WHERE r.id = %s
            FOR UPDATE
            """,
            (responsiva_id,),
        )

        responsiva = cursor.fetchone()

        if not responsiva:
            conexion.rollback()

            return jsonify(
                {"error": "Responsiva no encontrada."}
            ), 404

        if responsiva["estado"] == "Anulada":
            conexion.rollback()

            return jsonify(
                {
                    "error": (
                        "Una responsiva anulada no puede "
                        "marcarse como firmada."
                    )
                }
            ), 409

        cursor.execute(
            """
            UPDATE inventario_responsivas
            SET
                estado = 'Firmada',
                fecha_firma = CURRENT_TIMESTAMP,
                fecha_anulacion = NULL,
                motivo_anulacion = NULL,
                archivo_pdf = %s,
                observaciones = %s
            WHERE id = %s
            """,
            (
                archivo_pdf,
                observaciones,
                responsiva_id,
            ),
        )

        if (
            responsiva.get("estado_asignacion")
            == "Activa"
        ):
            cursor.execute(
                """
                UPDATE inventario_equipos
                SET responsiva_estado = 'Firmada'
                WHERE id = %s
                """,
                (responsiva["equipo_id"],),
            )

        conexion.commit()

        return jsonify(
            {
                "message": (
                    "Responsiva marcada como firmada."
                ),
                "id": responsiva_id,
                "estado": "Firmada",
                "archivoPdf": archivo_pdf,
            }
        )

    except Exception as error:
        conexion.rollback()
        print("Error al firmar responsiva:", error)

        return jsonify(
            {
                "error": (
                    "No se pudo firmar la responsiva."
                ),
                "detalle": str(error),
            }
        ), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# PUT: ANULAR RESPONSIVA
# ============================================================

@responsivas_bp.route(
    "/<int:responsiva_id>/anular",
    methods=["PUT"],
)
def anular_responsiva(responsiva_id):
    data = request.get_json(silent=True) or {}

    motivo = limpiar_texto(
        data.get("motivoAnulacion")
    )

    if not motivo:
        return jsonify(
            {
                "error": (
                    "Debes indicar el motivo de anulación."
                )
            }
        ), 400

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        conexion.start_transaction()

        cursor.execute(
            """
            SELECT
                r.id,
                r.equipo_id,
                r.estado,
                a.estado AS estado_asignacion

            FROM inventario_responsivas r

            LEFT JOIN inventario_asignaciones a
                ON a.id = r.asignacion_id

            WHERE r.id = %s
            FOR UPDATE
            """,
            (responsiva_id,),
        )

        responsiva = cursor.fetchone()

        if not responsiva:
            conexion.rollback()

            return jsonify(
                {"error": "Responsiva no encontrada."}
            ), 404

        if responsiva["estado"] == "Anulada":
            conexion.rollback()

            return jsonify(
                {
                    "error": (
                        "La responsiva ya está anulada."
                    )
                }
            ), 409

        cursor.execute(
            """
            UPDATE inventario_responsivas
            SET
                estado = 'Anulada',
                fecha_anulacion = CURRENT_TIMESTAMP,
                motivo_anulacion = %s
            WHERE id = %s
            """,
            (
                motivo,
                responsiva_id,
            ),
        )

        if (
            responsiva.get("estado_asignacion")
            == "Activa"
        ):
            cursor.execute(
                """
                UPDATE inventario_equipos
                SET responsiva_estado = 'Pendiente'
                WHERE id = %s
                """,
                (responsiva["equipo_id"],),
            )

        conexion.commit()

        return jsonify(
            {
                "message": (
                    "Responsiva anulada correctamente."
                ),
                "id": responsiva_id,
                "estado": "Anulada",
            }
        )

    except Exception as error:
        conexion.rollback()
        print("Error al anular responsiva:", error)

        return jsonify(
            {
                "error": (
                    "No se pudo anular la responsiva."
                ),
                "detalle": str(error),
            }
        ), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# GET: DESCARGAR RESPONSIVA EN PDF
# ============================================================

@responsivas_bp.route(
    "/<int:responsiva_id>/pdf",
    methods=["GET"],
)
def descargar_responsiva_pdf(responsiva_id):
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT
                r.id,
                r.folio,
                r.estado,
                r.fecha_generacion,
                r.observaciones,

                e.numero_inventario,
                e.descripcion AS equipo,
                e.categoria,
                e.marca,
                e.modelo,
                e.numero_serie,
                e.funcionamiento,
                e.extras,
                e.empresa AS empresa_equipo,

                c.nombre,
                c.apellido_paterno,
                c.apellido_materno,
                c.puesto,
                c.empresa AS empresa_colaborador

            FROM inventario_responsivas r

            INNER JOIN inventario_equipos e
                ON e.id = r.equipo_id

            LEFT JOIN inventario_colaboradores c
                ON c.id = r.colaborador_id

            WHERE r.id = %s
            LIMIT 1
            """,
            (responsiva_id,),
        )

        responsiva = cursor.fetchone()

        if not responsiva:
            return jsonify(
                {"error": "Responsiva no encontrada."}
            ), 404

        nombre_colaborador = nombre_completo(
            responsiva
        ) or "Sin nombre registrado"

        empresa_original = (
            responsiva.get("empresa_colaborador")
            or responsiva.get("empresa_equipo")
            or "ELITE BIKE"
        ).strip()

        empresa_normalizada = (
            empresa_original.upper()
        )

        if "GARNIER" in empresa_normalizada:
            nombre_logo = (
                "logo_garnier_sports.png"
            )
            nombre_empresa = (
                "GARNIER SPORTS S.A. DE C.V."
            )
        elif "ELITE" in empresa_normalizada:
            nombre_logo = "logo_elite_bike.png"
            nombre_empresa = (
                "ELITE BIKE S.A. DE C.V."
            )
        else:
            nombre_logo = "logo_elite_bike.png"
            nombre_empresa = empresa_original

        puesto = (
            responsiva.get("puesto")
            or "Sin puesto registrado"
        )

        folio = (
            responsiva.get("folio")
            or f"RESP-{responsiva_id}"
        )

        estado = (
            responsiva.get("estado")
            or "Pendiente"
        )

        fecha_generacion = (
            responsiva.get("fecha_generacion")
            or datetime.now()
        )

        meses_espanol = {
            1: "enero",
            2: "febrero",
            3: "marzo",
            4: "abril",
            5: "mayo",
            6: "junio",
            7: "julio",
            8: "agosto",
            9: "septiembre",
            10: "octubre",
            11: "noviembre",
            12: "diciembre",
        }

        fecha_pdf = (
            f"{fecha_generacion.day:02d} de "
            f"{meses_espanol[fecha_generacion.month]} "
            f"de {fecha_generacion.year}"
        )

        ruta_raiz_backend = os.path.dirname(
            os.path.dirname(
                os.path.abspath(__file__)
            )
        )

        ruta_logo = os.path.join(
            ruta_raiz_backend,
            "static",
            "images",
            nombre_logo,
        )

        buffer = BytesIO()

        documento = SimpleDocTemplate(
            buffer,
            pagesize=LETTER,
            rightMargin=2.0 * cm,
            leftMargin=2.0 * cm,
            topMargin=1.5 * cm,
            bottomMargin=1.6 * cm,
            title=folio,
            author="Inventario IT",
        )

        estilos_base = getSampleStyleSheet()

        estilo_normal = ParagraphStyle(
            "NormalResponsiva",
            parent=estilos_base["Normal"],
            fontName="Helvetica",
            fontSize=9.2,
            leading=12,
            textColor=colors.HexColor("#171717"),
            alignment=TA_LEFT,
        )

        estilo_titulo = ParagraphStyle(
            "TituloResponsiva",
            parent=estilos_base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=14,
            leading=17,
            textColor=colors.HexColor("#161616"),
            alignment=TA_CENTER,
            spaceAfter=8,
        )

        estilo_subtitulo = ParagraphStyle(
            "SubtituloResponsiva",
            parent=estilos_base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=10.5,
            leading=13,
            textColor=colors.HexColor("#161616"),
            spaceBefore=7,
            spaceAfter=7,
        )

        estilo_encabezado = ParagraphStyle(
            "EncabezadoEmpresa",
            parent=estilo_normal,
            fontName="Helvetica-Bold",
            fontSize=9.6,
            leading=12,
        )

        estilo_derecha = ParagraphStyle(
            "TextoDerecha",
            parent=estilo_normal,
            alignment=TA_RIGHT,
        )

        estilo_centro = ParagraphStyle(
            "TextoCentro",
            parent=estilo_normal,
            alignment=TA_CENTER,
        )

        contenido = []
        logo = ""

        if os.path.exists(ruta_logo):
            try:
                logo = Image(
                    ruta_logo,
                    width=3.4 * cm,
                    height=1.05 * cm,
                )
                logo.hAlign = "LEFT"
            except Exception as error_logo:
                print(
                    "No se pudo cargar el logo:",
                    error_logo,
                )
                logo = ""

        encabezado = Table(
            [
                [
                    logo,
                    Paragraph(
                        f"<b>{escape(nombre_empresa)}</b>",
                        estilo_encabezado,
                    ),
                    Paragraph(
                        (
                            f"<b>Folio:</b> "
                            f"{escape(folio)}<br/>"
                            f"<b>Estado:</b> "
                            f"{escape(estado)}"
                        ),
                        estilo_derecha,
                    ),
                ]
            ],
            colWidths=[
                3.8 * cm,
                7.0 * cm,
                6.2 * cm,
            ],
        )

        encabezado.setStyle(
            TableStyle(
                [
                    (
                        "VALIGN",
                        (0, 0),
                        (-1, -1),
                        "MIDDLE",
                    ),
                    (
                        "LEFTPADDING",
                        (0, 0),
                        (-1, -1),
                        3,
                    ),
                    (
                        "RIGHTPADDING",
                        (0, 0),
                        (-1, -1),
                        3,
                    ),
                    (
                        "BOTTOMPADDING",
                        (0, 0),
                        (-1, -1),
                        9,
                    ),
                    (
                        "LINEBELOW",
                        (0, 0),
                        (-1, -1),
                        1,
                        colors.HexColor("#ef6c23"),
                    ),
                ]
            )
        )

        contenido.append(encabezado)
        contenido.append(Spacer(1, 0.42 * cm))

        contenido.append(
            Paragraph(
                "CARTA RESPONSIVA DEL DISPOSITIVO",
                estilo_titulo,
            )
        )

        datos_fecha = Table(
            [
                [
                    Paragraph(
                        (
                            "<b>Lugar:</b> "
                            "Torreón, Coahuila"
                        ),
                        estilo_normal,
                    ),
                    Paragraph(
                        (
                            f"<b>Fecha:</b> "
                            f"{escape(fecha_pdf)}"
                        ),
                        estilo_derecha,
                    ),
                ]
            ],
            colWidths=[
                8.5 * cm,
                8.5 * cm,
            ],
        )

        datos_fecha.setStyle(
            TableStyle(
                [
                    (
                        "LEFTPADDING",
                        (0, 0),
                        (-1, -1),
                        0,
                    ),
                    (
                        "RIGHTPADDING",
                        (0, 0),
                        (-1, -1),
                        0,
                    ),
                ]
            )
        )

        contenido.append(datos_fecha)
        contenido.append(Spacer(1, 0.35 * cm))

        contenido.append(
            Paragraph(
                (
                    "Se hace entrega del equipo descrito "
                    "en el presente documento al colaborador, "
                    "quien lo recibe para el desempeño de sus "
                    "actividades laborales, quedando bajo su "
                    "resguardo y responsabilidad hasta su "
                    "devolución formal."
                ),
                estilo_normal,
            )
        )

        contenido.append(Spacer(1, 0.30 * cm))

        contenido.append(
            Paragraph(
                "DATOS DEL COLABORADOR",
                estilo_subtitulo,
            )
        )

        tabla_colaborador = Table(
            [
                [
                    Paragraph(
                        "<b>Nombre</b>",
                        estilo_normal,
                    ),
                    Paragraph(
                        escape(nombre_colaborador),
                        estilo_normal,
                    ),
                ],
                [
                    Paragraph(
                        "<b>Puesto</b>",
                        estilo_normal,
                    ),
                    Paragraph(
                        escape(str(puesto)),
                        estilo_normal,
                    ),
                ],
                [
                    Paragraph(
                        "<b>Empresa</b>",
                        estilo_normal,
                    ),
                    Paragraph(
                        escape(nombre_empresa),
                        estilo_normal,
                    ),
                ],
            ],
            colWidths=[
                4.1 * cm,
                12.9 * cm,
            ],
        )

        aplicar_estilo_tabla(tabla_colaborador)
        contenido.append(tabla_colaborador)

        contenido.append(
            Paragraph(
                "DATOS DEL EQUIPO",
                estilo_subtitulo,
            )
        )

        datos_equipo = [
            (
                "No. de inventario",
                responsiva.get("numero_inventario")
                or "Sin registro",
            ),
            (
                "Equipo",
                responsiva.get("equipo")
                or "Sin descripción",
            ),
            (
                "Categoría",
                responsiva.get("categoria")
                or "Sin categoría",
            ),
            (
                "Marca",
                responsiva.get("marca")
                or "Sin marca",
            ),
            (
                "Modelo",
                responsiva.get("modelo")
                or "Sin modelo",
            ),
            (
                "Número de serie",
                responsiva.get("numero_serie")
                or "Sin número de serie",
            ),
            (
                "Funcionamiento",
                responsiva.get("funcionamiento")
                or "Sin información",
            ),
            (
                "Accesorios / extras",
                responsiva.get("extras")
                or "Sin accesorios registrados",
            ),
        ]

        filas_equipo = [
            [
                Paragraph(
                    f"<b>{escape(etiqueta)}</b>",
                    estilo_normal,
                ),
                Paragraph(
                    escape(str(valor)),
                    estilo_normal,
                ),
            ]
            for etiqueta, valor in datos_equipo
        ]

        tabla_equipo = Table(
            filas_equipo,
            colWidths=[
                4.1 * cm,
                12.9 * cm,
            ],
        )

        aplicar_estilo_tabla(tabla_equipo)
        contenido.append(tabla_equipo)

        contenido.append(
            Paragraph(
                "CONDICIONES DE RESGUARDO",
                estilo_subtitulo,
            )
        )

        condiciones = [
            "Hacer uso adecuado del equipo entregado.",
            "Mantener el equipo en buenas condiciones.",
            (
                "Reportar inmediatamente cualquier falla, "
                "daño, pérdida o robo."
            ),
            (
                "No transferir el equipo a terceros sin "
                "autorización del área de Sistemas."
            ),
            (
                "No modificar el hardware, software o "
                "configuración sin autorización."
            ),
            (
                "Entregar el equipo y sus accesorios cuando "
                "la empresa lo solicite."
            ),
        ]

        for condicion in condiciones:
            contenido.append(
                Paragraph(
                    f"• {escape(condicion)}",
                    estilo_normal,
                )
            )

        contenido.append(Spacer(1, 0.20 * cm))

        contenido.append(
            Paragraph(
                (
                    "El equipo anteriormente descrito queda "
                    "bajo responsabilidad del colaborador hasta "
                    "que su devolución quede formalmente "
                    "registrada en el sistema de Inventario IT."
                ),
                estilo_normal,
            )
        )

        observaciones = (
            responsiva.get("observaciones")
            or ""
        ).strip()

        if observaciones:
            contenido.append(
                Paragraph(
                    "OBSERVACIONES",
                    estilo_subtitulo,
                )
            )
            contenido.append(
                Paragraph(
                    escape(observaciones),
                    estilo_normal,
                )
            )

        # ----------------------------------------------------
        # FIRMAS
        # ----------------------------------------------------

        contenido.append(Spacer(1, 0.55 * cm))

        tabla_firmas = Table(
            [
                ["", ""],
                [
                    Paragraph("________________________________", estilo_centro),
                    Paragraph("________________________________", estilo_centro),
                ],
                [
                    Paragraph("<b>ENTREGA</b>", estilo_centro),
                    Paragraph("<b>RECIBE</b>", estilo_centro),
                ],
                [
                    Paragraph("Nombre y firma - Área de Sistemas", estilo_centro),
                    Paragraph(escape(nombre_colaborador), estilo_centro),
                ],
            ],
            colWidths=[8.5 * cm, 8.5 * cm],
            rowHeights=[1.15 * cm, 0.32 * cm, 0.38 * cm, 0.45 * cm],
        )

        tabla_firmas.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )

        contenido.append(tabla_firmas)

        # ----------------------------------------------------
        # GENERAR Y ENVIAR PDF
        # ----------------------------------------------------

        documento.build(contenido)
        buffer.seek(0)

        nombre_archivo = f"{folio.replace('/', '-')}.pdf"

        return send_file(
            buffer,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=nombre_archivo,
        )

    except Exception as error:
        print(
            "Error al generar PDF de responsiva:",
            error,
        )

        return jsonify(
            {
                "error": (
                    "No se pudo generar el PDF "
                    "de la responsiva."
                ),
                "detalle": str(error),
            }
        ), 500

    finally:
        cursor.close()
        conexion.close()