# Revisión manual agrupada de Modelos dudosos

## Alcance y controles

- Productos dudosos analizados: 856.
- Nombres de Modelo candidato únicos: 42.
- Fuente: `validacion_comercial_modelos.csv`, filtrada exclusivamente a `DUDOSO`.
- No se tomó ninguna decisión automática y no se propuso Submodelo.
- Las columnas `decision_manual` y `comentario_manual` quedan deliberadamente vacías para revisión humana.
- Valores permitidos para `decision_manual`: `APROBAR`, `RECHAZAR`, `MANTENER_DUDOSO`.

## Top 30 por impacto

| Prioridad | Modelo candidato | Familia | Productos | Líneas de venta |
|---|---|---|---:|---:|
| P01 | ENDURANCE | ROPA | 154 | 388 |
| P02 | SUPRA | CASCO | 22 | 346 |
| P03 | VERTIC | ROPA | 156 | 289 |
| P04 | TRAIL VERTIC | ROPA | 49 | 130 |
| P05 | ULTIMATED | GUANTES | 24 | 124 |
| P06 | DEFINED DRI | ROPA | 49 | 103 |
| P07 | ICON | ROPA | 35 | 85 |
| P08 | EXPLORAIR | ROPA | 59 | 84 |
| P09 | BACKPRINT | ROPA | 35 | 79 |
| P10 | NO SHORTCUTS | ROPA | 36 | 75 |
| P11 | VOLT | ZAPATOS | 25 | 69 |
| P12 | TYPO | ROPA | 31 | 65 |
| P13 | SIERRA | CASCO | 15 | 64 |
| P14 | DEFINED | ROPA | 35 | 46 |
| P15 | GRAPHIC | ROPA | 22 | 38 |
| P16 | TUNED | ROPA | 16 | 37 |
| P17 | AUTENTHIC | ROPA | 14 | 33 |
| P18 | SPUNTO JUNIOR | CASCO | 2 | 25 |
| P19 | JIBE | CASCO | 6 | 24 |
| P20 | VIVO | CASCO | 4 | 22 |
| P21 | SPUNTO KID | CASCO | 1 | 22 |
| P22 | NEORIDE | GUANTES | 12 | 19 |
| P23 | SOFTCON | PROTECCIONES | 8 | 11 |
| P24 | SWISH | LENTES | 3 | 9 |
| P25 | TRAIL TUNED | ROPA | 7 | 8 |
| P26 | TRAIL TUNED SS | ROPA | 5 | 8 |
| P27 | TORICA | LENTES | 3 | 6 |
| P28 | MTB RC | ZAPATOS | 4 | 4 |
| P29 | TORICA LS | LENTES | 1 | 4 |
| P30 | GRAVEL TUNED SS | ROPA | 3 | 3 |

- Productos cubiertos por el Top 30: 836 de 856.
- Líneas de venta cubiertas por el Top 30: 2220 de 2245.
- Porcentaje acumulado de líneas: 98.9%.

## Desglose por familia

| Familia | Modelos candidatos | Productos dudosos | Líneas de venta |
|---|---:|---:|---:|
| ROPA | 21 | 714 | 1480 |
| CASCO | 6 | 50 | 503 |
| GUANTES | 3 | 37 | 146 |
| ZAPATOS | 3 | 31 | 75 |
| LENTES | 6 | 12 | 26 |
| PROTECCIONES | 3 | 12 | 15 |

## Uso de la revisión

Revisar cada fila del CSV por nombre comercial, usando las rutas, plantillas y variantes de ejemplo. La prioridad sólo ordena el impacto comercial y no equivale a una aprobación.
