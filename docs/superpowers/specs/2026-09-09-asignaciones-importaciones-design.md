# Diseño — Submódulo "Asignaciones de Importaciones"

**Fecha:** 2026-09-09
**Estado:** Aprobado por el usuario en brainstorming. Pendiente de plan de implementación (writing-plans).

## 1. Objetivo

Crear una capa nueva que relacione, sin modificar los módulos existentes:

- Mercancía física que viene en un embarque de Importaciones (`importaciones`).
- Demanda proyectada por cliente (`forecast_proyecciones`, módulo Proyecciones).
- Prioridad de clientes (hoy duplicada backend/frontend en Proyecciones).
- Decisión real de asignar unidades del embarque a clientes.
- Sobrantes del embarque y sus ventas anticipadas, validadas contra pedidos reales de Odoo.
- Historial de auditoría inmutable de todo lo anterior.

No se modifica la tabla `importaciones` (182 columnas) ni `forecast_proyecciones` estructuralmente. Ningún módulo existente pierde funcionalidad si Proyecciones u Odoo están caídos.

## 2. Arquitectura

```
Angular (asignaciones-importacion.component.ts)
        │  HTTP + JWT (Authorization: Bearer <token>)
        ▼
routes/asignaciones_importaciones.py   (blueprint nuevo, url_prefix='/importaciones')
        │  @token_required (reutilizado de routes/clientes.py) + chequeo rol in (1,3)
        ▼
services/asignaciones_service.py       (reglas de negocio: matemática de asignación,
                                         idempotencia Odoo, transacciones SQL)
        │                                        │
        ▼                                        ▼
MySQL/MariaDB                           services/proyecciones_service.py  (NUEVO)
 (obtener_conexion(),                            │
  tablas importacion_*,                          ├─ forecast_proyecciones (lectura)
  SELECT...FOR UPDATE                            ├─ _get_ordenes_my27() (Odoo confirmado, FIFO)
  en operaciones críticas)                       └─ utils/odoo_utils.get_odoo_models()
```

Reglas de flujo de información:

- Asignaciones **solo lee** `forecast_proyecciones` e `importaciones`; nunca escribe en ellas.
- Todo lo que Asignaciones persiste vive en sus 4 tablas nuevas.
- `services/proyecciones_service.py` es el único punto de acceso a la demanda proyectada y a la deducción de Odoo — tanto `routes/proyecciones_my27.py` como `routes/asignaciones_importaciones.py` lo consumen; ninguno reimplementa la lógica.

### Cambio a archivo existente (declarado, bajo riesgo)

- **Archivo:** `routes/proyecciones_my27.py`
- **Qué se mueve:** `PRIORIDAD_CLIENTES`, `_PRIORIDAD_MAP`, `_norm_sku()`, `_get_ordenes_my27()` (y sus globales de caché `_ORDENES_CACHE`/`_ORDENES_TTL`) — líneas ~41-69 y ~337-474.
- **Motivo:** evitar que Asignaciones duplique esta lógica; resolver de paso la duplicación ya existente con Angular (`CLIENTES_PRIORITARIOS` en `proyecciones-my27.component.ts`) vía el nuevo endpoint `GET /clientes/prioridad`.
- **Cambio:** relocación pura y mecánica a `services/proyecciones_service.py`. `proyecciones_my27.py` pasa a importar estos nombres en vez de definirlos localmente. Cero cambio de comportamiento.
- **Riesgo:** bajo, pero toca un archivo en producción del que dependen `/proyecciones-my27/cobertura-megamo` y `/proyecciones-my27/distribucion-prioritaria`. Se hace como paso aislado, verificado comparando la respuesta de ambos endpoints antes/después del refactor, antes de tocar nada de Asignaciones.

### Cambio a archivo existente #2 (declarado, muy bajo riesgo)

- **Archivo:** `routes/clientes.py`
- **Qué se agrega:** un endpoint nuevo `GET /clientes/prioridad` (no se toca nada existente en el archivo).
- **Motivo:** exponer la lista de prioridad centralizada para que Angular (Proyecciones y el nuevo Asignaciones) deje de tener una copia hardcodeada, y para que sea consumible por HTTP además de por import directo en Python.
- **Riesgo:** mínimo — es una función nueva, no toca las 15 rutas existentes del archivo. Reutiliza el decorador `token_required` ya definido ahí mismo (línea 474) si se decide proteger; se deja sin proteger por ser información no sensible (mismo criterio que otras rutas de solo-lectura del archivo).

### Desviación de estructura de carpetas

El brief original propone `routes/ → services/ → repositories/`. El repo real no tiene carpeta `repositories/` en ningún dominio; el patrón existente (`services/garantias_service.py`, etc.) es de 2 capas: `routes/` (HTTP + auth) → `services/` (negocio + acceso a datos vía `obtener_conexion()`). Se sigue esa convención de 2 capas para no introducir un patrón nuevo sin precedente en el proyecto. Aprobado por el usuario.

## 3. Modelo de datos

### `importacion_productos`

```sql
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

`periodo` se elige manualmente en el formulario (formato `"2026-2027"`, igual que `forecast_proyecciones.periodo`) — no se deriva automáticamente de la temporada del embarque, para no acoplar dos convenciones de nombres que hoy viven en módulos distintos. `sku_norm` se calcula en Python con `_norm_sku()` antes de insertar; el `UNIQUE` es sobre `sku_norm`, no sobre `sku`, para que variantes con/sin guiones del mismo SKU no dupliquen fila.

### `importacion_asignaciones`

```sql
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

Representa el estado **actual** del reparto por cliente (una fila por par producto/cliente, se actualiza con `UPSERT`). El detalle evento-a-evento vive en `importacion_movimientos`. No se repiten `sku`/`periodo` aquí (alcanzables por `JOIN`) para no crear una segunda fuente que pueda desincronizarse. `cantidad_proyectada` y `prioridad` son snapshots del momento de asignar, para poder comparar contra la proyección actual más adelante (sección 26 del brief) sin perder el historial de qué se decidió y por qué.

### `importacion_sobrantes_ventas`

```sql
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

Los estados se ajustaron de la propuesta original (`DISPONIBLE/RESERVADO/VENDIDO/CANCELADO`, que describían el estado del sobrante, no el de la venta) a un ciclo de vida claro de la venta misma: `PENDIENTE_VALIDACION` (recién creada, con o sin folio) → `VALIDADO` (Odoo confirmó pedido/cliente/SKU/cantidad) → `CANCELADO`. `UNIQUE(numero_pedido_odoo)` es la base de la idempotencia: MySQL permite múltiples `NULL`, así que varias ventas pendientes sin folio conviven, pero un mismo folio capturado dos veces choca y el backend responde con el registro existente en vez de duplicar.

### `importacion_movimientos`

```sql
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

Ledger inmutable, solo `INSERT` (nunca `UPDATE`/`DELETE`). `cantidad` lleva signo (`+10` entrada, `-3` asignación, etc.); `SUM(cantidad)` agrupado por `importacion_producto_id` reconstruye el disponible exacto en cualquier momento.

### Decisión de performance

No se agregan columnas cacheadas (`cantidad_disponible`, etc.) en `importacion_productos`. Con el volumen esperado (decenas de SKUs × decenas de clientes por embarque), un `SUM()` sobre `importacion_movimientos` es trivial en costo y evita que un valor cacheado se desincronice del ledger real. Prioridad explícita del usuario: integridad de inventario por encima de performance. Si el volumen lo justifica en el futuro, se agrega caché encima sin tocar el modelo.

## 4. Flujo de negocio

1. Usuario abre un embarque en Importaciones → botón "Asignaciones" → `GET /importaciones/<id>/asignaciones` trae resumen (KPIs + tabla de productos).
2. Si el embarque no tiene productos aún, el usuario los captura (`POST .../productos`): SKU + cantidad + periodo. Cada alta genera movimiento `ENTRADA` (+cantidad). Funciona aunque Proyecciones/Odoo estén caídos.
3. `POST .../recalcular`: por cada producto, `services/proyecciones_service.py` suma `forecast_proyecciones` por `(sku_norm, periodo)` agrupado por `clave_cliente`, resta lo ya confirmado en Odoo vía `_get_ordenes_my27(periodo)`, y devuelve demanda neta por cliente ordenada por prioridad. **Solo lectura** — no persiste nada; es una propuesta para que el usuario decida (proyección ≠ asignación).
4. Usuario confirma (`POST .../productos/<id>/asignar`): transacción con `SELECT ... FOR UPDATE` sobre el producto, valida `asignado ≤ disponible`, hace `UPSERT` en `importacion_asignaciones` e inserta movimiento `ASIGNACION` (negativo). Si no alcanza: `STOCK_INSUFICIENTE`, rollback completo.
5. El sobrante es el disponible después de asignar (calculado, no almacenado aparte).
6. Venta anticipada (`POST .../productos/<id>/venta-sobrante`): crea fila en `importacion_sobrantes_ventas` (`PENDIENTE_VALIDACION`) + movimiento `VENTA_SOBRANTE` (negativo), validando `cantidad ≤ disponible` en la misma transacción con `FOR UPDATE`. El folio Odoo puede venir vacío.
7. Validación Odoo (`POST .../ventas/<id>/validar-odoo`): busca `sale.order` por `name = numero_pedido_odoo` + `company_id = ODOO_COMPANY_ID`, compara cliente (`partner_id.ref`), SKU normalizado de las líneas, y cantidad. Si Odoo no responde, no se toca el movimiento ya hecho: error `PEDIDO_ODOO_NO_DISPONIBLE`, reintentable.
8. Cancelación: `estado='CANCELADO'`/`'CANCELADA'` + movimiento `CANCELACION` (positivo, revierte cantidad). Nunca se borra la fila original.
9. `GET .../movimientos` reconstruye el historial completo.

## 5. Endpoints

Todas las rutas del blueprint nuevo (excepto `/clientes/prioridad`) van protegidas con `@token_required` (reutilizado de `routes/clientes.py`) + chequeo `payload['rol'] in (1, 3)` — mismos roles que el guard actual de Importaciones en Angular. Formato de respuesta uniforme: éxito `{ok:true, data:{...}}`, error `{ok:false, error:{code, message}}`.

| Método | Ruta | Body | Respuesta OK | Errores |
|---|---|---|---|---|
| GET | `/importaciones/<id>/asignaciones` | — | `{embarque, kpis, productos:[...]}` | `IMPORTACION_NO_EXISTE` |
| GET | `/importaciones/<id>/asignaciones/productos` | — | lista con embarcado/asignado/sobrante/vendido/disponible calculados | `IMPORTACION_NO_EXISTE` |
| POST | `/importaciones/<id>/asignaciones/productos` | `{sku, cantidad_embarcada, periodo, descripcion?}` | producto creado | `SKU_INVALIDO`, `IMPORTACION_NO_EXISTE` |
| PUT | `/importaciones/<id>/asignaciones/productos/<pid>` | `{cantidad_embarcada?, descripcion?}` | producto actualizado + movimiento `AJUSTE` si cambia cantidad | `PRODUCTO_NO_EXISTE`, `AJUSTE_INVALIDO` |
| GET | `/importaciones/<id>/asignaciones/productos/<pid>/detalle` | — | `{proyecciones, asignaciones, sobrantes_ventas}` | `PRODUCTO_NO_EXISTE` |
| POST | `/importaciones/<id>/asignaciones/recalcular` | `{periodo?}` | propuesta de reparto (no persiste) | `PROYECCIONES_NO_DISPONIBLES` (degrada, no bloquea) |
| POST | `/importaciones/<id>/asignaciones/productos/<pid>/asignar` | `{asignaciones:[{clave_cliente, cantidad}]}` | asignaciones aplicadas | `STOCK_INSUFICIENTE`, `CLIENTE_NO_EXISTE`, `CONFLICTO_CONCURRENCIA` |
| POST | `/importaciones/<id>/asignaciones/productos/<pid>/venta-sobrante` | `{clave_cliente, cantidad, numero_pedido_odoo?}` | venta creada (idempotente) | `SOBRANTE_INSUFICIENTE`, `PEDIDO_ODOO_YA_ASOCIADO` |
| POST | `/importaciones/<id>/asignaciones/ventas/<vid>/validar-odoo` | `{numero_pedido_odoo?}` | `{estado:'VALIDADO', pedido:{...}}` | `PEDIDO_ODOO_NO_EXISTE`, `PEDIDO_ODOO_INVALIDO`, `PEDIDO_ODOO_NO_DISPONIBLE` |
| POST | `/importaciones/<id>/asignaciones/ventas/<vid>/cancelar` | — | venta cancelada, disponibilidad restaurada | `VENTA_NO_EXISTE`, `VENTA_YA_CANCELADA` |
| GET | `/importaciones/<id>/asignaciones/movimientos` | — | historial completo | `IMPORTACION_NO_EXISTE` |
| GET | `/clientes/prioridad` | — | `[{clave, nombre, prioridad}]` | — |

## 6. Decisiones tomadas en brainstorming (resumen)

1. **Demanda a repartir:** neta (proyección menos Odoo ya confirmado), reutilizando el mismo criterio que `_compute_distribucion_prioritaria`.
2. **Prioridad de clientes:** se centraliza en `services/proyecciones_service.py`, expuesta vía `GET /clientes/prioridad`; sigue hardcodeada en código por ahora (no se crea tabla BD todavía), pero deja de estar duplicada en Angular.
3. **Periodo del producto:** selector manual en el formulario, no derivado automáticamente de la temporada del embarque.
4. **Roles con acceso:** los mismos que Importaciones hoy (`rol 1` o `rol 3`).
5. **Capas backend:** `routes/ → services/` (2 capas), sin `repositories/`, siguiendo la convención real del repo.
6. **Performance:** sin columnas cacheadas de disponibilidad; todo se calcula desde `importacion_movimientos`.

## 7. Reglas absolutas heredadas del brief (no negociables)

Nunca `asignado > embarcado`. Nunca `vendido_sobrante > sobrante`. Nunca cantidades negativas. Ninguna operación de cantidades sin transacción + `SELECT ... FOR UPDATE`. Nunca se borra un movimiento. Nunca se descuenta dos veces el mismo `numero_pedido_odoo`. Importaciones y Asignaciones siguen funcionando si Proyecciones u Odoo caen (solo se degrada `recalcular`/`validar-odoo`). Ninguna validación crítica vive solo en Angular — todo se revalida en backend.

## 8. Pruebas mínimas a cubrir (Fase 7)

Los 12 casos del brief original: cobertura exacta, sobre-proyección, cobertura exacta sin sobrante, rechazo de sobre-asignación, rechazo de sobre-venta, idempotencia de pedido Odoo duplicado, cancelación restaura disponibilidad, concurrencia (dos ventas simultáneas no dejan negativo), Odoo caído no rompe el flujo, Proyecciones caída no rompe el registro de mercancía, SKU con/sin guiones coincide, cliente sin prioridad usa fallback 999 + orden alfabético.

## 9. Plan de fases

1. ✅ Análisis (este documento)
2. BD: 4 tablas nuevas + migración idempotente (`CREATE TABLE IF NOT EXISTS`)
3. Backend base: extracción a `services/proyecciones_service.py`, `services/asignaciones_service.py`, `routes/asignaciones_importaciones.py`, endpoint `/clientes/prioridad`
4. Integración Proyecciones (`recalcular`, demanda neta)
5. Integración Odoo (`validar-odoo`, idempotencia, manejo de caídas)
6. Frontend Angular (`asignaciones-importacion/`)
7. Pruebas (pytest, los 12 casos)
8. Revisión final contra checklist de la sección 8 del brief original
