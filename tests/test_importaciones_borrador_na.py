"""Tests del guardado de borrador (sección en progreso) de un embarque,
en particular el caso de quitar un N/A ya guardado en un borrador previo --
bug real reportado por el usuario viendo el formulario en vivo: al quitar
el N/A de un campo y volver a guardar el borrador, el campo volvía a
aparecer marcado N/A."""
from __future__ import annotations

import json
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


def _crear_embarque_de_prueba():
    conn = obtener_conexion()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO importaciones (referencia, via_transporte) VALUES (%s, 'MARITIMO')",
        (f"TEST-BORRADOR-{time.time()}",),
    )
    conn.commit()
    id_imp = cursor.lastrowid
    conn.close()
    return id_imp


def _borradores_de(id_imp) -> dict:
    conn = obtener_conexion()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT borradores FROM importaciones WHERE id = %s", (id_imp,))
    raw = cursor.fetchone()["borradores"]
    conn.close()
    return json.loads(raw) if raw else {}


def _campos_na_de(id_imp) -> set:
    conn = obtener_conexion()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT campos_na FROM importaciones WHERE id = %s", (id_imp,))
    raw = cursor.fetchone()["campos_na"]
    conn.close()
    return set(json.loads(raw)) if raw else set()


def test_quitar_na_en_borrador_se_queda_quitado_al_releer():
    """Dos campos N/A en el mismo borrador de sección; se quita el N/A de
    uno solo y se vuelve a guardar el borrador -- el campo quitado NO debe
    seguir marcado N/A, y el otro campo N/A no debe verse afectado."""
    id_imp = _crear_embarque_de_prueba()
    try:
        client = _cliente_test()

        resp1 = client.put(
            f"/importaciones/{id_imp}",
            json={
                "_borrador_seccion": "almacen",
                "alm_liberacion_uva_prog": "__NA__",
                "alm_liberacion_uva": "__NA__",
            },
            headers=_headers_auth(),
        )
        assert resp1.status_code == 200
        borradores = _borradores_de(id_imp)
        assert borradores["almacen"]["alm_liberacion_uva_prog"] == "__NA__"
        assert borradores["almacen"]["alm_liberacion_uva"] == "__NA__"

        resp2 = client.put(
            f"/importaciones/{id_imp}",
            json={
                "_borrador_seccion": "almacen",
                "alm_liberacion_uva_prog": None,
            },
            headers=_headers_auth(),
        )
        assert resp2.status_code == 200
        borradores = _borradores_de(id_imp)
        assert "alm_liberacion_uva_prog" not in borradores.get("almacen", {})
        assert borradores["almacen"]["alm_liberacion_uva"] == "__NA__"
    finally:
        conn = obtener_conexion()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM importaciones WHERE id = %s", (id_imp,))
        conn.commit()
        conn.close()


def test_na_en_borrador_se_refleja_de_inmediato_en_columna_oficial():
    """La columna campos_na (la que usan el dashboard y camposNA al recargar
    el detalle) es la fuente de verdad del estado N/A -- debe actualizarse
    aunque el guardado sea de borrador, no solo en un guardado oficial.
    Antes del fix, un N/A marcado o quitado en un guardado de borrador solo
    se veía en borradores.JSON, y el dashboard/la recarga del formulario
    seguían leyendo el estado viejo de campos_na indefinidamente."""
    id_imp = _crear_embarque_de_prueba()
    try:
        client = _cliente_test()

        resp1 = client.put(
            f"/importaciones/{id_imp}",
            json={
                "_borrador_seccion": "almacen",
                "alm_liberacion_uva_prog": "__NA__",
                "alm_liberacion_uva": "__NA__",
            },
            headers=_headers_auth(),
        )
        assert resp1.status_code == 200
        assert _campos_na_de(id_imp) == {"alm_liberacion_uva_prog", "alm_liberacion_uva"}

        resp2 = client.put(
            f"/importaciones/{id_imp}",
            json={
                "_borrador_seccion": "almacen",
                "alm_liberacion_uva_prog": None,
            },
            headers=_headers_auth(),
        )
        assert resp2.status_code == 200
        assert _campos_na_de(id_imp) == {"alm_liberacion_uva"}
    finally:
        conn = obtener_conexion()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM importaciones WHERE id = %s", (id_imp,))
        conn.commit()
        conn.close()
