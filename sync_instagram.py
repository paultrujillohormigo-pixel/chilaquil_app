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
        # ¡EL SECRETO!: Agregamos 'media_product_type' para saber si es Reel o Feed
        "fields": "id,timestamp,media_type,media_product_type,like_count,comments_count",
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
        print(f"Error al obtener posts: {e}")
        return []

def traducir_tipo_publicacion(media_type, product_type):
    """Traduce correctamente tomando en cuenta si es Reel o Video Normal"""
    if product_type == "REELS":
        return "Reel de Instagram"
    elif media_type == "CAROUSEL_ALBUM":
        return "Secuencia de Instagram"
    elif media_type == "IMAGE":
        return "Imagen de Instagram"
    elif media_type == "VIDEO" and product_type == "FEED":
        return "Video de Instagram" # Era un video viejo normal, no un Reel
    return media_type

def obtener_estadisticas(media_id, media_type, product_type):
    # Pedimos métricas según el tipo de producto exacto
    if product_type == "REELS":
        metricas = "reach,plays,saved,shares"
    elif media_type == "CAROUSEL_ALBUM":
        metricas = "carousel_album_reach,carousel_album_impressions,carousel_album_saved"
    else: 
        # Para Imágenes y Videos normales del Feed
        metricas = "reach,impressions,saved"

    url = f"https://graph.facebook.com/v19.0/{media_id}/insights"
    params = {
        "metric": metricas,
        "access_token": ACCESS_TOKEN
    }
    
    stats = {"alcance": 0, "visualizaciones": 0, "veces_compartido": 0, "veces_guardado": 0}
    
    try:
        res = requests.get(url, params=params)
        
        # Si Meta se sigue quejando por alguna razón, ahora nos lo dirá en los logs
        if res.status_code != 200:
            print(f"Meta rechazó insights de {media_id} ({media_type}/{product_type}): {res.text}")
            return stats
            
        data = res.json().get("data", [])
        for item in data:
            name = item["name"]
            val = item["values"][0]["value"]
            
            if name in ["reach", "carousel_album_reach"]: 
                stats["alcance"] = val
            elif name in ["impressions", "plays", "carousel_album_impressions"]: 
                stats["visualizaciones"] = val
            elif name == "shares": 
                stats["veces_compartido"] = val
            elif name in ["saved", "carousel_album_saved"]: 
                stats["veces_guardado"] = val
                
    except Exception as e:
        print(f"Error sacando insights de {media_id}: {e}")
        
    return stats

def sincronizar_bd():
    if not ACCESS_TOKEN or not IG_USER_ID:
        print("Faltan las credenciales.")
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
                
                # Rescatamos el tipo de media y el tipo de producto (Reel, Feed, etc.)
                tipo_meta = post.get("media_type", "")
                product_type = post.get("media_product_type", "FEED") 
                
                # Ahora sí, la traducción será exacta
                tipo_limpio = traducir_tipo_publicacion(tipo_meta, product_type)
                
                likes = post.get("like_count", 0)
                comentarios = post.get("comments_count", 0)
                
                # Pedimos las estadísticas pasando todos los datos
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
                print(f"Post {ig_id} actualizado -> Tipo: {tipo_limpio}, Likes: {likes}, Alcance: {stats['alcance']}")
            
            conn.commit()
            print("¡Sincronización guardada exitosamente en la base de datos!")
            
    except Exception as e:
        conn.rollback()
        print(f"Error guardando en MySQL: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    sincronizar_bd()
