from flask import Blueprint, render_template
import db # Veo que tienes un archivo db.py, asumo que ahí manejas la conexión

# Creamos el blueprint
cocina_bp = Blueprint('cocina', __name__)

@cocina_bp.route('/ficha-tecnica/<int:platillo_id>')
def ver_ficha(platillo_id):
    conexion = db.obtener_conexion() # Ajusta esto a la función que uses en db.py
    cursor = conexion.cursor(dictionary=True) # Depende de tu conector (pymysql o mysql-connector)
    
    # 1. Traer los datos generales del platillo
    cursor.execute("SELECT * FROM platillos WHERE id = %s", (platillo_id,))
    platillo = cursor.fetchone()
    
    # 2. Traer los ingredientes de la receta
    query_ingredientes = """
        SELECT i.nombre, r.cantidad_base, i.unidad_base 
        FROM recetas r
        JOIN insumos i ON r.insumo_id = i.id
        WHERE r.platillo_id = %s
    """
    cursor.execute(query_ingredientes, (platillo_id,))
    ingredientes = cursor.fetchall()
    
    cursor.close()
    conexion.close()
    
    return render_template('ficha_tecnica.html', platillo=platillo, ingredientes=ingredientes)
