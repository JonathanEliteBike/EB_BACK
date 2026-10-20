-- EJECUTAR ÚNICAMENTE sobre la BD local/de staging verificada.
-- No ejecuta USE: selecciona tú el schema correcto previamente.
-- Ningún usuario queda autorizado hasta insertar IDs explícitos.
CREATE TABLE IF NOT EXISTS inventario_usuarios_autorizados (
    usuario_id INT NOT NULL PRIMARY KEY,
    habilitado TINYINT(1) NOT NULL DEFAULT 1,
    creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY ix_inventario_acceso_habilitado (habilitado)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- CONSULTA PARA IDENTIFICAR CANDIDATOS; NO inserta a nadie.
-- SELECT id, usuario, nombre, rol_id, activo FROM usuarios WHERE activo = 1 ORDER BY nombre;
-- EJEMPLO (NO ejecutarlo sin cambiar los IDs por los reales):
-- INSERT INTO inventario_usuarios_autorizados (usuario_id, habilitado)
-- VALUES (101, 1), (102, 1);
-- Para revocar: UPDATE inventario_usuarios_autorizados SET habilitado=0 WHERE usuario_id=101;
