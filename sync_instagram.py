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
        # ¡EL CAMBIO!: Pedimos like_count y comments_count desde aquí
        "fields": "id,timestamp,media_type,like_count,comments_count",
        "access_token": ACCESS_TOKEN,
        "limit": 10
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

def obtener_estadisticas(media_id, media_type):
    # Ajustamos las métricas a lo que Meta realmente permite por tipo de post
    if media_type in ["REELS_V2", "VIDEO"]:
        metricas = "reach,plays,saved,shares"
    else:
        # Las imágenes no soportan "shares" ni likes/comments por esta vía
        metricas = "reach,impressions,saved"

    url = f"https://graph.facebook.com/v19.0/{media_id}/insights"
    params = {
        "metric": metricas,
        "access_token": ACCESS_TOKEN
    }
    
    stats = {"alcance": 0, "visualizaciones": 0, "veces_compartido": 0, "veces_guardado": 0}
    
    try:
        res = requests.get(url, params=params)
        
        # Si Meta arroja un error en los insights, ahora lo veremos en los logs
        if res.status_code != 200:
            print(f"Alerta de Meta en Insights del post {media_id}: {res.text}")
            return stats

        data = res.json().get("data", [])
        for item in data:
            name = item["name"]
            val = item["values"][0]["value"]
            if name == "reach": stats["alcance"] = val
            elif name in ["impressions", "plays"]: stats["visualizaciones"] = val
            elif name == "shares": stats["veces_compartido"] = val
            elif name == "saved": stats["veces_guardado"] = val
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
        print("No hay posts para procesar.")
        return

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            for post in posts:
                ig_id = post["id"]
                hora_pub = datetime.strptime(post["timestamp"], "%Y-%m-%dT%H:%M:%S%z").strftime("%Y-%m-%d %H:%M:%S")
                tipo = post["media_type"]
                
                # Extraemos likes y comentarios que ahora vienen en el post principal
                likes = post.get("like_count", 0)
                comentarios = post.get("comments_count", 0)
                
                # Buscamos el resto de las estadísticas
                stats = obtener_estadisticas(ig_id, tipo)
                
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
                    stats["alcance"], stats["visualizaciones"], 
                    likes, comentarios, stats["veces_compartido"], stats["veces_guardado"]
                )
                cursor.execute(sql, valores)
                print(f"Post {ig_id} - Likes: {likes}, Alcance: {stats['alcance']}")
            
            conn.commit()
            print("¡Sincronización guardada exitosamente en la base de datos!")
            
    except Exception as e:
        conn.rollback()
        print(f"Error guardando en MySQL: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    sincronizar_bd()
