from functools import wraps
from flask import session, flash, redirect, url_for
from db import get_connection
import pymysql

def login_requerido(f):
    """Candado básico: Solo revisa que el usuario haya iniciado sesión."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario_id' not in session:
            flash("Por favor, inicia sesión para continuar.", "warning")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def requiere_permiso(modulo_nombre):
    """Candado avanzado: Revisa si el rol del usuario tiene asignado el módulo."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # 1. ¿Está logueado?
            if 'usuario_id' not in session:
                flash("Por favor, inicia sesión para acceder.", "warning")
                return redirect(url_for('login'))
            
            rol_id = session.get('rol_id')
            
            # 2. "Modo Dios": Si es el Rol 1 (Admin General), entra a todo sin preguntar
            if rol_id == 1:
                return f(*args, **kwargs)

            # 3. Revisar si su rol tiene el permiso en la tabla rol_modulo
            conn = get_connection()
            tiene_acceso = False
            try:
                with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute("""
                        SELECT 1 FROM rol_modulo rm
                        JOIN modulos m ON rm.modulo_id = m.id
                        WHERE rm.rol_id = %s AND m.nombre = %s
                    """, (rol_id, modulo_nombre))
                    if cursor.fetchone():
                        tiene_acceso = True
            finally:
                conn.close()

            # 4. Si no tiene acceso, lo rebotamos al hub
            if not tiene_acceso:
                flash(f"Acceso Denegado: Tu rol no tiene permiso para el módulo '{modulo_nombre}'.", "error")
                return redirect(url_for('hub'))
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator
