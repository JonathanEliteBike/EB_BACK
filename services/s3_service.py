import os
import uuid
from datetime import datetime

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

load_dotenv()

AWS_S3_BUCKET = os.getenv("AWS_S3_BUCKET")
AWS_S3_PREFIX = os.getenv("AWS_S3_PREFIX", "garantias")
AWS_DEFAULT_REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-2")

# Solo desarrollo: con STORAGE_LOCAL=1 los archivos nuevos se guardan en disco
# (uploads/s3_local) y se sirven desde Flask en vez de subirse al bucket real.
# Los archivos que ya viven en S3 se siguen leyendo de ahí. Sin la variable
# (producción) todo funciona exactamente igual que antes.
STORAGE_LOCAL = os.getenv("STORAGE_LOCAL") == "1"
LOCAL_FILES_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "uploads", "s3_local")
)
LOCAL_FILES_BASE_URL = os.getenv("LOCAL_FILES_BASE_URL", "http://localhost:5000")

_s3_client = None


def _ruta_local(key_s3: str) -> str:
    return os.path.join(LOCAL_FILES_DIR, *key_s3.split("/"))


def _existe_local(key_s3: str) -> bool:
    return STORAGE_LOCAL and os.path.isfile(_ruta_local(key_s3))


def _get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3", region_name=AWS_DEFAULT_REGION)
    return _s3_client


def generar_nombre_s3(filename: str) -> tuple[str, str]:
    original = secure_filename(filename)
    fecha = datetime.now()
    unique_name = f"{uuid.uuid4().hex}_{original}"

    key_s3 = (
        f"{AWS_S3_PREFIX}/"
        f"{fecha.year}/"
        f"{fecha.month:02d}/"
        f"{unique_name}"
    )

    return key_s3, original


_TRANSFER_CONFIG = TransferConfig(
    multipart_threshold=8 * 1024 * 1024,   # inicia multipart desde 8 MB
    multipart_chunksize=8 * 1024 * 1024,    # chunks de 8 MB
    max_concurrency=4,
    use_threads=True,
)


def subir_archivo_s3(file_storage) -> dict:
    key_s3, original = generar_nombre_s3(file_storage.filename)
    content_type = file_storage.content_type or "application/octet-stream"

    if STORAGE_LOCAL:
        ruta = _ruta_local(key_s3)
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        file_storage.save(ruta)
        return {"key": key_s3, "original": original, "content_type": content_type}

    if not AWS_S3_BUCKET:
        raise ValueError("AWS_S3_BUCKET no está configurado")

    _get_s3_client().upload_fileobj(
        file_storage,
        AWS_S3_BUCKET,
        key_s3,
        ExtraArgs={"ContentType": content_type},
        Config=_TRANSFER_CONFIG,
    )

    return {
        "key": key_s3,
        "original": original,
        "content_type": content_type
    }


def generar_url_firmada_s3(key_s3: str, expires_in: int = 3600) -> str:
    if _existe_local(key_s3):
        return f"{LOCAL_FILES_BASE_URL}/api/archivos-locales/{key_s3}"

    if not AWS_S3_BUCKET:
        raise ValueError("AWS_S3_BUCKET no está configurado")

    return _get_s3_client().generate_presigned_url(
        "get_object",
        Params={
            "Bucket": AWS_S3_BUCKET,
            "Key": key_s3
        },
        ExpiresIn=expires_in
    )


def existe_archivo_s3(key_s3: str) -> bool:
    if _existe_local(key_s3):
        return True
    try:
        _get_s3_client().head_object(
            Bucket=AWS_S3_BUCKET,
            Key=key_s3
        )
        return True
    except ClientError:
        return False


def descargar_archivo_s3(key_s3: str) -> tuple[bytes, str]:
    """Descarga el objeto completo y su Content-Type.

    Se usa en vez de redirigir al presigned URL porque el bucket no tiene
    CORS habilitado: un <img>/<a> normal del navegador puede cargar la
    imagen sin problema, pero una descarga por fetch/XHR (necesaria para
    mandar el JWT) sí es bloqueada por el navegador al no traer el header
    Access-Control-Allow-Origin en la respuesta de S3.
    """
    if _existe_local(key_s3):
        import mimetypes
        with open(_ruta_local(key_s3), "rb") as f:
            return f.read(), mimetypes.guess_type(key_s3)[0] or "application/octet-stream"

    if not AWS_S3_BUCKET:
        raise ValueError("AWS_S3_BUCKET no está configurado")

    obj = _get_s3_client().get_object(Bucket=AWS_S3_BUCKET, Key=key_s3)
    contenido = obj["Body"].read()
    content_type = obj.get("ContentType") or "application/octet-stream"
    return contenido, content_type
