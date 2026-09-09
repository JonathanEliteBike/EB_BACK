# tests/test_asignaciones_service.py
from unittest.mock import MagicMock

import pytest

from services.asignaciones_service import AsignacionesError, crear_producto, listar_productos


def _mock_conn(mocker, cursor):
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mocker.patch("services.asignaciones_service.obtener_conexion", return_value=conn)
    return conn


def test_crear_producto_rechaza_cantidad_negativa():
    with pytest.raises(AsignacionesError) as exc:
        crear_producto(1, "SKU-1", -1, "2026-2027")
    assert exc.value.code == "SKU_INVALIDO"


def test_crear_producto_rechaza_sku_vacio():
    with pytest.raises(AsignacionesError) as exc:
        crear_producto(1, "   ", 10, "2026-2027")
    assert exc.value.code == "SKU_INVALIDO"


def test_crear_producto_importacion_no_existe(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = None  # SELECT id FROM importaciones -> nada
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        crear_producto(999, "SKU-1", 10, "2026-2027")
    assert exc.value.code == "IMPORTACION_NO_EXISTE"
    assert exc.value.status == 404


def test_crear_producto_sku_duplicado(mocker):
    cursor = MagicMock()
    # 1ra llamada: importacion existe. 2da: ya hay un producto con ese sku_norm.
    cursor.fetchone.side_effect = [{"id": 1}, {"id": 5}]
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        crear_producto(1, "427102-0001004", 10, "2026-2027")
    assert exc.value.code == "SKU_DUPLICADO"
    assert exc.value.status == 409


def test_crear_producto_exitoso_normaliza_sku_y_registra_entrada(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 1},                                            # importacion existe
        None,                                                  # no hay duplicado
        {"id": 10, "sku": "427102-0001004", "sku_norm": "4271020001004",
         "cantidad_embarcada": 10, "periodo": "2026-2027",
         "importacion_id": 1, "descripcion": None},            # SELECT final
    ]
    cursor.lastrowid = 10
    _mock_conn(mocker, cursor)

    resultado = crear_producto(1, "427102-0001004", 10, "2026-2027")

    assert resultado["sku_norm"] == "4271020001004"
    inserts = [c for c in cursor.execute.call_args_list if "INSERT INTO importacion_movimientos" in c.args[0]]
    assert len(inserts) == 1
    assert inserts[0].args[1][1] == "ENTRADA"
    assert inserts[0].args[1][2] == 10


def test_listar_productos_calcula_disponible_asignado_sobrante_vendido(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 1},                                   # importacion existe
        {"disponible": 7},                            # _disponible_producto
        {"total": 3},                                 # asignado
        {"total": 0},                                 # vendido
    ]
    cursor.fetchall.return_value = [
        {"id": 10, "importacion_id": 1, "sku": "SKU-1", "sku_norm": "SKU1",
         "cantidad_embarcada": 10, "periodo": "2026-2027", "descripcion": None},
    ]
    _mock_conn(mocker, cursor)

    resultado = listar_productos(1)

    assert len(resultado) == 1
    assert resultado[0]["cantidad_disponible"] == 7
    assert resultado[0]["cantidad_asignada"] == 3
    assert resultado[0]["cantidad_sobrante"] == 7  # 10 embarcado - 3 asignado
    assert resultado[0]["cantidad_vendida"] == 0
