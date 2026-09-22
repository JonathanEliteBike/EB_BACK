# Panorama técnico — Stack Elite Bike (EB_BACK + EB_FRONT)

**Fecha:** 2026-09-10
**Nivel:** panorama (arquitectura, esquema resumido, endpoints enumerados, flujos y pipelines a alto nivel).
**Fuentes:** `EB_BACK/` (checkout `main`), `EB_FRONT/` (checkout `main`), y el submódulo *Asignaciones de Importaciones* en las ramas `sdd-asig-back` / `sdd-asig-front` (en revisión, sin fusionar).

---

## 1. Resumen del stack

| Capa | Tecnología | Notas |
|---|---|---|
| Frontend | **Angular 19** standalone components · Angular Material 19 · Angular CDK · RxJS 7.8 · Chart.js 4 · socket.io-client 4 · jwt-decode · xlsx / jspdf / file-saver / html2canvas | SPA servida aparte. Dev: `ng serve` en `:4200` con proxy. |
| Backend | **Flask 3** (factory `create_app`) · Flask-CORS · Flask-SocketIO (async `threading`) · Werkzeug · Gunicorn/eventlet en prod | Un solo servicio HTTP en `:5000`. `socketio.run(...)` en dev. |
| Tareas async | **Celery 5** (broker + backend = Redis) · **APScheduler** (BackgroundScheduler, TZ `America/Mexico_City`) | Celery para correos/PDF/precalentado; APScheduler para el sync diario y el warm de cachés. |
| Datos | **MySQL 8.0** (`utf8mb4` / `utf8mb4_general_ci`) · **Redis** (caché + broker Celery) | 58 tablas. Conexión directa con `mysql-connector-python` (sin ORM). |
| Integraciones | **Odoo** vía XML-RPC (`xmlrpc/2/common` + `xmlrpc/2/object`) · **SMTP** (pool de conexiones) · **S3** (`services/s3_service.py`) | Odoo es la fuente de verdad de pedidos/facturas/stock. |
| Auth | **JWT HS256** (`PyJWT`), TTL 48 h · OTP (`pyotp`) para flujos que lo requieren · bcrypt para hash de password | Token en header `Authorization: Bearer`. |
| Reportes | ReportLab · WeasyPrint · XlsxWriter / openpyxl · pandas | Generación de PDF y Excel server-side. |

---

## 2. Topología

```
┌─────────────┐     HTTPS      ┌──────────────────────────────────────────────┐
│  Navegador  │ ─────────────► │  Frontend Angular 19 (SPA)                    │
│             │               │  dev: localhost:4200  ·  prod: app.elite-bike │
└─────────────┘               └───────────────┬──────────────────────────────┘
                                              │  REST JSON + WebSocket
                                              │  Authorization: Bearer <JWT>
                                              │  (dev: proxy /api → 127.0.0.1:5000)
                                              ▼
                          ┌───────────────────────────────────────────────┐
                          │  Flask API  ( :5000 )   create_app()           │
                          │  28 blueprints · ~330 endpoints · Socket.IO    │
                          │  CORS allowlist · JWT (@token_required)        │
                          └───┬───────────┬───────────┬───────────┬────────┘
                              │           │           │           │
                    mysql-connector   redis-py    xmlrpc.client  smtplib / boto3
                              │           │           │           │
                              ▼           ▼           ▼           ▼
                        ┌─────────┐  ┌─────────┐  ┌────────┐  ┌──────────┐
                        │ MySQL 8 │  │  Redis  │  │  Odoo  │  │ SMTP /S3 │
                        │ 58 tbl  │  │ caché + │  │ XML-RPC│  │          │
                        │         │  │ broker  │  │ compañía 1 │        │
                        └─────────┘  └────┬────┘  └────────┘  └──────────┘
                                          │
                          ┌───────────────┴───────────────┐
                          │  Celery worker(s)   +   APScheduler (in-proc) │
                          │  tasks: correos, PDF caratula, precalentado   │
                          │  jobs:  sync diario L-V, warm de cachés       │
                          └──────────────────────────────────────────────┘
```

---

## 3. Backend (`EB_BACK/`)

### 3.1 Estructura y responsabilidad por capa

```
app.py                 create_app(): CORS, Socket.IO, registro de 28 blueprints, init_scheduler()
celery_worker.py       Celery app + 3 tasks + pool SMTP + beat_schedule
db_conexion.py         obtener_conexion() → MySQL (TCP, utf8mb4, use_unicode); fallback de usuarios
socket_instance.py     instancia global SocketIO

routes/     Un blueprint por dominio. Validación de payload + JWT + try/except + jsonify.
models/     Queries MySQL crudas por dominio (solo monitor_odoo_model, user_model hoy).
services/   Lógica de negocio reutilizable (forecast_excel, garantías, S3, retroactivos, sync_scheduler).
utils/      Cross-cutting: odoo_utils, cache_manager, jwt_utils, seguridad (bcrypt), email_utils,
            otp_utils, estados_pedidos, temporada_utils, auditoria_utils, tiempo.
tests/      pytest + pytest-mock. Se mockea utils.odoo_utils.get_odoo_models.
```

> El patrón formal es `routes → services → (models | utils)`. Varios dominios antiguos meten SQL directo en `routes/`; los nuevos (importaciones, asignaciones) respetan `routes → services`.

### 3.2 Blueprints por dominio

| Blueprint | ~endpoints | Dominio |
|---|---:|---|
| `auth` | 8 | Login, refresh, OTP, recuperación de password |
| `usuarios` | 5 | CRUD de usuarios internos, roles |
| `clientes` | 15 | Clientes/distribuidores, grupos, **`/clientes/prioridad`** (lista central de prioridad) |
| `monitor_odoo` | 5 | Monitor de pedidos/facturas Odoo, sync |
| `metas` | 4 | Metas de venta por cliente/periodo |
| `previo` | 5 | Cálculo "previo" (anticipo de retroactivos) |
| `proyecciones` | 14 | Proyecciones de venta (versión general) |
| `proyecciones_my27` | 9 | Forecast MY27: distribución prioritaria, cobertura inventario, export Excel |
| `forecast` | 30 | Carga/consulta de `forecast_proyecciones`, avance, whitelist SKU |
| `disponible` | 5 | Disponibilidad de producto (Odoo + entrante) |
| `multimarcas` | 7 | Catálogo y equivalencias multimarca |
| `caratulas` | 23 | Carátulas EVAC A/B (documento de cierre), histórico, PDF async |
| `integrales` | 6 | Reporte integral (cruces Odoo + BD) |
| `retroactivos` | 20 | Cálculo y tabla de retroactivos |
| `solicitud_retroactivo` (+ `_campanias`) | 27 | Formularios de solicitud de retroactivo (normal y campañas) |
| `ordenes_compra` | 4 | Órdenes de compra |
| `dashboard_flujo` | 7 | Dashboard de flujo de efectivo |
| `logistica` / `gastos` / `ingresos` | 9 | Costos e ingresos operativos |
| `edicion_pedidos` | 6 | Edición de pedidos Odoo |
| `garantias` | 32 | Módulo de garantías (formularios, piezas, comentarios, estructura, S3) |
| `ventas` | 5 | Consultas de ventas |
| `email` | 3 | Envío de correos (delega a Celery) |
| `temporadas` | 3 | Temporadas comerciales (fecha_inicio/fin) |
| **`importaciones`** | 8 | Monitor de Importaciones: CRUD embarques, dashboard analítico, latencias, costos |
| **`asignaciones_importaciones`** *(rama `sdd-asig-back`)* | ~21 | Submódulo Asignaciones — ver §6 |

### 3.3 Autenticación / autorización

- **`utils/jwt_utils.py`** — `generar_token(...)` / `verificar_token(...)` HS256, TTL 48 h. También `verificar_token_con_gracia(minutos_gracia)`.
- **Payload del token:** `id`, `rol`, `usuario`, `nombre`, `flujo`, `cliente_id`, `clave`, `nombre_cliente`, `id_grupo`.
- **`@token_required`** (en `routes/clientes.py`, reutilizado por otros dominios) — extrae `Bearer`, valida, deja el payload en `request.cliente_data`.
- **Roles** (`tabla roles`): 1 = admin/interno, 2 = cliente/distribuidor, 3 = rol operativo intermedio. Decoradores por dominio (p. ej. `@_requiere_rol_importaciones` exige rol ∈ {1,3}).
- Frontend: guards `auth`, `admin`, `logged-in`, `no-auth`, `usuario`, `flujo`, `importaciones`; interceptor `auth.interceptor.ts` inyecta el header en cada request.

### 3.4 CORS y Socket.IO

- Allowlist explícita en `app.py` (`localhost/127.0.0.1:4200/3001/63012`, IPs EC2 `3.128.54.77` / `3.146.204.64`, `app.elite-bike.com`, `api.elite-bike.com`) + regla dinámica para `localhost:*` / `127.0.0.1:*`. Maneja preflight `OPTIONS` y *Chrome Private Network Access*.
- **Socket.IO** montado en `/socket.io/`, `async_mode='threading'`. Se usa para notificaciones push (frontend `socket.service.ts`).

### 3.5 Integración Odoo

- **`utils/odoo_utils.get_odoo_models(retries=3)`** → `(uid, models, err)` vía XML-RPC (`common.authenticate` + `object`). Ambiente `prod` / `test` por `ODOO_ENV`.
- **Blindaje multi-empresa:** `ODOO_COMPANY_ID = 1`; toda consulta a modelos con `company_id` (`sale.order`, `account.move`, `stock.picking`, …) filtra `('company_id','=',ODOO_COMPANY_ID)` como primer criterio.
- **Anti N+1:** acumular IDs y hacer un solo `search_read` por modelo relacionado; `fields` siempre explícito; `limit`/`offset` para listados grandes.
- Consumidores clave: `monitor_odoo`, `forecast`, `proyecciones_my27` (`_get_ordenes_my27` — pedidos confirmados por periodo, caché 3 min), `disponible`, `integrales`, `caratulas`.

### 3.6 Caché

- **Redis** (`redis://localhost:6379/0`) — caché de respuestas costosas de Odoo y broker de Celery.
- Patrón de doble capa: memoria de proceso (TTL corto, 3–5 min) + Redis (TTL 10–30 min, sobrevive reinicios). Ej.: `proyecciones_my27._get_stock_disponible_odoo`, `_get_ordenes_my27`, precalentado de `detalle-compras-odoo` y `/forecast`.
- `utils/cache_manager.py` — helper genérico por TTL.
- Degradación: si Redis no responde, el sistema sigue (log `WARNING`, cae a `{}` / valores frescos).

### 3.7 Async — Celery y APScheduler

**Celery** (`celery_worker.py`, broker/back = Redis, TZ México):

| Task | Para qué |
|---|---|
| `tasks.enviar_caratula_pdf_async` | Genera el PDF de la carátula y lo envía por correo (pool SMTP). |
| `tasks.recalcular_previo_async` | Recalcula la tabla "previo" tras un sync. |
| `tasks.precalentar_monitor_async` | Precalienta el caché de `detalle-compras-odoo`. `beat_schedule`: cada 25 min. |

**APScheduler** (`services/sync_scheduler.init_scheduler()`, in-process, arranca con `create_app`):

- **Sync diario L-V** (~08:30 CDMX): `sync-monitor-odoo` (con `recalcular_previo=true`) → `sincronizar_notas` (recalcula `tabla_retroactivos`).
- **Warm de cachés**: precalentado de `monitor` y de `forecast` para todos los clientes.
- En *startup* se manda `precalentar_monitor_async` una vez, con lock Redis de 60 s para que solo un worker lo dispare.

---

## 4. Base de datos (MySQL 8, `utf8mb4_general_ci`)

**58 tablas.** Conexión: `db_conexion.obtener_conexion()` (TCP, `use_unicode`, fallback `app_user`→`root`). Patrón obligatorio: `try/finally` que cierra `cursor` y `conexion`; `rollback()` en excepción antes de re-lanzar.

| Dominio | Tablas |
|---|---|
| **Auth / usuarios** | `usuarios`, `roles`, `otps` |
| **Clientes** | `clientes`, `clientes_multimarcas`, `grupo_clientes`, `niveles_distribuidor`, `tiendas` |
| **Odoo / monitor** | `monitor`, `monitor_odoo`, `detalles_cuentas_odoo`, `odoo_catalogo`, `cache_ultima_actualizacion`, `historial_actualizaciones` |
| **Productos / catálogo** | `productos`, `producto_detalle` |
| **Proyecciones / forecast** | `forecast_proyecciones`, `forecast_excel_productos`, `forecast_inventario_megamo`, `forecast_sku_whitelist`, `proyecciones_ventas`, `proyecciones_cliente`, `proyecciones_autoguardado`, `disponibilidad_proyeccion` |
| **Metas / flujo** | `metas`, `flujo_valores`, `flujo_valores_unificados` |
| **Carátulas** | `caratula_evac_a`(+`_historico`), `caratula_evac_b`(+`_historico`), `historial_caratulas` |
| **Retroactivos** | `tabla_retroactivos`(+`_historico`), `previo`(+`_historico`), `solicitud_retroactivo_*` (7 tablas: formulario, marca, venta, msi, campanias, campania_msi, campania_producto_detalle), `cat_conceptos`(+`_unificados`) |
| **Multimarcas** | `multimarcas`(+`_historico`), `clientes_multimarcas` |
| **Gastos / operativo** | `gastos_operativos` |
| **Garantías** | `garantia_formularios`, `garantia_piezas`, `garantia_comentarios`, `garantia_estructura` |
| **Temporadas** | `temporadas` |
| **Auditoría** | `auditoria_movimientos` |
| **Importaciones** | `importaciones` (embarque: referencia, nombre, estado, `log_*` de logística, fechas, costos) |
| **Asignaciones** *(rama)* | `importacion_productos`, `importacion_asignaciones`, `importacion_sobrantes_ventas`, `importacion_movimientos` |

---

## 5. Frontend (`EB_FRONT/`)

### 5.1 Stack y build

- **Angular 19** standalone (sin `NgModule` de app), **Angular Material 19** + CDK, **Chart.js 4** (dashboards), **socket.io-client 4**, **jwt-decode**, **xlsx / jspdf / jspdf-autotable / file-saver / html2canvas** (export cliente).
- Dev: `ng serve` → `:4200`; `proxy.conf.json` reescribe `/api` → `http://127.0.0.1:5000` (`^/api` → `''`).
- `environment.ts` → `apiUrl: http://127.0.0.1:5000`; `environment.prod.ts` → `https://api.elite-bike.com`.

### 5.2 Estructura `src/app/`

```
components/    UI reutilizable (home-bar, date-picker, temporada-selector, ...)
views/         Pantallas por ruta. internal-views/ (staff) y usuarios/ (portal cliente).
services/      Uno por dominio: @Injectable providedIn:'root', HttpClient, environment.apiUrl,
               caché con BehaviorSubject + shareReplay donde aplica.
guards/        auth, admin, logged-in, no-auth, usuario, flujo, importaciones
interceptors/  auth.interceptor.ts (inyecta Authorization: Bearer)
pipes/ directives/
```

### 5.3 Servicios (uno por dominio del backend)

`auth` · `usuarios` · `clientes` · `monitor-odoo` · `metas` · `previo` · `proyeccion` · `proyecciones-my27` · `disponibilidad` · `multimarcas` · `caratulas` · `integrales` · `retroactivos` · `solicitud-retroactivo` · `solicitud-retroactivo-campanias` · `flujo` · `garantias` · `ventas` · `email` · `importaciones` · `catalogo-excel` · `socket` · `filtro` · `shared-data` · `update` · `alerta` · `confirm`
*(rama `sdd-asig-front`: `asignaciones-importacion.service.ts`)*

### 5.4 Patrones frontend

- Respuestas tipadas con interfaces; `HttpClient.get<T>()` + `.pipe(map(r => r.data))` para el contrato `{ok, data}`.
- El interceptor pone el JWT; los services **no** setean auth ni `Content-Type` (para `FormData`, el navegador pone el boundary).
- Suscripción con `async` pipe en plantilla o `Subscription` liberada en `ngOnDestroy`.
- Rutas en `app.routes.ts` con su guard.

---

## 6. Submódulo *Asignaciones de Importaciones* (ramas `sdd-asig-*`, en revisión)

### 6.1 Lugar en la arquitectura

Blueprint Flask **`asignaciones_importaciones`** (`url_prefix=/importaciones`), decoradores `@token_required` + `@_requiere_rol_importaciones` (rol ∈ {1,3}) en todas las rutas salvo `GET /clientes/prioridad`. `errorhandler` de blueprint devuelve el contrato uniforme `{"ok": false, "error": {code, message}}`.
`routes → services/asignaciones_service.py → (MySQL | services/proyecciones_service.py | utils/odoo_utils)`.

### 6.2 Datos (4 tablas)

```
importaciones (existente)
      │ 1
      ▼ N
importacion_productos        (importacion_id, periodo, sku, sku_norm, descripcion, cantidad_embarcada)
      │ 1                       UNIQUE(importacion_id, sku_norm)
      ├──────────────► importacion_asignaciones  = RESERVAS
      │                  (clave_cliente, mes_objetivo DATE, origen INICIAL|REASIGNACION,
      │                   cantidad_proyectada, cantidad_asignada, prioridad,
      │                   estado {RESERVADA|PENDIENTE_CONFIRMACION|CONFIRMADA|RECHAZADA|CANCELADA},
      │                   confirmada_at, confirmada_por)
      │                  UNIQUE(importacion_producto_id, clave_cliente, mes_objetivo, origen)
      │
      ├──────────────► importacion_sobrantes_ventas
      │                  (clave_cliente, cantidad, numero_pedido_odoo,
      │                   estado {PENDIENTE_VALIDACION|VALIDADO|CANCELADO})
      │
      └──────────────► importacion_movimientos   = LEDGER (solo-inserción, fuente de verdad del disponible)
                         tipo {ENTRADA|ASIGNACION|LIBERACION|SOBRANTE|RESERVA_SOBRANTE|VENTA_SOBRANTE|
                               CANCELACION|AJUSTE|RESERVA|REASIGNACION|RECHAZO_RESERVA}
                         cantidad con signo · disponible = SUM(cantidad)
```

Migración idempotente vía `information_schema` en `POST /importaciones/asignaciones/inicializar-tablas`.

### 6.3 Endpoints

| Método · Ruta | Para qué |
|---|---|
| `POST /importaciones/asignaciones/inicializar-tablas` | Crea/migra las 4 tablas |
| `GET /importaciones/<id>/asignaciones` | Resumen del embarque + KPIs |
| `GET /importaciones/<id>/asignaciones/productos` | Productos del embarque con KPIs |
| `POST /importaciones/<id>/asignaciones/productos` | Alta manual de producto |
| `PUT /importaciones/<id>/asignaciones/productos/<pid>` | Editar cantidad/descripción (movimiento AJUSTE) |
| `POST /importaciones/<id>/asignaciones/productos/importar` | **Importar productos desde Excel** (.xlsx, openpyxl) |
| `POST /importaciones/<id>/asignaciones/recalcular` | Propuesta de reparto por **ventana de meses** (cliente-mayor / prioridad absoluta) |
| `POST /importaciones/<id>/asignaciones/productos/<pid>/asignar` · alias `/reservar` | Confirmar reservas iniciales (mes_objetivo, origen INICIAL) |
| `POST /importaciones/<id>/asignaciones/reasignar` · `/productos/<pid>/reasignar` | *(Task 3, pendiente)* Reasignación a meses anteriores |
| `POST /importaciones/<id>/asignaciones/reservas/<rid>/resolver` | *(Task 4, pendiente)* Cliente aceptó / rechazó |
| `POST /importaciones/<id>/asignaciones/productos/<pid>/venta-sobrante` | Registrar venta anticipada de sobrante |
| `POST /importaciones/<id>/asignaciones/ventas/<vid>/validar-odoo` | Validar el pedido en Odoo |
| `POST /importaciones/<id>/asignaciones/ventas/<vid>/cancelar` | Cancelar venta de sobrante (LIBERACION) |
| `POST /importaciones/<id>/asignaciones/productos/<pid>/asignaciones/<aid>/cancelar` | Cancelar/liberar una reserva |
| `GET /importaciones/<id>/asignaciones/movimientos` | Ledger del embarque |
| `GET /importaciones/asignaciones/embarques` | **Panel consolidado** — un renglón por embarque con KPIs |
| `GET /importaciones/asignaciones/productos` | **Panel consolidado** — lista plana por SKU cruzando embarques |

### 6.4 Pipeline de datos de una asignación

```
forecast_proyecciones (12 columnas de mes)                    Odoo: sale.order confirmados
   └── demanda_neta_por_cliente_mensual(periodo, skus) ◄──────┘  (_get_ordenes_my27, caché 3 min)
          │  resta Odoo mes-a-mes del más antiguo al más nuevo
          ▼
   recalcular_propuesta(embarque, mes_desde, mes_hasta)
          │  cliente-mayor (prioridad absoluta) · netea reservas vigentes de TODOS los embarques
          │  del mismo SKU+periodo (_reservas_vigentes_por_mes)
          ▼
   propuesta [{cliente, meses:[{mes, proyectado, vigente, sugerido}], faltante_total}]
          │  el usuario confirma
          ▼
   asignar()  →  fila importacion_asignaciones (RESERVADA/INICIAL, mes_objetivo)
              →  movimiento RESERVA (−cantidad) en el ledger
          │
          ▼  (Task 3–4)
   reasignar sobrante a meses anteriores → PENDIENTE_CONFIRMACION → resolver:
      ACEPTADA → CONFIRMADA (sin movimiento)
      RECHAZADA → RECHAZO_RESERVA (+cantidad) → vuelve a sobrante
```

Estado de avance detallado: `docs/superpowers/plans/2026-09-10-asignaciones-reservas-plan-implementacion.md`.

---

## 7. Flujos y pipelines clave

### 7.1 Login
`POST /login` → valida credencial (bcrypt) → `generar_token()` HS256 → el frontend guarda el JWT → `auth.interceptor` lo adjunta en cada request → guards deciden acceso por `rol`.

### 7.2 Request autenticado típico
`Angular service` → (`dev`: proxy `/api`) → `Flask blueprint` → `@token_required` (payload en `request.cliente_data`) → `service` → MySQL / Odoo / Redis → `jsonify({"ok": true, "data": ...})` → `service.pipe(map(r => r.data))`.

### 7.3 Sync diario (APScheduler, L-V ~08:30 CDMX)
`_run_sync_diario()` → `sync-monitor-odoo?recalcular_previo=true` (trae pedidos/facturas de Odoo, actualiza `monitor*`, dispara `recalcular_previo_async` en Celery) → `sincronizar_notas` (recalcula `tabla_retroactivos`). En paralelo, jobs de warm de `monitor` y `forecast`.

### 7.4 Precalentado de cachés
Startup y `beat` (25 min) → `precalentar_monitor_async` → recorre clientes y llena Redis de `detalle-compras-odoo`; job equivalente para `/forecast`. Lock Redis evita duplicados entre workers.

### 7.5 Importar productos de un embarque (Asignaciones)
`POST …/productos/importar` (multipart) → `parsear_excel_productos()` (openpyxl, busca encabezado SKU/Cantidad/Descripción) → `importar_productos()` por fila: `crear_producto` (movimiento ENTRADA) o `actualizar_producto` (movimiento AJUSTE); filas con cantidad < ya reservado van a `errores`. Respuesta `{insertados, actualizados, total_filas, errores:[{fila,sku,motivo}]}`.

### 7.6 Reserva por mes (Asignaciones) — ver §6.4.

---

## 8. Entornos y despliegue

| | Dev local | Producción |
|---|---|---|
| Frontend | `ng serve` `:4200` (proxy `/api` → `:5000`) | `https://app.elite-bike.com` (build estático) |
| Backend | `python app.py` → `socketio.run(:5000, debug=True, use_reloader=False)` | `https://api.elite-bike.com` (WSGI/eventlet), EC2 `3.128.54.77` / `3.146.204.64` |
| MySQL | `127.0.0.1:3306` (`MYSQL_*` en `.env`) | instancia gestionada |
| Redis | `redis://localhost:6379/0` | idem gestionado |
| Odoo | `ODOO_ENV=prod` → `ebik.odoo.com` (o `ODOO_ENV=test`) | prod |
| Secretos | `EB_BACK/.env` (`MYSQL_*`, `ODOO_*`, `CELERY_BROKER_URL`, SMTP, S3) — **nunca commiteado** | variables de entorno del host |

Nota: el backend usa `socketio.run` con `use_reloader=False` → un cambio en el código requiere reinicio manual del proceso.

---

## 9. Observaciones / deuda técnica (informativo)

| Tema | Detalle |
|---|---|
| `SECRET_KEY` del JWT | Hardcodeada en `utils/jwt_utils.py` (`"121221"`). Debería moverse a `.env`. |
| SQL en `routes/` | Dominios antiguos mezclan queries en las rutas; el patrón `routes → services` solo se cumple en los módulos nuevos. |
| Tests con Odoo | `tests/test_integrales.py` y `tests/test_monitor_odoo.py` fallan sin una instancia Odoo viva/consistente (2 rojos "ambientales" en la suite completa). |
| `ng test` (frontend) | No arranca por specs preexistentes rotos; la verificación de frontend es revisión + `ng serve` limpio. |
| `log_origen` en `importaciones` | 11 filas con el acento guardado como U+FFFD (corregido en `main`… en la rama, vía `_norm_origen`; pendiente correr el `UPDATE` en prod). |
| Migraciones de esquema | No hay herramienta de migraciones; los módulos nuevos traen su propio runner idempotente sobre `information_schema` (`inicializar-tablas`). |

---

*Documento panorama. Para el diseño detallado del submódulo Asignaciones (DDL completo, algoritmos, ejemplos numéricos), ver `docs/superpowers/specs/2026-09-10-asignaciones-reservas-por-mes-design.md`.*
