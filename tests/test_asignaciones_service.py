# tests/test_asignaciones_service.py
from unittest.mock import MagicMock

import pytest

from services.asignaciones_service import (
    AsignacionesError, actualizar_producto, crear_producto, listar_productos, recalcular_propuesta,
)


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


def test_actualizar_producto_no_existe(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        actualizar_producto(999, cantidad_embarcada=10)
    assert exc.value.code == "PRODUCTO_NO_EXISTE"


def test_actualizar_producto_rechaza_bajar_por_debajo_de_lo_asignado(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 10, "cantidad_embarcada": 10},  # producto (FOR UPDATE)
        {"total": 8},                          # ya asignado
    ]
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        actualizar_producto(10, cantidad_embarcada=5)
    assert exc.value.code == "AJUSTE_INVALIDO"


def test_actualizar_producto_registra_movimiento_ajuste(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 10, "cantidad_embarcada": 10},
        {"total": 3},
        {"id": 10, "cantidad_embarcada": 15, "descripcion": None},  # SELECT final
    ]
    _mock_conn(mocker, cursor)

    resultado = actualizar_producto(10, cantidad_embarcada=15)

    assert resultado["cantidad_embarcada"] == 15
    ajustes = [c for c in cursor.execute.call_args_list if "INSERT INTO importacion_movimientos" in c.args[0]]
    assert len(ajustes) == 1
    assert ajustes[0].args[1][1] == "AJUSTE"
    assert ajustes[0].args[1][2] == 5  # 15 - 10


def test_recalcular_reparte_por_prioridad_hasta_agotar_disponible(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [{"id": 1}]  # importacion existe
    cursor.fetchall.return_value = [
        {"id": 10, "sku": "SKU-1", "sku_norm": "SKU1", "periodo": "2026-2027",
         "cantidad_embarcada": 10, "importacion_id": 1},
    ]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=10)
    mocker.patch(
        "services.asignaciones_service.demanda_neta_por_cliente",
        return_value={"SKU1": {"MC677": 2, "LC657": 3}},  # LC657 tiene mayor prioridad (1 vs 2)
    )

    propuestas = recalcular_propuesta(1)

    assert len(propuestas) == 1
    orden = [c["clave_cliente"] for c in propuestas[0]["propuesta"]]
    assert orden == ["LC657", "MC677"]  # prioridad 1 antes que prioridad 2
    assert propuestas[0]["sobrante_estimado"] == 5  # 10 - 3 - 2


def test_recalcular_no_sobreasigna_cuando_demanda_supera_disponible(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [{"id": 1}]
    cursor.fetchall.return_value = [
        {"id": 10, "sku": "SKU-1", "sku_norm": "SKU1", "periodo": "2026-2027",
         "cantidad_embarcada": 4, "importacion_id": 1},
    ]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=4)
    mocker.patch(
        "services.asignaciones_service.demanda_neta_por_cliente",
        return_value={"SKU1": {"LC657": 3, "MC677": 3}},  # demanda total 6 > disponible 4
    )

    propuestas = recalcular_propuesta(1)

    total_sugerido = sum(c["cantidad_sugerida"] for c in propuestas[0]["propuesta"])
    assert total_sugerido == 4  # nunca más que el disponible
    assert propuestas[0]["sobrante_estimado"] == 0


def test_recalcular_degrada_sin_romper_si_proyecciones_falla(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [{"id": 1}]
    cursor.fetchall.return_value = [
        {"id": 10, "sku": "SKU-1", "sku_norm": "SKU1", "periodo": "2026-2027",
         "cantidad_embarcada": 4, "importacion_id": 1},
    ]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=4)
    mocker.patch(
        "services.asignaciones_service.demanda_neta_por_cliente",
        side_effect=RuntimeError("Sin conexión a BD para consultar forecast_proyecciones"),
    )

    propuestas = recalcular_propuesta(1)

    assert propuestas[0]["proyecciones_disponibles"] is False
    assert propuestas[0]["propuesta"] == []
    assert propuestas[0]["sobrante_estimado"] == 4
