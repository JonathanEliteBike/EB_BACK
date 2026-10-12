# Validación comercial de Modelo — Bloque 4

## Universo y alcance

- Periodo: 2026-01-01 a 2026-10-09.
- Vista: Facturas.
- Se analizaron exclusivamente los 1,484 candidatos `COHERENTE_TECNICAMENTE` de la validación anterior.
- Marca/familia incluida: SCOTT / ROPA (clasificación técnica previa); los grupos internos corresponden a Apparel.
- No se consultó Odoo, no se alteró MySQL y no se modificó clasificación productiva.
- No se propone ningún Submodelo nuevo.

## Resultado de validación comercial

- APROBADO: 604.
- RECHAZADO: 24.
- DUDOSO: 856.
- Total: 1484.

Un APROBADO aquí es una simulación conservadora, no una modificación al catálogo ni al Dashboard.

## Desglose por familia real

| Familia | Candidatos | Aprobados | Rechazados | Dudosos | Líneas de venta |
|---|---:|---:|---:|---:|---:|
| ROPA | 805 | 68 | 23 | 714 | 1596 |
| GUANTES | 240 | 203 | 0 | 37 | 1004 |
| ZAPATOS | 237 | 206 | 0 | 31 | 696 |
| CASCO | 154 | 104 | 0 | 50 | 1128 |
| LENTES | 32 | 19 | 1 | 12 | 96 |
| PROTECCIONES | 16 | 4 | 0 | 12 | 19 |

## Patrones aplicados

1. Se rechaza un candidato si coincide exactamente con un nivel de navegación observado o tiene formato de talla/color.
2. Se aprueba sólo un nombre compuesto que incluye un marcador comercial observable, por ejemplo un código, número o calificador comercial.
3. Un nombre de una sola palabra, aunque se repita en ruta, plantilla y variante, permanece DUDOSO. Esa evidencia no distingue con certeza una línea comercial de una característica de producto.
4. No se infirió información desde SKU, color, talla ni Submodelo.

## Principales candidatos por impacto

| Modelo candidato | Productos | Líneas de venta | Pzas | Validación |
|---|---:|---:|---|---|
| ENDURANCE | 154 | 388 | No disponible | DUDOSO |
| SUPRA | 22 | 346 | No disponible | DUDOSO |
| VERTIC | 156 | 289 | No disponible | DUDOSO |
| ESSENTIAL GEL SF | 24 | 191 | No disponible | APROBADO |
| ASPECT GEL SF | 30 | 149 | No disponible | APROBADO |
| TRAIL VERTIC | 49 | 130 | No disponible | DUDOSO |
| ULTIMATED | 24 | 124 | No disponible | DUDOSO |
| SUPRA PLUS | 16 | 122 | No disponible | APROBADO |
| ENDURANCE SF | 23 | 121 | No disponible | APROBADO |
| ROAD COMP BOA | 24 | 112 | No disponible | APROBADO |
| DEFINED DRI | 49 | 103 | No disponible | DUDOSO |
| TAGO PLUS | 16 | 103 | No disponible | APROBADO |
| ARGO PLUS | 13 | 99 | No disponible | APROBADO |
| TRACTION LF | 38 | 95 | No disponible | APROBADO |
| ICON | 35 | 85 | No disponible | DUDOSO |
| EXPLORAIR | 59 | 84 | No disponible | DUDOSO |
| ENDURANCE LF | 22 | 81 | No disponible | APROBADO |
| CRUS-R | 29 | 80 | No disponible | APROBADO |
| CRUS-R BOA | 21 | 80 | No disponible | APROBADO |
| BACKPRINT | 35 | 79 | No disponible | DUDOSO |

## Principales modelos aprobados por líneas de venta

| Modelo candidato | Productos | Líneas de venta |
|---|---:|---:|
| ESSENTIAL GEL SF | 24 | 191 |
| ASPECT GEL SF | 30 | 149 |
| SUPRA PLUS | 16 | 122 |
| ENDURANCE SF | 23 | 121 |
| ROAD COMP BOA | 24 | 112 |
| TAGO PLUS | 16 | 103 |
| ARGO PLUS | 13 | 99 |
| TRACTION LF | 38 | 95 |
| ENDURANCE LF | 22 | 81 |
| CRUS-R | 29 | 80 |
| CRUS-R BOA | 21 | 80 |
| CENTRIC PLUS | 13 | 77 |
| SPORT TRAIL EVO BOA | 20 | 74 |
| RC PRO LF | 17 | 63 |
| GROOVE PLUS | 7 | 63 |

## Casos rechazados

| Valor rechazado | Productos | Familia | Motivo |
|---|---:|---|---|
| CASUAL | 18 | ROPA | Coincide con un nivel estructural real de familia, categoría, uso o tipo de producto. |
| SHORT | 4 | ROPA | Coincide con un nivel estructural real de familia, categoría, uso o tipo de producto. |
| CABEZA | 1 | ROPA | Coincide con un nivel estructural real de familia, categoría, uso o tipo de producto. |
| SHIELD | 1 | LENTES | Coincide con un nivel estructural real de familia, categoría, uso o tipo de producto. |

## Principales casos dudosos

| Modelo candidato | Ruta | Plantilla | Variante | Motivo de duda |
|---|---|---|---|---|
| SUPRA | SCOTT / APPAREL / CASCO / ACTIVE / SUPRA | CASCO SCOTT SUPRA OS 22 SIN CAJA | CASCO SCOTT SUPRA OS 22 SIN CAJA | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| SUPRA | SCOTT / APPAREL / CASCO / ACTIVE / SUPRA | CASCO SCOTT SUPRA UNITALLA 23 | CASCO SCOTT SUPRA UNITALLA 23 | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| SUPRA | SCOTT / APPAREL / CASCO / ACTIVE / SUPRA | CASCO SCOTT SUPRA UNITALLA 23 | CASCO SCOTT SUPRA UNITALLA 23 | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| SUPRA | SCOTT / APPAREL / CASCO / ACTIVE / SUPRA | CASCO SCOTT SUPRA OS 22 SIN CAJA | CASCO SCOTT SUPRA OS 22 SIN CAJA | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| SUPRA | SCOTT / APPAREL / CASCO / ACTIVE / SUPRA | CASCO SCOTT SUPRA OS 22 SIN CAJA | CASCO SCOTT SUPRA OS 22 SIN CAJA | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| SUPRA | SCOTT / APPAREL / CASCO / ACTIVE / SUPRA | CASCO SCOTT SUPRA UNITALLA 23 | CASCO SCOTT SUPRA UNITALLA 23 | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| SUPRA | SCOTT / APPAREL / CASCO / ACTIVE / SUPRA | CASCO SCOTT SUPRA UNITALLA 23 | CASCO SCOTT SUPRA UNITALLA 23 | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| SPUNTO KID | SCOTT / APPAREL / CASCO / JUNIOR / SPUNTO KID | CASCO SCOTT SPUNTO KID OS RJ 23 | CASCO SCOTT SPUNTO KID OS RJ 23 | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| SUPRA | SCOTT / APPAREL / CASCO / ACTIVE / SUPRA | CASCO SCOTT SUPRA OS 22 SIN CAJA | CASCO SCOTT SUPRA OS 22 SIN CAJA | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| SPUNTO JUNIOR | SCOTT / APPAREL / CASCO / JUNIOR / SPUNTO JUNIOR | CASCO SCOTT SPUNTO JUNIOR OS RC 22 | CASCO SCOTT SPUNTO JUNIOR OS RC 22 | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| SUPRA | SCOTT / APPAREL / CASCO / ACTIVE / SUPRA | CASCO SCOTT SUPRA OS 22 SIN CAJA | CASCO SCOTT SUPRA OS 22 SIN CAJA | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| SUPRA | SCOTT / APPAREL / CASCO / ACTIVE / SUPRA | CASCO SCOTT SUPRA OS 22 SIN CAJA | CASCO SCOTT SUPRA OS 22 SIN CAJA | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| SPUNTO JUNIOR | SCOTT / APPAREL / CASCO / JUNIOR / SPUNTO JUNIOR | CASCO SCOTT SPUNTO JUNIOR OS RC 22 | CASCO SCOTT SPUNTO JUNIOR OS RC 22 | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| SIERRA | SCOTT / APPAREL / CASCO / PERFORMANCE / SIERRA | 425174 CASCO SCOTT SIERRA MIPS | 425174 CASCO SCOTT SIERRA MIPS | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| SIERRA | SCOTT / APPAREL / CASCO / PERFORMANCE / SIERRA | 425174 CASCO SCOTT SIERRA MIPS | 425174 CASCO SCOTT SIERRA MIPS | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| ULTIMATED | SCOTT / APPAREL / GUANTES / RACING / ULTIMATED | 419385 GUANTES SCOTT ULTIMATED. SF | 419385 GUANTES SCOTT ULTIMATED. SF | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| ENDURANCE | SCOTT / APPAREL / ROPA / JERSEY / ENDURANCE | 424664 JERSEY SCOTT M's RC ENDURANCE LS | 424664 JERSEY SCOTT M's RC ENDURANCE LS | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| ULTIMATED | SCOTT / APPAREL / GUANTES / RACING / ULTIMATED | 419385 GUANTES SCOTT ULTIMATED. SF | 419385 GUANTES SCOTT ULTIMATED. SF | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| ULTIMATED | SCOTT / APPAREL / GUANTES / RACING / ULTIMATED | 419385 GUANTES SCOTT ULTIMATED. SF | 419385 GUANTES SCOTT ULTIMATED. SF | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| ULTIMATED | SCOTT / APPAREL / GUANTES / RACING / ULTIMATED | 419386 GUANTES SCOTT ULTIMATED. LF | 419386 GUANTES SCOTT ULTIMATED. LF | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| ENDURANCE | SCOTT / APPAREL / ROPA / JERSEY / ENDURANCE | 424664 JERSEY SCOTT M's RC ENDURANCE LS | 424664 JERSEY SCOTT M's RC ENDURANCE LS | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| SUPRA | SCOTT / APPAREL / CASCO / ACTIVE / SUPRA | CASCO SCOTT SUPRA OS 22 SIN CAJA | CASCO SCOTT SUPRA OS 22 SIN CAJA | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| TRAIL VERTIC | SCOTT / APPAREL / ROPA / JERSEY / TRAIL VERTIC | 421412 JERSEY SCOTT TRAIL VERTIC PRO LS | 421412 JERSEY SCOTT TRAIL VERTIC PRO LS | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| ULTIMATED | SCOTT / APPAREL / GUANTES / RACING / ULTIMATED | 419385 GUANTES SCOTT ULTIMATED. SF | 419385 GUANTES SCOTT ULTIMATED. SF | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| ULTIMATED | SCOTT / APPAREL / GUANTES / RACING / ULTIMATED | 419385 GUANTES SCOTT ULTIMATED. SF | 419385 GUANTES SCOTT ULTIMATED. SF | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| ULTIMATED | SCOTT / APPAREL / GUANTES / RACING / ULTIMATED | 419386 GUANTES SCOTT ULTIMATED. LF | 419386 GUANTES SCOTT ULTIMATED. LF | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| VIVO | SCOTT / APPAREL / CASCO / MONTAÑA / VIVO | CASCO SCOTT VIVO 2021/22 | CASCO SCOTT VIVO 2021/22 | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| VIVO | SCOTT / APPAREL / CASCO / MONTAÑA / VIVO | CASCO SCOTT VIVO 2021/22 | CASCO SCOTT VIVO 2021/22 | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| ENDURANCE | SCOTT / APPAREL / ROPA / JERSEY / ENDURANCE | 424664 JERSEY SCOTT M's RC ENDURANCE LS | 424664 JERSEY SCOTT M's RC ENDURANCE LS | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |
| ENDURANCE | SCOTT / APPAREL / ROPA / JERSEY MUJER / ENDURANCE | 424686 JERSEY SCOTT DAMA ENDURANCE SS | 424686 JERSEY SCOTT DAMA ENDURANCE SS | El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; requiere catálogo o validación comercial. |

## Impacto simulado

- Productos Sin Modelo actuales: 2759.
- Modelos nuevos aprobados en la simulación: 604.
- Productos Sin Modelo esperados si sólo se aplicaran APROBADOS: 2155.
- Líneas de venta asociadas a los candidatos: 4539 de 19,468.
- Líneas de venta cubiertas por APROBADOS: 2256 de 19,468.
- Pzas no se calculan: detalle_productos.csv no contiene una métrica de cantidad vendida.

## Riesgos y recomendación

La coincidencia de categoría, plantilla y variante sigue siendo evidencia técnica, no un catálogo comercial. Los valores de una sola palabra pueden ser líneas comerciales, usos o características; por eso se mantienen como DUDOSO.
Recomendación: opción B. Sólo los grupos aprobados por el criterio conservador podrían pasar a una revisión comercial final; no existe evidencia suficiente para automatizar los 1,484 candidatos ni una regla segura para producción sin esa aprobación.
