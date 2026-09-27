from flask import Blueprint, render_template
import db

# Importamos el candado desde tu archivo de seguridad
from auth import requiere_permiso

cocina_bp = Blueprint('cocina', __name__)

# 1. Ruta para ver el índice (La lista de todos los platillos)
@cocina_bp.route('/recetario')
@requiere_permiso("cocina")
def index_recetario():
    conexion = db.get_connection()
    cursor = conexion.cursor() # Tu db.py ya lo hace DictCursor automáticamente
    
    # Traemos todos los platillos ordenados alfabéticamente
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
@requiere_permiso("cocina")
def ver_ficha(platillo_id):
    conexion = db.get_connection()
    cursor = conexion.cursor()
    
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
