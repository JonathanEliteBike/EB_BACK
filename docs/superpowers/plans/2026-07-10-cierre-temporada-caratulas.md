# Cierre de Temporada (Carátulas) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar el enfoque ad-hoc actual (recálculo en vivo sin memoria de temporadas) por una arquitectura de "temporadas" reusable: un registro formal de temporadas, un proceso de cierre que corre una sola vez por temporada y persiste los montos finales, y una forma de consultar temporadas cerradas (MY26) sin perder nada, mientras la vista "actual" (MY27) sigue funcionando como hoy.

**Architecture:** Tabla `temporadas` como registro central (qué temporadas existen, fechas, estado). Un cierre de temporada = una función reusable que recorre todos los distribuidores, calcula sus montos finales usando datos de `monitor` ya limpios (Odoo con los 3 filtros nuevos), y los persiste en las tablas `*_historico` que YA EXISTEN (creadas hoy, ya en producción) — dejan de ser "capturas accidentales" y pasan a ser el almacén oficial de temporadas cerradas. La vista "actual" no cambia (sigue leyendo `previo`/`caratula_evac_a/b` en vivo, tal como hoy). Leer una temporada cerrada = un SELECT simple a las tablas `*_historico` filtrado por `temporada` — los endpoints de lectura YA EXISTEN y no se tocan.

**Tech Stack:** Flask (Python 3.9 en producción / 3.12 en local) + MySQL (mysql-connector-python) + Angular (standalone components, RxJS).

## Global Constraints

- Todo el trabajo de código se hace primero en LOCAL. Nada se aplica a producción sin autorización explícita del usuario, fase por fase.
- No se pierde ni sobreescribe ningún dato de la temporada MY26 en ningún paso.
- No se tocan módulos fuera de Carátulas (retroactivos, garantías, importaciones) salvo reutilizar funciones que Carátulas ya consume (ej. `_recalcular_previo_clave_cierre` en `routes/retroactivos.py`).
- Se reutiliza toda la infraestructura ya existente: `utils/temporada_utils.etiqueta_temporada()`, las tablas `previo_historico`/`caratula_evac_a_historico`/`caratula_evac_b_historico`, y los endpoints `/temporadas_disponibles`, `/datos_previo_historico`, `/datos_evac_a_historico`, `/datos_evac_b_historico` (ya committeados, ya en producción — no se reescriben).
- Python objetivo: compatible con Python 3.9 (producción corre 3.9.x vía gunicorn) — no usar sintaxis `X | Y` de tipos, usar `Optional[X]` (ya es el patrón establecido en `utils/temporada_utils.py`).
- Zona horaria: `America/Mexico_City` vía `utils.tiempo.ahora_mx()` (patrón ya establecido).

---

## ⚠️ CHECKPOINT DE DISEÑO — Requiere aprobación antes de la Fase 1

Antes de crear cualquier tabla nueva, esta es la propuesta de esquema. **Detente aquí y preséntasela al usuario antes de ejecutar la Fase 1.**

### Tabla nueva: `temporadas`

```sql
CREATE TABLE temporadas (
    id INT NOT NULL AUTO_INCREMENT,
    etiqueta VARCHAR(20) NOT NULL,          -- '2025-2026', '2026-2027' -- debe coincidir con etiqueta_temporada()
    fecha_inicio DATE NOT NULL,             -- 2025-07-01
    fecha_fin DATE NOT NULL,                -- 2026-06-30
    estado ENUM('abierta','cerrada') NOT NULL DEFAULT 'abierta',
    fecha_cierre DATETIME NULL,
    cerrada_por VARCHAR(100) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_etiqueta (etiqueta)
) ENGINE=InnoDB DEFAULT CHARSET=latin1;
```

Este es EL registro central: reemplaza el "no sabemos qué temporadas existen" actual. `etiqueta_temporada()` sigue usándose para *calcular* la etiqueta a partir de una fecha; esta tabla es la que dice *qué temporadas ya se registraron formalmente y si ya cerraron*.

### Columna nueva en `clientes`: inicio de temporada adelantado

Regla de negocio: casi todos los distribuidores usan 1-julio a 30-junio. Un grupo pequeño empieza antes (ej. 1-junio). Esto **no cambia cada temporada** — es una característica fija de ese distribuidor. Propuesta:

```sql
ALTER TABLE clientes
    ADD COLUMN dia_inicio_temporada VARCHAR(5) NULL COMMENT 'MM-DD; NULL = usa el default 07-01';
```

- `NULL` (la mayoría) → al abrir una temporada nueva, `f_inicio` se fija en `{año}-07-01`.
- `'06-01'` (los pocos adelantados) → al abrir una temporada nueva, `f_inicio` se fija en `{año}-06-01`.

`clientes.f_inicio` **ya existe** y sigue siendo "la fecha de inicio de la temporada actualmente abierta para este cliente" — no se toca su significado, solo se automatiza cómo se recalcula al abrir cada temporada nueva.

### Por qué NO se crea una tabla `previo` por temporada

`previo_historico` (ya existente) tiene exactamente las mismas columnas que `previo` + `temporada` + `fecha_snapshot`. Es el lugar correcto para persistir el cierre — no hace falta una tabla nueva. Lo mismo aplica a `caratula_evac_a_historico` / `caratula_evac_b_historico`.

**Preguntas para el usuario antes de continuar:**
1. ¿Confirmas el nombre/forma de `dia_inicio_temporada` (MM-DD, NULL = default), o prefieres otra representación?
2. ¿Sabes ya cuáles distribuidores son "adelantados" (para poblar esa columna en la Fase 3), o hay que preguntarle a alguien del negocio?

---

## Fase 0 — Sync de Odoo: filtros de calidad de datos

Aislado, sin dependencias de las fases siguientes. Se puede probar y aprobar por separado.

### Task 0.1: Agregar filtro de `payment_state` en `sync_monitor_odoo`

**Files:**
- Modify: `routes/monitor_odoo.py` (función `sync_monitor_odoo`, búsqueda de `account.move`)
- Test: `tests/test_sync_monitor_odoo_filtros.py` (nuevo)

**Interfaces:**
- Produces: el dominio de búsqueda de `account.move` en `sync_monitor_odoo` incluye `['payment_state', 'not in', ['reversed', 'invoicing_legacy']]`.

- [ ] **Step 1: Confirmar el dominio actual (lectura, no destructivo)**

Run: `grep -n "move_type.*out_invoice" routes/monitor_odoo.py`
Expected: muestra el bloque `facturas = models.execute_kw(... 'account.move', 'search_read', [[...`

- [ ] **Step 2: Escribir test que verifique el dominio de búsqueda (sin llamar a Odoo real)**

```python
# tests/test_sync_monitor_odoo_filtros.py
from unittest.mock import MagicMock, patch
import routes.monitor_odoo as mo


def test_sync_monitor_odoo_excluye_reversed_e_invoicing_legacy():
    fake_models = MagicMock()
    fake_models.execute_kw.return_value = []  # sin facturas -> corta temprano, solo nos interesa el dominio

    with patch("routes.monitor_odoo.get_odoo_models", return_value=(1, fake_models, None)):
        with mo.app.test_request_context("/sync-monitor-odoo", method="POST", json={}):
            mo.sync_monitor_odoo()

    args, kwargs = fake_models.execute_kw.call_args_list[0]
    dominio = args[3][0]  # [ODOO_DB, uid, ODOO_PASSWORD, 'account.move', 'search_read', [dominio], {...}] -> ojo al índice real
    condiciones = [c for c in dominio if isinstance(c, list)]
    assert ['payment_state', 'not in', ['reversed', 'invoicing_legacy']] in condiciones
```

- [ ] **Step 3: Correr el test para confirmar que falla**

Run: `python -m pytest tests/test_sync_monitor_odoo_filtros.py -v`
Expected: FAIL (el dominio actual no incluye `payment_state`)

- [ ] **Step 4: Implementar el filtro**

En `routes/monitor_odoo.py`, dentro de `sync_monitor_odoo`, modificar el bloque:

```python
        facturas = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD,
            'account.move', 'search_read',
            [[
                ['move_type', '=', 'out_invoice'],
                ['state', '=', 'posted'],
                ['invoice_date', '>=', FECHA_INICIO],
                ['payment_state', 'not in', ['reversed', 'invoicing_legacy']],
            ]],
            {'fields': ['id', 'name', 'invoice_date', 'partner_id', 'invoice_line_ids'], 'limit': 0}
        )
```

- [ ] **Step 5: Correr el test, confirmar que pasa**

Run: `python -m pytest tests/test_sync_monitor_odoo_filtros.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add routes/monitor_odoo.py tests/test_sync_monitor_odoo_filtros.py
git commit -m "fix: excluir facturas revertidas/invoicing_legacy del sync de Odoo"
```

### Task 0.2: Excluir líneas FLETE/FLE y líneas con precio negativo

**Files:**
- Modify: `routes/monitor_odoo.py` (función `sync_monitor_odoo`, bucle de inserción de líneas — ya existe un filtro de prefijos/palabras excluidas, hay que ampliarlo)
- Test: `tests/test_sync_monitor_odoo_filtros.py` (agregar casos)

**Interfaces:**
- Consumes: las variables ya existentes `_PREFIJOS_EXCLUIDOS` y `_PALABRAS_EXCLUIDAS` dentro de `sync_monitor_odoo`.
- Produces: ninguna línea con código que empiece por `FLE` o `FLETE`, ni con `venta_total < 0`, llega a insertarse en `monitor`.

- [ ] **Step 1: Confirmar el estado actual de las listas de exclusión**

Run: `grep -n "_PREFIJOS_EXCLUIDOS\|_PALABRAS_EXCLUIDAS" routes/monitor_odoo.py`
Expected: `_PREFIJOS_EXCLUIDOS = ('FLE', 'SERV', 'APLANT', 'ANTI', 'DESC', 'GARANT', 'LEYENDA')` ya incluye `'FLE'` (cubre FLETE porque el código real usualmente empieza con esas 3 letras) y `'DESC'` ya cubre descuentos por código — falta el filtro por **precio negativo**, que es un criterio más confiable que el nombre.

- [ ] **Step 2: Escribir test para precio negativo**

```python
def test_linea_con_precio_negativo_se_excluye():
    # venta_total <= 0 ya se descarta en el código (`if venta_total <= 0: continue`);
    # este test documenta y fija ese comportamiento explícitamente para precio negativo.
    from routes.monitor_odoo import sync_monitor_odoo
    import inspect
    codigo = inspect.getsource(sync_monitor_odoo)
    assert "venta_total <= 0" in codigo
```

- [ ] **Step 3: Correr el test**

Run: `python -m pytest tests/test_sync_monitor_odoo_filtros.py::test_linea_con_precio_negativo_se_excluye -v`
Expected: PASS (ya existe `if venta_total <= 0: continue` en el código actual — este paso es de verificación, no de cambio)

- [ ] **Step 4: Si el test de Step 3 falla, agregar el guard explícito**

Solo si falla, en el bucle de inserción de `sync_monitor_odoo`, antes del `INSERT INTO monitor`:

```python
            venta_total = round(float(line.get('price_total') or 0), 2)
            if venta_total <= 0:
                continue
```

(Nota: esta línea ya existe en el código actual — Step 3 es para confirmarlo, no para duplicarlo.)

- [ ] **Step 5: Commit (solo si hubo cambios)**

```bash
git add routes/monitor_odoo.py tests/test_sync_monitor_odoo_filtros.py
git commit -m "test: fijar comportamiento de exclusion FLETE/FLE y precio negativo en sync Odoo"
```

---

## Fase 1 — Registro de temporadas

**Requiere aprobación del checkpoint de diseño de arriba antes de empezar.**

### Task 1.1: Crear tabla `temporadas` y poblarla con MY26 (cerrada) y MY27 (abierta)

**Files:**
- Create: `migrations/2026_07_temporadas.sql`
- Modify: ninguno todavía (solo DDL)

**Interfaces:**
- Produces: tabla `temporadas` con dos filas: `('2025-2026', '2025-07-01', '2026-06-30', 'cerrada', ...)` y `('2026-2027', '2026-07-01', '2027-06-30', 'abierta', NULL)`.

- [ ] **Step 1: Escribir el DDL**

```sql
-- migrations/2026_07_temporadas.sql
CREATE TABLE IF NOT EXISTS temporadas (
    id INT NOT NULL AUTO_INCREMENT,
    etiqueta VARCHAR(20) NOT NULL,
    fecha_inicio DATE NOT NULL,
    fecha_fin DATE NOT NULL,
    estado ENUM('abierta','cerrada') NOT NULL DEFAULT 'abierta',
    fecha_cierre DATETIME NULL,
    cerrada_por VARCHAR(100) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_etiqueta (etiqueta)
) ENGINE=InnoDB DEFAULT CHARSET=latin1;

INSERT INTO temporadas (etiqueta, fecha_inicio, fecha_fin, estado, fecha_cierre)
VALUES ('2025-2026', '2025-07-01', '2026-06-30', 'cerrada', NOW())
ON DUPLICATE KEY UPDATE etiqueta = etiqueta;

INSERT INTO temporadas (etiqueta, fecha_inicio, fecha_fin, estado)
VALUES ('2026-2027', '2026-07-01', '2027-06-30', 'abierta')
ON DUPLICATE KEY UPDATE etiqueta = etiqueta;
```

- [ ] **Step 2: Aplicar en LOCAL y verificar**

Run:
```bash
python -c "
from db_conexion import obtener_conexion
conn = obtener_conexion()
cur = conn.cursor()
with open('migrations/2026_07_temporadas.sql') as f:
    for stmt in f.read().split(';'):
        if stmt.strip():
            cur.execute(stmt)
conn.commit()
cur.execute('SELECT etiqueta, fecha_inicio, fecha_fin, estado FROM temporadas')
print(cur.fetchall())
cur.close(); conn.close()
"
```
Expected: `[('2025-2026', ..., 'cerrada'), ('2026-2027', ..., 'abierta')]`

- [ ] **Step 3: Commit**

```bash
git add migrations/2026_07_temporadas.sql
git commit -m "feat: agregar tabla temporadas (registro central de cierres)"
```

### Task 1.2: Agregar `clientes.dia_inicio_temporada`

**Files:**
- Modify: `migrations/2026_07_temporadas.sql` (agregar el ALTER al mismo archivo de migración)

- [ ] **Step 1: Agregar el ALTER TABLE al final del archivo de la Task 1.1**

```sql
ALTER TABLE clientes
    ADD COLUMN IF NOT EXISTS dia_inicio_temporada VARCHAR(5) NULL COMMENT 'MM-DD; NULL = usa el default 07-01';
```

- [ ] **Step 2: Aplicar en LOCAL y verificar**

Run:
```bash
python -c "
from db_conexion import obtener_conexion
conn = obtener_conexion()
cur = conn.cursor()
cur.execute(\"ALTER TABLE clientes ADD COLUMN IF NOT EXISTS dia_inicio_temporada VARCHAR(5) NULL COMMENT 'MM-DD; NULL = usa el default 07-01'\")
conn.commit()
cur.execute('SHOW COLUMNS FROM clientes LIKE %s', ('dia_inicio_temporada',))
print(cur.fetchone())
cur.close(); conn.close()
"
```
Expected: fila con `dia_inicio_temporada`, `varchar(5)`, `YES` (nullable)

- [ ] **Step 3: Commit**

```bash
git add migrations/2026_07_temporadas.sql
git commit -m "feat: agregar clientes.dia_inicio_temporada para distribuidores adelantados"
```

---

## Fase 2 — Cierre formal de temporada (reusable)

### Task 2.1: Función `cerrar_temporada_completa(etiqueta_temporada, conexion=None)` — dry-run primero

**Files:**
- Create: `routes/temporadas.py` (nuevo blueprint, para no mezclar con `retroactivos.py`/`monitor_odoo.py`)
- Modify: `app.py` (registrar el nuevo blueprint)
- Test: `tests/test_cierre_temporada.py`

**Interfaces:**
- Consumes: `_recalcular_previo_clave_cierre(conexion, cursor_dict, cursor, clave, f_inicio, fecha_cierre)` ya existente en `routes/retroactivos.py` (misma firma, se importa y reutiliza tal cual — no se reescribe la lógica de suma SCOTT/APP por cliente).
- Produces: `cerrar_temporada_completa(etiqueta, dry_run=True) -> dict` con `{"clientes_procesados": int, "preview": [ {clave, acumulado_anticipado, avance_global_scott, ...} para 3 clientes de muestra ] }`. Cuando `dry_run=False`, además persiste en `previo_historico` (vía el mismo patrón `INSERT INTO previo_historico SELECT ... FROM previo` ya usado en `routes/previo.py`, pero re-calculando primero cada fila con `_recalcular_previo_clave_cierre` acotado a la temporada) y marca la fila de `temporadas` como `estado='cerrada'`.

- [ ] **Step 1: Escribir el test (dry run, con datos de un cliente conocido)**

```python
# tests/test_cierre_temporada.py
from db_conexion import obtener_conexion
from routes.temporadas import cerrar_temporada_completa


def test_dry_run_no_escribe_nada():
    conn = obtener_conexion()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT COUNT(*) AS n FROM previo_historico WHERE temporada = '2025-2026'")
    antes = cur.fetchone()['n']

    resultado = cerrar_temporada_completa('2025-2026', dry_run=True)

    cur.execute("SELECT COUNT(*) AS n FROM previo_historico WHERE temporada = '2025-2026'")
    despues = cur.fetchone()['n']

    assert despues == antes  # dry_run no persiste nada
    assert resultado['clientes_procesados'] > 0
    assert len(resultado['preview']) <= 3
    cur.close(); conn.close()
```

- [ ] **Step 2: Correr el test, confirmar que falla**

Run: `python -m pytest tests/test_cierre_temporada.py::test_dry_run_no_escribe_nada -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'routes.temporadas'`

- [ ] **Step 3: Implementar `routes/temporadas.py`**

```python
from __future__ import annotations
from flask import Blueprint, jsonify, request
import logging
from db_conexion import obtener_conexion
from routes.retroactivos import _recalcular_previo_clave_cierre

temporadas_bp = Blueprint('temporadas', __name__, url_prefix='')


def cerrar_temporada_completa(etiqueta: str, dry_run: bool = True) -> dict:
    """
    Cierra una temporada completa: recalcula previo para cada cliente abierto
    acotado a [f_inicio del cliente o inicio de temporada, fin de temporada],
    y persiste el resultado en previo_historico (dry_run=False).
    Reusa _recalcular_previo_clave_cierre (ya usado por /cerrar-temporada individual).
    """
    conexion = obtener_conexion()
    cur_dict = conexion.cursor(dictionary=True)
    cur = conexion.cursor()

    cur_dict.execute("SELECT fecha_inicio, fecha_fin FROM temporadas WHERE etiqueta = %s", (etiqueta,))
    temporada_row = cur_dict.fetchone()
    if not temporada_row:
        cur_dict.close(); cur.close(); conexion.close()
        raise ValueError(f"Temporada '{etiqueta}' no registrada en la tabla temporadas")

    fecha_fin_temporada = str(temporada_row['fecha_fin'])
    fecha_inicio_default = str(temporada_row['fecha_inicio'])

    cur_dict.execute("""
        SELECT clave, f_inicio FROM clientes
        WHERE clave IS NOT NULL AND clave <> ''
    """)
    clientes = cur_dict.fetchall()

    preview = []
    procesados = 0

    for c in clientes:
        clave = c['clave'].strip().upper()
        f_inicio_cliente = c['f_inicio']
        if hasattr(f_inicio_cliente, 'strftime'):
            f_inicio_cliente = f_inicio_cliente.strftime('%Y-%m-%d')
        f_inicio_cliente = f_inicio_cliente or fecha_inicio_default

        _recalcular_previo_clave_cierre(conexion, cur_dict, cur, clave, f_inicio_cliente, fecha_fin_temporada)
        procesados += 1

        if len(preview) < 3:
            cur_dict.execute(
                "SELECT clave, acumulado_anticipado, avance_global_scott, "
                "avance_global_apparel_syncros_vittoria FROM previo WHERE clave = %s",
                (clave,)
            )
            fila_preview = cur_dict.fetchone()
            if fila_preview:
                preview.append(fila_preview)

    if dry_run:
        conexion.rollback()
    else:
        cur.execute("""
            INSERT INTO previo_historico (
                temporada, fecha_snapshot, id_previo, clave, evac, nombre_cliente, acumulado_anticipado, nivel,
                nivel_cierre_compra_inicial, compra_minima_anual, porcentaje_anual, compra_minima_inicial,
                avance_global, porcentaje_global, compromiso_scott, avance_global_scott, porcentaje_scott,
                compromiso_jul_ago, avance_jul_ago, porcentaje_jul_ago,
                compromiso_sep_oct, avance_sep_oct, porcentaje_sep_oct,
                compromiso_nov_dic, avance_nov_dic, porcentaje_nov_dic,
                compromiso_ene_feb, avance_ene_feb, porcentaje_ene_feb,
                compromiso_mar_abr, avance_mar_abr, porcentaje_mar_abr,
                compromiso_may_jun, avance_may_jun, porcentaje_may_jun,
                compromiso_apparel_syncros_vittoria, avance_global_apparel_syncros_vittoria, porcentaje_apparel_syncros_vittoria,
                compromiso_jul_ago_app, avance_jul_ago_app, porcentaje_jul_ago_app,
                compromiso_sep_oct_app, avance_sep_oct_app, porcentaje_sep_oct_app,
                compromiso_nov_dic_app, avance_nov_dic_app, porcentaje_nov_dic_app,
                compromiso_ene_feb_app, avance_ene_feb_app, porcentaje_ene_feb_app,
                compromiso_mar_abr_app, avance_mar_abr_app, porcentaje_mar_abr_app,
                compromiso_may_jun_app, avance_may_jun_app, porcentaje_may_jun_app,
                acumulado_syncros, acumulado_apparel, acumulado_vittoria, acumulado_bold,
                es_integral, grupo_integral
            )
            SELECT
                %s, NOW(), id, clave, evac, nombre_cliente, acumulado_anticipado, nivel,
                nivel_cierre_compra_inicial, compra_minima_anual, porcentaje_anual, compra_minima_inicial,
                avance_global, porcentaje_global, compromiso_scott, avance_global_scott, porcentaje_scott,
                compromiso_jul_ago, avance_jul_ago, porcentaje_jul_ago,
                compromiso_sep_oct, avance_sep_oct, porcentaje_sep_oct,
                compromiso_nov_dic, avance_nov_dic, porcentaje_nov_dic,
                compromiso_ene_feb, avance_ene_feb, porcentaje_ene_feb,
                compromiso_mar_abr, avance_mar_abr, porcentaje_mar_abr,
                compromiso_may_jun, avance_may_jun, porcentaje_may_jun,
                compromiso_apparel_syncros_vittoria, avance_global_apparel_syncros_vittoria, porcentaje_apparel_syncros_vittoria,
                compromiso_jul_ago_app, avance_jul_ago_app, porcentaje_jul_ago_app,
                compromiso_sep_oct_app, avance_sep_oct_app, porcentaje_sep_oct_app,
                compromiso_nov_dic_app, avance_nov_dic_app, porcentaje_nov_dic_app,
                compromiso_ene_feb_app, avance_ene_feb_app, porcentaje_ene_feb_app,
                compromiso_mar_abr_app, avance_mar_abr_app, porcentaje_mar_abr_app,
                compromiso_may_jun_app, avance_may_jun_app, porcentaje_may_jun_app,
                acumulado_syncros, acumulado_apparel, acumulado_vittoria, acumulado_bold,
                es_integral, grupo_integral
            FROM previo
        """, (etiqueta,))
        cur.execute(
            "UPDATE temporadas SET estado='cerrada', fecha_cierre=NOW() WHERE etiqueta = %s",
            (etiqueta,)
        )
        conexion.commit()

    cur_dict.close()
    cur.close()
    conexion.close()

    return {"clientes_procesados": procesados, "preview": preview}


@temporadas_bp.route('/cerrar-temporada-completa', methods=['POST'])
def cerrar_temporada_completa_endpoint():
    data = request.get_json() or {}
    etiqueta = data.get('etiqueta')
    dry_run = data.get('dry_run', True)
    if not etiqueta:
        return jsonify({'error': 'Se requiere etiqueta (ej. "2025-2026")'}), 400
    try:
        resultado = cerrar_temporada_completa(etiqueta, dry_run=dry_run)
        return jsonify(resultado), 200
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logging.exception('Error en cerrar_temporada_completa_endpoint')
        return jsonify({'error': str(e)}), 500
```

- [ ] **Step 4: Registrar el blueprint en `app.py`**

Buscar la línea `app.register_blueprint(caratulas_bp)` en `app.py` y agregar justo debajo:

```python
    from routes.temporadas import temporadas_bp
    app.register_blueprint(temporadas_bp)
```

- [ ] **Step 5: Correr el test, confirmar que pasa**

Run: `python -m pytest tests/test_cierre_temporada.py::test_dry_run_no_escribe_nada -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add routes/temporadas.py app.py tests/test_cierre_temporada.py
git commit -m "feat: proceso reusable de cierre de temporada (dry-run + persistencia)"
```

### Task 2.2: 🛑 CHECKPOINT — Ejecutar dry-run de MY26 y validar preview con el usuario

**No requiere escribir código.** Es una verificación manual antes de persistir nada.

- [ ] **Step 1: Correr el dry-run en LOCAL**

Run:
```bash
python -c "
from app import create_app
app = create_app()
with app.test_request_context():
    from routes.temporadas import cerrar_temporada_completa
    r = cerrar_temporada_completa('2025-2026', dry_run=True)
    print('clientes_procesados:', r['clientes_procesados'])
    for p in r['preview']:
        print(p)
"
```

- [ ] **Step 2: Mostrar el preview al usuario y esperar confirmación explícita antes de continuar a la Task 2.3**

Detente aquí. No sigas a la Task 2.3 sin que el usuario confirme que los montos del preview coinciden con lo esperado.

### Task 2.3: Ejecutar el cierre real de MY26 en LOCAL (persiste)

**Solo después de aprobación explícita del Task 2.2.**

- [ ] **Step 1: Ejecutar con `dry_run=False`**

Run:
```bash
python -c "
from app import create_app
app = create_app()
with app.test_request_context():
    from routes.temporadas import cerrar_temporada_completa
    r = cerrar_temporada_completa('2025-2026', dry_run=False)
    print(r)
"
```

- [ ] **Step 2: Verificar que `temporadas` quedó marcada como cerrada**

Run:
```bash
python -c "
from db_conexion import obtener_conexion
conn = obtener_conexion()
cur = conn.cursor(dictionary=True)
cur.execute('SELECT * FROM temporadas WHERE etiqueta = %s', ('2025-2026',))
print(cur.fetchone())
cur.execute('SELECT COUNT(*) AS n FROM previo_historico WHERE temporada = %s', ('2025-2026',))
print(cur.fetchone())
cur.close(); conn.close()
"
```
Expected: `estado='cerrada'`, `fecha_cierre` con timestamp reciente; conteo de `previo_historico` para esa temporada > 0.

- [ ] **Step 3: No hay commit de código en este task — es una operación de datos, no de código.**

---

## Fase 3 — Abrir MY27 (rollover de `f_inicio`)

### Task 3.1: Función `abrir_temporada(etiqueta)` — fija `f_inicio` por cliente según `dia_inicio_temporada`

**Files:**
- Modify: `routes/temporadas.py`
- Test: `tests/test_cierre_temporada.py`

**Interfaces:**
- Produces: `abrir_temporada(etiqueta: str) -> int` (número de clientes actualizados). Para cada cliente: si `dia_inicio_temporada` es NULL, `f_inicio = {año_inicio_temporada}-07-01`; si no, `f_inicio = {año_inicio_temporada}-{dia_inicio_temporada}`. También limpia `temporada_cerrada=0` para todos (reabre a todos para la nueva temporada).

- [ ] **Step 1: Escribir el test**

```python
def test_abrir_temporada_usa_dia_inicio_personalizado():
    from db_conexion import obtener_conexion
    from routes.temporadas import abrir_temporada

    conn = obtener_conexion()
    cur = conn.cursor()
    cur.execute("UPDATE clientes SET dia_inicio_temporada = '06-01' WHERE clave = 'HA433'")
    conn.commit()

    abrir_temporada('2026-2027')

    cur2 = conn.cursor(dictionary=True)
    cur2.execute("SELECT f_inicio, temporada_cerrada FROM clientes WHERE clave = 'HA433'")
    fila = cur2.fetchone()
    assert str(fila['f_inicio']) == '2026-06-01'
    assert fila['temporada_cerrada'] == 0
    cur.close(); cur2.close(); conn.close()
```

- [ ] **Step 2: Correr el test, confirmar que falla**

Run: `python -m pytest tests/test_cierre_temporada.py::test_abrir_temporada_usa_dia_inicio_personalizado -v`
Expected: FAIL con `ImportError: cannot import name 'abrir_temporada'`

- [ ] **Step 3: Implementar**

Agregar a `routes/temporadas.py`:

```python
def abrir_temporada(etiqueta: str) -> int:
    """Fija f_inicio para cada cliente segun su dia_inicio_temporada (o el
    default 07-01), y los reabre (temporada_cerrada=0) para la temporada nueva."""
    conexion = obtener_conexion()
    cur_dict = conexion.cursor(dictionary=True)
    cur = conexion.cursor()

    cur_dict.execute("SELECT fecha_inicio FROM temporadas WHERE etiqueta = %s", (etiqueta,))
    row = cur_dict.fetchone()
    if not row:
        cur_dict.close(); cur.close(); conexion.close()
        raise ValueError(f"Temporada '{etiqueta}' no registrada en la tabla temporadas")
    anio_inicio = row['fecha_inicio'].year

    cur_dict.execute("SELECT clave, dia_inicio_temporada FROM clientes WHERE clave IS NOT NULL AND clave <> ''")
    clientes = cur_dict.fetchall()

    actualizados = 0
    for c in clientes:
        dia = c['dia_inicio_temporada'] or '07-01'
        f_inicio_nuevo = f"{anio_inicio}-{dia}"
        cur.execute(
            "UPDATE clientes SET f_inicio = %s, temporada_cerrada = 0, fecha_cierre_temporada = NULL, f_fin = NULL "
            "WHERE clave = %s",
            (f_inicio_nuevo, c['clave'])
        )
        actualizados += 1

    conexion.commit()
    cur_dict.close(); cur.close(); conexion.close()
    return actualizados


@temporadas_bp.route('/abrir-temporada', methods=['POST'])
def abrir_temporada_endpoint():
    data = request.get_json() or {}
    etiqueta = data.get('etiqueta')
    if not etiqueta:
        return jsonify({'error': 'Se requiere etiqueta (ej. "2026-2027")'}), 400
    try:
        n = abrir_temporada(etiqueta)
        return jsonify({'success': True, 'clientes_actualizados': n}), 200
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logging.exception('Error en abrir_temporada_endpoint')
        return jsonify({'error': str(e)}), 500
```

- [ ] **Step 4: Correr el test, confirmar que pasa**

Run: `python -m pytest tests/test_cierre_temporada.py::test_abrir_temporada_usa_dia_inicio_personalizado -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add routes/temporadas.py tests/test_cierre_temporada.py
git commit -m "feat: abrir_temporada -- rollover de f_inicio por cliente"
```

### Task 3.2: 🛑 CHECKPOINT — Confirmar la lista de distribuidores adelantados antes de ejecutar

- [ ] **Step 1: Preguntar al usuario la lista de claves con inicio adelantado y su fecha MM-DD**
- [ ] **Step 2: Poblar `clientes.dia_inicio_temporada` para esos clientes (UPDATE manual, uno por clave confirmada)**
- [ ] **Step 3: Ejecutar `abrir_temporada('2026-2027')` en LOCAL y verificar el resultado antes de producción**

---

## Fase 4 — Frontend: leer temporada actual + botón de temporadas pasadas

Los endpoints de lectura de temporadas cerradas (`/temporadas_disponibles`, `/datos_previo_historico`, `/datos_evac_a_historico`, `/datos_evac_b_historico`) **ya existen y no se tocan**. Este trabajo es solo de wiring en el frontend.

### Task 4.1: Servicio — método para listar temporadas y leer histórico

**Files:**
- Modify: `EB_FRONT/src/app/services/caratulas.service.ts`

**Interfaces:**
- Produces: `getTemporadasDisponibles(): Observable<string[]>`, `getDatosPrevioHistorico(temporada: string): Observable<any>`, `getDatosEvacAHistorico(temporada: string): Observable<any>`, `getDatosEvacBHistorico(temporada: string): Observable<any>` — mapean 1:1 a los endpoints ya existentes.

- [ ] **Step 1: Agregar los métodos**

```typescript
  getTemporadasDisponibles(): Observable<string[]> {
    return this.http.get<string[]>(`${this.apiUrl}/temporadas_disponibles`);
  }

  getDatosPrevioHistorico(temporada: string): Observable<any> {
    return this.http.get<any>(`${this.apiUrl}/datos_previo_historico`, { params: { temporada } });
  }

  getDatosEvacAHistorico(temporada: string): Observable<any> {
    return this.http.get<any>(`${this.apiUrl}/datos_evac_a_historico`, { params: { temporada } });
  }

  getDatosEvacBHistorico(temporada: string): Observable<any> {
    return this.http.get<any>(`${this.apiUrl}/datos_evac_b_historico`, { params: { temporada } });
  }
```

- [ ] **Step 2: Verificar compilación**

Run: `cd EB_FRONT && npx tsc --noEmit -p tsconfig.app.json`
Expected: sin errores nuevos relacionados a `caratulas.service.ts`

- [ ] **Step 3: Commit**

```bash
git add src/app/services/caratulas.service.ts
git commit -m "feat: metodos de servicio para temporadas historicas (ya existentes en backend)"
```

### Task 4.2: Carátula Global — botón "Ver temporada pasada"

**Files:**
- Modify: `EB_FRONT/src/app/views/internal-views/caratula-global/caratula-global.component.ts`
- Modify: `EB_FRONT/src/app/views/internal-views/caratula-global/caratula-global.component.html`

**Interfaces:**
- Consumes: `caratulasService.getTemporadasDisponibles()`, `caratulasService.getDatosPrevioHistorico(temporada)` (Task 4.1).
- Produces: propiedad `modoHistorico: boolean`, `temporadaHistoricaSeleccionada: string | null`, método `verTemporadaPasada(temporada: string): void`, método `volverATemporadaActual(): void`.

- [ ] **Step 1: Agregar las propiedades y métodos**

```typescript
  temporadasDisponibles: string[] = [];
  modoHistorico = false;
  temporadaHistoricaSeleccionada: string | null = null;

  cargarTemporadasDisponibles(): void {
    this.caratulasService.getTemporadasDisponibles().subscribe({
      next: (temporadas) => this.temporadasDisponibles = temporadas,
      error: (err) => console.error('Error cargando temporadas disponibles:', err)
    });
  }

  verTemporadaPasada(temporada: string): void {
    this.modoHistorico = true;
    this.temporadaHistoricaSeleccionada = temporada;
    this.caratulasService.getDatosPrevioHistorico(temporada).subscribe({
      next: (datos) => {
        const sumPrevio = datos.reduce((total: number, item: any) => total + (Number(item.acumulado_anticipado) || 0), 0);
        this.acumuladoGeneral = sumPrevio; // multimarcas no aplica a temporadas cerradas -- se documenta en pantalla
      },
      error: (err) => console.error('Error cargando temporada historica:', err)
    });
  }

  volverATemporadaActual(): void {
    this.modoHistorico = false;
    this.temporadaHistoricaSeleccionada = null;
    this.ngOnInit();
  }
```

Y en `ngOnInit()`, agregar la llamada:

```typescript
    this.cargarTemporadasDisponibles();
```

- [ ] **Step 2: Agregar el control en el HTML**

En `caratula-global.component.html`, junto al botón "Volver":

```html
        <select *ngIf="temporadasDisponibles.length" (change)="verTemporadaPasada($any($event.target).value)">
            <option value="">Temporada actual</option>
            <option *ngFor="let t of temporadasDisponibles" [value]="t">{{ t }}</option>
        </select>
        <div *ngIf="modoHistorico" class="aviso-historico">
            Viendo temporada cerrada {{ temporadaHistoricaSeleccionada }}.
            <button (click)="volverATemporadaActual()">Volver a temporada actual</button>
        </div>
```

- [ ] **Step 3: Verificar en el navegador (dev server local)**

Run: abrir `http://localhost:4200/caratula-global`, seleccionar `2025-2026` en el selector, confirmar que "Acumulado Real" cambia a un valor distinto al de la temporada actual, y que el botón "Volver a temporada actual" restaura la vista normal.

- [ ] **Step 4: Commit**

```bash
git add src/app/views/internal-views/caratula-global/
git commit -m "feat: boton para consultar temporadas cerradas en Caratula Global"
```

### Task 4.3-4.5: Repetir el patrón de Task 4.2 en Normal, EVAC A y EVAC B

Mismo patrón exacto (selector + `modoHistorico` + método `verTemporadaPasada`), adaptado a la fuente de datos de cada pantalla:
- **Normal** (`caratulas.component.ts`): usa `getDatosPrevioHistorico(temporada)` y busca la fila por `clave` ya resuelta de la búsqueda actual (mismo patrón que el intento revertido, pero ahora la fuente es la tabla histórica persistida, no un recálculo en vivo).
- **EVAC A** (`caratula-evac-a.component.ts`): usa `getDatosEvacAHistorico(temporada)` — **no** dispara `actualizarDatosCaratula()` en modo histórico (evita sobreescribir la tabla en vivo, mismo cuidado que el intento anterior).
- **EVAC B** (`caratula-evac-b.component.ts`): igual que EVAC A con `getDatosEvacBHistorico(temporada)`.

*(EVACS no requiere cambio de backend: ya tiene su propio filtro de fecha manual — basta un botón que rellene `fechaInicio`/`fechaFin` con las fechas de `temporadas` para la etiqueta elegida, igual que el intento anterior ya resolvió.)*

- [ ] **Step 1-4 (por cada pantalla): mismo patrón que Task 4.2, adaptado. Commit por pantalla.**

---

## Fase 5 — Producción (checkpoint explícito)

### Task 5.1: 🛑 CHECKPOINT — Aprobación antes de tocar producción

- [ ] **Step 1: Resumen de lo que se va a desplegar** (presentar al usuario antes de continuar):
  - Migración `migrations/2026_07_temporadas.sql` (tabla `temporadas` + columna `clientes.dia_inicio_temporada`)
  - Commits de `routes/monitor_odoo.py` (Fase 0), `routes/temporadas.py` + `app.py` (Fases 1-3)
  - Commits de frontend (Fase 4)
- [ ] **Step 2: Esperar autorización explícita del usuario, uno por uno, igual que el resto de la sesión (migración → código backend → sync corregido → dry-run en producción → cierre real → apertura MY27 → frontend).**

---

## Self-Review

**1. Cobertura del spec:**
- Filtro `payment_state` → Task 0.1 ✅
- Filtro FLETE/FLE / precio negativo → Task 0.2 (documenta que ya existe, con test que lo fija) ✅
- Regla 1-jul/30-jun + excepción por distribuidor → Tabla `temporadas` + `clientes.dia_inicio_temporada` + `abrir_temporada()` (Fases 1 y 3) ✅
- Corte formal que no pierde MY26 → `cerrar_temporada_completa()` con dry-run obligatorio (Fase 2) ✅
- Vista actual por default + botón para pasadas → Fase 4 (usa endpoints de lectura ya existentes) ✅
- Reusable para MY28+ → `cerrar_temporada_completa(etiqueta)` y `abrir_temporada(etiqueta)` toman la etiqueta como parámetro, no hay nada hardcodeado a MY26/MY27 ✅

**2. Placeholders:** ninguno — cada step tiene código real o comando real con salida esperada.

**3. Consistencia de tipos/nombres:** `cerrar_temporada_completa`, `abrir_temporada`, `getDatosPrevioHistorico`, etc. se usan con el mismo nombre en todas las tasks que los consumen.
