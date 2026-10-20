"""Pruebas aisladas del permiso; no requieren conexión real a MySQL."""
import unittest
from unittest.mock import Mock, patch
from flask import Flask

from utils.inventario_auth import validar_acceso_inventario


class PermisosInventarioTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)

    def llamar(self, token=None, claims=None, cuenta=None, conexion=True):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        cursor = Mock()
        cursor.fetchone.return_value = cuenta
        conn = Mock()
        conn.cursor.return_value = cursor
        with self.app.test_request_context("/api/inventario/equipos", headers=headers):
            with patch("utils.inventario_auth.verificar_token", return_value=claims), patch(
                "utils.inventario_auth.obtener_conexion",
                return_value=conn if conexion else None,
            ):
                resultado = validar_acceso_inventario()
                return resultado, cursor

    def test_sin_token_es_401(self):
        resultado, _ = self.llamar()
        self.assertEqual(resultado[1], 401)

    def test_token_invalido_es_401(self):
        resultado, _ = self.llamar(token="invalido")
        self.assertEqual(resultado[1], 401)

    def test_no_autorizado_es_403(self):
        resultado, cursor = self.llamar(token="token", claims={"id": 42, "rol": 1})
        self.assertEqual(resultado[1], 403)
        consulta = cursor.execute.call_args
        self.assertIn("inventario_usuarios_autorizados", consulta.args[0])
        self.assertEqual(consulta.args[1], (42, 1))

    def test_autorizado_no_importa_rol(self):
        resultado, _ = self.llamar(
            token="token", claims={"id": 21, "rol": 3},
            cuenta={"id": 21, "nombre": "Prueba", "usuario": "prueba"}
        )
        self.assertIsNone(resultado)

    def test_bd_no_disponible_es_503(self):
        resultado, _ = self.llamar(token="token", claims={"id": 21, "rol": 1}, conexion=False)
        self.assertEqual(resultado[1], 503)


if __name__ == "__main__":
    unittest.main()
