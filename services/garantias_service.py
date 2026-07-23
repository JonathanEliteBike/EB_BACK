from __future__ import annotations
import io
import json
import logging
import re

from utils.tiempo import ahora_str


def invalidar_cache() -> None:
    """No-op: el dashboard ahora lee directo de BD, no hay caché que invalidar."""
    pass


def get_dashboard_data(desde: str | None = None, hasta: str | None = None) -> dict:
    """Calcula KPIs y métricas desde garantia_formularios (BD MySQL).

    Si se pasan `desde`/`hasta` (YYYY-MM-DD), todas las métricas se acotan a
    ese rango de `fecha_creacion` (p. ej. la temporada MY27 vigente) en vez de
    usar el histórico completo. `hasta` es inclusivo del día completo.
    """
    from db_conexion import obtener_conexion

    conn = obtener_conexion()
    if not conn:
        return _empty()
    try:
        cursor = conn.cursor(dictionary=True)

        con_rango = bool(desde and hasta)
        rango_params: tuple = (desde, hasta) if con_rango else ()
        rango_where = "WHERE fecha_creacion >= %s AND fecha_creacion < %s + INTERVAL 1 DAY" if con_rango else ""
        rango_and   = "AND fecha_creacion >= %s AND fecha_creacion < %s + INTERVAL 1 DAY" if con_rango else ""
        rango_where_f = "WHERE f.fecha_creacion >= %s AND f.fecha_creacion < %s + INTERVAL 1 DAY" if con_rango else ""

        # ── KPIs principales ─────────────────────────────────────────────────
        cursor.execute(f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN estatus = 'Cerrado' THEN 1 ELSE 0 END) AS cerradas,
                SUM(CASE WHEN estatus IN ('Enviado','En revisión','Aprobado') THEN 1 ELSE 0 END) AS en_proceso
            FROM garantia_formularios
            {rango_where}
        """, rango_params)
        kpi = cursor.fetchone() or {}

        # Latencia de atención: días desde creación hasta primer evento de validación de docs
        cursor.execute(f"""
            SELECT ROUND(AVG(dias), 1) AS lat_atencion FROM (
                SELECT f.id, DATEDIFF(MIN(c.fecha), f.fecha_creacion) AS dias
                FROM garantia_formularios f
                JOIN garantia_comentarios c ON c.formulario_id = f.id
                     AND c.tipo = 'validacion'
                {rango_where_f}
                GROUP BY f.id
                HAVING dias >= 0
            ) t
        """, rango_params)
        lat_atencion = float((cursor.fetchone() or {}).get('lat_atencion') or 0)

        # Latencia de cierre: días desde creación hasta que el ticket fue Cerrado o Rechazado
        cursor.execute(f"""
            SELECT ROUND(AVG(DATEDIFF(fecha_actualizacion, fecha_creacion)), 1) AS lat_cierre
            FROM garantia_formularios
            WHERE estatus IN ('Cerrado', 'Rechazado') {rango_and}
        """, rango_params)
        lat_cierre = float((cursor.fetchone() or {}).get('lat_cierre') or 0)

        # ── Por estatus ──────────────────────────────────────────────────────
        cursor.execute(f"""
            SELECT estatus, COUNT(*) AS cnt
            FROM garantia_formularios
            {rango_where}
            GROUP BY estatus ORDER BY cnt DESC
        """, rango_params)
        por_estatus = {r['estatus']: r['cnt'] for r in cursor.fetchall()}

        # ── Tickets por mes (latencia = días medios de antigüedad ese mes) ───
        cursor.execute(f"""
            SELECT DATE_FORMAT(fecha_creacion, '%Y-%m') AS ym,
                   DATE_FORMAT(fecha_creacion, '%b %Y')  AS label,
                   ROUND(AVG(DATEDIFF(NOW(), fecha_creacion)), 1) AS lat_prom
            FROM garantia_formularios
            {rango_where}
            GROUP BY ym, label
            ORDER BY ym
        """, rango_params)
        latencia_mensual = {r['label']: r['lat_prom'] for r in cursor.fetchall()}

        # ── Garantías por distribuidor (top 30) ──────────────────────────────
        cursor.execute(f"""
            SELECT distribuidor AS cli, COUNT(*) AS cnt
            FROM garantia_formularios
            WHERE distribuidor IS NOT NULL AND distribuidor != '' {rango_and}
            GROUP BY distribuidor ORDER BY cnt DESC LIMIT 30
        """, rango_params)
        gar_cliente = {r['cli']: r['cnt'] for r in cursor.fetchall()}

        # ── Latencia promedio por distribuidor (top 30) ──────────────────────
        cursor.execute(f"""
            SELECT distribuidor AS cli,
                   ROUND(AVG(DATEDIFF(NOW(), fecha_creacion)), 1) AS lat
            FROM garantia_formularios
            WHERE distribuidor IS NOT NULL AND distribuidor != '' {rango_and}
            GROUP BY distribuidor ORDER BY lat DESC LIMIT 30
        """, rango_params)
        lat_cliente = {r['cli']: r['lat'] for r in cursor.fetchall()}

        # ── Por marca ────────────────────────────────────────────────────────
        cursor.execute(f"""
            SELECT marca, COUNT(*) AS cnt
            FROM garantia_formularios
            WHERE marca IS NOT NULL AND marca != '' {rango_and}
            GROUP BY marca ORDER BY cnt DESC
        """, rango_params)
        por_marca = {r['marca']: r['cnt'] for r in cursor.fetchall()}

        # ── Piezas de reemplazo (columna directa) ────────────────────────────
        cursor.execute(f"""
            SELECT pieza_reemplazo AS pieza, COUNT(*) AS cnt
            FROM garantia_formularios
            WHERE pieza_reemplazo IS NOT NULL
              AND pieza_reemplazo != ''
              AND pieza_reemplazo != 'N/A'
              {rango_and}
            GROUP BY pieza_reemplazo ORDER BY cnt DESC
        """, rango_params)
        piezas_reemplazo = {r['pieza']: r['cnt'] for r in cursor.fetchall()}

        # ── Ubicación del daño y desglose de cuadros (desde JSON datos) ──────
        cursor.execute(f"""
            SELECT folio, distribuidor, pieza_reemplazo, datos
            FROM garantia_formularios WHERE datos IS NOT NULL {rango_and}
        """, rango_params)
        ubic_dano: dict = {}
        cuadros_por_tipo_marco: dict = {}
        cuadros_detalle: list = []
        tipo_dano_re = re.compile(r'^marco_tipo_dano_(\d+)$')
        for row in cursor.fetchall():
            try:
                d = json.loads(row['datos']) if isinstance(row['datos'], str) else (row['datos'] or {})
                for k, v in d.items():
                    if k.startswith('marco_localizacion_') and v:
                        key = str(v).strip()
                        ubic_dano[key] = ubic_dano.get(key, 0) + 1

                if (row.get('pieza_reemplazo') or '').strip().upper() == 'CUADRO':
                    tipo_marco = str(d.get('scott_tipo_marco') or 'Sin especificar').strip()
                    cuadros_por_tipo_marco[tipo_marco] = cuadros_por_tipo_marco.get(tipo_marco, 0) + 1

                    tipo_dano = 'Sin especificar'
                    for k, v in d.items():
                        m = tipo_dano_re.match(k)
                        if m and v:
                            tipo_dano = str(v).strip()
                            if tipo_dano == 'Otros':
                                otros = d.get(f'marco_tipo_dano_otros_{m.group(1)}')
                                if otros:
                                    tipo_dano = str(otros).strip()
                            break
                    cuadros_detalle.append({
                        "folio":        row.get('folio'),
                        "distribuidor": row.get('distribuidor'),
                        "tipo_marco":   tipo_marco,
                        "tipo_dano":    tipo_dano,
                    })
            except Exception:
                pass
        ubic_dano = dict(sorted(ubic_dano.items(), key=lambda x: x[1], reverse=True)[:25])
        cuadros_por_tipo_marco = dict(sorted(cuadros_por_tipo_marco.items(), key=lambda x: x[1], reverse=True))

        return {
            "kpis": {
                "total":        int(kpi.get("total", 0) or 0),
                "cerradas":     int(kpi.get("cerradas", 0) or 0),
                "en_proceso":   int(kpi.get("en_proceso", 0) or 0),
                "lat_atencion": lat_atencion,
                "lat_cierre":   lat_cierre,
            },
            "por_estatus":           por_estatus,
            "latencia_mensual":      latencia_mensual,
            "latencia_por_cliente":  lat_cliente,
            "garantias_por_cliente": gar_cliente,
            "piezas_reemplazo":      piezas_reemplazo,
            "ubicacion_dano":        ubic_dano,
            "por_marca":             por_marca,
            "cuadros_por_tipo_marco": cuadros_por_tipo_marco,
            "cuadros_detalle":        cuadros_detalle,
            "ultima_actualizacion":  ahora_str("%d/%m/%Y %H:%M"),
        }
    except Exception as e:
        logging.exception("Error en get_dashboard_data (DB): %s", e)
        return _empty()
    finally:
        conn.close()


def _empty() -> dict:
    return {
        "kpis": {"total": 0, "cerradas": 0, "en_proceso": 0, "lat_atencion": 0.0, "lat_cierre": 0.0},
        "por_estatus": {}, "latencia_mensual": {}, "latencia_por_cliente": {},
        "garantias_por_cliente": {}, "piezas_reemplazo": {}, "ubicacion_dano": {},
        "por_marca": {}, "cuadros_por_tipo_marco": {}, "cuadros_detalle": [],
        "ultima_actualizacion": ahora_str("%d/%m/%Y %H:%M"),
    }


_COLOR_ESTATUS_PDF = {
    "enviado":      "#f0ad4e",
    "en revisión":  "#5c9bd6",
    "aprobado":     "#4caf50",
    "rechazado":    "#e53935",
    "cerrado":      "#9b59b6",
}


def exportar_pdf(distribuidor: str | None = None, desde: str | None = None, hasta: str | None = None) -> bytes:
    """Exporta los tickets de garantía a PDF (mismo motor que las carátulas: WeasyPrint).

    Si se pasa `distribuidor`, exporta solo los tickets de ese cliente (Kardex).
    Si se pasan `desde`/`hasta` (YYYY-MM-DD), acota a esa temporada/rango.
    """
    try:
        from html import escape
        from weasyprint import HTML
        from db_conexion import obtener_conexion

        conn = obtener_conexion()
        if not conn:
            return b""
        cursor = conn.cursor(dictionary=True)
        condiciones = []
        params: list = []
        if distribuidor:
            condiciones.append("distribuidor = %s")
            params.append(distribuidor)
        if desde and hasta:
            condiciones.append("fecha_creacion >= %s AND fecha_creacion < %s + INTERVAL 1 DAY")
            params.extend([desde, hasta])
        where = ("WHERE " + " AND ".join(condiciones)) if condiciones else ""
        params = tuple(params)
        cursor.execute(f"""
            SELECT
                folio, distribuidor, contacto, puesto, email, marca, estatus,
                estatus_pieza, pieza_reemplazo, docs_validados, serie_validada,
                DATE_FORMAT(fecha_creacion,     '%d/%m/%Y %H:%i') AS fecha_creacion,
                DATE_FORMAT(fecha_actualizacion,'%d/%m/%Y %H:%i') AS fecha_actualizacion,
                CASE WHEN estatus IN ('Cerrado', 'Rechazado')
                     THEN DATEDIFF(COALESCE(fecha_estatus, DATE(fecha_actualizacion)), DATE(fecha_creacion))
                     ELSE NULL END AS lat_cierre
            FROM garantia_formularios
            {where}
            ORDER BY fecha_creacion DESC
        """, params)
        rows = cursor.fetchall()
        conn.close()

        ESTATUS_ABIERTO = ('Enviado', 'En revisión', 'Aprobado')
        ESTATUS_CERRADO = ('Cerrado', 'Rechazado')

        # ── Kardex de un cliente: resumen (total/abiertos/cerrados/latencia
        # de cierre) y la tabla solo con los tickets abiertos -- así el PDF
        # que se imprime sirve para dar seguimiento a lo pendiente, no como
        # archivo histórico completo (eso ya lo cubre "Reporte de Garantías").
        resumen_html = ""
        filas_para_tabla = rows
        if distribuidor:
            abiertos   = [r for r in rows if (r.get('estatus') or '') in ESTATUS_ABIERTO]
            cerrados   = [r for r in rows if (r.get('estatus') or '') in ESTATUS_CERRADO]
            lat_vals   = [r['lat_cierre'] for r in cerrados if r.get('lat_cierre') is not None]
            lat_prom   = round(sum(lat_vals) / len(lat_vals), 1) if lat_vals else None
            filas_para_tabla = abiertos

            resumen_html = f"""
            <div class="resumen">
                <div class="stat">
                    <span class="stat-num">{len(rows)}</span>
                    <span class="stat-label">Garantías totales</span>
                </div>
                <div class="stat">
                    <span class="stat-num" style="color:#f0ad4e">{len(abiertos)}</span>
                    <span class="stat-label">Abiertos</span>
                </div>
                <div class="stat">
                    <span class="stat-num" style="color:#9b59b6">{len(cerrados)}</span>
                    <span class="stat-label">Cerrados</span>
                </div>
                <div class="stat">
                    <span class="stat-num">{f'{lat_prom} d' if lat_prom is not None else '—'}</span>
                    <span class="stat-label">Latencia de cierre prom.</span>
                </div>
            </div>
            """

        def celda(v) -> str:
            return escape(str(v)) if v not in (None, "") else "—"

        def fila(r: dict) -> str:
            color = _COLOR_ESTATUS_PDF.get((r.get("estatus") or "").lower(), "#888")
            docs  = "Sí" if r.get("docs_validados") else "No"
            serie = "Sí" if r.get("serie_validada") else "No"
            return f"""
                <tr>
                    <td>{celda(r.get('folio'))}</td>
                    <td>{celda(r.get('distribuidor'))}</td>
                    <td>{celda(r.get('contacto'))}</td>
                    <td>{celda(r.get('puesto'))}</td>
                    <td>{celda(r.get('email'))}</td>
                    <td>{celda(r.get('marca'))}</td>
                    <td><span class="chip" style="background:{color}22;color:{color};border-color:{color}55;">{celda(r.get('estatus'))}</span></td>
                    <td>{celda(r.get('estatus_pieza'))}</td>
                    <td>{celda(r.get('pieza_reemplazo'))}</td>
                    <td>{docs}</td>
                    <td>{serie}</td>
                    <td>{celda(r.get('fecha_creacion'))}</td>
                    <td>{celda(r.get('fecha_actualizacion'))}</td>
                </tr>"""

        filas_html = "".join(fila(r) for r in filas_para_tabla)
        titulo = f"Kardex de Garantías — {escape(distribuidor)}" if distribuidor else "Reporte de Garantías"
        if desde and hasta:
            titulo += f" ({desde} a {hasta})"
        total_tabla = len(filas_para_tabla)
        meta_txt = (
            f"Mostrando {total_tabla} ticket{'s' if total_tabla != 1 else ''} abierto{'s' if total_tabla != 1 else ''} de {len(rows)} totales"
            if distribuidor else
            f"{total_tabla} ticket{'s' if total_tabla != 1 else ''}"
        )

        html = f"""
        <html>
        <head>
        <meta charset="utf-8">
        <style>
            @page {{ size: A4 landscape; margin: 14mm 10mm; }}
            body {{ font-family: 'Segoe UI', Arial, sans-serif; color: #222; }}
            h1 {{ font-size: 17px; color: #EB5E28; margin: 0 0 2px; }}
            .meta {{ font-size: 9px; color: #666; margin-bottom: 14px; }}
            table {{ width: 100%; border-collapse: collapse; font-size: 7.5px; }}
            th {{ background: #1A1A2E; color: #fff; padding: 5px 6px; text-align: left; }}
            td {{ padding: 4px 6px; border-bottom: 1px solid #e5e5e5; }}
            tr:nth-child(even) td {{ background: #fafafa; }}
            .chip {{ padding: 2px 7px; border-radius: 10px; border: 1px solid; font-weight: 600; white-space: nowrap; }}
            .resumen {{ display: flex; gap: 22px; margin: 4px 0 16px; }}
            .stat {{ display: flex; flex-direction: column; gap: 1px; }}
            .stat-num {{ font-size: 15px; font-weight: 700; color: #222; }}
            .stat-label {{ font-size: 7.5px; color: #666; text-transform: uppercase; letter-spacing: 0.3px; }}
        </style>
        </head>
        <body>
            <h1>{titulo}</h1>
            <div class="meta">Generado el {ahora_str('%d/%m/%Y %H:%M')} &middot; {meta_txt}</div>
            {resumen_html}
            <table>
                <thead>
                    <tr>
                        <th>Folio</th><th>Distribuidor</th><th>Contacto</th><th>Puesto</th>
                        <th>Correo</th><th>Marca</th><th>Estatus</th><th>Estado Pieza</th>
                        <th>Pieza Reemplazo</th><th>Docs Val.</th><th>Serie Val.</th>
                        <th>Fecha Envío</th><th>Últ. Actualización</th>
                    </tr>
                </thead>
                <tbody>{filas_html}</tbody>
            </table>
        </body>
        </html>
        """
        return HTML(string=html).write_pdf()
    except Exception as e:
        logging.exception("Error exportando PDF de garantías: %s", e)
        return b""
