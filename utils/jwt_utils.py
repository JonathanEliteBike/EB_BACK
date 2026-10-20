import os

import jwt
from dotenv import load_dotenv
import datetime
from flask import request
import pytz

load_dotenv()
SECRET_KEY = os.getenv("JWT_SECRET", "").strip()
if len(SECRET_KEY) < 32:
    raise RuntimeError("Configure JWT_SECRET con al menos 32 caracteres aleatorios antes de iniciar Flask")

def generar_token(id_usuario, rol, usuario, nombre, cliente_id, clave_cliente, nombre_cliente, id_grupo, flujo):
    payload = {
        # Datos del usuario
        'id': id_usuario,
        'rol': rol,
        'usuario': usuario,
        'nombre': nombre,
        'flujo': flujo,  
        
        # Datos del cliente
        'cliente_id': cliente_id,
        'clave': clave_cliente,
        'nombre_cliente': nombre_cliente,
        'id_grupo': id_grupo,
        
        # Expiración
        'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=48)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm='HS256')

def verificar_token(token):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return None  # Token expirado
    except jwt.InvalidTokenError:
        return None  # Token inválido

def registrar_auditoria(cursor, accion, tabla, id_registro, descripcion):
    """
    Extrae el usuario del token actual, calcula la hora de México y guarda el log.
    """
    try:
        # --- A. OBTENER DATOS DEL USUARIO (Tu lógica original) ---
        auth_header = request.headers.get('Authorization')
        usuario_id = None
        usuario_nombre = 'Sistema / Desconocido'

        if auth_header:
            try:
                if " " in auth_header:
                    token = auth_header.split(" ")[1]
                    data = verificar_token(token)
                    
                    if data:
                        usuario_id = data.get('id')
                        usuario_nombre = data.get('nombre')
                    else:
                        usuario_nombre = 'Token Inválido/Expirado'
            except Exception as e:
                print(f"Error procesando token en auditoría: {e}")

        # --- B. CALCULAR HORA EXACTA DE MÉXICO (El cambio nuevo) ---
        zona_mexico = pytz.timezone('America/Mexico_City')
        fecha_actual_mexico = datetime.datetime.now(zona_mexico)

        # --- C. INSERTAR EN BD ---
        # Nota: Agregamos 'fecha_hora' al SQL y el valor 'fecha_actual_mexico'
        sql = """
            INSERT INTO auditoria_movimientos 
            (id_usuario, nombre_usuario, accion, tabla_afectada, id_registro_afectado, descripcion, fecha_hora)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        
        cursor.execute(sql, (
            usuario_id, 
            usuario_nombre, 
            accion, 
            tabla, 
            id_registro, 
            descripcion, 
            fecha_actual_mexico  # <--- Aquí forzamos la hora correcta
        ))
        
        # Nota: No hacemos commit aquí, dependemos de la transacción principal.
        
    except Exception as e:
        print(f"Error registrando auditoría: {e}")