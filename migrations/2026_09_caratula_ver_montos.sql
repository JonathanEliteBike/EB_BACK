-- Acción específica para mostrar montos internos en Detalles de Compra.
-- La migración es idempotente y sólo afecta usuarios_caratula.

INSERT INTO acciones (nombre, identificador, activo)
SELECT 'Ver montos', 'ver_montos', 1
WHERE NOT EXISTS (
    SELECT 1 FROM acciones WHERE identificador = 'ver_montos'
);

INSERT IGNORE INTO modulo_acciones (modulo_id, accion_id)
SELECT m.id, a.id
FROM modulos m
INNER JOIN acciones a ON a.identificador = 'ver_montos'
WHERE m.identificador = 'usuarios_caratula';
