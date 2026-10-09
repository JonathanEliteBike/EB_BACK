"""Envio de correo via la API de Gmail (OAuth2 + Domain-Wide Delegation).

Reemplaza el login SMTP usuario/contraseña (desactivado por Google para todo
el Workspace de elitebike-mx.com) por una cuenta de servicio autorizada en el
Admin console para enviar como cualquier cuenta del dominio.
"""
import base64
import os
from email.mime.text import MIMEText

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

load_dotenv()

_SCOPES = ['https://www.googleapis.com/auth/gmail.send']
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_service_cache: dict[str, object] = {}


def _ruta_credencial() -> str:
    ruta = os.getenv('GOOGLE_SERVICE_ACCOUNT_FILE', 'secrets/gmail-service-account.json')
    return ruta if os.path.isabs(ruta) else os.path.join(_BASE_DIR, ruta)


def _gmail_service(remitente: str):
    """Construye (y cachea por remitente) el cliente de la API de Gmail,
    actuando como `remitente` via Domain-Wide Delegation."""
    if remitente in _service_cache:
        return _service_cache[remitente]

    credentials = service_account.Credentials.from_service_account_file(
        _ruta_credencial(), scopes=_SCOPES
    ).with_subject(remitente)

    service = build('gmail', 'v1', credentials=credentials, cache_discovery=False)
    _service_cache[remitente] = service
    return service


def enviar_correo_gmail_api(
    destinatario: str,
    asunto: str,
    cuerpo_html: str,
    remitente: str | None = None,
    cc: str | None = None,
) -> None:
    """Envia un correo HTML via la API de Gmail. `remitente` por defecto es
    GMAIL_SENDER (la cuenta autorizada en el Admin console)."""
    remitente = remitente or os.getenv('GMAIL_SENDER')
    if not remitente:
        raise ValueError("GMAIL_SENDER no esta configurado")

    mensaje = MIMEText(cuerpo_html, 'html')
    mensaje['to'] = destinatario
    mensaje['from'] = remitente
    mensaje['subject'] = asunto
    if cc:
        mensaje['cc'] = cc
    raw = base64.urlsafe_b64encode(mensaje.as_bytes()).decode()

    service = _gmail_service(remitente)
    service.users().messages().send(userId='me', body={'raw': raw}).execute()
