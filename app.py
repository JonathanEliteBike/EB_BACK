from flask import Flask
from flask_cors import CORS
from socket_instance import socketio

# --- BLUEPRINTS EXISTENTES ---
from routes.monitor_odoo import monitor_odoo_bp
from routes.auth import auth
from routes.usuarios import usuarios_bp
from routes.clientes import clientes_bp
from routes.metas import metas_bp
from routes.previo import previo_bp
from routes.proyecciones import proyecciones_bp
from routes.disponible import disponibilidad_bp
from routes.multimarcas import multimarcas_bp
from routes.caratulas import caratulas_bp
from routes.integrales import integrales_bp
from routes.email import email_bp

# --- NUEVOS BLUEPRINTS (Traídos de tu local) ---
from routes.ordenes_compra import ordenes_compra_bp
from routes.dashboard_flujo import dashboard_flujo_bp
from routes.logistica import logistica_bp
from routes.gastos import gastos_bp
from routes.ingresos import ingresos_bp

from routes.retroactivos import retroactivos_bp
from routes.edicion_pedidos import edicion_bp
from routes.forecast import forecast_bp
from routes.ventas import ventas_bp
from routes.garantias import garantias_bp
from routes.proyecciones_my27 import proyecciones_my27_bp
from routes.importaciones import importaciones_bp
from routes.inventario import inventario_bp
from routes.colaboradores import colaboradores_bp
from routes.asignaciones import asignaciones_bp
from routes.responsivas import responsivas_bp
from routes.historial import historial_bp
# Importamos la instancia de Celery desde celery_worker
from celery_worker import celery_app as celery

def create_app():
    app = Flask(__name__)

    # Límite de tamaño para uploads (500 MB — necesario para videos)
    app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024

    # Vinculamos y configuramos Celery después de crear la app
    celery.conf.update(
        broker_url=app.config.get('CELERY_BROKER_URL', 'redis://localhost:6379/0'),
        result_backend=app.config.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
    )
    celery.Task = type('Task', (celery.Task,), {'__call__': lambda self, *args, **kwargs: self.run(*args, **kwargs)})

    # Configuración CORS
    allowed_origins = [
        "http://localhost:4200",
        "http://127.0.0.1:4200",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "http://localhost:63012",
        "http://127.0.0.1:63012",
        "http://3.128.54.77",
        "http://3.146.204.64",
        "https://app.elite-bike.com",
        "https://api.elite-bike.com"
    ]

    # Inicialmente registramos Flask-CORS para los orígenes conocidos.
    CORS(app, resources={
        r"/*": {
            "origins": allowed_origins,
            "supports_credentials": True,
            "expose_headers": ["Content-Disposition", "Content-Type"],
            "allow_headers": ["Authorization", "Content-Type"]
        }
    })

    # Además, añadimos un handler que refleja dinámicamente orígenes localhost/127.0.0.1 con cualquier puerto.
    # Esto permite desarrollar con el servidor Angular en puertos aleatorios sin romper CORS.
    from flask import request

    from flask import make_response

    @app.before_request
    def _handle_preflight():
        if request.method == 'OPTIONS':
            origin = request.headers.get('Origin', '')
            if origin in allowed_origins or origin.startswith('http://localhost:') or origin.startswith('http://127.0.0.1:'):
                response = make_response('', 200)
                response.headers['Access-Control-Allow-Origin'] = origin
                response.headers['Access-Control-Allow-Methods'] = 'GET,POST,PUT,PATCH,DELETE,OPTIONS'
                response.headers['Access-Control-Allow-Headers'] = 'Authorization, Content-Type'
                response.headers['Access-Control-Allow-Credentials'] = 'true'
                return response

    @app.after_request
    def _apply_cors_headers(response):
        origin = request.headers.get('Origin')
        if not origin:
            return response

        # Allow explicit allowed_origins or localhosts with any port
        if origin in allowed_origins or origin.startswith('http://localhost:') or origin.startswith('http://127.0.0.1:'):
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Vary'] = 'Origin'
            response.headers['Access-Control-Allow-Credentials'] = 'true'
            response.headers['Access-Control-Expose-Headers'] = 'Content-Disposition, Content-Type'
            response.headers['Access-Control-Allow-Headers'] = 'Authorization, Content-Type'
            response.headers['Access-Control-Allow-Methods'] = 'GET,POST,PUT,PATCH,DELETE,OPTIONS'

        return response

     # Configuración Socket.IO con async_mode explícito
    socketio.init_app(
        app,
        cors_allowed_origins=allowed_origins,
        async_mode='threading',  # use threading temporarily for debugging
        logger=True,
        engineio_logger=True,
        path='/socket.io/'
    )

    # --- REGISTRO DE BLUEPRINTS ---
    
    # Viejos
    app.register_blueprint(monitor_odoo_bp)
    app.register_blueprint(auth)
    app.register_blueprint(usuarios_bp)
    app.register_blueprint(clientes_bp)
    app.register_blueprint(metas_bp)
    app.register_blueprint(previo_bp)
    app.register_blueprint(proyecciones_bp)
    app.register_blueprint(disponibilidad_bp)
    app.register_blueprint(multimarcas_bp)
    app.register_blueprint(caratulas_bp)
    app.register_blueprint(integrales_bp)
    app.register_blueprint(email_bp)

    # Nuevos (Agregados aquí)
    app.register_blueprint(ordenes_compra_bp)
    app.register_blueprint(dashboard_flujo_bp)
    app.register_blueprint(logistica_bp)
    app.register_blueprint(gastos_bp)
    app.register_blueprint(ingresos_bp)

    app.register_blueprint(retroactivos_bp)
    app.register_blueprint(edicion_bp)
    app.register_blueprint(forecast_bp)
    app.register_blueprint(ventas_bp)
    app.register_blueprint(garantias_bp)
    app.register_blueprint(proyecciones_my27_bp)
    app.register_blueprint(importaciones_bp)
    app.register_blueprint(inventario_bp)
    app.register_blueprint(colaboradores_bp)
    app.register_blueprint(asignaciones_bp)
    app.register_blueprint(responsivas_bp)
    app.register_blueprint(historial_bp)
    return app

app = create_app()

if __name__ == '__main__':
    # Usamos socketio.run para mantener compatibilidad con eventlet en el servidor
    socketio.run(
        app,
        host='0.0.0.0',
        port=5000,
        debug=True,
        use_reloader=False,  # Importante para eventlet
        log_output=True,
        allow_unsafe_werkzeug=True
    )