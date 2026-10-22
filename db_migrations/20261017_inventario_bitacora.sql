-- Ejecutar en STAGING con respaldo verificado ANTES de copiar el backend de Etapa 2.
-- Compatible con MySQL 8.x / InnoDB. CREATE TABLE IF NOT EXISTS es idempotente.
-- Escribir desde endpoints autenticados y solo leer desde administradores autorizados.
CREATE TABLE IF NOT EXISTS inventario_bitacora (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    usuario_id INT NOT NULL,
    usuario_nombre VARCHAR(180) NOT NULL,
    accion VARCHAR(32) NOT NULL,
    entidad VARCHAR(64) NOT NULL,
    entidad_id BIGINT NULL,
    detalle_json JSON NULL,
    fecha DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    KEY ix_ib_fecha (fecha),
    KEY ix_ib_usuario_fecha (usuario_id, fecha),
    KEY ix_ib_accion_fecha (accion, fecha)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
