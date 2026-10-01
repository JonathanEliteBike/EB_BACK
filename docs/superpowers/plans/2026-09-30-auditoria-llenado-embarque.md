# Auditoría de Llenado del Embarque Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an "Auditoría" section to the import monitor that compares the date each tracked milestone field was *actually* filled in against the date it was *expected* to be filled in, showing the delta in days (green = early, red = late).

**Architecture:** A new general-purpose `importaciones_historial_campos` table records the first time any tracked column on `importaciones` transitions from empty to filled, hooked into the existing `PUT /importaciones/<id>` save path. A new configurable `importaciones_hitos_auditoria` table (admin CRUD, same pattern as `importaciones_tiempos_estimados`) defines the milestones. A calculation function resolves each milestone's expected date against its anchor (embarque creation date, another milestone's expected date, or a non-tracked "trigger" field's real captured date) and compares it to the real capture date from the history table. Backend exposes this as `GET /importaciones/<id>/auditoria`; frontend adds a read-only tab plus an admin screen for the milestone config.

**Tech Stack:** Flask blueprint (`routes/importaciones.py`), MySQL (`db_conexion.py`), pytest + pytest-mock, Angular 19 standalone components, Jasmine.

**Spec:** `docs/superpowers/specs/2026-09-30-auditoria-llenado-embarque-design.md`

---

## Resumen de cierre (2026-10-01)

**Estado: las 8 tareas del plan original están completas** (backend en la rama `jonathan`, frontend en `feature/auditoria-llenado-embarque`, ambas subidas a GitHub). Después de la implementación inicial, el usuario probó la pantalla en vivo y pidió trece rondas más de ajustes — todas documentadas con su commit en el ledger de la ejecución (`EB_BACK_jonathan/.superpowers/sdd/2026-09-30-auditoria-llenado-embarque/progress.md`). Resumen de lo que se agregó más allá del plan original:

1. **Resumen cruzado de todos los embarques** (`/importaciones/auditoria`) — no estaba en el plan original; se agregó porque un supervisor necesitaba ver el estado de todos los embarques de un vistazo, no entrar uno por uno.
2. **Rediseño visual completo del resumen** siguiendo el patrón de tarjetas del dashboard existente: franja de hitos por embarque con Proyectado/Real y delta en días, en el orden exacto en que se capturan (no agrupado por sección).
3. **Estado "sin dato histórico"** — distingue un campo que ya tenía valor antes de existir esta auditoría (no se puede saber cuándo se llenó) de uno genuinamente pendiente, evitando que el sistema "mienta" sobre embarques anteriores a la fecha de lanzamiento.
4. **Dos bugs reales encontrados y corregidos durante las pruebas**: manejo de `datetime` vs `date` de MySQL que producía fechas inválidas, y cálculo de "hoy" en UTC en vez de hora local (afectaba el panorama cerca de medianoche).
5. **Clic en un hito lleva al campo de origen** en el detalle del embarque, para validar o corregir el dato directamente.
6. **Buscador y filtros** (referencia/nombre, estado, sección, rango de fechas) en el resumen cruzado.
7. **Consistencia visual del módulo**: selectores de fecha pasados de naranja a azul en todo Importaciones, fondo homogéneo con el dashboard en todas las pantallas nuevas.
8. **Pantalla de administración de Hitos** rediseñada (antes era texto plano sin tarjetas) y hecha accesible desde la UI (antes solo se llegaba escribiendo la URL a mano).
9. **Panorama general agregado**: latencia total y promedio de atraso/adelanto por hito y sección, entre todos los embarques dados de alta el mismo día — colapsable para no alargar la página.

**Pendiente al momento de este documento:** revisión final de código (en curso) antes de mergear `jonathan` → `main` (backend) y `feature/auditoria-llenado-embarque` → `main` (frontend) y desplegar a los servidores de producción.

---

## Global Constraints

- No backfill: fields already filled before this ships have no historical capture date — the audit shows "Sin dato histórico" for those, never an approximated date (spec §2).
- Milestones are configuration data, not hardcoded — stored in `importaciones_hitos_auditoria`, editable from a new admin screen, same CRUD pattern as `importaciones_tiempos_estimados` (`routes/importaciones.py:695-798`).
- Expected dates use a **fixed schedule**: a milestone anchored on another milestone uses the anchor's *expected* date, not its real date — a late anchor does not push its dependents' deadlines later (spec §2, §4).
- An anchor that is not itself one of the configured milestones (e.g. "Fecha de Entrega Real") has no expected date of its own — it is a trigger: dependents' expected dates are computed from its *real* captured date once available; until then the dependent is "pendiente" (spec §2, §4).
- Only the first capture of a field is recorded in history — corrections to an already-filled field do not overwrite `capturado_en` (spec §3.1).
- This blueprint (`routes/importaciones.py`) has no JWT/auth decorators on any existing route (verified: `tiempos-estimados` CRUD has none) — the new routes follow the same convention, no auth added.

## Review Focus

- **Circular or missing milestone references.** `campo_ancla` on a milestone points to a `campo_dato` that does not exist among active milestones and is not a tracked field either (typo in admin form, or milestone deleted while another still anchors on it) — the calc engine must not crash, and must surface that milestone as unresolved rather than silently mis-computing.
- **Two active milestones sharing the same `campo_dato`.** The config screen does not currently enforce uniqueness — if it happens, the calc engine must pick one deterministically (lowest `id`) rather than erroring or double-counting.
- **A field edited a second time after its first real value.** `_recalcular_campos()` already runs on every save and can rewrite computed fields (e.g. the `_prog` fields) — the history hook must only fire on transition from falsy to truthy, never on a value that changes from one non-empty value to another, or the "primera captura" date would silently drift on every later correction.
- **`__NA__` marks.** A field set to `"__NA__"` is stored as `NULL` in the real column (`routes/importaciones.py:1563-1568`) — that must not be recorded as a "real capture" in the history table, since the column ends up NULL just like an unfilled field.
- **An embarque with zero configured milestones, or a milestone whose anchor field was never captured.** `GET /importaciones/<id>/auditoria` must return an empty/pending list, never a 500 — this is the normal state for an embarque's very first months in the new flow.

---

## Task 1: Schema — historial de captura + config de hitos

**Files:**
- Modify: `routes/importaciones.py:655-657` (inside `inicializar_tablas()`, right before the final `return jsonify(...)`)
- Test: `tests/test_importaciones_auditoria.py` (new)

**Interfaces:**
- Produces: tables `importaciones_historial_campos` and `importaciones_hitos_auditoria`, created idempotently by `POST /importaciones/inicializar-tablas` (same endpoint that already creates `importaciones` and `importaciones_tiempos_estimados`).

- [x] **Step 1: Write the failing test**

```python
# tests/test_importaciones_auditoria.py
"""Tests de la auditoria de llenado del embarque: tablas nuevas, historial
de captura por campo, y el motor de calculo _calcular_auditoria()."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from flask import Flask

from routes.importaciones import importaciones_bp


def _cliente_test():
    app = Flask(__name__)
    app.register_blueprint(importaciones_bp)
    return app.test_client()


def test_inicializar_tablas_crea_historial_y_hitos(mocker):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    mocker.patch("routes.importaciones.obtener_conexion", return_value=conn)

    resp = _cliente_test().post("/importaciones/inicializar-tablas")

    assert resp.status_code == 201
    sql_ejecutados = " ".join(
        c.args[0] for c in cursor.execute.call_args_list if c.args and isinstance(c.args[0], str)
    )
    assert "CREATE TABLE IF NOT EXISTS importaciones_historial_campos" in sql_ejecutados
    assert "CREATE TABLE IF NOT EXISTS importaciones_hitos_auditoria" in sql_ejecutados
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_importaciones_auditoria.py::test_inicializar_tablas_crea_historial_y_hitos -v`
Expected: FAIL — `importaciones_historial_campos` not found in the SQL the mock recorded, because the table creation code does not exist yet.

- [x] **Step 3: Add the two CREATE TABLE statements**

In `routes/importaciones.py`, immediately before `return jsonify({"ok": True, "mensaje": "Tabla importaciones creada/verificada"}), 201` (currently line 657), add:

```python
        # Historial de primera captura por campo -- alimenta la auditoria de
        # llenado del embarque. Tabla de proposito general: sirve para estos
        # hitos y para cualquier auditoria futura ("quien capturo que y
        # cuando"), no se acopla a los hitos configurados.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS importaciones_historial_campos (
                id               INT AUTO_INCREMENT PRIMARY KEY,
                importacion_id   INT NOT NULL,
                campo            VARCHAR(100) NOT NULL,
                valor_anterior   TEXT,
                valor_nuevo      TEXT,
                capturado_en     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                usuario_id       INT,
                INDEX idx_importacion_campo (importacion_id, campo),
                FOREIGN KEY (importacion_id) REFERENCES importaciones(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)
        conn.commit()

        # Hitos configurables de la auditoria (seccion -> campo esperado en N
        # dias desde un ancla). Editable desde /importaciones/hitos-auditoria,
        # mismo patron que importaciones_tiempos_estimados.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS importaciones_hitos_auditoria (
                id               INT AUTO_INCREMENT PRIMARY KEY,
                seccion          VARCHAR(30) NOT NULL,
                orden_hito       INT NOT NULL,
                etiqueta         VARCHAR(150) NOT NULL,
                campo_dato       VARCHAR(100) NOT NULL,
                campo_ancla      VARCHAR(100),
                dias_esperados   INT NOT NULL DEFAULT 0,
                activo           TINYINT(1) NOT NULL DEFAULT 1,
                created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)
        conn.commit()

```

- [x] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_importaciones_auditoria.py::test_inicializar_tablas_crea_historial_y_hitos -v`
Expected: PASS

- [x] **Step 5: Run it against the real local database to verify the DDL is valid**

Run: `curl -X POST http://127.0.0.1:5000/importaciones/inicializar-tablas` (with the local Flask dev server running)
Expected: `{"ok": true, "mensaje": "Tabla importaciones creada/verificada"}` and no MySQL error in the server log. Confirm with `DESCRIBE importaciones_historial_campos;` and `DESCRIBE importaciones_hitos_auditoria;` in a MySQL client that both tables exist with the expected columns.

- [x] **Step 6: Commit**

```bash
git add routes/importaciones.py tests/test_importaciones_auditoria.py
git commit -m "feat(importaciones): esquema de auditoria de llenado (historial de captura + hitos configurables)"
```

---

## Task 2: Historial de captura — hook en el guardado oficial

**Files:**
- Modify: `routes/importaciones.py` (new helper function near `_serialize`, hook inside `actualizar()` around line 1595-1596)
- Test: `tests/test_importaciones_auditoria.py`

**Interfaces:**
- Consumes: `_COLS_PERMITIDAS` (existing set, `routes/importaciones.py:170-183`), `existing` dict (row before update), `merged` dict (row after `_recalcular_campos`), `campos_a_actualizar` (existing list).
- Produces: `_registrar_primera_captura(cursor, importacion_id, existing, merged, campos_a_actualizar)` — called from `actualizar()`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_importaciones_auditoria.py (append)
from datetime import date
from routes.importaciones import _registrar_primera_captura


def _cursor_mock():
    cursor = MagicMock()
    return cursor


def test_registrar_primera_captura_inserta_solo_campos_que_pasan_de_vacio_a_lleno():
    cursor = _cursor_mock()
    existing = {"log_contenedor": None, "log_buque": "Ya tenia valor"}
    merged   = {"log_contenedor": "MSKU1234567", "log_buque": "Buque Nuevo"}

    _registrar_primera_captura(cursor, importacion_id=42,
                                existing=existing, merged=merged,
                                campos_a_actualizar=["log_contenedor", "log_buque"])

    inserts = [c for c in cursor.execute.call_args_list
               if "INSERT INTO importaciones_historial_campos" in c.args[0]]
    assert len(inserts) == 1  # log_buque YA tenia valor -> no se registra, es una correccion
    assert inserts[0].args[1][1] == "log_contenedor"
    assert inserts[0].args[1][3] == "MSKU1234567"


def test_registrar_primera_captura_ignora_marcas_na():
    cursor = _cursor_mock()
    existing = {"log_contenedor": None}
    merged   = {"log_contenedor": None}  # __NA__ se guarda como NULL en la columna real

    _registrar_primera_captura(cursor, importacion_id=42,
                                existing=existing, merged=merged,
                                campos_a_actualizar=["log_contenedor"])

    inserts = [c for c in cursor.execute.call_args_list
               if "INSERT INTO importaciones_historial_campos" in c.args[0]]
    assert len(inserts) == 0


def test_registrar_primera_captura_ignora_campos_fuera_de_cols_permitidas():
    cursor = _cursor_mock()
    existing = {"campo_inventado": None}
    merged   = {"campo_inventado": "x"}

    _registrar_primera_captura(cursor, importacion_id=42,
                                existing=existing, merged=merged,
                                campos_a_actualizar=["campo_inventado"])

    assert cursor.execute.call_count == 0
```

- [x] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_importaciones_auditoria.py -k registrar_primera_captura -v`
Expected: FAIL with `ImportError: cannot import name '_registrar_primera_captura'`

- [x] **Step 3: Write the helper function**

Add this function in `routes/importaciones.py`, right after `_serialize` (after line 33):

```python
def _registrar_primera_captura(cursor, importacion_id: int, existing: dict, merged: dict,
                                campos_a_actualizar: list) -> None:
    """Inserta en importaciones_historial_campos la PRIMERA vez que cada
    campo rastreado pasa de vacio a tener valor. No se llama dentro de una
    transaccion propia -- corre en la misma conexion/commit que el UPDATE
    principal de actualizar().

    Solo registra columnas en _COLS_PERMITIDAS (los campos capturables del
    formulario); un valor vacio se define como None, cadena vacia o
    '__NA__'. Si el campo YA tenia un valor real antes de este guardado, es
    una correccion, no una primera captura -- no se toca el historial."""
    for campo in campos_a_actualizar:
        if campo not in _COLS_PERMITIDAS:
            continue
        valor_antes = existing.get(campo)
        tenia_valor = valor_antes not in (None, "", "__NA__")
        if tenia_valor:
            continue
        valor_despues = merged.get(campo)
        tiene_valor_ahora = valor_despues not in (None, "", "__NA__")
        if not tiene_valor_ahora:
            continue
        cursor.execute(
            "INSERT INTO importaciones_historial_campos "
            "(importacion_id, campo, valor_anterior, valor_nuevo) VALUES (%s, %s, %s, %s)",
            (importacion_id, campo, str(valor_antes) if valor_antes is not None else None,
             str(valor_despues)),
        )
```

- [x] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_importaciones_auditoria.py -k registrar_primera_captura -v`
Expected: PASS (3 tests)

- [x] **Step 5: Wire the hook into `actualizar()`**

In `routes/importaciones.py`, inside `actualizar()`, right after this existing block (currently lines 1594-1596):

```python
        vals.append(id_imp)
        cursor.execute(f"UPDATE importaciones SET {set_clause} WHERE id = %s", vals)
        conn.commit()
```

add immediately below:

```python
        _registrar_primera_captura(cursor, id_imp, existing, merged, campos_a_actualizar)
        conn.commit()
```

- [x] **Step 6: Write the end-to-end test against the real local database**

```python
# tests/test_importaciones_auditoria.py (append)
from db_conexion import obtener_conexion


def _crear_embarque_de_prueba():
    conn = obtener_conexion()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO importaciones (referencia, via_transporte) VALUES (%s, 'MARITIMO')",
        (f"TEST-AUDIT-{__import__('time').time()}",),
    )
    conn.commit()
    id_imp = cursor.lastrowid
    conn.close()
    return id_imp


def test_put_importacion_registra_historial_en_bd_real():
    id_imp = _crear_embarque_de_prueba()
    client = _cliente_test()

    # Primera captura de log_contenedor -> debe quedar en el historial.
    resp = client.put(f"/importaciones/{id_imp}", json={"log_contenedor": "MSKU9999999"})
    assert resp.status_code == 200

    conn = obtener_conexion()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT * FROM importaciones_historial_campos WHERE importacion_id = %s AND campo = %s",
        (id_imp, "log_contenedor"),
    )
    filas = cursor.fetchall()
    assert len(filas) == 1
    assert filas[0]["valor_nuevo"] == "MSKU9999999"

    # Segunda escritura del MISMO campo (correccion) -> NO debe duplicar la fila.
    resp2 = client.put(f"/importaciones/{id_imp}", json={"log_contenedor": "MSKU8888888"})
    assert resp2.status_code == 200
    cursor.execute(
        "SELECT COUNT(*) AS c FROM importaciones_historial_campos WHERE importacion_id = %s AND campo = %s",
        (id_imp, "log_contenedor"),
    )
    assert cursor.fetchone()["c"] == 1  # sigue siendo 1, no 2

    cursor.execute("DELETE FROM importaciones WHERE id = %s", (id_imp,))
    conn.commit()
    conn.close()
```

Run: `pytest tests/test_importaciones_auditoria.py::test_put_importacion_registra_historial_en_bd_real -v` (requires local MySQL with `inicializar-tablas` already run once)
Expected: PASS

- [x] **Step 7: Run the full test file and the existing importaciones tests to confirm nothing else broke**

Run: `pytest tests/test_importaciones_auditoria.py tests/test_importaciones_tiempos_estimados.py -v`
Expected: all PASS

- [x] **Step 8: Commit**

```bash
git add routes/importaciones.py tests/test_importaciones_auditoria.py
git commit -m "feat(importaciones): registrar primera captura de cada campo en importaciones_historial_campos"
```

---

## Task 3: CRUD de configuración de hitos

**Files:**
- Modify: `routes/importaciones.py` (new routes, after the `tiempos-estimados` DELETE route, i.e. after current line ~798)
- Test: `tests/test_importaciones_auditoria.py`

**Interfaces:**
- Produces: `GET /importaciones/hitos-auditoria`, `POST /importaciones/hitos-auditoria`, `PUT /importaciones/hitos-auditoria/<id>`, `DELETE /importaciones/hitos-auditoria/<id>`.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_importaciones_auditoria.py (append)

def test_listar_hitos_auditoria_devuelve_json_del_select(mocker):
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        {"id": 1, "seccion": "logistica", "orden_hito": 1, "etiqueta": "1. Importador",
         "campo_dato": "odoo_importador", "campo_ancla": None, "dias_esperados": 0, "activo": 1},
    ]
    conn.cursor.return_value = cursor
    mocker.patch("routes.importaciones.obtener_conexion", return_value=conn)

    resp = _cliente_test().get("/importaciones/hitos-auditoria")

    assert resp.status_code == 200
    assert resp.get_json()[0]["campo_dato"] == "odoo_importador"


def test_crear_hito_auditoria_rechaza_campo_dato_vacio(mocker):
    conn = MagicMock()
    mocker.patch("routes.importaciones.obtener_conexion", return_value=conn)

    resp = _cliente_test().post("/importaciones/hitos-auditoria", json={
        "seccion": "logistica", "orden_hito": 1, "etiqueta": "x",
        "campo_dato": "", "dias_esperados": 5,
    })

    assert resp.status_code == 400


def test_crear_hito_auditoria_ok(mocker):
    conn = MagicMock()
    cursor = MagicMock()
    cursor.lastrowid = 7
    conn.cursor.return_value = cursor
    mocker.patch("routes.importaciones.obtener_conexion", return_value=conn)

    resp = _cliente_test().post("/importaciones/hitos-auditoria", json={
        "seccion": "logistica", "orden_hito": 3, "etiqueta": "18. Contenedores",
        "campo_dato": "log_contenedor", "campo_ancla": "log_fecha_entrega", "dias_esperados": 1,
    })

    assert resp.status_code == 201
    assert resp.get_json()["id"] == 7


def test_actualizar_hito_auditoria_404_si_no_existe(mocker):
    conn = MagicMock()
    cursor = MagicMock()
    cursor.rowcount = 0
    conn.cursor.return_value = cursor
    mocker.patch("routes.importaciones.obtener_conexion", return_value=conn)

    resp = _cliente_test().put("/importaciones/hitos-auditoria/999", json={
        "seccion": "logistica", "orden_hito": 1, "etiqueta": "x",
        "campo_dato": "log_contenedor", "dias_esperados": 1,
    })

    assert resp.status_code == 404


def test_eliminar_hito_auditoria_ok(mocker):
    conn = MagicMock()
    cursor = MagicMock()
    cursor.rowcount = 1
    conn.cursor.return_value = cursor
    mocker.patch("routes.importaciones.obtener_conexion", return_value=conn)

    resp = _cliente_test().delete("/importaciones/hitos-auditoria/7")

    assert resp.status_code == 200
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_importaciones_auditoria.py -k hito_auditoria -v`
Expected: FAIL — all 404 (routes don't exist yet).

- [x] **Step 3: Write the validation helper and the four routes**

Add in `routes/importaciones.py`, right after `eliminar_tiempo_estimado` (currently ends at line 798, look for the blank lines after `return jsonify({"ok": True}), 200` / `finally: conn.close()` that closes that function):

```python
# ── Hitos de auditoría de llenado (sección → días esperados desde un ancla) ──
# Alimentan _calcular_auditoria(). Pantalla de administración en el frontend:
# /importaciones/hitos-auditoria

_SECCIONES_HITOS = {
    "logistica", "importacion", "despacho", "odoo",
    "almacen", "recepcion", "cierre", "costos",
}


def _validar_payload_hito(data: dict) -> str | None:
    if str(data.get("seccion") or "").strip() not in _SECCIONES_HITOS:
        return "Sección inválida"
    if not str(data.get("etiqueta") or "").strip():
        return "Falta la etiqueta"
    if str(data.get("campo_dato") or "").strip() not in _COLS_PERMITIDAS:
        return "'campo_dato' debe ser una columna válida del formulario"
    campo_ancla = data.get("campo_ancla")
    if campo_ancla and str(campo_ancla).strip() not in _COLS_PERMITIDAS:
        return "'campo_ancla' debe ser una columna válida del formulario (o vacío = Alta de embarque)"
    try:
        if int(data.get("dias_esperados")) < 0:
            return "'dias_esperados' no puede ser negativo"
    except (TypeError, ValueError):
        return "'dias_esperados' debe ser un número entero de días"
    try:
        int(data.get("orden_hito"))
    except (TypeError, ValueError):
        return "'orden_hito' debe ser un número entero"
    return None


@importaciones_bp.route("/hitos-auditoria", methods=["GET"])
def listar_hitos_auditoria():
    conn = obtener_conexion()
    if not conn:
        return jsonify({"error": "Sin conexion a BD"}), 500
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM importaciones_hitos_auditoria ORDER BY seccion, orden_hito"
        )
        return jsonify([_serialize(r) for r in cursor.fetchall()]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@importaciones_bp.route("/hitos-auditoria", methods=["POST"])
def crear_hito_auditoria():
    data = request.get_json() or {}
    error = _validar_payload_hito(data)
    if error:
        return jsonify({"error": error}), 400

    conn = obtener_conexion()
    if not conn:
        return jsonify({"error": "Sin conexion a BD"}), 500
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO importaciones_hitos_auditoria "
            "(seccion, orden_hito, etiqueta, campo_dato, campo_ancla, dias_esperados, activo) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                data["seccion"].strip(), int(data["orden_hito"]), data["etiqueta"].strip(),
                data["campo_dato"].strip(),
                (data.get("campo_ancla") or "").strip() or None,
                int(data["dias_esperados"]), 1 if data.get("activo", True) else 0,
            ),
        )
        conn.commit()
        return jsonify({"ok": True, "id": cursor.lastrowid}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@importaciones_bp.route("/hitos-auditoria/<int:id_hito>", methods=["PUT"])
def actualizar_hito_auditoria(id_hito):
    data = request.get_json() or {}
    error = _validar_payload_hito(data)
    if error:
        return jsonify({"error": error}), 400

    conn = obtener_conexion()
    if not conn:
        return jsonify({"error": "Sin conexion a BD"}), 500
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE importaciones_hitos_auditoria SET "
            "seccion = %s, orden_hito = %s, etiqueta = %s, campo_dato = %s, "
            "campo_ancla = %s, dias_esperados = %s, activo = %s WHERE id = %s",
            (
                data["seccion"].strip(), int(data["orden_hito"]), data["etiqueta"].strip(),
                data["campo_dato"].strip(),
                (data.get("campo_ancla") or "").strip() or None,
                int(data["dias_esperados"]), 1 if data.get("activo", True) else 0,
                id_hito,
            ),
        )
        conn.commit()
        if cursor.rowcount == 0:
            return jsonify({"error": "No encontrado"}), 404
        return jsonify({"ok": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@importaciones_bp.route("/hitos-auditoria/<int:id_hito>", methods=["DELETE"])
def eliminar_hito_auditoria(id_hito):
    conn = obtener_conexion()
    if not conn:
        return jsonify({"error": "Sin conexion a BD"}), 500
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM importaciones_hitos_auditoria WHERE id = %s", (id_hito,))
        conn.commit()
        if cursor.rowcount == 0:
            return jsonify({"error": "No encontrado"}), 404
        return jsonify({"ok": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_importaciones_auditoria.py -k hito_auditoria -v`
Expected: PASS (5 tests)

- [x] **Step 5: Seed the 20 milestones from the spec against the real local database**

```python
# tests/test_importaciones_auditoria.py (append) -- this is a one-time seed
# script disguised as a test so it's easy to re-run; it uses INSERT and is
# safe to run once against local. Mark it skipped by default.
import pytest as _pytest

_HITOS_INICIALES = [
    ("logistica",    1, "1. Importador",                                   "odoo_importador",              None,                              0),
    ("costos",       1, "Flete proyectado (USD)",                          "cos_flete_proyectado_usd",     None,                              0),
    ("logistica",    2, "9. Confirmación de cotización y forwarder",       "log_confirmacion_cotizacion",  None,                              7),
    ("logistica",    3, "18. Contenedores",                                "log_contenedor",               "log_fecha_entrega",              1),
    ("importacion",  1, "1. Fecha de entrega de traducción al RBF",        "imp_fecha_traduccion",         "log_fecha_entrega",              10),
    ("odoo",         1, "1. Recepción de documentos",                      "log_recepcion_documentos",     "log_fecha_entrega",              2),
    ("odoo",         2, "6. Folios de orden de compra",                    "odoo_folio_orden",             "log_recepcion_documentos",       10),
    ("importacion",  2, "18. Recepción de draft de pedimento",             "imp_recepcion_draft_pedimento","imp_llegada_contenedor_puerto",  5),
    ("importacion",  3, "34. Fecha de pago de pedimento",                  "imp_fecha_pago_pedimento",     "imp_pedimento_revisado",         4),
    ("despacho",     1, "1. Solicitud de cita para cruce",                 "des_solicitud_cita_cruce",     "imp_fecha_pago_pedimento",       1),
    ("almacen",      1, "1. Base de datos para etiquetas",                 "alm_base_datos_etiquetas",     "imp_fecha_pago_pedimento",       2),
    ("despacho",     2, "9. Llegada de contenedor a almacén",              "des_llegada_almacen",          "des_fecha_cruce_real",           2),
    ("despacho",     3, "15. Recepción de documento EIR",                  "des_recepcion_eir",            "des_fecha_cruce_real",           3),
    ("almacen",      2, "6. Envío de información a la UVA (Real)",         "alm_envio_info_uva",           "des_llegada_almacen",            2),
    ("almacen",      3, "10. Fecha de terminación de etiquetado (Real)",   "alm_terminacion_etiquetado",   "alm_inicio_etiquetado",          3),
    ("recepcion",    1, "Cédula de costeo de IGI",                         "rec_cedula_costeo",            "des_llegada_almacen",            2),
    ("recepcion",    2, "Liberación final del producto",                   "rec_liberacion_final",         "rec_liberacion_verificacion",    1),
    ("costos",       3, "Costos reales",                                   "cos_tipo_cambio_pedimento",    "des_fecha_cruce_real",           10),
    ("cierre",       1, "1. Recepción de cuentas de gastos",                "cie_recepcion_cuenta_gastos",  "cos_tipo_cambio_pedimento",       2),
    ("cierre",       2, "Fecha de pago a agente aduanal",                  "cie_fecha_pago_aa",            "cie_recepcion_cuenta_gastos",    4),
]


@_pytest.mark.skip(reason="Seed manual -- correr una vez contra local quitando el skip, luego restaurarlo")
def test_seed_hitos_iniciales():
    conn = obtener_conexion()
    cursor = conn.cursor()
    cursor.executemany(
        "INSERT INTO importaciones_hitos_auditoria "
        "(seccion, orden_hito, etiqueta, campo_dato, campo_ancla, dias_esperados) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        _HITOS_INICIALES,
    )
    conn.commit()
    conn.close()
```

Run manually once: remove the `@_pytest.mark.skip` line, run
`pytest tests/test_importaciones_auditoria.py::test_seed_hitos_iniciales -v`,
confirm with `SELECT COUNT(*) FROM importaciones_hitos_auditoria;` that it
shows 20, then put the `@_pytest.mark.skip` line back and commit it skipped
(it must never run again in CI — it is a one-time seed, not a repeatable
test; `INSERT` here has no uniqueness guard unlike `tiempos_estimados`'
`INSERT IGNORE`, so re-running it duplicates all 20 rows).

- [x] **Step 6: Commit**

```bash
git add routes/importaciones.py tests/test_importaciones_auditoria.py
git commit -m "feat(importaciones): CRUD de hitos-auditoria + seed de los 20 hitos iniciales"
```

---

## Task 4: Motor de cálculo + endpoint de auditoría

**Files:**
- Modify: `routes/importaciones.py` (new function `_calcular_auditoria`, new route)
- Test: `tests/test_importaciones_auditoria.py`

**Interfaces:**
- Consumes: `_calc_dias(fecha_desde, fecha_hasta)` (existing, `routes/importaciones.py:36-45`), tables from Task 1.
- Produces: `_calcular_auditoria(importacion_id, conn) -> list[dict]`, route `GET /importaciones/<id>/auditoria`.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_importaciones_auditoria.py (append)
from routes.importaciones import _calcular_auditoria


def _conn_mock_auditoria(embarque_row, hitos_rows, historial_rows):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    # 3 llamadas en orden: embarque, hitos activos, historial del embarque
    cursor.fetchone.side_effect = [embarque_row]
    cursor.fetchall.side_effect = [hitos_rows, historial_rows]
    return conn, cursor


def test_auditoria_hito_desde_alta_de_embarque_a_tiempo():
    embarque = {"id": 1, "created_at": date(2026, 1, 1)}
    hitos = [{"id": 1, "seccion": "logistica", "orden_hito": 1, "etiqueta": "1. Importador",
              "campo_dato": "odoo_importador", "campo_ancla": None, "dias_esperados": 0}]
    historial = [{"campo": "odoo_importador", "capturado_en": date(2026, 1, 1)}]
    conn, _ = _conn_mock_auditoria(embarque, hitos, historial)

    resultado = _calcular_auditoria(1, conn)

    assert len(resultado) == 1
    assert resultado[0]["estado"] == "a_tiempo"
    assert resultado[0]["dias_diferencia"] == 0


def test_auditoria_hito_capturado_antes_sale_verde():
    embarque = {"id": 1, "created_at": date(2026, 1, 1)}
    hitos = [{"id": 1, "seccion": "logistica", "orden_hito": 2, "etiqueta": "9. Confirmacion",
              "campo_dato": "log_confirmacion_cotizacion", "campo_ancla": None, "dias_esperados": 7}]
    # esperada = 2026-01-08; real 2 dias antes = 2026-01-06
    historial = [{"campo": "log_confirmacion_cotizacion", "capturado_en": date(2026, 1, 6)}]
    conn, _ = _conn_mock_auditoria(embarque, hitos, historial)

    resultado = _calcular_auditoria(1, conn)

    assert resultado[0]["estado"] == "adelantado"
    assert resultado[0]["dias_diferencia"] == 2


def test_auditoria_hito_capturado_despues_sale_rojo():
    embarque = {"id": 1, "created_at": date(2026, 1, 1)}
    hitos = [{"id": 1, "seccion": "logistica", "orden_hito": 2, "etiqueta": "9. Confirmacion",
              "campo_dato": "log_confirmacion_cotizacion", "campo_ancla": None, "dias_esperados": 7}]
    # esperada = 2026-01-08; real 3 dias tarde = 2026-01-11
    historial = [{"campo": "log_confirmacion_cotizacion", "capturado_en": date(2026, 1, 11)}]
    conn, _ = _conn_mock_auditoria(embarque, hitos, historial)

    resultado = _calcular_auditoria(1, conn)

    assert resultado[0]["estado"] == "atrasado"
    assert resultado[0]["dias_diferencia"] == -3


def test_auditoria_hito_anclado_en_otro_hito_usa_esperada_no_real():
    embarque = {"id": 1, "created_at": date(2026, 1, 1)}
    hitos = [
        {"id": 1, "seccion": "odoo", "orden_hito": 1, "etiqueta": "1. Recepcion de documentos",
         "campo_dato": "log_recepcion_documentos", "campo_ancla": "log_fecha_entrega", "dias_esperados": 2},
        {"id": 2, "seccion": "odoo", "orden_hito": 2, "etiqueta": "6. Folios de orden de compra",
         "campo_dato": "odoo_folio_orden", "campo_ancla": "log_recepcion_documentos", "dias_esperados": 10},
    ]
    # log_fecha_entrega (disparador) se captura TARDE el 2026-01-20 en vez de a tiempo.
    # El hito 1 (Recepcion de documentos) por lo tanto tiene esperada = 2026-01-22,
    # y se captura justo a tiempo el 2026-01-22.
    # El hito 2 (Folios) debe esperar 10 dias desde la ESPERADA del hito 1
    # (2026-01-22), no desde el 2026-01-20 del disparador ni desde otra fecha:
    # esperada del hito 2 = 2026-02-01.
    historial = [
        {"campo": "log_fecha_entrega", "capturado_en": date(2026, 1, 20)},
        {"campo": "log_recepcion_documentos", "capturado_en": date(2026, 1, 22)},
        {"campo": "odoo_folio_orden", "capturado_en": date(2026, 2, 1)},
    ]
    conn, _ = _conn_mock_auditoria(embarque, hitos, historial)

    resultado = _calcular_auditoria(1, conn)

    folios = next(h for h in resultado if h["campo_dato"] == "odoo_folio_orden")
    assert folios["fecha_esperada"] == "2026-02-01"
    assert folios["estado"] == "a_tiempo"


def test_auditoria_hito_sin_disparador_capturado_queda_pendiente():
    embarque = {"id": 1, "created_at": date(2026, 1, 1)}
    hitos = [{"id": 1, "seccion": "logistica", "orden_hito": 3, "etiqueta": "18. Contenedores",
              "campo_dato": "log_contenedor", "campo_ancla": "log_fecha_entrega", "dias_esperados": 1}]
    historial = []  # log_fecha_entrega nunca se capturo
    conn, _ = _conn_mock_auditoria(embarque, hitos, historial)

    resultado = _calcular_auditoria(1, conn)

    assert resultado[0]["estado"] == "pendiente"
    assert resultado[0]["fecha_esperada"] is None


def test_auditoria_sin_hitos_activos_devuelve_lista_vacia():
    embarque = {"id": 1, "created_at": date(2026, 1, 1)}
    conn, _ = _conn_mock_auditoria(embarque, [], [])

    assert _calcular_auditoria(1, conn) == []


def test_auditoria_referencia_circular_queda_no_resoluble_sin_crashear():
    embarque = {"id": 1, "created_at": date(2026, 1, 1)}
    # A ancla en B y B ancla en A -- config invalida (error de captura en el
    # admin de hitos), pero el motor no debe crashear ni colgarse.
    hitos = [
        {"id": 1, "seccion": "logistica", "orden_hito": 1, "etiqueta": "A",
         "campo_dato": "campo_a", "campo_ancla": "campo_b", "dias_esperados": 1},
        {"id": 2, "seccion": "logistica", "orden_hito": 2, "etiqueta": "B",
         "campo_dato": "campo_b", "campo_ancla": "campo_a", "dias_esperados": 1},
    ]
    conn, _ = _conn_mock_auditoria(embarque, hitos, [])

    resultado = _calcular_auditoria(1, conn)

    assert len(resultado) == 2
    assert all(h["estado"] == "pendiente" for h in resultado)


def test_auditoria_dos_hitos_con_mismo_campo_dato_usa_el_de_menor_id():
    embarque = {"id": 1, "created_at": date(2026, 1, 1)}
    hitos = [
        {"id": 5, "seccion": "logistica", "orden_hito": 1, "etiqueta": "Version vieja",
         "campo_dato": "log_contenedor", "campo_ancla": None, "dias_esperados": 10},
        {"id": 2, "seccion": "logistica", "orden_hito": 1, "etiqueta": "Version nueva (id menor)",
         "campo_dato": "log_contenedor", "campo_ancla": None, "dias_esperados": 3},
        {"id": 9, "seccion": "importacion", "orden_hito": 1, "etiqueta": "Depende de Contenedores",
         "campo_dato": "imp_fecha_traduccion", "campo_ancla": "log_contenedor", "dias_esperados": 1},
    ]
    conn, _ = _conn_mock_auditoria(embarque, hitos, [])

    resultado = _calcular_auditoria(1, conn)

    dependiente = next(h for h in resultado if h["campo_dato"] == "imp_fecha_traduccion")
    # esperada de log_contenedor debe resolverse con el hito id=2 (dias_esperados=3),
    # no con el id=5 (dias_esperados=10): 2026-01-01 + 3 + 1 = 2026-01-05.
    assert dependiente["fecha_esperada"] == "2026-01-05"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_importaciones_auditoria.py -k "auditoria_hito or auditoria_sin_hitos or auditoria_referencia_circular or auditoria_dos_hitos" -v`
Expected: FAIL with `ImportError: cannot import name '_calcular_auditoria'`

- [x] **Step 3: Write `_calcular_auditoria`**

Add in `routes/importaciones.py`, right after `_calc_dias` (after line 45):

```python
def _calcular_auditoria(importacion_id: int, conn) -> list[dict]:
    """Resuelve cada hito activo de importaciones_hitos_auditoria contra su
    ancla y devuelve el resultado de la auditoria de llenado. Ver
    docs/superpowers/specs/2026-09-30-auditoria-llenado-embarque-design.md
    §4 para las 3 reglas de resolucion de fecha esperada."""
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, created_at FROM importaciones WHERE id = %s", (importacion_id,))
    embarque = cursor.fetchone()
    if not embarque:
        return []

    cursor.execute(
        "SELECT * FROM importaciones_hitos_auditoria WHERE activo = 1 ORDER BY seccion, orden_hito"
    )
    hitos = cursor.fetchall()
    if not hitos:
        return []

    cursor.execute(
        "SELECT campo, capturado_en FROM importaciones_historial_campos WHERE importacion_id = %s",
        (importacion_id,),
    )
    capturado_en = {}
    for fila in cursor.fetchall():
        # Si un campo se capturo mas de una vez por error de datos viejos,
        # se queda con la mas antigua (la primera captura real).
        actual = capturado_en.get(fila["campo"])
        if actual is None or fila["capturado_en"] < actual:
            capturado_en[fila["campo"]] = fila["capturado_en"]

    # Un campo_dato puede repetirse entre hitos activos por error de
    # configuracion -- se usa el de menor id como el "dueño" de la fecha
    # esperada para que otros hitos que anclen en el mismo campo_dato
    # resuelvan de forma deterministica.
    hito_por_campo_dato: dict = {}
    for h in sorted(hitos, key=lambda x: x["id"]):
        hito_por_campo_dato.setdefault(h["campo_dato"], h)

    fecha_esperada_resuelta: dict = {}

    def resolver_esperada(hito, visitados=None):
        campo_dato = hito["campo_dato"]
        if campo_dato in fecha_esperada_resuelta:
            return fecha_esperada_resuelta[campo_dato]
        visitados = visitados or set()
        if campo_dato in visitados:
            fecha_esperada_resuelta[campo_dato] = None  # referencia circular -- no resoluble
            return None
        visitados = visitados | {campo_dato}

        ancla = hito["campo_ancla"]
        if not ancla:
            base = embarque["created_at"]
            if isinstance(base, str):
                base = date.fromisoformat(base[:10])
            elif hasattr(base, "date") and not isinstance(base, date):
                base = base.date()
        elif ancla in hito_por_campo_dato:
            base = resolver_esperada(hito_por_campo_dato[ancla], visitados)
            if base is None:
                fecha_esperada_resuelta[campo_dato] = None
                return None
        else:
            # Ancla "disparador": no es un hito con fecha esperada propia --
            # se usa su fecha REAL capturada, si existe.
            real_ancla = capturado_en.get(ancla)
            if real_ancla is None:
                fecha_esperada_resuelta[campo_dato] = None
                return None
            base = real_ancla if isinstance(real_ancla, date) else date.fromisoformat(str(real_ancla)[:10])

        resultado = base + timedelta(days=int(hito["dias_esperados"]))
        fecha_esperada_resuelta[campo_dato] = resultado
        return resultado

    salida = []
    for hito in hitos:
        esperada = resolver_esperada(hito)
        real = capturado_en.get(hito["campo_dato"])
        real_date = None
        if real is not None:
            real_date = real if isinstance(real, date) else date.fromisoformat(str(real)[:10])

        if esperada is None:
            estado, dias_diferencia = "pendiente", None
        elif real_date is None:
            estado, dias_diferencia = "en_espera", None
        else:
            dias_diferencia = (esperada - real_date).days
            if dias_diferencia > 0:
                estado = "adelantado"
            elif dias_diferencia < 0:
                estado = "atrasado"
            else:
                estado = "a_tiempo"

        salida.append({
            "id": hito["id"], "seccion": hito["seccion"], "orden_hito": hito["orden_hito"],
            "etiqueta": hito["etiqueta"], "campo_dato": hito["campo_dato"],
            "fecha_esperada": esperada.isoformat() if esperada else None,
            "fecha_real": real_date.isoformat() if real_date else None,
            "estado": estado, "dias_diferencia": dias_diferencia,
        })
    return salida
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_importaciones_auditoria.py -k "auditoria_hito or auditoria_sin_hitos or auditoria_referencia_circular or auditoria_dos_hitos" -v`
Expected: PASS (8 tests)

- [x] **Step 5: Add the route**

Add in `routes/importaciones.py`, right after `eliminar_hito_auditoria` (end of Task 3's code):

```python
@importaciones_bp.route("/<int:id_imp>/auditoria", methods=["GET"])
def obtener_auditoria(id_imp):
    conn = obtener_conexion()
    if not conn:
        return jsonify({"error": "Sin conexion a BD"}), 500
    try:
        return jsonify(_calcular_auditoria(id_imp, conn)), 200
    except Exception as e:
        logging.exception("Error calculando auditoria del embarque %s: %s", id_imp, e)
        return jsonify({"error": "No se pudo calcular la auditoría"}), 500
    finally:
        conn.close()
```

- [x] **Step 6: Write the route test**

```python
# tests/test_importaciones_auditoria.py (append)

def test_get_auditoria_devuelve_200_con_lista(mocker):
    conn = MagicMock()
    mocker.patch("routes.importaciones.obtener_conexion", return_value=conn)
    mocker.patch("routes.importaciones._calcular_auditoria", return_value=[
        {"id": 1, "seccion": "logistica", "estado": "a_tiempo"},
    ])

    resp = _cliente_test().get("/importaciones/123/auditoria")

    assert resp.status_code == 200
    assert resp.get_json()[0]["estado"] == "a_tiempo"
```

Run: `pytest tests/test_importaciones_auditoria.py -k get_auditoria -v`
Expected: PASS

- [x] **Step 7: Run the whole test file once more**

Run: `pytest tests/test_importaciones_auditoria.py -v`
Expected: all PASS (the manual seed test stays skipped)

- [x] **Step 8: Commit**

```bash
git add routes/importaciones.py tests/test_importaciones_auditoria.py
git commit -m "feat(importaciones): motor de calculo de auditoria + GET /importaciones/<id>/auditoria"
```

---

## Task 5: Frontend — servicios Angular

**Files:**
- Create: `EB_FRONT/src/app/services/hitos-auditoria.service.ts`
- Create: `EB_FRONT/src/app/services/hitos-auditoria.service.spec.ts`
- Modify: `EB_FRONT/src/app/services/importaciones.service.ts` (confirmed: this is the service `importaciones-detalle.component.ts` injects as `svc` and calls `.actualizar(id, data)` on; its `private base = \`${environment.apiUrl}/importaciones\`;` is at line 256)
- Modify: `EB_FRONT/src/app/services/importaciones.service.spec.ts`

**Interfaces:**
- Produces: `HitosAuditoriaService.listar/crear/actualizar/eliminar`, `<ExistingImportacionesService>.obtenerAuditoria(id): Observable<HitoAuditoria[]>`.

- [x] **Step 1: Write the failing spec for the new service**

```typescript
// EB_FRONT/src/app/services/hitos-auditoria.service.spec.ts
import { TestBed } from '@angular/core/testing';
import { HttpClientTestingModule, HttpTestingController } from '@angular/common/http/testing';
import { HitosAuditoriaService } from './hitos-auditoria.service';
import { environment } from '../../environments/environment';

describe('HitosAuditoriaService', () => {
  let service: HitosAuditoriaService;
  let httpMock: HttpTestingController;
  const base = `${environment.apiUrl}/importaciones/hitos-auditoria`;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [HttpClientTestingModule],
      providers: [HitosAuditoriaService],
    });
    service = TestBed.inject(HitosAuditoriaService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('listar() hace GET a /importaciones/hitos-auditoria', () => {
    service.listar().subscribe();
    const req = httpMock.expectOne(base);
    expect(req.request.method).toBe('GET');
    req.flush([]);
  });

  it('crear() hace POST con el payload', () => {
    const payload = { seccion: 'logistica', orden_hito: 1, etiqueta: 'x', campo_dato: 'log_contenedor', campo_ancla: null, dias_esperados: 1 };
    service.crear(payload).subscribe();
    const req = httpMock.expectOne(base);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(payload);
    req.flush({ ok: true, id: 1 });
  });

  it('actualizar() hace PUT a /hitos-auditoria/:id', () => {
    const payload = { seccion: 'logistica', orden_hito: 1, etiqueta: 'x', campo_dato: 'log_contenedor', campo_ancla: null, dias_esperados: 1 };
    service.actualizar(5, payload).subscribe();
    const req = httpMock.expectOne(`${base}/5`);
    expect(req.request.method).toBe('PUT');
    req.flush({ ok: true });
  });

  it('eliminar() hace DELETE a /hitos-auditoria/:id', () => {
    service.eliminar(5).subscribe();
    const req = httpMock.expectOne(`${base}/5`);
    expect(req.request.method).toBe('DELETE');
    req.flush({ ok: true });
  });
});
```

- [x] **Step 2: Run the spec to verify it fails**

Run: `ng test --include='**/hitos-auditoria.service.spec.ts' --watch=false`
Expected: FAIL — `hitos-auditoria.service.ts` does not exist.

- [x] **Step 3: Write the service**

```typescript
// EB_FRONT/src/app/services/hitos-auditoria.service.ts
import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';

export interface HitoAuditoria {
  id: number;
  seccion: string;
  orden_hito: number;
  etiqueta: string;
  campo_dato: string;
  campo_ancla: string | null;
  dias_esperados: number;
  activo: boolean;
  created_at?: string;
  updated_at?: string;
}

export type HitoAuditoriaPayload = Omit<HitoAuditoria, 'id' | 'created_at' | 'updated_at' | 'activo'> & { activo?: boolean };

@Injectable({ providedIn: 'root' })
export class HitosAuditoriaService {
  private base = `${environment.apiUrl}/importaciones/hitos-auditoria`;

  constructor(private http: HttpClient) {}

  listar(): Observable<HitoAuditoria[]> {
    return this.http.get<HitoAuditoria[]>(this.base);
  }

  crear(data: HitoAuditoriaPayload): Observable<{ ok: boolean; id: number }> {
    return this.http.post<{ ok: boolean; id: number }>(this.base, data);
  }

  actualizar(id: number, data: HitoAuditoriaPayload): Observable<{ ok: boolean }> {
    return this.http.put<{ ok: boolean }>(`${this.base}/${id}`, data);
  }

  eliminar(id: number): Observable<{ ok: boolean }> {
    return this.http.delete<{ ok: boolean }>(`${this.base}/${id}`);
  }
}
```

- [x] **Step 4: Run the spec to verify it passes**

Run: `ng test --include='**/hitos-auditoria.service.spec.ts' --watch=false`
Expected: PASS (4 specs)

- [x] **Step 5: Write the failing spec for `obtenerAuditoria`**

`importaciones.service.ts` has no existing `.spec.ts` (verified: the file does not exist in the repo). Create `EB_FRONT/src/app/services/importaciones.service.spec.ts` covering only the new method — retrofitting tests for the rest of the pre-existing service is out of scope for this project:

```typescript
// EB_FRONT/src/app/services/importaciones.service.spec.ts
import { TestBed } from '@angular/core/testing';
import { HttpClientTestingModule, HttpTestingController } from '@angular/common/http/testing';
import { ImportacionesService } from './importaciones.service';
import { environment } from '../../environments/environment';

describe('ImportacionesService.obtenerAuditoria', () => {
  let service: ImportacionesService;
  let httpMock: HttpTestingController;
  const base = `${environment.apiUrl}/importaciones`;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [HttpClientTestingModule],
      providers: [ImportacionesService],
    });
    service = TestBed.inject(ImportacionesService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('hace GET a /importaciones/:id/auditoria', () => {
    service.obtenerAuditoria(42).subscribe();
    const req = httpMock.expectOne(`${base}/42/auditoria`);
    expect(req.request.method).toBe('GET');
    req.flush([]);
  });
});
```

- [x] **Step 6: Run it, verify it fails, then add the method to `importaciones.service.ts`**

```typescript
obtenerAuditoria(id: number): Observable<HitoAuditoriaResultado[]> {
  return this.http.get<HitoAuditoriaResultado[]>(`${this.base}/${id}/auditoria`);
}
```

Add this interface near the service's other interfaces:

```typescript
export interface HitoAuditoriaResultado {
  id: number;
  seccion: string;
  orden_hito: number;
  etiqueta: string;
  campo_dato: string;
  fecha_esperada: string | null;
  fecha_real: string | null;
  estado: 'a_tiempo' | 'adelantado' | 'atrasado' | 'pendiente' | 'en_espera';
  dias_diferencia: number | null;
}
```

- [x] **Step 7: Run both spec files to verify everything passes**

Run: `ng test --include='**/hitos-auditoria.service.spec.ts' --include='**/importaciones.service.spec.ts' --watch=false`
Expected: PASS

- [x] **Step 8: Commit**

```bash
git add EB_FRONT/src/app/services/hitos-auditoria.service.ts EB_FRONT/src/app/services/hitos-auditoria.service.spec.ts EB_FRONT/src/app/services/importaciones.service.ts EB_FRONT/src/app/services/importaciones.service.spec.ts
git commit -m "feat(importaciones): servicios Angular de auditoria de llenado y CRUD de hitos"
```

---

## Task 6: Frontend — pestaña "Auditoría" en el detalle del embarque

**Files:**
- Modify: `EB_FRONT/src/app/views/internal-views/importaciones/importaciones-detalle/importaciones-detalle.component.ts`
- Modify: `EB_FRONT/src/app/views/internal-views/importaciones/importaciones-detalle/importaciones-detalle.component.html`
- Modify: `EB_FRONT/src/app/views/internal-views/importaciones/importaciones-detalle/importaciones-detalle.component.css`

**Interfaces:**
- Consumes: `Seccion` type and `seccionActiva`/`cambiarSeccion()` (existing, lines 12/59/619), `HitoAuditoriaResultado` and `obtenerAuditoria()` from Task 5.

- [x] **Step 1: Extend the `Seccion` type and tab list**

In `importaciones-detalle.component.ts`, change line 12 from:

```typescript
type Seccion = 'logistica' | 'importacion' | 'despacho' | 'odoo' | 'almacen' | 'recepcion' | 'cierre' | 'costos';
```

to:

```typescript
type Seccion = 'logistica' | 'importacion' | 'despacho' | 'odoo' | 'almacen' | 'recepcion' | 'cierre' | 'costos' | 'auditoria';
```

Find the tab definitions array (around line 77-84, the one with `{ key: 'logistica', label: 'Logística', icon: 'fa-ship' }, ...` ending with `cierre`) and add a final entry:

```typescript
    { key: 'auditoria',   label: 'Auditoría',   icon: 'fa-magnifying-glass-chart' },
```

- [x] **Step 2: Add component state and a load method**

Near the other `@Input`/state properties in the component class, add:

```typescript
  auditoria: HitoAuditoriaResultado[] = [];
  cargandoAuditoria = false;
  errorAuditoria = '';

  cargarAuditoria(): void {
    if (!this.embarque?.id) return;
    this.cargandoAuditoria = true;
    this.errorAuditoria = '';
    this.svc.obtenerAuditoria(this.embarque.id).subscribe({
      next: (res) => { this.auditoria = res; this.cargandoAuditoria = false; },
      error: () => { this.errorAuditoria = 'No se pudo cargar la auditoría.'; this.cargandoAuditoria = false; },
    });
  }

  auditoriaPorSeccion(): { seccion: string; hitos: HitoAuditoriaResultado[] }[] {
    const mapa = new Map<string, HitoAuditoriaResultado[]>();
    for (const h of this.auditoria) {
      if (!mapa.has(h.seccion)) mapa.set(h.seccion, []);
      mapa.get(h.seccion)!.push(h);
    }
    return [...mapa.entries()].map(([seccion, hitos]) => ({ seccion, hitos }));
  }
```

Add the import at the top of the file: `import { HitoAuditoriaResultado } from '../../../../services/importaciones.service';`

- [x] **Step 3: Trigger the load when the tab is opened**

Find `cambiarSeccion(s: Seccion): void {` (currently around line 619) and add at the top of its body:

```typescript
  cambiarSeccion(s: Seccion): void {
    if (s === 'auditoria' && !this.auditoria.length) this.cargarAuditoria();
    // ... resto del método existente sin cambios
```

- [x] **Step 4: Add the template block**

In `importaciones-detalle.component.html`, find the closing of the last section's `*ngIf="seccionActiva === 'cierre'"` block and add immediately after it, before the closing tag that wraps all sections:

```html
<ng-container *ngIf="seccionActiva === 'auditoria'">
  <div class="auditoria-header">
    <h3>Auditoría de llenado</h3>
    <p class="auditoria-nota">
      Compara la fecha en que cada hito se llenó realmente contra la fecha esperada.
      Verde = se adelantó, rojo = se atrasó. Los campos llenados antes de activar esta
      auditoría no tienen dato histórico y se muestran como "Sin dato histórico".
    </p>
  </div>

  <div *ngIf="cargandoAuditoria" class="auditoria-cargando">Calculando auditoría…</div>
  <div *ngIf="errorAuditoria" class="auditoria-error">{{ errorAuditoria }}</div>

  <div *ngIf="!cargandoAuditoria && !errorAuditoria">
    <div class="auditoria-grupo" *ngFor="let grupo of auditoriaPorSeccion()">
      <h4 class="auditoria-grupo-titulo">{{ grupo.seccion | titlecase }}</h4>
      <table class="auditoria-tabla">
        <thead>
          <tr><th>Hito</th><th>Fecha esperada</th><th>Fecha real</th><th>Diferencia</th></tr>
        </thead>
        <tbody>
          <tr *ngFor="let h of grupo.hitos">
            <td>{{ h.etiqueta }}</td>
            <td>{{ h.fecha_esperada || 'Pendiente' }}</td>
            <td>{{ h.fecha_real || 'Sin dato histórico' }}</td>
            <td>
              <span *ngIf="h.estado === 'adelantado'" class="auditoria-badge auditoria-verde">+{{ h.dias_diferencia }} días</span>
              <span *ngIf="h.estado === 'atrasado'" class="auditoria-badge auditoria-rojo">{{ h.dias_diferencia }} días</span>
              <span *ngIf="h.estado === 'a_tiempo'" class="auditoria-badge auditoria-gris">A tiempo</span>
              <span *ngIf="h.estado === 'pendiente'" class="auditoria-badge auditoria-gris">Pendiente</span>
              <span *ngIf="h.estado === 'en_espera'" class="auditoria-badge auditoria-gris">En espera</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p *ngIf="!auditoria.length" class="asig-vacio">No hay hitos de auditoría configurados.</p>
  </div>
</ng-container>
```

- [x] **Step 5: Add the CSS**

In `importaciones-detalle.component.css`, add:

```css
.auditoria-header { margin-bottom: 16px; }
.auditoria-nota { color: #94a3b8; font-size: 12.5px; max-width: 640px; }
.auditoria-grupo { margin-bottom: 24px; }
.auditoria-grupo-titulo { color: #e2e8f0; font-size: 13px; text-transform: uppercase; letter-spacing: .4px; margin-bottom: 8px; }
.auditoria-tabla { width: 100%; border-collapse: collapse; }
.auditoria-tabla th { text-align: left; font-size: 11px; color: #94a3b8; text-transform: uppercase; padding: 8px; border-bottom: 1px solid #2d3748; }
.auditoria-tabla td { padding: 10px 8px; border-bottom: 1px solid #1e2535; font-size: 13px; color: #e2e8f0; }
.auditoria-badge { display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: 11.5px; font-weight: 700; }
.auditoria-verde { background: #12251a; color: #4ade80; }
.auditoria-rojo { background: #2a1416; color: #f87171; }
.auditoria-gris { background: #1e293b; color: #94a3b8; }
.auditoria-cargando, .auditoria-error { color: #94a3b8; padding: 24px; text-align: center; }
```

- [x] **Step 6: Verify manually in the browser (per repo convention — frontend changes get checked in `ng serve` before commit)**

Run: `ng serve` (from `EB_FRONT`), open an existing embarque's detail page, click the new "Auditoría" tab, confirm it loads without console errors and shows either milestone rows or the empty-state message (seed data from Task 3 Step 5 must already be in the local database for rows to appear).

- [x] **Step 7: Commit**

```bash
git add EB_FRONT/src/app/views/internal-views/importaciones/importaciones-detalle/
git commit -m "feat(importaciones): pestana de Auditoria en el detalle del embarque"
```

---

## Task 7: Frontend — pantalla de administración de hitos

**Files:**
- Create: `EB_FRONT/src/app/views/internal-views/importaciones/importaciones-hitos-auditoria/importaciones-hitos-auditoria.component.ts`
- Create: `EB_FRONT/src/app/views/internal-views/importaciones/importaciones-hitos-auditoria/importaciones-hitos-auditoria.component.html`
- Create: `EB_FRONT/src/app/views/internal-views/importaciones/importaciones-hitos-auditoria/importaciones-hitos-auditoria.component.css`
- Modify: `EB_FRONT/src/app/app.routes.ts`

**Interfaces:**
- Consumes: `HitosAuditoriaService` (Task 5), `_COLS_PERMITIDAS`-equivalent list of selectable columns (hardcoded in the component, grouped by section, mirroring `CAMPOS_LOGISTICA`/etc. from `routes/importaciones.py:54-144` — only the human-readable subset needed for the select options, not a live API call).

- [x] **Step 1: Write the component, mirroring `importaciones-tiempos-estimados.component.ts` exactly for the modal/CRUD plumbing**

```typescript
// importaciones-hitos-auditoria.component.ts
import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { HomeBarComponent } from '../../../../components/home-bar/home-bar.component';
import { HitosAuditoriaService, HitoAuditoria, HitoAuditoriaPayload } from '../../../../services/hitos-auditoria.service';

const SECCIONES = ['logistica', 'importacion', 'despacho', 'odoo', 'almacen', 'recepcion', 'costos', 'cierre'];

const FORM_VACIO: HitoAuditoriaPayload = {
  seccion: 'logistica', orden_hito: 1, etiqueta: '', campo_dato: '', campo_ancla: null, dias_esperados: 0,
};

@Component({
  selector: 'app-importaciones-hitos-auditoria',
  standalone: true,
  imports: [CommonModule, RouterModule, FormsModule, HomeBarComponent],
  templateUrl: './importaciones-hitos-auditoria.component.html',
  styleUrl: './importaciones-hitos-auditoria.component.css',
})
export class ImportacionesHitosAuditoriaComponent implements OnInit {
  readonly SECCIONES = SECCIONES;

  hitos: HitoAuditoria[] = [];
  cargando = true;
  error = '';

  modalAbierto = false;
  editandoId: number | null = null;
  guardando = false;
  errorForm = '';
  form: HitoAuditoriaPayload = { ...FORM_VACIO };

  constructor(private svc: HitosAuditoriaService) {}

  ngOnInit(): void {
    this.cargar();
  }

  cargar(): void {
    this.cargando = true;
    this.svc.listar().subscribe({
      next: (res) => { this.hitos = res; this.cargando = false; },
      error: () => { this.error = 'No se pudieron cargar los hitos de auditoría.'; this.cargando = false; },
    });
  }

  hitosPorSeccion(seccion: string): HitoAuditoria[] {
    return this.hitos.filter((h) => h.seccion === seccion).sort((a, b) => a.orden_hito - b.orden_hito);
  }

  abrirNuevo(): void {
    this.editandoId = null;
    this.form = { ...FORM_VACIO };
    this.errorForm = '';
    this.modalAbierto = true;
  }

  abrirEdicion(h: HitoAuditoria): void {
    this.editandoId = h.id;
    this.form = {
      seccion: h.seccion, orden_hito: h.orden_hito, etiqueta: h.etiqueta,
      campo_dato: h.campo_dato, campo_ancla: h.campo_ancla, dias_esperados: h.dias_esperados,
    };
    this.errorForm = '';
    this.modalAbierto = true;
  }

  cerrarModal(): void {
    if (this.guardando) return;
    this.modalAbierto = false;
  }

  formValido(): boolean {
    return !!this.form.seccion && !!this.form.etiqueta.trim() && !!this.form.campo_dato.trim();
  }

  guardar(): void {
    if (!this.formValido() || this.guardando) return;
    this.guardando = true;
    this.errorForm = '';

    const payload: HitoAuditoriaPayload = {
      ...this.form,
      orden_hito: Number(this.form.orden_hito) || 1,
      dias_esperados: Number(this.form.dias_esperados) || 0,
      campo_ancla: this.form.campo_ancla?.trim() || null,
    };

    const obs = this.editandoId ? this.svc.actualizar(this.editandoId, payload) : this.svc.crear(payload);

    obs.subscribe({
      next: () => { this.guardando = false; this.modalAbierto = false; this.cargar(); },
      error: (err) => { this.guardando = false; this.errorForm = err?.error?.error || 'No se pudo guardar el hito.'; },
    });
  }

  eliminar(h: HitoAuditoria): void {
    if (!confirm(`¿Eliminar el hito "${h.etiqueta}"?\nLos embarques dejarán de auditar este campo hasta que se configure de nuevo.`)) return;
    this.svc.eliminar(h.id).subscribe({
      next: () => this.cargar(),
      error: () => { this.error = 'No se pudo eliminar el hito.'; },
    });
  }
}
```

- [x] **Step 2: Write the template**

```html
<!-- importaciones-hitos-auditoria.component.html -->
<app-home-bar></app-home-bar>

<div class="panel-page">
  <div class="panel-page-header">
    <h2>Hitos de auditoría de llenado</h2>
    <button class="btn-primary" (click)="abrirNuevo()">+ Nuevo hito</button>
  </div>

  <div class="wiz-error" *ngIf="error">{{ error }}</div>
  <div *ngIf="cargando">Cargando…</div>

  <div *ngIf="!cargando">
    <div class="hito-grupo" *ngFor="let seccion of SECCIONES">
      <h3 class="hito-grupo-titulo">{{ seccion | titlecase }}</h3>
      <table class="tabla-mini" *ngIf="hitosPorSeccion(seccion).length; else sinHitos">
        <thead>
          <tr><th>Orden</th><th>Etiqueta</th><th>Campo</th><th>Ancla</th><th>Días</th><th></th></tr>
        </thead>
        <tbody>
          <tr *ngFor="let h of hitosPorSeccion(seccion)">
            <td>{{ h.orden_hito }}</td>
            <td>{{ h.etiqueta }}</td>
            <td><span class="cli-clave">{{ h.campo_dato }}</span></td>
            <td>{{ h.campo_ancla || 'Alta de embarque' }}</td>
            <td class="num">{{ h.dias_esperados }}</td>
            <td>
              <button type="button" class="link-accion" (click)="abrirEdicion(h)">Editar</button>
              <button type="button" class="link-accion" (click)="eliminar(h)">Eliminar</button>
            </td>
          </tr>
        </tbody>
      </table>
      <ng-template #sinHitos><p class="asig-vacio">Sin hitos en esta sección.</p></ng-template>
    </div>
  </div>
</div>

<div class="panel-overlay" *ngIf="modalAbierto" (click)="cerrarModal()">
  <div class="panel" (click)="$event.stopPropagation()">
    <div class="panel-header">
      <h2 class="panel-titulo">{{ editandoId ? 'Editar hito' : 'Nuevo hito' }}</h2>
      <button class="panel-close" (click)="cerrarModal()"><i class="fas fa-times"></i></button>
    </div>

    <div class="wiz-campo">
      <label>Sección</label>
      <select [(ngModel)]="form.seccion">
        <option *ngFor="let s of SECCIONES" [value]="s">{{ s | titlecase }}</option>
      </select>
    </div>
    <div class="wiz-fila">
      <div class="wiz-campo"><label>Orden dentro de la sección</label>
        <input type="number" [(ngModel)]="form.orden_hito" min="1" /></div>
      <div class="wiz-campo"><label>Días esperados desde el ancla</label>
        <input type="number" [(ngModel)]="form.dias_esperados" min="0" /></div>
    </div>
    <div class="wiz-campo">
      <label>Etiqueta (texto a mostrar)</label>
      <input type="text" [(ngModel)]="form.etiqueta" placeholder="Ej: 18. Contenedores" />
    </div>
    <div class="wiz-campo">
      <label>Columna real que se audita (campo_dato)</label>
      <input type="text" [(ngModel)]="form.campo_dato" placeholder="Ej: log_contenedor" />
    </div>
    <div class="wiz-campo">
      <label>Columna ancla (vacío = Alta de embarque)</label>
      <input type="text" [(ngModel)]="form.campo_ancla" placeholder="Ej: log_fecha_entrega" />
    </div>

    <div class="wiz-error" *ngIf="errorForm">{{ errorForm }}</div>

    <div class="wiz-acciones">
      <button class="btn-secundario" [disabled]="guardando" (click)="cerrarModal()">Cancelar</button>
      <button class="btn-primary" [disabled]="!formValido() || guardando" (click)="guardar()">
        {{ guardando ? 'Guardando…' : 'Guardar' }}
      </button>
    </div>
  </div>
</div>
```

- [x] **Step 3: Add minimal CSS**

```css
/* importaciones-hitos-auditoria.component.css */
.panel-page { padding: 24px; }
.panel-page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.hito-grupo { margin-bottom: 28px; }
.hito-grupo-titulo { color: #e2e8f0; font-size: 14px; text-transform: uppercase; letter-spacing: .4px; margin-bottom: 8px; }
```

- [x] **Step 4: Register the route**

In `EB_FRONT/src/app/app.routes.ts`, add the import near line 61:

```typescript
import { ImportacionesHitosAuditoriaComponent } from './views/internal-views/importaciones/importaciones-hitos-auditoria/importaciones-hitos-auditoria.component';
```

and the route right after line 178 (`tiempos-estimados`):

```typescript
  { path: 'importaciones/hitos-auditoria',   component: ImportacionesHitosAuditoriaComponent,  canActivate: [adminGuard] },
```

- [x] **Step 5: Verify manually in the browser**

Run: `ng serve`, navigate to `/importaciones/hitos-auditoria` as an admin user, confirm the 20 seeded hitos (Task 3 Step 5) render grouped by section, and that creating/editing/deleting one round-trips correctly against the local backend.

- [x] **Step 6: Commit**

```bash
git add EB_FRONT/src/app/views/internal-views/importaciones/importaciones-hitos-auditoria/ EB_FRONT/src/app/app.routes.ts
git commit -m "feat(importaciones): pantalla de administracion de hitos de auditoria"
```

---

## Task 8: Suite completa y despliegue

**Files:** none new — verification only.

- [x] **Step 1: Run the full backend test suite**

Run: `pytest tests/ -q`
Expected: same pass/fail count as before this project started, plus all new `test_importaciones_auditoria.py` tests passing (no regressions in unrelated pre-existing failures documented elsewhere in this repo's history).

- [x] **Step 2: Run the full frontend test suite**

Run: `ng test --watch=false` (from `EB_FRONT`)
Expected: all specs pass, including the two new/extended service specs from Task 5.

- [x] **Step 3: Manual smoke test in `ng serve` per repo convention**

Open an embarque that has data in several sections, confirm: existing tabs still save/load exactly as before (this project must not have touched any existing section's save path other than adding the history-capture side effect), the new Auditoría tab renders, the new admin screen CRUDs correctly.

- [x] **Step 4: Deploy** — only after the user explicitly confirms, following this session's established deploy process (push to `main`, SSH to the backend EC2, `git pull`, `python -c 'import app'` smoke test, `sudo systemctl restart flaskapp`; build and ship the frontend to the frontend EC2 per its existing process). Do not deploy without that explicit confirmation.
