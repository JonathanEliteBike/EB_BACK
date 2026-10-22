from unittest.mock import MagicMock, patch
import routes.garantias as gar
from app import app
from utils.jwt_utils import generar_token

_TOKEN_ADMIN_TEST = generar_token(1, 1, 'test.admin', 'Test Admin', None, None, None, None, None)
_AUTH_HEADERS = {'Authorization': f'Bearer {_TOKEN_ADMIN_TEST}'}


def test_inicializar_tablas_con_garantia_piezas_vacia_no_truena():
    """
    inicializar_tablas() abre su cursor principal SIN dictionary=True (linea
    218) pero en la siembra de piezas por defecto lee el resultado como si
    fuera dict (cursor.fetchone()['cnt'], linea 264) -- con un cursor normal
    eso es una tupla, no un dict, y truena con
    "tuple indices must be integers or slices, not str".

    Esto nunca se disparaba en produccion porque garantia_piezas siempre
    tenia datos sembrados; el vaciado masivo de la base de datos
    (2026-10-21) la dejo en 0 filas y expuso el bug al querer re-sembrarla.
    """
    # Simula la diferencia real de mysql-connector: un cursor con
    # dictionary=True devuelve dicts, uno normal devuelve tuplas.
    mock_cursor_dict = MagicMock()
    mock_cursor_dict.fetchone.return_value = {'cnt': 0}
    mock_cursor_tuple = MagicMock()
    mock_cursor_tuple.fetchone.return_value = (0,)

    def fake_cursor(*args, **kwargs):
        return mock_cursor_dict if kwargs.get('dictionary') else mock_cursor_tuple

    mock_connection = MagicMock()
    mock_connection.cursor.side_effect = fake_cursor

    with patch("routes.garantias._conexion_garantias", return_value=mock_connection):
        with app.test_request_context("/garantias/inicializar-tablas", method="POST", headers=_AUTH_HEADERS):
            result = gar.inicializar_tablas()

    body = result[0].get_json() if isinstance(result, tuple) else result.get_json()
    assert 'error' not in body, f"inicializar_tablas devolvio error: {body}"
    assert body.get('ok') is True
