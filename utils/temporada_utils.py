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
