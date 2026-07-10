from unittest.mock import MagicMock, patch
import routes.monitor_odoo as mo
from app import app


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
