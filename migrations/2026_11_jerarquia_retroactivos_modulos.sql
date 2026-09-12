-- Segmenta Retroactivos en módulos de acceso. No crea tablas ni modifica
-- columnas: reutiliza modulos, permisos_delegables_modulos y usuario_modulos.
-- Es idempotente y conserva la Carátula existente como un hijo del área.

INSERT INTO modulos (padre_id, nombre, identificador, activo, delegable_a_hijos)
SELECT NULL, 'Retroactivos', 'usuarios_retroactivos', 1, 1
WHERE NOT EXISTS (
    SELECT 1 FROM modulos WHERE identificador = 'usuarios_retroactivos'
);

SET @retroactivos_id := (
    SELECT id FROM modulos WHERE identificador = 'usuarios_retroactivos' LIMIT 1
);

-- La Carátula conserva su id y queda organizada bajo Retroactivos, pero no
-- forma parte de la bolsa delegable: el acceso a esa vista lo representa el
-- módulo padre Retroactivos.
UPDATE modulos
SET padre_id = @retroactivos_id,
    activo = 1,
    delegable_a_hijos = 0
WHERE identificador = 'usuarios_caratula_retroactivos';

INSERT INTO modulos (padre_id, nombre, identificador, activo, delegable_a_hijos)
SELECT @retroactivos_id, 'Calculadora de Retroactivos', 'usuarios_calculadora_retroactivos', 1, 1
WHERE NOT EXISTS (
    SELECT 1 FROM modulos WHERE identificador = 'usuarios_calculadora_retroactivos'
);

INSERT INTO modulos (padre_id, nombre, identificador, activo, delegable_a_hijos)
SELECT @retroactivos_id, 'Solicitudes de Retroactivos', 'usuarios_solicitudes_retroactivos', 1, 1
WHERE NOT EXISTS (
    SELECT 1 FROM modulos WHERE identificador = 'usuarios_solicitudes_retroactivos'
);

-- Preserva la vista principal: quien ya tenía la Carátula recibe el padre
-- Retroactivos. Las dos funciones nuevas se habilitan en la bolsa del
-- distribuidor, pero no se asignan automáticamente a ningún usuario hijo.
INSERT IGNORE INTO permisos_delegables_modulos (administrador_id, modulo_id)
SELECT pdm.administrador_id, @retroactivos_id
FROM permisos_delegables_modulos pdm
INNER JOIN modulos caratula ON caratula.id = pdm.modulo_id
WHERE caratula.identificador = 'usuarios_caratula_retroactivos';

INSERT IGNORE INTO permisos_delegables_modulos (administrador_id, modulo_id)
SELECT pdm.administrador_id, modulo_nuevo.id
FROM permisos_delegables_modulos pdm
INNER JOIN modulos caratula ON caratula.id = pdm.modulo_id
INNER JOIN modulos modulo_nuevo
    ON modulo_nuevo.identificador IN (
        'usuarios_calculadora_retroactivos',
        'usuarios_solicitudes_retroactivos'
    )
WHERE caratula.identificador = 'usuarios_caratula_retroactivos';

INSERT IGNORE INTO usuario_modulos (usuario_id, modulo_id)
SELECT um.usuario_id, @retroactivos_id
FROM usuario_modulos um
INNER JOIN modulos caratula ON caratula.id = um.modulo_id
WHERE caratula.identificador = 'usuarios_caratula_retroactivos';
