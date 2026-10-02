import logging

from db_conexion import obtener_conexion


def recalcular_previo(aplicar=True):
    """Sincroniza metas MY27 y recalcula avances/porcentajes de ``previo``.

    ``aplicar=False`` funciona como preview y no modifica la base de datos.
    ``aplicar=True``:
      1. sincroniza las metas MY27 desde ``clientes.nivel``;
      2. calcula Integrales dinámicas con nivel común + número de sucursales;
      3. conserva los acuerdos especiales protegidos;
      4. recalcula avances y porcentajes desde ``monitor``.
    """
    conexion = None
    cursor = None

    try:
        from services.metas_previo_my27_service import sincronizar_metas_previo_my27

        conexion = obtener_conexion()
        if conexion is None:
            raise RuntimeError("No se pudo abrir conexión a MySQL")

        resultado_metas = sincronizar_metas_previo_my27(
            conexion,
            aplicar=bool(aplicar),
        )

        if not aplicar:
            conexion.rollback()
            return {
                "status": "success",
                "modo": "preview",
                "mensaje": "Preview MY27 ejecutado; no se modificó la base de datos",
                "filas_revisadas": resultado_metas["filas_revisadas"],
                "evaluables": resultado_metas["evaluables"],
                "con_cambios": resultado_metas["con_cambios"],
                "sin_cambios": resultado_metas["sin_cambios"],
                "protegidas": resultado_metas["protegidas"],
                "omitidas": resultado_metas["omitidas"],
                "por_tipo": resultado_metas["por_tipo"],
            }

        # Import local para evitar ciclos durante el arranque de Flask/Celery.
        from routes.monitor_odoo import _recalcular_acumulados_previo

        cursor = conexion.cursor(dictionary=True)
        filas_avances = _recalcular_acumulados_previo(conexion, cursor)

        logging.info(
            "Recalculo previo MY27 finalizado. metas_actualizadas=%s "
            "avances_actualizados=%s protegidas=%s omitidas=%s",
            resultado_metas["actualizadas"],
            filas_avances,
            resultado_metas["protegidas"],
            resultado_metas["omitidas"],
        )

        return {
            "status": "success",
            "modo": "aplicar",
            "mensaje": "Metas MY27 y avances de previo recalculados correctamente",
            "filas_revisadas": resultado_metas["filas_revisadas"],
            "metas_actualizadas": resultado_metas["actualizadas"],
            "filas_avances_actualizadas": filas_avances,
            "protegidas": resultado_metas["protegidas"],
            "omitidas": resultado_metas["omitidas"],
            "por_tipo": resultado_metas["por_tipo"],
        }

    except Exception as exc:
        if conexion:
            try:
                conexion.rollback()
            except Exception:
                logging.exception("No se pudo hacer rollback del recálculo de previo")

        logging.exception("Error recalculando previo MY27")
        return {
            "status": "error",
            "mensaje": str(exc),
        }

    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()
