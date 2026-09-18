-- Inicializa la bolsa delegable de todos los usuarios existentes con rol 2.
-- Aplicar despues del catalogo, permisos_modulos_capacidades y
-- acceso_propio_rol2_y_delegacion; requiere delegable_a_hijos configurado.
-- Solo agrega relaciones ausentes; conserva permisos y logica legacy.
-- No asigna modulos a usuarios hijo ni modifica roles, usuarios o modulos.
-- Incluye todos los usuarios rol 2, sin filtrar su estado de actividad.
-- Con 91 usuarios y 7 modulos elegibles hay 637 combinaciones posibles;
-- se insertan unicamente las que falten, sin fijar IDs ni cantidades.
-- Ejecutar en sesion dedicada y detenerse ante errores (sin --force).

START TRANSACTION;

INSERT INTO permisos_delegables_modulos (administrador_id, modulo_id)
SELECT usuario.id, modulo.id
FROM usuarios AS usuario
CROSS JOIN modulos AS modulo
WHERE usuario.rol_id = 2
  AND modulo.activo = 1
  AND modulo.delegable_a_hijos = 1
  AND modulo.identificador <> 'usuarios_creacion_usuarios'
  AND NOT EXISTS (
      SELECT 1
      FROM permisos_delegables_modulos AS existente
      WHERE existente.administrador_id = usuario.id
        AND existente.modulo_id = modulo.id
  );

COMMIT;
