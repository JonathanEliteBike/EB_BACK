# tests/test_clientes_prioridad.py
from flask import Flask

from routes.clientes import clientes_bp


def _cliente_test():
    app = Flask(__name__)
    app.register_blueprint(clientes_bp)
    return app.test_client()


def test_get_clientes_prioridad_devuelve_27_entradas_ordenadas():
    client = _cliente_test()
    resp = client.get('/clientes/prioridad')
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 27
    assert data[0] == {"clave": "LC657", "nombre": "Víctor Hugo Villanueva Guzman", "prioridad": 1}
    assert all({"clave", "nombre", "prioridad"} == set(row.keys()) for row in data)


def test_get_clientes_prioridad_no_requiere_autenticacion():
    client = _cliente_test()
    resp = client.get('/clientes/prioridad')  # sin header Authorization
    assert resp.status_code == 200
