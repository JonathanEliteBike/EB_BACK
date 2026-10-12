# Validación de reglas de Modelo/Submodelo — Bloque 3

## A. Resumen y conciliación

- Universo validado: 3373 productos.
- Bicicletas preservadas: 624.
- Servicios/soporte no aplicables: 12.
- Candidatos técnicamente coherentes: 1484.
- Candidatos con variante distinta: 0.
- Candidatos rechazados automáticamente: 20.
- Pendientes sin evidencia/ruta corta/Vittoria: 1233.
- Confianza alta / media / baja: 636 / 1484 / 1253.
- Requieren aprobación comercial: 2737.

La diferencia de 12 productos del Bloque 2 se concilia así: son servicios o soporte de ventas marcados como `No aplica`. Las categorías “conserva”, “nuevo candidato”, “sin evidencia” y “No aplica” son excluyentes; la confianza es una métrica independiente.

## B. Evaluación de candidatos

Los candidatos nacen de la coincidencia completa entre un nivel profundo de categoría y la plantilla. Esta prueba es técnica, no comercial: los candidatos coherentes conservan confianza media y requieren aprobación.

## C. Matriz de reglas recomendadas

| Familia | Modelo | Submodelo | Estado recomendado |
|---|---|---|---|
| Bicicletas | Conservar regla actual | Conservar regla actual | Implementable sin cambio funcional |
| Ropa / cascos / guantes / zapatos | Nivel profundo de categoría solo si coincide con plantilla y variante | Solo versión comercial explícita, nunca Color/Talla | Candidato, aprobación comercial |
| Vittoria llantas/cámaras/Air Liners | No inferir desde texto/SKU | No inferir | Requiere catálogo o equivalencias aprobadas |
| Syncros componentes/accesorios | No inferir con rutas cortas | No inferir | Pendiente de estructura comercial |
| Refacciones/cuadros | No forzar | No forzar | Pendiente; revisar ruta corta coherente |
| Servicios | No aplica | No aplica | Aprobable como no aplicable |

## D. Vittoria

- Productos Vittoria analizados: 127.
- Pendientes por falta de estructura: 121.
- Las categorías observadas no separan de manera verificable línea comercial y versión. Los nombres/plantillas pueden contener textos comerciales, pero sin atributo estructurado ni equivalencia aprobada no se convierten en Modelo.

## E. Riesgos que no deben automatizarse

- Usar el último segmento de categoría como Modelo sin corroboración.
- Extraer líneas Vittoria por coincidencias parciales de texto o SKU.
- Convertir Color, Talla, género, disciplina o característica técnica en Modelo/Submodelo.
- Tratar coincidencia con plantilla como aprobación comercial definitiva.

## F. Posibles problemas de catálogo

| SKU | Marca/Familia | Modelo candidato | Resultado | Motivo |
|---|---|---|---|---|
| 424829-0001128 | SCOTT / ROPA | JUNIOR | RECHAZAR_CANDIDATO_GENERICO | El valor candidato es una categoría amplia o parece una talla; no debe convertirse automáticamente en Modelo. |
| 424829-0001140 | SCOTT / ROPA | JUNIOR | RECHAZAR_CANDIDATO_GENERICO | El valor candidato es una categoría amplia o parece una talla; no debe convertirse automáticamente en Modelo. |
| 424829-0001152 | SCOTT / ROPA | JUNIOR | RECHAZAR_CANDIDATO_GENERICO | El valor candidato es una categoría amplia o parece una talla; no debe convertirse automáticamente en Modelo. |
| 424829-0001164 | SCOTT / ROPA | JUNIOR | RECHAZAR_CANDIDATO_GENERICO | El valor candidato es una categoría amplia o parece una talla; no debe convertirse automáticamente en Modelo. |
| 424829-8232128 | SCOTT / ROPA | JUNIOR | RECHAZAR_CANDIDATO_GENERICO | El valor candidato es una categoría amplia o parece una talla; no debe convertirse automáticamente en Modelo. |
| 424829-8232140 | SCOTT / ROPA | JUNIOR | RECHAZAR_CANDIDATO_GENERICO | El valor candidato es una categoría amplia o parece una talla; no debe convertirse automáticamente en Modelo. |
| 424829-8232152 | SCOTT / ROPA | JUNIOR | RECHAZAR_CANDIDATO_GENERICO | El valor candidato es una categoría amplia o parece una talla; no debe convertirse automáticamente en Modelo. |
| 424829-8232164 | SCOTT / ROPA | JUNIOR | RECHAZAR_CANDIDATO_GENERICO | El valor candidato es una categoría amplia o parece una talla; no debe convertirse automáticamente en Modelo. |
| 424829-8243128 | SCOTT / ROPA | JUNIOR | RECHAZAR_CANDIDATO_GENERICO | El valor candidato es una categoría amplia o parece una talla; no debe convertirse automáticamente en Modelo. |
| 424829-8243140 | SCOTT / ROPA | JUNIOR | RECHAZAR_CANDIDATO_GENERICO | El valor candidato es una categoría amplia o parece una talla; no debe convertirse automáticamente en Modelo. |
| 424829-8243152 | SCOTT / ROPA | JUNIOR | RECHAZAR_CANDIDATO_GENERICO | El valor candidato es una categoría amplia o parece una talla; no debe convertirse automáticamente en Modelo. |
| 424829-8243164 | SCOTT / ROPA | JUNIOR | RECHAZAR_CANDIDATO_GENERICO | El valor candidato es una categoría amplia o parece una talla; no debe convertirse automáticamente en Modelo. |
| 427185-0001410 | SCOTT / ROPA | TRAIL | RECHAZAR_CANDIDATO_GENERICO | El valor candidato es una categoría amplia o parece una talla; no debe convertirse automáticamente en Modelo. |
| 427185-0001420 | SCOTT / ROPA | TRAIL | RECHAZAR_CANDIDATO_GENERICO | El valor candidato es una categoría amplia o parece una talla; no debe convertirse automáticamente en Modelo. |
| 427185-0001430 | SCOTT / ROPA | TRAIL | RECHAZAR_CANDIDATO_GENERICO | El valor candidato es una categoría amplia o parece una talla; no debe convertirse automáticamente en Modelo. |
| 427185-0001440 | SCOTT / ROPA | TRAIL | RECHAZAR_CANDIDATO_GENERICO | El valor candidato es una categoría amplia o parece una talla; no debe convertirse automáticamente en Modelo. |
| 427185-0001450 | SCOTT / ROPA | TRAIL | RECHAZAR_CANDIDATO_GENERICO | El valor candidato es una categoría amplia o parece una talla; no debe convertirse automáticamente en Modelo. |
| 427185-0009420 | SCOTT / ROPA | TRAIL | RECHAZAR_CANDIDATO_GENERICO | El valor candidato es una categoría amplia o parece una talla; no debe convertirse automáticamente en Modelo. |
| 427185-0009430 | SCOTT / ROPA | TRAIL | RECHAZAR_CANDIDATO_GENERICO | El valor candidato es una categoría amplia o parece una talla; no debe convertirse automáticamente en Modelo. |
| 427185-0009440 | SCOTT / ROPA | TRAIL | RECHAZAR_CANDIDATO_GENERICO | El valor candidato es una categoría amplia o parece una talla; no debe convertirse automáticamente en Modelo. |

## G. Desglose por marca y familia

| Marca / familia | Productos | Coherentes | Pendientes |
|---|---:|---:|---:|
| SCOTT / ROPA | 2198 | 1484 | 694 |
| SCOTT / BICICLETA | 478 | 0 | 0 |
| MEGAMO / BICICLETA | 135 | 0 | 0 |
| SCOTT / REFACCIONES | 131 | 0 | 131 |
| SYNCROS / COMPONENTES | 106 | 0 | 106 |
| VITTORIA / LLANTAS | 95 | 0 | 95 |
| SYNCROS / ACCESORIOS | 94 | 0 | 94 |
| SCOTT / CUADRO | 20 | 0 | 20 |
| VITTORIA / AIR LINERS | 14 | 0 | 14 |
| SYNCROS / BOLSAS Y MOCHILAS | 11 | 0 | 11 |
| BOLD / BICICLETA | 10 | 0 | 0 |
| Sin marca / SERVICIOS | 7 | 0 | 0 |
| Sin marca / Sin producto | 7 | 0 | 7 |
| VITTORIA / DIVERSOS | 7 | 0 | 7 |
| SCOTT / ACCESORIOS | 6 | 0 | 6 |
| SCOTT / KIT DE CONVERSIÓN | 6 | 0 | 6 |
| VITTORIA / Sin producto | 6 | 0 | 6 |
| SCOTT / SOPORTE DE VENTAS | 5 | 0 | 0 |
| VITTORIA / CAMARAS | 5 | 0 | 5 |
| AVINOX / REFACCIONES | 4 | 0 | 4 |
| SCOTT / RUNNING | 3 | 0 | 3 |
| SYNCROS / DIVERSOS | 3 | 0 | 3 |
| AVINOX / CARGADOR | 2 | 0 | 2 |
| BOLD / DIVERSOS | 2 | 0 | 2 |
| BOLD / REFACCIONES | 2 | 0 | 2 |
| MEGAMO / ACCESORIOS | 2 | 0 | 2 |
| MEGAMO / COMPONENTES | 2 | 0 | 2 |
| AVINOX / BATERIA | 1 | 0 | 1 |
| AVINOX / CONTROL | 1 | 0 | 1 |
| AVINOX / PANTALLA | 1 | 0 | 1 |
| BOSCH / CABLES | 1 | 0 | 1 |
| BOSCH / MOTOR | 1 | 0 | 1 |
| LOOK / BICICLETA | 1 | 0 | 0 |
| LOOK / PEDALES | 1 | 0 | 1 |
| LOOK / PLACAS | 1 | 0 | 1 |
| SCOTT / COMPONENTES | 1 | 0 | 1 |
| SCOTT / Sin producto | 1 | 0 | 1 |
| SYNCROS / Sin producto | 1 | 0 | 1 |
| Sin marca / PERIFERICOS | 1 | 0 | 1 |

## H. Recomendación para el backend

No implementar aún las propuestas de ropa. Cuando exista aprobación comercial, el clasificador debe añadir una regla general por estructura de categoría y coincidencia con plantilla, manteniendo bicicletas sin cambio y dejando los casos ambiguos sin valor.

## I. Decisiones comerciales pendientes

1. Confirmar que el tercer nivel comercial de Apparel representa Modelo en cada subfamilia.
2. Definir si existe Submodelo para Apparel más allá de Color/Talla.
3. Proporcionar un catálogo o equivalencias aprobadas para Vittoria.
4. Decidir si las rutas cortas de cuadros/refacciones deben exponer Modelo o permanecer sin aplicación.
