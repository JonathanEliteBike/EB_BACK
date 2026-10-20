"""Consolida decisiones comerciales de Modelo/Línea sólo para auditoría.

Lee la validación comercial de SCOTT / ROPA y un CSV de decisiones manuales
agrupadas. No consulta Odoo ni altera la clasificación productiva.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path


RAIZ = Path(__file__).resolve().parents[1]
DIRECTORIO = RAIZ / "reportes_auditoria" / "auditoria_modelos_2026-01-01_2026-10-09_facturas"
DECISIONES_VALIDAS = {"MODELO", "LÍNEA", "POR_VALIDAR", "NO_ESPECIFICADO"}


class ErrorConsolidacion(RuntimeError):
    """Los insumos no son conciliables de forma segura."""


def leer_csv(ruta: Path) -> list[dict[str, str]]:
    if not ruta.is_file():
        raise ErrorConsolidacion(f"No existe el archivo requerido: {ruta}.")
    with ruta.open(encoding="utf-8-sig", newline="") as archivo:
        return list(csv.DictReader(archivo))


def entero(valor: str) -> int:
    try:
        return int(float(valor or 0))
    except ValueError as exc:
        raise ErrorConsolidacion(f"Valor de líneas de venta inválido: {valor!r}.") from exc


def es_sin_modelo(valor: str) -> bool:
    return not valor.strip() or valor.strip().casefold() == "sin modelo"


def decisiones_por_modelo(filas: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    resultado = {}
    for fila in filas:
        modelo = fila.get("modelo candidato", "").strip()
        decision = fila.get("decision_manual", "").strip()
        if not modelo:
            raise ErrorConsolidacion("La fuente manual contiene un Modelo candidato vacío.")
        if decision not in DECISIONES_VALIDAS:
            raise ErrorConsolidacion(f"Decisión manual inválida para {modelo!r}: {decision!r}.")
        if modelo in resultado:
            raise ErrorConsolidacion(f"El CSV manual repite el Modelo candidato {modelo!r}.")
        resultado[modelo] = fila
    if len(resultado) != 42:
        raise ErrorConsolidacion(f"Se esperaban 42 decisiones manuales; se obtuvieron {len(resultado)}.")
    return resultado


def consolidar(fuentes: list[dict[str, str]], decisiones: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    filas = []
    dudosos_encontrados = Counter()
    vistos = set()
    for fuente in fuentes:
        product_id = fuente.get("product_id", "").strip()
        if not product_id or product_id in vistos:
            raise ErrorConsolidacion("product_id vacío o duplicado en validacion_comercial_modelos.csv.")
        vistos.add(product_id)
        estado_previo = fuente.get("Clasificación comercial", "").strip()
        modelo = fuente.get("Modelo candidato", "").strip()
        clasificacion = ""
        modelo_final = ""
        referencia = ""
        tipo_referencia = ""
        motivo = ""
        decision_manual = ""

        if estado_previo == "APROBADO":
            clasificacion = "MODELO_CONFIRMADO"
            modelo_final = modelo
            motivo = "Aprobado en la validación comercial previa; no existe una decisión manual distinta aplicable."
        elif estado_previo == "RECHAZADO":
            clasificacion = "RECHAZADO"
            motivo = fuente.get("Motivo", "Rechazado en la validación comercial previa.")
        elif estado_previo == "DUDOSO":
            manual = decisiones.get(modelo)
            if manual is None:
                raise ErrorConsolidacion(f"No existe decisión manual para el candidato dudoso {modelo!r}.")
            dudosos_encontrados[modelo] += 1
            decision_manual = manual["decision_manual"].strip()
            comentario = manual.get("comentario_manual", "").strip()
            if decision_manual == "MODELO":
                clasificacion = "MODELO_CONFIRMADO"
                modelo_final = modelo
                motivo = comentario or "Decisión manual agrupada: MODELO."
            elif decision_manual == "LÍNEA":
                clasificacion = "LINEA_CONFIRMADA"
                referencia = modelo
                tipo_referencia = "LÍNEA"
                motivo = comentario or "Decisión manual agrupada: LÍNEA."
            elif decision_manual == "POR_VALIDAR":
                clasificacion = "POR_VALIDAR"
                referencia = modelo
                tipo_referencia = "POR VALIDAR"
                motivo = comentario or "Decisión manual agrupada: POR_VALIDAR."
            else:  # NO_ESPECIFICADO
                clasificacion = "NO_ESPECIFICADO"
                motivo = comentario or "Decisión manual agrupada: NO_ESPECIFICADO."
        else:
            raise ErrorConsolidacion(f"Clasificación comercial no reconocida en {product_id}: {estado_previo!r}.")

        filas.append({
            "product_id": product_id,
            "marca": fuente.get("Marca", ""),
            "producto/familia": fuente.get("Producto/familia normalizada", ""),
            "categoria": fuente.get("Categoría detectada", ""),
            "subcategoria": fuente.get("Subcategoría detectada", ""),
            "modelo_actual": fuente.get("Modelo actual", "Sin modelo"),
            "modelo_candidato": modelo,
            "modelo_final": modelo_final,
            "referencia_comercial": referencia,
            "tipo_referencia": tipo_referencia,
            "clasificacion_final": clasificacion,
            "decision_manual": decision_manual,
            "motivo": motivo,
            "lineas_venta": fuente.get("Líneas de venta", "0"),
        })
    if len(dudosos_encontrados) != 42 or set(dudosos_encontrados) != set(decisiones):
        raise ErrorConsolidacion("Los 42 candidatos manuales no quedaron conciliados con los productos dudosos.")
    return filas


def escribir_csv(ruta: Path, filas: list[dict[str, str]]) -> None:
    with ruta.open("w", encoding="utf-8-sig", newline="") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=list(filas[0]))
        escritor.writeheader()
        escritor.writerows(filas)


def escribir_reporte(ruta: Path, filas: list[dict[str, str]]) -> None:
    conteo = Counter(fila["clasificacion_final"] for fila in filas)
    lineas = Counter()
    for fila in filas:
        lineas[fila["clasificacion_final"]] += entero(fila["lineas_venta"])
    total = len(filas)
    sin_modelo_inicial = sum(es_sin_modelo(fila["modelo_actual"]) for fila in filas)
    dejan_sin_modelo = sum(
        es_sin_modelo(fila["modelo_actual"]) and fila["clasificacion_final"] == "MODELO_CONFIRMADO" for fila in filas
    )
    con_referencia = conteo["LINEA_CONFIRMADA"] + conteo["POR_VALIDAR"]
    reporte = [
        "# Consolidación final de clasificación comercial — Auditoría", "",
        "## Alcance", "",
        "- Universo: candidatos SCOTT / ROPA de la auditoría comercial.",
        f"- Total de productos consolidados: {total}.",
        "- Fuente manual: 42 decisiones agrupadas aplicadas exclusivamente a los 856 productos DUDOSO.",
        "- No se modificó producción, Odoo, MySQL, Dashboard ni reglas productivas.",
        "- No se generaron Submodelos.", "",
        "## Resultado final", "",
        "| Clasificación final | Productos | Líneas de venta |",
        "|---|---:|---:|",
    ]
    for estado in ("MODELO_CONFIRMADO", "LINEA_CONFIRMADA", "POR_VALIDAR", "RECHAZADO", "NO_ESPECIFICADO"):
        reporte.append(f"| {estado} | {conteo[estado]} | {lineas[estado]} |")
    reporte.extend([
        f"| Total | {total} | {sum(lineas.values())} |", "",
        "## Impacto simulado para el Dashboard", "",
        f"- Productos Sin Modelo al inicio de este universo: {sin_modelo_inicial}.",
        f"- Productos que dejarían de aparecer como Sin Modelo: {dejan_sin_modelo}.",
        f"- Productos que seguirían sin Modelo pero tendrían Referencia comercial: {con_referencia}.",
        f"- Productos realmente No especificados: {conteo['NO_ESPECIFICADO']}.",
        f"- Productos que siguen Por validar: {conteo['POR_VALIDAR']}.",
        "", "## Simulación de presentación posterior", "",
        "| Clasificación final | Modelo | Referencia comercial |",
        "|---|---|---|",
        "| MODELO_CONFIRMADO | nombre confirmado | vacío |",
        "| LINEA_CONFIRMADA | vacío | nombre · Línea |",
        "| POR_VALIDAR | vacío | nombre · Por validar |",
        "| NO_ESPECIFICADO | vacío | No especificado |",
        "| RECHAZADO | vacío | vacío |", "",
        "## Recomendación para el siguiente bloque", "",
        "Revisar y aprobar el mapeo de presentación Modelo/Referencia comercial antes de tocar `routes/ventas.py`. "
        "La siguiente implementación debe consumir esta consolidación como especificación, no volver a inferir nombres desde rutas de categoría.",
    ])
    ruta.write_text("\n".join(reporte) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Consolida la auditoría final sin aplicar cambios productivos.")
    parser.add_argument("--manual", type=Path, required=True, help="CSV agrupado con decisiones manuales.")
    parser.add_argument("--directorio", type=Path, default=DIRECTORIO)
    parser.add_argument("--sobrescribir", action="store_true", help="Reemplaza sólo las dos salidas de consolidación.")
    args = parser.parse_args()
    try:
        fuente = leer_csv(args.directorio / "validacion_comercial_modelos.csv")
        if len(fuente) != 1484:
            raise ErrorConsolidacion(f"Se esperaban 1484 candidatos; se obtuvieron {len(fuente)}.")
        decisiones = decisiones_por_modelo(leer_csv(args.manual))
        filas = consolidar(fuente, decisiones)
        conteo = Counter(fila["clasificacion_final"] for fila in filas)
        if sum(conteo.values()) != len(filas):
            raise ErrorConsolidacion("Las clasificaciones finales no suman el total.")
        salidas = [
            args.directorio / "clasificacion_final_productos.csv",
            args.directorio / "reporte_clasificacion_final.md",
        ]
        if any(ruta.exists() for ruta in salidas) and not args.sobrescribir:
            raise ErrorConsolidacion("Las salidas finales ya existen; no se sobrescribieron.")
        escribir_csv(salidas[0], filas)
        escribir_reporte(salidas[1], filas)
    except ErrorConsolidacion as exc:
        print(f"ERROR: Consolidación no generada. {exc}", file=sys.stderr)
        return 2
    print("Consolidación completada: " + ", ".join(ruta.name for ruta in salidas))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
