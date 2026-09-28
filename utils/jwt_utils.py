import jwt
import datetime
from flask import request
import pytz

SECRET_KEY = "121221"

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


def generar_token_prewarm_interno():
    """Genera un JWT de administrador activo para solicitudes locales de caché.

    Los precalentadores consultan endpoints ya protegidos para reutilizar su
    lógica de caché. No representan una sesión de navegador ni se exponen al
    cliente; si no hay un administrador activo, el precalentamiento se omite.
    """
    from db_conexion import obtener_conexion

    conexion = None
    cursor = None
    try:
        conexion = obtener_conexion()
        cursor = conexion.cursor(dictionary=True)
        cursor.execute("""
            SELECT u.id, u.usuario, u.nombre, u.cliente_id,
                   c.clave, c.nombre_cliente, c.id_grupo
            FROM usuarios u
            LEFT JOIN clientes c ON c.id = u.cliente_id
            WHERE u.rol_id = 1 AND u.activo = 1
            ORDER BY u.id
            LIMIT 1
        """)
        usuario = cursor.fetchone()
        if not usuario:
            return None

        return generar_token(
            usuario['id'], 1, usuario['usuario'], usuario['nombre'],
            usuario['cliente_id'], usuario['clave'], usuario['nombre_cliente'],
            usuario['id_grupo'], None,
        )
    finally:
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()

def verificar_token(token):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return None  # Token expirado
    except jwt.InvalidTokenError:
        return None  # Token inválido

def verificar_token_con_gracia(token, minutos_gracia=60):
    """Verifica el token ignorando la expiración si no lleva más de `minutos_gracia` expirado."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=['HS256'], options={"verify_exp": False})
        exp = payload.get('exp', 0)
        ahora = datetime.datetime.utcnow().timestamp()
        if exp > 0 and (ahora - exp) > minutos_gracia * 60:
            return None  # Expirado más allá de la gracia permitida
        return payload
    except jwt.InvalidTokenError:
        return None

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
