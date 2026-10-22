"""
El modulo de garantias debe quedar totalmente bloqueado para los roles
2 (Usuario/distribuidor) y 3 (Usuario hijo) -- ni siquiera pueden enviar
tickets. Antes de este fix, `usuario_actual_tiene_modulo()` le daba al
rol 2 un `return True` incondicional para cualquier modulo marcado con
`@requiere_modulo` (ver utils/auth_decorators.py), asi que su acceso a
garantias era imposible de revocar por datos. El fix cambia los 8
endpoints cliente-facing de garantias.py de `@requiere_modulo(...)` a
`@requiere_rol(1, 4)`, igual que el resto del archivo.
"""
from app import app
from utils.jwt_utils import generar_token

# requiere_autenticacion valida contra la BD que el usuario exista y este
# activo, asi que estos IDs deben ser cuentas reales y activas (rol 2 y 3)
# de la base local -- no se puede usar un ID inventado.
_TOKEN_ROL2 = generar_token(364, 2, 'EA219', 'Victor Alejandro Garnier Morga', 26, None, None, None, None)
_TOKEN_ROL3 = generar_token(474, 3, 'mestrada', 'Milagros Estrada', None, None, None, None, None)

_ENDPOINTS = [
    ("POST", "/garantias/formulario/enviar"),
    ("PUT", "/garantias/formulario/1/actualizar-dato"),
    ("GET", "/garantias/mis-tickets"),
    ("GET", "/garantias/formulario/1"),
    ("GET", "/garantias/estructura"),
    ("GET", "/garantias/ticket/1/comentarios"),
    ("POST", "/garantias/ticket/1/comentarios"),
    ("GET", "/garantias/archivo/subir"),
    ("GET", "/garantias/archivo/evidencia.jpg"),
]


def _llamar(method, path, token):
    cliente = app.test_client()
    headers = {'Authorization': f'Bearer {token}'}
    if method == "GET":
        return cliente.get(path, headers=headers)
    if method == "PUT":
        return cliente.put(path, headers=headers, json={})
    return cliente.post(path, headers=headers, json={})


def test_rol2_distribuidor_bloqueado_en_todos_los_endpoints_de_garantias():
    for method, path in _ENDPOINTS:
        if path == "/garantias/archivo/subir":
            continue  # tiene su propia ruta POST; se prueba aparte
        resp = _llamar(method, path, _TOKEN_ROL2)
        assert resp.status_code == 403, (
            f"{method} {path} devolvio {resp.status_code}, se esperaba 403 para rol 2"
        )


def test_rol3_usuario_hijo_bloqueado_en_todos_los_endpoints_de_garantias():
    for method, path in _ENDPOINTS:
        if path == "/garantias/archivo/subir":
            continue
        resp = _llamar(method, path, _TOKEN_ROL3)
        assert resp.status_code == 403, (
            f"{method} {path} devolvio {resp.status_code}, se esperaba 403 para rol 3"
        )


def test_rol2_distribuidor_bloqueado_al_subir_archivo_de_evidencia():
    cliente = app.test_client()
    headers = {'Authorization': f'Bearer {_TOKEN_ROL2}'}
    resp = cliente.post(
        "/garantias/archivo/subir",
        headers=headers,
        data={'accion': 'crear'},
        content_type='multipart/form-data',
    )
    assert resp.status_code == 403


def test_rol3_usuario_hijo_bloqueado_al_subir_archivo_de_evidencia():
    cliente = app.test_client()
    headers = {'Authorization': f'Bearer {_TOKEN_ROL3}'}
    resp = cliente.post(
        "/garantias/archivo/subir",
        headers=headers,
        data={'accion': 'crear'},
        content_type='multipart/form-data',
    )
    assert resp.status_code == 403
