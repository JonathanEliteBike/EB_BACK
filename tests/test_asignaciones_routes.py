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


def test_error_de_negocio_respeta_el_contrato_uniforme_end_to_end():
    """El errorhandler del blueprint debe producir {"ok": false, "error": {code, message}}
    con el status del error, no un HTML 500 ni otra forma."""
    conn = obtener_conexion()
    if not conn:
        import pytest
        pytest.skip("Sin conexión a BD local para este test")
    conn.close()

    client = _cliente_test()
    token = _token_valido(rol=1)
    resp = client.get(
        "/importaciones/999999/asignaciones",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 404
    assert resp.is_json
    body = resp.get_json()
    assert body["ok"] is False
    assert body["error"]["code"] == "IMPORTACION_NO_EXISTE"
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    assert set(body.keys()) == {"ok", "error"}


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
    import time
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

    # Use timestamp to ensure SKU uniqueness across test runs
    sku_unico = f"TEST-CONCURRENCIA-{int(time.time())}"
    producto = crear_producto(embarque["id"], sku_unico, 2, "2026-2027")

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


def test_importar_productos_sin_archivo_devuelve_400():
    client = _cliente_test()
    token = _token_valido(rol=1)
    resp = client.post(
        "/importaciones/1/asignaciones/productos/importar",
        headers={"Authorization": f"Bearer {token}"},
        data={"periodo": "2026-2027"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "ARCHIVO_INVALIDO"


def test_importar_productos_xlsx_real_end_to_end():
    """Sube un .xlsx armado en memoria contra la BD local y verifica el resumen."""
    import io
    import time
    import openpyxl

    conn = obtener_conexion()
    if not conn:
        import pytest
        pytest.skip("Sin conexión a BD local para este test")
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id FROM importaciones LIMIT 1")
    emb = cur.fetchone()
    conn.close()
    if not emb:
        import pytest
        pytest.skip("No hay embarques en la BD local")

    sufijo = int(time.time())

    def _archivo():
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["SKU", "CANTIDAD", "DESCRIPCION"])
        ws.append([f"IMPORT-{sufijo}-1", 5, "prueba import 1"])
        ws.append([f"IMPORT-{sufijo}-2", 8, None])
        b = io.BytesIO()
        wb.save(b)
        b.seek(0)
        return b

    client = _cliente_test()
    token = _token_valido(rol=1)
    resp = client.post(
        f"/importaciones/{emb['id']}/asignaciones/productos/importar",
        headers={"Authorization": f"Bearer {token}"},
        data={"periodo": "2026-2027", "file": (_archivo(), "carga.xlsx")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["insertados"] == 2
    assert data["actualizados"] == 0
    assert data["errores"] == []

    # Re-subir el mismo archivo: ahora los 2 SKUs existen -> se actualizan
    resp2 = client.post(
        f"/importaciones/{emb['id']}/asignaciones/productos/importar",
        headers={"Authorization": f"Bearer {token}"},
        data={"periodo": "2026-2027", "file": (_archivo(), "carga.xlsx")},
        content_type="multipart/form-data",
    )
    data2 = resp2.get_json()["data"]
    assert data2["insertados"] == 0
    assert data2["actualizados"] == 2


# ── Vistas consolidadas ───────────────────────────────────────────────────────

def test_asignaciones_embarques_sin_token_401():
    client = _cliente_test()
    resp = client.get("/importaciones/asignaciones/embarques")
    assert resp.status_code == 401


def test_asignaciones_embarques_rol_no_permitido_403():
    client = _cliente_test()
    token = _token_valido(rol=2)
    resp = client.get(
        "/importaciones/asignaciones/embarques",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.get_json()["error"]["code"] == "NO_AUTORIZADO"


def test_asignaciones_embarques_end_to_end():
    conn = obtener_conexion()
    if not conn:
        import pytest
        pytest.skip("Sin conexión a BD local para este test")
    conn.close()

    client = _cliente_test()
    headers = {"Authorization": f"Bearer {_token_valido(rol=1)}"}
    resp = client.get("/importaciones/asignaciones/embarques", headers=headers)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    data = body["data"]
    assert set(data.keys()) == {"embarques", "totales"}
    assert isinstance(data["embarques"], list)
    assert {"n_embarques", "embarcadas", "asignadas", "sobrantes", "vendidas", "disponibles"} <= set(data["totales"])
    if data["embarques"]:
        e = data["embarques"][0]
        assert {"id", "referencia", "nombre", "estado", "n_productos", "kpis"} <= set(e)
        assert set(e["kpis"]) == {"embarcadas", "asignadas", "pendientes", "sobrantes", "vendidas", "disponibles"}


def test_asignaciones_productos_global_end_to_end():
    conn = obtener_conexion()
    if not conn:
        import pytest
        pytest.skip("Sin conexión a BD local para este test")
    conn.close()

    client = _cliente_test()
    headers = {"Authorization": f"Bearer {_token_valido(rol=1)}"}
    resp = client.get(
        "/importaciones/asignaciones/productos?limite=5&solo_disponible=1", headers=headers
    )

    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert set(data.keys()) == {"productos", "totales", "total_filas", "limite", "offset"}
    assert data["limite"] == 5
    for p in data["productos"]:
        assert p["cantidad_disponible"] > 0
        assert p["cantidad_sobrante"] == max(p["cantidad_embarcada"] - p["cantidad_asignada"], 0)


def test_recalcular_exige_ventana_end_to_end():
    conn = obtener_conexion()
    if not conn:
        import pytest
        pytest.skip("Sin conexión a BD local para este test")
    conn.close()

    client = _cliente_test()
    headers = {"Authorization": f"Bearer {_token_valido(rol=1)}"}
    resp = client.post("/importaciones/1/asignaciones/recalcular", json={}, headers=headers)
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "VENTANA_REQUERIDA"


def test_reservar_por_mes_end_to_end():
    """Crea producto, reserva por la ruta /reservar con mes_objetivo y verifica
    la fila de reserva + el movimiento RESERVA + el disponible contra la BD real."""
    import time
    conn = obtener_conexion()
    if not conn:
        import pytest
        pytest.skip("Sin conexión a BD local para este test")
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id FROM importaciones LIMIT 1")
    emb = cur.fetchone()
    cur.execute("SELECT clave FROM clientes LIMIT 1")
    cli = cur.fetchone()
    conn.close()
    if not emb or not cli:
        import pytest
        pytest.skip("Se necesita 1 embarque y 1 cliente en la BD local")

    client = _cliente_test()
    headers = {"Authorization": f"Bearer {_token_valido(rol=1)}"}
    sku = f"RESV-{int(time.time())}"

    r1 = client.post(
        f"/importaciones/{emb['id']}/asignaciones/productos",
        json={"sku": sku, "cantidad_embarcada": 10, "periodo": "2026-2027"},
        headers=headers,
    )
    assert r1.status_code == 201
    pid = r1.get_json()["data"]["id"]

    r2 = client.post(
        f"/importaciones/{emb['id']}/asignaciones/productos/{pid}/reservar",
        json={"reservas": [
            {"clave_cliente": cli["clave"], "mes_objetivo": "2026-10", "cantidad": 4, "proyectado": 6},
        ]},
        headers=headers,
    )
    assert r2.status_code == 200, r2.get_json()
    assert r2.get_json()["data"]["disponible_restante"] == 6

    conn = obtener_conexion()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT mes_objetivo, origen, estado, cantidad_asignada, cantidad_proyectada "
        "FROM importacion_asignaciones WHERE importacion_producto_id = %s", (pid,),
    )
    fila = cur.fetchone()
    cur.execute(
        "SELECT tipo_movimiento, cantidad FROM importacion_movimientos "
        "WHERE importacion_producto_id = %s ORDER BY id", (pid,),
    )
    movs = cur.fetchall()
    conn.close()

    assert str(fila["mes_objetivo"]) == "2026-10-01"
    assert fila["origen"] == "INICIAL"
    assert fila["estado"] == "RESERVADA"
    assert fila["cantidad_asignada"] == 4 and fila["cantidad_proyectada"] == 6
    assert ("ENTRADA", 10) in [(m["tipo_movimiento"], m["cantidad"]) for m in movs]
    assert ("RESERVA", -4) in [(m["tipo_movimiento"], m["cantidad"]) for m in movs]


def test_reasignar_exige_ventana_desde_end_to_end():
    conn = obtener_conexion()
    if not conn:
        import pytest
        pytest.skip("Sin conexión a BD local para este test")
    conn.close()

    client = _cliente_test()
    headers = {"Authorization": f"Bearer {_token_valido(rol=1)}"}
    resp = client.post("/importaciones/1/asignaciones/reasignar", json={}, headers=headers)
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "VENTANA_REQUERIDA"


def test_confirmar_reasignacion_por_mes_end_to_end():
    """Crea producto, confirma una reasignación por la ruta /productos/<pid>/reasignar
    y verifica la fila PENDIENTE_CONFIRMACION + movimiento REASIGNACION contra la BD."""
    import time
    conn = obtener_conexion()
    if not conn:
        import pytest
        pytest.skip("Sin conexión a BD local para este test")
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id FROM importaciones LIMIT 1")
    emb = cur.fetchone()
    cur.execute("SELECT clave FROM clientes LIMIT 1")
    cli = cur.fetchone()
    conn.close()
    if not emb or not cli:
        import pytest
        pytest.skip("Se necesita 1 embarque y 1 cliente en la BD local")

    client = _cliente_test()
    headers = {"Authorization": f"Bearer {_token_valido(rol=1)}"}
    sku = f"REASIG-{int(time.time())}"

    r1 = client.post(
        f"/importaciones/{emb['id']}/asignaciones/productos",
        json={"sku": sku, "cantidad_embarcada": 10, "periodo": "2026-2027"},
        headers=headers,
    )
    assert r1.status_code == 201
    pid = r1.get_json()["data"]["id"]

    r2 = client.post(
        f"/importaciones/{emb['id']}/asignaciones/productos/{pid}/reasignar",
        json={"reservas": [
            {"clave_cliente": cli["clave"], "mes_objetivo": "2026-11", "cantidad": 3, "proyectado": 8},
        ]},
        headers=headers,
    )
    assert r2.status_code == 200, r2.get_json()
    assert r2.get_json()["data"]["disponible_restante"] == 7

    conn = obtener_conexion()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT mes_objetivo, origen, estado, cantidad_asignada FROM importacion_asignaciones "
        "WHERE importacion_producto_id = %s", (pid,),
    )
    fila = cur.fetchone()
    cur.execute(
        "SELECT tipo_movimiento, cantidad FROM importacion_movimientos "
        "WHERE importacion_producto_id = %s", (pid,),
    )
    movs = cur.fetchall()
    conn.close()

    assert str(fila["mes_objetivo"]) == "2026-11-01"
    assert fila["origen"] == "REASIGNACION"
    assert fila["estado"] == "PENDIENTE_CONFIRMACION"
    assert fila["cantidad_asignada"] == 3
    assert ("REASIGNACION", -3) in [(m["tipo_movimiento"], m["cantidad"]) for m in movs]


def test_resolver_reserva_rechazada_end_to_end():
    """Flujo real: producto -> confirmar reasignación (PENDIENTE_CONFIRMACION) ->
    resolver RECHAZADA -> la fila queda RECHAZADA y el stock vuelve a disponible."""
    import time
    conn = obtener_conexion()
    if not conn:
        import pytest
        pytest.skip("Sin conexión a BD local para este test")
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id FROM importaciones LIMIT 1")
    emb = cur.fetchone()
    cur.execute("SELECT clave FROM clientes LIMIT 1")
    cli = cur.fetchone()
    conn.close()
    if not emb or not cli:
        import pytest
        pytest.skip("Se necesita 1 embarque y 1 cliente en la BD local")

    client = _cliente_test()
    headers = {"Authorization": f"Bearer {_token_valido(rol=1)}"}
    sku = f"RESOLV-{int(time.time())}"

    pid = client.post(
        f"/importaciones/{emb['id']}/asignaciones/productos",
        json={"sku": sku, "cantidad_embarcada": 10, "periodo": "2026-2027"},
        headers=headers,
    ).get_json()["data"]["id"]

    client.post(
        f"/importaciones/{emb['id']}/asignaciones/productos/{pid}/reasignar",
        json={"reservas": [{"clave_cliente": cli["clave"], "mes_objetivo": "2026-11", "cantidad": 4}]},
        headers=headers,
    )

    conn = obtener_conexion()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id FROM importacion_asignaciones WHERE importacion_producto_id = %s", (pid,))
    rid = cur.fetchone()["id"]
    conn.close()

    # rechazo
    r = client.post(
        f"/importaciones/{emb['id']}/asignaciones/reservas/{rid}/resolver",
        json={"decision": "RECHAZADA"}, headers=headers,
    )
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["estado"] == "RECHAZADA"

    conn = obtener_conexion()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT tipo_movimiento, cantidad FROM importacion_movimientos "
        "WHERE importacion_producto_id = %s", (pid,),
    )
    movs = [(m["tipo_movimiento"], m["cantidad"]) for m in cur.fetchall()]
    conn.close()
    assert ("REASIGNACION", -4) in movs        # se conserva
    assert ("RECHAZO_RESERVA", 4) in movs      # se agrega, devuelve el stock
    assert sum(c for _, c in movs) == 10       # disponible neto = embarcado

    # segundo resolver sobre la misma reserva ya cerrada -> 409
    r2 = client.post(
        f"/importaciones/{emb['id']}/asignaciones/reservas/{rid}/resolver",
        json={"decision": "ACEPTADA"}, headers=headers,
    )
    assert r2.status_code == 409
    assert r2.get_json()["error"]["code"] == "RESERVA_NO_PENDIENTE"


def test_detalle_y_kpis_de_reserva_end_to_end():
    """GET .../detalle responde 200 (ya no llama a recalcular con firma vieja) y
    la reserva trae Proyectado/Reservado/Faltante; el resumen del embarque trae
    el desglose reservado_inicial/pendiente/confirmado."""
    import time
    conn = obtener_conexion()
    if not conn:
        import pytest
        pytest.skip("Sin conexión a BD local para este test")
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id FROM importaciones LIMIT 1")
    emb = cur.fetchone()
    cur.execute("SELECT clave FROM clientes LIMIT 1")
    cli = cur.fetchone()
    conn.close()
    if not emb or not cli:
        import pytest
        pytest.skip("Se necesita 1 embarque y 1 cliente en la BD local")

    client = _cliente_test()
    headers = {"Authorization": f"Bearer {_token_valido(rol=1)}"}
    sku = f"KPI-{int(time.time())}"

    pid = client.post(
        f"/importaciones/{emb['id']}/asignaciones/productos",
        json={"sku": sku, "cantidad_embarcada": 10, "periodo": "2026-2027"},
        headers=headers,
    ).get_json()["data"]["id"]
    client.post(
        f"/importaciones/{emb['id']}/asignaciones/productos/{pid}/reservar",
        json={"reservas": [{"clave_cliente": cli["clave"], "mes_objetivo": "2026-10",
                            "cantidad": 4, "proyectado": 7}]},
        headers=headers,
    )

    d = client.get(
        f"/importaciones/{emb['id']}/asignaciones/productos/{pid}/detalle", headers=headers
    )
    assert d.status_code == 200, d.get_json()
    data = d.get_json()["data"]
    assert data["proyecciones"] == []
    r = next(x for x in data["reservas"] if x["mes_objetivo"] == "2026-10")
    assert (r["proyectado"], r["reservado"], r["faltante"], r["origen"], r["estado"]) == \
        (7, 4, 3, "INICIAL", "RESERVADA")

    resumen = client.get(f"/importaciones/{emb['id']}/asignaciones", headers=headers).get_json()["data"]
    k = resumen["kpis"]
    assert {"reservado_inicial", "reservado_reasignacion_pendiente", "reservado_confirmado",
            "unidades_reservadas"} <= set(k)
    assert k["reservado_inicial"] >= 4


def test_ciclo_completo_reservas_dos_embarques_mismo_sku_end_to_end(monkeypatch):
    """Reproduce los ejemplos §9 del spec contra la BD real:
    recalcular (ventana) -> reservar -> (2o embarque) recalcular -> reservar ->
    reasignar -> confirmar_reasignacion -> resolver (ACEPTADA y RECHAZADA)."""
    import time
    from services.proyecciones_service import _norm_sku

    conn = obtener_conexion()
    if not conn:
        import pytest
        pytest.skip("Sin conexion a BD local para este test")
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id FROM importaciones WHERE estado <> 'eliminado' ORDER BY id LIMIT 2")
    embs = cur.fetchall()
    if len(embs) < 2:
        conn.close()
        import pytest
        pytest.skip("Se necesitan 2 embarques en la BD local")
    imp1, imp2 = embs[0]["id"], embs[1]["id"]

    sku = f"CICLO-{int(time.time())}"
    sn = _norm_sku(sku)
    periodo = "2026-2027"
    A, B, C, E = "LC657", "MC677", "MC679", "GC411"   # prioridades 1,2,3,4

    def _fp(clave, oct_, nov_, dic_):
        cur.execute(
            "INSERT INTO forecast_proyecciones (clave_cliente, periodo, sku, octubre, noviembre, diciembre) "
            "VALUES (%s,%s,%s,%s,%s,%s)", (clave, periodo, sku, oct_, nov_, dic_),
        )
    _fp(A, 5, 4, 3); _fp(B, 6, 0, 2); _fp(C, 0, 10, 0); _fp(E, 2, 2, 6)
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        "services.proyecciones_service._get_ordenes_my27",
        lambda _p: {A: {sn: 4}, E: {sn: 8}},
    )

    client = _cliente_test()
    headers = {"Authorization": f"Bearer {_token_valido(rol=1)}"}
    pid1 = pid2 = None
    try:
        # 9.1  IMP-1 embarcada=20, ventana oct-dic
        pid1 = client.post(
            f"/importaciones/{imp1}/asignaciones/productos",
            json={"sku": sku, "cantidad_embarcada": 20, "periodo": periodo}, headers=headers,
        ).get_json()["data"]["id"]

        prop = client.post(
            f"/importaciones/{imp1}/asignaciones/recalcular",
            json={"mes_desde": "octubre", "mes_hasta": "diciembre"}, headers=headers,
        ).get_json()["data"]
        fila = next(p for p in prop if p["producto_id"] == pid1)
        suger = {c["clave_cliente"]: {m["mes"]: m["sugerido"] for m in c["meses"]}
                 for c in fila["propuesta"]}
        assert suger[A] == {"2026-10": 1, "2026-11": 4, "2026-12": 3}   # Odoo tapa oct 5->1
        assert suger[B] == {"2026-10": 6, "2026-12": 2}
        assert suger[C] == {"2026-11": 4}                               # 10 proyectado, 4 caben
        assert fila["sobrante_estimado"] == 0
        c_row = next(c for c in fila["propuesta"] if c["clave_cliente"] == C)
        assert c_row["faltante_total"] == 6

        reservas = [
            {"clave_cliente": c["clave_cliente"], "mes_objetivo": m["mes"],
             "cantidad": m["sugerido"], "proyectado": m["proyectado"]}
            for c in fila["propuesta"] for m in c["meses"] if m["sugerido"] > 0
        ]
        r = client.post(
            f"/importaciones/{imp1}/asignaciones/productos/{pid1}/reservar",
            json={"reservas": reservas}, headers=headers,
        )
        assert r.status_code == 200 and r.get_json()["data"]["disponible_restante"] == 0

        k1 = client.get(f"/importaciones/{imp1}/asignaciones", headers=headers).get_json()["data"]["kpis"]
        assert k1["unidades_reservadas"] >= 20 and k1["reservado_inicial"] >= 20

        # 9.3  IMP-2 embarcada=15, ventana dic-abr
        pid2 = client.post(
            f"/importaciones/{imp2}/asignaciones/productos",
            json={"sku": sku, "cantidad_embarcada": 15, "periodo": periodo}, headers=headers,
        ).get_json()["data"]["id"]

        prop2 = client.post(
            f"/importaciones/{imp2}/asignaciones/recalcular",
            json={"mes_desde": "diciembre", "mes_hasta": "abril"}, headers=headers,
        ).get_json()["data"]
        fila2 = next(p for p in prop2 if p["producto_id"] == pid2)
        suger2 = {c["clave_cliente"]: {m["mes"]: m["sugerido"] for m in c["meses"]}
                  for c in fila2["propuesta"]}
        falt2 = {c["clave_cliente"]: c["faltante_total"] for c in fila2["propuesta"]}
        assert suger2.get(E) == {"2026-12": 2}
        # A y B ya estaban cubiertos por IMP-1: aparecen con sugerido 0 y faltante 0
        assert suger2.get(A) == {"2026-12": 0} and falt2.get(A) == 0
        assert suger2.get(B) == {"2026-12": 0} and falt2.get(B) == 0
        assert fila2["sobrante_estimado"] == 13

        client.post(
            f"/importaciones/{imp2}/asignaciones/productos/{pid2}/reservar",
            json={"reservas": [{"clave_cliente": E, "mes_objetivo": "2026-12", "cantidad": 2, "proyectado": 2}]},
            headers=headers,
        )

        # reasignar sobrante de IMP-2 a meses anteriores a diciembre
        reas = client.post(
            f"/importaciones/{imp2}/asignaciones/reasignar",
            json={"ventana_desde": "diciembre"}, headers=headers,
        ).get_json()["data"]
        fila_r = next(p for p in reas if p["producto_id"] == pid2)
        assert fila_r["origen"] == "REASIGNACION"
        # solo lo efectivamente sugerido: MC679 en noviembre = faltante real 10 - 4(IMP-1)
        suger_r = {
            c["clave_cliente"]: {m["mes"]: m["sugerido"] for m in c["meses"] if m["sugerido"] > 0}
            for c in fila_r["propuesta"] if c["sugerido_total"] > 0
        }
        assert suger_r == {C: {"2026-11": 6}}
        assert fila_r["sobrante_estimado"] == 7

        cr = client.post(
            f"/importaciones/{imp2}/asignaciones/productos/{pid2}/reasignar",
            json={"reservas": [{"clave_cliente": C, "mes_objetivo": "2026-11", "cantidad": 6, "proyectado": 6}]},
            headers=headers,
        )
        assert cr.status_code == 200 and cr.get_json()["data"]["disponible_restante"] == 7

        conn = obtener_conexion(); cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT id FROM importacion_asignaciones WHERE importacion_producto_id = %s AND origen = 'REASIGNACION'",
            (pid2,),
        )
        rid = cur.fetchone()["id"]
        conn.close()

        acc = client.post(
            f"/importaciones/{imp2}/asignaciones/reservas/{rid}/resolver",
            json={"decision": "ACEPTADA"}, headers=headers,
        )
        assert acc.status_code == 200 and acc.get_json()["data"]["estado"] == "CONFIRMADA"

        conn = obtener_conexion(); cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT tipo_movimiento, cantidad FROM importacion_movimientos WHERE importacion_producto_id = %s",
            (pid2,),
        )
        movs = [(m["tipo_movimiento"], m["cantidad"]) for m in cur.fetchall()]
        conn.close()
        assert movs.count(("REASIGNACION", -6)) == 1
        assert ("RECHAZO_RESERVA", 6) not in movs           # ACEPTADA no genera movimiento
        assert sum(c for _, c in movs) == 15 - 2 - 6         # disponible IMP-2 = 7

        k2 = client.get(f"/importaciones/{imp2}/asignaciones", headers=headers).get_json()["data"]["kpis"]
        assert k2["reservado_confirmado"] >= 6

        # RECHAZO en frio sobre una reasignacion nueva
        client.post(
            f"/importaciones/{imp2}/asignaciones/productos/{pid2}/reasignar",
            json={"reservas": [{"clave_cliente": C, "mes_objetivo": "2026-10", "cantidad": 1}]},
            headers=headers,
        )
        conn = obtener_conexion(); cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT id FROM importacion_asignaciones WHERE importacion_producto_id = %s "
            "AND origen = 'REASIGNACION' AND estado = 'PENDIENTE_CONFIRMACION'", (pid2,),
        )
        rid2 = cur.fetchone()["id"]
        conn.close()
        rej = client.post(
            f"/importaciones/{imp2}/asignaciones/reservas/{rid2}/resolver",
            json={"decision": "RECHAZADA"}, headers=headers,
        )
        assert rej.status_code == 200 and rej.get_json()["data"]["estado"] == "RECHAZADA"

        conn = obtener_conexion(); cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT tipo_movimiento, cantidad FROM importacion_movimientos WHERE importacion_producto_id = %s",
            (pid2,),
        )
        movs2 = [(m["tipo_movimiento"], m["cantidad"]) for m in cur.fetchall()]
        conn.close()
        assert ("REASIGNACION", -1) in movs2 and ("RECHAZO_RESERVA", 1) in movs2
        assert sum(c for _, c in movs2) == 7          # el rechazo devolvio la unidad
    finally:
        conn = obtener_conexion(); cur = conn.cursor()
        for pid in (pid1, pid2):
            if pid:
                cur.execute("DELETE FROM importacion_movimientos WHERE importacion_producto_id = %s", (pid,))
                cur.execute("DELETE FROM importacion_asignaciones WHERE importacion_producto_id = %s", (pid,))
                cur.execute("DELETE FROM importacion_productos WHERE id = %s", (pid,))
        cur.execute("DELETE FROM forecast_proyecciones WHERE sku = %s", (sku,))
        conn.commit(); conn.close()
