# tests/test_cierre_temporada.py
from db_conexion import obtener_conexion
from routes.temporadas import cerrar_temporada_completa


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
