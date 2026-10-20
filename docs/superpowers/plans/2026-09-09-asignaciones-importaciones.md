# Asignaciones de Importaciones Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir el submódulo "Asignaciones de Importaciones" (backend Flask + frontend Angular) que cruza mercancía física de un embarque, demanda de Proyecciones, prioridad de clientes y ventas anticipadas de sobrantes validadas contra Odoo, con historial auditable, sin modificar `importaciones` ni `forecast_proyecciones`.

**Architecture:** `Angular → routes/asignaciones_importaciones.py (blueprint, url_prefix='/importaciones') → services/asignaciones_service.py → MySQL`, con `services/proyecciones_service.py` nuevo (extraído de `routes/proyecciones_my27.py`) como único punto de acceso a la demanda proyectada, la prioridad de clientes y la deducción de órdenes Odoo confirmadas. 4 tablas nuevas (`importacion_productos`, `importacion_asignaciones`, `importacion_sobrantes_ventas`, `importacion_movimientos`).

**Tech Stack:** Flask 3, mysql-connector-python (MySQL 8 local / MariaDB 10.11 prod), pytest + pytest-mock, Angular 19 standalone components, Jasmine + HttpClientTestingModule.

**Spec:** `docs/superpowers/specs/2026-09-09-asignaciones-importaciones-design.md`

## Global Constraints

- Nunca modificar la tabla `importaciones` ni `forecast_proyecciones` (solo lectura).
- Nunca `asignado > embarcado`, nunca `vendido_sobrante > sobrante`, nunca cantidades negativas.
- Toda operación que cambie cantidades va dentro de una transacción con `SELECT ... FOR UPDATE` sobre la fila del producto (`importacion_productos`), y `conn.commit()`/`conn.rollback()` explícitos (autocommit está desactivado por defecto en mysql-connector-python en este proyecto).
- Nunca borrar una fila de `importacion_movimientos` — es un ledger solo-`INSERT`.
- Nunca descontar dos veces el mismo `numero_pedido_odoo` (`UNIQUE` + manejo de `IntegrityError`).
- Todas las rutas del blueprint nuevo (excepto `GET /clientes/prioridad`) van protegidas con `@token_required` (reutilizado de `routes/clientes.py`) + rol en `(1, 3)`.
- Formato de respuesta uniforme: éxito `{"ok": true, "data": ...}`, error `{"ok": false, "error": {"code": ..., "message": ...}}`.
- Si Proyecciones u Odoo no responden, Importaciones/Asignaciones deben seguir funcionando (se degrada solo `recalcular`/`validar-odoo`, nunca se rompe el registro de mercancía).
- No agregar columnas cacheadas de disponibilidad — todo disponible se calcula con `SUM(cantidad)` sobre `importacion_movimientos`.
- Sin capa `repositories/` — seguir el patrón real del repo: `routes/` (HTTP+auth) → `services/` (negocio + acceso a datos vía `obtener_conexion()`).

---

## Task 1: Extraer lógica compartida de Proyecciones a `services/proyecciones_service.py`

**Files:**
- Create: `services/proyecciones_service.py`
- Modify: `routes/proyecciones_my27.py:39-72` (bloque `PRIORIDAD_CLIENTES`/`_PRIORIDAD_MAP`), `routes/proyecciones_my27.py:102-103` (`_ORDENES_CACHE`/`_ORDENES_TTL`), `routes/proyecciones_my27.py:333-474` (`_norm_sku`/`_get_ordenes_my27`)
- Test: `tests/test_proyecciones_service.py`

**Interfaces:**
- Produces: `PRIORIDAD_CLIENTES: list[tuple[int,str,str]]`, `_PRIORIDAD_MAP: dict[str, tuple[int,str]]`, `_norm_sku(s: str) -> str`, `_get_ordenes_my27(periodo: str) -> dict`, `obtener_prioridad_clientes() -> list[dict]` — usados por Task 2 y por `services/asignaciones_service.py` en tareas posteriores.

**Nota de corrección de bug:** el código original de `_get_ordenes_my27` hace `_ORDENES_CACHE = {...}` (rebind) dentro de la función. Si `routes/proyecciones_my27.py` importa `_ORDENES_CACHE` por nombre y luego la función (ya movida) rebindea el nombre en su propio módulo, `routes/proyecciones_my27.py` se queda con una referencia obsoleta al diccionario viejo, y el "forzar refresco" de `listar()` (`_ORDENES_CACHE['ts'] = 0.0`) deja de tener efecto real. Por eso en la relocación se cambia `_ORDENES_CACHE = {...}` por `_ORDENES_CACHE.clear(); _ORDENES_CACHE.update({...})` (mutación in-place, no rebind) — comportamiento observable idéntico, pero la identidad del objeto se mantiene estable entre módulos.

- [ ] **Step 1: Crear `services/proyecciones_service.py` con el contenido relocado**

```python
"""
Servicio compartido de Proyecciones.
Centraliza la prioridad de clientes, la normalización de SKU y la deducción
de órdenes Odoo ya confirmadas, para que routes/proyecciones_my27.py y
routes/asignaciones_importaciones.py usen la misma fuente sin duplicar lógica.
"""
import logging
import re
import time

from utils.odoo_utils import get_odoo_models, ODOO_DB, ODOO_PASSWORD

# Lista de prioridad para distribución de inventario.
# Los clientes no en esta lista reciben stock después de la prioridad 27.
PRIORIDAD_CLIENTES = [
    (1,  'LC657', 'Víctor Hugo Villanueva Guzman'),
    (2,  'MC677', 'BICICLETAS SCJM'),
    (3,  'MC679', 'Adventure Bike Rider S. A. DE C. V. (GPE)'),
    (4,  'GC411', 'Adventure Bike Rider S. A. DE C. V.'),
    (5,  'HE420', 'Xavier James Lord Santos'),
    (6,  'EC216', 'Marco Tulio (Morelia)'),
    (7,  'JC539', 'Marco Tulio Andrade Navarro (León)'),
    (8,  'MD670', 'LIVING FOR BIKES'),
    (9,  'GD380', 'Cycling Riding de Mexico SA de CV (Metepec)'),
    (10, 'HA433', 'Lucia Salazar Lopez'),
    (11, 'ID506', 'Angelica Osorio Gasperin'),
    (12, '4E013', 'CHRISTIAN BOCCALETTI.'),
    (13, 'JE537', 'Christian Boccaletti.'),
    (14, 'LC625', 'Naruco S. A. de C. V. Arcos'),
    (15, 'LC626', 'Naruco S. A. de C. V. SJR'),
    (16, 'LC627', 'Naruco S. A. de C. V. (Jurica)'),
    (17, '84920', 'Naruco Corregidora'),
    (18, 'MD697', 'Fernando Pontón Rocha'),
    (19, 'EA219', 'Victor Alejandro Garnier Morga'),
    (20, 'HF427', 'Opciones Creativas SA de CV'),
    (21, 'FA271', 'Juan Manuel Ruacho Rangel'),
    (22, 'AG873', 'Alta Gama 87'),
    (23, 'LD664', 'Bikes 95 Cycling Club S. A. De C. V.'),
    (24, '5GEG6', 'FELIPE ENRIQUEZ ROJAS'),
    (25, 'IA500', 'Jesus Manuel Medrano Velarde'),
    (26, 'DC192', 'ANA CECILIA LOPEZ LOPEZ'),
    (27, 'JC554', 'ZIRANDA MADRIGAL EUGENA'),
]
# Lookup rápido: clave_cliente → (prioridad, nombre_canonical)
_PRIORIDAD_MAP: dict = {clave.strip().upper(): (prio, nombre)
                        for prio, clave, nombre in PRIORIDAD_CLIENTES}

_ORDENES_CACHE: dict = {'data': {}, 'periodo': '', 'ts': 0.0}
_ORDENES_TTL = 180  # 3 minutos


def _norm_sku(s: str) -> str:
    return re.sub(r'[\-\s]', '', str(s or '')).upper()


def obtener_prioridad_clientes() -> list:
    """[{clave, nombre, prioridad}] ordenado por prioridad — para GET /clientes/prioridad."""
    return [
        {"clave": clave, "nombre": nombre, "prioridad": prioridad}
        for prioridad, clave, nombre in PRIORIDAD_CLIENTES
    ]


def _get_ordenes_my27(periodo: str) -> dict:
    """
    Retorna {clave_cliente: {sku_norm: total_pedido}} con las unidades ya
    confirmadas en Odoo (sale.order state=sale/done) para el periodo MY27.
    Caché de 3 minutos para no saturar Odoo en cada recarga.
    """
    now = time.time()
    if (now - _ORDENES_CACHE['ts'] < _ORDENES_TTL and
            _ORDENES_CACHE['periodo'] == periodo):
        return _ORDENES_CACHE['data']

    try:
        m = re.match(r'^(\d{4})-(\d{4})$', periodo)
        if not m:
            return {}
        year1, year2 = int(m.group(1)), int(m.group(2))
        fecha_inicio = f'{year1 - 1}-07-01'
        fecha_fin    = f'{year2}-04-30'

        uid, models, err = get_odoo_models()
        if err or not uid:
            return _ORDENES_CACHE['data']

        orders = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'sale.order', 'search_read',
            [[['state', 'in', ['sale', 'done']],
              ['date_order', '>=', fecha_inicio],
              ['date_order', '<=', fecha_fin + ' 23:59:59']]],
            {'fields': ['id', 'partner_id'], 'limit': 0}
        )
        if not orders:
            _ORDENES_CACHE.update({'data': {}, 'periodo': periodo, 'ts': now})
            return {}

        partner_ids = list({o['partner_id'][0] for o in orders if o.get('partner_id')})
        partners = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'res.partner', 'search_read',
            [[['id', 'in', partner_ids]]],
            {'fields': ['id', 'ref', 'parent_id'], 'limit': 0}
        )
        ref_map: dict = {}
        sin_ref: list = []
        sin_ref_ni_padre: list = []
        for p in partners:
            ref = (p.get('ref') or '').strip()
            if ref:
                ref_map[p['id']] = ref
            elif p.get('parent_id'):
                sin_ref.append((p['id'], p['parent_id'][0]))
            else:
                sin_ref_ni_padre.append(p['id'])
        logging.info('[ordenes_my27] partners con ref: %d, hijos sin ref: %d, sin ref ni padre: %d',
                     len(ref_map), len(sin_ref), len(sin_ref_ni_padre))
        if sin_ref:
            parent_ids_lookup = list({pid for _, pid in sin_ref})
            parents = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD,
                'res.partner', 'search_read',
                [[['id', 'in', parent_ids_lookup]]],
                {'fields': ['id', 'ref', 'name'], 'limit': 0}
            )
            parent_ref_map = {p['id']: (p.get('ref') or '').strip() for p in parents}
            logging.info('[ordenes_my27] padres encontrados: %s',
                         {p['id']: (p.get('ref'), p.get('name')) for p in parents})
            for child_id, parent_id in sin_ref:
                pref = parent_ref_map.get(parent_id, '')
                logging.info('[ordenes_my27] hijo %s → padre %s ref=%r', child_id, parent_id, pref)
                if pref:
                    ref_map[child_id] = pref
        logging.info('[ordenes_my27] ref_map final: %d claves; EC216 presente: %s',
                     len(ref_map), 'EC216' in ref_map.values())

        order_clave = {
            o['id']: ref_map[o['partner_id'][0]]
            for o in orders
            if o.get('partner_id') and ref_map.get(o['partner_id'][0])
        }
        logging.info('[ordenes_my27] ordenes con clave: %d / %d totales', len(order_clave), len(orders))

        sol = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'sale.order.line', 'search_read',
            [[['order_id', 'in', list(order_clave.keys())],
              ['state', 'not in', ['cancel']]]],
            {'fields': ['order_id', 'product_id', 'product_uom_qty'], 'limit': 0}
        )

        prod_ids = list({l['product_id'][0] for l in sol if l.get('product_id')})
        prods = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'product.product', 'search_read',
            [[['id', 'in', prod_ids]]],
            {'fields': ['id', 'default_code'], 'limit': 0}
        )
        prod_code = {p['id']: (p.get('default_code') or '').strip() for p in prods}

        result: dict = {}
        for l in sol:
            oid = l['order_id'][0] if isinstance(l.get('order_id'), list) else l.get('order_id')
            clave = order_clave.get(oid, '')
            if not clave:
                continue
            pid = l['product_id'][0] if l.get('product_id') else None
            if not pid:
                continue
            sn = _norm_sku(prod_code.get(pid, ''))
            if not sn:
                continue
            qty = int(l.get('product_uom_qty') or 0)
            if clave not in result:
                result[clave] = {}
            result[clave][sn] = result[clave].get(sn, 0) + qty

        _ORDENES_CACHE.update({'data': result, 'periodo': periodo, 'ts': now})
        logging.info('[ordenes_my27] %d clientes con pedidos en %s', len(result), periodo)
        if 'EC216' in result:
            logging.info('[ordenes_my27] EC216 pedidos: %s', result['EC216'])
        else:
            logging.info('[ordenes_my27] EC216 NO está en result — no se descontará')
        return result

    except Exception as e:
        logging.exception('[ordenes_my27] error: %s', e)
        return _ORDENES_CACHE['data']
```

- [ ] **Step 2: Reemplazar el bloque de prioridad en `routes/proyecciones_my27.py`**

En `routes/proyecciones_my27.py`, reemplazar las líneas 39-72 (desde el comentario `# Lista de prioridad...` hasta el cierre de `_PRIORIDAD_MAP`) por:

```python
from services.proyecciones_service import (
    PRIORIDAD_CLIENTES, _PRIORIDAD_MAP, _norm_sku, _get_ordenes_my27,
    _ORDENES_CACHE, _ORDENES_TTL,
)
```

- [ ] **Step 3: Eliminar las líneas 102-103 y 333-474 ya relocadas**

En `routes/proyecciones_my27.py`, eliminar la declaración duplicada `_ORDENES_CACHE: dict = {...}` / `_ORDENES_TTL = 180` (líneas 102-103, ya cubierta por el import del Step 2) y eliminar por completo las definiciones de `_norm_sku` y `_get_ordenes_my27` (líneas 333-474, incluido el comentario de sección `# Helper: órdenes confirmadas...`), dejando solo el import agregado en el Step 2 como fuente de estos nombres.

- [ ] **Step 4: Test de regresión — el import no cambia el comportamiento**

```python
# tests/test_proyecciones_service.py
from services.proyecciones_service import (
    PRIORIDAD_CLIENTES, _PRIORIDAD_MAP, _norm_sku, obtener_prioridad_clientes,
)


def test_prioridad_clientes_tiene_27_entradas_unicas():
    claves = [clave for _, clave, _ in PRIORIDAD_CLIENTES]
    assert len(claves) == 27
    assert len(set(claves)) == 27


def test_prioridad_map_lc657_es_prioridad_1():
    assert _PRIORIDAD_MAP['LC657'] == (1, 'Víctor Hugo Villanueva Guzman')


def test_norm_sku_quita_guiones_espacios_y_normaliza_mayusculas():
    assert _norm_sku('427102-0001004') == '4271020001004'
    assert _norm_sku(' 286383 - 704 ') == '286383704'
    assert _norm_sku(None) == ''


def test_obtener_prioridad_clientes_devuelve_27_dicts_ordenados():
    resultado = obtener_prioridad_clientes()
    assert len(resultado) == 27
    assert resultado[0] == {"clave": "LC657", "nombre": "Víctor Hugo Villanueva Guzman", "prioridad": 1}
    assert resultado[-1]["clave"] == "JC554"


def test_proyecciones_my27_importa_sin_error():
    # Si el refactor rompió algo (nombre no exportado, import circular), esto falla al importar.
    import routes.proyecciones_my27 as mod
    assert mod.PRIORIDAD_CLIENTES is PRIORIDAD_CLIENTES
    assert mod._PRIORIDAD_MAP is _PRIORIDAD_MAP
```

- [ ] **Step 5: Ejecutar los tests**

Run: `pytest tests/test_proyecciones_service.py -v`
Expected: 5 tests PASS.

- [ ] **Step 6: Verificación manual de no-regresión (Flask local corriendo)**

Con el backend local corriendo (`python app.py` o el proceso ya activo), comparar la respuesta de:
```
GET http://127.0.0.1:5000/proyecciones-my27/distribucion-prioritaria?periodo=2026-2027&refresh=1
```
antes y después del refactor — deben ser idénticas (mismo JSON). Este es el endpoint que más profundamente depende de `_PRIORIDAD_MAP` y `_get_ordenes_my27`.

- [ ] **Step 7: Commit**

```bash
git add services/proyecciones_service.py routes/proyecciones_my27.py tests/test_proyecciones_service.py
git commit -m "refactor: extraer prioridad de clientes y deducción Odoo a services/proyecciones_service.py

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: Endpoint `GET /clientes/prioridad`

**Files:**
- Modify: `routes/clientes.py` (agregar import + 1 endpoint nuevo, no tocar nada existente)
- Test: `tests/test_clientes_prioridad.py`

**Interfaces:**
- Consumes: `services.proyecciones_service.obtener_prioridad_clientes() -> list[dict]` (Task 1)
- Produces: `GET /clientes/prioridad` → `200 [{clave, nombre, prioridad}, ...]` — consumido por el frontend en Task 15 (tanto proyecciones-my27 como el nuevo módulo de asignaciones).

- [ ] **Step 1: Agregar el import al inicio de `routes/clientes.py`**

Junto a los demás imports del archivo (línea 1-9), agregar:

```python
from services.proyecciones_service import obtener_prioridad_clientes
```

- [ ] **Step 2: Agregar el endpoint nuevo**

Al final de `routes/clientes.py` (después de la última ruta existente, `/facturas-grupo/<int:id_grupo>`):

```python
@clientes_bp.route('/clientes/prioridad', methods=['GET'])
def obtener_prioridad_clientes_endpoint():
    """Lista de prioridad de clientes centralizada — fuente única para
    Proyecciones y Asignaciones de Importaciones (evita copias hardcodeadas)."""
    return jsonify(obtener_prioridad_clientes()), 200
```

- [ ] **Step 3: Escribir el test**

```python
# tests/test_clientes_prioridad.py
from flask import Flask

from routes.clientes import clientes_bp


def _cliente_test():
    app = Flask(__name__)
    app.register_blueprint(clientes_bp)
    return app.test_client()


def test_get_clientes_prioridad_devuelve_27_entradas_ordenadas():
    client = _cliente_test()
    resp = client.get('/clientes/prioridad')
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 27
    assert data[0] == {"clave": "LC657", "nombre": "Víctor Hugo Villanueva Guzman", "prioridad": 1}
    assert all({"clave", "nombre", "prioridad"} == set(row.keys()) for row in data)


def test_get_clientes_prioridad_no_requiere_autenticacion():
    client = _cliente_test()
    resp = client.get('/clientes/prioridad')  # sin header Authorization
    assert resp.status_code == 200
```

- [ ] **Step 4: Ejecutar el test**

Run: `pytest tests/test_clientes_prioridad.py -v`
Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add routes/clientes.py tests/test_clientes_prioridad.py
git commit -m "feat: exponer GET /clientes/prioridad como fuente única de prioridad de clientes

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: Blueprint `asignaciones_importaciones` + tablas nuevas + registro en `app.py`

**Files:**
- Create: `services/asignaciones_service.py` (arranca con `AsignacionesError` + `TABLAS_SQL`)
- Create: `routes/asignaciones_importaciones.py`
- Modify: `app.py` (import + `register_blueprint`)
- Test: `tests/test_asignaciones_routes.py`

**Interfaces:**
- Produces: clase `AsignacionesError(code: str, message: str, status: int = 400)`, constante `TABLAS_SQL: list[str]` — usados por todas las tareas siguientes. Blueprint `asignaciones_bp` montado en `/importaciones`, ruta `POST /importaciones/asignaciones/inicializar-tablas`.

- [ ] **Step 1: Crear `services/asignaciones_service.py` con el error y el DDL**

```python
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
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
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
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
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
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
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
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]
```

- [ ] **Step 2: Crear `routes/asignaciones_importaciones.py` con el blueprint y el endpoint de inicialización**

```python
import logging
from functools import wraps

from flask import Blueprint, request, jsonify

from db_conexion import obtener_conexion
from routes.clientes import token_required
from services import asignaciones_service as svc

asignaciones_bp = Blueprint("asignaciones_importaciones", __name__, url_prefix="/importaciones")


def _requiere_rol_importaciones(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        payload = getattr(request, "cliente_data", {}) or {}
        if payload.get("rol") not in (1, 3):
            return jsonify({
                "ok": False,
                "error": {"code": "NO_AUTORIZADO", "message": "No tienes permiso para acceder a este módulo"},
            }), 403
        return f(*args, **kwargs)
    return decorated


@asignaciones_bp.errorhandler(svc.AsignacionesError)
def _manejar_error_asignaciones(err):
    return jsonify({"ok": False, "error": {"code": err.code, "message": err.message}}), err.status


@asignaciones_bp.route("/asignaciones/inicializar-tablas", methods=["POST"])
@token_required
@_requiere_rol_importaciones
def inicializar_tablas():
    conn = obtener_conexion()
    if not conn:
        return jsonify({"ok": False, "error": {"code": "DB_NO_DISPONIBLE", "message": "Sin conexión a BD"}}), 500
    try:
        cursor = conn.cursor()
        for ddl in svc.TABLAS_SQL:
            cursor.execute(ddl)
        conn.commit()
        return jsonify({"ok": True, "mensaje": "Tablas de asignaciones creadas/verificadas"}), 201
    except Exception as e:
        logging.exception("Error creando tablas de asignaciones")
        return jsonify({"ok": False, "error": {"code": "DB_ERROR", "message": str(e)}}), 500
    finally:
        conn.close()
```

- [ ] **Step 3: Registrar el blueprint en `app.py`**

Junto a los demás imports de blueprints (cerca de la línea 31-32):

```python
from routes.asignaciones_importaciones import asignaciones_bp
```

Junto a `app.register_blueprint(proyecciones_my27_bp)` (línea ~172):

```python
    app.register_blueprint(asignaciones_bp)
```

- [ ] **Step 4: Test — auth requerida y creación de tablas contra la BD local real**

Este test usa la conexión real a la BD de desarrollo local (mismo patrón que las verificaciones manuales ya hechas en este proyecto) porque el objetivo es validar que el DDL es válido, no solo que se llamó `cursor.execute`.

```python
# tests/test_asignaciones_routes.py
from flask import Flask

from db_conexion import obtener_conexion
from utils.jwt_utils import generar_token
from routes.asignaciones_importaciones import asignaciones_bp


def _cliente_test():
    app = Flask(__name__)
    app.register_blueprint(asignaciones_bp)
    return app.test_client()


def _token_valido(rol=1):
    return generar_token(
        id_usuario=1, rol=rol, usuario="test", nombre="Test",
        cliente_id=None, clave_cliente=None, nombre_cliente=None, id_grupo=None, flujo=None,
    )


def test_inicializar_tablas_sin_token_devuelve_401():
    client = _cliente_test()
    resp = client.post("/importaciones/asignaciones/inicializar-tablas")
    assert resp.status_code == 401


def test_inicializar_tablas_con_rol_no_permitido_devuelve_403():
    client = _cliente_test()
    token = _token_valido(rol=2)
    resp = client.post(
        "/importaciones/asignaciones/inicializar-tablas",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.get_json()["error"]["code"] == "NO_AUTORIZADO"


def test_inicializar_tablas_crea_las_4_tablas_en_bd_real():
    conn = obtener_conexion()
    if not conn:
        import pytest
        pytest.skip("Sin conexión a BD local para este test")
    conn.close()

    client = _cliente_test()
    token = _token_valido(rol=1)
    resp = client.post(
        "/importaciones/asignaciones/inicializar-tablas",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201

    conn = obtener_conexion()
    cursor = conn.cursor()
    cursor.execute("SHOW TABLES LIKE 'importacion_%'")
    tablas = {row[0] for row in cursor.fetchall()}
    conn.close()
    assert {
        "importacion_productos", "importacion_asignaciones",
        "importacion_sobrantes_ventas", "importacion_movimientos",
    }.issubset(tablas)
```

- [ ] **Step 5: Ejecutar los tests**

Run: `pytest tests/test_asignaciones_routes.py -v`
Expected: 3 tests PASS (el tercero crea las tablas reales en la BD local de desarrollo — quedan ahí para las siguientes tareas, es el comportamiento esperado).

- [ ] **Step 6: Commit**

```bash
git add services/asignaciones_service.py routes/asignaciones_importaciones.py app.py tests/test_asignaciones_routes.py
git commit -m "feat: crear blueprint asignaciones_importaciones y las 4 tablas del submódulo

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: Registrar y listar productos del embarque

**Files:**
- Modify: `services/asignaciones_service.py` (agregar helpers + `crear_producto`, `listar_productos`)
- Modify: `routes/asignaciones_importaciones.py` (agregar 2 rutas)
- Test: `tests/test_asignaciones_service.py` (nuevo), `tests/test_asignaciones_routes.py` (agregar)

**Interfaces:**
- Consumes: `services.proyecciones_service._norm_sku` (Task 1)
- Produces: `_disponible_producto(cursor, producto_id: int) -> int`, `_registrar_movimiento(cursor, producto_id, tipo_movimiento, cantidad, clave_cliente=None, referencia_externa=None, usuario_id=None, metadata=None) -> None`, `crear_producto(importacion_id, sku, cantidad_embarcada, periodo, descripcion=None, usuario_id=None) -> dict`, `listar_productos(importacion_id: int) -> list[dict]` — usados por todas las tareas siguientes (los dicts que devuelven traen las claves `id, importacion_id, periodo, sku, sku_norm, descripcion, cantidad_embarcada, cantidad_asignada, cantidad_vendida, cantidad_sobrante, cantidad_disponible, created_at, updated_at`).

- [ ] **Step 1: Agregar los helpers y las dos funciones a `services/asignaciones_service.py`**

Agregar al final del archivo (después de `TABLAS_SQL`):

```python
from services.proyecciones_service import _norm_sku


def _disponible_producto(cursor, producto_id: int) -> int:
    cursor.execute(
        "SELECT COALESCE(SUM(cantidad), 0) AS disponible FROM importacion_movimientos "
        "WHERE importacion_producto_id = %s",
        (producto_id,),
    )
    return cursor.fetchone()["disponible"]


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
            asignado = cursor.fetchone()["total"]
            cursor.execute(
                "SELECT COALESCE(SUM(cantidad), 0) AS total FROM importacion_sobrantes_ventas "
                "WHERE importacion_producto_id = %s AND estado = 'VALIDADO'",
                (p["id"],),
            )
            vendido = cursor.fetchone()["total"]
            resultado.append({
                **p,
                "cantidad_asignada": asignado,
                "cantidad_vendida": vendido,
                "cantidad_sobrante": max(p["cantidad_embarcada"] - asignado, 0),
                "cantidad_disponible": disponible,
            })
        return resultado
    finally:
        conn.close()
```

- [ ] **Step 2: Agregar las 2 rutas en `routes/asignaciones_importaciones.py`**

```python
@asignaciones_bp.route("/<int:importacion_id>/asignaciones/productos", methods=["GET"])
@token_required
@_requiere_rol_importaciones
def listar_productos_ruta(importacion_id):
    data = svc.listar_productos(importacion_id)
    return jsonify({"ok": True, "data": data}), 200


@asignaciones_bp.route("/<int:importacion_id>/asignaciones/productos", methods=["POST"])
@token_required
@_requiere_rol_importaciones
def crear_producto_ruta(importacion_id):
    body = request.get_json(silent=True) or {}
    payload = getattr(request, "cliente_data", {}) or {}
    data = svc.crear_producto(
        importacion_id=importacion_id,
        sku=body.get("sku"),
        cantidad_embarcada=body.get("cantidad_embarcada"),
        periodo=body.get("periodo"),
        descripcion=body.get("descripcion"),
        usuario_id=payload.get("id"),
    )
    return jsonify({"ok": True, "data": data}), 201
```

- [ ] **Step 3: Escribir los tests de servicio (mockeando la conexión)**

```python
# tests/test_asignaciones_service.py
from unittest.mock import MagicMock

import pytest

from services.asignaciones_service import AsignacionesError, crear_producto, listar_productos


def _mock_conn(mocker, cursor):
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mocker.patch("services.asignaciones_service.obtener_conexion", return_value=conn)
    return conn


def test_crear_producto_rechaza_cantidad_negativa():
    with pytest.raises(AsignacionesError) as exc:
        crear_producto(1, "SKU-1", -1, "2026-2027")
    assert exc.value.code == "SKU_INVALIDO"


def test_crear_producto_rechaza_sku_vacio():
    with pytest.raises(AsignacionesError) as exc:
        crear_producto(1, "   ", 10, "2026-2027")
    assert exc.value.code == "SKU_INVALIDO"


def test_crear_producto_importacion_no_existe(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = None  # SELECT id FROM importaciones -> nada
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        crear_producto(999, "SKU-1", 10, "2026-2027")
    assert exc.value.code == "IMPORTACION_NO_EXISTE"
    assert exc.value.status == 404


def test_crear_producto_sku_duplicado(mocker):
    cursor = MagicMock()
    # 1ra llamada: importacion existe. 2da: ya hay un producto con ese sku_norm.
    cursor.fetchone.side_effect = [{"id": 1}, {"id": 5}]
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        crear_producto(1, "427102-0001004", 10, "2026-2027")
    assert exc.value.code == "SKU_DUPLICADO"
    assert exc.value.status == 409


def test_crear_producto_exitoso_normaliza_sku_y_registra_entrada(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 1},                                            # importacion existe
        None,                                                  # no hay duplicado
        {"id": 10, "sku": "427102-0001004", "sku_norm": "4271020001004",
         "cantidad_embarcada": 10, "periodo": "2026-2027",
         "importacion_id": 1, "descripcion": None},            # SELECT final
    ]
    cursor.lastrowid = 10
    _mock_conn(mocker, cursor)

    resultado = crear_producto(1, "427102-0001004", 10, "2026-2027")

    assert resultado["sku_norm"] == "4271020001004"
    inserts = [c for c in cursor.execute.call_args_list if "INSERT INTO importacion_movimientos" in c.args[0]]
    assert len(inserts) == 1
    assert inserts[0].args[1][1] == "ENTRADA"
    assert inserts[0].args[1][2] == 10


def test_listar_productos_calcula_disponible_asignado_sobrante_vendido(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 1},                                   # importacion existe
        {"disponible": 7},                            # _disponible_producto
        {"total": 3},                                 # asignado
        {"total": 0},                                 # vendido
    ]
    cursor.fetchall.return_value = [
        {"id": 10, "importacion_id": 1, "sku": "SKU-1", "sku_norm": "SKU1",
         "cantidad_embarcada": 10, "periodo": "2026-2027", "descripcion": None},
    ]
    _mock_conn(mocker, cursor)

    resultado = listar_productos(1)

    assert len(resultado) == 1
    assert resultado[0]["cantidad_disponible"] == 7
    assert resultado[0]["cantidad_asignada"] == 3
    assert resultado[0]["cantidad_sobrante"] == 7  # 10 embarcado - 3 asignado
    assert resultado[0]["cantidad_vendida"] == 0
```

- [ ] **Step 4: Ejecutar los tests**

Run: `pytest tests/test_asignaciones_service.py -v`
Expected: 6 tests PASS.

- [ ] **Step 5: Test de integración end-to-end contra la BD local**

Agregar a `tests/test_asignaciones_routes.py` (requiere un embarque real; usa el primero que exista):

```python
def test_crear_y_listar_producto_end_to_end():
    conn = obtener_conexion()
    if not conn:
        import pytest
        pytest.skip("Sin conexión a BD local para este test")
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id FROM importaciones LIMIT 1")
    embarque = cursor.fetchone()
    conn.close()
    if not embarque:
        import pytest
        pytest.skip("No hay embarques en la BD local para probar")

    client = _cliente_test()
    token = _token_valido(rol=1)
    headers = {"Authorization": f"Bearer {token}"}
    sku_unico = f"TEST-{embarque['id']}-000001"

    resp = client.post(
        f"/importaciones/{embarque['id']}/asignaciones/productos",
        json={"sku": sku_unico, "cantidad_embarcada": 5, "periodo": "2026-2027"},
        headers=headers,
    )
    assert resp.status_code == 201
    producto = resp.get_json()["data"]
    assert producto["sku_norm"] == sku_unico.replace("-", "")

    resp2 = client.get(f"/importaciones/{embarque['id']}/asignaciones/productos", headers=headers)
    assert resp2.status_code == 200
    productos = resp2.get_json()["data"]
    encontrado = next(p for p in productos if p["id"] == producto["id"])
    assert encontrado["cantidad_disponible"] == 5
    assert encontrado["cantidad_asignada"] == 0
```

Run: `pytest tests/test_asignaciones_routes.py -v`
Expected: todos PASS (o `SKIPPED` si no hay BD local/embarques, nunca FAIL).

- [ ] **Step 6: Commit**

```bash
git add services/asignaciones_service.py routes/asignaciones_importaciones.py tests/test_asignaciones_service.py tests/test_asignaciones_routes.py
git commit -m "feat: registrar y listar productos de un embarque en Asignaciones

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5: Actualizar producto (ajuste de cantidad embarcada / descripción)

**Files:**
- Modify: `services/asignaciones_service.py` (agregar `actualizar_producto`)
- Modify: `routes/asignaciones_importaciones.py` (agregar ruta `PUT`)
- Modify: `tests/test_asignaciones_service.py`, `tests/test_asignaciones_routes.py`

**Interfaces:**
- Consumes: `_registrar_movimiento` (Task 4)
- Produces: `actualizar_producto(producto_id: int, cantidad_embarcada=None, descripcion=None, usuario_id=None) -> dict`

- [ ] **Step 1: Agregar `actualizar_producto` a `services/asignaciones_service.py`**

```python
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
```

- [ ] **Step 2: Agregar la ruta `PUT`**

```python
@asignaciones_bp.route("/<int:importacion_id>/asignaciones/productos/<int:producto_id>", methods=["PUT"])
@token_required
@_requiere_rol_importaciones
def actualizar_producto_ruta(importacion_id, producto_id):
    body = request.get_json(silent=True) or {}
    payload = getattr(request, "cliente_data", {}) or {}
    data = svc.actualizar_producto(
        producto_id=producto_id,
        cantidad_embarcada=body.get("cantidad_embarcada"),
        descripcion=body.get("descripcion"),
        usuario_id=payload.get("id"),
    )
    return jsonify({"ok": True, "data": data}), 200
```

- [ ] **Step 3: Tests de servicio**

Agregar a `tests/test_asignaciones_service.py`:

```python
from services.asignaciones_service import actualizar_producto


def test_actualizar_producto_no_existe(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        actualizar_producto(999, cantidad_embarcada=10)
    assert exc.value.code == "PRODUCTO_NO_EXISTE"


def test_actualizar_producto_rechaza_bajar_por_debajo_de_lo_asignado(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 10, "cantidad_embarcada": 10},  # producto (FOR UPDATE)
        {"total": 8},                          # ya asignado
    ]
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        actualizar_producto(10, cantidad_embarcada=5)
    assert exc.value.code == "AJUSTE_INVALIDO"


def test_actualizar_producto_registra_movimiento_ajuste(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 10, "cantidad_embarcada": 10},
        {"total": 3},
        {"id": 10, "cantidad_embarcada": 15, "descripcion": None},  # SELECT final
    ]
    _mock_conn(mocker, cursor)

    resultado = actualizar_producto(10, cantidad_embarcada=15)

    assert resultado["cantidad_embarcada"] == 15
    ajustes = [c for c in cursor.execute.call_args_list if "INSERT INTO importacion_movimientos" in c.args[0]]
    assert len(ajustes) == 1
    assert ajustes[0].args[1][1] == "AJUSTE"
    assert ajustes[0].args[1][2] == 5  # 15 - 10
```

- [ ] **Step 4: Ejecutar los tests**

Run: `pytest tests/test_asignaciones_service.py -v`
Expected: 9 tests PASS (6 anteriores + 3 nuevos).

- [ ] **Step 5: Commit**

```bash
git add services/asignaciones_service.py routes/asignaciones_importaciones.py tests/test_asignaciones_service.py
git commit -m "feat: permitir actualizar cantidad_embarcada y descripcion de un producto

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 6: Demanda neta por cliente + propuesta de reparto (`recalcular`)

**Files:**
- Modify: `services/proyecciones_service.py` (agregar `demanda_neta_por_cliente`)
- Modify: `services/asignaciones_service.py` (agregar `recalcular_propuesta`)
- Modify: `routes/asignaciones_importaciones.py` (agregar ruta `POST recalcular`)
- Modify: `tests/test_proyecciones_service.py`, `tests/test_asignaciones_service.py`

**Interfaces:**
- Consumes: `forecast_proyecciones` (lectura), `_get_ordenes_my27`, `_PRIORIDAD_MAP` (Task 1), `_disponible_producto` (Task 4)
- Produces: `demanda_neta_por_cliente(periodo: str, skus_norm: list[str]) -> dict` (`{sku_norm: {clave_cliente: cantidad_neta}}`), `recalcular_propuesta(importacion_id: int, periodo_filtro: str = None) -> list[dict]` — cada item: `{producto_id, sku, periodo, cantidad_embarcada, disponible, proyecciones_disponibles, propuesta: [{clave_cliente, prioridad, cantidad_proyectada, cantidad_sugerida}], sobrante_estimado}`. Esta función es **de solo lectura**, no persiste nada (Task 7 hace la persistencia real).

- [ ] **Step 1: Agregar `demanda_neta_por_cliente` a `services/proyecciones_service.py`**

```python
from db_conexion import obtener_conexion


def demanda_neta_por_cliente(periodo: str, skus_norm: list) -> dict:
    """
    {sku_norm: {clave_cliente: cantidad_neta_pendiente}} — proyección total
    del periodo por cliente, menos lo que Odoo ya tiene confirmado (sale.order).
    Lanza RuntimeError si no hay conexión a BD (el caller decide cómo degradar).
    """
    if not skus_norm:
        return {}
    skus_norm_set = set(skus_norm)

    conn = obtener_conexion()
    if not conn:
        raise RuntimeError("Sin conexión a BD para consultar forecast_proyecciones")
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT clave_cliente, sku, "
            "(mayo+junio+julio+agosto+septiembre+octubre+noviembre+diciembre"
            "+enero+febrero+marzo+abril) AS total "
            "FROM forecast_proyecciones WHERE periodo = %s",
            (periodo,),
        )
        crudo: dict = {}
        for row in cursor.fetchall():
            sku_n = _norm_sku(row["sku"])
            if sku_n not in skus_norm_set:
                continue
            crudo.setdefault(sku_n, {})
            crudo[sku_n][row["clave_cliente"]] = crudo[sku_n].get(row["clave_cliente"], 0) + (row["total"] or 0)
    finally:
        conn.close()

    try:
        ordenes = _get_ordenes_my27(periodo)
    except Exception:
        logging.exception("No se pudo deducir órdenes Odoo para %s", periodo)
        ordenes = {}

    resultado: dict = {}
    for sku_n, por_cliente in crudo.items():
        resultado[sku_n] = {}
        for clave, cantidad in por_cliente.items():
            ya_en_odoo = ordenes.get(clave, {}).get(sku_n, 0)
            neta = max(cantidad - ya_en_odoo, 0)
            if neta > 0:
                resultado[sku_n][clave] = neta
    return resultado
```

- [ ] **Step 2: Agregar `recalcular_propuesta` a `services/asignaciones_service.py`**

```python
import logging as _logging

from services.proyecciones_service import demanda_neta_por_cliente, _PRIORIDAD_MAP


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
            _logging.exception("Proyecciones no disponibles al recalcular embarque %s", importacion_id)
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
```

- [ ] **Step 3: Agregar la ruta `POST recalcular`**

```python
@asignaciones_bp.route("/<int:importacion_id>/asignaciones/recalcular", methods=["POST"])
@token_required
@_requiere_rol_importaciones
def recalcular_ruta(importacion_id):
    body = request.get_json(silent=True) or {}
    data = svc.recalcular_propuesta(importacion_id, body.get("periodo"))
    return jsonify({"ok": True, "data": data}), 200
```

- [ ] **Step 4: Tests de `demanda_neta_por_cliente`**

Agregar a `tests/test_proyecciones_service.py`:

```python
from unittest.mock import MagicMock

from services.proyecciones_service import demanda_neta_por_cliente


def test_demanda_neta_resta_lo_ya_confirmado_en_odoo(mocker):
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        {"clave_cliente": "LC657", "sku": "427102-0001004", "total": 5},
        {"clave_cliente": "MC677", "sku": "427102-0001004", "total": 2},
    ]
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mocker.patch("services.proyecciones_service.obtener_conexion", return_value=conn)
    mocker.patch(
        "services.proyecciones_service._get_ordenes_my27",
        return_value={"LC657": {"4271020001004": 4}},
    )

    resultado = demanda_neta_por_cliente("2026-2027", ["4271020001004"])

    assert resultado["4271020001004"]["LC657"] == 1   # 5 - 4 ya confirmado
    assert resultado["4271020001004"]["MC677"] == 2   # sin órdenes previas, se mantiene


def test_demanda_neta_degrada_a_demanda_bruta_si_odoo_falla(mocker):
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        {"clave_cliente": "LC657", "sku": "SKU-1", "total": 5},
    ]
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mocker.patch("services.proyecciones_service.obtener_conexion", return_value=conn)
    mocker.patch(
        "services.proyecciones_service._get_ordenes_my27",
        side_effect=Exception("Odoo no disponible"),
    )

    resultado = demanda_neta_por_cliente("2026-2027", ["SKU1"])

    assert resultado["SKU1"]["LC657"] == 5  # sin deducción, pero no falla


def test_demanda_neta_lanza_runtime_error_sin_conexion(mocker):
    mocker.patch("services.proyecciones_service.obtener_conexion", return_value=None)
    with pytest.raises(RuntimeError):
        demanda_neta_por_cliente("2026-2027", ["SKU1"])
```

(agregar `import pytest` al inicio del archivo si no está ya presente por el Step 4 de la Task 1)

- [ ] **Step 5: Tests de `recalcular_propuesta`**

Agregar a `tests/test_asignaciones_service.py`:

```python
from services.asignaciones_service import recalcular_propuesta


def test_recalcular_reparte_por_prioridad_hasta_agotar_disponible(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [{"id": 1}]  # importacion existe
    cursor.fetchall.return_value = [
        {"id": 10, "sku": "SKU-1", "sku_norm": "SKU1", "periodo": "2026-2027",
         "cantidad_embarcada": 10, "importacion_id": 1},
    ]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=10)
    mocker.patch(
        "services.asignaciones_service.demanda_neta_por_cliente",
        return_value={"SKU1": {"MC677": 2, "LC657": 3}},  # LC657 tiene mayor prioridad (1 vs 2)
    )

    propuestas = recalcular_propuesta(1)

    assert len(propuestas) == 1
    orden = [c["clave_cliente"] for c in propuestas[0]["propuesta"]]
    assert orden == ["LC657", "MC677"]  # prioridad 1 antes que prioridad 2
    assert propuestas[0]["sobrante_estimado"] == 5  # 10 - 3 - 2


def test_recalcular_no_sobreasigna_cuando_demanda_supera_disponible(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [{"id": 1}]
    cursor.fetchall.return_value = [
        {"id": 10, "sku": "SKU-1", "sku_norm": "SKU1", "periodo": "2026-2027",
         "cantidad_embarcada": 4, "importacion_id": 1},
    ]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=4)
    mocker.patch(
        "services.asignaciones_service.demanda_neta_por_cliente",
        return_value={"SKU1": {"LC657": 3, "MC677": 3}},  # demanda total 6 > disponible 4
    )

    propuestas = recalcular_propuesta(1)

    total_sugerido = sum(c["cantidad_sugerida"] for c in propuestas[0]["propuesta"])
    assert total_sugerido == 4  # nunca más que el disponible
    assert propuestas[0]["sobrante_estimado"] == 0


def test_recalcular_degrada_sin_romper_si_proyecciones_falla(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [{"id": 1}]
    cursor.fetchall.return_value = [
        {"id": 10, "sku": "SKU-1", "sku_norm": "SKU1", "periodo": "2026-2027",
         "cantidad_embarcada": 4, "importacion_id": 1},
    ]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=4)
    mocker.patch(
        "services.asignaciones_service.demanda_neta_por_cliente",
        side_effect=RuntimeError("Sin conexión a BD para consultar forecast_proyecciones"),
    )

    propuestas = recalcular_propuesta(1)

    assert propuestas[0]["proyecciones_disponibles"] is False
    assert propuestas[0]["propuesta"] == []
    assert propuestas[0]["sobrante_estimado"] == 4
```

- [ ] **Step 6: Ejecutar todos los tests**

Run: `pytest tests/test_proyecciones_service.py tests/test_asignaciones_service.py -v`
Expected: todos PASS.

- [ ] **Step 7: Commit**

```bash
git add services/proyecciones_service.py services/asignaciones_service.py routes/asignaciones_importaciones.py tests/test_proyecciones_service.py tests/test_asignaciones_service.py
git commit -m "feat: recalcular propuesta de reparto por prioridad con demanda neta de Odoo

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 7: Confirmar asignación (transaccional, rechaza sobre-asignación)

**Files:**
- Modify: `services/asignaciones_service.py` (agregar `asignar`)
- Modify: `routes/asignaciones_importaciones.py` (agregar ruta `POST asignar`)
- Modify: `tests/test_asignaciones_service.py`

**Interfaces:**
- Consumes: `_disponible_producto`, `_registrar_movimiento` (Task 4), `_PRIORIDAD_MAP` (Task 1)
- Produces: `asignar(producto_id: int, asignaciones: list[dict], usuario_id: int = None) -> dict` (`{producto_id, disponible_restante}`). Cada item de `asignaciones` es `{"clave_cliente": str, "cantidad": int}`.

**Nota de concurrencia:** el primer `SELECT ... FOR UPDATE` bloquea la fila de `importacion_productos`, serializando **todas** las operaciones que mutan cantidades sobre ese producto (asignar, venta-sobrante — Task 8 usa el mismo patrón) — es lo que garantiza que dos asignaciones simultáneas nunca dejen el disponible en negativo.

- [ ] **Step 1: Agregar `asignar` a `services/asignaciones_service.py`**

```python
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
```

- [ ] **Step 2: Agregar la ruta `POST asignar`**

```python
@asignaciones_bp.route("/<int:importacion_id>/asignaciones/productos/<int:producto_id>/asignar", methods=["POST"])
@token_required
@_requiere_rol_importaciones
def asignar_ruta(importacion_id, producto_id):
    body = request.get_json(silent=True) or {}
    payload = getattr(request, "cliente_data", {}) or {}
    data = svc.asignar(producto_id, body.get("asignaciones") or [], usuario_id=payload.get("id"))
    return jsonify({"ok": True, "data": data}), 200
```

- [ ] **Step 3: Tests de servicio**

Agregar a `tests/test_asignaciones_service.py`:

```python
from services.asignaciones_service import asignar


def test_asignar_rechaza_lista_vacia():
    with pytest.raises(AsignacionesError) as exc:
        asignar(10, [])
    assert exc.value.code == "ASIGNACION_INVALIDA"


def test_asignar_rechaza_cantidad_no_entera_o_negativa():
    with pytest.raises(AsignacionesError) as exc:
        asignar(10, [{"clave_cliente": "LC657", "cantidad": -1}])
    assert exc.value.code == "ASIGNACION_INVALIDA"


def test_asignar_rechaza_producto_inexistente(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        asignar(999, [{"clave_cliente": "LC657", "cantidad": 1}])
    assert exc.value.code == "PRODUCTO_NO_EXISTE"


def test_asignar_rechaza_cliente_inexistente(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [{"id": 10}, None]  # producto existe, cliente no
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        asignar(10, [{"clave_cliente": "ZZZZZ", "cantidad": 1}])
    assert exc.value.code == "CLIENTE_NO_EXISTE"


def test_asignar_rechaza_sobreasignacion(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [{"id": 10}, {"clave": "LC657"}]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=2)

    with pytest.raises(AsignacionesError) as exc:
        asignar(10, [{"clave_cliente": "LC657", "cantidad": 3}])  # pide 3, solo hay 2
    assert exc.value.code == "STOCK_INSUFICIENTE"
    assert exc.value.status == 409


def test_asignar_exitoso_inserta_asignacion_y_movimiento(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 10},                # producto FOR UPDATE
        {"clave": "LC657"},        # cliente existe
        None,                      # no hay asignación previa para este cliente
    ]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=5)

    resultado = asignar(10, [{"clave_cliente": "lc657", "cantidad": 3}], usuario_id=7)

    assert resultado == {"producto_id": 10, "disponible_restante": 2}
    inserts = [c for c in cursor.execute.call_args_list if "INSERT INTO importacion_asignaciones" in c.args[0]]
    assert len(inserts) == 1
    assert inserts[0].args[1][1] == "LC657"  # clave normalizada a mayúsculas
    movimientos = [c for c in cursor.execute.call_args_list if "INSERT INTO importacion_movimientos" in c.args[0]]
    assert movimientos[0].args[1][1] == "ASIGNACION"
    assert movimientos[0].args[1][2] == -3


def test_asignar_a_cliente_ya_asignado_suma_en_lugar_de_duplicar(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 10},
        {"clave": "LC657"},
        {"id": 55},  # ya existe una fila de asignación para este producto+cliente
    ]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=5)

    asignar(10, [{"clave_cliente": "LC657", "cantidad": 2}])

    updates = [c for c in cursor.execute.call_args_list if "UPDATE importacion_asignaciones" in c.args[0]]
    assert len(updates) == 1
    inserts = [c for c in cursor.execute.call_args_list if "INSERT INTO importacion_asignaciones" in c.args[0]]
    assert len(inserts) == 0
```

- [ ] **Step 4: Ejecutar los tests**

Run: `pytest tests/test_asignaciones_service.py -v`
Expected: 22 tests PASS (16 anteriores + 6 nuevos).

- [ ] **Step 5: Commit**

```bash
git add services/asignaciones_service.py routes/asignaciones_importaciones.py tests/test_asignaciones_service.py
git commit -m "feat: confirmar asignación de unidades a clientes con bloqueo transaccional

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 8: Venta anticipada de sobrante (idempotente)

**Files:**
- Modify: `services/asignaciones_service.py` (agregar `crear_venta_sobrante`)
- Modify: `routes/asignaciones_importaciones.py` (agregar ruta `POST venta-sobrante`)
- Modify: `tests/test_asignaciones_service.py`

**Interfaces:**
- Consumes: `_disponible_producto`, `_registrar_movimiento` (Task 4)
- Produces: `crear_venta_sobrante(producto_id, clave_cliente, cantidad, numero_pedido_odoo=None, usuario_id=None) -> dict` (fila de `importacion_sobrantes_ventas`)

**Nota de idempotencia:** el `UNIQUE(numero_pedido_odoo)` de la tabla es la garantía real. El chequeo previo (`SELECT ... WHERE numero_pedido_odoo = %s`) es solo una vía rápida para el caso normal; el `try/except IntegrityError` alrededor del `INSERT` cubre la carrera de dos clicks simultáneos con el mismo folio — en ese caso, en vez de fallar con un error genérico de BD, se devuelve el registro que ya ganó la carrera.

- [ ] **Step 1: Agregar `crear_venta_sobrante` a `services/asignaciones_service.py`**

```python
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
```

- [ ] **Step 2: Agregar la ruta `POST venta-sobrante`**

```python
@asignaciones_bp.route(
    "/<int:importacion_id>/asignaciones/productos/<int:producto_id>/venta-sobrante", methods=["POST"]
)
@token_required
@_requiere_rol_importaciones
def venta_sobrante_ruta(importacion_id, producto_id):
    body = request.get_json(silent=True) or {}
    payload = getattr(request, "cliente_data", {}) or {}
    data = svc.crear_venta_sobrante(
        producto_id=producto_id,
        clave_cliente=body.get("clave_cliente"),
        cantidad=body.get("cantidad"),
        numero_pedido_odoo=body.get("numero_pedido_odoo"),
        usuario_id=payload.get("id"),
    )
    return jsonify({"ok": True, "data": data}), 201
```

- [ ] **Step 3: Tests de servicio**

Agregar a `tests/test_asignaciones_service.py`:

```python
import mysql.connector.errors

from services.asignaciones_service import crear_venta_sobrante


def test_venta_sobrante_rechaza_cantidad_invalida():
    with pytest.raises(AsignacionesError) as exc:
        crear_venta_sobrante(10, "LC657", 0)
    assert exc.value.code == "VENTA_INVALIDA"


def test_venta_sobrante_rechaza_sobreventa(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [{"id": 10}, {"clave": "LC657"}]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=1)

    with pytest.raises(AsignacionesError) as exc:
        crear_venta_sobrante(10, "LC657", 2)  # pide 2, solo hay 1 disponible
    assert exc.value.code == "SOBRANTE_INSUFICIENTE"


def test_venta_sobrante_exitosa_queda_pendiente_de_validacion(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 10},                    # producto FOR UPDATE
        {"clave": "LC657"},            # cliente existe
        {"id": 77, "estado": "PENDIENTE_VALIDACION", "numero_pedido_odoo": None},  # SELECT final
    ]
    cursor.lastrowid = 77
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=5)

    resultado = crear_venta_sobrante(10, "LC657", 1)

    assert resultado["estado"] == "PENDIENTE_VALIDACION"


def test_venta_sobrante_con_folio_repetido_devuelve_la_existente_sin_duplicar(mocker):
    cursor = MagicMock()
    venta_existente = {"id": 1, "numero_pedido_odoo": "SO12345", "estado": "VALIDADO"}
    cursor.fetchone.side_effect = [venta_existente]  # el chequeo previo ya la encuentra
    _mock_conn(mocker, cursor)

    resultado = crear_venta_sobrante(10, "LC657", 1, numero_pedido_odoo="SO12345")

    assert resultado == venta_existente
    inserts = [c for c in cursor.execute.call_args_list if c.args[0].startswith("INSERT")]
    assert len(inserts) == 0  # nunca llegó a intentar el INSERT


def test_venta_sobrante_race_condition_en_integrity_error_no_duplica(mocker):
    cursor = MagicMock()
    venta_ganadora = {"id": 1, "numero_pedido_odoo": "SO12345", "estado": "PENDIENTE_VALIDACION"}
    cursor.fetchone.side_effect = [
        None,               # chequeo previo: todavía no existe
        {"id": 10},          # producto FOR UPDATE
        {"clave": "LC657"},  # cliente existe
        venta_ganadora,       # SELECT tras el IntegrityError: otro proceso ganó la carrera
    ]
    cursor.execute.side_effect = [
        None, None, None, None,  # SELECT folio, SELECT producto, SELECT cliente, _disponible_producto SQL
        mysql.connector.errors.IntegrityError("Duplicate entry"),  # INSERT falla por la carrera
        None,  # SELECT de recuperación
    ]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=5)

    resultado = crear_venta_sobrante(10, "LC657", 1, numero_pedido_odoo="SO12345")

    assert resultado == venta_ganadora
```

- [ ] **Step 4: Ejecutar los tests**

Run: `pytest tests/test_asignaciones_service.py -v`
Expected: 27 tests PASS (22 anteriores + 5 nuevos).

- [ ] **Step 5: Commit**

```bash
git add services/asignaciones_service.py routes/asignaciones_importaciones.py tests/test_asignaciones_service.py
git commit -m "feat: registrar venta anticipada de sobrante de forma idempotente

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 9: Validar el pedido de Odoo de una venta anticipada

**Files:**
- Modify: `services/asignaciones_service.py` (agregar `validar_venta_odoo`)
- Modify: `routes/asignaciones_importaciones.py` (agregar ruta `POST validar-odoo`)
- Modify: `tests/test_asignaciones_service.py`

**Interfaces:**
- Consumes: `utils.odoo_utils.get_odoo_models, ODOO_DB, ODOO_PASSWORD, ODOO_COMPANY_ID`, `services.proyecciones_service._norm_sku`
- Produces: `validar_venta_odoo(venta_id: int, numero_pedido_odoo: str = None) -> dict` (fila de `importacion_sobrantes_ventas` con `estado='VALIDADO'`)

**Nota de resiliencia:** si Odoo no responde, la función **no toca** el movimiento ya registrado en el Task 8 — solo lanza `PEDIDO_ODOO_NO_DISPONIBLE` (503), la venta se queda en `PENDIENTE_VALIDACION` y el usuario puede reintentar más tarde sin perder nada (sección 49 del brief).

- [ ] **Step 1: Agregar `validar_venta_odoo` a `services/asignaciones_service.py`**

```python
from utils.odoo_utils import get_odoo_models, ODOO_DB, ODOO_PASSWORD, ODOO_COMPANY_ID


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
        lineas = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD, "sale.order.line", "read",
            [pedido["order_line"]], {"fields": ["product_id", "product_uom_qty"]},
        )

    producto_ids = list({linea["product_id"][0] for linea in lineas if linea.get("product_id")})
    codigos = {}
    if producto_ids:
        info = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD, "product.product", "read",
            [producto_ids], {"fields": ["default_code"]},
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
```

- [ ] **Step 2: Agregar la ruta `POST validar-odoo`**

```python
@asignaciones_bp.route(
    "/<int:importacion_id>/asignaciones/ventas/<int:venta_id>/validar-odoo", methods=["POST"]
)
@token_required
@_requiere_rol_importaciones
def validar_odoo_ruta(importacion_id, venta_id):
    body = request.get_json(silent=True) or {}
    data = svc.validar_venta_odoo(venta_id, body.get("numero_pedido_odoo"))
    return jsonify({"ok": True, "data": data}), 200
```

- [ ] **Step 3: Tests de servicio (mockeando `get_odoo_models`)**

Agregar a `tests/test_asignaciones_service.py`:

```python
from services.asignaciones_service import validar_venta_odoo


def _mock_conn_secuencia(mocker, fetchone_side_effect):
    cursor = MagicMock()
    cursor.fetchone.side_effect = fetchone_side_effect
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mocker.patch("services.asignaciones_service.obtener_conexion", return_value=conn)
    return cursor


def test_validar_odoo_venta_no_existe(mocker):
    _mock_conn_secuencia(mocker, [None])
    with pytest.raises(AsignacionesError) as exc:
        validar_venta_odoo(999, "SO1")
    assert exc.value.code == "VENTA_NO_EXISTE"


def test_validar_odoo_rechaza_venta_cancelada(mocker):
    _mock_conn_secuencia(mocker, [{"id": 1, "estado": "CANCELADO", "numero_pedido_odoo": "SO1"}])
    with pytest.raises(AsignacionesError) as exc:
        validar_venta_odoo(1, "SO1")
    assert exc.value.code == "VENTA_YA_CANCELADA"


def test_validar_odoo_no_disponible_no_rompe_la_venta(mocker):
    _mock_conn_secuencia(mocker, [
        {"id": 1, "estado": "PENDIENTE_VALIDACION", "numero_pedido_odoo": "SO1",
         "clave_cliente": "LC657", "cantidad": 2, "importacion_producto_id": 10},
        {"sku_norm": "SKU1"},
    ])
    mocker.patch("services.asignaciones_service.get_odoo_models", return_value=(None, None, "timeout"))

    with pytest.raises(AsignacionesError) as exc:
        validar_venta_odoo(1, "SO1")
    assert exc.value.code == "PEDIDO_ODOO_NO_DISPONIBLE"
    assert exc.value.status == 503


def test_validar_odoo_pedido_no_existe_en_odoo(mocker):
    _mock_conn_secuencia(mocker, [
        {"id": 1, "estado": "PENDIENTE_VALIDACION", "numero_pedido_odoo": "SO1",
         "clave_cliente": "LC657", "cantidad": 2, "importacion_producto_id": 10},
        {"sku_norm": "SKU1"},
    ])
    models = MagicMock()
    models.execute_kw.return_value = []  # sale.order search_read no encuentra nada
    mocker.patch("services.asignaciones_service.get_odoo_models", return_value=(1, models, None))

    with pytest.raises(AsignacionesError) as exc:
        validar_venta_odoo(1, "SO1")
    assert exc.value.code == "PEDIDO_ODOO_NO_EXISTE"


def test_validar_odoo_rechaza_cliente_distinto(mocker):
    _mock_conn_secuencia(mocker, [
        {"id": 1, "estado": "PENDIENTE_VALIDACION", "numero_pedido_odoo": "SO1",
         "clave_cliente": "LC657", "cantidad": 2, "importacion_producto_id": 10},
        {"sku_norm": "SKU1"},
    ])
    models = MagicMock()
    models.execute_kw.side_effect = [
        [{"id": 5, "name": "SO1", "partner_id": [99, "Otro"], "state": "sale", "order_line": []}],
        [{"ref": "MC677"}],  # partner con ref distinto al de la venta
    ]
    mocker.patch("services.asignaciones_service.get_odoo_models", return_value=(1, models, None))

    with pytest.raises(AsignacionesError) as exc:
        validar_venta_odoo(1, "SO1")
    assert exc.value.code == "PEDIDO_ODOO_INVALIDO"


def test_validar_odoo_rechaza_cantidad_insuficiente_en_las_lineas(mocker):
    _mock_conn_secuencia(mocker, [
        {"id": 1, "estado": "PENDIENTE_VALIDACION", "numero_pedido_odoo": "SO1",
         "clave_cliente": "LC657", "cantidad": 2, "importacion_producto_id": 10},
        {"sku_norm": "4271020001004"},
    ])
    models = MagicMock()
    models.execute_kw.side_effect = [
        [{"id": 5, "name": "SO1", "partner_id": [1, "LC657"], "state": "sale", "order_line": [50]}],
        [{"ref": "LC657"}],
        [{"product_id": [200, "Bici"], "product_uom_qty": 1.0}],  # solo 1 unidad, se esperaban 2
        [{"id": 200, "default_code": "427102-0001004"}],
    ]
    mocker.patch("services.asignaciones_service.get_odoo_models", return_value=(1, models, None))

    with pytest.raises(AsignacionesError) as exc:
        validar_venta_odoo(1, "SO1")
    assert exc.value.code == "PEDIDO_ODOO_INVALIDO"


def test_validar_odoo_exitoso_marca_validado(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 1, "estado": "PENDIENTE_VALIDACION", "numero_pedido_odoo": "SO1",
         "clave_cliente": "LC657", "cantidad": 2, "importacion_producto_id": 10},
        {"sku_norm": "4271020001004"},
        {"id": 1, "estado": "VALIDADO", "numero_pedido_odoo": "SO1"},  # SELECT final (2da conexión)
    ]
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mocker.patch("services.asignaciones_service.obtener_conexion", return_value=conn)

    models = MagicMock()
    models.execute_kw.side_effect = [
        [{"id": 5, "name": "SO1", "partner_id": [1, "LC657"], "state": "sale", "order_line": [50]}],
        [{"ref": "LC657"}],
        [{"product_id": [200, "Bici"], "product_uom_qty": 2.0}],
        [{"id": 200, "default_code": "427102-0001004"}],
    ]
    mocker.patch("services.asignaciones_service.get_odoo_models", return_value=(1, models, None))

    resultado = validar_venta_odoo(1, "SO1")

    assert resultado["estado"] == "VALIDADO"
```

- [ ] **Step 4: Ejecutar los tests**

Run: `pytest tests/test_asignaciones_service.py -v`
Expected: 34 tests PASS (27 anteriores + 7 nuevos).

- [ ] **Step 5: Commit**

```bash
git add services/asignaciones_service.py routes/asignaciones_importaciones.py tests/test_asignaciones_service.py
git commit -m "feat: validar pedido de Odoo antes de confirmar una venta de sobrante

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 10: Cancelar una venta anticipada (restaura disponibilidad)

**Files:**
- Modify: `services/asignaciones_service.py` (agregar `cancelar_venta`)
- Modify: `routes/asignaciones_importaciones.py` (agregar ruta `POST cancelar`)
- Modify: `tests/test_asignaciones_service.py`

**Interfaces:**
- Consumes: `_registrar_movimiento` (Task 4)
- Produces: `cancelar_venta(venta_id: int, usuario_id: int = None) -> dict`

- [ ] **Step 1: Agregar `cancelar_venta` a `services/asignaciones_service.py`**

```python
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
```

- [ ] **Step 2: Agregar la ruta `POST cancelar`**

```python
@asignaciones_bp.route(
    "/<int:importacion_id>/asignaciones/ventas/<int:venta_id>/cancelar", methods=["POST"]
)
@token_required
@_requiere_rol_importaciones
def cancelar_venta_ruta(importacion_id, venta_id):
    payload = getattr(request, "cliente_data", {}) or {}
    data = svc.cancelar_venta(venta_id, usuario_id=payload.get("id"))
    return jsonify({"ok": True, "data": data}), 200
```

- [ ] **Step 3: Tests de servicio**

Agregar a `tests/test_asignaciones_service.py`:

```python
from services.asignaciones_service import cancelar_venta


def test_cancelar_venta_no_existe(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        cancelar_venta(999)
    assert exc.value.code == "VENTA_NO_EXISTE"


def test_cancelar_venta_ya_cancelada_no_se_puede_cancelar_dos_veces(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = {
        "id": 1, "estado": "CANCELADO", "importacion_producto_id": 10,
        "clave_cliente": "LC657", "cantidad": 2, "numero_pedido_odoo": "SO1",
    }
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        cancelar_venta(1)
    assert exc.value.code == "VENTA_YA_CANCELADA"


def test_cancelar_venta_restaura_disponibilidad_con_movimiento_positivo(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 1, "estado": "PENDIENTE_VALIDACION", "importacion_producto_id": 10,
         "clave_cliente": "LC657", "cantidad": 2, "numero_pedido_odoo": "SO1"},
        {"id": 1, "estado": "CANCELADO", "importacion_producto_id": 10,
         "clave_cliente": "LC657", "cantidad": 2, "numero_pedido_odoo": "SO1"},
    ]
    _mock_conn(mocker, cursor)

    resultado = cancelar_venta(1, usuario_id=7)

    assert resultado["estado"] == "CANCELADO"
    movimientos = [c for c in cursor.execute.call_args_list if "INSERT INTO importacion_movimientos" in c.args[0]]
    assert movimientos[0].args[1][1] == "CANCELACION"
    assert movimientos[0].args[1][2] == 2  # positivo: restaura las 2 unidades
```

- [ ] **Step 4: Ejecutar los tests**

Run: `pytest tests/test_asignaciones_service.py -v`
Expected: 37 tests PASS (34 anteriores + 3 nuevos).

- [ ] **Step 5: Commit**

```bash
git add services/asignaciones_service.py routes/asignaciones_importaciones.py tests/test_asignaciones_service.py
git commit -m "feat: cancelar venta de sobrante y restaurar disponibilidad

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 11: Detalle de producto, resumen del embarque y movimientos (lectura)

**Files:**
- Modify: `services/asignaciones_service.py` (agregar `obtener_detalle_producto`, `resumen_embarque`, `listar_movimientos`)
- Modify: `routes/asignaciones_importaciones.py` (agregar 3 rutas `GET`)
- Modify: `tests/test_asignaciones_service.py`

**Interfaces:**
- Consumes: `listar_productos` (Task 4), `recalcular_propuesta` (Task 6)
- Produces: `obtener_detalle_producto(producto_id: int) -> dict` (`{producto, proyecciones, asignaciones, sobrantes_ventas}`), `resumen_embarque(importacion_id: int) -> dict` (`{embarque, kpis, productos}`), `listar_movimientos(importacion_id: int) -> list[dict]`

- [ ] **Step 1: Agregar las 3 funciones a `services/asignaciones_service.py`**

```python
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
```

- [ ] **Step 2: Agregar las 3 rutas `GET`**

```python
@asignaciones_bp.route("/<int:importacion_id>/asignaciones", methods=["GET"])
@token_required
@_requiere_rol_importaciones
def resumen_ruta(importacion_id):
    data = svc.resumen_embarque(importacion_id)
    return jsonify({"ok": True, "data": data}), 200


@asignaciones_bp.route(
    "/<int:importacion_id>/asignaciones/productos/<int:producto_id>/detalle", methods=["GET"]
)
@token_required
@_requiere_rol_importaciones
def detalle_producto_ruta(importacion_id, producto_id):
    data = svc.obtener_detalle_producto(producto_id)
    return jsonify({"ok": True, "data": data}), 200


@asignaciones_bp.route("/<int:importacion_id>/asignaciones/movimientos", methods=["GET"])
@token_required
@_requiere_rol_importaciones
def movimientos_ruta(importacion_id):
    data = svc.listar_movimientos(importacion_id)
    return jsonify({"ok": True, "data": data}), 200
```

- [ ] **Step 3: Tests de servicio**

Agregar a `tests/test_asignaciones_service.py`:

```python
from services.asignaciones_service import obtener_detalle_producto, resumen_embarque, listar_movimientos


def test_obtener_detalle_producto_no_existe(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        obtener_detalle_producto(999)
    assert exc.value.code == "PRODUCTO_NO_EXISTE"


def test_obtener_detalle_producto_incluye_los_3_bloques(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = {
        "id": 10, "importacion_id": 1, "periodo": "2026-2027", "sku_norm": "SKU1",
    }
    cursor.fetchall.side_effect = [
        [{"id": 1, "clave_cliente": "LC657", "cantidad_asignada": 3}],  # asignaciones
        [{"id": 1, "clave_cliente": "MC677", "cantidad": 1, "estado": "VALIDADO"}],  # ventas
    ]
    _mock_conn(mocker, cursor)
    mocker.patch(
        "services.asignaciones_service.recalcular_propuesta",
        return_value=[{"producto_id": 10, "propuesta": [{"clave_cliente": "LC657", "cantidad_sugerida": 3}]}],
    )

    detalle = obtener_detalle_producto(10)

    assert detalle["producto"]["id"] == 10
    assert len(detalle["asignaciones"]) == 1
    assert len(detalle["sobrantes_ventas"]) == 1
    assert detalle["proyecciones"][0]["clave_cliente"] == "LC657"


def test_resumen_embarque_no_existe(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        resumen_embarque(999)
    assert exc.value.code == "IMPORTACION_NO_EXISTE"


def test_resumen_embarque_suma_kpis_de_todos_los_productos(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = {"id": 1, "referencia": "IMP-001", "nombre": "Test", "estado": "activo"}
    _mock_conn(mocker, cursor)
    mocker.patch(
        "services.asignaciones_service.listar_productos",
        return_value=[
            {"cantidad_embarcada": 10, "cantidad_asignada": 6, "cantidad_sobrante": 4,
             "cantidad_vendida": 1, "cantidad_disponible": 3},
            {"cantidad_embarcada": 5, "cantidad_asignada": 5, "cantidad_sobrante": 0,
             "cantidad_vendida": 0, "cantidad_disponible": 0},
        ],
    )

    resumen = resumen_embarque(1)

    assert resumen["kpis"] == {
        "unidades_embarcadas": 15, "unidades_asignadas": 11, "unidades_sobrantes": 4,
        "unidades_vendidas": 1, "unidades_disponibles": 3,
    }


def test_listar_movimientos_importacion_no_existe(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        listar_movimientos(999)
    assert exc.value.code == "IMPORTACION_NO_EXISTE"


def test_listar_movimientos_devuelve_los_del_embarque(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = {"id": 1}
    cursor.fetchall.return_value = [{"id": 1, "tipo_movimiento": "ENTRADA", "cantidad": 10}]
    _mock_conn(mocker, cursor)

    resultado = listar_movimientos(1)

    assert len(resultado) == 1
    assert resultado[0]["tipo_movimiento"] == "ENTRADA"
```

- [ ] **Step 4: Ejecutar los tests**

Run: `pytest tests/test_asignaciones_service.py -v`
Expected: 43 tests PASS (37 anteriores + 6 nuevos).

- [ ] **Step 5: Prueba manual end-to-end de todo el flujo backend**

Con el backend local corriendo y un embarque de prueba con al menos un producto creado (Task 4), ejecutar en secuencia contra `http://127.0.0.1:5000`:
1. `POST /importaciones/<id>/asignaciones/productos/<pid>/asignar` con una asignación válida.
2. `GET /importaciones/<id>/asignaciones` — confirmar que `kpis.unidades_asignadas` refleja lo asignado.
3. `POST /importaciones/<id>/asignaciones/productos/<pid>/venta-sobrante` con el sobrante restante.
4. `GET /importaciones/<id>/asignaciones/movimientos` — confirmar que aparecen `ENTRADA`, `ASIGNACION` y `VENTA_SOBRANTE` en orden.

Esto confirma que las 13 rutas del blueprint (Tasks 3-11) trabajan juntas correctamente antes de pasar al frontend.

- [ ] **Step 6: Commit**

```bash
git add services/asignaciones_service.py routes/asignaciones_importaciones.py tests/test_asignaciones_service.py
git commit -m "feat: exponer detalle de producto, resumen del embarque y movimientos

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 12: Servicio Angular `AsignacionesImportacionService`

**Files:**
- Create: `src/app/services/asignaciones-importacion.service.ts` (en `EB_FRONT`)
- Test: `src/app/services/asignaciones-importacion.service.spec.ts`

**Interfaces:**
- Produces: interfaces `AsignacionesProducto`, `AsignacionesKpis`, `AsignacionesResumen`, `PropuestaCliente`, `PropuestaProducto`, `AsignacionRow`, `VentaSobrante`, `DetalleProducto`, `Movimiento`, `ClientePrioridad`, y la clase `AsignacionesImportacionService` con un método por endpoint del blueprint — usados por los componentes de las Tasks 13-14.

- [ ] **Step 1: Crear el archivo del servicio**

```typescript
import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { map } from 'rxjs/operators';
import { environment } from '../../environments/environment';

export interface AsignacionesProducto {
  id: number;
  importacion_id: number;
  periodo: string;
  sku: string;
  sku_norm: string;
  descripcion: string | null;
  cantidad_embarcada: number;
  cantidad_asignada: number;
  cantidad_vendida: number;
  cantidad_sobrante: number;
  cantidad_disponible: number;
  created_at: string;
  updated_at: string;
}

export interface AsignacionesKpis {
  unidades_embarcadas: number;
  unidades_asignadas: number;
  unidades_sobrantes: number;
  unidades_vendidas: number;
  unidades_disponibles: number;
}

export interface AsignacionesResumen {
  embarque: { id: number; referencia: string; nombre: string; estado: string };
  kpis: AsignacionesKpis;
  productos: AsignacionesProducto[];
}

export interface PropuestaCliente {
  clave_cliente: string;
  prioridad: number;
  cantidad_proyectada: number;
  cantidad_sugerida: number;
}

export interface PropuestaProducto {
  producto_id: number;
  sku: string;
  periodo: string;
  cantidad_embarcada: number;
  disponible: number;
  proyecciones_disponibles: boolean;
  propuesta: PropuestaCliente[];
  sobrante_estimado: number;
}

export interface AsignacionRow {
  id: number;
  importacion_producto_id: number;
  clave_cliente: string;
  cantidad_proyectada: number;
  cantidad_asignada: number;
  prioridad: number;
  estado: 'ACTIVA' | 'CANCELADA';
}

export interface VentaSobrante {
  id: number;
  importacion_producto_id: number;
  clave_cliente: string;
  cantidad: number;
  numero_pedido_odoo: string | null;
  estado: 'PENDIENTE_VALIDACION' | 'VALIDADO' | 'CANCELADO';
  created_at: string;
}

export interface DetalleProducto {
  producto: AsignacionesProducto;
  proyecciones: PropuestaCliente[];
  asignaciones: AsignacionRow[];
  sobrantes_ventas: VentaSobrante[];
}

export interface Movimiento {
  id: number;
  importacion_producto_id: number;
  tipo_movimiento: string;
  cantidad: number;
  clave_cliente: string | null;
  referencia_externa: string | null;
  created_at: string;
}

export interface ClientePrioridad {
  clave: string;
  nombre: string;
  prioridad: number;
}

interface ApiOk<T> { ok: true; data: T; }

@Injectable({ providedIn: 'root' })
export class AsignacionesImportacionService {
  private base = `${environment.apiUrl}/importaciones`;

  constructor(private http: HttpClient) {}

  resumen(importacionId: number): Observable<AsignacionesResumen> {
    return this.http.get<ApiOk<AsignacionesResumen>>(`${this.base}/${importacionId}/asignaciones`)
      .pipe(map(r => r.data));
  }

  listarProductos(importacionId: number): Observable<AsignacionesProducto[]> {
    return this.http.get<ApiOk<AsignacionesProducto[]>>(`${this.base}/${importacionId}/asignaciones/productos`)
      .pipe(map(r => r.data));
  }

  crearProducto(
    importacionId: number,
    body: { sku: string; cantidad_embarcada: number; periodo: string; descripcion?: string }
  ): Observable<AsignacionesProducto> {
    return this.http
      .post<ApiOk<AsignacionesProducto>>(`${this.base}/${importacionId}/asignaciones/productos`, body)
      .pipe(map(r => r.data));
  }

  actualizarProducto(
    importacionId: number,
    productoId: number,
    body: { cantidad_embarcada?: number; descripcion?: string }
  ): Observable<AsignacionesProducto> {
    return this.http
      .put<ApiOk<AsignacionesProducto>>(`${this.base}/${importacionId}/asignaciones/productos/${productoId}`, body)
      .pipe(map(r => r.data));
  }

  detalleProducto(importacionId: number, productoId: number): Observable<DetalleProducto> {
    return this.http
      .get<ApiOk<DetalleProducto>>(`${this.base}/${importacionId}/asignaciones/productos/${productoId}/detalle`)
      .pipe(map(r => r.data));
  }

  recalcular(importacionId: number, periodo?: string): Observable<PropuestaProducto[]> {
    return this.http
      .post<ApiOk<PropuestaProducto[]>>(`${this.base}/${importacionId}/asignaciones/recalcular`, { periodo })
      .pipe(map(r => r.data));
  }

  asignar(
    importacionId: number,
    productoId: number,
    asignaciones: { clave_cliente: string; cantidad: number }[]
  ): Observable<{ producto_id: number; disponible_restante: number }> {
    return this.http
      .post<ApiOk<{ producto_id: number; disponible_restante: number }>>(
        `${this.base}/${importacionId}/asignaciones/productos/${productoId}/asignar`, { asignaciones }
      )
      .pipe(map(r => r.data));
  }

  ventaSobrante(
    importacionId: number,
    productoId: number,
    body: { clave_cliente: string; cantidad: number; numero_pedido_odoo?: string }
  ): Observable<VentaSobrante> {
    return this.http
      .post<ApiOk<VentaSobrante>>(
        `${this.base}/${importacionId}/asignaciones/productos/${productoId}/venta-sobrante`, body
      )
      .pipe(map(r => r.data));
  }

  validarOdoo(importacionId: number, ventaId: number, numeroPedidoOdoo?: string): Observable<VentaSobrante> {
    return this.http
      .post<ApiOk<VentaSobrante>>(
        `${this.base}/${importacionId}/asignaciones/ventas/${ventaId}/validar-odoo`,
        { numero_pedido_odoo: numeroPedidoOdoo }
      )
      .pipe(map(r => r.data));
  }

  cancelarVenta(importacionId: number, ventaId: number): Observable<VentaSobrante> {
    return this.http
      .post<ApiOk<VentaSobrante>>(`${this.base}/${importacionId}/asignaciones/ventas/${ventaId}/cancelar`, {})
      .pipe(map(r => r.data));
  }

  movimientos(importacionId: number): Observable<Movimiento[]> {
    return this.http
      .get<ApiOk<Movimiento[]>>(`${this.base}/${importacionId}/asignaciones/movimientos`)
      .pipe(map(r => r.data));
  }

  prioridadClientes(): Observable<ClientePrioridad[]> {
    return this.http.get<ClientePrioridad[]>(`${environment.apiUrl}/clientes/prioridad`);
  }
}
```

- [ ] **Step 2: Escribir el spec con `HttpClientTestingModule`**

```typescript
import { TestBed } from '@angular/core/testing';
import { HttpClientTestingModule, HttpTestingController } from '@angular/common/http/testing';
import { environment } from '../../environments/environment';
import { AsignacionesImportacionService, AsignacionesResumen } from './asignaciones-importacion.service';

describe('AsignacionesImportacionService', () => {
  let service: AsignacionesImportacionService;
  let httpMock: HttpTestingController;
  const base = `${environment.apiUrl}/importaciones`;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [HttpClientTestingModule],
    });
    service = TestBed.inject(AsignacionesImportacionService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('resumen() hace GET y desenvuelve data', () => {
    const mockResumen: AsignacionesResumen = {
      embarque: { id: 1, referencia: 'IMP-001', nombre: 'Test', estado: 'activo' },
      kpis: {
        unidades_embarcadas: 10, unidades_asignadas: 5, unidades_sobrantes: 5,
        unidades_vendidas: 0, unidades_disponibles: 5,
      },
      productos: [],
    };

    service.resumen(1).subscribe(res => expect(res).toEqual(mockResumen));

    const req = httpMock.expectOne(`${base}/1/asignaciones`);
    expect(req.request.method).toBe('GET');
    req.flush({ ok: true, data: mockResumen });
  });

  it('crearProducto() hace POST con el body correcto', () => {
    const body = { sku: 'SKU-1', cantidad_embarcada: 10, periodo: '2026-2027' };

    service.crearProducto(1, body).subscribe();

    const req = httpMock.expectOne(`${base}/1/asignaciones/productos`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(body);
    req.flush({ ok: true, data: { id: 10, ...body } });
  });

  it('asignar() hace POST a la ruta de asignar del producto', () => {
    const asignaciones = [{ clave_cliente: 'LC657', cantidad: 3 }];

    service.asignar(1, 10, asignaciones).subscribe();

    const req = httpMock.expectOne(`${base}/1/asignaciones/productos/10/asignar`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ asignaciones });
    req.flush({ ok: true, data: { producto_id: 10, disponible_restante: 2 } });
  });

  it('prioridadClientes() hace GET a /clientes/prioridad (sin envolver en data)', () => {
    service.prioridadClientes().subscribe(res => expect(res.length).toBe(1));

    const req = httpMock.expectOne(`${environment.apiUrl}/clientes/prioridad`);
    expect(req.request.method).toBe('GET');
    req.flush([{ clave: 'LC657', nombre: 'Test', prioridad: 1 }]);
  });
});
```

- [ ] **Step 3: Ejecutar los tests**

Run: `ng test --watch=false --include='**/asignaciones-importacion.service.spec.ts'`
Expected: 5 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/app/services/asignaciones-importacion.service.ts src/app/services/asignaciones-importacion.service.spec.ts
git commit -m "feat: agregar servicio Angular de Asignaciones de Importaciones

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 13: Componente principal `AsignacionesImportacionComponent` (KPIs + tabla de productos + alta)

**Files:**
- Create: `src/app/views/internal-views/importaciones/asignaciones-importacion/asignaciones-importacion.component.ts`
- Create: `src/app/views/internal-views/importaciones/asignaciones-importacion/asignaciones-importacion.component.html`
- Create: `src/app/views/internal-views/importaciones/asignaciones-importacion/asignaciones-importacion.component.css`
- Test: `src/app/views/internal-views/importaciones/asignaciones-importacion/asignaciones-importacion.component.spec.ts`

**Interfaces:**
- Consumes: `AsignacionesImportacionService` (Task 12) — `resumen()`, `crearProducto()`
- Produces: propiedad pública `productoSeleccionado: AsignacionesProducto | null` — consumida por `<app-asignaciones-detalle-producto>` en Task 14 vía `[producto]` / `(cerrar)` / `(cambio)`.

- [ ] **Step 1: Crear el componente TypeScript**

```typescript
import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router, RouterModule } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { HomeBarComponent } from '../../../../components/home-bar/home-bar.component';
import {
  AsignacionesImportacionService,
  AsignacionesResumen,
  AsignacionesProducto,
} from '../../../../services/asignaciones-importacion.service';

@Component({
  selector: 'app-asignaciones-importacion',
  standalone: true,
  imports: [CommonModule, RouterModule, FormsModule, HomeBarComponent],
  templateUrl: './asignaciones-importacion.component.html',
  styleUrl: './asignaciones-importacion.component.css',
})
export class AsignacionesImportacionComponent implements OnInit {
  importacionId!: number;
  resumen: AsignacionesResumen | null = null;
  cargando = true;
  error = '';

  mostrarFormNuevo = false;
  guardandoProducto = false;
  errorProducto = '';
  nuevoProducto = { sku: '', cantidad_embarcada: null as number | null, periodo: '', descripcion: '' };

  productoSeleccionado: AsignacionesProducto | null = null;

  constructor(
    private route: ActivatedRoute,
    private router: Router,
    private svc: AsignacionesImportacionService,
  ) {}

  ngOnInit(): void {
    this.importacionId = Number(this.route.snapshot.paramMap.get('id'));
    this.cargar();
  }

  cargar(): void {
    this.cargando = true;
    this.error = '';
    this.svc.resumen(this.importacionId).subscribe({
      next: (data) => { this.resumen = data; this.cargando = false; },
      error: (err) => {
        this.error = err?.error?.error?.message || 'No se pudo cargar el embarque';
        this.cargando = false;
      },
    });
  }

  volver(): void {
    this.router.navigate(['/importaciones', this.importacionId]);
  }

  toggleFormNuevo(): void {
    this.mostrarFormNuevo = !this.mostrarFormNuevo;
    this.errorProducto = '';
  }

  agregarProducto(): void {
    if (!this.nuevoProducto.sku.trim() || !this.nuevoProducto.periodo.trim() ||
        this.nuevoProducto.cantidad_embarcada === null || this.nuevoProducto.cantidad_embarcada < 0) {
      this.errorProducto = 'SKU, periodo y cantidad embarcada (>= 0) son obligatorios';
      return;
    }
    this.guardandoProducto = true;
    this.errorProducto = '';
    this.svc.crearProducto(this.importacionId, {
      sku: this.nuevoProducto.sku.trim(),
      cantidad_embarcada: this.nuevoProducto.cantidad_embarcada,
      periodo: this.nuevoProducto.periodo.trim(),
      descripcion: this.nuevoProducto.descripcion.trim() || undefined,
    }).subscribe({
      next: () => {
        this.guardandoProducto = false;
        this.nuevoProducto = { sku: '', cantidad_embarcada: null, periodo: '', descripcion: '' };
        this.mostrarFormNuevo = false;
        this.cargar();
      },
      error: (err) => {
        this.guardandoProducto = false;
        this.errorProducto = err?.error?.error?.message || 'No se pudo registrar el producto';
      },
    });
  }

  abrirDetalle(producto: AsignacionesProducto): void {
    this.productoSeleccionado = producto;
  }

  cerrarDetalle(): void {
    this.productoSeleccionado = null;
  }

  onCambioEnDetalle(): void {
    this.cargar();
  }
}
```

- [ ] **Step 2: Crear la plantilla HTML**

```html
<app-home-bar></app-home-bar>

<div class="asig-container" *ngIf="!cargando; else cargandoTpl">
  <div class="asig-error" *ngIf="error">{{ error }}</div>

  <ng-container *ngIf="resumen">
    <div class="asig-header">
      <button class="back-btn" (click)="volver()"><i class="fas fa-arrow-left"></i></button>
      <div>
        <span class="asig-ref">{{ resumen.embarque.referencia }}</span>
        <h1>{{ resumen.embarque.nombre }} — Asignaciones</h1>
      </div>
    </div>

    <div class="kpi-row">
      <div class="kpi-card">
        <span class="kpi-num">{{ resumen.kpis.unidades_embarcadas }}</span>
        <span class="kpi-label">Embarcadas</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-num">{{ resumen.kpis.unidades_asignadas }}</span>
        <span class="kpi-label">Asignadas</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-num">{{ resumen.kpis.unidades_sobrantes }}</span>
        <span class="kpi-label">Sobrantes</span>
      </div>
      <div class="kpi-card">
        <span class="kpi-num">{{ resumen.kpis.unidades_vendidas }}</span>
        <span class="kpi-label">Vendidas (sobrante)</span>
      </div>
      <div class="kpi-card kpi-highlight">
        <span class="kpi-num">{{ resumen.kpis.unidades_disponibles }}</span>
        <span class="kpi-label">Disponibles</span>
      </div>
    </div>

    <div class="asig-toolbar">
      <h2>Productos del embarque</h2>
      <button class="btn-primary" (click)="toggleFormNuevo()">
        {{ mostrarFormNuevo ? 'Cancelar' : '+ Agregar producto' }}
      </button>
    </div>

    <div class="form-nuevo" *ngIf="mostrarFormNuevo">
      <input type="text" placeholder="SKU" [(ngModel)]="nuevoProducto.sku" name="sku" />
      <input type="number" placeholder="Cantidad embarcada" [(ngModel)]="nuevoProducto.cantidad_embarcada" name="cantidad" min="0" />
      <input type="text" placeholder="Periodo (ej. 2026-2027)" [(ngModel)]="nuevoProducto.periodo" name="periodo" />
      <input type="text" placeholder="Descripción (opcional)" [(ngModel)]="nuevoProducto.descripcion" name="descripcion" />
      <button class="btn-primary" [disabled]="guardandoProducto" (click)="agregarProducto()">Guardar</button>
      <div class="form-error" *ngIf="errorProducto">{{ errorProducto }}</div>
    </div>

    <table class="asig-tabla" *ngIf="resumen.productos.length; else sinProductos">
      <thead>
        <tr>
          <th>SKU</th><th>Periodo</th><th>Embarcado</th><th>Asignado</th>
          <th>Sobrante</th><th>Vendido</th><th>Disponible</th><th></th>
        </tr>
      </thead>
      <tbody>
        <tr *ngFor="let p of resumen.productos" (click)="abrirDetalle(p)" class="fila-clickable">
          <td>{{ p.sku }}</td>
          <td>{{ p.periodo }}</td>
          <td>{{ p.cantidad_embarcada }}</td>
          <td>{{ p.cantidad_asignada }}</td>
          <td>{{ p.cantidad_sobrante }}</td>
          <td>{{ p.cantidad_vendida }}</td>
          <td class="celda-disponible">{{ p.cantidad_disponible }}</td>
          <td><i class="fas fa-chevron-right"></i></td>
        </tr>
      </tbody>
    </table>
    <ng-template #sinProductos>
      <p class="asig-vacio">Este embarque todavía no tiene productos registrados.</p>
    </ng-template>
  </ng-container>
</div>

<ng-template #cargandoTpl>
  <div class="asig-cargando">Cargando...</div>
</ng-template>

<app-asignaciones-detalle-producto
  *ngIf="productoSeleccionado"
  [importacionId]="importacionId"
  [producto]="productoSeleccionado"
  (cerrar)="cerrarDetalle()"
  (cambio)="onCambioEnDetalle()"
></app-asignaciones-detalle-producto>
```

- [ ] **Step 3: Crear el CSS (paleta azul del dashboard)**

```css
.asig-container { max-width: 1200px; margin: 0 auto; padding: 24px; }
.asig-error { background: #fee2e2; color: #b91c1c; padding: 12px 16px; border-radius: 8px; margin-bottom: 16px; }
.asig-cargando { text-align: center; padding: 60px; color: #64748b; }

.asig-header { display: flex; align-items: center; gap: 16px; margin-bottom: 24px; }
.back-btn { background: none; border: 1px solid #cbd5e1; border-radius: 8px; width: 40px; height: 40px; cursor: pointer; }
.asig-ref { font-size: 13px; font-weight: 600; color: #3b82f6; text-transform: uppercase; letter-spacing: .5px; }
.asig-header h1 { margin: 4px 0 0; font-size: 22px; color: #0f172a; }

.kpi-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 28px; }
.kpi-card { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 16px; display: flex; flex-direction: column; gap: 4px; }
.kpi-highlight { background: #eff6ff; border-color: #93c5fd; }
.kpi-num { font-size: 24px; font-weight: 700; color: #0f172a; }
.kpi-label { font-size: 12px; color: #64748b; }

.asig-toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.btn-primary { background: #3b82f6; color: #fff; border: none; border-radius: 8px; padding: 8px 16px; cursor: pointer; font-weight: 600; }
.btn-primary:disabled { opacity: .6; cursor: not-allowed; }

.form-nuevo { display: flex; flex-wrap: wrap; gap: 8px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 16px; margin-bottom: 16px; }
.form-nuevo input { flex: 1; min-width: 140px; padding: 8px 10px; border: 1px solid #cbd5e1; border-radius: 6px; }
.form-error { color: #b91c1c; font-size: 13px; width: 100%; }

.asig-tabla { width: 100%; border-collapse: collapse; }
.asig-tabla th { text-align: left; font-size: 12px; color: #64748b; text-transform: uppercase; padding: 10px; border-bottom: 2px solid #e2e8f0; }
.asig-tabla td { padding: 12px 10px; border-bottom: 1px solid #f1f5f9; }
.fila-clickable { cursor: pointer; }
.fila-clickable:hover { background: #f8fafc; }
.celda-disponible { font-weight: 700; color: #3b82f6; }
.asig-vacio { color: #64748b; text-align: center; padding: 40px; }
```

- [ ] **Step 4: Escribir el spec del componente**

```typescript
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { HttpClientTestingModule, HttpTestingController } from '@angular/common/http/testing';
import { ActivatedRoute } from '@angular/router';
import { of } from 'rxjs';
import { AsignacionesImportacionComponent } from './asignaciones-importacion.component';
import { AsignacionesImportacionService, AsignacionesResumen } from '../../../../services/asignaciones-importacion.service';

describe('AsignacionesImportacionComponent', () => {
  let fixture: ComponentFixture<AsignacionesImportacionComponent>;
  let component: AsignacionesImportacionComponent;
  let svcSpy: jasmine.SpyObj<AsignacionesImportacionService>;

  const resumenMock: AsignacionesResumen = {
    embarque: { id: 1, referencia: 'IMP-001', nombre: 'Test', estado: 'activo' },
    kpis: { unidades_embarcadas: 10, unidades_asignadas: 4, unidades_sobrantes: 6, unidades_vendidas: 0, unidades_disponibles: 6 },
    productos: [],
  };

  beforeEach(async () => {
    svcSpy = jasmine.createSpyObj('AsignacionesImportacionService', ['resumen', 'crearProducto']);
    svcSpy.resumen.and.returnValue(of(resumenMock));

    await TestBed.configureTestingModule({
      imports: [AsignacionesImportacionComponent, HttpClientTestingModule],
      providers: [
        { provide: AsignacionesImportacionService, useValue: svcSpy },
        { provide: ActivatedRoute, useValue: { snapshot: { paramMap: { get: () => '1' } } } },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(AsignacionesImportacionComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should be created', () => {
    expect(component).toBeTruthy();
  });

  it('carga el resumen al iniciar', () => {
    expect(svcSpy.resumen).toHaveBeenCalledWith(1);
    expect(component.resumen).toEqual(resumenMock);
    expect(component.cargando).toBeFalse();
  });

  it('agregarProducto() valida campos obligatorios antes de llamar al servicio', () => {
    component.nuevoProducto = { sku: '', cantidad_embarcada: null, periodo: '', descripcion: '' };
    component.agregarProducto();
    expect(component.errorProducto).toContain('obligatorios');
    expect(svcSpy.crearProducto).not.toHaveBeenCalled();
  });

  it('abrirDetalle() setea productoSeleccionado', () => {
    const producto = { id: 5 } as any;
    component.abrirDetalle(producto);
    expect(component.productoSeleccionado).toBe(producto);
  });

  it('cerrarDetalle() limpia productoSeleccionado', () => {
    component.productoSeleccionado = { id: 5 } as any;
    component.cerrarDetalle();
    expect(component.productoSeleccionado).toBeNull();
  });
});
```

**Nota:** este spec referencia `<app-asignaciones-detalle-producto>` en la plantilla (Task 14, todavía no existe en este punto del plan). Angular con `standalone: true` y `imports` explícitos en el propio componente de detalle no falla al compilar el padre aunque el hijo no esté declarado aquí — pero como el padre usa el selector directamente en su HTML, hay que importar `AsignacionesDetalleProductoComponent` en el array `imports` del padre. Como ese componente se crea recién en la Task 14, dejar este import agregado como parte del Step 1 de la Task 14 (no aquí) — en este punto, comentar temporalmente la etiqueta en el HTML no es necesario porque Angular solo resuelve el selector si el import está declarado; sin el import, la etiqueta se ignora en tiempo de compilación de plantilla standalone con `NO_ERRORS_SCHEMA` implícito... **para evitar ese riesgo, ejecutar el test de este Step 4 recién al terminar la Task 14** (donde el import ya existe). Este orden se explicita en el Step 5.

- [ ] **Step 5: Ejecutar los tests (después de completar la Task 14)**

Run: `ng test --watch=false --include='**/asignaciones-importacion.component.spec.ts'`
Expected: 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/app/views/internal-views/importaciones/asignaciones-importacion/
git commit -m "feat: componente principal de Asignaciones de Importaciones (KPIs, tabla, alta de producto)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 14: Componente de detalle de producto (recalcular, asignar, sobrantes/ventas, Odoo, movimientos)

**Files:**
- Create: `src/app/views/internal-views/importaciones/asignaciones-importacion/asignaciones-detalle-producto/asignaciones-detalle-producto.component.ts`
- Create: `.../asignaciones-detalle-producto/asignaciones-detalle-producto.component.html`
- Create: `.../asignaciones-detalle-producto/asignaciones-detalle-producto.component.css`
- Modify: `.../asignaciones-importacion/asignaciones-importacion.component.ts` (agregar el import al array `imports`)
- Test: `.../asignaciones-detalle-producto/asignaciones-detalle-producto.component.spec.ts`

**Interfaces:**
- Consumes: `AsignacionesImportacionService.detalleProducto/recalcular/asignar/ventaSobrante/validarOdoo/cancelarVenta/movimientos` (Task 12), `AsignacionesProducto` (Task 12)
- Produces: `@Input() importacionId: number`, `@Input() producto: AsignacionesProducto`, `@Output() cerrar: EventEmitter<void>`, `@Output() cambio: EventEmitter<void>` — consumidos por `AsignacionesImportacionComponent` (Task 13).

- [ ] **Step 1: Crear el componente TypeScript**

```typescript
import { Component, EventEmitter, Input, OnChanges, Output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import {
  AsignacionesImportacionService,
  AsignacionesProducto,
  DetalleProducto,
  PropuestaProducto,
  Movimiento,
  VentaSobrante,
} from '../../../../../services/asignaciones-importacion.service';

type Tab = 'proyecciones' | 'asignaciones' | 'sobrantes' | 'movimientos';

@Component({
  selector: 'app-asignaciones-detalle-producto',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './asignaciones-detalle-producto.component.html',
  styleUrl: './asignaciones-detalle-producto.component.css',
})
export class AsignacionesDetalleProductoComponent implements OnChanges {
  @Input() importacionId!: number;
  @Input() producto!: AsignacionesProducto;
  @Output() cerrar = new EventEmitter<void>();
  @Output() cambio = new EventEmitter<void>();

  tab: Tab = 'proyecciones';
  cargando = true;
  detalle: DetalleProducto | null = null;

  propuesta: PropuestaProducto | null = null;
  recalculando = false;
  formAsignacion: { clave_cliente: string; cantidad: number }[] = [];
  guardandoAsignacion = false;
  errorAsignacion = '';

  nuevaVenta = { clave_cliente: '', cantidad: null as number | null, numero_pedido_odoo: '' };
  guardandoVenta = false;
  errorVenta = '';
  validandoVentaId: number | null = null;
  folioParaValidar = '';

  movimientos: Movimiento[] = [];
  cargandoMovimientos = false;

  constructor(private svc: AsignacionesImportacionService) {}

  ngOnChanges(): void {
    this.tab = 'proyecciones';
    this.propuesta = null;
    this.cargarDetalle();
  }

  cargarDetalle(): void {
    this.cargando = true;
    this.svc.detalleProducto(this.importacionId, this.producto.id).subscribe({
      next: (data) => { this.detalle = data; this.cargando = false; },
      error: () => { this.cargando = false; },
    });
  }

  cambiarTab(tab: Tab): void {
    this.tab = tab;
    if (tab === 'movimientos' && this.movimientos.length === 0) {
      this.cargarMovimientos();
    }
  }

  recalcular(): void {
    this.recalculando = true;
    this.svc.recalcular(this.importacionId, this.producto.periodo).subscribe({
      next: (propuestas) => {
        this.recalculando = false;
        this.propuesta = propuestas.find((p) => p.producto_id === this.producto.id) || null;
        this.formAsignacion = (this.propuesta?.propuesta || []).map((c) => ({
          clave_cliente: c.clave_cliente,
          cantidad: c.cantidad_sugerida,
        }));
      },
      error: () => { this.recalculando = false; },
    });
  }

  agregarFilaManual(): void {
    this.formAsignacion.push({ clave_cliente: '', cantidad: 0 });
  }

  quitarFila(i: number): void {
    this.formAsignacion.splice(i, 1);
  }

  confirmarAsignacion(): void {
    const asignaciones = this.formAsignacion.filter((f) => f.clave_cliente.trim() && f.cantidad > 0);
    if (!asignaciones.length) {
      this.errorAsignacion = 'Agrega al menos una asignación con cantidad > 0';
      return;
    }
    this.guardandoAsignacion = true;
    this.errorAsignacion = '';
    this.svc.asignar(this.importacionId, this.producto.id, asignaciones).subscribe({
      next: () => {
        this.guardandoAsignacion = false;
        this.formAsignacion = [];
        this.propuesta = null;
        this.cargarDetalle();
        this.cambio.emit();
      },
      error: (err) => {
        this.guardandoAsignacion = false;
        this.errorAsignacion = err?.error?.error?.message || 'No se pudo confirmar la asignación';
      },
    });
  }

  registrarVenta(): void {
    if (!this.nuevaVenta.clave_cliente.trim() || !this.nuevaVenta.cantidad || this.nuevaVenta.cantidad <= 0) {
      this.errorVenta = 'Cliente y cantidad (> 0) son obligatorios';
      return;
    }
    this.guardandoVenta = true;
    this.errorVenta = '';
    this.svc.ventaSobrante(this.importacionId, this.producto.id, {
      clave_cliente: this.nuevaVenta.clave_cliente.trim(),
      cantidad: this.nuevaVenta.cantidad,
      numero_pedido_odoo: this.nuevaVenta.numero_pedido_odoo.trim() || undefined,
    }).subscribe({
      next: () => {
        this.guardandoVenta = false;
        this.nuevaVenta = { clave_cliente: '', cantidad: null, numero_pedido_odoo: '' };
        this.cargarDetalle();
        this.cambio.emit();
      },
      error: (err) => {
        this.guardandoVenta = false;
        this.errorVenta = err?.error?.error?.message || 'No se pudo registrar la venta';
      },
    });
  }

  iniciarValidacion(venta: VentaSobrante): void {
    this.validandoVentaId = venta.id;
    this.folioParaValidar = venta.numero_pedido_odoo || '';
  }

  cancelarValidacion(): void {
    this.validandoVentaId = null;
    this.folioParaValidar = '';
  }

  confirmarValidacionOdoo(venta: VentaSobrante): void {
    if (!this.folioParaValidar.trim()) {
      this.errorVenta = 'Ingresa el número de pedido de Odoo';
      return;
    }
    this.errorVenta = '';
    this.svc.validarOdoo(this.importacionId, venta.id, this.folioParaValidar.trim()).subscribe({
      next: () => {
        this.validandoVentaId = null;
        this.cargarDetalle();
        this.cambio.emit();
      },
      error: (err) => {
        this.errorVenta = err?.error?.error?.message || 'No se pudo validar el pedido en Odoo';
      },
    });
  }

  cancelarVentaSobrante(venta: VentaSobrante): void {
    if (!confirm(`¿Cancelar la venta de ${venta.cantidad} unidades a ${venta.clave_cliente}?`)) {
      return;
    }
    this.svc.cancelarVenta(this.importacionId, venta.id).subscribe({
      next: () => { this.cargarDetalle(); this.cambio.emit(); },
    });
  }

  cargarMovimientos(): void {
    this.cargandoMovimientos = true;
    this.svc.movimientos(this.importacionId).subscribe({
      next: (data) => {
        this.movimientos = data.filter((m) => m.importacion_producto_id === this.producto.id);
        this.cargandoMovimientos = false;
      },
      error: () => { this.cargandoMovimientos = false; },
    });
  }

  cerrarPanel(): void {
    this.cerrar.emit();
  }
}
```

- [ ] **Step 2: Agregar el import al componente padre (Task 13)**

En `asignaciones-importacion.component.ts`, agregar al import y al array `imports` del decorador:

```typescript
import { AsignacionesDetalleProductoComponent } from './asignaciones-detalle-producto/asignaciones-detalle-producto.component';
```

```typescript
  imports: [CommonModule, RouterModule, FormsModule, HomeBarComponent, AsignacionesDetalleProductoComponent],
```

- [ ] **Step 3: Crear la plantilla HTML**

```html
<div class="panel-overlay" (click)="cerrarPanel()">
  <div class="panel" (click)="$event.stopPropagation()">
    <div class="panel-header">
      <div>
        <span class="panel-sku">{{ producto.sku }}</span>
        <span class="panel-periodo">{{ producto.periodo }}</span>
      </div>
      <button class="panel-close" (click)="cerrarPanel()"><i class="fas fa-times"></i></button>
    </div>

    <div class="panel-kpis">
      <span>Embarcado: <b>{{ producto.cantidad_embarcada }}</b></span>
      <span>Asignado: <b>{{ producto.cantidad_asignada }}</b></span>
      <span>Sobrante: <b>{{ producto.cantidad_sobrante }}</b></span>
      <span>Disponible: <b class="disponible">{{ producto.cantidad_disponible }}</b></span>
    </div>

    <div class="panel-tabs">
      <button [class.activo]="tab === 'proyecciones'" (click)="cambiarTab('proyecciones')">Proyecciones</button>
      <button [class.activo]="tab === 'asignaciones'" (click)="cambiarTab('asignaciones')">Asignaciones</button>
      <button [class.activo]="tab === 'sobrantes'" (click)="cambiarTab('sobrantes')">Sobrantes / Ventas</button>
      <button [class.activo]="tab === 'movimientos'" (click)="cambiarTab('movimientos')">Historial</button>
    </div>

    <div class="panel-body" *ngIf="!cargando; else cargandoTpl">

      <!-- ── Proyecciones / propuesta de reparto ── -->
      <div *ngIf="tab === 'proyecciones'">
        <button class="btn-secundario" [disabled]="recalculando" (click)="recalcular()">
          {{ recalculando ? 'Calculando...' : 'Recalcular propuesta' }}
        </button>
        <p class="hint" *ngIf="propuesta && !propuesta.proyecciones_disponibles">
          Proyecciones no disponible en este momento — se muestra solo lo embarcado.
        </p>

        <table class="tabla-mini" *ngIf="formAsignacion.length">
          <thead><tr><th>Cliente</th><th>Prioridad</th><th>Proyectado</th><th>A asignar</th><th></th></tr></thead>
          <tbody>
            <tr *ngFor="let fila of formAsignacion; let i = index">
              <td><input type="text" [(ngModel)]="fila.clave_cliente" [name]="'cliente' + i" /></td>
              <td>
                {{ (propuesta?.propuesta || []).find(c => c.clave_cliente === fila.clave_cliente)?.prioridad ?? '—' }}
              </td>
              <td>
                {{ (propuesta?.propuesta || []).find(c => c.clave_cliente === fila.clave_cliente)?.cantidad_proyectada ?? '—' }}
              </td>
              <td><input type="number" min="0" [(ngModel)]="fila.cantidad" [name]="'cantidad' + i" /></td>
              <td><button class="btn-quitar" (click)="quitarFila(i)">✕</button></td>
            </tr>
          </tbody>
        </table>

        <button class="btn-texto" (click)="agregarFilaManual()">+ agregar cliente manualmente</button>

        <div class="form-error" *ngIf="errorAsignacion">{{ errorAsignacion }}</div>
        <button class="btn-primary" [disabled]="guardandoAsignacion || !formAsignacion.length" (click)="confirmarAsignacion()">
          Confirmar asignación
        </button>
      </div>

      <!-- ── Asignaciones ya confirmadas ── -->
      <div *ngIf="tab === 'asignaciones'">
        <table class="tabla-mini" *ngIf="detalle?.asignaciones?.length; else sinDatos">
          <thead><tr><th>Cliente</th><th>Prioridad</th><th>Asignado</th><th>Estado</th></tr></thead>
          <tbody>
            <tr *ngFor="let a of detalle!.asignaciones">
              <td>{{ a.clave_cliente }}</td>
              <td>{{ a.prioridad }}</td>
              <td>{{ a.cantidad_asignada }}</td>
              <td><span class="badge" [class.badge-cancelada]="a.estado === 'CANCELADA'">{{ a.estado }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- ── Sobrantes / ventas anticipadas ── -->
      <div *ngIf="tab === 'sobrantes'">
        <div class="form-nuevo">
          <input type="text" placeholder="Cliente" [(ngModel)]="nuevaVenta.clave_cliente" name="ventaCliente" />
          <input type="number" placeholder="Cantidad" min="1" [(ngModel)]="nuevaVenta.cantidad" name="ventaCantidad" />
          <input type="text" placeholder="Pedido Odoo (opcional)" [(ngModel)]="nuevaVenta.numero_pedido_odoo" name="ventaFolio" />
          <button class="btn-primary" [disabled]="guardandoVenta" (click)="registrarVenta()">Registrar venta</button>
        </div>
        <div class="form-error" *ngIf="errorVenta">{{ errorVenta }}</div>

        <table class="tabla-mini" *ngIf="detalle?.sobrantes_ventas?.length; else sinDatos">
          <thead><tr><th>Cliente</th><th>Cantidad</th><th>Pedido Odoo</th><th>Estado</th><th></th></tr></thead>
          <tbody>
            <tr *ngFor="let v of detalle!.sobrantes_ventas">
              <td>{{ v.clave_cliente }}</td>
              <td>{{ v.cantidad }}</td>
              <td>{{ v.numero_pedido_odoo || '—' }}</td>
              <td><span class="badge" [ngClass]="'badge-' + v.estado.toLowerCase()">{{ v.estado }}</span></td>
              <td class="acciones-venta">
                <ng-container *ngIf="v.estado !== 'CANCELADO' && v.estado !== 'VALIDADO'">
                  <ng-container *ngIf="validandoVentaId === v.id; else botonValidar">
                    <input type="text" placeholder="Folio Odoo" [(ngModel)]="folioParaValidar" [name]="'folio' + v.id" />
                    <button class="btn-texto" (click)="confirmarValidacionOdoo(v)">Validar</button>
                    <button class="btn-texto" (click)="cancelarValidacion()">✕</button>
                  </ng-container>
                  <ng-template #botonValidar>
                    <button class="btn-texto" (click)="iniciarValidacion(v)">Validar en Odoo</button>
                  </ng-template>
                  <button class="btn-texto btn-danger" (click)="cancelarVentaSobrante(v)">Cancelar</button>
                </ng-container>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- ── Historial de movimientos ── -->
      <div *ngIf="tab === 'movimientos'">
        <div *ngIf="cargandoMovimientos">Cargando historial...</div>
        <table class="tabla-mini" *ngIf="!cargandoMovimientos && movimientos.length; else sinDatos">
          <thead><tr><th>Fecha</th><th>Tipo</th><th>Cantidad</th><th>Cliente</th><th>Referencia</th></tr></thead>
          <tbody>
            <tr *ngFor="let m of movimientos">
              <td>{{ m.created_at | date: 'short' }}</td>
              <td>{{ m.tipo_movimiento }}</td>
              <td [class.positivo]="m.cantidad > 0" [class.negativo]="m.cantidad < 0">{{ m.cantidad }}</td>
              <td>{{ m.clave_cliente || '—' }}</td>
              <td>{{ m.referencia_externa || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <ng-template #sinDatos><p class="asig-vacio">Sin registros todavía.</p></ng-template>
    </div>

    <ng-template #cargandoTpl><div class="asig-cargando">Cargando...</div></ng-template>
  </div>
</div>
```

- [ ] **Step 4: Crear el CSS**

```css
.panel-overlay { position: fixed; inset: 0; background: rgba(15, 23, 42, .45); display: flex; justify-content: flex-end; z-index: 1000; }
.panel { width: min(720px, 100%); background: #fff; height: 100%; overflow-y: auto; padding: 24px; box-shadow: -4px 0 24px rgba(0,0,0,.15); }
.panel-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.panel-sku { font-size: 18px; font-weight: 700; color: #0f172a; margin-right: 10px; }
.panel-periodo { font-size: 13px; color: #64748b; }
.panel-close { background: none; border: none; font-size: 18px; cursor: pointer; color: #64748b; }

.panel-kpis { display: flex; gap: 20px; flex-wrap: wrap; background: #f8fafc; border-radius: 8px; padding: 12px 16px; margin-bottom: 16px; font-size: 13px; color: #475569; }
.panel-kpis .disponible { color: #3b82f6; }

.panel-tabs { display: flex; gap: 4px; border-bottom: 1px solid #e2e8f0; margin-bottom: 16px; }
.panel-tabs button { background: none; border: none; padding: 8px 12px; cursor: pointer; font-size: 13px; color: #64748b; border-bottom: 2px solid transparent; }
.panel-tabs button.activo { color: #3b82f6; border-bottom-color: #3b82f6; font-weight: 600; }

.tabla-mini { width: 100%; border-collapse: collapse; margin-bottom: 12px; }
.tabla-mini th { text-align: left; font-size: 11px; color: #94a3b8; text-transform: uppercase; padding: 6px 8px; }
.tabla-mini td { padding: 8px; border-bottom: 1px solid #f1f5f9; font-size: 13px; }
.tabla-mini input { width: 100%; padding: 4px 6px; border: 1px solid #cbd5e1; border-radius: 4px; }

.btn-primary { background: #3b82f6; color: #fff; border: none; border-radius: 8px; padding: 8px 16px; cursor: pointer; font-weight: 600; }
.btn-primary:disabled { opacity: .6; cursor: not-allowed; }
.btn-secundario { background: #eff6ff; color: #3b82f6; border: 1px solid #93c5fd; border-radius: 8px; padding: 6px 12px; cursor: pointer; margin-bottom: 12px; }
.btn-texto { background: none; border: none; color: #3b82f6; cursor: pointer; font-size: 12px; padding: 4px 6px; }
.btn-danger { color: #dc2626; }
.btn-quitar { background: none; border: none; color: #94a3b8; cursor: pointer; }

.form-nuevo { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.form-nuevo input { flex: 1; min-width: 120px; padding: 6px 8px; border: 1px solid #cbd5e1; border-radius: 6px; }
.form-error { color: #b91c1c; font-size: 12px; margin: 4px 0; }
.hint { font-size: 12px; color: #b45309; background: #fffbeb; padding: 6px 10px; border-radius: 6px; }

.badge { font-size: 11px; padding: 2px 8px; border-radius: 999px; background: #e2e8f0; color: #475569; }
.badge-validado { background: #dcfce7; color: #15803d; }
.badge-cancelado, .badge-cancelada { background: #fee2e2; color: #b91c1c; }
.badge-pendiente_validacion { background: #fef3c7; color: #b45309; }

.acciones-venta { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.acciones-venta input { width: 100px; padding: 3px 6px; border: 1px solid #cbd5e1; border-radius: 4px; }

.positivo { color: #15803d; }
.negativo { color: #b91c1c; }
.asig-vacio { color: #94a3b8; text-align: center; padding: 24px; }
.asig-cargando { text-align: center; padding: 40px; color: #64748b; }
```

- [ ] **Step 5: Escribir el spec del componente**

```typescript
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { AsignacionesDetalleProductoComponent } from './asignaciones-detalle-producto.component';
import { AsignacionesImportacionService, DetalleProducto, AsignacionesProducto } from '../../../../../services/asignaciones-importacion.service';

describe('AsignacionesDetalleProductoComponent', () => {
  let fixture: ComponentFixture<AsignacionesDetalleProductoComponent>;
  let component: AsignacionesDetalleProductoComponent;
  let svcSpy: jasmine.SpyObj<AsignacionesImportacionService>;

  const producto: AsignacionesProducto = {
    id: 10, importacion_id: 1, periodo: '2026-2027', sku: 'SKU-1', sku_norm: 'SKU1',
    descripcion: null, cantidad_embarcada: 10, cantidad_asignada: 3, cantidad_vendida: 0,
    cantidad_sobrante: 7, cantidad_disponible: 7, created_at: '', updated_at: '',
  };

  const detalleMock: DetalleProducto = {
    producto, proyecciones: [], asignaciones: [], sobrantes_ventas: [],
  };

  beforeEach(async () => {
    svcSpy = jasmine.createSpyObj('AsignacionesImportacionService', [
      'detalleProducto', 'recalcular', 'asignar', 'ventaSobrante', 'validarOdoo', 'cancelarVenta', 'movimientos',
    ]);
    svcSpy.detalleProducto.and.returnValue(of(detalleMock));

    await TestBed.configureTestingModule({
      imports: [AsignacionesDetalleProductoComponent],
      providers: [{ provide: AsignacionesImportacionService, useValue: svcSpy }],
    }).compileComponents();

    fixture = TestBed.createComponent(AsignacionesDetalleProductoComponent);
    component = fixture.componentInstance;
    component.importacionId = 1;
    component.producto = producto;
    fixture.detectChanges();
  });

  it('should be created', () => {
    expect(component).toBeTruthy();
  });

  it('carga el detalle al recibir el producto', () => {
    expect(svcSpy.detalleProducto).toHaveBeenCalledWith(1, 10);
    expect(component.detalle).toEqual(detalleMock);
  });

  it('recalcular() precarga formAsignacion con la propuesta sugerida', () => {
    svcSpy.recalcular.and.returnValue(of([
      {
        producto_id: 10, sku: 'SKU-1', periodo: '2026-2027', cantidad_embarcada: 10, disponible: 7,
        proyecciones_disponibles: true, sobrante_estimado: 2,
        propuesta: [{ clave_cliente: 'LC657', prioridad: 1, cantidad_proyectada: 5, cantidad_sugerida: 5 }],
      },
    ]));

    component.recalcular();

    expect(component.formAsignacion).toEqual([{ clave_cliente: 'LC657', cantidad: 5 }]);
  });

  it('confirmarAsignacion() no llama al servicio si no hay filas válidas', () => {
    component.formAsignacion = [{ clave_cliente: '', cantidad: 0 }];
    component.confirmarAsignacion();
    expect(component.errorAsignacion).toContain('al menos una asignación');
    expect(svcSpy.asignar).not.toHaveBeenCalled();
  });

  it('registrarVenta() valida cliente y cantidad antes de llamar al servicio', () => {
    component.nuevaVenta = { clave_cliente: '', cantidad: null, numero_pedido_odoo: '' };
    component.registrarVenta();
    expect(component.errorVenta).toContain('obligatorios');
    expect(svcSpy.ventaSobrante).not.toHaveBeenCalled();
  });

  it('cerrarPanel() emite el evento cerrar', () => {
    spyOn(component.cerrar, 'emit');
    component.cerrarPanel();
    expect(component.cerrar.emit).toHaveBeenCalled();
  });
});
```

- [ ] **Step 6: Ejecutar los tests (componente de detalle + el padre pendiente de la Task 13)**

Run: `ng test --watch=false --include='**/asignaciones-detalle-producto.component.spec.ts' --include='**/asignaciones-importacion.component.spec.ts'`
Expected: 6 + 5 = 11 tests PASS (con el import agregado en el Step 2, el spec del padre de la Task 13 ya puede resolver `<app-asignaciones-detalle-producto>`).

- [ ] **Step 7: Commit**

```bash
git add "src/app/views/internal-views/importaciones/asignaciones-importacion/"
git commit -m "feat: panel de detalle de producto — recalcular, asignar, sobrantes, Odoo, historial

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 15: Ruta `/importaciones/:id/asignaciones` + botón "Asignaciones" en el detalle del embarque

**Files:**
- Modify: `src/app/app.routes.ts`
- Modify: `src/app/views/internal-views/importaciones/importaciones-detalle/importaciones-detalle.component.html:40-42`

**Interfaces:**
- Consumes: `AsignacionesImportacionComponent` (Task 13), `importacionesGuard` (guard ya existente, reutilizado tal cual)

- [ ] **Step 1: Agregar la ruta en `app.routes.ts`**

Junto a los imports de componentes de Importaciones (cerca de la línea 53-55):

```typescript
import { AsignacionesImportacionComponent } from './views/internal-views/importaciones/asignaciones-importacion/asignaciones-importacion.component';
```

Junto a las rutas existentes de `importaciones` (cerca de la línea 136-138), agregar una ruta nueva **antes** de `'importaciones/:id'` (Angular hace match en orden — una ruta con un segmento fijo `/asignaciones` después del `:id` no choca con `importaciones/:id` porque tiene un segmento extra, pero se agrega explícita para claridad):

```typescript
  { path: 'importaciones/:id/asignaciones', component: AsignacionesImportacionComponent, canActivate: [importacionesGuard] },
```

- [ ] **Step 2: Agregar el botón en el header del detalle del embarque**

En `importaciones-detalle.component.html`, dentro de `det-header-right` (línea 41-42), agregar el botón **antes** del `<!-- Progreso global -->`:

```html
      <div class="det-header-right">
        <a class="btn-asignaciones" [routerLink]="['/importaciones', embarque.id, 'asignaciones']">
          <i class="fas fa-people-arrows"></i> Asignaciones
        </a>
        <!-- Progreso global -->
```

- [ ] **Step 3: Agregar el estilo del botón nuevo**

En `importaciones-detalle.component.css`, agregar:

```css
.btn-asignaciones {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: #eff6ff;
  color: #3b82f6;
  border: 1px solid #93c5fd;
  border-radius: 8px;
  padding: 8px 14px;
  font-size: 13px;
  font-weight: 600;
  text-decoration: none;
  margin-right: 12px;
}
.btn-asignaciones:hover { background: #dbeafe; }
```

- [ ] **Step 4: Verificación manual en el navegador**

Con `ng serve` corriendo:
1. Entrar a `/importaciones`, abrir cualquier embarque.
2. Confirmar que el botón "Asignaciones" aparece en el header y navega a `/importaciones/<id>/asignaciones`.
3. En esa vista, agregar un producto, recalcular, asignar, registrar una venta de sobrante y revisar el historial — confirmar que todo el flujo funciona visualmente de punta a punta.
4. Confirmar que el guard bloquea el acceso a un usuario con rol distinto de 1 o 3 (redirige igual que ya lo hace para `/importaciones`).

- [ ] **Step 5: Commit**

```bash
git add src/app/app.routes.ts src/app/views/internal-views/importaciones/importaciones-detalle/importaciones-detalle.component.html src/app/views/internal-views/importaciones/importaciones-detalle/importaciones-detalle.component.css
git commit -m "feat: enlazar Asignaciones desde el detalle del embarque

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 16: Casos de prueba restantes (concurrencia real, fallback de prioridad, SKU con/sin guiones) + revisión final

**Files:**
- Modify: `tests/test_asignaciones_service.py` (agregar los casos que faltan de los 12 del spec)
- Modify: `tests/test_asignaciones_routes.py` (agregar el test de concurrencia real contra la BD local)

**Interfaces:** ninguna nueva — esta tarea solo agrega cobertura de prueba sobre lo ya construido en las Tasks 1-15.

De los 12 casos del spec (sección 8), los casos 4, 5, 6, 7, 9 ya quedaron cubiertos en las Tasks 7, 8, 9, 10. Esta tarea cubre los que faltan: 1, 2, 3 (números exactos de los ejemplos del brief), 8 (concurrencia real), 11 (SKU con/sin guiones bloquea duplicado) y 12 (cliente sin prioridad).

- [ ] **Step 1: Casos 1, 2 y 3 — números exactos de los ejemplos del brief**

Agregar a `tests/test_asignaciones_service.py`:

```python
def test_caso_10_embarcadas_8_proyectadas_deja_2_sobrantes(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [{"id": 1}]
    cursor.fetchall.return_value = [
        {"id": 10, "sku": "SKU-1", "sku_norm": "SKU1", "periodo": "2026-2027",
         "cantidad_embarcada": 10, "importacion_id": 1},
    ]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=10)
    mocker.patch(
        "services.asignaciones_service.demanda_neta_por_cliente",
        return_value={"SKU1": {"LC657": 3, "MC677": 2, "HE420": 1, "JC539": 2}},  # total 8
    )

    propuestas = recalcular_propuesta(1)

    assert sum(c["cantidad_sugerida"] for c in propuestas[0]["propuesta"]) == 8
    assert propuestas[0]["sobrante_estimado"] == 2


def test_caso_10_embarcadas_15_proyectadas_asigna_10_deja_0_sobrante_5_faltan(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [{"id": 1}]
    cursor.fetchall.return_value = [
        {"id": 10, "sku": "SKU-1", "sku_norm": "SKU1", "periodo": "2026-2027",
         "cantidad_embarcada": 10, "importacion_id": 1},
    ]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=10)
    mocker.patch(
        "services.asignaciones_service.demanda_neta_por_cliente",
        return_value={"SKU1": {"LC657": 8, "MC677": 7}},  # total 15 > 10 embarcado
    )

    propuestas = recalcular_propuesta(1)

    total_asignado = sum(c["cantidad_sugerida"] for c in propuestas[0]["propuesta"])
    assert total_asignado == 10  # nunca más de lo embarcado
    assert propuestas[0]["sobrante_estimado"] == 0
    faltante = sum(c["cantidad_proyectada"] for c in propuestas[0]["propuesta"]) - total_asignado
    assert faltante == 5  # 15 proyectado - 10 asignado


def test_caso_10_embarcadas_10_proyectadas_deja_0_sobrante(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [{"id": 1}]
    cursor.fetchall.return_value = [
        {"id": 10, "sku": "SKU-1", "sku_norm": "SKU1", "periodo": "2026-2027",
         "cantidad_embarcada": 10, "importacion_id": 1},
    ]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=10)
    mocker.patch(
        "services.asignaciones_service.demanda_neta_por_cliente",
        return_value={"SKU1": {"LC657": 10}},
    )

    propuestas = recalcular_propuesta(1)

    assert propuestas[0]["sobrante_estimado"] == 0
```

- [ ] **Step 2: Caso 12 — cliente sin prioridad usa fallback 999 y orden alfabético**

Agregar a `tests/test_asignaciones_service.py`:

```python
def test_caso_cliente_sin_prioridad_usa_fallback_999_y_orden_alfabetico(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [{"id": 1}]
    cursor.fetchall.return_value = [
        {"id": 10, "sku": "SKU-1", "sku_norm": "SKU1", "periodo": "2026-2027",
         "cantidad_embarcada": 100, "importacion_id": 1},
    ]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=100)
    mocker.patch(
        "services.asignaciones_service.demanda_neta_por_cliente",
        return_value={"SKU1": {
            "ZZ999": 5,    # no está en PRIORIDAD_CLIENTES -> 999
            "AA111": 5,    # tampoco está -> 999, pero alfabéticamente antes que ZZ999
            "LC657": 5,    # prioridad real 1
        }},
    )

    propuestas = recalcular_propuesta(1)

    orden = [c["clave_cliente"] for c in propuestas[0]["propuesta"]]
    assert orden == ["LC657", "AA111", "ZZ999"]  # prioridad real primero, luego alfabético entre los 999
    assert next(c["prioridad"] for c in propuestas[0]["propuesta"] if c["clave_cliente"] == "AA111") == 999
```

- [ ] **Step 3: Caso 11 — SKU con y sin guiones se tratan como el mismo producto**

Agregar a `tests/test_asignaciones_service.py`:

```python
def test_caso_sku_con_y_sin_guiones_se_detecta_como_duplicado(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 1},                                # importacion existe
        {"id": 5, "sku_norm": "4271020001004"},   # YA existe el mismo sku_norm (se cargó antes con guiones)
    ]
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        crear_producto(1, "4271020001004", 10, "2026-2027")  # ahora sin guiones
    assert exc.value.code == "SKU_DUPLICADO"
```

- [ ] **Step 4: Ejecutar todos los tests de servicio**

Run: `pytest tests/test_asignaciones_service.py tests/test_proyecciones_service.py -v`
Expected: todos PASS (47 + 5 nuevos = 52 en `test_asignaciones_service.py`, más los de `test_proyecciones_service.py`).

- [ ] **Step 5: Caso 8 — concurrencia real con hilos contra la BD local**

Agregar a `tests/test_asignaciones_routes.py` (usa 2 hilos reales golpeando `asignar()` sobre el mismo producto con solo 2 unidades disponibles, uno pide 2 y el otro pide 1 — el disponible nunca debe quedar negativo y solo una de las dos peticiones puede completarse con éxito total):

```python
def test_asignar_concurrente_nunca_deja_disponible_negativo():
    conn = obtener_conexion()
    if not conn:
        import pytest
        pytest.skip("Sin conexión a BD local para este test")

    from services.asignaciones_service import crear_producto, asignar, AsignacionesError, _disponible_producto

    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id FROM importaciones LIMIT 1")
    embarque = cursor.fetchone()
    cursor.execute("SELECT clave FROM clientes LIMIT 2")
    clientes = cursor.fetchall()
    conn.close()
    if not embarque or len(clientes) < 2:
        import pytest
        pytest.skip("Se necesita al menos 1 embarque y 2 clientes en la BD local para este test")

    producto = crear_producto(embarque["id"], "TEST-CONCURRENCIA-0001", 2, "2026-2027")

    import threading
    resultados = []
    errores = []

    def intentar(clave, cantidad):
        try:
            resultados.append(asignar(producto["id"], [{"clave_cliente": clave, "cantidad": cantidad}]))
        except AsignacionesError as e:
            errores.append(e)

    t1 = threading.Thread(target=intentar, args=(clientes[0]["clave"], 2))
    t2 = threading.Thread(target=intentar, args=(clientes[1]["clave"], 1))
    t1.start(); t2.start()
    t1.join(); t2.join()

    conn = obtener_conexion()
    cursor = conn.cursor(dictionary=True)
    disponible_final = _disponible_producto(cursor, producto["id"])
    conn.close()

    assert disponible_final >= 0  # la garantía central: nunca queda negativo
    assert len(resultados) + len(errores) == 2
    if len(resultados) == 2:
        # ambos cupieron exactamente (2+1 > 2 disponible, así que esto NO debería pasar,
        # pero si el bloqueo fallara, este assert lo detectaría)
        assert False, "Ambas asignaciones se completaron pese a que la demanda (3) excede el disponible (2)"
    assert len(errores) >= 1  # al menos una de las dos debe haber sido rechazada por STOCK_INSUFICIENTE
    assert all(e.code == "STOCK_INSUFICIENTE" for e in errores)
```

Run: `pytest tests/test_asignaciones_routes.py::test_asignar_concurrente_nunca_deja_disponible_negativo -v`
Expected: PASS (o SKIPPED si no hay BD local/clientes — nunca FAIL).

- [ ] **Step 6: Commit de los tests**

```bash
git add tests/test_asignaciones_service.py tests/test_asignaciones_routes.py
git commit -m "test: cubrir concurrencia real, fallback de prioridad y SKU con/sin guiones

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

- [ ] **Step 7: Checklist final de revisión (manual, contra la sección 8 del brief original)**

Repasar uno por uno, sin código nuevo salvo que se encuentre un problema real:

- [ ] ¿Hay lógica duplicada? → No: prioridad, `_norm_sku` y deducción Odoo viven solo en `services/proyecciones_service.py` (Task 1); Angular consume `/clientes/prioridad` en vez de tener su propia copia.
- [ ] ¿Hay SQL directo desde Angular? → No, todo pasa por `AsignacionesImportacionService` → backend.
- [ ] ¿Hay validaciones solo en frontend? → No: cantidades, SKU, cliente, stock, sobrante y pedido Odoo se validan todos en `services/asignaciones_service.py`; el frontend solo valida campos vacíos para dar feedback inmediato, nunca como única barrera.
- [ ] ¿Puede quedar stock negativo? → No: `asignar` y `crear_venta_sobrante` bloquean la fila del producto con `FOR UPDATE` antes de comparar contra el disponible (Task 16 Step 5 lo verifica con hilos reales).
- [ ] ¿Hay operaciones no transaccionales? → No: toda mutación de cantidades hace `commit`/`rollback` explícito dentro de un único `try/except`.
- [ ] ¿Puede duplicarse una venta? → No: `UNIQUE(numero_pedido_odoo)` + manejo de `IntegrityError` (Task 8).
- [ ] ¿Puede romperse Importaciones? → No: Asignaciones nunca escribe en `importaciones`; solo lee `id/referencia/nombre/estado`.
- [ ] ¿Puede romperse Proyecciones? → No: Asignaciones nunca escribe en `forecast_proyecciones`; si Proyecciones falla, `recalcular` degrada (`proyecciones_disponibles: false`) sin bloquear el registro de mercancía (Task 6).
- [ ] ¿Existe historial? → Sí: `importacion_movimientos` es insert-only y cubre `ENTRADA/ASIGNACION/VENTA_SOBRANTE/CANCELACION/AJUSTE`.
- [ ] ¿Los SKU coinciden correctamente? → Sí: `_norm_sku` se aplica consistentemente al capturar, al comparar contra `forecast_proyecciones` y al comparar contra `product.product.default_code` de Odoo.
- [ ] ¿La prioridad tiene una sola fuente? → Sí: `PRIORIDAD_CLIENTES`/`_PRIORIDAD_MAP` viven solo en `services/proyecciones_service.py`, expuestas vía `GET /clientes/prioridad`.

Si algún punto falla en la revisión manual, documentar el hallazgo y decidir con el usuario si amerita una tarea de corrección adicional antes de dar el submódulo por terminado.

---

