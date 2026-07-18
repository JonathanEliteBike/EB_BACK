from __future__ import annotations
from flask import Blueprint, jsonify, request, Response
from db_conexion import obtener_conexion
from decimal import Decimal
import json
import os
import re
import redis as _redis_lib
from utils.email_utils import crear_cuerpo_email
from utils.odoo_utils import get_odoo_models, ODOO_DB, ODOO_PASSWORD
from utils.temporada_utils import etiqueta_temporada
import logging
import traceback

caratulas_bp = Blueprint('caratulas', __name__, url_prefix='')

# ── Caché Redis para detalle-compras-odoo (TTL = 30 min) ─────────────────────
_ODOO_PEDIDOS_TTL = 1800         # segundos (30 min)
_REDIS_URL = os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')
try:
    _redis = _redis_lib.from_url(_REDIS_URL, decode_responses=True, socket_connect_timeout=2)
    _redis.ping()
    logging.info('Redis cache activo: %s', _REDIS_URL)
except Exception as _re:
    _redis = None
    logging.warning('Redis no disponible, cache desactivado: %s', _re)


_WARM_WORKERS = 4   # peticiones paralelas a Odoo (no subir de 5 para no saturar)


def _precalentar_claves(claves: list[str], host: str = 'http://localhost:5000') -> None:
    """Carga todos los clientes en Redis usando un pool de threads paralelos."""
    import requests as _req
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _cargar_uno(clave: str) -> str:
        try:
            _req.get(
                f'{host}/detalle-compras-odoo',
                params={'cliente': clave, 'ref_exacta': '1'},
                timeout=120,
            )
            return f'OK:{clave}'
        except Exception as _e:
            return f'ERR:{clave}:{_e}'

    logging.info('Precalentamiento: %d clientes, %d workers paralelos', len(claves), _WARM_WORKERS)
    with ThreadPoolExecutor(max_workers=_WARM_WORKERS) as pool:
        futuros = {pool.submit(_cargar_uno, c): c for c in claves}
        ok = err = 0
        for fut in as_completed(futuros):
            res = fut.result()
            if res.startswith('OK'):
                ok += 1
            else:
                err += 1
                logging.warning('Precalentamiento error: %s', res)
    logging.info('Precalentamiento terminado: %d OK, %d errores', ok, err)


def iniciar_precalentamiento(host: str = 'http://localhost:5000') -> int:
    """Lanza un thread daemon que precalienta Redis para todos los clientes activos."""
    import threading
    try:
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT clave FROM clientes "
            "WHERE clave IS NOT NULL AND clave != '' AND f_inicio IS NOT NULL"
        )
        claves = [r['clave'] for r in cur.fetchall()]
        cur.close()
        conn.close()
    except Exception as _e:
        logging.warning('iniciar_precalentamiento: no se pudo leer clientes: %s', _e)
        return 0

    t = threading.Thread(target=_precalentar_claves, args=(claves, host), daemon=True)
    t.start()
    logging.info('Precalentamiento iniciado para %d clientes', len(claves))
    return len(claves)

@caratulas_bp.route('/precalentar-monitor', methods=['POST'])
def precalentar_monitor():
    """Dispara el pre-calentamiento del cache Redis para todos los clientes."""
    total = iniciar_precalentamiento()
    return jsonify({'status': 'iniciado', 'clientes': total}), 202


@caratulas_bp.route('/caratula_evac', methods=['GET'])
def buscar_caratula_evac():
    try:
        clave = request.args.get('clave')
        nombre_cliente = request.args.get('nombre_cliente')
        
        if not clave and not nombre_cliente:
            return jsonify({'error': 'Se requiere clave o nombre_cliente'}), 400

        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)
        
        nombre_a_buscar = nombre_cliente
        columna_a_buscar = "nombre_cliente" # Por defecto buscamos en nombre_cliente
        
        # Si la búsqueda es por nombre y contiene "Integral", es un grupo.
        if nombre_cliente and "integral" in nombre_cliente.lower():
            cursor.execute("SELECT id FROM grupo_clientes WHERE nombre_grupo = %s", (nombre_cliente,))
            grupo = cursor.fetchone()
            
            if grupo:
                # Si es un grupo, CAMBIAMOS la columna y el valor a buscar
                nombre_a_buscar = f"Integral {grupo['id']}"
                columna_a_buscar = "clave" # ¡Aquí está la magia!
                logging.info("Búsqueda de GRUPO: traducido '%s' a buscar '%s' en la columna '%s'", nombre_cliente, nombre_a_buscar, columna_a_buscar)

        # Construir consulta dinámica
        query = "SELECT * FROM previo WHERE "
        params = []
        conditions = []
        
        if clave:
            conditions.append("clave = %s")
            params.append(clave)

        # Usamos la columna y el nombre correctos para la búsqueda
        if nombre_a_buscar:
            # Usamos f-string para insertar el nombre de la columna dinámicamente
            conditions.append(f"{columna_a_buscar} LIKE %s")
            params.append(f"%{nombre_a_buscar}%")
        
        query += " AND ".join(conditions)
        
        cursor.execute(query, tuple(params))
        resultados = cursor.fetchall()

        if not resultados:
            return jsonify({'error': 'No se encontraron registros'}), 404

        # Convertir Decimal a float
        for fila in resultados:
            for key, value in fila.items():
                if isinstance(value, Decimal):
                    fila[key] = float(value)
        
        return jsonify(resultados), 200

    except Exception as e:
        logging.exception("Error en buscar_caratula_evac")
        return jsonify({'error': 'Error al procesar la solicitud'}), 500
        
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conexion' in locals() and conexion.is_connected():
            conexion.close()

@caratulas_bp.route('/nombres_caratula', methods=['GET'])
def obtener_nombres():
    try:
        # Conexión a BD
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)

        # Consulta directa
        query = """
        SELECT clave, nombre_cliente
        FROM previo
        """
        cursor.execute(query)
        resultados = cursor.fetchall()

        if not resultados:
            return jsonify({'error': 'No se encontraron registros'}), 404

        return jsonify(resultados), 200

    except Exception as e:
        logging.exception("Error en obtener_nombres")
        return jsonify({'error': 'Error al procesar la solicitud'}), 500

    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@caratulas_bp.route('/clientes_a', methods=['GET'])
def obtener_previo_evac_a():
    try:
        conexion = obtener_conexion()
        with conexion.cursor(dictionary=True) as cursor:
            query = "SELECT * FROM previo WHERE evac = %s"
            cursor.execute(query, ("A",))
            resultados = cursor.fetchall()
        
        # Convertir valores Decimal a float para JSON
        for fila in resultados:
            for key, value in fila.items():
                if isinstance(value, Decimal):
                    fila[key] = float(value)

        return jsonify(resultados), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()
            
@caratulas_bp.route('/clientes_b', methods=['GET'])
def obtener_previo_evac_b():
    try:
        conexion = obtener_conexion()
        with conexion.cursor(dictionary=True) as cursor:
            query = "SELECT * FROM previo WHERE evac = %s"
            cursor.execute(query, ("B",))
            resultados = cursor.fetchall()
        
        # Convertir valores Decimal a float para JSON
        for fila in resultados:
            for key, value in fila.items():
                if isinstance(value, Decimal):
                    fila[key] = float(value)

        return jsonify(resultados), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@caratulas_bp.route('/clientes_go', methods=['GET'])
def obtener_previo_evac_go():
    try:
        conexion = obtener_conexion()
        with conexion.cursor(dictionary=True) as cursor:
            query = "SELECT * FROM previo WHERE evac = %s"
            cursor.execute(query, ("GO",))
            resultados = cursor.fetchall()
        
        # Convertir valores Decimal a float para JSON
        for fila in resultados:
            for key, value in fila.items():
                if isinstance(value, Decimal):
                    fila[key] = float(value)

        return jsonify(resultados), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@caratulas_bp.route('/caratula_evac_a', methods=['POST'])
def actualizar_caratula_evac_a():
    try:
        datos = request.get_json()
        
        # CORRECCIÓN: El frontend envía {datos: [...]} no directamente [...]
        datos_array = datos.get('datos') if isinstance(datos, dict) else datos
        
        if not datos_array or not isinstance(datos_array, list):
            return jsonify({'error': 'Datos no proporcionados correctamente'}), 400
        
        conexion = obtener_conexion()
        with conexion.cursor() as cursor:
            # Snapshot: preservar el estado actual en caratula_evac_a_historico antes de
            # truncar, para poder consultar temporadas anteriores.
            cursor.execute("""
                INSERT INTO caratula_evac_a_historico
                    (temporada, fecha_snapshot, id_original, categoria, meta, acumulado_real, avance_proyectado, porcentaje)
                SELECT %s, NOW(), id, categoria, meta, acumulado_real, avance_proyectado, porcentaje
                FROM caratula_evac_a
            """, (etiqueta_temporada(),))

            cursor.execute("TRUNCATE TABLE caratula_evac_a")
            for i, item in enumerate(datos_array):
                cursor.execute("""
                    INSERT INTO caratula_evac_a 
                    (categoria, meta, acumulado_real, avance_proyectado, porcentaje)
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    item.get('categoria'),
                    item.get('meta', 0),
                    item.get('acumulado_real', 0),
                    item.get('avance_proyectado', 0),
                    item.get('porcentaje', 0)
                ))
            
            conexion.commit()
            return jsonify({'success': True, 'message': 'Datos actualizados'}), 200
            
    except Exception as e:
        if 'conexion' in locals():
            conexion.rollback()
            logging.exception("Error en actualizar_caratula_evac_a")
        return jsonify({'error': str(e)}), 500
    
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@caratulas_bp.route('/caratula_evac_b', methods=['POST'])
def actualizar_caratula_evac_b():
    try:
        datos = request.get_json()
        
        # CORRECCIÓN: El frontend envía {datos: [...]} no directamente [...]
        datos_array = datos.get('datos') if isinstance(datos, dict) else datos
        
        if not datos_array or not isinstance(datos_array, list):
            return jsonify({'error': 'Datos no proporcionados correctamente'}), 400
        
        conexion = obtener_conexion()
        with conexion.cursor() as cursor:
            # Snapshot: preservar el estado actual en caratula_evac_b_historico antes de
            # truncar, para poder consultar temporadas anteriores.
            cursor.execute("""
                INSERT INTO caratula_evac_b_historico
                    (temporada, fecha_snapshot, id_original, categoria, meta, acumulado_real, avance_proyectado, porcentaje)
                SELECT %s, NOW(), id, categoria, meta, acumulado_real, avance_proyectado, porcentaje
                FROM caratula_evac_b
            """, (etiqueta_temporada(),))

            cursor.execute("TRUNCATE TABLE caratula_evac_b")
            for i, item in enumerate(datos_array):
                cursor.execute("""
                    INSERT INTO caratula_evac_b
                    (categoria, meta, acumulado_real, avance_proyectado, porcentaje)
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    item.get('categoria'),
                    item.get('meta', 0),
                    item.get('acumulado_real', 0),
                    item.get('avance_proyectado', 0),
                    item.get('porcentaje', 0)
                ))
            
            conexion.commit()
            return jsonify({'success': True, 'message': 'Datos actualizados'}), 200
            
    except Exception as e:
        if 'conexion' in locals():
            conexion.rollback()
        logging.exception("Error en actualizar_caratula_evac_b")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@caratulas_bp.route('/datos_evac_a', methods=['GET'])
def obtener_caratula_evac_a():
        try:
            conexion = obtener_conexion()
            with conexion.cursor(dictionary=True) as cursor:
                cursor.execute("SELECT * FROM caratula_evac_a")
                resultados = cursor.fetchall()
                # Convertir Decimal a float si es necesario
                for fila in resultados:
                    for key, value in fila.items():
                        if isinstance(value, Decimal):
                            fila[key] = float(value)
            return jsonify(resultados), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500
        finally:
            if cursor:
                cursor.close()
            if conexion and conexion.is_connected():
                conexion.close()

@caratulas_bp.route('/datos_evac_b', methods=['GET'])
def obtener_caratula_evac_b():
        try:
            conexion = obtener_conexion()
            with conexion.cursor(dictionary=True) as cursor:
                cursor.execute("SELECT * FROM caratula_evac_b")
                resultados = cursor.fetchall()
                # Convertir Decimal a float si es necesario
                for fila in resultados:
                    for key, value in fila.items():
                        if isinstance(value, Decimal):
                            fila[key] = float(value)
            return jsonify(resultados), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500
        finally:
            if cursor:
                cursor.close()
            if conexion and conexion.is_connected():
                conexion.close()

@caratulas_bp.route('/datos_previo', methods=['GET'])
def obtener_datos_previo():
    try:
        conexion = obtener_conexion()
        with conexion.cursor(dictionary=True) as cursor:
            # Excluir las claves dadas
            cursor.execute("""
                SELECT * 
                FROM previo
                WHERE clave NOT IN (
                    'JC539','EC216','LC657',
                    'GC411','MC679','MC677',
                    'LC625','LC626','LC627',
                    'LD653','MD680','ID492',
                    'LD660','NA718','7C042'
                )
                AND nombre_cliente IS NOT NULL
                AND nombre_cliente <> ''
                AND nivel IS NOT NULL
                AND nivel <> ''
                AND clave NOT LIKE 'ODOO%'
            """)
            resultados = cursor.fetchall()
            
            # Convertir Decimal a float si es necesario
            for fila in resultados:
                for key, value in fila.items():
                    if isinstance(value, Decimal):
                        fila[key] = float(value)
                    elif hasattr(value, 'strftime'):
                        fila[key] = value.strftime('%Y-%m-%d')
                        
        return jsonify(resultados), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@caratulas_bp.route('/temporadas_disponibles', methods=['GET'])
def temporadas_disponibles():
    """Devuelve las temporadas (ej. '2025-2026') que tienen snapshots guardados
    en cualquiera de las tablas de histórico, para poblar un selector en el frontend.

    Excluye la etiqueta de la temporada actual (por calendario): previo/evac se
    auto-archivan en cada guardado usando etiqueta_temporada(), que solo mira la
    fecha de hoy -- sin importar si la temporada realmente cerro. Eso deja
    snapshots intermedios "ruidosos" bajo la etiqueta vigente que no representan
    un cierre real y confunden en el selector de "temporadas cerradas".
    """
    try:
        conexion = obtener_conexion()
        with conexion.cursor() as cursor:
            cursor.execute("""
                SELECT temporada FROM previo_historico
                UNION
                SELECT temporada FROM caratula_evac_a_historico
                UNION
                SELECT temporada FROM caratula_evac_b_historico
                ORDER BY temporada DESC
            """)
            temporada_actual = etiqueta_temporada()
            temporadas = [row[0] for row in cursor.fetchall() if row[0] != temporada_actual]
        return jsonify(temporadas), 200
    except Exception as e:
        logging.exception("Error en temporadas_disponibles")
        return jsonify({'error': str(e)}), 500
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conexion' in locals() and conexion and conexion.is_connected():
            conexion.close()


@caratulas_bp.route('/datos_previo_historico', methods=['GET'])
def obtener_datos_previo_historico():
    """Histórico de la tabla `previo`. Filtra por ?temporada=2025-2026 (opcional).
    Si no se especifica temporada, devuelve todos los snapshots guardados.
    """
    temporada = request.args.get('temporada')
    try:
        conexion = obtener_conexion()
        with conexion.cursor(dictionary=True) as cursor:
            if temporada:
                cursor.execute(
                    "SELECT * FROM previo_historico WHERE temporada = %s ORDER BY fecha_snapshot DESC",
                    (temporada,)
                )
            else:
                cursor.execute("SELECT * FROM previo_historico ORDER BY fecha_snapshot DESC")
            resultados = cursor.fetchall()
            for fila in resultados:
                for key, value in fila.items():
                    if isinstance(value, Decimal):
                        fila[key] = float(value)
                    elif hasattr(value, 'strftime'):
                        fila[key] = value.strftime('%Y-%m-%d')
        return jsonify(resultados), 200
    except Exception as e:
        logging.exception("Error en obtener_datos_previo_historico")
        return jsonify({'error': str(e)}), 500
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conexion' in locals() and conexion and conexion.is_connected():
            conexion.close()


@caratulas_bp.route('/datos_evac_a_historico', methods=['GET'])
def obtener_datos_evac_a_historico():
    """Histórico de la tabla `caratula_evac_a`. Filtra por ?temporada=2025-2026 (opcional)."""
    temporada = request.args.get('temporada')
    try:
        conexion = obtener_conexion()
        with conexion.cursor(dictionary=True) as cursor:
            if temporada:
                cursor.execute(
                    "SELECT * FROM caratula_evac_a_historico WHERE temporada = %s ORDER BY fecha_snapshot DESC",
                    (temporada,)
                )
            else:
                cursor.execute("SELECT * FROM caratula_evac_a_historico ORDER BY fecha_snapshot DESC")
            resultados = cursor.fetchall()
            for fila in resultados:
                for key, value in fila.items():
                    if isinstance(value, Decimal):
                        fila[key] = float(value)
                    elif hasattr(value, 'strftime'):
                        fila[key] = value.strftime('%Y-%m-%d')
        return jsonify(resultados), 200
    except Exception as e:
        logging.exception("Error en obtener_datos_evac_a_historico")
        return jsonify({'error': str(e)}), 500
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conexion' in locals() and conexion and conexion.is_connected():
            conexion.close()


@caratulas_bp.route('/datos_evac_b_historico', methods=['GET'])
def obtener_datos_evac_b_historico():
    """Histórico de la tabla `caratula_evac_b`. Filtra por ?temporada=2025-2026 (opcional)."""
    temporada = request.args.get('temporada')
    try:
        conexion = obtener_conexion()
        with conexion.cursor(dictionary=True) as cursor:
            if temporada:
                cursor.execute(
                    "SELECT * FROM caratula_evac_b_historico WHERE temporada = %s ORDER BY fecha_snapshot DESC",
                    (temporada,)
                )
            else:
                cursor.execute("SELECT * FROM caratula_evac_b_historico ORDER BY fecha_snapshot DESC")
            resultados = cursor.fetchall()
            for fila in resultados:
                for key, value in fila.items():
                    if isinstance(value, Decimal):
                        fila[key] = float(value)
                    elif hasattr(value, 'strftime'):
                        fila[key] = value.strftime('%Y-%m-%d')
        return jsonify(resultados), 200
    except Exception as e:
        logging.exception("Error en obtener_datos_evac_b_historico")
        return jsonify({'error': str(e)}), 500
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conexion' in locals() and conexion and conexion.is_connected():
            conexion.close()


@caratulas_bp.route('/debug-caratula-global-otros', methods=['GET'])
def debug_caratula_global_otros():
    conexion = None
    cursor = None

    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)

        claves_excluidas = (
            'JC539','EC216','LC657',
            'GC411','MC679','MC677',
            'LC625','LC626','LC627',
            'LD653','MD680','ID492',
            'LD660','NA718','7C042'
        )

        placeholders = ",".join(["%s"] * len(claves_excluidas))

        # 1) Resumen por cliente desde previo
        cursor.execute(f"""
            SELECT
                clave,
                nombre_cliente,
                acumulado_anticipado,
                avance_global_scott,
                acumulado_bold,
                avance_global_apparel_syncros_vittoria,
                (
                    COALESCE(avance_global_scott, 0)
                    + COALESCE(acumulado_bold, 0)
                    + COALESCE(avance_global_apparel_syncros_vittoria, 0)
                ) AS suma_categorias,
                (
                    COALESCE(acumulado_anticipado, 0)
                    -
                    (
                        COALESCE(avance_global_scott, 0)
                        + COALESCE(acumulado_bold, 0)
                        + COALESCE(avance_global_apparel_syncros_vittoria, 0)
                    )
                ) AS diferencia
            FROM previo
            WHERE clave NOT IN ({placeholders})
            AND nombre_cliente IS NOT NULL
            AND nombre_cliente <> ''
            AND nivel IS NOT NULL
            AND nivel <> ''
            AND clave NOT LIKE 'ODOO%'
            HAVING ABS(diferencia) > 1
            ORDER BY diferencia DESC
        """, claves_excluidas)

        clientes_con_diferencia = cursor.fetchall()

        claves_con_diferencia = [
            row["clave"] for row in clientes_con_diferencia
            if row.get("clave") and not str(row.get("clave")).startswith("Integral")
        ]

        # 2) Si no hay claves normales, devolver solo resumen
        if not claves_con_diferencia:
            return jsonify({
                "mensaje": "No se encontraron clientes normales con diferencia. Solo hay integrales o no hay diferencia.",
                "clientes_con_diferencia": clientes_con_diferencia
            }), 200

        placeholders_clientes = ",".join(["%s"] * len(claves_con_diferencia))

        # 3) Clasificación por marca/categoría desde monitor_odoo
        cursor.execute(f"""
            SELECT
                CASE
                    WHEN UPPER(COALESCE(marca, '')) = 'SCOTT'
                         AND UPPER(COALESCE(apparel, '')) = 'SI'
                        THEN 'APPAREL'

                    WHEN UPPER(COALESCE(marca, '')) = 'SCOTT'
                         AND UPPER(COALESCE(apparel, '')) <> 'SI'
                        THEN 'SCOTT'

                    WHEN UPPER(COALESCE(marca, '')) = 'BOLD'
                        THEN 'BOLD'

                    WHEN UPPER(COALESCE(marca, '')) = 'SYNCROS'
                        THEN 'SYNCROS'

                    WHEN UPPER(COALESCE(marca, '')) = 'VITTORIA'
                        THEN 'VITTORIA'

                    WHEN UPPER(COALESCE(categoria_producto, '')) LIKE 'SCOTT / APPAREL%%'
                        THEN 'APPAREL_CATEGORIA_SIN_MARCA'

                    WHEN UPPER(COALESCE(categoria_producto, '')) LIKE 'SCOTT%%'
                        THEN 'SCOTT_CATEGORIA_SIN_MARCA'

                    WHEN UPPER(COALESCE(categoria_producto, '')) LIKE 'BOLD%%'
                        THEN 'BOLD_CATEGORIA_SIN_MARCA'

                    WHEN UPPER(COALESCE(categoria_producto, '')) LIKE 'SYNCROS%%'
                        THEN 'SYNCROS_CATEGORIA_SIN_MARCA'

                    WHEN UPPER(COALESCE(categoria_producto, '')) LIKE 'VITTORIA%%'
                        THEN 'VITTORIA_CATEGORIA_SIN_MARCA'

                    WHEN UPPER(COALESCE(categoria_producto, '')) LIKE 'SERVICIOS%%'
                        THEN 'SERVICIOS'

                    WHEN COALESCE(marca, '') = ''
                         AND COALESCE(categoria_producto, '') = ''
                        THEN 'SIN_MARCA_SIN_CATEGORIA'

                    ELSE 'OTROS'
                END AS clasificacion_debug,

                COALESCE(marca, '') AS marca,
                COALESCE(subcategoria, '') AS subcategoria,
                COALESCE(apparel, '') AS apparel,
                COALESCE(eride, '') AS eride,
                COALESCE(categoria_producto, '') AS categoria_producto,

                COUNT(*) AS registros,
                ROUND(SUM(COALESCE(venta_total, 0)), 2) AS total
            FROM monitor
            WHERE contacto_referencia IN ({placeholders_clientes})
            GROUP BY
                clasificacion_debug,
                marca,
                subcategoria,
                apparel,
                eride,
                categoria_producto
            HAVING total <> 0
            ORDER BY
                clasificacion_debug,
                total DESC
        """, claves_con_diferencia)

        detalle_categorias = cursor.fetchall()

        # 4) Resumen solo de categorías sospechosas
        cursor.execute(f"""
            SELECT
                CASE
                    WHEN UPPER(COALESCE(marca, '')) = 'SCOTT'
                         AND UPPER(COALESCE(apparel, '')) = 'SI'
                        THEN 'APPAREL'

                    WHEN UPPER(COALESCE(marca, '')) = 'SCOTT'
                         AND UPPER(COALESCE(apparel, '')) <> 'SI'
                        THEN 'SCOTT'

                    WHEN UPPER(COALESCE(marca, '')) = 'BOLD'
                        THEN 'BOLD'

                    WHEN UPPER(COALESCE(marca, '')) = 'SYNCROS'
                        THEN 'SYNCROS'

                    WHEN UPPER(COALESCE(marca, '')) = 'VITTORIA'
                        THEN 'VITTORIA'

                    WHEN UPPER(COALESCE(categoria_producto, '')) LIKE 'SCOTT / APPAREL%%'
                        THEN 'APPAREL_CATEGORIA_SIN_MARCA'

                    WHEN UPPER(COALESCE(categoria_producto, '')) LIKE 'SCOTT%%'
                        THEN 'SCOTT_CATEGORIA_SIN_MARCA'

                    WHEN UPPER(COALESCE(categoria_producto, '')) LIKE 'BOLD%%'
                        THEN 'BOLD_CATEGORIA_SIN_MARCA'

                    WHEN UPPER(COALESCE(categoria_producto, '')) LIKE 'SYNCROS%%'
                        THEN 'SYNCROS_CATEGORIA_SIN_MARCA'

                    WHEN UPPER(COALESCE(categoria_producto, '')) LIKE 'VITTORIA%%'
                        THEN 'VITTORIA_CATEGORIA_SIN_MARCA'

                    WHEN UPPER(COALESCE(categoria_producto, '')) LIKE 'SERVICIOS%%'
                        THEN 'SERVICIOS'

                    WHEN COALESCE(marca, '') = ''
                         AND COALESCE(categoria_producto, '') = ''
                        THEN 'SIN_MARCA_SIN_CATEGORIA'

                    ELSE 'OTROS'
                END AS clasificacion_debug,

                COUNT(*) AS registros,
                ROUND(SUM(COALESCE(venta_total, 0)), 2) AS total
            FROM monitor
            WHERE contacto_referencia IN ({placeholders_clientes})
            GROUP BY clasificacion_debug
            HAVING total <> 0
            ORDER BY total DESC
        """, claves_con_diferencia)

        resumen_clasificacion = cursor.fetchall()

        total_diferencia_previo = sum(float(row["diferencia"] or 0) for row in clientes_con_diferencia)

        return jsonify({
            "objetivo": "Detectar qué categorías/marcas explican la diferencia entre acumulado general y SCOTT + BOLD + APPAREL/SYNCROS/VITTORIA",
            "total_diferencia_previo": round(total_diferencia_previo, 2),
            "clientes_con_diferencia": clientes_con_diferencia,
            "resumen_clasificacion_monitor_odoo": resumen_clasificacion,
            "detalle_categorias_monitor_odoo": detalle_categorias
        }), 200

    except Exception as e:
        logging.exception("Error en debug_caratula_global_otros")
        return jsonify({"error": str(e)}), 500

    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@caratulas_bp.route('/generar-pdf', methods=['POST'])
def generar_caratula_pdf():
    """
    Endpoint para generar un PDF de la carátula en el servidor y devolverlo.
    """
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)
    
    try:
        # 1. Obtener los datos del cliente enviados desde Angular
        data = request.get_json()
        if not data or 'datos_caratula' not in data:
            return jsonify({"error": "No se proporcionaron datos de la carátula"}), 400

        # 2. Reutilizar la lógica para crear el HTML del PDF
        # La función crear_cuerpo_email devuelve un dict con 'html_caratula_pdf'
        htmls = crear_cuerpo_email(data)
        html_para_pdf = htmls['html_caratula_pdf']

        # 3. Generar el PDF en memoria usando WeasyPrint (import dinámico)
        try:
            from weasyprint import HTML
        except Exception as e:
            return jsonify({
                "error": (
                    "WeasyPrint no disponible en el entorno. "
                    "Instale las dependencias del sistema (p.ej. libgobject, pango) "
                    "o ejecute en un entorno donde WeasyPrint esté instalado. Detalle: " + str(e)
                )
            }), 500

        pdf_bytes = HTML(string=html_para_pdf).write_pdf()

        # 4. Preparar el nombre del archivo
        clave_cliente = data.get('datos_caratula', {}).get('clave', 'SIN_CLAVE')
        filename = f"Caratula_{clave_cliente}.pdf"

        # 5. Crear una respuesta de Flask con el contenido del PDF
        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={"Content-Disposition": f"attachment;filename={filename}"}
        )

    except Exception as e:
            logging.exception("Error al generar PDF")
            return jsonify({"error": f"Error interno al generar el PDF: {str(e)}"}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()
    
@caratulas_bp.route('/verificar_grupo_cliente', methods=['GET'])
def verificar_grupo_cliente():
    """
    Verifica si un cliente, basado en su clave, pertenece a un grupo.
    Si pertenece, devuelve el ID y el nombre del grupo.
    """
    clave = request.args.get('clave')
    if not clave:
        return jsonify({'error': 'Se requiere la clave del cliente'}), 400

    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)
        
        query = """
            SELECT
                c.id_grupo,
                g.nombre_grupo
            FROM clientes c
            JOIN grupo_clientes g ON c.id_grupo = g.id
            WHERE c.clave = %s AND c.id_grupo IS NOT NULL;
        """
        cursor.execute(query, (clave,))
        resultado = cursor.fetchone()

        if resultado:
            # ¡Éxito! El cliente tiene un grupo.
            return jsonify({
                'tiene_grupo': True,
                'id_grupo': resultado['id_grupo'],
                'nombre_grupo': resultado['nombre_grupo']
            })
        else:
            # El cliente no pertenece a ningún grupo.
            return jsonify({'tiene_grupo': False})

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conexion' in locals() and conexion.is_connected():
            conexion.close()


@caratulas_bp.route('/debug-odoo', methods=['GET'])
def debug_odoo():
    """Debug helper (dev only): intenta conectarse a Odoo y buscar partners para un cliente dado.
    Devuelve uid y número de partners encontrados o el error.
    """
    cliente = request.args.get('cliente')
    try:
        uid, models = get_odoo_models()
        if not uid or not models:
            return jsonify({'ok': False, 'error': 'No se pudo autenticar en Odoo', 'uid': uid}), 500

        if not cliente:
            return jsonify({'ok': True, 'uid': uid})

        try:
            partners = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'res.partner', 'search_read', [[['name', 'ilike', cliente]]], {'fields': ['id', 'name']})
            return jsonify({'ok': True, 'uid': uid, 'partners_count': len(partners), 'sample': partners[:5]}), 200
        except Exception as ex:
            logging.exception('debug-odoo: error buscando partners')
            return jsonify({'ok': False, 'error': str(ex)}), 500

    except Exception as e:
        logging.exception('debug-odoo: excepción inesperada')
        return jsonify({'ok': False, 'error': str(e)}), 500


@caratulas_bp.route('/detalle-compras-odoo', methods=['GET'])
def detalle_compras_odoo():
    """
    Devuelve el historial completo de órdenes de venta de un cliente desde Odoo.
    - Incluye el estado de la orden (Cotización, Confirmada, Bloqueada, Cancelada).
    - Excluye productos con clave FLE o nombre "Standard delivery".
    - Todos los pickings, moves y move_lines se leen en batch (una sola llamada por modelo)
      para minimizar la latencia.
    """
    cliente = request.args.get('cliente')
    estado_filtro = request.args.get('estado')  # opcional
    # grupo_odoo: Vista Global de integral → consulta DB por claves del grupo, luego Odoo con ref IN
    grupo_odoo = request.args.get('grupo')
    # Cuando ref_exacta=1 la búsqueda es solo por ref exact en res.partner
    # (usado en "Mis Pedidos" de usuarios integrales para evitar matches parciales)
    ref_exacta    = request.args.get('ref_exacta') in ('1', 'true', 'True')
    force_refresh = request.args.get('force_refresh') in ('1', 'true', 'True')
    # temporada: etiqueta histórica opcional (ej. "2025-2026"). Cuando se manda,
    # se acotan las órdenes al rango fijo de esa temporada en vez del f_inicio
    # actual del cliente (que ya se reseteó a la temporada abierta).
    temporada_param = request.args.get('temporada')
    try:
        _limit_raw = request.args.get('limit')
        limit = int(_limit_raw) if _limit_raw is not None else None
        if limit is not None and limit <= 0:
            limit = None  # 0 o negativo → sin límite, devolver todo
    except Exception:
        limit = None
    try:
        offset = int(request.args.get('offset')) if request.args.get('offset') is not None else 0
    except Exception:
        offset = 0

    if not cliente and not grupo_odoo:
        return jsonify({'error': 'Se requiere parámetro cliente o grupo'}), 400

    # ── Caché Redis (5 min TTL) ───────────────────────────────────────────────
    # La clave NO incluye limit/offset/estado: esos parámetros se aplican
    # en caliente sobre los datos cacheados, evitando entradas duplicadas por página.
    _cache_key = f"monitor_pedidos:{cliente or ''}:{int(bool(ref_exacta))}:{grupo_odoo or ''}:{temporada_param or ''}"
    if _redis and force_refresh:
        try:
            _redis.delete(_cache_key)
            logging.info('Cache invalidado por force_refresh: %s', _cache_key)
        except Exception as _de:
            logging.warning('Redis delete error: %s', _de)
    if _redis and not force_refresh:
        try:
            _raw = _redis.get(_cache_key)
            if _raw:
                _cached = json.loads(_raw)
                _c_resultado, _c_filas, _c_meta_base = _cached
                # Si el caché es de una versión anterior sin etiquetas, descartarlo
                if _c_filas and 'etiquetas' not in _c_filas[0]:
                    _redis.delete(_cache_key)
                    logging.info('Cache obsoleto (sin etiquetas), forzando reconsulta: %s', _cache_key)
                else:
                    _c_filas_fil = [f for f in _c_filas if f.get('estatus_out') == estado_filtro] if estado_filtro else _c_filas
                    _c_total = len(_c_filas_fil)
                    _c_pag = _c_filas_fil[offset: offset + limit] if limit is not None else _c_filas_fil[offset:]
                    _c_nombre = _c_resultado[0].get('cliente') if _c_resultado else None
                    return jsonify({
                        'data': _c_resultado,
                        'rows': _c_pag,
                        'meta': {**_c_meta_base, 'total': _c_total, 'limit': limit, 'offset': offset, 'returned': len(_c_pag)},
                        'cliente': {'nombre_cliente': _c_nombre, 'clave': cliente},
                    }), 200
        except Exception as _ce:
            logging.warning('Redis cache hit error: %s', _ce)

    # ── Fecha de inicio de temporada por cliente / grupo ──────────────────────
    # En lugar del hard-code '2025-07-01' usamos f_inicio de la tabla clientes,
    # lo que permite incluir pedidos de clientes con temporada anticipada.
    FECHA_INICIO_DEFAULT = '2025-07-01'
    fecha_inicio_temporada = FECHA_INICIO_DEFAULT
    fecha_fin_temporada = None  # solo se acota cuando se pide una temporada histórica
    try:
        _conn_fi = obtener_conexion()
        _cur_fi = _conn_fi.cursor(dictionary=True)
        if temporada_param:
            # Temporada histórica: usar el rango fijo de esa temporada, sin
            # importar el f_inicio individual actual del cliente (ya reseteado
            # a la temporada abierta).
            _cur_fi.execute(
                "SELECT fecha_inicio, fecha_fin FROM temporadas WHERE etiqueta = %s",
                (temporada_param,)
            )
            _row_temp = _cur_fi.fetchone()
            if _row_temp:
                fecha_inicio_temporada = str(_row_temp['fecha_inicio'])
                fecha_fin_temporada = str(_row_temp['fecha_fin'])
        elif grupo_odoo:
            _cur_fi.execute(
                "SELECT MIN(f_inicio) AS fi FROM clientes "
                "WHERE id_grupo = %s AND f_inicio IS NOT NULL",
                (grupo_odoo,)
            )
            _row_fi = _cur_fi.fetchone()
            if _row_fi and _row_fi.get('fi'):
                fecha_inicio_temporada = str(_row_fi['fi'])
        elif cliente:
            # Primero buscar por clave exacta, luego por nombre LIKE
            _cur_fi.execute(
                "SELECT f_inicio FROM clientes WHERE clave = %s",
                (cliente,)
            )
            _row_fi = _cur_fi.fetchone()
            if _row_fi and _row_fi.get('f_inicio'):
                fecha_inicio_temporada = str(_row_fi['f_inicio'])
            else:
                _cur_fi.execute(
                    "SELECT MIN(f_inicio) AS fi FROM clientes "
                    "WHERE nombre_cliente LIKE %s AND f_inicio IS NOT NULL",
                    (f'%{cliente}%',)
                )
                _row_fi = _cur_fi.fetchone()
                if _row_fi and _row_fi.get('fi'):
                    fecha_inicio_temporada = str(_row_fi['fi'])
        _cur_fi.close()
        _conn_fi.close()
    except Exception:
        fecha_inicio_temporada = FECHA_INICIO_DEFAULT

    uid, models, odoo_err = get_odoo_models()
    if not uid or not models:
        logging.error('detalle_compras_odoo: no se pudo conectar a Odoo')
        return jsonify({'error': 'No se pudo conectar a Odoo', 'detail': odoo_err}), 500

    # Etiquetas legibles para el estado de la orden de venta
    SALE_STATE_LABELS = {
        'draft':  'Cotización',
        'sent':   'Cotización Enviada',
        'sale':   'Orden Confirmada',
        'done':   'Bloqueada',
        'cancel': 'Cancelada',
    }

    def map_estado_picking(state):
        if state == 'assigned':
            return 'Almacén EB'
        if state == 'done':
            return 'Entregado'
        if state == 'waiting':
            return 'Falta de confirmación'
        if state in ('confirmed', 'partially_available'):
            return 'En tránsito'
        if state == 'cancel':
            return 'Cancelado'
        return state or ''

    def es_producto_excluido(prod):
        """True si el producto es FLE, Standard delivery, Descuento o línea sin SKU de ese tipo."""
        if not prod:
            return False
        code = (prod.get('default_code') or '').strip().upper()
        name = (prod.get('name') or '').strip().lower()
        return (
            code.startswith('FLE')
            or 'standard delivery' in name
            or 'descuento' in name
        )

    try:
        # ── 1) Determinar el dominio de partners según el modo de búsqueda ──────────────
        try:
            if grupo_odoo:
                # Vista Global de integral: obtener todas las claves Y nombres del grupo
                # desde DB, luego buscar en Odoo por ref. Si algún miembro del grupo
                # no tiene ref en Odoo (distribuidor nuevo), se busca también por nombre.
                try:
                    _conn = obtener_conexion()
                    _cur = _conn.cursor(dictionary=True)
                    _cur.execute(
                        "SELECT clave, nombre_cliente FROM clientes "
                        "WHERE id_grupo = %s AND clave IS NOT NULL AND clave != ''",
                        (grupo_odoo,)
                    )
                    _grupo_rows = _cur.fetchall()
                    _claves = [r['clave'] for r in _grupo_rows]
                    _cur.close()
                    _conn.close()
                except Exception as db_ex:
                    return jsonify({'error': f'Error consultando claves del grupo: {str(db_ex)}'}), 500

                if not _claves:
                    return jsonify({'data': [], 'rows': [], 'meta': {'total': 0}}), 200

                partner_domain = [['ref', 'in', _claves]]
            elif ref_exacta:
                # Modo "Mis Pedidos" de integral: match exacto por ref
                partner_domain = [['ref', '=', cliente]]
            else:
                # Modo global/normal: busca por nombre o ref con ilike
                partner_domain = ['|', ['name', 'ilike', cliente], ['ref', 'ilike', cliente]]

            partners = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                'res.partner', 'search_read',
                [partner_domain],
                {'fields': ['id', 'name', 'ref', 'child_ids'], 'limit': 0}
            )

            # ── Fallback por nombre para distribuidores sin ref en Odoo ─────────────
            # Si ref_exacta y no encontró nada: el distribuidor existe en nuestra DB
            # pero no tiene clave/ref asignada en Odoo → buscar por nombre_cliente.
            if not partners and ref_exacta:
                try:
                    _conn_fb = obtener_conexion()
                    _cur_fb = _conn_fb.cursor(dictionary=True)
                    _cur_fb.execute(
                        "SELECT nombre_cliente FROM clientes WHERE clave = %s",
                        (cliente,)
                    )
                    _row_fb = _cur_fb.fetchone()
                    _cur_fb.close()
                    _conn_fb.close()
                    if _row_fb and _row_fb.get('nombre_cliente'):
                        partners = models.execute_kw(
                            ODOO_DB, uid, ODOO_PASSWORD,
                            'res.partner', 'search_read',
                            [[['name', 'ilike', _row_fb['nombre_cliente']]]],
                            {'fields': ['id', 'name', 'ref', 'child_ids'], 'limit': 0}
                        )
                except Exception:
                    pass

            # ── Fallback por nombre para miembros de grupo sin ref en Odoo ──────────
            # Algunos distribuidores del grupo pueden no tener ref en Odoo.
            # Detectamos cuáles faltan comparando los refs devueltos vs los esperados,
            # y hacemos una búsqueda adicional por nombre para cada uno.
            if grupo_odoo and _grupo_rows:
                refs_encontradas = {(p.get('ref') or '').strip() for p in partners}
                claves_sin_match = [
                    r for r in _grupo_rows
                    if r['clave'].strip() not in refs_encontradas and r.get('nombre_cliente')
                ]
                if claves_sin_match:
                    nombres_faltantes = [r['nombre_cliente'] for r in claves_sin_match]
                    try:
                        # Construir dominio OR con todos los nombres faltantes
                        _name_domain: list = []
                        for _nm in nombres_faltantes:
                            _name_domain.extend(['|', ['name', 'ilike', _nm]])
                        # El último '|' sobra; re-construir correctamente con OR apilado
                        _name_domain_clean: list = []
                        for i, _nm in enumerate(nombres_faltantes):
                            if i < len(nombres_faltantes) - 1:
                                _name_domain_clean.append('|')
                            _name_domain_clean.append(['name', 'ilike', _nm])
                        extra_partners = models.execute_kw(
                            ODOO_DB, uid, ODOO_PASSWORD,
                            'res.partner', 'search_read',
                            [_name_domain_clean],
                            {'fields': ['id', 'name', 'ref', 'child_ids'], 'limit': 0}
                        )
                        # Mergear evitando duplicados por id
                        existing_ids = {p['id'] for p in partners}
                        partners = list(partners) + [
                            p for p in extra_partners if p['id'] not in existing_ids
                        ]
                    except Exception:
                        pass

        except Exception as ex:
            return jsonify({'error': f'Error consultando res.partner: {str(ex)}'}), 500

        if not partners:
            return jsonify({'data': [], 'rows': [], 'meta': {'total': 0}}), 200

        # Expandimos child_ids: los contactos hijo de un partner son sub-cuentas
        # del mismo cliente (portales B2B, direcciones de entrega, personas de
        # contacto) y sus órdenes pertenecen al mismo distribuidor.
        all_partner_ids = set()
        for p in partners:
            all_partner_ids.add(p['id'])
            for child_id in (p.get('child_ids') or []):
                all_partner_ids.add(child_id)
        partner_ids = list(all_partner_ids)

        # ── 2) Traer órdenes de venta desde la fecha de inicio de temporada del cliente ──
        # Excluimos únicamente borradores (state='draft') — órdenes que aún no han
        # sido confirmadas y no deben aparecer en el monitor.
        # Las órdenes canceladas (state='cancel') SÍ se muestran con estatus "Cancelado".
        try:
            _domain_orders = [['partner_id', 'in', partner_ids],
                               ['date_order', '>=', fecha_inicio_temporada],
                               ['state', '!=', 'draft']]
            if fecha_fin_temporada:
                _domain_orders.append(['date_order', '<=', fecha_fin_temporada])
            orders = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                'sale.order', 'search_read',
                [_domain_orders],
                {'fields': ['id', 'name', 'date_order', 'partner_id', 'order_line', 'amount_total', 'state', 'tag_ids'],
                 'order': 'date_order desc', 'limit': 0}
            )
        except Exception as ex:
            return jsonify({'error': f'Error consultando sale.order: {str(ex)}'}), 500

        if not orders:
            return jsonify({'data': [], 'rows': [], 'meta': {'total': 0}}), 200

        # ── 2.5) Batch-leer nombres de etiquetas ─────────────────────────────────
        all_tag_ids = set()
        for o in orders:
            for tid in (o.get('tag_ids') or []):
                all_tag_ids.add(tid)

        tags_map: dict = {}
        if all_tag_ids:
            for _tag_model in ('crm.tag', 'sale.order.tag'):
                try:
                    _tag_rows = models.execute_kw(
                        ODOO_DB, uid, ODOO_PASSWORD,
                        _tag_model, 'read',
                        [list(all_tag_ids)],
                        {'fields': ['id', 'name']}
                    )
                    tags_map = {t['id']: t['name'] for t in _tag_rows}
                    break
                except Exception:
                    continue

        # ── 3) Leer líneas en batch ───────────────────────────────────────────────
        all_line_ids = []
        for o in orders:
            all_line_ids.extend(o.get('order_line') or [])

        lines_map = {}
        if all_line_ids:
            try:
                sol_want = ['id', 'order_id', 'product_id', 'name', 'product_uom_qty',
                             'qty_delivered', 'price_unit', 'discount', 'price_total',
                             'price_subtotal']
                # forecast_expected_date: fecha en que Odoo pronostica disponibilidad
                # is_mto: True si la línea usa ruta MTO (pedido a proveedor bajo demanda)
                sol_all_keys = set()
                try:
                    _sf = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD,
                        'sale.order.line', 'fields_get', [], {'attributes': ['string']})
                    sol_all_keys = set(_sf.keys())
                except Exception:
                    pass
                if 'forecast_expected_date' in sol_all_keys:
                    sol_want.append('forecast_expected_date')
                if 'is_mto' in sol_all_keys:
                    sol_want.append('is_mto')
                # search_read falla cuando el usuario no tiene acceso a product.product.
                # read() con IDs explícitos funciona, PERO algunos campos computados
                # (como is_mto) requieren product.product y también fallan.
                # Estrategia: intentar con todos los campos; si falla, quitar is_mto y reintentar.
                _SOL_BATCH = 500

                def _leer_lineas(fields):
                    result = {}
                    for _i in range(0, len(all_line_ids), _SOL_BATCH):
                        _chunk = all_line_ids[_i:_i + _SOL_BATCH]
                        for l in models.execute_kw(
                            ODOO_DB, uid, ODOO_PASSWORD,
                            'sale.order.line', 'read',
                            [_chunk],
                            {'fields': fields}
                        ):
                            result[l['id']] = l
                    return result

                try:
                    lines_map = _leer_lineas(sol_want)
                except Exception:
                    # is_mto (u otro campo computado) puede requerir product.product; omitirlo.
                    sol_want_fallback = [f for f in sol_want if f != 'is_mto']
                    try:
                        lines_map = _leer_lineas(sol_want_fallback)
                    except Exception:
                        pass
            except Exception:
                pass

        # ── 4) Construir products_map desde display_name de las líneas ────────────
        # product.product.search_read requiere permisos que el usuario puede no tener.
        # El display_name en sale.order.line ya incluye el código "[SKU] Nombre",
        # así que extraemos la info directamente sin llamada adicional a Odoo.
        _CODE_RE_SOL = re.compile(r'^\[([^\]]+)\]\s*(.*)')
        products_map = {}
        for l in lines_map.values():
            pid_raw = l.get('product_id')
            if not pid_raw:
                continue
            pid = pid_raw[0]
            if pid in products_map:
                continue
            display_name = pid_raw[1] if isinstance(pid_raw, (list, tuple)) and len(pid_raw) > 1 else ''
            m_code = _CODE_RE_SOL.match(display_name)
            if m_code:
                code = m_code.group(1).strip()
                pname = m_code.group(2).strip()
            else:
                code = ''
                pname = display_name.strip()
            products_map[pid] = {
                'id': pid,
                'default_code': code,
                'name': pname,
                'display_name': display_name,
            }

        # ── 5) Leer facturas en batch ─────────────────────────────────────────────
        order_names = [o['name'] for o in orders if o.get('name')]
        invoices_map_by_origin = {}
        try:
            inv_rows = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                'account.move', 'search_read',
                [[['origin', 'in', order_names], ['move_type', '=', 'out_invoice']]],
                {'fields': ['id', 'name', 'invoice_date', 'origin', 'state', 'amount_total'], 'limit': 0}
            )
            for m in inv_rows:
                invoices_map_by_origin.setdefault(m.get('origin'), []).append(m)
        except Exception:
            pass

        # ── 6) Determinar campos disponibles en stock.* UNA SOLA VEZ ─────────────
        picking_keys = set()
        try:
            pf = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'stock.picking', 'fields_get', [], {})
            picking_keys = set(pf.keys()) if isinstance(pf, dict) else set()
        except Exception:
            pass

        move_keys = set()
        try:
            mf = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'stock.move', 'fields_get', [], {})
            move_keys = set(mf.keys()) if isinstance(mf, dict) else set()
        except Exception:
            pass

        mline_keys = set()
        try:
            mlf = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'stock.move.line', 'fields_get', [], {})
            mline_keys = set(mlf.keys()) if isinstance(mlf, dict) else set()
        except Exception:
            pass

        # ── 7) Leer TODOS los pickings en un solo batch ───────────────────────────
        picking_want_fields = ['name', 'state', 'picking_type_id', 'picking_type_code', 'scheduled_date', 'origin']
        if 'move_ids' in picking_keys:
            picking_want_fields.append('move_ids')
        if 'move_line_ids' in picking_keys:
            picking_want_fields.append('move_line_ids')

        all_pickings = []
        try:
            all_pickings = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                'stock.picking', 'search_read',
                [[['origin', 'in', order_names]]],
                {'fields': picking_want_fields, 'limit': 0}
            )
        except Exception:
            pass

        pickings_by_origin = {}
        for p in all_pickings:
            pickings_by_origin.setdefault(p.get('origin'), []).append(p)

        # ── 8) Leer TODOS los stock.move en un solo batch ─────────────────────────
        all_move_ids = []
        for p in all_pickings:
            all_move_ids.extend(p.get('move_ids') or [])

        m_fields = ['product_id', 'product_uom_qty', 'state', 'picking_id']
        if 'quantity_done' in move_keys:
            m_fields.append('quantity_done')
        elif 'qty_done' in move_keys:
            m_fields.append('qty_done')
        if 'purchase_line_id' in move_keys:
            m_fields.append('purchase_line_id')
        # move_orig_ids: IDs de los moves de los que depende este move (cadena MTO).
        # Un outgoing 'waiting' depende del incoming de la OC → accedemos a su purchase_line_id.
        if 'move_orig_ids' in move_keys:
            m_fields.append('move_orig_ids')

        moves_by_picking = {}
        move_orig_map: dict = {}  # move_id → list[orig_move_id]
        if all_move_ids:
            try:
                move_rows = models.execute_kw(
                    ODOO_DB, uid, ODOO_PASSWORD,
                    'stock.move', 'search_read',
                    [[['id', 'in', all_move_ids]]],
                    {'fields': m_fields, 'limit': 0}
                )
                for m in move_rows:
                    p_id = m.get('picking_id') and m['picking_id'][0]
                    if p_id:
                        moves_by_picking.setdefault(p_id, []).append(m)
                    orig_ids = m.get('move_orig_ids') or []
                    if orig_ids:
                        move_orig_map[m['id']] = orig_ids
            except Exception:
                pass

        # ── 8.5) Leer purchase.order.line → fecha esperada de entrega ──────────────
        # Hay dos rutas para encontrar la OC ligada a un outgoing move:
        #   A) Directa: el propio outgoing move tiene purchase_line_id (raro en v15+)
        #   B) Indirecta (MTO): outgoing 'waiting' → move_orig_ids → incoming move → purchase_line_id
        pol_fecha_map: dict = {}          # pol_id → {date_planned, po_name}
        upstream_move_pol: dict = {}      # orig_move_id → pol_id  (para resolución en paso 10)
        try:
            pol_ids_set: set = set()

            # Ruta A — POLs directos en los moves que ya leímos
            for moves_list in moves_by_picking.values():
                for m in moves_list:
                    pol_ref = m.get('purchase_line_id')
                    if pol_ref:
                        pol_id_val = pol_ref[0] if isinstance(pol_ref, (list, tuple)) else pol_ref
                        if isinstance(pol_id_val, int) and pol_id_val > 0:
                            pol_ids_set.add(pol_id_val)

            # Ruta B — Leer upstream moves (move_orig_ids de los outgoing waiting)
            # para alcanzar el incoming que tiene purchase_line_id
            all_orig_ids: set = set()
            for orig_list in move_orig_map.values():
                all_orig_ids.update(orig_list)
            if all_orig_ids:
                try:
                    upstream_rows = models.execute_kw(
                        ODOO_DB, uid, ODOO_PASSWORD,
                        'stock.move', 'search_read',
                        [[['id', 'in', list(all_orig_ids)]]],
                        {'fields': ['id', 'purchase_line_id', 'state'], 'limit': 0}
                    )
                    for um in upstream_rows:
                        pol_ref = um.get('purchase_line_id')
                        if pol_ref:
                            pol_id_val = pol_ref[0] if isinstance(pol_ref, (list, tuple)) else pol_ref
                            if isinstance(pol_id_val, int) and pol_id_val > 0:
                                pol_ids_set.add(pol_id_val)
                                upstream_move_pol[um['id']] = pol_id_val
                except Exception as _ex_up:
                    logging.warning('detalle_compras_odoo: error al leer upstream moves: %s', _ex_up)

            # Leer todas las POL en un solo batch
            if pol_ids_set:
                pol_rows = models.execute_kw(
                    ODOO_DB, uid, ODOO_PASSWORD,
                    'purchase.order.line', 'search_read',
                    [[['id', 'in', list(pol_ids_set)]]],
                    {'fields': ['id', 'date_planned', 'product_id', 'order_id'], 'limit': 0}
                )
                for pol in pol_rows:
                    dp = pol.get('date_planned')
                    if dp:
                        pol_fecha_map[pol['id']] = {
                            'date_planned': str(dp),
                            'po_name': pol['order_id'][1] if pol.get('order_id') else None
                        }
        except Exception as _ex_pol:
            logging.warning('detalle_compras_odoo: error al leer purchase.order.line: %s', _ex_pol)

        # ── 9) Leer TODOS los stock.move.line en un solo batch ───────────────────
        all_mline_ids = []
        for p in all_pickings:
            all_mline_ids.extend(p.get('move_line_ids') or [])

        ml_fields = ['product_id', 'product_uom_qty', 'state', 'picking_id']
        if 'qty_done' in mline_keys:
            ml_fields.append('qty_done')
        elif 'quantity_done' in mline_keys:
            ml_fields.append('quantity_done')

        mlines_by_picking = {}
        if all_mline_ids:
            try:
                ml_rows = models.execute_kw(
                    ODOO_DB, uid, ODOO_PASSWORD,
                    'stock.move.line', 'search_read',
                    [[['id', 'in', all_mline_ids]]],
                    {'fields': ml_fields, 'limit': 0}
                )
                for ml in ml_rows:
                    p_id = ml.get('picking_id') and ml['picking_id'][0]
                    if p_id:
                        mlines_by_picking.setdefault(p_id, []).append(ml)
            except Exception:
                pass

        # ── 10) Mapa de entrega por (orden, product_id) ─────────────────────────
        # Procesamos outgoing (entrega final) E internal (PICK) para poder
        # desambiguar el estado 'waiting':
        #   - outgoing waiting + internal assigned/done  → mercancía EN bodega (Almacén EB)
        #   - outgoing waiting + internal sin reserva    → mercancía en tránsito del proveedor
        # ────────────────────────────────────────────────────────────────────────────────────
        # Clave = (nombre_orden, product_id_odoo)  ← combinación única por producto por orden
        entrega_por_prod = {}
        for p in all_pickings:
            ptype = p.get('picking_type_code') or ''
            # Ignoramos recepciones de proveedor (incoming) — su origin es la OC, no la OV
            if ptype not in ('outgoing', 'internal'):
                continue
            is_outgoing = (ptype == 'outgoing')
            origin = p.get('origin') or ''
            p_id = p['id']
            for m in (moves_by_picking.get(p_id) or []):
                prod_id = m.get('product_id') and m['product_id'][0]
                if not prod_id or not origin:
                    continue
                key = (origin, prod_id)
                if key not in entrega_por_prod:
                    entrega_por_prod[key] = {
                        'qty': 0.0, 'done': 0.0,
                        'estados_out': set(),   # estados de stock.move outgoing
                        'estados_int': set(),   # estados de stock.move interno (PICK)
                        'has_purchase': False,  # True si algún move apunta a una OC
                        'fecha_esperada': None, 'po_name': None
                    }
                raw_state = m.get('state') or ''
                if is_outgoing:
                    entrega_por_prod[key]['qty'] += float(m.get('product_uom_qty') or 0)
                    done_qty = m.get('quantity_done') or m.get('qty_done') or 0
                    entrega_por_prod[key]['done'] += float(done_qty)
                    if raw_state:
                        entrega_por_prod[key]['estados_out'].add(raw_state)
                else:
                    if raw_state:
                        entrega_por_prod[key]['estados_int'].add(raw_state)
                # ── Fecha esperada / vínculo OC ─────────────────────────────────────
                # Ruta A: purchase_line_id directo en este move
                pol_ref = m.get('purchase_line_id')
                pol_id_direct = None
                if pol_ref:
                    pol_id_direct = pol_ref[0] if isinstance(pol_ref, (list, tuple)) else pol_ref
                    entrega_por_prod[key]['has_purchase'] = True
                    if isinstance(pol_id_direct, int) and pol_id_direct in pol_fecha_map:
                        if entrega_por_prod[key]['fecha_esperada'] is None:
                            entrega_por_prod[key]['fecha_esperada'] = pol_fecha_map[pol_id_direct]['date_planned']
                            entrega_por_prod[key]['po_name'] = pol_fecha_map[pol_id_direct].get('po_name')

                # Ruta B: buscar en los upstream moves (cadena MTO)
                if not entrega_por_prod[key]['has_purchase']:
                    for orig_id in (move_orig_map.get(m['id']) or []):
                        pol_id_up = upstream_move_pol.get(orig_id)
                        if pol_id_up:
                            entrega_por_prod[key]['has_purchase'] = True
                            if entrega_por_prod[key]['fecha_esperada'] is None and pol_id_up in pol_fecha_map:
                                entrega_por_prod[key]['fecha_esperada'] = pol_fecha_map[pol_id_up]['date_planned']
                                entrega_por_prod[key]['po_name'] = pol_fecha_map[pol_id_up].get('po_name')
                            break

        # ── 10.5) Fecha esperada via POL directo al producto (sin cadena MTO directa) ──
        # Cubre dos escenarios sin enlace directo a OC:
        #   A) outgoing en confirmed/partially_available  (move directo sin PICK intermedio)
        #   B) outgoing waiting con PICK interno en confirmed (flujo multi-paso sin cadena MTO)
        # IMPORTANTE: para evitar mostrar fechas anteriores a la orden de venta
        # (que pertenecen a otras OCs), filtramos los POLs por fecha >= fecha_orden.
        order_date_map = {o['name']: str(o.get('date_order') or '')[:10] for o in orders}

        pending_items = {
            (order_name, prod_id): order_date_map.get(order_name, '')
            for (order_name, prod_id), info in entrega_por_prod.items()
            if not info['has_purchase'] and (
                info['estados_out'] & {'confirmed', 'partially_available'}
                or 'confirmed' in info.get('estados_int', set())
            )
        }
        pending_prod_ids = {pid for (_, pid) in pending_items}

        if pending_prod_ids:
            try:
                pol_fallback_rows = models.execute_kw(
                    ODOO_DB, uid, ODOO_PASSWORD,
                    'purchase.order.line', 'search_read',
                    [[['product_id', 'in', list(pending_prod_ids)],
                      ['order_id.state', 'in', ['purchase', 'done']]]],
                    {'fields': ['id', 'product_id', 'date_planned', 'order_id',
                                'qty_received', 'product_qty'], 'limit': 0}
                )
                # Filtrar en Python: sólo líneas con pendiente de recibir
                pol_fallback_rows = [
                    p for p in pol_fallback_rows
                    if float(p.get('qty_received') or 0) < float(p.get('product_qty') or 0)
                ]

                # Agrupar todos los POLs disponibles por product_id, ordenados por fecha
                pols_by_prod: dict = {}  # prod_id → [{date_planned, po_name}, ...]
                for pol in pol_fallback_rows:
                    dp = pol.get('date_planned')
                    if not dp:
                        continue
                    pid = pol['product_id'][0] if pol.get('product_id') else None
                    if not pid:
                        continue
                    pols_by_prod.setdefault(pid, []).append({
                        'date_planned': str(dp)[:10],
                        'po_name': pol['order_id'][1] if pol.get('order_id') else None
                    })
                for pid in pols_by_prod:
                    pols_by_prod[pid].sort(key=lambda x: x['date_planned'])

                # Por cada (orden, producto) elegir el POL más cercano
                # cuya fecha sea >= fecha de la orden de venta.
                # Si ninguno cumple esa condición (entregas tardías), tomar la más reciente.
                for (order_name, prod_id), order_date in pending_items.items():
                    info = entrega_por_prod.get((order_name, prod_id))
                    if not info or info['has_purchase']:
                        continue
                    cands = pols_by_prod.get(prod_id, [])
                    if not cands:
                        continue
                    # Buscar el primer POL con fecha >= fecha de la OV
                    chosen = next(
                        (c for c in cands if c['date_planned'] >= order_date),
                        None
                    )
                    # Siempre marcar has_purchase=True si existe cualquier POL
                    # (para que el producto aparezca como "En tránsito")
                    info['has_purchase'] = True
                    # Solo asignar fecha si es igual o posterior a la OV:
                    # fechas del pasado no aportan información útil al usuario
                    if chosen is not None and info['fecha_esperada'] is None:
                        info['fecha_esperada'] = chosen['date_planned']
                        info['po_name'] = chosen.get('po_name')
            except Exception as _ex_fb:
                logging.warning('detalle_compras_odoo: error al leer POL fallback: %s', _ex_fb)


        def estatus_por_producto(order_name: str, product_id):
            """Devuelve el estatus de entrega de un producto específico en una orden.

            Prioridad revisada para almacenes multi-paso (Pick + Ship):
            1. Entregado / Entregado Parcial
            2. En tránsito  — 'confirmed'/'partially_available' en outgoing,
               incluso si coexiste con 'assigned' (parte en ruta, parte lista)
            3. Almacén EB   — 'assigned' en outgoing  O  'waiting' en outgoing
               con PICK interno reservado (mercancía ya en bodega, falta mover)
            4. Falta de confirmación — 'waiting' sin stock en bodega y sin OC
            5. Cancelado
            """
            info = entrega_por_prod.get((order_name, product_id))
            if not info or not info['estados_out']:
                return None
            estados_out = info['estados_out']
            estados_int = info.get('estados_int', set())
            has_purchase = info.get('has_purchase', False)
            qty  = info['qty']
            done = info['done']

            # 1. Entregado
            if 'done' in estados_out:
                if qty > 0 and done >= qty:
                    return 'Entregado'
                elif done > 0:
                    return 'Entregado Parcial'
                return 'Entregado'

            # 2. En tránsito — prioridad sobre Almacén EB
            #    Si alguna unidad aún no está disponible es la info más urgente
            if estados_out & {'confirmed', 'partially_available'}:
                return 'En tránsito'

            # 3. Almacén EB — out reservado en zona de salida
            if 'assigned' in estados_out:
                return 'Almacén EB'

            # 4. Waiting — desambiguar con move interno (PICK)
            if 'waiting' in estados_out:
                # PICK interno con reserva → mercancía físicamente en bodega
                if estados_int & {'assigned', 'done'}:
                    return 'Almacén EB'
                # Hay vínculo a OC (directo o via fallback) → en tránsito del proveedor
                if has_purchase:
                    return 'En tránsito'
                # Sin stock y sin OC → falta confirmar abasto
                return 'Falta de confirmación'

            # 5. Cancelado
            if 'cancel' in estados_out:
                return 'Cancelado'

            return map_estado_picking(next(iter(estados_out)))

        # ── 11) Construir resultado ───────────────────────────────────────────────
        resultado = []
        filas_planas = []

        # Pre-cargar SKUs del forecast para marcar líneas con de_proyeccion
        def _norm_fc(s): return re.sub(r'[\-\s]', '', str(s or '')).upper()
        _forecast_skus: set = set()
        try:
            _conn_fc = obtener_conexion()
            _cur_fc = _conn_fc.cursor(dictionary=True)
            if grupo_odoo:
                _cur_fc.execute(
                    "SELECT sku FROM forecast_proyecciones "
                    "WHERE clave_cliente IN "
                    "  (SELECT clave FROM clientes WHERE id_grupo = %s)",
                    (grupo_odoo,)
                )
            else:
                _cur_fc.execute(
                    "SELECT sku FROM forecast_proyecciones "
                    "WHERE clave_cliente = %s",
                    (cliente,)
                )
            _forecast_skus = {_norm_fc(r['sku']) for r in _cur_fc.fetchall()}
            _cur_fc.close()
            _conn_fc.close()
        except Exception as _ex_fc:
            logging.warning('detalle_compras_odoo: error al leer forecast SKUs: %s', _ex_fc)

        for o in orders:
            estado_orden_raw = o.get('state') or ''
            estado_orden = SALE_STATE_LABELS.get(estado_orden_raw, estado_orden_raw)

            _etiquetas = [tags_map[tid] for tid in (o.get('tag_ids') or []) if tid in tags_map]
            order_obj = {
                'orden': o.get('name'),
                'fecha': o.get('date_order'),
                'cliente': o['partner_id'][1] if o.get('partner_id') else None,
                'monto_total': float(o.get('amount_total') or 0),
                'estado_orden': estado_orden,
                'estado_orden_raw': estado_orden_raw,
                'etiquetas': _etiquetas,
                'lineas': [],
                'pickings': []
            }

            # Líneas — filtrando FLE / Standard delivery
            for lid in (o.get('order_line') or []):
                l = lines_map.get(lid)
                if not l:
                    continue
                pid = l.get('product_id') and l['product_id'][0]
                prod = products_map.get(pid) if pid else None
                if es_producto_excluido(prod):
                    continue
                clave = prod.get('default_code') if prod else None
                if prod:
                    dn = prod.get('display_name') or prod.get('name') or ''
                    dc = prod.get('default_code') or ''
                    producto_nombre = dn[len(f'[{dc}] '):] if (dc and dn.startswith(f'[{dc}] ')) else dn
                else:
                    producto_nombre = l['product_id'][1] if l.get('product_id') else None
                cantidad = float(l.get('product_uom_qty') or 0)
                # Omitir líneas con cantidad 0 (producto cancelado/removido sin borrar la línea)
                if cantidad == 0:
                    continue
                qty_entregada = float(l.get('qty_delivered') or 0)
                # Usar price_total de Odoo (incluye el IVA real de cada producto)
                # evitando el multiplicador fijo 1.16 que no aplica a todos los productos.
                price_total_odoo = float(l.get('price_total') or 0)
                if price_total_odoo <= 0 and cantidad > 0:
                    # Fallback al cálculo manual si Odoo no devuelve price_total
                    descuento = float(l.get('discount') or 0)
                    price_total_odoo = round(float(l.get('price_unit') or 0) * (1 - descuento / 100) * 1.16 * cantidad, 2)
                precio = round(price_total_odoo / cantidad, 4) if cantidad > 0 else 0
                total_entregado_linea = round((qty_entregada / cantidad) * price_total_odoo, 2) if cantidad > 0 else 0
                order_obj['lineas'].append({
                    'id': l['id'],
                    'product_id_odoo': pid,   # guardamos el ID para cruzar con moves
                    'producto': producto_nombre,
                    'clave_producto': clave,
                    'descripcion': l.get('name'),
                    'cantidad_pedida': cantidad,
                    'cantidad_entregada': qty_entregada,
                    'precio_unitario': precio,
                    'total_linea': round(price_total_odoo, 2),
                    'total_entregado_linea': total_entregado_linea,
                    'forecast_expected_date': l.get('forecast_expected_date') or None,
                    'is_mto': bool(l.get('is_mto')),
                })

            # Pickings (todos leídos en batch, solo se indexan aquí)
            for p in (pickings_by_origin.get(o.get('name')) or []):
                estado_mapeado = map_estado_picking(p.get('state'))
                ptype_code = p.get('picking_type_code') or ''
                p_id = p['id']
                moves_result = []

                for m in (moves_by_picking.get(p_id) or []):
                    cantidad_hecha = m.get('quantity_done') or m.get('qty_done') or 0
                    moves_result.append({
                        'producto': m['product_id'][1] if m.get('product_id') else None,
                        'cantidad': float(m.get('product_uom_qty') or 0),
                        'cantidad_hecha': float(cantidad_hecha),
                        'state': m.get('state')
                    })

                for ml in (mlines_by_picking.get(p_id) or []):
                    cantidad_hecha_ml = ml.get('qty_done') or ml.get('quantity_done') or 0
                    moves_result.append({
                        'producto': ml['product_id'][1] if ml.get('product_id') else None,
                        'cantidad': float(ml.get('product_uom_qty') or 0),
                        'cantidad_hecha': float(cantidad_hecha_ml),
                        'state': ml.get('state')
                    })

                order_obj['pickings'].append({
                    'picking': p.get('name'),
                    'estado': estado_mapeado,
                    'picking_type_code': ptype_code,
                    'scheduled_date': p.get('scheduled_date'),
                    'moves': moves_result
                })

            # Filas planas para la tabla del frontend
            facturas_rel = invoices_map_by_origin.get(o.get('name'), [])
            factura_nombre = facturas_rel[0]['name'] if facturas_rel else None
            fecha_factura = facturas_rel[0].get('invoice_date') if facturas_rel else None
            order_name = o.get('name')

            for lin in order_obj['lineas']:
                # ── Estatus por picking (cruce con moves)
                estatus_out_lin = estatus_por_producto(order_name, lin.get('product_id_odoo'))
                # Fallback: si no hay moves en pickings outgoing, usar el primer picking outgoing de la orden
                if estatus_out_lin is None:
                    pickings_out = [p for p in order_obj['pickings'] if (p.get('picking_type_code') or '') == 'outgoing']
                    if pickings_out:
                        estatus_out_lin = pickings_out[0]['estado']
                    elif order_obj['pickings']:
                        estatus_out_lin = order_obj['pickings'][0]['estado']
                # Último fallback: si la orden de venta está cancelada y no hay pickings
                # (se canceló antes de crear movimientos), reflejar "Cancelado" directamente.
                if estatus_out_lin is None and estado_orden_raw == 'cancel':
                    estatus_out_lin = 'Cancelado'

                # ── Override con qty_delivered de Odoo (campo autoritativo)
                # qty_delivered es el campo que Odoo calcula directamente;
                # evita que movimientos multi-paso pasen desapercibidos.
                qty_ped = lin.get('cantidad_pedida', 0)
                qty_del = lin.get('cantidad_entregada', 0)
                if qty_ped > 0 and estatus_out_lin != 'Cancelado':
                    if qty_del >= qty_ped:
                        estatus_out_lin = 'Entregado'
                    elif qty_del > 0 and estatus_out_lin not in ('Entregado',):
                        estatus_out_lin = 'Entregado Parcial'

                # ── Override adicional: si el pedido tiene factura posted → entregado+facturado
                facturas_orden = invoices_map_by_origin.get(order_name, [])
                if facturas_orden and any(f.get('state') == 'posted' for f in facturas_orden):
                    if estatus_out_lin not in ('Cancelado', 'Entregado Parcial', 'Entregado'):
                        estatus_out_lin = 'Entregado'

                _ep_info = entrega_por_prod.get((order_name, lin.get('product_id_odoo'))) or {}
                # Fuente primaria: forecast_expected_date de sale.order.line (Odoo calcula esto
                # considerando toda la cadena de abasto; es el mismo dato del tooltip rojo).
                # Fallback: fecha obtenida via cadena de OC en pasos 8.5 / 10.5.
                raw_forecast = lin.get('forecast_expected_date')
                fecha_esp_final = (str(raw_forecast)[:10] if raw_forecast else None) \
                    or _ep_info.get('fecha_esperada')
                po_name_final = _ep_info.get('po_name')
                filas_planas.append({
                    'numero_factura': factura_nombre or order_name,
                    'clave_producto': lin.get('clave_producto'),
                    'producto': lin.get('producto'),
                    'descripcion': lin.get('descripcion'),
                    'fecha': fecha_factura or o.get('date_order'),
                    'precio_unitario': lin.get('precio_unitario'),
                    'cantidad': lin.get('cantidad_pedida'),
                    'cantidad_entregada': lin.get('cantidad_entregada', 0),
                    'total': lin.get('total_linea'),
                    'total_entregado': lin.get('total_entregado_linea', 0),
                    'orden': order_name,
                    'estado_orden': estado_orden,
                    'estado_orden_raw': estado_orden_raw,
                    'cliente': order_obj['cliente'],
                    'pickings': order_obj['pickings'],
                    'estatus_out': estatus_out_lin,
                    'fecha_esperada': fecha_esp_final,
                    'po_name': po_name_final,
                    'de_proyeccion': _norm_fc(lin.get('clave_producto')) in _forecast_skus,
                    'etiquetas': order_obj['etiquetas'],
                })

            resultado.append(order_obj)

        # ── 12) Leer acumulado_anticipado desde previo ────────────────────────────
        # Campo exacto que muestra la carátula como "Entregado".
        # Para grupos usa la fila resumen ("Integral N", es_integral=1).
        # Para clientes individuales usa la fila con su clave.
        avance_previo = None
        try:
            _conn_ap = obtener_conexion()
            _cur_ap = _conn_ap.cursor(dictionary=True)
            _clave_ap = f"Integral {grupo_odoo}" if grupo_odoo else cliente
            if temporada_param:
                # Temporada histórica: leer el snapshot archivado, no el previo en vivo
                _cur_ap.execute(
                    "SELECT acumulado_anticipado AS total FROM previo_historico "
                    "WHERE clave = %s AND temporada = %s "
                    "ORDER BY fecha_snapshot DESC LIMIT 1",
                    (_clave_ap, temporada_param)
                )
            elif grupo_odoo:
                # La fila resumen del integral tiene clave = "Integral {id}"
                _cur_ap.execute(
                    "SELECT acumulado_anticipado AS total FROM previo "
                    "WHERE clave = %s LIMIT 1",
                    (_clave_ap,)
                )
            else:
                _cur_ap.execute(
                    "SELECT acumulado_anticipado AS total FROM previo "
                    "WHERE clave = %s AND (es_integral = 0 OR es_integral IS NULL) LIMIT 1",
                    (_clave_ap,)
                )
            _row_ap = _cur_ap.fetchone()
            if _row_ap and _row_ap.get('total') is not None:
                avance_previo = float(_row_ap['total'])
            _cur_ap.close()
            _conn_ap.close()
        except Exception as _ex_ap:
            logging.warning('detalle_compras_odoo: error al leer acumulado_anticipado: %s', _ex_ap)
            avance_previo = None

        # ── Guardar en caché los datos crudos (sin filtro ni paginación) ─────────
        _meta_base = {
            'fecha_inicio_temporada': fecha_inicio_temporada,
            'avance_previo': avance_previo,
            'temporada': temporada_param,
        }
        if _redis:
            try:
                _redis.setex(_cache_key, _ODOO_PEDIDOS_TTL, json.dumps([resultado, filas_planas, _meta_base]))
            except Exception as _ce:
                logging.warning('Redis cache store error: %s', _ce)

        # ── Filtro opcional por estado de picking
        filas_fil = [f for f in filas_planas if f.get('estatus_out') == estado_filtro] if estado_filtro else filas_planas
        total = len(filas_fil)
        filas_pag = filas_fil[offset: offset + limit] if limit is not None else filas_fil[offset:]

        _nombre_partner = partners[0]['name'] if partners else cliente
        _clave_partner  = (partners[0].get('ref') or '').strip() if partners else cliente
        return jsonify({
            'data': resultado,
            'rows': filas_pag,
            'meta': {
                'total': total,
                'limit': limit,
                'offset': offset,
                'returned': len(filas_pag),
                'fecha_inicio_temporada': fecha_inicio_temporada,
                'avance_previo': avance_previo,
                'temporada': temporada_param,
            },
            'cliente': {
                'nombre_cliente': _nombre_partner,
                'clave': _clave_partner,
            },
        }), 200

    except Exception as e:
        tb = traceback.format_exc()
        logging.exception('detalle_compras_odoo: excepción inesperada')
        return jsonify({'error': str(e), 'trace': tb}), 500


@caratulas_bp.route('/ventas_no_registradas', methods=['GET'])
def ventas_no_registradas():
    """Total de ventas en `monitor` que no corresponden a ningun cliente
    registrado -- ni por clave en `clientes`/`clientes_multimarcas`, ni por
    nombre en `clientes_multimarcas.cliente_razon_social` -- pero que SI
    forman parte del acumulado general de la comercializadora (Caratula
    Global). Acepta ?fecha_desde=&fecha_hasta= opcionales (YYYY-MM-DD).
    """
    conexion = None
    cursor = None
    try:
        fecha_desde = request.args.get('fecha_desde')
        fecha_hasta = request.args.get('fecha_hasta')

        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)

        # NOT EXISTS correlacionado en vez de NOT IN (SELECT ...): con los
        # indices en monitor.contacto_referencia/contacto_nombre y
        # clientes_multimarcas.clave/cliente_razon_social, MariaDB resuelve
        # esto con lookups indexados por fila en vez de escanear las
        # subconsultas repetidamente -- bajo de ~2.7s a milisegundos.
        params = []
        filtro_fecha = ""
        if fecha_desde:
            filtro_fecha += " AND m.fecha_factura >= %s"
            params.append(fecha_desde)
        if fecha_hasta:
            filtro_fecha += " AND m.fecha_factura <= %s"
            params.append(fecha_hasta)

        query = f"""
            SELECT COALESCE(SUM(m.venta_total), 0) AS total, COUNT(*) AS filas
            FROM monitor m
            WHERE NOT EXISTS (
                SELECT 1 FROM clientes c
                WHERE UPPER(TRIM(c.clave)) = UPPER(TRIM(m.contacto_referencia)) AND c.clave <> ''
            )
            AND NOT EXISTS (
                SELECT 1 FROM clientes_multimarcas cm
                WHERE UPPER(TRIM(cm.clave)) = UPPER(TRIM(m.contacto_referencia)) AND cm.clave <> ''
            )
            AND NOT EXISTS (
                SELECT 1 FROM clientes_multimarcas cm2
                WHERE UPPER(TRIM(cm2.cliente_razon_social)) = UPPER(TRIM(m.contacto_nombre))
                  AND cm2.cliente_razon_social <> ''
            )
            {filtro_fecha}
        """
        cursor.execute(query, tuple(params))
        fila = cursor.fetchone()

        return jsonify({
            'total': float(fila['total'] or 0),
            'filas': fila['filas'],
        }), 200

    except Exception as e:
        logging.exception('ventas_no_registradas: error')
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()