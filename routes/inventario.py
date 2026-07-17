from flask import Blueprint, jsonify, request
from db_conexion import obtener_conexion

inventario_bp = Blueprint(
    "inventario",
    __name__,
    url_prefix="/api/inventario"
)


def limpiar_valor(valor):
    """
    Convierte cadenas vacías en None para guardarlas como NULL
    en la base de datos.
    """
    if isinstance(valor, str):
        valor = valor.strip()
        return valor if valor else None

    return valor


def formatear_equipo(row):
    """
    Convierte los nombres de columnas de MySQL
    al formato utilizado por Angular.
    """
    return {
        "id": row["id"],
        "inventario": row["numero_inventario"] or "",
        "fechaRegistro": (
            str(row["fecha_registro"])
            if row["fecha_registro"]
            else ""
        ),
        "empresa": row["empresa"] or "ELITE BIKE",
        "departamento": row["departamento"] or "",
        "responsable": row["responsable"] or "",
        "cargo": row["cargo"] or "",
        "categoria": row["categoria"] or "",
        "nombre": row["descripcion"] or "",
        "marca": row["marca"] or "",
        "modelo": row["modelo"] or "",
        "serie": row["numero_serie"] or "",
        "funcionamiento": row["funcionamiento"] or "",
        "estado": row["estado"] or "Disponible",
        "ubicacion": row["ubicacion"] or "",
        "imagenUrl": row["imagen_url"] or "",
        "comentariosSistemas": row["comentarios_sistemas"] or "",
        "extras": row["extras"] or "",
        "responsiva": row["responsiva_estado"] or "No aplica"
    }


@inventario_bp.route("/equipos", methods=["GET"])
def listar_equipos():
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT
                id,
                numero_inventario,
                fecha_registro,
                empresa,
                departamento,
                responsable,
                cargo,
                categoria,
                descripcion,
                marca,
                modelo,
                numero_serie,
                funcionamiento,
                estado,
                ubicacion,
                imagen_url,
                comentarios_sistemas,
                extras,
                responsiva_estado
            FROM inventario_equipos
            ORDER BY id DESC
        """)

        registros = cursor.fetchall()
        equipos = [formatear_equipo(row) for row in registros]

        return jsonify(equipos)

    except Exception as error:
        return jsonify({
            "error": "No se pudieron obtener los equipos",
            "detalle": str(error)
        }), 500

    finally:
        cursor.close()
        conexion.close()


@inventario_bp.route("/equipos/<int:equipo_id>", methods=["GET"])
def obtener_equipo(equipo_id):
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT
                id,
                numero_inventario,
                fecha_registro,
                empresa,
                departamento,
                responsable,
                cargo,
                categoria,
                descripcion,
                marca,
                modelo,
                numero_serie,
                funcionamiento,
                estado,
                ubicacion,
                imagen_url,
                comentarios_sistemas,
                extras,
                responsiva_estado
            FROM inventario_equipos
            WHERE id = %s
        """, (equipo_id,))

        row = cursor.fetchone()

        if not row:
            return jsonify({
                "error": "Equipo no encontrado"
            }), 404

        return jsonify(formatear_equipo(row))

    except Exception as error:
        return jsonify({
            "error": "No se pudo obtener el equipo",
            "detalle": str(error)
        }), 500

    finally:
        cursor.close()
        conexion.close()


@inventario_bp.route("/equipos", methods=["POST"])
def crear_equipo():
    data = request.get_json(silent=True)

    if not data:
        return jsonify({
            "error": "No se recibieron datos"
        }), 400

    numero_inventario = str(data.get("inventario") or "").strip()
    categoria = str(data.get("categoria") or "").strip()
    descripcion = str(data.get("nombre") or "").strip()

    if not numero_inventario or not categoria or not descripcion:
        return jsonify({
            "error": (
                "Los campos inventario, categoría y nombre "
                "son obligatorios"
            )
        }), 400

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:
        cursor.execute("""
            INSERT INTO inventario_equipos (
                numero_inventario,
                fecha_registro,
                empresa,
                departamento,
                responsable,
                cargo,
                categoria,
                descripcion,
                marca,
                modelo,
                numero_serie,
                funcionamiento,
                estado,
                ubicacion,
                imagen_url,
                comentarios_sistemas,
                extras,
                responsiva_estado
            )
            VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s
            )
        """, (
            numero_inventario,
            limpiar_valor(data.get("fechaRegistro")),
            limpiar_valor(data.get("empresa")) or "ELITE BIKE",
            limpiar_valor(data.get("departamento")),
            limpiar_valor(data.get("responsable")),
            limpiar_valor(data.get("cargo")),
            categoria,
            descripcion,
            limpiar_valor(data.get("marca")),
            limpiar_valor(data.get("modelo")),
            limpiar_valor(data.get("serie")),
            limpiar_valor(data.get("funcionamiento")) or "Bueno",
            limpiar_valor(data.get("estado")) or "Disponible",
            limpiar_valor(data.get("ubicacion")),
            limpiar_valor(data.get("imagenUrl")),
            limpiar_valor(data.get("comentariosSistemas")),
            limpiar_valor(data.get("extras")),
            limpiar_valor(data.get("responsiva")) or "No aplica"
        ))

        conexion.commit()
        nuevo_id = cursor.lastrowid

        return jsonify({
            "message": "Equipo registrado correctamente",
            "id": nuevo_id
        }), 201

    except Exception as error:
        conexion.rollback()

        return jsonify({
            "error": "No se pudo registrar el equipo",
            "detalle": str(error)
        }), 500

    finally:
        cursor.close()
        conexion.close()


@inventario_bp.route("/equipos/<int:equipo_id>", methods=["PUT"])
def actualizar_equipo(equipo_id):
    data = request.get_json(silent=True)

    if not data:
        return jsonify({
            "error": "No se recibieron datos"
        }), 400

    numero_inventario = str(data.get("inventario") or "").strip()
    categoria = str(data.get("categoria") or "").strip()
    descripcion = str(data.get("nombre") or "").strip()

    if not numero_inventario or not categoria or not descripcion:
        return jsonify({
            "error": (
                "Los campos inventario, categoría y nombre "
                "son obligatorios"
            )
        }), 400

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:
        # Primero se comprueba que el equipo exista.
        # Esto evita depender de rowcount, que puede ser 0 cuando
        # se guardan exactamente los mismos valores.
        cursor.execute("""
            SELECT id
            FROM inventario_equipos
            WHERE id = %s
        """, (equipo_id,))

        equipo_existente = cursor.fetchone()

        if not equipo_existente:
            return jsonify({
                "error": "Equipo no encontrado"
            }), 404

        cursor.execute("""
            UPDATE inventario_equipos
            SET
                numero_inventario = %s,
                fecha_registro = %s,
                empresa = %s,
                departamento = %s,
                responsable = %s,
                cargo = %s,
                categoria = %s,
                descripcion = %s,
                marca = %s,
                modelo = %s,
                numero_serie = %s,
                funcionamiento = %s,
                estado = %s,
                ubicacion = %s,
                imagen_url = %s,
                comentarios_sistemas = %s,
                extras = %s,
                responsiva_estado = %s
            WHERE id = %s
        """, (
            numero_inventario,
            limpiar_valor(data.get("fechaRegistro")),
            limpiar_valor(data.get("empresa")) or "ELITE BIKE",
            limpiar_valor(data.get("departamento")),
            limpiar_valor(data.get("responsable")),
            limpiar_valor(data.get("cargo")),
            categoria,
            descripcion,
            limpiar_valor(data.get("marca")),
            limpiar_valor(data.get("modelo")),
            limpiar_valor(data.get("serie")),
            limpiar_valor(data.get("funcionamiento")) or "Bueno",
            limpiar_valor(data.get("estado")) or "Disponible",
            limpiar_valor(data.get("ubicacion")),
            limpiar_valor(data.get("imagenUrl")),
            limpiar_valor(data.get("comentariosSistemas")),
            limpiar_valor(data.get("extras")),
            limpiar_valor(data.get("responsiva")) or "No aplica",
            equipo_id
        ))

        conexion.commit()

        return jsonify({
            "message": "Equipo actualizado correctamente",
            "id": equipo_id
        })

    except Exception as error:
        conexion.rollback()

        return jsonify({
            "error": "No se pudo actualizar el equipo",
            "detalle": str(error)
        }), 500

    finally:
        cursor.close()
        conexion.close()


@inventario_bp.route("/equipos/<int:equipo_id>", methods=["DELETE"])
def eliminar_equipo(equipo_id):
    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:
        cursor.execute("""
            DELETE FROM inventario_equipos
            WHERE id = %s
        """, (equipo_id,))

        if cursor.rowcount == 0:
            conexion.rollback()

            return jsonify({
                "error": "Equipo no encontrado"
            }), 404

        conexion.commit()

        return jsonify({
            "message": "Equipo eliminado correctamente",
            "id": equipo_id
        })

    except Exception as error:
        conexion.rollback()

        return jsonify({
            "error": "No se pudo eliminar el equipo",
            "detalle": str(error)
        }), 500

    finally:
        cursor.close()
        conexion.close()