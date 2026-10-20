# Simulación de clasificación Modelo/Submodelo

## Validación de fuentes

- Periodo auditado: 2026-01-01 a 2026-10-09
- Vista auditada: facturas
- detalle_productos.csv: 3373 productos únicos y 19468 líneas.
- resumen_clasificacion.csv: los mismos totales y faltantes (2759 modelo, 2766 submodelo).

No se detectaron diferencias; la simulación usa el universo completo de detalle_productos.csv.

## Resultado general

- Productos analizados: 3373
- Conservan clasificación: 624
- Nuevo modelo candidato: 1504
- Nuevo submodelo candidato: 0
- Sin información suficiente: 1233
- Confianza alta / media / baja: 636 / 1504 / 1233
- Requieren validación comercial: 2737

## Reglas simuladas

1. Bicicletas: se conserva literalmente Modelo y Submodelo actuales.
2. Servicios y soporte de ventas: Modelo/Submodelo = No aplica.
3. Otras familias: se propone Modelo solo cuando el tercer nivel comercial después de la familia aparece también en la plantilla.
4. Se propone Submodelo solo cuando un cuarto nivel también aparece en la plantilla.
5. SKU, Color y Talla no participan en la inferencia.
6. Una coincidencia categoría/plantilla es confianza media y requiere validación comercial; ausencia de evidencia permanece pendiente.

## Comparativa por marca y familia

| Marca | Familia | Productos | Conservan | Modelo candidato | Submodelo candidato | Pendientes | Alta | Media | Baja |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| SCOTT | ROPA | 2198 | 0 | 1504 | 0 | 694 | 0 | 1504 | 694 |
| SCOTT | BICICLETA | 478 | 478 | 0 | 0 | 0 | 478 | 0 | 0 |
| MEGAMO | BICICLETA | 135 | 135 | 0 | 0 | 0 | 135 | 0 | 0 |
| SCOTT | REFACCIONES | 131 | 0 | 0 | 0 | 131 | 0 | 0 | 131 |
| SYNCROS | COMPONENTES | 106 | 0 | 0 | 0 | 106 | 0 | 0 | 106 |
| VITTORIA | LLANTAS | 95 | 0 | 0 | 0 | 95 | 0 | 0 | 95 |
| SYNCROS | ACCESORIOS | 94 | 0 | 0 | 0 | 94 | 0 | 0 | 94 |
| SCOTT | CUADRO | 20 | 0 | 0 | 0 | 20 | 0 | 0 | 20 |
| VITTORIA | AIR LINERS | 14 | 0 | 0 | 0 | 14 | 0 | 0 | 14 |
| SYNCROS | BOLSAS Y MOCHILAS | 11 | 0 | 0 | 0 | 11 | 0 | 0 | 11 |
| BOLD | BICICLETA | 10 | 10 | 0 | 0 | 0 | 10 | 0 | 0 |
| Sin marca | SERVICIOS | 7 | 0 | 0 | 0 | 0 | 7 | 0 | 0 |
| Sin marca | Sin producto | 7 | 0 | 0 | 0 | 7 | 0 | 0 | 7 |
| VITTORIA | DIVERSOS | 7 | 0 | 0 | 0 | 7 | 0 | 0 | 7 |
| SCOTT | ACCESORIOS | 6 | 0 | 0 | 0 | 6 | 0 | 0 | 6 |
| SCOTT | KIT DE CONVERSIÓN | 6 | 0 | 0 | 0 | 6 | 0 | 0 | 6 |
| VITTORIA | Sin producto | 6 | 0 | 0 | 0 | 6 | 0 | 0 | 6 |
| SCOTT | SOPORTE DE VENTAS | 5 | 0 | 0 | 0 | 0 | 5 | 0 | 0 |
| VITTORIA | CAMARAS | 5 | 0 | 0 | 0 | 5 | 0 | 0 | 5 |
| AVINOX | REFACCIONES | 4 | 0 | 0 | 0 | 4 | 0 | 0 | 4 |
| SCOTT | RUNNING | 3 | 0 | 0 | 0 | 3 | 0 | 0 | 3 |
| SYNCROS | DIVERSOS | 3 | 0 | 0 | 0 | 3 | 0 | 0 | 3 |
| AVINOX | CARGADOR | 2 | 0 | 0 | 0 | 2 | 0 | 0 | 2 |
| BOLD | DIVERSOS | 2 | 0 | 0 | 0 | 2 | 0 | 0 | 2 |
| BOLD | REFACCIONES | 2 | 0 | 0 | 0 | 2 | 0 | 0 | 2 |
| MEGAMO | ACCESORIOS | 2 | 0 | 0 | 0 | 2 | 0 | 0 | 2 |
| MEGAMO | COMPONENTES | 2 | 0 | 0 | 0 | 2 | 0 | 0 | 2 |
| AVINOX | BATERIA | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 1 |
| AVINOX | CONTROL | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 1 |
| AVINOX | PANTALLA | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 1 |
| BOSCH | CABLES | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 1 |
| BOSCH | MOTOR | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 1 |
| LOOK | BICICLETA | 1 | 1 | 0 | 0 | 0 | 1 | 0 | 0 |
| LOOK | PEDALES | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 1 |
| LOOK | PLACAS | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 1 |
| SCOTT | COMPONENTES | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 1 |
| SCOTT | Sin producto | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 1 |
| SYNCROS | Sin producto | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 1 |
| Sin marca | PERIFERICOS | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 1 |

## Casos ambiguos que requieren revisión

| SKU | Marca / Familia | Categoría | Plantilla | Motivo |
|---|---|---|---|---|
| ME-CO-17966 | AVINOX / BATERIA | AVINOX / BATERIA | BATERIA AVINOX 800 WH (FIJA) 2026 | No se pudo identificar una estructura comercial de categoría compatible con la familia. |
| ME-CO-15283 | AVINOX / CARGADOR | AVINOX / CARGADOR | CARGADOR AVINOX 12A | No se pudo identificar una estructura comercial de categoría compatible con la familia. |
| ME-CO-17980 | AVINOX / CARGADOR | AVINOX / CARGADOR | CARGADOR PARA BATERIA AVINOX 4A 2026 | No se pudo identificar una estructura comercial de categoría compatible con la familia. |
| ME-CO-17997 | AVINOX / CONTROL | AVINOX / CONTROL | CONTROL INHALAMBRICO AVINOX BC100 (IZQUIERDO) 2026 | No se pudo identificar una estructura comercial de categoría compatible con la familia. |
| ME-CO-17973 | AVINOX / PANTALLA | AVINOX / PANTALLA | PANTALLA AVINOX DP100 2026 | No se pudo identificar una estructura comercial de categoría compatible con la familia. |
| ME-CO-18017 | AVINOX / REFACCIONES | AVINOX / REEMPLAZOS | CABLE DE SENSOR DE VELOCIDAD AVINOX (800MM) 2026 | No se pudo identificar una estructura comercial de categoría compatible con la familia. |
| ME-CO-18024 | AVINOX / REFACCIONES | AVINOX / REEMPLAZOS | CABLE PARA PANTALLA A MOTOR (900MM) 2026 | No se pudo identificar una estructura comercial de categoría compatible con la familia. |
| ME-CO-18031 | AVINOX / REFACCIONES | AVINOX / REEMPLAZOS | ANILLO SENSOR DE VELOCIDAD AVINOX DE 6 TORNILLOS | No se pudo identificar una estructura comercial de categoría compatible con la familia. |
| ME-CO-18062 | AVINOX / REFACCIONES | AVINOX / REEMPLAZOS | ANILLO SENSOR DE VELOCIDAD AVINOX CENTERLOCK | No se pudo identificar una estructura comercial de categoría compatible con la familia. |
| BLDHE672-9999222 | BOLD / DIVERSOS | BOLD / DIVERSOS | HERRAMIENTA PARA INSTALAR BALEROS 292672 | No se pudo identificar una estructura comercial de categoría compatible con la familia. |
| SCOAN949-5576630 | BOLD / DIVERSOS | BOLD / DIVERSOS | ANFORA BOLD CORP. PLUS 650ml PAQ-5NG22 | No se pudo identificar una estructura comercial de categoría compatible con la familia. |
| BLDRE219-9999222 | BOLD / REFACCIONES | BOLD / REEMPLAZOS | PALANCA BOLD TRACLOC LINKIN 22 291219 | No se pudo identificar una estructura comercial de categoría compatible con la familia. |
| SCORE748-9999006 | BOLD / REFACCIONES | BOLD / REEMPLAZOS | TAPA CABLES BOLD UNPLUGGED S 297748 | No se pudo identificar una estructura comercial de categoría compatible con la familia. |
| 294636-9999222 | BOSCH / CABLES | BOSCH / CABLES | CABLE BOSCH P SENSOR DE VELOCIDAD 294636 | No se pudo identificar una estructura comercial de categoría compatible con la familia. |
| SCORE404-9999222 | BOSCH / MOTOR | BOSCH / MOTOR | MOTOR BOSCH ERIDE CX 20MPH BS2 287404 | No se pudo identificar una estructura comercial de categoría compatible con la familia. |
| LOK14PE01N18321E | LOOK / PEDALES | LOOK / PEDALES | PEDAL LOOK KEO BLADE 2 CR 16 | No se pudo identificar una estructura comercial de categoría compatible con la familia. |
| LOK13AC24N160010 | LOOK / PLACAS | LOOK / PLACAS | PLACAS LOOK S-TRACK DCS | No se pudo identificar una estructura comercial de categoría compatible con la familia. |
| ME-CO-15306 | MEGAMO / ACCESORIOS | MEGAMO / ACCESORIOS VARIOS | HERRAMIENTA PARA ANILLO DE BLOQUEO AVINOX | No se pudo identificar una estructura comercial de categoría compatible con la familia. |
| TNT-004 | MEGAMO / ACCESORIOS | MEGAMO / ACCESORIOS VARIOS | KIT DE SERVICIO PARA REASON (26) MEGAMO | No se pudo identificar una estructura comercial de categoría compatible con la familia. |
| ME-CO-18499 | MEGAMO / COMPONENTES | MEGAMO / COMPONENTES | CARGADOR BOSCH 4A | No se pudo identificar una estructura comercial de categoría compatible con la familia. |
| ME-CO-18529 | MEGAMO / COMPONENTES | MEGAMO / COMPONENTES | BATERIA BOSCH 800 | No se pudo identificar una estructura comercial de categoría compatible con la familia. |
| 422716-9999222 | SCOTT / ACCESORIOS | SCOTT / ACCESORIOS / PROTECTOR DE CUADRO | 422716 CUBIERTA PARA CUADRO SCOTT SPARK 25 | La ruta no tiene suficientes niveles comerciales para separar modelo y submodelo sin inferencia. |
| SCOAC254-0001222 | SCOTT / ACCESORIOS | SCOTT / ACCESORIOS VARIOS / LAMPARAS | LUZ TRASERA SCOTT JR/KID NG GAR 278254 | La ruta no tiene suficientes niveles comerciales para separar modelo y submodelo sin inferencia. |
| SCOASAC155000122 | SCOTT / ACCESORIOS | SCOTT / ACCESORIOS VARIOS | PROTECTOR SCOTT RANSOM CB19 JG NG270155 | No se pudo identificar una estructura comercial de categoría compatible con la familia. |
| SCOASAC513XXXX22 | SCOTT / ACCESORIOS | SCOTT / ACCESORIOS VARIOS | PROTECTOR SCOTT SCALE NEOPREN TPU OS | No se pudo identificar una estructura comercial de categoría compatible con la familia. |
| SCOASAC514XXXX22 | SCOTT / ACCESORIOS | SCOTT / ACCESORIOS VARIOS | PROTECTOR SCOTT ASPECT NEOPRENO TPU OS | No se pudo identificar una estructura comercial de categoría compatible con la familia. |
| SCOASCA1970001OS | SCOTT / ACCESORIOS | SCOTT / ACCESORIOS VARIOS | PROTECTOR SCOTT P/CUADRO UNIV.NEOPREN NG | No se pudo identificar una estructura comercial de categoría compatible con la familia. |
| 271380-0001222 | SCOTT / COMPONENTES | SCOTT / COMPONENTES / CUBRE PLACAS | 271380 CUBIERTA PARA PLACAS SCOTT TRAIL DESDE 2019 40-48 | La ruta no tiene suficientes niveles comerciales para separar modelo y submodelo sin inferencia. |
| 266382-1457006 | SCOTT / CUADRO | SCOTT / CUADRO / MONTAÑA / SCALE | 266382 CUADRO SCOTT SCALE 960 18 GRIS/VERDE S wty | La ruta no tiene suficientes niveles comerciales para separar modelo y submodelo sin inferencia. |
| 271195-1018222 | SCOTT / CUADRO | SCOTT / CUADRO / PARTES PARA CUADRO / HORQUILLA | 271195 HORQUILLA SCOTT FOIL 30 19 ROJO/ NEGRO | La ruta no tiene suficientes niveles comerciales para separar modelo y submodelo sin inferencia. |

## Riesgos y siguiente paso

- La ruta de categoría no siempre representa una jerarquía de producto comercial.
- La coincidencia con plantilla es evidencia, no una confirmación de negocio.
- Vittoria y familias con rutas poco profundas permanecen pendientes; no se dedujo modelo desde SKU o texto libre.
- Antes de implementar, validar muestras por familia y comprobar que los totales de Pzas/Venta no cambian al reagrupar.
