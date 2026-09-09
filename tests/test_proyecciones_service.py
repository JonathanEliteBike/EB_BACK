from unittest.mock import MagicMock

import pytest

from services.proyecciones_service import (
    PRIORIDAD_CLIENTES, _PRIORIDAD_MAP, _norm_sku, obtener_prioridad_clientes,
    demanda_neta_por_cliente,
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
