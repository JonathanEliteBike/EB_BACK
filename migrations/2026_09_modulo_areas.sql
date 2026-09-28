START TRANSACTION;
CREATE TABLE IF NOT EXISTS modulo_areas (
    modulo_id INT NOT NULL,
    area_id INT NOT NULL,
    creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (modulo_id, area_id),
    KEY idx_modulo_areas_area_id (area_id),
    CONSTRAINT fk_modulo_areas_modulo
        FOREIGN KEY (modulo_id) REFERENCES modulos (id)
        ON UPDATE CASCADE ON DELETE CASCADE,
    CONSTRAINT fk_modulo_areas_area
        FOREIGN KEY (area_id) REFERENCES areas (id)
        ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
INSERT IGNORE INTO modulo_areas (modulo_id, area_id)
SELECT id, area_id
FROM modulos
WHERE area_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS modulo_area_acciones (
    modulo_id INT NOT NULL, area_id INT NOT NULL, accion_id INT NOT NULL,
    creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (modulo_id, area_id, accion_id),
    KEY idx_maa_accion_id (accion_id),
    CONSTRAINT fk_maa_modulo_area FOREIGN KEY (modulo_id, area_id)
        REFERENCES modulo_areas (modulo_id, area_id) ON UPDATE CASCADE ON DELETE CASCADE,
    CONSTRAINT fk_maa_accion FOREIGN KEY (accion_id)
        REFERENCES acciones (id) ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
INSERT IGNORE INTO modulo_area_acciones (modulo_id, area_id, accion_id)
SELECT ma.modulo_id, ma.area_id, macc.accion_id
FROM modulo_areas ma INNER JOIN modulo_acciones macc ON macc.modulo_id = ma.modulo_id;
SET @upi_area_col := (SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='usuario_permisos_internos' AND COLUMN_NAME='area_id');
SET @sql := IF(@upi_area_col=0, 'ALTER TABLE usuario_permisos_internos ADD COLUMN area_id INT NULL AFTER modulo_id', 'SELECT 1'); PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
UPDATE usuario_permisos_internos upi INNER JOIN usuarios u ON u.id=upi.usuario_id
INNER JOIN modulo_area_acciones maa ON maa.modulo_id=upi.modulo_id AND maa.area_id=u.area_id AND maa.accion_id=upi.accion_id
SET upi.area_id=u.area_id WHERE upi.area_id IS NULL AND u.area_id IS NOT NULL;
-- Se agrega primero una llave sustituta. El índice UNIQUE satisface el requisito
-- de AUTO_INCREMENT mientras la PK histórica continúa activa.
SET @upi_id_col := (SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='usuario_permisos_internos' AND COLUMN_NAME='id');
SET @sql := IF(@upi_id_col=0, 'ALTER TABLE usuario_permisos_internos ADD COLUMN id INT NOT NULL AUTO_INCREMENT FIRST, ADD UNIQUE KEY uq_upi_id (id)', 'SELECT 1'); PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
-- La FK fk_upi_usuario no puede depender de la PK que se retirará.
SET @upi_usuario_idx := (SELECT COUNT(*) FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='usuario_permisos_internos' AND COLUMN_NAME='usuario_id' AND INDEX_NAME<>'PRIMARY');
SET @sql := IF(@upi_usuario_idx=0, 'ALTER TABLE usuario_permisos_internos ADD KEY idx_upi_usuario_id (usuario_id)', 'SELECT 1'); PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
SET @upi_pk_es_id := (SELECT COUNT(*) FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='usuario_permisos_internos' AND INDEX_NAME='PRIMARY' AND COLUMN_NAME='id');
SET @upi_pk := (SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='usuario_permisos_internos' AND CONSTRAINT_TYPE='PRIMARY KEY');
SET @sql := IF(@upi_pk>0 AND @upi_pk_es_id=0, 'ALTER TABLE usuario_permisos_internos DROP PRIMARY KEY', 'SELECT 1'); PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
SET @upi_pk := (SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='usuario_permisos_internos' AND CONSTRAINT_TYPE='PRIMARY KEY');
SET @sql := IF(@upi_pk=0, 'ALTER TABLE usuario_permisos_internos ADD PRIMARY KEY (id)', 'SELECT 1'); PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
SET @upi_uq := (SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='usuario_permisos_internos' AND CONSTRAINT_NAME='uq_upi_usuario_modulo_area_accion');
SET @sql := IF(@upi_uq=0, 'ALTER TABLE usuario_permisos_internos ADD UNIQUE KEY uq_upi_usuario_modulo_area_accion (usuario_id, modulo_id, area_id, accion_id)', 'SELECT 1'); PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
SET @upi_area_idx := (SELECT COUNT(*) FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='usuario_permisos_internos' AND COLUMN_NAME='area_id');
SET @sql := IF(@upi_area_idx=0, 'ALTER TABLE usuario_permisos_internos ADD KEY idx_upi_area_id (area_id)', 'SELECT 1'); PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
SET @upi_area_fk := (SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME='usuario_permisos_internos' AND CONSTRAINT_NAME='fk_upi_area');
SET @sql := IF(@upi_area_fk=0, 'ALTER TABLE usuario_permisos_internos ADD CONSTRAINT fk_upi_area FOREIGN KEY (area_id) REFERENCES areas(id) ON UPDATE CASCADE ON DELETE RESTRICT', 'SELECT 1'); PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
COMMIT;
