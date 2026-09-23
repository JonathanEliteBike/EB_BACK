"""Tests del cálculo automático de fechas proyectadas Booking→Almacén
(regla por origen+tipo_producto+vía en importaciones_tiempos_estimados) y de
_estado_actual() (avance de etapa una vez que la anterior ya tiene fecha real).

No golpean MySQL real: el `conn`/cursor que necesita _recalcular_campos() para
consultar la tabla de reglas se mockea con unittest.mock.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from routes.importaciones import (
    _estado_actual,
    _ETAPAS_ORDEN,
    _buscar_regla_tiempos,
    _recalcular_campos,
    _validar_payload_tiempo,
)


def _mock_conn_con_regla(regla: dict | None):
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = regla
    conn.cursor.return_value = cursor
    return conn


# ── _estado_actual(): avanza a la siguiente etapa, no se queda en la última completada ──

def test_estado_actual_pendiente_sin_nada_capturado():
    assert _estado_actual({}) == "Pendiente"


def test_estado_actual_avanza_a_la_siguiente_etapa_tras_completar_booking():
    # Booking ya tiene fecha real -> debe mostrar Lleg. Puerto, no Booking.
    r = {"log_fecha_entrega": "2026-01-01", "log_fecha_booking": "2026-01-17"}
    assert _estado_actual(r) == "Lleg. Puerto"


def test_estado_actual_usa_la_etapa_mas_avanzada_capturada():
    r = {
        "log_fecha_entrega": "2026-01-01",
        "log_fecha_booking": "2026-01-17",
        "imp_llegada_contenedor_puerto": "2026-02-08",
    }
    assert _estado_actual(r) == "Destino"


def test_estado_actual_liberado_cuando_todo_esta_completo():
    r = {campo: "2026-01-01" for _, campo in _ETAPAS_ORDEN}
    assert _estado_actual(r) == "Liberado"


def test_estado_actual_ignora_rec_recepcion_odoo():
    # rec_recepcion_odoo no es parte de la secuencia de etapas -- no debe alterar el resultado.
    r = {"log_fecha_entrega": "2026-01-01", "rec_recepcion_odoo": "2026-01-05"}
    assert _estado_actual(r) == "Booking"


# ── _buscar_regla_tiempos() ──────────────────────────────────────────────────

def test_buscar_regla_tiempos_sin_conexion_devuelve_none():
    assert _buscar_regla_tiempos(None, "TAIWAN", "Bicicleta", "MARITIMO") is None


def test_buscar_regla_tiempos_devuelve_lo_que_da_el_cursor():
    regla = {
        "dias_hasta_booking": 15, "dias_booking_a_puerto": 23,
        "dias_puerto_a_destino": 15, "dias_destino_a_almacen": 2,
    }
    conn = _mock_conn_con_regla(regla)
    assert _buscar_regla_tiempos(conn, "TAIWAN", "Bicicleta", "MARITIMO") == regla


# ── _recalcular_campos(): cadena de fechas proyectadas ───────────────────────

def test_recalcular_campos_calcula_cadena_completa_con_regla():
    regla = {
        "dias_hasta_booking": 15, "dias_booking_a_puerto": 23,
        "dias_puerto_a_destino": 15, "dias_destino_a_almacen": 2,
    }
    conn = _mock_conn_con_regla(regla)
    data = {
        "log_fecha_entrega": "2026-01-01",
        "log_origen": "TAIWAN",
        "log_tipo_productos": "Bicicleta",
        "via_transporte": "MARITIMO",
    }
    out = _recalcular_campos(data, conn)
    assert out["log_fecha_booking_prog"] == "2026-01-16"
    assert out["imp_llegada_contenedor_prog"] == "2026-02-08"
    assert out["des_fecha_cruce_prog"] == "2026-02-23"
    assert out["des_fecha_entrega_almacen_prog"] == "2026-02-25"
    assert out["tiempos_estimados_faltantes"] is False


def test_recalcular_campos_sin_regla_marca_alerta_sin_tocar_las_fechas():
    conn = _mock_conn_con_regla(None)
    data = {
        "log_fecha_entrega": "2026-01-01",
        "log_origen": "VIETNAM",
        "log_tipo_productos": "Cascos",
        "via_transporte": "MARITIMO",
    }
    out = _recalcular_campos(data, conn)
    # No hay regla -> no se agregan/sobre-escriben las 4 fechas (si no venían
    # en `data`, siguen sin venir; ver el siguiente test para el caso en que
    # un embarque viejo ya las traía capturadas a mano).
    assert "log_fecha_booking_prog" not in out
    assert "imp_llegada_contenedor_prog" not in out
    assert "des_fecha_cruce_prog" not in out
    assert "des_fecha_entrega_almacen_prog" not in out
    assert out["tiempos_estimados_faltantes"] is True


def test_recalcular_campos_sin_regla_no_borra_fecha_capturada_a_mano():
    # Embarque de antes de que existiera el cálculo automático: alguien ya
    # había capturado Booking (Proyectado) a mano. Si la combinación
    # origen+producto+vía no matchea ninguna regla, esa fecha NO debe
    # perderse -- este es el bug real que se detectó en producción
    # (2026-09-23): guardar cualquier otro campo del embarque la borraba.
    conn = _mock_conn_con_regla(None)
    data = {
        "log_fecha_entrega": "2026-01-01",
        "log_origen": "TAIWAN",
        "log_tipo_productos": "BICICLETAS SPARK Y FOIL Y ADDICT RC",
        "via_transporte": "MARITIMO",
        "log_fecha_booking_prog": "2026-01-20",
    }
    out = _recalcular_campos(data, conn)
    assert out["log_fecha_booking_prog"] == "2026-01-20"
    assert out["tiempos_estimados_faltantes"] is True


def test_recalcular_campos_sin_entrega_no_toca_las_fechas_proyectadas():
    conn = _mock_conn_con_regla({
        "dias_hasta_booking": 15, "dias_booking_a_puerto": 23,
        "dias_puerto_a_destino": 15, "dias_destino_a_almacen": 2,
    })
    data = {
        "log_origen": "TAIWAN",
        "log_tipo_productos": "Bicicleta",
        "via_transporte": "MARITIMO",
        "log_fecha_booking_prog": "2026-05-01",  # valor previo en edición (merged)
    }
    out = _recalcular_campos(data, conn)
    # Sin Entrega no hay base para calcular -- se conserva lo que ya traía `data`.
    assert out["log_fecha_booking_prog"] == "2026-05-01"


# ── _validar_payload_tiempo() ─────────────────────────────────────────────────

def _payload_valido(**overrides):
    base = {
        "origen": "TAIWAN", "tipo_producto": "Bicicleta", "via_transporte": "MARITIMO",
        "dias_hasta_booking": 15, "dias_booking_a_puerto": 23,
        "dias_puerto_a_destino": 15, "dias_destino_a_almacen": 2,
    }
    base.update(overrides)
    return base


def test_validar_payload_tiempo_ok():
    assert _validar_payload_tiempo(_payload_valido()) is None


def test_validar_payload_tiempo_falta_origen():
    assert _validar_payload_tiempo(_payload_valido(origen="")) is not None


def test_validar_payload_tiempo_dias_negativos():
    assert _validar_payload_tiempo(_payload_valido(dias_hasta_booking=-1)) is not None


def test_validar_payload_tiempo_dias_no_numericos():
    assert _validar_payload_tiempo(_payload_valido(dias_hasta_booking="quince")) is not None


def test_validar_payload_tiempo_bicicleta_electrica_solo_espana_aereo():
    # Combinación válida: España + Aéreo.
    assert _validar_payload_tiempo(_payload_valido(
        tipo_producto="Bicicleta eléctrica", origen="ESPAÑA", via_transporte="AEREO",
    )) is None
    # Origen distinto de España -> rechazado.
    assert _validar_payload_tiempo(_payload_valido(
        tipo_producto="Bicicleta eléctrica", origen="TAIWAN", via_transporte="AEREO",
    )) is not None
    # Vía marítima -> rechazado.
    assert _validar_payload_tiempo(_payload_valido(
        tipo_producto="Bicicleta eléctrica", origen="ESPAÑA", via_transporte="MARITIMO",
    )) is not None
