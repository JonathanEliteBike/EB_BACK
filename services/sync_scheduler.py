import os
import logging
import requests
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

MEXICO_TZ = pytz.timezone('America/Mexico_City')
_scheduler = None


def _run_sync_diario():
    """
    Paso 1: sync-monitor-odoo (recalcular_previo=true)  →  actualiza monitor y recalcula previo
    Paso 2: sincronizar_notas  →  recalcula tabla_retroactivos
    """
    port = os.environ.get('FLASK_PORT', '5000')
    base = f'http://localhost:{port}'

    try:
        logger.info('[SCHEDULER] === Sync diario iniciado ===')

        r1 = requests.post(f'{base}/sync-monitor-odoo', json={'recalcular_previo': True}, timeout=300)
        data1 = r1.json() if r1.headers.get('Content-Type', '').startswith('application/json') else {}
        logger.info('[SCHEDULER] sync-monitor-odoo → %s | registros: %s',
                    r1.status_code, data1.get('count', '?'))

        if not data1.get('success', False):
            logger.error('[SCHEDULER] sync-monitor-odoo falló: %s', data1)
            return

        r2 = requests.post(f'{base}/sincronizar_notas', timeout=300)
        data2 = r2.json() if r2.headers.get('Content-Type', '').startswith('application/json') else {}
        logger.info('[SCHEDULER] sincronizar_notas → %s | %s',
                    r2.status_code, data2.get('mensaje', '?'))

        logger.info('[SCHEDULER] === Sync diario completado ===')

    except Exception as e:
        logger.error('[SCHEDULER] Error en sync diario: %s', e)


def init_scheduler():
    """
    Inicia el scheduler en background.
    Llama esta función UNA sola vez desde app.py.

    En producción (gunicorn) se debe arrancar con --preload para que solo
    el proceso master inicie el scheduler antes de hacer fork a workers:
        gunicorn --preload -w 4 ...
    """
    global _scheduler

    # Evitar doble inicio si Werkzeug reloader está activo
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        return

    if _scheduler is not None and _scheduler.running:
        return

    _scheduler = BackgroundScheduler(timezone=MEXICO_TZ)

    _scheduler.add_job(
        _run_sync_diario,
        CronTrigger(
            day_of_week='mon-fri',
            hour=8,
            minute=30,
            timezone=MEXICO_TZ
        ),
        id='sync_diario_retroactivos',
        name='Sync Diario Odoo → Retroactivos (L-V 08:30 CDMX)',
        replace_existing=True,
        misfire_grace_time=300   # si el servidor estaba apagado, corre hasta 5 min tarde
    )

    _scheduler.start()

    next_run = _scheduler.get_job('sync_diario_retroactivos').next_run_time
    logger.info('[SCHEDULER] Iniciado. Próxima ejecución: %s', next_run)
