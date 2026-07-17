import calendar
from datetime import date
from typing import Optional
from utils.tiempo import ahora_mx


def etiqueta_temporada(fecha: Optional[date] = None) -> str:
    """Etiqueta de temporada tipo '2025-2026' para una temporada que corre del
    1 de julio al 30 de junio. Jul-Dic pertenece al año que arranca; Ene-Jun
    pertenece a la temporada que empezó el julio anterior.
    """
    d = fecha or ahora_mx().date()
    inicio = d.year if d.month >= 7 else d.year - 1
    return f"{inicio}-{inicio + 1}"


_NOMBRES_BIMESTRES = ['jul_ago', 'sep_oct', 'nov_dic', 'ene_feb', 'mar_abr', 'may_jun']


def rangos_bimestres_temporada(fecha_inicio) -> list:
    """A partir del inicio de una temporada (ej. '2025-07-01' o date(2025,7,1)),
    regresa 6 tuplas (nombre, primer_dia, ultimo_dia) para los bimestres
    jul_ago..may_jun de ESA temporada. El año se calcula dinamicamente a
    partir de fecha_inicio, nunca hardcodeado, para que sirva para cualquier
    temporada (MY26, MY27...) -- evita el bug de fechas de bimestre fijas a
    MY26 que dejaba todo en 0 al abrir una temporada nueva.
    """
    if isinstance(fecha_inicio, str):
        inicio = date.fromisoformat(fecha_inicio[:10])
    else:
        inicio = fecha_inicio

    anio, mes = inicio.year, inicio.month
    rangos = []
    for nombre in _NOMBRES_BIMESTRES:
        primer_dia = date(anio, mes, 1)
        mes_fin, anio_fin = mes + 1, anio
        if mes_fin > 12:
            mes_fin -= 12
            anio_fin += 1
        ultimo_dia = date(anio_fin, mes_fin, calendar.monthrange(anio_fin, mes_fin)[1])
        rangos.append((nombre, primer_dia.isoformat(), ultimo_dia.isoformat()))

        mes += 2
        if mes > 12:
            mes -= 12
            anio += 1
    return rangos
