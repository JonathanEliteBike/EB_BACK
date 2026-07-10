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
    Prueba comportamental: sync_monitor_odoo excluye líneas con venta_total <= 0.
    Crea una factura con dos líneas: una con precio_total negativo (debe excluirse)
    y otra válida (debe incluirse), y verifica que solo la válida se inserta en monitor.
    """
    fake_models = MagicMock()

    # Factura con dos líneas
    fake_factura = {
        'id': 1,
        'name': 'INV-2025-NEG-001',
        'invoice_date': '2025-07-15',
        'partner_id': [1, 'Partner Test'],
        'invoice_line_ids': [100, 101],
    }

    # Línea 1: precio negativo (será excluida)
    # Línea 2: precio válido (será incluida)
    fake_lines = [
        {
            'id': 100,
            'product_id': [200, '[PROD001] Ajuste negativo'],
            'price_unit': -100.0,
            'price_total': -200.0,  # <= 0 -> debe excluirse
            'quantity': 2.0,
            'display_type': False,
        },
        {
            'id': 101,
            'product_id': [201, '[PROD002] Bicicleta valida'],
            'price_unit': 150.0,
            'price_total': 300.0,  # > 0 -> debe incluirse
            'quantity': 2.0,
            'display_type': False,
        },
    ]

    # sale.report: mapeo product_id -> categ_id
    fake_sale_report = [
        {'product_id': [200, '[PROD001] Ajuste negativo'], 'categ_id': [10, 'SCOTT / BICICLETA']},
        {'product_id': [201, '[PROD002] Bicicleta valida'], 'categ_id': [11, 'SCOTT / BICICLETA']},
    ]

    # product.category
    fake_categs = [
        {'id': 10, 'complete_name': 'All / SCOTT / BICICLETA'},
        {'id': 11, 'complete_name': 'All / SCOTT / BICICLETA'},
    ]

    # res.partner
    fake_partner = {
        'id': 1,
        'ref': 'CLAVE001',
        'name': 'Partner Test',
        'parent_id': False,
    }

    # Configurar execute_kw para retornar diferentes valores según el modelo
    def side_effect_execute_kw(*args, **kwargs):
        model = args[3] if len(args) > 3 else ''
        if model == 'account.move':
            return [fake_factura]
        elif model == 'account.move.line':
            return fake_lines
        elif model == 'sale.report':
            return fake_sale_report
        elif model == 'product.category':
            return fake_categs
        elif model == 'res.partner':
            return [fake_partner]
        return []

    fake_models.execute_kw.side_effect = side_effect_execute_kw

    # Mock de DB para capturar INSERTs
    mock_cursor = MagicMock()
    mock_connection = MagicMock()
    mock_connection.cursor.return_value = mock_cursor
    mock_connection.is_connected.return_value = True

    insert_calls = []
    def capture_execute(sql, params=None):
        if params and 'INSERT INTO monitor' in sql:
            insert_calls.append(params)

    mock_cursor.execute.side_effect = capture_execute

    with patch("routes.monitor_odoo.get_odoo_models", return_value=(1, fake_models, None)):
        with patch("routes.monitor_odoo.obtener_conexion", return_value=mock_connection):
            with app.test_request_context("/sync-monitor-odoo", method="POST", json={}):
                result = mo.sync_monitor_odoo()

    # Verificar que solo se insertó la línea válida, NO la de precio negativo
    assert len(insert_calls) == 1, f"Esperaba 1 INSERT, obtuvo {len(insert_calls)}"
    # El primer parámetro es numero_factura, el tercero es nombre_producto
    assert 'Bicicleta valida' in str(insert_calls[0][2])


def test_prefijos_excluidos_incluye_fle():
    """
    Prueba comportamental: sync_monitor_odoo excluye líneas cuyo código
    inicia con prefijos en _PREFIJOS_EXCLUIDOS (incluyendo 'FLE' para flete).
    Crea una factura con dos líneas: una con código [FLE001] (debe excluirse)
    y otra válida (debe incluirse), y verifica que solo la válida se inserta.
    """
    fake_models = MagicMock()

    # Factura con dos líneas
    fake_factura = {
        'id': 2,
        'name': 'INV-2025-FLE-001',
        'invoice_date': '2025-07-16',
        'partner_id': [2, 'Partner Test 2'],
        'invoice_line_ids': [200, 201],
    }

    # Línea 1: código FLE (será excluida por prefijo)
    # Línea 2: código válido (será incluida)
    fake_lines = [
        {
            'id': 200,
            'product_id': [300, '[FLE001] Componente especial'],
            'price_unit': 50.0,
            'price_total': 100.0,
            'quantity': 2.0,
            'display_type': False,
        },
        {
            'id': 201,
            'product_id': [301, '[BIKE001] Bicicleta valida'],
            'price_unit': 150.0,
            'price_total': 300.0,
            'quantity': 2.0,
            'display_type': False,
        },
    ]

    # sale.report: ambas en categorías válidas (no SERVICIOS)
    fake_sale_report = [
        {'product_id': [300, '[FLE001] Componente especial'], 'categ_id': [20, 'SCOTT / ACCESORIOS']},
        {'product_id': [301, '[BIKE001] Bicicleta valida'], 'categ_id': [21, 'SCOTT / BICICLETA']},
    ]

    # product.category
    fake_categs = [
        {'id': 20, 'complete_name': 'All / SCOTT / ACCESORIOS'},
        {'id': 21, 'complete_name': 'All / SCOTT / BICICLETA'},
    ]

    # res.partner
    fake_partner = {
        'id': 2,
        'ref': 'CLAVE002',
        'name': 'Partner Test 2',
        'parent_id': False,
    }

    def side_effect_execute_kw(*args, **kwargs):
        model = args[3] if len(args) > 3 else ''
        if model == 'account.move':
            return [fake_factura]
        elif model == 'account.move.line':
            return fake_lines
        elif model == 'sale.report':
            return fake_sale_report
        elif model == 'product.category':
            return fake_categs
        elif model == 'res.partner':
            return [fake_partner]
        return []

    fake_models.execute_kw.side_effect = side_effect_execute_kw

    # Mock de DB
    mock_cursor = MagicMock()
    mock_connection = MagicMock()
    mock_connection.cursor.return_value = mock_cursor
    mock_connection.is_connected.return_value = True

    insert_calls = []
    def capture_execute(sql, params=None):
        if params and 'INSERT INTO monitor' in sql:
            insert_calls.append(params)

    mock_cursor.execute.side_effect = capture_execute

    with patch("routes.monitor_odoo.get_odoo_models", return_value=(1, fake_models, None)):
        with patch("routes.monitor_odoo.obtener_conexion", return_value=mock_connection):
            with app.test_request_context("/sync-monitor-odoo", method="POST", json={}):
                result = mo.sync_monitor_odoo()

    # Solo debe insertarse la línea sin prefijo FLE
    assert len(insert_calls) == 1, f"Esperaba 1 INSERT, obtuvo {len(insert_calls)}"
    # El tercer parámetro es nombre_producto
    assert 'Bicicleta valida' in str(insert_calls[0][2])
