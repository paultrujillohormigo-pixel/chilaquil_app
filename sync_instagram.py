import os
import requests
import pymysql
from datetime import datetime
from db import get_connection

# === CREDENCIALES DESDE RAILWAY ===
ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN")
AD_ACCOUNT_ID = os.environ.get("META_AD_ACCOUNT_ID") # Ejemplo: act_123456789
# ==================================

def obtener_insights_pago():
    if not AD_ACCOUNT_ID.startswith("act_"):
        # Nos aseguramos de que tenga el prefijo correcto que exige Meta
        cuenta_id = f"act_{AD_ACCOUNT_ID}"
    else:
        cuenta_id = AD_ACCOUNT_ID

    url = f"https://graph.facebook.com/v19.0/{cuenta_id}/insights"
    
    params = {
        "level": "ad",  # Trae los datos detallados a nivel de anuncio
        "fields": "date_start,campaign_id,campaign_name,ad_name,reach,impressions,frequency,spend",
        "time_increment": 1,  # ¡CLAVE! Nos desglosa la info día por día
        "date_preset": "last_30d",  # Revisa los últimos 30 días para actualizar cambios de atribución
        "access_token": ACCESS_TOKEN,
        "limit": 150
    }
    
    lista_insights = []
    
    try:
        print(f"Consultando la API de Marketing para la cuenta {cuenta_id}...")
        res = requests.get(url, params=params)
        res.raise_for_status()
        
        datos_json = res.json()
        lista_insights.extend(datos_json.get("data", []))
        
        # Lógica de paginación por si tienes muchísimos anuncios circulando
        while "paging" in datos_json and "next" in datos_json["paging"]:
            res = requests.get(datos_json["paging"]["next"])
            res.raise_for_status()
            datos_json = res.json()
            lista_insights.extend(datos_json.get("data", []))
            
        print(f"Se obtuvieron {len(lista_insights)} registros diarios de rendimiento pagado.")
        return lista_insights

    except Exception as e:
        print(f"Error al obtener insights de pago: {e}")
        return []

def sincronizar_bd_pago():
    # Print de diagnóstico temporal
    print(f"[DIAGNÓSTICO] TOKEN encontrado: {'SÍ' if ACCESS_TOKEN else 'NO'}")
    print(f"[DIAGNÓSTICO] AD_ACCOUNT_ID encontrado: {'SÍ' if AD_ACCOUNT_ID else 'NO'}")

    if not ACCESS_TOKEN or not AD_ACCOUNT_ID:
        print("Faltan las credenciales de Meta Ads en las variables de entorno.")
        return

    records = obtener_insights_pago()
    if not records:
        print("No hay datos de pago para procesar.")
        return

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            for item in records:
                # Meta nos entrega strings, los formateamos al tipo de dato de tu MySQL
                dia = item.get("date_start")
                campana_id = item.get("campaign_id")
                campana_nombre = item.get("campaign_name")
                anuncio_nombre = item.get("ad_name")
                
                alcance = int(item.get("reach", 0))
                impresiones = int(item.get("impressions", 0))
                frecuencia = float(item.get("frequency", 0.00))
                importe_gastado = float(item.get("spend", 0.00))
                
                # SQL adaptado exactamente a las columnas de tu nueva tabla
                sql = """
                    INSERT INTO tu_nombre_de_tabla_pagada 
                        (dia, identificador_campana, nombre_campana, nombre_anuncio, 
                         alcance, impresiones, frecuencia, importe_gastado, fecha_importacion)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON DUPLICATE KEY UPDATE
                        nombre_campana = VALUES(nombre_campana),
                        nombre_anuncio = VALUES(nombre_anuncio),
                        alcance = VALUES(alcance),
                        impresiones = VALUES(impresiones),
                        frecuencia = VALUES(frecuencia),
                        importe_gastado = VALUES(importe_gastado),
                        fecha_importacion = NOW()
                """
                
                valores = (dia, campana_id, campana_nombre, anuncio_nombre, alcance, impresiones, frecuencia, importe_gastado)
                cursor.execute(sql, valores)
            
            conn.commit()
            print("¡Sincronización de campañas de pago guardada exitosamente!")
            
    except Exception as e:
        conn.rollback()
        print(f"Error guardando datos de pago en MySQL: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    sincronizar_bd_pago()
