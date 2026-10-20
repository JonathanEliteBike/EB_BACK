from flask import Blueprint, jsonify, request
from uuid import uuid4

from db_conexion import obtener_conexion
from utils.inventario_actividad import registrar_evento, capturar_estado
from utils.inventario_auth import validar_acceso_inventario


inventario_bp = Blueprint(
    "inventario",
    __name__,
    url_prefix="/api/inventario"
)

# Aplica a todos los endpoints del blueprint (lectura y escritura).
inventario_bp.before_request(validar_acceso_inventario)


# ============================================================
# CATÁLOGOS Y VALORES PERMITIDOS
# ============================================================

ESTADOS_EQUIPO_VALIDOS = {
    "Disponible",
    "Asignado",
    "Baja"
}


ESTADOS_RESPONSIVA_VALIDOS = {
    "Pendiente",
    "Firmada",
    "No aplica"
}


UBICACIONES_VALIDAS = {
    "almacen": "Almacen",
    "almacén": "Almacen",
    "oficina": "Oficina",
    "tienda": "Tienda"
}


ACRONIMOS_VALIDOS = {
    "ti": "TI",
    "it": "IT",
    "pc": "PC",
    "usb": "USB",
    "ssd": "SSD",
    "hdd": "HDD",
    "ram": "RAM",
    "cpu": "CPU",
    "ups": "UPS",
    "hp": "HP",
    "led": "LED",
    "lcd": "LCD",
    "hdmi": "HDMI",
    "vga": "VGA",
    "wifi": "WiFi"
}


# ============================================================
# FUNCIONES AUXILIARES
# ============================================================

def limpiar_valor(valor):
    """
    Convierte cadenas vacías en None para almacenarlas como NULL.
    """

    if valor is None:
        return None

    if isinstance(valor, str):
        valor = valor.strip()

        return valor if valor else None

    return valor


def normalizar_texto_titulo(valor):
    """
    Convierte cada palabra a formato de título.

    Ejemplos:

    laptop -> Laptop
    laptop lenovo -> Laptop Lenovo
    equipo de computo -> Equipo De Computo
    hp -> HP
    usb -> USB

    No debe utilizarse en números de serie, modelos,
    URLs, correos o comentarios.
    """

    valor = limpiar_valor(valor)

    if not valor:
        return None

    palabras_normalizadas = []

    for palabra in str(valor).split():
        palabra_minuscula = palabra.lower()

        if palabra_minuscula in ACRONIMOS_VALIDOS:
            palabras_normalizadas.append(
                ACRONIMOS_VALIDOS[
                    palabra_minuscula
                ]
            )

        else:
            palabras_normalizadas.append(
                palabra.capitalize()
            )

    return " ".join(
        palabras_normalizadas
    )


def normalizar_ubicacion(valor):
    """
    Valida y normaliza la ubicación física.

    Valores permitidos:

    Almacen
    Oficina
    Tienda
    """

    valor = limpiar_valor(valor)

    if not valor:
        return None

    clave = str(valor).strip().lower()

    ubicacion_normalizada = (
        UBICACIONES_VALIDAS.get(clave)
    )

    if not ubicacion_normalizada:
        raise ValueError(
            "La ubicación física debe ser "
            "Almacen, Oficina o Tienda."
        )

    return ubicacion_normalizada


def validar_estado_equipo(estado):
    """
    Valida el estado general del equipo.
    """

    if estado not in ESTADOS_EQUIPO_VALIDOS:
        raise ValueError(
            "El estado del equipo debe ser "
            "Disponible, Asignado o Baja."
        )


def validar_estado_responsiva(estado):
    """
    Valida el estado de la responsiva.
    """

    if estado not in ESTADOS_RESPONSIVA_VALIDOS:
        raise ValueError(
            "El estado de la responsiva debe ser "
            "Pendiente, Firmada o No aplica."
        )


def formatear_equipo(row):
    """
    Convierte los nombres de columnas de MySQL
    al formato utilizado por Angular.
    """

    return {
        "id": row["id"],

        "inventario":
            row.get("numero_inventario") or "",

        "fechaRegistro": (
            str(row.get("fecha_registro"))
            if row.get("fecha_registro")
            else ""
        ),

        "empresa":
            row.get("empresa") or "ELITE BIKE",

        "departamento":
            row.get("departamento") or "",

        "responsable":
            row.get("responsable") or "",

        "cargo":
            row.get("cargo") or "",

        "categoria":
            row.get("categoria") or "",

        "nombre":
            row.get("descripcion") or "",

        "marca":
            row.get("marca") or "",

        "modelo":
            row.get("modelo") or "",

        "serie":
            row.get("numero_serie") or "",

        "funcionamiento":
            row.get("funcionamiento") or "",

        "estado":
            row.get("estado") or "Disponible",

        "ubicacion":
            row.get("ubicacion") or "",

        "imagenUrl":
            row.get("imagen_url") or "",

        "comentariosSistemas":
            row.get("comentarios_sistemas") or "",

        "extras":
            row.get("extras") or "",

        "responsiva":
            row.get("responsiva_estado") or "No aplica"
    }


def obtener_conflicto_duplicado(
    cursor,
    numero_inventario,
    numero_serie=None,
    equipo_id_excluir=None
):
    """
    Comprueba que no exista otro equipo con el mismo:

    - Número de inventario.
    - Número de serie.
    """

    parametros = [
        numero_inventario
    ]

    excluir_sql = ""

    if equipo_id_excluir is not None:
        excluir_sql = " AND id <> %s"

        parametros.append(
            equipo_id_excluir
        )

    cursor.execute(
        f"""
        SELECT id
        FROM inventario_equipos

        WHERE numero_inventario = %s
        {excluir_sql}

        LIMIT 1
        """,
        tuple(parametros)
    )

    inventario_duplicado = cursor.fetchone()

    if inventario_duplicado:
        return (
            "Ya existe un equipo con el mismo "
            "número de inventario."
        )

    if numero_serie:
        parametros = [
            numero_serie
        ]

        excluir_sql = ""

        if equipo_id_excluir is not None:
            excluir_sql = " AND id <> %s"

            parametros.append(
                equipo_id_excluir
            )

        cursor.execute(
            f"""
            SELECT id
            FROM inventario_equipos

            WHERE numero_serie = %s
            {excluir_sql}

            LIMIT 1
            """,
            tuple(parametros)
        )

        serie_duplicada = cursor.fetchone()

        if serie_duplicada:
            return (
                "Ya existe un equipo con el mismo "
                "número de serie."
            )

    return None


# ============================================================
# GET: LISTAR EQUIPOS
# ============================================================

@inventario_bp.route(
    "/equipos",
    methods=["GET"]
)
def listar_equipos():
    conexion = obtener_conexion()

    cursor = conexion.cursor(
        dictionary=True
    )

    try:
        cursor.execute("""
            SELECT
                id,
                numero_inventario,
                fecha_registro,
                empresa,
                departamento,
                responsable,
                cargo,
                categoria,
                descripcion,
                marca,
                modelo,
                numero_serie,
                funcionamiento,
                estado,
                ubicacion,
                imagen_url,
                comentarios_sistemas,
                extras,
                responsiva_estado

            FROM inventario_equipos

            ORDER BY id DESC
        """)

        registros = cursor.fetchall()

        equipos = [
            formatear_equipo(row)
            for row in registros
        ]

        return jsonify(equipos)

    except Exception as error:
        return jsonify({
            "error": (
                "No se pudieron obtener los equipos"
            ),
            "detalle": str(error)
        }), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# GET: OBTENER UN EQUIPO
# ============================================================

@inventario_bp.route(
    "/equipos/<int:equipo_id>",
    methods=["GET"]
)
def obtener_equipo(equipo_id):
    conexion = obtener_conexion()

    cursor = conexion.cursor(
        dictionary=True
    )

    try:
        cursor.execute("""
            SELECT
                id,
                numero_inventario,
                fecha_registro,
                empresa,
                departamento,
                responsable,
                cargo,
                categoria,
                descripcion,
                marca,
                modelo,
                numero_serie,
                funcionamiento,
                estado,
                ubicacion,
                imagen_url,
                comentarios_sistemas,
                extras,
                responsiva_estado

            FROM inventario_equipos

            WHERE id = %s
        """, (
            equipo_id,
        ))

        row = cursor.fetchone()

        if not row:
            return jsonify({
                "error": "Equipo no encontrado"
            }), 404

        return jsonify(
            formatear_equipo(row)
        )

    except Exception as error:
        return jsonify({
            "error": (
                "No se pudo obtener el equipo"
            ),
            "detalle": str(error)
        }), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# POST: CREAR EQUIPO
# ============================================================

@inventario_bp.route(
    "/equipos",
    methods=["POST"]
)
def crear_equipo():
    data = request.get_json(
        silent=True
    )

    if not data:
        return jsonify({
            "error": "No se recibieron datos"
        }), 400

    # El folio se asigna exclusivamente en el servidor.
    # Se ignora cualquier código enviado por el navegador.

    categoria = normalizar_texto_titulo(
        data.get("categoria")
    )

    descripcion = normalizar_texto_titulo(
        data.get("nombre")
    )

    marca = normalizar_texto_titulo(
        data.get("marca")
    )

    funcionamiento = (
        normalizar_texto_titulo(
            data.get("funcionamiento")
        )
        or "Bueno"
    )

    numero_serie = limpiar_valor(
        data.get("serie")
    )

    estado_solicitado = (
        normalizar_texto_titulo(
            data.get("estado")
        )
        or "Disponible"
    )

    try:
        ubicacion = normalizar_ubicacion(
            data.get("ubicacion")
        )

        validar_estado_equipo(
            estado_solicitado
        )

    except ValueError as error:
        return jsonify({
            "error": str(error)
        }), 400

    if not categoria or not descripcion:
        return jsonify({
            "error": "La categoría y el nombre del equipo son obligatorios."
        }), 400

    if estado_solicitado == "Asignado":
        return jsonify({
            "error": (
                "Un equipo nuevo no puede registrarse "
                "directamente como Asignado. "
                "Primero regístralo como Disponible "
                "y después utiliza el módulo "
                "de Asignaciones."
            )
        }), 409

    conexion = obtener_conexion()

    cursor = conexion.cursor(
        dictionary=True
    )

    try:
        conexion.start_transaction()

        # Primer INSERT con folio temporal único. El ID autoincremental
        # de MySQL permite asignar un consecutivo sin consultar MAX()+1,
        # evitando colisiones entre registros simultáneos.
        numero_temporal = f"TEMP-IT-{uuid4().hex}"
        conflicto = obtener_conflicto_duplicado(
            cursor,
            numero_temporal,
            numero_serie
        )

        if conflicto:
            conexion.rollback()

            return jsonify({
                "error": conflicto
            }), 409

        cursor.execute("""
            INSERT INTO inventario_equipos (
                numero_inventario,
                fecha_registro,
                empresa,
                departamento,
                responsable,
                cargo,
                categoria,
                descripcion,
                marca,
                modelo,
                numero_serie,
                funcionamiento,
                estado,
                ubicacion,
                imagen_url,
                comentarios_sistemas,
                extras,
                responsiva_estado
            )
            VALUES (
                %s,
                %s,
                %s,
                NULL,
                NULL,
                NULL,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                'No aplica'
            )
        """, (
            numero_temporal,

            limpiar_valor(
                data.get("fechaRegistro")
            ),

            (
                limpiar_valor(
                    data.get("empresa")
                )
                or "ELITE BIKE"
            ),

            categoria,

            descripcion,

            marca,

            limpiar_valor(
                data.get("modelo")
            ),

            numero_serie,

            funcionamiento,

            estado_solicitado,

            ubicacion,

            limpiar_valor(
                data.get("imagenUrl")
            ),

            limpiar_valor(
                data.get(
                    "comentariosSistemas"
                )
            ),

            limpiar_valor(
                data.get("extras")
            )
        ))

        nuevo_id = cursor.lastrowid

        empresa_equipo = str(data.get("empresa") or "ELITE BIKE").strip().upper()
        prefijo = "GS" if empresa_equipo == "GARNIER SPORTS" else "EB"
        numero_inventario = f"{prefijo}-IT-{nuevo_id:06d}"

        # Se respeta el inventario histórico: si ya existe un código
        # idéntico por carga manual antigua, se cancela sin dejar
        # registros parciales ni un folio temporal.
        cursor.execute(
            "SELECT id FROM inventario_equipos "
            "WHERE numero_inventario = %s AND id <> %s LIMIT 1",
            (numero_inventario, nuevo_id),
        )
        if cursor.fetchone():
            conexion.rollback()
            return jsonify({
                "error": (
                    "El folio automático coincide con un equipo antiguo. "
                    "Contacta a Sistemas para revisar la numeración."
                )
            }), 409

        cursor.execute(
            "UPDATE inventario_equipos SET numero_inventario = %s WHERE id = %s",
            (numero_inventario, nuevo_id),
        )

        # La bitácora captura el folio definitivo, no el temporal.
        registrar_evento(cursor, 'crear', 'Equipo', nuevo_id)
        conexion.commit()

        return jsonify({
            "message": (
                "Equipo registrado correctamente"
            ),
            "id": nuevo_id,
            "inventario": numero_inventario,
            "estado": estado_solicitado,
            "ubicacion": ubicacion or "",
            "responsiva": "No aplica"
        }), 201

    except Exception as error:
        conexion.rollback()

        codigo_error = getattr(
            error,
            "errno",
            None
        )

        if codigo_error == 1062:
            return jsonify({
                "error": (
                    "El número de inventario o "
                    "el número de serie ya está registrado."
                )
            }), 409

        return jsonify({
            "error": (
                "No se pudo registrar el equipo"
            ),
            "detalle": str(error)
        }), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# PUT: ACTUALIZAR EQUIPO
# ============================================================

@inventario_bp.route(
    "/equipos/<int:equipo_id>",
    methods=["PUT"]
)
def actualizar_equipo(equipo_id):
    data = request.get_json(
        silent=True
    )

    if not data:
        return jsonify({
            "error": "No se recibieron datos"
        }), 400

    numero_inventario = str(
        data.get("inventario") or ""
    ).strip()

    categoria = normalizar_texto_titulo(
        data.get("categoria")
    )

    descripcion = normalizar_texto_titulo(
        data.get("nombre")
    )

    marca = normalizar_texto_titulo(
        data.get("marca")
    )

    funcionamiento = (
        normalizar_texto_titulo(
            data.get("funcionamiento")
        )
        or "Bueno"
    )

    numero_serie = limpiar_valor(
        data.get("serie")
    )

    estado_solicitado = (
        normalizar_texto_titulo(
            data.get("estado")
        )
        or "Disponible"
    )

    responsiva_solicitada = (
        limpiar_valor(
            data.get("responsiva")
        )
        or "No aplica"
    )

    try:
        ubicacion = normalizar_ubicacion(
            data.get("ubicacion")
        )

        validar_estado_equipo(
            estado_solicitado
        )

        validar_estado_responsiva(
            responsiva_solicitada
        )

    except ValueError as error:
        return jsonify({
            "error": str(error)
        }), 400

    if (
        not numero_inventario
        or not categoria
        or not descripcion
    ):
        return jsonify({
            "error": (
                "Los campos inventario, categoría "
                "y nombre son obligatorios"
            )
        }), 400

    conexion = obtener_conexion()

    cursor = conexion.cursor(
        dictionary=True
    )

    try:
        conexion.start_transaction()

        # ----------------------------------------------------
        # 1. BLOQUEAR Y VALIDAR EQUIPO
        # ----------------------------------------------------

        cursor.execute("""
            SELECT
                id,
                numero_inventario,
                numero_serie,
                estado,
                responsable,
                departamento,
                cargo,
                responsiva_estado

            FROM inventario_equipos

            WHERE id = %s

            FOR UPDATE
        """, (
            equipo_id,
        ))

        equipo_actual = cursor.fetchone()

        if not equipo_actual:
            conexion.rollback()

            return jsonify({
                "error": "Equipo no encontrado"
            }), 404

        # ----------------------------------------------------
        # 2. VALIDAR DUPLICADOS
        # ----------------------------------------------------

        conflicto = obtener_conflicto_duplicado(
            cursor,
            numero_inventario,
            numero_serie,
            equipo_id
        )

        if conflicto:
            conexion.rollback()

            return jsonify({
                "error": conflicto
            }), 409

        # ----------------------------------------------------
        # 3. BUSCAR ASIGNACIÓN ACTIVA
        # ----------------------------------------------------

        cursor.execute("""
            SELECT
                a.id,
                a.colaborador_id,

                c.nombre,
                c.apellido_paterno,
                c.apellido_materno,
                c.departamento,
                c.puesto

            FROM inventario_asignaciones a

            INNER JOIN inventario_colaboradores c
                ON c.id = a.colaborador_id

            WHERE a.equipo_id = %s
              AND a.estado = 'Activa'

            LIMIT 1

            FOR UPDATE
        """, (
            equipo_id,
        ))

        asignacion_activa = cursor.fetchone()

        # ----------------------------------------------------
        # 4. PROTEGER ESTADO Y RESPONSABLE
        # ----------------------------------------------------

        if asignacion_activa:
            if estado_solicitado != "Asignado":
                conexion.rollback()

                return jsonify({
                    "error": (
                        "El equipo tiene una asignación activa. "
                        "No puede cambiarse a Disponible o Baja "
                        "desde Equipos. Primero registra la "
                        "devolución en Asignaciones."
                    ),
                    "asignacionId": (
                        asignacion_activa["id"]
                    )
                }), 409

            estado_final = "Asignado"

            responsable_final = " ".join(
                parte
                for parte in [
                    asignacion_activa.get(
                        "nombre"
                    ),
                    asignacion_activa.get(
                        "apellido_paterno"
                    ),
                    asignacion_activa.get(
                        "apellido_materno"
                    )
                ]
                if parte
            )

            departamento_final = (
                asignacion_activa.get(
                    "departamento"
                )
            )

            cargo_final = (
                asignacion_activa.get(
                    "puesto"
                )
            )

            responsiva_final = (
                equipo_actual.get(
                    "responsiva_estado"
                )
                or "Pendiente"
            )

        else:
            if estado_solicitado == "Asignado":
                conexion.rollback()

                return jsonify({
                    "error": (
                        "El equipo no tiene una asignación "
                        "activa. Para colocarlo como Asignado "
                        "utiliza el módulo de Asignaciones."
                    )
                }), 409

            estado_final = estado_solicitado

            responsable_final = None

            departamento_final = None

            cargo_final = None

            responsiva_final = "No aplica"

        # ----------------------------------------------------
        # 5. ACTUALIZAR EQUIPO
        # ----------------------------------------------------

        estado_anterior_bitacora = capturar_estado(cursor, 'Equipo', equipo_id)
        cursor.execute("""
            UPDATE inventario_equipos

            SET
                numero_inventario = %s,
                fecha_registro = %s,
                empresa = %s,
                departamento = %s,
                responsable = %s,
                cargo = %s,
                categoria = %s,
                descripcion = %s,
                marca = %s,
                modelo = %s,
                numero_serie = %s,
                funcionamiento = %s,
                estado = %s,
                ubicacion = %s,
                imagen_url = %s,
                comentarios_sistemas = %s,
                extras = %s,
                responsiva_estado = %s

            WHERE id = %s
        """, (
            numero_inventario,

            limpiar_valor(
                data.get("fechaRegistro")
            ),

            (
                limpiar_valor(
                    data.get("empresa")
                )
                or "ELITE BIKE"
            ),

            departamento_final,

            responsable_final,

            cargo_final,

            categoria,

            descripcion,

            marca,

            limpiar_valor(
                data.get("modelo")
            ),

            numero_serie,

            funcionamiento,

            estado_final,

            ubicacion,

            limpiar_valor(
                data.get("imagenUrl")
            ),

            limpiar_valor(
                data.get(
                    "comentariosSistemas"
                )
            ),

            limpiar_valor(
                data.get("extras")
            ),

            responsiva_final,

            equipo_id
        ))

        registrar_evento(cursor, 'editar', 'Equipo', equipo_id, anterior=estado_anterior_bitacora)
        conexion.commit()

        return jsonify({
            "message": (
                "Equipo actualizado correctamente"
            ),
            "id": equipo_id,
            "estado": estado_final,
            "ubicacion": ubicacion or "",
            "responsiva": responsiva_final,
            "asignacionActiva": bool(
                asignacion_activa
            )
        })

    except Exception as error:
        conexion.rollback()

        codigo_error = getattr(
            error,
            "errno",
            None
        )

        if codigo_error == 1062:
            return jsonify({
                "error": (
                    "El número de inventario o "
                    "el número de serie ya está registrado."
                )
            }), 409

        return jsonify({
            "error": (
                "No se pudo actualizar el equipo"
            ),
            "detalle": str(error)
        }), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# DELETE: ELIMINAR EQUIPO
# ============================================================

@inventario_bp.route(
    "/equipos/<int:equipo_id>",
    methods=["DELETE"]
)
def eliminar_equipo(equipo_id):
    conexion = obtener_conexion()

    cursor = conexion.cursor(
        dictionary=True
    )

    try:
        conexion.start_transaction()

        # ----------------------------------------------------
        # 1. BLOQUEAR Y VALIDAR EQUIPO
        # ----------------------------------------------------

        cursor.execute("""
            SELECT
                id,
                numero_inventario,
                estado

            FROM inventario_equipos

            WHERE id = %s

            FOR UPDATE
        """, (
            equipo_id,
        ))

        equipo = cursor.fetchone()

        if not equipo:
            conexion.rollback()

            return jsonify({
                "error": "Equipo no encontrado"
            }), 404

        # ----------------------------------------------------
        # 2. BLOQUEAR SI TIENE ASIGNACIÓN ACTIVA
        # ----------------------------------------------------

        cursor.execute("""
            SELECT id

            FROM inventario_asignaciones

            WHERE equipo_id = %s
              AND estado = 'Activa'

            LIMIT 1

            FOR UPDATE
        """, (
            equipo_id,
        ))

        asignacion_activa = cursor.fetchone()

        if asignacion_activa:
            conexion.rollback()

            return jsonify({
                "error": (
                    "No se puede eliminar el equipo porque "
                    "tiene una asignación activa. Primero "
                    "registra la devolución."
                ),
                "asignacionId": (
                    asignacion_activa["id"]
                )
            }), 409

        # ----------------------------------------------------
        # 3. CONTAR ASIGNACIONES
        # ----------------------------------------------------

        cursor.execute("""
            SELECT COUNT(*) AS total

            FROM inventario_asignaciones

            WHERE equipo_id = %s
        """, (
            equipo_id,
        ))

        resultado_asignaciones = (
            cursor.fetchone() or {}
        )

        total_asignaciones = int(
            resultado_asignaciones.get(
                "total"
            ) or 0
        )

        # ----------------------------------------------------
        # 4. CONTAR RESPONSIVAS
        # ----------------------------------------------------

        cursor.execute("""
            SELECT COUNT(*) AS total

            FROM inventario_responsivas

            WHERE equipo_id = %s
        """, (
            equipo_id,
        ))

        resultado_responsivas = (
            cursor.fetchone() or {}
        )

        total_responsivas = int(
            resultado_responsivas.get(
                "total"
            ) or 0
        )

        # ----------------------------------------------------
        # 5. CONTAR MOVIMIENTOS
        # ----------------------------------------------------

        cursor.execute("""
            SELECT COUNT(*) AS total

            FROM inventario_movimientos

            WHERE equipo_id = %s
        """, (
            equipo_id,
        ))

        resultado_movimientos = (
            cursor.fetchone() or {}
        )

        total_movimientos = int(
            resultado_movimientos.get(
                "total"
            ) or 0
        )

        # ----------------------------------------------------
        # 6. BLOQUEAR ELIMINACIÓN SI TIENE HISTORIAL
        # ----------------------------------------------------

        if (
            total_asignaciones > 0
            or total_responsivas > 0
            or total_movimientos > 0
        ):
            conexion.rollback()

            return jsonify({
                "error": (
                    "El equipo ya tiene historial y no puede "
                    "eliminarse. Utiliza el estado Baja para "
                    "conservar la trazabilidad."
                ),
                "relaciones": {
                    "asignaciones":
                        total_asignaciones,

                    "responsivas":
                        total_responsivas,

                    "movimientos":
                        total_movimientos
                }
            }), 409

        # ----------------------------------------------------
        # 7. ELIMINAR EQUIPO SIN HISTORIAL
        # ----------------------------------------------------

        estado_anterior_bitacora = capturar_estado(cursor, 'Equipo', equipo_id)
        cursor.execute("""
            DELETE FROM inventario_equipos

            WHERE id = %s
        """, (
            equipo_id,
        ))

        if cursor.rowcount != 1:
            raise RuntimeError(
                "No fue posible eliminar el equipo."
            )

        registrar_evento(cursor, 'eliminar', 'Equipo', equipo_id, anterior=estado_anterior_bitacora)
        conexion.commit()

        return jsonify({
            "message": (
                "Equipo eliminado correctamente"
            ),
            "id": equipo_id
        })

    except Exception as error:
        conexion.rollback()

        codigo_error = getattr(
            error,
            "errno",
            None
        )

        if codigo_error in {
            1217,
            1451
        }:
            return jsonify({
                "error": (
                    "El equipo está relacionado con otros "
                    "registros y no puede eliminarse. "
                    "Utiliza el estado Baja."
                )
            }), 409

        return jsonify({
            "error": (
                "No se pudo eliminar el equipo"
            ),
            "detalle": str(error)
        }), 500

    finally:
        cursor.close()
        conexion.close()