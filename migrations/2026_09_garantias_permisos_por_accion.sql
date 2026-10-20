-- Garantías: catálogo de acciones para el piloto de permisos por acción.
-- No elimina filas históricas de permisos_delegables ni usuario_permisos.

START TRANSACTION;

INSERT INTO acciones (nombre, identificador, activo)
SELECT 'Editar', 'editar', 1
WHERE NOT EXISTS (
    SELECT 1 FROM acciones WHERE identificador = 'editar'
);

SET @modulo_garantias := (
    SELECT id FROM modulos WHERE identificador = 'usuarios_garantias' LIMIT 1
);
SET @accion_editar := (
    SELECT id FROM acciones WHERE identificador = 'editar' LIMIT 1
);
SET @accion_eliminar := (
    SELECT id FROM acciones WHERE identificador = 'eliminar' LIMIT 1
);

INSERT IGNORE INTO modulo_acciones (modulo_id, accion_id)
SELECT @modulo_garantias, @accion_editar
WHERE @modulo_garantias IS NOT NULL AND @accion_editar IS NOT NULL;

-- Eliminar queda como acción global para rol 1, pero deja de ser una acción
-- delegable del módulo Garantías. Las asignaciones históricas se conservan.
DELETE FROM modulo_acciones
WHERE modulo_id = @modulo_garantias
  AND accion_id = @accion_eliminar;

COMMIT;
