"""
Lógica de negocio del submódulo Asignaciones de Importaciones.
Relaciona mercancía física de un embarque (importaciones) con la demanda
de Proyecciones (forecast_proyecciones, vía services/proyecciones_service.py)
y con pedidos reales de Odoo, sin modificar ninguno de esos dos módulos.
"""
import logging

import mysql.connector

from db_conexion import obtener_conexion


class AsignacionesError(Exception):
    """Error de negocio con código estable para el frontend (ver sección 30 del spec)."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


TABLAS_SQL = [
    """
    CREATE TABLE IF NOT EXISTS importacion_productos (
      id                 INT AUTO_INCREMENT PRIMARY KEY,
      importacion_id     INT NOT NULL,
      periodo            VARCHAR(20) NOT NULL,
      sku                VARCHAR(64) NOT NULL,
      sku_norm           VARCHAR(64) NOT NULL,
      descripcion        VARCHAR(255) NULL,
      cantidad_embarcada INT NOT NULL,
      created_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
      updated_at         DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      CONSTRAINT fk_prod_importacion FOREIGN KEY (importacion_id) REFERENCES importaciones(id),
      CONSTRAINT chk_prod_cantidad CHECK (cantidad_embarcada >= 0),
      UNIQUE KEY uq_producto_embarque (importacion_id, sku_norm),
      KEY idx_prod_sku (sku_norm),
      KEY idx_prod_periodo (periodo)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS importacion_asignaciones (
      id                       INT AUTO_INCREMENT PRIMARY KEY,
      importacion_producto_id  INT NOT NULL,
      clave_cliente            VARCHAR(10) NOT NULL,
      cantidad_proyectada      INT NOT NULL DEFAULT 0,
      cantidad_asignada        INT NOT NULL DEFAULT 0,
      prioridad                INT NOT NULL,
      estado                   ENUM('ACTIVA','CANCELADA') NOT NULL DEFAULT 'ACTIVA',
      usuario_id               INT NULL,
      created_at               DATETIME DEFAULT CURRENT_TIMESTAMP,
      updated_at               DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      CONSTRAINT fk_asig_producto FOREIGN KEY (importacion_producto_id) REFERENCES importacion_productos(id),
      CONSTRAINT fk_asig_cliente FOREIGN KEY (clave_cliente) REFERENCES clientes(clave),
      CONSTRAINT chk_asig_cantidad CHECK (cantidad_asignada >= 0),
      UNIQUE KEY uq_asignacion_producto_cliente (importacion_producto_id, clave_cliente),
      KEY idx_asig_cliente (clave_cliente),
      KEY idx_asig_estado (estado)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS importacion_sobrantes_ventas (
      id                       INT AUTO_INCREMENT PRIMARY KEY,
      importacion_producto_id  INT NOT NULL,
      clave_cliente            VARCHAR(10) NOT NULL,
      cantidad                 INT NOT NULL,
      numero_pedido_odoo       VARCHAR(64) NULL,
      estado                   ENUM('PENDIENTE_VALIDACION','VALIDADO','CANCELADO') NOT NULL DEFAULT 'PENDIENTE_VALIDACION',
      created_by               INT NULL,
      created_at               DATETIME DEFAULT CURRENT_TIMESTAMP,
      updated_at               DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      CONSTRAINT fk_venta_producto FOREIGN KEY (importacion_producto_id) REFERENCES importacion_productos(id),
      CONSTRAINT fk_venta_cliente FOREIGN KEY (clave_cliente) REFERENCES clientes(clave),
      CONSTRAINT chk_venta_cantidad CHECK (cantidad > 0),
      UNIQUE KEY uq_pedido_odoo (numero_pedido_odoo),
      KEY idx_venta_producto (importacion_producto_id),
      KEY idx_venta_estado (estado)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS importacion_movimientos (
      id                       INT AUTO_INCREMENT PRIMARY KEY,
      importacion_producto_id  INT NOT NULL,
      tipo_movimiento          ENUM('ENTRADA','ASIGNACION','LIBERACION','SOBRANTE',
                                     'RESERVA_SOBRANTE','VENTA_SOBRANTE','CANCELACION','AJUSTE') NOT NULL,
      cantidad                 INT NOT NULL,
      clave_cliente            VARCHAR(10) NULL,
      referencia_externa       VARCHAR(64) NULL,
      usuario_id               INT NULL,
      metadata_json            JSON NULL,
      created_at               DATETIME DEFAULT CURRENT_TIMESTAMP,
      CONSTRAINT fk_mov_producto FOREIGN KEY (importacion_producto_id) REFERENCES importacion_productos(id),
      KEY idx_mov_producto (importacion_producto_id),
      KEY idx_mov_tipo (tipo_movimiento),
      KEY idx_mov_cliente (clave_cliente)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
]
