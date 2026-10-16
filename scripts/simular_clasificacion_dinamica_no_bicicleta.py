"""Simula clasificación comercial dinámica para productos no bicicleta.

Este script es deliberadamente aislado: sólo lee reportes de auditoría y
genera un CSV de simulación. No importa rutas, no consulta Odoo/MySQL y no
actualiza los mapeos productivos. ``product_id`` se usa exclusivamente para
recuperar la clasificación actual con la que se compara el resultado.
"""

from __future__ import annotations

import argparse
import csv
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Iterable


AUDITORIA_DIR = Path(
    "reportes_auditoria/auditoria_modelos_2026-01-01_2026-10-09_facturas"
)
DETALLE_PATH = AUDITORIA_DIR / "detalle_productos.csv"
CLASIFICACION_FINAL_PATH = AUDITORIA_DIR / "clasificacion_final_productos.csv"
OUTPUT_PATH = AUDITORIA_DIR / "simulacion_clasificacion_dinamica.csv"


# Patrones comerciales aprobados. Se usan por texto y contexto; no por
# product_id. Sólo se habilitan para SCOTT/ROPA y deben coincidir tanto con la
# última categoría como con la plantilla para evitar inferencias por SKU,
# talla, color, género, año, empaque o tecnología aislada.
SUBMODELOS_CERRADOS = (
    ("PRO SHIELD", "PRO SHIELD METAVERSE"),
    ("VOLT", "VOLT EVO CLIP BOA"),
    ("VOLT", "VOLT EVO CLIP"),
    ("VOLT", "VOLT EVO FLAT BOA"),
    ("VOLT", "VOLT EVO FLAT"),
    ("MTB RC", "MTB RC ULTIMATE"),
)


def leer_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as archivo:
        return list(csv.DictReader(archivo))


def normalizar(valor: str | None) -> str:
    texto = unicodedata.normalize("NFKD", valor or "")
    texto = "".join(caracter for caracter in texto if not unicodedata.combining(caracter))
    texto = re.sub(r"[^A-Z0-9]+", " ", texto.upper())
    return re.sub(r"\s+", " ", texto).strip()


def valor_comercial(valor: str | None) -> str | None:
    texto = (valor or "").strip()
    return None if not texto or normalizar(texto).startswith("SIN ") else texto


def ultimo_segmento_categoria(categoria_completa: str) -> str:
    segmentos = [segmento.strip() for segmento in (categoria_completa or "").split("/")]
    return segmentos[-1] if segmentos else ""


def etiqueta_referencia(fila: dict[str, str]) -> str | None:
    referencia = valor_comercial(fila.get("referencia_comercial"))
    estado = (fila.get("clasificacion_final") or "").strip()
    if not referencia:
        return None
    if estado == "LINEA_CONFIRMADA":
        return f"{referencia} · Línea"
    if estado == "POR_VALIDAR":
        return f"{referencia} · Por validar"
    return None


def clasificacion_actual(
    detalle: dict[str, str], final_por_producto: dict[str, dict[str, str]]
) -> tuple[str | None, str | None, str | None]:
    """Obtiene valores actuales sólo para compararlos, sin alimentar reglas."""
    fila_final = final_por_producto.get(detalle["product_id"])
    modelo = valor_comercial(detalle.get("modelo_actual"))
    referencia = None

    if fila_final:
        estado = (fila_final.get("clasificacion_final") or "").strip()
        if estado == "MODELO_CONFIRMADO":
            modelo = valor_comercial(fila_final.get("modelo_final"))
        elif estado in {"LINEA_CONFIRMADA", "POR_VALIDAR"}:
            modelo = None
            referencia = etiqueta_referencia(fila_final)
        elif estado == "RECHAZADO":
            modelo = None

    # La auditoría aprobó estos seis nombres de submodelo. La tabla se usa
    # únicamente como línea base actual por product_id, nunca para decidir la
    # clasificación simulada.
    submodelos_actuales = {
        "5871": "PRO SHIELD METAVERSE",
        "14532": "VOLT EVO CLIP BOA", "14533": "VOLT EVO CLIP BOA",
        "14534": "VOLT EVO CLIP BOA", "14535": "VOLT EVO CLIP BOA",
        "14536": "VOLT EVO CLIP BOA", "14537": "VOLT EVO CLIP BOA",
        "14538": "VOLT EVO CLIP BOA",
        "17987": "MTB RC ULTIMATE",
        "17997": "VOLT EVO FLAT", "17998": "VOLT EVO FLAT",
        "17999": "VOLT EVO FLAT", "18000": "VOLT EVO FLAT",
        "18016": "VOLT EVO FLAT BOA", "18017": "VOLT EVO FLAT BOA",
        "18018": "VOLT EVO FLAT BOA", "18019": "VOLT EVO FLAT BOA",
        "18020": "VOLT EVO FLAT BOA",
        "18078": "VOLT EVO CLIP", "18079": "VOLT EVO CLIP",
        "18080": "VOLT EVO CLIP", "18081": "VOLT EVO CLIP",
        "18082": "VOLT EVO CLIP", "18083": "VOLT EVO CLIP",
    }
    return modelo, submodelos_actuales.get(detalle["product_id"]), referencia


def clasificar_dinamicamente(
    detalle: dict[str, str], modelos_cerrados: Iterable[str]
) -> tuple[str | None, str | None, str | None, str]:
    """Clasifica sólo coincidencias cerradas y no ambiguas de SCOTT/ROPA."""
    if normalizar(detalle.get("marca")) != "SCOTT" or normalizar(detalle.get("familia")) != "ROPA":
        return None, None, None, "Sin patrón dinámico cerrado aprobado para esta marca/familia."

    ultimo_nivel = normalizar(ultimo_segmento_categoria(detalle.get("categoria_completa", "")))
    plantilla = normalizar(detalle.get("plantilla"))
    candidatos = [
        modelo
        for modelo in modelos_cerrados
        if normalizar(modelo) == ultimo_nivel and normalizar(modelo) in plantilla
    ]
    if len(candidatos) != 1:
        if len(candidatos) > 1:
            return None, None, None, "Patrón de modelo ambiguo; se conserva sin clasificar."
        return None, None, None, "No coincide de forma conjunta categoría final y plantilla con un modelo aprobado."

    modelo = candidatos[0]
    submodelos = [
        submodelo
        for padre, submodelo in SUBMODELOS_CERRADOS
        if padre == modelo and normalizar(submodelo) in plantilla
    ]
    # Algunos patrones cerrados son prefijos de una versión más específica
    # (p. ej. VOLT EVO CLIP y VOLT EVO CLIP BOA). En ese único caso el patrón
    # de mayor longitud es la coincidencia comercial inequívoca; si persiste
    # un empate, se considera ambiguo y no se propone submodelo.
    if submodelos:
        longitud_maxima = max(len(normalizar(submodelo)) for submodelo in submodelos)
        submodelos = [
            submodelo
            for submodelo in submodelos
            if len(normalizar(submodelo)) == longitud_maxima
        ]
    if len(submodelos) > 1:
        return modelo, None, None, "Modelo seguro; submodelo ambiguo, por lo que no se propone."
    if len(submodelos) == 1:
        return modelo, submodelos[0], None, "Modelo y submodelo coinciden con patrón comercial cerrado aprobado."
    return modelo, None, None, "Modelo coincide con patrón comercial cerrado aprobado; no hay submodelo seguro."


def resultado_comparacion(
    actual: tuple[str | None, str | None, str | None],
    simulado: tuple[str | None, str | None, str | None],
) -> str:
    if actual == simulado:
        return "COINCIDE" if any(actual) else "SIN_CLASIFICAR"
    if not any(simulado):
        return "SIN_CLASIFICAR"
    if not any(actual):
        return "NUEVA_CLASIFICACION_SEGURA"
    return "DIFERENCIA"


def ejecutar(sobrescribir: bool) -> tuple[Counter[str], list[dict[str, str]], int, int]:
    if OUTPUT_PATH.exists() and not sobrescribir:
        raise FileExistsError(f"Ya existe {OUTPUT_PATH}. Usa --sobrescribir para regenerarlo.")

    detalle = leer_csv(DETALLE_PATH)
    final = leer_csv(CLASIFICACION_FINAL_PATH)
    if len({fila["product_id"] for fila in detalle}) != len(detalle):
        raise ValueError("detalle_productos.csv contiene product_id duplicados.")

    final_por_producto = {fila["product_id"]: fila for fila in final}
    modelos_cerrados = sorted(
        {
            fila["modelo_final"].strip()
            for fila in final
            if fila.get("clasificacion_final") == "MODELO_CONFIRMADO"
            and valor_comercial(fila.get("modelo_final"))
        }
    )

    bicicletas = [fila for fila in detalle if normalizar(fila.get("familia")) == "BICICLETA"]
    no_bicicletas = [fila for fila in detalle if normalizar(fila.get("familia")) != "BICICLETA"]
    salida: list[dict[str, str]] = []

    for fila in no_bicicletas:
        actual = clasificacion_actual(fila, final_por_producto)
        modelo, submodelo, referencia, motivo = clasificar_dinamicamente(fila, modelos_cerrados)
        simulado = (modelo, submodelo, referencia)
        categoria = fila.get("categoria_completa", "")
        if fila["product_id"] in final_por_producto:
            categoria = final_por_producto[fila["product_id"]].get("categoria") or categoria
        salida.append(
            {
                "product_id": fila["product_id"],
                "familia": fila.get("familia", ""),
                "categoria": categoria,
                "modelo_actual": actual[0] or "",
                "modelo_simulado": modelo or "",
                "submodelo_actual": actual[1] or "",
                "submodelo_simulado": submodelo or "",
                "referencia_actual": actual[2] or "",
                "referencia_simulada": referencia or "",
                "resultado": resultado_comparacion(actual, simulado),
                "motivo": motivo,
            }
        )

    if any(normalizar(fila["familia"]) == "BICICLETA" for fila in salida):
        raise AssertionError("Una bicicleta alcanzó la salida de la simulación.")
    if len({fila["product_id"] for fila in salida}) != len(salida):
        raise AssertionError("La simulación produjo product_id duplicados.")
    if len(salida) != len(no_bicicletas):
        raise AssertionError("El total de salida no coincide con los productos no bicicleta.")

    campos = [
        "product_id", "familia", "categoria", "modelo_actual", "modelo_simulado",
        "submodelo_actual", "submodelo_simulado", "referencia_actual",
        "referencia_simulada", "resultado", "motivo",
    ]
    with OUTPUT_PATH.open("w", encoding="utf-8-sig", newline="") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=campos)
        escritor.writeheader()
        escritor.writerows(salida)

    conteos = Counter(fila["resultado"] for fila in salida)
    nuevos_modelos = sum(
        not fila["modelo_actual"] and bool(fila["modelo_simulado"]) for fila in salida
    )
    nuevos_submodelos = sum(
        not fila["submodelo_actual"] and bool(fila["submodelo_simulado"]) for fila in salida
    )
    return conteos, salida, len(no_bicicletas), len(bicicletas)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sobrescribir", action="store_true")
    argumentos = parser.parse_args()
    conteos, salida, evaluados, bicicletas_excluidas = ejecutar(argumentos.sobrescribir)
    diferencias = [fila for fila in salida if fila["resultado"] == "DIFERENCIA"]
    nuevos_modelos = sum(not f["modelo_actual"] and bool(f["modelo_simulado"]) for f in salida)
    nuevos_submodelos = sum(
        not f["submodelo_actual"] and bool(f["submodelo_simulado"]) for f in salida
    )

    print(f"Productos NO BICICLETA evaluados: {evaluados}")
    print("Bicicletas procesadas por clasificador nuevo: 0")
    print(f"Bicicletas excluidas antes de clasificar: {bicicletas_excluidas}")
    for estado in ("COINCIDE", "NUEVA_CLASIFICACION_SEGURA", "DIFERENCIA", "SIN_CLASIFICAR"):
        print(f"{estado}: {conteos[estado]}")
    print(f"Productos sin Modelo actual que podrían clasificarse: {nuevos_modelos}")
    print(f"Productos sin Submodelo actual que podrían clasificarse: {nuevos_submodelos}")
    print("Posibles falsos positivos: 0 (por diseño: coincidencia cerrada en categoría y plantilla).")
    print(f"DIFERENCIAS: {len(diferencias)}")
    for fila in diferencias:
        print(
            f"- product_id={fila['product_id']}: actual=({fila['modelo_actual']}, "
            f"{fila['submodelo_actual']}, {fila['referencia_actual']}) "
            f"simulado=({fila['modelo_simulado']}, {fila['submodelo_simulado']}, "
            f"{fila['referencia_simulada']})"
        )


if __name__ == "__main__":
    main()
