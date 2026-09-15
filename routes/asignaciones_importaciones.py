import logging
from functools import wraps

from flask import Blueprint, request, jsonify

from db_conexion import obtener_conexion
from routes.clientes import token_required
from services import asignaciones_service as svc

asignaciones_bp = Blueprint("asignaciones_importaciones", __name__, url_prefix="/importaciones")


def _requiere_rol_importaciones(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        payload = getattr(request, "cliente_data", {}) or {}
        if payload.get("rol") not in (1, 3):
            return jsonify({
                "ok": False,
                "error": {"code": "NO_AUTORIZADO", "message": "No tienes permiso para acceder a este módulo"},
            }), 403
        return f(*args, **kwargs)
    return decorated


@asignaciones_bp.errorhandler(svc.AsignacionesError)
def _manejar_error_asignaciones(err):
    return jsonify({"ok": False, "error": {"code": err.code, "message": err.message}}), err.status


@asignaciones_bp.errorhandler(Exception)
def _manejar_error_inesperado(err):
    # Flask resuelve primero el handler más específico (AsignacionesError), así que esto
    # solo atrapa lo que hoy se escaparía como HTML 500 fuera del contrato {ok, error}.
    logging.exception("Error inesperado en asignaciones")
    return jsonify({"ok": False, "error": {"code": "ERROR_INTERNO", "message": "Error interno"}}), 500


@asignaciones_bp.route("/asignaciones/inicializar-tablas", methods=["POST"])
@token_required
@_requiere_rol_importaciones
def inicializar_tablas():
    conn = obtener_conexion()
    if not conn:
        return jsonify({"ok": False, "error": {"code": "DB_NO_DISPONIBLE", "message": "Sin conexión a BD"}}), 500
    try:
        cursor = conn.cursor()
        for ddl in svc.TABLAS_SQL:
            cursor.execute(ddl)
        migraciones = svc._migrar_esquema_reservas(cursor)
        conn.commit()
        return jsonify({
            "ok": True,
            "mensaje": "Tablas de asignaciones creadas/verificadas",
            "migraciones_aplicadas": migraciones,
        }), 201
    except Exception as e:
        logging.exception("Error creando tablas de asignaciones")
        return jsonify({"ok": False, "error": {"code": "DB_ERROR", "message": str(e)}}), 500
    finally:
        conn.close()


@asignaciones_bp.route("/<int:importacion_id>/asignaciones/productos", methods=["GET"])
@token_required
@_requiere_rol_importaciones
def listar_productos_ruta(importacion_id):
    data = svc.listar_productos(importacion_id)
    return jsonify({"ok": True, "data": data}), 200


@asignaciones_bp.route("/<int:importacion_id>/asignaciones/productos", methods=["POST"])
@token_required
@_requiere_rol_importaciones
def crear_producto_ruta(importacion_id):
    body = request.get_json(silent=True) or {}
    payload = getattr(request, "cliente_data", {}) or {}
    data = svc.crear_producto(
        importacion_id=importacion_id,
        sku=body.get("sku"),
        cantidad_embarcada=body.get("cantidad_embarcada"),
        periodo=body.get("periodo"),
        descripcion=body.get("descripcion"),
        usuario_id=payload.get("id"),
    )
    return jsonify({"ok": True, "data": data}), 201


@asignaciones_bp.route("/<int:importacion_id>/asignaciones/productos/importar", methods=["POST"])
@token_required
@_requiere_rol_importaciones
def importar_productos_ruta(importacion_id):
    payload = getattr(request, "cliente_data", {}) or {}
    f = request.files.get("file")
    if not f or not (f.filename or "").lower().endswith((".xlsx", ".xls")):
        return jsonify({
            "ok": False,
            "error": {"code": "ARCHIVO_INVALIDO", "message": "Se requiere un archivo .xlsx o .xls en el campo 'file'"},
        }), 400
    periodo = (request.form.get("periodo") or "").strip()
    parseado = svc.parsear_excel_productos(f.read())
    resultado = svc.importar_productos(
        importacion_id=importacion_id,
        periodo=periodo,
        filas=parseado["filas"],
        usuario_id=payload.get("id"),
    )
    # Errores de parseo (cantidad no numérica, etc.) se suman a los de negocio.
    resultado["errores"] = parseado["errores"] + resultado["errores"]
    return jsonify({"ok": True, "data": resultado}), 200


@asignaciones_bp.route("/<int:importacion_id>/asignaciones/productos/<int:producto_id>", methods=["PUT"])
@token_required
@_requiere_rol_importaciones
def actualizar_producto_ruta(importacion_id, producto_id):
    body = request.get_json(silent=True) or {}
    payload = getattr(request, "cliente_data", {}) or {}
    data = svc.actualizar_producto(
        producto_id=producto_id,
        cantidad_embarcada=body.get("cantidad_embarcada"),
        descripcion=body.get("descripcion"),
        usuario_id=payload.get("id"),
        importacion_id=importacion_id,
    )
    return jsonify({"ok": True, "data": data}), 200


@asignaciones_bp.route("/<int:importacion_id>/asignaciones/recalcular", methods=["POST"])
@token_required
@_requiere_rol_importaciones
def recalcular_ruta(importacion_id):
    body = request.get_json(silent=True) or {}
    data = svc.recalcular_propuesta(
        importacion_id, body.get("mes_desde"), body.get("mes_hasta"), body.get("periodo")
    )
    return jsonify({"ok": True, "data": data}), 200


@asignaciones_bp.route(
    "/<int:importacion_id>/asignaciones/productos/<int:producto_id>/asignar", methods=["POST"]
)
@asignaciones_bp.route(
    "/<int:importacion_id>/asignaciones/productos/<int:producto_id>/reservar", methods=["POST"]
)
@token_required
@_requiere_rol_importaciones
def asignar_ruta(importacion_id, producto_id):
    body = request.get_json(silent=True) or {}
    payload = getattr(request, "cliente_data", {}) or {}
    data = svc.asignar(
        producto_id,
        body.get("reservas") or body.get("asignaciones") or [],
        usuario_id=payload.get("id"),
        importacion_id=importacion_id,
    )
    return jsonify({"ok": True, "data": data}), 200


@asignaciones_bp.route("/<int:importacion_id>/asignaciones/reasignar", methods=["POST"])
@token_required
@_requiere_rol_importaciones
def reasignar_ruta(importacion_id):
    body = request.get_json(silent=True) or {}
    data = svc.proponer_reasignacion(
        importacion_id, body.get("ventana_desde"), body.get("periodo")
    )
    return jsonify({"ok": True, "data": data}), 200


@asignaciones_bp.route(
    "/<int:importacion_id>/asignaciones/productos/<int:producto_id>/reasignar", methods=["POST"]
)
@token_required
@_requiere_rol_importaciones
def confirmar_reasignacion_ruta(importacion_id, producto_id):
    body = request.get_json(silent=True) or {}
    payload = getattr(request, "cliente_data", {}) or {}
    data = svc.confirmar_reasignacion(
        producto_id,
        body.get("reservas") or [],
        usuario_id=payload.get("id"),
        importacion_id=importacion_id,
    )
    return jsonify({"ok": True, "data": data}), 200


@asignaciones_bp.route(
    "/<int:importacion_id>/asignaciones/productos/<int:producto_id>/venta-sobrante", methods=["POST"]
)
@token_required
@_requiere_rol_importaciones
def venta_sobrante_ruta(importacion_id, producto_id):
    body = request.get_json(silent=True) or {}
    payload = getattr(request, "cliente_data", {}) or {}
    data = svc.crear_venta_sobrante(
        producto_id=producto_id,
        clave_cliente=body.get("clave_cliente"),
        cantidad=body.get("cantidad"),
        numero_pedido_odoo=body.get("numero_pedido_odoo"),
        usuario_id=payload.get("id"),
        importacion_id=importacion_id,
    )
    return jsonify({"ok": True, "data": data}), 201


@asignaciones_bp.route(
    "/<int:importacion_id>/asignaciones/productos/<int:producto_id>/asignaciones/<int:asignacion_id>/cancelar",
    methods=["POST"],
)
@token_required
@_requiere_rol_importaciones
def cancelar_asignacion_ruta(importacion_id, producto_id, asignacion_id):
    payload = getattr(request, "cliente_data", {}) or {}
    data = svc.cancelar_asignacion(
        asignacion_id, usuario_id=payload.get("id"), importacion_id=importacion_id
    )
    return jsonify({"ok": True, "data": data}), 200


@asignaciones_bp.route(
    "/<int:importacion_id>/asignaciones/reservas/<int:reserva_id>/resolver", methods=["POST"]
)
@token_required
@_requiere_rol_importaciones
def resolver_reserva_ruta(importacion_id, reserva_id):
    body = request.get_json(silent=True) or {}
    payload = getattr(request, "cliente_data", {}) or {}
    data = svc.resolver_reserva(
        reserva_id, body.get("decision"),
        usuario_id=payload.get("id"), importacion_id=importacion_id,
    )
    return jsonify({"ok": True, "data": data}), 200


@asignaciones_bp.route(
    "/<int:importacion_id>/asignaciones/ventas/<int:venta_id>/validar-odoo", methods=["POST"]
)
@token_required
@_requiere_rol_importaciones
def validar_odoo_ruta(importacion_id, venta_id):
    body = request.get_json(silent=True) or {}
    data = svc.validar_venta_odoo(
        venta_id, body.get("numero_pedido_odoo"), importacion_id=importacion_id
    )
    return jsonify({"ok": True, "data": data}), 200


@asignaciones_bp.route(
    "/<int:importacion_id>/asignaciones/ventas/<int:venta_id>/cancelar", methods=["POST"]
)
@token_required
@_requiere_rol_importaciones
def cancelar_venta_ruta(importacion_id, venta_id):
    payload = getattr(request, "cliente_data", {}) or {}
    data = svc.cancelar_venta(venta_id, usuario_id=payload.get("id"), importacion_id=importacion_id)
    return jsonify({"ok": True, "data": data}), 200


@asignaciones_bp.route("/<int:importacion_id>/asignaciones", methods=["GET"])
@token_required
@_requiere_rol_importaciones
def resumen_ruta(importacion_id):
    data = svc.resumen_embarque(importacion_id)
    return jsonify({"ok": True, "data": data}), 200


@asignaciones_bp.route(
    "/<int:importacion_id>/asignaciones/productos/<int:producto_id>/detalle", methods=["GET"]
)
@token_required
@_requiere_rol_importaciones
def detalle_producto_ruta(importacion_id, producto_id):
    data = svc.obtener_detalle_producto(producto_id, importacion_id=importacion_id)
    return jsonify({"ok": True, "data": data}), 200


@asignaciones_bp.route("/<int:importacion_id>/asignaciones/movimientos", methods=["GET"])
@token_required
@_requiere_rol_importaciones
def movimientos_ruta(importacion_id):
    data = svc.listar_movimientos(importacion_id)
    return jsonify({"ok": True, "data": data}), 200


@asignaciones_bp.route("/<int:importacion_id>/asignaciones/reservas", methods=["GET"])
@token_required
@_requiere_rol_importaciones
def reservas_embarque_ruta(importacion_id):
    clave_cliente = request.args.get("clave_cliente")
    data = svc.listar_reservas_embarque(importacion_id, clave_cliente=clave_cliente)
    return jsonify({"ok": True, "data": data}), 200


# ── Vistas consolidadas (dashboard) ───────────────────────────────────────────
# Rutas estáticas: "asignaciones" nunca calza con <int:importacion_id>.

@asignaciones_bp.route("/asignaciones/embarques", methods=["GET"])
@token_required
@_requiere_rol_importaciones
def resumen_global_ruta():
    filtros = {
        "estado": request.args.get("estado"),
        "origen": request.args.get("origen"),
        "anio": request.args.get("anio"),
        "q": request.args.get("q"),
        "solo_con_disponible": request.args.get("solo_con_disponible"),
    }
    data = svc.resumen_global(filtros)
    return jsonify({"ok": True, "data": data}), 200


@asignaciones_bp.route("/asignaciones/productos", methods=["GET"])
@token_required
@_requiere_rol_importaciones
def listar_productos_global_ruta():
    filtros = {
        "estado": request.args.get("estado"),
        "origen": request.args.get("origen"),
        "anio": request.args.get("anio"),
        "importacion_id": request.args.get("importacion_id"),
        "periodo": request.args.get("periodo"),
        "sku": request.args.get("sku"),
        "q": request.args.get("q"),
        "solo_disponible": request.args.get("solo_disponible"),
    }
    data = svc.listar_productos_global(
        filtros,
        limite=request.args.get("limite", 200),
        offset=request.args.get("offset", 0),
    )
    return jsonify({"ok": True, "data": data}), 200
