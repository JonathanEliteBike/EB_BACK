from flask import Blueprint, abort, send_from_directory

from services.s3_service import LOCAL_FILES_DIR, STORAGE_LOCAL

archivos_locales_bp = Blueprint('archivos-locales', __name__)


# Solo desarrollo: sirve lo que subir_archivo_s3 guardó en disco con
# STORAGE_LOCAL=1. Sin esa variable la ruta no existe (404), igual que en
# producción. send_from_directory bloquea rutas fuera de LOCAL_FILES_DIR.
@archivos_locales_bp.route('/api/archivos-locales/<path:key>', methods=['GET'])
def servir_archivo_local(key):
    if not STORAGE_LOCAL:
        abort(404)
    return send_from_directory(LOCAL_FILES_DIR, key)
