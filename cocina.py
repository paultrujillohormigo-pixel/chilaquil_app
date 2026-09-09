from flask import Blueprint, render_template
import db # Asegúrate de que este import coincida con cómo llamas a tu base de datos

cocina_bp = Blueprint('cocina', __name__)

# 1. Ruta para ver el índice (La lista de todos los platillos)
@cocina_bp.route('/recetario')
def index_recetario():
    conexion = db.obtener_conexion()
    cursor = conexion.cursor(dictionary=True)
    
    # Traemos todos los platillos ordenados alfabéticamente
    # Si hiciste el ALTER TABLE, aquí ya traemos la imagen y el tiempo
    cursor.execute("""
        SELECT id, nombre, imagen_url, tiempo_prep_min 
        FROM platillos 
        ORDER BY nombre ASC
    """)
    platillos = cursor.fetchall()
    
    cursor.close()
    conexion.close()
    
    return render_template('recetario_index.html', platillos=platillos)

# 2. Ruta para ver la ficha técnica de un platillo en específico
@cocina_bp.route('/recetario/ficha/<int:platillo_id>')
def ver_ficha(platillo_id):
    conexion = db.obtener_conexion()
    cursor = conexion.cursor(dictionary=True)
    
    # Datos generales del platillo
    cursor.execute("SELECT * FROM platillos WHERE id = %s", (platillo_id,))
    platillo = cursor.fetchone()
    
    # Ingredientes de la receta principal
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
