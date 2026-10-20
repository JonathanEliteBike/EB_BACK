"""Pruebas aisladas del control de acceso de Inventario IT.

Desde EB_BACK: python -m unittest discover -s tests -p test_inventario_auth.py -v
(La aplicación real necesita Flask, PyJWT, mysql-connector-python y python-dotenv.)
"""
import os
import unittest
from unittest.mock import patch

# Clave únicamente de pruebas, antes de importar el validador.
os.environ["JWT_SECRET"] = "test-only-" + "x" * 60

from flask import Flask, g, jsonify
from utils.inventario_auth import validar_acceso_inventario


class CursorFalso:
    def __init__(self, cuenta):
        self.cuenta = cuenta
        self.query = ""
        self.params = None

    def execute(self, sql, params):
        self.query = sql
        self.params = params

    def fetchone(self):
        return self.cuenta

    def close(self):
        pass


class ConexionFalsa:
    def __init__(self, cuenta):
        self.cursor_falso = CursorFalso(cuenta)

    def cursor(self, dictionary=False):
        assert dictionary
        return self.cursor_falso

    def close(self):
        pass


class SeguridadInventarioTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.before_request(validar_acceso_inventario)

        @app.route("/protegida")
        def protegida():
            return jsonify({"usuario": g.inventario_usuario["nombre"]})

        self.client = app.test_client()
        self.cuenta_admin = {"id": 3, "usuario": "admin", "nombre": "Administración"}

    def test_sin_token_devuelve_401(self):
        respuesta = self.client.get("/protegida")
        self.assertEqual(respuesta.status_code, 401)

    @patch("utils.inventario_auth.verificar_token", return_value=None)
    def test_token_invalido_devuelve_401(self, _verificar):
        respuesta = self.client.get("/protegida", headers={"Authorization": "Bearer invalido"})
        self.assertEqual(respuesta.status_code, 401)

    @patch("utils.inventario_auth.verificar_token", return_value={"id": 3, "rol": 2})
    def test_usuario_distribuidor_devuelve_403(self, _verificar):
        respuesta = self.client.get("/protegida", headers={"Authorization": "Bearer token"})
        self.assertEqual(respuesta.status_code, 403)

    @patch("utils.inventario_auth.verificar_token", return_value={"id": 3, "rol": 1})
    @patch("utils.inventario_auth.obtener_conexion", return_value=ConexionFalsa(None))
    def test_admin_inactivo_devuelve_403(self, _conexion, _verificar):
        respuesta = self.client.get("/protegida", headers={"Authorization": "Bearer token"})
        self.assertEqual(respuesta.status_code, 403)

    @patch("utils.inventario_auth.verificar_token", return_value={"id": 3, "rol": 1})
    @patch("utils.inventario_auth.obtener_conexion", return_value=None)
    def test_base_no_disponible_devuelve_503(self, _conexion, _verificar):
        respuesta = self.client.get("/protegida", headers={"Authorization": "Bearer token"})
        self.assertEqual(respuesta.status_code, 503)

    @patch("utils.inventario_auth.verificar_token", return_value={"id": 3, "rol": 1})
    @patch("utils.inventario_auth.obtener_conexion")
    def test_administrador_activo_puede_entrar(self, obtener_conexion, _verificar):
        obtener_conexion.return_value = ConexionFalsa(self.cuenta_admin)
        respuesta = self.client.get("/protegida", headers={"Authorization": "Bearer token"})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json["usuario"], "Administración")
        consulta = obtener_conexion.return_value.cursor_falso
        self.assertIn("rol_id = 1 AND activo = 1", consulta.query)
        self.assertEqual(consulta.params, (3,))


if __name__ == "__main__":
    unittest.main()
