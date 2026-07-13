# tests/test_cierre_temporada.py
from unittest.mock import MagicMock, patch

from db_conexion import obtener_conexion
from routes.temporadas import cerrar_temporada_completa, abrir_temporada
from app import create_app


def test_dry_run_no_escribe_nada():
    conn = obtener_conexion()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT COUNT(*) AS n FROM previo_historico WHERE temporada = '2025-2026'")
    antes = cur.fetchone()['n']

    resultado = cerrar_temporada_completa('2025-2026', dry_run=True)

    cur.execute("SELECT COUNT(*) AS n FROM previo_historico WHERE temporada = '2025-2026'")
    despues = cur.fetchone()['n']

    assert despues == antes  # dry_run no persiste nada
    assert resultado['clientes_procesados'] > 0
    assert len(resultado['preview']) <= 3
    cur.close(); conn.close()


def test_cerrar_temporada_completa_nunca_escribe_previo_en_vivo():
    """
    Regresion: cerrar_temporada_completa NUNCA debe escribir en `previo` (la
    tabla en vivo) -- ese fue el bug real detectado dos veces en la sesion
    de cierre de MY26: el cierre masivo sobreescribia el avance en vivo de la
    temporada actual porque reusaba _recalcular_previo_clave_cierre (que si
    escribe previo -- correcto solo para el cierre INDIVIDUAL via
    /cerrar-temporada). Ahora usa _calcular_valores_previo_clave, una funcion
    pura de solo lectura, y solo escribe en previo_historico.

    Se fija este invariante a nivel de codigo fuente (en vez de mockear toda
    la cadena de cursores) porque es la forma mas directa y confiable de
    garantizar que no se reintroduzca ni un "UPDATE previo SET" ni una
    llamada a _recalcular_previo_clave_cierre dentro de esta funcion.
    """
    import inspect
    from routes import temporadas as temporadas_module

    codigo = inspect.getsource(temporadas_module.cerrar_temporada_completa)
    assert 'UPDATE previo SET' not in codigo
    # Busca la SINTAXIS DE LLAMADA (con parentesis pegado), no la mencion en
    # prosa dentro del propio docstring de la funcion (que si nombra a
    # _recalcular_previo_clave_cierre para explicar que ya no se usa).
    assert '_recalcular_previo_clave_cierre(' not in codigo


def test_ignora_f_inicio_ya_rodado_a_temporada_siguiente():
    """
    Regresion: clientes.f_inicio NO debe usarse para acotar el cierre de una
    temporada pasada. Ese campo refleja el inicio de la temporada ACTUALMENTE
    abierta y puede ya apuntar al futuro (p. ej. '2026-07-01' mientras se
    cierra '2025-2026') si el rollover a la temporada siguiente ya ocurrio por
    cualquier via antes de correr el cierre formal. Si el codigo usara
    f_inicio tal cual, el rango de fecha_factura quedaria invertido
    (inicio > fin) y el recalculo sumaria 0 para todos los clientes -- error
    real detectado al correr el checkpoint 2.2 del plan contra datos locales.
    """
    conn = obtener_conexion()
    cur = conn.cursor(dictionary=True)
    cur_w = conn.cursor()

    cur.execute(
        "SELECT clave, f_inicio, dia_inicio_temporada FROM clientes "
        "WHERE clave IS NOT NULL AND clave <> '' LIMIT 1"
    )
    cliente = cur.fetchone()
    clave = cliente['clave']
    backup_f_inicio = cliente['f_inicio']
    backup_dia = cliente['dia_inicio_temporada']

    try:
        # Simula el rollover ya ocurrido: f_inicio apunta a MY27, no a MY26.
        cur_w.execute(
            "UPDATE clientes SET f_inicio = '2026-07-01', dia_inicio_temporada = NULL WHERE clave = %s",
            (clave,)
        )
        conn.commit()

        resultado = cerrar_temporada_completa('2025-2026', dry_run=True)

        fila = next((p for p in resultado['preview'] if p['clave'] == clave), None)
        # No aseguramos que este cliente caiga en el preview de 3; lo que
        # importa es que el cierre completo no haya sumado 0 para todos por
        # culpa del rango invertido.
        assert resultado['clientes_procesados'] > 0
        montos_no_todos_cero = any(
            float(p['acumulado_anticipado']) != 0 for p in resultado['preview']
        )
        assert montos_no_todos_cero, (
            "Todos los montos del preview dieron 0 -- probable regresion al "
            "rango de fechas invertido causado por clientes.f_inicio ya rodado"
        )
    finally:
        cur_w.execute(
            "UPDATE clientes SET f_inicio = %s, dia_inicio_temporada = %s WHERE clave = %s",
            (backup_f_inicio, backup_dia, clave)
        )
        conn.commit()
        cur.close(); cur_w.close(); conn.close()


def test_clientes_ya_cerrados_no_se_tocan():
    """
    Clientes con temporada_cerrada = 1 (p. ej. HA433, cerrado individualmente
    con un corte anticipado distinto al fin de temporada estándar) deben
    quedar completamente fuera del cierre masivo: no se recalcula su previo
    y no cuentan como 'procesados'.
    """
    conn = obtener_conexion()
    cur = conn.cursor(dictionary=True)

    cur.execute("SELECT COUNT(*) AS n FROM clientes WHERE temporada_cerrada = 1")
    n_cerrados = cur.fetchone()['n']
    assert n_cerrados > 0, "Se esperaba al menos un cliente ya cerrado en la BD local para esta prueba"

    cur.execute("""
        SELECT clave, acumulado_anticipado, avance_global_scott,
               avance_global_apparel_syncros_vittoria
        FROM previo WHERE clave = 'HA433'
    """)
    previo_antes = cur.fetchone()

    resultado = cerrar_temporada_completa('2025-2026', dry_run=True)

    cur.execute("""
        SELECT clave, acumulado_anticipado, avance_global_scott,
               avance_global_apparel_syncros_vittoria
        FROM previo WHERE clave = 'HA433'
    """)
    previo_despues = cur.fetchone()

    assert resultado['clientes_omitidos_ya_cerrados'] == n_cerrados
    # HA433 nunca debe aparecer en el preview de clientes procesados
    assert all(fila['clave'] != 'HA433' for fila in resultado['preview'])
    # Su previo debe permanecer exactamente igual (no fue recalculado)
    assert previo_antes == previo_despues

    cur.close(); conn.close()


def test_no_reejecuta_cierre_real_de_temporada_ya_cerrada():
    """
    Si la temporada ya tiene estado='cerrada' (p. ej. '2025-2026', cerrada al
    abrir MY27), un cierre real (dry_run=False) debe rechazarse con
    ValueError: abrir_temporada() resetea temporada_cerrada=0 en TODOS los
    clientes, por lo que el chequeo de "clientes ya cerrados" (linea 74-78)
    ya no evita un re-cierre accidental -- sin este guard, un segundo cierre
    real insertaria un set de filas duplicado/incorrecto en previo_historico
    (que no tiene UNIQUE en (temporada, clave)), corrompiendo el snapshot
    congelado original.

    Se mockea la conexion (en vez de reusar el patron de BD real del resto
    de este archivo) porque este caso puntual ejercita dry_run=False: si el
    guard tuviera un bug, correrlo contra la BD viva insertaria una fila
    corrupta en previo_historico de produccion -- exactamente el dano que
    esta guarda existe para evitar. Se verifica ademas que el cursor "plano"
    (el que ejecuta el INSERT hacia previo_historico) nunca es invocado.
    """
    mock_cur_dict = MagicMock()
    mock_cur_dict.fetchone.return_value = {
        'fecha_inicio': '2025-07-01', 'fecha_fin': '2026-06-30', 'estado': 'cerrada'
    }
    mock_cur = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.side_effect = [mock_cur_dict, mock_cur]

    with patch('routes.temporadas.obtener_conexion', return_value=mock_conn):
        try:
            cerrar_temporada_completa('2025-2026', dry_run=False)
            assert False, "Se esperaba ValueError al re-cerrar una temporada ya cerrada"
        except ValueError as e:
            assert 'cerrada' in str(e)

    for call in mock_cur.execute.call_args_list:
        sql = call.args[0] if call.args else ''
        assert 'INSERT INTO previo_historico' not in sql
    mock_conn.commit.assert_not_called()


def test_dry_run_permitido_sobre_temporada_ya_cerrada():
    """
    dry_run=True sigue permitido aunque la temporada este marcada como
    'cerrada': el guard solo bloquea la ejecucion real (dry_run=False), no
    la vista previa de solo lectura. cerrar_temporada_completa ya no escribe
    NADA en `previo` (ni siquiera durante dry_run=False -- ver
    _calcular_valores_previo_clave, funcion pura de solo lectura), asi que
    dry_run=True no necesita hacer rollback de nada: simplemente no llega al
    bloque `if not dry_run` que archiva en previo_historico. Se mockea por la
    misma razon que el test anterior: no queremos que una prueba automática
    dependa de/mute el estado real de la BD local para 2025-2026.
    """
    mock_cur_dict = MagicMock()
    mock_cur_dict.fetchone.side_effect = [
        {'fecha_inicio': '2025-07-01', 'fecha_fin': '2026-06-30', 'estado': 'cerrada'},
        {'n': 0},
    ]
    mock_cur_dict.fetchall.return_value = []
    mock_cur = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.side_effect = [mock_cur_dict, mock_cur]

    with patch('routes.temporadas.obtener_conexion', return_value=mock_conn):
        resultado = cerrar_temporada_completa('2025-2026', dry_run=True)

    assert resultado['clientes_procesados'] == 0
    mock_cur.executemany.assert_not_called()
    for call in mock_cur.execute.call_args_list:
        sql = call.args[0] if call.args else ''
        assert 'UPDATE temporadas' not in sql


def test_endpoint_sin_token_devuelve_401():
    app = create_app()
    client = app.test_client()

    resp = client.post('/cerrar-temporada-completa', json={'etiqueta': '2025-2026'})

    assert resp.status_code == 401
    data = resp.get_json()
    assert data and 'error' in data


def test_abrir_temporada_usa_dia_inicio_personalizado():
    """
    abrir_temporada no acepta un filtro por cliente: por diseño opera sobre
    TODA la tabla clientes (reabre a todos para la temporada nueva). Para
    poder ejercitar la función real contra la BD local (sin mocks, siguiendo
    la convención de este repo para código de BD) sin dejar corrompido el
    estado real de los ~140 clientes, respaldamos clientes ANTES de llamar a
    la función y restauramos ese respaldo completo en el finally, pase lo que
    pase. La corrida real (sin restaurar) contra toda la base queda reservada
    para el checkpoint humano de la Tarea 3.2, no para esta suite automática.
    """
    conn = obtener_conexion()
    cur = conn.cursor(dictionary=True)
    cur_w = conn.cursor()

    cur.execute(
        "SELECT clave, f_inicio, f_fin, dia_inicio_temporada, temporada_cerrada, "
        "fecha_cierre_temporada FROM clientes WHERE clave IS NOT NULL AND clave <> ''"
    )
    backup = cur.fetchall()

    cur_w.execute("UPDATE clientes SET dia_inicio_temporada = '06-01' WHERE clave = 'HA433'")
    conn.commit()

    try:
        abrir_temporada('2026-2027')

        cur.execute("SELECT f_inicio, temporada_cerrada FROM clientes WHERE clave = 'HA433'")
        fila = cur.fetchone()
        assert str(fila['f_inicio']) == '2026-06-01'
        assert fila['temporada_cerrada'] == 0
    finally:
        for row in backup:
            cur_w.execute(
                "UPDATE clientes SET f_inicio=%s, f_fin=%s, dia_inicio_temporada=%s, "
                "temporada_cerrada=%s, fecha_cierre_temporada=%s WHERE clave=%s",
                (row['f_inicio'], row['f_fin'], row['dia_inicio_temporada'],
                 row['temporada_cerrada'], row['fecha_cierre_temporada'], row['clave'])
            )
        conn.commit()
        cur.close(); cur_w.close(); conn.close()


def test_abrir_temporada_endpoint_sin_token_devuelve_401():
    app = create_app()
    client = app.test_client()

    resp = client.post('/abrir-temporada', json={'etiqueta': '2026-2027'})

    assert resp.status_code == 401
    data = resp.get_json()
    assert data and 'error' in data
