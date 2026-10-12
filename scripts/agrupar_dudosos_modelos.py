"""Agrupa para revisión manual los candidatos DUDOSO del Bloque 4.

No reclasifica, no consulta Odoo y no modifica la lógica productiva. Sólo
resume por nombre de Modelo candidato el CSV de auditoría comercial existente.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path


RAIZ = Path(__file__).resolve().parents[1]
DIRECTORIO = RAIZ / "reportes_auditoria" / "auditoria_modelos_2026-01-01_2026-10-09_facturas"
TOP_LIMITE = 30


class ErrorAgrupacion(RuntimeError):
    """La fuente de auditoría no cumple los controles del bloque."""


def leer_csv(ruta: Path) -> list[dict[str, str]]:
    if not ruta.is_file():
        raise ErrorAgrupacion(f"No existe el archivo requerido: {ruta.name}.")
    with ruta.open(encoding="utf-8-sig", newline="") as archivo:
        return list(csv.DictReader(archivo))


def entero(valor: str) -> int:
    try:
        return int(float(valor or 0))
    except ValueError as exc:
        raise ErrorAgrupacion(f"Líneas de venta inválidas: {valor!r}.") from exc


def ejemplos(filas: list[dict[str, str]], campo: str, limite: int = 3) -> str:
    valores: list[str] = []
    for fila in filas:
        valor = fila.get(campo, "").strip()
        if valor and valor not in valores:
            valores.append(valor)
        if len(valores) == limite:
            break
    return " | ".join(valores)


def agrupar(filas: list[dict[str, str]]) -> list[dict[str, str]]:
    grupos: dict[str, list[dict[str, str]]] = defaultdict(list)
    for fila in filas:
        modelo = fila.get("Modelo candidato", "").strip()
        if not modelo:
            raise ErrorAgrupacion("Se encontró un DUDOSO sin Modelo candidato.")
        grupos[modelo].append(fila)

    salida = []
    for modelo, miembros in grupos.items():
        ordenados = sorted(miembros, key=lambda fila: (-entero(fila["Líneas de venta"]), fila["product_id"]))
        familias = sorted({fila["Familia/grupo"].strip() for fila in miembros})
        if len(familias) != 1:
            raise ErrorAgrupacion(f"{modelo!r} aparece en más de una familia: {', '.join(familias)}.")
        salida.append({
            "modelo candidato": modelo,
            "familia": familias[0],
            "cantidad de productos asociados": str(len(miembros)),
            "cantidad de líneas de venta": str(sum(entero(fila["Líneas de venta"]) for fila in miembros)),
            "ejemplos de rutas": ejemplos(ordenados, "Categoría completa"),
            "ejemplos de plantilla": ejemplos(ordenados, "Nombre plantilla"),
            "ejemplos de variante": ejemplos(ordenados, "Nombre variante"),
            "product_id de ejemplo": ordenados[0]["product_id"],
            "prioridad por impacto": "",  # Se completa después de ordenar.
            "decision_manual": "",
            "comentario_manual": "",
        })
    salida.sort(
        key=lambda fila: (
            -entero(fila["cantidad de líneas de venta"]),
            -entero(fila["cantidad de productos asociados"]),
            fila["modelo candidato"],
        )
    )
    for indice, fila in enumerate(salida, start=1):
        fila["prioridad por impacto"] = f"P{indice:02d}"
    return salida


def escribir_csv(ruta: Path, filas: list[dict[str, str]]) -> None:
    with ruta.open("w", encoding="utf-8-sig", newline="") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=list(filas[0]))
        escritor.writeheader()
        escritor.writerows(filas)


def escribir_reporte(ruta: Path, filas: list[dict[str, str]], total_productos: int) -> None:
    top = filas[:TOP_LIMITE]
    lineas_total = sum(entero(fila["cantidad de líneas de venta"]) for fila in filas)
    productos_top = sum(entero(fila["cantidad de productos asociados"]) for fila in top)
    lineas_top = sum(entero(fila["cantidad de líneas de venta"]) for fila in top)
    porcentaje = (lineas_top / lineas_total * 100) if lineas_total else 0
    familias: dict[str, Counter] = defaultdict(Counter)
    for fila in filas:
        valores = familias[fila["familia"]]
        valores["modelos"] += 1
        valores["productos"] += entero(fila["cantidad de productos asociados"])
        valores["lineas"] += entero(fila["cantidad de líneas de venta"])
    contenido = [
        "# Revisión manual agrupada de Modelos dudosos", "",
        "## Alcance y controles", "",
        f"- Productos dudosos analizados: {total_productos}.",
        f"- Nombres de Modelo candidato únicos: {len(filas)}.",
        "- Fuente: `validacion_comercial_modelos.csv`, filtrada exclusivamente a `DUDOSO`.",
        "- No se tomó ninguna decisión automática y no se propuso Submodelo.",
        "- Las columnas `decision_manual` y `comentario_manual` quedan deliberadamente vacías para revisión humana.",
        "- Valores permitidos para `decision_manual`: `APROBAR`, `RECHAZAR`, `MANTENER_DUDOSO`.", "",
        f"## Top {TOP_LIMITE} por impacto", "",
        "| Prioridad | Modelo candidato | Familia | Productos | Líneas de venta |",
        "|---|---|---|---:|---:|",
    ]
    for fila in top:
        contenido.append(
            f"| {fila['prioridad por impacto']} | {fila['modelo candidato']} | {fila['familia']} | "
            f"{fila['cantidad de productos asociados']} | {fila['cantidad de líneas de venta']} |"
        )
    contenido.extend([
        "", f"- Productos cubiertos por el Top {TOP_LIMITE}: {productos_top} de {total_productos}.",
        f"- Líneas de venta cubiertas por el Top {TOP_LIMITE}: {lineas_top} de {lineas_total}.",
        f"- Porcentaje acumulado de líneas: {porcentaje:.1f}%.", "",
        "## Desglose por familia", "",
        "| Familia | Modelos candidatos | Productos dudosos | Líneas de venta |",
        "|---|---:|---:|---:|",
    ])
    for familia, valores in sorted(familias.items(), key=lambda item: (-item[1]["lineas"], item[0])):
        contenido.append(f"| {familia} | {valores['modelos']} | {valores['productos']} | {valores['lineas']} |")
    contenido.extend([
        "", "## Uso de la revisión", "",
        "Revisar cada fila del CSV por nombre comercial, usando las rutas, plantillas y variantes de ejemplo. "
        "La prioridad sólo ordena el impacto comercial y no equivale a una aprobación.",
    ])
    ruta.write_text("\n".join(contenido) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Agrupa candidatos dudosos sin reclasificarlos.")
    parser.add_argument("--directorio", type=Path, default=DIRECTORIO)
    parser.add_argument("--sobrescribir", action="store_true", help="Reemplaza sólo las dos salidas de este bloque.")
    args = parser.parse_args()
    try:
        directorio = args.directorio
        fuente = leer_csv(directorio / "validacion_comercial_modelos.csv")
        dudosos = [fila for fila in fuente if fila.get("Clasificación comercial") == "DUDOSO"]
        if len(dudosos) != 856:
            raise ErrorAgrupacion(f"Se esperaban 856 productos DUDOSO; se obtuvieron {len(dudosos)}.")
        ids = [fila.get("product_id", "") for fila in dudosos]
        if not all(ids) or len(ids) != len(set(ids)):
            raise ErrorAgrupacion("Los DUDOSO contienen product_id vacío o duplicado.")
        grupos = agrupar(dudosos)
        if sum(entero(fila["cantidad de productos asociados"]) for fila in grupos) != 856:
            raise ErrorAgrupacion("La suma de productos agrupados no coincide con 856.")
        if len({fila["modelo candidato"] for fila in grupos}) != len(grupos):
            raise ErrorAgrupacion("Existe más de una fila para un Modelo candidato.")
        salidas = [
            directorio / "revision_dudosos_agrupados.csv",
            directorio / "reporte_dudosos_agrupados.md",
        ]
        if any(ruta.exists() for ruta in salidas) and not args.sobrescribir:
            raise ErrorAgrupacion("Las salidas de dudosos ya existen; no se sobrescribieron.")
        escribir_csv(salidas[0], grupos)
        escribir_reporte(salidas[1], grupos, len(dudosos))
    except ErrorAgrupacion as exc:
        print(f"ERROR: Agrupación no generada. {exc}", file=sys.stderr)
        return 2
    print("Agrupación completada: " + ", ".join(ruta.name for ruta in salidas))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
