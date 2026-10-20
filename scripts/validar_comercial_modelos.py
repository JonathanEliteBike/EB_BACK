"""Bloque 4 de auditoría: validación comercial conservadora de Modelo.

No consulta Odoo ni modifica el Dashboard. Lee exclusivamente los CSV de la
auditoría previa y clasifica los candidatos técnicos SCOTT / ROPA como
APROBADO, RECHAZADO o DUDOSO para revisión comercial posterior.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path


RAIZ = Path(__file__).resolve().parents[1]
DIRECTORIO = RAIZ / "reportes_auditoria" / "auditoria_modelos_2026-01-01_2026-10-09_facturas"

# Vocabulario centralizado de dimensiones que nunca deben promocionarse a
# Modelo. Se complementa con los niveles estructurales encontrados en el CSV,
# por lo que no depende sólo de una lista fija de ejemplos.
TERMINOS_GENERICOS_BASE = {
    "HOMBRE", "MUJER", "MEN", "WOMEN", "UNISEX", "JUNIOR", "INFANTIL",
    "TRAIL", "MONTANA", "RUTA", "URBAN", "PERFORMANCE", "LIFESTYLE",
    "CASUAL", "CABEZA", "SHORT", "PANTS", "JERSEY", "ROPA", "CASCO",
    "GUANTES", "ZAPATOS", "LENTES", "PROTECCIONES", "SPORT", "MTB",
    "GRAVEL", "ENDURO", "ROAD", "DH",
}
PATRON_TALLA = re.compile(r"^(?:XXS|XS|S|M|L|XL|XXL|XXXL|OS|ONE SIZE|\d{2,3})$", re.I)
PATRON_COLOR = re.compile(
    r"^(?:NEGRO|NEGRA|BLANCO|BLANCA|ROJO|ROJA|AZUL|VERDE|GRIS|GRIS[ /-]?[A-Z]+|"
    r"YELLOW|ORANGE|PINK|PURPLE|BROWN|BLACK|WHITE|RED|BLUE|GREEN)$",
    re.I,
)
MARCADORES_COMERCIALES = {
    "PLUS", "BOA", "MIPS", "LF", "SF", "PRO", "COMP", "TEAM", "GTX",
    "EVO", "RS", "SL", "GORE", "TEX", "FACTORY", "TRAINING", "SHIELD",
    "GEL", "FLAT", "LADY", "JR", "10", "24", "940",
}


class ErrorValidacion(RuntimeError):
    """Indica que la evidencia de entrada no permite emitir una simulación."""


def normalizar(valor: str) -> str:
    texto = unicodedata.normalize("NFD", str(valor or ""))
    return "".join(c for c in texto if unicodedata.category(c) != "Mn").upper().strip()


def leer_csv(ruta: Path) -> list[dict[str, str]]:
    if not ruta.is_file():
        raise ErrorValidacion(f"No existe el archivo requerido: {ruta.name}.")
    with ruta.open(encoding="utf-8-sig", newline="") as archivo:
        return list(csv.DictReader(archivo))


def segmentos(ruta: str) -> list[str]:
    return [parte.strip() for parte in str(ruta or "").split("/") if parte.strip()]


def clave_detalle(fila: dict[str, str]) -> tuple[str, str, str]:
    return fila.get("sku", ""), fila.get("producto", ""), fila.get("categoria_completa", "")


def clave_validacion(fila: dict[str, str]) -> tuple[str, str, str]:
    return fila.get("SKU", ""), fila.get("Nombre de producto", ""), fila.get("Categoría completa", "")


def construir_vocabulario_estructural(candidatos: list[dict[str, str]]) -> set[str]:
    """Obtiene términos usados realmente como niveles de navegación, no modelos."""
    vocabulario = set(TERMINOS_GENERICOS_BASE)
    for fila in candidatos:
        niveles = segmentos(fila["Categoría completa"])
        # Marca y Producto son metadatos de la ruta; los niveles intermedios
        # describen familia/categoría/uso y no deben pasar por Modelo.
        vocabulario.update(normalizar(nivel) for nivel in niveles[:-1])
    return vocabulario


def descomponer_ruta(ruta: str) -> tuple[str, str, str, str]:
    """Devuelve producto, familia, categoría y subcategoría sin asumir profundidad.

    En este universo todas las rutas tienen cinco niveles. Conservamos el
    tratamiento general para documentar explícitamente los niveles intermedios.
    """
    niveles = segmentos(ruta)
    producto = niveles[1] if len(niveles) > 1 else ""
    familia = niveles[2] if len(niveles) > 2 else ""
    categoria = niveles[3] if len(niveles) > 3 else ""
    subcategoria = " / ".join(niveles[4:-1]) if len(niveles) > 5 else ""
    return producto, familia, categoria, subcategoria


def es_nombre_compuesto_especifico(candidato: str) -> bool:
    palabras = [normalizar(parte) for parte in candidato.replace("-", " ").split() if parte]
    if len(palabras) < 2:
        return False
    return (
        any(palabra in MARCADORES_COMERCIALES for palabra in palabras)
        or any(any(caracter.isdigit() for caracter in palabra) for palabra in palabras)
        or "-" in candidato
    )


def clasificar(candidato: str, vocabulario_estructural: set[str]) -> tuple[str, str]:
    clave = normalizar(candidato)
    if not clave:
        return "DUDOSO", "No hay candidato de Modelo para validar."
    if clave in vocabulario_estructural:
        return "RECHAZADO", "Coincide con un nivel estructural real de familia, categoría, uso o tipo de producto."
    if PATRON_TALLA.match(clave) or PATRON_COLOR.match(clave):
        return "RECHAZADO", "Tiene formato de talla o color; no representa un Modelo comercial."
    if es_nombre_compuesto_especifico(candidato):
        return "APROBADO", (
            "Nombre compuesto con marcador comercial y coincidencia técnica previa en ruta, plantilla y variante; "
            "aprobado sólo para la simulación."
        )
    return "DUDOSO", (
        "El nombre coincide técnicamente, pero es de una sola palabra o no contiene un marcador comercial suficiente; "
        "requiere catálogo o validación comercial."
    )


def preparar_filas(candidatos: list[dict[str, str]], detalles: dict[tuple[str, str, str], dict[str, str]]) -> list[dict[str, str]]:
    vocabulario = construir_vocabulario_estructural(candidatos)
    filas = []
    vistos = set()
    for origen in candidatos:
        detalle = detalles.get(clave_validacion(origen))
        if detalle is None:
            raise ErrorValidacion(f"No se encontró el detalle para SKU {origen.get('SKU') or '(sin SKU)'}.")
        product_id = detalle.get("product_id", "")
        if not product_id or product_id in vistos:
            raise ErrorValidacion("product_id no es único dentro de los candidatos comerciales.")
        vistos.add(product_id)
        producto, familia, categoria, subcategoria = descomponer_ruta(origen["Categoría completa"])
        resultado, motivo = clasificar(origen["Modelo candidato"], vocabulario)
        filas.append({
            "product_id": product_id,
            "SKU": origen["SKU"],
            "Marca": origen["Marca"],
            "Producto/familia normalizada": f"{producto} / {familia}".strip(" /"),
            "Familia/grupo": familia,
            "Categoría detectada": categoria,
            "Subcategoría detectada": subcategoria,
            "Categoría completa": origen["Categoría completa"],
            "Nombre plantilla": detalle.get("plantilla", ""),
            "Nombre variante": detalle.get("producto", ""),
            "Modelo candidato": origen["Modelo candidato"],
            "Submodelo candidato": "",
            "Líneas de venta": detalle.get("lineas_venta", "0"),
            "Pzas vendidas": "No disponible en los CSV de auditoría",
            "Clasificación comercial": resultado,
            "Motivo": motivo,
        })
    return filas


def entero(fila: dict[str, str], campo: str) -> int:
    try:
        return int(float(fila.get(campo, "0") or 0))
    except ValueError:
        return 0


def seleccionar_muestra(filas: list[dict[str, str]]) -> list[dict[str, str]]:
    """Incluye máximo y mínimo de líneas por grupo para no escoger sólo casos frecuentes."""
    grupos: dict[tuple[str, str, str, str], list[dict[str, str]]] = defaultdict(list)
    for fila in filas:
        grupos[(fila["Familia/grupo"], fila["Categoría detectada"], fila["Subcategoría detectada"], fila["Modelo candidato"])].append(fila)
    muestra = []
    for grupo in sorted(grupos):
        ordenadas = sorted(grupos[grupo], key=lambda fila: (entero(fila, "Líneas de venta"), fila["product_id"]))
        muestra.append(ordenadas[0])
        if ordenadas[-1]["product_id"] != ordenadas[0]["product_id"]:
            muestra.append(ordenadas[-1])
    return sorted(muestra, key=lambda fila: (fila["Familia/grupo"], fila["Modelo candidato"], fila["product_id"]))


def escribir_csv(ruta: Path, filas: list[dict[str, str]]) -> None:
    if not filas:
        raise ErrorValidacion("No hay filas para escribir.")
    with ruta.open("w", encoding="utf-8-sig", newline="") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=list(filas[0]))
        escritor.writeheader()
        escritor.writerows(filas)


def tabla_top(filas: list[dict[str, str]], estado: str, limite: int = 15) -> list[str]:
    totales: dict[str, Counter] = defaultdict(Counter)
    for fila in filas:
        if fila["Clasificación comercial"] == estado:
            clave = fila["Modelo candidato"]
            totales[clave]["productos"] += 1
            totales[clave]["lineas"] += entero(fila, "Líneas de venta")
    lineas = ["| Modelo candidato | Productos | Líneas de venta |", "|---|---:|---:|"]
    for modelo, valores in sorted(totales.items(), key=lambda item: (-item[1]["lineas"], -item[1]["productos"], item[0]))[:limite]:
        lineas.append(f"| {modelo} | {valores['productos']} | {valores['lineas']} |")
    return lineas if len(lineas) > 2 else ["- Sin casos."]


def tabla_top_candidatos(filas: list[dict[str, str]], limite: int = 20) -> list[str]:
    """Resume prioridad comercial sin mezclarla con una aprobación productiva."""
    totales: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for fila in filas:
        clave = fila["Modelo candidato"], fila["Clasificación comercial"]
        totales[clave]["productos"] += 1
        totales[clave]["lineas"] += entero(fila, "Líneas de venta")
    lineas = [
        "| Modelo candidato | Productos | Líneas de venta | Pzas | Validación |",
        "|---|---:|---:|---|---|",
    ]
    for (modelo, estado), valores in sorted(
        totales.items(), key=lambda item: (-item[1]["lineas"], -item[1]["productos"], item[0][0])
    )[:limite]:
        lineas.append(f"| {modelo} | {valores['productos']} | {valores['lineas']} | No disponible | {estado} |")
    return lineas


def escribir_reporte(ruta: Path, filas: list[dict[str, str]], total_sin_modelo: int) -> None:
    conteo = Counter(fila["Clasificación comercial"] for fila in filas)
    lineas = sum(entero(fila, "Líneas de venta") for fila in filas)
    lineas_aprobadas = sum(entero(fila, "Líneas de venta") for fila in filas if fila["Clasificación comercial"] == "APROBADO")
    por_familia: dict[str, Counter] = defaultdict(Counter)
    for fila in filas:
        valores = por_familia[fila["Familia/grupo"]]
        valores["total"] += 1
        valores[fila["Clasificación comercial"]] += 1
        valores["lineas"] += entero(fila, "Líneas de venta")
    rechazados: dict[tuple[str, str, str], int] = Counter(
        (fila["Modelo candidato"], fila["Familia/grupo"], fila["Motivo"])
        for fila in filas if fila["Clasificación comercial"] == "RECHAZADO"
    )
    dudosos = [fila for fila in filas if fila["Clasificación comercial"] == "DUDOSO"]
    lineas_md = [
        "# Validación comercial de Modelo — Bloque 4", "",
        "## Universo y alcance", "",
        "- Periodo: 2026-01-01 a 2026-10-09.",
        "- Vista: Facturas.",
        "- Se analizaron exclusivamente los 1,484 candidatos `COHERENTE_TECNICAMENTE` de la validación anterior.",
        "- Marca/familia incluida: SCOTT / ROPA (clasificación técnica previa); los grupos internos corresponden a Apparel.",
        "- No se consultó Odoo, no se alteró MySQL y no se modificó clasificación productiva.",
        "- No se propone ningún Submodelo nuevo.", "",
        "## Resultado de validación comercial", "",
        f"- APROBADO: {conteo['APROBADO']}.",
        f"- RECHAZADO: {conteo['RECHAZADO']}.",
        f"- DUDOSO: {conteo['DUDOSO']}.",
        f"- Total: {len(filas)}.", "",
        "Un APROBADO aquí es una simulación conservadora, no una modificación al catálogo ni al Dashboard.", "",
        "## Desglose por familia real", "",
        "| Familia | Candidatos | Aprobados | Rechazados | Dudosos | Líneas de venta |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for familia, valores in sorted(por_familia.items(), key=lambda item: (-item[1]["total"], item[0])):
        lineas_md.append(
            f"| {familia} | {valores['total']} | {valores['APROBADO']} | {valores['RECHAZADO']} | "
            f"{valores['DUDOSO']} | {valores['lineas']} |"
        )
    lineas_md.extend([
        "", "## Patrones aplicados", "",
        "1. Se rechaza un candidato si coincide exactamente con un nivel de navegación observado o tiene formato de talla/color.",
        "2. Se aprueba sólo un nombre compuesto que incluye un marcador comercial observable, por ejemplo un código, número o calificador comercial.",
        "3. Un nombre de una sola palabra, aunque se repita en ruta, plantilla y variante, permanece DUDOSO. Esa evidencia no distingue con certeza una línea comercial de una característica de producto.",
        "4. No se infirió información desde SKU, color, talla ni Submodelo.", "",
        "## Principales candidatos por impacto", "",
        *tabla_top_candidatos(filas), "",
        "## Principales modelos aprobados por líneas de venta", "",
        *tabla_top(filas, "APROBADO"), "",
        "## Casos rechazados", "",
        "| Valor rechazado | Productos | Familia | Motivo |",
        "|---|---:|---|---|",
    ])
    for (modelo, familia, motivo), cantidad in sorted(rechazados.items(), key=lambda item: (-item[1], item[0][0])):
        lineas_md.append(f"| {modelo} | {cantidad} | {familia} | {motivo} |")
    if not rechazados:
        lineas_md.append("| - | 0 | - | - |")
    lineas_md.extend([
        "", "## Principales casos dudosos", "",
        "| Modelo candidato | Ruta | Plantilla | Variante | Motivo de duda |",
        "|---|---|---|---|---|",
    ])
    for fila in sorted(dudosos, key=lambda fila: (-entero(fila, "Líneas de venta"), fila["Modelo candidato"]))[:30]:
        lineas_md.append(
            f"| {fila['Modelo candidato']} | {fila['Categoría completa']} | {fila['Nombre plantilla']} | "
            f"{fila['Nombre variante']} | {fila['Motivo']} |"
        )
    lineas_md.extend([
        "", "## Impacto simulado", "",
        f"- Productos Sin Modelo actuales: {total_sin_modelo}.",
        f"- Modelos nuevos aprobados en la simulación: {conteo['APROBADO']}.",
        f"- Productos Sin Modelo esperados si sólo se aplicaran APROBADOS: {total_sin_modelo - conteo['APROBADO']}.",
        f"- Líneas de venta asociadas a los candidatos: {lineas} de 19,468.",
        f"- Líneas de venta cubiertas por APROBADOS: {lineas_aprobadas} de 19,468.",
        "- Pzas no se calculan: detalle_productos.csv no contiene una métrica de cantidad vendida.", "",
        "## Riesgos y recomendación", "",
        "La coincidencia de categoría, plantilla y variante sigue siendo evidencia técnica, no un catálogo comercial. Los valores de una sola palabra pueden ser líneas comerciales, usos o características; por eso se mantienen como DUDOSO.",
        "Recomendación: opción B. Sólo los grupos aprobados por el criterio conservador podrían pasar a una revisión comercial final; no existe evidencia suficiente para automatizar los 1,484 candidatos ni una regla segura para producción sin esa aprobación.",
    ])
    ruta.write_text("\n".join(lineas_md) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Valida comercialmente candidatos de Modelo sin consultar Odoo.")
    parser.add_argument("--directorio", type=Path, default=DIRECTORIO)
    parser.add_argument("--sobrescribir", action="store_true", help="Reemplaza sólo los tres archivos del Bloque 4.")
    args = parser.parse_args()
    try:
        directorio = args.directorio
        validacion = leer_csv(directorio / "validacion_candidatos.csv")
        simulacion = leer_csv(directorio / "simulacion_clasificacion.csv")
        detalle = leer_csv(directorio / "detalle_productos.csv")
        if len(validacion) != len(simulacion):
            raise ErrorValidacion("validacion_candidatos.csv y simulacion_clasificacion.csv no tienen el mismo número de filas.")
        candidatos = []
        for fila_validacion, fila_simulacion in zip(validacion, simulacion):
            controles = (
                ("SKU", "SKU"),
                ("Categoría completa", "Categoría completa"),
                ("Modelo candidato", "Modelo propuesto"),
            )
            if any(fila_validacion.get(izquierda, "") != fila_simulacion.get(derecha, "") for izquierda, derecha in controles):
                raise ErrorValidacion("El orden o las llaves de validación y simulación no coinciden.")
            if fila_validacion.get("Resultado de la validación automática") == "COHERENTE_TECNICAMENTE":
                candidato = dict(fila_validacion)
                candidato["Nombre de producto"] = fila_simulacion.get("Nombre de producto", "")
                candidatos.append(candidato)
        if len(candidatos) != 1484:
            raise ErrorValidacion(f"Se esperaban 1484 candidatos técnicos; se obtuvieron {len(candidatos)}.")
        if any(normalizar(fila.get("Familia", "")) == "BICICLETA" for fila in candidatos):
            raise ErrorValidacion("El archivo de candidatos contiene bicicletas, fuera del alcance.")
        detalles = {clave_detalle(fila): fila for fila in detalle}
        if len(detalles) != len(detalle):
            raise ErrorValidacion("La llave SKU/producto/categoría no es única en detalle_productos.csv.")
        filas = preparar_filas(candidatos, detalles)
        if len(filas) != 1484 or sum(Counter(f["Clasificación comercial"] for f in filas).values()) != 1484:
            raise ErrorValidacion("La conciliación APROBADO + RECHAZADO + DUDOSO no coincide con 1484.")
        salida = [
            directorio / "validacion_comercial_modelos.csv",
            directorio / "muestra_validacion_comercial.csv",
            directorio / "reporte_validacion_comercial.md",
        ]
        if any(ruta.exists() for ruta in salida) and not args.sobrescribir:
            raise ErrorValidacion("Ya existen reportes del Bloque 4; no se sobrescribieron.")
        escribir_csv(salida[0], filas)
        escribir_csv(salida[1], seleccionar_muestra(filas))
        total_sin_modelo = sum(fila.get("sin_modelo", "").casefold() == "true" for fila in detalle)
        escribir_reporte(salida[2], filas, total_sin_modelo)
    except ErrorValidacion as exc:
        print(f"ERROR: Validación comercial no generada. {exc}", file=sys.stderr)
        return 2
    print("Validación comercial completada: " + ", ".join(ruta.name for ruta in salida))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
