from flask import Blueprint, jsonify, request
import logging
from db_conexion import obtener_conexion
import jwt
from datetime import date, datetime

from utils.jwt_utils import verificar_token
from functools import wraps

# Crear el Blueprint para integrales
integrales_bp = Blueprint('integrales', __name__, url_prefix='')

@integrales_bp.route('/integrales/grupos', methods=['GET'])
def obtener_grupos():
    """Obtener todos los grupos de clientes"""
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("SELECT id, nombre_grupo FROM grupo_clientes ORDER BY nombre_grupo")
        grupos = cursor.fetchall()
        return jsonify(grupos), 200
    except Exception as e:
        logging.exception("Error al obtener grupos")
        return jsonify({"error": "Error al obtener grupos"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@integrales_bp.route('/integrales/agregar', methods=['POST'])
def agregar_grupo():
    """Agregar un nuevo grupo de clientes"""
    data = request.get_json()
    nombre_grupo = data.get('nombre_grupo')

    if not nombre_grupo:
        return jsonify({"error": "El nombre del grupo es obligatorio"}), 400

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:
        # Validar que no exista un grupo con el mismo nombre
        cursor.execute("SELECT id FROM grupo_clientes WHERE nombre_grupo = %s", (nombre_grupo,))
        existente = cursor.fetchone()

        if existente:
            return jsonify({"error": "Ya existe un grupo con ese nombre"}), 409

        # Insertar el nuevo grupo
        query = "INSERT INTO grupo_clientes (nombre_grupo) VALUES (%s)"
        cursor.execute(query, (nombre_grupo,))
        conexion.commit()

        return jsonify({"mensaje": "Grupo agregado exitosamente", "id": cursor.lastrowid}), 201
    except Exception as e:
        logging.exception("Error al agregar grupo")
        return jsonify({"error": "Error al agregar grupo"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@integrales_bp.route('/integrales/grupos/editar/<int:id_grupo>', methods=['PUT'])
def editar_grupo(id_grupo):
    """Editar un grupo existente"""
    data = request.get_json()
    nombre_grupo = data.get('nombre_grupo')

    if not nombre_grupo:
        return jsonify({"error": "El nombre del grupo es obligatorio"}), 400

    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:
        # Verificar que el grupo exista
        cursor.execute("SELECT id FROM grupo_clientes WHERE id = %s", (id_grupo,))
        if cursor.fetchone() is None:
            return jsonify({"error": "Grupo no encontrado"}), 404

        # Validar que no exista otro grupo con el mismo nombre
        cursor.execute("SELECT id FROM grupo_clientes WHERE nombre_grupo = %s AND id != %s", (nombre_grupo, id_grupo))
        if cursor.fetchone():
            return jsonify({"error": "Ya existe otro grupo con ese nombre"}), 409

        # Actualizar el grupo
        query = "UPDATE grupo_clientes SET nombre_grupo = %s WHERE id = %s"
        cursor.execute(query, (nombre_grupo, id_grupo))
        conexion.commit()

        return jsonify({"mensaje": "Grupo actualizado exitosamente"}), 200
    except Exception as e:
        logging.exception("Error al editar grupo")
        return jsonify({"error": "Error al editar grupo"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@integrales_bp.route('/integrales/grupos/eliminar/<int:id_grupo>', methods=['DELETE'])
def eliminar_grupo(id_grupo):
    """Eliminar un grupo (solo si no tiene clientes asociados)"""
    conexion = obtener_conexion()
    cursor = conexion.cursor()

    try:
        # Verificar que el grupo exista
        cursor.execute("SELECT id FROM grupo_clientes WHERE id = %s", (id_grupo,))
        if cursor.fetchone() is None:
            return jsonify({"error": "Grupo no encontrado"}), 404

        # Verificar que no tenga clientes asociados
        cursor.execute("SELECT id FROM clientes WHERE id_grupo = %s LIMIT 1", (id_grupo,))
        if cursor.fetchone():
            return jsonify({"error": "No se puede eliminar el grupo porque tiene clientes asociados"}), 409

        # Eliminar el grupo
        cursor.execute("DELETE FROM grupo_clientes WHERE id = %s", (id_grupo,))
        conexion.commit()

        return jsonify({"mensaje": "Grupo eliminado correctamente"}), 200
    except Exception as e:
        logging.exception("Error al eliminar grupo")
        return jsonify({"error": "Error al eliminar grupo"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@integrales_bp.route('/integrales/clientes/grupo/<int:id_grupo>', methods=['GET'])
def obtener_clientes_por_grupo(id_grupo):
    """Obtener todos los clientes de un grupo específico"""
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        # Verificar que el grupo exista
        cursor.execute("SELECT nombre_grupo FROM grupo_clientes WHERE id = %s", (id_grupo,))
        grupo = cursor.fetchone()
        if not grupo:
            return jsonify({"error": "Grupo no encontrado"}), 404

        # Obtener clientes del grupo
        cursor.execute("""
            SELECT 
                c.id,
                c.clave,
                c.evac,
                c.nombre_cliente,
                c.nivel,
                c.f_inicio,
                c.f_fin
            FROM clientes c
            WHERE c.id_grupo = %s
            ORDER BY c.nombre_cliente
        """, (id_grupo,))
        
        clientes = cursor.fetchall()
        
        # Convertir fechas a strings
        for cliente in clientes:
            if cliente['f_inicio'] and isinstance(cliente['f_inicio'], (datetime, date)):
                cliente['f_inicio'] = cliente['f_inicio'].strftime('%Y-%m-%d')
            if cliente['f_fin'] and isinstance(cliente['f_fin'], (datetime, date)):
                cliente['f_fin'] = cliente['f_fin'].strftime('%Y-%m-%d')
        
        return jsonify({
            "grupo": grupo['nombre_grupo'],
            "clientes": clientes
        }), 200
    except Exception as e:
        logging.exception("Error al obtener clientes del grupo")
        return jsonify({"error": "Error al obtener clientes del grupo"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@integrales_bp.route('/integrales/clientes/asignar-grupo', methods=['POST'])
def asignar_grupo_cliente():
    """Asignar/remover grupo y mantener metas/avances de Integrales alineados."""
    data = request.get_json() or {}
    id_cliente = data.get('id_cliente')
    id_grupo = data.get('id_grupo')

    if id_cliente is None or id_grupo is None:
        return jsonify({"error": "ID del cliente y ID del grupo son obligatorios"}), 400

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT id, clave, nombre_cliente, nivel, id_grupo
            FROM clientes
            WHERE id = %s
            """,
            (id_cliente,),
        )
        cliente = cursor.fetchone()
        if cliente is None:
            return jsonify({"error": "Cliente no encontrado"}), 404

        grupo_anterior = cliente.get('id_grupo')
        grupo_nuevo = None if int(id_grupo) == 0 else int(id_grupo)

        if grupo_nuevo is not None:
            cursor.execute(
                "SELECT id FROM grupo_clientes WHERE id = %s",
                (grupo_nuevo,),
            )
            if cursor.fetchone() is None:
                return jsonify({"error": "Grupo no encontrado"}), 404

        cursor.execute(
            "UPDATE clientes SET id_grupo = %s WHERE id = %s",
            (grupo_nuevo, id_cliente),
        )

        from services.metas_previo_my27_service import (
            sincronizar_grupos_afectados_my27,
            sincronizar_metas_cliente_my27,
        )

        # El miembro conserva la meta de su propio nivel como 1 sucursal.
        sync_cliente = sincronizar_metas_cliente_my27(
            conexion,
            id_cliente,
            aplicar=True,
        )

        # Recalcular tanto el grupo del que salió como aquel al que entró.
        sync_grupos = sincronizar_grupos_afectados_my27(
            conexion,
            [grupo_anterior, grupo_nuevo],
            aplicar=True,
        )

        # La composición del grupo también cambia sus avances consolidados.
        from routes.monitor_odoo import _recalcular_acumulados_previo

        filas_avances = _recalcular_acumulados_previo(conexion, cursor)

        mensaje = (
            "Grupo removido del cliente exitosamente"
            if grupo_nuevo is None
            else "Grupo asignado al cliente exitosamente"
        )
        return jsonify({
            "mensaje": mensaje,
            "sincronizacion_cliente_my27": sync_cliente,
            "integrales_afectadas": sync_grupos,
            "filas_avances_actualizadas": filas_avances,
        }), 200

    except Exception as e:
        if conexion:
            conexion.rollback()
        logging.exception("Error al asignar grupo al cliente")
        return jsonify({
            "error": "Error al asignar grupo al cliente",
            "detalle": str(e),
        }), 500

    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()
