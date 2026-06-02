import os
from datetime import datetime
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import base64
import requests

from datetime import datetime
from db_conexion import obtener_conexion

# Configuración de Gmail para diferentes usuarios
EMAIL_CONFIGS = {
    'evacA': {
        'user': os.getenv('EVACA_GMAIL_USER', 'servicioalcliente01@elitebike-mx.com'),
        'password': os.getenv('EVACA_GMAIL_PASSWORD', 'tkjh fnvu tnli mmig')
    },
    'evacB': {
        'user': os.getenv('EVACB_GMAIL_USER', 'servicioalcliente02@elitebike-mx.com'),
        'password': os.getenv('EVACB_GMAIL_PASSWORD', 'otns ntux ebvs hnlc')
    },
    'alma_fraire': {
        'user': os.getenv('ALMAF_GMAIL_USER', 'gerencia.operaciones@elitebike-mx.com'), 
        'password': os.getenv('ALMAF_GMAIL_PASSWORD', 'huko lvse pbpf kazh') 
    },
    'default': {
        'user': os.getenv('GMAIL_USER', 'sistemas@elitebike-mx.com'),
        'password': os.getenv('GMAIL_PASSWORD', 'yjtt fmca kbrr htar')
    }
}

def obtener_imagen_base64(url):
    try:
        response = requests.get(url)
        if response.status_code == 200:
            content_type = response.headers.get('content-type')
            encoded_string = base64.b64encode(response.content).decode('utf-8')
            return f"data:{content_type};base64,{encoded_string}"
    except Exception as e:
        print(f"Error cargando imagen: {e}")
    return url # Retorna la URL original como fallback

def obtener_credenciales_por_usuario(usuario):
    """Obtener credenciales basado en el nombre de usuario"""
    usuario_lower = usuario.lower()
    
    if usuario_lower.startswith('evaca') or usuario_lower.startswith('evac_a'):
        return EMAIL_CONFIGS['evacA']
    elif usuario_lower.startswith('evacb') or usuario_lower.startswith('evac_b'):
        return EMAIL_CONFIGS['evacB']
    elif usuario_lower == 'gerencia.operaciones' or 'alma fraire' in usuario_lower:
        return EMAIL_CONFIGS['alma_fraire']
    else:
        return EMAIL_CONFIGS['default']

def crear_cuerpo_email(data):
    """
    Crea el HTML para el cuerpo del email y para un PDF adjunto, con el diseño
    final de fondo blanco, tarjetas oscuras y encabezado reorganizado.
    """
    datos_caratula = data.get('datos_caratula', {})
    mensaje_personalizado = data.get('mensaje_personalizado', '')
    periodos = data.get('periodos', [])
    fecha_actual = datetime.now().strftime('%d/%m/%Y')
    logo_url = "https://eb-imagenes-26.s3.us-east-2.amazonaws.com/logo_elite_2.png"

    def to_currency(value):
        try: return f"${float(value):,.2f}"
        except (ValueError, TypeError): return "$0.00"

    def to_percent(value):
        try: return f"{float(value):.0f}"
        except (ValueError, TypeError): return "0"

    def get_status_text(value):
        try:
            val = float(value)
            return 'Cumplido' if val <= 0 else 'Faltante'
        except (ValueError, TypeError): return 'Faltante'

    def get_status_class(value):
        try:
            val = float(value)
            return 'estado-cumplido' if val <= 0 else 'estado-faltante'
        except (ValueError, TypeError): return 'estado-faltante'

    def calcular_compromiso_acumulado_scott(d):
        # Obtenemos el mes actual (1 para Enero, 2 para Febrero, etc.)
        mes_actual = datetime.now().month
        
        # Siempre sumamos el primer semestre completo (Julio a Diciembre 2025)
        total = (float(d.get('compromiso_jul_ago', 0)) + 
                float(d.get('compromiso_sep_oct', 0)) + 
                float(d.get('compromiso_nov_dic', 0)))
        
        # Lógica de activación por fechas de 2026:
        if mes_actual >= 1: # Enero-Febrero
            total += float(d.get('compromiso_ene_feb', 0))
        if mes_actual >= 3: # Marzo-Abril
            total += float(d.get('compromiso_mar_abr', 0))
        if mes_actual >= 5: # Mayo-Junio
            total += float(d.get('compromiso_may_jun', 0))
            
        return total

    def calcular_avance_acumulado_scott(d):
        # ACTIVACIÓN DEL SOBRANTE: Sumamos TODOS los avances reales de la temporada completa.
        # Al sumar todos los periodos, si un bimestre anterior tuvo ventas de más, 
        # ese excedente ya está en este "total" y ayuda a cubrir la meta del acumulado actual.
        return (float(d.get('avance_jul_ago', 0)) + 
                float(d.get('avance_sep_oct', 0)) + 
                float(d.get('avance_nov_dic', 0)) +
                float(d.get('avance_ene_feb', 0)) +
                float(d.get('avance_mar_abr', 0)) +
                float(d.get('avance_may_jun', 0)))

    def calcular_compromiso_acumulado_apparel(d):
        mes_actual = datetime.now().month
        total = (float(d.get('compromiso_jul_ago_app', 0)) + 
                float(d.get('compromiso_sep_oct_app', 0)) + 
                float(d.get('compromiso_nov_dic_app', 0)))
        
        if mes_actual >= 1:
            total += float(d.get('compromiso_ene_feb_app', 0))
        if mes_actual >= 3:
            total += float(d.get('compromiso_mar_abr_app', 0))
        if mes_actual >= 5:
            total += float(d.get('compromiso_may_jun_app', 0))
            
        return total

    def calcular_avance_acumulado_apparel(d):
        # Sumatoria total de Apparel para considerar el sobrante
        return (float(d.get('avance_jul_ago_app', 0)) + 
                float(d.get('avance_sep_oct_app', 0)) + 
                float(d.get('avance_nov_dic_app', 0)) +
                float(d.get('avance_ene_feb_app', 0)) +
                float(d.get('avance_mar_abr_app', 0)) +
                float(d.get('avance_may_jun_app', 0)))
        
    def get_sobrante_acumulado(compromiso, avance):
        diferencia = avance - compromiso
        return to_currency(diferencia) if diferencia > 0 else to_currency(0)

    # --- HTML del correo (sin cambios) ---
    cuerpo_email_html = f"""
    <!DOCTYPE html><html><head><meta charset="UTF-8"><style>body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }} 
    .header {{ display: flex; align-items: flex-start; background-color: #FFFFFF; padding: 20px; border-radius: 5px; }} 
    .logo {{ width: 80px; height: auto; margin-right: 20px; }} .header-text {{ display: flex; flex-direction: column; justify-content: flex-start; padding-top: 30px; }} 
    .content {{ padding: 20px; }} .cliente-info {{ background-color: #e9ecef; padding: 15px; border-radius: 5px; margin-bottom: 20px; border-left: 4px solid #007bff; }}
    </style></head><body><div class="header"><img src="{logo_url}" alt="Logo Elite Bike" style="width: 150px; height: auto; display: block; margin: 0;"><div class="header-text"><h2 style="margin: 0; color: #2c3e50; line-height: 1.2;">
    Elite Bike - Carátula</h2></div></div><div class="content"><p>Estimado distribuidor,</p><p>Se adjunta la carátula respecto a su avance en esta temporada:</p><div class="cliente-info">
    <h3 style="margin-top: 0;">Distribuidor:</h3><p style="font-size: 16px; font-weight: bold;">{data.get('cliente_nombre', '')}</p><p><strong>Fecha de envío:</strong> {fecha_actual}</p>
    </div>{f'<p><strong>Comentarios adicionales:</strong><br>{mensaje_personalizado}</p>' if mensaje_personalizado else ''}<p>Si tiene alguna pregunta o necesita información adicional, 
    no dude en contactarnos.</p><p>Atentamente,<br>Equipo de <strong>Elite Bike</strong></p></div></body></html>
    """

    # =====================================================================
    # === SECCIÓN 2: CSS PARA EL PDF (AJUSTES PARA COINCIDIR CON IMAGEN) ===
    # =====================================================================

    css_styles_pdf = """
    :root {
        --primary-color: #EB5E28; --secondary-color: #403d39; --success-color: #28a745;
        --danger-color: #dc3545; --light-color: #FFFCF2; --dark-color: #1e1d1b;
        --border-color: #555; --border-radius: 8px;
    }
    @page { size: A4; margin: 0.6cm; }
    body { 
        font-family: 'Segoe UI', Tahoma, sans-serif; 
        background-color: #FFFFFF; color: #333; 
        line-height: 1.4; margin: 0; padding: 10px; font-size: 9px;
    }
    /* --- Encabezado centrado --- */
    .titulo-wrapper { 
        display: flex;
        justify-content: center;
        align-items: center;
        margin-bottom: 10px; 
        width: 100%;
    }
    .titulo { 
        display: flex;
        align-items: center; 
        gap: 10px;
        margin-bottom: 5px;
    }
    .titulo img { height: 35px; }
    .titulo h1 { 
        color: #333; font-size: 1.2rem; margin: 0; 
        font-weight: bold;
    }
    /* --- Información del cliente en línea --- */
    .client-info { 
        background: var(--secondary-color); 
        padding: 10px 15px; 
        margin-bottom: 12px;
        border-radius: var(--border-radius);
        display: flex; 
        justify-content: space-between; 
        flex-wrap: wrap;
    }
    .info-item {
        flex: 1;
        min-width: 120px;
        margin: 5px 0;
        text-align: center;
    }
    .info-label { 
        font-size: 8px; 
        margin-bottom: 2px; 
        text-transform: uppercase; 
        color: #d0cec6;
        display: block;
        font-weight: bold;
    }
    .info-value { 
        font-size: 11px; 
        font-weight: 600; 
        color: var(--light-color); 
        display: block;
    }
    .section { 
        background: var(--secondary-color); 
        padding: 12px; 
        margin-bottom: 12px;
        border-radius: var(--border-radius);
    }
    .section h3 { 
        color: var(--primary-color); 
        font-size: 13px; 
        margin: 0 0 10px 0; 
        padding-bottom: 5px; 
        border-bottom: 1px solid var(--border-color);
        font-weight: bold;
    }
    .periodo-header { 
        margin-bottom: 8px; 
        padding: 6px 10px; 
        background-color: var(--border-color); 
        border-radius: 6px; 
        display: flex; 
        justify-content: space-between; 
        align-items: center;
    }
    .periodo-title { 
        font-weight: 600; 
        color: var(--light-color); 
        font-size: 10px; 
    }
    .status-value { 
        font-weight: 600; 
        padding: 3px 8px; 
        border-radius: 12px; 
        font-size: 8px;
    }
    .en-curso { background-color: var(--primary-color); color: var(--light-color); }
    .cerrado { background-color: var(--danger-color); color: var(--light-color); }
    table { 
        width: 100%; 
        border-collapse: collapse; 
    }
    th { 
        background-color: var(--border-color); 
        color: var(--light-color); 
        padding: 6px 8px; 
        text-align: left; 
        font-weight: 600; 
        font-size: 9px;
    }
    td { 
        padding: 6px 8px; 
        border-bottom: 1px solid var(--border-color); 
        color: var(--light-color); 
        font-size: 10px;
    }
    tr:nth-child(even) { background-color: #333; }
    tr:last-child td { border-bottom: none; }
    .estado-cumplido { color: var(--success-color); font-weight: 600; }
    .estado-faltante { color: var(--danger-color); font-weight: 600; }
    /* --- Compra mínima en línea (3 columnas) --- */
    .compra-minima { 
        display: flex; 
        justify-content: space-between; 
        gap: 10px; 
        margin-top: 10px;
    }
    .compra-item { 
        background-color: var(--border-color); 
        padding: 8px;
        border-radius: 6px; 
        text-align: center; 
        flex: 1;
    }
    .compra-label { 
        font-size: 8px; 
        color: #d0cec6; 
        margin-bottom: 3px; 
        text-transform: uppercase;
        font-weight: bold;
    }
    .compra-value { 
        font-size: 11px; 
        font-weight: 600; 
        color: var(--light-color); 
    }
    /* --- Tamaño del porcentaje más pequeño --- */
    .compra-porcentaje { 
        font-size: 14px;  /* Reducido de 1.2rem (aprox 19px) a 14px */
        font-weight: 700; 
        color: var(--success-color); 
    }
    """
    
    def generar_headers_periodo_pdf(lista_periodos):
        html = ""
        status_classes = {'En curso': 'en-curso', 'Cerrado': 'cerrado'}
        for p in lista_periodos:
            if p.get('estado') != 'Sin iniciar':
                nombre = p.get('nombre', 'N/A').replace('-', ' - ')
                estado = p.get('estado', 'N/A')
                status_class = status_classes.get(estado, '')
                html += f'<div class="periodo-header"><div class="periodo-title">Periodo - {nombre}</div><div class="status-value {status_class}">{estado}</div></div>'
        return html

    # --- HTML del PDF (ajustado para coincidir con la imagen) ---
    html_cliente_info = f"""
    <div class="titulo-wrapper">
        <div class="titulo">
            <img src="{logo_url}" alt="Logo" width="140" height="70" style="display:block; object-fit: contain;">
            <h1>Avance MY26</h1>
        </div>
    </div>
    <div class="client-info">
        <div class="info-item"><span class="info-label">Clave</span><span class="info-value">{datos_caratula.get('clave','')}</span></div>
        <div class="info-item"><span class="info-label">Evac</span><span class="info-value">{datos_caratula.get('evac','')}</span></div>
        <div class="info-item"><span class="info-label">Nombre Cliente</span><span class="info-value">{datos_caratula.get('nombre_cliente','')}</span></div>
        <div class="info-item"><span class="info-label">Nivel</span><span class="info-value">{datos_caratula.get('nivel','')}</span></div>
    </div>"""
    
    # --- Resto del HTML sin cambios ---
    compromiso_acum_scott = calcular_compromiso_acumulado_scott(datos_caratula)
    avance_acum_scott = calcular_avance_acumulado_scott(datos_caratula)
    faltante_scott = compromiso_acum_scott - avance_acum_scott
    
    compromiso_acum_apparel = calcular_compromiso_acumulado_apparel(datos_caratula)
    avance_acum_apparel = calcular_avance_acumulado_apparel(datos_caratula)
    faltante_apparel = compromiso_acum_apparel - avance_acum_apparel

    porcentaje_scott = (avance_acum_scott / compromiso_acum_scott * 100) if compromiso_acum_scott > 0 else 0
    porcentaje_apparel = (avance_acum_apparel / compromiso_acum_apparel * 100) if compromiso_acum_apparel > 0 else 0
    
    # --- Dentro de crear_cuerpo_email, actualiza tabla_bimestral ---
    tabla_bimestral = f"""
    <div class="section">
        <h3>a) Compromiso BIMESTRAL</h3>
        {generar_headers_periodo_pdf(periodos)}
        <table>
            <thead><tr><th>RESUMEN ACUMULADO</th><th>SCOTT</th><th>APPAREL, SYNCROS, VITTORIA</th></tr></thead>
            <tbody>
                <tr><td>Compromiso Acumulado</td><td>{to_currency(compromiso_acum_scott)}</td><td>{to_currency(compromiso_acum_apparel)}</td></tr>
                <tr><td>Avance Real Total</td><td>{to_currency(avance_acum_scott)}</td><td>{to_currency(avance_acum_apparel)}</td></tr>
                <tr><td>% de Cumplimiento</td><td>{to_percent(porcentaje_scott)}%</td><td>{to_percent(porcentaje_apparel)}%</td></tr>
                <tr><td>Importe Faltante</td><td>{to_currency(faltante_scott if faltante_scott > 0 else 0)}</td><td>{to_currency(faltante_apparel if faltante_apparel > 0 else 0)}</td></tr>
                <tr>
                    <td>Estatus Acumulado</td>
                    <td class="{get_status_class(faltante_scott)}">{get_status_text(faltante_scott)}</td>
                    <td class="{get_status_class(faltante_apparel)}">{get_status_text(faltante_apparel)}</td>
                </tr>
            </tbody>
        </table>
    </div>"""

    avance_compra_minima = datos_caratula.get(
        'avance_global',
        datos_caratula.get('acumulado_anticipado', 0)
    )
    
    html_compra_minima_inicial = f"""<div class="section"><h3>b) Compra Mínima INICIAL</h3><div class="compra-minima"><div class="compra-item"><div class="compra-label">Meta</div><div class="compra-value">{to_currency(datos_caratula.get('compra_minima_inicial',0))}</div></div><div class="compra-item"><div class="compra-label">Avance marcas válidas</div><div class="compra-value">{to_currency(avance_compra_minima)}</div></div><div class="compra-item"><div class="compra-label">Porcentaje</div><div class="compra-porcentaje">{to_percent(datos_caratula.get('porcentaje_global',0))}%</div></div></div></div>"""
    html_compra_minima_anual = f"""<div class="section"><h3>c) Compra Mínima ANUAL</h3><div class="compra-minima"><div class="compra-item"><div class="compra-label">Meta</div><div class="compra-value">{to_currency(datos_caratula.get('compra_minima_anual',0))}</div></div><div class="compra-item"><div class="compra-label">Avance marcas válidas</div><div class="compra-value">{to_currency(avance_compra_minima)}</div></div><div class="compra-item"><div class="compra-label">Porcentaje</div><div class="compra-porcentaje">{to_percent(datos_caratula.get('porcentaje_anual',0))}%</div></div></div></div>"""    
        
    html_caratula_pdf = f"""
    <!DOCTYPE html><html><head><meta charset="UTF-8"><style>{css_styles_pdf}</style></head>
    <body>{html_cliente_info}{tabla_bimestral}{html_compra_minima_inicial}{html_compra_minima_anual}</body></html>"""

    return {
        'cuerpo_email_html': cuerpo_email_html,
        'html_caratula_pdf': html_caratula_pdf
    }
    
def obtener_nombre_usuario(usuario):
    """Obtener el nombre completo del usuario desde la BD"""
    try:
        conexion = obtener_conexion()
        with conexion.cursor() as cursor:
            cursor.execute("SELECT nombre FROM usuarios WHERE usuario = %s", (usuario,))
            resultado = cursor.fetchone()
            return resultado[0] if resultado else usuario
    except Exception as e:
        print(f"Error al obtener nombre de usuario: {str(e)}")
        return usuario
    finally:
        if conexion:
            conexion.close()

def guardar_historial_inicial(usuario, nombre_usuario, correo_remitente, correo_destinatario, cliente_nombre, clave_cliente):
    """Guarda un registro inicial permitiendo claves de la tabla clientes o previo."""
    try:
        conexion = obtener_conexion()
        with conexion.cursor() as cursor:
            # Ahora clave_cliente puede ser 'Integral 1' sin problemas
            sql = """
                INSERT INTO historial_caratulas 
                (nombre_usuario, usuario_envio, correo_remitente, correo_destinatario, 
                 cliente_nombre, clave_cliente, fecha_envio, hora_envio, estado)
                VALUES (%s, %s, %s, %s, %s, %s, CURDATE(), CURTIME(), 'pendiente')
            """
            cursor.execute(sql, (nombre_usuario, usuario, correo_remitente, correo_destinatario, 
                                 cliente_nombre, clave_cliente))
        conexion.commit()
        return cursor.lastrowid
    except Exception as e:
        # Si esto falla, el log nos dirá por qué, pero ya no será por la Foreign Key
        print(f"Error al guardar historial inicial: {str(e)}")
        return None
    finally:
        if conexion:
            conexion.close()

def actualizar_estado_historial(historial_id, estado):
    """Actualiza el estado de un registro en la tabla historial_caratulas."""
    try:
        conexion = obtener_conexion()
        with conexion.cursor() as cursor:
            sql = "UPDATE historial_caratulas SET estado = %s WHERE id = %s"
            cursor.execute(sql, (estado, historial_id))
        conexion.commit()
    except Exception as e:
        print(f"Error al actualizar estado del historial: {str(e)}")
    finally:
        if conexion:
            conexion.close()