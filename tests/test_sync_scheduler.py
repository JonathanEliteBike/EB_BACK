"""
El cron diario (services/sync_scheduler.py::_run_sync_diario) llama a
/sync-monitor-odoo y /sincronizar_notas via requests.post sin header
Authorization. Ambos endpoints requieren @requiere_autenticacion desde los
commits 6278118 (16-oct) y 9ce4f71 (19-oct) de sistemas3 -- nadie actualizo
el scheduler al blindar esos endpoints, asi que el cron de L-V 08:30 CDMX
falla todos los dias con "Token de autenticacion requerido." (ver logs de
produccion 2026-10-22, 23 y 26).
"""
from unittest.mock import MagicMock, patch

import services.sync_scheduler as sched


def _mock_response(status_code=200, json_data=None, content_type='application/json'):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {'Content-Type': content_type}
    resp.json.return_value = json_data or {}
    return resp


def test_run_sync_diario_envia_token_de_autorizacion_en_ambas_llamadas():
    with patch('services.sync_scheduler.generar_token_prewarm_interno', return_value='FAKE_TOKEN'):
        with patch('services.sync_scheduler.requests.post') as mock_post:
            mock_post.side_effect = [
                _mock_response(200, {'success': True, 'count': 5}),
                _mock_response(200, {'mensaje': 'ok'}),
            ]
            sched._run_sync_diario()

    assert mock_post.call_count == 2
    for call in mock_post.call_args_list:
        headers = call.kwargs.get('headers') or {}
        assert headers.get('Authorization') == 'Bearer FAKE_TOKEN', (
            f"Llamada sin token: {call}"
        )


def test_run_sync_diario_no_truena_si_no_hay_admin_activo():
    with patch('services.sync_scheduler.generar_token_prewarm_interno', return_value=None):
        with patch('services.sync_scheduler.requests.post') as mock_post:
            sched._run_sync_diario()

    mock_post.assert_not_called()
