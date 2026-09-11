-- Proyección Compras: "Editar" no tiene flujo para roles 2 o 3.
-- Se conserva la acción global y cualquier asignación histórica; sólo se
-- retira la combinación vigente de modulo_acciones para este módulo.
START TRANSACTION;

SET @modulo_proyeccion_compras := (
    SELECT id FROM modulos
    WHERE identificador = 'usuarios_proyeccion_compras'
    LIMIT 1
);
SET @accion_editar := (
    SELECT id FROM acciones
    WHERE identificador = 'editar'
    LIMIT 1
);

DELETE FROM modulo_acciones
WHERE modulo_id = @modulo_proyeccion_compras
  AND accion_id = @accion_editar;

COMMIT;
