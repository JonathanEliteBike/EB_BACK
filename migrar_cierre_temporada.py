"""
Migración: Agrega columnas temporada_cerrada y fecha_cierre_temporada a la tabla clientes.
Ejecutar UNA SOLA VEZ en local.
"""
import mysql.connector
from dotenv import load_dotenv
import os

load_dotenv()

conn = mysql.connector.connect(
    host=os.getenv('MYSQL_HOST', '127.0.0.1'),
    user=os.getenv('MYSQL_USER', 'root'),
    password=os.getenv('MYSQL_PASSWORD', '1234'),
    database=os.getenv('MYSQL_DATABASE', 'elite_bike'),
    port=int(os.getenv('MYSQL_PORT', 3306))
)
cursor = conn.cursor()

alteraciones = [
    "ALTER TABLE clientes ADD COLUMN temporada_cerrada TINYINT(1) NOT NULL DEFAULT 0",
    "ALTER TABLE clientes ADD COLUMN fecha_cierre_temporada DATE NULL",
]

for sql in alteraciones:
    col = sql.split('ADD COLUMN')[1].strip().split()[0]
    cursor.execute("SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'clientes' AND COLUMN_NAME = %s", (col,))
    if cursor.fetchone()[0] == 0:
        cursor.execute(sql)
        print(f"[OK] Columna '{col}' agregada.")
    else:
        print(f"[SKIP] Columna '{col}' ya existe.")

conn.commit()
cursor.close()
conn.close()
print("Migración completada.")
