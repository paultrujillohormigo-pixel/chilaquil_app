import requests
import pymysql
from datetime import datetime
from db import get_connection # Reutilizamos tu conexión

# === TUS CREDENCIALES DE META ===
ACCESS_TOKEN = "AQUI_TU_TOKEN_LARGO_DE_META"
IG_USER_ID = "AQUI_TU_IG_USER_ID"
# ================================

def obtener_posts():
    url = f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media"
    params = {
        "fields": "id,timestamp,media_type",
        "access_token": ACCESS_TOKEN,
        "limit": 10 # Jalamos los últimos 10 posts para actualizar
    }
    
    try:
        res = requests.get(url, params=params)
        res.raise_for_status()
        return res.json().get("data", [])
    except Exception as e:
        print(f"Error al obtener posts: {e}")
        return []

def obtener_estadisticas(media_id, media_type):
    # Meta pide métricas distintas si es Imagen o Reel
    if media_type == "REELS_V2" or media_type == "VIDEO":
        metricas = "reach,plays,likes,comments,shares,saved"
    else:
        metricas = "reach,impressions,likes,comments,shares,saved"

    url = f"https://graph.facebook.com/v19.0/{media_id}/insights"
    params = {
        "metric": metricas,
        "access_token": ACCESS_TOKEN
    }
    
    stats = {
        "alcance": 0, "visualizaciones": 0, "me_gusta": 0, 
        "comentarios": 0, "veces_compartido": 0, "veces_guardado": 0
    }
    
    try:
        res = requests.get(url, params=params)
        data = res.json().get("data", [])
        
        for item in data:
            name = item["name"]
            val = item["values"][0]["value"]
            if name == "reach": stats["alcance"] = val
            elif name in ["impressions", "plays"]: stats["visualizaciones"] = val
            elif name == "likes": stats["me_gusta"] = val
            elif name == "comments": stats["comentarios"] = val
            elif name == "shares": stats["veces_compartido"] = val
            elif name == "saved": stats["veces_guardado"] = val
            
    except Exception as e:
        print(f"Error sacando insights de {media_id}: {e}")
        
    return stats

def sincronizar_bd():
    print("Iniciando sincronización con Instagram...")
    posts = obtener_posts()
    
    if not posts:
        print("No se encontraron posts.")
        return

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            for post in posts:
                ig_id = post["id"]
                hora_pub = datetime.strptime(post["timestamp"], "%Y-%m-%dT%H:%M:%S%z").strftime("%Y-%m-%d %H:%M:%S")
                tipo = post["media_type"]
                
                print(f"Procesando post {ig_id} ({tipo})...")
                stats = obtener_estadisticas(ig_id, tipo)
                
                # Insertamos o actualizamos (ON DUPLICATE KEY UPDATE para no duplicar si corres el script diario)
                # Asumimos que identificador_publicacion es UNIQUE en tu tabla
                sql = """
                    INSERT INTO organic_instagram_performance 
                        (hora_publicacion, identificador_publicacion, tipo_publicacion, 
                         alcance, visualizaciones, me_gusta, comentarios, veces_compartido, veces_guardado, fecha_importacion)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON DUPLICATE KEY UPDATE
                        alcance = VALUES(alcance),
                        visualizaciones = VALUES(visualizaciones),
                        me_gusta = VALUES(me_gusta),
                        comentarios = VALUES(comentarios),
                        veces_compartido = VALUES(veces_compartido),
                        veces_guardado = VALUES(veces_guardado),
                        fecha_importacion = NOW()
                """
                
                valores = (
                    hora_pub, ig_id, tipo,
                    stats["alcance"], stats["visualizaciones"], stats["me_gusta"], 
                    stats["comentarios"], stats["veces_compartido"], stats["veces_guardado"]
                )
                
                cursor.execute(sql, valores)
                
            conn.commit()
            print("Sincronización exitosa padrino.")
            
    except Exception as e:
        conn.rollback()
        print(f"Error guardando en MySQL: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    sincronizar_bd()
