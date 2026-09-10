# tests/test_asignaciones_service.py
from unittest.mock import MagicMock

import mysql.connector.errors
import pytest

from services.asignaciones_service import (
    AsignacionesError, actualizar_producto, crear_producto, listar_productos, recalcular_propuesta,
)
from services.asignaciones_service import asignar
from services.asignaciones_service import proponer_reasignacion, confirmar_reasignacion
from services.asignaciones_service import crear_venta_sobrante
from services.asignaciones_service import validar_venta_odoo
from services.asignaciones_service import cancelar_venta
from services.asignaciones_service import cancelar_asignacion
from services.asignaciones_service import resolver_reserva
from services.asignaciones_service import obtener_detalle_producto, resumen_embarque, listar_movimientos
from services.asignaciones_service import resumen_global, listar_productos_global
from services.asignaciones_service import (
    _columna_a_fecha, _fecha_a_columna, _meses_en_ventana, _meses_reasignables,
    _split_periodo, _migrar_esquema_reservas, MESES_ORDEN,
)
from services.proyecciones_service import demanda_neta_por_cliente_mensual
from db_conexion import obtener_conexion
import datetime as _dt


def _mock_conn(mocker, cursor):
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mocker.patch("services.asignaciones_service.obtener_conexion", return_value=conn)
    return conn


def _recalc_setup(mocker, *, producto, demanda_mensual, disponible, vigentes_rows=None):
    """Prepara mocks para recalcular_propuesta (un solo producto).

    demanda_mensual: {clave_cliente: {mes_col: neta}}  para el sku del producto.
    vigentes_rows:   filas de _reservas_vigentes_por_mes (list de dict clave_cliente/mes_objetivo/total).
    """
    cursor = MagicMock()
    cursor.fetchone.side_effect = [{"id": 1}]                       # importacion existe
    cursor.fetchall.side_effect = [[producto], list(vigentes_rows or [])]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=disponible)
    mocker.patch(
        "services.asignaciones_service.demanda_neta_por_cliente_mensual",
        return_value={producto["sku_norm"]: demanda_mensual},
    )
    return cursor


_PROD_SKU1 = {"id": 10, "sku": "SKU-1", "sku_norm": "SKU1", "periodo": "2026-2027",
              "cantidad_embarcada": 10, "importacion_id": 1, "descripcion": None}


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


def test_crear_producto_sku_duplicado_por_carrera_en_el_insert(mocker):
    """El chequeo previo pasa (nadie tenía el SKU) pero el INSERT choca con
    uq_producto_embarque: debe salir como el mismo 409 SKU_DUPLICADO, no un 500 crudo."""
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 1},  # importacion existe
        None,       # chequeo previo: no hay duplicado (todavía)
    ]
    cursor.execute.side_effect = [
        None,  # SELECT importaciones
        None,  # SELECT duplicado
        mysql.connector.errors.IntegrityError("Duplicate entry for key 'uq_producto_embarque'"),
    ]
    conn = _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        crear_producto(1, "427102-0001004", 10, "2026-2027")
    assert exc.value.code == "SKU_DUPLICADO"
    assert exc.value.status == 409
    assert conn.rollback.called
    assert not conn.commit.called


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
    consultas_vendido = [
        c for c in cursor.execute.call_args_list
        if "FROM importacion_sobrantes_ventas" in c.args[0]
    ]
    assert len(consultas_vendido) == 1
    # una venta PENDIENTE_VALIDACION ya descontó del disponible: debe contar como vendida
    assert "estado IN ('VALIDADO', 'PENDIENTE_VALIDACION')" in consultas_vendido[0].args[0]


def test_listar_productos_cuenta_ventas_pendientes_de_validacion_como_vendidas(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 1},            # importacion existe
        {"disponible": 8},     # _disponible_producto (10 embarcadas - 2 vendidas pendientes)
        {"total": 0},          # asignado
        {"total": 2},          # vendido: 2 unidades en PENDIENTE_VALIDACION
    ]
    cursor.fetchall.return_value = [
        {"id": 10, "importacion_id": 1, "sku": "SKU-1", "sku_norm": "SKU1",
         "cantidad_embarcada": 10, "periodo": "2026-2027", "descripcion": None},
    ]
    _mock_conn(mocker, cursor)

    resultado = listar_productos(1)

    assert resultado[0]["cantidad_vendida"] == 2
    # sobrante (10 - 0 asignado) menos vendida (2) reconcilia con disponible (8)
    assert resultado[0]["cantidad_sobrante"] - resultado[0]["cantidad_vendida"] == \
        resultado[0]["cantidad_disponible"]


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
    _recalc_setup(
        mocker, producto=_PROD_SKU1, disponible=10,
        demanda_mensual={"MC677": {"octubre": 2}, "LC657": {"octubre": 3}},  # LC657 prioridad 1
    )
    propuestas = recalcular_propuesta(1, "octubre", "octubre")

    assert len(propuestas) == 1
    orden = [c["clave_cliente"] for c in propuestas[0]["propuesta"]]
    assert orden == ["LC657", "MC677"]                    # prioridad absoluta
    assert propuestas[0]["sobrante_estimado"] == 5        # 10 - 3 - 2
    assert propuestas[0]["ventana"] == {"desde": "2026-10", "hasta": "2026-10"}


def test_recalcular_cliente_mayor_recorre_meses_cronologicos(mocker):
    _recalc_setup(
        mocker, producto=_PROD_SKU1, disponible=8,
        demanda_mensual={
            "LC657": {"octubre": 5, "noviembre": 5},   # prioridad 1: 10 de necesidad
            "MC677": {"octubre": 5},                    # prioridad 2
        },
    )
    propuestas = recalcular_propuesta(1, "octubre", "noviembre")

    fila_lc = next(c for c in propuestas[0]["propuesta"] if c["clave_cliente"] == "LC657")
    # cliente-mayor: LC657 se lleva oct 5 + nov 3 (se acaba el stock) antes de tocar a MC677
    assert {m["mes"]: m["sugerido"] for m in fila_lc["meses"]} == {"2026-10": 5, "2026-11": 3}
    assert fila_lc["faltante_total"] == 2
    assert all(c["clave_cliente"] != "MC677" or c["sugerido_total"] == 0
               for c in propuestas[0]["propuesta"])
    assert propuestas[0]["sobrante_estimado"] == 0


def test_recalcular_netea_reservas_vigentes_de_otros_embarques(mocker):
    import datetime as _d
    _recalc_setup(
        mocker, producto=_PROD_SKU1, disponible=10,
        demanda_mensual={"LC657": {"noviembre": 10}},
        vigentes_rows=[
            {"clave_cliente": "LC657", "mes_objetivo": _d.date(2026, 11, 1), "total": 4},
        ],
    )
    propuestas = recalcular_propuesta(1, "noviembre", "noviembre")

    fila = propuestas[0]["propuesta"][0]
    mes = fila["meses"][0]
    assert mes == {"mes": "2026-11", "proyectado": 10, "vigente": 4, "sugerido": 6}
    assert fila["faltante_total"] == 0        # 10 - 4 vigente - 6 sugerido
    assert propuestas[0]["sobrante_estimado"] == 4


def test_recalcular_no_sobreasigna_cuando_demanda_supera_disponible(mocker):
    _recalc_setup(
        mocker, producto={**_PROD_SKU1, "cantidad_embarcada": 4}, disponible=4,
        demanda_mensual={"LC657": {"octubre": 3}, "MC677": {"octubre": 3}},  # total 6 > 4
    )
    propuestas = recalcular_propuesta(1, "octubre", "octubre")

    total_sugerido = sum(m["sugerido"] for c in propuestas[0]["propuesta"] for m in c["meses"])
    assert total_sugerido == 4
    assert propuestas[0]["sobrante_estimado"] == 0


def test_recalcular_degrada_sin_romper_si_proyecciones_falla(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [{"id": 1}]
    cursor.fetchall.side_effect = [
        [{**_PROD_SKU1, "cantidad_embarcada": 4}],
        [],   # _reservas_vigentes_por_mes
    ]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=4)
    mocker.patch(
        "services.asignaciones_service.demanda_neta_por_cliente_mensual",
        side_effect=RuntimeError("Sin conexión a BD para consultar forecast_proyecciones"),
    )

    propuestas = recalcular_propuesta(1, "octubre", "octubre")

    assert propuestas[0]["proyecciones_disponibles"] is False
    assert propuestas[0]["propuesta"] == []
    assert propuestas[0]["sobrante_estimado"] == 4


def test_recalcular_exige_ventana():
    with pytest.raises(AsignacionesError) as exc:
        recalcular_propuesta(1, None, "octubre")
    assert exc.value.code == "VENTANA_REQUERIDA"


# ── proponer_reasignacion (meses anteriores, prioridad -> cronologico) ────────

def test_reasignacion_prioridad_absoluta_cliente_llena_sus_meses_antes_del_siguiente(mocker):
    _recalc_setup(
        mocker, producto=_PROD_SKU1, disponible=8,
        demanda_mensual={
            "LC657": {"agosto": 5, "septiembre": 5},   # prioridad 1: 10 de necesidad
            "MC677": {"agosto": 6},                     # prioridad 2, necesidad en agosto
        },
    )
    props = proponer_reasignacion(1, "diciembre")

    assert props[0]["origen"] == "REASIGNACION"
    fila_lc = next(c for c in props[0]["propuesta"] if c["clave_cliente"] == "LC657")
    # LC657 (P1) se lleva ago 5 + sep 3 (se acaba el stock) antes de tocar a MC677 (P2)
    assert {m["mes"]: m["sugerido"] for m in fila_lc["meses"]} == {"2026-08": 5, "2026-09": 3}
    assert fila_lc["faltante_total"] == 2
    assert all(c["clave_cliente"] != "MC677" or c["sugerido_total"] == 0
               for c in props[0]["propuesta"])
    assert props[0]["sobrante_estimado"] == 0


def test_reasignacion_excluye_may_jun_jul_y_la_ventana(mocker):
    _recalc_setup(
        mocker, producto=_PROD_SKU1, disponible=100,
        demanda_mensual={"LC657": {"mayo": 5, "junio": 5, "julio": 5,
                                   "agosto": 3, "diciembre": 9}},
    )
    props = proponer_reasignacion(1, "diciembre")   # reasignable = ago..nov

    meses = [m["mes"] for c in props[0]["propuesta"] for m in c["meses"]]
    assert meses == ["2026-08"]        # may/jun/jul excluidos; diciembre fuera del rango


def test_reasignacion_netea_reservas_vigentes_de_otros_embarques(mocker):
    import datetime as _d
    _recalc_setup(
        mocker, producto=_PROD_SKU1, disponible=10,
        demanda_mensual={"LC657": {"noviembre": 10}},
        vigentes_rows=[{"clave_cliente": "LC657", "mes_objetivo": _d.date(2026, 11, 1), "total": 4}],
    )
    props = proponer_reasignacion(1, "diciembre")

    mes = props[0]["propuesta"][0]["meses"][0]
    assert mes == {"mes": "2026-11", "proyectado": 10, "vigente": 4, "sugerido": 6}
    assert props[0]["sobrante_estimado"] == 4


def test_reasignacion_salta_productos_sin_disponible(mocker):
    _recalc_setup(
        mocker, producto=_PROD_SKU1, disponible=0,
        demanda_mensual={"LC657": {"agosto": 5}},
    )
    assert proponer_reasignacion(1, "diciembre") == []


def test_reasignacion_exige_ventana_desde():
    with pytest.raises(AsignacionesError) as exc:
        proponer_reasignacion(1, None)
    assert exc.value.code == "VENTANA_REQUERIDA"


# ── confirmar_reasignacion ──────────────────────────────────────────────────

def test_confirmar_reasignacion_crea_fila_pendiente_y_movimiento_reasignacion(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 10, "importacion_id": 1, "periodo": "2026-2027", "sku_norm": "SKU1"},
        {"clave": "LC657"},   # cliente existe
        None,                 # no hay reserva REASIGNACION previa
    ]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=10)

    res = confirmar_reasignacion(
        10, [{"clave_cliente": "LC657", "mes_objetivo": "2026-11", "cantidad": 6, "proyectado": 6}],
        importacion_id=1,
    )

    assert res == {"producto_id": 10, "disponible_restante": 4}
    inserts = [c for c in cursor.execute.call_args_list if "INSERT INTO importacion_asignaciones" in c.args[0]]
    assert len(inserts) == 1
    assert "'REASIGNACION'" in inserts[0].args[0] and "'PENDIENTE_CONFIRMACION'" in inserts[0].args[0]
    movs = [c for c in cursor.execute.call_args_list if "INSERT INTO importacion_movimientos" in c.args[0]]
    assert movs[0].args[1][1] == "REASIGNACION"
    assert movs[0].args[1][2] == -6


def test_confirmar_reasignacion_rechaza_sobre_stock(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 10, "importacion_id": 1, "periodo": "2026-2027", "sku_norm": "SKU1"},
        {"clave": "LC657"},
    ]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=3)

    with pytest.raises(AsignacionesError) as exc:
        confirmar_reasignacion(10, [{"clave_cliente": "LC657", "cantidad": 6}], importacion_id=1)
    assert exc.value.code == "STOCK_INSUFICIENTE"


def test_asignar_rechaza_lista_vacia():
    with pytest.raises(AsignacionesError) as exc:
        asignar(10, [])
    assert exc.value.code == "ASIGNACION_INVALIDA"


def test_asignar_rechaza_cantidad_no_entera_o_negativa():
    with pytest.raises(AsignacionesError) as exc:
        asignar(10, [{"clave_cliente": "LC657", "cantidad": -1}])
    assert exc.value.code == "ASIGNACION_INVALIDA"


def test_asignar_rechaza_producto_inexistente(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        asignar(999, [{"clave_cliente": "LC657", "cantidad": 1}])
    assert exc.value.code == "PRODUCTO_NO_EXISTE"


def test_asignar_rechaza_cliente_inexistente(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [{"id": 10}, None]  # producto existe, cliente no
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        asignar(10, [{"clave_cliente": "ZZZZZ", "cantidad": 1}])
    assert exc.value.code == "CLIENTE_NO_EXISTE"


def test_asignar_rechaza_sobreasignacion(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [{"id": 10}, {"clave": "LC657"}]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=2)

    with pytest.raises(AsignacionesError) as exc:
        asignar(10, [{"clave_cliente": "LC657", "cantidad": 3}])  # pide 3, solo hay 2
    assert exc.value.code == "STOCK_INSUFICIENTE"
    assert exc.value.status == 409


def test_asignar_exitoso_inserta_asignacion_y_movimiento(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 10},                # producto FOR UPDATE
        {"clave": "LC657"},        # cliente existe
        None,                      # no hay asignación previa para este cliente
    ]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=5)

    resultado = asignar(10, [{"clave_cliente": "lc657", "cantidad": 3}], usuario_id=7)

    assert resultado == {"producto_id": 10, "disponible_restante": 2}
    inserts = [c for c in cursor.execute.call_args_list if "INSERT INTO importacion_asignaciones" in c.args[0]]
    assert len(inserts) == 1
    assert inserts[0].args[1][1] == "LC657"  # clave normalizada a mayúsculas
    assert inserts[0].args[1][2] is None     # mes_objetivo omitido -> NULL
    assert inserts[0].args[1][3] == 0        # proyectado omitido -> default 0
    movimientos = [c for c in cursor.execute.call_args_list if "INSERT INTO importacion_movimientos" in c.args[0]]
    assert movimientos[0].args[1][1] == "RESERVA"
    assert movimientos[0].args[1][2] == -3


def test_asignar_rechaza_cantidad_proyectada_negativa():
    with pytest.raises(AsignacionesError) as exc:
        asignar(10, [{"clave_cliente": "LC657", "cantidad": 1, "cantidad_proyectada": -1}])
    assert exc.value.code == "ASIGNACION_INVALIDA"


def test_asignar_guarda_cantidad_proyectada_provista_en_insert(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 10},                # producto FOR UPDATE
        {"clave": "LC657"},        # cliente existe
        None,                      # no hay asignación previa para este cliente
    ]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=5)

    asignar(10, [{"clave_cliente": "LC657", "cantidad": 3, "cantidad_proyectada": 8}])

    inserts = [c for c in cursor.execute.call_args_list if "INSERT INTO importacion_asignaciones" in c.args[0]]
    assert len(inserts) == 1
    assert inserts[0].args[1][3] == 8  # proyectado se guarda tal cual, no igual a la cantidad (3)


def test_asignar_sobrescribe_cantidad_proyectada_en_update_no_suma(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 10},
        {"clave": "LC657"},
        {"id": 55},  # ya existe una fila de asignación para este producto+cliente
    ]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=5)

    asignar(10, [{"clave_cliente": "LC657", "cantidad": 2, "cantidad_proyectada": 9}])

    updates = [c for c in cursor.execute.call_args_list if "UPDATE importacion_asignaciones" in c.args[0]]
    assert len(updates) == 1
    assert "cantidad_proyectada = %s" in updates[0].args[0]
    assert updates[0].args[1][1] == 9  # sobrescribe, no suma sobre un valor previo


def test_asignar_no_toca_cantidad_proyectada_si_se_omite_en_update(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 10},
        {"clave": "LC657"},
        {"id": 55},  # ya existe una fila de asignación para este producto+cliente
    ]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=5)

    asignar(10, [{"clave_cliente": "LC657", "cantidad": 2}])  # sin cantidad_proyectada

    updates = [c for c in cursor.execute.call_args_list if "UPDATE importacion_asignaciones" in c.args[0]]
    assert len(updates) == 1
    assert "cantidad_proyectada" not in updates[0].args[0]


def test_asignar_a_cliente_ya_asignado_suma_en_lugar_de_duplicar(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 10},
        {"clave": "LC657"},
        {"id": 55},  # ya existe una fila de asignación para este producto+cliente
    ]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=5)

    asignar(10, [{"clave_cliente": "LC657", "cantidad": 2}])

    updates = [c for c in cursor.execute.call_args_list if "UPDATE importacion_asignaciones" in c.args[0]]
    assert len(updates) == 1
    inserts = [c for c in cursor.execute.call_args_list if "INSERT INTO importacion_asignaciones" in c.args[0]]
    assert len(inserts) == 0


def test_venta_sobrante_rechaza_cantidad_invalida():
    with pytest.raises(AsignacionesError) as exc:
        crear_venta_sobrante(10, "LC657", 0)
    assert exc.value.code == "VENTA_INVALIDA"


def test_venta_sobrante_rechaza_sobreventa(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [{"id": 10}, {"clave": "LC657"}]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=1)

    with pytest.raises(AsignacionesError) as exc:
        crear_venta_sobrante(10, "LC657", 2)  # pide 2, solo hay 1 disponible
    assert exc.value.code == "SOBRANTE_INSUFICIENTE"


def test_venta_sobrante_exitosa_queda_pendiente_de_validacion(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 10},                    # producto FOR UPDATE
        {"clave": "LC657"},            # cliente existe
        {"id": 77, "estado": "PENDIENTE_VALIDACION", "numero_pedido_odoo": None},  # SELECT final
    ]
    cursor.lastrowid = 77
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=5)

    resultado = crear_venta_sobrante(10, "LC657", 1)

    assert resultado["estado"] == "PENDIENTE_VALIDACION"


def test_venta_sobrante_con_folio_repetido_devuelve_la_existente_sin_duplicar(mocker):
    cursor = MagicMock()
    venta_existente = {"id": 1, "numero_pedido_odoo": "SO12345", "estado": "VALIDADO"}
    cursor.fetchone.side_effect = [venta_existente]  # el chequeo previo ya la encuentra
    _mock_conn(mocker, cursor)

    resultado = crear_venta_sobrante(10, "LC657", 1, numero_pedido_odoo="SO12345")

    assert resultado == venta_existente
    inserts = [c for c in cursor.execute.call_args_list if c.args[0].startswith("INSERT")]
    assert len(inserts) == 0  # nunca llegó a intentar el INSERT


def test_venta_sobrante_race_condition_en_integrity_error_no_duplica(mocker):
    cursor = MagicMock()
    venta_ganadora = {"id": 1, "numero_pedido_odoo": "SO12345", "estado": "PENDIENTE_VALIDACION"}
    cursor.fetchone.side_effect = [
        None,               # chequeo previo: todavía no existe
        {"id": 10},          # producto FOR UPDATE
        {"clave": "LC657"},  # cliente existe
        venta_ganadora,       # SELECT tras el IntegrityError: otro proceso ganó la carrera
    ]
    cursor.execute.side_effect = [
        None, None, None,  # SELECT folio, SELECT producto FOR UPDATE, SELECT cliente
        # (_disponible_producto está mockeado a nivel de función más abajo, así que no
        # pasa por cursor.execute: no le corresponde una entrada aquí)
        mysql.connector.errors.IntegrityError("Duplicate entry"),  # INSERT falla por la carrera
        None,  # SELECT de recuperación
    ]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service._disponible_producto", return_value=5)

    resultado = crear_venta_sobrante(10, "LC657", 1, numero_pedido_odoo="SO12345")

    assert resultado == venta_ganadora


def _mock_conn_secuencia(mocker, fetchone_side_effect):
    cursor = MagicMock()
    cursor.fetchone.side_effect = fetchone_side_effect
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mocker.patch("services.asignaciones_service.obtener_conexion", return_value=conn)
    return cursor


def test_validar_odoo_venta_no_existe(mocker):
    _mock_conn_secuencia(mocker, [None])
    with pytest.raises(AsignacionesError) as exc:
        validar_venta_odoo(999, "SO1")
    assert exc.value.code == "VENTA_NO_EXISTE"


def test_validar_odoo_rechaza_venta_cancelada(mocker):
    _mock_conn_secuencia(mocker, [{"id": 1, "estado": "CANCELADO", "numero_pedido_odoo": "SO1"}])
    with pytest.raises(AsignacionesError) as exc:
        validar_venta_odoo(1, "SO1")
    assert exc.value.code == "VENTA_YA_CANCELADA"


def test_validar_odoo_no_disponible_no_rompe_la_venta(mocker):
    _mock_conn_secuencia(mocker, [
        {"id": 1, "estado": "PENDIENTE_VALIDACION", "numero_pedido_odoo": "SO1",
         "clave_cliente": "LC657", "cantidad": 2, "importacion_producto_id": 10},
        {"sku_norm": "SKU1"},
    ])
    mocker.patch("services.asignaciones_service.get_odoo_models", return_value=(None, None, "timeout"))

    with pytest.raises(AsignacionesError) as exc:
        validar_venta_odoo(1, "SO1")
    assert exc.value.code == "PEDIDO_ODOO_NO_DISPONIBLE"
    assert exc.value.status == 503


def test_validar_odoo_pedido_no_existe_en_odoo(mocker):
    _mock_conn_secuencia(mocker, [
        {"id": 1, "estado": "PENDIENTE_VALIDACION", "numero_pedido_odoo": "SO1",
         "clave_cliente": "LC657", "cantidad": 2, "importacion_producto_id": 10},
        {"sku_norm": "SKU1"},
    ])
    models = MagicMock()
    models.execute_kw.return_value = []  # sale.order search_read no encuentra nada
    mocker.patch("services.asignaciones_service.get_odoo_models", return_value=(1, models, None))

    with pytest.raises(AsignacionesError) as exc:
        validar_venta_odoo(1, "SO1")
    assert exc.value.code == "PEDIDO_ODOO_NO_EXISTE"


def test_validar_odoo_rechaza_cliente_distinto(mocker):
    _mock_conn_secuencia(mocker, [
        {"id": 1, "estado": "PENDIENTE_VALIDACION", "numero_pedido_odoo": "SO1",
         "clave_cliente": "LC657", "cantidad": 2, "importacion_producto_id": 10},
        {"sku_norm": "SKU1"},
    ])
    models = MagicMock()
    models.execute_kw.side_effect = [
        [{"id": 5, "name": "SO1", "partner_id": [99, "Otro"], "state": "sale", "order_line": []}],
        [{"ref": "MC677"}],  # partner con ref distinto al de la venta
    ]
    mocker.patch("services.asignaciones_service.get_odoo_models", return_value=(1, models, None))

    with pytest.raises(AsignacionesError) as exc:
        validar_venta_odoo(1, "SO1")
    assert exc.value.code == "PEDIDO_ODOO_INVALIDO"


def test_validar_odoo_rechaza_cantidad_insuficiente_en_las_lineas(mocker):
    _mock_conn_secuencia(mocker, [
        {"id": 1, "estado": "PENDIENTE_VALIDACION", "numero_pedido_odoo": "SO1",
         "clave_cliente": "LC657", "cantidad": 2, "importacion_producto_id": 10},
        {"sku_norm": "4271020001004"},
    ])
    models = MagicMock()
    models.execute_kw.side_effect = [
        [{"id": 5, "name": "SO1", "partner_id": [1, "LC657"], "state": "sale", "order_line": [50]}],
        [{"ref": "LC657"}],
        [{"product_id": [200, "Bici"], "product_uom_qty": 1.0}],  # solo 1 unidad, se esperaban 2
        [{"id": 200, "default_code": "427102-0001004"}],
    ]
    mocker.patch("services.asignaciones_service.get_odoo_models", return_value=(1, models, None))

    with pytest.raises(AsignacionesError) as exc:
        validar_venta_odoo(1, "SO1")
    assert exc.value.code == "PEDIDO_ODOO_INVALIDO"


def _models_odoo_ok(mocker):
    models = MagicMock()
    models.execute_kw.side_effect = [
        [{"id": 5, "name": "SO1", "partner_id": [1, "LC657"], "state": "sale", "order_line": [50]}],
        [{"ref": "LC657"}],
        [{"product_id": [200, "Bici"], "product_uom_qty": 2.0}],
        [{"id": 200, "default_code": "427102-0001004"}],
    ]
    mocker.patch("services.asignaciones_service.get_odoo_models", return_value=(1, models, None))
    return models


def test_validar_odoo_exitoso_marca_validado(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        # Fase 1 (lectura)
        {"id": 1, "estado": "PENDIENTE_VALIDACION", "numero_pedido_odoo": "SO1",
         "clave_cliente": "LC657", "cantidad": 2, "importacion_producto_id": 10},
        {"sku_norm": "4271020001004"},
        # Fase 3 (escritura bloqueada)
        {"id": 1, "estado": "PENDIENTE_VALIDACION", "numero_pedido_odoo": "SO1"},  # SELECT ... FOR UPDATE
        {"id": 1, "estado": "VALIDADO", "numero_pedido_odoo": "SO1"},              # SELECT final
    ]
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mocker.patch("services.asignaciones_service.obtener_conexion", return_value=conn)
    _models_odoo_ok(mocker)

    resultado = validar_venta_odoo(1, "SO1")

    assert resultado["estado"] == "VALIDADO"
    # la escritura final se hace sobre la fila bloqueada
    locks = [c for c in cursor.execute.call_args_list if "FOR UPDATE" in c.args[0]]
    assert len(locks) == 1
    assert "importacion_sobrantes_ventas" in locks[0].args[0]
    updates = [c for c in cursor.execute.call_args_list if c.args[0].startswith("UPDATE")]
    assert len(updates) == 1
    assert "SET estado = 'VALIDADO'" in updates[0].args[0]


def test_validar_odoo_persiste_folio_nuevo_y_estado_en_un_solo_update(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 1, "estado": "PENDIENTE_VALIDACION", "numero_pedido_odoo": None,
         "clave_cliente": "LC657", "cantidad": 2, "importacion_producto_id": 10},
        {"sku_norm": "4271020001004"},
        {"id": 1, "estado": "PENDIENTE_VALIDACION", "numero_pedido_odoo": None},
        {"id": 1, "estado": "VALIDADO", "numero_pedido_odoo": "SO1"},
    ]
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mocker.patch("services.asignaciones_service.obtener_conexion", return_value=conn)
    _models_odoo_ok(mocker)

    resultado = validar_venta_odoo(1, "SO1")

    assert resultado["numero_pedido_odoo"] == "SO1"
    updates = [c for c in cursor.execute.call_args_list if c.args[0].startswith("UPDATE")]
    assert len(updates) == 1  # folio y estado en un único UPDATE, misma transacción
    assert "numero_pedido_odoo = %s" in updates[0].args[0]
    assert "estado = 'VALIDADO'" in updates[0].args[0]


def test_validar_odoo_no_persiste_folio_si_la_validacion_falla(mocker):
    """Un folio nuevo nunca debe quedar escrito si Odoo no confirma el pedido."""
    cursor = _mock_conn_secuencia(mocker, [
        {"id": 1, "estado": "PENDIENTE_VALIDACION", "numero_pedido_odoo": None,
         "clave_cliente": "LC657", "cantidad": 2, "importacion_producto_id": 10},
        {"sku_norm": "4271020001004"},
    ])
    models = MagicMock()
    models.execute_kw.side_effect = [
        [{"id": 5, "name": "SO1", "partner_id": [1, "LC657"], "state": "sale", "order_line": [50]}],
        [{"ref": "LC657"}],
        Exception("boom"),  # sale.order.line read falla a mitad de la validación
    ]
    mocker.patch("services.asignaciones_service.get_odoo_models", return_value=(1, models, None))

    with pytest.raises(AsignacionesError) as exc:
        validar_venta_odoo(1, "SO1")
    assert exc.value.code == "PEDIDO_ODOO_NO_DISPONIBLE"
    assert [c for c in cursor.execute.call_args_list if c.args[0].startswith("UPDATE")] == []


def test_validar_odoo_venta_cancelada_durante_el_viaje_a_odoo(mocker):
    """La re-verificación bajo FOR UPDATE evita revivir una venta cancelada mientras
    se consultaba Odoo."""
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 1, "estado": "PENDIENTE_VALIDACION", "numero_pedido_odoo": "SO1",
         "clave_cliente": "LC657", "cantidad": 2, "importacion_producto_id": 10},
        {"sku_norm": "4271020001004"},
        {"id": 1, "estado": "CANCELADO", "numero_pedido_odoo": "SO1"},  # cancelada entre tanto
    ]
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mocker.patch("services.asignaciones_service.obtener_conexion", return_value=conn)
    _models_odoo_ok(mocker)

    with pytest.raises(AsignacionesError) as exc:
        validar_venta_odoo(1, "SO1")
    assert exc.value.code == "VENTA_YA_CANCELADA"
    assert exc.value.status == 409
    assert [c for c in cursor.execute.call_args_list if c.args[0].startswith("UPDATE")] == []


def test_validar_odoo_integrity_error_en_el_update_final_devuelve_409(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 1, "estado": "PENDIENTE_VALIDACION", "numero_pedido_odoo": None,
         "clave_cliente": "LC657", "cantidad": 2, "importacion_producto_id": 10},
        {"sku_norm": "4271020001004"},
        {"id": 1, "estado": "PENDIENTE_VALIDACION", "numero_pedido_odoo": None},
    ]
    cursor.execute.side_effect = [
        None,  # SELECT venta
        None,  # SELECT sku_norm
        None,  # SELECT ... FOR UPDATE
        mysql.connector.errors.IntegrityError("Duplicate entry for key 'uq_pedido_odoo'"),
    ]
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mocker.patch("services.asignaciones_service.obtener_conexion", return_value=conn)
    _models_odoo_ok(mocker)

    with pytest.raises(AsignacionesError) as exc:
        validar_venta_odoo(1, "SO1")
    assert exc.value.code == "PEDIDO_ODOO_YA_ASOCIADO"
    assert exc.value.status == 409
    assert conn.rollback.called
    assert not conn.commit.called


def test_validar_odoo_no_disponible_si_falla_sale_order_line_read(mocker):
    cursor = _mock_conn_secuencia(mocker, [
        {"id": 1, "estado": "PENDIENTE_VALIDACION", "numero_pedido_odoo": "SO1",
         "clave_cliente": "LC657", "cantidad": 2, "importacion_producto_id": 10},
        {"sku_norm": "4271020001004"},
    ])
    models = MagicMock()
    models.execute_kw.side_effect = [
        [{"id": 5, "name": "SO1", "partner_id": [1, "LC657"], "state": "sale", "order_line": [50]}],
        [{"ref": "LC657"}],
        Exception("boom"),  # sale.order.line read falla
    ]
    mocker.patch("services.asignaciones_service.get_odoo_models", return_value=(1, models, None))

    with pytest.raises(AsignacionesError) as exc:
        validar_venta_odoo(1, "SO1")
    assert exc.value.code == "PEDIDO_ODOO_NO_DISPONIBLE"
    assert exc.value.status == 503
    updates_validado = [
        c for c in cursor.execute.call_args_list
        if "SET estado = 'VALIDADO'" in c.args[0]
    ]
    assert len(updates_validado) == 0  # la venta no se tocó


def test_validar_odoo_no_disponible_si_falla_product_product_read(mocker):
    cursor = _mock_conn_secuencia(mocker, [
        {"id": 1, "estado": "PENDIENTE_VALIDACION", "numero_pedido_odoo": "SO1",
         "clave_cliente": "LC657", "cantidad": 2, "importacion_producto_id": 10},
        {"sku_norm": "4271020001004"},
    ])
    models = MagicMock()
    models.execute_kw.side_effect = [
        [{"id": 5, "name": "SO1", "partner_id": [1, "LC657"], "state": "sale", "order_line": [50]}],
        [{"ref": "LC657"}],
        [{"product_id": [200, "Bici"], "product_uom_qty": 2.0}],
        Exception("boom"),  # product.product read falla
    ]
    mocker.patch("services.asignaciones_service.get_odoo_models", return_value=(1, models, None))

    with pytest.raises(AsignacionesError) as exc:
        validar_venta_odoo(1, "SO1")
    assert exc.value.code == "PEDIDO_ODOO_NO_DISPONIBLE"
    assert exc.value.status == 503
    updates_validado = [
        c for c in cursor.execute.call_args_list
        if "SET estado = 'VALIDADO'" in c.args[0]
    ]
    assert len(updates_validado) == 0  # la venta no se tocó


def test_cancelar_venta_no_existe(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        cancelar_venta(999)
    assert exc.value.code == "VENTA_NO_EXISTE"


def test_cancelar_venta_ya_cancelada_no_se_puede_cancelar_dos_veces(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = {
        "id": 1, "estado": "CANCELADO", "importacion_producto_id": 10,
        "clave_cliente": "LC657", "cantidad": 2, "numero_pedido_odoo": "SO1",
    }
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        cancelar_venta(1)
    assert exc.value.code == "VENTA_YA_CANCELADA"


def test_cancelar_venta_restaura_disponibilidad_con_movimiento_positivo(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 1, "estado": "PENDIENTE_VALIDACION", "importacion_producto_id": 10,
         "clave_cliente": "LC657", "cantidad": 2, "numero_pedido_odoo": "SO1"},
        {"id": 1, "estado": "CANCELADO", "importacion_producto_id": 10,
         "clave_cliente": "LC657", "cantidad": 2, "numero_pedido_odoo": "SO1"},
    ]
    _mock_conn(mocker, cursor)

    resultado = cancelar_venta(1, usuario_id=7)

    assert resultado["estado"] == "CANCELADO"
    movimientos = [c for c in cursor.execute.call_args_list if "INSERT INTO importacion_movimientos" in c.args[0]]
    assert movimientos[0].args[1][1] == "CANCELACION"
    assert movimientos[0].args[1][2] == 2  # positivo: restaura las 2 unidades


def test_cancelar_asignacion_no_existe(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        cancelar_asignacion(999)
    assert exc.value.code == "ASIGNACION_NO_EXISTE"
    assert exc.value.status == 404


def test_cancelar_asignacion_ya_cancelada_no_se_puede_cancelar_dos_veces(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = {
        "id": 1, "estado": "CANCELADA", "importacion_producto_id": 10,
        "clave_cliente": "LC657", "cantidad_asignada": 5, "origen": "INICIAL",
    }
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        cancelar_asignacion(1)
    assert exc.value.code == "RESERVA_YA_CERRADA"
    assert exc.value.status == 409


def test_cancelar_asignacion_rechaza_una_reserva_ya_rechazada(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = {
        "id": 1, "estado": "RECHAZADA", "importacion_producto_id": 10,
        "clave_cliente": "LC657", "cantidad_asignada": 5, "origen": "REASIGNACION",
    }
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        cancelar_asignacion(1)
    assert exc.value.code == "RESERVA_YA_CERRADA"


def test_cancelar_asignacion_acepta_pendiente_de_confirmacion(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 1, "estado": "PENDIENTE_CONFIRMACION", "importacion_producto_id": 10,
         "clave_cliente": "LC657", "cantidad_asignada": 6, "origen": "REASIGNACION"},
        {"id": 1, "estado": "CANCELADA", "importacion_producto_id": 10,
         "clave_cliente": "LC657", "cantidad_asignada": 6, "origen": "REASIGNACION"},
    ]
    _mock_conn(mocker, cursor)

    resultado = cancelar_asignacion(1, usuario_id=7)

    assert resultado["estado"] == "CANCELADA"
    movs = [c for c in cursor.execute.call_args_list if "INSERT INTO importacion_movimientos" in c.args[0]]
    assert movs[0].args[1][1] == "LIBERACION" and movs[0].args[1][2] == 6


def test_resolver_reserva_aceptada_confirma_sin_movimiento(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 5, "estado": "PENDIENTE_CONFIRMACION", "importacion_producto_id": 10,
         "clave_cliente": "LC657", "cantidad_asignada": 6, "origen": "REASIGNACION"},
        {"id": 5, "estado": "CONFIRMADA", "importacion_producto_id": 10,
         "clave_cliente": "LC657", "cantidad_asignada": 6},
    ]
    _mock_conn(mocker, cursor)

    res = resolver_reserva(5, "ACEPTADA", usuario_id=7)

    assert res["estado"] == "CONFIRMADA"
    updates = [c for c in cursor.execute.call_args_list if "SET estado = 'CONFIRMADA'" in c.args[0]]
    assert len(updates) == 1
    movs = [c for c in cursor.execute.call_args_list if "INSERT INTO importacion_movimientos" in c.args[0]]
    assert movs == []                     # ACEPTADA no genera movimiento


def test_resolver_reserva_rechazada_libera_a_sobrante(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 5, "estado": "PENDIENTE_CONFIRMACION", "importacion_producto_id": 10,
         "clave_cliente": "LC657", "cantidad_asignada": 6, "origen": "REASIGNACION"},
        {"id": 5, "estado": "RECHAZADA", "importacion_producto_id": 10,
         "clave_cliente": "LC657", "cantidad_asignada": 6},
    ]
    _mock_conn(mocker, cursor)

    res = resolver_reserva(5, "rechazada", usuario_id=7)

    assert res["estado"] == "RECHAZADA"
    movs = [c for c in cursor.execute.call_args_list if "INSERT INTO importacion_movimientos" in c.args[0]]
    assert movs[0].args[1][1] == "RECHAZO_RESERVA"
    assert movs[0].args[1][2] == 6        # positivo: vuelve a disponible


def test_resolver_reserva_solo_sobre_pendiente(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = {
        "id": 5, "estado": "RESERVADA", "importacion_producto_id": 10,
        "clave_cliente": "LC657", "cantidad_asignada": 6, "origen": "INICIAL",
    }
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        resolver_reserva(5, "ACEPTADA")
    assert exc.value.code == "RESERVA_NO_PENDIENTE"
    assert exc.value.status == 409


def test_resolver_reserva_decision_invalida():
    with pytest.raises(AsignacionesError) as exc:
        resolver_reserva(5, "quiza")
    assert exc.value.code == "DECISION_INVALIDA"


def test_resolver_reserva_no_existe(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        resolver_reserva(999, "ACEPTADA")
    assert exc.value.code == "RESERVA_NO_EXISTE"


def test_cancelar_asignacion_restaura_disponibilidad_con_movimiento_positivo(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 1, "estado": "RESERVADA", "importacion_producto_id": 10,
         "clave_cliente": "LC657", "cantidad_asignada": 5},
        {"id": 1, "estado": "CANCELADA", "importacion_producto_id": 10,
         "clave_cliente": "LC657", "cantidad_asignada": 5},
    ]
    _mock_conn(mocker, cursor)

    resultado = cancelar_asignacion(1, usuario_id=7)

    assert resultado["estado"] == "CANCELADA"
    movimientos = [c for c in cursor.execute.call_args_list if "INSERT INTO importacion_movimientos" in c.args[0]]
    assert movimientos[0].args[1][1] == "LIBERACION"
    assert movimientos[0].args[1][2] == 5  # positivo: restaura las 5 unidades asignadas


def test_obtener_detalle_producto_no_existe(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        obtener_detalle_producto(999)
    assert exc.value.code == "PRODUCTO_NO_EXISTE"


def _detalle_producto_mocks(mocker, producto_calculado=None):
    cursor = MagicMock()
    cursor.fetchone.return_value = {
        "id": 10, "importacion_id": 1, "periodo": "2026-2027", "sku_norm": "SKU1",
        "cantidad_embarcada": 10,
    }
    cursor.fetchall.side_effect = [
        [{"id": 1, "clave_cliente": "LC657", "cantidad_asignada": 3}],  # asignaciones
        [{"id": 1, "clave_cliente": "MC677", "cantidad": 1, "estado": "VALIDADO"}],  # ventas
    ]
    _mock_conn(mocker, cursor)
    mocker.patch(
        "services.asignaciones_service.listar_productos",
        return_value=[producto_calculado or {
            "id": 10, "importacion_id": 1, "periodo": "2026-2027", "sku_norm": "SKU1",
            "cantidad_embarcada": 10, "cantidad_asignada": 3, "cantidad_vendida": 1,
            "cantidad_sobrante": 7, "cantidad_disponible": 6,
        }],
    )
    mocker.patch(
        "services.asignaciones_service.recalcular_propuesta",
        return_value=[{"producto_id": 10, "propuesta": [{"clave_cliente": "LC657", "cantidad_sugerida": 3}]}],
    )
    return cursor


def test_obtener_detalle_producto_incluye_los_3_bloques(mocker):
    _detalle_producto_mocks(mocker)

    detalle = obtener_detalle_producto(10)

    assert detalle["producto"]["id"] == 10
    assert len(detalle["asignaciones"]) == 1
    assert len(detalle["sobrantes_ventas"]) == 1
    assert detalle["proyecciones"][0]["clave_cliente"] == "LC657"


def test_obtener_detalle_producto_incluye_las_cantidades_calculadas(mocker):
    _detalle_producto_mocks(mocker)

    detalle = obtener_detalle_producto(10)

    assert detalle["producto"]["cantidad_asignada"] == 3
    assert detalle["producto"]["cantidad_vendida"] == 1
    assert detalle["producto"]["cantidad_sobrante"] == 7
    assert detalle["producto"]["cantidad_disponible"] == 6


def test_resumen_embarque_no_existe(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        resumen_embarque(999)
    assert exc.value.code == "IMPORTACION_NO_EXISTE"


def test_resumen_embarque_suma_kpis_de_todos_los_productos(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = {"id": 1, "referencia": "IMP-001", "nombre": "Test", "estado": "activo"}
    _mock_conn(mocker, cursor)
    mocker.patch(
        "services.asignaciones_service.listar_productos",
        return_value=[
            {"cantidad_embarcada": 10, "cantidad_asignada": 6, "cantidad_sobrante": 4,
             "cantidad_vendida": 1, "cantidad_disponible": 3},
            {"cantidad_embarcada": 5, "cantidad_asignada": 5, "cantidad_sobrante": 0,
             "cantidad_vendida": 0, "cantidad_disponible": 0},
        ],
    )

    resumen = resumen_embarque(1)

    assert resumen["kpis"] == {
        "unidades_embarcadas": 15, "unidades_asignadas": 11, "unidades_sobrantes": 4,
        "unidades_vendidas": 1, "unidades_disponibles": 3,
    }


def test_listar_movimientos_importacion_no_existe(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        listar_movimientos(999)
    assert exc.value.code == "IMPORTACION_NO_EXISTE"


def test_listar_movimientos_devuelve_los_del_embarque(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = {"id": 1}
    cursor.fetchall.return_value = [{"id": 1, "tipo_movimiento": "ENTRADA", "cantidad": 10}]
    _mock_conn(mocker, cursor)

    resultado = listar_movimientos(1)

    assert len(resultado) == 1
    assert resultado[0]["tipo_movimiento"] == "ENTRADA"


def test_caso_10_embarcadas_8_proyectadas_deja_2_sobrantes(mocker):
    _recalc_setup(
        mocker, producto=_PROD_SKU1, disponible=10,
        demanda_mensual={"LC657": {"octubre": 3}, "MC677": {"octubre": 2},
                         "HE420": {"octubre": 1}, "JC539": {"octubre": 2}},  # total 8
    )
    propuestas = recalcular_propuesta(1, "octubre", "octubre")

    total = sum(m["sugerido"] for c in propuestas[0]["propuesta"] for m in c["meses"])
    assert total == 8
    assert propuestas[0]["sobrante_estimado"] == 2


def test_caso_10_embarcadas_15_proyectadas_asigna_10_deja_0_sobrante_5_faltan(mocker):
    _recalc_setup(
        mocker, producto=_PROD_SKU1, disponible=10,
        demanda_mensual={"LC657": {"octubre": 8}, "MC677": {"octubre": 7}},  # total 15 > 10
    )
    propuestas = recalcular_propuesta(1, "octubre", "octubre")

    total_asignado = sum(m["sugerido"] for c in propuestas[0]["propuesta"] for m in c["meses"])
    assert total_asignado == 10
    assert propuestas[0]["sobrante_estimado"] == 0
    faltante = sum(c["faltante_total"] for c in propuestas[0]["propuesta"])
    assert faltante == 5  # 15 proyectado - 10 sugerido


def test_caso_10_embarcadas_10_proyectadas_deja_0_sobrante(mocker):
    _recalc_setup(
        mocker, producto=_PROD_SKU1, disponible=10,
        demanda_mensual={"LC657": {"octubre": 10}},
    )
    propuestas = recalcular_propuesta(1, "octubre", "octubre")
    assert propuestas[0]["sobrante_estimado"] == 0


def test_caso_cliente_sin_prioridad_usa_fallback_999_y_orden_alfabetico(mocker):
    _recalc_setup(
        mocker, producto={**_PROD_SKU1, "cantidad_embarcada": 100}, disponible=100,
        demanda_mensual={
            "ZZ999": {"octubre": 5},   # fuera de PRIORIDAD_CLIENTES -> 999
            "AA111": {"octubre": 5},   # fuera -> 999, alfabéticamente antes que ZZ999
            "LC657": {"octubre": 5},   # prioridad real 1
        },
    )
    propuestas = recalcular_propuesta(1, "octubre", "octubre")

    orden = [c["clave_cliente"] for c in propuestas[0]["propuesta"]]
    assert orden == ["LC657", "AA111", "ZZ999"]
    assert next(c["prioridad"] for c in propuestas[0]["propuesta"] if c["clave_cliente"] == "AA111") == 999


def test_caso_sku_con_y_sin_guiones_se_detecta_como_duplicado(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 1},                                # importacion existe
        {"id": 5, "sku_norm": "4271020001004"},   # YA existe el mismo sku_norm (se cargó antes con guiones)
    ]
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        crear_producto(1, "4271020001004", 10, "2026-2027")  # ahora sin guiones
    assert exc.value.code == "SKU_DUPLICADO"


# --- Pertenencia al embarque de la URL (rutas anidadas) ---------------------------------
# Un producto/venta de OTRO embarque debe verse igual que uno inexistente: 404, sin pistas.

def test_actualizar_producto_de_otro_embarque_se_ve_como_inexistente(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = {"id": 10, "importacion_id": 2, "cantidad_embarcada": 10}
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        actualizar_producto(10, descripcion="x", importacion_id=1)  # el producto es del embarque 2
    assert exc.value.code == "PRODUCTO_NO_EXISTE"
    assert exc.value.status == 404
    assert [c for c in cursor.execute.call_args_list if c.args[0].startswith("UPDATE")] == []


def test_obtener_detalle_producto_de_otro_embarque_se_ve_como_inexistente(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = {"id": 10, "importacion_id": 2, "periodo": "2026-2027"}
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        obtener_detalle_producto(10, importacion_id=1)
    assert exc.value.code == "PRODUCTO_NO_EXISTE"
    assert exc.value.status == 404


def test_asignar_a_producto_de_otro_embarque_se_ve_como_inexistente(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = {"id": 10, "importacion_id": 2}
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        asignar(10, [{"clave_cliente": "LC657", "cantidad": 1}], importacion_id=1)
    assert exc.value.code == "PRODUCTO_NO_EXISTE"
    assert exc.value.status == 404
    assert [c for c in cursor.execute.call_args_list if c.args[0].startswith("INSERT")] == []


def test_venta_sobrante_sobre_producto_de_otro_embarque_se_ve_como_inexistente(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = {"id": 10, "importacion_id": 2}
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        crear_venta_sobrante(10, "LC657", 1, importacion_id=1)
    assert exc.value.code == "PRODUCTO_NO_EXISTE"
    assert exc.value.status == 404
    assert [c for c in cursor.execute.call_args_list if c.args[0].startswith("INSERT")] == []


def test_cancelar_venta_de_otro_embarque_se_ve_como_inexistente(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 1, "estado": "PENDIENTE_VALIDACION", "importacion_producto_id": 10,
         "clave_cliente": "LC657", "cantidad": 2, "numero_pedido_odoo": "SO1"},
        {"importacion_id": 2},  # el producto de la venta pertenece a otro embarque
    ]
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        cancelar_venta(1, importacion_id=1)
    assert exc.value.code == "VENTA_NO_EXISTE"
    assert exc.value.status == 404
    assert [c for c in cursor.execute.call_args_list if c.args[0].startswith("UPDATE")] == []


def test_cancelar_asignacion_de_otro_embarque_se_ve_como_inexistente(mocker):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"id": 1, "estado": "RESERVADA", "importacion_producto_id": 10,
         "clave_cliente": "LC657", "cantidad_asignada": 5},
        {"importacion_id": 2},  # el producto de la asignación pertenece a otro embarque
    ]
    _mock_conn(mocker, cursor)

    with pytest.raises(AsignacionesError) as exc:
        cancelar_asignacion(1, importacion_id=1)
    assert exc.value.code == "ASIGNACION_NO_EXISTE"
    assert exc.value.status == 404
    assert [c for c in cursor.execute.call_args_list if c.args[0].startswith("UPDATE")] == []


def test_validar_odoo_de_venta_de_otro_embarque_se_ve_como_inexistente(mocker):
    cursor = _mock_conn_secuencia(mocker, [
        {"id": 1, "estado": "PENDIENTE_VALIDACION", "numero_pedido_odoo": "SO1",
         "clave_cliente": "LC657", "cantidad": 2, "importacion_producto_id": 10},
        {"importacion_id": 2},  # el producto de la venta pertenece a otro embarque
    ])
    odoo = mocker.patch("services.asignaciones_service.get_odoo_models")

    with pytest.raises(AsignacionesError) as exc:
        validar_venta_odoo(1, "SO1", importacion_id=1)
    assert exc.value.code == "VENTA_NO_EXISTE"
    assert exc.value.status == 404
    assert not odoo.called  # ni siquiera se consulta Odoo
    assert [c for c in cursor.execute.call_args_list if c.args[0].startswith("UPDATE")] == []


# ---------------------------------------------------------------------------
# Importación desde Excel
# ---------------------------------------------------------------------------
import io as _io

from services.asignaciones_service import parsear_excel_productos, importar_productos


def _xlsx(rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = _io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parsear_excel_detecta_headers_y_filas_validas():
    data = _xlsx([
        ["SKU", "CANTIDAD", "DESCRIPCION"],
        ["427102-0001004", 10, "Scott R24"],
        ["427102-0001005", "20", None],
        [None, 5, "sin sku, se ignora"],
    ])
    res = parsear_excel_productos(data)
    assert [f["sku"] for f in res["filas"]] == ["427102-0001004", "427102-0001005"]
    assert res["filas"][0] == {"fila": 2, "sku": "427102-0001004", "cantidad": 10, "descripcion": "Scott R24"}
    assert res["filas"][1]["cantidad"] == 20 and res["filas"][1]["descripcion"] is None
    assert res["errores"] == []


def test_parsear_excel_reporta_cantidad_invalida_sin_abortar():
    data = _xlsx([
        ["SKU", "CANT"],
        ["A-1", "diez"],
        ["A-2", 0],
        ["A-3", 7],
    ])
    res = parsear_excel_productos(data)
    assert [f["sku"] for f in res["filas"]] == ["A-3"]
    assert len(res["errores"]) == 2
    assert res["errores"][0]["sku"] == "A-1" and res["errores"][0]["fila"] == 2
    assert res["errores"][1]["sku"] == "A-2" and "mayor a 0" in res["errores"][1]["motivo"]


def test_parsear_excel_sin_columna_sku_es_400():
    data = _xlsx([["CODIGO", "CANTIDAD"], ["A-1", 5]])
    with pytest.raises(AsignacionesError) as exc:
        parsear_excel_productos(data)
    assert exc.value.code == "EXCEL_SIN_COLUMNAS"
    assert exc.value.status == 400


def test_parsear_excel_archivo_corrupto_es_400():
    with pytest.raises(AsignacionesError) as exc:
        parsear_excel_productos(b"esto no es un xlsx")
    assert exc.value.code == "EXCEL_INVALIDO"
    assert exc.value.status == 400


def test_importar_productos_rechaza_periodo_vacio():
    with pytest.raises(AsignacionesError) as exc:
        importar_productos(1, "  ", [{"fila": 2, "sku": "A", "cantidad": 5}])
    assert exc.value.code == "PERIODO_REQUERIDO"


def test_importar_productos_importacion_no_existe(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    _mock_conn(mocker, cursor)
    with pytest.raises(AsignacionesError) as exc:
        importar_productos(999, "2026-2027", [{"fila": 2, "sku": "A", "cantidad": 5}])
    assert exc.value.code == "IMPORTACION_NO_EXISTE"


def test_importar_productos_inserta_nuevos_y_actualiza_existentes(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = {"id": 1}  # el embarque existe
    cursor.fetchall.return_value = [{"id": 10, "sku_norm": "AA1"}]  # AA-1 ya existe
    _mock_conn(mocker, cursor)
    crear = mocker.patch("services.asignaciones_service.crear_producto",
                         return_value={"id": 99})
    actualizar = mocker.patch("services.asignaciones_service.actualizar_producto")

    res = importar_productos(1, "2026-2027", [
        {"fila": 2, "sku": "AA-1", "cantidad": 12, "descripcion": "existente"},
        {"fila": 3, "sku": "BB-2", "cantidad": 8, "descripcion": None},
    ], usuario_id=7)

    assert res["insertados"] == 1 and res["actualizados"] == 1 and res["total_filas"] == 2
    assert res["errores"] == []
    actualizar.assert_called_once()
    assert actualizar.call_args.kwargs["cantidad_embarcada"] == 12
    assert actualizar.call_args.kwargs["importacion_id"] == 1
    crear.assert_called_once()
    assert crear.call_args.args[1] == "BB-2" and crear.call_args.args[2] == 8


def test_importar_productos_fila_que_falla_va_a_errores_y_el_resto_se_procesa(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = {"id": 1}
    cursor.fetchall.return_value = [{"id": 10, "sku_norm": "AA1"}]
    _mock_conn(mocker, cursor)
    mocker.patch("services.asignaciones_service.actualizar_producto",
                 side_effect=AsignacionesError("AJUSTE_INVALIDO", "No puedes bajar la cantidad", 400))
    crear = mocker.patch("services.asignaciones_service.crear_producto", return_value={"id": 99})

    res = importar_productos(1, "2026-2027", [
        {"fila": 2, "sku": "AA-1", "cantidad": 1},   # falla: por debajo de lo asignado
        {"fila": 3, "sku": "CC-3", "cantidad": 5},   # ok
    ])

    assert res["insertados"] == 1 and res["actualizados"] == 0
    assert res["errores"] == [{"fila": 2, "sku": "AA-1", "motivo": "No puedes bajar la cantidad"}]
    crear.assert_called_once()


def test_importar_productos_mismo_sku_repetido_en_la_hoja_se_actualiza_la_segunda_vez(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = {"id": 1}
    cursor.fetchall.return_value = []  # nada preexistente
    _mock_conn(mocker, cursor)
    crear = mocker.patch("services.asignaciones_service.crear_producto", return_value={"id": 50})
    actualizar = mocker.patch("services.asignaciones_service.actualizar_producto")

    res = importar_productos(1, "2026-2027", [
        {"fila": 2, "sku": "DD-4", "cantidad": 3},
        {"fila": 5, "sku": "DD 4", "cantidad": 9},  # mismo sku_norm que la fila 2
    ])

    assert res["insertados"] == 1 and res["actualizados"] == 1
    crear.assert_called_once()
    actualizar.assert_called_once_with(50, cantidad_embarcada=9, descripcion=None,
                                       usuario_id=None, importacion_id=1)


# ── Vistas consolidadas ───────────────────────────────────────────────────────

def test_resumen_global_arma_kpis_y_totales_por_embarque(mocker):
    from datetime import datetime
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        {"id": 12, "referencia": "IMP-012", "nombre": "Scott", "estado": "activo",
         "n_productos": 2, "n_periodos": 1, "embarcadas": 30, "asignadas": 18,
         "vendidas": 2, "sobrantes": 12, "disponibles": 10,
         "ultima_actividad": datetime(2026, 9, 9, 15, 30)},
        {"id": 7, "referencia": "IMP-007", "nombre": "Megamo", "estado": "cerrado",
         "n_productos": 1, "n_periodos": 1, "embarcadas": 5, "asignadas": 5,
         "vendidas": 0, "sobrantes": 0, "disponibles": 0, "ultima_actividad": None},
    ]
    _mock_conn(mocker, cursor)

    res = resumen_global({})

    assert [e["id"] for e in res["embarques"]] == [12, 7]
    assert res["embarques"][0]["kpis"] == {
        "embarcadas": 30, "asignadas": 18, "sobrantes": 12, "vendidas": 2, "disponibles": 10,
    }
    assert res["embarques"][0]["ultima_actividad"] == "2026-09-09 15:30:00"
    assert res["embarques"][1]["ultima_actividad"] is None
    assert res["totales"] == {
        "embarcadas": 35, "asignadas": 23, "sobrantes": 12, "vendidas": 2,
        "disponibles": 10, "n_embarques": 2,
    }


def test_resumen_global_solo_con_disponible_agrega_having(mocker):
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    _mock_conn(mocker, cursor)

    resumen_global({"solo_con_disponible": "1"})

    sql = cursor.execute.call_args[0][0]
    assert "HAVING disponibles > 0" in sql


def test_listar_productos_global_calcula_sobrante_y_totales(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = {"n": 2}
    cursor.fetchall.return_value = [
        {"importacion_id": 12, "referencia": "IMP-012", "embarque_nombre": "Scott",
         "embarque_estado": "activo", "producto_id": 100, "sku": "AB-1", "sku_norm": "AB1",
         "descripcion": "Bici", "periodo": "2026-2027", "cantidad_embarcada": 20,
         "cantidad_asignada": 15, "cantidad_vendida": 1, "cantidad_disponible": 4},
        {"importacion_id": 7, "referencia": "IMP-007", "embarque_nombre": "Megamo",
         "embarque_estado": "cerrado", "producto_id": 55, "sku": "CD-2", "sku_norm": "CD2",
         "descripcion": None, "periodo": "2026-2027", "cantidad_embarcada": 5,
         "cantidad_asignada": 8, "cantidad_vendida": 0, "cantidad_disponible": 0},
    ]
    _mock_conn(mocker, cursor)

    res = listar_productos_global({}, limite=200, offset=0)

    assert res["total_filas"] == 2
    assert res["productos"][0]["cantidad_sobrante"] == 5   # 20 - 15
    assert res["productos"][1]["cantidad_sobrante"] == 0   # 5 - 8 -> clamp a 0
    assert res["totales"] == {
        "embarcadas": 25, "asignadas": 23, "sobrantes": 5, "vendidas": 1, "disponibles": 4,
    }


def test_listar_productos_global_filtra_sku_normalizado(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = {"n": 0}
    cursor.fetchall.return_value = []
    _mock_conn(mocker, cursor)

    listar_productos_global({"sku": "ab 1"})

    count_params = cursor.execute.call_args_list[0][0][1]
    assert "%AB1%" in count_params


def test_listar_productos_global_solo_disponible_filtra_en_sql(mocker):
    cursor = MagicMock()
    cursor.fetchone.return_value = {"n": 0}
    cursor.fetchall.return_value = []
    _mock_conn(mocker, cursor)

    listar_productos_global({"solo_disponible": "true"})

    count_sql = cursor.execute.call_args_list[0][0][0]
    assert "importacion_movimientos m" in count_sql and "> 0" in count_sql


# ── Reservas por mes: helpers de calendario ───────────────────────────────────

def test_split_periodo_valido_e_invalido():
    assert _split_periodo("2026-2027") == (2026, 2027)
    with pytest.raises(AsignacionesError) as e:
        _split_periodo("2026")
    assert e.value.code == "PERIODO_INVALIDO"


def test_columna_a_fecha_mapea_year1_y_year2():
    assert _columna_a_fecha("2026-2027", "mayo") == _dt.date(2026, 5, 1)
    assert _columna_a_fecha("2026-2027", "diciembre") == _dt.date(2026, 12, 1)
    assert _columna_a_fecha("2026-2027", "enero") == _dt.date(2027, 1, 1)
    assert _columna_a_fecha("2026-2027", "abril") == _dt.date(2027, 4, 1)


def test_fecha_a_columna_ida_y_vuelta():
    for col in MESES_ORDEN:
        assert _fecha_a_columna(_columna_a_fecha("2026-2027", col)) == col


def test_meses_en_ventana_es_subsecuencia_cronologica():
    assert _meses_en_ventana("2026-2027", "octubre", "diciembre") == ["octubre", "noviembre", "diciembre"]
    assert _meses_en_ventana("2026-2027", "2026-11", "2027-02") == ["noviembre", "diciembre", "enero", "febrero"]
    with pytest.raises(AsignacionesError) as e:
        _meses_en_ventana("2026-2027", "diciembre", "octubre")
    assert e.value.code == "VENTANA_INVALIDA"


def test_meses_reasignables_excluye_may_jun_jul_y_la_ventana():
    # ventana empieza en diciembre -> reasignable ago..nov
    assert _meses_reasignables("2026-2027", "diciembre") == ["agosto", "septiembre", "octubre", "noviembre"]
    # ventana empieza en agosto -> nada reasignable (may/jun/jul excluidos)
    assert _meses_reasignables("2026-2027", "agosto") == []


# ── demanda_neta_por_cliente_mensual: deducción Odoo cronológica ──────────────

def test_demanda_mensual_descuenta_odoo_del_mes_mas_antiguo(mocker):
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        {"clave_cliente": "CLI-A", "sku": "BIKE-X",
         "mayo": 2, "junio": 1, "julio": 0, "agosto": 0, "septiembre": 0, "octubre": 5,
         "noviembre": 4, "diciembre": 3, "enero": 0, "febrero": 0, "marzo": 0, "abril": 0},
    ]
    conn = MagicMock(); conn.cursor.return_value = cursor
    mocker.patch("services.proyecciones_service.obtener_conexion", return_value=conn)
    mocker.patch("services.proyecciones_service._get_ordenes_my27",
                 return_value={"CLI-A": {"BIKEX": 7}})

    res = demanda_neta_por_cliente_mensual("2026-2027", ["BIKEX"])

    # Odoo 7 tapa mayo 2 + junio 1 + octubre 4 -> octubre neto 1
    assert res == {"BIKEX": {"CLI-A": {"octubre": 1, "noviembre": 4, "diciembre": 3}}}


def test_demanda_mensual_sin_odoo_devuelve_proyeccion_intacta(mocker):
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        {"clave_cliente": "CLI-C", "sku": "BIKE-X",
         **{m: 0 for m in MESES_ORDEN}, "noviembre": 10},
    ]
    conn = MagicMock(); conn.cursor.return_value = cursor
    mocker.patch("services.proyecciones_service.obtener_conexion", return_value=conn)
    mocker.patch("services.proyecciones_service._get_ordenes_my27", return_value={})

    res = demanda_neta_por_cliente_mensual("2026-2027", ["BIKEX"])
    assert res == {"BIKEX": {"CLI-C": {"noviembre": 10}}}


def test_migrar_esquema_reservas_es_idempotente():
    conn = obtener_conexion()
    if not conn:
        pytest.skip("Sin conexión a BD local para este test")
    try:
        cur = conn.cursor()
        _migrar_esquema_reservas(cur)   # asegura estado final
        conn.commit()
        segunda = _migrar_esquema_reservas(cur)
        conn.commit()
        assert segunda == []            # nada que aplicar la segunda vez
        cur.execute(
            "SELECT COLUMN_TYPE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='importacion_asignaciones' AND COLUMN_NAME='estado'"
        )
        tipo_estado = cur.fetchone()[0]
        assert "RESERVADA" in tipo_estado and "ACTIVA" not in tipo_estado
        cur.execute(
            "SELECT COLUMN_TYPE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='importacion_movimientos' AND COLUMN_NAME='tipo_movimiento'"
        )
        assert "RECHAZO_RESERVA" in cur.fetchone()[0]
        cur.close()
    finally:
        conn.close()
