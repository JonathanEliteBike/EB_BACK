# Auditoría de Submodelos — SCOTT / ROPA

## Universo

- Productos con Modelo confirmado analizados: 916.
- Productos actualmente sin Submodelo: 916.
- Bicicletas y productos sin Modelo confirmado quedan fuera de este bloque.
- No se consultó Odoo ni se alteró ninguna regla activa.

## Resultado

| Clasificación | Productos | Líneas de venta |
|---|---:|---:|
| SUBMODELO_APROBADO | 0 | 0 |
| RECHAZADO | 892 | 3317 |
| DUDOSO | 24 | 71 |

## Desglose por familia

| Familia | Productos | Aprobados | Rechazados | Dudosos |
|---|---:|---:|---:|---:|
| ROPA | 251 | 0 | 251 | 0 |
| GUANTES | 240 | 0 | 240 | 0 |
| ZAPATOS | 235 | 0 | 212 | 23 |
| CASCO | 154 | 0 | 154 | 0 |
| LENTES | 28 | 0 | 27 | 1 |
| PROTECCIONES | 8 | 0 | 8 | 0 |

## Top candidatos por impacto

| Modelo | Submodelo candidato | Estado | Productos | Líneas |
|---|---|---|---:|---:|
| SUPRA | SUPRA OS 22 SIN CAJA | RECHAZADO | 7 | 173 |
| SUPRA | SUPRA UNITALLA 23 | RECHAZADO | 6 | 149 |
| SUPRA PLUS | SUPRA PLUS 23 | RECHAZADO | 16 | 122 |
| ARGO PLUS | ARGO PLUS CE) 22 | RECHAZADO | 13 | 99 |
| ICON | ICON SS | RECHAZADO | 35 | 85 |
| BACKPRINT | BACKPRINT SS | RECHAZADO | 35 | 79 |
| CENTRIC PLUS | CENTRIC PLUS 21 | RECHAZADO | 13 | 77 |
| DEFINED DRI | DEFINED DRI SS | RECHAZADO | 37 | 76 |
| ULTIMATED | ULTIMATED SF | RECHAZADO | 12 | 66 |
| TYPO | TYPO SS | RECHAZADO | 31 | 65 |
| SIERRA | SIERRA MIPS | RECHAZADO | 15 | 64 |
| GROOVE PLUS | GROOVE PLUS 22 | RECHAZADO | 7 | 63 |
| ULTIMATED | ULTIMATED LF | RECHAZADO | 12 | 58 |
| MTB COMP | MTB COMP MY27 | RECHAZADO | 18 | 53 |
| STEGO PLUS | STEGO PLUS S MO | RECHAZADO | 10 | 43 |
| CRUS-R | CRUS-R BOA | RECHAZADO | 15 | 40 |
| GRAVEL PRO | GRAVEL PRO GRIS/NEGRO | RECHAZADO | 7 | 40 |
| ARX MIPS | ARX MIPS CE/CPSC | RECHAZADO | 12 | 37 |
| ARX PLUS | ARX PLUS S BC/NG | RECHAZADO | 4 | 35 |
| AUTENTHIC | AUTENTHIC SS | RECHAZADO | 14 | 33 |
| ROAD COMP BOA | ROAD COMP BOA NGO/PLATA | RECHAZADO | 5 | 29 |
| DEFINED DRI | DEFINED DRI LS | RECHAZADO | 12 | 27 |
| VOLT | VOLT EVO CLIP BOA | DUDOSO | 7 | 27 |
| ROAD COMP | ROAD COMP MY27 | RECHAZADO | 8 | 26 |
| SPUNTO JUNIOR | SPUNTO JUNIOR OS RC 22 | RECHAZADO | 2 | 25 |
| RC PRO | RC PRO SS | RECHAZADO | 21 | 24 |
| SPUNTO KID | SPUNTO KID OS RJ 23 | RECHAZADO | 1 | 22 |
| SUPRA | SUPRA UNITALLA caja c/10 | RECHAZADO | 6 | 21 |
| VIVO | VIVO 2021/22 | RECHAZADO | 3 | 20 |
| CRUS-R BOA | CRUS-R BOA ECO | RECHAZADO | 7 | 17 |

## Ejemplos

### SUBMODELO_APROBADO

| product_id | Modelo | Candidato | Plantilla | Motivo |
|---|---|---|---|---|
| - | - | - | - | Sin casos aprobados automáticamente bajo el criterio conservador. |

### RECHAZADO

| product_id | Modelo | Candidato | Plantilla | Motivo |
|---|---|---|---|---|
| 19873 | RC PRO | RC PRO MY27 | PAQUETE DE JERSEYS Y SHORTS CON TIRANTES SCOTT RC PRO MY27 | El texto adicional representa talla, color, género, año, código o tecnología aislada. |
| 6106 | AQUA GTX LF | - | GUANTES SCOTT AQUA GTX LF | No existe un nombre adicional posterior al Modelo en plantilla ni variante. |
| 14725 | ROAD COMP BOA | ROAD COMP BOA NGO/PLATA | 251817 ZAPATO SCOTT ROAD COMP BOA NGO/PLATA | El texto adicional representa talla, color, género, año, código o tecnología aislada. |
| 14726 | ROAD COMP BOA | ROAD COMP BOA NGO/PLATA | 251817 ZAPATO SCOTT ROAD COMP BOA NGO/PLATA | El texto adicional representa talla, color, género, año, código o tecnología aislada. |
| 14727 | ROAD COMP BOA | ROAD COMP BOA NGO/PLATA | 251817 ZAPATO SCOTT ROAD COMP BOA NGO/PLATA | El texto adicional representa talla, color, género, año, código o tecnología aislada. |

### DUDOSO

| product_id | Modelo | Candidato | Plantilla | Motivo |
|---|---|---|---|---|
| 17987 | MTB RC | MTB RC ULTIMATE | 296555 ZAPATOS SCOTT MTB RC ULTIMATE | El sufijo parece una versión potencial, pero no hay nivel posterior de categoría ni catálogo estructurado que lo confirme. |
| 17997 | VOLT | VOLT EVO FLAT | 421020 ZAPATO SCOTT VOLT EVO FLAT | El sufijo parece una versión potencial, pero no hay nivel posterior de categoría ni catálogo estructurado que lo confirme. |
| 17998 | VOLT | VOLT EVO FLAT | 421020 ZAPATO SCOTT VOLT EVO FLAT | El sufijo parece una versión potencial, pero no hay nivel posterior de categoría ni catálogo estructurado que lo confirme. |
| 17999 | VOLT | VOLT EVO FLAT | 421020 ZAPATO SCOTT VOLT EVO FLAT | El sufijo parece una versión potencial, pero no hay nivel posterior de categoría ni catálogo estructurado que lo confirme. |
| 18000 | VOLT | VOLT EVO FLAT | 421020 ZAPATO SCOTT VOLT EVO FLAT | El sufijo parece una versión potencial, pero no hay nivel posterior de categoría ni catálogo estructurado que lo confirme. |

## Impacto y recomendación

- Productos que podrían recibir Submodelo con evidencia actual suficiente: 0.
- Líneas de venta que quedarían más detalladas: 0.
- Riesgo principal: convertir mangas, ajuste, talla, color, año o tecnología en una versión comercial falsa.
- Recomendación del siguiente bloque: validar comercialmente los candidatos DUDOSO agrupados antes de incorporar cualquier mapeo de Submodelo por product_id.
