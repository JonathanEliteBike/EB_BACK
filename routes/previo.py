from flask import Blueprint, jsonify, request
import logging
from models.monitor_odoo_model import obtener_todos_los_registros
from db_conexion import obtener_conexion
import decimal

previo_bp = Blueprint('previo', __name__, url_prefix='')

@previo_bp.route('/obtener_previo', methods=['GET'])
def obtener_previo():
    conexion = None
    cursor = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)

        cursor.execute("""
            SELECT 
                id,
                clave,
                evac,
                nombre_cliente,
                acumulado_anticipado,
                nivel,
                compra_minima_anual,
                porcentaje_anual,
                compra_minima_inicial,
                avance_global,
                porcentaje_global,
                compromiso_scott,
                avance_global_scott,
                porcentaje_scott,
                compromiso_jul_ago,
                avance_jul_ago,
                porcentaje_jul_ago,
                compromiso_sep_oct,
                avance_sep_oct,
                porcentaje_sep_oct,
                compromiso_nov_dic,
                avance_nov_dic,
                porcentaje_nov_dic,
                compromiso_ene_feb,
                avance_ene_feb,
                porcentaje_ene_feb,
                compromiso_mar_abr,
                avance_mar_abr,
                porcentaje_mar_abr,
                compromiso_may_jun,
                avance_may_jun,
                porcentaje_may_jun,
                compromiso_apparel_syncros_vittoria,
                avance_global_apparel_syncros_vittoria,
                porcentaje_apparel_syncros_vittoria,
                compromiso_jul_ago_app,
                avance_jul_ago_app,
                porcentaje_jul_ago_app,
                compromiso_sep_oct_app,
                avance_sep_oct_app,
                porcentaje_sep_oct_app,
                compromiso_nov_dic_app,
                avance_nov_dic_app,
                porcentaje_nov_dic_app,
                compromiso_ene_feb_app,
                avance_ene_feb_app,
                porcentaje_ene_feb_app,
                compromiso_mar_abr_app,
                avance_mar_abr_app,
                porcentaje_mar_abr_app,
                compromiso_may_jun_app,
                avance_may_jun_app,
                porcentaje_may_jun_app,
                acumulado_syncros,
                acumulado_apparel,
                acumulado_vittoria,
                acumulado_bold,
                es_integral,
                grupo_integral
            FROM previo
            WHERE nombre_cliente IS NOT NULL
                AND nombre_cliente <> ''
                AND nivel IS NOT NULL
                AND nivel <> ''
            ORDER BY es_integral ASC, nombre_cliente ASC
        """)

        registros = cursor.fetchall()
        return jsonify(registros), 200

    except Exception as e:
        logging.exception("Error obteniendo previo")
        return jsonify({'error': str(e)}), 500

    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()

@previo_bp.route('/actualizar_previo', methods=['POST'])
def actualizar_previo():
    conexion = None
    cursor = None
    try:
        if not request.is_json:
            return jsonify({'error': 'Se esperaba un JSON'}), 400
        
        data = request.get_json()
        registros = data.get('datos') if isinstance(data, dict) else data
        
        if not isinstance(registros, list) or len(registros) == 0:
            return jsonify({'error': 'No hay registros para actualizar'}), 400
        
        conexion = obtener_conexion()
        conexion.autocommit = False 
        cursor = conexion.cursor()
        
        def seguro_float(valor):
            try:
                if valor is None or str(valor).strip() == '': return 0.0
                return float(str(valor).replace(',', '').replace('$', ''))
            except:
                return 0.0

        def get_porcentaje(reg, key, fallback_val=0):
            value = reg.get(key, 0)
            if isinstance(value, (float, decimal.Decimal)): # Asegúrate de importar decimal si usas Decimal
                return int(round(value))
            return int(value or fallback_val)

        # Limpiamos la tabla antes de insertar lo nuevo
        cursor.execute("DELETE FROM previo") 
        registros_insertados = 0
        
        for i, registro in enumerate(registros):
            try:
                if 'clave' not in registro: continue
                
                # --- VALORES BASE ---
                meta_inicial = seguro_float(registro.get('compra_minima_inicial'))
                meta_anual = seguro_float(registro.get('compra_minima_anual'))
                avance_real = seguro_float(registro.get('acumulado_anticipado'))

                p_global_calc = int(round((avance_real / meta_inicial) * 100)) if meta_inicial > 0 else 0
                p_anual_calc = int(round((avance_real / meta_anual) * 100)) if meta_anual > 0 else 0

                es_integral_val = int(bool(registro.get('esIntegral', False)))
                grupo_integral_val = registro.get('grupoIntegral')

                # --- NUEVOS VALORES ACUMULADOS (Incluyendo BOLD) ---
                # Es vital obtener estos para pasarlos al INSERT
                ac_syncros = seguro_float(registro.get('acumulado_syncros'))
                ac_apparel = seguro_float(registro.get('acumulado_apparel'))
                ac_vittoria = seguro_float(registro.get('acumulado_vittoria'))
                ac_bold = seguro_float(registro.get('acumulado_bold')) # <--- AQUÍ ESTÁ EL DE BOLD

                # Variables temporales para 2026
                c_ene_feb = seguro_float(registro.get('compromiso_ene_feb'))
                a_ene_feb = seguro_float(registro.get('avance_ene_feb'))
                p_ene_feb = get_porcentaje(registro, 'porcentaje_ene_feb')
                c_mar_abr = seguro_float(registro.get('compromiso_mar_abr'))
                a_mar_abr = seguro_float(registro.get('avance_mar_abr'))
                p_mar_abr = get_porcentaje(registro, 'porcentaje_mar_abr')
                c_may_jun = seguro_float(registro.get('compromiso_may_jun'))
                a_may_jun = seguro_float(registro.get('avance_may_jun'))
                p_may_jun = get_porcentaje(registro, 'porcentaje_may_jun')

                c_ene_feb_app = seguro_float(registro.get('compromiso_ene_feb_app'))
                a_ene_feb_app = seguro_float(registro.get('avance_ene_feb_app'))
                p_ene_feb_app = get_porcentaje(registro, 'porcentaje_ene_feb_app')
                c_mar_abr_app = seguro_float(registro.get('compromiso_mar_abr_app'))
                a_mar_abr_app = seguro_float(registro.get('avance_mar_abr_app'))
                p_mar_abr_app = get_porcentaje(registro, 'porcentaje_mar_abr_app')
                c_may_jun_app = seguro_float(registro.get('compromiso_may_jun_app'))
                a_may_jun_app = seguro_float(registro.get('avance_may_jun_app'))
                p_may_jun_app = get_porcentaje(registro, 'porcentaje_may_jun_app')

                cursor.execute("""
                    INSERT INTO previo (
                        clave, evac, nombre_cliente, acumulado_anticipado, nivel,
                        compra_minima_anual, porcentaje_anual,
                        compra_minima_inicial, avance_global, porcentaje_global,
                        compromiso_scott, avance_global_scott, porcentaje_scott,
                        compromiso_jul_ago, avance_jul_ago, porcentaje_jul_ago,
                        compromiso_sep_oct, avance_sep_oct, porcentaje_sep_oct,
                        compromiso_nov_dic, avance_nov_dic, porcentaje_nov_dic,
                        
                        compromiso_ene_feb, avance_ene_feb, porcentaje_ene_feb,
                        compromiso_mar_abr, avance_mar_abr, porcentaje_mar_abr,
                        compromiso_may_jun, avance_may_jun, porcentaje_may_jun,

                        compromiso_apparel_syncros_vittoria, avance_global_apparel_syncros_vittoria,
                        porcentaje_apparel_syncros_vittoria, 
                        compromiso_jul_ago_app, avance_jul_ago_app, porcentaje_jul_ago_app,
                        compromiso_sep_oct_app, avance_sep_oct_app, porcentaje_sep_oct_app,
                        compromiso_nov_dic_app, avance_nov_dic_app, porcentaje_nov_dic_app,
                        
                        compromiso_ene_feb_app, avance_ene_feb_app, porcentaje_ene_feb_app,
                        compromiso_mar_abr_app, avance_mar_abr_app, porcentaje_mar_abr_app,
                        compromiso_may_jun_app, avance_may_jun_app, porcentaje_may_jun_app,

                        -- AQUÍ AGREGAMOS LAS COLUMNAS FALTANTES --
                        acumulado_syncros, acumulado_apparel, acumulado_vittoria, acumulado_bold,

                        es_integral, grupo_integral
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 
                        %s, %s, 
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, 
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, 
                        
                        -- PLACEHOLDERS PARA LOS ACUMULADOS --
                        %s, %s, %s, %s,

                        %s, %s
                    )
                """, (
                    registro.get('clave'), registro.get('evac'), registro.get('nombre_cliente'),
                    avance_real, registro.get('nivel'), meta_anual, p_anual_calc,
                    meta_inicial, registro.get('avance_global', 0), p_global_calc,
                    registro.get('compromiso_scott', 0), registro.get('avance_global_scott', 0),
                    get_porcentaje(registro, 'porcentaje_scott'), registro.get('compromiso_jul_ago', 0),
                    registro.get('avance_jul_ago', 0), get_porcentaje(registro, 'porcentaje_jul_ago'),
                    registro.get('compromiso_sep_oct', 0), registro.get('avance_sep_oct', 0),
                    get_porcentaje(registro, 'porcentaje_sep_oct'), registro.get('compromiso_nov_dic', 0),
                    registro.get('avance_nov_dic', 0), get_porcentaje(registro, 'porcentaje_nov_dic'),

                    # VALORES 2026 Scott
                    c_ene_feb, a_ene_feb, p_ene_feb,
                    c_mar_abr, a_mar_abr, p_mar_abr,
                    c_may_jun, a_may_jun, p_may_jun,

                    registro.get('compromiso_apparel_syncros_vittoria', 0),
                    registro.get('avance_global_apparel_syncros_vittoria', 0),
                    get_porcentaje(registro, 'porcentaje_apparel_syncros_vittoria'),
                    registro.get('compromiso_jul_ago_app', 0), registro.get('avance_jul_ago_app', 0), get_porcentaje(registro, 'porcentaje_jul_ago_app'),
                    registro.get('compromiso_sep_oct_app', 0), registro.get('avance_sep_oct_app', 0), get_porcentaje(registro, 'porcentaje_sep_oct_app'),
                    registro.get('compromiso_nov_dic_app', 0), registro.get('avance_nov_dic_app', 0), get_porcentaje(registro, 'porcentaje_nov_dic_app'),

                    # VALORES 2026 App
                    c_ene_feb_app, a_ene_feb_app, p_ene_feb_app,
                    c_mar_abr_app, a_mar_abr_app, p_mar_abr_app,
                    c_may_jun_app, a_may_jun_app, p_may_jun_app,

                    # --- VALORES ACUMULADOS AL FINAL ---
                    ac_syncros, ac_apparel, ac_vittoria, ac_bold,

                    es_integral_val, grupo_integral_val
                ))
                registros_insertados += 1
                
            except Exception as e:
                logging.exception("Error procesando registro %s", i)
                raise Exception(f"Falla en registro {registro.get('clave')}: {str(e)}")
        
        conexion.commit() 
        return jsonify({'mensaje': f'Actualización completa. {registros_insertados} registros procesados.'}), 200
        
    except Exception as e:
        if conexion: conexion.rollback()
        return jsonify({'error': f'Error interno: {str(e)}'}), 500
    finally:
        if cursor: cursor.close()
        if conexion: conexion.close()

@previo_bp.route('/obtener_previo_int', methods=['GET'])
def obtener_previo_int():
    conexion = None
    cursor = None
    
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)
        
        # Se agregaron los nuevos campos al SELECT
        cursor.execute("""
            SELECT id, clave, evac, nombre_cliente, acumulado_anticipado, nivel, nivel_cierre_compra_inicial,
                   compra_minima_anual, porcentaje_anual, compra_minima_inicial, avance_global, porcentaje_global,
                   compromiso_scott, avance_global_scott, porcentaje_scott, 
                   compromiso_jul_ago, avance_jul_ago, porcentaje_jul_ago, 
                   compromiso_sep_oct, avance_sep_oct, porcentaje_sep_oct, 
                   compromiso_nov_dic, avance_nov_dic, porcentaje_nov_dic,
                   compromiso_ene_feb, avance_ene_feb, porcentaje_ene_feb,
                   compromiso_mar_abr, avance_mar_abr, porcentaje_mar_abr,
                   compromiso_may_jun, avance_may_jun, porcentaje_may_jun,
                   compromiso_apparel_syncros_vittoria,
                   avance_global_apparel_syncros_vittoria, porcentaje_apparel_syncros_vittoria,
                   compromiso_jul_ago_app, avance_jul_ago_app, porcentaje_jul_ago_app,
                   compromiso_sep_oct_app, avance_sep_oct_app, porcentaje_sep_oct_app,
                   compromiso_nov_dic_app, avance_nov_dic_app, porcentaje_nov_dic_app,
                   compromiso_ene_feb_app, avance_ene_feb_app, porcentaje_ene_feb_app,
                   compromiso_mar_abr_app, avance_mar_abr_app, porcentaje_mar_abr_app,
                   compromiso_may_jun_app, avance_may_jun_app, porcentaje_may_jun_app,
                   acumulado_syncros, acumulado_apparel, acumulado_vittoria, acumulado_bold
            FROM previo
            WHERE clave NOT IN ('Integral 1', 'Integral 2', 'Integral 3')
        """)
        
        registros = cursor.fetchall()
        return jsonify(registros), 200
        
    except Exception as e:
        logging.exception("Error obteniendo datos (excluyendo integrales)")
        return jsonify({'error': str(e)}), 500
        
    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()

@previo_bp.route('/recalcular_previo', methods=['POST'])
def disparar_recalculo_previo():
    try:
        from celery_worker import celery_app

        task = celery_app.send_task('tasks.recalcular_previo_async')

        return jsonify({
            'mensaje': 'Recalculo de previo iniciado',
            'task_id': task.id
        }), 202

    except Exception as e:
        logging.exception("Error iniciando recalculo de previo")
        return jsonify({
            'error': str(e)
        }), 500