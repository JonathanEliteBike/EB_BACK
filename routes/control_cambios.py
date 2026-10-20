"""Revisión administrativa de inventario_bitacora, sin duplicar movimientos.

La bitácora original no se edita. Estado por evento y cada dictamen
se almacenan aparte. Todos los endpoints usan la autorización de Inventario IT.
"""
from datetime import date, datetime, timedelta
from io import BytesIO

from flask import Blueprint, current_app, g, jsonify, request, send_file
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from db_conexion import obtener_conexion
from utils.inventario_actividad import leer_detalle_evento
from utils.inventario_auth import validar_acceso_inventario

control_cambios_bp = Blueprint('control_cambios', __name__,
                             url_prefix='/api/inventario/control-cambios')
control_cambios_bp.before_request(validar_acceso_inventario)

VALIDOS = frozenset(('Pendiente', 'Validado', 'Observado'))


def filtros_periodo(*, ignorar_estado=False):
    hoy = date.today()
    desde_txt = (request.args.get('desde') or hoy.replace(day=1).isoformat()).strip()
    hasta_txt = (request.args.get('hasta') or hoy.isoformat()).strip()
    try:
        desde = datetime.strptime(desde_txt, '%Y-%m-%d').date()
        hasta = datetime.strptime(hasta_txt, '%Y-%m-%d').date()
    except ValueError as exc:
        raise ValueError('Las fechas deben usar AAAA-MM-DD.') from exc
    if desde > hasta or (hasta - desde).days > 366:
        raise ValueError('Selecciona un intervalo válido de hasta 366 días.')
    estado = (request.args.get('estado') or 'Todos').strip()
    if estado not in (*VALIDOS, 'Todos'):
        raise ValueError('Estado de revisión no reconocido.')
    usuario = (request.args.get('usuario') or '').strip()
    if len(usuario) > 120:
        raise ValueError('Filtro de usuario demasiado largo.')
    tipo = (request.args.get('tipo') or 'Todos').strip()
    if tipo not in ('Todos', 'Altas', 'Ediciones', 'Asignaciones', 'Devoluciones', 'Responsivas', 'Verificaciones', 'Otros'):
        raise ValueError('Tipo de operación no reconocido.')
    buscar = (request.args.get('buscar') or '').strip()
    if len(buscar) > 120:
        raise ValueError('La búsqueda es demasiado larga.')
    orden = (request.args.get('orden') or 'recientes').strip()
    if orden not in ('recientes', 'antiguos'):
        raise ValueError('Orden no reconocido.')
    condiciones = ['b.fecha >= %s', 'b.fecha < %s']
    params = [desde, hasta + timedelta(days=1)]
    if estado != 'Todos' and not ignorar_estado:
        condiciones.append("COALESCE(r.estado, 'Pendiente') = %s")
        params.append(estado)
    if usuario:
        condiciones.append('b.usuario_nombre LIKE %s')
        params.append('%' + usuario.replace('%', '\\%').replace('_', '\\_') + '%')
    # La clasificación utiliza solamente acciones que realmente existen en la bitácora.
    tipos = {
        'Altas': "b.accion='crear' AND b.entidad IN ('Equipo','Colaborador')",
        'Ediciones': "b.accion='editar'",
        'Asignaciones': "b.accion='asignar'",
        'Devoluciones': "b.accion='devolver'",
        'Responsivas': "b.entidad='Responsiva'",
        'Verificaciones': "b.entidad IN ('Auditoría','Detalle de auditoría','Corrección de auditoría')",
        'Otros': "b.accion NOT IN ('crear','editar','asignar','devolver') AND b.entidad NOT IN ('Responsiva','Auditoría','Detalle de auditoría','Corrección de auditoría')",
    }
    if tipo != 'Todos':
        condiciones.append('(' + tipos[tipo] + ')')
    if buscar:
        # LIKE está parametrizado; las series/folios se buscan solo como valores actuales.
        patron = '%' + buscar.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        condiciones.append("(CAST(b.id AS CHAR)=%s OR CAST(b.entidad_id AS CHAR)=%s "
                           "OR b.entidad LIKE %s OR b.usuario_nombre LIKE %s "
                           "OR EXISTS (SELECT 1 FROM inventario_equipos eq "
                           "WHERE (eq.id=b.entidad_id AND b.entidad='Equipo' "
                           "OR eq.id=(SELECT aa.equipo_id FROM inventario_asignaciones aa WHERE aa.id=b.entidad_id) AND b.entidad='Asignación' "
                           "OR eq.id=(SELECT rr.equipo_id FROM inventario_responsivas rr WHERE rr.id=b.entidad_id) AND b.entidad='Responsiva') "
                           "AND (eq.numero_inventario LIKE %s OR eq.numero_serie LIKE %s)))")
        params.extend([buscar, buscar, patron, patron, patron, patron])
    return desde, hasta, estado, usuario, ' AND '.join(condiciones), params


def conectar():
    conn = obtener_conexion()
    if conn is None:
        raise ConnectionError('No se pudo conectar a MySQL.')
    return conn


def resultado_resumen(cursor, where, params):
    cursor.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN COALESCE(r.estado,'Pendiente')='Pendiente' THEN 1 ELSE 0 END) AS pendientes, "
        "SUM(CASE WHEN r.estado='Validado' THEN 1 ELSE 0 END) AS validados, "
        "SUM(CASE WHEN r.estado='Observado' THEN 1 ELSE 0 END) AS observados "
        "FROM inventario_bitacora b "
        "LEFT JOIN inventario_revision_cambios r ON r.bitacora_id=b.id WHERE " + where,
        tuple(params),
    )
    row = cursor.fetchone() or {}
    return {k: int(row.get(k) or 0) for k in ('total', 'pendientes', 'validados', 'observados')}


def consultar_eventos(cursor, where, params, *, limite=30, offset=0, orden='recientes'):
    cursor.execute(
        "SELECT b.id, b.usuario_id, b.usuario_nombre, b.accion, b.entidad, b.entidad_id, "
        "b.detalle_json, DATE_FORMAT(b.fecha, '%Y-%m-%d %H:%i:%S') AS fecha, "
        "COALESCE(r.estado,'Pendiente') AS estado_revision, r.observacion, "
        "r.revisor_id, r.revisor_nombre, "
        "DATE_FORMAT(r.revisado_en, '%Y-%m-%d %H:%i:%S') AS revisado_en "
        "FROM inventario_bitacora b "
        "LEFT JOIN inventario_revision_cambios r ON r.bitacora_id=b.id WHERE " + where +
        (' ORDER BY b.fecha ASC, b.id ASC LIMIT %s OFFSET %s' if orden == 'antiguos' else ' ORDER BY b.fecha DESC, b.id DESC LIMIT %s OFFSET %s'),
        tuple(params + [limite, offset]),
    )
    return [evento_formato(fila) for fila in cursor.fetchall()]


def evento_formato(fila):
    detalle = leer_detalle_evento(fila.get('detalle_json'))
    return {
        'id': fila['id'], 'usuarioId': fila['usuario_id'],
        'usuario': fila['usuario_nombre'], 'accion': fila['accion'],
        'entidad': fila['entidad'], 'entidadId': fila['entidad_id'],
        'fecha': fila['fecha'], 'estadoRevision': fila['estado_revision'],
        'observacion': fila.get('observacion') or '',
        'revisorId': fila.get('revisor_id'),
        'revisorNombre': fila.get('revisor_nombre') or '',
        'revisadoEn': fila.get('revisado_en') or '',
        'cambios': detalle['cambios'],
        'detalleDisponible': detalle['detalleDisponible'],
        'camposEnviados': detalle['camposEnviados'],
    }


@control_cambios_bp.get('')
@control_cambios_bp.get('/')
def listado():
    try:
        desde, hasta, estado, usuario, where, params = filtros_periodo()
        limite = int(request.args.get('limite', '30'))
        offset = int(request.args.get('offset', '0'))
        if limite < 1 or limite > 100 or offset < 0 or offset > 100000:
            return jsonify(error='Paginación no válida.'), 400
    except (ValueError, TypeError) as exc:
        return jsonify(error=str(exc)), 400
    conn = None
    cursor = None
    try:
        conn = conectar()
        cursor = conn.cursor(dictionary=True)
        resumen_filtro = resultado_resumen(cursor, where, params)
        _, _, _, _, where_total, params_total = filtros_periodo(ignorar_estado=True)
        resumen = resultado_resumen(cursor, where_total, params_total)
        eventos = consultar_eventos(cursor, where, params, limite=limite, offset=offset, orden=request.args.get('orden', 'recientes'))
        return jsonify(desde=desde.isoformat(), hasta=hasta.isoformat(), estado=estado,
                       usuario=usuario, resumen=resumen, totalFiltrado=resumen_filtro['total'], eventos=eventos,
                       limite=limite, offset=offset, tipo=request.args.get('tipo', 'Todos'),
                       buscar=request.args.get('buscar', ''), orden=request.args.get('orden','recientes'))
    except Exception:
        current_app.logger.exception('Inventario IT: error al consultar control mensual')
        return jsonify(error='No fue posible consultar el control mensual. Revisa el registro del backend.'), 503
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


@control_cambios_bp.get('/resumen')
def resumen():
    try:
        desde, hasta, _, _, where, params = filtros_periodo()
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    conn = None
    cursor = None
    try:
        conn = conectar()
        cursor = conn.cursor(dictionary=True)
        return jsonify(desde=desde.isoformat(), hasta=hasta.isoformat(),
                       resumen=resultado_resumen(cursor, where, params))
    except Exception:
        current_app.logger.exception('Inventario IT: error al cargar resumen mensual')
        return jsonify(error='No fue posible cargar el resumen mensual.'), 503
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


@control_cambios_bp.put('/<int:evento_id>/revision')
def revisar(evento_id):
    datos = request.get_json(silent=True) or {}
    estado = datos.get('estado')
    observacion = (datos.get('observacion') or '').strip()
    if estado not in VALIDOS:
        return jsonify(error='Estado inválido.'), 400
    if len(observacion) > 2000:
        return jsonify(error='La observación no puede exceder 2000 caracteres.'), 400
    if estado == 'Observado' and len(observacion) < 5:
        return jsonify(error='Describe la observación (mínimo 5 caracteres).'), 400
    conn = None
    cursor = None
    try:
        conn = conectar()
        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT id, usuario_id FROM inventario_bitacora WHERE id=%s FOR UPDATE',
                       (evento_id,))
        evento = cursor.fetchone()
        if not evento:
            return jsonify(error='Movimiento no encontrado.'), 404
        cuenta = g.inventario_usuario
        if estado == 'Validado' and int(evento['usuario_id']) == int(cuenta['id']):
            return jsonify(error='No puedes validar de forma independiente un cambio realizado por ti.'), 403
        cursor.execute('SELECT estado, observacion FROM inventario_revision_cambios WHERE bitacora_id=%s FOR UPDATE',
                       (evento_id,))
        actual = cursor.fetchone()
        anterior = actual['estado'] if actual else 'Pendiente'
        nota_anterior = actual.get('observacion') if actual else ''
        if anterior == estado and (nota_anterior or '') == observacion:
            conn.rollback()
            return jsonify(ok=True, sinCambios=True, estado=estado)
        if actual:
            cursor.execute(
                'UPDATE inventario_revision_cambios SET estado=%s, observacion=%s, revisor_id=%s, '
                'revisor_nombre=%s, revisado_en=NOW(6) WHERE bitacora_id=%s',
                (estado, observacion or None, cuenta['id'], cuenta['nombre'], evento_id),
            )
        else:
            cursor.execute(
                'INSERT INTO inventario_revision_cambios '
                '(bitacora_id, estado, observacion, revisor_id, revisor_nombre, revisado_en) '
                'VALUES (%s,%s,%s,%s,%s,NOW(6))',
                (evento_id, estado, observacion or None, cuenta['id'], cuenta['nombre']),
            )
        cursor.execute(
            'INSERT INTO inventario_revision_cambios_historial '
            '(bitacora_id,estado_anterior,estado_nuevo,observacion_anterior,observacion_nueva,revisor_id,revisor_nombre) '
            'VALUES (%s,%s,%s,%s,%s,%s,%s)',
            (evento_id, anterior, estado, nota_anterior or None, observacion or None,
             cuenta['id'], cuenta['nombre']),
        )
        conn.commit()
        return jsonify(ok=True, id=evento_id, estado=estado, revisor=cuenta['nombre'])
    except Exception:
        if conn: conn.rollback()
        return jsonify(error='No se pudo guardar la revisión. Verifica la migración SQL.'), 503
    finally:
        if cursor: cursor.close()
        if conn: conn.close()



def _fecha(valor):
    if isinstance(valor, (date, datetime)):
        return valor.isoformat(sep=' ') if isinstance(valor, datetime) else valor.isoformat()
    return str(valor) if valor is not None else ''


def _texto(valor):
    return str(valor or '').strip()


def _nombre_colaborador(fila):
    return ' '.join(_texto(fila.get(k)) for k in ('nombre', 'apellido_paterno', 'apellido_materno')).strip()


def armar_expediente(cursor, evento):
    """Consulta un expediente de SOLO LECTURA. No deduce datos históricos faltantes.

    El id de evento resuelve su equipo mediante FK verificadas. El estado de
    equipos/asignaciones/responsivas es ACTUAL, no el que existía cuando se editó.
    """
    entidad, entidad_id = evento['entidad'], evento.get('entidad_id')
    if entidad_id is None:
        return {'disponible': False, 'motivo': 'El evento no está vinculado a un registro concreto.',
                'equipo': None, 'creacion': None, 'asignacion': None, 'responsiva': None,
                'comprobaciones': [], 'notaHistorica': 'La comparación histórica está en la bitácora, no en la ficha actual.'}

    equipo_id = None
    asignacion_id = None
    responsiva_id = None
    if entidad == 'Equipo':
        equipo_id = entidad_id
    elif entidad == 'Asignación':
        asignacion_id = entidad_id
        cursor.execute('SELECT equipo_id FROM inventario_asignaciones WHERE id=%s', (entidad_id,))
        fila = cursor.fetchone()
        equipo_id = fila['equipo_id'] if fila else None
    elif entidad in ('Detalle de auditoría', 'Corrección de auditoría'):
        cursor.execute('SELECT equipo_id FROM inventario_auditoria_detalles WHERE id=%s', (entidad_id,))
        fila = cursor.fetchone()
        equipo_id = fila['equipo_id'] if fila else None
    elif entidad == 'Responsiva':
        responsiva_id = entidad_id
        cursor.execute('SELECT equipo_id, asignacion_id FROM inventario_responsivas WHERE id=%s', (entidad_id,))
        fila = cursor.fetchone()
        if fila:
            equipo_id, asignacion_id = fila['equipo_id'], fila['asignacion_id']
    else:
        return {'disponible': False, 'motivo': 'Este movimiento corresponde a otro tipo de registro, no a un equipo vinculado.',
                'equipo': None, 'creacion': None, 'asignacion': None, 'responsiva': None,
                'comprobaciones': [], 'notaHistorica': 'Consulta el registro original para verificar sus datos.'}

    if equipo_id is None:
        return {'disponible': False, 'motivo': 'El registro original ya no existe o no tiene un equipo asociado.',
                'equipo': None, 'creacion': None, 'asignacion': None, 'responsiva': None,
                'comprobaciones': [], 'notaHistorica': 'No se reconstruyen datos eliminados a partir del estado actual.'}

    cursor.execute('''SELECT id, numero_inventario, fecha_registro, empresa, departamento, responsable,
        cargo, categoria, descripcion, marca, modelo, numero_serie, funcionamiento,
        estado, ubicacion, imagen_url, comentarios_sistemas, extras, responsiva_estado
        FROM inventario_equipos WHERE id=%s''', (equipo_id,))
    equipo_row = cursor.fetchone()
    if not equipo_row:
        return {'disponible': False, 'motivo': 'El equipo ya no se encuentra en el inventario actual.',
                'equipo': None, 'creacion': None, 'asignacion': None, 'responsiva': None,
                'comprobaciones': [], 'notaHistorica': 'La bitácora conserva solamente los datos históricos registrados.'}

    equipo = {k: _fecha(v) if isinstance(v, (date, datetime)) else v
              for k, v in equipo_row.items()}
    cursor.execute("SELECT usuario_nombre, DATE_FORMAT(fecha, '%Y-%m-%d %H:%i:%S') AS fecha "
                   "FROM inventario_bitacora WHERE entidad='Equipo' AND entidad_id=%s "
                   "AND accion='crear' ORDER BY fecha ASC, id ASC LIMIT 1", (equipo_id,))
    creacion = cursor.fetchone()

    if asignacion_id is not None:
        cursor.execute('SELECT id, equipo_id, colaborador_id, estado, fecha_asignacion, fecha_devolucion, '
                       'observaciones_entrega, observaciones_devolucion FROM inventario_asignaciones '
                       'WHERE id=%s AND equipo_id=%s', (asignacion_id, equipo_id))
    else:
        cursor.execute("SELECT id, equipo_id, colaborador_id, estado, fecha_asignacion, fecha_devolucion, "
                       "observaciones_entrega, observaciones_devolucion FROM inventario_asignaciones "
                       "WHERE equipo_id=%s ORDER BY (estado='Activa') DESC, fecha_asignacion DESC, id DESC LIMIT 1", (equipo_id,))
    asignacion_row = cursor.fetchone()
    asignacion = None
    if asignacion_row:
        cursor.execute('SELECT numero_empleado, nombre, apellido_paterno, apellido_materno, empresa, departamento, puesto '
                       'FROM inventario_colaboradores WHERE id=%s', (asignacion_row['colaborador_id'],))
        colaborador = cursor.fetchone()
        asignacion = {k: _fecha(v) if isinstance(v, (date, datetime)) else v
                      for k, v in asignacion_row.items()}
        asignacion['colaborador'] = _nombre_colaborador(colaborador) if colaborador else ''
        asignacion['numeroEmpleado'] = _texto(colaborador.get('numero_empleado')) if colaborador else ''
        asignacion['origen'] = 'Movimiento seleccionado' if asignacion_id is not None else 'Asignación más reciente (estado actual)'

    if responsiva_id is not None:
        cursor.execute('SELECT id, asignacion_id, equipo_id, colaborador_id, folio, responsable, estado, '
                       'fecha_generacion, fecha_firma, archivo_pdf FROM inventario_responsivas WHERE id=%s AND equipo_id=%s',
                       (responsiva_id, equipo_id))
    elif asignacion:
        cursor.execute('SELECT id, asignacion_id, equipo_id, colaborador_id, folio, responsable, estado, '
                       'fecha_generacion, fecha_firma, archivo_pdf FROM inventario_responsivas '
                       'WHERE asignacion_id=%s AND equipo_id=%s ORDER BY id DESC LIMIT 1',
                       (asignacion['id'], equipo_id))
    else:
        # Sin asignación no se atribuye al dispositivo una responsiva de otra entrega.
        # No consultamos una responsiva histórica como si fuese la actual.
        resp = None
    if responsiva_id is not None or asignacion:
        resp = cursor.fetchone()
    responsiva = ({k: _fecha(v) if isinstance(v, (date, datetime)) else v
                  for k, v in resp.items()} if resp else None)

    checks = []
    def check(clave, nombre, estado, mensaje):
        checks.append({'clave': clave, 'nombre': nombre, 'estado': estado, 'mensaje': mensaje})

    if not _texto(equipo.get('numero_serie')) or _texto(equipo.get('numero_serie')) in ('-', 'N/A'):
        check('serie', 'Número de serie del equipo', 'advertencia', 'El inventario actual no contiene una serie útil.')
    else:
        check('serie', 'Número de serie del equipo', 'ok', 'Existe en el registro del inventario. Confirma que coincida con el PDF firmado.')

    if asignacion:
        if not responsiva:
            check('responsiva', 'Responsiva de esta asignación', 'advertencia', 'No se encontró una responsiva vinculada a esta asignación.')
        elif responsiva.get('estado') == 'Anulada':
            check('responsiva', 'Responsiva de esta asignación', 'advertencia', 'La responsiva vinculada está anulada.')
        else:
            check('responsiva', 'Responsiva vinculada', 'ok', 'Existe un registro de responsiva para esta asignación.')
        if responsiva:
            if responsiva.get('colaborador_id') != asignacion.get('colaborador_id'):
                check('responsable', 'Responsable en asignación y responsiva', 'advertencia', 'El colaborador asociado no coincide entre ambos registros.')
            else:
                check('responsable', 'Responsable en asignación y responsiva', 'ok', 'Coincide el identificador del colaborador en ambos registros.')
    else:
        check('asignacion', 'Asignación', 'no_aplica', 'El equipo no tiene asignaciones registradas; una responsiva podría no ser necesaria.')

    if responsiva:
        if responsiva.get('estado') == 'Firmada' and _texto(responsiva.get('archivo_pdf')):
            check('firma', 'Documento firmado', 'pendiente', 'Está marcada como Firmada y existe una referencia al PDF. Auditoría debe abrirlo y comprobar firma, serie y responsable.')
        elif responsiva.get('estado') == 'Firmada':
            check('firma', 'Evidencia de firma', 'advertencia', 'Figura como Firmada, pero no tiene archivo o enlace registrado.')
        elif responsiva.get('estado') == 'Anulada':
            check('firma', 'Evidencia de firma', 'advertencia', 'La responsiva se encuentra anulada.')
        else:
            check('firma', 'Evidencia de firma', 'pendiente', 'La firma aún no está registrada como confirmada.')
        check('serie_documento', 'Serie dentro del PDF', 'pendiente', 'No se ha leído ni comprobado el contenido del PDF automáticamente.')

    return {'disponible': True, 'motivo': '', 'equipo': equipo,
            'creacion': ({'usuario': creacion['usuario_nombre'], 'fecha': creacion['fecha']} if creacion else None),
            'asignacion': asignacion, 'responsiva': responsiva, 'comprobaciones': checks,
            'notaHistorica': 'Ficha, asignación y responsiva consultadas en su estado actual. El antes/después de la operación proviene exclusivamente de la bitácora; no se reconstruye un estado histórico completo.'}


@control_cambios_bp.get('/<int:evento_id>/expediente')
def expediente(evento_id):
    conn = None
    cursor = None
    try:
        conn = conectar()
        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT id, entidad, entidad_id, usuario_nombre, fecha FROM inventario_bitacora WHERE id=%s', (evento_id,))
        evento = cursor.fetchone()
        if not evento:
            return jsonify(error='Movimiento no encontrado.'), 404
        return jsonify(armar_expediente(cursor, evento))
    except Exception:
        current_app.logger.exception('Inventario IT: error al consultar expediente del movimiento %s', evento_id)
        return jsonify(error='No fue posible consultar la ficha del movimiento.'), 503
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


@control_cambios_bp.get('/<int:evento_id>/historial')
def historial_revisiones(evento_id):
    conn = None
    cursor = None
    try:
        conn = conectar()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT estado_anterior AS anterior, estado_nuevo AS nuevo, "
            "observacion_anterior AS observacionAnterior, observacion_nueva AS observacionNueva, "
            "revisor_nombre AS revisor, DATE_FORMAT(fecha, '%Y-%m-%d %H:%i:%S') AS fecha "
            'FROM inventario_revision_cambios_historial WHERE bitacora_id=%s ORDER BY id DESC',
            (evento_id,),
        )
        return jsonify(cursor.fetchall())
    except Exception:
        return jsonify(error='No fue posible cargar los dictámenes anteriores.'), 503
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


@control_cambios_bp.get('/exportar')
def exportar():
    try:
        desde, hasta, _, _, where, params = filtros_periodo()
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    conn = None
    cursor = None
    try:
        conn = conectar()
        cursor = conn.cursor(dictionary=True)
        total = resultado_resumen(cursor, where, params)['total']
        if total > 20000:
            return jsonify(error='El reporte supera 20 000 registros; reduce el periodo.'), 400
        registros = consultar_eventos(cursor, where, params, limite=max(total, 1), orden=request.args.get('orden','recientes')) if total <= 100 else []
        if total > 100:
            # Exportación por lotes; no restringida al tamaño de página de la API.
            registros = []
            for offset in range(0, total, 100):
                registros.extend(consultar_eventos(cursor, where, params,
                                                  limite=min(100, total-offset), offset=offset, orden=request.args.get('orden','recientes')))
        libro = Workbook()
        hoja = libro.active
        hoja.title = 'Control de cambios'
        encabezados = ['ID evento', 'Fecha y hora', 'Usuario', 'Acción', 'Entidad',
                       'ID entidad', 'Estado revisión', 'Observación', 'Validado por',
                       'Fecha revisión', 'Diferencias (antes → después)']
        hoja.append(encabezados)
        for celda in hoja[1]:
            celda.font = Font(bold=True, color='FFFFFF')
            celda.fill = PatternFill('solid', fgColor='343633')
        def valor_excel(v):
            # Excel puede interpretar como fórmula valores introducidos por terceros.
            if isinstance(v, str) and v.startswith(('=', '+', '-', '@')):
                return "'" + v
            return v

        for ev in registros:
            cambios = '; '.join(f"{c.get('campo')}: {c.get('antes')} → {c.get('despues')}"
                               for c in ev['cambios'])
            fila = [ev['id'], ev['fecha'], ev['usuario'], ev['accion'], ev['entidad'],
                    ev['entidadId'], ev['estadoRevision'], ev['observacion'],
                    ev['revisorNombre'], ev['revisadoEn'], cambios]
            hoja.append([valor_excel(v) for v in fila])
        hoja.freeze_panes = 'A2'
        hoja.auto_filter.ref = hoja.dimensions
        for col, ancho in {'A': 13, 'B': 22, 'C': 27, 'D': 18, 'E': 27,
                           'F': 15, 'G': 19, 'H': 48, 'I': 25, 'J': 22, 'K': 65}.items():
            hoja.column_dimensions[col].width = ancho
        for fila in hoja.iter_rows(min_row=2):
            for celda in fila:
                celda.alignment = Alignment(wrap_text=True, vertical='top')
                if celda.data_type == 'f':
                    celda.data_type = 's'
        salida = BytesIO()
        libro.save(salida)
        salida.seek(0)
        return send_file(salida, as_attachment=True,
                         download_name=f'control_cambios_{desde}_{hasta}.xlsx',
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception:
        return jsonify(error='No fue posible generar el reporte Excel.'), 503
    finally:
        if cursor: cursor.close()
        if conn: conn.close()
