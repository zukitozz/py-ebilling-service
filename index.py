import pyodbc
import time
import os
import requests
import threading
from dotenv import load_dotenv
from escpos.printer import Usb
import json

# Cargar configuración
base_path = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(base_path, '.env'))

lock_impresora = threading.Lock()

EMP_CONFIG = {
    'rs': os.getenv('RS'),
    'ruc': os.getenv('RUC'),
    'ubigeo': os.getenv('UBIGEO'),
    'direccion': os.getenv('EMISOR_DIR'),
    'nc': ''
}
# --- CONFIGURACIÓN DE IMPRESORA (EPSON TM-T88V) ---
# Verifica estos IDs en el administrador de dispositivos
# Convertir IDs de hex string a entero
ID_VENDOR = int(os.getenv('USB_VENDOR_ID'), 16)
ID_PRODUCT = int(os.getenv('USB_PRODUCT_ID'), 16)
# --- CONFIGURACIÓN ---
INTERVALO_SUNAT = int(os.getenv('INTERVALO_SUNAT', 60))    # Cada 1 min
INTERVALO_IMPRESION = int(os.getenv('INTERVALO_IMPRESION', 30)) # Cada 30 seg
ISLAS = os.getenv('ISLAS')
ISLA_CIERRE = os.getenv('ISLA_CIERRE')
# --- CONFIGURACIÓN MIFACT ---
MIFACT_API_URL = os.getenv('MIFACT_API')
MIFACT_TOKEN = os.getenv('MIFACT_TOKEN')

def enviar_a_mifact(comprobante, items, receptor):
    try:
        # Split de numeración (F001-0001 -> ['F001', '0001'])
        serie_correlativo = comprobante.numeracion_comprobante.split("-")
        serie = serie_correlativo[0]
        correlativo = serie_correlativo[1]

        # Preparar Items
        placa_str = f"| PLACA: {comprobante.placa.upper()}" if comprobante.placa else ""
        split_afectado = comprobante.numeracion_documento_afectado.split("-") if comprobante.numeracion_documento_afectado else []

        arr_items = []
        for item in items:
            arr_items.append({
                "COD_ITEM": "BCF-RR01",
                "COD_UNID_ITEM": item.medida if hasattr(item, 'medida') and item.medida else "GLL",
                "CANT_UNID_ITEM": float(item.cantidad),
                "VAL_UNIT_ITEM": float(item.valor),
                "PRC_VTA_UNIT_ITEM": float(item.precio),
                "VAL_VTA_ITEM": "{:.2f}".format(float(item.valor_venta)),
                "MNT_BRUTO": "{:.2f}".format(float(item.valor_venta)),
                "MNT_PV_ITEM": float(item.precio_venta),
                "COD_TIP_PRC_VTA": "01",
                "COD_TIP_AFECT_IGV_ITEM": "10",
                "COD_TRIB_IGV_ITEM": "1000",
                "POR_IGV_ITEM": "18",
                "MNT_IGV_ITEM": float(item.igv_venta),
                "TXT_DESC_ITEM": f"{item.descripcion}{placa_str}",
                "DET_VAL_ADIC01": "", "DET_VAL_ADIC02": "", "DET_VAL_ADIC03": "", "DET_VAL_ADIC04": ""
            })
        
        # Cuerpo de la petición (Payload)
        body = {
            "TOKEN": MIFACT_TOKEN, # Asegúrate que no sea None
            "COD_TIP_NIF_EMIS": "6",
            "NUM_NIF_EMIS": EMP_CONFIG['ruc'],
            "NOM_RZN_SOC_EMIS": EMP_CONFIG['rs'],
            "NOM_COMER_EMIS": EMP_CONFIG['nc'] or "",
            "COD_UBI_EMIS": EMP_CONFIG['ubigeo'],
            "TXT_DMCL_FISC_EMIS": EMP_CONFIG['direccion'],
            "COD_TIP_NIF_RECP": str(receptor.tipo_documento),
            "NUM_NIF_RECP": str(receptor.numero_documento),
            "NOM_RZN_SOC_RECP": receptor.razon_social,
            "TXT_DMCL_FISC_RECEP": receptor.direccion or "",
            "FEC_EMIS": str(comprobante.fecha_emision),
            "FEC_VENCIMIENTO": "",
            "COD_TIP_CPE": comprobante.tipo_comprobante,
            "NUM_SERIE_CPE": serie,
            "NUM_CORRE_CPE": correlativo,
            "COD_MND": "PEN",
            "MailEnvio": getattr(receptor, 'correo', '') or '',
            "COD_PRCD_CARGA": "001",
            "MNT_TOT_GRAVADO": float(comprobante.gravadas),
            "MNT_TOT_TRIB_IGV": float(comprobante.igv),
            "MNT_TOT": float(comprobante.total),
            "COD_PTO_VENTA": "jmifact",
            "ENVIAR_A_SUNAT": "true",
            "RETORNA_XML_ENVIO": "false",
            "RETORNA_XML_CDR": "false",
            "RETORNA_PDF": "true",
            "COD_FORM_IMPR": "001",
            "TXT_VERS_UBL": "2.1",
            "TXT_VERS_ESTRUCT_UBL": "2.0",
            "COD_ANEXO_EMIS": "0000",
            "COD_TIP_OPE_SUNAT": "0101",
            "items": arr_items,
            "docs_referenciado": [
                {
                      "COD_TIP_DOC_REF": comprobante.tipo_documento_afectado if comprobante.tipo_documento_afectado else "",
                      "NUM_SERIE_CPE_REF": split_afectado[0] if comprobante.tipo_comprobante in ('07', '08') and len(split_afectado) > 0 else "",
                      "NUM_CORRE_CPE_REF": split_afectado[1] if comprobante.tipo_comprobante in ('07', '08') and len(split_afectado) > 0 else "",
                      "FEC_DOC_REF": comprobante.fecha_documento_afectado if comprobante.tipo_comprobante in ('07', '08') else "",
                }
          ]            
        }

        # Manejo de Nota de Crédito (Referencia)
        if comprobante.tipo_comprobante == "07":
            body["COD_TIP_NC"] = "01"
            body["TXT_DESC_MTVO"] = "anulacion de comprobante"
            # Aquí deberías agregar la lógica de docs_referenciado si existen en tu DB

        response = requests.post(MIFACT_API_URL, json=body, timeout=30)
        response.raise_for_status()
        data = response.json()

        if data["errors"] == "":
            print(f"[{comprobante.numeracion_comprobante}] Enviado con éxito.")
            
        else:
            print(f"[{comprobante.numeracion_comprobante}] API respondió con errores.")

        retorno = { "xml": json.dumps(body), "response": data}

        return retorno

    except Exception as e:
        print(f"Error en peticion API MiFact: {e}")
        return None

def obtener_conexion():
    return pyodbc.connect(
        f"DRIVER={os.getenv('DB_DRIVER')};SERVER={os.getenv('DB_SERVER')};"
        f"DATABASE={os.getenv('DB_DATABASE')};UID={os.getenv('DB_USER')};PWD={os.getenv('DB_PASSWORD')}"
    )

# --- PROCESO 1: FACTURACIÓN ELECTRÓNICA (MIFACT) ---
def proceso_mifact():
    print(f"🚀 Hilo de MIFACT iniciado (Cada {INTERVALO_SUNAT}s)")
    while True:
        conn = None
        try:
            conn = obtener_conexion()
            cursor = conn.cursor()
            
            # Buscar comprobantes NO enviados (enviado = 0)
            cursor.execute(f"SELECT top 50 * FROM Comprobantes WHERE enviado = 0")
            pendientes = cursor.fetchall()

            for comp in pendientes:
                print(f"☁️ Enviando a MiFact: {comp.numeracion_comprobante}")
                
                # Obtener datos para el JSON
                cursor.execute("SELECT * FROM Receptores WHERE id = ?", comp.ReceptorId)
                receptor = cursor.fetchone()
                cursor.execute("SELECT * FROM Items WHERE ComprobanteId = ?", comp.id)
                items = cursor.fetchall()

                api_mifact = enviar_a_mifact(comp, items, receptor)
                print("REspuesta de mifact")

                response = api_mifact["response"]
                xml = api_mifact["xml"]
                
                if response["errors"]:
                    cursor.execute("""
                        UPDATE Comprobantes 
                        SET enviado = 1, errors = ?, xml_envio = ? 
                        WHERE id = ?
                    """, (response["errors"], xml, comp.id))
                else:
                    cursor.execute("""
                        UPDATE Comprobantes 
                        SET enviado = 1, codigo_hash = ?, url = ?, cadena_para_codigo_qr = ?, xml_envio = ? 
                        WHERE id = ?
                    """, (response["codigo_hash"], response["url"], response["cadena_para_codigo_qr"], xml, comp.id))                    

                conn.commit()

                print(f"✅ {comp.numeracion_comprobante} marcado como ENVIADO.")

        except Exception as e:
            print(f"❌ Error en hilo MiFact: {e}")
        finally:
            if conn: conn.close()
        
        time.sleep(INTERVALO_SUNAT)

    # Lógica similar a la anterior pero consolidando datos del día
    p = None
    try:
        p = Usb(ID_VENDOR, ID_PRODUCT, profile="TM-T88V")
        p.set(align='center', bold=True, width=2, height=2)
        p.text("CIERRE DIARIO\n")
        p.set(bold=False, width=1, height=1)
        p.text(f"FECHA: {dia.fecha}\n")
        p.text("-" * 40 + "\n")
        p.set(align='right', bold=True)
        p.text(f"TOTAL DEL DIA: S/ {dia.total:.2f}\n")
        p.cut()

        if p.device:
            from usb.util import dispose_resources
            dispose_resources(p.device)        
        return True
    except Exception as e:
        print(f"Error imprimir cierre dia: {e}")
        return False
    finally:
        if p: del p
        time.sleep(1) 
# --- LANZADOR PRINCIPAL ---
if __name__ == "__main__":
    print("--- INICIANDO SERVICIOS MULTI-HILO ---")
    
    # Crear los hilos
    hilo_sunat = threading.Thread(target=proceso_mifact, name="HiloSunat")
    # Iniciar los hilos
    hilo_sunat.start()

    # Mantener el programa principal vivo
    hilo_sunat.join()