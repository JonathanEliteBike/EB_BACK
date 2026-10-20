"""Comprobaciones estáticas sin Flask/MySQL instalados; complementan las pruebas integrales."""
import ast
import pathlib
import unittest

RUTA = pathlib.Path(__file__).resolve().parents[1] / 'routes' / 'auditorias.py'
CODIGO = RUTA.read_text(encoding='utf-8')
ARBOL = ast.parse(CODIGO)
FUNCIONES = {
    nodo.name: ast.get_source_segment(CODIGO, nodo) or ''
    for nodo in ARBOL.body if isinstance(nodo, ast.FunctionDef)
}

class SeguridadEtapa5(unittest.TestCase):
    def test_creacion_desde_sesion(self):
        s = FUNCIONES['crear_auditoria']
        self.assertIn("g.inventario_usuario['nombre']", s)
        self.assertNotIn('data.get("auditorResponsable")', s)
        self.assertNotIn('data.get("usuarioRegistro")', s)

    def test_edicion_no_reasigna_auditor(self):
        s = FUNCIONES['actualizar_auditoria']
        self.assertNotIn('data.get("auditorResponsable")', s)
        self.assertIn('auditoria["auditor_responsable"]', s)

    def test_revision_identifica_usuario(self):
        s = FUNCIONES['revisar_equipo']
        self.assertIn("g.inventario_usuario['nombre']", s)
        self.assertNotIn('data.get("revisadoPor")', s)

    def test_eliminacion_bloqueada_en_backend(self):
        s = FUNCIONES['eliminar_auditoria']
        self.assertIn('405', s)
        self.assertNotIn('obtener_conexion', s)
        self.assertNotIn('DELETE FROM', s)

    def test_cancelacion_con_motivo_y_bitacora(self):
        s = FUNCIONES['cancelar_auditoria']
        self.assertIn('if not motivo:', s)
        self.assertIn("registrar_evento(cursor, 'cancelar'", s)
        self.assertIn('conexion.commit()', s)

if __name__ == '__main__':
    unittest.main()
