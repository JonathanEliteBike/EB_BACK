# services/usuarios_hijos_service.py

from db_conexion import obtener_conexion
from utils.seguridad import hash_password
import re

class UsuariosHijosService:

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
        usuario = datos_hijo['usuario']
        contrasena = datos_hijo['contrasena']

        if not all(isinstance(valor, str) for valor in (nombre, correo, usuario, contrasena)):
            raise ValueError("Los datos del usuario hijo no son válidos.")

        nombre = nombre.strip()
        correo = correo.strip()
        usuario = usuario.strip()

        if not nombre:
            raise ValueError("El nombre es obligatorio.")
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{3,20}", usuario):
            raise ValueError("El nombre de usuario debe tener entre 3 y 20 caracteres alfanuméricos.")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", correo):
            raise ValueError("El correo electrónico no tiene un formato válido.")
        if not isinstance(contrasena, str) or len(contrasena) < 6:
            raise ValueError("La contraseña debe tener al menos 6 caracteres.")

        cupo = UsuariosHijosService.obtener_cupo_padre(padre_id)
        if not cupo["tiene_cupo"]:
            raise ValueError("Ha alcanzado el límite máximo de usuarios permitidos.")

        conn = obtener_conexion()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT cliente_id FROM usuarios WHERE id = %s", (padre_id,))
            padre_res = cur.fetchone()
            
            if not padre_res:
                raise ValueError("El distribuidor autenticado no existe.")
                
            cliente_id = padre_res['cliente_id']

            cur.execute("SELECT id FROM usuarios WHERE correo = %s", (correo,))
            if cur.fetchone():
                raise ValueError("El correo electrónico ya está registrado.")

            cur.execute("SELECT id FROM usuarios WHERE usuario = %s", (usuario,))
            if cur.fetchone():
                raise ValueError("El nombre de usuario ya está registrado.")

            # Encriptar la contraseña con el mismo método del sistema
            contrasena_hash = hash_password(contrasena)

            sql_usuario = """
                INSERT INTO usuarios (nombre, correo, usuario, contrasena, activo, rol_id, cliente_id)
                VALUES (%s, %s, %s, %s, 1, 3, %s)
            """
            
            cur.execute(sql_usuario, (
                nombre,
                correo,
                usuario,
                contrasena_hash,
                cliente_id
            ))
            hijo_id = cur.lastrowid

            sql_jerarquia = "INSERT INTO jerarquia_usuarios (padre_id, hijo_id) VALUES (%s, %s)"
            cur.execute(sql_jerarquia, (padre_id, hijo_id))

            conn.commit()
            return {"id": hijo_id, "mensaje": "Usuario hijo creado correctamente."}

        except Exception as e:
            conn.rollback()
            raise e
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
