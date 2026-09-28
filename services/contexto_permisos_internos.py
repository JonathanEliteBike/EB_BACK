"""Contexto HTTP y redacción monetaria exclusivos del rol interno."""

import re

from flask import current_app, g, request


def coincide_ruta(modulo, ruta):
    patron = modulo.get('modulo_ruta')
    if not patron:
        return False
    patron_normalizado = patron.split('?')[0].split('#')[0].strip('/')
    ruta_normalizada = ruta.split('?')[0].split('#')[0].strip('/')
    partes = patron_normalizado.split('/')
    expresion = '/'.join('[^/]+' if p.startswith(':') else re.escape(p) for p in partes)
    if re.fullmatch(expresion, ruta_normalizada) is not None:
        return True
    # Los módulos hijo conservan la acción del padre cuando éste es el módulo
    # técnico de la regla. No aplica a patrones parametrizados.
    return not any(parte.startswith(':') for parte in partes) and ruta_normalizada.startswith(f'{patron_normalizado}/')


def puede_ver_montos(usuario_id):
    from services.permisos_internos_service import PermisosInternosService
    modulos = getattr(g, '_modulos_internos_autorizados', ())
    # Sin contexto o con varios módulos, nunca tomar el permiso más amplio.
    return bool(modulos) and all(
        PermisosInternosService.validar_permiso_usuario(usuario_id, modulo, 'ver_montos')
        for modulo in modulos
    )


def registrar_capa_interna(app):
    """Cubre también vistas históricas sin decorador, sólo con JWT de rol 4."""
    @app.before_request
    def autorizar_interno():
        from utils.jwt_utils import verificar_token
        from utils.auth_decorators import _resultado_permiso_interno_endpoint, _error_permiso_interno_endpoint
        if request.method == 'OPTIONS' or not request.endpoint:
            return None
        authorization = request.headers.get('Authorization', '')
        if not authorization.startswith('Bearer '):
            return None
        payload = verificar_token(authorization[7:].strip())
        if not payload or str(payload.get('rol')) != '4':
            return None
        # Conserva las operaciones de sesión y el catálogo necesario para los guards.
        vista = current_app.view_functions[request.endpoint]
        if (request.blueprint == 'auth'
                or getattr(vista, '_permite_acceso_interno_sin_regla', False)
                or (request.path == '/api/modulos' and request.method == 'GET')):
            return None
        g.usuario_actual = payload
        resultado = _resultado_permiso_interno_endpoint()
        if resultado is not True:
            return _error_permiso_interno_endpoint(resultado)

    @app.after_request
    def proteger_montos_internos(response):
        payload = getattr(g, 'usuario_actual', {})
        if str(payload.get('rol')) != '4' or response.status_code >= 400:
            return response
        if not getattr(g, '_modulos_internos_autorizados', None):
            return response
        if not response.is_json:
            return response
        if not puede_ver_montos(payload['id']):
            response.set_data(current_app.json.dumps(redactar_montos(response.get_json(), request.blueprint)))
        response.headers['Cache-Control'] = 'private, no-store'
        return response


# Campos monetarios conocidos. "total" sólo es importe en Ventas; en Garantías
# es un conteo. Los precios públicos de Forecast siempre se conservan.
CAMPOS_MONETARIOS = {
    'precio', 'precio_unitario', 'precio_distribuidor', 'precio_partner',
    'precio_partner_elite', 'precio_partner_elite_plus', 'nivel_precio',
    'subtotal', 'importe', 'importe_final', 'monto', 'costo', 'coste',
    'compra_minima_anual', 'compra_minima_apparel', 'compras_totales_crudo',
    'compra_global_scott', 'compra_global_apparel', 'compra_global_bold',
    'compra_anual_crudo', 'compra_adicional', 'total_acumulado',
    'notas_credito', 'productos_ofertados', 'bicicleta_demo', 'bicicletas_bold',
    'acumulado_global_calculado', 'total_bicis_deduccion', 'retroactivo_total',
    'venta_total',
}

# Los tableros de Flujo usan nombres de columnas contables que no contienen
# "monto", "importe" ni "costo". Son sensibles sólo en ese blueprint.
CAMPOS_MONETARIOS_FLUJO = {
    'col_proyectado', 'col_real', 'col_diferencia',
    'proyectado', 'real', 'diferencia',
    'monto_proyectado', 'monto_real',
}

# Compromisos anuales del catálogo de Metas. Se declaran de forma explícita
# para no tratar otros campos de nivel o clasificación como monetarios.
CAMPOS_MONETARIOS_METAS = {
    'compromiso_scott', 'compromiso_syncros',
    'compromiso_apparel', 'compromiso_vittoria',
}

# Previo conserva porcentajes y datos descriptivos, pero sus acumulados,
# compromisos y avances son valores monetarios aunque sus nombres no usen
# "monto", "importe" ni "costo".
CAMPOS_MONETARIOS_PREVIO = {
    'acumulado_anticipado', 'compra_minima_anual', 'compra_minima_inicial',
    'avance_global', 'compromiso_scott', 'avance_global_scott',
    'compromiso_apparel_syncros_vittoria',
    'avance_global_apparel_syncros_vittoria',
    'acumulado_syncros', 'acumulado_apparel', 'acumulado_vittoria',
    'acumulado_bold',
}

# La respuesta MY27 de Carátulas usa nombres de negocio que no incluyen las
# palabras monto, importe o costo. Se limitan a este blueprint para no
# redactor campos homónimos de otras pantallas.
CAMPOS_MONETARIOS_CARATULAS = {
    'meta', 'categoria', 'distribuidor', 'bicicletas', 'apparel',
    'general', 'scott', 'megamo', 'bold', 'syncros', 'vittoria', 'otros',
    'compbici', 'compapp', 'avbici', 'avapp', 'faltbici', 'faltapp',
}


def redactar_montos(datos, blueprint):
    if isinstance(datos, list):
        return [redactar_montos(item, blueprint) for item in datos]
    if not isinstance(datos, dict):
        return datos
    resultado = {}
    for campo, valor in datos.items():
        nombre = campo.lower()
        sensible = nombre in CAMPOS_MONETARIOS
        sensible |= any(p in nombre for p in ('monto', 'importe', 'costo', 'coste'))
        if blueprint == 'dashboard_flujo_bp':
            sensible |= nombre in CAMPOS_MONETARIOS_FLUJO
        if blueprint == 'metas':
            sensible |= nombre in CAMPOS_MONETARIOS_METAS
        if blueprint == 'previo':
            sensible |= (nombre in CAMPOS_MONETARIOS_PREVIO
                         or nombre.startswith(('compromiso_', 'avance_')))
        if blueprint == 'caratulas':
            sensible |= (
                nombre in CAMPOS_MONETARIOS_CARATULAS and
                isinstance(valor, (int, float))
            )
            sensible |= nombre.startswith(('compra_', 'compras_', 'acumulado_', 'compromiso_', 'avance_'))
        if blueprint == 'ventas':
            sensible |= nombre in {'total', 'total1', 'total2', 'global_total', 'delta'}
        if blueprint == 'retroactivos':
            sensible |= nombre.startswith(('compra_', 'compras_', 'acumulado_')) or nombre == 'garantias'
        # Mantener la estructura de listas/objetos y los conteos.
        resultado[campo] = None if sensible and not isinstance(valor, (dict, list)) else redactar_montos(valor, blueprint)
    return resultado
