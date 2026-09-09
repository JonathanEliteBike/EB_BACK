"""
Lógica de negocio del submódulo Asignaciones de Importaciones.
Relaciona mercancía física de un embarque (importaciones) con la demanda
de Proyecciones (forecast_proyecciones, vía services/proyecciones_service.py)
y con pedidos reales de Odoo, sin modificar ninguno de esos dos módulos.
"""
import logging

import mysql.connector

from db_conexion import obtener_conexion
from utils.odoo_utils import get_odoo_models, ODOO_DB, ODOO_PASSWORD, ODOO_COMPANY_ID


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


from services.proyecciones_service import _norm_sku, demanda_neta_por_cliente, _PRIORIDAD_MAP


def _disponible_producto(cursor, producto_id: int) -> int:
    cursor.execute(
        "SELECT COALESCE(SUM(cantidad), 0) AS disponible FROM importacion_movimientos "
        "WHERE importacion_producto_id = %s",
        (producto_id,),
    )
    return int(cursor.fetchone()["disponible"])


def _registrar_movimiento(cursor, producto_id, tipo_movimiento, cantidad, clave_cliente=None,
                           referencia_externa=None, usuario_id=None, metadata=None):
    import json
    cursor.execute(
        "INSERT INTO importacion_movimientos "
        "(importacion_producto_id, tipo_movimiento, cantidad, clave_cliente, referencia_externa, "
        "usuario_id, metadata_json) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (producto_id, tipo_movimiento, cantidad, clave_cliente, referencia_externa, usuario_id,
         json.dumps(metadata) if metadata else None),
    )


def crear_producto(importacion_id: int, sku: str, cantidad_embarcada, periodo: str,
                    descripcion: str = None, usuario_id: int = None) -> dict:
    if not isinstance(cantidad_embarcada, int) or cantidad_embarcada < 0:
        raise AsignacionesError("SKU_INVALIDO", "La cantidad embarcada debe ser un entero >= 0")
    sku = (sku or "").strip()
    if not sku:
        raise AsignacionesError("SKU_INVALIDO", "El SKU es obligatorio")
    periodo = (periodo or "").strip()
    if not periodo:
        raise AsignacionesError("SKU_INVALIDO", "El periodo es obligatorio")
    sku_norm = _norm_sku(sku)

    conn = obtener_conexion()
    if not conn:
        raise AsignacionesError("DB_NO_DISPONIBLE", "Sin conexión a BD", 500)
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id FROM importaciones WHERE id = %s", (importacion_id,))
        if not cursor.fetchone():
            raise AsignacionesError("IMPORTACION_NO_EXISTE", "El embarque no existe", 404)

        cursor.execute(
            "SELECT id FROM importacion_productos WHERE importacion_id = %s AND sku_norm = %s",
            (importacion_id, sku_norm),
        )
        if cursor.fetchone():
            raise AsignacionesError("SKU_DUPLICADO", "Este SKU ya está registrado en el embarque", 409)

        cursor.execute(
            "INSERT INTO importacion_productos "
            "(importacion_id, periodo, sku, sku_norm, descripcion, cantidad_embarcada) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (importacion_id, periodo, sku, sku_norm, descripcion, cantidad_embarcada),
        )
        producto_id = cursor.lastrowid
        _registrar_movimiento(cursor, producto_id, "ENTRADA", cantidad_embarcada, usuario_id=usuario_id)
        conn.commit()

        cursor.execute("SELECT * FROM importacion_productos WHERE id = %s", (producto_id,))
        return cursor.fetchone()
    except AsignacionesError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def listar_productos(importacion_id: int) -> list:
    conn = obtener_conexion()
    if not conn:
        raise AsignacionesError("DB_NO_DISPONIBLE", "Sin conexión a BD", 500)
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id FROM importaciones WHERE id = %s", (importacion_id,))
        if not cursor.fetchone():
            raise AsignacionesError("IMPORTACION_NO_EXISTE", "El embarque no existe", 404)

        cursor.execute(
            "SELECT * FROM importacion_productos WHERE importacion_id = %s ORDER BY sku",
            (importacion_id,),
        )
        productos = cursor.fetchall()
        resultado = []
        for p in productos:
            disponible = _disponible_producto(cursor, p["id"])
            cursor.execute(
                "SELECT COALESCE(SUM(cantidad_asignada), 0) AS total FROM importacion_asignaciones "
                "WHERE importacion_producto_id = %s AND estado = 'ACTIVA'",
                (p["id"],),
            )
            asignado = int(cursor.fetchone()["total"])
            cursor.execute(
                "SELECT COALESCE(SUM(cantidad), 0) AS total FROM importacion_sobrantes_ventas "
                "WHERE importacion_producto_id = %s AND estado = 'VALIDADO'",
                (p["id"],),
            )
            vendido = int(cursor.fetchone()["total"])
            resultado.append({
                **p,
                "cantidad_asignada": asignado,
                "cantidad_vendida": vendido,
                "cantidad_sobrante": max(p["cantidad_embarcada"] - asignado, 0),
                "cantidad_disponible": int(disponible),
            })
        return resultado
    finally:
        conn.close()


def actualizar_producto(producto_id: int, cantidad_embarcada=None, descripcion: str = None,
                         usuario_id: int = None) -> dict:
    conn = obtener_conexion()
    if not conn:
        raise AsignacionesError("DB_NO_DISPONIBLE", "Sin conexión a BD", 500)
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM importacion_productos WHERE id = %s FOR UPDATE", (producto_id,))
        producto = cursor.fetchone()
        if not producto:
            raise AsignacionesError("PRODUCTO_NO_EXISTE", "El producto no existe", 404)

        if cantidad_embarcada is not None:
            if not isinstance(cantidad_embarcada, int) or cantidad_embarcada < 0:
                raise AsignacionesError("AJUSTE_INVALIDO", "La cantidad embarcada debe ser un entero >= 0")
            cursor.execute(
                "SELECT COALESCE(SUM(cantidad_asignada), 0) AS total FROM importacion_asignaciones "
                "WHERE importacion_producto_id = %s AND estado = 'ACTIVA'",
                (producto_id,),
            )
            asignado = cursor.fetchone()["total"]
            if cantidad_embarcada < asignado:
                raise AsignacionesError(
                    "AJUSTE_INVALIDO",
                    f"No puedes bajar la cantidad embarcada ({cantidad_embarcada}) por debajo "
                    f"de lo ya asignado ({asignado})",
                )
            diferencia = cantidad_embarcada - producto["cantidad_embarcada"]
            if diferencia != 0:
                cursor.execute(
                    "UPDATE importacion_productos SET cantidad_embarcada = %s WHERE id = %s",
                    (cantidad_embarcada, producto_id),
                )
                _registrar_movimiento(cursor, producto_id, "AJUSTE", diferencia, usuario_id=usuario_id)

        if descripcion is not None:
            cursor.execute(
                "UPDATE importacion_productos SET descripcion = %s WHERE id = %s",
                (descripcion, producto_id),
            )

        conn.commit()
        cursor.execute("SELECT * FROM importacion_productos WHERE id = %s", (producto_id,))
        return cursor.fetchone()
    except AsignacionesError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def asignar(producto_id: int, asignaciones: list, usuario_id: int = None) -> dict:
    if not asignaciones:
        raise AsignacionesError("ASIGNACION_INVALIDA", "Debes enviar al menos una asignación")
    for item in asignaciones:
        cantidad = item.get("cantidad")
        if not isinstance(cantidad, int) or cantidad <= 0:
            raise AsignacionesError("ASIGNACION_INVALIDA", "La cantidad de cada asignación debe ser un entero > 0")
        if not item.get("clave_cliente"):
            raise AsignacionesError("CLIENTE_NO_EXISTE", "Falta clave_cliente en una de las asignaciones")

    conn = obtener_conexion()
    if not conn:
        raise AsignacionesError("DB_NO_DISPONIBLE", "Sin conexión a BD", 500)
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id FROM importacion_productos WHERE id = %s FOR UPDATE", (producto_id,))
        if not cursor.fetchone():
            raise AsignacionesError("PRODUCTO_NO_EXISTE", "El producto no existe", 404)

        for item in asignaciones:
            clave = item["clave_cliente"].strip().upper()
            cursor.execute("SELECT clave FROM clientes WHERE clave = %s", (clave,))
            if not cursor.fetchone():
                raise AsignacionesError("CLIENTE_NO_EXISTE", f"El cliente {clave} no existe", 404)

        disponible = _disponible_producto(cursor, producto_id)
        total_solicitado = sum(item["cantidad"] for item in asignaciones)
        if total_solicitado > disponible:
            raise AsignacionesError(
                "STOCK_INSUFICIENTE",
                f"Disponible: {disponible}, solicitado: {total_solicitado}",
                409,
            )

        for item in asignaciones:
            clave = item["clave_cliente"].strip().upper()
            cantidad = item["cantidad"]
            prio_info = _PRIORIDAD_MAP.get(clave, (999, clave))

            cursor.execute(
                "SELECT id FROM importacion_asignaciones "
                "WHERE importacion_producto_id = %s AND clave_cliente = %s FOR UPDATE",
                (producto_id, clave),
            )
            existente = cursor.fetchone()
            if existente:
                cursor.execute(
                    "UPDATE importacion_asignaciones SET cantidad_asignada = cantidad_asignada + %s, "
                    "estado = 'ACTIVA', usuario_id = %s, prioridad = %s WHERE id = %s",
                    (cantidad, usuario_id, prio_info[0], existente["id"]),
                )
            else:
                cursor.execute(
                    "INSERT INTO importacion_asignaciones "
                    "(importacion_producto_id, clave_cliente, cantidad_proyectada, cantidad_asignada, "
                    "prioridad, estado, usuario_id) VALUES (%s, %s, %s, %s, %s, 'ACTIVA', %s)",
                    (producto_id, clave, cantidad, cantidad, prio_info[0], usuario_id),
                )
            _registrar_movimiento(cursor, producto_id, "ASIGNACION", -cantidad, clave_cliente=clave,
                                   usuario_id=usuario_id)

        conn.commit()
        return {"producto_id": producto_id, "disponible_restante": disponible - total_solicitado}
    except AsignacionesError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def crear_venta_sobrante(producto_id: int, clave_cliente: str, cantidad, numero_pedido_odoo: str = None,
                          usuario_id: int = None) -> dict:
    if not isinstance(cantidad, int) or cantidad <= 0:
        raise AsignacionesError("VENTA_INVALIDA", "La cantidad debe ser un entero > 0")
    clave = (clave_cliente or "").strip().upper()
    if not clave:
        raise AsignacionesError("CLIENTE_NO_EXISTE", "Falta clave_cliente")
    folio = (numero_pedido_odoo or "").strip() or None

    conn = obtener_conexion()
    if not conn:
        raise AsignacionesError("DB_NO_DISPONIBLE", "Sin conexión a BD", 500)
    try:
        cursor = conn.cursor(dictionary=True)

        if folio:
            cursor.execute(
                "SELECT * FROM importacion_sobrantes_ventas WHERE numero_pedido_odoo = %s", (folio,)
            )
            existente = cursor.fetchone()
            if existente:
                return existente  # idempotente: no duplica, devuelve la venta ya creada

        cursor.execute("SELECT id FROM importacion_productos WHERE id = %s FOR UPDATE", (producto_id,))
        if not cursor.fetchone():
            raise AsignacionesError("PRODUCTO_NO_EXISTE", "El producto no existe", 404)

        cursor.execute("SELECT clave FROM clientes WHERE clave = %s", (clave,))
        if not cursor.fetchone():
            raise AsignacionesError("CLIENTE_NO_EXISTE", f"El cliente {clave} no existe", 404)

        disponible = _disponible_producto(cursor, producto_id)
        if cantidad > disponible:
            raise AsignacionesError(
                "SOBRANTE_INSUFICIENTE", f"Disponible: {disponible}, solicitado: {cantidad}", 409
            )

        try:
            cursor.execute(
                "INSERT INTO importacion_sobrantes_ventas "
                "(importacion_producto_id, clave_cliente, cantidad, numero_pedido_odoo, estado, created_by) "
                "VALUES (%s, %s, %s, %s, 'PENDIENTE_VALIDACION', %s)",
                (producto_id, clave, cantidad, folio, usuario_id),
            )
        except mysql.connector.errors.IntegrityError:
            conn.rollback()
            if not folio:
                raise
            cursor.execute(
                "SELECT * FROM importacion_sobrantes_ventas WHERE numero_pedido_odoo = %s", (folio,)
            )
            existente = cursor.fetchone()
            if existente:
                return existente
            raise AsignacionesError(
                "PEDIDO_ODOO_YA_ASOCIADO", f"El pedido {folio} ya está asociado a otra venta", 409
            )

        venta_id = cursor.lastrowid
        _registrar_movimiento(cursor, producto_id, "VENTA_SOBRANTE", -cantidad, clave_cliente=clave,
                               referencia_externa=folio, usuario_id=usuario_id)
        conn.commit()
        cursor.execute("SELECT * FROM importacion_sobrantes_ventas WHERE id = %s", (venta_id,))
        return cursor.fetchone()
    except AsignacionesError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def recalcular_propuesta(importacion_id: int, periodo_filtro: str = None) -> list:
    conn = obtener_conexion()
    if not conn:
        raise AsignacionesError("DB_NO_DISPONIBLE", "Sin conexión a BD", 500)
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id FROM importaciones WHERE id = %s", (importacion_id,))
        if not cursor.fetchone():
            raise AsignacionesError("IMPORTACION_NO_EXISTE", "El embarque no existe", 404)

        sql = "SELECT * FROM importacion_productos WHERE importacion_id = %s"
        params = [importacion_id]
        if periodo_filtro:
            sql += " AND periodo = %s"
            params.append(periodo_filtro)
        cursor.execute(sql, params)
        productos = cursor.fetchall()

        disponibles = {p["id"]: _disponible_producto(cursor, p["id"]) for p in productos}
    finally:
        conn.close()

    if not productos:
        return []

    por_periodo: dict = {}
    for p in productos:
        por_periodo.setdefault(p["periodo"], []).append(p)

    propuestas = []
    for periodo, prods in por_periodo.items():
        skus_norm = [p["sku_norm"] for p in prods]
        try:
            demanda = demanda_neta_por_cliente(periodo, skus_norm)
            proyecciones_disponibles = True
        except Exception:
            logging.exception("Proyecciones no disponibles al recalcular embarque %s", importacion_id)
            demanda = {}
            proyecciones_disponibles = False

        for p in prods:
            demanda_sku = demanda.get(p["sku_norm"], {})
            clientes_ordenados = sorted(
                demanda_sku.items(),
                key=lambda item: (_PRIORIDAD_MAP.get(item[0].strip().upper(), (999, ""))[0], item[0]),
            )
            disponible = disponibles[p["id"]]
            restante = disponible
            sugerido = []
            for clave, cantidad_neta in clientes_ordenados:
                asignar_cant = min(cantidad_neta, restante)
                if asignar_cant <= 0:
                    continue
                prio_info = _PRIORIDAD_MAP.get(clave.strip().upper(), (999, clave))
                sugerido.append({
                    "clave_cliente": clave,
                    "prioridad": prio_info[0],
                    "cantidad_proyectada": cantidad_neta,
                    "cantidad_sugerida": asignar_cant,
                })
                restante -= asignar_cant
            propuestas.append({
                "producto_id": p["id"],
                "sku": p["sku"],
                "periodo": periodo,
                "cantidad_embarcada": p["cantidad_embarcada"],
                "disponible": disponible,
                "proyecciones_disponibles": proyecciones_disponibles,
                "propuesta": sugerido,
                "sobrante_estimado": restante,
            })
    return propuestas


def cancelar_venta(venta_id: int, usuario_id: int = None) -> dict:
    conn = obtener_conexion()
    if not conn:
        raise AsignacionesError("DB_NO_DISPONIBLE", "Sin conexión a BD", 500)
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM importacion_sobrantes_ventas WHERE id = %s FOR UPDATE", (venta_id,))
        venta = cursor.fetchone()
        if not venta:
            raise AsignacionesError("VENTA_NO_EXISTE", "La venta no existe", 404)
        if venta["estado"] == "CANCELADO":
            raise AsignacionesError("VENTA_YA_CANCELADA", "La venta ya estaba cancelada", 409)

        cursor.execute(
            "UPDATE importacion_sobrantes_ventas SET estado = 'CANCELADO' WHERE id = %s", (venta_id,)
        )
        _registrar_movimiento(
            cursor, venta["importacion_producto_id"], "CANCELACION", venta["cantidad"],
            clave_cliente=venta["clave_cliente"], referencia_externa=venta["numero_pedido_odoo"],
            usuario_id=usuario_id,
        )
        conn.commit()
        cursor.execute("SELECT * FROM importacion_sobrantes_ventas WHERE id = %s", (venta_id,))
        return cursor.fetchone()
    except AsignacionesError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def validar_venta_odoo(venta_id: int, numero_pedido_odoo: str = None) -> dict:
    conn = obtener_conexion()
    if not conn:
        raise AsignacionesError("DB_NO_DISPONIBLE", "Sin conexión a BD", 500)
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM importacion_sobrantes_ventas WHERE id = %s", (venta_id,))
        venta = cursor.fetchone()
        if not venta:
            raise AsignacionesError("VENTA_NO_EXISTE", "La venta no existe", 404)
        if venta["estado"] == "CANCELADO":
            raise AsignacionesError("VENTA_YA_CANCELADA", "No se puede validar una venta cancelada", 409)

        folio = (numero_pedido_odoo or venta["numero_pedido_odoo"] or "").strip()
        if not folio:
            raise AsignacionesError("PEDIDO_ODOO_INVALIDO", "Falta el número de pedido de Odoo")

        if folio != venta["numero_pedido_odoo"]:
            cursor.execute(
                "UPDATE importacion_sobrantes_ventas SET numero_pedido_odoo = %s WHERE id = %s",
                (folio, venta_id),
            )
            conn.commit()

        cursor.execute(
            "SELECT sku_norm FROM importacion_productos WHERE id = %s",
            (venta["importacion_producto_id"],),
        )
        producto = cursor.fetchone()
        sku_norm_esperado = producto["sku_norm"] if producto else None
    finally:
        conn.close()

    uid, models, err = get_odoo_models()
    if not uid:
        logging.error("Odoo no disponible al validar pedido %s: %s", folio, err)
        raise AsignacionesError(
            "PEDIDO_ODOO_NO_DISPONIBLE", "No fue posible validar el pedido en Odoo, intenta de nuevo", 503
        )

    try:
        pedidos = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD, "sale.order", "search_read",
            [[("company_id", "=", ODOO_COMPANY_ID), ("name", "=", folio)]],
            {"fields": ["id", "name", "partner_id", "state", "order_line"]},
        )
    except Exception:
        logging.exception("Error consultando sale.order %s en Odoo", folio)
        raise AsignacionesError(
            "PEDIDO_ODOO_NO_DISPONIBLE", "No fue posible validar el pedido en Odoo, intenta de nuevo", 503
        )

    if not pedidos:
        raise AsignacionesError("PEDIDO_ODOO_NO_EXISTE", f"No existe el pedido {folio} en Odoo", 404)
    pedido = pedidos[0]

    partner_ref = None
    try:
        partner = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD, "res.partner", "read",
            [[pedido["partner_id"][0]]], {"fields": ["ref"]},
        )
        partner_ref = (partner[0].get("ref") or "").strip().upper() if partner else None
    except Exception:
        logging.exception("Error leyendo res.partner para pedido %s", folio)

    if partner_ref and partner_ref != venta["clave_cliente"].strip().upper():
        raise AsignacionesError(
            "PEDIDO_ODOO_INVALIDO",
            f"El pedido {folio} pertenece a {partner_ref}, no a {venta['clave_cliente']}",
        )

    lineas = []
    if pedido.get("order_line"):
        try:
            lineas = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD, "sale.order.line", "read",
                [pedido["order_line"]], {"fields": ["product_id", "product_uom_qty"]},
            )
        except Exception:
            logging.exception("Error leyendo sale.order.line para pedido %s", folio)
            raise AsignacionesError(
                "PEDIDO_ODOO_NO_DISPONIBLE", "No fue posible validar el pedido en Odoo, intenta de nuevo", 503
            )

    producto_ids = list({linea["product_id"][0] for linea in lineas if linea.get("product_id")})
    codigos = {}
    if producto_ids:
        try:
            info = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD, "product.product", "read",
                [producto_ids], {"fields": ["default_code"]},
            )
        except Exception:
            logging.exception("Error leyendo product.product para pedido %s", folio)
            raise AsignacionesError(
                "PEDIDO_ODOO_NO_DISPONIBLE", "No fue posible validar el pedido en Odoo, intenta de nuevo", 503
            )
        codigos = {row["id"]: row.get("default_code") for row in info}

    cantidad_en_pedido = 0
    for linea in lineas:
        prod = linea.get("product_id")
        default_code = codigos.get(prod[0]) if prod else None
        if default_code and _norm_sku(default_code) == sku_norm_esperado:
            cantidad_en_pedido += linea.get("product_uom_qty", 0)

    if cantidad_en_pedido < venta["cantidad"]:
        raise AsignacionesError(
            "PEDIDO_ODOO_INVALIDO",
            f"El pedido {folio} solo tiene {cantidad_en_pedido} unidades del SKU, "
            f"se esperaban {venta['cantidad']}",
        )

    conn2 = obtener_conexion()
    if not conn2:
        raise AsignacionesError("DB_NO_DISPONIBLE", "Sin conexión a BD", 500)
    try:
        cursor2 = conn2.cursor(dictionary=True)
        cursor2.execute(
            "UPDATE importacion_sobrantes_ventas SET estado = 'VALIDADO' WHERE id = %s", (venta_id,)
        )
        conn2.commit()
        cursor2.execute("SELECT * FROM importacion_sobrantes_ventas WHERE id = %s", (venta_id,))
        return cursor2.fetchone()
    finally:
        conn2.close()


def obtener_detalle_producto(producto_id: int) -> dict:
    conn = obtener_conexion()
    if not conn:
        raise AsignacionesError("DB_NO_DISPONIBLE", "Sin conexión a BD", 500)
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM importacion_productos WHERE id = %s", (producto_id,))
        producto = cursor.fetchone()
        if not producto:
            raise AsignacionesError("PRODUCTO_NO_EXISTE", "El producto no existe", 404)

        cursor.execute(
            "SELECT * FROM importacion_asignaciones WHERE importacion_producto_id = %s ORDER BY prioridad",
            (producto_id,),
        )
        asignaciones = cursor.fetchall()

        cursor.execute(
            "SELECT * FROM importacion_sobrantes_ventas WHERE importacion_producto_id = %s "
            "ORDER BY created_at DESC",
            (producto_id,),
        )
        ventas = cursor.fetchall()
    finally:
        conn.close()

    propuesta = recalcular_propuesta(producto["importacion_id"], producto["periodo"])
    fila = next((p for p in propuesta if p["producto_id"] == producto_id), None)

    return {
        "producto": producto,
        "proyecciones": fila["propuesta"] if fila else [],
        "asignaciones": asignaciones,
        "sobrantes_ventas": ventas,
    }


def resumen_embarque(importacion_id: int) -> dict:
    conn = obtener_conexion()
    if not conn:
        raise AsignacionesError("DB_NO_DISPONIBLE", "Sin conexión a BD", 500)
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, referencia, nombre, estado FROM importaciones WHERE id = %s", (importacion_id,)
        )
        embarque = cursor.fetchone()
        if not embarque:
            raise AsignacionesError("IMPORTACION_NO_EXISTE", "El embarque no existe", 404)
    finally:
        conn.close()

    productos = listar_productos(importacion_id)
    kpis = {
        "unidades_embarcadas": sum(p["cantidad_embarcada"] for p in productos),
        "unidades_asignadas": sum(p["cantidad_asignada"] for p in productos),
        "unidades_sobrantes": sum(p["cantidad_sobrante"] for p in productos),
        "unidades_vendidas": sum(p["cantidad_vendida"] for p in productos),
        "unidades_disponibles": sum(p["cantidad_disponible"] for p in productos),
    }
    return {"embarque": embarque, "kpis": kpis, "productos": productos}


def listar_movimientos(importacion_id: int) -> list:
    conn = obtener_conexion()
    if not conn:
        raise AsignacionesError("DB_NO_DISPONIBLE", "Sin conexión a BD", 500)
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id FROM importaciones WHERE id = %s", (importacion_id,))
        if not cursor.fetchone():
            raise AsignacionesError("IMPORTACION_NO_EXISTE", "El embarque no existe", 404)
        cursor.execute(
            "SELECT m.* FROM importacion_movimientos m "
            "JOIN importacion_productos p ON p.id = m.importacion_producto_id "
            "WHERE p.importacion_id = %s ORDER BY m.created_at DESC, m.id DESC",
            (importacion_id,),
        )
        return cursor.fetchall()
    finally:
        conn.close()
