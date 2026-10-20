"""Pruebas unitarias de selección endpoint + método + X-Ruta-Interna."""

import unittest
from unittest.mock import patch

from services.permisos_internos_service import PermisosInternosService
from services.contexto_permisos_internos import redactar_montos


class _Cursor:
    def __init__(self, reglas):
        self.reglas = reglas

    def execute(self, *_args, **_kwargs):
        pass

    def fetchall(self):
        return self.reglas

    def close(self):
        pass


class _Conexion:
    def __init__(self, reglas):
        self.reglas = reglas

    def cursor(self, **_kwargs):
        return _Cursor(self.reglas)

    def close(self):
        pass


def _regla(ruta, metodo, modulo, modulo_ruta, accion):
    return {
        'ruta_patron': ruta,
        'modulo_identificador': modulo,
        'modulo_ruta': modulo_ruta,
        'accion_identificador': accion,
    }


class PermisosInternosEndpointContextoTests(unittest.TestCase):
    def _validar(self, reglas, ruta, metodo, contexto, permitidos):
        def tiene_permiso(_usuario_id, modulo, accion):
            return (modulo, accion) in permitidos

        with patch('services.permisos_internos_service.obtener_conexion', return_value=_Conexion(reglas)), \
             patch.object(PermisosInternosService, 'validar_permiso_usuario', side_effect=tiene_permiso):
            return PermisosInternosService.validar_permiso_endpoint(4, ruta, metodo, contexto)

    def test_acciones_exigen_ver_y_su_capacidad(self):
        casos = [
            ('GET', '/lectura', 'ver'),
            ('POST', '/crear', 'crear'),
            ('PUT', '/editar', 'editar'),
            ('PATCH', '/editar-parcial', 'editar'),
            ('DELETE', '/eliminar', 'eliminar'),
        ]
        for metodo, ruta, accion in casos:
            with self.subTest(metodo=metodo):
                reglas = [_regla(ruta, metodo, 'modulo_prueba', '/prueba', accion)]
                permitidos = {('modulo_prueba', 'ver'), ('modulo_prueba', accion)}
                self.assertTrue(self._validar(reglas, ruta, metodo, '/prueba', permitidos))

                if accion == 'ver':
                    self.assertFalse(self._validar(reglas, ruta, metodo, '/prueba', set()))
                else:
                    self.assertFalse(self._validar(
                        reglas, ruta, metodo, '/prueba', {('modulo_prueba', 'ver')}
                    ))

    def test_endpoint_compartido_se_resuelve_por_contexto(self):
        reglas = [
            _regla('/monitor_odoo', 'GET', 'monitor', '/monitor', 'ver'),
            _regla('/monitor_odoo', 'GET', 'caratula_evacs', '/caratula-evacs', 'ver'),
        ]
        permiso_evacs = {('caratula_evacs', 'ver')}
        self.assertTrue(self._validar(reglas, '/monitor_odoo', 'GET', '/caratula-evacs', permiso_evacs))
        self.assertFalse(self._validar(reglas, '/monitor_odoo', 'GET', '/monitor', permiso_evacs))
        self.assertFalse(self._validar(reglas, '/monitor_odoo', 'GET', None, permiso_evacs))

    def test_regla_de_caratulas_no_autoriza_el_contexto_evacs(self):
        reglas = [
            _regla('/clientes_a', 'GET', 'caratulas', '/caratulas', 'ver'),
            _regla('/clientes_a', 'GET', 'caratula_evacs', '/caratula-evacs', 'ver'),
        ]
        permiso_caratulas = {('caratulas', 'ver')}
        self.assertFalse(self._validar(
            reglas, '/clientes_a', 'GET', '/caratula-evacs', permiso_caratulas
        ))

    def test_flujo_redacta_columnas_contables_sin_alterar_campos_publicos(self):
        respuesta = redactar_montos({
            'col_proyectado': 100,
            'col_real': 125,
            'col_diferencia': 25,
            'concepto': 'Ventas',
            'precio_publico': 999,
        }, 'dashboard_flujo_bp')
        self.assertIsNone(respuesta['col_proyectado'])
        self.assertIsNone(respuesta['col_real'])
        self.assertIsNone(respuesta['col_diferencia'])
        self.assertEqual(respuesta['concepto'], 'Ventas')
        self.assertEqual(respuesta['precio_publico'], 999)

    def test_metas_redacta_compromisos_y_conserva_un_cero_real_fuera_de_politica(self):
        respuesta = redactar_montos({
            'nivel': 'Oro',
            'compromiso_scott': 0,
            'compromiso_syncros': 100,
            'compromiso_apparel': 200,
            'compromiso_vittoria': 300,
            'clientes_activos': 0,
        }, 'metas')
        self.assertIsNone(respuesta['compromiso_scott'])
        self.assertIsNone(respuesta['compromiso_syncros'])
        self.assertIsNone(respuesta['compromiso_apparel'])
        self.assertIsNone(respuesta['compromiso_vittoria'])
        self.assertEqual(respuesta['clientes_activos'], 0)

    def test_caratulas_redacta_resumen_my27_y_detalle_por_cliente(self):
        respuesta = redactar_montos({
            'evac_a': {
                'meta': 1000,
                'acumulado_general': 500,
                'desglose': {'scott': 300, 'apparel': 200},
            },
            'cliente': {
                'nombre_cliente': 'Distribuidor',
                'compromiso_scott': 600,
                'avance_global_scott': 400,
            },
            'factura': {'venta_total': 250, 'cantidad': 2},
        }, 'caratulas')
        self.assertIsNone(respuesta['evac_a']['meta'])
        self.assertIsNone(respuesta['evac_a']['acumulado_general'])
        self.assertIsNone(respuesta['evac_a']['desglose']['scott'])
        self.assertIsNone(respuesta['evac_a']['desglose']['apparel'])
        self.assertIsNone(respuesta['cliente']['compromiso_scott'])
        self.assertIsNone(respuesta['cliente']['avance_global_scott'])
        self.assertEqual(respuesta['cliente']['nombre_cliente'], 'Distribuidor')
        self.assertIsNone(respuesta['factura']['venta_total'])
        self.assertEqual(respuesta['factura']['cantidad'], 2)


if __name__ == '__main__':
    unittest.main()
