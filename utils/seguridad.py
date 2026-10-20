import hashlib
import jwt
import datetime
import bcrypt

from utils.jwt_utils import SECRET_KEY  

def hash_password(contrasena):
    hashed = bcrypt.hashpw(contrasena.encode('utf-8'), bcrypt.gensalt())
    return hashed.decode('utf-8')

def verificar_password(contrasena, hash_guardado):
    return bcrypt.checkpw(contrasena.encode('utf-8'), hash_guardado.encode('utf-8'))

def generar_token(usuario_id):
    payload = {
        "usuario_id": usuario_id,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=6)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")
