from __future__ import annotations
import secrets
from db_conexion import obtener_conexion

OTP_VALIDITY_SECONDS = 3600  # 1 hora

TIPOS_VALIDOS = ('super', 'eliminar', 'meses')


def _ensure_table():
    conn = obtener_conexion()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS otps (
            id INT PRIMARY KEY AUTO_INCREMENT,
            usuario_id INT NOT NULL DEFAULT 0,
            codigo VARCHAR(10) NOT NULL,
            expira_en DATETIME NOT NULL,
            usado TINYINT(1) NOT NULL DEFAULT 0,
            tipo VARCHAR(10) NOT NULL DEFAULT 'super',
            creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_codigo (codigo),
            INDEX idx_usuario (usuario_id),
            INDEX idx_activo (usado, expira_en)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    # Migraciones para tablas que ya existían
    for ddl in [
        "ALTER TABLE otps ADD COLUMN usuario_id INT NOT NULL DEFAULT 0 AFTER id",
        "ALTER TABLE otps ADD INDEX idx_usuario (usuario_id)",
        "ALTER TABLE otps ADD COLUMN tipo VARCHAR(10) NOT NULL DEFAULT 'super' AFTER usado",
    ]:
        try:
            cur.execute(ddl)
            conn.commit()
        except Exception:
            pass  # Columna/índice ya existe
    conn.commit()
    cur.close()
    conn.close()


def generar_otp(usuario_id: int, tipo: str = 'super') -> str:
    """Invalida tokens anteriores del mismo tipo para el usuario y genera uno nuevo de 6 dígitos."""
    if tipo not in TIPOS_VALIDOS:
        raise ValueError(f"tipo inválido: {tipo}")
    _ensure_table()
    codigo = f"{secrets.randbelow(1000000):06d}"
    conn = obtener_conexion()
    cur = conn.cursor()
    cur.execute(
        "UPDATE otps SET usado = 1 WHERE usuario_id = %s AND tipo = %s AND usado = 0",
        (usuario_id, tipo)
    )
    cur.execute(
        "INSERT INTO otps (usuario_id, codigo, expira_en, tipo) "
        "VALUES (%s, %s, DATE_ADD(NOW(), INTERVAL %s SECOND), %s)",
        (usuario_id, codigo, OTP_VALIDITY_SECONDS, tipo),
    )
    conn.commit()
    cur.close()
    conn.close()
    return codigo


def verificar_otp(codigo: str):
    """Verifica el código y lo marca como usado. Retorna el tipo ('super'/'eliminar'/'meses') o None si inválido."""
    _ensure_table()
    conn = obtener_conexion()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, tipo FROM otps "
        "WHERE codigo = %s AND usado = 0 AND expira_en > NOW() LIMIT 1",
        (codigo,),
    )
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE otps SET usado = 1 WHERE id = %s", (row[0],))
        conn.commit()
        cur.close()
        conn.close()
        return row[1]  # tipo
    cur.close()
    conn.close()
    return None


def otp_activo(usuario_id: int = None, tipo: str = None):
    """Devuelve el OTP vigente más reciente de un usuario/tipo, o None si no hay."""
    _ensure_table()
    conn = obtener_conexion()
    cur = conn.cursor()
    if usuario_id is not None and tipo is not None:
        cur.execute(
            "SELECT codigo, expira_en FROM otps "
            "WHERE usuario_id = %s AND tipo = %s AND usado = 0 AND expira_en > NOW() "
            "ORDER BY id DESC LIMIT 1",
            (usuario_id, tipo)
        )
    elif usuario_id is not None:
        cur.execute(
            "SELECT codigo, expira_en FROM otps "
            "WHERE usuario_id = %s AND usado = 0 AND expira_en > NOW() "
            "ORDER BY id DESC LIMIT 1",
            (usuario_id,)
        )
    else:
        cur.execute(
            "SELECT codigo, expira_en FROM otps "
            "WHERE usado = 0 AND expira_en > NOW() ORDER BY id DESC LIMIT 1"
        )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return {"codigo": row[0], "expira_en": str(row[1])}
    return None


def listar_tokens_usuarios() -> list:
    """
    Devuelve TODOS los usuarios con cuenta de login que no son admins.
    Cada usuario incluye un objeto 'tokens' con slots para 'super', 'eliminar' y 'meses'.
    """
    _ensure_table()
    conn = obtener_conexion()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT
            u.id,
            u.nombre,
            u.usuario,
            u.activo,
            c.clave,
            c.id_grupo,
            g.nombre_grupo
        FROM usuarios u
        LEFT JOIN clientes c ON u.cliente_id = c.id
        LEFT JOIN grupo_clientes g ON c.id_grupo = g.id
        WHERE u.rol_id IS NOT NULL AND u.rol_id != 1
        ORDER BY u.nombre
    """)
    usuarios = cur.fetchall()
    cur.close()

    # Obtener todos los OTPs activos de una sola query para eficiencia
    cur2 = conn.cursor(dictionary=True)
    ids = [u["id"] for u in usuarios]
    if not ids:
        conn.close()
        return []

    fmt = ",".join(["%s"] * len(ids))
    cur2.execute(f"""
        SELECT usuario_id, tipo, codigo, expira_en, creado_en
        FROM otps
        WHERE usuario_id IN ({fmt})
          AND usado = 0
          AND expira_en > NOW()
          AND id IN (
              SELECT MAX(o2.id) FROM otps o2
              WHERE o2.usado = 0 AND o2.expira_en > NOW()
              GROUP BY o2.usuario_id, o2.tipo
          )
    """, ids)
    otp_rows = cur2.fetchall()
    cur2.close()
    conn.close()

    # Indexar por (usuario_id, tipo)
    otp_map: dict = {}
    for o in otp_rows:
        key = (o["usuario_id"], o["tipo"])
        otp_map[key] = {
            "codigo": o["codigo"],
            "expira_en": str(o["expira_en"]) if o["expira_en"] else None,
            "creado_en": str(o["creado_en"]) if o["creado_en"] else None,
        }

    result = []
    for u in usuarios:
        uid = u["id"]
        result.append({
            "id": uid,
            "nombre": u["nombre"],
            "usuario": u["usuario"],
            "activo": bool(u["activo"]) if u["activo"] is not None else False,
            "clave": u["clave"] or None,
            "nombre_grupo": u["nombre_grupo"] or None,
            "tokens": {
                "super":    otp_map.get((uid, "super")),
                "eliminar": otp_map.get((uid, "eliminar")),
                "meses":    otp_map.get((uid, "meses")),
            },
        })
    return result


def obtener_usuarios_monitor() -> list:
    """
    Obtiene TODOS los usuarios visibles en Monitor de Pedidos (clientes con/sin usuario vinculado).
    """
    conn = obtener_conexion()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT
            c.id AS id_cliente,
            u.id AS id_usuario,
            COALESCE(u.nombre, c.nombre_cliente) AS nombre,
            u.usuario,
            u.rol_id,
            u.activo,
            c.clave,
            c.id_grupo,
            g.nombre_grupo
        FROM clientes c
        LEFT JOIN usuarios u ON u.cliente_id = c.id
        LEFT JOIN grupo_clientes g ON c.id_grupo = g.id

        UNION

        SELECT
            NULL AS id_cliente,
            u.id AS id_usuario,
            u.nombre,
            u.usuario,
            u.rol_id,
            u.activo,
            NULL AS clave,
            NULL AS id_grupo,
            NULL AS nombre_grupo
        FROM usuarios u
        WHERE u.cliente_id IS NULL

        ORDER BY nombre
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    result = []
    for r in rows:
        if r["id_usuario"] is not None:
            result.append({
                "id_usuario": r["id_usuario"],
                "nombre": r["nombre"],
                "usuario": r["usuario"],
                "activo": bool(r["activo"]) if r["activo"] is not None else False,
                "clave": r["clave"],
                "id_grupo": r["id_grupo"],
                "nombre_grupo": r["nombre_grupo"],
                "rol_id": r["rol_id"] or 0
            })
    return result


def listar_tokens_usuarios_monitor() -> list:
    """
    Devuelve TODOS los usuarios visibles en Monitor de Pedidos con sus tokens activos.
    Incluye usuarios de Odoo, huérfanos y vinculados a clientes.
    Cada usuario incluye un objeto 'tokens' con slots para 'super', 'eliminar' y 'meses'.
    """
    _ensure_table()
    usuarios_monitor = obtener_usuarios_monitor()
    if not usuarios_monitor:
        return []

    conn = obtener_conexion()
    cur = conn.cursor(dictionary=True)

    # Obtener todos los OTPs activos de una sola query para eficiencia
    ids = [u["id_usuario"] for u in usuarios_monitor if u["id_usuario"]]
    if not ids:
        conn.close()
        return []

    fmt = ",".join(["%s"] * len(ids))
    cur.execute(f"""
        SELECT usuario_id, tipo, codigo, expira_en, creado_en
        FROM otps
        WHERE usuario_id IN ({fmt})
          AND usado = 0
          AND expira_en > NOW()
          AND id IN (
              SELECT MAX(o2.id) FROM otps o2
              WHERE o2.usado = 0 AND o2.expira_en > NOW()
              GROUP BY o2.usuario_id, o2.tipo
          )
    """, ids)
    otp_rows = cur.fetchall()
    cur.close()
    conn.close()

    # Indexar por (usuario_id, tipo)
    otp_map: dict = {}
    for o in otp_rows:
        key = (o["usuario_id"], o["tipo"])
        otp_map[key] = {
            "codigo": o["codigo"],
            "expira_en": str(o["expira_en"]) if o["expira_en"] else None,
            "creado_en": str(o["creado_en"]) if o["creado_en"] else None,
        }

    result = []
    for u in usuarios_monitor:
        uid = u["id_usuario"]
        result.append({
            "id": uid,
            "nombre": u["nombre"],
            "usuario": u["usuario"],
            "activo": u["activo"],
            "clave": u["clave"],
            "nombre_grupo": u["nombre_grupo"],
            "tokens": {
                "super":    otp_map.get((uid, "super")),
                "eliminar": otp_map.get((uid, "eliminar")),
                "meses":    otp_map.get((uid, "meses")),
            },
        })
    return result


def sincronizar_usuarios_desde_monitor() -> dict:
    """Pre-genera OTP super para TODOS los usuarios visibles en Monitor de Pedidos."""
    _ensure_table()
    usuarios_monitor = obtener_usuarios_monitor()
    sincronizados = 0
    errores = 0
    detalles = []

    for u in usuarios_monitor:
        try:
            usuario_id = u["id_usuario"]
            codigo = generar_otp(usuario_id, tipo='super')
            sincronizados += 1
            detalles.append({
                "id_usuario": usuario_id,
                "nombre": u["nombre"],
                "estado": "sincronizado",
                "codigo": codigo
            })
        except Exception as e:
            errores += 1
            detalles.append({
                "id_usuario": u["id_usuario"],
                "nombre": u["nombre"],
                "estado": "error",
                "error": str(e)
            })

    return {
        "sincronizados": sincronizados,
        "errores": errores,
        "total_monitor": len(usuarios_monitor),
        "detalles": detalles
    }
