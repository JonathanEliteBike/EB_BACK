from services.proyecciones_service import (
    PRIORIDAD_CLIENTES, _PRIORIDAD_MAP, _norm_sku, obtener_prioridad_clientes,
)


def test_prioridad_clientes_tiene_27_entradas_unicas():
    claves = [clave for _, clave, _ in PRIORIDAD_CLIENTES]
    assert len(claves) == 27
    assert len(set(claves)) == 27


def test_prioridad_map_lc657_es_prioridad_1():
    assert _PRIORIDAD_MAP['LC657'] == (1, 'Víctor Hugo Villanueva Guzman')


def test_norm_sku_quita_guiones_espacios_y_normaliza_mayusculas():
    assert _norm_sku('427102-0001004') == '4271020001004'
    assert _norm_sku(' 286383 - 704 ') == '286383704'
    assert _norm_sku(None) == ''


def test_obtener_prioridad_clientes_devuelve_27_dicts_ordenados():
    resultado = obtener_prioridad_clientes()
    assert len(resultado) == 27
    assert resultado[0] == {"clave": "LC657", "nombre": "Víctor Hugo Villanueva Guzman", "prioridad": 1}
    assert resultado[-1]["clave"] == "JC554"


def test_proyecciones_my27_importa_sin_error():
    # Si el refactor rompió algo (nombre no exportado, import circular), esto falla al importar.
    import routes.proyecciones_my27 as mod
    assert mod.PRIORIDAD_CLIENTES is PRIORIDAD_CLIENTES
    assert mod._PRIORIDAD_MAP is _PRIORIDAD_MAP
