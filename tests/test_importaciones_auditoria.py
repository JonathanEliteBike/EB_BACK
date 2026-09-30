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
