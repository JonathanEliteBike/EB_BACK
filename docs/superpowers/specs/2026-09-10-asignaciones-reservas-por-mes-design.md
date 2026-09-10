# Spec — Reservas por mes objetivo, reasignación y confirmación con ventas

Fecha: 2026-09-10
Módulo: Asignaciones de Importaciones (`sdd-asig-back` / `sdd-asig-front`)
Estado: **pendiente de aprobación del usuario** — no implementar hasta el visto bueno.

---

## 1. Contexto y objetivo

Hoy el submódulo "Asignaciones de Importaciones" reparte el stock de un embarque
entre clientes por prioridad, tomando la proyección **anual** del periodo
(`forecast_proyecciones`, suma de los 12 meses) menos lo confirmado en Odoo, y
crea filas en `importacion_asignaciones` (estado `ACTIVA`).

El negocio necesita algo más fino:

1. El producto de un embarque se **reserva para meses concretos** (p. ej. un
   embarque que llega en septiembre se reserva para octubre, noviembre y
   diciembre), no para "todo el periodo".
2. Tras el reparto puede sobrar stock. Si hay clientes de la lista de prioridad
   con proyecciones de **meses anteriores** todavía sin cubrir, ese sobrante se
   puede **reasignar** a esas proyecciones anteriores, otra vez por prioridad.
3. Las reservas por reasignación son **tentativas**: ventas contacta al cliente.
   Si acepta, la reserva se mantiene; si rechaza, la unidad se **libera a
   sobrante** y queda registrada históricamente.

`importacion_asignaciones` se **adapta** para representar estas reservas. No se
crea una arquitectura paralela. `importacion_movimientos` sigue siendo la única
fuente de verdad de la disponibilidad.

---

## 2. Glosario

| Término | Definición |
|---|---|
| **Ventana** | rango de meses `[mes_desde .. mes_hasta]` que el usuario elige para el reparto inicial de un embarque. |
| **Proyección neta** | `forecast_proyecciones` del cliente/SKU para un mes, menos la porción de pedidos Odoo confirmados que "cae" en ese mes (regla cronológica, §5.2). |
| **Reserva** | fila de `importacion_asignaciones`: unidades de un embarque apartadas para `(cliente, SKU, mes objetivo)`, con `origen` y `estado`. |
| **Reserva vigente** | reserva en estado `RESERVADA`, `PENDIENTE_CONFIRMACION` o `CONFIRMADA`. Las `RECHAZADA` y `CANCELADA` no son vigentes. |
| **Proyectado** | proyección neta del cliente para el mes / la ventana. |
| **Reservado** | unidades del embarque efectivamente apartadas para el cliente ese mes. |
| **Faltante** | `Proyectado − (reservas vigentes + lo reservado en esta operación)`. Necesidad real todavía sin cubrir. |
| **Meses despachados** | `mayo`, `junio`, `julio` (`MESES_PASADOS_DIST` en `proyecciones_my27.py`). Informativos: nunca reciben reserva ni reasignación. |

---

## 3. Decisiones cerradas (acordadas con el usuario)

1. **Periodo.** `2026-2027` = **mayo 2026 → abril 2027**. Columnas de mes en orden
   cronológico: `mayo, junio, julio, agosto, septiembre, octubre, noviembre,
   diciembre, enero, febrero, marzo, abril` (`routes/proyecciones_my27.py:34`).
2. **Prioridad = cliente-mayor / absoluta.** Se atiende por completo al cliente de
   prioridad 1, luego al 2, etc. Dentro de cada cliente, sus meses se atienden en
   **orden cronológico**. No se usa la lógica "trimestre-mayor" de Proyecciones MY27.
3. **Odoo.** Los pedidos confirmados consumen la proyección **del mes más antiguo
   al más reciente** (regla ya existente, `proyecciones_my27.py:1273-1293`).
4. **Reasignación.** Manual, mismo periodo. Puede usar meses anteriores a la
   ventana, **excluyendo siempre `mayo/junio/julio`**. Para `2026-2027`: de
   `agosto` hasta el mes inmediatamente anterior al `mes_desde` de la ventana.
5. **Orden de reasignación.** Prioridad del cliente primero; dentro del cliente,
   sus meses pendientes en orden cronológico. Un cliente P1 con faltantes
   `agosto=5` y `septiembre=5` recibe esas 10 antes de pasar al cliente P2,
   aunque P2 tenga necesidad en agosto.
6. **Una reserva por `(cliente, mes_objetivo, origen)`.** Un cliente puede tener
   varias filas (octubre, noviembre, diciembre…).
7. **Proyectado / Reservado / Faltante** visibles en la propuesta y en el detalle
   de reservas.
8. **Ciclo completo en esta entrega:** asignación inicial + reasignación +
   `PENDIENTE_CONFIRMACION` + aceptada/rechazada + liberación a sobrante +
   trazabilidad.

---

## 4. Modelo de datos

### 4.1 `importacion_productos` — sin cambios

`sku`, `sku_norm`, `descripcion`, `cantidad_embarcada`, `periodo` ya existen. La
columna de descripción ya se muestra en la tabla de productos y en el panel de
detalle (cambio ya aplicado en frontend).

### 4.2 `importacion_asignaciones` → tabla de **reservas** (ALTER)

Columnas nuevas / modificadas:

| Columna | Definición | Nota |
|---|---|---|
| `mes_objetivo` | `DATE NULL` | día 1 del mes objetivo (`2026-10-01`). NULL solo en reservas legacy previas a esta feature. |
| `origen` | `ENUM('INICIAL','REASIGNACION') NOT NULL DEFAULT 'INICIAL'` | de qué paso salió la reserva. |
| `estado` | `ENUM('RESERVADA','PENDIENTE_CONFIRMACION','CONFIRMADA','RECHAZADA','CANCELADA') NOT NULL DEFAULT 'RESERVADA'` | reemplaza `ENUM('ACTIVA','CANCELADA')`. |
| `confirmada_at` | `DATETIME NULL` | fecha de la resolución con ventas (aceptada o rechazada). |
| `confirmada_por` | `INT NULL` | usuario que resolvió. |
| `cantidad_asignada` | *(sin cambio de tipo)* | = cantidad reservada. |
| `cantidad_proyectada` | *(sin cambio)* | snapshot del Proyectado neto del cliente para ese mes al momento de reservar. |
| `prioridad` | *(sin cambio)* | snapshot de la prioridad del cliente. |

Índice único: se reemplaza
`uq_asignacion_producto_cliente (importacion_producto_id, clave_cliente)`
por
`uq_reserva (importacion_producto_id, clave_cliente, mes_objetivo, origen)`.

> MySQL permite múltiples `NULL` en un índice único, así que las reservas legacy
> con `mes_objetivo NULL` no colisionan.

### 4.3 Máquina de estados de la reserva

```
                 reservar (paso inicial)
                        │
                        ▼
                   [RESERVADA] ───────── cancelar ──────────► [CANCELADA]   (+ LIBERACION)
                        ▲
                        │ (no aplica confirmación: es la propia proyección del cliente
                        │  para su ventana objetivo)
   confirmar_reasignacion
                        │
                        ▼
            [PENDIENTE_CONFIRMACION] ──── resolver: ACEPTADA ──► [CONFIRMADA]   (sin movimiento)
                        │                                             │
                        │                                             └── cancelar ──► [CANCELADA] (+ LIBERACION)
                        ├──── resolver: RECHAZADA ──► [RECHAZADA]   (+ RECHAZO_RESERVA)
                        └──── cancelar ────────────► [CANCELADA]    (+ LIBERACION)
```

- **Vigentes** (cuentan como demanda cubierta y descuentan disponible vía ledger):
  `RESERVADA`, `PENDIENTE_CONFIRMACION`, `CONFIRMADA`.
- **Terminales no vigentes:** `RECHAZADA`, `CANCELADA`. Nunca se borran (historia).
- Las reservas `INICIAL` no pasan por `PENDIENTE_CONFIRMACION`: nacen `RESERVADA`.
- Solo las `REASIGNACION` nacen `PENDIENTE_CONFIRMACION` y pueden `resolver`.

### 4.4 `importacion_movimientos` — ampliar ENUM (ALTER)

Se agregan tres tipos; los existentes se conservan:

| `tipo_movimiento` | Signo | Cuándo | Estado que lo genera |
|---|---|---|---|
| `RESERVA` | `−cantidad` | paso inicial aparta stock | crear reserva `INICIAL` |
| `REASIGNACION` | `−cantidad` | reasignación aparta stock hacia un mes anterior | crear reserva `REASIGNACION` |
| `RECHAZO_RESERVA` | `+cantidad` | el cliente rechazó la reasignación | `resolver(RECHAZADA)` |
| `LIBERACION` *(ya existe)* | `+cantidad` | cancelación manual de una reserva | `cancelar` |
| `AJUSTE` *(ya existe)* | ± | corrección de cantidad_embarcada | `actualizar_producto` |

`_disponible_producto(producto)` = `SUM(cantidad)` sobre todos los movimientos del
producto — **sin cambios**, sigue dando el saldo correcto.

`resolver(ACEPTADA)` **no** genera movimiento: el stock ya estaba apartado por el
`REASIGNACION` previo; solo cambia el estado de la reserva.

### 4.5 Migración de esquema

Función `_migrar_esquema_reservas(cursor)` (nueva, en `asignaciones_service.py`),
ejecutada desde la ruta ya existente `POST /importaciones/asignaciones/inicializar-tablas`.
MySQL no soporta `ADD COLUMN IF NOT EXISTS`, así que introspecciona
`information_schema` y aplica cada paso solo si falta:

1. `ADD COLUMN mes_objetivo DATE NULL` (si no existe).
2. `ADD COLUMN origen ENUM('INICIAL','REASIGNACION') NOT NULL DEFAULT 'INICIAL'`.
3. `ADD COLUMN confirmada_at DATETIME NULL`, `ADD COLUMN confirmada_por INT NULL`.
4. `MODIFY estado ENUM('ACTIVA','RESERVADA','PENDIENTE_CONFIRMACION','CONFIRMADA','RECHAZADA','CANCELADA') NOT NULL DEFAULT 'RESERVADA'`
   → `UPDATE importacion_asignaciones SET estado='RESERVADA' WHERE estado='ACTIVA'`
   → `MODIFY estado ENUM('RESERVADA','PENDIENTE_CONFIRMACION','CONFIRMADA','RECHAZADA','CANCELADA') NOT NULL DEFAULT 'RESERVADA'`.
5. `MODIFY tipo_movimiento` en `importacion_movimientos` agregando `RESERVA`,
   `REASIGNACION`, `RECHAZO_RESERVA` (conservando todos los actuales).
6. `DROP INDEX uq_asignacion_producto_cliente`, `ADD UNIQUE KEY uq_reserva
   (importacion_producto_id, clave_cliente, mes_objetivo, origen)`.

Las reservas legacy (datos de prueba previos, ninguno en producción) quedan con
`mes_objetivo NULL`, `origen='INICIAL'`, `estado='RESERVADA'`. La UI las muestra
como "sin mes". `TABLAS_SQL` se actualiza para que instalaciones nuevas ya creen
la forma final.

---

## 5. Algoritmos

### 5.1 Meses, columnas y ventana

```
MESES_ORDEN = [mayo, junio, julio, agosto, septiembre, octubre,
               noviembre, diciembre, enero, febrero, marzo, abril]   # cronológico
MESES_DESPACHADOS = {mayo, junio, julio}                              # nunca reciben stock
```

Helpers nuevos:
- `_columna_a_fecha(periodo, "octubre") -> date(2026, 10, 1)` — `mayo..diciembre`
  usan `year1`; `enero..abril` usan `year2` (periodo `"2026-2027"` → `year1=2026`, `year2=2027`).
- `_fecha_a_columna(date(2026,10,1)) -> "octubre"`.
- `_meses_en_ventana(mes_desde, mes_hasta) -> ["octubre","noviembre","diciembre"]`
  (subsecuencia cronológica de `MESES_ORDEN`).

### 5.2 Proyección neta mensual (Odoo cronológico)

Nueva función en `proyecciones_service.py`:
`demanda_neta_por_cliente_mensual(periodo, skus_norm) -> {sku_norm: {clave_cliente: {mes: neta}}}`

```
por cada fila de forecast_proyecciones (periodo, sku, cliente):
    proy[mes] = columna <mes>            para los 12 meses de MESES_ORDEN

odoo_total = _get_ordenes_my27(periodo)[cliente].get(sku_norm, 0)   # unidades confirmadas
restante = odoo_total
por mes en MESES_ORDEN:                                             # del más antiguo al más nuevo
    ded        = min(restante, proy[mes])
    proy[mes] -= ded
    restante  -= ded
    si restante == 0: break
# proy = proyección NETA de Odoo, por mes
```

La función `demanda_neta_por_cliente` actual (suma anual) se **conserva
intacta** para no tocar otros consumidores; la nueva es aditiva.

### 5.3 Reparto inicial — cliente-mayor (`recalcular_propuesta`)

Firma nueva:
`recalcular_propuesta(importacion_id, mes_desde, mes_hasta, periodo_filtro=None)`.

```
ventana = _meses_en_ventana(mes_desde, mes_hasta)          # excluye implícitamente may/jun/jul si no están en el rango
por cada producto del embarque (opcionalmente filtrado por periodo):
    neta = demanda_neta_por_cliente_mensual(producto.periodo, [producto.sku_norm])[sku_norm]
    disponible = _disponible_producto(producto)            # embarcado − ya reservado (ledger)
    restante   = disponible

    clientes = [c con Σ(neta[c][m] for m in ventana) > 0]
    clientes.sort(key = (prioridad_de(c), c))              # PRIORIDAD ABSOLUTA

    sugerido = {}                                          # sugerido[c][mes]
    por cliente c en clientes:                             # cliente-mayor
        por mes en ventana:                                # cronológico dentro del cliente
            proy_neta = neta[c].get(mes, 0)
            if proy_neta <= 0 or restante <= 0: continue
            ya = reservas_vigentes(c, sku_norm, periodo, mes)   # §5.5 — todos los embarques
            faltante_real = max(0, proy_neta - ya)
            alloc = min(faltante_real, restante)
            if alloc > 0:
                sugerido[c][mes] = alloc
                restante -= alloc

    propuesta[producto] = por cada c:
        {
          clave_cliente, nombre_cliente, prioridad,
          meses: [ {mes, proyectado: neta[c][mes], vigente: reservas_vigentes(...), sugerido: sugerido[c].get(mes,0)} ],
          proyectado_total: Σ neta[c][ventana],
          sugerido_total:   Σ sugerido[c],
          faltante_total:   proyectado_total − vigente_total − sugerido_total
        }
    sobrante_estimado = restante
```

### 5.4 Reparto de reasignación (`proponer_reasignacion`)

Firma: `proponer_reasignacion(importacion_id, ventana_desde, periodo_filtro=None)`
donde `ventana_desde` = `mes_desde` de la ventana ya trabajada.

```
meses_reasig = [m in MESES_ORDEN : m ∉ MESES_DESPACHADOS  AND  m < ventana_desde]
               # para 2026-2027 y ventana_desde = diciembre → [agosto, septiembre, octubre, noviembre]

por cada producto con _disponible_producto(producto) > 0:
    neta    = demanda_neta_por_cliente_mensual(periodo, [sku_norm])[sku_norm]
    restante = _disponible_producto(producto)

    clientes = [c con algún faltante en meses_reasig], ordenados por (prioridad, clave)
    por cliente c en clientes:                             # PRIORIDAD ABSOLUTA
        por mes en meses_reasig:                           # cronológico dentro del cliente
            proy_neta = neta[c].get(mes, 0)
            ya        = reservas_vigentes(c, sku_norm, periodo, mes)   # todos los embarques
            faltante  = max(0, proy_neta - ya)
            if faltante <= 0 or restante <= 0: continue
            alloc = min(faltante, restante)
            propuesta_reasig[c][mes] = alloc
            restante -= alloc
```

Cumple la decisión #5: un cliente P1 con faltantes `agosto=5`, `septiembre=5`
recibe las 10 antes de que el algoritmo pase al siguiente cliente.

`confirmar_reasignacion(producto_id, reservas, usuario_id, importacion_id)` crea
filas `estado='PENDIENTE_CONFIRMACION'`, `origen='REASIGNACION'`,
`mes_objetivo=<mes anterior>`, movimiento `REASIGNACION (−cantidad)`. Mismo
candado `SELECT ... FOR UPDATE` + chequeo `STOCK_INSUFICIENTE` que `reservar`.

### 5.5 Fórmula del faltante y no-doble-conteo

Para `(cliente, sku_norm, periodo, mes)`:

```
proyeccion_mes  = Σ forecast_proyecciones.<mes>        (filas del cliente+sku+periodo)
odoo_en_mes     = porción del pedido Odoo del cliente+sku que cae en ese mes,
                  según la deducción cronológica de §5.2   (se reparte UNA vez)
reservas_vigentes_mes =
    SELECT COALESCE(SUM(a.cantidad_asignada), 0)
    FROM importacion_asignaciones a
    JOIN importacion_productos p ON p.id = a.importacion_producto_id
    WHERE a.clave_cliente = :cliente
      AND a.mes_objetivo  = :mes                 -- DATE día 1
      AND p.sku_norm      = :sku_norm
      AND p.periodo       = :periodo
      AND a.estado IN ('RESERVADA','PENDIENTE_CONFIRMACION','CONFIRMADA')

faltante_mes = max(0, proyeccion_mes − odoo_en_mes − reservas_vigentes_mes)
```

Garantías contra doble conteo:

1. **Odoo** se reparte una sola vez, cronológicamente → un número "Odoo cae en
   este mes". Nunca se resta el total de Odoo a cada mes.
2. **Reservas**: cada reserva es una fila con clave única
   `(producto, cliente, mes_objetivo, origen)`; el `SUM` la cuenta una vez. Los
   estados `RECHAZADA` y `CANCELADA` quedan fuera → una reserva rechazada deja de
   "tapar" la necesidad y vuelve a aparecer como faltante para el siguiente embarque.
3. En una misma corrida de reparto, `restante` se decrementa en memoria conforme
   se asigna; y si se vuelve a "Recalcular" para el mismo embarque, sus propias
   filas ya cuentan como vigentes → la nueva propuesta sale en 0 para lo ya reservado.

> **Nota de implementación (rendimiento):** en vez de una query por
> `(cliente, mes)`, la reasignación precarga **un** `SELECT` agregado
> `GROUP BY clave_cliente, mes_objetivo` para el `(sku_norm, periodo)` y lo
> resuelve en memoria. Para 41 productos × 27 clientes × ~4 meses es holgado.

---

## 6. KPIs

`resumen_embarque`, `listar_productos`, `obtener_detalle_producto`,
`resumen_global`, `listar_productos_global` exponen:

| Campo | Cálculo |
|---|---|
| `unidades_embarcadas` | Σ `cantidad_embarcada` |
| `reservado_inicial` | Σ `cantidad_asignada` WHERE `estado='RESERVADA'` |
| `reservado_reasignacion_pendiente` | Σ WHERE `estado='PENDIENTE_CONFIRMACION'` |
| `reservado_confirmado` | Σ WHERE `estado='CONFIRMADA'` |
| `unidades_reservadas` | suma de los tres anteriores |
| `unidades_disponibles` | `SUM(importacion_movimientos.cantidad)` (ledger) |
| `unidades_sobrantes` | `max(unidades_embarcadas − unidades_reservadas, 0)` |
| `unidades_vendidas` | sin cambio (`importacion_sobrantes_ventas` VALIDADO/PENDIENTE_VALIDACION) |

Cada fila de reserva en el detalle incluye: `clave_cliente`, `mes_objetivo`,
`origen`, `estado`, `cantidad_asignada` (Reservado), `cantidad_proyectada`
(Proyectado), y `faltante` calculado = `max(0, proyectado − reservado)` para ese
mes.

---

## 7. API

### 7.1 Endpoints modificados

| Ruta | Cambio |
|---|---|
| `POST /importaciones/<id>/asignaciones/recalcular` | body: `{ mes_desde:"2026-10", mes_hasta:"2026-12", periodo?:"2026-2027" }`. Respuesta: array de `{ producto_id, sku, descripcion, periodo, cantidad_embarcada, disponible, proyecciones_disponibles, ventana:{desde,hasta}, propuesta:[ {clave_cliente, nombre_cliente, prioridad, meses:[{mes,proyectado,vigente,sugerido}], proyectado_total, sugerido_total, faltante_total} ], sobrante_estimado }`. |
| `POST /importaciones/<id>/asignaciones/productos/<pid>/asignar` <br>(+ alias `/reservar`) | body: `{ reservas:[ {clave_cliente, mes_objetivo:"2026-10", cantidad, proyectado?} ] }`. Crea filas `RESERVADA`/`INICIAL`; movimiento `RESERVA`. Upsert por `(producto,cliente,mes,'INICIAL')`. Candado `FOR UPDATE` + `STOCK_INSUFICIENTE` sumando por `(cliente,mes)`. |
| `GET /importaciones/<id>/asignaciones` · `.../productos` · `.../productos/<pid>/detalle` · `.../asignaciones/embarques` · `.../asignaciones/productos` | KPIs de §6; filas de reserva con `mes_objetivo`, `origen`, `estado`, `proyectado`, `faltante`. |
| `POST /importaciones/<id>/asignaciones/productos/<pid>/asignaciones/<aid>/cancelar` | acepta `RESERVADA`, `PENDIENTE_CONFIRMACION`, `CONFIRMADA` → `CANCELADA` + `LIBERACION`. Rechaza si ya `CANCELADA`/`RECHAZADA` (`RESERVA_YA_CERRADA`). |

### 7.2 Endpoints nuevos

| Ruta | body | efecto |
|---|---|---|
| `POST /importaciones/<id>/asignaciones/reasignar` | `{ ventana_desde:"2026-12", periodo?:"2026-2027" }` | propuesta de reasignación (§5.4). Respuesta con `propuesta[].origen="REASIGNACION"` y `meses[].mes` en `agosto..(ventana_desde−1)`. |
| `POST /importaciones/<id>/asignaciones/productos/<pid>/reasignar` | `{ reservas:[ {clave_cliente, mes_objetivo, cantidad, proyectado?} ] }` | crea filas `PENDIENTE_CONFIRMACION`/`REASIGNACION`; movimiento `REASIGNACION`. |
| `POST /importaciones/<id>/asignaciones/reservas/<rid>/resolver` | `{ decision:"ACEPTADA" \| "RECHAZADA" }` | `ACEPTADA` → `CONFIRMADA`, `confirmada_at/por`, sin movimiento. `RECHAZADA` → `RECHAZADA` + `RECHAZO_RESERVA (+cantidad)`. Solo sobre `PENDIENTE_CONFIRMACION` (si no: `RESERVA_NO_PENDIENTE`, 409). |

Todos los endpoints mantienen `@token_required` + `@_requiere_rol_importaciones`
y el contrato `{"ok": bool, ...}`.

### 7.3 Funciones de servicio

**Modificar** (`asignaciones_service.py`):
`recalcular_propuesta` · `asignar` (→ semántica "reservar", nuevo parámetro
`mes_objetivo`) · `cancelar_asignacion` · `listar_productos` · `resumen_embarque`
· `obtener_detalle_producto` · `resumen_global` · `listar_productos_global` ·
`TABLAS_SQL`.

**Modificar** (`proyecciones_service.py`): agregar
`demanda_neta_por_cliente_mensual(periodo, skus_norm)`
(la anual `demanda_neta_por_cliente` se conserva sin cambios).

**Nuevas** (`asignaciones_service.py`): `_migrar_esquema_reservas` ·
`_columna_a_fecha` · `_fecha_a_columna` · `_meses_en_ventana` ·
`_reservas_vigentes_por_mes` (SELECT agregado) · `proponer_reasignacion` ·
`confirmar_reasignacion` · `resolver_reserva`.

**Nuevas** (`routes/asignaciones_importaciones.py`): `reasignar_ruta` ·
`confirmar_reasignacion_ruta` · `resolver_reserva_ruta`.

---

## 8. Frontend (`sdd-asig-front`)

Componente `asignaciones-detalle-producto` (panel de detalle):

- **Pestaña "Proyecciones"**: selector de rango de meses (`mes_desde` / `mes_hasta`,
  dos `<select>` con los 12 meses del periodo). Botón **"Reservar"** (antes
  "Confirmar asignación"). La propuesta muestra por fila **Proyectado / Reservado
  / Faltante** y el desglose por mes.
- **Botón "Reasignar a meses anteriores"** → llama a `/reasignar`, muestra la
  propuesta de reasignación (badge `Reasignación`, meses anteriores) y un botón
  para confirmarla.
- **Pestaña "Asignaciones" → "Reservas"**: tabla con `cliente · mes objetivo ·
  origen (badge Inicial/Reasignación) · proyectado · reservado · faltante ·
  estado`. En filas `PENDIENTE_CONFIRMACION`: botones **"Cliente aceptó"** /
  **"Cliente rechazó"** → `/reservas/<id>/resolver`.
- **Pestaña "Historial"**: ya muestra movimientos; ahora incluye `RESERVA`,
  `REASIGNACION`, `RECHAZO_RESERVA`.

Servicio `asignaciones-importacion.service.ts`: interfaces nuevas
(`PropuestaMensual`, `ReservaFila`, `ReasignacionPropuesta`) y métodos
`recalcular(id, mesDesde, mesHasta)`, `reservar(id, pid, reservas)`,
`proponerReasignacion(id, ventanaDesde)`, `confirmarReasignacion(id, pid, reservas)`,
`resolverReserva(id, rid, decision)`.

El **panel consolidado del dashboard** (`asignaciones-panel`) usa los KPIs nuevos
sin cambios estructurales (columna "Reservado" en vez de "Asignado", + badge de
pendientes de confirmación).

---

## 9. Ejemplos numéricos validados

**Datos base.** SKU `BIKE-X`, periodo `2026-2027`.
Prioridades: `CLI-A=1`, `CLI-B=2`, `CLI-C=3`, `CLI-E=5`.

Proyección mensual (unidades) y Odoo confirmado:

| Cliente | oct | nov | dic | Odoo |
|---|---|---|---|---|
| CLI-A | 5 | 4 | 3 | 4 |
| CLI-B | 6 | 0 | 2 | 0 |
| CLI-C | 0 | 10 | 0 | 0 |
| CLI-E | 2 | 2 | 6 | 8 |

**Proyección neta** (Odoo cronológico, §5.2):
- CLI-A: Odoo 4 tapa `oct 5→1` → **oct 1, nov 4, dic 3**.
- CLI-B: **oct 6, dic 2**.
- CLI-C: **nov 10**.
- CLI-E: Odoo 8 tapa `oct 2 + nov 2 + dic 4` → **dic 2**.

### 9.1 Primera asignación

**Embarque IMP-1**, llega septiembre, `BIKE-X` embarcado = **20**. Ventana
elegida: **octubre–diciembre**. Reparto cliente-mayor (§5.3):

| Cliente | mes | Proyectado | Reservado | Faltante | restante |
|---|---|---|---|---|---|
| CLI-A | oct | 1 | 1 | 0 | 19 |
| CLI-A | nov | 4 | 4 | 0 | 15 |
| CLI-A | dic | 3 | 3 | 0 | 12 |
| CLI-B | oct | 6 | 6 | 0 | 6 |
| CLI-B | dic | 2 | 2 | 0 | 4 |
| CLI-C | nov | 10 | **4** | **6** | 0 |
| CLI-E | dic | 2 | 0 | 2 | 0 |

Se crean 6 filas `RESERVADA`/`INICIAL` (CLI-A×3, CLI-B×2, CLI-C×1) + 6
movimientos `RESERVA (−)`. Total `RESERVA` = −20.
IMP-1: embarcadas 20 · reservadas 20 · disponibles 0 · sobrantes 0.

### 9.2 Cliente que no alcanza producto

CLI-C: Proyectado nov 10, Reservado 4, **Faltante 6** — dentro de la ventana. No
dispara reasignación; queda registrado y espera el siguiente embarque. CLI-E:
Faltante dic 2, igual.

### 9.3 Reasignación a meses anteriores

**Embarque IMP-2**, llega noviembre, `BIKE-X` embarcado = **15**. Ventana inicial
elegida: **diciembre–abril**.

Paso inicial IMP-2, demanda neta en `dic..abr` **descontando reservas vigentes de
IMP-1** (§5.5):
- dic: CLI-A `3 − 3(IMP-1) = 0`; CLI-B `2 − 2 = 0`; CLI-E `2 − 0 = 2`.
- ene–abr: 0 en este ejemplo.

→ CLI-E dic: reserva **2**. Sobra **13**. (1 fila `RESERVADA`/`INICIAL`,
movimiento `RESERVA −2`.)

**Botón "Reasignar a meses anteriores"** con `ventana_desde = diciembre`.
`meses_reasig = [agosto, septiembre, octubre, noviembre]` (excluye may/jun/jul).
Faltante real por `(cliente, mes)`:
- noviembre: CLI-C `10 − 0(Odoo) − 4(IMP-1) = 6`.
- octubre: CLI-A `1 − 1 = 0`; CLI-B `6 − 6 = 0` → cubierto.
- agosto/septiembre: sin proyección → 0.

Reparto de 13 por prioridad, mes más antiguo primero → CLI-C noviembre: **6**.
Sobran 7.

Se crea 1 fila: `IMP-2 · CLI-C · mes_objetivo=2026-11-01 · origen=REASIGNACION ·
estado=PENDIENTE_CONFIRMACION · cantidad_asignada=6 · cantidad_proyectada=6` +
movimiento `REASIGNACION −6`.
IMP-2: embarcadas 15 · reservadas 8 (2 CLI-E + 6 CLI-C) · disponibles 7 · sobrantes 7.

### 9.4 Cliente acepta la reasignación

`POST /importaciones/<IMP-2>/asignaciones/reservas/<rid>/resolver
{"decision":"ACEPTADA"}`
→ reserva CLI-C nov: `estado=CONFIRMADA`, `confirmada_at`, `confirmada_por`.
**Sin movimiento.** IMP-2: reservadas 8 · disponibles 7. La reserva queda firme.

### 9.5 Cliente rechaza → vuelve a sobrante

`{"decision":"RECHAZADA"}`
→ reserva CLI-C nov: `estado=RECHAZADA` + movimiento `RECHAZO_RESERVA +6`.
IMP-2: disponibles 7 → **13**, reservadas 8 → 2.
Historial conserva `REASIGNACION −6` **y** `RECHAZO_RESERVA +6`. Las 6 unidades
quedan como sobrante de IMP-2 y entran al flujo de venta-sobrante ya existente.
La fila de reserva permanece `RECHAZADA` (histórica, no se borra).

### 9.6 Varios embarques del mismo SKU

Ilustrado en 9.3: IMP-2 netea contra las reservas vigentes de IMP-1 tanto en su
paso inicial (`dic: 3−3=0`, `2−2=0`) como en la reasignación (`nov: 10−4=6`, no
10). Sin doble conteo porque cada reserva es una fila sumada una vez y solo
cuentan `RESERVADA/PENDIENTE_CONFIRMACION/CONFIRMADA`. Si CLI-C rechaza la de
IMP-2 (9.5), esas 6 salen de "vigentes" y un IMP-3 futuro vuelve a ver el
faltante de 6 en noviembre.

---

## 10. Plan de pruebas

**Backend — unitarias (cursor mockeado):**
- `demanda_neta_por_cliente_mensual`: deducción Odoo cronológica, incluyendo
  "spill" de Odoo a la ventana.
- Reparto inicial cliente-mayor: prioridad absoluta; orden cronológico dentro del
  cliente; corte por `restante`; neteo de reservas vigentes de otros embarques.
- Reasignación: exclusión `may/jun/jul`; rango `agosto..(ventana_desde−1)`; orden
  prioridad→cronológico (caso P1 `ago=5, sep=5` antes que P2).
- `resolver_reserva`: `ACEPTADA`→`CONFIRMADA` sin movimiento; `RECHAZADA`→
  `RECHAZADA` + `RECHAZO_RESERVA`; error sobre estado no `PENDIENTE_CONFIRMACION`.
- `cancelar_asignacion` con los estados nuevos.
- Fórmula de faltante y KPIs.
- `_migrar_esquema_reservas` idempotente (correr dos veces).

**Backend — e2e (BD local):**
- Sembrar 2 embarques del mismo SKU + filas `forecast_proyecciones` + pedidos
  Odoo simulados; correr `recalcular → reservar → reasignar → confirmar_reasignacion
  → resolver(ACEPTADA/RECHAZADA)`; verificar KPIs, ledger y estados en cada paso
  contra los números de §9.

**Frontend:**
- Servicio: métodos nuevos, params y shapes de respuesta.
- Componente: render del selector de meses; propuesta con Proyectado/Reservado/
  Faltante; flujo de reasignación; botones Aceptó/Rechazó sobre
  `PENDIENTE_CONFIRMACION`.
- `ng serve` compila limpio (HMR). `ng test` no arranca en este repo (specs
  preexistentes rotos) → verificación por revisión + compilación.

---

## 11. Fuera de alcance

- Reasignación **entre periodos** (solo mismo periodo).
- Lógica "trimestre-mayor" de Proyecciones MY27 (este módulo usa cliente-mayor).
- Cambios al flujo de venta-sobrante (`importacion_sobrantes_ventas`): se reutiliza
  tal cual — una unidad liberada por rechazo entra a disponible y de ahí al flujo
  actual sin modificaciones.
- `mayo/junio/julio`: nunca reciben reserva ni reasignación.
- Deducción de Odoo "mes lejano primero" (se descartó; se usa "mes antiguo primero").

---

## 12. Supuestos y riesgos

| # | Supuesto / riesgo | Mitigación |
|---|---|---|
| 1 | `_get_ordenes_my27` usa ventana de fechas `year1−1-07-01 … year2-04-30` (22 meses para 2026-2027). No define el mapeo de meses de proyección. | Confirmado con el equipo: `mayo` de `2026-2027` = mayo 2026. El mapeo lo da el orden cronológico de columnas. |
| 2 | Reservas legacy con `mes_objetivo NULL` (datos de prueba previos). | Se conservan como "sin mes"; no rompen el índice único (MySQL admite múltiples NULL). Nada en producción. |
| 3 | Reasignación con N consultas de "vigentes". | Un solo `SELECT` agregado `GROUP BY cliente, mes` por `(sku_norm, periodo)`, resuelto en memoria. |
| 4 | Concurrencia en `reservar` / `confirmar_reasignacion` / `resolver`. | `SELECT ... FOR UPDATE` sobre la fila del producto + chequeo `_disponible_producto` antes de escribir movimientos negativos (mismo patrón que `asignar` hoy). |
| 5 | `forecast_proyecciones` sin datos para un SKU del embarque. | La propuesta sale vacía para ese producto con `proyecciones_disponibles=false` (comportamiento actual conservado). |

---

## 13. Cambios ya aplicados (previos a este spec, en revisión)

- Columna **Descripción** visible en la tabla de productos del embarque y en el
  encabezado del panel de detalle.
- Buscador de cliente (combobox) en la pestaña Proyecciones.
- Panel consolidado de Asignaciones como pestaña del dashboard de Importaciones
  (vista por embarque + vista por SKU).
- Fix de texto corrupto en el filtro de orígenes del dashboard.

Estos quedan como están; este spec solo agrega el flujo de reservas por mes.
