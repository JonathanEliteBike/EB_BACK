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

_s3_client = None

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
    if not AWS_S3_BUCKET:
        raise ValueError("AWS_S3_BUCKET no está configurado")

    key_s3, original = generar_nombre_s3(file_storage.filename)
    content_type = file_storage.content_type or "application/octet-stream"

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
    try:
        _get_s3_client().head_object(
            Bucket=AWS_S3_BUCKET,
            Key=key_s3
        )
        return True
    except ClientError:
        return False