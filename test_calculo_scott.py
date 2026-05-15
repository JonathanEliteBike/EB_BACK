import sys
import os
import traceback  # <--- Agregamos esto para ver los logs reales
from decimal import Decimal

# 1. Agregamos la raíz al path para que encuentre db_conexion.py
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 2. Única importación, con logs detallados del error
try:
    from db_conexion import obtener_conexion
except Exception as e:
    print("\n❌ ERROR FATAL AL IMPORTAR db_conexion.py ❌")
    print("El archivo db_conexion.py SÍ se encontró, pero algo falló en su interior.")
    print("Este es el log del error real:")
    print("-" * 60)
    traceback.print_exc()  # <--- Esto nos dará el error exacto en consola
    print("-" * 60)
    sys.exit(1)

def es_bicicleta_scott(factura):
    """
    Replica EXACTAMENTE la lógica de previo.component.ts para Avance Global SCOTT (Bicicletas).
    """
    marca = str(factura.get('marca') or '').strip().upper()
    subcategoria = str(factura.get('subcategoria') or '').strip().upper()
    apparel = str(factura.get('apparel') or '').strip().upper()
    nombre_producto = str(factura.get('nombre_producto') or '').strip().upper()

    es_marca_valida = marca in ['SCOTT', 'MEGAMO']
    contiene_bicicleta = 'BICICLETA' in nombre_producto
    
    es_bicicleta = (subcategoria == 'BICICLETA') or contiene_bicicleta
    es_apparel_no = (apparel == 'NO') or contiene_bicicleta

    es_valido = es_marca_valida and es_bicicleta and es_apparel_no
    return es_valido

def probar_calculo_cliente(busqueda):
    conexion = obtener_conexion()
    
    # 3. Validación crucial: Si no hay conexión, detenemos la ejecución
    if conexion is None:
        print(f"\n🚫 No se pudo establecer la conexión a MySQL.")
        return

    cursor = conexion.cursor(dictionary=True)
    
    try:
        # Buscar todas las facturas del cliente
        query = """
            SELECT nombre_cliente, contacto_referencia, num_factura, nombre_producto, 
                   marca, subcategoria, apparel, venta_total, fecha_factura
            FROM monitor
            WHERE nombre_cliente LIKE %s OR contacto_referencia LIKE %s
        """
        like_str = f"%{busqueda}%"
        cursor.execute(query, (like_str, like_str))
        facturas = cursor.fetchall()
        
        if not facturas:
            print(f"No se encontraron facturas para: {busqueda}")
            return

        print(f"\n=======================================================")
        print(f"ANALIZANDO FACTURAS PARA: {busqueda}")
        print(f"=======================================================\n")

        total_scott = Decimal('0.0')
        facturas_validas = []
        facturas_rechazadas = []

        for f in facturas:
            # Limpiamos posibles comas antes de convertir a Decimal
            venta_str = str(f.get('venta_total') or '0').replace(',', '')
            venta = Decimal(venta_str)
            
            if es_bicicleta_scott(f):
                total_scott += venta
                facturas_validas.append(f)
            else:
                # Solo guardamos las rechazadas de SCOTT/MEGAMO para ver por qué se rechazaron
                marca = str(f.get('marca') or '').upper()
                if marca in ['SCOTT', 'MEGAMO']:
                    facturas_rechazadas.append(f)

        print(f"✅ FACTURAS APROBADAS COMO BICICLETA SCOTT/MEGAMO: {len(facturas_validas)}")
        print("-" * 120)
        print(f"{'FACTURA':<15} | {'PRODUCTO':<50} | {'MARCA':<10} | {'SUBCAT':<15} | {'APPAREL':<8} | {'MONTO':<15}")
        print("-" * 120)
        for f in facturas_validas:
            prod = str(f['nombre_producto'])[:48]
            print(f"{f['num_factura']:<15} | {prod:<50} | {f['marca']:<10} | {f['subcategoria']:<15} | {f['apparel']:<8} | ${f['venta_total']:,.2f}")

        print(f"\n\n❌ FACTURAS RECHAZADAS (Eran Scott/Megamo pero fallaron la regla): {len(facturas_rechazadas)}")
        print("-" * 120)
        print(f"{'FACTURA':<15} | {'PRODUCTO':<50} | {'MARCA':<10} | {'SUBCAT':<15} | {'APPAREL':<8} | {'MONTO':<15}")
        print("-" * 120)
        for f in facturas_rechazadas:
            prod = str(f['nombre_producto'])[:48]
            print(f"{f['num_factura']:<15} | {prod:<50} | {f['marca']:<10} | {f['subcategoria']:<15} | {f['apparel']:<8} | ${f['venta_total']:,.2f}")

        print(f"\n=======================================================")
        print(f"💰 SUMA TOTAL DE BICICLETAS (Avance Global Scott): ${total_scott:,.2f}")
        print(f"=======================================================\n")

    finally:
        # Se cierra el cursor y la conexión independientemente de si el código funcionó o falló
        if cursor:
            cursor.close()
        if conexion:
            conexion.close()

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Uso: python tests/test_calculo_scott.py \"NOMBRE DEL CLIENTE O CLAVE\"")
        print("Ejemplo: python tests/test_calculo_scott.py \"KA578\"")
    else:
        probar_calculo_cliente(sys.argv[1])