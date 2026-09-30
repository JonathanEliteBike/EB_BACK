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


def test_listar_hitos_auditoria_devuelve_json_del_select(mocker):
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        {"id": 1, "seccion": "logistica", "orden_hito": 1, "etiqueta": "1. Importador",
         "campo_dato": "odoo_importador", "campo_ancla": None, "dias_esperados": 0, "activo": 1},
    ]
    conn.cursor.return_value = cursor
    mocker.patch("routes.importaciones.obtener_conexion", return_value=conn)

    resp = _cliente_test().get("/importaciones/hitos-auditoria")

    assert resp.status_code == 200
    assert resp.get_json()[0]["campo_dato"] == "odoo_importador"


def test_crear_hito_auditoria_rechaza_campo_dato_vacio(mocker):
    conn = MagicMock()
    mocker.patch("routes.importaciones.obtener_conexion", return_value=conn)

    resp = _cliente_test().post("/importaciones/hitos-auditoria", json={
        "seccion": "logistica", "orden_hito": 1, "etiqueta": "x",
        "campo_dato": "", "dias_esperados": 5,
    })

    assert resp.status_code == 400


def test_crear_hito_auditoria_ok(mocker):
    conn = MagicMock()
    cursor = MagicMock()
    cursor.lastrowid = 7
    conn.cursor.return_value = cursor
    mocker.patch("routes.importaciones.obtener_conexion", return_value=conn)

    resp = _cliente_test().post("/importaciones/hitos-auditoria", json={
        "seccion": "logistica", "orden_hito": 3, "etiqueta": "18. Contenedores",
        "campo_dato": "log_contenedor", "campo_ancla": "log_fecha_entrega", "dias_esperados": 1,
    })

    assert resp.status_code == 201
    assert resp.get_json()["id"] == 7


def test_actualizar_hito_auditoria_404_si_no_existe(mocker):
    conn = MagicMock()
    cursor = MagicMock()
    cursor.rowcount = 0
    conn.cursor.return_value = cursor
    mocker.patch("routes.importaciones.obtener_conexion", return_value=conn)

    resp = _cliente_test().put("/importaciones/hitos-auditoria/999", json={
        "seccion": "logistica", "orden_hito": 1, "etiqueta": "x",
        "campo_dato": "log_contenedor", "dias_esperados": 1,
    })

    assert resp.status_code == 404


def test_eliminar_hito_auditoria_ok(mocker):
    conn = MagicMock()
    cursor = MagicMock()
    cursor.rowcount = 1
    conn.cursor.return_value = cursor
    mocker.patch("routes.importaciones.obtener_conexion", return_value=conn)

    resp = _cliente_test().delete("/importaciones/hitos-auditoria/7")

    assert resp.status_code == 200


import pytest as _pytest

_HITOS_INICIALES = [
    ("logistica",    1, "1. Importador",                                   "odoo_importador",              None,                              0),
    ("costos",       1, "Flete proyectado (USD)",                          "cos_flete_proyectado_usd",     None,                              0),
    ("logistica",    2, "9. Confirmación de cotización y forwarder",       "log_confirmacion_cotizacion",  None,                              7),
    ("logistica",    3, "18. Contenedores",                                "log_contenedor",               "log_fecha_entrega",              1),
    ("importacion",  1, "1. Fecha de entrega de traducción al RBF",        "imp_fecha_traduccion",         "log_fecha_entrega",              10),
    ("odoo",         1, "1. Recepción de documentos",                      "log_recepcion_documentos",     "log_fecha_entrega",              2),
    ("odoo",         2, "6. Folios de orden de compra",                    "odoo_folio_orden",             "log_recepcion_documentos",       10),
    ("importacion",  2, "18. Recepción de draft de pedimento",             "imp_recepcion_draft_pedimento","imp_llegada_contenedor_puerto",  5),
    ("importacion",  3, "34. Fecha de pago de pedimento",                  "imp_fecha_pago_pedimento",     "imp_pedimento_revisado",         4),
    ("despacho",     1, "1. Solicitud de cita para cruce",                 "des_solicitud_cita_cruce",     "imp_fecha_pago_pedimento",       1),
    ("almacen",      1, "1. Base de datos para etiquetas",                 "alm_base_datos_etiquetas",     "imp_fecha_pago_pedimento",       2),
    ("despacho",     2, "9. Llegada de contenedor a almacén",              "des_llegada_almacen",          "des_fecha_cruce_real",           2),
    ("despacho",     3, "15. Recepción de documento EIR",                  "des_recepcion_eir",            "des_fecha_cruce_real",           3),
    ("almacen",      2, "6. Envío de información a la UVA (Real)",         "alm_envio_info_uva",           "des_llegada_almacen",            2),
    ("almacen",      3, "10. Fecha de terminación de etiquetado (Real)",   "alm_terminacion_etiquetado",   "alm_inicio_etiquetado",          3),
    ("recepcion",    1, "Cédula de costeo de IGI",                         "rec_cedula_costeo",            "des_llegada_almacen",            2),
    ("recepcion",    2, "Liberación final del producto",                   "rec_liberacion_final",         "rec_liberacion_verificacion",    1),
    ("costos",       3, "Costos reales",                                   "cos_tipo_cambio_pedimento",    "des_fecha_cruce_real",           10),
    ("cierre",       1, "1. Recepción de cuentas de gastos",                "cie_recepcion_cuenta_gastos",  "cos_tipo_cambio_pedimento",       2),
    ("cierre",       2, "Fecha de pago a agente aduanal",                  "cie_fecha_pago_aa",            "cie_recepcion_cuenta_gastos",    4),
]


@_pytest.mark.skip(reason="Seed manual -- correr una vez contra local quitando el skip, luego restaurarlo")
def test_seed_hitos_iniciales():
    conn = obtener_conexion()
    cursor = conn.cursor()
    cursor.executemany(
        "INSERT INTO importaciones_hitos_auditoria "
        "(seccion, orden_hito, etiqueta, campo_dato, campo_ancla, dias_esperados) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        _HITOS_INICIALES,
    )
    conn.commit()
    conn.close()
