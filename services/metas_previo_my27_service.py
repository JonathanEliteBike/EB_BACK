"""Metas MY27 para ``previo`` basadas en el simulador de retroactivos.

Reglas principales
------------------
- Cliente individual: siempre usa la regla de 1 sucursal según ``clientes.nivel``.
- Miembro de Integral: sigue siendo individual a 1 sucursal.
- Fila consolidada de Integral dinámica: usa el nivel común de sus miembros y
  el multiplicador por número de sucursales definido por el simulador.
- Integrales 1-4 y Christian Boccaletti: se consideran acuerdos especiales y
  sus metas no se modifican automáticamente.
- Filas sin nivel válido: se omiten; nunca se inventa una clasificación.

Este módulo NO hace commit. El caller controla la transacción.
Los avances y porcentajes se recalculan después con la lógica existente de
``routes.monitor_odoo._recalcular_acumulados_previo``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple

from utils.metas_my27 import CAMPOS_META_PREVIO, calcular_metas_my27, normalizar_nivel


INTEGRALES_PROTEGIDAS = {
    "INTEGRAL 1",
    "INTEGRAL 2",
    "INTEGRAL 3",
    "INTEGRAL 4",
}

CLAVES_PROTEGIDAS = {"JE537", "4E013"}
NOMBRES_PROTEGIDOS = {
    "CHRISTIAN BOCCALETTI",
    "CHRISTIAN BOCCALETTI.",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm(valor: Any) -> str:
    return " ".join(str(valor or "").strip().upper().split())


def _dec(valor: Any) -> Decimal:
    if valor in (None, ""):
        return Decimal("0")
    return Decimal(str(valor))


def _es_integral(fila: Dict[str, Any]) -> bool:
    clave = _norm(fila.get("clave"))
    return bool(fila.get("es_integral")) or clave.startswith("INTEGRAL ")


def es_fila_protegida(clave: Any, nombre_cliente: Any) -> bool:
    clave_norm = _norm(clave)
    nombre_norm = _norm(nombre_cliente)
    return (
        clave_norm in INTEGRALES_PROTEGIDAS
        or clave_norm in CLAVES_PROTEGIDAS
        or nombre_norm in NOMBRES_PROTEGIDOS
    )


def _diferencias_metas(
    fila: Dict[str, Any],
    nivel_objetivo: str,
    metas: Dict[str, Any],
) -> List[Tuple[str, Any, Any]]:
    diferencias: List[Tuple[str, Any, Any]] = []

    if _norm(fila.get("nivel")) != _norm(nivel_objetivo):
        diferencias.append(("nivel", fila.get("nivel"), nivel_objetivo))

    for campo in CAMPOS_META_PREVIO:
        actual = _dec(fila.get(campo))
        esperado = _dec(metas[campo])
        if actual != esperado:
            diferencias.append((campo, actual, esperado))

    return diferencias


def _actualizar_fila_previo(
    cursor,
    fila: Dict[str, Any],
    nivel_objetivo: str,
    metas: Dict[str, Any],
    aplicar: bool,
) -> Dict[str, Any]:
    diferencias = _diferencias_metas(fila, nivel_objetivo, metas)

    resultado = {
        "id": fila.get("id"),
        "clave": fila.get("clave"),
        "cliente": fila.get("nombre_cliente"),
        "nivel_anterior": fila.get("nivel"),
        "nivel_objetivo": nivel_objetivo,
        "diferencias": diferencias,
        "actualizada": False,
    }

    if not diferencias or not aplicar:
        return resultado

    set_metas = ", ".join(f"{campo} = %s" for campo in CAMPOS_META_PREVIO)
    sql = f"""
        UPDATE previo
        SET nivel = %s,
            {set_metas}
        WHERE id = %s
    """
    params = [
        nivel_objetivo,
        *[metas[campo] for campo in CAMPOS_META_PREVIO],
        fila["id"],
    ]
    cursor.execute(sql, tuple(params))

    if cursor.rowcount != 1:
        raise RuntimeError(
            f"Se esperaba actualizar 1 fila de previo id={fila['id']}; "
            f"rowcount={cursor.rowcount}"
        )

    resultado["actualizada"] = True
    return resultado


def _obtener_miembros_grupo(cursor, grupo_id: int) -> List[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT id, clave, nombre_cliente, nivel, id_grupo
        FROM clientes
        WHERE id_grupo = %s
        ORDER BY id
        """,
        (grupo_id,),
    )
    return cursor.fetchall()


def _objetivo_integral(cursor, fila: Dict[str, Any]):
    """Devuelve (tipo, nivel, sucursales, metas, motivo_omision)."""
    if es_fila_protegida(fila.get("clave"), fila.get("nombre_cliente")):
        return "PROTEGIDO", None, None, None, "Integral/acuerdo especial protegido"

    grupo_id = fila.get("grupo_integral")
    if grupo_id is None:
        return "OMITIDO", None, None, None, "Integral sin grupo_integral"

    miembros = _obtener_miembros_grupo(cursor, int(grupo_id))
    if not miembros:
        return "OMITIDO", None, None, None, f"Grupo {grupo_id} sin miembros"

    niveles = [m.get("nivel") for m in miembros if str(m.get("nivel") or "").strip()]
    if len(niveles) != len(miembros):
        return "OMITIDO", None, None, None, f"Grupo {grupo_id} con miembro(s) sin nivel"

    niveles_norm = {normalizar_nivel(n) for n in niveles}
    if len(niveles_norm) != 1:
        return (
            "OMITIDO",
            None,
            None,
            None,
            f"Grupo {grupo_id} con niveles mixtos: {sorted(niveles_norm)}",
        )

    numero_sucursales = len(miembros)
    try:
        metas = calcular_metas_my27(niveles[0], numero_sucursales)
    except ValueError as exc:
        return "OMITIDO", None, None, None, str(exc)

    return (
        "INTEGRAL_DINAMICA",
        metas["nivel"],
        numero_sucursales,
        metas,
        None,
    )


def _objetivo_individual(cursor, fila: Dict[str, Any]):
    """Meta individual: SIEMPRE 1 sucursal, aunque pertenezca a Integral."""
    if es_fila_protegida(fila.get("clave"), fila.get("nombre_cliente")):
        return "PROTEGIDO", None, None, None, "Cliente/acuerdo especial protegido"

    cursor.execute(
        """
        SELECT id, clave, nombre_cliente, nivel, id_grupo
        FROM clientes
        WHERE UPPER(TRIM(clave)) = UPPER(TRIM(%s))
        LIMIT 1
        """,
        (fila.get("clave"),),
    )
    cliente = cursor.fetchone()

    if not cliente:
        return "OMITIDO", None, None, None, "No existe cliente con la misma clave"

    nivel = cliente.get("nivel")
    if not str(nivel or "").strip():
        return "OMITIDO", None, None, None, "Cliente sin nivel"

    try:
        metas = calcular_metas_my27(nivel, 1)
    except ValueError as exc:
        return "OMITIDO", None, None, None, str(exc)

    tipo = "MIEMBRO_INTEGRAL" if cliente.get("id_grupo") is not None else "NORMAL"
    return tipo, metas["nivel"], 1, metas, None


def _objetivo_para_fila(cursor, fila: Dict[str, Any]):
    if _es_integral(fila):
        return _objetivo_integral(cursor, fila)
    return _objetivo_individual(cursor, fila)


# ---------------------------------------------------------------------------
# API de sincronización
# ---------------------------------------------------------------------------

def sincronizar_metas_previo_my27(conexion, aplicar: bool = False) -> Dict[str, Any]:
    """Audita o sincroniza todas las metas MY27 existentes en ``previo``.

    ``aplicar=False`` funciona como preview y no escribe nada.
    ``aplicar=True`` actualiza únicamente nivel + campos de metas.
    """
    cursor = conexion.cursor(dictionary=True)
    try:
        columnas = [
            "id",
            "clave",
            "nombre_cliente",
            "nivel",
            "es_integral",
            "grupo_integral",
            *CAMPOS_META_PREVIO,
        ]
        cursor.execute(
            f"""
            SELECT {", ".join(columnas)}
            FROM previo
            WHERE nombre_cliente IS NOT NULL
              AND nombre_cliente <> ''
            ORDER BY id
            """
        )
        filas = cursor.fetchall()

        resumen: Dict[str, Any] = {
            "filas_revisadas": len(filas),
            "evaluables": 0,
            "con_cambios": 0,
            "sin_cambios": 0,
            "actualizadas": 0,
            "protegidas": 0,
            "omitidas": 0,
            "por_tipo": {},
            "cambios": [],
            "omisiones": [],
        }

        for fila in filas:
            tipo, nivel_obj, sucursales, metas, motivo = _objetivo_para_fila(cursor, fila)
            resumen["por_tipo"][tipo] = resumen["por_tipo"].get(tipo, 0) + 1

            if tipo == "PROTEGIDO":
                resumen["protegidas"] += 1
                resumen["omisiones"].append(
                    {
                        "id": fila.get("id"),
                        "clave": fila.get("clave"),
                        "cliente": fila.get("nombre_cliente"),
                        "tipo": tipo,
                        "motivo": motivo,
                    }
                )
                continue

            if metas is None:
                resumen["omitidas"] += 1
                resumen["omisiones"].append(
                    {
                        "id": fila.get("id"),
                        "clave": fila.get("clave"),
                        "cliente": fila.get("nombre_cliente"),
                        "tipo": tipo,
                        "motivo": motivo,
                    }
                )
                continue

            resumen["evaluables"] += 1
            detalle = _actualizar_fila_previo(cursor, fila, nivel_obj, metas, aplicar)
            detalle["tipo"] = tipo
            detalle["sucursales"] = sucursales

            if detalle["diferencias"]:
                resumen["con_cambios"] += 1
                resumen["cambios"].append(detalle)
                if detalle["actualizada"]:
                    resumen["actualizadas"] += 1
            else:
                resumen["sin_cambios"] += 1

        return resumen
    finally:
        cursor.close()


def sincronizar_metas_cliente_my27(
    conexion,
    id_cliente: int,
    aplicar: bool = True,
) -> Dict[str, Any]:
    """Sincroniza únicamente la fila individual de un cliente.

    La meta individual siempre se calcula como 1 sucursal, incluso si el
    cliente pertenece a una Integral.
    """
    cursor = conexion.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT id, clave, nombre_cliente, nivel, id_grupo
            FROM clientes
            WHERE id = %s
            """,
            (id_cliente,),
        )
        cliente = cursor.fetchone()
        if not cliente:
            return {
                "status": "omitido",
                "motivo": "Cliente no encontrado",
                "id_cliente": id_cliente,
            }

        cursor.execute(
            f"""
            SELECT id, clave, nombre_cliente, nivel, es_integral, grupo_integral,
                   {", ".join(CAMPOS_META_PREVIO)}
            FROM previo
            WHERE UPPER(TRIM(clave)) = UPPER(TRIM(%s))
              AND COALESCE(es_integral, 0) = 0
            LIMIT 1
            """,
            (cliente.get("clave"),),
        )
        fila = cursor.fetchone()
        if not fila:
            return {
                "status": "omitido",
                "motivo": "No existe fila individual en previo",
                "id_cliente": id_cliente,
                "clave": cliente.get("clave"),
                "id_grupo": cliente.get("id_grupo"),
            }

        tipo, nivel_obj, sucursales, metas, motivo = _objetivo_individual(cursor, fila)
        if metas is None:
            return {
                "status": tipo.lower(),
                "motivo": motivo,
                "id_cliente": id_cliente,
                "clave": cliente.get("clave"),
                "id_grupo": cliente.get("id_grupo"),
            }

        detalle = _actualizar_fila_previo(cursor, fila, nivel_obj, metas, aplicar)
        return {
            "status": "success",
            "tipo": tipo,
            "id_cliente": id_cliente,
            "clave": cliente.get("clave"),
            "id_grupo": cliente.get("id_grupo"),
            "sucursales": sucursales,
            "actualizadas": 1 if detalle["actualizada"] else 0,
            "con_cambios": bool(detalle["diferencias"]),
            "detalle": detalle,
        }
    finally:
        cursor.close()


def sincronizar_metas_grupo_my27(
    conexion,
    id_grupo: int,
    aplicar: bool = True,
) -> Dict[str, Any]:
    """Sincroniza la fila consolidada de una Integral dinámica existente."""
    cursor = conexion.cursor(dictionary=True)
    try:
        cursor.execute(
            f"""
            SELECT id, clave, nombre_cliente, nivel, es_integral, grupo_integral,
                   {", ".join(CAMPOS_META_PREVIO)}
            FROM previo
            WHERE COALESCE(es_integral, 0) = 1
              AND (
                    grupo_integral = %s
                    OR UPPER(TRIM(clave)) = UPPER(TRIM(%s))
                  )
            LIMIT 1
            """,
            (id_grupo, f"Integral {id_grupo}"),
        )
        fila = cursor.fetchone()
        if not fila:
            return {
                "status": "omitido",
                "motivo": "No existe fila consolidada de la Integral en previo",
                "id_grupo": id_grupo,
            }

        tipo, nivel_obj, sucursales, metas, motivo = _objetivo_integral(cursor, fila)
        if metas is None:
            return {
                "status": tipo.lower(),
                "motivo": motivo,
                "id_grupo": id_grupo,
                "clave": fila.get("clave"),
            }

        detalle = _actualizar_fila_previo(cursor, fila, nivel_obj, metas, aplicar)
        return {
            "status": "success",
            "tipo": tipo,
            "id_grupo": id_grupo,
            "clave": fila.get("clave"),
            "sucursales": sucursales,
            "actualizadas": 1 if detalle["actualizada"] else 0,
            "con_cambios": bool(detalle["diferencias"]),
            "detalle": detalle,
        }
    finally:
        cursor.close()


def sincronizar_grupos_afectados_my27(
    conexion,
    grupos: Iterable[Optional[int]],
    aplicar: bool = True,
) -> List[Dict[str, Any]]:
    """Sincroniza una colección de grupos, ignorando ``None`` y duplicados."""
    ids = sorted({int(g) for g in grupos if g not in (None, 0, "0", "")})
    return [sincronizar_metas_grupo_my27(conexion, gid, aplicar=aplicar) for gid in ids]


def capturar_metas_protegidas(conexion) -> List[Dict[str, Any]]:
    """Captura nivel + metas de acuerdos especiales antes de reconstruir previo.

    Se usa por ``/actualizar_previo`` porque esa ruta borra y reinserta la tabla.
    De esta forma Angular nunca puede sobrescribir accidentalmente los acuerdos
    especiales con hardcodes o datos de caché.
    """
    cursor = conexion.cursor(dictionary=True)
    try:
        cursor.execute(
            f"""
            SELECT clave, nombre_cliente, nivel, {", ".join(CAMPOS_META_PREVIO)}
            FROM previo
            WHERE UPPER(TRIM(clave)) IN ({", ".join(["%s"] * (len(INTEGRALES_PROTEGIDAS) + len(CLAVES_PROTEGIDAS)))})
               OR UPPER(TRIM(nombre_cliente)) IN ({", ".join(["%s"] * len(NOMBRES_PROTEGIDOS))})
            """,
            tuple(sorted(INTEGRALES_PROTEGIDAS | CLAVES_PROTEGIDAS))
            + tuple(sorted(NOMBRES_PROTEGIDOS)),
        )
        return cursor.fetchall()
    finally:
        cursor.close()


def restaurar_metas_protegidas(conexion, snapshot: Iterable[Dict[str, Any]]) -> int:
    """Restaura nivel + metas de filas protegidas después de un reinserto."""
    cursor = conexion.cursor()
    restauradas = 0
    try:
        set_metas = ", ".join(f"{campo} = %s" for campo in CAMPOS_META_PREVIO)
        sql = f"""
            UPDATE previo
            SET nivel = %s,
                {set_metas}
            WHERE UPPER(TRIM(clave)) = UPPER(TRIM(%s))
        """

        for fila in snapshot:
            params = [
                fila.get("nivel"),
                *[fila.get(campo) for campo in CAMPOS_META_PREVIO],
                fila.get("clave"),
            ]
            cursor.execute(sql, tuple(params))
            restauradas += cursor.rowcount

        return restauradas
    finally:
        cursor.close()
