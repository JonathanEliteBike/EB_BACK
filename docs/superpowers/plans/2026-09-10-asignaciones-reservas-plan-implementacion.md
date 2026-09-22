# Plan de implementación — Reservas por mes objetivo

**Módulo:** Asignaciones de Importaciones · ramas `sdd-asig-back` / `sdd-asig-front`
**Fecha:** 2026-09-10
**Estado:** en implementación — Task 1 y Task 2 hechas y commiteadas; Task 3–7 pendientes.
**Restricciones vigentes:** todo en la rama `sdd-asig-back`; sin merge a `main`; sin push a GitHub.

---

## 0. Dónde vive todo esto

| Artefacto | Ubicación | Notas |
|---|---|---|
| **Spec formal aprobada** | `docs/superpowers/specs/2026-09-10-asignaciones-reservas-por-mes-design.md` | commit `b2c1e29`. Incluido íntegro en la Parte C de este documento. |
| **Este plan de implementación** | `docs/superpowers/plans/2026-09-10-asignaciones-reservas-plan-implementacion.md` | el archivo que estás leyendo. |
| **Bitácora de avance (ledger)** | `.superpowers/sdd/2026-09-09-asignaciones-importaciones/progress.md` | local (gitignored). Historial libre de toda la iniciativa Asignaciones. |
| **Historial de commits** | `git log sdd-asig-back` | Parte B de este documento. |
| **Reportes por task** | conversación de trabajo (chat) | se resumen en la Parte A. |

---

## Parte A — Plan por tasks

Cada task se cierra con: archivos tocados · qué se implementó · pruebas · commit · pendientes.

### Task 1 — Esquema de reservas + demanda neta mensual  ·  ✅ HECHA  ·  `a0a559d`

**Alcance**
- `importacion_asignaciones` adaptada a reservas: `+mes_objetivo (DATE)`, `+origen (INICIAL|REASIGNACION)`, `+confirmada_at/por`; `estado` ENUM ampliado a `RESERVADA / PENDIENTE_CONFIRMACION / CONFIRMADA / RECHAZADA / CANCELADA`; índice único `uq_reserva (producto, cliente, mes_objetivo, origen)`.
- `importacion_movimientos`: `+RESERVA`, `+REASIGNACION`, `+RECHAZO_RESERVA`.
- `_migrar_esquema_reservas(cursor)` — migración idempotente vía `information_schema`, cableada a `POST /importaciones/asignaciones/inicializar-tablas`. Crea `uq_reserva` **antes** de soltar el índice viejo (lo necesita la FK sobre `importacion_producto_id`).
- Helpers de calendario en `asignaciones_service.py`: `_split_periodo`, `_columna_a_fecha`, `_fecha_a_columna`, `_meses_en_ventana`, `_meses_reasignables`; constantes `MESES_ORDEN`, `MESES_DESPACHADOS`.
- `proyecciones_service.demanda_neta_por_cliente_mensual(periodo, skus_norm)` — proyección por mes menos Odoo descontado cronológicamente (mes más antiguo primero). La `demanda_neta_por_cliente` anual queda intacta.
- Lecturas y `asignar()` migradas al set de estados vigentes (`RESERVADA/PENDIENTE_CONFIRMACION/CONFIRMADA`); `asignar()` escribe `estado=RESERVADA`, movimiento `RESERVA`, `origen=INICIAL`.

**Archivos:** `services/asignaciones_service.py`, `services/proyecciones_service.py`, `routes/asignaciones_importaciones.py`, `tests/test_asignaciones_service.py`.

**Pruebas:** 112 passed (asignaciones + proyecciones + clientes). Nuevas: helpers de calendario, demanda mensual con deducción Odoo, migración idempotente contra BD real.

**Pendiente que dejó:** `asignar()` aún no recibía `mes_objetivo` (se resolvió en Task 2).

---

### Task 2 — Reparto inicial por ventana de meses (cliente-mayor)  ·  ✅ HECHA  ·  `d1ad189`

**Alcance**
- `recalcular_propuesta(importacion_id, mes_desde, mes_hasta, periodo_filtro=None)`:
  - `mes_desde` / `mes_hasta` aceptan `"YYYY-MM"` o nombre de mes; sin ellos → `AsignacionesError("VENTANA_REQUERIDA", 400)`.
  - Reparto **cliente-mayor / prioridad absoluta**: se llena por completo al cliente de prioridad 1 recorriendo sus meses en orden cronológico, antes de pasar al siguiente.
  - Netea cada `(cliente, mes)` contra `_reservas_vigentes_por_mes` — un `SELECT ... GROUP BY clave_cliente, mes_objetivo` por `(sku_norm, periodo)`, sumando reservas `RESERVADA/PENDIENTE_CONFIRMACION/CONFIRMADA` de todos los embarques.
  - Respuesta por producto: `{producto_id, sku, descripcion, periodo, cantidad_embarcada, disponible, proyecciones_disponibles, ventana:{desde,hasta}, propuesta:[{clave_cliente, nombre_cliente, prioridad, meses:[{mes, proyectado, vigente, sugerido}], proyectado_total, sugerido_total, faltante_total}], sobrante_estimado}`.
  - Degradación: si las proyecciones fallan → `proyecciones_disponibles=false`, `propuesta=[]`.
- `asignar(producto_id, reservas, ...)`: acepta `mes_objetivo` por item (`"YYYY-MM"` / nombre de mes / omitido = NULL) y `proyectado`/`cantidad_proyectada`. Upsert por `(producto, cliente, mes_objetivo, origen='INICIAL')` con `mes_objetivo <=> %s`. Movimiento `RESERVA` con `metadata={mes_objetivo}`. Conserva `FOR UPDATE` + `STOCK_INSUFICIENTE`.
- Helpers nuevos: `_fecha_a_ym`, `_reservas_vigentes_por_mes`, `_ordenar_clientes_por_prioridad`.
- Rutas: `POST .../recalcular` body `{mes_desde, mes_hasta, periodo?}`; `POST .../productos/<pid>/asignar` **y alias `.../reservar`** body `{reservas:[...]}`.

**Archivos:** `services/asignaciones_service.py`, `routes/asignaciones_importaciones.py`, `tests/test_asignaciones_service.py`, `tests/test_asignaciones_routes.py`.

**Pruebas:** 117 passed (asignaciones + proyecciones + clientes). Suite completa: 144 passed, 2 failed preexistentes y ajenos (`test_integrales` / `test_monitor_odoo`, dependen de Odoo). 2 e2e reales nuevas: `recalcular` exige ventana; `reservar` por mes verifica fila + movimiento + disponible contra la BD.

**Pendiente que dejó:** el frontend todavía llama `recalcular` con `{periodo}` → responde 400 hasta la fase de frontend (Task 6).

---

### Task 3 — Reasignación a meses anteriores  ·  ⏳ PENDIENTE

**Alcance (spec §5.4, §7.2)**
- `proponer_reasignacion(importacion_id, ventana_desde, periodo_filtro=None)`:
  - `meses_reasig` = meses de `MESES_ORDEN` anteriores a `ventana_desde`, **excluyendo `mayo/junio/julio`**. Para `2026-2027` con `ventana_desde=diciembre` → `[agosto, septiembre, octubre, noviembre]`.
  - Por cada producto con `disponible > 0`: faltante real por `(cliente, mes)` = `max(0, proyección_neta − reservas_vigentes(todos los embarques))`.
  - Reparto por **prioridad del cliente primero; dentro del cliente, meses pendientes en orden cronológico** (un P1 con `ago=5, sep=5` recibe las 10 antes de pasar a P2).
  - Respuesta con `propuesta[].origen="REASIGNACION"`.
- `confirmar_reasignacion(producto_id, reservas, usuario_id, importacion_id)`: crea filas `estado=PENDIENTE_CONFIRMACION`, `origen=REASIGNACION`, `mes_objetivo=<mes anterior>`, movimiento `REASIGNACION (−cantidad)`. Mismo candado `FOR UPDATE` + `STOCK_INSUFICIENTE`.
- Rutas nuevas: `POST /importaciones/<id>/asignaciones/reasignar`, `POST /importaciones/<id>/asignaciones/productos/<pid>/reasignar`.

**Pruebas previstas:** exclusión `may/jun/jul`; rango `agosto..(ventana_desde−1)`; orden prioridad→cronológico; neteo cruzado entre embarques; e2e real.

---

### Task 4 — Ciclo de confirmación con ventas  ·  ⏳ PENDIENTE

**Alcance (spec §4.3, §7.2)**
- `resolver_reserva(reserva_id, decision, usuario_id=None, importacion_id=None)`:
  - `decision="ACEPTADA"` → `estado=CONFIRMADA`, `confirmada_at/por`. **Sin movimiento** (el stock ya estaba apartado).
  - `decision="RECHAZADA"` → `estado=RECHAZADA` + movimiento `RECHAZO_RESERVA (+cantidad)`. Las unidades vuelven a disponible = sobrante.
  - Solo sobre `PENDIENTE_CONFIRMACION`; si no → `AsignacionesError("RESERVA_NO_PENDIENTE", 409)`. Historial intacto.
- `cancelar_asignacion` adaptado a los estados nuevos: acepta `RESERVADA / PENDIENTE_CONFIRMACION / CONFIRMADA` → `CANCELADA` + `LIBERACION`; rechaza si ya `CANCELADA` / `RECHAZADA`.
- Ruta nueva: `POST /importaciones/<id>/asignaciones/reservas/<rid>/resolver` body `{decision}`.

**Pruebas previstas:** `ACEPTADA`→`CONFIRMADA` sin movimiento; `RECHAZADA`→`RECHAZADA` + `RECHAZO_RESERVA`; error sobre estado no pendiente; `cancelar_asignacion` con cada estado; e2e real.

---

### Task 5 — KPIs de reserva en las lecturas  ·  ⏳ PENDIENTE

**Alcance (spec §6)**
- `listar_productos`, `resumen_embarque`, `obtener_detalle_producto`, `resumen_global`, `listar_productos_global`:
  - `reservado_inicial` (Σ `RESERVADA`), `reservado_reasignacion_pendiente` (Σ `PENDIENTE_CONFIRMACION`), `reservado_confirmado` (Σ `CONFIRMADA`), `unidades_reservadas` (suma de los tres).
  - `unidades_disponibles` = ledger (sin cambio). `unidades_sobrantes = max(unidades_embarcadas − unidades_reservadas, 0)`.
- Cada fila de reserva del detalle: `clave_cliente`, `mes_objetivo`, `origen`, `estado`, `cantidad_asignada` (Reservado), `cantidad_proyectada` (Proyectado), `faltante = max(0, proyectado − reservado)`.

**Pruebas previstas:** rollups de KPI con estados mezclados; forma de las filas de reserva.

---

### Task 6 — Frontend (`sdd-asig-front`)  ·  ⏳ PENDIENTE

**Alcance (spec §8)**
- `asignaciones-importacion.service.ts`: interfaces (`PropuestaMensual`, `ReservaFila`, `ReasignacionPropuesta`) y métodos `recalcular(id, mesDesde, mesHasta)`, `reservar(id, pid, reservas)`, `proponerReasignacion(id, ventanaDesde)`, `confirmarReasignacion(id, pid, reservas)`, `resolverReserva(id, rid, decision)`.
- Panel de detalle `asignaciones-detalle-producto`:
  - Pestaña **Proyecciones**: selector de rango de meses (dos `<select>` con los 12 meses del periodo), botón **"Reservar"** (antes "Confirmar asignación"), propuesta con **Proyectado / Reservado / Faltante** y desglose por mes.
  - Botón **"Reasignar a meses anteriores"** → `/reasignar` + tabla de propuesta (badge `Reasignación`) + confirmar.
  - Pestaña **Reservas**: `cliente · mes objetivo · origen (badge) · proyectado · reservado · faltante · estado`; en `PENDIENTE_CONFIRMACION`, botones **"Cliente aceptó" / "Cliente rechazó"**.
  - Pestaña **Historial**: incluye `RESERVA / REASIGNACION / RECHAZO_RESERVA`.
- Panel consolidado del dashboard (`asignaciones-panel`): columna "Reservado" + badge de pendientes de confirmación.

**Verificación:** `ng serve` compila limpio (HMR). `ng test` no arranca en el repo (specs preexistentes rotos) → revisión + compilación + specs nuevos donde apliquen.

> **Nota:** en la rama `sdd-asig-front` ya hay trabajo previo sin commitear de la sesión de revisión (panel consolidado, columna descripción, buscador de cliente, rediseño del dropdown). Se ordena/commitea junto con esta task o cuando lo autorices.

---

### Task 7 — e2e integral contra los ejemplos numéricos  ·  ⏳ PENDIENTE

**Alcance (spec §10)**
- Sembrar 2 embarques del mismo SKU + filas `forecast_proyecciones` + pedidos Odoo simulados.
- Correr `recalcular → reservar → reasignar → confirmar_reasignacion → resolver(ACEPTADA/RECHAZADA)`.
- Verificar KPIs, ledger y estados en cada paso contra los números del §9 (Parte C).

---

## Parte B — Historial de commits (`sdd-asig-back`)

```
d1ad189  2026-09-10  feat(asignaciones): reparto inicial por ventana de meses (cliente-mayor)   [Task 2]
a0a559d  2026-09-10  feat(asignaciones): esquema de reservas por mes + demanda neta mensual      [Task 1]
395811d  2026-09-10  fix(importaciones): filtro de origenes descarta bytes corruptos (U+FFFD)    [trabajo previo]
8eaad7f  2026-09-10  feat(asignaciones): panel consolidado de asignaciones (backend)             [trabajo previo]
b2c1e29  2026-09-10  docs: spec de reservas por mes objetivo + reasignacion + confirmacion       [spec aprobada]
```

Backup local (sin subir): rama `backup-pre-split-1789067556` con el estado previo a la reorganización de commits.

**Diff acumulado de la iniciativa** (`b2c1e29^..HEAD`): 7 archivos, ~1735 inserciones / 168 borrados.

| Archivo | Δ |
|---|---|
| `docs/superpowers/specs/2026-09-10-asignaciones-reservas-por-mes-design.md` | +587 |
| `services/asignaciones_service.py` | +622 / cambios |
| `services/proyecciones_service.py` | +68 |
| `routes/asignaciones_importaciones.py` | +62 |
| `routes/importaciones.py` | +8 |
| `tests/test_asignaciones_service.py` | +415 / cambios |
| `tests/test_asignaciones_routes.py` | +141 |

---

## Parte C — Spec técnica completa (aprobada)

> Contenido íntegro de `docs/superpowers/specs/2026-09-10-asignaciones-reservas-por-mes-design.md`.

<!-- SPEC -->

---

## Parte D — Estado actual y pendientes

### Hecho
- Esquema de reservas migrado en la BD local (idempotente).
- Demanda neta mensual con deducción Odoo cronológica.
- Reparto inicial por ventana de meses, cliente-mayor / prioridad absoluta, con neteo de reservas vigentes de todos los embarques.
- Rutas `recalcular` (con ventana) y `reservar`.
- 117 pruebas verdes en el set del módulo (2 fallos ajenos y preexistentes en la suite completa, dependientes de Odoo).

### Pendiente
- **Task 3** — reasignación (`proponer_reasignacion`, `confirmar_reasignacion`, rutas).
- **Task 4** — `resolver_reserva` (ACEPTADA/RECHAZADA→sobrante) + `cancelar_asignacion` con estados nuevos + ruta.
- **Task 5** — KPIs de reserva en las 5 lecturas + filas de reserva con proyectado/reservado/faltante.
- **Task 6** — frontend completo (servicio + panel de detalle + panel consolidado).
- **Task 7** — e2e integral contra los ejemplos del §9.

### Riesgos / notas abiertas
1. Frontend desincronizado hasta Task 6: "Recalcular propuesta" en la UI responde `400 VENTANA_REQUERIDA`.
2. Datos de prueba que dejan los e2e reales en la BD local (`RESV-*`, `TEST-*`, etc.) — se limpian al cerrar la revisión, como la vez pasada.
3. `mayo/junio/julio` nunca reciben reserva ni reasignación (decisión #4).
4. Sin merge ni push hasta autorización explícita.
