from flask import Blueprint, jsonify, request
from db_conexion import obtener_conexion
from utils.jwt_utils import registrar_auditoria

# Mantenemos el mismo Blueprint
gastos_bp = Blueprint('gastos_bp', __name__, url_prefix='/flujo')

# ==============================================================================
# 1. CREAR NUEVO CONCEPTO (Agregar una fila nueva a la tabla)
# ==============================================================================
@gastos_bp.route('/conceptos', methods=['POST'])
def crear_concepto():
    data = request.get_json()
    conexion = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor()
        
        # Insertamos en el catálogo
        sql = """
            INSERT INTO cat_conceptos (nombre_concepto, categoria, orden_reporte)
            VALUES (%s, %s, %s)
        """
        # Sugerencia: El orden podría calcularse automático, pero aquí lo pedimos manual
        cursor.execute(sql, (
            data.get('nombre'),
            data.get('categoria'), # Ej: 'Egresos Operativos'
            data.get('orden', 99)  # Por defecto al final
        ))
        conexion.commit()
        return jsonify({"mensaje": "Concepto creado correctamente"}), 201

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conexion: conexion.close()

# ==============================================================================
# 2. GUARDAR VALOR (La magia: Insertar o Actualizar Monto)
# ==============================================================================
def guardar_valor():
    data = request.get_json()
    # LOG 1: Ver qué manda Angular
    print(f"\n--- INICIO PETICION GUARDAR VALOR ---")
    print(f"RECIBIDO: id:{data['id_concepto']}, fecha:{data['fecha']}, monto:{data['monto']}, tipo:{data.get('tipo')}")
    
    conexion = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor()

        id_concepto = data['id_concepto']
        fecha = data['fecha']
        monto_nuevo = float(data['monto']) # Aseguramos que sea número
        tipo = data.get('tipo', 'real')

        # LOG 2: Ver si el registro existe antes de hacer nada
        check_sql = "SELECT id_valor, monto_proyectado, monto_real FROM flujo_valores WHERE id_concepto = %s AND fecha_reporte = %s"
        cursor.execute(check_sql, (id_concepto, fecha))
        registro_existente = cursor.fetchone()
        
        print(f"RESULTADO BUSQUEDA EN BD: {registro_existente}")

        if registro_existente:
            id_valor = registro_existente[0]
            # Obtenemos el valor que está guardado actualmente en la celda
            monto_actual_en_bd = float(registro_existente[1]) if tipo == 'proyectado' else float(registro_existente[2])
            
            print(f"SUMANDO: {monto_actual_en_bd} (BD) + {monto_nuevo} (FORM) = {monto_actual_en_bd + monto_nuevo}")

            if tipo == 'proyectado':
                # OPCION A (Más segura): Sumar nosotros mismos y mandar el resultado final
                nuevo_total = monto_actual_en_bd + monto_nuevo
                update_sql = "UPDATE flujo_valores SET monto_proyectado = %s WHERE id_valor = %s"
                cursor.execute(update_sql, (nuevo_total, id_valor))
            else:
                update_sql = "UPDATE flujo_valores SET monto_real = %s WHERE id_valor = %s"
                cursor.execute(update_sql, (monto_nuevo, id_valor))
            
            print(f"SQL UPDATE EJECUTADO PARA ID_VALOR: {id_valor}")
        else:
            print(f"NO EXISTE REGISTRO: Insertando nuevo...")
            if tipo == 'real':
                insert_sql = "INSERT INTO flujo_valores (id_concepto, fecha_reporte, monto_real, monto_proyectado) VALUES (%s, %s, %s, 0)"
            else:
                insert_sql = "INSERT INTO flujo_valores (id_concepto, fecha_reporte, monto_real, monto_proyectado) VALUES (%s, %s, 0, %s)"
            cursor.execute(insert_sql, (id_concepto, fecha, monto_nuevo))

        conexion.commit()
        print(f"--- TRANSACCION EXITOSA ---")
        return jsonify({"mensaje": "Monto acumulado correctamente"}), 200

    except Exception as e:
        if conexion: conexion.rollback()
        print(f"❌ ERROR EN GUARDAR_VALOR: {str(e)}")
        return jsonify({'error': str(e)}), 500
    finally:
        if conexion: conexion.close()

@gastos_bp.route('/gastos-operativos', methods=['POST'])
def crear_gasto_operativo():
    data = request.get_json()
    conexion = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor()
        
        # 1. Extraer datos
        monto = float(data.get('monto_base', 0))
        fecha = data.get('fecha_reporte') # YYYY-MM-01
        
        # ID oficial para GASTOS GENERALES FIJOS en tu nueva tabla
        ID_CONCEPTO_GASTOS_FIJOS = 40 

        # 2. INSERTAR EN TU CATÁLOGO AUXILIAR (Mantienes tu registro histórico)
        sql_cat = """
            INSERT INTO gastos_operativos 
            (concepto, categoria, proveedor_fijo, dia_pago_std, monto_base, frecuencia, activo)
            VALUES (%s, %s, %s, 1, %s, 'MENSUAL', 1)
        """
        cursor.execute(sql_cat, (data.get('concepto'), data.get('categoria'), data.get('proveedor_fijo'), monto))
        id_aux_generado = cursor.lastrowid

        # 3. SUMAR AL TABLERO MAESTRO (Tabla Unificada)
        # Buscamos si ya existe el registro para el mes y el concepto 40
        check_sql = """
            SELECT id_valor FROM flujo_valores_unificados 
            WHERE id_concepto = %s AND fecha_reporte = %s
        """
        cursor.execute(check_sql, (ID_CONCEPTO_GASTOS_FIJOS, fecha))
        resultado = cursor.fetchone()

        if resultado:
            # Si existe, sumamos al proyectado
            update_sql = """
                UPDATE flujo_valores_unificados 
                SET monto_proyectado = COALESCE(monto_proyectado, 0) + %s 
                WHERE id_valor = %s
            """
            cursor.execute(update_sql, (monto, resultado[0]))
        else:
            # Si no existe, creamos el registro con el monto inicial
            insert_val = """
                INSERT INTO flujo_valores_unificados 
                (id_concepto, fecha_reporte, monto_proyectado, monto_real) 
                VALUES (%s, %s, %s, 0)
            """
            cursor.execute(insert_val, (ID_CONCEPTO_GASTOS_FIJOS, fecha, monto))

        # 4. AUDITORÍA
        desc = f"Gasto {data.get('concepto')} (${monto}) sumado al ID 40 para {fecha}"
        registrar_auditoria(cursor, 'INSERT_GASTO_UNIFICADO', 'gastos_operativos', id_aux_generado, desc)

        conexion.commit()
        return jsonify({"mensaje": "Gasto registrado y tablero actualizado"}), 201

    except Exception as e:
        if conexion: conexion.rollback()
        print(f"❌ ERROR: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if conexion: conexion.close()

# 4. OBTENER GASTOS (Para llenar la tabla en el frontend)
@gastos_bp.route('/gastos-operativos', methods=['GET'])
def obtener_gastos_operativos():
    conexion = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True) # Para que devuelva objetos
        cursor.execute("SELECT * FROM gastos_operativos WHERE activo = 1")
        gastos = cursor.fetchall()
        return jsonify(gastos), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conexion: conexion.close()
