import os
import requests
import pymysql
from datetime import datetime
from db import get_connection

ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN")
IG_USER_ID = os.environ.get("META_IG_USER_ID")

def obtener_posts():
    url = f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media"
    params = {
        "fields": "id,timestamp,media_type,media_product_type",
        "access_token": ACCESS_TOKEN,
        "limit": 30  
    }
    try:
        res = requests.get(url, params=params)
        res.raise_for_status()
        data = res.json().get("data", [])
        print(f"Se encontraron {len(data)} posts en Instagram.")
        return data
    except Exception as e:
        print(f"Error al obtener lista de posts: {e}")
        return []

def traducir_tipo_publicacion(media_type, product_type):
    if product_type == "REELS":
        return "Reel de Instagram"
    elif media_type == "CAROUSEL_ALBUM":
        return "Secuencia de Instagram"
    elif media_type == "IMAGE":
        return "Imagen de Instagram"
    elif media_type == "VIDEO":
        return "Video de Instagram"
    return media_type

def obtener_likes_y_comentarios(media_id):
    url = f"https://graph.facebook.com/v19.0/{media_id}"
    params = {
        "fields": "like_count,comments_count",
        "access_token": ACCESS_TOKEN
    }
    try:
        res = requests.get(url, params=params)
        if res.status_code == 200:
            datos = res.json()
            return datos.get("like_count", 0), datos.get("comments_count", 0)
    except Exception as e:
        print(f"Error al obtener interacciones del post {media_id}: {e}")
    return 0, 0

def obtener_estadisticas(media_id, media_type, product_type):
    # ========================================================
    # ¡NUEVAS REGLAS DE META APLICADAS AQUÍ!
    # ========================================================
    if product_type == "REELS" or media_type == "VIDEO":
        # Meta cambió 'plays' por 'views'
        metricas = "reach,views,saved,shares"
    elif media_type == "CAROUSEL_ALBUM":
        # Meta eliminó los prefijos 'carousel_album_'
        metricas = "reach,impressions,saved,shares"
    else: 
        # IMAGE: Meta eliminó 'impressions' para imágenes
        metricas = "reach,saved,shares"

    url = f"https://graph.facebook.com/v19.0/{media_id}/insights"
    params = {
        "metric": metricas,
        "access_token": ACCESS_TOKEN
    }
    
    stats = {"alcance": 0, "visualizaciones": 0, "veces_compartido": 0, "veces_guardado": 0}
    
    try:
        res = requests.get(url, params=params)
        if res.status_code != 200:
            print(f"Meta no otorgó insights para {media_id} ({media_type}): {res.text}")
            return stats
            
        data = res.json().get("data", [])
        for item in data:
            name = item["name"]
            val = item["values"][0]["value"]
            
            # Asignación inteligente basada en lo nuevo que nos manda Meta
            if name == "reach": 
                stats["alcance"] = val
                # Si es una Imagen, usamos el 'alcance' como 'visualizaciones' para no tener ceros
                if media_type == "IMAGE":
                    stats["visualizaciones"] = val
            elif name in ["impressions", "views"]: 
                stats["visualizaciones"] = val
            elif name == "shares": 
                stats["veces_compartido"] = val
            elif name == "saved": 
                stats["veces_guardado"] = val
                
    except Exception as e:
        print(f"Error sacando insights de {media_id}: {e}")
        
    return stats

def sincronizar_bd():
    if not ACCESS_TOKEN or not IG_USER_ID:
        print("Faltan las credenciales de Meta.")
        return

    print("Iniciando sincronización con Instagram...")
    posts = obtener_posts()
    
    if not posts:
        return

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            for post in posts:
                ig_id = post["id"]
                hora_pub = datetime.strptime(post["timestamp"], "%Y-%m-%dT%H:%M:%S%z").strftime("%Y-%m-%d %H:%M:%S")
                
                tipo_meta = post.get("media_type", "")
                product_type = post.get("media_product_type", "FEED") 
                
                tipo_limpio = traducir_tipo_publicacion(tipo_meta, product_type)
                likes, comentarios = obtener_likes_y_comentarios(ig_id)
                stats = obtener_estadisticas(ig_id, tipo_meta, product_type)
                
                sql = """
                    INSERT INTO organic_instagram_performance 
                        (hora_publicacion, identificador_publicacion, tipo_publicacion, 
                         alcance, visualizaciones, me_gusta, comentarios, veces_compartido, seguimientos, veces_guardado, fecha_importacion)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, %s, NOW())
                    ON DUPLICATE KEY UPDATE
                        tipo_publicacion = VALUES(tipo_publicacion),
                        alcance = VALUES(alcance),
                        visualizaciones = VALUES(visualizaciones),
                        me_gusta = VALUES(me_gusta),
                        comentarios = VALUES(comentarios),
                        veces_compartido = VALUES(veces_compartido),
                        veces_guardado = VALUES(veces_guardado),
                        fecha_importacion = NOW()
                """
                valores = (
                    hora_pub, ig_id, tipo_limpio, 
                    stats["alcance"], stats["visualizaciones"], 
                    likes, comentarios, stats["veces_compartido"], stats["veces_guardado"]
                )
                cursor.execute(sql, valores)
                print(f"Post sincronizado -> Tipo: {tipo_limpio} | Alcance: {stats['alcance']} | Visualizaciones: {stats['visualizaciones']}")
            
            conn.commit()
            print("¡Sincronización guardada exitosamente en la base de datos!")
            
    except Exception as e:
        conn.rollback()
        print(f"Error guardando en MySQL: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    sincronizar_bd()
