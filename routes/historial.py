import csv

from datetime import datetime
from io import BytesIO, StringIO

from flask import Blueprint, jsonify, request, send_file

from db_conexion import obtener_conexion


historial_bp = Blueprint(
    "historial",
    __name__,
    url_prefix="/api/inventario/historial"
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


def fecha_a_texto(valor):
    if not valor:
        return ""

    if isinstance(valor, datetime):
        return valor.isoformat(sep=" ")

    return str(valor)


def generar_folio_movimiento(movimiento_id):
    return f"MOV-{int(movimiento_id):06d}"


def validar_fecha(valor, nombre_campo):
    if not valor:
        return None

    try:
        datetime.strptime(valor, "%Y-%m-%d")
        return valor

    except ValueError as error:
        raise ValueError(
            f"El campo {nombre_campo} debe tener el formato AAAA-MM-DD."
        ) from error


def formatear_movimiento(row):
    return {
        "id": row["id"],
        "folio": generar_folio_movimiento(row["id"]),
        "equipoId": row.get("equipo_id"),
        "tipoMovimiento": row.get("tipo_movimiento") or "Sin tipo",
        "descripcion": row.get("descripcion") or "",
        "responsableAnterior": row.get("responsable_anterior") or "",
        "responsableNuevo": row.get("responsable_nuevo") or "",
        "usuarioRegistro": row.get("usuario_registro") or "Sistema",
        "fechaMovimiento": fecha_a_texto(row.get("fecha_movimiento")),
        "equipo": {
            "id": row.get("equipo_id"),
            "inventario": row.get("numero_inventario") or "",
            "nombre": row.get("descripcion_equipo") or "",
            "categoria": row.get("categoria_equipo") or "",
            "marca": row.get("marca_equipo") or "",
            "modelo": row.get("modelo_equipo") or "",
            "serie": row.get("numero_serie_equipo") or "",
            "empresa": row.get("empresa_equipo") or "",
            "estadoActual": row.get("estado_actual_equipo") or "",
            "funcionamiento": row.get("funcionamiento_equipo") or "",
            "ubicacion": row.get("ubicacion_equipo") or "",
            "departamentoActual": row.get("departamento_actual_equipo") or "",
            "responsableActual": row.get("responsable_actual_equipo") or ""
        }
    }


def consulta_base():
    return """
        SELECT
            m.id,
            m.equipo_id,
            m.tipo_movimiento,
            m.descripcion,
            m.responsable_anterior,
            m.responsable_nuevo,
            m.usuario_registro,
            m.fecha_movimiento,

            e.numero_inventario,
            e.descripcion AS descripcion_equipo,
            e.categoria AS categoria_equipo,
            e.marca AS marca_equipo,
            e.modelo AS modelo_equipo,
            e.numero_serie AS numero_serie_equipo,
            e.empresa AS empresa_equipo,
            e.estado AS estado_actual_equipo,
            e.funcionamiento AS funcionamiento_equipo,
            e.ubicacion AS ubicacion_equipo,
            e.departamento AS departamento_actual_equipo,
            e.responsable AS responsable_actual_equipo

        FROM inventario_movimientos m

        LEFT JOIN inventario_equipos e
            ON e.id = m.equipo_id
    """


def construir_filtros():
    busqueda = limpiar_texto(request.args.get("busqueda"))
    tipo = limpiar_texto(request.args.get("tipo"))
    empresa = limpiar_texto(request.args.get("empresa"))
    fecha_inicio = validar_fecha(
        limpiar_texto(request.args.get("fechaInicio")),
        "fecha inicial"
    )
    fecha_fin = validar_fecha(
        limpiar_texto(request.args.get("fechaFin")),
        "fecha final"
    )

    condiciones = []
    parametros = []

    if busqueda:
        termino = f"%{busqueda}%"

        condiciones.append("""
            (
                CONCAT('MOV-', LPAD(m.id, 6, '0')) LIKE %s
                OR e.numero_inventario LIKE %s
                OR e.descripcion LIKE %s
                OR e.categoria LIKE %s
                OR e.marca LIKE %s
                OR e.modelo LIKE %s
                OR e.numero_serie LIKE %s
                OR m.tipo_movimiento LIKE %s
                OR m.descripcion LIKE %s
                OR m.responsable_anterior LIKE %s
                OR m.responsable_nuevo LIKE %s
                OR m.usuario_registro LIKE %s
            )
        """)

        parametros.extend([termino] * 12)

    if tipo and tipo != "Todos":
        condiciones.append("m.tipo_movimiento = %s")
        parametros.append(tipo)

    if empresa and empresa != "Todas":
        condiciones.append("e.empresa = %s")
        parametros.append(empresa)

    if fecha_inicio:
        condiciones.append("DATE(m.fecha_movimiento) >= %s")
        parametros.append(fecha_inicio)

    if fecha_fin:
        condiciones.append("DATE(m.fecha_movimiento) <= %s")
        parametros.append(fecha_fin)

    clausula_where = ""

    if condiciones:
        clausula_where = " WHERE " + " AND ".join(condiciones)

    return clausula_where, parametros


# ============================================================
# GET: LISTAR MOVIMIENTOS
# ============================================================

@historial_bp.route("", methods=["GET"])
@historial_bp.route("/", methods=["GET"])
def listar_movimientos():
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        clausula_where, parametros = construir_filtros()

        consulta = consulta_base() + clausula_where + """
            ORDER BY m.fecha_movimiento DESC, m.id DESC
        """

        cursor.execute(consulta, tuple(parametros))
        registros = cursor.fetchall()

        movimientos = [
            formatear_movimiento(registro)
            for registro in registros
        ]

        return jsonify(movimientos)

    except ValueError as error:
        return jsonify({
            "error": str(error)
        }), 400

    except Exception as error:
        print("Error al listar movimientos:", error)

        return jsonify({
            "error": "No se pudo cargar el historial de movimientos.",
            "detalle": str(error)
        }), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# GET: ESTADÍSTICAS DEL HISTORIAL
# ============================================================

@historial_bp.route("/estadisticas", methods=["GET"])
def obtener_estadisticas():
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        clausula_where, parametros = construir_filtros()

        consulta = """
            SELECT
                COUNT(*) AS total,

                SUM(
                    CASE
                        WHEN m.tipo_movimiento = 'Asignación'
                        THEN 1
                        ELSE 0
                    END
                ) AS asignaciones,

                SUM(
                    CASE
                        WHEN m.tipo_movimiento = 'Devolución'
                        THEN 1
                        ELSE 0
                    END
                ) AS devoluciones,

                SUM(
                    CASE
                        WHEN m.tipo_movimiento NOT IN (
                            'Asignación',
                            'Devolución'
                        )
                        THEN 1
                        ELSE 0
                    END
                ) AS otros

            FROM inventario_movimientos m

            LEFT JOIN inventario_equipos e
                ON e.id = m.equipo_id
        """ + clausula_where

        cursor.execute(consulta, tuple(parametros))
        resultado = cursor.fetchone() or {}

        return jsonify({
            "total": int(resultado.get("total") or 0),
            "asignaciones": int(resultado.get("asignaciones") or 0),
            "devoluciones": int(resultado.get("devoluciones") or 0),
            "otros": int(resultado.get("otros") or 0)
        })

    except ValueError as error:
        return jsonify({
            "error": str(error)
        }), 400

    except Exception as error:
        print("Error al obtener estadísticas del historial:", error)

        return jsonify({
            "error": "No se pudieron obtener las estadísticas del historial.",
            "detalle": str(error)
        }), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# GET: CATÁLOGOS PARA FILTROS
# ============================================================

@historial_bp.route("/catalogos", methods=["GET"])
def obtener_catalogos():
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT DISTINCT tipo_movimiento
            FROM inventario_movimientos
            WHERE tipo_movimiento IS NOT NULL
              AND TRIM(tipo_movimiento) <> ''
            ORDER BY tipo_movimiento
        """)

        registros_tipos = cursor.fetchall()

        cursor.execute("""
            SELECT DISTINCT empresa
            FROM inventario_equipos
            WHERE empresa IS NOT NULL
              AND TRIM(empresa) <> ''
            ORDER BY empresa
        """)

        registros_empresas = cursor.fetchall()

        tipos = [
            registro["tipo_movimiento"]
            for registro in registros_tipos
        ]

        empresas = [
            registro["empresa"]
            for registro in registros_empresas
        ]

        return jsonify({
            "tipos": tipos,
            "empresas": empresas
        })

    except Exception as error:
        print("Error al obtener catálogos del historial:", error)

        return jsonify({
            "error": "No se pudieron obtener los catálogos del historial.",
            "detalle": str(error)
        }), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# GET: DETALLE DE UN MOVIMIENTO
# ============================================================

@historial_bp.route("/<int:movimiento_id>", methods=["GET"])
def obtener_movimiento(movimiento_id):
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        consulta = consulta_base() + """
            WHERE m.id = %s
            LIMIT 1
        """

        cursor.execute(consulta, (movimiento_id,))
        registro = cursor.fetchone()

        if not registro:
            return jsonify({
                "error": "Movimiento no encontrado."
            }), 404

        return jsonify(
            formatear_movimiento(registro)
        )

    except Exception as error:
        print("Error al obtener movimiento:", error)

        return jsonify({
            "error": "No se pudo obtener el detalle del movimiento.",
            "detalle": str(error)
        }), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# GET: EXPORTAR HISTORIAL FILTRADO A CSV
# ============================================================

@historial_bp.route("/exportar", methods=["GET"])
def exportar_historial():
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        clausula_where, parametros = construir_filtros()

        consulta = consulta_base() + clausula_where + """
            ORDER BY m.fecha_movimiento DESC, m.id DESC
        """

        cursor.execute(consulta, tuple(parametros))
        registros = cursor.fetchall()

        archivo_texto = StringIO()
        escritor = csv.writer(archivo_texto)

        escritor.writerow([
            "Folio",
            "Fecha del movimiento",
            "Tipo de movimiento",
            "Número de inventario",
            "Equipo",
            "Categoría",
            "Marca",
            "Modelo",
            "Número de serie",
            "Empresa",
            "Responsable anterior",
            "Responsable nuevo",
            "Responsable actual",
            "Estado actual",
            "Ubicación",
            "Descripción",
            "Usuario que registró"
        ])

        for registro in registros:
            escritor.writerow([
                generar_folio_movimiento(registro["id"]),
                fecha_a_texto(registro.get("fecha_movimiento")),
                registro.get("tipo_movimiento") or "",
                registro.get("numero_inventario") or "",
                registro.get("descripcion_equipo") or "",
                registro.get("categoria_equipo") or "",
                registro.get("marca_equipo") or "",
                registro.get("modelo_equipo") or "",
                registro.get("numero_serie_equipo") or "",
                registro.get("empresa_equipo") or "",
                registro.get("responsable_anterior") or "",
                registro.get("responsable_nuevo") or "",
                registro.get("responsable_actual_equipo") or "",
                registro.get("estado_actual_equipo") or "",
                registro.get("ubicacion_equipo") or "",
                registro.get("descripcion") or "",
                registro.get("usuario_registro") or "Sistema"
            ])

        contenido = archivo_texto.getvalue().encode("utf-8-sig")
        archivo_binario = BytesIO(contenido)
        archivo_binario.seek(0)

        fecha_archivo = datetime.now().strftime("%Y%m%d_%H%M%S")
        nombre_archivo = f"historial_movimientos_{fecha_archivo}.csv"

        return send_file(
            archivo_binario,
            mimetype="text/csv; charset=utf-8",
            as_attachment=True,
            download_name=nombre_archivo
        )

    except ValueError as error:
        return jsonify({
            "error": str(error)
        }), 400

    except Exception as error:
        print("Error al exportar historial:", error)

        return jsonify({
            "error": "No se pudo exportar el historial.",
            "detalle": str(error)
        }), 500

    finally:
        cursor.close()
        conexion.close()