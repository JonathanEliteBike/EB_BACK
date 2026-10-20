"""
Sincroniza a demanda: facturas/pedidos desde Odoo, caratulas (previo) y
retroactivos. Mismo flujo que corre automaticamente L-V 8:30am CDMX
(services/sync_scheduler.py), pero ejecutable manualmente cuando se necesite
forzarlo ya, sin esperar al horario programado.

Requiere que el backend Flask ya este corriendo (local: `python app.py`;
servidor: el servicio systemd `flaskapp` siempre esta activo) -- este script
solo hace las mismas llamadas HTTP que ya usamos manualmente con curl.

Uso:
    python sincronizar_todo.py

    Local:      corre tal cual, apunta a http://127.0.0.1:5000
    Servidor:   conectarse por SSH y correrlo ahi mismo (el backend escucha
                en 127.0.0.1:5000 detras de gunicorn en ambos entornos)
"""
import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import requests

PORT = os.environ.get('FLASK_PORT', '5000')
BASE = f'http://127.0.0.1:{PORT}'


def _paso(nombre: str) -> None:
    print(f"\n{'=' * 70}\n{nombre}\n{'=' * 70}")


def main() -> None:
    _paso("PASO 1/2: Facturas/pedidos (Odoo -> monitor) + caratulas (previo)")
    try:
        r1 = requests.post(f'{BASE}/sync-monitor-odoo', json={'recalcular_previo': True}, timeout=300)
        r1.raise_for_status()
        data1 = r1.json()
        if not data1.get('success', False):
            print(f"  ERROR: {data1}")
            sys.exit(1)
        print(f"  OK -- {data1.get('count', '?')} registros sincronizados desde Odoo")
        print(f"  OK -- {data1.get('previo_actualizado', '?')} filas de caratulas/previo recalculadas")
    except Exception as e:
        print(f"  ERROR en sync-monitor-odoo: {e}")
        sys.exit(1)

    _paso("PASO 2/2: Retroactivos (notas de credito, garantias, productos ofertados)")
    try:
        r2 = requests.post(f'{BASE}/sincronizar_notas', timeout=300)
        r2.raise_for_status()
        data2 = r2.json()
        if not data2.get('success', False):
            print(f"  ERROR: {data2}")
            sys.exit(1)
        print(f"  OK -- {data2.get('mensaje', 'sincronizacion completada')}")
    except Exception as e:
        print(f"  ERROR en sincronizar_notas: {e}")
        sys.exit(1)

    print("\nSincronizacion completa: facturas, pedidos, caratulas y retroactivos actualizados.")


if __name__ == '__main__':
    main()
