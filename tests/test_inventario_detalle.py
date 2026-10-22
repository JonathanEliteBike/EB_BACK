"""Ejecutar desde EB_BACK con: python -m unittest tests.test_inventario_detalle"""
import json
import unittest
from datetime import datetime
from unittest.mock import Mock
from flask import Flask, g
from utils.inventario_actividad import (
    capturar_estado, construir_cambios, leer_detalle_evento, registrar_evento,
)


class DetalleBitacoraTest(unittest.TestCase):
    def test_diferencias_de_valores_reales(self):
        cambios = construir_cambios(
            {'ubicacion': 'Almacén', 'estado': 'Disponible'},
            {'ubicacion': 'Sistemas', 'estado': 'Disponible'},
        )
        self.assertEqual(cambios, [{'campo': 'ubicacion', 'antes': 'Almacén', 'despues': 'Sistemas'}])

    def test_fecha_convertida_y_lista_blanca(self):
        cursor = Mock()
        cursor.fetchone.return_value = {
            'numero_inventario': 'EB-123',
            'empresa': 'ELITE BIKE',
            'estado': 'Disponible',
            'fecha_firma': datetime(2026, 10, 19, 10, 30),
        }
        estado = capturar_estado(cursor, 'Equipo', 12)
        self.assertEqual(estado['numero_inventario'], 'EB-123')
        self.assertNotIn('password', estado)
        self.assertNotIn('fecha_firma', estado)
        self.assertIn('WHERE id = %s', cursor.execute.call_args.args[0])
        self.assertEqual(cursor.execute.call_args.args[1], (12,))

    def test_evento_de_edicion_usa_jwt_y_estado_de_bd(self):
        app = Flask(__name__)
        with app.test_request_context('/api/inventario/equipos/11', method='PUT',
                                      json={'ubicacion': 'Sistemas', 'password': 'MUY-SECRETA'}):
            g.inventario_usuario = {'id': 3, 'nombre': 'Natalia'}
            cursor = Mock()
            cursor.fetchone.return_value = {'numero_inventario': 'EB-123',
                                            'ubicacion': 'Sistemas'}
            registrar_evento(cursor, 'editar', 'Equipo', 11,
                             anterior={'numero_inventario': 'EB-123', 'ubicacion': 'Almacén'})
            consulta, argumentos = cursor.execute.call_args.args
            self.assertIn('INSERT INTO inventario_bitacora', consulta)
            self.assertEqual(argumentos[:5], (3, 'Natalia', 'editar', 'Equipo', 11))
            info = json.loads(argumentos[5])
            self.assertEqual(info['cambios'], [
                {'campo': 'ubicacion', 'antes': 'Almacén', 'despues': 'Sistemas'}])
            self.assertNotIn('MUY-SECRETA', argumentos[5])

    def test_evento_antiguo_sin_valores(self):
        info = leer_detalle_evento('{"camposEnviados": ["ubicacion"]}')
        self.assertFalse(info['detalleDisponible'])
        self.assertEqual(info['cambios'], [])

    def test_evento_invalido_sin_error(self):
        self.assertFalse(leer_detalle_evento('no es json')['detalleDisponible'])


if __name__ == '__main__':
    unittest.main()
