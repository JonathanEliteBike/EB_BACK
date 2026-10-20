# Auditoría de llenado del embarque — Diseño

**Fecha:** 2026-09-30
**Módulo:** Importaciones (EB_BACK / EB_FRONT)
**Estado:** Propuesto — pendiente de revisión del usuario

## 1. Objetivo

Hoy el monitor de importaciones registra el *valor* de cada fecha capturada
en un embarque, pero no registra *cuándo* se capturó ese valor. No hay forma
de saber si el equipo llenó un campo a tiempo, adelantado o tarde respecto a
lo que el proceso espera.

Este proyecto agrega una pestaña de **Auditoría** al detalle del embarque
que, para un conjunto configurable de hitos (fechas clave del proceso),
compara la fecha en que el campo *realmente* se llenó contra la fecha en que
se *esperaba* que se llenara, y muestra el resultado en verde (adelanto) o
rojo (atraso).

## 2. Alcance y reglas acordadas con el usuario

- **Sin retroactividad.** Los embarques/campos ya llenados antes de
  desplegar este cambio no tienen fecha de captura histórica — se muestran
  como "Sin dato histórico" en la auditoría, nunca se aproxima ni se
  inventa una fecha. La captura de "cuándo se llenó" empieza a correr desde
  que este cambio esté en producción.
- **Hitos configurables**, no fijos en código — se guardan en una tabla y se
  administran desde una pantalla nueva, con el mismo patrón CRUD que ya
  existe para `importaciones_tiempos_estimados` (`routes/importaciones.py`
  línea ~695-790). Así se pueden agregar/editar/quitar hitos sin tocar
  código.
- **Cronograma fijo, no en cascada.** La fecha esperada de un hito anclado
  en OTRO hito de la lista se calcula con la fecha *esperada* del ancla
  (`esperada_ancla + días`), no con su fecha real — un atraso en un paso no
  recorre automáticamente el plazo de los siguientes.
- **Campos "disparador".** Algunos anclas (ej. "Fecha de Entrega Real") no
  son ellos mismos un hito con plazo propio — no tienen fecha esperada.
  Para los hitos que anclan en uno de estos campos, la fecha esperada se
  calcula como `fecha REAL capturada del disparador + días`, y el hito
  queda en estado "pendiente" hasta que el disparador se captura.

## 3. Modelo de datos

### 3.1 `importaciones_historial_campos` (tabla nueva)

Registra la primera vez que cualquier campo rastreado pasa de vacío a tener
un valor. Es de propósito general — sirve para estos 20 hitos y para
cualquier auditoría futura ("quién capturó qué y cuándo"), no solo para
este proyecto.

```sql
CREATE TABLE importaciones_historial_campos (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    importacion_id   INT NOT NULL,
    campo            VARCHAR(100) NOT NULL,   -- nombre real de columna, ej. "log_contenedor"
    valor_anterior   TEXT,
    valor_nuevo      TEXT,
    capturado_en     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    usuario_id       INT,
    INDEX idx_importacion_campo (importacion_id, campo),
    FOREIGN KEY (importacion_id) REFERENCES importaciones(id) ON DELETE CASCADE
);
```

**Cuándo se escribe:** dentro de `PUT /importaciones/<id>` (`actualizar()`,
`routes/importaciones.py` línea ~1487), en la rama de guardado oficial (no
en la de borradores). Justo antes del `UPDATE importaciones SET ...`, para
cada campo en `data` que esté en la lista de campos rastreados (los que
participan como DATO o como ancla-disparador en algún hito activo): si el
valor actual en `existing` es `NULL`/vacío y el nuevo valor no lo es, se
inserta una fila. Si el campo ya tenía valor (es una corrección, no una
primera captura), **no se vuelve a insertar** — `capturado_en` representa
la primera captura, no cada edición posterior.

### 3.2 `importaciones_hitos_auditoria` (tabla nueva — configuración)

```sql
CREATE TABLE importaciones_hitos_auditoria (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    seccion          VARCHAR(30) NOT NULL,   -- logistica, importacion, despacho, odoo, almacen, recepcion, costos, cierre
    orden_hito       INT NOT NULL,           -- "HITO" de la tabla del usuario, para ordenar dentro de la sección
    etiqueta         VARCHAR(150) NOT NULL,  -- texto a mostrar, ej. "18. Contenedores"
    campo_dato       VARCHAR(100) NOT NULL,  -- columna real que se audita
    campo_ancla      VARCHAR(100),           -- columna real del ancla, o NULL = "ALTA DE EMBARQUE"
    dias_esperados   INT NOT NULL,
    activo           TINYINT(1) DEFAULT 1,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
```

Pantalla de administración nueva (`/importaciones/hitos-auditoria`, mismo
patrón que `/importaciones/tiempos-estimados`): listar, crear, editar,
desactivar. `campo_dato` y `campo_ancla` se eligen de un select con los
nombres de columna reales — no texto libre, para evitar hitos que apunten a
un campo que no existe.

## 4. Motor de cálculo

Función nueva `_calcular_auditoria(importacion_id, conn)` en
`routes/importaciones.py`, junto a `_recalcular_campos`:

1. Lee los hitos activos de `importaciones_hitos_auditoria`, agrupados por
   sección, ordenados por `orden_hito`.
2. Lee `importaciones_historial_campos` del embarque → `{campo: capturado_en}`.
3. Resuelve `fecha_esperada` de cada hito, en el orden de dependencia
   (un hito no puede resolverse antes que su ancla si el ancla es otro
   hito de la lista):
   - `campo_ancla IS NULL` → `fecha_esperada = importaciones.created_at + dias_esperados`
   - `campo_ancla` coincide con el `campo_dato` de otro hito activo →
     `fecha_esperada = fecha_esperada(ancla) + dias_esperados`
   - cualquier otro caso (`campo_ancla` no es un hito de la lista) →
     si `campo_ancla` tiene fila en el historial: `fecha_esperada =
     capturado_en(ancla) + dias_esperados`; si no, `fecha_esperada = null`
     (estado "pendiente")
4. Para cada hito, busca `capturado_en(campo_dato)` en el historial:
   - Sin fecha esperada → estado `pendiente`
   - Sin captura real → estado `en_espera` (con la fecha esperada visible, para saber si ya se vence)
   - Con ambas → `dias = fecha_esperada - fecha_real` (positivo = adelanto/verde, negativo = atraso/rojo), estado `adelantado` / `a_tiempo` / `atrasado`

Esto se expone en `GET /importaciones/<id>/auditoria`, que devuelve la
lista de hitos con su estado, para que el frontend solo pinte.

## 5. Mapeo de los 20 hitos a columnas reales

Confirmado contra `CAMPOS_LOGISTICA` / `CAMPOS_IMPORTACION` / ... en
`routes/importaciones.py`. Dos son compuestos (varias columnas) — marcados
como **supuesto a confirmar**.

| Sección | Ancla | Días | Hito (DATO) | Columna real |
|---|---|---|---|---|
| Logística | Alta de embarque | 0 | 1. Importador | `odoo_importador` |
| Costos | Alta de embarque | 0 | Flete proyectado (USD) | `cos_flete_proyectado_usd` |
| Logística | Alta de embarque | 7 | 9. Confirmación de cotización y forwarder | `log_confirmacion_cotizacion` |
| Logística | 3. Fecha de Entrega (Real) *(disparador)* | 1 | 18. Contenedores | `log_contenedor` |
| Importación | 3. Fecha de Entrega (Real) *(disparador)* | 10 | 1. Fecha de entrega de traducción al RBF | `imp_fecha_traduccion` |
| Odoo | 3. Fecha de Entrega (Real) *(disparador)* | 2 | 1. Recepción de documentos | `log_recepcion_documentos` |
| Odoo | 1. Recepción de documentos | 10 | 6. Folios de orden de compra | `odoo_folio_orden` |
| Costos | 6. Folios de orden de compra | 0 | Piezas por tipo de caja | **compuesto**: `cos_caja_scott_r24`, `cos_caja_scott_r20`, `cos_caja_scott_adulto`, `cos_caja_scott_tw`, `cos_caja_scott_tw_electrica`, `cos_caja_megamo_track`, `cos_caja_megamo_reason`, `cos_caja_megamo_vitae` — *supuesto: "capturado" = todas llenas o N/A* |
| Importación | 8. Llegada de contenedor a puerto (Real) *(disparador)* | 5 | 18. Recepción de draft de pedimento | `imp_recepcion_draft_pedimento` |
| Importación | 20. Pedimento revisado definitivo *(disparador)* | 4 | 34. Fecha de pago de pedimento | `imp_fecha_pago_pedimento` |
| Despacho | 34. Fecha de pago de pedimento | 1 | 1. Solicitud de cita para cruce | `des_solicitud_cita_cruce` |
| Almacén | 34. Fecha de pago de pedimento | 2 | 1. Base de datos para etiquetas | `alm_base_datos_etiquetas` |
| Despacho | Fecha de Cruce (Real) *(disparador)* | 2 | 9. Llegada de contenedor a almacén | `des_llegada_almacen` |
| Despacho | Fecha de Cruce (Real) *(disparador)* | 3 | 15. Recepción de documento EIR | `des_recepcion_eir` |
| Almacén | 9. Llegada de contenedor a almacén | 2 | 6. Envío de información a la UVA (Real) | `alm_envio_info_uva` |
| Almacén | 9. Fecha de inicio de etiquetado *(disparador)* | 3 | 10. Fecha de terminación de etiquetado (Real) | `alm_terminacion_etiquetado` |
| Recepción | 9. Llegada de contenedor a almacén | 2 | Cédula de costeo de IGI | `rec_cedula_costeo` |
| Recepción | 4. Liberación de productos a verificación (Real) *(disparador)* | 1 | Liberación final del producto | `rec_liberacion_final` |
| Costos | Fecha de Cruce (Real) *(disparador)* | 10 | Costos reales | **compuesto**: sección "Costos Reales" del formulario, desde `cos_tipo_cambio_pedimento` hasta el último campo real de esa sección — *supuesto: "capturado" = todos llenos o N/A* |
| Cierre | Costos reales | 2 | 1. Recepción de cuentas de gastos | `cie_recepcion_cuenta_gastos` |
| Cierre | 1. Recepción de cuentas de gastos | 4 | Fecha de pago a agente aduanal | `cie_fecha_pago_aa` |

**Ancla "9. Recepción de documentos" → nota:** aunque tiene prefijo `log_`,
vive en `CAMPOS_ODOO` (comentario en el código: "moved to CAMPOS_ODOO") —
confirmado que es la sección Odoo, no Logística.

## 6. API nueva

- `GET /importaciones/<id>/auditoria` — devuelve los hitos resueltos (ver §4).
- `GET /importaciones/hitos-auditoria` / `POST` / `PUT /<id>` / `DELETE /<id>` — CRUD de configuración, mismo patrón que `tiempos-estimados`.

## 7. Frontend

- Nueva pestaña **"Auditoría"** en `importaciones-detalle.component`, junto
  a las 8 existentes (Logística, Importación, Despacho, Odoo/SAE, Almacén,
  Recepción, Costos, Cierre) — no reemplaza ninguna, ni cambia el formulario
  de captura actual.
- Tabla agrupada por sección (igual patrón visual que ya usa el módulo de
  Asignaciones para grupos por cliente): hito, fecha esperada, fecha real,
  estado con badge de color (verde = adelanto, rojo = atraso, gris =
  pendiente/en espera) y los días de diferencia.
- Pantalla nueva de administración `/importaciones/hitos-auditoria` (CRUD),
  accesible desde el mismo lugar donde hoy se administra Tiempos Estimados.

## 8. Fuera de alcance (por ahora)

- No se backfillea historial para datos ya capturados (§2).
- No se notifica ni alerta proactivamente sobre atrasos — esta fase es solo
  de visualización/auditoría, no de alertas automáticas.
- No se audita corrección de campos ya llenados (solo la primera captura).
