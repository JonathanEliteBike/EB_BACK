"""Tests de la auditoria de llenado del embarque: tablas nuevas, historial
de captura por campo, y el motor de calculo _calcular_auditoria()."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from flask import Flask

from routes.importaciones import importaciones_bp


def _cliente_test():
    app = Flask(__name__)
    app.register_blueprint(importaciones_bp)
    return app.test_client()


def test_inicializar_tablas_crea_historial_y_hitos(mocker):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    mocker.patch("routes.importaciones.obtener_conexion", return_value=conn)

    resp = _cliente_test().post("/importaciones/inicializar-tablas")

    assert resp.status_code == 201
    sql_ejecutados = " ".join(
        c.args[0] for c in cursor.execute.call_args_list if c.args and isinstance(c.args[0], str)
    )
    assert "CREATE TABLE IF NOT EXISTS importaciones_historial_campos" in sql_ejecutados
    assert "CREATE TABLE IF NOT EXISTS importaciones_hitos_auditoria" in sql_ejecutados


from datetime import date
from routes.importaciones import _registrar_primera_captura


def _cursor_mock():
    cursor = MagicMock()
    return cursor


def test_registrar_primera_captura_inserta_solo_campos_que_pasan_de_vacio_a_lleno():
    cursor = _cursor_mock()
    existing = {"log_contenedor": None, "log_buque": "Ya tenia valor"}
    merged   = {"log_contenedor": "MSKU1234567", "log_buque": "Buque Nuevo"}

    _registrar_primera_captura(cursor, importacion_id=42,
                                existing=existing, merged=merged,
                                campos_a_actualizar=["log_contenedor", "log_buque"])

    inserts = [c for c in cursor.execute.call_args_list
               if "INSERT INTO importaciones_historial_campos" in c.args[0]]
    assert len(inserts) == 1  # log_buque YA tenia valor -> no se registra, es una correccion
    assert inserts[0].args[1][1] == "log_contenedor"
    assert inserts[0].args[1][3] == "MSKU1234567"


def test_registrar_primera_captura_ignora_marcas_na():
    cursor = _cursor_mock()
    existing = {"log_contenedor": None}
    merged   = {"log_contenedor": None}  # __NA__ se guarda como NULL en la columna real

    _registrar_primera_captura(cursor, importacion_id=42,
                                existing=existing, merged=merged,
                                campos_a_actualizar=["log_contenedor"])

    inserts = [c for c in cursor.execute.call_args_list
               if "INSERT INTO importaciones_historial_campos" in c.args[0]]
    assert len(inserts) == 0


def test_registrar_primera_captura_ignora_campos_fuera_de_cols_permitidas():
    cursor = _cursor_mock()
    existing = {"campo_inventado": None}
    merged   = {"campo_inventado": "x"}

    _registrar_primera_captura(cursor, importacion_id=42,
                                existing=existing, merged=merged,
                                campos_a_actualizar=["campo_inventado"])

    assert cursor.execute.call_count == 0


from db_conexion import obtener_conexion


def _crear_embarque_de_prueba():
    conn = obtener_conexion()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO importaciones (referencia, via_transporte) VALUES (%s, 'MARITIMO')",
        (f"TEST-AUDIT-{__import__('time').time()}",),
    )
    conn.commit()
    id_imp = cursor.lastrowid
    conn.close()
    return id_imp


def test_put_importacion_registra_historial_en_bd_real():
    id_imp = _crear_embarque_de_prueba()
    client = _cliente_test()

    # Primera captura de log_contenedor -> debe quedar en el historial.
    resp = client.put(f"/importaciones/{id_imp}", json={"log_contenedor": "MSKU9999999"})
    assert resp.status_code == 200

    conn = obtener_conexion()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT * FROM importaciones_historial_campos WHERE importacion_id = %s AND campo = %s",
        (id_imp, "log_contenedor"),
    )
    filas = cursor.fetchall()
    assert len(filas) == 1
    assert filas[0]["valor_nuevo"] == "MSKU9999999"

    # Segunda escritura del MISMO campo (correccion) -> NO debe duplicar la fila.
    resp2 = client.put(f"/importaciones/{id_imp}", json={"log_contenedor": "MSKU8888888"})
    assert resp2.status_code == 200
    cursor.execute(
        "SELECT COUNT(*) AS c FROM importaciones_historial_campos WHERE importacion_id = %s AND campo = %s",
        (id_imp, "log_contenedor"),
    )
    assert cursor.fetchone()["c"] == 1  # sigue siendo 1, no 2

    cursor.execute("DELETE FROM importaciones WHERE id = %s", (id_imp,))
    conn.commit()
    conn.close()
