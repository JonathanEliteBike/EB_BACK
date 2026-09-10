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
      mes_objetivo             DATE NULL,
      origen                   ENUM('INICIAL','REASIGNACION') NOT NULL DEFAULT 'INICIAL',
      cantidad_proyectada      INT NOT NULL DEFAULT 0,
      cantidad_asignada        INT NOT NULL DEFAULT 0,
      prioridad                INT NOT NULL,
      estado                   ENUM('RESERVADA','PENDIENTE_CONFIRMACION','CONFIRMADA','RECHAZADA','CANCELADA')
                                 NOT NULL DEFAULT 'RESERVADA',
      usuario_id               INT NULL,
      confirmada_at            DATETIME NULL,
      confirmada_por           INT NULL,
      created_at               DATETIME DEFAULT CURRENT_TIMESTAMP,
      updated_at               DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      CONSTRAINT fk_asig_producto FOREIGN KEY (importacion_producto_id) REFERENCES importacion_productos(id),
      CONSTRAINT fk_asig_cliente FOREIGN KEY (clave_cliente) REFERENCES clientes(clave),
      CONSTRAINT chk_asig_cantidad CHECK (cantidad_asignada >= 0),
      UNIQUE KEY uq_reserva (importacion_producto_id, clave_cliente, mes_objetivo, origen),
      KEY idx_asig_cliente (clave_cliente),
      KEY idx_asig_estado (estado),
      KEY idx_asig_mes (mes_objetivo)
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
                                     'RESERVA_SOBRANTE','VENTA_SOBRANTE','CANCELACION','AJUSTE',
                                     'RESERVA','REASIGNACION','RECHAZO_RESERVA') NOT NULL,
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


from services.proyecciones_service import (
    _norm_sku, demanda_neta_por_cliente, demanda_neta_por_cliente_mensual, _PRIORIDAD_MAP,
)

# ── Meses del periodo MY27 (mismo orden cronológico que forecast_proyecciones) ──
# mayo..diciembre pertenecen a year1; enero..abril a year2 (periodo "2026-2027").
MESES_ORDEN = [
    "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
    "noviembre", "diciembre", "enero", "febrero", "marzo", "abril",
]
# Meses ya despachados: informativos, nunca reciben reserva ni reasignación.
MESES_DESPACHADOS = frozenset({"mayo", "junio", "julio"})
_MESES_YEAR2 = frozenset({"enero", "febrero", "marzo", "abril"})
_MES_NUM = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}
_NUM_MES = {v: k for k, v in _MES_NUM.items()}

# Estados de reserva que "ocupan" stock: cuentan como demanda cubierta y como
# reservado en los KPIs. RECHAZADA y CANCELADA quedan fuera.
_ESTADOS_VIGENTES = ("RESERVADA", "PENDIENTE_CONFIRMACION", "CONFIRMADA")
_SQL_ESTADOS_VIGENTES = "('RESERVADA', 'PENDIENTE_CONFIRMACION', 'CONFIRMADA')"


def _split_periodo(periodo: str):
    """'2026-2027' -> (2026, 2027). Lanza AsignacionesError si el formato es inválido."""
    import re
    m = re.match(r"^\s*(\d{4})-(\d{4})\s*$", str(periodo or ""))
    if not m:
        raise AsignacionesError("PERIODO_INVALIDO", f"Periodo con formato inválido: {periodo!r}", 400)
    return int(m.group(1)), int(m.group(2))


def _columna_a_fecha(periodo: str, columna: str):
    """('2026-2027', 'octubre') -> datetime.date(2026, 10, 1)."""
    import datetime as _dt
    col = (columna or "").strip().lower()
    if col not in _MES_NUM:
        raise AsignacionesError("MES_INVALIDO", f"Mes desconocido: {columna!r}", 400)
    year1, year2 = _split_periodo(periodo)
    year = year2 if col in _MESES_YEAR2 else year1
    return _dt.date(year, _MES_NUM[col], 1)


def _fecha_a_columna(fecha) -> str:
    """date/datetime/'YYYY-MM-DD' -> 'octubre'."""
    import datetime as _dt
    if isinstance(fecha, str):
        fecha = _dt.date.fromisoformat(fecha[:10])
    return _NUM_MES[fecha.month]


def _fecha_a_ym(fecha) -> str:
    """date/datetime -> 'YYYY-MM' (formato de mes en la API)."""
    return f"{fecha.year:04d}-{fecha.month:02d}"


def _parse_mes_arg(periodo: str, valor: str):
    """Acepta 'octubre' | '2026-10' | '2026-10-01' -> date día 1 dentro del periodo."""
    import datetime as _dt
    s = str(valor or "").strip().lower()
    if not s:
        raise AsignacionesError("MES_INVALIDO", "Falta el mes", 400)
    if s in _MES_NUM:
        return _columna_a_fecha(periodo, s)
    try:
        partes = s.split("-")
        year, month = int(partes[0]), int(partes[1])
        return _dt.date(year, month, 1)
    except (ValueError, IndexError):
        raise AsignacionesError("MES_INVALIDO", f"Mes con formato inválido: {valor!r}", 400)


def _meses_en_ventana(periodo: str, mes_desde, mes_hasta) -> list:
    """Lista de nombres de columna (cronológica) entre mes_desde y mes_hasta inclusive."""
    d_desde = _parse_mes_arg(periodo, mes_desde)
    d_hasta = _parse_mes_arg(periodo, mes_hasta)
    if d_hasta < d_desde:
        raise AsignacionesError("VENTANA_INVALIDA", "mes_hasta es anterior a mes_desde", 400)
    col_desde = _fecha_a_columna(d_desde)
    col_hasta = _fecha_a_columna(d_hasta)
    i, j = MESES_ORDEN.index(col_desde), MESES_ORDEN.index(col_hasta)
    return MESES_ORDEN[i:j + 1]


def _meses_reasignables(periodo: str, ventana_desde) -> list:
    """Meses anteriores a ventana_desde, dentro del periodo, excluyendo may/jun/jul."""
    d_desde = _parse_mes_arg(periodo, ventana_desde)
    col_desde = _fecha_a_columna(d_desde)
    tope = MESES_ORDEN.index(col_desde)
    return [m for m in MESES_ORDEN[:tope] if m not in MESES_DESPACHADOS]


def _migrar_esquema_reservas(cursor) -> list:
    """Adapta importacion_asignaciones/movimientos al modelo de reservas por mes.

    Idempotente: introspecciona information_schema y solo aplica lo que falta.
    Se invoca desde POST /importaciones/asignaciones/inicializar-tablas.
    Devuelve la lista de pasos aplicados (para logging/tests).
    """
    aplicados = []

    def _col_existe(tabla, col):
        cursor.execute(
            "SELECT 1 FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
            (tabla, col),
        )
        return cursor.fetchone() is not None

    def _col_tipo(tabla, col):
        cursor.execute(
            "SELECT COLUMN_TYPE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
            (tabla, col),
        )
        row = cursor.fetchone()
        return (row[0] if row else "") or ""

    def _indice_existe(tabla, nombre):
        cursor.execute(
            "SELECT 1 FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s LIMIT 1",
            (tabla, nombre),
        )
        return cursor.fetchone() is not None

    T = "importacion_asignaciones"

    if not _col_existe(T, "mes_objetivo"):
        cursor.execute(f"ALTER TABLE {T} ADD COLUMN mes_objetivo DATE NULL AFTER clave_cliente")
        aplicados.append("add mes_objetivo")
    if not _col_existe(T, "origen"):
        cursor.execute(
            f"ALTER TABLE {T} ADD COLUMN origen ENUM('INICIAL','REASIGNACION') "
            f"NOT NULL DEFAULT 'INICIAL' AFTER mes_objetivo"
        )
        aplicados.append("add origen")
    if not _col_existe(T, "confirmada_at"):
        cursor.execute(f"ALTER TABLE {T} ADD COLUMN confirmada_at DATETIME NULL AFTER usuario_id")
        aplicados.append("add confirmada_at")
    if not _col_existe(T, "confirmada_por"):
        cursor.execute(f"ALTER TABLE {T} ADD COLUMN confirmada_por INT NULL AFTER confirmada_at")
        aplicados.append("add confirmada_por")

    tipo_estado = _col_tipo(T, "estado").lower()
    if "reservada" not in tipo_estado:
        # Paso 1: enum ampliado que aún admite 'ACTIVA'
        cursor.execute(
            f"ALTER TABLE {T} MODIFY estado "
            f"ENUM('ACTIVA','RESERVADA','PENDIENTE_CONFIRMACION','CONFIRMADA','RECHAZADA','CANCELADA') "
            f"NOT NULL DEFAULT 'RESERVADA'"
        )
        cursor.execute(f"UPDATE {T} SET estado = 'RESERVADA' WHERE estado = 'ACTIVA'")
        # Paso 2: enum final sin 'ACTIVA'
        cursor.execute(
            f"ALTER TABLE {T} MODIFY estado "
            f"ENUM('RESERVADA','PENDIENTE_CONFIRMACION','CONFIRMADA','RECHAZADA','CANCELADA') "
            f"NOT NULL DEFAULT 'RESERVADA'"
        )
        aplicados.append("estado enum -> reservas")

    # El índice viejo cubre la FK sobre importacion_producto_id: hay que crear el
    # nuevo (que también lidera con esa columna) ANTES de poder soltar el viejo.
    if not _indice_existe(T, "uq_reserva"):
        cursor.execute(
            f"ALTER TABLE {T} ADD UNIQUE KEY uq_reserva "
            f"(importacion_producto_id, clave_cliente, mes_objetivo, origen)"
        )
        aplicados.append("add uq_reserva")
    if not _indice_existe(T, "idx_asig_mes"):
        cursor.execute(f"ALTER TABLE {T} ADD KEY idx_asig_mes (mes_objetivo)")
        aplicados.append("add idx_asig_mes")
    if _indice_existe(T, "uq_asignacion_producto_cliente"):
        cursor.execute(f"ALTER TABLE {T} DROP INDEX uq_asignacion_producto_cliente")
        aplicados.append("drop uq_asignacion_producto_cliente")

    tipo_mov = _col_tipo("importacion_movimientos", "tipo_movimiento").lower()
    if "'reserva'" not in tipo_mov or "reasignacion" not in tipo_mov or "rechazo_reserva" not in tipo_mov:
        cursor.execute(
            "ALTER TABLE importacion_movimientos MODIFY tipo_movimiento "
            "ENUM('ENTRADA','ASIGNACION','LIBERACION','SOBRANTE','RESERVA_SOBRANTE',"
            "'VENTA_SOBRANTE','CANCELACION','AJUSTE','RESERVA','REASIGNACION','RECHAZO_RESERVA') NOT NULL"
        )
        aplicados.append("movimientos enum +RESERVA/REASIGNACION/RECHAZO_RESERVA")

    return aplicados


def _disponible_producto(cursor, producto_id: int) -> int:
    cursor.execute(
        "SELECT COALESCE(SUM(cantidad), 0) AS disponible FROM importacion_movimientos "
        "WHERE importacion_producto_id = %s",
        (producto_id,),
    )
    return int(cursor.fetchone()["disponible"])


def _verificar_pertenencia(producto: dict, importacion_id, code: str = "PRODUCTO_NO_EXISTE",
                            mensaje: str = "El producto no existe"):
    """Confirma que el recurso pertenece al embarque que viene en la URL.

    Un recurso de otro embarque se reporta igual que uno inexistente (404), para no
    filtrar a qué embarque pertenece realmente.
    """
    if importacion_id is None:
        return
    if not producto or producto.get("importacion_id") != importacion_id:
        raise AsignacionesError(code, mensaje, 404)


def _verificar_pertenencia_venta(cursor, venta: dict, importacion_id):
    """Igual que _verificar_pertenencia, pero para recursos identificados por venta_id:
    resuelve el embarque a través del producto al que pertenece la venta."""
    if importacion_id is None:
        return
    cursor.execute(
        "SELECT importacion_id FROM importacion_productos WHERE id = %s",
        (venta["importacion_producto_id"],),
    )
    _verificar_pertenencia(cursor.fetchone(), importacion_id, "VENTA_NO_EXISTE", "La venta no existe")


def _verificar_pertenencia_asignacion(cursor, asignacion: dict, importacion_id):
    """Igual que _verificar_pertenencia_venta, pero para recursos identificados por
    asignacion_id: resuelve el embarque a través del producto al que pertenece la asignación."""
    if importacion_id is None:
        return
    cursor.execute(
        "SELECT importacion_id FROM importacion_productos WHERE id = %s",
        (asignacion["importacion_producto_id"],),
    )
    _verificar_pertenencia(cursor.fetchone(), importacion_id, "ASIGNACION_NO_EXISTE", "La asignación no existe")


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

        try:
            cursor.execute(
                "INSERT INTO importacion_productos "
                "(importacion_id, periodo, sku, sku_norm, descripcion, cantidad_embarcada) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (importacion_id, periodo, sku, sku_norm, descripcion, cantidad_embarcada),
            )
        except mysql.connector.errors.IntegrityError:
            # Carrera contra otro alta simultánea del mismo SKU: uq_producto_embarque la
            # rechaza en BD; la devolvemos como el mismo 409 del chequeo previo.
            conn.rollback()
            raise AsignacionesError("SKU_DUPLICADO", "Este SKU ya está registrado en el embarque", 409)
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
                f"WHERE importacion_producto_id = %s AND estado IN {_SQL_ESTADOS_VIGENTES}",
                (p["id"],),
            )
            asignado = int(cursor.fetchone()["total"])
            # PENDIENTE_VALIDACION cuenta igual que VALIDADO: la venta ya descontó del
            # disponible (movimiento VENTA_SOBRANTE) en el momento de crearse.
            cursor.execute(
                "SELECT COALESCE(SUM(cantidad), 0) AS total FROM importacion_sobrantes_ventas "
                "WHERE importacion_producto_id = %s AND estado IN ('VALIDADO', 'PENDIENTE_VALIDACION')",
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
                         usuario_id: int = None, importacion_id: int = None) -> dict:
    conn = obtener_conexion()
    if not conn:
        raise AsignacionesError("DB_NO_DISPONIBLE", "Sin conexión a BD", 500)
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM importacion_productos WHERE id = %s FOR UPDATE", (producto_id,))
        producto = cursor.fetchone()
        if not producto:
            raise AsignacionesError("PRODUCTO_NO_EXISTE", "El producto no existe", 404)
        _verificar_pertenencia(producto, importacion_id)

        if cantidad_embarcada is not None:
            if not isinstance(cantidad_embarcada, int) or cantidad_embarcada < 0:
                raise AsignacionesError("AJUSTE_INVALIDO", "La cantidad embarcada debe ser un entero >= 0")
            cursor.execute(
                "SELECT COALESCE(SUM(cantidad_asignada), 0) AS total FROM importacion_asignaciones "
                f"WHERE importacion_producto_id = %s AND estado IN {_SQL_ESTADOS_VIGENTES}",
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


_COLS_SKU = ("SKU",)
_COLS_CANT = ("CANTIDAD", "CANT", "QTY", "UNIDADES", "PIEZAS", "CANTIDAD EMBARCADA",
              "CANTIDAD ENTRANTE", "ENTRANTE")
_COLS_DESC = ("DESCRIPCION", "DESCRIPCIÓN", "NOMBRE", "PRODUCTO")


def parsear_excel_productos(file_bytes: bytes) -> dict:
    """Parsea un .xlsx/.xls de productos de embarque. Mismo enfoque que
    `subir_inventario_megamo`: busca la fila de encabezados en las primeras
    filas y mapea columnas SKU / CANTIDAD / DESCRIPCION.

    Devuelve {"filas": [{"fila", "sku", "cantidad", "descripcion"}], "errores": [str]}.
    Lanza AsignacionesError(400) si el archivo no se puede leer o no tiene columna SKU.
    """
    import io
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as e:
        raise AsignacionesError("EXCEL_INVALIDO", f"No se pudo leer el archivo: {e}", 400)

    ws = wb.active
    if ws is None or ws.max_row < 1:
        raise AsignacionesError("EXCEL_VACIO", "El archivo no tiene datos", 400)

    header_row = None
    col_sku = col_cant = None
    col_desc = None
    for ri in range(1, min(ws.max_row, 6) + 1):
        row_upper = [str(ws.cell(ri, ci).value).strip().upper() if ws.cell(ri, ci).value is not None else ""
                     for ci in range(1, ws.max_column + 1)]
        if any(k in row_upper for k in _COLS_SKU):
            header_row = ri
            col_sku = next(row_upper.index(k) + 1 for k in _COLS_SKU if k in row_upper)
            for k in _COLS_CANT:
                if k in row_upper:
                    col_cant = row_upper.index(k) + 1
                    break
            for k in _COLS_DESC:
                if k in row_upper:
                    col_desc = row_upper.index(k) + 1
                    break
            break

    if header_row is None or col_cant is None:
        raise AsignacionesError(
            "EXCEL_SIN_COLUMNAS",
            "El archivo debe tener una fila de encabezados con al menos las columnas 'SKU' y 'CANTIDAD'",
            400,
        )

    filas = []
    errores = []
    for ri in range(header_row + 1, ws.max_row + 1):
        sku_val = ws.cell(ri, col_sku).value
        if sku_val is None or not str(sku_val).strip():
            continue
        sku = str(sku_val).strip()
        cant_val = ws.cell(ri, col_cant).value
        try:
            cantidad = int(float(str(cant_val).replace(",", "").strip()))
        except (TypeError, ValueError):
            errores.append({"fila": ri, "sku": sku, "motivo": f"Cantidad inválida ({cant_val!r})"})
            continue
        if cantidad <= 0:
            errores.append({"fila": ri, "sku": sku, "motivo": f"La cantidad debe ser mayor a 0 ({cantidad})"})
            continue
        desc_val = ws.cell(ri, col_desc).value if col_desc else None
        descripcion = str(desc_val).strip()[:255] if desc_val is not None and str(desc_val).strip() else None
        filas.append({"fila": ri, "sku": sku, "cantidad": cantidad, "descripcion": descripcion})

    return {"filas": filas, "errores": errores}


def importar_productos(importacion_id: int, periodo: str, filas: list, usuario_id: int = None) -> dict:
    """Alta/actualización masiva de productos del embarque desde filas ya parseadas.

    Reutiliza `crear_producto` (INSERT + movimiento ENTRADA) para SKUs nuevos y
    `actualizar_producto` (UPDATE + movimiento AJUSTE) para los que ya existen en el
    embarque. Cada fila que falle (p. ej. bajar la cantidad por debajo de lo ya
    asignado) se salta y se reporta; el resto se procesa.
    """
    periodo = (periodo or "").strip()
    if not periodo:
        raise AsignacionesError("PERIODO_REQUERIDO", "El periodo es obligatorio", 400)

    conn = obtener_conexion()
    if not conn:
        raise AsignacionesError("DB_NO_DISPONIBLE", "Sin conexión a BD", 500)
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id FROM importaciones WHERE id = %s", (importacion_id,))
        if not cur.fetchone():
            raise AsignacionesError("IMPORTACION_NO_EXISTE", "El embarque no existe", 404)
        cur.execute(
            "SELECT id, sku_norm FROM importacion_productos WHERE importacion_id = %s",
            (importacion_id,),
        )
        existentes = {r["sku_norm"]: r["id"] for r in cur.fetchall()}
    finally:
        conn.close()

    insertados = 0
    actualizados = 0
    errores = []
    for f in filas:
        sku = (f.get("sku") or "").strip()
        if not sku:
            continue
        sku_norm = _norm_sku(sku)
        cantidad = f.get("cantidad")
        descripcion = f.get("descripcion") or None
        num_fila = f.get("fila")
        try:
            if sku_norm in existentes:
                actualizar_producto(
                    existentes[sku_norm],
                    cantidad_embarcada=cantidad,
                    descripcion=descripcion,
                    usuario_id=usuario_id,
                    importacion_id=importacion_id,
                )
                actualizados += 1
            else:
                nuevo = crear_producto(
                    importacion_id, sku, cantidad, periodo,
                    descripcion=descripcion, usuario_id=usuario_id,
                )
                existentes[sku_norm] = nuevo["id"]
                insertados += 1
        except AsignacionesError as e:
            errores.append({"fila": num_fila, "sku": sku, "motivo": e.message})

    return {
        "insertados": insertados,
        "actualizados": actualizados,
        "total_filas": len(filas),
        "errores": errores,
    }


def asignar(producto_id: int, reservas: list, usuario_id: int = None,
            importacion_id: int = None) -> dict:
    """Crea/incrementa reservas INICIAL para la ventana objetivo.

    Cada item: `clave_cliente`, `cantidad` (entero > 0), `mes_objetivo` opcional
    ('YYYY-MM' o nombre de mes; NULL si se omite) y `proyectado`/`cantidad_proyectada`
    opcional (snapshot de la demanda neta del cliente para ese mes: al insertar se
    guarda tal cual, al actualizar se sobrescribe si se envía, nunca se suma).
    La clave de upsert es (producto, cliente, mes_objetivo, origen='INICIAL')."""
    if not reservas:
        raise AsignacionesError("ASIGNACION_INVALIDA", "Debes enviar al menos una reserva")
    for item in reservas:
        cantidad = item.get("cantidad")
        if not isinstance(cantidad, int) or cantidad <= 0:
            raise AsignacionesError("ASIGNACION_INVALIDA", "La cantidad de cada reserva debe ser un entero > 0")
        if not item.get("clave_cliente"):
            raise AsignacionesError("CLIENTE_NO_EXISTE", "Falta clave_cliente en una de las reservas")
        proyectado = item.get("proyectado", item.get("cantidad_proyectada"))
        if proyectado is not None and (not isinstance(proyectado, int) or proyectado < 0):
            raise AsignacionesError("ASIGNACION_INVALIDA", "proyectado debe ser un entero >= 0")

    conn = obtener_conexion()
    if not conn:
        raise AsignacionesError("DB_NO_DISPONIBLE", "Sin conexión a BD", 500)
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, importacion_id, periodo, sku_norm FROM importacion_productos "
            "WHERE id = %s FOR UPDATE",
            (producto_id,),
        )
        producto = cursor.fetchone()
        if not producto:
            raise AsignacionesError("PRODUCTO_NO_EXISTE", "El producto no existe", 404)
        _verificar_pertenencia(producto, importacion_id)

        # Normaliza mes_objetivo (dentro del periodo del producto) por item.
        for item in reservas:
            mv = item.get("mes_objetivo")
            item["_mes_fecha"] = _parse_mes_arg(producto["periodo"], mv) if mv else None
            clave = item["clave_cliente"].strip().upper()
            cursor.execute("SELECT clave FROM clientes WHERE clave = %s", (clave,))
            if not cursor.fetchone():
                raise AsignacionesError("CLIENTE_NO_EXISTE", f"El cliente {clave} no existe", 404)

        disponible = _disponible_producto(cursor, producto_id)
        total_solicitado = sum(item["cantidad"] for item in reservas)
        if total_solicitado > disponible:
            raise AsignacionesError(
                "STOCK_INSUFICIENTE",
                f"Disponible: {disponible}, solicitado: {total_solicitado}",
                409,
            )

        for item in reservas:
            clave = item["clave_cliente"].strip().upper()
            cantidad = item["cantidad"]
            mes_fecha = item["_mes_fecha"]
            proyectado = item.get("proyectado", item.get("cantidad_proyectada"))
            prio_info = _PRIORIDAD_MAP.get(clave, (999, clave))

            cursor.execute(
                "SELECT id FROM importacion_asignaciones "
                "WHERE importacion_producto_id = %s AND clave_cliente = %s "
                "AND (mes_objetivo <=> %s) AND origen = 'INICIAL' FOR UPDATE",
                (producto_id, clave, mes_fecha),
            )
            existente = cursor.fetchone()
            if existente:
                if proyectado is not None:
                    cursor.execute(
                        "UPDATE importacion_asignaciones SET cantidad_asignada = cantidad_asignada + %s, "
                        "cantidad_proyectada = %s, estado = 'RESERVADA', usuario_id = %s, prioridad = %s "
                        "WHERE id = %s",
                        (cantidad, proyectado, usuario_id, prio_info[0], existente["id"]),
                    )
                else:
                    cursor.execute(
                        "UPDATE importacion_asignaciones SET cantidad_asignada = cantidad_asignada + %s, "
                        "estado = 'RESERVADA', usuario_id = %s, prioridad = %s WHERE id = %s",
                        (cantidad, usuario_id, prio_info[0], existente["id"]),
                    )
            else:
                cursor.execute(
                    "INSERT INTO importacion_asignaciones "
                    "(importacion_producto_id, clave_cliente, mes_objetivo, origen, "
                    "cantidad_proyectada, cantidad_asignada, prioridad, estado, usuario_id) "
                    "VALUES (%s, %s, %s, 'INICIAL', %s, %s, %s, 'RESERVADA', %s)",
                    (producto_id, clave, mes_fecha, proyectado if proyectado is not None else 0,
                     cantidad, prio_info[0], usuario_id),
                )
            _registrar_movimiento(
                cursor, producto_id, "RESERVA", -cantidad, clave_cliente=clave,
                usuario_id=usuario_id,
                metadata={"mes_objetivo": _fecha_a_ym(mes_fecha)} if mes_fecha else None,
            )

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
                          usuario_id: int = None, importacion_id: int = None) -> dict:
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

        cursor.execute(
            "SELECT id, importacion_id FROM importacion_productos WHERE id = %s FOR UPDATE",
            (producto_id,),
        )
        producto = cursor.fetchone()
        if not producto:
            raise AsignacionesError("PRODUCTO_NO_EXISTE", "El producto no existe", 404)
        _verificar_pertenencia(producto, importacion_id)

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


def _reservas_vigentes_por_mes(cursor, sku_norm: str, periodo: str) -> dict:
    """{(clave_cliente_MAYUS, mes_objetivo:date): unidades} de TODOS los embarques
    del mismo SKU+periodo cuyas reservas siguen vigentes. Fuente para netear la
    necesidad real de un cliente (spec §5.5)."""
    cursor.execute(
        "SELECT a.clave_cliente, a.mes_objetivo, COALESCE(SUM(a.cantidad_asignada), 0) AS total "
        "FROM importacion_asignaciones a "
        "JOIN importacion_productos p ON p.id = a.importacion_producto_id "
        "WHERE p.sku_norm = %s AND p.periodo = %s AND a.mes_objetivo IS NOT NULL "
        f"AND a.estado IN {_SQL_ESTADOS_VIGENTES} "
        "GROUP BY a.clave_cliente, a.mes_objetivo",
        (sku_norm, periodo),
    )
    return {
        (r["clave_cliente"].strip().upper(), r["mes_objetivo"]): int(r["total"])
        for r in cursor.fetchall()
    }


def _ordenar_clientes_por_prioridad(claves):
    """Orden de prioridad absoluta: (prioridad asc, clave asc). Clientes fuera de
    la lista centralizada caen al lugar 999."""
    return sorted(claves, key=lambda c: (_PRIORIDAD_MAP.get(c.strip().upper(), (999, ""))[0], c))


def recalcular_propuesta(importacion_id: int, mes_desde, mes_hasta, periodo_filtro: str = None) -> list:
    """Propuesta de reserva inicial para la ventana [mes_desde .. mes_hasta].

    Reparto CLIENTE-MAYOR (prioridad absoluta): se atiende por completo al cliente
    de prioridad 1 —recorriendo sus meses en orden cronológico— antes de pasar al
    siguiente. La necesidad de cada cliente/mes se netea contra las reservas
    vigentes de todos los embarques del mismo SKU+periodo (spec §5.3).
    """
    if not mes_desde or not mes_hasta:
        raise AsignacionesError("VENTANA_REQUERIDA", "Indica mes_desde y mes_hasta", 400)

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
        vigentes_por_sku = {
            (p["sku_norm"], p["periodo"]): _reservas_vigentes_por_mes(cursor, p["sku_norm"], p["periodo"])
            for p in productos
        }
    finally:
        conn.close()

    if not productos:
        return []

    por_periodo: dict = {}
    for p in productos:
        por_periodo.setdefault(p["periodo"], []).append(p)

    propuestas = []
    for periodo, prods in por_periodo.items():
        ventana = _meses_en_ventana(periodo, mes_desde, mes_hasta)
        ventana_ym = {m: _fecha_a_ym(_columna_a_fecha(periodo, m)) for m in ventana}
        ventana_fecha = {m: _columna_a_fecha(periodo, m) for m in ventana}

        skus_norm = [p["sku_norm"] for p in prods]
        try:
            demanda = demanda_neta_por_cliente_mensual(periodo, skus_norm)
            proyecciones_disponibles = True
        except Exception:
            logging.exception("Proyecciones no disponibles al recalcular embarque %s", importacion_id)
            demanda = {}
            proyecciones_disponibles = False

        for p in prods:
            neta = demanda.get(p["sku_norm"], {})           # {clave: {mes_col: neta}}
            vigentes = vigentes_por_sku.get((p["sku_norm"], p["periodo"]), {})
            restante = disponibles[p["id"]]

            clientes = _ordenar_clientes_por_prioridad(
                [c for c in neta if sum(neta[c].get(m, 0) for m in ventana) > 0]
            )

            filas = []
            for clave in clientes:
                cu = clave.strip().upper()
                prio, nombre = _PRIORIDAD_MAP.get(cu, (999, clave))
                meses_fila = []
                proyectado_total = vigente_total = sugerido_total = 0
                for m in ventana:
                    proy = neta[clave].get(m, 0)
                    if proy <= 0:
                        continue
                    ya = vigentes.get((cu, ventana_fecha[m]), 0)
                    faltante_real = max(0, proy - ya)
                    alloc = min(faltante_real, restante) if restante > 0 else 0
                    restante -= alloc
                    proyectado_total += proy
                    vigente_total += ya
                    sugerido_total += alloc
                    meses_fila.append({
                        "mes": ventana_ym[m],
                        "proyectado": proy,
                        "vigente": ya,
                        "sugerido": alloc,
                    })
                if not meses_fila:
                    continue
                filas.append({
                    "clave_cliente": cu,
                    "nombre_cliente": nombre,
                    "prioridad": prio,
                    "meses": meses_fila,
                    "proyectado_total": proyectado_total,
                    "sugerido_total": sugerido_total,
                    "faltante_total": max(0, proyectado_total - vigente_total - sugerido_total),
                })

            propuestas.append({
                "producto_id": p["id"],
                "sku": p["sku"],
                "descripcion": p.get("descripcion"),
                "periodo": periodo,
                "cantidad_embarcada": p["cantidad_embarcada"],
                "disponible": disponibles[p["id"]],
                "proyecciones_disponibles": proyecciones_disponibles,
                "ventana": {"desde": ventana_ym[ventana[0]], "hasta": ventana_ym[ventana[-1]]},
                "propuesta": filas,
                "sobrante_estimado": restante,
            })
    return propuestas


def cancelar_venta(venta_id: int, usuario_id: int = None, importacion_id: int = None) -> dict:
    conn = obtener_conexion()
    if not conn:
        raise AsignacionesError("DB_NO_DISPONIBLE", "Sin conexión a BD", 500)
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM importacion_sobrantes_ventas WHERE id = %s FOR UPDATE", (venta_id,))
        venta = cursor.fetchone()
        if not venta:
            raise AsignacionesError("VENTA_NO_EXISTE", "La venta no existe", 404)
        _verificar_pertenencia_venta(cursor, venta, importacion_id)
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


def cancelar_asignacion(asignacion_id: int, usuario_id: int = None, importacion_id: int = None) -> dict:
    conn = obtener_conexion()
    if not conn:
        raise AsignacionesError("DB_NO_DISPONIBLE", "Sin conexión a BD", 500)
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM importacion_asignaciones WHERE id = %s FOR UPDATE", (asignacion_id,))
        asignacion = cursor.fetchone()
        if not asignacion:
            raise AsignacionesError("ASIGNACION_NO_EXISTE", "La asignación no existe", 404)
        _verificar_pertenencia_asignacion(cursor, asignacion, importacion_id)
        if asignacion["estado"] == "CANCELADA":
            raise AsignacionesError("ASIGNACION_YA_CANCELADA", "La asignación ya estaba cancelada", 409)

        cursor.execute(
            "UPDATE importacion_asignaciones SET estado = 'CANCELADA' WHERE id = %s", (asignacion_id,)
        )
        _registrar_movimiento(
            cursor, asignacion["importacion_producto_id"], "LIBERACION", asignacion["cantidad_asignada"],
            clave_cliente=asignacion["clave_cliente"], usuario_id=usuario_id,
        )
        conn.commit()
        cursor.execute("SELECT * FROM importacion_asignaciones WHERE id = %s", (asignacion_id,))
        return cursor.fetchone()
    except AsignacionesError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def validar_venta_odoo(venta_id: int, numero_pedido_odoo: str = None,
                        importacion_id: int = None) -> dict:
    # Fase 1 — solo lectura. El folio NO se persiste hasta que Odoo confirme el pedido:
    # si la validación falla en cualquier punto, la BD queda intacta.
    conn = obtener_conexion()
    if not conn:
        raise AsignacionesError("DB_NO_DISPONIBLE", "Sin conexión a BD", 500)
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM importacion_sobrantes_ventas WHERE id = %s", (venta_id,))
        venta = cursor.fetchone()
        if not venta:
            raise AsignacionesError("VENTA_NO_EXISTE", "La venta no existe", 404)
        _verificar_pertenencia_venta(cursor, venta, importacion_id)
        if venta["estado"] == "CANCELADO":
            raise AsignacionesError("VENTA_YA_CANCELADA", "No se puede validar una venta cancelada", 409)

        folio = (numero_pedido_odoo or venta["numero_pedido_odoo"] or "").strip()
        if not folio:
            raise AsignacionesError("PEDIDO_ODOO_INVALIDO", "Falta el número de pedido de Odoo")

        cursor.execute(
            "SELECT sku_norm FROM importacion_productos WHERE id = %s",
            (venta["importacion_producto_id"],),
        )
        producto = cursor.fetchone()
        sku_norm_esperado = producto["sku_norm"] if producto else None
    finally:
        conn.close()

    # Fase 2 — validación contra Odoo, sin tocar la BD.
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

    # Fase 3 — única escritura, ya con el pedido confirmado en Odoo: fila bloqueada,
    # estado re-verificado (pudieron cancelarla durante el viaje a Odoo) y folio+estado
    # en un solo UPDATE dentro de la misma transacción.
    conn2 = obtener_conexion()
    if not conn2:
        raise AsignacionesError("DB_NO_DISPONIBLE", "Sin conexión a BD", 500)
    try:
        cursor2 = conn2.cursor(dictionary=True)
        cursor2.execute(
            "SELECT id, estado, numero_pedido_odoo FROM importacion_sobrantes_ventas "
            "WHERE id = %s FOR UPDATE",
            (venta_id,),
        )
        actual = cursor2.fetchone()
        if not actual:
            raise AsignacionesError("VENTA_NO_EXISTE", "La venta no existe", 404)
        if actual["estado"] == "CANCELADO":
            raise AsignacionesError("VENTA_YA_CANCELADA", "No se puede validar una venta cancelada", 409)

        try:
            if folio != actual["numero_pedido_odoo"]:
                cursor2.execute(
                    "UPDATE importacion_sobrantes_ventas "
                    "SET numero_pedido_odoo = %s, estado = 'VALIDADO' WHERE id = %s",
                    (folio, venta_id),
                )
            else:
                cursor2.execute(
                    "UPDATE importacion_sobrantes_ventas SET estado = 'VALIDADO' WHERE id = %s",
                    (venta_id,),
                )
        except mysql.connector.errors.IntegrityError:
            conn2.rollback()
            raise AsignacionesError(
                "PEDIDO_ODOO_YA_ASOCIADO", f"El pedido {folio} ya está asociado a otra venta", 409
            )

        conn2.commit()
        cursor2.execute("SELECT * FROM importacion_sobrantes_ventas WHERE id = %s", (venta_id,))
        return cursor2.fetchone()
    except AsignacionesError:
        conn2.rollback()
        raise
    except Exception:
        conn2.rollback()
        raise
    finally:
        conn2.close()


def obtener_detalle_producto(producto_id: int, importacion_id: int = None) -> dict:
    conn = obtener_conexion()
    if not conn:
        raise AsignacionesError("DB_NO_DISPONIBLE", "Sin conexión a BD", 500)
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM importacion_productos WHERE id = %s", (producto_id,))
        producto = cursor.fetchone()
        if not producto:
            raise AsignacionesError("PRODUCTO_NO_EXISTE", "El producto no existe", 404)
        _verificar_pertenencia(producto, importacion_id)

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

    # Reusa el cálculo de listar_productos para no duplicar la lógica de
    # cantidad_asignada/vendida/sobrante/disponible.
    productos = listar_productos(producto["importacion_id"])
    producto_calculado = next((p for p in productos if p["id"] == producto_id), producto)

    propuesta = recalcular_propuesta(producto["importacion_id"], producto["periodo"])
    fila = next((p for p in propuesta if p["producto_id"] == producto_id), None)

    return {
        "producto": producto_calculado,
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


# ── Vistas consolidadas (todos los embarques) ──────────────────────────────────

_TRUES = ("1", "true", "si", "sí", "on")


def _es_true(v) -> bool:
    return str(v).strip().lower() in _TRUES


def _filtros_embarque(f: dict):
    """WHERE + params comunes sobre la tabla `importaciones` (alias i)."""
    where = ["i.estado <> 'eliminado'"]
    params: list = []
    f = f or {}
    if f.get("estado"):
        where.append("i.estado = %s")
        params.append(f["estado"])
    if f.get("origen"):
        where.append("i.log_origen LIKE %s")
        params.append(f"%{f['origen']}%")
    if f.get("anio"):
        try:
            params.append(int(f["anio"]))
            where.append("YEAR(COALESCE(i.log_fecha_booking, i.created_at)) = %s")
        except (TypeError, ValueError):
            pass
    if f.get("q"):
        where.append("(i.referencia LIKE %s OR i.nombre LIKE %s)")
        like = f"%{f['q']}%"
        params += [like, like]
    return " AND ".join(where), params


# Expresiones que replican el cálculo por producto de listar_productos():
#  - asignado : Σ cantidad_asignada de asignaciones ACTIVA
#  - vendido  : Σ cantidad de ventas VALIDADO/PENDIENTE_VALIDACION
#  - disponible: Σ cantidad del ledger de movimientos (es un saldo con signo)
_SUB_ASIGNADO = (
    "(SELECT COALESCE(SUM(a.cantidad_asignada), 0) FROM importacion_asignaciones a "
    f"WHERE a.importacion_producto_id = p.id AND a.estado IN {_SQL_ESTADOS_VIGENTES})"
)
_SUB_VENDIDO = (
    "(SELECT COALESCE(SUM(v.cantidad), 0) FROM importacion_sobrantes_ventas v "
    "WHERE v.importacion_producto_id = p.id AND v.estado IN ('VALIDADO', 'PENDIENTE_VALIDACION'))"
)
_SUB_DISPONIBLE = (
    "(SELECT COALESCE(SUM(m.cantidad), 0) FROM importacion_movimientos m "
    "WHERE m.importacion_producto_id = p.id)"
)


def resumen_global(filtros: dict = None) -> dict:
    """Un renglón por embarque con al menos un producto registrado, con sus KPIs
    de asignación agregados. Alimenta el panel consolidado del dashboard."""
    conn = obtener_conexion()
    if not conn:
        raise AsignacionesError("DB_NO_DISPONIBLE", "Sin conexión a BD", 500)
    filtros = filtros or {}
    where_sql, params = _filtros_embarque(filtros)
    having = "HAVING disponibles > 0" if _es_true(filtros.get("solo_con_disponible")) else ""
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            f"""
            SELECT
              i.id, i.referencia, i.nombre, i.estado,
              COUNT(pa.id)                              AS n_productos,
              COUNT(DISTINCT pa.periodo)                AS n_periodos,
              COALESCE(SUM(pa.cantidad_embarcada), 0)   AS embarcadas,
              COALESCE(SUM(pa.asignado), 0)             AS asignadas,
              COALESCE(SUM(pa.vendido), 0)              AS vendidas,
              COALESCE(SUM(GREATEST(pa.cantidad_embarcada - pa.asignado, 0)), 0) AS sobrantes,
              COALESCE(SUM(pa.disponible), 0)           AS disponibles,
              MAX(pa.ultima_actividad)                  AS ultima_actividad
            FROM importaciones i
            JOIN (
              SELECT
                p.id, p.importacion_id, p.periodo, p.cantidad_embarcada,
                {_SUB_ASIGNADO}  AS asignado,
                {_SUB_VENDIDO}   AS vendido,
                {_SUB_DISPONIBLE} AS disponible,
                (SELECT MAX(m.created_at) FROM importacion_movimientos m
                   WHERE m.importacion_producto_id = p.id) AS ultima_actividad
              FROM importacion_productos p
            ) pa ON pa.importacion_id = i.id
            WHERE {where_sql}
            GROUP BY i.id, i.referencia, i.nombre, i.estado
            {having}
            ORDER BY i.id DESC
            """,
            params,
        )
        filas = cursor.fetchall()
    finally:
        conn.close()

    embarques = []
    tot = {"embarcadas": 0, "asignadas": 0, "sobrantes": 0, "vendidas": 0, "disponibles": 0}
    for r in filas:
        kpis = {k: int(r[k]) for k in ("embarcadas", "asignadas", "sobrantes", "vendidas", "disponibles")}
        ua = r["ultima_actividad"]
        embarques.append({
            "id": r["id"],
            "referencia": r["referencia"],
            "nombre": r["nombre"],
            "estado": r["estado"],
            "n_productos": int(r["n_productos"]),
            "n_periodos": int(r["n_periodos"]),
            "kpis": kpis,
            "ultima_actividad": ua.isoformat(sep=" ") if hasattr(ua, "isoformat") else ua,
        })
        for k in tot:
            tot[k] += kpis[k]
    tot["n_embarques"] = len(embarques)
    return {"embarques": embarques, "totales": tot}


def listar_productos_global(filtros: dict = None, limite: int = 200, offset: int = 0) -> dict:
    """Lista plana de productos cruzando todos los embarques (vista por SKU)."""
    conn = obtener_conexion()
    if not conn:
        raise AsignacionesError("DB_NO_DISPONIBLE", "Sin conexión a BD", 500)
    f = filtros or {}
    where = ["i.estado <> 'eliminado'"]
    params: list = []
    if f.get("estado"):
        where.append("i.estado = %s")
        params.append(f["estado"])
    if f.get("origen"):
        where.append("i.log_origen LIKE %s")
        params.append(f"%{f['origen']}%")
    if f.get("anio"):
        try:
            params.append(int(f["anio"]))
            where.append("YEAR(COALESCE(i.log_fecha_booking, i.created_at)) = %s")
        except (TypeError, ValueError):
            pass
    if f.get("importacion_id"):
        try:
            params.append(int(f["importacion_id"]))
            where.append("p.importacion_id = %s")
        except (TypeError, ValueError):
            pass
    if f.get("periodo"):
        where.append("p.periodo = %s")
        params.append(f["periodo"])
    if f.get("sku"):
        where.append("p.sku_norm LIKE %s")
        params.append(f"%{_norm_sku(f['sku'])}%")
    if f.get("q"):
        where.append("(p.sku LIKE %s OR p.descripcion LIKE %s OR i.referencia LIKE %s OR i.nombre LIKE %s)")
        like = f"%{f['q']}%"
        params += [like, like, like, like]
    if _es_true(f.get("solo_disponible")):
        where.append(f"{_SUB_DISPONIBLE} > 0")
    where_sql = " AND ".join(where)
    base_from = (
        "FROM importacion_productos p "
        "JOIN importaciones i ON i.id = p.importacion_id "
        f"WHERE {where_sql}"
    )
    try:
        limite = min(max(int(limite), 1), 1000)
    except (TypeError, ValueError):
        limite = 200
    try:
        offset = max(int(offset), 0)
    except (TypeError, ValueError):
        offset = 0
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"SELECT COUNT(*) AS n {base_from}", params)
        total = int(cursor.fetchone()["n"])
        cursor.execute(
            f"""
            SELECT
              p.importacion_id, i.referencia,
              i.nombre AS embarque_nombre, i.estado AS embarque_estado,
              p.id AS producto_id, p.sku, p.sku_norm, p.descripcion, p.periodo,
              p.cantidad_embarcada,
              {_SUB_ASIGNADO}   AS cantidad_asignada,
              {_SUB_VENDIDO}    AS cantidad_vendida,
              {_SUB_DISPONIBLE} AS cantidad_disponible
            {base_from}
            ORDER BY i.id DESC, p.sku
            LIMIT %s OFFSET %s
            """,
            params + [limite, offset],
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    productos = []
    tot = {"embarcadas": 0, "asignadas": 0, "sobrantes": 0, "vendidas": 0, "disponibles": 0}
    for r in rows:
        embarcada = int(r["cantidad_embarcada"])
        asignada = int(r["cantidad_asignada"])
        vendida = int(r["cantidad_vendida"])
        disponible = int(r["cantidad_disponible"])
        sobrante = max(embarcada - asignada, 0)
        productos.append({
            "importacion_id": r["importacion_id"],
            "referencia": r["referencia"],
            "embarque_nombre": r["embarque_nombre"],
            "embarque_estado": r["embarque_estado"],
            "producto_id": r["producto_id"],
            "sku": r["sku"],
            "descripcion": r["descripcion"],
            "periodo": r["periodo"],
            "cantidad_embarcada": embarcada,
            "cantidad_asignada": asignada,
            "cantidad_sobrante": sobrante,
            "cantidad_vendida": vendida,
            "cantidad_disponible": disponible,
        })
        tot["embarcadas"] += embarcada
        tot["asignadas"] += asignada
        tot["sobrantes"] += sobrante
        tot["vendidas"] += vendida
        tot["disponibles"] += disponible
    return {
        "productos": productos,
        "totales": tot,
        "total_filas": total,
        "limite": limite,
        "offset": offset,
    }


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
