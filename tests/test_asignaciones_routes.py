# tests/test_asignaciones_routes.py
from flask import Flask

from db_conexion import obtener_conexion
from utils.jwt_utils import generar_token
from routes.asignaciones_importaciones import asignaciones_bp


def _cliente_test():
    app = Flask(__name__)
    app.register_blueprint(asignaciones_bp)
    return app.test_client()


def _token_valido(rol=1):
    return generar_token(
        id_usuario=1, rol=rol, usuario="test", nombre="Test",
        cliente_id=None, clave_cliente=None, nombre_cliente=None, id_grupo=None, flujo=None,
    )


def test_inicializar_tablas_sin_token_devuelve_401():
    client = _cliente_test()
    resp = client.post("/importaciones/asignaciones/inicializar-tablas")
    assert resp.status_code == 401


def test_inicializar_tablas_con_rol_no_permitido_devuelve_403():
    client = _cliente_test()
    token = _token_valido(rol=2)
    resp = client.post(
        "/importaciones/asignaciones/inicializar-tablas",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.get_json()["error"]["code"] == "NO_AUTORIZADO"


def test_inicializar_tablas_crea_las_4_tablas_en_bd_real():
    conn = obtener_conexion()
    if not conn:
        import pytest
        pytest.skip("Sin conexión a BD local para este test")
    conn.close()

    client = _cliente_test()
    token = _token_valido(rol=1)
    resp = client.post(
        "/importaciones/asignaciones/inicializar-tablas",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201

    conn = obtener_conexion()
    cursor = conn.cursor()
    cursor.execute("SHOW TABLES LIKE 'importacion_%'")
    tablas = {row[0] for row in cursor.fetchall()}
    conn.close()
    assert {
        "importacion_productos", "importacion_asignaciones",
        "importacion_sobrantes_ventas", "importacion_movimientos",
    }.issubset(tablas)


def test_crear_y_listar_producto_end_to_end():
    import time
    conn = obtener_conexion()
    if not conn:
        import pytest
        pytest.skip("Sin conexión a BD local para este test")
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id FROM importaciones LIMIT 1")
    embarque = cursor.fetchone()
    conn.close()
    if not embarque:
        import pytest
        pytest.skip("No hay embarques en la BD local para probar")

    client = _cliente_test()
    token = _token_valido(rol=1)
    headers = {"Authorization": f"Bearer {token}"}
    # Use timestamp to ensure SKU uniqueness across test runs
    sku_unico = f"TEST-{embarque['id']}-{int(time.time())}"

    resp = client.post(
        f"/importaciones/{embarque['id']}/asignaciones/productos",
        json={"sku": sku_unico, "cantidad_embarcada": 5, "periodo": "2026-2027"},
        headers=headers,
    )
    assert resp.status_code == 201
    producto = resp.get_json()["data"]
    assert producto["sku_norm"] == sku_unico.replace("-", "")

    resp2 = client.get(f"/importaciones/{embarque['id']}/asignaciones/productos", headers=headers)
    assert resp2.status_code == 200
    productos = resp2.get_json()["data"]
    encontrado = next(p for p in productos if p["id"] == producto["id"])
    assert encontrado["cantidad_disponible"] == 5
    assert encontrado["cantidad_asignada"] == 0


def test_asignar_concurrente_nunca_deja_disponible_negativo():
    conn = obtener_conexion()
    if not conn:
        import pytest
        pytest.skip("Sin conexión a BD local para este test")

    from services.asignaciones_service import crear_producto, asignar, AsignacionesError, _disponible_producto

    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id FROM importaciones LIMIT 1")
    embarque = cursor.fetchone()
    cursor.execute("SELECT clave FROM clientes LIMIT 2")
    clientes = cursor.fetchall()
    conn.close()
    if not embarque or len(clientes) < 2:
        import pytest
        pytest.skip("Se necesita al menos 1 embarque y 2 clientes en la BD local para este test")

    producto = crear_producto(embarque["id"], "TEST-CONCURRENCIA-0001", 2, "2026-2027")

    import threading
    resultados = []
    errores = []

    def intentar(clave, cantidad):
        try:
            resultados.append(asignar(producto["id"], [{"clave_cliente": clave, "cantidad": cantidad}]))
        except AsignacionesError as e:
            errores.append(e)

    t1 = threading.Thread(target=intentar, args=(clientes[0]["clave"], 2))
    t2 = threading.Thread(target=intentar, args=(clientes[1]["clave"], 1))
    t1.start(); t2.start()
    t1.join(); t2.join()

    conn = obtener_conexion()
    cursor = conn.cursor(dictionary=True)
    disponible_final = _disponible_producto(cursor, producto["id"])
    conn.close()

    assert disponible_final >= 0  # la garantía central: nunca queda negativo
    assert len(resultados) + len(errores) == 2
    if len(resultados) == 2:
        # ambos cupieron exactamente (2+1 > 2 disponible, así que esto NO debería pasar,
        # pero si el bloqueo fallara, este assert lo detectaría)
        assert False, "Ambas asignaciones se completaron pese a que la demanda (3) excede el disponible (2)"
    assert len(errores) >= 1  # al menos una de las dos debe haber sido rechazada por STOCK_INSUFICIENTE
    assert all(e.code == "STOCK_INSUFICIENTE" for e in errores)
