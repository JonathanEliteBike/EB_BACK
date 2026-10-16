"""Simula Modelo/Submodelo con los CSV de una auditoría ya concluida.

No consulta Odoo, MySQL, ni modifica código productivo. Conserva literalmente
la clasificación de bicicletas y solo produce candidatos cuando una ruta de
categoría profunda coincide con el nombre de la plantilla.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable
import unicodedata


RAIZ = Path(__file__).resolve().parents[1]
DIRECTORIO_PREDETERMINADO = RAIZ / 'reportes_auditoria' / 'auditoria_modelos_2026-01-01_2026-10-09_facturas'

FAMILIAS_CATEGORIA = {
    'BICICLETA': ('BICICLETA',),
    'ROPA': ('APPAREL',),
    'ACCESORIOS': ('ACCESORIOS', 'ACCESORIOS VARIOS'),
    'COMPONENTES': ('COMPONENTES',),
    'REFACCIONES': ('REEMPLAZO', 'REEMPLAZOS'),
    'CUADRO': ('CUADRO',),
    'HERRAMIENTAS': ('HERRAMIENTAS',),
    'PERIFERICOS': ('PERIFERICOS',),
    'SERVICIOS': ('SERVICIOS',),
}
SERVICIOS = {'SERVICIOS', 'SOPORTE DE VENTAS'}


class ErrorSimulacion(RuntimeError):
    pass


def normalizar(valor: str) -> str:
    texto = unicodedata.normalize('NFD', str(valor or ''))
    return ''.join(caracter for caracter in texto if unicodedata.category(caracter) != 'Mn').upper().strip()


def es_faltante(valor: str) -> bool:
    return not str(valor or '').strip() or str(valor).strip().casefold().startswith('sin ')


def leer_csv(ruta: Path) -> list[dict[str, str]]:
    if not ruta.is_file():
        raise ErrorSimulacion(f'No existe el archivo requerido: {ruta.name}')
    with ruta.open('r', encoding='utf-8-sig', newline='') as archivo:
        return list(csv.DictReader(archivo))


def entero(fila: dict[str, str], campo: str) -> int:
    try:
        return int(fila.get(campo) or 0)
    except ValueError as exc:
        raise ErrorSimulacion(f'Valor no numérico en {campo}: {fila.get(campo)!r}') from exc


def validar_fuentes(directorio: Path) -> tuple[list[dict[str, str]], dict]:
    detalle = leer_csv(directorio / 'detalle_productos.csv')
    resumen = leer_csv(directorio / 'resumen_clasificacion.csv')
    estado_ruta = directorio / 'estado_ejecucion.json'
    if not estado_ruta.is_file():
        raise ErrorSimulacion('Falta estado_ejecucion.json; no se puede validar periodo ni vista.')
    estado = json.loads(estado_ruta.read_text(encoding='utf-8'))
    if not estado.get('completado'):
        raise ErrorSimulacion('La auditoría de origen no está marcada como completada.')

    totales_detalle = {
        'productos_unicos': len(detalle),
        'lineas_venta': sum(entero(fila, 'lineas_venta') for fila in detalle),
        'sin_modelo': sum(fila.get('sin_modelo') == 'True' for fila in detalle),
        'sin_submodelo': sum(fila.get('sin_submodelo') == 'True' for fila in detalle),
    }
    totales_resumen = {
        campo: sum(entero(fila, campo) for fila in resumen)
        for campo in totales_detalle
    }
    if totales_detalle != totales_resumen:
        raise ErrorSimulacion(
            f'Inconsistencia entre detalle y resumen: detalle={totales_detalle}; resumen={totales_resumen}.'
        )
    if len({fila.get('product_id') for fila in detalle}) != len(detalle):
        raise ErrorSimulacion('detalle_productos.csv contiene product_id duplicados; no se puede simular productos únicos.')
    return detalle, {'estado': estado, 'totales': totales_detalle}


def niveles_comerciales(fila: dict[str, str]) -> list[str]:
    segmentos = [segmento.strip() for segmento in (fila.get('categoria_completa') or '').split('/') if segmento.strip()]
    familia = normalizar(fila.get('familia', ''))
    posibles = FAMILIAS_CATEGORIA.get(familia, (familia,))
    for indice, segmento in enumerate(segmentos):
        if normalizar(segmento) in posibles:
            return segmentos[indice + 1:]
    # Familias con rama propia (p.ej. VITTORIA / LLANTAS): el segundo segmento
    # ya es el producto, de modo que lo que siga son niveles comerciales.
    if len(segmentos) > 1 and normalizar(segmentos[1]) == familia:
        return segmentos[2:]
    return []


def coincide_plantilla(candidato: str, plantilla: str) -> bool:
    candidato_normalizado = normalizar(candidato)
    plantilla_normalizada = normalizar(plantilla)
    return len(candidato_normalizado) >= 4 and candidato_normalizado in plantilla_normalizada


def simular(fila: dict[str, str]) -> dict[str, str]:
    actual_modelo = fila.get('modelo_actual', '')
    actual_submodelo = fila.get('submodelo_actual', '')
    familia = normalizar(fila.get('familia', ''))
    niveles = niveles_comerciales(fila)
    base = {
        'SKU': fila.get('sku', ''),
        'Marca': fila.get('marca', ''),
        'Familia': fila.get('familia', ''),
        'Nombre de producto': fila.get('producto', ''),
        'Plantilla': fila.get('plantilla', ''),
        'Categoría completa': fila.get('categoria_completa', ''),
        'Modelo actual': actual_modelo,
        'Submodelo actual': actual_submodelo,
        'Modelo propuesto': '',
        'Submodelo propuesto': '',
        'Origen del dato propuesto': '',
        'Nivel de confianza': 'Baja',
        'Motivo de la propuesta': '',
        'Requiere validación comercial': 'Sí',
    }
    if familia == 'BICICLETA':
        base.update({
            'Modelo propuesto': actual_modelo,
            'Submodelo propuesto': actual_submodelo,
            'Origen del dato propuesto': 'Clasificación actual de bicicleta',
            'Nivel de confianza': 'Alta',
            'Motivo de la propuesta': 'Se conserva literalmente la clasificación vigente de bicicletas.',
            'Requiere validación comercial': 'No',
        })
        return base
    if familia in SERVICIOS:
        base.update({
            'Modelo propuesto': 'No aplica',
            'Submodelo propuesto': 'No aplica',
            'Origen del dato propuesto': 'Familia de servicio',
            'Nivel de confianza': 'Alta',
            'Motivo de la propuesta': 'Los servicios no se fuerzan a una taxonomía de modelo/submodelo.',
            'Requiere validación comercial': 'No',
        })
        return base
    if not es_faltante(actual_modelo) or not es_faltante(actual_submodelo):
        base.update({
            'Modelo propuesto': actual_modelo,
            'Submodelo propuesto': actual_submodelo,
            'Origen del dato propuesto': 'Clasificación actual existente',
            'Nivel de confianza': 'Alta',
            'Motivo de la propuesta': 'Se conserva un valor existente fuera de la regla de inferencia.',
            'Requiere validación comercial': 'No',
        })
        return base

    # Para una familia no bicicleta se exige: categoría con al menos tres
    # niveles después de la familia y coincidencia explícita con plantilla.
    # Esto cubre CASCO / MONTAÑA / ARGO PLUS, pero no LLANTAS / DE MONTAÑA.
    if len(niveles) >= 3 and coincide_plantilla(niveles[2], fila.get('plantilla', '')):
        base.update({
            'Modelo propuesto': niveles[2],
            'Origen del dato propuesto': 'Nivel 3 de categoría + coincidencia con plantilla',
            'Nivel de confianza': 'Media',
            'Motivo de la propuesta': 'El nivel comercial profundo está contenido en la plantilla; requiere validación semántica por familia.',
        })
        if len(niveles) >= 4 and coincide_plantilla(niveles[3], fila.get('plantilla', '')):
            base['Submodelo propuesto'] = niveles[3]
            base['Origen del dato propuesto'] = 'Niveles 3 y 4 de categoría + coincidencia con plantilla'
            base['Motivo de la propuesta'] = 'Ambos niveles comerciales aparecen en la plantilla; requieren validación semántica por familia.'
        return base
    if len(niveles) >= 3:
        base['Motivo de la propuesta'] = 'La categoría contiene un posible nivel comercial, pero no hay coincidencia suficiente con la plantilla.'
    elif niveles:
        base['Motivo de la propuesta'] = 'La ruta no tiene suficientes niveles comerciales para separar modelo y submodelo sin inferencia.'
    else:
        base['Motivo de la propuesta'] = 'No se pudo identificar una estructura comercial de categoría compatible con la familia.'
    return base


def estadisticas(filas: Iterable[dict[str, str]]) -> tuple[dict[str, int], list[dict[str, object]], list[dict[str, str]]]:
    filas = list(filas)
    resumen = {
        'productos': len(filas),
        'conservan': 0,
        'nuevo_modelo': 0,
        'nuevo_submodelo': 0,
        'sin_informacion': 0,
        'alta': 0,
        'media': 0,
        'baja': 0,
        'requieren_validacion': 0,
    }
    por_grupo = defaultdict(lambda: defaultdict(int))
    ambiguos = []
    for fila in filas:
        modelo_nuevo = bool(fila['Modelo propuesto']) and fila['Modelo propuesto'] not in (fila['Modelo actual'], 'No aplica')
        submodelo_nuevo = bool(fila['Submodelo propuesto']) and fila['Submodelo propuesto'] not in (fila['Submodelo actual'], 'No aplica')
        conserva = fila['Modelo propuesto'] == fila['Modelo actual'] and fila['Submodelo propuesto'] == fila['Submodelo actual']
        pendiente = not fila['Modelo propuesto'] and es_faltante(fila['Modelo actual'])
        resumen['conservan'] += int(conserva)
        resumen['nuevo_modelo'] += int(modelo_nuevo)
        resumen['nuevo_submodelo'] += int(submodelo_nuevo)
        resumen['sin_informacion'] += int(pendiente)
        resumen[fila['Nivel de confianza'].lower()] += 1
        resumen['requieren_validacion'] += int(fila['Requiere validación comercial'] == 'Sí')
        grupo = por_grupo[(fila['Marca'], fila['Familia'])]
        grupo['productos'] += 1
        grupo['conservan'] += int(conserva)
        grupo['nuevo_modelo'] += int(modelo_nuevo)
        grupo['nuevo_submodelo'] += int(submodelo_nuevo)
        grupo['sin_informacion'] += int(pendiente)
        grupo['alta'] += int(fila['Nivel de confianza'] == 'Alta')
        grupo['media'] += int(fila['Nivel de confianza'] == 'Media')
        grupo['baja'] += int(fila['Nivel de confianza'] == 'Baja')
        if fila['Nivel de confianza'] == 'Baja' and len(ambiguos) < 30:
            ambiguos.append(fila)
    grupos = [
        {'marca': marca, 'familia': familia, **dict(valores)}
        for (marca, familia), valores in por_grupo.items()
    ]
    grupos.sort(key=lambda fila: (-fila['productos'], fila['marca'], fila['familia']))
    return resumen, grupos, ambiguos


def escribir_csv(ruta: Path, filas: list[dict[str, str]]) -> None:
    columnas = [
        'SKU', 'Marca', 'Familia', 'Nombre de producto', 'Plantilla', 'Categoría completa',
        'Modelo actual', 'Submodelo actual', 'Modelo propuesto', 'Submodelo propuesto',
        'Origen del dato propuesto', 'Nivel de confianza', 'Motivo de la propuesta',
        'Requiere validación comercial',
    ]
    with ruta.open('w', encoding='utf-8-sig', newline='') as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=columnas)
        escritor.writeheader()
        escritor.writerows(filas)


def escribir_markdown(ruta: Path, validacion: dict, resumen: dict[str, int], grupos: list[dict], ambiguos: list[dict]) -> None:
    estado = validacion['estado']
    totales = validacion['totales']
    lineas = [
        '# Simulación de clasificación Modelo/Submodelo', '',
        '## Validación de fuentes', '',
        f"- Periodo auditado: {estado.get('fecha_inicio')} a {estado.get('fecha_fin')}",
        f"- Vista auditada: {estado.get('vista')}",
        f"- detalle_productos.csv: {totales['productos_unicos']} productos únicos y {totales['lineas_venta']} líneas.",
        f"- resumen_clasificacion.csv: los mismos totales y faltantes ({totales['sin_modelo']} modelo, {totales['sin_submodelo']} submodelo).", '',
        'No se detectaron diferencias; la simulación usa el universo completo de detalle_productos.csv.', '',
        '## Resultado general', '',
        f"- Productos analizados: {resumen['productos']}",
        f"- Conservan clasificación: {resumen['conservan']}",
        f"- Nuevo modelo candidato: {resumen['nuevo_modelo']}",
        f"- Nuevo submodelo candidato: {resumen['nuevo_submodelo']}",
        f"- Sin información suficiente: {resumen['sin_informacion']}",
        f"- Confianza alta / media / baja: {resumen['alta']} / {resumen['media']} / {resumen['baja']}",
        f"- Requieren validación comercial: {resumen['requieren_validacion']}", '',
        '## Reglas simuladas', '',
        '1. Bicicletas: se conserva literalmente Modelo y Submodelo actuales.',
        '2. Servicios y soporte de ventas: Modelo/Submodelo = No aplica.',
        '3. Otras familias: se propone Modelo solo cuando el tercer nivel comercial después de la familia aparece también en la plantilla.',
        '4. Se propone Submodelo solo cuando un cuarto nivel también aparece en la plantilla.',
        '5. SKU, Color y Talla no participan en la inferencia.',
        '6. Una coincidencia categoría/plantilla es confianza media y requiere validación comercial; ausencia de evidencia permanece pendiente.', '',
        '## Comparativa por marca y familia', '',
        '| Marca | Familia | Productos | Conservan | Modelo candidato | Submodelo candidato | Pendientes | Alta | Media | Baja |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|',
    ]
    for fila in grupos:
        lineas.append(
            f"| {fila['marca']} | {fila['familia']} | {fila['productos']} | {fila['conservan']} | "
            f"{fila['nuevo_modelo']} | {fila['nuevo_submodelo']} | {fila['sin_informacion']} | "
            f"{fila['alta']} | {fila['media']} | {fila['baja']} |"
        )
    lineas.extend(['', '## Casos ambiguos que requieren revisión', ''])
    if not ambiguos:
        lineas.append('- No se encontraron casos ambiguos bajo esta regla conservadora.')
    else:
        lineas.extend([
            '| SKU | Marca / Familia | Categoría | Plantilla | Motivo |',
            '|---|---|---|---|---|',
        ])
        for fila in ambiguos:
            lineas.append(
                f"| {fila['SKU'] or '-'} | {fila['Marca']} / {fila['Familia']} | {fila['Categoría completa'] or '-'} | "
                f"{fila['Plantilla'] or '-'} | {fila['Motivo de la propuesta']} |"
            )
    lineas.extend([
        '', '## Riesgos y siguiente paso', '',
        '- La ruta de categoría no siempre representa una jerarquía de producto comercial.',
        '- La coincidencia con plantilla es evidencia, no una confirmación de negocio.',
        '- Vittoria y familias con rutas poco profundas permanecen pendientes; no se dedujo modelo desde SKU o texto libre.',
        '- Antes de implementar, validar muestras por familia y comprobar que los totales de Pzas/Venta no cambian al reagrupar.',
    ])
    ruta.write_text('\n'.join(lineas) + '\n', encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser(description='Simula clasificación usando reportes ya generados.')
    parser.add_argument('--directorio', type=Path, default=DIRECTORIO_PREDETERMINADO)
    args = parser.parse_args()
    try:
        detalle, validacion = validar_fuentes(args.directorio)
        simulados = [simular(fila) for fila in detalle]
        resumen, grupos, ambiguos = estadisticas(simulados)
        if resumen['productos'] != validacion['totales']['productos_unicos']:
            raise ErrorSimulacion('El total simulado no coincide con detalle_productos.csv.')
        salida_csv = args.directorio / 'simulacion_clasificacion.csv'
        salida_md = args.directorio / 'reporte_simulacion.md'
        if salida_csv.exists() or salida_md.exists():
            raise ErrorSimulacion('Ya existen reportes de simulación; no se sobrescribieron.')
        escribir_csv(salida_csv, simulados)
        escribir_markdown(salida_md, validacion, resumen, grupos, ambiguos)
    except ErrorSimulacion as exc:
        print(f'ERROR: Simulación no generada. {exc}', file=sys.stderr)
        return 2
    print(f'Simulación completada: {salida_csv.name}, {salida_md.name}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
