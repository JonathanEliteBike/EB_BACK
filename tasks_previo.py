import logging
from db_conexion import obtener_conexion


def recalcular_previo():
    conexion = None
    cursor = None

    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)

        cursor.execute("SELECT COUNT(*) AS total FROM previo")
        resultado = cursor.fetchone()

        total = resultado["total"] if resultado else 0

        logging.info(f"Recalculo previo ejecutado correctamente. Registros actuales en previo: {total}")

        return {
            "status": "success",
            "mensaje": "Recalculo previo ejecutado correctamente",
            "registros_actuales": total
        }

    except Exception as e:
        logging.exception("Error recalculando previo")
        return {
            "status": "error",
            "mensaje": str(e)
        }

    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()