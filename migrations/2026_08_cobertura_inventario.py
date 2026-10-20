"""
Migration: crear tabla forecast_inventario_megamo
Almacena las cantidades de bicicletas Megamo por llegar, agrupadas por periodo y SKU.
Idempotente: usa CREATE TABLE IF NOT EXISTS.
Uso: python migrations/2026_08_cobertura_inventario.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db_conexion import obtener_conexion

DDL = """
CREATE TABLE IF NOT EXISTS forecast_inventario_megamo (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    periodo     VARCHAR(50)  NOT NULL,
    sku         VARCHAR(255) NOT NULL,
    cantidad    INT          NOT NULL DEFAULT 0,
    descripcion VARCHAR(500) NULL,
    subido_en   DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_inventario_periodo_sku (periodo, sku)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

def run():
    conn = obtener_conexion()
    cur  = conn.cursor()
    cur.execute(DDL)
    conn.commit()
    cur.close()
    conn.close()
    print("[OK] Tabla forecast_inventario_megamo creada o ya existente.")

if __name__ == '__main__':
    run()
