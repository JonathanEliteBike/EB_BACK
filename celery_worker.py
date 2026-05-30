from celery import Celery
import os
import sys
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import base64
from datetime import datetime
import time
import logging

# Importa la librería para generar PDFs cuando sea necesario (import dinámico)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Importa las funciones auxiliares de tu módulo de utilidades
from utils.email_utils import (
    obtener_credenciales_por_usuario,
    crear_cuerpo_email,
    actualizar_estado_historial,
)

# Configuración de Celery para usar Redis como intermediario
redis_url = os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')
celery_app = Celery(__name__, broker=redis_url, backend=redis_url)

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='America/Mexico_City',
    enable_utc=True,
    worker_hijack_root_logger=False,
)

# ----------------- POOL DE CONEXIONES SMTP -----------------
# Creamos un pool de conexiones global para reutilizar las conexiones SMTP
smtp_pool = {}
# Creamos un pool para guardar el usuario de email asociado a la conexión
smtp_users = {}

def get_smtp_connection(usuario):
    """Obtiene una conexión SMTP reutilizable para un usuario y su email."""
    global smtp_pool
    global smtp_users
    
    # 1. Intentamos reutilizar la conexión si ya existe
    if usuario in smtp_pool:
        try:
            smtp_pool[usuario].noop()
            # Retornamos el servidor y el email del remitente (guardado previamente)
            return smtp_pool[usuario], smtp_users[usuario] 
        except (smtplib.SMTPServerDisconnected, smtplib.SMTPException):
            logging.warning(f"Conexión SMTP para {usuario} perdida. Reestableciendo...")
            del smtp_pool[usuario] 
            if usuario in smtp_users:
                del smtp_users[usuario]
    
    # 2. Si no existe o se perdió, creamos una nueva conexión
    try:
        credenciales = obtener_credenciales_por_usuario(usuario)
        gmail_user = credenciales['user']
        gmail_password = credenciales['password']

        # Usamos SMTP_SSL en el puerto 465 (más robusto que 587 + starttls)
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(gmail_user, gmail_password)
        
        smtp_pool[usuario] = server   # Guardamos la nueva conexión en el pool
        smtp_users[usuario] = gmail_user # Guardamos el email del remitente
        
        return server, gmail_user
    except Exception as e:
        logging.error(f"Error al establecer conexión SMTP para {usuario}: {e}")
        return None, None # Retorna None, None en caso de error
# ----------------- FIN DEL POOL DE CONEXIONES SMTP -----------------

@celery_app.task(name='tasks.enviar_caratula_pdf_async')
def enviar_caratula_pdf_async(data, usuario, historial_id):
    """Tarea asíncrona que se encarga de generar el PDF y enviar el email."""
    try:
        logging.info(f"[{datetime.now()}] Tarea {historial_id} iniciada.")
        
        # --- Generar el HTML de la carátula y el PDF ---
        start_time = time.time()
        htmls = crear_cuerpo_email(data) 
        
        # Usamos el HTML de la carátula para generar el PDF
        try:
            from weasyprint import HTML
        except Exception as e:
            logging.error(f"WeasyPrint no disponible: {e}")
            actualizar_estado_historial(historial_id, 'Fallido')
            return {"status": "error", "mensaje": f"WeasyPrint no disponible: {str(e)}"}

        pdf_file = HTML(string=htmls['html_caratula_pdf']).write_pdf()
        logging.info(f"[{datetime.now()}] PDF generado en el backend en {time.time() - start_time:.2f} segundos.")
        
        # --- Obtener conexión del pool y enviar el mensaje ---
        start_time = time.time()
        # ¡CORRECCIÓN! Ahora get_smtp_connection devuelve (server, email_remitente)
        server, email_remitente = get_smtp_connection(usuario) 
        
        if not server or not email_remitente:
            raise Exception("No se pudo obtener una conexión SMTP o el email remitente.")
        logging.info(f"[{datetime.now()}] Conexión SMTP obtenida en {time.time() - start_time:.2f} segundos.")

        msg = MIMEMultipart()
        # ¡CORRECCIÓN CLAVE! Usamos el email_remitente correcto para la cabecera From
        msg['From'] = email_remitente
        msg['To'] = data['to']
        msg['Subject'] = f"Carátula - {data['cliente_nombre']} - {datetime.now().strftime('%d/%m/%Y')}"
        
        # Adjuntamos el HTML del cuerpo del email
        msg.attach(MIMEText(htmls['cuerpo_email_html'], 'html'))
        
        # Adjuntar el PDF generado
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(pdf_file)
        encoders.encode_base64(part)
        
        filename = f"Caratula_{data['clave']}_{datetime.now().strftime('%Y%m%d')}.pdf"
        part.add_header('Content-Disposition', f'attachment; filename={filename}')
        msg.attach(part)
        
        # Usamos send_message (más moderno y requiere solo un argumento de remitente)
        server.send_message(msg)
        logging.info(f"[{datetime.now()}] Email enviado en {time.time() - start_time:.2f} segundos.")

        actualizar_estado_historial(historial_id, 'Enviado')
        logging.info(f"[{datetime.now()}] Tarea {historial_id} finalizada con éxito.")
        
        return {"status": "success", "mensaje": "Email enviado correctamente."}

    except Exception as e:
        logging.error(f"[{datetime.now()}] Error en tarea {historial_id}: {str(e)}")
        actualizar_estado_historial(historial_id, 'Fallido')
        return {"status": "error", "mensaje": f"Error al enviar email: {str(e)}"}
    
@celery_app.task(name='tasks.recalcular_previo_async')
def recalcular_previo_async():
    """
    Tarea asíncrona para recalcular la tabla previo.
    """
    try:
        import os
        import sys

        base_dir = os.path.dirname(os.path.abspath(__file__))

        if base_dir not in sys.path:
            sys.path.insert(0, base_dir)

        logging.info(f"BASE_DIR Celery: {base_dir}")
        logging.info(f"sys.path Celery: {sys.path}")

        from tasks_previo import recalcular_previo

        return recalcular_previo()

    except Exception as e:
        logging.exception("Error en tarea recalcular_previo_async")
        return {
            "status": "error",
            "mensaje": str(e)
        }