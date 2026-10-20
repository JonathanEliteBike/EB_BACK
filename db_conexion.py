import os
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

_base = {
    'user':        os.getenv('MYSQL_USER', 'app_user'),
    'password':    os.getenv('MYSQL_PASSWORD', '1234'),
    'database':    os.getenv('MYSQL_DATABASE', 'elite_bike'),
    'charset':     'utf8mb4',
    'use_unicode': True,
}

# En Linux usar TCP; el socket también funciona pero TCP es más estable entre versiones
db_config = {
    **_base,
    'host': os.getenv('MYSQL_HOST', '127.0.0.1'),
    'port': int(os.getenv('MYSQL_PORT', 3306)),
}

# Usuarios alternativos a intentar si el principal falla por acceso denegado
_FALLBACK_USERS = ['app_user', 'root']


def obtener_conexion():
    # Primer intento con la config normal
    try:
        conn = mysql.connector.connect(**db_config)
        return conn
    except mysql.connector.errors.ProgrammingError as e:
        if '1698' not in str(e) and 'Access denied' not in str(e):
            print(f"❌ Error al conectar a MySQL: {e}")
            return None
        # Si es Access Denied, probar usuarios alternativos
        for user in _FALLBACK_USERS:
            if user == db_config.get('user'):
                continue
            try:
                fallback = {**db_config, 'user': user}
                conn = mysql.connector.connect(**fallback)
                return conn
            except Exception:
                pass
        print(f"❌ Error al conectar a MySQL: {e}")
        return None
    except Exception as e:
        print(f"❌ Error al conectar a MySQL: {e}")
        return None
