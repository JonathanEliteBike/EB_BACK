import os

# GUÍA: código numérico compartido que Auditoría usa para validar notas de
# crédito (Banca y Pagos las captura, pero solo Auditoría puede validarlas).
# Vive en una env var para poder cambiarlo sin tocar código -- valor por
# defecto '1234' mientras Auditoría define el código real de producción.
def verificar_codigo_auditoria(codigo):
    pin_real = os.getenv('AUDITORIA_NC_PIN', '1234')
    return str(codigo or '').strip() == pin_real
