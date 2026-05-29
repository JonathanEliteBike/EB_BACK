import os
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

# --- DB CONFIG FROM .env OR DEFAULTS ---
db_config = {
    'host': os.getenv('MYSQL_HOST', '127.0.0.1'),
    'user': os.getenv('MYSQL_USER', 'root'),
    'password': os.getenv('MYSQL_PASSWORD', 'root'),
    'database': os.getenv('MYSQL_DATABASE', 'elite_bike'),
    'port': int(os.getenv('MYSQL_PORT', 3306)),
    'connection_timeout': 10,
}

def obtener_conexion():
    try:
        conn = mysql.connector.connect(**db_config)
        return conn
    except Exception as e:
        print(f"❌ Error al conectar a MySQL: {e}")
        return None
