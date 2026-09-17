from unittest.mock import MagicMock

import pytest

from services.proyecciones_service import (
    PRIORIDAD_CLIENTES, _PRIORIDAD_MAP, _norm_sku, obtener_prioridad_clientes,
    demanda_neta_por_cliente,
    VENDEDOR_POR_CLIENTE, _mes_abreviado_desde_ym, reservar_en_odoo, OdooReservaError,
)


def test_prioridad_clientes_tiene_27_entradas_unicas():
    claves = [clave for _, clave, _ in PRIORIDAD_CLIENTES]
    assert len(claves) == 27
    assert len(set(claves)) == 27


def test_prioridad_map_lc657_es_prioridad_1():
    assert _PRIORIDAD_MAP['LC657'] == (1, 'Víctor Hugo Villanueva Guzman')


def test_norm_sku_quita_guiones_espacios_y_normaliza_mayusculas():
    assert _norm_sku('427102-0001004') == '4271020001004'
    assert _norm_sku(' 286383 - 704 ') == '286383704'
    assert _norm_sku(None) == ''


def test_obtener_prioridad_clientes_devuelve_27_dicts_ordenados():
    resultado = obtener_prioridad_clientes()
    assert len(resultado) == 27
    assert resultado[0] == {"clave": "LC657", "nombre": "Víctor Hugo Villanueva Guzman", "prioridad": 1}
    assert resultado[-1]["clave"] == "JC554"


def test_proyecciones_my27_importa_sin_error():
    # Si el refactor rompió algo (nombre no exportado, import circular), esto falla al importar.
    import routes.proyecciones_my27 as mod
    assert mod.PRIORIDAD_CLIENTES is PRIORIDAD_CLIENTES
    assert mod._PRIORIDAD_MAP is _PRIORIDAD_MAP


def test_demanda_neta_resta_lo_ya_confirmado_en_odoo(mocker):
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        {"clave_cliente": "LC657", "sku": "427102-0001004", "total": 5},
        {"clave_cliente": "MC677", "sku": "427102-0001004", "total": 2},
    ]
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mocker.patch("services.proyecciones_service.obtener_conexion", return_value=conn)
    mocker.patch(
        "services.proyecciones_service._get_ordenes_my27",
        return_value={"LC657": {"4271020001004": 4}},
    )

    resultado = demanda_neta_por_cliente("2026-2027", ["4271020001004"])

    assert resultado["4271020001004"]["LC657"] == 1   # 5 - 4 ya confirmado
    assert resultado["4271020001004"]["MC677"] == 2   # sin órdenes previas, se mantiene


def test_demanda_neta_degrada_a_demanda_bruta_si_odoo_falla(mocker):
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        {"clave_cliente": "LC657", "sku": "SKU-1", "total": 5},
    ]
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mocker.patch("services.proyecciones_service.obtener_conexion", return_value=conn)
    mocker.patch(
        "services.proyecciones_service._get_ordenes_my27",
        side_effect=Exception("Odoo no disponible"),
    )

    resultado = demanda_neta_por_cliente("2026-2027", ["SKU1"])

    assert resultado["SKU1"]["LC657"] == 5  # sin deducción, pero no falla


def test_demanda_neta_lanza_runtime_error_sin_conexion(mocker):
    mocker.patch("services.proyecciones_service.obtener_conexion", return_value=None)
    with pytest.raises(RuntimeError):
        demanda_neta_por_cliente("2026-2027", ["SKU1"])


# ── reservar_en_odoo ───────────────────────────────────────────────────────────

def test_vendedor_por_cliente_cubre_los_mismos_clientes_que_prioridad_mas_uno():
    # FA318 es el único cliente nuevo en la tabla de vendedores que aún no
    # está en la lista de prioridad de clientes.
    claves_prioridad = {clave for _, clave, _ in PRIORIDAD_CLIENTES}
    assert claves_prioridad - set(VENDEDOR_POR_CLIENTE) == set()
    assert set(VENDEDOR_POR_CLIENTE) - claves_prioridad == {"FA318"}
    assert VENDEDOR_POR_CLIENTE["LC657"] == 18
    assert VENDEDOR_POR_CLIENTE["FA318"] == 17


def test_mes_abreviado_desde_ym():
    assert _mes_abreviado_desde_ym("2026-10") == "OCT"
    assert _mes_abreviado_desde_ym("2026-01") == "ENE"
    with pytest.raises(ValueError):
        _mes_abreviado_desde_ym("octubre")
    with pytest.raises(ValueError):
        _mes_abreviado_desde_ym("2026-13")


def test_reservar_en_odoo_crea_orden_nueva_con_vendedor_y_actividad(mocker):
    models = MagicMock()
    models.execute_kw.side_effect = [
        [{"id": 501, "name": "Víctor Hugo"}],              # res.partner search_read
        [{"id": 900, "default_code": "SKU-1", "lst_price": 1000.0}],  # product.product search_read
        [],                                                  # crm.tag search_read (no existe)
        77,                                                  # crm.tag create -> tag_id
        [],                                                  # sale.order search_read (no hay orden abierta)
        3001,                                                # sale.order create -> order_id
        [{"name": "S00042"}],                               # sale.order read -> name
        [733],                                               # ir.model search -> id de sale.order
        ("mail.activity.type", 5),                          # ir.model.data check_object_reference
        9001,                                                # mail.activity create
    ]
    mocker.patch("services.proyecciones_service.get_odoo_models", return_value=(1, models, None))

    resultado = reservar_en_odoo("lc657", "2026-10", [{"sku": "SKU-1", "cantidad": 3}])

    assert resultado == {"order_id": 3001, "order_name": "S00042"}
    create_call = [c for c in models.execute_kw.call_args_list if c.args[3] == "sale.order" and c.args[4] == "create"][0]
    order_vals = create_call.args[5][0]
    assert order_vals["partner_id"] == 501
    assert order_vals["user_id"] == 18  # VENDEDOR_POR_CLIENTE["LC657"]
    assert order_vals["tag_ids"] == [(4, 77)]
    activity_call = [c for c in models.execute_kw.call_args_list if c.args[3] == "mail.activity"][0]
    assert activity_call.args[5][0]["summary"] == "Revisar reserva de proyección"
    assert activity_call.args[5][0]["user_id"] == 18
    # res_model_id (no res_model) es el campo obligatorio que espera mail.activity
    assert activity_call.args[5][0]["res_model_id"] == 733
    assert "res_model" not in activity_call.args[5][0]


def test_reservar_en_odoo_agrega_lineas_a_orden_en_borrador_existente_sin_crear_otra(mocker):
    models = MagicMock()
    models.execute_kw.side_effect = [
        [{"id": 501, "name": "Víctor Hugo"}],                        # partner
        [{"id": 900, "default_code": "SKU-1", "lst_price": 1000.0}], # producto
        [{"id": 77}],                                                 # tag ya existe
        [{"id": 3001, "name": "S00042", "order_line": [11]}],         # orden en borrador ya existe
        [{"id": 11, "product_id": [900, "SKU-1"], "product_uom_qty": 3}],  # línea ya existente para ese producto
        True,                                                          # sale.order write
    ]
    mocker.patch("services.proyecciones_service.get_odoo_models", return_value=(1, models, None))

    resultado = reservar_en_odoo("LC657", "2026-10", [{"sku": "SKU-1", "cantidad": 2}])

    assert resultado == {"order_id": 3001, "order_name": "S00042"}
    # No debe haber llamado a crear una orden ni una actividad nueva.
    metodos_llamados = [(c.args[3], c.args[4]) for c in models.execute_kw.call_args_list]
    assert ("sale.order", "create") not in metodos_llamados
    assert ("mail.activity", "create") not in metodos_llamados
    write_call = [c for c in models.execute_kw.call_args_list if c.args[3] == "sale.order" and c.args[4] == "write"][0]
    order_line_cmds = write_call.args[5][1]["order_line"]
    assert order_line_cmds == [(1, 11, {"product_uom_qty": 5})]  # 3 existentes + 2 nuevas


def test_reservar_en_odoo_lanza_error_si_no_encuentra_el_contacto(mocker):
    models = MagicMock()
    models.execute_kw.return_value = []  # res.partner search_read vacío
    mocker.patch("services.proyecciones_service.get_odoo_models", return_value=(1, models, None))

    with pytest.raises(OdooReservaError, match="contacto"):
        reservar_en_odoo("LC657", "2026-10", [{"sku": "SKU-1", "cantidad": 1}])


def test_reservar_en_odoo_lanza_error_si_el_sku_no_existe_en_odoo(mocker):
    models = MagicMock()
    models.execute_kw.side_effect = [
        [{"id": 501, "name": "Víctor Hugo"}],  # partner
        [],                                      # product.product search_read: ningún SKU encontrado
    ]
    mocker.patch("services.proyecciones_service.get_odoo_models", return_value=(1, models, None))

    with pytest.raises(OdooReservaError, match="SKU-1"):
        reservar_en_odoo("LC657", "2026-10", [{"sku": "SKU-1", "cantidad": 1}])


def test_reservar_en_odoo_lanza_error_si_no_hay_conexion_a_odoo(mocker):
    mocker.patch("services.proyecciones_service.get_odoo_models", return_value=(None, None, "timeout"))
    with pytest.raises(OdooReservaError, match="timeout"):
        reservar_en_odoo("LC657", "2026-10", [{"sku": "SKU-1", "cantidad": 1}])


def test_reservar_en_odoo_lanza_error_con_mes_objetivo_invalido(mocker):
    with pytest.raises(OdooReservaError):
        reservar_en_odoo("LC657", "no-es-un-mes", [{"sku": "SKU-1", "cantidad": 1}])
