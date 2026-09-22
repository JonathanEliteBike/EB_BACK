# services/usuarios_hijos_service.py

from db_conexion import obtener_conexion
from utils.seguridad import hash_password
import re


class ErrorSecuenciaUsuario(ValueError):
    """Error de negocio para una secuencia de usernames no generable."""
    codigo_http = 409


class UsuariosHijosService:

    @staticmethod
    def _valor_alfabetico(sufijo):
        """Convierte A..Z, AA..ZZ a su valor ordinal tipo columnas Excel."""
        valor = 0
        for letra in sufijo.upper():
            valor = valor * 26 + (ord(letra) - ord('A') + 1)
        return valor

    @staticmethod
    def _sufijo_alfabetico(valor):
        """Convierte un ordinal positivo a A..Z, AA..ZZ, AAA..."""
        if valor < 1:
            raise ValueError('El consecutivo alfabético no es válido.')
        letras = []
        while valor:
            valor, residuo = divmod(valor - 1, 26)
            letras.append(chr(ord('A') + residuo))
        return ''.join(reversed(letras))

    @staticmethod
    def _descomponer_username_padre(usuario):
        """Devuelve (modo, prefijo, consecutivo) sin alterar el prefijo real."""
        if re.search(r"\d{2}$", usuario):
            return 'numerico', usuario[:-2], int(usuario[-2:])
        if re.search(r"[A-Za-z]$", usuario):
            return 'alfabetico', usuario[:-1], UsuariosHijosService._valor_alfabetico(usuario[-1])
        raise ValueError(
            'El formato del usuario distribuidor no permite generar usuarios hijos automáticamente. Contacta al administrador.'
        )

    @staticmethod
    def _siguiente_username_con_cursor(padre_id, cur, bloquear_padre=False):
        bloqueo = ' FOR UPDATE' if bloquear_padre else ''
        cur.execute(
            f"SELECT id, usuario FROM usuarios WHERE id = %s AND rol_id = 2 AND activo = 1{bloqueo}",
            (padre_id,),
        )
        padre = cur.fetchone()
        if not padre:
            raise ValueError('El distribuidor autenticado no existe o está inactivo.')

        usuario_padre = padre['usuario']
        modo, prefijo, consecutivo_padre = UsuariosHijosService._descomponer_username_padre(usuario_padre)
        cur.execute(
            """
            SELECT u.usuario
            FROM jerarquia_usuarios ju
            INNER JOIN usuarios u ON u.id = ju.hijo_id
            WHERE ju.padre_id = %s
            """,
            (padre_id,),
        )
        usernames_hijos = [fila['usuario'] for fila in cur.fetchall()]

        mayor = consecutivo_padre
        if modo == 'numerico':
            patron = re.compile(r'^' + re.escape(prefijo) + r'(\d{2})$')
            for username_hijo in usernames_hijos:
                coincidencia = patron.fullmatch(username_hijo)
                if coincidencia:
                    mayor = max(mayor, int(coincidencia.group(1)))
            if mayor >= 99:
                raise ErrorSecuenciaUsuario('La secuencia numérica de usuarios para este distribuidor se ha agotado.')
            candidato = f'{prefijo}{mayor + 1:02d}'
        else:
            patron = re.compile(r'^' + re.escape(prefijo) + r'([A-Za-z]+)$')
            for username_hijo in usernames_hijos:
                coincidencia = patron.fullmatch(username_hijo)
                if coincidencia:
                    mayor = max(mayor, UsuariosHijosService._valor_alfabetico(coincidencia.group(1)))
            sufijo = UsuariosHijosService._sufijo_alfabetico(mayor + 1)
            # La regla conserva el estilo del padre, incluso si usa minúsculas.
            if usuario_padre[-1].islower():
                sufijo = sufijo.lower()
            candidato = f'{prefijo}{sufijo}'

        if not re.fullmatch(r"[a-zA-Z0-9_.-]{3,20}", candidato):
            raise ErrorSecuenciaUsuario('La secuencia generada supera o no cumple el formato permitido de username.')

        # La secuencia es por padre, pero usuarios.usuario es globalmente UNIQUE.
        # Una colisión ajena se comunica como conflicto, nunca se sustituye por
        # un username elegido por el cliente.
        cur.execute("SELECT id FROM usuarios WHERE usuario = %s", (candidato,))
        if cur.fetchone():
            raise ErrorSecuenciaUsuario('El username generado ya existe y no puede reutilizarse.')
        return candidato

    @staticmethod
    def siguiente_username_hijo(padre_id):
        """Calcula una previsualización no reservada para el distribuidor."""
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            return UsuariosHijosService._siguiente_username_con_cursor(padre_id, cur)
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def obtener_cupo_padre(padre_id):
        """Retorna el limite maximo, hijos activos y disponibilidad del padre."""
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            # Detecta dinámicamente la columna exacta de limites_usuario (padre_id / usuario_id / administrador_id)
            cur.execute("SHOW COLUMNS FROM limites_usuario")
            columnas = [col['Field'] for col in cur.fetchall()]
            col_fk = next((c for c in ['padre_id', 'usuario_id', 'administrador_id', 'admin_id'] if c in columnas), 'padre_id')

            cur.execute(f"SELECT max_hijos FROM limites_usuario WHERE {col_fk} = %s", (padre_id,))
            limite_res = cur.fetchone()

            # Si no existe registro en limites_usuario, el cupo por defecto es 0
            max_hijos = limite_res['max_hijos'] if limite_res is not None else 0

            cur.execute("""
                SELECT COUNT(*) as activos
                FROM jerarquia_usuarios ju
                INNER JOIN usuarios u ON ju.hijo_id = u.id
                WHERE ju.padre_id = %s AND u.activo = 1
            """, (padre_id,))
            activos_res = cur.fetchone()
            hijos_activos = activos_res['activos'] if activos_res else 0

            disponibles = max(0, max_hijos - hijos_activos)

            return {
                "max_hijos": max_hijos,
                "hijos_activos": hijos_activos,
                "disponibles": disponibles,
                "tiene_cupo": disponibles > 0 and max_hijos > hijos_activos
            }
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def _obtener_cupo_padre_con_cursor(cur, padre_id):
        """Versión transaccional del cupo para la creación concurrente."""
        cur.execute("SHOW COLUMNS FROM limites_usuario")
        columnas = [col['Field'] for col in cur.fetchall()]
        col_fk = next((c for c in ['padre_id', 'usuario_id', 'administrador_id', 'admin_id'] if c in columnas), 'padre_id')
        cur.execute(f"SELECT max_hijos FROM limites_usuario WHERE {col_fk} = %s", (padre_id,))
        limite_res = cur.fetchone()
        max_hijos = limite_res['max_hijos'] if limite_res is not None else 0
        cur.execute(
            """
            SELECT COUNT(*) AS activos
            FROM jerarquia_usuarios ju
            INNER JOIN usuarios u ON ju.hijo_id = u.id
            WHERE ju.padre_id = %s AND u.activo = 1
            """,
            (padre_id,),
        )
        hijos_activos = cur.fetchone()['activos']
        return max_hijos, hijos_activos

    @staticmethod
    def validar_pertenencia_hijo(padre_id, hijo_id):
        """Regla de seguridad: verifica que el hijo pertenezca al ambito del padre."""
        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT id FROM jerarquia_usuarios
                WHERE padre_id = %s AND hijo_id = %s
            """, (padre_id, hijo_id))
            return cur.fetchone() is not None
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def crear_usuario_hijo(padre_id, datos_hijo):
        """Crea el usuario y la relacion jerarquica en una sola transaccion atomica."""
        nombre = datos_hijo['nombre']
        correo = datos_hijo['correo']
        contrasena = datos_hijo['contrasena']

        if not all(isinstance(valor, str) for valor in (nombre, correo, contrasena)):
            raise ValueError("Los datos del usuario hijo no son válidos.")

        nombre = nombre.strip()
        correo = correo.strip()

        if not nombre:
            raise ValueError("El nombre es obligatorio.")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", correo):
            raise ValueError("El correo electrónico no tiene un formato válido.")
        if not isinstance(contrasena, str) or len(contrasena) < 6:
            raise ValueError("La contraseña debe tener al menos 6 caracteres.")

        # El lock de la fila padre serializa altas de un mismo distribuidor.
        # UNIQUE usuarios.usuario sigue siendo la última defensa ante procesos
        # externos que no usen este flujo.
        for intento in range(2):
            conn = obtener_conexion()
            cur = conn.cursor(dictionary=True)
            try:
                conn.start_transaction()
                cur.execute(
                    "SELECT cliente_id FROM usuarios WHERE id = %s AND rol_id = 2 AND activo = 1 FOR UPDATE",
                    (padre_id,),
                )
                padre_res = cur.fetchone()
                if not padre_res:
                    raise ValueError("El distribuidor autenticado no existe o está inactivo.")

                max_hijos, hijos_activos = UsuariosHijosService._obtener_cupo_padre_con_cursor(cur, padre_id)
                if max_hijos <= hijos_activos:
                    raise ValueError("Ha alcanzado el límite máximo de usuarios permitidos.")

                # Nunca se usa datos_hijo['usuario']; el valor se recalcula bajo lock.
                usuario = UsuariosHijosService._siguiente_username_con_cursor(padre_id, cur)
                cliente_id = padre_res['cliente_id']

                cur.execute("SELECT id FROM usuarios WHERE correo = %s", (correo,))
                if cur.fetchone():
                    raise ValueError("El correo electrónico ya está registrado.")

                contrasena_hash = hash_password(contrasena)
                cur.execute(
                    """
                    INSERT INTO usuarios (nombre, correo, usuario, contrasena, activo, rol_id, cliente_id)
                    VALUES (%s, %s, %s, %s, 1, 3, %s)
                    """,
                    (nombre, correo, usuario, contrasena_hash, cliente_id),
                )
                hijo_id = cur.lastrowid
                cur.execute("INSERT INTO jerarquia_usuarios (padre_id, hijo_id) VALUES (%s, %s)", (padre_id, hijo_id))
                cur.execute(
                    "INSERT INTO configuracion_montos_usuario (usuario_id, ocultar_montos_global) VALUES (%s, 1)",
                    (hijo_id,),
                )
                conn.commit()
                return {"id": hijo_id, "usuario": usuario, "mensaje": "Usuario hijo creado correctamente."}
            except Exception as e:
                conn.rollback()
                # Un 1062 puede ocurrir frente a un escritor externo. Reintentar
                # una sola vez permite recalcular; el segundo error es controlado.
                if getattr(e, 'errno', None) == 1062:
                    detalle = str(e).lower()
                    es_colision_username = any(marca in detalle for marca in (
                        "key 'usuario'", "key 'usuario_2'", 'usuarios.usuario'
                    ))
                    if es_colision_username and intento == 0:
                        continue
                    if es_colision_username:
                        raise ErrorSecuenciaUsuario('No fue posible reservar un username consecutivo. Intenta nuevamente.')
                    raise ValueError('El nombre del usuario ya está registrado.')
                raise
            finally:
                cur.close()
                conn.close()

    @staticmethod
    def listar_hijos(padre_id):
        """Devuelve la lista de todos los usuarios hijos pertenecientes al padre."""
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("""
                SELECT u.id, u.nombre, u.correo, u.usuario, u.activo, u.cliente_id, ju.creado_en
                FROM jerarquia_usuarios ju
                INNER JOIN usuarios u ON ju.hijo_id = u.id
                WHERE ju.padre_id = %s
                ORDER BY u.id DESC
            """, (padre_id,))
            return cur.fetchall()
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def cambiar_contrasena_hijo(padre_id, hijo_id, nueva_contrasena):
        """Cambia la contraseña de un hijo previa validación del ámbito del padre."""
        if not UsuariosHijosService.validar_pertenencia_hijo(padre_id, hijo_id):
            raise Exception("Acceso denegado: Este usuario no pertenece a su ámbito de administración.")

        # Encriptar la contraseña al actualizarla
        contrasena_hash = hash_password(nueva_contrasena)

        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            cur.execute("UPDATE usuarios SET contrasena = %s WHERE id = %s", (contrasena_hash, hijo_id))
            conn.commit()
            return {"mensaje": "Contraseña actualizada correctamente."}
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def cambiar_estado_hijo(padre_id, hijo_id, nuevo_estado):
        """
        Activa o desactiva un usuario hijo.
        Si se intenta reactivar (estado=1), valida la disponibilidad de cupo.
        """
        if not UsuariosHijosService.validar_pertenencia_hijo(padre_id, hijo_id):
            raise Exception("Acceso denegado: Este usuario no pertenece a su ámbito de administración.")

        # Si se desea reactivar, validar que haya cupo disponible
        if nuevo_estado == 1:
            cupo = UsuariosHijosService.obtener_cupo_padre(padre_id)
            if not cupo["tiene_cupo"]:
                raise Exception("No se puede reactivar al usuario: Ha alcanzado el límite máximo permitido.")

        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            cur.execute("UPDATE usuarios SET activo = %s WHERE id = %s", (nuevo_estado, hijo_id))
            conn.commit()
            estado_texto = "activado" if nuevo_estado == 1 else "desactivado"
            return {"mensaje": f"Usuario {estado_texto} correctamente."}
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def eliminar_usuario_hijo(padre_id, hijo_id):
        """Elimina físicamente un usuario hijo (Rol 3) y sus relaciones previa validación de ámbito."""
        if not UsuariosHijosService.validar_pertenencia_hijo(padre_id, hijo_id):
            raise Exception("Acceso denegado: Este usuario no pertenece a su ámbito de administración.")

        conn = obtener_conexion()
        cur = conn.cursor()
        try:
            # 1. Eliminar permisos asignados en usuario_permisos
            cur.execute("DELETE FROM usuario_permisos WHERE usuario_id = %s", (hijo_id,))
            # Mantiene eliminable al hijo después de activar el modelo nuevo.
            cur.execute("DELETE FROM usuario_modulos WHERE usuario_id = %s", (hijo_id,))
            cur.execute("DELETE FROM usuario_capacidades WHERE usuario_id = %s", (hijo_id,))
            # Las configuraciones de montos tienen FK hacia usuarios y deben
            # limpiarse antes de borrar al hijo.
            cur.execute("DELETE FROM configuracion_montos_usuario_ambito WHERE usuario_id = %s", (hijo_id,))
            cur.execute("DELETE FROM configuracion_montos_usuario WHERE usuario_id = %s", (hijo_id,))
            # 2. Eliminar relación jerárquica en jerarquia_usuarios
            cur.execute("DELETE FROM jerarquia_usuarios WHERE hijo_id = %s", (hijo_id,))
            # 3. Eliminar usuario asegurando que sea un usuario hijo (rol_id = 3)
            cur.execute("DELETE FROM usuarios WHERE id = %s AND rol_id = 3", (hijo_id,))
            conn.commit()
            return {"mensaje": "Usuario hijo eliminado permanentemente."}
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def obtener_correo_padre(padre_id):
        """Obtiene únicamente el correo electrónico del usuario Administrador (Rol 2)."""
        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT correo FROM usuarios WHERE id = %s", (padre_id,))
            resultado = cur.fetchone()
            return {"correo": resultado['correo']} if resultado else {"correo": ""}
        except Exception as e:
            raise e
        finally:
            cur.close()
            conn.close()
