# tests/test_cierre_temporada.py
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
