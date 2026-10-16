"""Tests de las banderas N/A del pipeline del dashboard de embarques.

El usuario pidió explícitamente: el N/A se muestra por campo, no por toda
la etapa -- si solo el Proyectado está marcado N/A, el Proyectado debe
mostrar "N/A" pero el Real debe seguir mostrando su "-" normal (pendiente)
hasta que de verdad se capture. Cada lado (proyectado/real) es
independiente."""
from __future__ import annotations

import time

from flask import Flask

from routes.importaciones import importaciones_bp
from db_conexion import obtener_conexion
from utils.jwt_utils import generar_token


def _cliente_test():
    app = Flask(__name__)
    app.register_blueprint(importaciones_bp)
    return app.test_client()


def _headers_auth():
    token = generar_token(
        id_usuario=1, rol=1, usuario="test", nombre="Test",
        cliente_id=None, clave_cliente=None, nombre_cliente=None, id_grupo=None, flujo=None,
    )
    return {"Authorization": f"Bearer {token}"}


def _crear_embarque_con_campos_na(campos_na: list, referencia: str) -> int:
    import json as _json
    conn = obtener_conexion()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO importaciones (referencia, via_transporte, campos_na) VALUES (%s, 'MARITIMO', %s)",
        (referencia, _json.dumps(campos_na)),
    )
    conn.commit()
    id_imp = cursor.lastrowid
    conn.close()
    return id_imp


def _pipeline_verif(id_imp_referencia: str):
    client = _cliente_test()
    resp = client.get("/importaciones/dashboard?tab=embarques", headers=_headers_auth())
    assert resp.status_code == 200
    for e in resp.get_json()["embarques"]:
        if e["referencia"] == id_imp_referencia:
            return e["pipeline"]["verif"]
    raise AssertionError(f"{id_imp_referencia} no aparecio en el dashboard")


def test_proyectado_na_solo_marca_na_proy_pero_no_na_real():
    referencia = f"TEST-DASH-NA-{time.time()}"
    id_imp = _crear_embarque_con_campos_na(["alm_liberacion_uva_prog"], referencia)
    try:
        verif = _pipeline_verif(referencia)
        assert verif["na_proy"] is True
        assert verif["na_real"] is False
    finally:
        conn = obtener_conexion()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM importaciones WHERE id = %s", (id_imp,))
        conn.commit()
        conn.close()


def test_real_na_solo_marca_na_real_pero_no_na_proy():
    referencia = f"TEST-DASH-NA-{time.time()}"
    id_imp = _crear_embarque_con_campos_na(["alm_liberacion_uva"], referencia)
    try:
        verif = _pipeline_verif(referencia)
        assert verif["na_proy"] is False
        assert verif["na_real"] is True
    finally:
        conn = obtener_conexion()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM importaciones WHERE id = %s", (id_imp,))
        conn.commit()
        conn.close()


def test_proyectado_na_pero_real_con_fecha_real_no_oculta_el_dato():
    """Si el proyectado se marco N/A pero despues si se capturo una fecha
    real, esa fecha real es informacion valida y no debe esconderse detras
    de un N/A -- na_real debe seguir en False y el valor real presente."""
    referencia = f"TEST-DASH-NA-{time.time()}"
    id_imp = _crear_embarque_con_campos_na(["alm_liberacion_uva_prog"], referencia)
    conn = obtener_conexion()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE importaciones SET alm_liberacion_uva = %s WHERE id = %s",
        ("2026-01-15", id_imp),
    )
    conn.commit()
    conn.close()
    try:
        verif = _pipeline_verif(referencia)
        assert verif["na_proy"] is True
        assert verif["na_real"] is False
        assert verif["real"] == "2026-01-15"
    finally:
        conn = obtener_conexion()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM importaciones WHERE id = %s", (id_imp,))
        conn.commit()
        conn.close()


def test_ninguno_na():
    referencia = f"TEST-DASH-NA-{time.time()}"
    id_imp = _crear_embarque_con_campos_na([], referencia)
    try:
        verif = _pipeline_verif(referencia)
        assert verif["na_proy"] is False
        assert verif["na_real"] is False
    finally:
        conn = obtener_conexion()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM importaciones WHERE id = %s", (id_imp,))
        conn.commit()
        conn.close()
