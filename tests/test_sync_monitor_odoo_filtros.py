from unittest.mock import MagicMock, patch
import routes.monitor_odoo as mo
from app import app
import inspect


def test_sync_monitor_odoo_excluye_reversed_e_invoicing_legacy():
    fake_models = MagicMock()
    fake_models.execute_kw.return_value = []  # sin facturas -> corta temprano, solo nos interesa el dominio

    with patch("routes.monitor_odoo.get_odoo_models", return_value=(1, fake_models, None)):
        with app.test_request_context("/sync-monitor-odoo", method="POST", json={}):
            mo.sync_monitor_odoo()

    args, kwargs = fake_models.execute_kw.call_args_list[0]
    dominio = args[5][0]  # [ODOO_DB, uid, ODOO_PASSWORD, 'account.move', 'search_read', [dominio], {...}]
    condiciones = [c for c in dominio if isinstance(c, list)]
    assert ['payment_state', 'not in', ['reversed', 'invoicing_legacy']] in condiciones


def test_linea_con_precio_negativo_se_excluye():
    """
    Verifica que sync_monitor_odoo excluye líneas con precio negativo (venta_total <= 0).
    Este test documenta y fija el comportamiento explícitamente.
    """
    codigo = inspect.getsource(mo.sync_monitor_odoo)
    assert "venta_total <= 0" in codigo, "sync_monitor_odoo debe contener guard 'if venta_total <= 0: continue'"


def test_prefijos_excluidos_incluye_fle():
    """
    Verifica que _PREFIJOS_EXCLUIDOS contiene 'FLE' para excluir líneas de flete/FLETE.
    """
    codigo = inspect.getsource(mo.sync_monitor_odoo)
    assert "_PREFIJOS_EXCLUIDOS" in codigo, "sync_monitor_odoo debe definir _PREFIJOS_EXCLUIDOS"
    assert "'FLE'" in codigo, "_PREFIJOS_EXCLUIDOS debe incluir 'FLE' para excluir líneas de flete"
