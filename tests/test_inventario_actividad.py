import unittest
from unittest.mock import MagicMock
from flask import Flask, g
from utils.inventario_actividad import calcular_participacion, registrar_evento


class ActividadTest(unittest.TestCase):
    def test_3_20_15_y_cero(self):
        filas = [{'usuario_id':i,'usuario_nombre':str(i),'total':n}
                 for i,n in ((1,3),(2,20),(3,15))]
        total, lista = calcular_participacion(filas)
        por_id = {r['usuarioId']:r['porcentaje'] for r in lista}
        self.assertEqual(total, 38)
        self.assertEqual([por_id[i] for i in (1,2,3)], [7.89, 52.63, 39.47])
        self.assertNotIn(4, por_id)
        self.assertEqual(len(lista), 3)

    def test_sin_ediciones(self):
        total, usuarios = calcular_participacion([])
        self.assertEqual(total, 0)
        self.assertEqual(usuarios, [])

    def test_cuenta_con_actividad_fuera_del_equipo_admin(self):
        total, usuarios = calcular_participacion([
            {'usuario_id': 7, 'usuario_nombre': 'Natalia', 'total': 1},
            {'usuario_id': 8, 'usuario_nombre': 'Editor histórico', 'total': 2},
            {'usuario_id': 9, 'usuario_nombre': 'Sin movimientos', 'total': 0},
        ])
        self.assertEqual(total, 3)
        self.assertEqual([u['usuarioId'] for u in usuarios], [8, 7])
        self.assertEqual([u['porcentaje'] for u in usuarios], [66.67, 33.33])

    def test_no_suplanta_nombre(self):
        app = Flask(__name__)
        with app.test_request_context('/api/inventario/equipos', method='PUT',
                json={'usuarioRegistro':'Otro', 'nombre':'Laptop', 'contrasena':'secreto'}):
            g.inventario_usuario = {'id':10,'nombre':'Natalia'}
            cursor=MagicMock()
            registrar_evento(cursor,'editar','Equipo',5)
            params = cursor.execute.call_args.args[1]
            self.assertEqual(params[:5],(10,'Natalia','editar','Equipo',5))
            self.assertNotIn('Laptop', params[5])
            self.assertNotIn('secreto',params[5])

    def test_no_permitir_sin_usuario(self):
        app=Flask(__name__)
        with app.test_request_context('/api/inventario/equipos',method='PUT'):
            with self.assertRaises(RuntimeError):
                registrar_evento(MagicMock(),'editar','Equipo',5)

if __name__=='__main__': unittest.main()
