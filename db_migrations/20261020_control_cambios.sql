-- Inventario IT: revisión mensual de eventos existentes, sin duplicar la bitácora.
-- Ejecutar primero en la BD de PRUEBAS, después de respaldarla.
CREATE TABLE IF NOT EXISTS inventario_revision_cambios (
    bitacora_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    estado VARCHAR(16) NOT NULL DEFAULT 'Pendiente',
    observacion TEXT NULL,
    revisor_id INT NULL,
    revisor_nombre VARCHAR(180) NULL,
    revisado_en DATETIME(6) NULL,
    actualizado_en DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_revision_evento FOREIGN KEY (bitacora_id)
        REFERENCES inventario_bitacora(id) ON DELETE RESTRICT,
    KEY ix_revision_estado (estado),
    KEY ix_revision_revisor (revisor_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Una nueva fila por decisión/cambio de observación; no se sobrescribe el historial.
CREATE TABLE IF NOT EXISTS inventario_revision_cambios_historial (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    bitacora_id BIGINT UNSIGNED NOT NULL,
    estado_anterior VARCHAR(16) NOT NULL,
    estado_nuevo VARCHAR(16) NOT NULL,
    observacion_anterior TEXT NULL,
    observacion_nueva TEXT NULL,
    revisor_id INT NOT NULL,
    revisor_nombre VARCHAR(180) NOT NULL,
    fecha DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_revision_historial_evento FOREIGN KEY (bitacora_id)
        REFERENCES inventario_bitacora(id) ON DELETE RESTRICT,
    KEY ix_revision_hist_fecha (fecha),
    KEY ix_revision_hist_evento (bitacora_id, fecha)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
