import os
import pymysql

def get_connection():
    conn = pymysql.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", 3306)),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        cursorclass=pymysql.cursors.DictCursor,
        charset="utf8mb4",
        use_unicode=True,
        autocommit=False,
    )

    # CLAVE: forzar charset/collation por sesión
    with conn.cursor() as cur:
        cur.execute("SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci;")
        cur.execute("SET character_set_client = utf8mb4;")
        cur.execute("SET character_set_connection = utf8mb4;")
        cur.execute("SET character_set_results = utf8mb4;")

    return conn
