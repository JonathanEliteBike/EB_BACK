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


from routes.importaciones import _calcular_auditoria


def _conn_mock_auditoria(embarque_row, hitos_rows, historial_rows):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    # 3 llamadas en orden: embarque, hitos activos, historial del embarque
    cursor.fetchone.side_effect = [embarque_row]
    cursor.fetchall.side_effect = [hitos_rows, historial_rows]
    return conn, cursor


def test_auditoria_hito_desde_alta_de_embarque_a_tiempo():
    embarque = {"id": 1, "created_at": date(2026, 1, 1)}
    hitos = [{"id": 1, "seccion": "logistica", "orden_hito": 1, "etiqueta": "1. Importador",
              "campo_dato": "odoo_importador", "campo_ancla": None, "dias_esperados": 0}]
    historial = [{"campo": "odoo_importador", "capturado_en": date(2026, 1, 1)}]
    conn, _ = _conn_mock_auditoria(embarque, hitos, historial)

    resultado = _calcular_auditoria(1, conn)

    assert len(resultado) == 1
    assert resultado[0]["estado"] == "a_tiempo"
    assert resultado[0]["dias_diferencia"] == 0


def test_auditoria_hito_capturado_antes_sale_verde():
    embarque = {"id": 1, "created_at": date(2026, 1, 1)}
    hitos = [{"id": 1, "seccion": "logistica", "orden_hito": 2, "etiqueta": "9. Confirmacion",
              "campo_dato": "log_confirmacion_cotizacion", "campo_ancla": None, "dias_esperados": 7}]
    # esperada = 2026-01-08; real 2 dias antes = 2026-01-06
    historial = [{"campo": "log_confirmacion_cotizacion", "capturado_en": date(2026, 1, 6)}]
    conn, _ = _conn_mock_auditoria(embarque, hitos, historial)

    resultado = _calcular_auditoria(1, conn)

    assert resultado[0]["estado"] == "adelantado"
    assert resultado[0]["dias_diferencia"] == 2


def test_auditoria_hito_capturado_despues_sale_rojo():
    embarque = {"id": 1, "created_at": date(2026, 1, 1)}
    hitos = [{"id": 1, "seccion": "logistica", "orden_hito": 2, "etiqueta": "9. Confirmacion",
              "campo_dato": "log_confirmacion_cotizacion", "campo_ancla": None, "dias_esperados": 7}]
    # esperada = 2026-01-08; real 3 dias tarde = 2026-01-11
    historial = [{"campo": "log_confirmacion_cotizacion", "capturado_en": date(2026, 1, 11)}]
    conn, _ = _conn_mock_auditoria(embarque, hitos, historial)

    resultado = _calcular_auditoria(1, conn)

    assert resultado[0]["estado"] == "atrasado"
    assert resultado[0]["dias_diferencia"] == -3


def test_auditoria_hito_anclado_en_otro_hito_usa_esperada_no_real():
    embarque = {"id": 1, "created_at": date(2026, 1, 1)}
    hitos = [
        {"id": 1, "seccion": "odoo", "orden_hito": 1, "etiqueta": "1. Recepcion de documentos",
         "campo_dato": "log_recepcion_documentos", "campo_ancla": "log_fecha_entrega", "dias_esperados": 2},
        {"id": 2, "seccion": "odoo", "orden_hito": 2, "etiqueta": "6. Folios de orden de compra",
         "campo_dato": "odoo_folio_orden", "campo_ancla": "log_recepcion_documentos", "dias_esperados": 10},
    ]
    # log_fecha_entrega (disparador) se captura TARDE el 2026-01-20 en vez de a tiempo.
    # El hito 1 (Recepcion de documentos) por lo tanto tiene esperada = 2026-01-22,
    # y se captura justo a tiempo el 2026-01-22.
    # El hito 2 (Folios) debe esperar 10 dias desde la ESPERADA del hito 1
    # (2026-01-22), no desde el 2026-01-20 del disparador ni desde otra fecha:
    # esperada del hito 2 = 2026-02-01.
    historial = [
        {"campo": "log_fecha_entrega", "capturado_en": date(2026, 1, 20)},
        {"campo": "log_recepcion_documentos", "capturado_en": date(2026, 1, 22)},
        {"campo": "odoo_folio_orden", "capturado_en": date(2026, 2, 1)},
    ]
    conn, _ = _conn_mock_auditoria(embarque, hitos, historial)

    resultado = _calcular_auditoria(1, conn)

    folios = next(h for h in resultado if h["campo_dato"] == "odoo_folio_orden")
    assert folios["fecha_esperada"] == "2026-02-01"
    assert folios["estado"] == "a_tiempo"


def test_auditoria_hito_sin_disparador_capturado_queda_pendiente():
    embarque = {"id": 1, "created_at": date(2026, 1, 1)}
    hitos = [{"id": 1, "seccion": "logistica", "orden_hito": 3, "etiqueta": "18. Contenedores",
              "campo_dato": "log_contenedor", "campo_ancla": "log_fecha_entrega", "dias_esperados": 1}]
    historial = []  # log_fecha_entrega nunca se capturo
    conn, _ = _conn_mock_auditoria(embarque, hitos, historial)

    resultado = _calcular_auditoria(1, conn)

    assert resultado[0]["estado"] == "pendiente"
    assert resultado[0]["fecha_esperada"] is None


def test_auditoria_sin_hitos_activos_devuelve_lista_vacia():
    embarque = {"id": 1, "created_at": date(2026, 1, 1)}
    conn, _ = _conn_mock_auditoria(embarque, [], [])

    assert _calcular_auditoria(1, conn) == []


def test_auditoria_referencia_circular_queda_no_resoluble_sin_crashear():
    embarque = {"id": 1, "created_at": date(2026, 1, 1)}
    # A ancla en B y B ancla en A -- config invalida (error de captura en el
    # admin de hitos), pero el motor no debe crashear ni colgarse.
    hitos = [
        {"id": 1, "seccion": "logistica", "orden_hito": 1, "etiqueta": "A",
         "campo_dato": "campo_a", "campo_ancla": "campo_b", "dias_esperados": 1},
        {"id": 2, "seccion": "logistica", "orden_hito": 2, "etiqueta": "B",
         "campo_dato": "campo_b", "campo_ancla": "campo_a", "dias_esperados": 1},
    ]
    conn, _ = _conn_mock_auditoria(embarque, hitos, [])

    resultado = _calcular_auditoria(1, conn)

    assert len(resultado) == 2
    assert all(h["estado"] == "pendiente" for h in resultado)


def test_auditoria_dos_hitos_con_mismo_campo_dato_usa_el_de_menor_id():
    embarque = {"id": 1, "created_at": date(2026, 1, 1)}
    hitos = [
        {"id": 5, "seccion": "logistica", "orden_hito": 1, "etiqueta": "Version vieja",
         "campo_dato": "log_contenedor", "campo_ancla": None, "dias_esperados": 10},
        {"id": 2, "seccion": "logistica", "orden_hito": 1, "etiqueta": "Version nueva (id menor)",
         "campo_dato": "log_contenedor", "campo_ancla": None, "dias_esperados": 3},
        {"id": 9, "seccion": "importacion", "orden_hito": 1, "etiqueta": "Depende de Contenedores",
         "campo_dato": "imp_fecha_traduccion", "campo_ancla": "log_contenedor", "dias_esperados": 1},
    ]
    conn, _ = _conn_mock_auditoria(embarque, hitos, [])

    resultado = _calcular_auditoria(1, conn)

    dependiente = next(h for h in resultado if h["campo_dato"] == "imp_fecha_traduccion")
    # esperada de log_contenedor debe resolverse con el hito id=2 (dias_esperados=3),
    # no con el id=5 (dias_esperados=10): 2026-01-01 + 3 + 1 = 2026-01-05.
    assert dependiente["fecha_esperada"] == "2026-01-05"


def test_get_auditoria_devuelve_200_con_lista(mocker):
    conn = MagicMock()
    mocker.patch("routes.importaciones.obtener_conexion", return_value=conn)
    mocker.patch("routes.importaciones._calcular_auditoria", return_value=[
        {"id": 1, "seccion": "logistica", "estado": "a_tiempo"},
    ])

    resp = _cliente_test().get("/importaciones/123/auditoria")

    assert resp.status_code == 200
    assert resp.get_json()[0]["estado"] == "a_tiempo"


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


# ── Resumen de auditoria (todos los embarques, contador por estado) ──────────

from routes.importaciones import _calcular_auditoria_resumen


def test_resumen_auditoria_cuenta_por_estado(mocker):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = [
        {"id": 1, "referencia": "R26-0001", "nombre": "Embarque 1", "created_at": date(2026, 1, 1)},
        {"id": 2, "referencia": "R26-0002", "nombre": "Embarque 2", "created_at": date(2026, 1, 2)},
    ]
    mocker.patch(
        "routes.importaciones._calcular_auditoria",
        side_effect=[
            [{"estado": "atrasado"}, {"estado": "atrasado"}, {"estado": "a_tiempo"}],
            [{"estado": "adelantado"}, {"estado": "pendiente"}, {"estado": "en_espera"}],
        ],
    )

    resultado = _calcular_auditoria_resumen(conn)

    assert resultado[0] == {
        "id": 1, "referencia": "R26-0001", "nombre": "Embarque 1",
        "atrasados": 2, "adelantados": 0, "a_tiempo": 1, "pendientes": 0, "en_espera": 0,
    }
    assert resultado[1] == {
        "id": 2, "referencia": "R26-0002", "nombre": "Embarque 2",
        "atrasados": 0, "adelantados": 1, "a_tiempo": 0, "pendientes": 1, "en_espera": 1,
    }


def test_resumen_auditoria_sin_embarques_devuelve_lista_vacia(mocker):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = []

    assert _calcular_auditoria_resumen(conn) == []


def test_get_auditoria_resumen_devuelve_200(mocker):
    conn = MagicMock()
    mocker.patch("routes.importaciones.obtener_conexion", return_value=conn)
    mocker.patch("routes.importaciones._calcular_auditoria_resumen", return_value=[
        {"id": 1, "referencia": "R26-0001", "nombre": "Embarque 1",
         "atrasados": 2, "adelantados": 0, "a_tiempo": 1, "pendientes": 0, "en_espera": 0},
    ])

    resp = _cliente_test().get("/importaciones/auditoria-resumen")

    assert resp.status_code == 200
    assert resp.get_json()[0]["atrasados"] == 2
