"""Audita candidatos de Submodelo sin modificar la lógica del Dashboard.

Usa exclusivamente las consolidaciones CSV ya creadas. Un texto posterior al
Modelo no se promueve a Submodelo si carece de evidencia comercial estructurada.
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
PATRON_TALLA = re.compile(r"^(?:XXS|XS|S|M|L|XL|XXL|XXXL|OS|UNITALLA|ONE SIZE|\d{2,3})$", re.I)
PATRON_ANIO = re.compile(r"^(?:MY\s*)?(?:20\d{2}|\d{2}/\d{2}|\d{2})$", re.I)
TERMINOS_NO_COMERCIALES = {
    "SS", "LS", "SF", "LF", "BOA", "MIPS", "CLIP", "FLAT", "ECO", "CE", "CPSC",
    "DAMA", "HOMBRE", "MUJER", "MEN", "WOMEN", "JUNIOR", "JR", "KID", "OS", "UNITALLA",
    "NEGRO", "NEGRA", "BLANCO", "BLANCA", "ROJO", "ROJA", "AZUL", "VERDE", "GRIS",
    "NG", "NGO", "RJ", "VD", "AZ", "PL", "ORO", "CROM", "MICA", "MATE", "CAJA",
    "SIN", "PIEZA", "COLOR", "TALLA",
}
TERMINOS_COMERCIALES_POTENCIALES = {"EVO", "ULTIMATE", "METAVERSE"}


class ErrorAuditoria(RuntimeError):
    """Los insumos no cumplen las conciliaciones requeridas."""


def leer_csv(ruta: Path) -> list[dict[str, str]]:
    if not ruta.is_file():
        raise ErrorAuditoria(f"No existe {ruta.name}.")
    with ruta.open(encoding="utf-8-sig", newline="") as archivo:
        return list(csv.DictReader(archivo))


def normalizar(valor: str) -> str:
    texto = unicodedata.normalize("NFD", str(valor or ""))
    return "".join(c for c in texto if unicodedata.category(c) != "Mn").upper().strip()


def entero(valor: str) -> int:
    try:
        return int(float(valor or 0))
    except ValueError as exc:
        raise ErrorAuditoria(f"Líneas de venta inválidas: {valor!r}.") from exc


def sufijo_despues_modelo(nombre: str, modelo: str) -> str:
    """Extrae sólo el texto posterior al Modelo dentro de plantilla/variante."""
    if not nombre or not modelo:
        return ""
    coincidencia = re.search(re.escape(modelo), nombre, flags=re.IGNORECASE)
    if not coincidencia:
        return ""
    return nombre[coincidencia.end():].strip(" .,_-/()")


def es_termino_no_comercial(sufijo: str) -> bool:
    limpio = normalizar(sufijo)
    if not limpio:
        return True
    if PATRON_TALLA.match(limpio) or PATRON_ANIO.match(limpio):
        return True
    palabras = [palabra for palabra in re.split(r"[\s/(),._+-]+", limpio) if palabra]
    if not palabras:
        return True
    if any(PATRON_ANIO.match(palabra) or PATRON_TALLA.match(palabra) for palabra in palabras):
        return True
    # Las combinaciones separadas por diagonal provienen en este universo de
    # códigos de color/acabado; no constituyen versiones comerciales.
    if "/" in limpio:
        return True
    # Un código final breve (por ejemplo, AR) tampoco aporta un submodelo.
    if len(palabras) == 1 and re.fullmatch(r"[A-Z]{1,3}", palabras[0]):
        return True
    if any(palabra in {"TRANS", "CROMADO", "PLATA"} for palabra in palabras):
        return True
    if (
        not any(palabra in TERMINOS_COMERCIALES_POTENCIALES for palabra in palabras)
        and all(
            palabra in TERMINOS_NO_COMERCIALES
            or re.fullmatch(r"[A-Z]{1,3}\d{0,2}", palabra)
            for palabra in palabras
        )
    ):
        return True
    # Un sufijo compuesto únicamente de rasgos técnicos, género o color no es
    # una versión comercial autónoma.
    return all(palabra in TERMINOS_NO_COMERCIALES for palabra in palabras)


def preparar_filas(finales: list[dict[str, str]], comerciales: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    filas = []
    vistos = set()
    for final in finales:
        if final.get("clasificacion_final") != "MODELO_CONFIRMADO":
            continue
        product_id = final.get("product_id", "")
        if not product_id or product_id in vistos:
            raise ErrorAuditoria("product_id vacío o duplicado entre Modelos confirmados.")
        vistos.add(product_id)
        comercial = comerciales.get(product_id)
        if comercial is None:
            raise ErrorAuditoria(f"No existe contexto comercial para product_id {product_id}.")
        modelo = final.get("modelo_final", "")
        plantilla = comercial.get("Nombre plantilla", "")
        variante = comercial.get("Nombre variante", "")
        sufijo = sufijo_despues_modelo(plantilla, modelo) or sufijo_despues_modelo(variante, modelo)
        candidato = f"{modelo} {sufijo}".strip() if sufijo else ""
        if not sufijo:
            clasificacion = "RECHAZADO"
            motivo = "No existe un nombre adicional posterior al Modelo en plantilla ni variante."
        elif es_termino_no_comercial(sufijo):
            clasificacion = "RECHAZADO"
            motivo = "El texto adicional representa talla, color, género, año, código o tecnología aislada."
        else:
            # Las rutas de este universo terminan en el Modelo y no contienen
            # un nivel posterior validable. Aunque el texto sea prometedor, no
            # hay evidencia estructurada suficiente para aprobarlo.
            clasificacion = "DUDOSO"
            motivo = "El sufijo parece una versión potencial, pero no hay nivel posterior de categoría ni catálogo estructurado que lo confirme."
        filas.append({
            "product_id": product_id,
            "familia": comercial.get("Familia/grupo", ""),
            "categoria": comercial.get("Categoría detectada", ""),
            "subcategoria": comercial.get("Subcategoría detectada", ""),
            "modelo": modelo,
            "referencia_comercial": final.get("referencia_comercial", ""),
            "plantilla": plantilla,
            "variante": variante,
            "submodelo_candidato": candidato,
            "clasificacion": clasificacion,
            "motivo": motivo,
            "lineas_venta": final.get("lineas_venta", "0"),
        })
    return filas


def ranking(filas: list[dict[str, str]]) -> list[dict[str, str]]:
    grupos: dict[tuple[str, str, str, str], list[dict[str, str]]] = defaultdict(list)
    for fila in filas:
        if fila["submodelo_candidato"]:
            grupos[(fila["familia"], fila["modelo"], fila["submodelo_candidato"], fila["clasificacion"])].append(fila)
    salida = []
    for (familia, modelo, candidato, clasificacion), miembros in grupos.items():
        ordenados = sorted(miembros, key=lambda fila: (-entero(fila["lineas_venta"]), fila["product_id"]))
        salida.append({
            "familia": familia,
            "modelo": modelo,
            "submodelo_candidato": candidato,
            "clasificacion": clasificacion,
            "cantidad_productos": str(len(miembros)),
            "lineas_venta": str(sum(entero(fila["lineas_venta"]) for fila in miembros)),
            "product_id_ejemplo": ordenados[0]["product_id"],
            "plantilla_ejemplo": ordenados[0]["plantilla"],
        })
    return sorted(salida, key=lambda fila: (-entero(fila["lineas_venta"]), -entero(fila["cantidad_productos"]), fila["submodelo_candidato"]))


def escribir_csv(ruta: Path, filas: list[dict[str, str]]) -> None:
    with ruta.open("w", encoding="utf-8-sig", newline="") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=list(filas[0]))
        escritor.writeheader()
        escritor.writerows(filas)


def escribir_reporte(ruta: Path, filas: list[dict[str, str]], grupos: list[dict[str, str]]) -> None:
    conteo = Counter(fila["clasificacion"] for fila in filas)
    lineas = Counter()
    por_familia: dict[str, Counter] = defaultdict(Counter)
    for fila in filas:
        lineas[fila["clasificacion"]] += entero(fila["lineas_venta"])
        por_familia[fila["familia"]]["productos"] += 1
        por_familia[fila["familia"]][fila["clasificacion"]] += 1
    ejemplos = {estado: [fila for fila in filas if fila["clasificacion"] == estado][:5] for estado in conteo}
    contenido = [
        "# Auditoría de Submodelos — SCOTT / ROPA", "",
        "## Universo", "",
        f"- Productos con Modelo confirmado analizados: {len(filas)}.",
        f"- Productos actualmente sin Submodelo: {len(filas)}.",
        "- Bicicletas y productos sin Modelo confirmado quedan fuera de este bloque.",
        "- No se consultó Odoo ni se alteró ninguna regla activa.", "",
        "## Resultado", "",
        "| Clasificación | Productos | Líneas de venta |",
        "|---|---:|---:|",
    ]
    for estado in ("SUBMODELO_APROBADO", "RECHAZADO", "DUDOSO"):
        contenido.append(f"| {estado} | {conteo[estado]} | {lineas[estado]} |")
    contenido.extend(["", "## Desglose por familia", "", "| Familia | Productos | Aprobados | Rechazados | Dudosos |", "|---|---:|---:|---:|---:|"])
    for familia, valores in sorted(por_familia.items(), key=lambda item: -item[1]["productos"]):
        contenido.append(f"| {familia} | {valores['productos']} | {valores['SUBMODELO_APROBADO']} | {valores['RECHAZADO']} | {valores['DUDOSO']} |")
    contenido.extend(["", "## Top candidatos por impacto", "", "| Modelo | Submodelo candidato | Estado | Productos | Líneas |", "|---|---|---|---:|---:|"])
    for fila in grupos[:30]:
        contenido.append(f"| {fila['modelo']} | {fila['submodelo_candidato']} | {fila['clasificacion']} | {fila['cantidad_productos']} | {fila['lineas_venta']} |")
    contenido.extend(["", "## Ejemplos", ""])
    for estado in ("SUBMODELO_APROBADO", "RECHAZADO", "DUDOSO"):
        contenido.extend([f"### {estado}", "", "| product_id | Modelo | Candidato | Plantilla | Motivo |", "|---|---|---|---|---|"])
        for fila in ejemplos.get(estado, []):
            contenido.append(f"| {fila['product_id']} | {fila['modelo']} | {fila['submodelo_candidato'] or '-'} | {fila['plantilla']} | {fila['motivo']} |")
        if not ejemplos.get(estado):
            contenido.append("| - | - | - | - | Sin casos aprobados automáticamente bajo el criterio conservador. |")
        contenido.append("")
    contenido.extend([
        "## Impacto y recomendación", "",
        f"- Productos que podrían recibir Submodelo con evidencia actual suficiente: {conteo['SUBMODELO_APROBADO']}.",
        f"- Líneas de venta que quedarían más detalladas: {lineas['SUBMODELO_APROBADO']}.",
        "- Riesgo principal: convertir mangas, ajuste, talla, color, año o tecnología en una versión comercial falsa.",
        "- Recomendación del siguiente bloque: validar comercialmente los candidatos DUDOSO agrupados antes de incorporar cualquier mapeo de Submodelo por product_id.",
    ])
    ruta.write_text("\n".join(contenido) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audita Submodelos a partir de CSV consolidados.")
    parser.add_argument("--directorio", type=Path, default=DIRECTORIO)
    parser.add_argument("--sobrescribir", action="store_true", help="Reemplaza sólo las tres salidas de esta auditoría.")
    args = parser.parse_args()
    try:
        finales = leer_csv(args.directorio / "clasificacion_final_productos.csv")
        comerciales = {fila["product_id"]: fila for fila in leer_csv(args.directorio / "validacion_comercial_modelos.csv")}
        filas = preparar_filas(finales, comerciales)
        if len(filas) != 916:
            raise ErrorAuditoria(f"Se esperaban 916 Modelos confirmados; se obtuvieron {len(filas)}.")
        if len({fila["product_id"] for fila in filas}) != len(filas):
            raise ErrorAuditoria("Existen product_id duplicados en la auditoría.")
        grupos = ranking(filas)
        salidas = [
            args.directorio / "auditoria_submodelos.csv",
            args.directorio / "ranking_submodelos_candidatos.csv",
            args.directorio / "reporte_auditoria_submodelos.md",
        ]
        if any(ruta.exists() for ruta in salidas) and not args.sobrescribir:
            raise ErrorAuditoria("Las salidas de Submodelo ya existen; no se sobrescribieron.")
        escribir_csv(salidas[0], filas)
        escribir_csv(salidas[1], grupos)
        escribir_reporte(salidas[2], filas, grupos)
    except ErrorAuditoria as exc:
        print(f"ERROR: Auditoría de Submodelo no generada. {exc}", file=sys.stderr)
        return 2
    print("Auditoría completada: " + ", ".join(ruta.name for ruta in salidas))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
