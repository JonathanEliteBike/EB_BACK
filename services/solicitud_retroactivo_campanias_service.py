"""
Capa de acceso a datos (Data Access) para Solicitud de Retroactivos - Campañas.

GUÍA DEL PROYECTO:
- Este archivo contiene únicamente consultas SQL.
- Las rutas viven en:
    routes/solicitud_retroactivo_campanias.py
- Este service únicamente:
    * Recibe un cursor ya abierto.
    * Ejecuta SQL.
    * Devuelve los resultados.
"""


# ============================================================
# MSI
# ============================================================

def listar_msi(cursor):
    """
    Obtiene el catálogo completo de MSI.
    """
    cursor.execute("""
        SELECT
            id,
            plazo_meses,
            porcentaje
        FROM solicitud_retroactivo_msi
        ORDER BY plazo_meses ASC
    """)
    return cursor.fetchall()


def obtener_msi_por_id(cursor, msi_id):
    """
    Obtiene un MSI específico para validar su existencia.
    """
    cursor.execute("""
        SELECT
            id,
            plazo_meses,
            porcentaje
        FROM solicitud_retroactivo_msi
        WHERE id = %s
    """, (msi_id,))
    return cursor.fetchone()


def obtener_msi_por_plazo(cursor, plazo_meses):
    """
    Busca un MSI del catálogo global por su plazo (para evitar duplicados
    al crear uno nuevo desde el formulario de campañas).
    """
    cursor.execute("""
        SELECT id, plazo_meses, porcentaje
        FROM solicitud_retroactivo_msi
        WHERE plazo_meses = %s
    """, (plazo_meses,))
    return cursor.fetchone()


def crear_msi(cursor, plazo_meses, porcentaje):
    """
    Agrega un plazo nuevo al catálogo global de MSI (el % aquí es solo un
    valor base/informativo -- el % real que aplica en cada campaña vive en
    solicitud_retroactivo_campania_msi).
    """
    cursor.execute("""
        INSERT INTO solicitud_retroactivo_msi (plazo_meses, porcentaje)
        VALUES (%s, %s)
    """, (plazo_meses, porcentaje))
    return cursor.lastrowid


# ============================================================
# PRODUCTOS DETALLE (VARIANTES / SKUs)
# ============================================================

def validar_productos(cursor, productos):
    """
    Comprueba qué productos detalle (variantes/SKUs) existen.
    """
    if not productos:
        return []

    placeholders = ', '.join(['%s'] * len(productos))
    cursor.execute(f"""
        SELECT id
        FROM producto_detalle
        WHERE id IN ({placeholders})
    """, tuple(productos))
    
    return cursor.fetchall()

# ============================================================
# CAMPAÑAS
# ============================================================
def listar_campanias(cursor):
    """
    Obtiene todas las campañas con sus MSI (cada uno con su % propio de esta
    campaña) y el detalle completo de sus SKUs asociados.
    """
    cursor.execute("""
        SELECT
            c.id,
            c.nombre,
            c.fecha_inicio,
            c.fecha_fin,
            c.activa,
            COALESCE(
                (
                    SELECT JSON_ARRAYAGG(
                        JSON_OBJECT(
                            'msi_id', cm.msi_id,
                            'plazo_meses', m.plazo_meses,
                            'porcentaje', cm.porcentaje
                        )
                    )
                    FROM solicitud_retroactivo_campania_msi cm
                    INNER JOIN solicitud_retroactivo_msi m ON m.id = cm.msi_id
                    WHERE cm.campania_id = c.id
                ),
                JSON_ARRAY()
            ) AS msi,
            COALESCE(
                (
                    SELECT JSON_ARRAYAGG(
                        JSON_OBJECT(
                            'id', pd.id,
                            'id_producto', p.id,
                            'modelo', p.modelo,
                            'codigo', p.codigo,
                            'sku', pd.sku,
                            'talla', pd.talla,
                            'color', pd.color
                        )
                    )
                    FROM solicitud_retroactivo_campania_producto_detalle cp
                    INNER JOIN producto_detalle pd ON cp.producto_detalle_id = pd.id
                    INNER JOIN productos p ON pd.codigo_producto = p.codigo
                    WHERE cp.campania_id = c.id
                ),
                JSON_ARRAY()
            ) AS productos
        FROM solicitud_retroactivo_campanias c
        ORDER BY
            c.fecha_fin DESC;
    """)
    return cursor.fetchall()


def obtener_campania(cursor, id_campania):
    """
    Obtiene una campaña específica por ID con sus MSI y SKUs detallados.
    """
    cursor.execute("""
        SELECT
            c.id,
            c.nombre,
            c.fecha_inicio,
            c.fecha_fin,
            c.activa,
            COALESCE(
                (
                    SELECT JSON_ARRAYAGG(
                        JSON_OBJECT(
                            'msi_id', cm.msi_id,
                            'plazo_meses', m.plazo_meses,
                            'porcentaje', cm.porcentaje
                        )
                    )
                    FROM solicitud_retroactivo_campania_msi cm
                    INNER JOIN solicitud_retroactivo_msi m ON m.id = cm.msi_id
                    WHERE cm.campania_id = c.id
                ),
                JSON_ARRAY()
            ) AS msi,
            COALESCE(
                (
                    SELECT JSON_ARRAYAGG(
                        JSON_OBJECT(
                            'id', pd.id,
                            'id_producto', p.id,
                            'modelo', p.modelo,
                            'codigo', p.codigo,
                            'sku', pd.sku,
                            'talla', pd.talla,
                            'color', pd.color
                        )
                    )
                    FROM solicitud_retroactivo_campania_producto_detalle cp
                    INNER JOIN producto_detalle pd ON cp.producto_detalle_id = pd.id
                    INNER JOIN productos p ON pd.codigo_producto = p.codigo
                    WHERE cp.campania_id = c.id
                ),
                JSON_ARRAY()
            ) AS productos
        FROM solicitud_retroactivo_campanias c
        WHERE c.id = %s
    """, (id_campania,))
    return cursor.fetchone()


# ============================================================
# CREAR CAMPAÑA
# ============================================================

def crear_campania(cursor, nombre, fecha_inicio, fecha_fin, activa):
    """
    Crea una campaña (sin MSI todavía -- eso se liga aparte, ver
    agregar_msi_campania) y devuelve el ID generado.
    """
    cursor.execute("""
        INSERT INTO solicitud_retroactivo_campanias (
            nombre,
            fecha_inicio,
            fecha_fin,
            activa
        )
        VALUES (%s, %s, %s, %s)
    """, (nombre, fecha_inicio, fecha_fin, activa))

    return cursor.lastrowid


# ============================================================
# EDITAR CAMPAÑA
# ============================================================

def actualizar_campania(cursor, id_campania, nombre, fecha_inicio, fecha_fin, activa):
    """
    Actualiza los datos principales de una campaña.
    """
    cursor.execute("""
        UPDATE solicitud_retroactivo_campanias
        SET
            nombre = %s,
            fecha_inicio = %s,
            fecha_fin = %s,
            activa = %s
        WHERE id = %s
    """, (nombre, fecha_inicio, fecha_fin, activa, id_campania))


# ============================================================
# MSI DE LA CAMPAÑA (cada plazo con su % propio de esta campaña)
# ============================================================

def agregar_msi_campania(cursor, id_campania, msi_list):
    """
    Liga plazos MSI a una campaña, cada uno con su propio %.
    msi_list: lista de tuplas (msi_id, porcentaje).
    """
    if not msi_list:
        return

    valores = [(id_campania, msi_id, porcentaje) for msi_id, porcentaje in msi_list]

    cursor.executemany("""
        INSERT INTO solicitud_retroactivo_campania_msi (
            campania_id,
            msi_id,
            porcentaje
        )
        VALUES (%s, %s, %s)
    """, valores)


def eliminar_msi_campania(cursor, id_campania):
    """
    Elimina todos los plazos MSI ligados a una campaña.
    """
    cursor.execute("""
        DELETE FROM solicitud_retroactivo_campania_msi
        WHERE campania_id = %s
    """, (id_campania,))


# ============================================================
# PRODUCTOS DE LA CAMPAÑA
# ============================================================

def agregar_productos_campania(cursor, id_campania, productos):
    """
    Agrega productos detalle (variantes) asociados a una campaña.
    """
    if not productos:
        return

    valores = [(id_campania, producto_id) for producto_id in productos]

    cursor.executemany("""
        INSERT INTO solicitud_retroactivo_campania_producto_detalle (
            campania_id,
            producto_detalle_id
        )
        VALUES (%s, %s)
    """, valores)


def eliminar_productos_campania(cursor, id_campania):
    """
    Elimina todas las relaciones de productos detalle de una campaña.
    """
    cursor.execute("""
        DELETE FROM solicitud_retroactivo_campania_producto_detalle
        WHERE campania_id = %s
    """, (id_campania,))


# ============================================================
# ELIMINAR CAMPAÑA
# ============================================================

def eliminar_campania(cursor, id_campania):
    """
    Elimina físicamente una campaña de la tabla principal.
    """
    cursor.execute("""
        DELETE FROM solicitud_retroactivo_campanias
        WHERE id = %s
    """, (id_campania,))


# ============================================================
# CATÁLOGO DE PRODUCTOS Y MARCAS (PARA EL MODAL)
# ============================================================

def listar_marcas(cursor):
    """
    Obtiene el catálogo de marcas registradas.
    """
    cursor.execute("""
        SELECT
            id,
            nombre
        FROM solicitud_retroactivo_marca
        ORDER BY nombre ASC
    """)
    return cursor.fetchall()


def buscar_productos_por_skus(cursor, skus):
    """
    Busca productos detalle (variantes) por una lista de SKUs exactos, para
    la carga masiva de productos en una campaña. sku es UNIQUE en
    producto_detalle, así que cada SKU encontrado mapea a un solo registro.
    """
    if not skus:
        return []

    placeholders = ', '.join(['%s'] * len(skus))
    cursor.execute(f"""
        SELECT
            pd.id,
            p.id AS id_producto,
            m.nombre AS marca,
            p.modelo,
            p.codigo,
            pd.talla,
            pd.color,
            pd.sku
        FROM producto_detalle pd
        INNER JOIN productos p ON pd.codigo_producto = p.codigo
        LEFT JOIN solicitud_retroactivo_marca m ON p.marca_id = m.id
        WHERE pd.sku IN ({placeholders})
    """, tuple(skus))
    return cursor.fetchall()


def buscar_catalogo_productos(cursor, query=None, marca_id=None, sku=None, page=1, limit=10):
    """
    Consulta las variantes/SKUs (producto_detalle) enlazadas con sus productos
    base y marcas, con filtros de búsqueda y paginación.
    """
    condiciones = []
    parametros = []

    if query:
        condiciones.append("(p.modelo LIKE %s OR p.codigo LIKE %s OR pd.sku LIKE %s)")
        patron = f"%{query}%"
        parametros.extend([patron, patron, patron])

    if marca_id:
        condiciones.append("p.marca_id = %s")
        parametros.append(marca_id)

    if sku:
        condiciones.append("pd.sku LIKE %s")
        parametros.append(f"%{sku}%")

    where_clause = " WHERE " + " AND ".join(condiciones) if condiciones else ""

    # 1. Total de registros filtrados
    sql_count = f"""
        SELECT COUNT(*) AS total
        FROM producto_detalle pd
        INNER JOIN productos p ON pd.codigo_producto = p.codigo
        LEFT JOIN solicitud_retroactivo_marca m ON p.marca_id = m.id
        {where_clause}
    """
    cursor.execute(sql_count, tuple(parametros))
    resultado_count = cursor.fetchone()
    total = resultado_count['total'] if resultado_count else 0

    # 2. Registros paginados
    offset = (page - 1) * limit
    sql_data = f"""
        SELECT
            pd.id,
            p.id AS id_producto,
            m.nombre AS marca,
            p.modelo,
            p.codigo,
            pd.talla,
            pd.color,
            pd.sku
        FROM producto_detalle pd
        INNER JOIN productos p ON pd.codigo_producto = p.codigo
        LEFT JOIN solicitud_retroactivo_marca m ON p.marca_id = m.id
        {where_clause}
        ORDER BY p.modelo ASC, pd.sku ASC
        LIMIT %s OFFSET %s
    """
    parametros_data = parametros + [limit, offset]
    cursor.execute(sql_data, tuple(parametros_data))
    filas = cursor.fetchall()

    return {
        "data": filas,
        "total": total,
        "page": page,
        "pageSize": limit
    }