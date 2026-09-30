from flask import Blueprint, request, jsonify
from db_conexion import obtener_conexion
from utils.auth_decorators import requiere_autenticacion

metas_bp = Blueprint('metas', __name__, url_prefix='')

@metas_bp.route('/metas', methods=['GET'])
@requiere_autenticacion
def listar_metas():
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("SELECT * FROM niveles_distribuidor ORDER BY id")
        resultados = cursor.fetchall()

        if resultados:
            return jsonify(resultados), 200
        else:
            return jsonify({"mensaje": "No hay niveles registrados"}), 404
    except Exception as e:
        print("Error al obtener niveles:", str(e))
        return jsonify({"error": "Error al obtener niveles"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@metas_bp.route('/metas/agregar', methods=['POST'])
@requiere_autenticacion
def agregar_meta():
    data = request.get_json()
    nivel = data.get('nivel')
    cs = data.get('compromiso_scott')
    sy = data.get('compromiso_syncros')
    ap = data.get('compromiso_apparel')
    vi = data.get('compromiso_vittoria')

    if not all([nivel, cs, sy, ap, vi]):
        return jsonify({"error": "Todos los campos son obligatorios"}), 400

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:
        cursor.execute("SELECT id FROM niveles_distribuidor WHERE nivel = %s", (nivel,))
        if cursor.fetchone():
            return jsonify({"error": "Ese nivel ya existe"}), 409

        cursor.execute("""
            INSERT INTO niveles_distribuidor (nivel, compromiso_scott, compromiso_syncros, compromiso_apparel, compromiso_vittoria)
            VALUES (%s, %s, %s, %s, %s)
        """, (nivel, cs, sy, ap, vi))
        conexion.commit()

        return jsonify({"mensaje": "Nivel agregado correctamente"}), 201
    except Exception as e:
        print("Error al agregar nivel:", str(e))
        return jsonify({"error": "Error al agregar nivel"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@metas_bp.route('/metas/editar/<int:id_meta>', methods=['PUT'])
@requiere_autenticacion
def editar_meta(id_meta):
    data = request.get_json()
    nivel = data.get('nivel')
    cs = data.get('compromiso_scott')
    sy = data.get('compromiso_syncros')
    ap = data.get('compromiso_apparel')
    vi = data.get('compromiso_vittoria')

    if not all([nivel, cs, sy, ap, vi]):
        return jsonify({"error": "Todos los campos son obligatorios"}), 400

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:
        cursor.execute("SELECT id FROM niveles_distribuidor WHERE id = %s", (id_meta,))
        if cursor.fetchone() is None:
            return jsonify({"error": "Nivel no encontrado"}), 404

        cursor.execute("""
            UPDATE niveles_distribuidor
            SET nivel = %s,
                compromiso_scott = %s,
                compromiso_syncros = %s,
                compromiso_apparel = %s,
                compromiso_vittoria = %s
            WHERE id = %s
        """, (nivel, cs, sy, ap, vi, id_meta))
        conexion.commit()

        return jsonify({"mensaje": "Nivel actualizado correctamente"}), 200
    except Exception as e:
        print("Error al editar nivel:", str(e))
        return jsonify({"error": "Error al editar nivel"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@metas_bp.route('/metas/eliminar/<int:id_meta>', methods=['DELETE'])
@requiere_autenticacion
def eliminar_meta(id_meta):
    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:
        cursor.execute("SELECT id FROM niveles_distribuidor WHERE id = %s", (id_meta,))
        if cursor.fetchone() is None:
            return jsonify({"error": "Nivel no encontrado"}), 404

        cursor.execute("DELETE FROM niveles_distribuidor WHERE id = %s", (id_meta,))
        conexion.commit()

        return jsonify({"mensaje": "Nivel eliminado correctamente"}), 200
    except Exception as e:
        print("Error al eliminar nivel:", str(e))
        return jsonify({"error": "Error al eliminar nivel"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()
