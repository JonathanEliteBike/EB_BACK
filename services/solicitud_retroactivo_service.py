"""Capa de acceso a datos (Data Access) para Solicitud de Retroactivos.

Cada función recibe un cursor ya abierto (dictionary=True, buffered=True,
mismo cursor que routes/solicitud_retroactivo.py ya creaba) y ejecuta
exactamente el mismo SQL/stored procedure que antes vivía inline en la
ruta. No valida, no calcula montos, no decide respuestas HTTP ni hace
commit/rollback -- esa lógica de negocio y el manejo de la conexión se
quedan en las rutas, sin cambios.
"""

from utils.odoo_utils import get_odoo_models, ODOO_DB, ODOO_PASSWORD


class SeriesOdooError(Exception):
    """Error esperado al consultar series entregadas en Odoo.

    ``codigo`` permite que la ruta responda cada caso de negocio sin
    convertirlo en un error técnico genérico.
    """

    def __init__(self, codigo, mensaje, http_status=502):
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje
        self.http_status = http_status

# GUÍA: MY27 corre del 1-jul-2026 al 30-jun-2027; MY28 el mismo rango un año
# después, etc. No es un campo capturado -- se deriva de fecha_venta.
ANIO_MODELO_SQL = """
    CASE
        WHEN MONTH(v.fecha_venta) >= 7 THEN CONCAT('MY', LPAD((YEAR(v.fecha_venta) + 1) % 100, 2, '0'))
        ELSE CONCAT('MY', LPAD(YEAR(v.fecha_venta) % 100, 2, '0'))
    END
"""


# GUÍA: el % retroactivo ya no es fijo por plazo MSI -- cada campaña liga
# los plazos que le aplican con SU PROPIO % (ver
# solicitud_retroactivo_campania_msi, tabla del módulo de Campañas). El
# formulario de venta ahora usa esto en vez de obtener_porcentaje_msi.
def obtener_porcentaje_campania_msi(cursor, id_campania, id_msi):
    cursor.execute("""
        SELECT porcentaje
        FROM solicitud_retroactivo_campania_msi
        WHERE campania_id = %s AND msi_id = %s
    """, (id_campania, id_msi))
    return cursor.fetchone()


def listar_campanias_activas(cursor):
    """Campañas vigentes hoy (activa=1 y dentro de su rango de fechas) --
    para el selector de "Campaña" del formulario de venta."""
    cursor.execute("""
        SELECT id, nombre
        FROM solicitud_retroactivo_campanias
        WHERE activa = 1
          AND CURDATE() BETWEEN fecha_inicio AND fecha_fin
        ORDER BY nombre ASC
    """)
    return cursor.fetchall()


def listar_msi_por_campania(cursor, id_campania):
    """Plazos MSI ligados a una campaña específica, con el % propio de esa
    campaña -- para el selector de "Meses sin intereses" del formulario de
    venta, que depende de qué campaña se eligió."""
    cursor.execute("""
        SELECT m.id, m.plazo_meses, cm.porcentaje
        FROM solicitud_retroactivo_campania_msi cm
        JOIN solicitud_retroactivo_msi m ON m.id = cm.msi_id
        WHERE cm.campania_id = %s
        ORDER BY m.plazo_meses ASC
    """, (id_campania,))
    return cursor.fetchall()


def obtener_productos_por_campania(cursor, id_campania):
    """Productos detalle (variantes/SKUs) ligados a una campaña -- para el
    selector de "Modelo" del formulario de venta, que solo debe ofrecer los
    productos que esa campaña realmente incluye (no el catálogo completo)."""
    cursor.execute("""
        SELECT
            pd.id,
            pd.sku,
            p.modelo,
            p.codigo,
            pd.talla,
            pd.color,
            m.id AS marca_id,
            m.nombre AS marca
        FROM solicitud_retroactivo_campania_producto_detalle cp
        JOIN producto_detalle pd ON pd.id = cp.producto_detalle_id
        JOIN productos p ON p.codigo = pd.codigo_producto
        LEFT JOIN solicitud_retroactivo_marca m ON m.id = p.marca_id
        WHERE cp.campania_id = %s
        ORDER BY p.modelo ASC, pd.sku ASC
    """, (id_campania,))
    return cursor.fetchall()


def obtener_campania_vigente(cursor, id_campania):
    """Confirma que la campaña puede recibir solicitudes hoy."""
    cursor.execute("""
        SELECT id
        FROM solicitud_retroactivo_campanias
        WHERE id = %s
          AND activa = 1
          AND CURDATE() BETWEEN fecha_inicio AND fecha_fin
    """, (id_campania,))
    return cursor.fetchone()


def obtener_producto_detalle_de_campania(cursor, id_campania, producto_detalle_id):
    """Obtiene el SKU autoritativo de una variante ligada a la campaña."""
    cursor.execute("""
        SELECT pd.id, pd.sku
        FROM solicitud_retroactivo_campania_producto_detalle cp
        INNER JOIN producto_detalle pd ON pd.id = cp.producto_detalle_id
        WHERE cp.campania_id = %s
          AND cp.producto_detalle_id = %s
    """, (id_campania, producto_detalle_id))
    return cursor.fetchone()


def obtener_marcas_por_campania(cursor, id_campania):
    """Marcas distintas entre los productos ligados a una campaña -- el
    selector de "Marca" del formulario de venta solo debe activarse cuando
    la campaña realmente mezcla 2 o más marcas (ej. campaña "Multimarca");
    si es de una sola marca, no tiene sentido preguntarla."""
    cursor.execute("""
        SELECT DISTINCT m.id, m.nombre
        FROM solicitud_retroactivo_campania_producto_detalle cp
        JOIN producto_detalle pd ON pd.id = cp.producto_detalle_id
        JOIN productos p ON p.codigo = pd.codigo_producto
        JOIN solicitud_retroactivo_marca m ON m.id = p.marca_id
        WHERE cp.campania_id = %s
        ORDER BY m.nombre ASC
    """, (id_campania,))
    return cursor.fetchall()


def crear_venta(cursor, parametros):
    cursor.callproc('sp_solicitud_retroactivo_crear_venta', parametros)
    while cursor.nextset():
        pass


def obtener_id_por_numero_serie(cursor, numero_serie):
    cursor.execute(
        "SELECT id FROM solicitud_retroactivo_venta WHERE numero_serie = %s",
        (numero_serie,)
    )
    return cursor.fetchone()


def guardar_historial_inicial(cursor, id_venta, historial_json):
    cursor.execute(
        "UPDATE solicitud_retroactivo_venta SET historial_json = %s WHERE id = %s",
        (historial_json, id_venta)
    )


def buscar_msi(cursor):
    cursor.callproc('sp_solicitud_retroactivo_buscar_msi')
    datos = []
    for resultado in cursor.stored_results():
        datos = resultado.fetchall()
    while cursor.nextset():
        pass
    return datos


def buscar_marca(cursor):
    cursor.callproc('sp_solicitud_retroactivo_buscar_marca')
    datos = []
    for resultado in cursor.stored_results():
        datos = resultado.fetchall()
    while cursor.nextset():
        pass
    return datos


def buscar_razones_sociales(cursor):
    cursor.execute("""
        SELECT DISTINCT c.id, c.nombre_cliente, c.clave
        FROM clientes c
        INNER JOIN usuarios u ON u.cliente_id = c.id
        WHERE u.rol_id = 2
          AND c.nombre_cliente IS NOT NULL
          AND c.nombre_cliente != ''
        ORDER BY c.nombre_cliente ASC
    """)
    datos = cursor.fetchall()
    if not datos:
        cursor.execute("""
            SELECT DISTINCT c.id, c.nombre_cliente, c.clave
            FROM clientes c
            INNER JOIN usuarios u ON u.cliente_id = c.id
            WHERE u.rol_id = 2
            ORDER BY c.nombre_cliente ASC
        """)
        datos = cursor.fetchall()
    return datos


def asegurar_tabla_tiendas(cursor):
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tiendas (
                id INT AUTO_INCREMENT PRIMARY KEY,
                nombre VARCHAR(255) NOT NULL,
                cliente_id INT NOT NULL,
                KEY idx_cliente (cliente_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)
    except Exception:
        pass


def buscar_tiendas_por_cliente(cursor, cliente_id):
    asegurar_tabla_tiendas(cursor)
    cursor.execute("""
        SELECT id, nombre, cliente_id
        FROM tiendas
        WHERE cliente_id = %s
        ORDER BY nombre ASC
    """, (cliente_id,))
    return cursor.fetchall()


def obtener_nombres_cliente_y_tienda(cursor, cliente_id, tienda_id):
    nombre_cliente = None
    nombre_sucursal = None

    if cliente_id:
        cursor.execute("SELECT nombre_cliente FROM clientes WHERE id = %s", (cliente_id,))
        res_c = cursor.fetchone()
        if res_c:
            nombre_cliente = res_c.get('nombre_cliente')

    if tienda_id:
        asegurar_tabla_tiendas(cursor)
        cursor.execute("SELECT nombre FROM tiendas WHERE id = %s", (tienda_id,))
        res_t = cursor.fetchone()
        if res_t:
            nombre_sucursal = res_t.get('nombre')

    return nombre_cliente, nombre_sucursal


def listar_ventas(cursor):
    cursor.execute(f"""
        SELECT
            v.id, v.id_usuario, v.id_formulario, f.nombre AS nombre_formulario,
            v.id_marca_bicicleta, v.id_msi, m.plazo_meses,
            v.nombre_sucursal, v.correo_electronico, v.nombre_completo,
            v.fecha_venta, v.modelo_bicicleta, v.numero_serie,
            v.precio_publico, v.porcentaje, v.monto_pagar, v.monto_aplicar,
            v.nota_credito, v.nota_credito_estatus,
            v.validacion_docs_json, v.historial_json, {ANIO_MODELO_SQL} AS anio_modelo,
            v.ticket_compra_key, v.voucher_key, v.factura_pdf_key, v.factura_xml_key,
            v.fecha_registro
        FROM solicitud_retroactivo_venta v
        LEFT JOIN solicitud_retroactivo_campanias f ON f.id = v.id_formulario
        LEFT JOIN solicitud_retroactivo_msi m ON m.id = v.id_msi
        ORDER BY v.fecha_registro DESC
    """)
    return cursor.fetchall()


def obtener_totales_generales(cursor):
    cursor.execute("""
        SELECT
            COUNT(*) AS total_solicitudes,
            COALESCE(SUM(monto_pagar), 0) AS monto_total_pagar,
            COALESCE(SUM(monto_aplicar), 0) AS monto_total_aplicar
        FROM solicitud_retroactivo_venta
    """)
    return cursor.fetchone()


def obtener_validaciones_docs(cursor):
    cursor.execute("SELECT validacion_docs_json, factura_xml_key FROM solicitud_retroactivo_venta")
    return cursor.fetchall()


def obtener_dashboard_por_campana(cursor):
    cursor.execute("""
        SELECT
            v.id_formulario, COALESCE(f.nombre, 'Sin campaña') AS nombre_formulario,
            COUNT(*) AS total_solicitudes, COALESCE(SUM(v.monto_pagar), 0) AS monto_total
        FROM solicitud_retroactivo_venta v
        LEFT JOIN solicitud_retroactivo_campanias f ON f.id = v.id_formulario
        GROUP BY v.id_formulario, f.nombre
        ORDER BY monto_total DESC
    """)
    return cursor.fetchall()


def obtener_dashboard_por_cliente(cursor):
    cursor.execute("""
        SELECT
            nombre_completo, correo_electronico,
            COUNT(*) AS total_solicitudes, COALESCE(SUM(monto_pagar), 0) AS monto_total
        FROM solicitud_retroactivo_venta
        GROUP BY nombre_completo, correo_electronico
        ORDER BY monto_total DESC
    """)
    return cursor.fetchall()


def obtener_dashboard_por_anio_modelo(cursor):
    cursor.execute(f"""
        SELECT
            {ANIO_MODELO_SQL} AS anio_modelo,
            COUNT(*) AS total_solicitudes, COALESCE(SUM(v.monto_pagar), 0) AS monto_total
        FROM solicitud_retroactivo_venta v
        GROUP BY anio_modelo
        ORDER BY anio_modelo DESC
    """)
    return cursor.fetchall()


def obtener_dashboard_por_producto(cursor):
    cursor.execute("""
        SELECT
            COALESCE(TRIM(modelo_bicicleta), 'Sin producto') AS producto,
            COUNT(*) AS total_solicitudes,
            COALESCE(SUM(monto_pagar), 0) AS monto_total_pagar,
            COALESCE(SUM(monto_aplicar), 0) AS monto_total_aplicar
        FROM solicitud_retroactivo_venta
        GROUP BY COALESCE(TRIM(modelo_bicicleta), 'Sin producto')
        ORDER BY monto_total_pagar DESC
    """)
    return cursor.fetchall()


def obtener_venta_para_validacion(cursor, id_venta):
    cursor.execute(
        """SELECT validacion_docs_json, historial_json,
                  ticket_compra_key, voucher_key, factura_pdf_key, factura_xml_key
           FROM solicitud_retroactivo_venta WHERE id = %s""",
        (id_venta,)
    )
    return cursor.fetchone()


def actualizar_validacion_documento(cursor, id_venta, validacion_docs_json, historial_json):
    cursor.execute(
        "UPDATE solicitud_retroactivo_venta SET validacion_docs_json = %s, historial_json = %s WHERE id = %s",
        (validacion_docs_json, historial_json, id_venta)
    )


def obtener_venta_para_nota_credito(cursor, id_venta):
    cursor.execute(
        "SELECT nota_credito, nota_credito_estatus, historial_json FROM solicitud_retroactivo_venta WHERE id = %s",
        (id_venta,)
    )
    return cursor.fetchone()


def actualizar_nota_credito(cursor, id_venta, nota_credito, historial_json):
    # GUÍA: capturar/editar la NC (BCYP) siempre la deja en 'pendiente' --
    # incluso si ya estaba validada, un cambio de valor invalida esa
    # validación anterior y Auditoría debe volver a revisarla.
    cursor.execute(
        "UPDATE solicitud_retroactivo_venta SET nota_credito = %s, nota_credito_estatus = 'pendiente', historial_json = %s WHERE id = %s",
        (nota_credito, historial_json, id_venta)
    )


def validar_nota_credito(cursor, id_venta, historial_json):
    """Auditoría valida la NC ya capturada (ver utils/auditoria_utils.py
    para el código requerido)."""
    cursor.execute(
        "UPDATE solicitud_retroactivo_venta SET nota_credito_estatus = 'validada', historial_json = %s WHERE id = %s",
        (historial_json, id_venta)
    )


def obtener_venta_para_precio(cursor, id_venta):
    cursor.execute(
        "SELECT porcentaje, precio_publico, historial_json FROM solicitud_retroactivo_venta WHERE id = %s",
        (id_venta,)
    )
    return cursor.fetchone()


def actualizar_precio(cursor, id_venta, precio_publico, monto_pagar, monto_aplicar, historial_json):
    cursor.execute(
        "UPDATE solicitud_retroactivo_venta SET precio_publico = %s, monto_pagar = %s, monto_aplicar = %s, historial_json = %s WHERE id = %s",
        (precio_publico, monto_pagar, monto_aplicar, historial_json, id_venta)
    )


def listar_mis_ventas(cursor, id_usuario):
    cursor.execute(f"""
        SELECT
            v.id, v.id_formulario, f.nombre AS nombre_formulario,
            v.id_marca_bicicleta, v.id_msi, m.plazo_meses,
            v.nombre_sucursal, v.correo_electronico, v.nombre_completo,
            v.fecha_venta, v.modelo_bicicleta, v.numero_serie,
            v.precio_publico, v.porcentaje, v.monto_pagar,
            v.nota_credito, v.nota_credito_estatus,
            v.validacion_docs_json, v.historial_json, {ANIO_MODELO_SQL} AS anio_modelo,
            v.ticket_compra_key, v.voucher_key, v.factura_pdf_key, v.factura_xml_key,
            v.fecha_registro
        FROM solicitud_retroactivo_venta v
        LEFT JOIN solicitud_retroactivo_campanias f ON f.id = v.id_formulario
        LEFT JOIN solicitud_retroactivo_msi m ON m.id = v.id_msi
        WHERE v.id_usuario = %s
        ORDER BY v.fecha_registro DESC
    """, (id_usuario,))
    return cursor.fetchall()


def listar_ventas_por_cliente(cursor, cliente_id):
    """Solicitudes de todos los usuarios pertenecientes a un cliente.

    El filtro pasa por ``usuarios.cliente_id`` para incluir al distribuidor,
    sus usuarios hijo y cualquier otro usuario del mismo cliente, sin usar
    razón social ni datos recibidos desde HTTP.
    """
    cursor.execute(f"""
        SELECT
            v.id, v.id_usuario, u.usuario AS usuario_registro,
            v.id_formulario, f.nombre AS nombre_formulario,
            v.id_marca_bicicleta, v.id_msi, m.plazo_meses,
            v.nombre_sucursal, v.correo_electronico, v.nombre_completo,
            v.fecha_venta, v.modelo_bicicleta, v.numero_serie,
            v.precio_publico, v.porcentaje, v.monto_pagar, v.monto_aplicar,
            v.nota_credito, v.nota_credito_estatus,
            v.validacion_docs_json, v.historial_json, {ANIO_MODELO_SQL} AS anio_modelo,
            v.ticket_compra_key, v.voucher_key, v.factura_pdf_key, v.factura_xml_key,
            v.fecha_registro
        FROM solicitud_retroactivo_venta v
        INNER JOIN usuarios u ON u.id = v.id_usuario
        LEFT JOIN solicitud_retroactivo_campanias f ON f.id = v.id_formulario
        LEFT JOIN solicitud_retroactivo_msi m ON m.id = v.id_msi
        WHERE u.cliente_id = %s
        ORDER BY v.fecha_registro DESC
    """, (cliente_id,))
    return cursor.fetchall()


def obtener_venta_para_edicion(cursor, id_venta):
    cursor.execute(
        "SELECT id_usuario, validacion_docs_json, historial_json FROM solicitud_retroactivo_venta WHERE id = %s",
        (id_venta,)
    )
    return cursor.fetchone()


def actualizar_venta(cursor, id_venta, valores_fijos, columnas_archivo_sql, valores_archivo):
    """valores_fijos: tupla en el mismo orden que las columnas fijas del SET
    (id_formulario, id_marca_bicicleta, id_msi, nombre_sucursal,
    correo_electronico, nombre_completo, fecha_venta, modelo_bicicleta,
    numero_serie, precio_publico, porcentaje, monto_pagar, monto_aplicar,
    validacion_docs_json, historial_json). columnas_archivo_sql: string tipo
    "ticket_compra_key = %s, voucher_key = %s" (o '' si no hay archivos que
    resubir). valores_archivo: valores para esas columnas, en el mismo orden.
    """
    set_archivos = f", {columnas_archivo_sql}" if columnas_archivo_sql else ""
    cursor.execute(f"""
        UPDATE solicitud_retroactivo_venta SET
            id_formulario = %s, id_marca_bicicleta = %s, id_msi = %s,
            nombre_sucursal = %s, correo_electronico = %s, nombre_completo = %s,
            fecha_venta = %s, modelo_bicicleta = %s, numero_serie = %s,
            precio_publico = %s, porcentaje = %s, monto_pagar = %s, monto_aplicar = %s,
            validacion_docs_json = %s, historial_json = %s
            {set_archivos}
        WHERE id = %s
    """, (*valores_fijos, *valores_archivo, id_venta))


# =============================================================================
# SERIES ENTREGADAS EN ODOO
# =============================================================================

def obtener_cliente_odoo_usuario(cursor, usuario_id):
    """Obtiene el cliente del usuario autenticado para consultas Odoo.

    La ruta nunca recibe esta identidad del frontend: ``usuarios.cliente_id``
    es la fuente de autoridad y ``clientes.clave`` es la referencia principal
    del partner en Odoo.
    """
    cursor.execute("""
        SELECT c.id, c.clave, c.nombre_cliente
        FROM usuarios u
        INNER JOIN clientes c ON c.id = u.cliente_id
        WHERE u.id = %s AND u.activo = 1
    """, (usuario_id,))
    return cursor.fetchone()


def _m2o_id(valor):
    """Extrae el ID de un many2one XML-RPC sin asumir su representación."""
    if isinstance(valor, (list, tuple)) and valor:
        return valor[0]
    return valor if isinstance(valor, int) else None


def _leer_campos_series_odoo(models, uid):
    """Verifica la estructura mínima necesaria antes de consultar movimientos."""
    try:
        move_fields = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD, 'stock.move.line', 'fields_get', [],
            {'attributes': ['type', 'relation']}
        )
        lot_fields = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD, 'stock.lot', 'fields_get', [],
            {'attributes': ['type', 'relation']}
        )
        picking_fields = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD, 'stock.picking', 'fields_get', [],
            {'attributes': ['type', 'relation']}
        )
    except Exception as exc:
        raise SeriesOdooError(
            'estructura_odoo_no_disponible',
            'No fue posible verificar los modelos de inventario en Odoo.',
        ) from exc

    obligatorios_move = {'lot_id', 'product_id', 'picking_id'}
    faltantes_move = obligatorios_move - set(move_fields)
    if faltantes_move:
        raise SeriesOdooError(
            'odoo_sin_lot_id',
            'Odoo no expone los campos requeridos de stock.move.line: ' + ', '.join(sorted(faltantes_move)),
        )

    faltantes_lot = {'name', 'product_id'} - set(lot_fields)
    if faltantes_lot:
        raise SeriesOdooError(
            'odoo_sin_stock_lot',
            'Odoo no expone los campos requeridos de stock.lot: ' + ', '.join(sorted(faltantes_lot)),
        )

    faltantes_picking = {'picking_type_code', 'state', 'date_done', 'sale_id'} - set(picking_fields)
    if faltantes_picking:
        raise SeriesOdooError(
            'odoo_sin_campos_picking',
            'Odoo no expone los campos requeridos de stock.picking: ' + ', '.join(sorted(faltantes_picking)),
        )

    if 'qty_done' in move_fields:
        campo_cantidad = 'qty_done'
    elif 'quantity_done' in move_fields:
        campo_cantidad = 'quantity_done'
    else:
        raise SeriesOdooError(
            'odoo_sin_cantidad_realizada',
            'Odoo no expone qty_done ni quantity_done en stock.move.line.',
        )

    return campo_cantidad


def _resolver_partners_odoo(models, uid, clave_cliente, razon_social):
    """Resuelve partners por clave y, sólo si falla, por razón social exacta.

    La clave local es la fuente principal. El respaldo por razón social exige
    un resultado único para no asociar compras de otro distribuidor.
    """
    try:
        principales = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD, 'res.partner', 'search_read',
            [[('ref', '=', clave_cliente)]],
            {'fields': ['id', 'name', 'ref', 'parent_id'], 'limit': 0}
        )
    except Exception as exc:
        raise SeriesOdooError(
            'error_partner_odoo',
            'No fue posible resolver el cliente en Odoo mediante su clave.',
        ) from exc

    origen = 'clave'
    if not principales:
        if not razon_social or not str(razon_social).strip():
            raise SeriesOdooError(
                'cliente_sin_partner_odoo',
                'El cliente no tiene un partner Odoo asociado por clave.',
                404,
            )
        try:
            principales = models.execute_kw(
                ODOO_DB, uid, ODOO_PASSWORD, 'res.partner', 'search_read',
                [[('name', '=ilike', str(razon_social).strip())]],
                {'fields': ['id', 'name', 'ref', 'parent_id'], 'limit': 0}
            )
        except Exception as exc:
            raise SeriesOdooError(
                'error_partner_odoo',
                'No fue posible buscar el partner por razón social.',
            ) from exc

        if len(principales) > 1:
            raise SeriesOdooError(
                'partner_odoo_ambiguo',
                'La razón social coincide con más de un partner Odoo; no se seleccionó ninguno.',
                409,
            )
        if not principales:
            raise SeriesOdooError(
                'cliente_sin_partner_odoo',
                'No se encontró un partner Odoo para el cliente autenticado.',
                404,
            )
        origen = 'razon_social'

    principal_ids = [partner['id'] for partner in principales]
    try:
        hijos = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD, 'res.partner', 'search_read',
            [[('parent_id', 'in', principal_ids)]],
            {'fields': ['id'], 'limit': 0}
        )
    except Exception as exc:
        raise SeriesOdooError(
            'error_partner_odoo',
            'No fue posible resolver los partners hijo del distribuidor.',
        ) from exc

    partner_ids = sorted({*principal_ids, *(partner['id'] for partner in hijos)})
    return partner_ids, origen


def obtener_series_entregadas_odoo(clave_cliente, razon_social, sku=None, producto=None):
    """Obtiene series de bicicletas entregadas al distribuidor autenticado.

    Toda la lectura es batch: partners, pedidos, entregas, movimientos,
    lotes y productos se consultan por grupos de IDs. No realiza escrituras
    en Odoo ni acepta identidad de cliente desde la capa HTTP.
    """
    uid, models, error_odoo = get_odoo_models()
    if not uid or not models:
        raise SeriesOdooError(
            'autenticacion_odoo_fallida',
            'No fue posible autenticar la conexión de solo lectura con Odoo.',
            503,
        )

    campo_cantidad = _leer_campos_series_odoo(models, uid)
    partner_ids, origen_partner = _resolver_partners_odoo(
        models, uid, clave_cliente, razon_social
    )

    try:
        ordenes = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD, 'sale.order', 'search_read',
            [[('partner_id', 'in', partner_ids), ('state', '!=', 'cancel')]],
            {'fields': ['id', 'name', 'partner_id'], 'limit': 0}
        )
    except Exception as exc:
        raise SeriesOdooError('error_pedidos_odoo', 'No fue posible consultar los pedidos del distribuidor.') from exc

    if not ordenes:
        return _respuesta_series_vacia('sin_pedidos', campo_cantidad, origen_partner)

    orden_por_id = {orden['id']: orden for orden in ordenes}
    try:
        pickings = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD, 'stock.picking', 'search_read',
            [[
                ('sale_id', 'in', list(orden_por_id)),
                ('picking_type_code', '=', 'outgoing'),
                ('state', '=', 'done'),
            ]],
            {'fields': ['id', 'name', 'sale_id', 'state', 'date_done', 'picking_type_code'], 'limit': 0,
             'order': 'date_done desc, id desc'}
        )
    except Exception as exc:
        raise SeriesOdooError('error_entregas_odoo', 'No fue posible consultar las entregas completadas.') from exc

    if not pickings:
        return _respuesta_series_vacia('sin_entregas_completadas', campo_cantidad, origen_partner)

    picking_por_id = {picking['id']: picking for picking in pickings}
    try:
        movimientos = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD, 'stock.move.line', 'search_read',
            [[
                ('picking_id', 'in', list(picking_por_id)),
                ('lot_id', '!=', False),
                (campo_cantidad, '>', 0),
            ]],
            {'fields': ['product_id', 'lot_id', 'picking_id', campo_cantidad], 'limit': 0}
        )
    except Exception as exc:
        raise SeriesOdooError('error_movimientos_odoo', 'No fue posible consultar los movimientos de entrega.') from exc

    if not movimientos:
        return _respuesta_series_vacia('sin_series', campo_cantidad, origen_partner)

    lot_ids = sorted({_m2o_id(movimiento.get('lot_id')) for movimiento in movimientos if _m2o_id(movimiento.get('lot_id'))})
    product_ids = sorted({_m2o_id(movimiento.get('product_id')) for movimiento in movimientos if _m2o_id(movimiento.get('product_id'))})

    try:
        lotes = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD, 'stock.lot', 'search_read',
            [[('id', 'in', lot_ids)]],
            {'fields': ['id', 'name', 'product_id'], 'limit': 0}
        )
        productos = models.execute_kw(
            ODOO_DB, uid, ODOO_PASSWORD, 'product.product', 'search_read',
            [[('id', 'in', product_ids)]],
            {'fields': ['id', 'name', 'display_name', 'default_code'], 'limit': 0}
        )
    except Exception as exc:
        raise SeriesOdooError('error_lotes_odoo', 'No fue posible resolver los lotes o productos de las entregas.') from exc

    lote_por_id = {lote['id']: lote for lote in lotes}
    producto_por_id = {producto_odoo['id']: producto_odoo for producto_odoo in productos}
    sku_filtro = str(sku or '').strip().casefold()
    producto_filtro = str(producto or '').strip().casefold()

    series = []
    series_vistas = set()
    for movimiento in movimientos:
        lot_id = _m2o_id(movimiento.get('lot_id'))
        product_id = _m2o_id(movimiento.get('product_id'))
        picking_id = _m2o_id(movimiento.get('picking_id'))
        lote = lote_por_id.get(lot_id)
        producto_odoo = producto_por_id.get(product_id)
        picking = picking_por_id.get(picking_id)

        if not lote or not producto_odoo or not picking:
            continue
        if _m2o_id(lote.get('product_id')) != product_id:
            continue

        numero_serie = str(lote.get('name') or '').strip()
        if not numero_serie or lot_id in series_vistas:
            continue

        sku_producto = str(producto_odoo.get('default_code') or '').strip()
        nombre_producto = str(producto_odoo.get('display_name') or producto_odoo.get('name') or '').strip()
        if sku_filtro and sku_producto.casefold() != sku_filtro:
            continue
        if producto_filtro and producto_filtro not in nombre_producto.casefold():
            continue

        orden = orden_por_id.get(_m2o_id(picking.get('sale_id')))
        series_vistas.add(lot_id)
        series.append({
            'numero_serie': numero_serie,
            'product_id_odoo': product_id,
            'sku': sku_producto or None,
            'nombre_producto': nombre_producto or None,
            'sale_order': orden.get('name') if orden else None,
            'picking': picking.get('name'),
            'fecha_entrega': picking.get('date_done'),
            'cantidad_realizada': movimiento.get(campo_cantidad),
        })

    estado = 'ok' if series else ('sin_series_para_producto' if (sku_filtro or producto_filtro) else 'sin_series')
    return {
        'estado': estado,
        'campo_cantidad': campo_cantidad,
        'origen_partner': origen_partner,
        'series': series,
    }


def validar_serie_entregada_odoo(cursor, usuario_id, sku_real, numero_serie):
    """Valida una serie contra las entregas elegibles del cliente del JWT.

    Se reutiliza la misma cadena Odoo de ``obtener_series_entregadas_odoo``;
    la identidad del cliente se obtiene localmente, nunca del multipart que
    envía el navegador. Se consulta sin filtro de SKU para poder distinguir
    una serie propia de otro producto de una serie no entregada al cliente.
    """
    cliente = obtener_cliente_odoo_usuario(cursor, usuario_id)
    if not cliente or not cliente.get('clave'):
        raise SeriesOdooError(
            'cliente_sin_clave_odoo',
            'El usuario autenticado no tiene un cliente con clave Odoo asociada.',
            403,
        )

    resultado = obtener_series_entregadas_odoo(
        clave_cliente=str(cliente['clave']).strip(),
        razon_social=cliente.get('nombre_cliente'),
    )
    serie_buscada = str(numero_serie).strip()
    coincidencias = [
        serie for serie in resultado['series']
        if str(serie.get('numero_serie') or '').strip() == serie_buscada
    ]
    if not coincidencias:
        raise SeriesOdooError(
            'serie_no_pertenece_distribuidor',
            'El número de serie no corresponde a una bicicleta entregada al distribuidor autenticado.',
            422,
        )

    sku_normalizado = str(sku_real).strip().casefold()
    serie_valida = next(
        (
            serie for serie in coincidencias
            if str(serie.get('sku') or '').strip().casefold() == sku_normalizado
        ),
        None,
    )
    if not serie_valida:
        raise SeriesOdooError(
            'serie_no_corresponde_sku',
            'El número de serie no corresponde al producto seleccionado.',
            422,
        )
    return serie_valida


def _respuesta_series_vacia(estado, campo_cantidad, origen_partner):
    return {
        'estado': estado,
        'campo_cantidad': campo_cantidad,
        'origen_partner': origen_partner,
        'series': [],
    }
