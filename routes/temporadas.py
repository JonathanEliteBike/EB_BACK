from __future__ import annotations
from flask import Blueprint, jsonify, request
import logging
from db_conexion import obtener_conexion
from routes.retroactivos import _recalcular_previo_clave_cierre

temporadas_bp = Blueprint('temporadas', __name__, url_prefix='')


def cerrar_temporada_completa(etiqueta: str, dry_run: bool = True) -> dict:
    """
    Cierra una temporada completa: recalcula previo para cada cliente abierto
    acotado a [f_inicio del cliente o inicio de temporada, fin de temporada],
    y persiste el resultado en previo_historico (dry_run=False).
    Reusa _recalcular_previo_clave_cierre (ya usado por /cerrar-temporada individual).
    """
    conexion = obtener_conexion()
    cur_dict = conexion.cursor(dictionary=True)
    cur = conexion.cursor()

    cur_dict.execute("SELECT fecha_inicio, fecha_fin FROM temporadas WHERE etiqueta = %s", (etiqueta,))
    temporada_row = cur_dict.fetchone()
    if not temporada_row:
        cur_dict.close(); cur.close(); conexion.close()
        raise ValueError(f"Temporada '{etiqueta}' no registrada en la tabla temporadas")

    fecha_fin_temporada = str(temporada_row['fecha_fin'])
    fecha_inicio_default = str(temporada_row['fecha_inicio'])

    cur_dict.execute("""
        SELECT clave, f_inicio FROM clientes
        WHERE clave IS NOT NULL AND clave <> ''
    """)
    clientes = cur_dict.fetchall()

    preview = []
    procesados = 0

    for c in clientes:
        clave = c['clave'].strip().upper()
        f_inicio_cliente = c['f_inicio']
        if hasattr(f_inicio_cliente, 'strftime'):
            f_inicio_cliente = f_inicio_cliente.strftime('%Y-%m-%d')
        f_inicio_cliente = f_inicio_cliente or fecha_inicio_default

        _recalcular_previo_clave_cierre(conexion, cur_dict, cur, clave, f_inicio_cliente, fecha_fin_temporada)
        procesados += 1

        if len(preview) < 3:
            cur_dict.execute(
                "SELECT clave, acumulado_anticipado, avance_global_scott, "
                "avance_global_apparel_syncros_vittoria FROM previo WHERE clave = %s",
                (clave,)
            )
            fila_preview = cur_dict.fetchone()
            if fila_preview:
                preview.append(fila_preview)

    if dry_run:
        conexion.rollback()
    else:
        cur.execute("""
            INSERT INTO previo_historico (
                temporada, fecha_snapshot, id_previo, clave, evac, nombre_cliente, acumulado_anticipado, nivel,
                nivel_cierre_compra_inicial, compra_minima_anual, porcentaje_anual, compra_minima_inicial,
                avance_global, porcentaje_global, compromiso_scott, avance_global_scott, porcentaje_scott,
                compromiso_jul_ago, avance_jul_ago, porcentaje_jul_ago,
                compromiso_sep_oct, avance_sep_oct, porcentaje_sep_oct,
                compromiso_nov_dic, avance_nov_dic, porcentaje_nov_dic,
                compromiso_ene_feb, avance_ene_feb, porcentaje_ene_feb,
                compromiso_mar_abr, avance_mar_abr, porcentaje_mar_abr,
                compromiso_may_jun, avance_may_jun, porcentaje_may_jun,
                compromiso_apparel_syncros_vittoria, avance_global_apparel_syncros_vittoria, porcentaje_apparel_syncros_vittoria,
                compromiso_jul_ago_app, avance_jul_ago_app, porcentaje_jul_ago_app,
                compromiso_sep_oct_app, avance_sep_oct_app, porcentaje_sep_oct_app,
                compromiso_nov_dic_app, avance_nov_dic_app, porcentaje_nov_dic_app,
                compromiso_ene_feb_app, avance_ene_feb_app, porcentaje_ene_feb_app,
                compromiso_mar_abr_app, avance_mar_abr_app, porcentaje_mar_abr_app,
                compromiso_may_jun_app, avance_may_jun_app, porcentaje_may_jun_app,
                acumulado_syncros, acumulado_apparel, acumulado_vittoria, acumulado_bold,
                es_integral, grupo_integral
            )
            SELECT
                %s, NOW(), id, clave, evac, nombre_cliente, acumulado_anticipado, nivel,
                nivel_cierre_compra_inicial, compra_minima_anual, porcentaje_anual, compra_minima_inicial,
                avance_global, porcentaje_global, compromiso_scott, avance_global_scott, porcentaje_scott,
                compromiso_jul_ago, avance_jul_ago, porcentaje_jul_ago,
                compromiso_sep_oct, avance_sep_oct, porcentaje_sep_oct,
                compromiso_nov_dic, avance_nov_dic, porcentaje_nov_dic,
                compromiso_ene_feb, avance_ene_feb, porcentaje_ene_feb,
                compromiso_mar_abr, avance_mar_abr, porcentaje_mar_abr,
                compromiso_may_jun, avance_may_jun, porcentaje_may_jun,
                compromiso_apparel_syncros_vittoria, avance_global_apparel_syncros_vittoria, porcentaje_apparel_syncros_vittoria,
                compromiso_jul_ago_app, avance_jul_ago_app, porcentaje_jul_ago_app,
                compromiso_sep_oct_app, avance_sep_oct_app, porcentaje_sep_oct_app,
                compromiso_nov_dic_app, avance_nov_dic_app, porcentaje_nov_dic_app,
                compromiso_ene_feb_app, avance_ene_feb_app, porcentaje_ene_feb_app,
                compromiso_mar_abr_app, avance_mar_abr_app, porcentaje_mar_abr_app,
                compromiso_may_jun_app, avance_may_jun_app, porcentaje_may_jun_app,
                acumulado_syncros, acumulado_apparel, acumulado_vittoria, acumulado_bold,
                es_integral, grupo_integral
            FROM previo
        """, (etiqueta,))
        cur.execute(
            "UPDATE temporadas SET estado='cerrada', fecha_cierre=NOW() WHERE etiqueta = %s",
            (etiqueta,)
        )
        conexion.commit()

    cur_dict.close()
    cur.close()
    conexion.close()

    return {"clientes_procesados": procesados, "preview": preview}


@temporadas_bp.route('/cerrar-temporada-completa', methods=['POST'])
def cerrar_temporada_completa_endpoint():
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
