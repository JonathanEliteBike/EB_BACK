"""Bloque 3: valida técnicamente la simulación, sin consultar ni modificar Odoo.

Lee detalle_productos.csv y simulacion_clasificacion.csv ya existentes. Sus
resultados distinguen evidencia técnica de aprobación comercial; no alteran la
clasificación del Dashboard.
"""
from __future__ import annotations

import csv
import argparse
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path


RAIZ = Path(__file__).resolve().parents[1]
DIRECTORIO = RAIZ / 'reportes_auditoria' / 'auditoria_modelos_2026-01-01_2026-10-09_facturas'
GENERICO = {
    'PERFORMANCE', 'LIFESTYLE', 'MONTANA', 'RUTA', 'TRAIL', 'ENDURO', 'GRAVEL',
    'JUNIOR', 'INFANTIL', 'HOMBRE', 'MUJER', 'MEN', 'WOMEN', 'UNISEX', 'ADULTOS',
    'ACCESORIOS', 'COMPONENTES', 'REFACCIONES', 'DIVERSOS', 'ROPA', 'CASCO',
    'GUANTES', 'ZAPATOS', 'JERSEY', 'LLANTAS', 'CAMARAS', 'AIR LINERS',
}
PATRON_TALLA = re.compile(r'^(?:XXS|XS|S|M|L|XL|XXL|XXXL|OS|ONE SIZE|\d{2,3})$', re.I)


class ErrorValidacion(RuntimeError):
    pass


def normalizar(valor: str) -> str:
    texto = unicodedata.normalize('NFD', str(valor or ''))
    return ''.join(c for c in texto if unicodedata.category(c) != 'Mn').upper().strip()


def faltante(valor: str) -> bool:
    return not str(valor or '').strip() or str(valor).strip().casefold().startswith('sin ')


def csv_leer(ruta: Path) -> list[dict[str, str]]:
    if not ruta.is_file():
        raise ErrorValidacion(f'No existe {ruta.name}.')
    with ruta.open(encoding='utf-8-sig', newline='') as archivo:
        return list(csv.DictReader(archivo))


def ruta_segmentos(categoria: str) -> list[str]:
    return [segmento.strip() for segmento in str(categoria or '').split('/') if segmento.strip()]


def contiene(texto: str, candidato: str) -> bool:
    clave = normalizar(candidato)
    return len(clave) >= 4 and clave in normalizar(texto)


def es_generico(valor: str) -> bool:
    clave = normalizar(valor)
    return clave in GENERICO or bool(PATRON_TALLA.match(clave))


def atributo_contiene_valor(atributos: str, valor: str) -> bool:
    clave = normalizar(valor)
    return bool(clave) and clave in normalizar(atributos)


def validar_fila(sim: dict[str, str], detalle: dict[str, str]) -> dict[str, str]:
    familia = normalizar(sim['Familia'])
    marca = normalizar(sim['Marca'])
    modelo = sim['Modelo propuesto']
    submodelo = sim['Submodelo propuesto']
    resultado = 'PENDIENTE_SIN_EVIDENCIA'
    motivo = sim['Motivo de la propuesta']
    aprobacion = 'Sí'
    confianza = sim['Nivel de confianza']

    if familia == 'BICICLETA':
        resultado = 'PRESERVADA_BICICLETA'
        motivo = 'La simulación conserva exactamente la clasificación vigente de bicicletas.'
        aprobacion = 'No'
    elif modelo == 'No aplica':
        resultado = 'NO_APLICA_SERVICIO'
        motivo = 'Familia de servicio o soporte: Modelo/Submodelo no aplican.'
        aprobacion = 'No'
    elif modelo:
        plantilla = detalle.get('plantilla', '')
        producto = detalle.get('producto', '')
        atributos = detalle.get('atributos_variante', '')
        if es_generico(modelo):
            resultado = 'RECHAZAR_CANDIDATO_GENERICO'
            motivo = 'El valor candidato es una categoría amplia o parece una talla; no debe convertirse automáticamente en Modelo.'
            confianza = 'Baja'
        elif atributo_contiene_valor(atributos, modelo):
            resultado = 'RECHAZAR_CANDIDATO_VARIANTE'
            motivo = 'El valor candidato coincide con atributos de variante; no debe convertirse automáticamente en Modelo.'
            confianza = 'Baja'
        elif contiene(plantilla, modelo) and contiene(producto, modelo):
            resultado = 'COHERENTE_TECNICAMENTE'
            motivo = 'El nivel de categoría candidato aparece completo tanto en plantilla como en variante; requiere aprobación comercial.'
        elif contiene(plantilla, modelo):
            resultado = 'REVISAR_VARIANTE_DIFIERE'
            motivo = 'El candidato coincide con plantilla, pero no con el nombre de variante; revisar catálogo/variantes antes de aprobar.'
            confianza = 'Baja'
        else:
            resultado = 'RECHAZAR_SIN_RESPALDO'
            motivo = 'El candidato no conserva la evidencia categoría/plantilla exigida por la simulación.'
            confianza = 'Baja'
    else:
        niveles = ruta_segmentos(detalle.get('categoria_completa', ''))
        ultimo = niveles[-1] if niveles else ''
        if ultimo and contiene(detalle.get('plantilla', ''), ultimo) and not es_generico(ultimo):
            resultado = 'PENDIENTE_RUTA_CORTA_COHERENTE'
            motivo = 'El último nivel coincide con la plantilla, pero la estructura es demasiado corta para determinar si es categoría o Modelo.'
        elif marca == 'VITTORIA':
            resultado = 'PENDIENTE_VITTORIA_SIN_ESTRUCTURA'
            motivo = 'La categoría no expone una línea comercial separable y no existen atributos comerciales estructurados.'
        elif not faltante(sim['Modelo actual']):
            resultado = 'VALOR_EXISTENTE_FUERA_DE_REGLA'
            aprobacion = 'No'
            confianza = 'Alta'
        else:
            resultado = 'PENDIENTE_SIN_EVIDENCIA'

    return {
        'SKU': sim['SKU'], 'Marca': sim['Marca'], 'Familia': sim['Familia'],
        'Categoría completa': sim['Categoría completa'],
        'Modelo actual': sim['Modelo actual'], 'Submodelo actual': sim['Submodelo actual'],
        'Modelo candidato': modelo, 'Submodelo candidato': submodelo,
        'Origen del dato': sim['Origen del dato propuesto'], 'Confianza': confianza,
        'Resultado de la validación automática': resultado, 'Motivo': motivo,
        'Requiere aprobación comercial': aprobacion,
    }


def conciliar(simulacion: list[dict[str, str]], detalle: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    detalle_por_sku_producto = {}
    for fila in detalle:
        clave = (fila.get('sku', ''), fila.get('producto', ''), fila.get('categoria_completa', ''))
        if clave in detalle_por_sku_producto:
            raise ErrorValidacion('La llave SKU/producto/categoría no es única en detalle_productos.csv.')
        detalle_por_sku_producto[clave] = fila
    if len(simulacion) != len(detalle):
        raise ErrorValidacion(f'La simulación ({len(simulacion)}) no coincide con detalle ({len(detalle)}).')
    resultado = {}
    for fila in simulacion:
        clave = (fila.get('SKU', ''), fila.get('Nombre de producto', ''), fila.get('Categoría completa', ''))
        origen = detalle_por_sku_producto.get(clave)
        if origen is None:
            raise ErrorValidacion(f'No se encontró en detalle el producto simulado: {clave[0] or "sin SKU"}.')
        resultado[clave] = origen
    return resultado


def estadisticas(filas: list[dict[str, str]]) -> tuple[Counter, list[dict], list[dict], dict[str, Counter]]:
    totales = Counter()
    por_grupo: dict[str, Counter] = defaultdict(Counter)
    vittoria = []
    casos_catalogo = []
    for fila in filas:
        totales['productos'] += 1
        totales[fila['Resultado de la validación automática']] += 1
        totales[f'confianza_{fila["Confianza"].lower()}'] += 1
        totales['requiere_aprobacion'] += fila['Requiere aprobación comercial'] == 'Sí'
        clave = f"{fila['Marca']} / {fila['Familia']}"
        por_grupo[clave]['productos'] += 1
        por_grupo[clave][fila['Resultado de la validación automática']] += 1
        if normalizar(fila['Marca']) == 'VITTORIA':
            vittoria.append(fila)
        if fila['Resultado de la validación automática'] in {
            'REVISAR_VARIANTE_DIFIERE', 'RECHAZAR_CANDIDATO_GENERICO', 'RECHAZAR_CANDIDATO_VARIANTE',
        }:
            casos_catalogo.append(fila)
    return totales, vittoria, casos_catalogo, por_grupo


def escribir_csv(ruta: Path, filas: list[dict[str, str]]) -> None:
    columnas = [
        'SKU', 'Marca', 'Familia', 'Categoría completa', 'Modelo actual', 'Submodelo actual',
        'Modelo candidato', 'Submodelo candidato', 'Origen del dato', 'Confianza',
        'Resultado de la validación automática', 'Motivo', 'Requiere aprobación comercial',
    ]
    with ruta.open('w', encoding='utf-8-sig', newline='') as archivo:
        writer = csv.DictWriter(archivo, fieldnames=columnas)
        writer.writeheader()
        writer.writerows(filas)


def escribir_reporte(ruta: Path, filas: list[dict[str, str]], totales: Counter, vittoria: list[dict], casos: list[dict], grupos: dict[str, Counter]) -> None:
    lineas = [
        '# Validación de reglas de Modelo/Submodelo — Bloque 3', '',
        '## A. Resumen y conciliación', '',
        f"- Universo validado: {totales['productos']} productos.",
        f"- Bicicletas preservadas: {totales['PRESERVADA_BICICLETA']}.",
        f"- Servicios/soporte no aplicables: {totales['NO_APLICA_SERVICIO']}.",
        f"- Candidatos técnicamente coherentes: {totales['COHERENTE_TECNICAMENTE']}.",
        f"- Candidatos con variante distinta: {totales['REVISAR_VARIANTE_DIFIERE']}.",
        f"- Candidatos rechazados automáticamente: {totales['RECHAZAR_CANDIDATO_GENERICO'] + totales['RECHAZAR_CANDIDATO_VARIANTE'] + totales['RECHAZAR_SIN_RESPALDO']}.",
        f"- Pendientes sin evidencia/ruta corta/Vittoria: {totales['PENDIENTE_SIN_EVIDENCIA'] + totales['PENDIENTE_RUTA_CORTA_COHERENTE'] + totales['PENDIENTE_VITTORIA_SIN_ESTRUCTURA']}.",
        f"- Confianza alta / media / baja: {totales['confianza_alta']} / {totales['confianza_media']} / {totales['confianza_baja']}.",
        f"- Requieren aprobación comercial: {totales['requiere_aprobacion']}.", '',
        'La diferencia de 12 productos del Bloque 2 se concilia así: son servicios o soporte de ventas marcados como `No aplica`. '
        'Las categorías “conserva”, “nuevo candidato”, “sin evidencia” y “No aplica” son excluyentes; la confianza es una métrica independiente.', '',
        '## B. Evaluación de candidatos', '',
        'Los candidatos nacen de la coincidencia completa entre un nivel profundo de categoría y la plantilla. '
        'Esta prueba es técnica, no comercial: los candidatos coherentes conservan confianza media y requieren aprobación.', '',
        '## C. Matriz de reglas recomendadas', '',
        '| Familia | Modelo | Submodelo | Estado recomendado |',
        '|---|---|---|---|',
        '| Bicicletas | Conservar regla actual | Conservar regla actual | Implementable sin cambio funcional |',
        '| Ropa / cascos / guantes / zapatos | Nivel profundo de categoría solo si coincide con plantilla y variante | Solo versión comercial explícita, nunca Color/Talla | Candidato, aprobación comercial |',
        '| Vittoria llantas/cámaras/Air Liners | No inferir desde texto/SKU | No inferir | Requiere catálogo o equivalencias aprobadas |',
        '| Syncros componentes/accesorios | No inferir con rutas cortas | No inferir | Pendiente de estructura comercial |',
        '| Refacciones/cuadros | No forzar | No forzar | Pendiente; revisar ruta corta coherente |',
        '| Servicios | No aplica | No aplica | Aprobable como no aplicable |', '',
        '## D. Vittoria', '',
        f"- Productos Vittoria analizados: {len(vittoria)}.",
        f"- Pendientes por falta de estructura: {sum(f['Resultado de la validación automática'] == 'PENDIENTE_VITTORIA_SIN_ESTRUCTURA' for f in vittoria)}.",
        '- Las categorías observadas no separan de manera verificable línea comercial y versión. Los nombres/plantillas pueden contener textos comerciales, pero sin atributo estructurado ni equivalencia aprobada no se convierten en Modelo.', '',
        '## E. Riesgos que no deben automatizarse', '',
        '- Usar el último segmento de categoría como Modelo sin corroboración.',
        '- Extraer líneas Vittoria por coincidencias parciales de texto o SKU.',
        '- Convertir Color, Talla, género, disciplina o característica técnica en Modelo/Submodelo.',
        '- Tratar coincidencia con plantilla como aprobación comercial definitiva.', '',
        '## F. Posibles problemas de catálogo', '',
    ]
    if casos:
        lineas.extend(['| SKU | Marca/Familia | Modelo candidato | Resultado | Motivo |', '|---|---|---|---|---|'])
        for fila in casos[:30]:
            lineas.append(f"| {fila['SKU'] or '-'} | {fila['Marca']} / {fila['Familia']} | {fila['Modelo candidato'] or '-'} | {fila['Resultado de la validación automática']} | {fila['Motivo']} |")
    else:
        lineas.append('- No se detectaron contradicciones técnicas dentro de los candidatos de categoría/plantilla.')
    lineas.extend(['', '## G. Desglose por marca y familia', '', '| Marca / familia | Productos | Coherentes | Pendientes |', '|---|---:|---:|---:|'])
    for clave, valores in sorted(grupos.items(), key=lambda item: -item[1]['productos']):
        pendientes = sum(valor for nombre, valor in valores.items() if nombre.startswith('PENDIENTE_'))
        lineas.append(f"| {clave} | {valores['productos']} | {valores['COHERENTE_TECNICAMENTE']} | {pendientes} |")
    lineas.extend([
        '', '## H. Recomendación para el backend', '',
        'No implementar aún las propuestas de ropa. Cuando exista aprobación comercial, el clasificador debe añadir una regla general '
        'por estructura de categoría y coincidencia con plantilla, manteniendo bicicletas sin cambio y dejando los casos ambiguos sin valor.', '',
        '## I. Decisiones comerciales pendientes', '',
        '1. Confirmar que el tercer nivel comercial de Apparel representa Modelo en cada subfamilia.',
        '2. Definir si existe Submodelo para Apparel más allá de Color/Talla.',
        '3. Proporcionar un catálogo o equivalencias aprobadas para Vittoria.',
        '4. Decidir si las rutas cortas de cuadros/refacciones deben exponer Modelo o permanecer sin aplicación.',
    ])
    ruta.write_text('\n'.join(lineas) + '\n', encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser(description='Valida la simulación de clasificación sin consultar Odoo.')
    parser.add_argument('--directorio', type=Path, default=DIRECTORIO)
    parser.add_argument('--sobrescribir', action='store_true', help='Reemplaza únicamente los dos reportes del Bloque 3.')
    args = parser.parse_args()
    directorio = args.directorio
    try:
        detalle = csv_leer(directorio / 'detalle_productos.csv')
        simulacion = csv_leer(directorio / 'simulacion_clasificacion.csv')
        origenes = conciliar(simulacion, detalle)
        filas = [
            validar_fila(fila, origenes[(fila.get('SKU', ''), fila.get('Nombre de producto', ''), fila.get('Categoría completa', ''))])
            for fila in simulacion
        ]
        totales, vittoria, casos, grupos = estadisticas(filas)
        if totales['productos'] != 3373:
            raise ErrorValidacion(f"El universo esperado es 3373; se obtuvieron {totales['productos']}.")
        salida_csv = directorio / 'validacion_candidatos.csv'
        salida_md = directorio / 'reporte_validacion_reglas.md'
        if (salida_csv.exists() or salida_md.exists()) and not args.sobrescribir:
            raise ErrorValidacion('Los reportes de Bloque 3 ya existen; no se sobrescribieron.')
        escribir_csv(salida_csv, filas)
        escribir_reporte(salida_md, filas, totales, vittoria, casos, grupos)
    except ErrorValidacion as exc:
        print(f'ERROR: Validación no generada. {exc}', file=sys.stderr)
        return 2
    print(f'Validación completada: {salida_csv.name}, {salida_md.name}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
