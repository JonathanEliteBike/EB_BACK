import csv
from datetime import date, datetime
from io import BytesIO, StringIO

from flask import Blueprint, g, jsonify, request, send_file

from db_conexion import obtener_conexion
from utils.inventario_actividad import registrar_evento, calcular_participacion, capturar_estado, leer_detalle_evento
from utils.inventario_auth import validar_acceso_inventario


auditorias_bp = Blueprint(
    "auditorias",
    __name__,
    url_prefix="/api/inventario/auditorias",
)

# Aplica a todos los endpoints del blueprint (lectura y escritura).
auditorias_bp.before_request(validar_acceso_inventario)


@auditorias_bp.route('/mi-usuario', methods=['GET'])
def obtener_auditor_autenticado():
    """Solo exponer la identidad verificada por el guard del backend."""
    cuenta = g.inventario_usuario
    return jsonify({'id': cuenta['id'], 'nombre': cuenta['nombre']})

@auditorias_bp.route('/actividad', methods=['GET'])
def obtener_actividad_usuarios():
    """Comparativo por usuario. Un evento exitoso = una operación.

    modo=ediciones considera 'editar' únicamente; modo=todas incluye
    operaciones de alta, asignación, baja, revisión y cambios de estado.
    """
    modo = request.args.get('modo', 'todas')
    if modo not in ('ediciones', 'todas'):
        return jsonify({'error': 'Modo de consulta inválido.'}), 400

    desde = (request.args.get('desde') or '').strip()
    hasta = (request.args.get('hasta') or '').strip()
    try:
        desde_fecha = datetime.strptime(desde, '%Y-%m-%d').date() if desde else None
        hasta_fecha = datetime.strptime(hasta, '%Y-%m-%d').date() if hasta else None
    except ValueError:
        return jsonify({'error': 'Utiliza fechas AAAA-MM-DD.'}), 400
    if desde_fecha and hasta_fecha and desde_fecha > hasta_fecha:
        return jsonify({'error': 'La fecha inicial debe ser anterior a la final.'}), 400

    filtros = []
    parametros = []
    if modo == 'ediciones':
        filtros.append('accion = %s')
        parametros.append('editar')
    if desde_fecha:
        filtros.append('fecha >= %s')
        parametros.append(desde_fecha)
    if hasta_fecha:
        filtros.append('fecha < DATE_ADD(%s, INTERVAL 1 DAY)')
        parametros.append(hasta_fecha)
    where = ' WHERE ' + ' AND '.join(filtros) if filtros else ''

    conexion = obtener_conexion()
    if conexion is None:
        return jsonify({'error': 'No se pudo conectar a la base de datos.'}), 503
    cursor = None
    try:
        cursor = conexion.cursor(dictionary=True)
        cursor.execute(
            'SELECT usuario_id, MAX(usuario_nombre) AS usuario_nombre, '
            'COUNT(*) AS total FROM inventario_bitacora' + where +
            ' GROUP BY usuario_id', tuple(parametros),
        )
        conteos = cursor.fetchall()
        total, participantes = calcular_participacion(conteos)
        cursor.execute(
            'SELECT id, usuario_id, usuario_nombre, accion, entidad, entidad_id, '
            'DATE_FORMAT(fecha, %s) AS fecha FROM inventario_bitacora' + where +
            ' ORDER BY fecha DESC, id DESC LIMIT 50',
            tuple(['%Y-%m-%d %H:%i:%s'] + parametros),
        )
        eventos = [
            {'id': r['id'], 'usuarioId': r['usuario_id'],
             'usuario': r['usuario_nombre'], 'accion': r['accion'],
             'entidad': r['entidad'], 'entidadId': r['entidad_id'],
             'fecha': r['fecha']}
            for r in cursor.fetchall()
        ]
        return jsonify({'total': total, 'usuarios': participantes, 'eventos': eventos,
                        'modo': modo, 'desde': desde, 'hasta': hasta})
    except Exception:
        # Evitar exponer detalles de tablas o SQL a clientes web.
        return jsonify({'error': 'No se pudo consultar la bitácora. Verifica que '
                                 'la migración SQL esté aplicada.'}), 503
    finally:
        if cursor: cursor.close()
        conexion.close()



@auditorias_bp.route('/actividad/usuarios/<int:usuario_id>', methods=['GET'])
def historial_actividad_usuario(usuario_id):
    """Detalle paginado; mismos filtros que las métricas de participación.

    El acceso lo controla before_request(validar_acceso_inventario).
    La identidad del historial proviene siempre de la base de datos.
    """
    if usuario_id <= 0:
        return jsonify({'error': 'Usuario inválido.'}), 400
    modo = request.args.get('modo', 'todas')
    if modo not in ('ediciones', 'todas'):
        return jsonify({'error': 'Modo inválido.'}), 400
    desde = (request.args.get('desde') or '').strip()
    hasta = (request.args.get('hasta') or '').strip()
    try:
        desde_fecha = datetime.strptime(desde, '%Y-%m-%d').date() if desde else None
        hasta_fecha = datetime.strptime(hasta, '%Y-%m-%d').date() if hasta else None
        limite = int(request.args.get('limite', '25'))
        desplazamiento = int(request.args.get('offset', '0'))
    except (ValueError, TypeError):
        return jsonify({'error': 'Filtros de historial inválidos.'}), 400
    if (desde_fecha and hasta_fecha and desde_fecha > hasta_fecha
            or limite < 1 or limite > 100 or desplazamiento < 0
            or desplazamiento > 100000):
        return jsonify({'error': 'Rango de consulta inválido.'}), 400

    condiciones = ['usuario_id = %s']
    argumentos = [usuario_id]
    if modo == 'ediciones':
        condiciones.append('accion = %s')
        argumentos.append('editar')
    if desde_fecha:
        condiciones.append('fecha >= %s')
        argumentos.append(desde_fecha)
    if hasta_fecha:
        condiciones.append('fecha < DATE_ADD(%s, INTERVAL 1 DAY)')
        argumentos.append(hasta_fecha)
    where = ' WHERE ' + ' AND '.join(condiciones)

    conexion = obtener_conexion()
    if conexion is None:
        return jsonify({'error': 'No se pudo conectar a la base de datos.'}), 503
    cursor = None
    try:
        cursor = conexion.cursor(dictionary=True)
        cursor.execute('SELECT COUNT(*) AS total FROM inventario_bitacora' + where,
                       tuple(argumentos))
        total = int((cursor.fetchone() or {}).get('total') or 0)
        cursor.execute(
            'SELECT id, usuario_id, usuario_nombre, accion, entidad, entidad_id, detalle_json, '
            'DATE_FORMAT(fecha, %s) AS fecha FROM inventario_bitacora' + where +
            ' ORDER BY fecha DESC, id DESC LIMIT %s OFFSET %s',
            tuple(['%Y-%m-%d %H:%i:%s'] + argumentos + [limite, desplazamiento]),
        )
        eventos = []
        nombre = ''
        for fila in cursor.fetchall():
            if not nombre:
                nombre = fila['usuario_nombre']
            detalle = leer_detalle_evento(fila.get('detalle_json'))
            eventos.append({
                'id': fila['id'], 'usuarioId': fila['usuario_id'],
                'usuario': fila['usuario_nombre'], 'accion': fila['accion'],
                'entidad': fila['entidad'], 'entidadId': fila['entidad_id'],
                'fecha': fila['fecha'], **detalle,
            })
        return jsonify({'usuarioId': usuario_id, 'usuario': nombre,
                        'total': total, 'eventos': eventos,
                        'offset': desplazamiento, 'limite': limite})
    except Exception:
        return jsonify({'error': 'No se pudo consultar el historial.'}), 503
    finally:
        if cursor:
            cursor.close()
        conexion.close()


TIPOS_AUDITORIA = {
    "General",
    "Empresa",
    "Departamento",
    "Ubicación",
    "Muestra",
}

ESTADOS_AUDITORIA = {
    "Planeada",
    "En proceso",
    "Finalizada",
    "Cancelada",
}

RESULTADOS_DETALLE = {
    "Pendiente",
    "Conforme",
    "Con diferencia",
    "No localizado",
    "No aplica",
}

ESTADOS_CORRECCION = {
    "No aplica",
    "Pendiente",
    "En proceso",
    "Corregida",
}

# Campos que deben verificarse de manera individual. El codigo de barras NO es
# obligatorio: los activos fisicos no utilizan etiquetas actualmente.
CAMPOS_COMPROBACION = (
    ("serie", "numeroSerieEncontrado", "numero_serie_snapshot", "Número de serie"),
    ("empresa", "empresaEncontrada", "empresa_esperada", "Empresa"),
    ("departamento", "departamentoEncontrado", "departamento_esperado", "Departamento"),
    ("responsable", "responsableEncontrado", "responsable_esperado", "Responsable"),
    ("estado", "estadoEncontrado", "estado_esperado", "Estado"),
    ("ubicacion", "ubicacionEncontrada", "ubicacion_esperada", "Ubicación"),
    ("funcionamiento", "funcionamientoEncontrado", "funcionamiento_esperado", "Funcionamiento"),
    ("extras", "extrasEncontrados", "extras_esperados", "Accesorios / extras"),
)


def validar_comprobaciones_fisicas(data, detalle):
    """Nunca completar campos sin verificar con el valor esperado.

    Debe llegar una decision explicita por cada campo:
      Coincide: el operador afirma haber contrastado el dato.
      Diferente: el operador capturo el valor real, no vacio.
      No verificable: no pudo validar el dato; queda como discrepancia.
      Sin dato: disponible solo si la base carece de valor esperado.
    """
    verificaciones = data.get("verificaciones")
    if not isinstance(verificaciones, dict):
        raise ValueError("Selecciona el resultado de cada dato físico antes de guardar.")

    encontrados = {"codigoBarras": limpiar_texto(data.get("codigoBarrasEncontrado"))}
    diferencias = []
    for clave, campo_data, campo_bd, etiqueta in CAMPOS_COMPROBACION:
        esperado = limpiar_texto(detalle.get(campo_bd))
        decision = verificaciones.get(clave)
        if decision == "Coincide":
            if not esperado:
                raise ValueError(f"{etiqueta}: no existe un valor registrado que se pueda confirmar.")
            encontrados[clave] = esperado
        elif decision == "Diferente":
            real = limpiar_texto(data.get(campo_data))
            if not isinstance(real, str):
                raise ValueError(f"{etiqueta}: captura el valor físico encontrado.")
            encontrados[clave] = real
            if normalizar_comparacion(esperado) == normalizar_comparacion(real):
                raise ValueError(f"{etiqueta}: si coincide con el registro, selecciónalo como Coincide.")
            diferencias.append(etiqueta)
        elif decision == "No verificable":
            encontrados[clave] = None
            diferencias.append(f"{etiqueta} no verificable")
        elif decision == "Sin dato" and not esperado:
            encontrados[clave] = None
        else:
            raise ValueError(f"Debes verificar {etiqueta} o indicar que no fue verificable.")
    # Un código de barras escrito opcionalmente no valida el equipo: no
    # convertir su ausencia en discrepancia ni exigir su captura.
    return encontrados, diferencias


# ============================================================
# FUNCIONES AUXILIARES
# ============================================================


def limpiar_texto(valor):
    if valor is None:
        return None

    if isinstance(valor, str):
        valor = valor.strip()
        return valor if valor else None

    return valor


def convertir_entero(valor, nombre_campo):
    try:
        numero = int(valor)
        if numero <= 0:
            raise ValueError
        return numero
    except (TypeError, ValueError) as error:
        raise ValueError(f"El campo {nombre_campo} no es válido.") from error


def convertir_booleano(valor, valor_por_defecto=False):
    if valor is None:
        return valor_por_defecto

    if isinstance(valor, bool):
        return valor

    if isinstance(valor, (int, float)):
        return bool(valor)

    if isinstance(valor, str):
        return valor.strip().lower() in {"1", "true", "sí", "si", "yes", "on"}

    return valor_por_defecto


def normalizar_fecha(valor, nombre_campo, requerida=False):
    valor = limpiar_texto(valor)

    if not valor:
        if requerida:
            raise ValueError(f"El campo {nombre_campo} es obligatorio.")
        return None

    try:
        return datetime.strptime(valor, "%Y-%m-%d").date()
    except ValueError as error:
        raise ValueError(
            f"El campo {nombre_campo} debe tener el formato AAAA-MM-DD."
        ) from error


def fecha_a_texto(valor):
    if not valor:
        return ""

    if isinstance(valor, datetime):
        return valor.isoformat(sep=" ")

    if isinstance(valor, date):
        return valor.isoformat()

    return str(valor)


def normalizar_comparacion(valor):
    if valor is None:
        return ""

    return " ".join(str(valor).strip().casefold().split())


def generar_folio(cursor):
    anio = datetime.now().year
    prefijo = f"AUD-{anio}-"

    cursor.execute(
        """
        SELECT folio
        FROM inventario_auditorias
        WHERE folio LIKE %s
        ORDER BY id DESC
        LIMIT 1
        FOR UPDATE
        """,
        (f"{prefijo}%",),
    )

    ultimo = cursor.fetchone()
    consecutivo = 1

    if ultimo and ultimo.get("folio"):
        try:
            consecutivo = int(ultimo["folio"].split("-")[-1]) + 1
        except (TypeError, ValueError, IndexError):
            consecutivo = 1

    return f"{prefijo}{consecutivo:06d}"


def calcular_porcentaje(revisados, total):
    total = int(total or 0)
    revisados = int(revisados or 0)

    if total <= 0:
        return 0

    return round((revisados / total) * 100, 2)


def formatear_auditoria(row):
    total = int(row.get("total_equipos") or 0)
    revisados = int(row.get("revisados") or 0)

    return {
        "id": row["id"],
        "folio": row.get("folio") or "",
        "nombre": row.get("nombre") or "",
        "tipo": row.get("tipo") or "General",
        "empresa": row.get("empresa") or "",
        "departamento": row.get("departamento") or "",
        "ubicacion": row.get("ubicacion") or "",
        "incluirBajas": bool(row.get("incluir_bajas")),
        "fechaProgramada": fecha_a_texto(row.get("fecha_programada")),
        "fechaInicio": fecha_a_texto(row.get("fecha_inicio")),
        "fechaFinalizacion": fecha_a_texto(row.get("fecha_finalizacion")),
        "estado": row.get("estado") or "Planeada",
        "auditorResponsable": row.get("auditor_responsable") or "",
        "observaciones": row.get("observaciones") or "",
        "conclusiones": row.get("conclusiones") or "",
        "usuarioRegistro": row.get("usuario_registro") or "Sistema",
        "fechaCreacion": fecha_a_texto(row.get("fecha_creacion")),
        "fechaActualizacion": fecha_a_texto(row.get("fecha_actualizacion")),
        "resumen": {
            "totalEquipos": total,
            "revisados": revisados,
            "pendientes": int(row.get("pendientes") or 0),
            "conformes": int(row.get("conformes") or 0),
            "conDiferencia": int(row.get("con_diferencia") or 0),
            "noLocalizados": int(row.get("no_localizados") or 0),
            "noAplica": int(row.get("no_aplica") or 0),
            "correccionesPendientes": int(row.get("correcciones_pendientes") or 0),
            "porcentaje": calcular_porcentaje(revisados, total),
        },
    }


def formatear_detalle(row):
    return {
        "id": row["id"],
        "auditoriaId": row["auditoria_id"],
        "equipoId": row.get("equipo_id"),
        "esperado": {
            "inventario": row.get("numero_inventario_snapshot") or "",
            "codigoBarras": row.get("codigo_barras_snapshot") or "",
            "descripcion": row.get("descripcion_snapshot") or "",
            "categoria": row.get("categoria_snapshot") or "",
            "marca": row.get("marca_snapshot") or "",
            "modelo": row.get("modelo_snapshot") or "",
            "serie": row.get("numero_serie_snapshot") or "",
            "empresa": row.get("empresa_esperada") or "",
            "departamento": row.get("departamento_esperado") or "",
            "responsable": row.get("responsable_esperado") or "",
            "estado": row.get("estado_esperado") or "",
            "ubicacion": row.get("ubicacion_esperada") or "",
            "funcionamiento": row.get("funcionamiento_esperado") or "",
            "extras": row.get("extras_esperados") or "",
        },
        "encontrado": (
            None if row.get("encontrado") is None else bool(row.get("encontrado"))
        ),
        "encontradoDatos": {
            "codigoBarras": row.get("codigo_barras_encontrado") or "",
            "serie": row.get("numero_serie_encontrado") or "",
            "empresa": row.get("empresa_encontrada") or "",
            "departamento": row.get("departamento_encontrado") or "",
            "responsable": row.get("responsable_encontrado") or "",
            "estado": row.get("estado_encontrado") or "",
            "ubicacion": row.get("ubicacion_encontrada") or "",
            "funcionamiento": row.get("funcionamiento_encontrado") or "",
            "extras": row.get("extras_encontrados") or "",
        },
        "resultado": row.get("resultado") or "Pendiente",
        "tipoDiferencia": row.get("tipo_diferencia") or "",
        "observaciones": row.get("observaciones") or "",
        "evidenciaUrl": row.get("evidencia_url") or "",
        "accionRequerida": row.get("accion_requerida") or "",
        "estadoCorreccion": row.get("estado_correccion") or "No aplica",
        "fechaRevision": fecha_a_texto(row.get("fecha_revision")),
        "revisadoPor": row.get("revisado_por") or "",
        "fechaCreacion": fecha_a_texto(row.get("fecha_creacion")),
        "fechaActualizacion": fecha_a_texto(row.get("fecha_actualizacion")),
    }


def consulta_auditorias_base():
    return """
        SELECT
            a.id,
            a.folio,
            a.nombre,
            a.tipo,
            a.empresa,
            a.departamento,
            a.ubicacion,
            a.incluir_bajas,
            a.fecha_programada,
            a.fecha_inicio,
            a.fecha_finalizacion,
            a.estado,
            a.auditor_responsable,
            a.observaciones,
            a.conclusiones,
            a.usuario_registro,
            a.fecha_creacion,
            a.fecha_actualizacion,

            COALESCE(r.total_equipos, 0) AS total_equipos,
            COALESCE(r.revisados, 0) AS revisados,
            COALESCE(r.pendientes, 0) AS pendientes,
            COALESCE(r.conformes, 0) AS conformes,
            COALESCE(r.con_diferencia, 0) AS con_diferencia,
            COALESCE(r.no_localizados, 0) AS no_localizados,
            COALESCE(r.no_aplica, 0) AS no_aplica,
            COALESCE(r.correcciones_pendientes, 0) AS correcciones_pendientes

        FROM inventario_auditorias a

        LEFT JOIN (
            SELECT
                auditoria_id,
                COUNT(*) AS total_equipos,
                SUM(CASE WHEN resultado <> 'Pendiente' THEN 1 ELSE 0 END) AS revisados,
                SUM(CASE WHEN resultado = 'Pendiente' THEN 1 ELSE 0 END) AS pendientes,
                SUM(CASE WHEN resultado = 'Conforme' THEN 1 ELSE 0 END) AS conformes,
                SUM(CASE WHEN resultado = 'Con diferencia' THEN 1 ELSE 0 END) AS con_diferencia,
                SUM(CASE WHEN resultado = 'No localizado' THEN 1 ELSE 0 END) AS no_localizados,
                SUM(CASE WHEN resultado = 'No aplica' THEN 1 ELSE 0 END) AS no_aplica,
                SUM(
                    CASE
                        WHEN estado_correccion IN ('Pendiente', 'En proceso')
                        THEN 1
                        ELSE 0
                    END
                ) AS correcciones_pendientes

            FROM inventario_auditoria_detalles
            GROUP BY auditoria_id
        ) r ON r.auditoria_id = a.id
    """


def construir_filtros_auditoria():
    busqueda = limpiar_texto(request.args.get("busqueda"))
    estado = limpiar_texto(request.args.get("estado"))
    tipo = limpiar_texto(request.args.get("tipo"))
    empresa = limpiar_texto(request.args.get("empresa"))
    fecha_inicio = normalizar_fecha(request.args.get("fechaInicio"), "fecha inicial")
    fecha_fin = normalizar_fecha(request.args.get("fechaFin"), "fecha final")

    condiciones = []
    parametros = []

    if busqueda:
        termino = f"%{busqueda}%"
        condiciones.append(
            """
            (
                a.folio LIKE %s
                OR a.nombre LIKE %s
                OR a.auditor_responsable LIKE %s
                OR a.empresa LIKE %s
                OR a.departamento LIKE %s
                OR a.ubicacion LIKE %s
                OR a.observaciones LIKE %s
                OR a.conclusiones LIKE %s
                OR a.usuario_registro LIKE %s
            )
            """
        )
        parametros.extend([termino] * 9)

    if estado and estado != "Todos":
        condiciones.append("a.estado = %s")
        parametros.append(estado)

    if tipo and tipo != "Todos":
        condiciones.append("a.tipo = %s")
        parametros.append(tipo)

    if empresa and empresa != "Todas":
        condiciones.append("a.empresa = %s")
        parametros.append(empresa)

    if fecha_inicio:
        condiciones.append("a.fecha_programada >= %s")
        parametros.append(fecha_inicio)

    if fecha_fin:
        condiciones.append("a.fecha_programada <= %s")
        parametros.append(fecha_fin)

    clausula = ""
    if condiciones:
        clausula = " WHERE " + " AND ".join(condiciones)

    return clausula, parametros


# ============================================================
# GET: LISTAR AUDITORÍAS
# ============================================================


@auditorias_bp.route("", methods=["GET"])
@auditorias_bp.route("/", methods=["GET"])
def listar_auditorias():
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        clausula, parametros = construir_filtros_auditoria()
        consulta = consulta_auditorias_base() + clausula + """
            ORDER BY
                CASE a.estado
                    WHEN 'En proceso' THEN 1
                    WHEN 'Planeada' THEN 2
                    WHEN 'Finalizada' THEN 3
                    WHEN 'Cancelada' THEN 4
                    ELSE 5
                END,
                a.fecha_programada DESC,
                a.id DESC
        """

        cursor.execute(consulta, tuple(parametros))
        registros = cursor.fetchall()

        return jsonify([formatear_auditoria(registro) for registro in registros])

    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    except Exception as error:
        print("Error al listar auditorías:", error)
        return jsonify(
            {
                "error": "No se pudieron cargar las auditorías.",
                "detalle": str(error),
            }
        ), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# GET: ESTADÍSTICAS
# ============================================================


@auditorias_bp.route("/estadisticas", methods=["GET"])
def obtener_estadisticas():
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        clausula, parametros = construir_filtros_auditoria()
        consulta = """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN a.estado = 'Planeada' THEN 1 ELSE 0 END) AS planeadas,
                SUM(CASE WHEN a.estado = 'En proceso' THEN 1 ELSE 0 END) AS en_proceso,
                SUM(CASE WHEN a.estado = 'Finalizada' THEN 1 ELSE 0 END) AS finalizadas,
                SUM(CASE WHEN a.estado = 'Cancelada' THEN 1 ELSE 0 END) AS canceladas
            FROM inventario_auditorias a
        """ + clausula

        cursor.execute(consulta, tuple(parametros))
        resultado = cursor.fetchone() or {}

        return jsonify(
            {
                "total": int(resultado.get("total") or 0),
                "planeadas": int(resultado.get("planeadas") or 0),
                "enProceso": int(resultado.get("en_proceso") or 0),
                "finalizadas": int(resultado.get("finalizadas") or 0),
                "canceladas": int(resultado.get("canceladas") or 0),
            }
        )

    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    except Exception as error:
        print("Error al obtener estadísticas de auditorías:", error)
        return jsonify(
            {
                "error": "No se pudieron obtener las estadísticas.",
                "detalle": str(error),
            }
        ), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# GET: CATÁLOGOS
# ============================================================


@auditorias_bp.route("/catalogos", methods=["GET"])
def obtener_catalogos():
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT DISTINCT empresa
            FROM inventario_equipos
            WHERE empresa IS NOT NULL AND TRIM(empresa) <> ''
            ORDER BY empresa
            """
        )
        empresas = [fila["empresa"] for fila in cursor.fetchall()]

        cursor.execute(
            """
            SELECT DISTINCT departamento
            FROM inventario_equipos
            WHERE departamento IS NOT NULL AND TRIM(departamento) <> ''
            ORDER BY departamento
            """
        )
        departamentos = [fila["departamento"] for fila in cursor.fetchall()]

        cursor.execute(
            """
            SELECT DISTINCT ubicacion
            FROM inventario_equipos
            WHERE ubicacion IS NOT NULL AND TRIM(ubicacion) <> ''
            ORDER BY ubicacion
            """
        )
        ubicaciones = [fila["ubicacion"] for fila in cursor.fetchall()]

        cursor.execute(
            """
            SELECT
                id,
                numero_inventario,
                descripcion,
                marca,
                modelo,
                empresa,
                departamento,
                ubicacion,
                estado
            FROM inventario_equipos
            ORDER BY numero_inventario
            """
        )

        equipos = []
        for fila in cursor.fetchall():
            equipos.append(
                {
                    "id": fila["id"],
                    "inventario": fila.get("numero_inventario") or "",
                    "nombre": fila.get("descripcion") or "",
                    "marca": fila.get("marca") or "",
                    "modelo": fila.get("modelo") or "",
                    "empresa": fila.get("empresa") or "",
                    "departamento": fila.get("departamento") or "",
                    "ubicacion": fila.get("ubicacion") or "",
                    "estado": fila.get("estado") or "",
                }
            )

        return jsonify(
            {
                "tipos": sorted(TIPOS_AUDITORIA),
                "estados": ["Todos", "Planeada", "En proceso", "Finalizada", "Cancelada"],
                "empresas": empresas,
                "departamentos": departamentos,
                "ubicaciones": ubicaciones,
                "equipos": equipos,
            }
        )

    except Exception as error:
        print("Error al obtener catálogos de auditorías:", error)
        return jsonify(
            {
                "error": "No se pudieron obtener los catálogos.",
                "detalle": str(error),
            }
        ), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# GET: DETALLE COMPLETO DE AUDITORÍA
# ============================================================


@auditorias_bp.route("/<int:auditoria_id>", methods=["GET"])
def obtener_auditoria(auditoria_id):
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute(
            consulta_auditorias_base() + " WHERE a.id = %s LIMIT 1",
            (auditoria_id,),
        )
        auditoria = cursor.fetchone()

        if not auditoria:
            return jsonify({"error": "Auditoría no encontrada."}), 404

        busqueda = limpiar_texto(request.args.get("busquedaDetalle"))
        resultado = limpiar_texto(request.args.get("resultado"))

        condiciones = ["auditoria_id = %s"]
        parametros = [auditoria_id]

        if busqueda:
            termino = f"%{busqueda}%"
            condiciones.append(
                """
                (
                    numero_inventario_snapshot LIKE %s
                    OR descripcion_snapshot LIKE %s
                    OR marca_snapshot LIKE %s
                    OR modelo_snapshot LIKE %s
                    OR numero_serie_snapshot LIKE %s
                    OR responsable_esperado LIKE %s
                    OR responsable_encontrado LIKE %s
                    OR observaciones LIKE %s
                )
                """
            )
            parametros.extend([termino] * 8)

        if resultado and resultado != "Todos":
            condiciones.append("resultado = %s")
            parametros.append(resultado)

        cursor.execute(
            """
            SELECT *
            FROM inventario_auditoria_detalles
            WHERE """ + " AND ".join(condiciones) + """
            ORDER BY
                CASE resultado
                    WHEN 'Pendiente' THEN 1
                    WHEN 'Con diferencia' THEN 2
                    WHEN 'No localizado' THEN 3
                    WHEN 'Conforme' THEN 4
                    WHEN 'No aplica' THEN 5
                    ELSE 6
                END,
                numero_inventario_snapshot
            """,
            tuple(parametros),
        )

        respuesta = formatear_auditoria(auditoria)
        respuesta["detalles"] = [formatear_detalle(fila) for fila in cursor.fetchall()]

        return jsonify(respuesta)

    except Exception as error:
        print("Error al obtener auditoría:", error)
        return jsonify(
            {
                "error": "No se pudo obtener el detalle de la auditoría.",
                "detalle": str(error),
            }
        ), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# POST: CREAR AUDITORÍA Y GENERAR SNAPSHOT
# ============================================================


@auditorias_bp.route("", methods=["POST"])
@auditorias_bp.route("/", methods=["POST"])
def crear_auditoria():
    data = request.get_json(silent=True)

    if not data:
        return jsonify({"error": "No se recibieron datos."}), 400

    try:
        nombre = limpiar_texto(data.get("nombre"))
        tipo = limpiar_texto(data.get("tipo")) or "General"
        fecha_programada = normalizar_fecha(
            data.get("fechaProgramada"), "fecha programada", requerida=True
        )
        # Nunca aceptar el auditor ni el autor desde el navegador.
        # La cuenta está validada por validar_acceso_inventario().
        auditor_responsable = limpiar_texto(g.inventario_usuario['nombre'])

        if not nombre:
            raise ValueError("El nombre de la auditoría es obligatorio.")

        if tipo not in TIPOS_AUDITORIA:
            raise ValueError("El tipo de auditoría no es válido.")

        if not auditor_responsable:
            raise ValueError("El auditor responsable es obligatorio.")

        empresa = limpiar_texto(data.get("empresa"))
        departamento = limpiar_texto(data.get("departamento"))
        ubicacion = limpiar_texto(data.get("ubicacion"))
        incluir_bajas = convertir_booleano(data.get("incluirBajas"), False)
        observaciones = limpiar_texto(data.get("observaciones"))
        usuario_registro = auditor_responsable

        if tipo == "Empresa" and not empresa:
            raise ValueError("Selecciona la empresa que se auditará.")

        if tipo == "Departamento" and not departamento:
            raise ValueError("Selecciona el departamento que se auditará.")

        if tipo == "Ubicación" and not ubicacion:
            raise ValueError("Selecciona la ubicación que se auditará.")

        equipo_ids = []
        if tipo == "Muestra":
            valores = data.get("equipoIds") or []
            if not isinstance(valores, list) or not valores:
                raise ValueError("Selecciona al menos un equipo para la muestra.")

            equipo_ids = sorted(
                {
                    convertir_entero(valor, "equipo")
                    for valor in valores
                }
            )

    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        conexion.start_transaction()
        folio = generar_folio(cursor)

        cursor.execute(
            """
            INSERT INTO inventario_auditorias (
                folio,
                nombre,
                tipo,
                empresa,
                departamento,
                ubicacion,
                incluir_bajas,
                fecha_programada,
                estado,
                auditor_responsable,
                observaciones,
                usuario_registro
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Planeada', %s, %s, %s)
            """,
            (
                folio,
                nombre,
                tipo,
                empresa,
                departamento,
                ubicacion,
                1 if incluir_bajas else 0,
                fecha_programada,
                auditor_responsable,
                observaciones,
                usuario_registro,
            ),
        )

        auditoria_id = cursor.lastrowid

        condiciones = []
        parametros = []

        if not incluir_bajas:
            condiciones.append("estado <> 'Baja'")

        if tipo == "Empresa":
            condiciones.append("empresa = %s")
            parametros.append(empresa)
        elif tipo == "Departamento":
            condiciones.append("departamento = %s")
            parametros.append(departamento)
        elif tipo == "Ubicación":
            condiciones.append("ubicacion = %s")
            parametros.append(ubicacion)
        elif tipo == "Muestra":
            marcadores = ", ".join(["%s"] * len(equipo_ids))
            condiciones.append(f"id IN ({marcadores})")
            parametros.extend(equipo_ids)

        consulta_equipos = """
            SELECT
                id,
                numero_inventario,
                codigo_barras,
                descripcion,
                categoria,
                marca,
                modelo,
                numero_serie,
                empresa,
                departamento,
                responsable,
                estado,
                ubicacion,
                funcionamiento,
                extras
            FROM inventario_equipos
        """

        if condiciones:
            consulta_equipos += " WHERE " + " AND ".join(condiciones)

        consulta_equipos += " ORDER BY numero_inventario"

        cursor.execute(consulta_equipos, tuple(parametros))
        equipos = cursor.fetchall()

        if not equipos:
            conexion.rollback()
            return jsonify(
                {
                    "error": "No se encontraron equipos para el alcance seleccionado."
                }
            ), 409

        filas_detalle = []
        for equipo in equipos:
            filas_detalle.append(
                (
                    auditoria_id,
                    equipo["id"],
                    equipo.get("numero_inventario") or f"EQUIPO-{equipo['id']}",
                    equipo.get("codigo_barras"),
                    equipo.get("descripcion"),
                    equipo.get("categoria"),
                    equipo.get("marca"),
                    equipo.get("modelo"),
                    equipo.get("numero_serie"),
                    equipo.get("empresa"),
                    equipo.get("departamento"),
                    equipo.get("responsable"),
                    equipo.get("estado"),
                    equipo.get("ubicacion"),
                    equipo.get("funcionamiento"),
                    equipo.get("extras"),
                )
            )

        cursor.executemany(
            """
            INSERT INTO inventario_auditoria_detalles (
                auditoria_id,
                equipo_id,
                numero_inventario_snapshot,
                codigo_barras_snapshot,
                descripcion_snapshot,
                categoria_snapshot,
                marca_snapshot,
                modelo_snapshot,
                numero_serie_snapshot,
                empresa_esperada,
                departamento_esperado,
                responsable_esperado,
                estado_esperado,
                ubicacion_esperada,
                funcionamiento_esperado,
                extras_esperados
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            filas_detalle,
        )

        registrar_evento(cursor, 'crear', 'Auditoría', auditoria_id)
        conexion.commit()

        return jsonify(
            {
                "message": "Auditoría creada correctamente.",
                "id": auditoria_id,
                "folio": folio,
                "estado": "Planeada",
                "totalEquipos": len(equipos),
            }
        ), 201

    except Exception as error:
        conexion.rollback()
        print("Error al crear auditoría:", error)
        return jsonify(
            {
                "error": "No se pudo crear la auditoría.",
                "detalle": str(error),
            }
        ), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# PUT: ACTUALIZAR AUDITORÍA PLANEADA
# ============================================================


@auditorias_bp.route("/<int:auditoria_id>", methods=["PUT"])
def actualizar_auditoria(auditoria_id):
    data = request.get_json(silent=True)

    if not data:
        return jsonify({"error": "No se recibieron datos."}), 400

    try:
        nombre = limpiar_texto(data.get("nombre"))
        fecha_programada = normalizar_fecha(
            data.get("fechaProgramada"), "fecha programada", requerida=True
        )
        observaciones = limpiar_texto(data.get("observaciones"))

        if not nombre:
            raise ValueError("El nombre de la auditoría es obligatorio.")

    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        conexion.start_transaction()
        cursor.execute(
            """
            SELECT id, estado, auditor_responsable
            FROM inventario_auditorias
            WHERE id = %s
            FOR UPDATE
            """,
            (auditoria_id,),
        )
        auditoria = cursor.fetchone()

        if not auditoria:
            conexion.rollback()
            return jsonify({"error": "Auditoría no encontrada."}), 404

        if auditoria["estado"] != "Planeada":
            conexion.rollback()
            return jsonify(
                {
                    "error": "Solamente una auditoría planeada puede editarse."
                }
            ), 409

        estado_anterior_bitacora = capturar_estado(cursor, 'Auditoría', auditoria_id)
        cursor.execute(
            """
            UPDATE inventario_auditorias
            SET
                nombre = %s,
                fecha_programada = %s,
                auditor_responsable = %s,
                observaciones = %s
            WHERE id = %s
            """,
            (
                nombre,
                fecha_programada,
                auditoria["auditor_responsable"],
                observaciones,
                auditoria_id,
            ),
        )

        registrar_evento(cursor, 'editar', 'Auditoría', auditoria_id, anterior=estado_anterior_bitacora)
        conexion.commit()
        return jsonify(
            {
                "message": "Auditoría actualizada correctamente.",
                "id": auditoria_id,
            }
        )

    except Exception as error:
        conexion.rollback()
        print("Error al actualizar auditoría:", error)
        return jsonify(
            {
                "error": "No se pudo actualizar la auditoría.",
                "detalle": str(error),
            }
        ), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# PUT: INICIAR AUDITORÍA
# ============================================================


@auditorias_bp.route("/<int:auditoria_id>/iniciar", methods=["PUT"])
def iniciar_auditoria(auditoria_id):
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        conexion.start_transaction()
        cursor.execute(
            """
            SELECT id, estado
            FROM inventario_auditorias
            WHERE id = %s
            FOR UPDATE
            """,
            (auditoria_id,),
        )
        auditoria = cursor.fetchone()

        if not auditoria:
            conexion.rollback()
            return jsonify({"error": "Auditoría no encontrada."}), 404

        if auditoria["estado"] != "Planeada":
            conexion.rollback()
            return jsonify(
                {
                    "error": "Solamente una auditoría planeada puede iniciarse."
                }
            ), 409

        cursor.execute(
            """
            SELECT COUNT(*) AS total
            FROM inventario_auditoria_detalles
            WHERE auditoria_id = %s
            """,
            (auditoria_id,),
        )
        total = int((cursor.fetchone() or {}).get("total") or 0)

        if total == 0:
            conexion.rollback()
            return jsonify(
                {
                    "error": "La auditoría no contiene equipos para revisar."
                }
            ), 409

        estado_anterior_bitacora = capturar_estado(cursor, 'Auditoría', auditoria_id)
        cursor.execute(
            """
            UPDATE inventario_auditorias
            SET estado = 'En proceso', fecha_inicio = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (auditoria_id,),
        )

        registrar_evento(cursor, 'iniciar', 'Auditoría', auditoria_id, anterior=estado_anterior_bitacora)
        conexion.commit()
        return jsonify(
            {
                "message": "Auditoría iniciada correctamente.",
                "id": auditoria_id,
                "estado": "En proceso",
            }
        )

    except Exception as error:
        conexion.rollback()
        print("Error al iniciar auditoría:", error)
        return jsonify(
            {
                "error": "No se pudo iniciar la auditoría.",
                "detalle": str(error),
            }
        ), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# PUT: REGISTRAR REVISIÓN DE UN EQUIPO
# ============================================================


@auditorias_bp.route(
    "/<int:auditoria_id>/detalles/<int:detalle_id>/revisar",
    methods=["PUT"],
)
def revisar_equipo(auditoria_id, detalle_id):
    data = request.get_json(silent=True)

    if not data:
        return jsonify({"error": "No se recibieron datos."}), 400

    if data.get("resultado") != "No aplica" and not isinstance(data.get("encontrado"), bool):
        return jsonify({"error": "Indica expresamente si localizaste el dispositivo."}), 400

    encontrado = data.get("encontrado") is True
    resultado_solicitado = limpiar_texto(data.get("resultado"))
    # El responsable de cada revisión se toma de la cuenta verificada.
    revisado_por = limpiar_texto(g.inventario_usuario['nombre'])
    observaciones = limpiar_texto(data.get("observaciones"))
    evidencia_url = limpiar_texto(data.get("evidenciaUrl"))
    accion_requerida = limpiar_texto(data.get("accionRequerida"))

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        conexion.start_transaction()

        cursor.execute(
            """
            SELECT id, estado
            FROM inventario_auditorias
            WHERE id = %s
            FOR UPDATE
            """,
            (auditoria_id,),
        )
        auditoria = cursor.fetchone()

        if not auditoria:
            conexion.rollback()
            return jsonify({"error": "Auditoría no encontrada."}), 404

        if auditoria["estado"] != "En proceso":
            conexion.rollback()
            return jsonify(
                {
                    "error": "Solo se pueden revisar equipos en una auditoría en proceso."
                }
            ), 409

        cursor.execute(
            """
            SELECT *
            FROM inventario_auditoria_detalles
            WHERE id = %s AND auditoria_id = %s
            FOR UPDATE
            """,
            (detalle_id, auditoria_id),
        )
        detalle = cursor.fetchone()

        if not detalle:
            conexion.rollback()
            return jsonify({"error": "Detalle de auditoría no encontrado."}), 404

        if resultado_solicitado == "No aplica":
            if not observaciones:
                conexion.rollback()
                return jsonify({"error": "Explica por qué este equipo no aplica a la auditoría."}), 400
            encontrado = False
            resultado = "No aplica"
            tipo_diferencia = None
            estado_correccion = "No aplica"
            # No copiar como encontrados valores que nunca se comprobaron.
            encontrados = {"codigoBarras": None, **{clave: None for clave, _, _, _ in CAMPOS_COMPROBACION}}

        elif not encontrado:
            if not observaciones:
                conexion.rollback()
                return jsonify({"error": "Describe la búsqueda del equipo no localizado."}), 400
            resultado = "No localizado"
            tipo_diferencia = "Equipo no localizado"
            estado_correccion = "Pendiente"

            encontrados = {
                "codigoBarras": None,
                "serie": None,
                "empresa": None,
                "departamento": None,
                "responsable": None,
                "estado": None,
                "ubicacion": None,
                "funcionamiento": None,
                "extras": None,
            }

        else:
            # El backend exige confirmación explícita. No utilizar valores
            # esperados como respaldo de campos sin comprobar.
            try:
                encontrados, diferencias = validar_comprobaciones_fisicas(data, detalle)
            except ValueError as error:
                conexion.rollback()
                return jsonify({"error": str(error)}), 400

            if diferencias:
                resultado = "Con diferencia"
                # Etiquetas calculadas por servidor: no se pueden ocultar con texto manual.
                tipo_diferencia = ", ".join(diferencias)
                estado_correccion = "Pendiente"
            else:
                resultado = "Conforme"
                tipo_diferencia = None
                accion_requerida = None
                estado_correccion = "No aplica"

        estado_anterior_bitacora = capturar_estado(cursor, 'Detalle de auditoría', detalle_id)
        cursor.execute(
            """
            UPDATE inventario_auditoria_detalles
            SET
                encontrado = %s,
                codigo_barras_encontrado = %s,
                numero_serie_encontrado = %s,
                empresa_encontrada = %s,
                departamento_encontrado = %s,
                responsable_encontrado = %s,
                estado_encontrado = %s,
                ubicacion_encontrada = %s,
                funcionamiento_encontrado = %s,
                extras_encontrados = %s,
                resultado = %s,
                tipo_diferencia = %s,
                observaciones = %s,
                evidencia_url = %s,
                accion_requerida = %s,
                estado_correccion = %s,
                fecha_revision = CURRENT_TIMESTAMP,
                revisado_por = %s
            WHERE id = %s AND auditoria_id = %s
            """,
            (
                1 if encontrado else 0,
                encontrados["codigoBarras"],
                encontrados["serie"],
                encontrados["empresa"],
                encontrados["departamento"],
                encontrados["responsable"],
                encontrados["estado"],
                encontrados["ubicacion"],
                encontrados["funcionamiento"],
                encontrados["extras"],
                resultado,
                tipo_diferencia,
                observaciones,
                evidencia_url,
                accion_requerida,
                estado_correccion,
                revisado_por,
                detalle_id,
                auditoria_id,
            ),
        )

        cursor.execute(
            """
            SELECT *
            FROM inventario_auditoria_detalles
            WHERE id = %s
            """,
            (detalle_id,),
        )
        detalle_actualizado = cursor.fetchone()

        registrar_evento(cursor, 'revisar', 'Detalle de auditoría', detalle_id, anterior=estado_anterior_bitacora)
        conexion.commit()

        return jsonify(
            {
                "message": "Revisión registrada correctamente.",
                "detalle": formatear_detalle(detalle_actualizado),
            }
        )

    except Exception as error:
        conexion.rollback()
        print("Error al revisar equipo:", error)
        return jsonify(
            {
                "error": "No se pudo guardar la revisión del equipo.",
                "detalle": str(error),
            }
        ), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# PUT: ACTUALIZAR ACCIÓN CORRECTIVA
# ============================================================


@auditorias_bp.route(
    "/<int:auditoria_id>/detalles/<int:detalle_id>/correccion",
    methods=["PUT"],
)
def actualizar_correccion(auditoria_id, detalle_id):
    data = request.get_json(silent=True) or {}

    estado_correccion = limpiar_texto(data.get("estadoCorreccion"))
    accion_requerida = limpiar_texto(data.get("accionRequerida"))

    if estado_correccion not in ESTADOS_CORRECCION:
        return jsonify({"error": "El estado de corrección no es válido."}), 400

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT id, resultado
            FROM inventario_auditoria_detalles
            WHERE id = %s AND auditoria_id = %s
            """,
            (detalle_id, auditoria_id),
        )
        detalle = cursor.fetchone()

        if not detalle:
            return jsonify({"error": "Detalle de auditoría no encontrado."}), 404

        if detalle["resultado"] in {"Pendiente", "Conforme", "No aplica"}:
            return jsonify(
                {
                    "error": "Este resultado no requiere una acción correctiva."
                }
            ), 409

        estado_anterior_bitacora = capturar_estado(cursor, 'Corrección de auditoría', detalle_id)
        cursor.execute(
            """
            UPDATE inventario_auditoria_detalles
            SET accion_requerida = %s, estado_correccion = %s
            WHERE id = %s AND auditoria_id = %s
            """,
            (accion_requerida, estado_correccion, detalle_id, auditoria_id),
        )

        registrar_evento(cursor, 'editar', 'Corrección de auditoría', detalle_id, anterior=estado_anterior_bitacora)
        conexion.commit()
        return jsonify(
            {
                "message": "Acción correctiva actualizada correctamente.",
                "id": detalle_id,
                "estadoCorreccion": estado_correccion,
            }
        )

    except Exception as error:
        conexion.rollback()
        print("Error al actualizar corrección:", error)
        return jsonify(
            {
                "error": "No se pudo actualizar la acción correctiva.",
                "detalle": str(error),
            }
        ), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# PUT: FINALIZAR AUDITORÍA
# ============================================================


@auditorias_bp.route("/<int:auditoria_id>/finalizar", methods=["PUT"])
def finalizar_auditoria(auditoria_id):
    data = request.get_json(silent=True) or {}
    conclusiones = limpiar_texto(data.get("conclusiones"))

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        conexion.start_transaction()
        cursor.execute(
            """
            SELECT id, estado
            FROM inventario_auditorias
            WHERE id = %s
            FOR UPDATE
            """,
            (auditoria_id,),
        )
        auditoria = cursor.fetchone()

        if not auditoria:
            conexion.rollback()
            return jsonify({"error": "Auditoría no encontrada."}), 404

        if auditoria["estado"] != "En proceso":
            conexion.rollback()
            return jsonify(
                {
                    "error": "Solamente una auditoría en proceso puede finalizarse."
                }
            ), 409

        cursor.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN resultado = 'Pendiente' THEN 1 ELSE 0 END) AS pendientes
            FROM inventario_auditoria_detalles
            WHERE auditoria_id = %s
            """,
            (auditoria_id,),
        )
        resumen = cursor.fetchone() or {}
        pendientes = int(resumen.get("pendientes") or 0)

        if pendientes > 0:
            conexion.rollback()
            return jsonify(
                {
                    "error": "No puedes finalizar mientras existan equipos pendientes de revisión.",
                    "pendientes": pendientes,
                }
            ), 409

        estado_anterior_bitacora = capturar_estado(cursor, 'Auditoría', auditoria_id)
        cursor.execute(
            """
            UPDATE inventario_auditorias
            SET
                estado = 'Finalizada',
                fecha_finalizacion = CURRENT_TIMESTAMP,
                conclusiones = %s
            WHERE id = %s
            """,
            (conclusiones, auditoria_id),
        )

        registrar_evento(cursor, 'finalizar', 'Auditoría', auditoria_id, anterior=estado_anterior_bitacora)
        conexion.commit()
        return jsonify(
            {
                "message": "Auditoría finalizada correctamente.",
                "id": auditoria_id,
                "estado": "Finalizada",
            }
        )

    except Exception as error:
        conexion.rollback()
        print("Error al finalizar auditoría:", error)
        return jsonify(
            {
                "error": "No se pudo finalizar la auditoría.",
                "detalle": str(error),
            }
        ), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# PUT: CANCELAR AUDITORÍA
# ============================================================


@auditorias_bp.route("/<int:auditoria_id>/cancelar", methods=["PUT"])
def cancelar_auditoria(auditoria_id):
    data = request.get_json(silent=True) or {}
    motivo = limpiar_texto(data.get("motivo"))

    if not motivo:
        return jsonify({"error": "Indica el motivo de cancelación."}), 400

    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        conexion.start_transaction()
        cursor.execute(
            """
            SELECT id, estado, observaciones
            FROM inventario_auditorias
            WHERE id = %s
            FOR UPDATE
            """,
            (auditoria_id,),
        )
        auditoria = cursor.fetchone()

        if not auditoria:
            conexion.rollback()
            return jsonify({"error": "Auditoría no encontrada."}), 404

        if auditoria["estado"] in {"Finalizada", "Cancelada"}:
            conexion.rollback()
            return jsonify(
                {
                    "error": "La auditoría ya no puede cancelarse."
                }
            ), 409

        observaciones_anteriores = limpiar_texto(auditoria.get("observaciones"))
        texto_cancelacion = (
            f"Cancelada el {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: {motivo}"
        )
        observaciones = (
            f"{observaciones_anteriores}\n\n{texto_cancelacion}"
            if observaciones_anteriores
            else texto_cancelacion
        )

        estado_anterior_bitacora = capturar_estado(cursor, 'Auditoría', auditoria_id)
        cursor.execute(
            """
            UPDATE inventario_auditorias
            SET estado = 'Cancelada', observaciones = %s
            WHERE id = %s
            """,
            (observaciones, auditoria_id),
        )

        registrar_evento(cursor, 'cancelar', 'Auditoría', auditoria_id, anterior=estado_anterior_bitacora)
        conexion.commit()
        return jsonify(
            {
                "message": "Auditoría cancelada correctamente.",
                "id": auditoria_id,
                "estado": "Cancelada",
            }
        )

    except Exception as error:
        conexion.rollback()
        print("Error al cancelar auditoría:", error)
        return jsonify(
            {
                "error": "No se pudo cancelar la auditoría.",
                "detalle": str(error),
            }
        ), 500

    finally:
        cursor.close()
        conexion.close()


# ============================================================
# DELETE: ELIMINAR AUDITORÍA PLANEADA
# ============================================================


@auditorias_bp.route("/<int:auditoria_id>", methods=["DELETE"])
def eliminar_auditoria(auditoria_id):
    """Las auditorías son registros históricos. Nunca borrarlas físicamente."""
    return jsonify({
        "error": "No se permite eliminar auditorías. Utiliza Cancelar con un motivo."
    }), 405


# ============================================================
# GET: EXPORTAR AUDITORÍA A CSV
# ============================================================


@auditorias_bp.route("/<int:auditoria_id>/exportar", methods=["GET"])
def exportar_auditoria(auditoria_id):
    conexion = obtener_conexion()
    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT folio, nombre
            FROM inventario_auditorias
            WHERE id = %s
            """,
            (auditoria_id,),
        )
        auditoria = cursor.fetchone()

        if not auditoria:
            return jsonify({"error": "Auditoría no encontrada."}), 404

        cursor.execute(
            """
            SELECT *
            FROM inventario_auditoria_detalles
            WHERE auditoria_id = %s
            ORDER BY numero_inventario_snapshot
            """,
            (auditoria_id,),
        )
        detalles = cursor.fetchall()

        archivo_texto = StringIO()
        escritor = csv.writer(archivo_texto)

        escritor.writerow(
            [
                "Folio auditoría",
                "Nombre auditoría",
                "No. inventario",
                "Equipo",
                "Marca",
                "Modelo",
                "Serie esperada",
                "Serie encontrada",
                "Empresa esperada",
                "Empresa encontrada",
                "Departamento esperado",
                "Departamento encontrado",
                "Responsable esperado",
                "Responsable encontrado",
                "Estado esperado",
                "Estado encontrado",
                "Ubicación esperada",
                "Ubicación encontrada",
                "Funcionamiento esperado",
                "Funcionamiento encontrado",
                "Encontrado",
                "Resultado",
                "Tipo de diferencia",
                "Observaciones",
                "Acción requerida",
                "Estado corrección",
                "Revisado por",
                "Fecha revisión",
                "Evidencia URL",
            ]
        )

        for detalle in detalles:
            escritor.writerow(
                [
                    auditoria.get("folio") or "",
                    auditoria.get("nombre") or "",
                    detalle.get("numero_inventario_snapshot") or "",
                    detalle.get("descripcion_snapshot") or "",
                    detalle.get("marca_snapshot") or "",
                    detalle.get("modelo_snapshot") or "",
                    detalle.get("numero_serie_snapshot") or "",
                    detalle.get("numero_serie_encontrado") or "",
                    detalle.get("empresa_esperada") or "",
                    detalle.get("empresa_encontrada") or "",
                    detalle.get("departamento_esperado") or "",
                    detalle.get("departamento_encontrado") or "",
                    detalle.get("responsable_esperado") or "",
                    detalle.get("responsable_encontrado") or "",
                    detalle.get("estado_esperado") or "",
                    detalle.get("estado_encontrado") or "",
                    detalle.get("ubicacion_esperada") or "",
                    detalle.get("ubicacion_encontrada") or "",
                    detalle.get("funcionamiento_esperado") or "",
                    detalle.get("funcionamiento_encontrado") or "",
                    "Sí" if detalle.get("encontrado") else "No",
                    detalle.get("resultado") or "",
                    detalle.get("tipo_diferencia") or "",
                    detalle.get("observaciones") or "",
                    detalle.get("accion_requerida") or "",
                    detalle.get("estado_correccion") or "",
                    detalle.get("revisado_por") or "",
                    fecha_a_texto(detalle.get("fecha_revision")),
                    detalle.get("evidencia_url") or "",
                ]
            )

        contenido = archivo_texto.getvalue().encode("utf-8-sig")
        archivo_binario = BytesIO(contenido)
        archivo_binario.seek(0)

        nombre_archivo = f"{auditoria['folio']}_auditoria.csv".replace("/", "-")

        return send_file(
            archivo_binario,
            mimetype="text/csv; charset=utf-8",
            as_attachment=True,
            download_name=nombre_archivo,
        )

    except Exception as error:
        print("Error al exportar auditoría:", error)
        return jsonify(
            {
                "error": "No se pudo exportar la auditoría.",
                "detalle": str(error),
            }
        ), 500

    finally:
        cursor.close()
        conexion.close()

