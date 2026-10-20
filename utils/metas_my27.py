"""Reglas de metas MY27 compartidas por el monitor de cumplimiento.

La lógica de este módulo replica la calculadora de retroactivos del frontend:
- ``clasificacion-model.ts``
- ``calculadora-retroactivos.component.ts``

La intención es que el backend deje de depender de metas MY26 persistidas en
``previo`` y pueda reconstruirlas de forma determinística a partir del nivel y
la cantidad de sucursales.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Dict


D = Decimal

# Mismo multiplicador de src/.../calculadora-retroactivos/models/sucursal.model.ts
MULTIPLICADOR_SUCURSALES = {
    1: D("1"),
    2: D("1.5"),
    3: D("2.25"),
    4: D("3"),
    5: D("3.75"),
    6: D("4.5"),
}

# Valores base de clasificacion-model.ts ANTES de inicializarAtributosClasificaciones().
_CONFIG_NIVELES = {
    "PARTNER ELITE PLUS": {
        "base_bicicletas": D("6000000"),
        "multiplo_bicicletas": D("1.15"),
        "multiplo_multimarca": D("0.089"),
        "porcentaje_inicial_bicicletas": D("0.65"),
        "porcentaje_inicial_multimarca": D("0.50"),
        "redondeo_bicicletas": D("10000"),
    },
    "PARTNER ELITE": {
        "base_bicicletas": D("2200000"),
        "multiplo_bicicletas": D("1.10"),
        "multiplo_multimarca": D("0.133"),
        "porcentaje_inicial_bicicletas": D("0.65"),
        "porcentaje_inicial_multimarca": D("0.50"),
        "redondeo_bicicletas": D("10000"),
    },
    "PARTNER": {
        "base_bicicletas": D("1500000"),
        "multiplo_bicicletas": D("1.05"),
        "multiplo_multimarca": D("0.12"),
        "porcentaje_inicial_bicicletas": D("0.65"),
        "porcentaje_inicial_multimarca": D("0.50"),
        "redondeo_bicicletas": D("5000"),
    },
    "DISTRIBUIDOR": {
        "base_bicicletas": D("450000"),
        "multiplo_bicicletas": D("1"),
        "multiplo_multimarca": D("0.13"),
        "porcentaje_inicial_bicicletas": D("0.65"),
        "porcentaje_inicial_multimarca": D("0.55"),
        "redondeo_bicicletas": D("10000"),
    },
}

_NOMBRE_CANONICO = {
    "PARTNER ELITE PLUS": "Partner Elite Plus",
    "PARTNER ELITE": "Partner Elite",
    "PARTNER": "Partner",
    "DISTRIBUIDOR": "Distribuidor",
}

# Porcentajes visibles en calculadora-retroactivos.component.ts
_P_BIMESTRES_BICI = {
    "jul_ago": D("0.33") * D("0.65"),
    "sep_oct": D("0.35") * D("0.65"),
    "nov_dic": D("0.32") * D("0.65"),
    "ene_feb": D("0.40") * D("0.35"),
    "mar_abr": D("0.45") * D("0.35"),
    "may_jun": D("0.15") * D("0.35"),
}

# La calculadora reparte Apparel/Syncros con 33/35/32 sobre la compra inicial
# de multimarca y repite ese patrón en el segundo semestre (HTML actual).
_P_BIMESTRES_APP = {
    "jul_ago": D("0.33"),
    "sep_oct": D("0.35"),
    "nov_dic": D("0.32"),
    "ene_feb": D("0.33"),
    "mar_abr": D("0.35"),
    "may_jun": D("0.32"),
}


def normalizar_nivel(nivel: str) -> str:
    return " ".join(str(nivel or "").strip().upper().split())


def _ceil_a_multiplo(valor: Decimal, multiplo: Decimal) -> Decimal:
    if multiplo <= 0:
        raise ValueError("El múltiplo de redondeo debe ser mayor que cero")
    return (valor / multiplo).to_integral_value(rounding=ROUND_CEILING) * multiplo


def _floor(valor: Decimal) -> Decimal:
    return valor.to_integral_value(rounding=ROUND_FLOOR)


def _dinero(valor: Decimal) -> Decimal:
    return valor.quantize(D("0.01"))


def calcular_metas_my27(nivel: str, numero_sucursales: int = 1) -> Dict[str, Decimal | str | int]:
    """Calcula las metas MY27 igual que la calculadora de retroactivos.

    Devuelve los nombres de columnas usados por ``previo``. No toca la base de
    datos y por ello se puede probar de forma aislada antes del despliegue.
    """
    nivel_norm = normalizar_nivel(nivel)
    if nivel_norm not in _CONFIG_NIVELES:
        raise ValueError(f"Nivel no soportado para MY27: {nivel!r}")

    try:
        sucursales = int(numero_sucursales or 1)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Cantidad de sucursales inválida: {numero_sucursales!r}") from exc

    if sucursales not in MULTIPLICADOR_SUCURSALES:
        raise ValueError(
            f"La calculadora MY27 solo define de 1 a 6 sucursales; recibido: {sucursales}"
        )

    cfg = _CONFIG_NIVELES[nivel_norm]
    mult_sucursal = MULTIPLICADOR_SUCURSALES[sucursales]

    # Equivale a inicializarAtributosClasificaciones().
    bici_base_ajustada = (cfg["base_bicicletas"] * cfg["multiplo_bicicletas"]).to_integral_value(
        rounding=ROUND_CEILING
    )
    multimarca_base_ajustada = _ceil_a_multiplo(
        bici_base_ajustada * cfg["multiplo_multimarca"], D("5000")
    )

    # Equivale a calcularDetalleRetroActivo()/obtenerCompraMinimaSimulador().
    compromiso_scott = _ceil_a_multiplo(
        bici_base_ajustada * mult_sucursal, cfg["redondeo_bicicletas"]
    )
    compromiso_app = _ceil_a_multiplo(
        multimarca_base_ajustada * mult_sucursal, D("10000")
    )

    compra_inicial_bici = _floor(
        compromiso_scott * cfg["porcentaje_inicial_bicicletas"]
    )
    compra_inicial_app = _floor(
        compromiso_app * cfg["porcentaje_inicial_multimarca"]
    )

    metas: Dict[str, Decimal | str | int] = {
        "nivel": _NOMBRE_CANONICO[nivel_norm],
        "numero_sucursales": sucursales,
        "compra_minima_anual": _dinero(compromiso_scott + compromiso_app),
        "compra_minima_inicial": _dinero(compra_inicial_bici + compra_inicial_app),
        "compromiso_scott": _dinero(compromiso_scott),
        "compromiso_apparel_syncros_vittoria": _dinero(compromiso_app),
    }

    for periodo, porcentaje in _P_BIMESTRES_BICI.items():
        metas[f"compromiso_{periodo}"] = _dinero(compromiso_scott * porcentaje)

    for periodo, porcentaje in _P_BIMESTRES_APP.items():
        metas[f"compromiso_{periodo}_app"] = _dinero(compra_inicial_app * porcentaje)

    return metas


CAMPOS_META_PREVIO = (
    "compra_minima_anual",
    "compra_minima_inicial",
    "compromiso_scott",
    "compromiso_jul_ago",
    "compromiso_sep_oct",
    "compromiso_nov_dic",
    "compromiso_ene_feb",
    "compromiso_mar_abr",
    "compromiso_may_jun",
    "compromiso_apparel_syncros_vittoria",
    "compromiso_jul_ago_app",
    "compromiso_sep_oct_app",
    "compromiso_nov_dic_app",
    "compromiso_ene_feb_app",
    "compromiso_mar_abr_app",
    "compromiso_may_jun_app",
)
