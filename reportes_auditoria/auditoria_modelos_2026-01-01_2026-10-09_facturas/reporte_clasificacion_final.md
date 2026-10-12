# Consolidación final de clasificación comercial — Auditoría

## Alcance

- Universo: candidatos SCOTT / ROPA de la auditoría comercial.
- Total de productos consolidados: 1484.
- Fuente manual: 42 decisiones agrupadas aplicadas exclusivamente a los 856 productos DUDOSO.
- No se modificó producción, Odoo, MySQL, Dashboard ni reglas productivas.
- No se generaron Submodelos.

## Resultado final

| Clasificación final | Productos | Líneas de venta |
|---|---:|---:|
| MODELO_CONFIRMADO | 916 | 3388 |
| LINEA_CONFIRMADA | 541 | 1104 |
| POR_VALIDAR | 3 | 9 |
| RECHAZADO | 24 | 38 |
| NO_ESPECIFICADO | 0 | 0 |
| Total | 1484 | 4539 |

## Impacto simulado para el Dashboard

- Productos Sin Modelo al inicio de este universo: 1484.
- Productos que dejarían de aparecer como Sin Modelo: 916.
- Productos que seguirían sin Modelo pero tendrían Referencia comercial: 544.
- Productos realmente No especificados: 0.
- Productos que siguen Por validar: 3.

## Simulación de presentación posterior

| Clasificación final | Modelo | Referencia comercial |
|---|---|---|
| MODELO_CONFIRMADO | nombre confirmado | vacío |
| LINEA_CONFIRMADA | vacío | nombre · Línea |
| POR_VALIDAR | vacío | nombre · Por validar |
| NO_ESPECIFICADO | vacío | No especificado |
| RECHAZADO | vacío | vacío |

## Recomendación para el siguiente bloque

Revisar y aprobar el mapeo de presentación Modelo/Referencia comercial antes de tocar `routes/ventas.py`. La siguiente implementación debe consumir esta consolidación como especificación, no volver a inferir nombres desde rutas de categoría.
