from __future__ import annotations
from flask import Blueprint, jsonify, request
import logging
from db_conexion import obtener_conexion
from routes.retroactivos import _calcular_valores_previo_clave
from utils.jwt_utils import verificar_token

temporadas_bp = Blueprint('temporadas', __name__, url_prefix='')


@temporadas_bp.route('/temporadas', methods=['GET'])
def listar_temporadas():
    """Devuelve todas las temporadas registradas con sus fechas, para poblar
    selectores en el frontend (p. ej. el filtro manual de fecha de Carátula EVACs).
    Lectura pública, sin autenticación -- misma politica que /temporadas_disponibles
    en routes/caratulas.py (no expone nada sensible, solo rangos de fecha por etiqueta).
    """
    conexion = obtener_conexion()
    cur_dict = conexion.cursor(dictionary=True)

    try:
        cur_dict.execute(
            "SELECT etiqueta, fecha_inicio, fecha_fin, estado FROM temporadas ORDER BY fecha_inicio DESC"
        )
        resultados = cur_dict.fetchall()
        for fila in resultados:
            fila['fecha_inicio'] = str(fila['fecha_inicio'])
            fila['fecha_fin'] = str(fila['fecha_fin'])
        return jsonify(resultados), 200
    except Exception as e:
        logging.exception('Error en listar_temporadas')
        return jsonify({'error': str(e)}), 500
    finally:
        cur_dict.close()
        conexion.close()


_CAMPOS_STATICOS_PREVIO = [
    'id', 'evac', 'nombre_cliente', 'nivel', 'nivel_cierre_compra_inicial',
    'compra_minima_anual', 'compra_minima_inicial',
    'compromiso_scott', 'compromiso_apparel_syncros_vittoria',
    'compromiso_jul_ago', 'compromiso_sep_oct', 'compromiso_nov_dic',
    'compromiso_ene_feb', 'compromiso_mar_abr', 'compromiso_may_jun',
    'compromiso_jul_ago_app', 'compromiso_sep_oct_app', 'compromiso_nov_dic_app',
    'compromiso_ene_feb_app', 'compromiso_mar_abr_app', 'compromiso_may_jun_app',
    'grupo_integral',
]

_CAMPOS_SUMA_INTEGRAL = [
    'acumulado_anticipado', 'acumulado_syncros', 'acumulado_apparel', 'acumulado_vittoria', 'acumulado_bold',
    'avance_global', 'avance_global_scott', 'avance_global_apparel_syncros_vittoria',
    'avance_jul_ago', 'avance_sep_oct', 'avance_nov_dic', 'avance_ene_feb', 'avance_mar_abr', 'avance_may_jun',
    'avance_jul_ago_app', 'avance_sep_oct_app', 'avance_nov_dic_app',
    'avance_ene_feb_app', 'avance_mar_abr_app', 'avance_may_jun_app',
]

_COLUMNAS_PREVIO_HISTORICO = [
    'id_previo', 'clave', 'evac', 'nombre_cliente', 'acumulado_anticipado', 'nivel',
    'nivel_cierre_compra_inicial', 'compra_minima_anual', 'porcentaje_anual', 'compra_minima_inicial',
    'avance_global', 'porcentaje_global', 'compromiso_scott', 'avance_global_scott', 'porcentaje_scott',
    'compromiso_jul_ago', 'avance_jul_ago', 'porcentaje_jul_ago',
    'compromiso_sep_oct', 'avance_sep_oct', 'porcentaje_sep_oct',
    'compromiso_nov_dic', 'avance_nov_dic', 'porcentaje_nov_dic',
    'compromiso_ene_feb', 'avance_ene_feb', 'porcentaje_ene_feb',
    'compromiso_mar_abr', 'avance_mar_abr', 'porcentaje_mar_abr',
    'compromiso_may_jun', 'avance_may_jun', 'porcentaje_may_jun',
    'compromiso_apparel_syncros_vittoria', 'avance_global_apparel_syncros_vittoria', 'porcentaje_apparel_syncros_vittoria',
    'compromiso_jul_ago_app', 'avance_jul_ago_app', 'porcentaje_jul_ago_app',
    'compromiso_sep_oct_app', 'avance_sep_oct_app', 'porcentaje_sep_oct_app',
    'compromiso_nov_dic_app', 'avance_nov_dic_app', 'porcentaje_nov_dic_app',
    'compromiso_ene_feb_app', 'avance_ene_feb_app', 'porcentaje_ene_feb_app',
    'compromiso_mar_abr_app', 'avance_mar_abr_app', 'porcentaje_mar_abr_app',
    'compromiso_may_jun_app', 'avance_may_jun_app', 'porcentaje_may_jun_app',
    'acumulado_syncros', 'acumulado_apparel', 'acumulado_vittoria', 'acumulado_bold',
    'es_integral', 'grupo_integral',
]


def _pct(avance: float, compromiso: float) -> int:
    return int(round(avance / compromiso * 100)) if compromiso else 0


def cerrar_temporada_completa(etiqueta: str, dry_run: bool = True) -> dict:
    """
    Cierra una temporada completa: calcula (sin escribir en `previo`, la tabla
    en vivo) los valores de cada cliente ABIERTO acotados a [f_inicio efectivo
    de ese cliente para esta temporada, fin de temporada], y los archiva
    directo en `previo_historico` (dry_run=False). Los renglones "Integral N"
    se calculan sumando a sus miembros ya calculados, sin tocar `monitor` para
    el grupo directamente.

    IMPORTANTE: a diferencia de versiones anteriores, esta funcion NUNCA
    escribe en `previo`. Usa _calcular_valores_previo_clave (funcion pura de
    solo lectura) en vez de _recalcular_previo_clave_cierre (que congela
    `previo` en vivo -- correcto para el cierre INDIVIDUAL de un cliente via
    /cerrar-temporada, pero un bug si se usa para los 140 clientes de golpe:
    sobreescribia el avance en vivo de la temporada actual cada vez que se
    corria un cierre masivo).

    Los clientes que ya tienen temporada_cerrada = 1 (cerrados individualmente
    via /cerrar-temporada) NO se recalculan -- su previo ya está congelado y es
    autoritativo -- pero SÍ se copian tal cual a previo_historico, para que
    aparezcan al consultar esta temporada desde el selector histórico.
    """
    conexion = obtener_conexion()
    cur_dict = conexion.cursor(dictionary=True)
    cur = conexion.cursor()

    procesados = 0
    omitidos = 0
    preview = []
    filas_historico = []

    try:
        cur_dict.execute("SELECT fecha_inicio, fecha_fin, estado FROM temporadas WHERE etiqueta = %s", (etiqueta,))
        temporada_row = cur_dict.fetchone()
        if not temporada_row:
            raise ValueError(f"Temporada '{etiqueta}' no registrada en la tabla temporadas")

        if temporada_row['estado'] == 'cerrada' and not dry_run:
            raise ValueError(
                f"Temporada '{etiqueta}' ya está cerrada (estado='cerrada'). "
                "No se puede volver a ejecutar el cierre real sobre una temporada ya cerrada "
                "-- esto duplicaría/corrompería las filas ya congeladas en previo_historico."
            )

        fecha_fin_temporada = str(temporada_row['fecha_fin'])
        anio_inicio_temporada = int(str(temporada_row['fecha_inicio'])[:4])

        # Solo clientes abiertos: los ya cerrados individualmente conservan su
        # propio cierre y no deben ser reprocesados por el cierre masivo.
        # NOTA: no se usa clientes.f_inicio aqui -- ese campo refleja el inicio
        # de la temporada ACTUALMENTE abierta (lo mueve abrir_temporada()/otros
        # procesos conforme avanza el tiempo) y puede ya apuntar a una temporada
        # posterior a la que se esta cerrando. El inicio efectivo para ESTA
        # temporada se deriva de dia_inicio_temporada (o el default 07-01) + el
        # anio de fecha_inicio de la temporada que se esta cerrando, igual que
        # abrir_temporada() lo calcula para la temporada que abre.
        cur_dict.execute("""
            SELECT clave, dia_inicio_temporada FROM clientes
            WHERE clave IS NOT NULL AND clave <> ''
              AND (temporada_cerrada IS NULL OR temporada_cerrada = 0)
        """)
        clientes = cur_dict.fetchall()

        # Clientes ya cerrados individualmente: su `previo` ya está congelado
        # y es autoritativo -- se copian tal cual a previo_historico (sin
        # recalcular nada) para que SÍ aparezcan al consultar esta temporada
        # desde el selector histórico, en vez de quedar fuera por completo.
        _campos_previo_directo = [c for c in _COLUMNAS_PREVIO_HISTORICO if c not in ('id_previo', 'clave')]
        cur_dict.execute(f"""
            SELECT id, clave, {', '.join(_campos_previo_directo)}
            FROM previo
            WHERE UPPER(TRIM(clave)) IN (
                SELECT UPPER(TRIM(clave)) FROM clientes
                WHERE clave IS NOT NULL AND clave <> '' AND temporada_cerrada = 1
            )
        """)
        cerrados_previo = cur_dict.fetchall()
        omitidos = len(cerrados_previo)

        for fila_cerrada in cerrados_previo:
            fila_cerrada['id_previo'] = fila_cerrada['id']
            filas_historico.append(fila_cerrada)
            if len(preview) < 3:
                preview.append({
                    'clave': fila_cerrada['clave'],
                    'acumulado_anticipado': fila_cerrada['acumulado_anticipado'],
                    'avance_global_scott': fila_cerrada['avance_global_scott'],
                    'avance_global_apparel_syncros_vittoria': fila_cerrada['avance_global_apparel_syncros_vittoria'],
                    'cerrado_individualmente': True,
                })

        for c in clientes:
            clave = c['clave'].strip().upper()
            dia = c['dia_inicio_temporada'] or '07-01'
            f_inicio_cliente = f"{anio_inicio_temporada}-{dia}"

            valores = _calcular_valores_previo_clave(cur_dict, clave, f_inicio_cliente, fecha_fin_temporada)
            if valores is None:
                continue  # sin fila en previo (o es un renglon Integral -- esos se calculan aparte abajo)

            cur_dict.execute(
                f"SELECT {', '.join(_CAMPOS_STATICOS_PREVIO)} FROM previo WHERE id = %s",
                (valores['id'],)
            )
            estatico = cur_dict.fetchone()

            fila = {**estatico, **valores, 'clave': clave, 'es_integral': 0, 'id_previo': valores['id']}
            filas_historico.append(fila)
            procesados += 1

            if len(preview) < 3:
                preview.append({
                    'clave': clave,
                    'acumulado_anticipado': valores['acumulado_anticipado'],
                    'avance_global_scott': valores['avance_global_scott'],
                    'avance_global_apparel_syncros_vittoria': valores['avance_global_apparel_syncros_vittoria'],
                })

        # Renglones "Integral": suma de sus miembros ya calculados arriba
        # (nunca se lee `monitor` para el grupo directamente). Reusa sus
        # propios compromiso_* (fijos, de su propia fila en `previo`).
        grupos: dict[int, list[dict]] = {}
        for fila in filas_historico:
            gid = fila.get('grupo_integral')
            if gid:
                grupos.setdefault(gid, []).append(fila)

        if grupos:
            cur_dict.execute(
                f"SELECT {', '.join(_CAMPOS_STATICOS_PREVIO)} FROM previo WHERE es_integral = 1"
            )
            for grupo_row in cur_dict.fetchall():
                gid = grupo_row['grupo_integral']
                miembros = grupos.get(gid, [])
                sumas = {c: sum(float(m.get(c) or 0) for m in miembros) for c in _CAMPOS_SUMA_INTEGRAL}

                fila_grupo = {
                    **grupo_row,
                    **sumas,
                    'clave': None,  # se sobreescribe abajo con la clave real de la fila Integral
                    'es_integral': 1,
                    'id_previo': grupo_row['id'],
                    'porcentaje_global': _pct(sumas['avance_global'], float(grupo_row.get('compra_minima_inicial') or 0)),
                    'porcentaje_anual': _pct(sumas['avance_global'], float(grupo_row.get('compra_minima_anual') or 0)),
                    'porcentaje_scott': _pct(sumas['avance_global_scott'], float(grupo_row.get('compromiso_scott') or 0)),
                    'porcentaje_apparel_syncros_vittoria': _pct(
                        sumas['avance_global_apparel_syncros_vittoria'],
                        float(grupo_row.get('compromiso_apparel_syncros_vittoria') or 0)
                    ),
                }
                for p in ('jul_ago', 'sep_oct', 'nov_dic', 'ene_feb', 'mar_abr', 'may_jun'):
                    fila_grupo[f'porcentaje_{p}'] = _pct(sumas[f'avance_{p}'], float(grupo_row.get(f'compromiso_{p}') or 0))
                    fila_grupo[f'porcentaje_{p}_app'] = _pct(sumas[f'avance_{p}_app'], float(grupo_row.get(f'compromiso_{p}_app') or 0))

                cur_dict.execute("SELECT clave FROM previo WHERE id = %s", (grupo_row['id'],))
                fila_grupo['clave'] = cur_dict.fetchone()['clave']

                filas_historico.append(fila_grupo)

        if not dry_run:
            filas_valores = [
                tuple(fila.get(col) for col in _COLUMNAS_PREVIO_HISTORICO)
                for fila in filas_historico
            ]
            cur.executemany(
                f"""INSERT INTO previo_historico (
                    temporada, fecha_snapshot, {', '.join(_COLUMNAS_PREVIO_HISTORICO)}
                ) VALUES (
                    %s, NOW(), {', '.join(['%s'] * len(_COLUMNAS_PREVIO_HISTORICO))}
                )""",
                [(etiqueta, *valores_fila) for valores_fila in filas_valores]
            )
            cur.execute(
                "UPDATE temporadas SET estado='cerrada', fecha_cierre=NOW() WHERE etiqueta = %s",
                (etiqueta,)
            )
            conexion.commit()
    except Exception:
        conexion.rollback()
        raise
    finally:
        cur_dict.close()
        cur.close()
        conexion.close()

    logging.info(
        "cerrar_temporada_completa(%s): %d procesados, %d ya cerrados (omitidos)",
        etiqueta, procesados, omitidos
    )

    return {
        "clientes_procesados": procesados,
        "clientes_omitidos_ya_cerrados": omitidos,
        "preview": preview,
    }


@temporadas_bp.route('/cerrar-temporada-completa', methods=['POST'])
def cerrar_temporada_completa_endpoint():
    # Admin only, irreversible: mismo patrón de autenticación que
    # /cerrar-temporada en routes/retroactivos.py.
    auth_header = request.headers.get('Authorization', '')
    raw_token = auth_header.split(' ')[1] if ' ' in auth_header else None
    if not raw_token:
        return jsonify({"error": "No autorizado"}), 401

    payload = verificar_token(raw_token)
    if not payload:
        return jsonify({"error": "Sesión expirada, por favor inicia sesión de nuevo"}), 401
    rol = payload.get('rol')
    try:
        es_admin = int(rol) == 1
    except (TypeError, ValueError):
        es_admin = False
    if not es_admin:
        return jsonify({"error": "Solo administradores pueden cerrar la temporada"}), 403

    data = request.get_json() or {}
    etiqueta = data.get('etiqueta')
    dry_run = data.get('dry_run', True)
    if not etiqueta:
        return jsonify({'error': 'Se requiere etiqueta (ej. "2025-2026")'}), 400
    try:
        resultado = cerrar_temporada_completa(etiqueta, dry_run=dry_run)
        return jsonify(resultado), 200
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logging.exception('Error en cerrar_temporada_completa_endpoint')
        return jsonify({'error': str(e)}), 500


def abrir_temporada(etiqueta: str) -> int:
    """Fija f_inicio para cada cliente segun su dia_inicio_temporada (o el
    default 07-01), y los reabre (temporada_cerrada=0) para la temporada nueva."""
    conexion = obtener_conexion()
    cur_dict = conexion.cursor(dictionary=True)
    cur = conexion.cursor()

    actualizados = 0

    try:
        cur_dict.execute("SELECT fecha_inicio FROM temporadas WHERE etiqueta = %s", (etiqueta,))
        row = cur_dict.fetchone()
        if not row:
            raise ValueError(f"Temporada '{etiqueta}' no registrada en la tabla temporadas")
        anio_inicio = row['fecha_inicio'].year

        cur_dict.execute("SELECT clave, dia_inicio_temporada FROM clientes WHERE clave IS NOT NULL AND clave <> ''")
        clientes = cur_dict.fetchall()

        for c in clientes:
            dia = c['dia_inicio_temporada'] or '07-01'
            f_inicio_nuevo = f"{anio_inicio}-{dia}"
            cur.execute(
                "UPDATE clientes SET f_inicio = %s, temporada_cerrada = 0, fecha_cierre_temporada = NULL, f_fin = NULL "
                "WHERE clave = %s",
                (f_inicio_nuevo, c['clave'])
            )
            actualizados += 1

        conexion.commit()
    except Exception:
        conexion.rollback()
        raise
    finally:
        cur_dict.close()
        cur.close()
        conexion.close()

    logging.info("abrir_temporada(%s): %d clientes actualizados", etiqueta, actualizados)

    return actualizados


@temporadas_bp.route('/abrir-temporada', methods=['POST'])
def abrir_temporada_endpoint():
    # Admin only, bulk-reset de temporada_cerrada/f_inicio para TODOS los
    # clientes: mismo patrón de autenticación que /cerrar-temporada-completa.
    auth_header = request.headers.get('Authorization', '')
    raw_token = auth_header.split(' ')[1] if ' ' in auth_header else None
    if not raw_token:
        return jsonify({"error": "No autorizado"}), 401

    payload = verificar_token(raw_token)
    if not payload:
        return jsonify({"error": "Sesión expirada, por favor inicia sesión de nuevo"}), 401
    rol = payload.get('rol')
    try:
        es_admin = int(rol) == 1
    except (TypeError, ValueError):
        es_admin = False
    if not es_admin:
        return jsonify({"error": "Solo administradores pueden abrir la temporada"}), 403

    data = request.get_json() or {}
    etiqueta = data.get('etiqueta')
    if not etiqueta:
        return jsonify({'error': 'Se requiere etiqueta (ej. "2026-2027")'}), 400
    try:
        n = abrir_temporada(etiqueta)
        return jsonify({'success': True, 'clientes_actualizados': n}), 200
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logging.exception('Error en abrir_temporada_endpoint')
        return jsonify({'error': str(e)}), 500
