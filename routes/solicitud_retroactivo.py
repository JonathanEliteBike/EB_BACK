from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import logging
import os

from flask import Blueprint, jsonify, request, g
from db_conexion import obtener_conexion
from services.s3_service import generar_url_firmada_s3, subir_archivo_s3
from services import solicitud_retroactivo_service as data
from utils.jwt_utils import verificar_token
from utils.auditoria_utils import verificar_codigo_auditoria
from utils.auth_decorators import requiere_autenticacion, requiere_modulos, requiere_rol
from services.politica_montos_service import PoliticaMontosService

solicitud_retroactivo_bp = Blueprint('solicitud-retroactivo', __name__)

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '..', 'uploads', 'solicitudes-retroactivos')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {'pdf', 'jpg', 'jpeg', 'png', 'xml'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Definimos los campos de archivos que esperamos recibir
ARCHIVOS_REQUERIDOS = {
    "ticket_compra": "Ticket de compra",
    "voucher": "Voucher de pago",
    "factura_pdf": "Factura PDF",
    "factura_xml": "Factura XML"
}


def _usuario_desde_token(request):
    """Decodifica el JWT del header Authorization. None si falta o es inválido."""
    auth_header = request.headers.get('Authorization', '')
    raw_token = auth_header.split(' ')[1] if ' ' in auth_header else None
    if not raw_token:
        return None
    return verificar_token(raw_token)


def _requiere_admin(request):
    """Ver routes/retroactivos.py:_requiere_admin_retroactivos, mismo patrón."""
    payload = _usuario_desde_token(request)
    if not payload:
        return False
    try:
        return int(payload.get('rol')) == 1
    except (TypeError, ValueError):
        return False


def _debe_ocultar_montos_retroactivos():
    return PoliticaMontosService.debe_ocultar_montos(g.usuario_actual['id'], 'retroactivos')


def _redactar_montos_solicitud(fila):
    for campo in list(fila):
        nombre = str(campo).lower()
        if 'precio' in nombre or 'monto' in nombre or 'importe' in nombre or 'costo' in nombre:
            fila.pop(campo, None)
    return fila


def _cliente_id_autenticado(cursor):
    """Resuelve el cliente real del JWT; nunca acepta el cliente del body."""
    if int(g.usuario_actual.get('rol', 0)) == 1:
        return None

    cursor.execute(
        """
        SELECT cliente_id
        FROM usuarios
        WHERE id = %s AND activo = 1
        """,
        (g.usuario_actual['id'],),
    )
    usuario = cursor.fetchone()
    if not usuario or not usuario.get('cliente_id'):
        raise PermissionError('El usuario autenticado no tiene un cliente válido asociado.')
    return int(usuario['cliente_id'])


def _parsear_validacion_docs(raw):
    """validacion_docs_json puede venir como dict (ya parseado), str (JSON
    crudo) o None (nadie ha revisado nada todavía)."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


# GUÍA: historial como columna JSON (mismo patrón que validacion_docs_json)
# en vez de una tabla de auditoría nueva -- no hay volumen que justifique una
# tabla aparte, y evita otra migración + endpoint. Cada mutación relevante
# (creación, validar/rechazar documento, corrección de precio, reenvío del
# cliente) le agrega una entrada. Se expone en listar/mis-solicitudes.
def _parsear_historial(raw):
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return []


def _entrada_historial(tipo, descripcion, usuario=None):
    entrada = {"fecha": datetime.now().isoformat(), "tipo": tipo, "descripcion": descripcion}
    if usuario:
        entrada["usuario"] = usuario
    return entrada


# GUÍA: validación por archivo, no por solicitud completa -- si el admin
# rechaza 1 de los 4 documentos, la solicitud se ve como 'rechazado' en su
# conjunto (decisión de producto: más simple de entender para el cliente que
# un estado "parcial"), pero el cliente solo tiene que resubir ESE archivo
# (ver PUT /venta/<id>), no los 4. Solo es 'validado' cuando TODOS los
# documentos que el distribuidor realmente subió quedaron 'valido' --
# ticket_compra/voucher/factura_pdf son siempre obligatorios (ver
# registrar_venta), factura_xml solo cuenta si se subió (es opcional).
# Antes solo miraba ticket_compra+voucher, así que 2 documentos validados
# bastaban para marcar todo el ticket como validado sin importar los demás.
def _calcular_estatus(validacion_docs, tiene_factura_xml=False):
    if 'rechazado' in validacion_docs.values():
        return 'rechazado'
    documentos_a_validar = ['ticket_compra', 'voucher', 'factura_pdf']
    if tiene_factura_xml:
        documentos_a_validar.append('factura_xml')
    if all(validacion_docs.get(doc) == 'valido' for doc in documentos_a_validar):
        return 'validado'
    return 'pendiente'


def _tiene_nota_credito(nota_credito):
    """Mantiene la misma convención de seguimiento: vacío, None y 0 no son NC."""
    valor = str(nota_credito or '').strip()
    return bool(valor and valor.lower() not in ('none', '0'))

@solicitud_retroactivo_bp.route('/api/solicitud-retroactivo/registrar/venta', methods=['POST'])
@requiere_autenticacion
@requiere_modulos('usuarios_retroactivos', 'usuarios_solicitudes_retroactivos')
def registrar_venta():
    campos_obligatorios = [
        'id_usuario', 'id_formulario', 'id_msi',
        'correo_electronico', 'fecha_venta',
        'modelo_bicicleta', 'numero_serie', 'precio_publico',
        'producto_detalle_id', 'sku'
    ]

    datos = {campo: request.form.get(campo) for campo in campos_obligatorios}
    datos['id_marca_bicicleta'] = request.form.get('id_marca_bicicleta')
    datos['id_cliente'] = request.form.get('id_cliente')
    datos['id_tienda'] = request.form.get('id_tienda')
    datos['nombre_completo'] = request.form.get('nombre_completo')
    datos['nombre_sucursal'] = request.form.get('nombre_sucursal')

    nombre_usuario = g.usuario_actual.get('nombre') or g.usuario_actual.get('usuario')

    faltantes = [campo for campo in campos_obligatorios if not datos.get(campo)]
    if faltantes:
        return jsonify({"error": "Campos de texto faltantes", "campos": faltantes}), 400

    try:
        id_formulario_seleccionado = int(datos['id_formulario'])
        id_producto_detalle = int(datos['producto_detalle_id'])
        id_msi_seleccionado = int(datos['id_msi'])
    except (TypeError, ValueError):
        return jsonify({
            "error": "Los identificadores de campaña, producto o MSI son inválidos.",
            "codigo": "identificador_invalido"
        }), 400

    # Se inicializan antes de validar para mantener la respuesta existente,
    # pero no se sube ningún archivo hasta que campaña, SKU y serie pasen.
    archivos_procesados = {}
    keys_archivos = {k: None for k in ARCHIVOS_REQUERIDOS.keys()}
    conexion = obtener_conexion()
    if not conexion:
        return jsonify({"error": "No se pudo conectar a la base de datos."}), 500

    cursor = conexion.cursor(dictionary=True, buffered=True)

    try:
        # La identidad del distribuidor viene siempre del JWT, no de
        # id_usuario/id_cliente enviados en el multipart.
        cliente_id_autenticado = _cliente_id_autenticado(cursor)
        if cliente_id_autenticado is not None:
            datos['id_cliente'] = str(cliente_id_autenticado)
        datos['id_usuario'] = str(g.usuario_actual['id'])

        # Resolver nombre_completo y nombre_sucursal si se enviaron IDs de cliente/tienda.
        if datos.get('id_cliente') or datos.get('id_tienda'):
            nom_cli, nom_suc = data.obtener_nombres_cliente_y_tienda(cursor, datos.get('id_cliente'), datos.get('id_tienda'))
            if nom_cli:
                datos['nombre_completo'] = nom_cli
            if nom_suc:
                datos['nombre_sucursal'] = nom_suc

        if cliente_id_autenticado is not None and datos.get('id_tienda'):
            cursor.execute(
                "SELECT 1 FROM tiendas WHERE id = %s AND cliente_id = %s",
                (datos['id_tienda'], cliente_id_autenticado),
            )
            if not cursor.fetchone():
                return jsonify({'error': 'La tienda no pertenece al cliente autenticado.'}), 403

        if not datos.get('nombre_completo'):
            datos['nombre_completo'] = request.form.get('nombre_completo') or 'Cliente'
        if not datos.get('nombre_sucursal'):
            datos['nombre_sucursal'] = request.form.get('nombre_sucursal') or 'Matriz'

        if not data.obtener_campania_vigente(cursor, id_formulario_seleccionado):
            return jsonify({
                "error": "La campaña seleccionada no está vigente.",
                "codigo": "campania_no_vigente"
            }), 422

        producto_detalle = data.obtener_producto_detalle_de_campania(
            cursor, id_formulario_seleccionado, id_producto_detalle
        )
        if not producto_detalle:
            return jsonify({
                "error": "El producto seleccionado no pertenece a la campaña.",
                "codigo": "producto_no_pertenece_campania"
            }), 422

        sku_real = str(producto_detalle['sku'] or '').strip()
        if str(datos['sku']).strip().casefold() != sku_real.casefold():
            return jsonify({
                "error": "El SKU no corresponde al producto seleccionado.",
                "codigo": "sku_no_corresponde_producto"
            }), 422

        # Reutiliza la consulta Odoo de series entregadas. Sólo continúa si la
        # serie fue entregada a este distribuidor y corresponde al SKU local.
        data.validar_serie_entregada_odoo(
            cursor,
            g.usuario_actual['id'],
            sku_real,
            datos['numero_serie'],
        )

        raw_marca = datos.get('id_marca_bicicleta')
        id_marca = int(raw_marca) if raw_marca and str(raw_marca).isdigit() else None

        # GUÍA: el % ya no es fijo por plazo MSI -- depende de la campaña
        # elegida (ver solicitud_retroactivo_campania_msi). Si el plazo no
        # está ligado a esa campaña, es una combinación inválida.
        resultado = data.obtener_porcentaje_campania_msi(cursor, id_formulario_seleccionado, id_msi_seleccionado)
        if not resultado:
            return jsonify({"error": "El plazo MSI seleccionado no aplica para esta campaña."}), 400

        porcentaje = Decimal(str(resultado['porcentaje']))

        precio_publico = Decimal(str(datos['precio_publico']).replace(',', ''))
        monto_pagar = (precio_publico * porcentaje) / Decimal(100)
        monto_aplicar = monto_pagar

        # Se revisan los archivos antes de subirlos; hasta este punto no hubo
        # escrituras en S3 ni en la base de datos.
        for key_archivo in ARCHIVOS_REQUERIDOS:
            file = request.files.get(key_archivo)
            if key_archivo == 'factura_xml' and (not file or not file.filename):
                continue
            if not file:
                return jsonify({"error": f"No se recibió el archivo: {key_archivo}"}), 400
            if not file.filename:
                return jsonify({"error": f"Nombre de archivo vacío para: {key_archivo}"}), 400
            if not allowed_file(file.filename):
                return jsonify({"error": f"Tipo de archivo no permitido para: {key_archivo}"}), 400

        try:
            for key_archivo in ARCHIVOS_REQUERIDOS:
                file = request.files.get(key_archivo)
                if not file or not file.filename:
                    continue
                resultado = subir_archivo_s3(file)
                keys_archivos[key_archivo] = resultado["key"]
                archivos_procesados[key_archivo] = {
                    "key": resultado["key"],
                    "original": resultado["original"],
                    "url": generar_url_firmada_s3(resultado["key"]),
                    "storage": "s3"
                }
        except Exception as e:
            logging.exception("Error al subir archivos a S3: %s", e)
            return jsonify({"error": "Ocurrió un error al subir los archivos."}), 500

        parametros = (
            int(datos['id_usuario']),
            int(datos['id_formulario']),
            id_marca,
            id_msi_seleccionado,
            datos['nombre_sucursal'],
            datos['correo_electronico'],
            datos['nombre_completo'],
            datos['fecha_venta'],
            datos['modelo_bicicleta'],
            datos['numero_serie'],
            precio_publico,
            porcentaje,
            monto_pagar,
            monto_aplicar,
            keys_archivos['ticket_compra'],
            keys_archivos['voucher'],
            keys_archivos['factura_pdf'],
            keys_archivos['factura_xml']
        )

        data.crear_venta(cursor, parametros)
        conexion.commit()

        nueva = data.obtener_id_por_numero_serie(cursor, datos['numero_serie'])
        if nueva:
            data.guardar_historial_inicial(
                cursor, nueva['id'],
                json.dumps([_entrada_historial('creacion', 'Solicitud registrada', nombre_usuario)])
            )
            conexion.commit()

        respuesta = {
            "respuesta": True,
            "mensaje": "Venta y archivos registrados exitosamente",
            "datos_venta": datos,
            "archivos": archivos_procesados
        }
        if _debe_ocultar_montos_retroactivos():
            _redactar_montos_solicitud(respuesta['datos_venta'])
        return jsonify(respuesta), 200

    except data.SeriesOdooError as e:
        conexion.rollback()
        return jsonify({'error': e.mensaje, 'codigo': e.codigo}), e.http_status
    except PermissionError as e:
        conexion.rollback()
        return jsonify({'error': str(e)}), 403
    except Exception as e:
        conexion.rollback()
        logging.exception("Error en BD al registrar la venta: %s", e)
        return jsonify({"error": "Los archivos se subieron pero ocurrió un error al registrar la venta en la base de datos.", "detalle": str(e)}), 500
    finally:
        cursor.close()
        conexion.close()

@solicitud_retroactivo_bp.route('/api/solicitud-retroactivo/msi', methods=['GET'])
@requiere_autenticacion
@requiere_modulos('usuarios_retroactivos', 'usuarios_solicitudes_retroactivos')
def buscar_msi():
    conexion = obtener_conexion()
    if not conexion:
        return jsonify({"error": "No se pudo conectar a la base de datos."}), 500
            
    cursor = conexion.cursor(dictionary=True, buffered=True) 
    
    try:
        datos = data.buscar_msi(cursor)
        return jsonify(datos), 200

    except Exception as e:
        return jsonify({"error": "Error al consultar los meses sin intereses.", "detalle": str(e)}), 500
        
    finally:
        cursor.close()
        conexion.close()

@solicitud_retroactivo_bp.route('/api/solicitud-retroactivo/marca', methods=['GET'])
@requiere_autenticacion
@requiere_modulos('usuarios_retroactivos', 'usuarios_solicitudes_retroactivos')
def buscar_marca():
    conexion = obtener_conexion()
    if not conexion:
        return jsonify({"error": "No se pudo conectar a la base de datos."}), 500
            
    cursor = conexion.cursor(dictionary=True, buffered=True) 
    
    try:
        datos = data.buscar_marca(cursor)
        return jsonify(datos), 200

    except Exception as e:
        return jsonify({"error": "Error al consultar las marcas.", "detalle": str(e)}), 500
        
    finally:
        cursor.close()
        conexion.close()

@solicitud_retroactivo_bp.route('/api/solicitud-retroactivo/formulario', methods=['GET'])
@requiere_autenticacion
@requiere_modulos('usuarios_retroactivos', 'usuarios_solicitudes_retroactivos')
def buscar_formulario():
    """GUÍA: antes leía del catálogo viejo (solicitud_retroactivo_formulario,
    solo 2 filas fijas vía SP). Ahora regresa las campañas del módulo de
    Campañas que están activas y vigentes hoy -- así lo que crea MKT
    aparece de inmediato en el formulario de venta."""
    conexion = obtener_conexion()
    if not conexion:
        return jsonify({"error": "No se pudo conectar a la base de datos."}), 500

    cursor = conexion.cursor(dictionary=True, buffered=True)

    try:
        datos = data.listar_campanias_activas(cursor)
        return jsonify(datos), 200

    except Exception as e:
        return jsonify({"error": "Error al consultar los formularios.", "detalle": str(e)}), 500

    finally:
        cursor.close()
        conexion.close()


@solicitud_retroactivo_bp.route('/api/solicitud-retroactivo/campania/<int:id_campania>/msi', methods=['GET'])
@requiere_autenticacion
@requiere_modulos('usuarios_retroactivos', 'usuarios_solicitudes_retroactivos')
def msi_por_campania(id_campania):
    """Plazos MSI ligados a una campaña, cada uno con SU % propio -- el
    formulario de venta los carga en cuanto el usuario elige la campaña."""
    conexion = obtener_conexion()
    if not conexion:
        return jsonify({"error": "No se pudo conectar a la base de datos."}), 500

    cursor = conexion.cursor(dictionary=True, buffered=True)

    try:
        datos = data.listar_msi_por_campania(cursor, id_campania)
        return jsonify(datos), 200

    except Exception as e:
        return jsonify({"error": "Error al consultar los MSI de la campaña.", "detalle": str(e)}), 500

    finally:
        cursor.close()
        conexion.close()


@solicitud_retroactivo_bp.route('/api/solicitud-retroactivo/campania/<int:id_campania>/productos', methods=['GET'])
@requiere_autenticacion
@requiere_modulos('usuarios_retroactivos', 'usuarios_solicitudes_retroactivos')
def productos_por_campania(id_campania):
    """Productos ligados a una campaña -- el formulario de venta los carga en
    cuanto el usuario elige la campaña, para que "Modelo" solo ofrezca lo que
    esa campaña realmente incluye."""
    conexion = obtener_conexion()
    if not conexion:
        return jsonify({"error": "No se pudo conectar a la base de datos."}), 500

    cursor = conexion.cursor(dictionary=True, buffered=True)

    try:
        datos = data.obtener_productos_por_campania(cursor, id_campania)
        return jsonify(datos), 200

    except Exception as e:
        return jsonify({"error": "Error al consultar los productos de la campaña.", "detalle": str(e)}), 500

    finally:
        cursor.close()
        conexion.close()


@solicitud_retroactivo_bp.route('/api/solicitud-retroactivo/campania/<int:id_campania>/marcas', methods=['GET'])
@requiere_autenticacion
@requiere_modulos('usuarios_retroactivos', 'usuarios_solicitudes_retroactivos')
def marcas_por_campania(id_campania):
    """Marcas distintas entre los productos de una campaña -- el selector de
    "Marca" solo se activa en el formulario de venta si hay 2 o más."""
    conexion = obtener_conexion()
    if not conexion:
        return jsonify({"error": "No se pudo conectar a la base de datos."}), 500

    cursor = conexion.cursor(dictionary=True, buffered=True)

    try:
        datos = data.obtener_marcas_por_campania(cursor, id_campania)
        return jsonify(datos), 200

    except Exception as e:
        return jsonify({"error": "Error al consultar las marcas de la campaña.", "detalle": str(e)}), 500

    finally:
        cursor.close()
        conexion.close()


@solicitud_retroactivo_bp.route('/api/solicitud-retroactivo/series-disponibles', methods=['GET'])
@requiere_autenticacion
@requiere_rol(2, 3)
@requiere_modulos('usuarios_retroactivos', 'usuarios_solicitudes_retroactivos')
def series_disponibles():
    """Devuelve únicamente series entregadas al cliente del JWT.

    ``sku`` y ``producto`` son filtros opcionales; nunca identifican al
    distribuidor. La autoridad para ello siempre es usuarios.cliente_id.
    """
    conexion = obtener_conexion()
    if not conexion:
        return jsonify({'error': 'No se pudo conectar a la base de datos.'}), 500

    cursor = conexion.cursor(dictionary=True, buffered=True)
    try:
        cliente = data.obtener_cliente_odoo_usuario(cursor, g.usuario_actual['id'])
        if not cliente or not cliente.get('clave'):
            return jsonify({
                'error': 'El usuario autenticado no tiene un cliente con clave Odoo asociada.',
                'codigo': 'cliente_sin_clave_odoo'
            }), 403

        resultado = data.obtener_series_entregadas_odoo(
            clave_cliente=str(cliente['clave']).strip(),
            razon_social=cliente.get('nombre_cliente'),
            sku=request.args.get('sku'),
            producto=request.args.get('producto'),
        )
        return jsonify(resultado), 200

    except data.SeriesOdooError as e:
        return jsonify({'error': e.mensaje, 'codigo': e.codigo}), e.http_status
    except Exception as e:
        logging.exception('Error al consultar series elegibles en Odoo: %s', e)
        return jsonify({
            'error': 'Error técnico al consultar las series en Odoo.',
            'codigo': 'error_tecnico_odoo'
        }), 500
    finally:
        cursor.close()
        conexion.close()


@solicitud_retroactivo_bp.route('/api/solicitud-retroactivo/razones-sociales', methods=['GET'])
@requiere_autenticacion
@requiere_modulos('usuarios_retroactivos', 'usuarios_solicitudes_retroactivos')
def buscar_razones_sociales():
    conexion = obtener_conexion()
    if not conexion:
        return jsonify({"error": "No se pudo conectar a la base de datos."}), 500
            
    cursor = conexion.cursor(dictionary=True, buffered=True) 
    
    try:
        datos = data.buscar_razones_sociales(cursor)
        cliente_id_autenticado = _cliente_id_autenticado(cursor)
        if cliente_id_autenticado is not None:
            datos = [cliente for cliente in datos if cliente['id'] == cliente_id_autenticado]
        return jsonify(datos), 200

    except PermissionError as e:
        return jsonify({'error': str(e)}), 403
    except Exception as e:
        return jsonify({"error": "Error al consultar las razones sociales.", "detalle": str(e)}), 500

    finally:
        cursor.close()
        conexion.close()


@solicitud_retroactivo_bp.route('/api/solicitud-retroactivo/tiendas/<int:cliente_id>', methods=['GET'])
@requiere_autenticacion
@requiere_modulos('usuarios_retroactivos', 'usuarios_solicitudes_retroactivos')
def buscar_tiendas(cliente_id):
    conexion = obtener_conexion()
    if not conexion:
        return jsonify({"error": "No se pudo conectar a la base de datos."}), 500
            
    cursor = conexion.cursor(dictionary=True, buffered=True) 
    
    try:
        cliente_id_autenticado = _cliente_id_autenticado(cursor)
        if cliente_id_autenticado is not None:
            cliente_id = cliente_id_autenticado
        datos = data.buscar_tiendas_por_cliente(cursor, cliente_id)
        return jsonify(datos), 200

    except PermissionError as e:
        return jsonify({'error': str(e)}), 403
    except Exception as e:
        return jsonify({"error": "Error al consultar las tiendas.", "detalle": str(e)}), 500

    finally:
        cursor.close()
        conexion.close()


@solicitud_retroactivo_bp.route('/api/solicitud-retroactivo/listar', methods=['GET'])
@requiere_autenticacion
@requiere_rol(1)
def listar_solicitudes():
    # if not _requiere_admin(request):
    #     return jsonify({"error": "No autorizado"}), 403

    conexion = obtener_conexion()
    if not conexion:
        return jsonify({"error": "No se pudo conectar a la base de datos."}), 500

    cursor = conexion.cursor(dictionary=True, buffered=True)
    try:
        filas = data.listar_ventas(cursor)

        # Firma las URLs al vuelo (las guardadas al momento del registro expiran)
        # y arma el detalle de validación por archivo + el estatus general.
        for fila in filas:
            validacion_docs = _parsear_validacion_docs(fila.pop('validacion_docs_json'))
            fila['historial'] = _parsear_historial(fila.pop('historial_json'))
            fila['archivos'] = {}
            for campo, nombre in (
                ('ticket_compra_key', 'ticket_compra'), ('voucher_key', 'voucher'),
                ('factura_pdf_key', 'factura_pdf'), ('factura_xml_key', 'factura_xml'),
            ):
                key = fila.pop(campo)
                fila['archivos'][nombre] = {
                    "key": key,
                    "url": generar_url_firmada_s3(key) if key else None,
                    "estatus": validacion_docs.get(nombre, 'pendiente')
                }
            fila['estatus'] = _calcular_estatus(validacion_docs, tiene_factura_xml=bool(fila['archivos']['factura_xml']['key']))
            if _debe_ocultar_montos_retroactivos():
                _redactar_montos_solicitud(fila)

        return jsonify(filas), 200

    except Exception as e:
        logging.exception("Error al listar solicitudes de retroactivo: %s", e)
        return jsonify({"error": "Error al listar las solicitudes.", "detalle": str(e)}), 500
    finally:
        cursor.close()
        conexion.close()


@solicitud_retroactivo_bp.route('/api/solicitud-retroactivo/dashboard', methods=['GET'])
@requiere_autenticacion
@requiere_rol(1)
def dashboard_solicitudes():
    # if not _requiere_admin(request):
        # return jsonify({"error": "No autorizado"}), 403

    conexion = obtener_conexion()
    if not conexion:
        return jsonify({"error": "No se pudo conectar a la base de datos."}), 500

    cursor = conexion.cursor(dictionary=True, buffered=True)
    try:
        totales_generales = data.obtener_totales_generales(cursor)

        # GUÍA: pendientes/validados/rechazados ya no viven en la columna
        # 'validado' (esa quedó sin usar desde que la validación pasó a ser
        # por archivo) -- se derivan de validacion_docs_json en Python, igual
        # que en listar_solicitudes/mis_solicitudes.
        conteo = {"pendiente": 0, "validado": 0, "rechazado": 0}
        for fila in data.obtener_validaciones_docs(cursor):
            estatus = _calcular_estatus(
                _parsear_validacion_docs(fila['validacion_docs_json']),
                tiene_factura_xml=bool(fila.get('factura_xml_key'))
            )
            conteo[estatus] += 1
        totales_generales['pendientes'] = conteo['pendiente']
        totales_generales['validados'] = conteo['validado']
        totales_generales['rechazados'] = conteo['rechazado']

        por_campana = data.obtener_dashboard_por_campana(cursor)
        por_cliente = data.obtener_dashboard_por_cliente(cursor)
        por_anio_modelo = data.obtener_dashboard_por_anio_modelo(cursor)
        por_producto = data.obtener_dashboard_por_producto(cursor)

        return jsonify({
            "totales_generales": totales_generales,
            "por_campana": por_campana,
            "por_cliente": por_cliente,
            "por_anio_modelo": por_anio_modelo,
            "por_producto": por_producto
        }), 200

    except Exception as e:
        logging.exception("Error al generar dashboard de solicitudes de retroactivo: %s", e)
        return jsonify({"error": "Error al generar el dashboard.", "detalle": str(e)}), 500
    finally:
        cursor.close()
        conexion.close()


@solicitud_retroactivo_bp.route('/api/solicitud-retroactivo/validar-documento/<int:id_venta>', methods=['POST'])
@requiere_autenticacion
@requiere_rol(1)
def validar_documento(id_venta):
    # if not _requiere_admin(request):
        # return jsonify({"error": "No autorizado"}), 403

    body = request.get_json(force=True, silent=True) or {}
    documento = body.get('documento')
    estatus_doc = body.get('estatus')

    if documento not in ARCHIVOS_REQUERIDOS:
        return jsonify({"error": "Documento inválido."}), 400
    if estatus_doc not in ('valido', 'rechazado'):
        return jsonify({"error": "Estatus inválido, debe ser 'valido' o 'rechazado'."}), 400

    conexion = obtener_conexion()
    if not conexion:
        return jsonify({"error": "No se pudo conectar a la base de datos."}), 500

    payload = _usuario_desde_token(request)
    nombre_usuario = payload.get('nombre') or payload.get('usuario') or f"uid:{payload.get('id')}"\
        if payload else None

    cursor = conexion.cursor(dictionary=True, buffered=True)
    try:
        fila = data.obtener_venta_para_validacion(cursor, id_venta)
        if not fila:
            return jsonify({"error": "Solicitud no encontrada."}), 404

        # GUÍA: no se puede validar/rechazar un documento que el distribuidor
        # nunca subió (ej. factura_xml, que es opcional) -- el frontend ya
        # oculta esos botones, esto es el respaldo del lado del servidor.
        if not fila.get(f'{documento}_key'):
            return jsonify({"error": "Este documento no fue subido, no se puede validar."}), 400

        validacion_docs = _parsear_validacion_docs(fila['validacion_docs_json'])
        historial = _parsear_historial(fila['historial_json'])
        etiqueta_doc = ARCHIVOS_REQUERIDOS[documento]

        # GUÍA: click sobre el MISMO estatus que ya tiene = deshacer (vuelve
        # a 'pendiente'), no duplicar. Antes cada click agregaba una entrada
        # nueva al historial aunque no hubiera cambio real -- clicks
        # repetidos (doble click, red lenta) inundaban el historial con la
        # misma entrada una y otra vez.
        if validacion_docs.get(documento) == estatus_doc:
            validacion_docs.pop(documento, None)
            historial.append(_entrada_historial('validacion', f"{etiqueta_doc}: deshecho (vuelve a pendiente)", usuario=nombre_usuario))
        else:
            validacion_docs[documento] = estatus_doc
            etiqueta_estatus = 'validado' if estatus_doc == 'valido' else 'rechazado'
            historial.append(_entrada_historial('validacion', f"{etiqueta_doc}: {etiqueta_estatus}", usuario=nombre_usuario))

        data.actualizar_validacion_documento(cursor, id_venta, json.dumps(validacion_docs), json.dumps(historial))
        conexion.commit()

        return jsonify({
            "ok": True,
            "id": id_venta,
            "validacion_docs": validacion_docs,
            "estatus": _calcular_estatus(validacion_docs, tiene_factura_xml=bool(fila.get('factura_xml_key'))),
            "historial": historial
        }), 200

    except Exception as e:
        conexion.rollback()
        logging.exception("Error al validar documento de solicitud %s: %s", id_venta, e)
        return jsonify({"error": "Error al actualizar la solicitud.", "detalle": str(e)}), 500
    finally:
        cursor.close()
        conexion.close()


# GUÍA: el precio lo escribe el cliente a mano en el formulario y a veces se
# equivoca (typo, número mal leído del ticket). En vez de rechazar TODA la
# solicitud por un dato que el admin puede verificar contra la factura/ticket
# adjuntos, el admin lo corrige directo aquí -- no pasa por el flujo de
# rechazo/reenvío de archivos. Recalcula monto_pagar/monto_aplicar con el
# mismo porcentaje ya guardado (el % depende del plan MSI, no del precio).
@solicitud_retroactivo_bp.route('/api/solicitud-retroactivo/nota-credito/<int:id_venta>', methods=['POST'])
@requiere_autenticacion
@requiere_rol(1)
def corregir_nota_credito(id_venta):
    # if not _requiere_admin(request):
    #     return jsonify({"error": "No autorizado"}), 403

    body = request.get_json(force=True, silent=True) or {}
    nueva_nota_credito = body.get('nota_credito')
    if nueva_nota_credito is None:
        return jsonify({"error": "Falta nota_credito."}), 400

    conexion = obtener_conexion()
    if not conexion:
        return jsonify({"error": "No se pudo conectar a la base de datos."}), 500

    payload = _usuario_desde_token(request)
    nombre_usuario = payload.get('nombre') or payload.get('usuario') or f"uid:{payload.get('id')}"\
        if payload else None

    cursor = conexion.cursor(dictionary=True, buffered=True)
    try:
        fila = data.obtener_venta_para_nota_credito(cursor, id_venta)
        if not fila:
            return jsonify({"error": "Solicitud no encontrada."}), 404

        nota_anterior = fila.get('nota_credito')
        
        # Evalúa si existía una nota de crédito previa para dar un texto más natural sin 'None' ni comillas
        if not nota_anterior or str(nota_anterior).strip().lower() in ('none', '', '0'):
            desc_historial = f"Nota de crédito asignada: #{nueva_nota_credito}"
        else:
            desc_historial = f"Nota de crédito actualizada de #{nota_anterior} a #{nueva_nota_credito}"

        historial = _parsear_historial(fila['historial_json'])
        historial.append(_entrada_historial(
            'nota_credito',
            desc_historial,
            usuario=nombre_usuario
        ))

        data.actualizar_nota_credito(cursor, id_venta, nueva_nota_credito, json.dumps(historial))
        conexion.commit()

        return jsonify({
            "ok": True,
            "id": id_venta,
            "nota_credito": str(nueva_nota_credito),
            "nota_credito_estatus": "pendiente",
            "historial": historial
        }), 200

    except Exception as e:
        conexion.rollback()
        logging.exception("Error al corregir nota de crédito de solicitud %s: %s", id_venta, e)
        return jsonify({"error": "Error al actualizar la nota de crédito.", "detalle": str(e)}), 500
    finally:
        cursor.close()
        conexion.close()


# GUÍA: Banca y Pagos (BCYP) captura la nota de crédito arriba en
# corregir_nota_credito, pero solo Auditoría puede validarla -- por eso
# este endpoint pide un código numérico (ver utils/auditoria_utils.py) en
# vez de solo requerir estar logueado como admin.
@solicitud_retroactivo_bp.route('/api/solicitud-retroactivo/nota-credito/<int:id_venta>/validar', methods=['POST'])
@requiere_autenticacion
@requiere_rol(1)
def validar_nota_credito(id_venta):
    body = request.get_json(force=True, silent=True) or {}
    codigo = body.get('codigo')

    if not verificar_codigo_auditoria(codigo):
        return jsonify({"error": "Código de Auditoría inválido."}), 401

    conexion = obtener_conexion()
    if not conexion:
        return jsonify({"error": "No se pudo conectar a la base de datos."}), 500

    payload = _usuario_desde_token(request)
    nombre_usuario = payload.get('nombre') or payload.get('usuario') or f"uid:{payload.get('id')}"\
        if payload else None

    cursor = conexion.cursor(dictionary=True, buffered=True)
    try:
        fila = data.obtener_venta_para_nota_credito(cursor, id_venta)
        if not fila:
            return jsonify({"error": "Solicitud no encontrada."}), 404

        if not fila.get('nota_credito'):
            return jsonify({"error": "Esta solicitud todavía no tiene una nota de crédito capturada."}), 400

        historial = _parsear_historial(fila['historial_json'])
        historial.append(_entrada_historial(
            'nota_credito',
            f"Nota de crédito #{fila['nota_credito']} validada por Auditoría",
            usuario=nombre_usuario
        ))

        data.validar_nota_credito(cursor, id_venta, json.dumps(historial))
        conexion.commit()

        return jsonify({
            "ok": True,
            "id": id_venta,
            "nota_credito_estatus": "validada",
            "historial": historial
        }), 200

    except Exception as e:
        conexion.rollback()
        logging.exception("Error al validar nota de crédito de solicitud %s: %s", id_venta, e)
        return jsonify({"error": "Error al validar la nota de crédito.", "detalle": str(e)}), 500
    finally:
        cursor.close()
        conexion.close()


@solicitud_retroactivo_bp.route('/api/solicitud-retroactivo/precio/<int:id_venta>', methods=['POST'])
@requiere_autenticacion
@requiere_rol(1)
def corregir_precio(id_venta):
    # if not _requiere_admin(request):
    #     return jsonify({"error": "No autorizado"}), 403

    body = request.get_json(force=True, silent=True) or {}
    nuevo_precio_raw = body.get('precio_publico')
    if nuevo_precio_raw is None:
        return jsonify({"error": "Falta precio_publico."}), 400

    try:
        nuevo_precio = Decimal(str(nuevo_precio_raw).replace(',', ''))
        if nuevo_precio < 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        return jsonify({"error": "precio_publico inválido."}), 400

    conexion = obtener_conexion()
    if not conexion:
        return jsonify({"error": "No se pudo conectar a la base de datos."}), 500

    payload = _usuario_desde_token(request)
    nombre_usuario = payload.get('nombre') or payload.get('usuario') or f"uid:{payload.get('id')}"\
        if payload else None

    cursor = conexion.cursor(dictionary=True, buffered=True)
    try:
        fila = data.obtener_venta_para_precio(cursor, id_venta)
        if not fila:
            return jsonify({"error": "Solicitud no encontrada."}), 404

        porcentaje = fila['porcentaje'] or Decimal('0')
        monto = (nuevo_precio * porcentaje) / Decimal(100)

        historial = _parsear_historial(fila['historial_json'])
        historial.append(_entrada_historial(
            'precio',
            f"Precio corregido de ${fila['precio_publico']} a ${nuevo_precio}",
            usuario=nombre_usuario
        ))

        data.actualizar_precio(cursor, id_venta, nuevo_precio, monto, monto, json.dumps(historial))
        conexion.commit()

        return jsonify({
            "ok": True,
            "id": id_venta,
            "precio_publico": str(nuevo_precio),
            "monto_pagar": str(monto),
            "monto_aplicar": str(monto),
            "historial": historial
        }), 200

    except Exception as e:
        conexion.rollback()
        logging.exception("Error al corregir precio de solicitud %s: %s", id_venta, e)
        return jsonify({"error": "Error al actualizar el precio.", "detalle": str(e)}), 500
    finally:
        cursor.close()
        conexion.close()


@solicitud_retroactivo_bp.route('/api/solicitud-retroactivo/mis-solicitudes', methods=['GET'])
@requiere_autenticacion
@requiere_modulos('usuarios_retroactivos', 'usuarios_solicitudes_retroactivos')
def mis_solicitudes():
    conexion = obtener_conexion()
    if not conexion:
        return jsonify({"error": "No se pudo conectar a la base de datos."}), 500

    cursor = conexion.cursor(dictionary=True, buffered=True)
    try:
        filas = data.listar_mis_ventas(cursor, g.usuario_actual['id'])

        # GUÍA: el cliente ve exactamente lo mismo que el admin en el Gestor
        # (todos los campos, estatus por archivo con su URL para previsualizar
        # lo que subió, e historial de cambios) -- solo que de sus propias
        # solicitudes. Necesita saber EXACTAMENTE qué archivo(s) fueron
        # rechazados para poder resubir solo esos (ver PUT /venta/<id>).
        for fila in filas:
            validacion_docs = _parsear_validacion_docs(fila.pop('validacion_docs_json'))
            fila['validacion_docs'] = validacion_docs
            fila['historial'] = _parsear_historial(fila.pop('historial_json'))

            fila['archivos'] = {}
            for campo, nombre in (
                ('ticket_compra_key', 'ticket_compra'), ('voucher_key', 'voucher'),
                ('factura_pdf_key', 'factura_pdf'), ('factura_xml_key', 'factura_xml'),
            ):
                key = fila.pop(campo)
                fila['archivos'][nombre] = {
                    "key": key,
                    "url": generar_url_firmada_s3(key) if key else None,
                    "estatus": validacion_docs.get(nombre, 'pendiente')
                }
            fila['estatus'] = _calcular_estatus(validacion_docs, tiene_factura_xml=bool(fila['archivos']['factura_xml']['key']))

            if _debe_ocultar_montos_retroactivos():
                _redactar_montos_solicitud(fila)

        return jsonify(filas), 200
    except Exception as e:
        logging.exception("Error al listar mis-solicitudes: %s", e)
        return jsonify({"error": "Error al consultar tus solicitudes.", "detalle": str(e)}), 500
    finally:
        cursor.close()
        conexion.close()


@solicitud_retroactivo_bp.route('/api/solicitud-retroactivo/dashboard-distribuidor', methods=['GET'])
@requiere_autenticacion
@requiere_rol(2, 3)
@requiere_modulos('usuarios_retroactivos', 'usuarios_solicitudes_retroactivos')
def dashboard_distribuidor():
    """Resumen y detalle de solicitudes del cliente del usuario autenticado."""
    conexion = obtener_conexion()
    if not conexion:
        return jsonify({'error': 'No se pudo conectar a la base de datos.'}), 500

    cursor = conexion.cursor(dictionary=True, buffered=True)
    try:
        cliente_id = _cliente_id_autenticado(cursor)
        if cliente_id is None:
            return jsonify({'error': 'El usuario autenticado no tiene un cliente válido asociado.'}), 403

        solicitudes = data.listar_ventas_por_cliente(cursor, cliente_id)
        ocultar_montos = _debe_ocultar_montos_retroactivos()
        totales = {
            'total_solicitudes': len(solicitudes),
            'pendientes': 0,
            'validadas': 0,
            'rechazadas': 0,
            'notas_credito_capturadas': 0,
            'notas_credito_validadas': 0,
            'bicicletas_aplicadas': 0,
        }
        monto_total_estimado = Decimal('0')
        monto_total_aplicado = Decimal('0')
        notas_por_numero = {}

        for solicitud in solicitudes:
            validacion_docs = _parsear_validacion_docs(solicitud.pop('validacion_docs_json'))
            solicitud['validacion_docs'] = validacion_docs
            solicitud['historial'] = _parsear_historial(solicitud.pop('historial_json'))
            solicitud['archivos'] = {}
            for campo, nombre in (
                ('ticket_compra_key', 'ticket_compra'), ('voucher_key', 'voucher'),
                ('factura_pdf_key', 'factura_pdf'), ('factura_xml_key', 'factura_xml'),
            ):
                key = solicitud.pop(campo)
                solicitud['archivos'][nombre] = {
                    'key': key,
                    'url': generar_url_firmada_s3(key) if key else None,
                    'estatus': validacion_docs.get(nombre, 'pendiente')
                }

            solicitud['estatus'] = _calcular_estatus(
                validacion_docs,
                tiene_factura_xml=bool(solicitud['archivos']['factura_xml']['key'])
            )
            totales[f"{solicitud['estatus']}s"] += 1
            monto_solicitud = Decimal(str(solicitud.get('monto_pagar') or 0))
            monto_total_estimado += monto_solicitud

            if (
                _tiene_nota_credito(solicitud.get('nota_credito'))
                and solicitud.get('nota_credito_estatus') == 'validada'
            ):
                totales['bicicletas_aplicadas'] += 1
                monto_total_aplicado += monto_solicitud

            if _tiene_nota_credito(solicitud.get('nota_credito')):
                numero_nc = str(solicitud['nota_credito']).strip()
                nota = notas_por_numero.setdefault(numero_nc, {
                    'numero_nota_credito': numero_nc,
                    'estatuses': [],
                    'cantidad_solicitudes_relacionadas': 0,
                    'monto_asociado_estimado': Decimal('0'),
                })
                nota['estatuses'].append(solicitud.get('nota_credito_estatus'))
                nota['cantidad_solicitudes_relacionadas'] += 1
                nota['monto_asociado_estimado'] += monto_solicitud

            if ocultar_montos:
                _redactar_montos_solicitud(solicitud)

        notas_credito = []
        for nota in notas_por_numero.values():
            estado = 'validada' if all(estatus == 'validada' for estatus in nota.pop('estatuses')) else 'pendiente'
            nota['estado'] = estado
            if ocultar_montos:
                nota.pop('monto_asociado_estimado')
            else:
                nota['monto_asociado_estimado'] = str(nota['monto_asociado_estimado'])
            notas_credito.append(nota)

        notas_credito.sort(key=lambda nota: nota['numero_nota_credito'])
        totales['notas_credito_capturadas'] = len(notas_credito)
        totales['notas_credito_validadas'] = sum(
            1 for nota in notas_credito if nota['estado'] == 'validada'
        )
        if not ocultar_montos:
            totales['monto_total_estimado'] = str(monto_total_estimado)
            totales['monto_total_aplicado'] = f'{monto_total_aplicado:.2f}'

        return jsonify({
            'totales': totales,
            'notas_credito': notas_credito,
            'solicitudes': solicitudes,
        }), 200

    except PermissionError as e:
        return jsonify({'error': str(e)}), 403
    except Exception as e:
        logging.exception('Error al generar el dashboard del distribuidor: %s', e)
        return jsonify({'error': 'Error al consultar el dashboard del distribuidor.'}), 500
    finally:
        cursor.close()
        conexion.close()


@solicitud_retroactivo_bp.route('/api/solicitud-retroactivo/venta/<int:id_venta>', methods=['PUT'])
@requiere_autenticacion
@requiere_modulos('usuarios_retroactivos', 'usuarios_solicitudes_retroactivos')
def editar_venta(id_venta):
    campos_obligatorios = [
        'id_formulario', 'id_msi', 'nombre_sucursal',
        'correo_electronico', 'nombre_completo', 'fecha_venta',
        'modelo_bicicleta', 'numero_serie', 'precio_publico'
    ]
    datos = {campo: request.form.get(campo) for campo in campos_obligatorios}
    datos['id_marca_bicicleta'] = request.form.get('id_marca_bicicleta')

    faltantes = [campo for campo, valor in datos.items() if campo in campos_obligatorios and not valor]
    if faltantes:
        return jsonify({"error": "Campos de texto faltantes", "campos": faltantes}), 400

    conexion = obtener_conexion()
    if not conexion:
        return jsonify({"error": "No se pudo conectar a la base de datos."}), 500

    cursor = conexion.cursor(dictionary=True, buffered=True)

    try:
        # GUÍA: solo el dueño de la solicitud puede reeditarla, y solo
        # mientras esté rechazada (algún documento marcado 'rechazado').
        # Solo se piden/reemplazan los archivos rechazados -- los que ya
        # estaban 'valido' se quedan tal cual en S3, no hace falta resubirlos.
        fila = data.obtener_venta_para_edicion(cursor, id_venta)
        if not fila:
            return jsonify({"error": "Solicitud no encontrada."}), 404
        if fila['id_usuario'] != g.usuario_actual['id']:
            return jsonify({"error": "No autorizado"}), 403

        validacion_docs = _parsear_validacion_docs(fila['validacion_docs_json'])
        if _calcular_estatus(validacion_docs) != 'rechazado':
            return jsonify({"error": "Solo se pueden editar solicitudes con algún archivo rechazado."}), 400

        docs_a_resubir = [k for k in ARCHIVOS_REQUERIDOS if validacion_docs.get(k) == 'rechazado']
        historial = _parsear_historial(fila['historial_json'])
    except Exception as e:
        logging.exception("Error al validar propiedad de la solicitud %s: %s", id_venta, e)
        return jsonify({"error": "Error al verificar la solicitud.", "detalle": str(e)}), 500
    finally:
        cursor.close()
        conexion.close()

    faltantes_archivos = []
    for key_archivo in docs_a_resubir:
        file = request.files.get(key_archivo)
        if key_archivo in ['factura_pdf', 'factura_xml'] and (not file or not file.filename):
            continue
        if not file or not file.filename:
            faltantes_archivos.append(key_archivo)
        elif not allowed_file(file.filename):
            return jsonify({"error": f"Tipo de archivo no permitido para: {key_archivo}"}), 400
    if faltantes_archivos:
        return jsonify({
            "error": "Faltan los archivos rechazados por corregir",
            "campos": faltantes_archivos
        }), 400

    # Subida a S3 primero (mismo motivo que en registrar_venta: la key en BD
    # debe ser la key real que S3 asigna, no una fabricada). Solo se suben
    # los archivos que estaban rechazados.
    archivos_procesados = {}
    keys_archivos = {}
    try:
        for key_archivo in docs_a_resubir:
            file = request.files.get(key_archivo)
            if key_archivo in ['factura_pdf', 'factura_xml'] and (not file or not file.filename):
                keys_archivos[key_archivo] = None
                continue
                
            resultado = subir_archivo_s3(file)
            keys_archivos[key_archivo] = resultado["key"]
            archivos_procesados[key_archivo] = {
                "key": resultado["key"],
                "original": resultado["original"],
                "url": generar_url_firmada_s3(resultado["key"]),
                "storage": "s3"
            }
    except Exception as e:
        logging.exception("Error al subir archivos a S3 al reeditar la venta %s: %s", id_venta, e)
        return jsonify({"error": "Ocurrió un error al subir los archivos."}), 500

    conexion = obtener_conexion()
    if not conexion:
        return jsonify({"error": "No se pudo conectar a la base de datos."}), 500

    cursor = conexion.cursor(dictionary=True, buffered=True)
    try:
        raw_marca = datos.get('id_marca_bicicleta')
        id_marca = int(raw_marca) if raw_marca and str(raw_marca).isdigit() else None

        id_msi_seleccionado = int(datos['id_msi'])
        id_formulario_seleccionado = int(datos['id_formulario'])
        resultado = data.obtener_porcentaje_campania_msi(cursor, id_formulario_seleccionado, id_msi_seleccionado)
        if not resultado:
            return jsonify({"error": "El plazo MSI seleccionado no aplica para esta campaña."}), 400
        porcentaje = Decimal(str(resultado['porcentaje']))

        precio_publico = Decimal(str(datos['precio_publico']).replace(',', ''))
        monto_pagar = (precio_publico * porcentaje) / Decimal(100)
        monto_aplicar = monto_pagar

        # Los archivos resubidos vuelven a 'pendiente' (se quitan del dict);
        # los que ya estaban 'valido' se quedan intactos.
        for key_archivo in docs_a_resubir:
            validacion_docs.pop(key_archivo, None)

        etiquetas_resubidas = ', '.join(ARCHIVOS_REQUERIDOS[k] for k in docs_a_resubir)
        historial.append(_entrada_historial('reenvio', f"Cliente reenvió: {etiquetas_resubidas}"))

        columnas_archivo = ', '.join(f"{k}_key = %s" for k in docs_a_resubir)
        valores_archivo = [keys_archivos[k] for k in docs_a_resubir]

        valores_fijos = (
            int(datos['id_formulario']), id_marca, id_msi_seleccionado,
            datos['nombre_sucursal'], datos['correo_electronico'], datos['nombre_completo'],
            datos['fecha_venta'], datos['modelo_bicicleta'], datos['numero_serie'],
            precio_publico, porcentaje, monto_pagar, monto_aplicar,
            json.dumps(validacion_docs), json.dumps(historial),
        )
        data.actualizar_venta(cursor, id_venta, valores_fijos, columnas_archivo, valores_archivo)
        conexion.commit()

        return jsonify({
            "respuesta": True,
            "mensaje": "Solicitud actualizada y enviada de nuevo a revisión.",
            "archivos": archivos_procesados,
            "historial": historial
        }), 200

    except Exception as e:
        conexion.rollback()
        logging.exception("Error en BD al editar la venta %s: %s", id_venta, e)
        return jsonify({"error": "Los archivos se subieron pero ocurrió un error al actualizar la venta.", "detalle": str(e)}), 500
    finally:
        cursor.close()
        conexion.close()
