"""Audita localmente la segunda pasada de Modelo no bicicleta.

No consulta Odoo ni persiste datos: usa el CSV de auditoría existente y las
funciones de clasificación que emplea el Dashboard.
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes.ventas import (  # noqa: E402
    _dashboard_dimensiones_comerciales_producto,
    _dashboard_marcas_por_ruta,
    _dashboard_ruta_categoria,
)
import utils.dashboard_clasificacion_comercial as clasificacion  # noqa: E402


ARCHIVO = Path(
    'reportes_auditoria/auditoria_modelos_2026-01-01_2026-10-09_facturas/'
    'detalle_productos.csv'
)


@contextmanager
def sin_segunda_pasada():
    """Permite medir el antes sin alterar el código ni el CSV."""
    original = clasificacion._MODELOS_B2B_ADICIONALES_POR_TIPO
    clasificacion._MODELOS_B2B_ADICIONALES_POR_TIPO = {}
    try:
        yield
    finally:
        clasificacion._MODELOS_B2B_ADICIONALES_POR_TIPO = original


def clasificar(filas: list[dict]) -> list[tuple[dict, dict]]:
    rutas = [_dashboard_ruta_categoria(fila['categoria_completa']) for fila in filas]
    marcas = _dashboard_marcas_por_ruta(rutas)
    return [
        (
            fila,
            _dashboard_dimensiones_comerciales_producto(
                int(fila['product_id']), ruta, [], fila.get('plantilla'), marcas,
            )[0],
        )
        for fila, ruta in zip(filas, rutas)
    ]


def principal() -> None:
    with ARCHIVO.open(encoding='utf-8-sig', newline='') as archivo:
        filas = list(csv.DictReader(archivo))

    with sin_segunda_pasada():
        antes = {fila['product_id']: dimensiones for fila, dimensiones in clasificar(filas)}
    despues = {fila['product_id']: dimensiones for fila, dimensiones in clasificar(filas)}

    no_bicicleta = [
        fila for fila in filas if despues[fila['product_id']].get('producto') != 'BICICLETA'
    ]
    nuevos = [
        fila for fila in no_bicicleta
        if not antes[fila['product_id']].get('modelo') and despues[fila['product_id']].get('modelo')
    ]
    sin_modelo = [fila for fila in no_bicicleta if not despues[fila['product_id']].get('modelo')]
    submodelos_nuevos = sum(
        bool(
            not antes[fila['product_id']].get('submodelo')
            and despues[fila['product_id']].get('submodelo')
        )
        for fila in no_bicicleta
    )
    bicicletas_modificadas = sum(
        antes[fila['product_id']] != despues[fila['product_id']]
        and despues[fila['product_id']].get('producto') == 'BICICLETA'
        for fila in filas
    )

    print(f'Total no bicicleta: {len(no_bicicleta)}')
    print(f'Con Modelo antes: {len(no_bicicleta) - sum(not antes[f["product_id"]].get("modelo") for f in no_bicicleta)}')
    print(f'Sin modelo antes: {sum(not antes[f["product_id"]].get("modelo") for f in no_bicicleta)}')
    print(f'Con Modelo después: {len(no_bicicleta) - len(sin_modelo)}')
    print(f'Sin modelo después: {len(sin_modelo)}')
    print(f'Nuevos Modelos: {len(nuevos)}')
    print(f'product_id afectados (únicos): {len({fila["product_id"] for fila in nuevos})}')
    print(f'Bicicletas modificadas: {bicicletas_modificadas}')
    print(f'Submodelos nuevos: {submodelos_nuevos}')

    estados_manuales = Counter(
        (clasificacion.obtener_clasificacion_comercial(int(fila['product_id'])) or {}).get('c', 'SIN_MAPEO')
        for fila in nuevos
    )
    print(f'Estados manuales de los afectados: {dict(estados_manuales)}')
    if bicicletas_modificadas or submodelos_nuevos:
        raise RuntimeError('La segunda pasada no debe generar Submodelos.')

    print('\nMODELOS NUEVOS')
    modelos_nuevos = Counter(despues[f['product_id']]['modelo'] for f in nuevos)
    for modelo, cantidad in modelos_nuevos.most_common():
        print(f'{cantidad:3} | {modelo}')

    prohibidos = {
        'MTB', 'TRAIL', 'SPORT', 'PERFORMANCE', 'ENDURANCE', 'RACING',
        'SHORT', 'ALL MOUNTAIN', 'BOSCH', 'TQ',
    }
    falsos_positivos = sorted(
        modelo for modelo in modelos_nuevos if str(modelo).upper() in prohibidos
    )
    print(f'Falsos positivos por término prohibido: {falsos_positivos or "ninguno"}')
    if falsos_positivos:
        raise RuntimeError('Se detectaron términos genéricos promovidos a Modelo.')

    print('\nEJEMPLOS')
    for fila in nuevos[:20]:
        dimensiones = despues[fila['product_id']]
        print(
            f"{fila['product_id']} | {fila['plantilla']} | {dimensiones.get('marca')} | "
            f"{dimensiones.get('producto')} | {dimensiones.get('categoria')} | "
            f"{dimensiones.get('subcategoria')} | {dimensiones.get('modelo')}"
        )

    print('\nTOP PENDIENTES DESPUÉS')
    for clave, cantidad in Counter(
        (despues[fila['product_id']].get('marca') or 'Sin marca',
         despues[fila['product_id']].get('producto') or 'Sin producto',
         despues[fila['product_id']].get('categoria') or 'Sin categoría')
        for fila in sin_modelo
    ).most_common(20):
        print(f'{cantidad:3} | {clave}')


if __name__ == '__main__':
    principal()
