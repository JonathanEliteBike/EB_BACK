"""Bitácora de actividad del Inventario IT; inserciones en la misma transacción."""
import json
from datetime import datetime
from flask import g, request

# Registrar nombres de campos, no datos personales ni adjuntos.
CAMPOS_PERMITIDOS = frozenset({
    'inventario','numeroInventario','equipoId','colaboradorId','nombre','descripcion',
    'categoria','marca','modelo','serie','empresa','departamento','responsable',
    'cargo','estado','ubicacion','funcionamiento','extras','estadoCorreccion',
    'resultado','fechaProgramada','auditorResponsable','fechaDevolucion',
    'fechaAsignacion','accionRequerida','conclusiones','motivo','encontrado',
    'observaciones','comentariosSistemas','tipo','fechaRegistro','estatus',
})


# Solo campos operativos permitidos. No se registran contraseñas, tokens,
# datos de contacto, archivos adjuntos ni el contenido completo de las peticiones.
# Los identificadores de tabla y columna son constantes internas, nunca SQL del usuario.
CAMPOS_HISTORICOS = {
    'Equipo': ('inventario_equipos', (
        'numero_inventario', 'empresa', 'departamento', 'responsable',
        'cargo', 'categoria', 'descripcion', 'marca', 'modelo',
        'numero_serie', 'funcionamiento', 'estado', 'ubicacion',
        'extras', 'responsiva_estado',
    )),
    'Colaborador': ('inventario_colaboradores', (
        'numero_empleado', 'nombre', 'apellido_paterno', 'apellido_materno',
        'empresa', 'departamento', 'puesto', 'ubicacion', 'estado',
    )),
    'Auditoría': ('inventario_auditorias', (
        'nombre', 'fecha_programada', 'auditor_responsable', 'estado',
        'observaciones', 'conclusiones',
    )),
    'Detalle de auditoría': ('inventario_auditoria_detalles', (
        'encontrado', 'resultado', 'ubicacion_encontrada', 'estado_encontrado',
        'accion_requerida', 'estado_correccion', 'observaciones',
    )),
    'Corrección de auditoría': ('inventario_auditoria_detalles', (
        'encontrado', 'resultado', 'ubicacion_encontrada', 'estado_encontrado',
        'accion_requerida', 'estado_correccion', 'observaciones',
    )),
    'Responsiva': ('inventario_responsivas', (
        'estado', 'observaciones', 'fecha_firma', 'fecha_anulacion',
        'motivo_anulacion',
    )),
    'Asignación': ('inventario_asignaciones', (
        'equipo_id', 'colaborador_id', 'estado', 'fecha_devolucion',
        'observaciones_devolucion',
    )),
}


def normalizar_para_bitacora(valor):
    """Serializar números/fechas sin registrar contenido arbitrario o enorme."""
    if valor is None or isinstance(valor, (int, float, bool)):
        return valor
    if hasattr(valor, 'isoformat'):
        return valor.isoformat(sep=' ') if isinstance(valor, datetime) else valor.isoformat()
    return str(valor)[:300]


def capturar_estado(cursor, entidad, entidad_id):
    """Consultar lista blanca de campos del registro en la misma conexión/tx."""
    configuracion = CAMPOS_HISTORICOS.get(entidad)
    if not configuracion or entidad_id is None:
        return None
    tabla, columnas = configuracion
    cursor.execute('SELECT ' + ', '.join(columnas) + ' FROM ' + tabla +
                   ' WHERE id = %s', (int(entidad_id),))
    fila = cursor.fetchone()
    return {k: normalizar_para_bitacora(fila.get(k)) for k in columnas} if fila else None


def construir_cambios(anterior, posterior):
    """Differences from actual database rows (not request payload)."""
    if anterior is None and posterior is None:
        return []
    claves = list(dict.fromkeys(list(anterior or {}) + list(posterior or {})))
    return [
        {'campo': campo, 'antes': (anterior or {}).get(campo),
         'despues': (posterior or {}).get(campo)}
        for campo in claves
        if (anterior or {}).get(campo) != (posterior or {}).get(campo)
    ]


_SIN_ESTADO = object()


def registrar_evento(cursor, accion, entidad, entidad_id=None, *, anterior=_SIN_ESTADO):
    """Escribir bitácora dentro de la operación: falla => rollback conjunto.

    Para modificaciones, `anterior` se captura antes del UPDATE y `posterior`
    se consulta después del UPDATE. Para altas se captura solo `posterior`,
    y para eliminaciones se captura `anterior` antes del DELETE.
    """
    cuenta = getattr(g, 'inventario_usuario', None)
    if not cuenta or not cuenta.get('id'):
        raise RuntimeError('No se permite modificar inventario sin usuario autenticado')
    datos = request.get_json(silent=True)
    campos = sorted(set(datos).intersection(CAMPOS_PERMITIDOS)) if isinstance(datos, dict) else []
    detalle = {'camposEnviados': campos, 'cambios': []}
    if entidad in CAMPOS_HISTORICOS and entidad_id is not None:
        if accion in ('crear', 'asignar'):
            detalle['cambios'] = construir_cambios(None, capturar_estado(cursor, entidad, entidad_id))
        elif anterior is not _SIN_ESTADO:
            detalle['cambios'] = construir_cambios(anterior, capturar_estado(cursor, entidad, entidad_id))
        # Eventos antiguos seguirán sin 'cambios'; no se simulan estados pasados.
    cursor.execute(
        """INSERT INTO inventario_bitacora
            (usuario_id, usuario_nombre, accion, entidad, entidad_id, detalle_json)
            VALUES (%s, %s, %s, %s, %s, %s)""",
        (int(cuenta['id']), str(cuenta['nombre'])[:180], accion, entidad,
         int(entidad_id) if entidad_id is not None else None,
         json.dumps(detalle, ensure_ascii=False)),
    )


def leer_detalle_evento(valor):
    """No ofrecer datos que no fueron capturados; tolera el JSON de Etapa 2."""
    try:
        info = json.loads(valor) if isinstance(valor, (str, bytes)) else valor
    except (ValueError, TypeError):
        info = None
    if not isinstance(info, dict):
        return {'cambios': [], 'camposEnviados': [], 'detalleDisponible': False}
    cambios = info.get('cambios')
    return {'cambios': cambios if isinstance(cambios, list) else [],
            'camposEnviados': info.get('camposEnviados', []) if isinstance(info.get('camposEnviados', []), list) else [],
            'detalleDisponible': isinstance(cambios, list) and bool(cambios)}


def calcular_participacion(conteos):
    """Mostrar exclusivamente autores con actividad en el filtro aplicado.

    Un usuario con asignaciones pero sin ediciones aparecerá en modo "todas",
    pero no en modo "ediciones". Nunca se agregan cuentas sin eventos.
    Se agrupa por ID estable, no por nombre, para evitar mezclar homónimos.
    """
    por_id = {}
    for fila in conteos:
        cantidad = int(fila['total'])
        if cantidad <= 0:
            continue
        uid = int(fila['usuario_id'])
        if uid not in por_id:
            por_id[uid] = {
                'usuarioId': uid,
                'usuario': fila['usuario_nombre'] or f'Usuario {uid}',
                'cantidad': 0,
            }
        por_id[uid]['cantidad'] += cantidad
    total = sum(u['cantidad'] for u in por_id.values())
    resultados = sorted(
        por_id.values(),
        key=lambda x: (-x['cantidad'], x['usuario'].casefold()),
    )
    for item in resultados:
        item['porcentaje'] = round(100 * item['cantidad'] / total, 2)
    return total, resultados
