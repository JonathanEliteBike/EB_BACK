from __future__ import annotations
import xmlrpc.client
import ssl
import time
import logging
import os
from pathlib import Path
from dotenv import load_dotenv

# Cargar .env desde la raíz del proyecto (EB_BACK/)
load_dotenv(Path(__file__).resolve().parent.parent / '.env')

# --- AMBIENTE ACTIVO (prod | test) ---
_ENV = os.getenv('ODOO_ENV', 'prod').strip().lower()

if _ENV == 'test':
    ODOO_URL      = os.getenv('ODOO_TEST_URL',      'https://ebik-pruebas031826-30774924.dev.odoo.com')
    ODOO_DB       = os.getenv('ODOO_TEST_DB',       'ebik-pruebas031826-30774924')
    ODOO_USER     = os.getenv('ODOO_TEST_USER',     'sistemas@elitebike-mx.com')
    ODOO_PASSWORD = os.getenv('ODOO_TEST_PASSWORD', '')
else:
    # ODOO_PROD_* tiene prioridad; si no existe, usa ODOO_* (nombres del servidor)
    ODOO_URL      = os.getenv('ODOO_PROD_URL')      or os.getenv('ODOO_URL',      'https://ebik.odoo.com')
    ODOO_DB       = os.getenv('ODOO_PROD_DB')       or os.getenv('ODOO_DB',       'ebik-prod-15375115')
    ODOO_USER     = os.getenv('ODOO_PROD_USER')     or os.getenv('ODOO_USER',     'admin')
    ODOO_PASSWORD = os.getenv('ODOO_PROD_PASSWORD') or os.getenv('ODOO_PASSWORD', '')

logging.info('Odoo ambiente: %s | URL: %s | DB: %s', _ENV.upper(), ODOO_URL, ODOO_DB)

# 👇 BLINDAJE MULTI-EMPRESA: Fijamos el ID de Elite Bike
ODOO_COMPANY_ID = 1

PREFIJOS_MODO_BALANZA = ['6', '5', '7', '2'] 

def get_odoo_models(retries: int = 3, delay: float = 1.0):
    """Attempt to connect and authenticate to Odoo XML-RPC."""
    context = ssl._create_unverified_context()
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common", context=context)
            models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", context=context)
            uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASSWORD, {})
            if uid:
                return uid, models, None
            else:
                logging.warning("Odoo authenticate returned falsy uid on attempt %d", attempt)
                last_exc = RuntimeError("Odoo authentication failed (falsy uid)")
        except Exception as e:
            last_exc = e
            logging.exception("Error conexión Odoo (attempt %d): %s", attempt, e)

        time.sleep(delay * attempt)

    tb = None
    try:
        import traceback as _tb
        tb = _tb.format_exception(type(last_exc), last_exc, last_exc.__traceback__)
        tb = ''.join(tb)
    except Exception:
        tb = str(last_exc)
    logging.error("Could not connect to Odoo after %d attempts: %s", retries, last_exc)
    return None, None, tb

def obtener_saldo_cuenta_odoo(codigo_cuenta, fecha_inicio, fecha_fin, es_ingreso=True, columna_saldo='Debe', excluir_txt=None, incluir_txt=None, palabras_incluidas=None):
    uid, models, _ = get_odoo_models()
    
    if not uid: return 0.0

    if codigo_cuenta == 'TODAS_VENTAS':
        return _motor_flujo_global_clientes(models, uid, fecha_inicio, fecha_fin)

    reglas_especiales = ['Acumulado_Haber', 'Acumulado_Debe', 'Solo_Haber', 'Solo_Debe', 'Haber', 'Balance']
    forzar_balanza = bool(incluir_txt or excluir_txt or palabras_incluidas or columna_saldo in reglas_especiales)
    usar_balanza = forzar_balanza or any(codigo_cuenta.startswith(p) for p in PREFIJOS_MODO_BALANZA)
    
    if usar_balanza:
        return _motor_balanza(models, uid, codigo_cuenta, fecha_inicio, fecha_fin, columna_saldo, excluir_txt, incluir_txt, palabras_incluidas)
    else:
        return _motor_flujo(models, uid, codigo_cuenta, fecha_inicio, fecha_fin, es_ingreso)

# ==============================================================================
# MOTOR GLOBAL
# ==============================================================================
def _motor_flujo_global_clientes(models, uid, fecha_inicio, fecha_fin):
    print(f"🌍 [Motor Global] Trayendo todos los pagos de clientes...")
    domain = [
        ('company_id', '=', ODOO_COMPANY_ID), # 🚀 PROTECCIÓN
        ('date', '>=', fecha_inicio),
        ('date', '<=', fecha_fin),
        ('state', '=', 'posted'),
        ('payment_type', '=', 'inbound'),
        ('partner_type', '=', 'customer')
    ]
    try:
        pagos = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'account.payment', 'search_read', [domain], {'fields': ['amount']})
        return sum(p['amount'] for p in pagos)
    except Exception as e:
        print(f"❌ Error Motor Global: {e}")
        return 0.0

# ==============================================================================
# MOTOR A: BALANZA 
# ==============================================================================
def _motor_balanza(models, uid, codigo_cuenta, fecha_inicio, fecha_fin, columna_saldo='Debe', excluir_txt=None, incluir_txt=None, palabras_incluidas=None):
    
    codigo_cuenta = str(codigo_cuenta).strip()

    # 🚀 TRUCO DE SUB-CUENTAS: Usamos %=like para atrapar variaciones (ej. 102.01.07.1)
    # Solo si el usuario NO está usando filtros de texto. Si hay filtros, es mejor ser estricto.
    if not (incluir_txt or excluir_txt or palabras_incluidas) and columna_saldo == 'Balance':
        operador = '=like'
        valor_busqueda = f"{codigo_cuenta}%"
    else:
        operador = '=' if len(codigo_cuenta) >= 8 else '=like'
        valor_busqueda = codigo_cuenta if operador == '=' else f"{codigo_cuenta}%"

    # 1. DOMINIO BASE
    domain_base = [
        ('company_id', '=', ODOO_COMPANY_ID), # 🚀 PROTECCIÓN
        ('date', '<=', fecha_fin),
        ('parent_state', '=', 'posted'),
        ('account_id.code', operador, valor_busqueda), 
    ]

    # 🚨 Liberamos las fechas si es un acumulado o balance
    reglas_acumuladas = ['Acumulado_Haber', 'Acumulado_Debe', 'Balance']
    if columna_saldo not in reglas_acumuladas:
        domain_base.append(('date', '>=', fecha_inicio))

    domain_estricto = list(domain_base)
    nomenclatura = None
    
    if incluir_txt and str(incluir_txt).strip():
        nomenclatura = str(incluir_txt).strip()
        domain_estricto.extend([
            '|', '|', 
            ('name', 'ilike', nomenclatura), 
            ('ref', 'ilike', nomenclatura),
            ('move_id.name', 'ilike', nomenclatura)
        ])

    try:
        # 🚀 OPTIMIZACIÓN EXTREMA: Si solo piden Balance sin filtros, usamos read_group
        if columna_saldo == 'Balance' and not (incluir_txt or excluir_txt or palabras_incluidas):
            resultado = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'account.move.line', 'read_group',
                [domain_base],
                {'fields': ['balance'], 'groupby': ['parent_state']} 
            )
            return resultado[0].get('balance', 0.0) if resultado else 0.0

        # --- LÓGICA NORMAL (Con descarga de apuntes para filtros) ---
        apuntes = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'account.move.line', 'search_read', [domain_estricto], {'fields': ['debit', 'credit', 'name', 'ref', 'partner_id', 'account_id', 'move_id']})
        
        if len(apuntes) == 0 and nomenclatura and columna_saldo in reglas_acumuladas:
            print(f"⚠️ Sin resultados para '{nomenclatura}' en cuenta {codigo_cuenta}. Ejecutando respaldo acumulado...")
            apuntes = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'account.move.line', 'search_read', [domain_base], {'fields': ['debit', 'credit', 'name', 'ref', 'partner_id', 'account_id', 'move_id']})

        lista_exclusion = [x.strip().upper() for x in str(excluir_txt).split(',')] if excluir_txt and str(excluir_txt).strip().upper() != 'NONE' else []
        lista_inclusion = [x.strip().upper() for x in str(palabras_incluidas).split(',')] if palabras_incluidas and str(palabras_incluidas).strip().upper() != 'NONE' else []

        neto = 0.0
        
        for a in apuntes:
            account_data = a.get('account_id')
            account_name = account_data[1] if account_data else ''
            codigo_real = account_name.split(' ')[0] if account_name else ''

            # Quitamos el escudo si el operador es '=like'
            if operador == '=' and codigo_real != codigo_cuenta:
                continue

            asiento = str(a.get('move_id')[1]) if a.get('move_id') else ''
            contacto = str(a.get('partner_id')[1]) if a.get('partner_id') else ''
            
            texto_linea = (str(a['name'] or '') + ' ' + str(a['ref'] or '') + ' ' + contacto + ' ' + asiento).upper()
            
            if any(palabra in texto_linea for palabra in lista_exclusion):
                continue 
            if lista_inclusion and not any(palabra in texto_linea for palabra in lista_inclusion):
                continue
            
            # 🧮 CÁLCULO DE COLUMNAS ACTUALIZADO 🧮
            if columna_saldo in ['Solo_Debe', 'Acumulado_Debe']: 
                val = a['debit']
            elif columna_saldo in ['Solo_Haber', 'Acumulado_Haber']: 
                val = a['credit']
            elif columna_saldo == 'Haber':
                val = a['credit'] - a['debit']
            elif columna_saldo == 'Balance':
                val = a['debit'] - a['credit'] 
            else: # Debe (Default)
                val = a['debit'] - a['credit']
            
            neto += val
            
        return neto
    
    except Exception as e:
        print(f"❌ Error Balanza (Cuenta {codigo_cuenta}): {e}")
        return 0.0
   
# ==============================================================================
# MOTOR B: FLUJO ESPECÍFICO 
# ==============================================================================
def _motor_flujo(models, uid, codigo_cuenta, fecha_inicio, fecha_fin, es_ingreso):
    print(f"💸 [Motor Flujo] Cuenta específica {codigo_cuenta}...")
    
    operador = '=like' if len(codigo_cuenta) <= 4 else '='
    
    # 🚀 PROTECCIÓN
    account_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'account.account', 'search', [[
        ('company_id', '=', ODOO_COMPANY_ID),
        ('code', operador, codigo_cuenta + ('%' if operador == '=like' else ''))
    ]])
    
    if not account_ids: return 0.0

    journal_ids = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'account.journal', 'search', [[
        ('company_id', '=', ODOO_COMPANY_ID),
        ('default_account_id', 'in', account_ids)
    ]])

    tipo_pago = 'inbound' if es_ingreso else 'outbound'
    domain = [
        ('company_id', '=', ODOO_COMPANY_ID), # 🚀 PROTECCIÓN
        ('date', '>=', fecha_inicio),
        ('date', '<=', fecha_fin),
        ('state', '=', 'posted'),
        ('payment_type', '=', tipo_pago),
    ]
    
    if journal_ids:
        domain.append(('journal_id', 'in', journal_ids))
    else:
        return _motor_balanza(models, uid, codigo_cuenta, fecha_inicio, fecha_fin)

    try:
        pagos = models.execute_kw(ODOO_DB, uid, ODOO_PASSWORD, 'account.payment', 'search_read', [domain], {'fields': ['amount']})
        return sum(p['amount'] for p in pagos)
    except Exception as e:
        return 0.0