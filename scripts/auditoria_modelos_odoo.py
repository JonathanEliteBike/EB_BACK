"""Auditoría de solo lectura para Modelo y Submodelo del Dashboard de Ventas.

Ejemplo (PowerShell, desde EB_BACK):
  .\\.venv\\Scripts\\python.exe .\\scripts\\auditoria_modelos_odoo.py `
    --fecha-inicio 2026-01-01 --fecha-fin 2026-10-09 --vista facturas

No arranca Flask, no usa MySQL y no hace escrituras en Odoo. Reutiliza la
configuración XML-RPC y las funciones de clasificación existentes del backend.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import shutil
import socket
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable


RAIZ_PROYECTO = Path(__file__).resolve().parents[1]
if str(RAIZ_PROYECTO) not in sys.path:
    sys.path.insert(0, str(RAIZ_PROYECTO))

from routes.ventas import (  # noqa: E402 - se importa tras registrar la raíz.
    _dashboard_clave_texto,
    _dashboard_dimensiones_producto,
    _dashboard_lotes,
    _dashboard_marcas_por_ruta,
    _dashboard_ruta_categoria,
)
from utils.odoo_utils import (  # noqa: E402
    ODOO_COMPANY_ID,
    ODOO_DB,
    ODOO_PASSWORD,
    get_odoo_models,
)


ATRIBUTOS_COMERCIALES = ('MODELO', 'MODEL', 'LINEA', 'GAMA', 'VERSION', 'SERIE', 'RANGE')
ATRIBUTOS_VARIANTE = ('COLOR', 'COLO', 'COLOUR', 'TALLA', 'TAMANO', 'SIZE')
# Solo se requiere product_id para contar líneas y deduplicar productos. display_type
# se verifica porque forma parte del dominio del Dashboard, pero no se vuelve a leer.
CAMPO_REQUERIDO_LINEA = {'product_id', 'display_type'}
CAMPO_PRODUCTO = {
    'id', 'name', 'default_code', 'categ_id', 'product_tmpl_id',
    'product_template_attribute_value_ids',
}
CAMPO_PLANTILLA = {'id', 'name', 'attribute_line_ids'}
CAMPO_CATEGORIA = {'id', 'complete_name'}
CAMPO_PTAV = {'id', 'attribute_id', 'name'}
CAMPO_LINEA_ATRIBUTO = {'id', 'attribute_id', 'value_ids'}


class ErrorAuditoria(RuntimeError):
    """Error explicable al usuario, sin incluir secretos ni traza sensible."""


@dataclass
class Limitaciones:
    mensajes: list[str] = field(default_factory=list)

    def agregar(self, mensaje: str) -> None:
        if mensaje not in self.mensajes:
            self.mensajes.append(mensaje)


def argumentos() -> argparse.Namespace:
    hoy = date.today().isoformat()
    parser = argparse.ArgumentParser(
        description='Audita datos de Modelo/Submodelo sin modificar Odoo ni el Dashboard.'
    )
    parser.add_argument('--fecha-inicio', default=f'{date.today().year}-01-01', help='YYYY-MM-DD')
    parser.add_argument('--fecha-fin', default=hoy, help='YYYY-MM-DD')
    parser.add_argument('--vista', choices=('facturas', 'cobranza'), default='facturas')
    parser.add_argument('--page-size', type=int, default=500, help='Tamaño de lote XML-RPC (1 a 1000).')
    parser.add_argument(
        '--salida', type=Path, default=RAIZ_PROYECTO / 'reportes_auditoria',
        help='Carpeta padre para el reporte fechado.',
    )
    args = parser.parse_args()
    try:
        inicio = date.fromisoformat(args.fecha_inicio)
        fin = date.fromisoformat(args.fecha_fin)
    except ValueError as exc:
        parser.error('Las fechas deben usar formato YYYY-MM-DD.')
        raise AssertionError from exc
    if inicio > fin:
        parser.error('--fecha-inicio no puede ser posterior a --fecha-fin.')
    if not 1 <= args.page_size <= 1000:
        parser.error('--page-size debe estar entre 1 y 1000.')
    return args


def ejecutar(modelos: Any, modelo: str, operacion: str, args: list, kwargs: dict | None = None) -> Any:
    """Centraliza XML-RPC de lectura y convierte errores frecuentes en mensajes claros."""
    try:
        return modelos.execute_kw(ODOO_DB, _UID, ODOO_PASSWORD, modelo, operacion, args, kwargs or {})
    except socket.gaierror as exc:
        raise ErrorAuditoria('No fue posible resolver el host de Odoo (DNS o red local).') from exc
    except Exception as exc:  # XML-RPC no expone una jerarquía uniforme de errores.
        detalle = str(exc).replace(ODOO_PASSWORD, '[oculto]') if ODOO_PASSWORD else str(exc)
        raise ErrorAuditoria(f'Lectura de Odoo falló en {modelo}.{operacion}: {detalle}') from exc


def campos_disponibles(modelos: Any, modelo: str, esperados: set[str], limitaciones: Limitaciones) -> set[str]:
    try:
        definiciones = ejecutar(modelos, modelo, 'fields_get', [], {'attributes': ['string', 'type']})
    except ErrorAuditoria as exc:
        limitaciones.agregar(f'No se pudo verificar campos de {modelo}: {exc}')
        return set()
    disponibles = set(definiciones or {})
    faltantes = sorted(esperados - disponibles)
    if faltantes:
        limitaciones.agregar(f'{modelo}: campos no disponibles o sin permiso: {", ".join(faltantes)}.')
    return disponibles & esperados


def leer_por_ids(modelos: Any, modelo: str, ids: Iterable[int], campos: set[str], lote: int) -> list[dict]:
    if not ids or not campos:
        return []
    registros: list[dict] = []
    for grupo in _dashboard_lotes(list(ids), lote):
        registros.extend(ejecutar(modelos, modelo, 'read', [grupo], {'fields': sorted(campos)}))
    return registros


def leer_lineas_venta(
    modelos: Any, fecha_inicio: str, fecha_fin: str, vista: str, lote: int, campos: set[str]
) -> list[dict]:
    dominio = [
        ['move_id.move_type', '=', 'out_invoice'],
        ['move_id.state', '=', 'posted'],
        ['move_id.invoice_date', '>=', fecha_inicio],
        ['move_id.invoice_date', '<=', fecha_fin],
        ['move_id.company_id', '=', ODOO_COMPANY_ID],
        ['display_type', '=', 'product'],
        ['product_id', '!=', False],
    ]
    if vista == 'cobranza':
        dominio.insert(2, ['move_id.payment_state', 'in', ['paid', 'partial']])

    lineas: list[dict] = []
    offset = 0
    while True:
        ids = ejecutar(modelos, 'account.move.line', 'search', [dominio], {
            'offset': offset, 'limit': lote, 'order': 'id asc',
        })
        if not ids:
            break
        lineas.extend(ejecutar(modelos, 'account.move.line', 'read', [ids], {
            'fields': sorted(campos),
        }))
        if len(ids) < lote:
            break
        offset += len(ids)
    return lineas


def m2o_id(valor: Any) -> int | None:
    return valor[0] if isinstance(valor, (list, tuple)) and valor else None


def texto(valor: Any) -> str:
    return ' '.join(str(valor or '').split()).strip()


def es_atributo_comercial(nombre: str) -> bool:
    clave = _dashboard_clave_texto(nombre)
    return any(token in clave for token in ATRIBUTOS_COMERCIALES)


def es_atributo_variante(nombre: str) -> bool:
    clave = _dashboard_clave_texto(nombre)
    return any(token in clave for token in ATRIBUTOS_VARIANTE)


def csv_en_memoria(columnas: list[str], filas: list[dict]) -> str:
    salida = io.StringIO(newline='')
    writer = csv.DictWriter(salida, fieldnames=columnas, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(filas)
    return salida.getvalue()


def crear_reportes(args: argparse.Namespace, datos: dict, limitaciones: Limitaciones) -> dict[str, str]:
    productos = datos['productos']
    resumen: dict[tuple[str, str], dict] = {}
    for producto in productos:
        clave = (producto['marca'], producto['familia'])
        fila = resumen.setdefault(clave, {
            'marca': producto['marca'], 'familia': producto['familia'],
            'productos_unicos': 0, 'lineas_venta': 0, 'sin_modelo': 0,
            'sin_submodelo': 0, 'atributos_estructurados_aprovechables': 0,
            'plantilla_candidata_modelo': 0, 'informacion_insuficiente': 0,
            'clasificacion_ambigua': 0,
        })
        fila['productos_unicos'] += 1
        fila['lineas_venta'] += producto['lineas_venta']
        for campo, bandera in (
            ('sin_modelo', producto['sin_modelo']),
            ('sin_submodelo', producto['sin_submodelo']),
            ('atributos_estructurados_aprovechables', bool(producto['atributos_comerciales'])),
            ('plantilla_candidata_modelo', producto['plantilla_candidata_modelo']),
            ('informacion_insuficiente', producto['informacion_insuficiente']),
            ('clasificacion_ambigua', producto['clasificacion_ambigua']),
        ):
            fila[campo] += int(bool(bandera))
    resumen_filas = []
    for fila in resumen.values():
        total = fila['productos_unicos'] or 1
        fila['pct_sin_modelo'] = round(fila['sin_modelo'] / total * 100, 2)
        fila['pct_sin_submodelo'] = round(fila['sin_submodelo'] / total * 100, 2)
        resumen_filas.append(fila)
    resumen_filas.sort(key=lambda fila: (-fila['lineas_venta'], fila['marca'], fila['familia']))

    atributos = defaultdict(lambda: {'atributo': '', 'productos_unicos': set(), 'lineas_venta': 0, 'familias': set(), 'es_candidato_comercial': False})
    for producto in productos:
        for atributo in producto['_atributos_lista']:
            nombre = atributo['atributo'] or 'Sin nombre de atributo'
            fila = atributos[nombre]
            fila['atributo'] = nombre
            fila['productos_unicos'].add(producto['product_id'])
            fila['lineas_venta'] += producto['lineas_venta']
            fila['familias'].add(f'{producto["marca"]} / {producto["familia"]}')
            fila['es_candidato_comercial'] = fila['es_candidato_comercial'] or es_atributo_comercial(nombre)
    atributos_filas = [{
        'atributo': fila['atributo'], 'productos_unicos': len(fila['productos_unicos']),
        'lineas_venta': fila['lineas_venta'], 'familias': ' | '.join(sorted(fila['familias'])),
        'es_candidato_comercial': 'Sí' if fila['es_candidato_comercial'] else 'No',
    } for fila in atributos.values()]
    atributos_filas.sort(key=lambda fila: (-fila['productos_unicos'], fila['atributo']))

    totales = {
        'productos': len(productos), 'lineas': sum(p['lineas_venta'] for p in productos),
        'sin_modelo': sum(p['sin_modelo'] for p in productos),
        'sin_submodelo': sum(p['sin_submodelo'] for p in productos),
        'vittoria': sum(p['marca'].casefold() == 'vittoria' for p in productos),
    }
    ejemplos = [p for p in productos if p['sin_modelo'] or p['sin_submodelo']][:12]
    markdown = [
        '# Auditoría de clasificación Modelo/Submodelo', '',
        f"- Periodo: {args.fecha_inicio} a {args.fecha_fin}",
        f"- Vista: {args.vista}",
        f"- Productos únicos: {totales['productos']}",
        f"- Líneas de venta: {totales['lineas']}",
        f"- Productos sin modelo actual: {totales['sin_modelo']}",
        f"- Productos sin submodelo actual: {totales['sin_submodelo']}",
        f"- Productos Vittoria: {totales['vittoria']}", '',
        '## Interpretación', '',
        'La clasificación actual se calcula con la misma función del Dashboard. '
        'Un atributo o plantilla candidato no representa una clasificación validada.', '',
        '## Ejemplos con clasificación faltante', '',
        '| SKU | Producto | Marca | Familia | Modelo actual | Submodelo actual | Atributos comerciales candidatos |',
        '|---|---|---|---|---|---|---|',
    ]
    for producto in ejemplos:
        markdown.append(
            f"| {producto['sku'] or '-'} | {producto['producto'] or '-'} | {producto['marca']} | "
            f"{producto['familia']} | {producto['modelo_actual']} | {producto['submodelo_actual']} | "
            f"{producto['atributos_comerciales'] or '-'} |"
        )
    markdown.extend(['', '## Limitaciones'])
    markdown.extend([f'- {mensaje}' for mensaje in limitaciones.mensajes] or ['- Ninguna limitación reportada por la extracción.'])
    markdown.extend([
        '', '## Próximo paso propuesto', '',
        'Validar por familia los atributos comerciales detectados y confirmar si la plantilla representa el modelo comercial. '
        'No usar SKU ni coincidencias libres de texto como regla automática sin esa validación.',
    ])

    detalle_columnas = [
        'product_id', 'sku', 'producto', 'plantilla', 'categoria_completa', 'marca', 'familia',
        'lineas_venta', 'modelo_actual', 'submodelo_actual', 'sin_modelo', 'sin_submodelo',
        'atributos_variante', 'atributos_comerciales', 'atributos_plantilla',
        'plantilla_candidata_modelo', 'informacion_insuficiente', 'clasificacion_ambigua',
    ]
    return {
        'resumen_clasificacion.csv': csv_en_memoria(list(resumen_filas[0].keys()) if resumen_filas else [
            'marca', 'familia', 'productos_unicos', 'lineas_venta', 'sin_modelo', 'sin_submodelo',
            'atributos_estructurados_aprovechables', 'plantilla_candidata_modelo',
            'informacion_insuficiente', 'clasificacion_ambigua', 'pct_sin_modelo', 'pct_sin_submodelo',
        ], resumen_filas),
        'detalle_productos.csv': csv_en_memoria(detalle_columnas, productos),
        'atributos_detectados.csv': csv_en_memoria(
            ['atributo', 'productos_unicos', 'lineas_venta', 'familias', 'es_candidato_comercial'], atributos_filas
        ),
        'reporte_auditoria.md': '\n'.join(markdown) + '\n',
    }


def auditar(args: argparse.Namespace) -> tuple[dict[str, str], Limitaciones]:
    global _UID
    limitaciones = Limitaciones()
    uid, modelos, error = get_odoo_models()
    if not uid or not modelos:
        texto_error = str(error or '').lower()
        if 'getaddrinfo' in texto_error or 'name resolution' in texto_error:
            raise ErrorAuditoria('No fue posible resolver el host de Odoo. Verifica DNS o red antes de reintentar.')
        raise ErrorAuditoria('No fue posible conectar o autenticar contra Odoo. Revisa conectividad, ambiente y permisos.')
    _UID = uid

    disponibles_linea = campos_disponibles(modelos, 'account.move.line', CAMPO_REQUERIDO_LINEA, limitaciones)
    if 'product_id' not in disponibles_linea:
        raise ErrorAuditoria('Faltan campos esenciales de account.move.line; no se puede construir una auditoría confiable.')
    # El dominio requiere display_type; si su metadata no es legible no es seguro cambiar el universo.
    if 'display_type' not in disponibles_linea:
        raise ErrorAuditoria('No se puede verificar display_type en account.move.line sin alterar el criterio del Dashboard.')
    disponibles_producto = campos_disponibles(modelos, 'product.product', CAMPO_PRODUCTO, limitaciones)
    if 'id' not in disponibles_producto:
        raise ErrorAuditoria('No se puede leer product.product.id; la auditoría no puede continuar.')
    disponibles_plantilla = campos_disponibles(modelos, 'product.template', CAMPO_PLANTILLA, limitaciones)
    disponibles_categoria = campos_disponibles(modelos, 'product.category', CAMPO_CATEGORIA, limitaciones)
    disponibles_ptav = campos_disponibles(modelos, 'product.template.attribute.value', CAMPO_PTAV, limitaciones)
    disponibles_linea_atributo = campos_disponibles(
        modelos, 'product.template.attribute.line', CAMPO_LINEA_ATRIBUTO, limitaciones
    )

    lineas = leer_lineas_venta(
        modelos, args.fecha_inicio, args.fecha_fin, args.vista, args.page_size, {'product_id'}
    )
    conteo_lineas = Counter(m2o_id(linea.get('product_id')) for linea in lineas if m2o_id(linea.get('product_id')))
    productos = leer_por_ids(modelos, 'product.product', conteo_lineas.keys(), disponibles_producto, args.page_size)
    productos_por_id = {producto['id']: producto for producto in productos if producto.get('id')}
    if len(productos_por_id) != len(conteo_lineas):
        limitaciones.agregar('Algunos product_id presentes en ventas no pudieron leerse; se excluyeron del detalle.')

    plantilla_ids = sorted({m2o_id(p.get('product_tmpl_id')) for p in productos if m2o_id(p.get('product_tmpl_id'))})
    plantillas = leer_por_ids(modelos, 'product.template', plantilla_ids, disponibles_plantilla, args.page_size)
    plantillas_por_id = {plantilla['id']: plantilla for plantilla in plantillas if plantilla.get('id')}
    categoria_ids = sorted({m2o_id(p.get('categ_id')) for p in productos if m2o_id(p.get('categ_id'))})
    categorias = leer_por_ids(modelos, 'product.category', categoria_ids, disponibles_categoria, args.page_size)
    rutas = {c['id']: _dashboard_ruta_categoria(c.get('complete_name')) for c in categorias if c.get('id')}
    marcas_por_ruta = _dashboard_marcas_por_ruta(rutas.values())

    ptav_ids = sorted({
        valor for producto in productos
        for valor in (producto.get('product_template_attribute_value_ids') or [])
    }) if 'product_template_attribute_value_ids' in disponibles_producto else []
    ptavs = leer_por_ids(modelos, 'product.template.attribute.value', ptav_ids, disponibles_ptav, args.page_size)
    ptav_por_id = {
        atributo['id']: {
            'atributo': texto((atributo.get('attribute_id') or [None, ''])[1]),
            'valor': texto(atributo.get('name')),
        }
        for atributo in ptavs if atributo.get('id')
    }
    linea_ids = sorted({
        valor for plantilla in plantillas
        for valor in (plantilla.get('attribute_line_ids') or [])
    }) if 'attribute_line_ids' in disponibles_plantilla else []
    lineas_atributo = leer_por_ids(
        modelos, 'product.template.attribute.line', linea_ids, disponibles_linea_atributo, args.page_size
    )
    atributos_plantilla = defaultdict(list)
    for linea in lineas_atributo:
        nombre = texto((linea.get('attribute_id') or [None, ''])[1])
        if nombre:
            atributos_plantilla[nombre].append(linea.get('id'))
    atributos_plantilla_por_id = defaultdict(list)
    for plantilla in plantillas:
        for linea_id in plantilla.get('attribute_line_ids') or []:
            linea = next((x for x in lineas_atributo if x.get('id') == linea_id), None)
            relacion = (linea or {}).get('attribute_id') or [None, '']
            nombre = texto(relacion[1] if isinstance(relacion, (list, tuple)) and len(relacion) > 1 else '')
            if nombre:
                atributos_plantilla_por_id[plantilla['id']].append(nombre)

    productos_por_plantilla = Counter(
        m2o_id(producto.get('product_tmpl_id')) for producto in productos if m2o_id(producto.get('product_tmpl_id'))
    )
    detalle: list[dict] = []
    for product_id, lineas_venta in conteo_lineas.items():
        producto = productos_por_id.get(product_id)
        if not producto:
            continue
        plantilla_id = m2o_id(producto.get('product_tmpl_id'))
        plantilla = plantillas_por_id.get(plantilla_id, {})
        atributos = [ptav_por_id[valor] for valor in producto.get('product_template_attribute_value_ids') or [] if valor in ptav_por_id]
        dimensiones = _dashboard_dimensiones_producto(
            rutas.get(m2o_id(producto.get('categ_id')), []), atributos, plantilla.get('name'), marcas_por_ruta
        )
        atributos_comerciales = [a for a in atributos if es_atributo_comercial(a['atributo'])]
        atributos_plantilla_nombres = atributos_plantilla_por_id.get(plantilla_id, [])
        candidata_plantilla = bool(
            dimensiones.get('producto') != 'BICICLETA' and texto(plantilla.get('name'))
            and (productos_por_plantilla.get(plantilla_id, 0) > 1 or any(es_atributo_variante(a) for a in atributos_plantilla_nombres))
        )
        ambigua = len({a['atributo'] for a in atributos_comerciales}) > 1
        insuficiente = not atributos_comerciales and not candidata_plantilla and not dimensiones.get('modelo')
        detalle.append({
            'product_id': product_id,
            'sku': texto(producto.get('default_code')),
            'producto': texto(producto.get('name')),
            'plantilla': texto(plantilla.get('name')),
            'categoria_completa': ' / '.join(rutas.get(m2o_id(producto.get('categ_id')), [])),
            'marca': dimensiones.get('marca') or 'Sin marca',
            'familia': dimensiones.get('producto') or 'Sin producto',
            'lineas_venta': lineas_venta,
            'modelo_actual': dimensiones.get('modelo') or 'Sin modelo',
            'submodelo_actual': dimensiones.get('submodelo') or 'Sin submodelo',
            'sin_modelo': not bool(dimensiones.get('modelo')),
            'sin_submodelo': not bool(dimensiones.get('submodelo')),
            'atributos_variante': ' | '.join(f"{a['atributo']}: {a['valor']}" for a in atributos),
            'atributos_comerciales': ' | '.join(f"{a['atributo']}: {a['valor']}" for a in atributos_comerciales),
            'atributos_plantilla': ' | '.join(sorted(set(atributos_plantilla_nombres))),
            'plantilla_candidata_modelo': candidata_plantilla,
            'informacion_insuficiente': insuficiente,
            'clasificacion_ambigua': ambigua,
            '_atributos_lista': atributos,
        })

    return crear_reportes(args, {'productos': sorted(detalle, key=lambda p: (p['marca'], p['familia'], p['sku']))}, limitaciones), limitaciones


def guardar_reportes(carpeta_padre: Path, archivos: dict[str, str], args: argparse.Namespace) -> Path:
    sello = f"auditoria_modelos_{args.fecha_inicio}_{args.fecha_fin}_{args.vista}"
    destino = carpeta_padre / sello
    temporal = carpeta_padre / f'.{sello}.tmp'
    if destino.exists():
        raise ErrorAuditoria(f'Ya existe un reporte para esa ejecución: {destino}')
    if temporal.exists():
        shutil.rmtree(temporal)
    temporal.mkdir(parents=True, exist_ok=False)
    try:
        for nombre, contenido in archivos.items():
            (temporal / nombre).write_text(contenido, encoding='utf-8-sig', newline='')
        (temporal / 'estado_ejecucion.json').write_text(
            json.dumps({'completado': True, 'fecha_inicio': args.fecha_inicio, 'fecha_fin': args.fecha_fin, 'vista': args.vista}, indent=2),
            encoding='utf-8',
        )
        temporal.replace(destino)
    except Exception:
        shutil.rmtree(temporal, ignore_errors=True)
        raise
    return destino


def main() -> int:
    args = argumentos()
    try:
        archivos, _ = auditar(args)
        destino = guardar_reportes(args.salida, archivos, args)
    except ErrorAuditoria as exc:
        print(f'ERROR: Auditoría no completada. {exc}', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('ERROR: Auditoría cancelada; no se generó un reporte final.', file=sys.stderr)
        return 130
    except Exception as exc:
        print(f'ERROR: Auditoría no completada por un error inesperado: {exc}', file=sys.stderr)
        return 1
    print(f'Auditoría completada. Reportes: {destino}')
    return 0


_UID = None

if __name__ == '__main__':
    raise SystemExit(main())
