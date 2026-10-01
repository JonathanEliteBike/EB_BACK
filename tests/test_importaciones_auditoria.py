"""Tests de la auditoria de llenado del embarque: tablas nuevas, historial
de captura por campo, y el motor de calculo _calcular_auditoria()."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

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


def _llamadas_executemany_hitos(cursor):
    return [
        c for c in cursor.executemany.call_args_list
        if c.args and "importaciones_hitos_auditoria" in c.args[0]
    ]


def test_inicializar_tablas_siembra_los_hitos_si_la_tabla_esta_vacia(mocker):
    from routes.importaciones import _HITOS_SEED

    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = (0,)
    mocker.patch("routes.importaciones.obtener_conexion", return_value=conn)

    resp = _cliente_test().post("/importaciones/inicializar-tablas")

    assert resp.status_code == 201
    llamadas = _llamadas_executemany_hitos(cursor)
    assert len(llamadas) == 1
    assert llamadas[0].args[1] == _HITOS_SEED


def test_inicializar_tablas_no_duplica_hitos_si_ya_hay_datos(mocker):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = (20,)
    mocker.patch("routes.importaciones.obtener_conexion", return_value=conn)

    resp = _cliente_test().post("/importaciones/inicializar-tablas")

    assert resp.status_code == 201
    assert _llamadas_executemany_hitos(cursor) == []


from datetime import date, datetime
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
    try:
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
        conn.close()
    finally:
        # try/finally: si algun assert de arriba falla, igual se limpia el
        # embarque de prueba en vez de dejarlo huerfano en la BD real.
        conn = obtener_conexion()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM importaciones WHERE id = %s", (id_imp,))
        conn.commit()
        conn.close()


def test_put_importacion_no_truena_si_falla_el_registro_de_historial():
    """Si _registrar_primera_captura() truena (ej. la tabla
    importaciones_historial_campos no existe todavia en un servidor donde
    no se corrio /inicializar-tablas antes del despliegue), el guardado
    principal del embarque YA se hizo commit -- no debe convertirse en un
    500 para el usuario, que vería un error aunque su dato si se guardo."""
    id_imp = _crear_embarque_de_prueba()
    try:
        with patch(
            "routes.importaciones._registrar_primera_captura",
            side_effect=Exception("tabla no existe"),
        ):
            resp = _cliente_test().put(f"/importaciones/{id_imp}", json={"log_contenedor": "MSKU7777777"})

        assert resp.status_code == 200

        conn = obtener_conexion()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT log_contenedor FROM importaciones WHERE id = %s", (id_imp,))
        fila = cursor.fetchone()
        conn.close()
        assert fila["log_contenedor"] == "MSKU7777777"
    finally:
        conn = obtener_conexion()
        cursor = conn.cursor()
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


def test_auditoria_campo_con_valor_previo_sin_historial_queda_sin_historial():
    # El campo ya tiene un valor en la tabla importaciones (se llenó antes de
    # que existiera esta auditoria), pero nunca se registró en el historial
    # -- no se debe mostrar como "en_espera" (daría a entender que sigue
    # vacío cuando en realidad ya está lleno, solo que no sabemos cuándo).
    embarque = {"id": 1, "created_at": date(2026, 1, 1), "odoo_importador": "ACME SA"}
    hitos = [{"id": 1, "seccion": "logistica", "orden_hito": 1, "etiqueta": "1. Importador",
              "campo_dato": "odoo_importador", "campo_ancla": None, "dias_esperados": 0}]
    historial = []
    conn, _ = _conn_mock_auditoria(embarque, hitos, historial)

    resultado = _calcular_auditoria(1, conn)

    assert resultado[0]["estado"] == "sin_historial"
    assert resultado[0]["dias_diferencia"] is None
    assert resultado[0]["fecha_real"] is None


def test_auditoria_campo_vacio_sin_historial_sigue_en_espera():
    embarque = {"id": 1, "created_at": date(2026, 1, 1), "odoo_importador": None}
    hitos = [{"id": 1, "seccion": "logistica", "orden_hito": 1, "etiqueta": "1. Importador",
              "campo_dato": "odoo_importador", "campo_ancla": None, "dias_esperados": 0}]
    conn, _ = _conn_mock_auditoria(embarque, hitos, [])

    resultado = _calcular_auditoria(1, conn)

    assert resultado[0]["estado"] == "en_espera"


def test_auditoria_campo_marcado_na_sigue_en_espera():
    embarque = {"id": 1, "created_at": date(2026, 1, 1), "odoo_importador": "__NA__"}
    hitos = [{"id": 1, "seccion": "logistica", "orden_hito": 1, "etiqueta": "1. Importador",
              "campo_dato": "odoo_importador", "campo_ancla": None, "dias_esperados": 0}]
    conn, _ = _conn_mock_auditoria(embarque, hitos, [])

    resultado = _calcular_auditoria(1, conn)

    assert resultado[0]["estado"] == "en_espera"


def test_auditoria_consulta_hitos_ordenados_por_id_no_por_seccion():
    # El usuario llena los 20 hitos en el orden en que se los dieron
    # (mezclando secciones), no agrupados por seccion -- la vista tiene que
    # respetar ese orden de captura, no reagruparlo.
    embarque = {"id": 1, "created_at": date(2026, 1, 1)}
    conn, cursor = _conn_mock_auditoria(embarque, [], [])

    _calcular_auditoria(1, conn)

    sql_hitos = cursor.execute.call_args_list[1].args[0]
    assert "ORDER BY id" in sql_hitos
    assert "ORDER BY seccion" not in sql_hitos


def test_auditoria_created_at_datetime_no_genera_fecha_invalida():
    # MySQL devuelve TIMESTAMP/DATETIME como datetime.datetime, no como
    # date -- y datetime.datetime ES subclase de date, asi que un simple
    # isinstance(base, date) no lo detecta para convertirlo. Si no se
    # normaliza, fecha_esperada queda como "2026-01-01T10:30:00" en vez de
    # "2026-01-01", y el frontend lo interpreta como "Invalid Date".
    embarque = {"id": 1, "created_at": datetime(2026, 1, 1, 10, 30, 0)}
    hitos = [{"id": 1, "seccion": "logistica", "orden_hito": 1, "etiqueta": "1. Importador",
              "campo_dato": "odoo_importador", "campo_ancla": None, "dias_esperados": 0}]
    conn, _ = _conn_mock_auditoria(embarque, hitos, [])

    resultado = _calcular_auditoria(1, conn)

    assert resultado[0]["fecha_esperada"] == "2026-01-01"


def test_auditoria_capturado_en_datetime_calcula_dias_diferencia_correctamente():
    embarque = {"id": 1, "created_at": date(2026, 1, 1)}
    hitos = [{"id": 1, "seccion": "logistica", "orden_hito": 2, "etiqueta": "9. Confirmacion",
              "campo_dato": "log_confirmacion_cotizacion", "campo_ancla": None, "dias_esperados": 7}]
    # esperada = 2026-01-08; capturado_en llega como datetime (TIMESTAMP real), no date
    historial = [{"campo": "log_confirmacion_cotizacion", "capturado_en": datetime(2026, 1, 6, 14, 0, 0)}]
    conn, _ = _conn_mock_auditoria(embarque, hitos, historial)

    resultado = _calcular_auditoria(1, conn)

    assert resultado[0]["estado"] == "adelantado"
    assert resultado[0]["dias_diferencia"] == 2
    assert resultado[0]["fecha_real"] == "2026-01-06"


def test_auditoria_disparador_capturado_en_datetime_resuelve_bien():
    embarque = {"id": 1, "created_at": date(2026, 1, 1)}
    hitos = [{"id": 1, "seccion": "logistica", "orden_hito": 3, "etiqueta": "18. Contenedores",
              "campo_dato": "log_contenedor", "campo_ancla": "log_fecha_entrega", "dias_esperados": 1}]
    historial = [{"campo": "log_fecha_entrega", "capturado_en": datetime(2026, 1, 10, 9, 15, 0)}]
    conn, _ = _conn_mock_auditoria(embarque, hitos, historial)

    resultado = _calcular_auditoria(1, conn)

    assert resultado[0]["fecha_esperada"] == "2026-01-11"
    assert resultado[0]["estado"] == "en_espera"


def test_get_auditoria_devuelve_200_con_lista(mocker):
    conn = MagicMock()
    mocker.patch("routes.importaciones.obtener_conexion", return_value=conn)
    mocker.patch("routes.importaciones._calcular_auditoria", return_value=[
        {"id": 1, "seccion": "logistica", "estado": "a_tiempo"},
    ])

    resp = _cliente_test().get("/importaciones/123/auditoria")

    assert resp.status_code == 200
    assert resp.get_json()[0]["estado"] == "a_tiempo"


# El seed manual de hitos (_HITOS_INICIALES + test con skip) se reemplazó
# por el seed automático e idempotente dentro de inicializar_tablas() (ver
# _HITOS_SEED en routes/importaciones.py y los tests
# test_inicializar_tablas_siembra_los_hitos_si_la_tabla_esta_vacia /
# test_inicializar_tablas_no_duplica_hitos_si_ya_hay_datos más arriba) --
# ya no hace falta un paso manual de despliegue para sembrar los 20 hitos.


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
    hitos_emb1 = [{"estado": "atrasado"}, {"estado": "atrasado"}, {"estado": "a_tiempo"}]
    hitos_emb2 = [{"estado": "adelantado"}, {"estado": "pendiente"}, {"estado": "en_espera"}, {"estado": "sin_historial"}]
    mocker.patch(
        "routes.importaciones._calcular_auditoria",
        side_effect=[hitos_emb1, hitos_emb2],
    )

    resultado = _calcular_auditoria_resumen(conn)

    assert resultado[0] == {
        "id": 1, "referencia": "R26-0001", "nombre": "Embarque 1", "creado_en": "2026-01-01",
        "atrasados": 2, "adelantados": 0, "a_tiempo": 1, "pendientes": 0, "en_espera": 0, "sin_historial": 0,
        "hitos": hitos_emb1,
    }
    assert resultado[1] == {
        "id": 2, "referencia": "R26-0002", "nombre": "Embarque 2", "creado_en": "2026-01-02",
        "atrasados": 0, "adelantados": 1, "a_tiempo": 0, "pendientes": 1, "en_espera": 1, "sin_historial": 1,
        "hitos": hitos_emb2,
    }


def test_resumen_auditoria_creado_en_normaliza_datetime_a_date(mocker):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = [
        {"id": 1, "referencia": "R26-0001", "nombre": "Embarque 1", "created_at": datetime(2026, 1, 1, 15, 30)},
    ]
    mocker.patch("routes.importaciones._calcular_auditoria", return_value=[])

    resultado = _calcular_auditoria_resumen(conn)

    assert resultado[0]["creado_en"] == "2026-01-01"


def test_resumen_auditoria_sin_embarques_devuelve_lista_vacia(mocker):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = []

    assert _calcular_auditoria_resumen(conn) == []


def test_resumen_auditoria_omite_embarque_con_created_at_null(mocker):
    """Un embarque con created_at NULL (dato faltante/corrupto) no debe
    tumbar el resumen completo con un 500 -- se omite esa fila y el resto
    del resumen se calcula normal."""
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = [
        {"id": 1, "referencia": "R26-0001", "nombre": "Sin fecha", "created_at": None},
        {"id": 2, "referencia": "R26-0002", "nombre": "Con fecha", "created_at": date(2026, 1, 2)},
    ]
    mocker.patch("routes.importaciones._calcular_auditoria", return_value=[])

    resultado = _calcular_auditoria_resumen(conn)

    assert [e["id"] for e in resultado] == [2]


def test_get_auditoria_resumen_devuelve_200(mocker):
    conn = MagicMock()
    mocker.patch("routes.importaciones.obtener_conexion", return_value=conn)
    mocker.patch("routes.importaciones._calcular_auditoria_resumen", return_value=[
        {"id": 1, "referencia": "R26-0001", "nombre": "Embarque 1",
         "atrasados": 2, "adelantados": 0, "a_tiempo": 1, "pendientes": 0, "en_espera": 0,
         "hitos": [{"seccion": "Logistica", "etiqueta": "Contenedores", "estado": "atrasado"}]},
    ])

    resp = _cliente_test().get("/importaciones/auditoria-resumen")

    assert resp.status_code == 200
    assert resp.get_json()[0]["atrasados"] == 2
    assert resp.get_json()[0]["hitos"][0]["etiqueta"] == "Contenedores"
