-- Corrige trg_previo_insert: la version anterior usaba
-- "INSERT ... ON DUPLICATE KEY UPDATE" contra tabla_retroactivos, pero esa
-- tabla no tiene ninguna llave unica aparte de su PRIMARY KEY(id)
-- autoincremental -- la clausula ON DUPLICATE KEY nunca podia encontrar la
-- fila existente de un cliente, así que cada INSERT masivo en `previo`
-- (recarga completa via /actualizar_previo, que borra e reinserta todo con
-- ids nuevos) creaba una fila duplicada por cliente en tabla_retroactivos en
-- vez de actualizar la existente.
--
-- La comprobacion de existencia se hace por CLAVE (la llave de negocio real),
-- no por id_previo: en un ciclo de borrado+reinsercion, previo.id siempre es
-- nuevo, así que comprobar por id_previo nunca encontraria la fila vieja del
-- mismo cliente. trg_previo_update sí puede usar id_previo porque un UPDATE
-- nunca cambia previo.id (esa fila ya se corrigió antes, no se toca aqui).
--
-- Tambien excluye clientes con nivel='Distribuidor' (no deben aparecer en
-- retroactivos) y limpia cualquier fila vieja de tabla_retroactivos si un
-- cliente reaparece como Distribuidor tras un ciclo de recarga.

DROP TRIGGER IF EXISTS trg_previo_insert;

DELIMITER $$
CREATE TRIGGER trg_previo_insert AFTER INSERT ON previo
FOR EACH ROW
BEGIN
    IF COALESCE(NEW.nivel, '') = 'Distribuidor' THEN
        DELETE FROM tabla_retroactivos WHERE CLAVE = NEW.clave;
    ELSEIF EXISTS (SELECT 1 FROM tabla_retroactivos WHERE CLAVE = NEW.clave) THEN
        UPDATE tabla_retroactivos
        SET id_previo = NEW.id, ZONA = NEW.evac, CLIENTE = NEW.nombre_cliente, CATEGORIA = NEW.nivel,
            COMPRA_MINIMA_ANUAL = COALESCE(NEW.compra_minima_anual, 0),
            COMPRA_MINIMA_APPAREL = COALESCE(NEW.compromiso_apparel_syncros_vittoria, 0),
            COMPRAS_TOTALES_CRUDO = COALESCE(NEW.acumulado_anticipado, 0),
            META_MY26_CUMPLIDA = CASE WHEN COALESCE(NEW.avance_global, 0) >= COALESCE(NEW.compra_minima_anual, 0) THEN 1 ELSE 0 END,
            COMPRA_GLOBAL_SCOTT = COALESCE(NEW.avance_global_scott, 0),
            COMPRA_GLOBAL_APPAREL = COALESCE(NEW.avance_global_apparel_syncros_vittoria, 0),
            COMPRA_GLOBAL_BOLD = COALESCE(NEW.acumulado_bold, 0),
            TOTAL_ACUMULADO = COALESCE(NEW.avance_global, 0)
        WHERE CLAVE = NEW.clave;
    ELSE
        INSERT INTO tabla_retroactivos (
            id_previo, CLAVE, ZONA, CLIENTE, CATEGORIA,
            COMPRA_MINIMA_ANUAL, COMPRA_MINIMA_APPAREL, COMPRAS_TOTALES_CRUDO,
            META_MY26_CUMPLIDA, COMPRA_GLOBAL_SCOTT, COMPRA_GLOBAL_APPAREL, COMPRA_GLOBAL_BOLD,
            TOTAL_ACUMULADO
        ) VALUES (
            NEW.id, NEW.clave, NEW.evac, NEW.nombre_cliente, NEW.nivel,
            COALESCE(NEW.compra_minima_anual, 0), COALESCE(NEW.compromiso_apparel_syncros_vittoria, 0),
            COALESCE(NEW.acumulado_anticipado, 0),
            CASE WHEN COALESCE(NEW.avance_global, 0) >= COALESCE(NEW.compra_minima_anual, 0) THEN 1 ELSE 0 END,
            COALESCE(NEW.avance_global_scott, 0), COALESCE(NEW.avance_global_apparel_syncros_vittoria, 0), COALESCE(NEW.acumulado_bold, 0),
            COALESCE(NEW.avance_global, 0)
        );
    END IF;
END$$
DELIMITER ;
