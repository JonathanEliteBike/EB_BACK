
"""Bitácora de actividad del Inventario IT.

Registra los movimientos y sus diferencias dentro de la misma
transacción de base de datos.
"""

import json
from datetime import datetime

from flask import g, request


# ============================================================
# CAMPOS PERMITIDOS EN LAS SOLICITUDES
# ============================================================

# Únicamente se registran los nombres de los campos enviados.
# No se almacena el contenido completo de las peticiones.

CAMPOS_PERMITIDOS = frozenset({
    'inventario',
    'numeroInventario',
    'equipoId',
    'colaboradorId',
    'nombre',
    'descripcion',
    'categoria',
    'marca',
    'modelo',
    'serie',
    'empresa',
    'departamento',
    'prefijo',
    'responsable',
    'cargo',
    'estado',
    'ubicacion',
    'funcionamiento',
    'extras',
    'estadoCorreccion',
    'resultado',
    'fechaProgramada',
    'auditorResponsable',
    'fechaDevolucion',
    'fechaAsignacion',
    'accionRequerida',
    'conclusiones',
    'motivo',
    'encontrado',
    'observaciones',
    'observacionesEntrega',
    'comentariosSistemas',
    'tipo',
    'fechaRegistro',
    'estatus',
})


# ============================================================
# CAMPOS HISTÓRICOS POR ENTIDAD
# ============================================================

# Solo se consultan los campos definidos en esta lista.
# No se registran contraseñas, tokens ni archivos adjuntos.
#
# Los nombres de tablas y columnas son constantes internas.
#
# IMPORTANTE:
# Se agregó "observaciones_entrega" a la entidad Asignación
# para identificar las correcciones realizadas después de
# entregar un equipo.

CAMPOS_HISTORICOS = {

    'Equipo': (
        'inventario_equipos',
        (
            'numero_inventario',
            'empresa',
            'departamento',
            'responsable',
            'cargo',
            'categoria',
            'descripcion',
            'marca',
            'modelo',
            'numero_serie',
            'funcionamiento',
            'estado',
            'ubicacion',
            'extras',
            'responsiva_estado',
        )
    ),

    'Colaborador': (
        'inventario_colaboradores',
        (
            'numero_empleado',
            'nombre',
            'apellido_paterno',
            'apellido_materno',
            'empresa',
            'departamento',
            'puesto',
            'prefijo',
            'ubicacion',
            'estado',
        )
    ),

    'Auditoría': (
        'inventario_auditorias',
        (
            'nombre',
            'fecha_programada',
            'auditor_responsable',
            'estado',
            'observaciones',
            'conclusiones',
        )
    ),

    'Detalle de auditoría': (
        'inventario_auditoria_detalles',
        (
            'encontrado',
            'resultado',
            'ubicacion_encontrada',
            'estado_encontrado',
            'accion_requerida',
            'estado_correccion',
            'observaciones',
        )
    ),

    'Corrección de auditoría': (
        'inventario_auditoria_detalles',
        (
            'encontrado',
            'resultado',
            'ubicacion_encontrada',
            'estado_encontrado',
            'accion_requerida',
            'estado_correccion',
            'observaciones',
        )
    ),

    'Responsiva': (
        'inventario_responsivas',
        (
            'estado',
            'observaciones',
            'fecha_firma',
            'fecha_anulacion',
            'motivo_anulacion',
        )
    ),

    'Asignación': (
        'inventario_asignaciones',
        (
            'equipo_id',
            'colaborador_id',
            'estado',
            'fecha_devolucion',

            # Correcciones de observaciones de entrega
            'observaciones_entrega',

            # Observaciones al devolver el equipo
            'observaciones_devolucion',
        )
    ),
}


# ============================================================
# NORMALIZACIÓN DE VALORES
# ============================================================

def normalizar_para_bitacora(valor):
    """
    Convierte valores de base de datos a formatos
    compatibles con JSON.

    Los textos se limitan a 300 caracteres.
    """

    if valor is None:
        return None

    if isinstance(valor, (int, float, bool)):
        return valor

    if isinstance(valor, datetime):
        return valor.isoformat(sep=' ')

    if hasattr(valor, 'isoformat'):
        return valor.isoformat()

    return str(valor)[:300]


# ============================================================
# CAPTURAR ESTADO DEL REGISTRO
# ============================================================

def capturar_estado(cursor, entidad, entidad_id):
    """
    Obtiene los campos autorizados de un registro.

    Se utiliza antes y después de modificar un registro
    para identificar exactamente qué valores cambiaron.
    """

    configuracion = CAMPOS_HISTORICOS.get(entidad)

    if not configuracion or entidad_id is None:
        return None

    tabla, columnas = configuracion

    consulta = (
        'SELECT '
        + ', '.join(columnas)
        + ' FROM '
        + tabla
        + ' WHERE id = %s'
    )

    cursor.execute(
        consulta,
        (int(entidad_id),)
    )

    fila = cursor.fetchone()

    if not fila:
        return None

    return {
        campo: normalizar_para_bitacora(
            fila.get(campo)
        )
        for campo in columnas
    }


# ============================================================
# COMPARAR VALORES ANTERIORES Y NUEVOS
# ============================================================

def construir_cambios(anterior, posterior):
    """
    Compara dos estados de un registro.

    Devuelve únicamente los campos que realmente
    cambiaron, incluyendo su valor anterior y nuevo.
    """

    if anterior is None and posterior is None:
        return []

    estado_anterior = anterior or {}
    estado_posterior = posterior or {}

    claves = list(
        dict.fromkeys(
            list(estado_anterior)
            + list(estado_posterior)
        )
    )

    cambios = []

    for campo in claves:

        valor_anterior = estado_anterior.get(campo)
        valor_nuevo = estado_posterior.get(campo)

        if valor_anterior == valor_nuevo:
            continue

        cambios.append({
            'campo': campo,
            'antes': valor_anterior,
            'despues': valor_nuevo,
        })

    return cambios


# ============================================================
# CONTROL PARA DETECTAR ESTADOS NO PROPORCIONADOS
# ============================================================

_SIN_ESTADO = object()


# ============================================================
# REGISTRAR EVENTO EN BITÁCORA
# ============================================================

def registrar_evento(
    cursor,
    accion,
    entidad,
    entidad_id=None,
    *,
    anterior=_SIN_ESTADO
):
    """
    Registra un movimiento dentro de la transacción actual.

    Funcionamiento:

    - Crear / asignar:
      Captura el estado nuevo.

    - Editar / devolver / anular:
      Compara el estado anterior con el actual.

    - Eliminar:
      Conserva el estado anterior si fue proporcionado.

    La transacción y su commit son responsabilidad
    del endpoint que llama esta función.
    """

    cuenta = getattr(
        g,
        'inventario_usuario',
        None
    )

    if not cuenta or not cuenta.get('id'):
        raise RuntimeError(
            'No se permite modificar inventario '
            'sin usuario autenticado'
        )

    datos = request.get_json(silent=True)

    if isinstance(datos, dict):
        campos = sorted(
            set(datos).intersection(
                CAMPOS_PERMITIDOS
            )
        )
    else:
        campos = []

    detalle = {
        'camposEnviados': campos,
        'cambios': [],
    }

    if (
        entidad in CAMPOS_HISTORICOS
        and entidad_id is not None
    ):

        if accion in ('crear', 'asignar'):

            estado_nuevo = capturar_estado(
                cursor,
                entidad,
                entidad_id
            )

            detalle['cambios'] = construir_cambios(
                None,
                estado_nuevo
            )

        elif anterior is not _SIN_ESTADO:

            estado_nuevo = capturar_estado(
                cursor,
                entidad,
                entidad_id
            )

            detalle['cambios'] = construir_cambios(
                anterior,
                estado_nuevo
            )

    # No se inventan diferencias de movimientos antiguos.

    cursor.execute(
        """
        INSERT INTO inventario_bitacora
        (
            usuario_id,
            usuario_nombre,
            accion,
            entidad,
            entidad_id,
            detalle_json
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            int(cuenta['id']),
            str(cuenta['nombre'])[:180],
            accion,
            entidad,
            (
                int(entidad_id)
                if entidad_id is not None
                else None
            ),
            json.dumps(
                detalle,
                ensure_ascii=False
            ),
        )
    )


# ============================================================
# LEER DETALLE DE UN EVENTO
# ============================================================

def leer_detalle_evento(valor):
    """
    Interpreta el JSON almacenado en la bitácora.

    Mantiene compatibilidad con movimientos anteriores
    que no contienen diferencias detalladas.
    """

    try:

        if isinstance(valor, (str, bytes)):
            info = json.loads(valor)
        else:
            info = valor

    except (ValueError, TypeError):
        info = None

    if not isinstance(info, dict):

        return {
            'cambios': [],
            'camposEnviados': [],
            'detalleDisponible': False,
        }

    cambios = info.get('cambios')

    campos = info.get(
        'camposEnviados',
        []
    )

    if not isinstance(cambios, list):
        cambios = []

    if not isinstance(campos, list):
        campos = []

    return {
        'cambios': cambios,
        'camposEnviados': campos,
        'detalleDisponible': bool(cambios),
    }


# ============================================================
# CALCULAR PARTICIPACIÓN POR USUARIO
# ============================================================

def calcular_participacion(conteos):
    """
    Calcula la participación de cada usuario
    en los movimientos registrados.

    - Solo incluye usuarios con actividad.
    - Agrupa por ID para evitar mezclar homónimos.
    - Ordena de mayor a menor participación.
    """

    por_id = {}

    for fila in conteos:

        cantidad = int(fila['total'])

        if cantidad <= 0:
            continue

        uid = int(
            fila['usuario_id']
        )

        if uid not in por_id:

            por_id[uid] = {
                'usuarioId': uid,
                'usuario': (
                    fila['usuario_nombre']
                    or f'Usuario {uid}'
                ),
                'cantidad': 0,
            }

        por_id[uid]['cantidad'] += cantidad

    total = sum(
        usuario['cantidad']
        for usuario in por_id.values()
    )

    resultados = sorted(
        por_id.values(),
        key=lambda usuario: (
            -usuario['cantidad'],
            usuario['usuario'].casefold(),
        )
    )

    for usuario in resultados:

        usuario['porcentaje'] = (
            round(
                100 * usuario['cantidad'] / total,
                2
            )
            if total
            else 0
        )

    return total, resultados
