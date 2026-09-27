from flask import Blueprint, render_template, request, redirect, url_for, flash
from werkzeug.security import generate_password_hash
import pymysql

# Usamos get_connection de db.py igual que en todos tus módulos
from db import get_connection

# IMPORTAMOS EL CANDADO DESDE auth.py
from auth import requiere_permiso

seguridad_bp = Blueprint("seguridad_bp", __name__, url_prefix="/seguridad")

# =========================================================
# ================== GESTIÓN DE USUARIOS ==================
# =========================================================

@seguridad_bp.route("/usuarios", methods=["GET", "POST"])
@requiere_permiso("seguridad") # Este permiso solo lo tienes tú (Administrador General)
def gestion_usuarios():
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            # POST: Procesamos la creación de un nuevo usuario
            if request.method == "POST":
                nombre = request.form.get("nombre", "").strip()
                username = request.form.get("username", "").strip().lower()
                password = request.form.get("password", "")
                rol_id = request.form.get("rol_id")

                if not nombre or not username or not password or not rol_id:
                    flash("Todos los campos son obligatorios.", "error")
                else:
                    # ENCRIPTAMOS LA CONTRASEÑA (Regla de Oro)
                    hashed_pw = generate_password_hash(password)
                    try:
                        cursor.execute("""
                            INSERT INTO usuarios (nombre, username, password_hash, rol_id)
                            VALUES (%s, %s, %s, %s)
                        """, (nombre, username, hashed_pw, rol_id))
                        conn.commit()
                        flash(f"Usuario '{nombre}' creado exitosamente.", "success")
                    except pymysql.err.IntegrityError:
                        flash(f"El usuario '{username}' ya existe. Por favor elige otro.", "error")
                
                return redirect(url_for("seguridad_bp.gestion_usuarios"))

            # GET: Mostramos la tabla de usuarios actuales y el formulario
            cursor.execute("""
                SELECT u.id, u.nombre, u.username, u.activo, r.nombre as rol
                FROM usuarios u
                LEFT JOIN roles r ON u.rol_id = r.id
                ORDER BY u.nombre
            """)
            usuarios = cursor.fetchall()

            # Para llenar el select de Roles en el formulario
            cursor.execute("SELECT id, nombre FROM roles ORDER BY nombre")
            roles = cursor.fetchall()
            
    finally:
        conn.close()

    return render_template("admin/admin_usuarios.html", usuarios=usuarios, roles=roles)

# Ruta rápida para desactivar (borrar lógicamente) a un usuario
@seguridad_bp.route("/usuarios/<int:usuario_id>/desactivar", methods=["POST"])
@requiere_permiso("seguridad")
def desactivar_usuario(usuario_id):
    # Por seguridad, evitamos que el admin se borre a sí mismo (asumiendo que tú eres el ID 1)
    if usuario_id == 1:
        flash("No puedes desactivar a tu propio usuario Administrador Principal.", "error")
        return redirect(url_for("seguridad_bp.gestion_usuarios"))

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE usuarios SET activo = 0 WHERE id = %s", (usuario_id,))
            conn.commit()
            flash("Usuario desactivado. Ya no podrá iniciar sesión.", "success")
    finally:
        conn.close()
    
    return redirect(url_for("seguridad_bp.gestion_usuarios"))


# =========================================================
# ================== GESTIÓN DE ROLES Y PERMISOS ==========
# =========================================================

@seguridad_bp.route("/roles", methods=["GET", "POST"])
@requiere_permiso("seguridad")
def gestion_roles():
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            # POST: Procesar los checkboxes de la matriz de permisos
            if request.method == "POST":
                rol_id = request.form.get("rol_id")
                # Obtenemos los modulos que el admin "palomeó" para este rol
                modulos_seleccionados = request.form.getlist("modulos[]")

                if str(rol_id) == "1":
                    flash("El Rol de 'Administrador General' tiene acceso a todo por defecto y no puede ser modificado.", "warning")
                    return redirect(url_for("seguridad_bp.gestion_roles"))

                # 1. Borramos los permisos actuales de este rol específico
                cursor.execute("DELETE FROM rol_modulo WHERE rol_id = %s", (rol_id,))
                
                # 2. Insertamos los nuevos permisos seleccionados
                if modulos_seleccionados:
                    insert_data = [(rol_id, mod_id) for mod_id in modulos_seleccionados]
                    cursor.executemany("INSERT INTO rol_modulo (rol_id, modulo_id) VALUES (%s, %s)", insert_data)
                
                conn.commit()
                flash("Permisos actualizados correctamente.", "success")
                return redirect(url_for("seguridad_bp.gestion_roles"))

            # GET: Armar la vista de la matriz
            cursor.execute("SELECT * FROM roles WHERE id != 1 ORDER BY id") # Excluimos al rol 1 para evitar que lo rompan
            roles = cursor.fetchall()

            cursor.execute("SELECT * FROM modulos ORDER BY nombre")
            modulos = cursor.fetchall()

            # Obtenemos todos los permisos actuales
            cursor.execute("SELECT * FROM rol_modulo")
            permisos_raw = cursor.fetchall()
            
            # Los agrupamos en un diccionario (ej: {rol_2: [modulo_1, modulo_3]}) para que el HTML los pinte como "checked"
            permisos_dict = {}
            for p in permisos_raw:
                r_id = p["rol_id"]
                if r_id not in permisos_dict:
                    permisos_dict[r_id] = []
                permisos_dict[r_id].append(p["modulo_id"])

    finally:
        conn.close()

    return render_template("admin/admin_roles.html", roles=roles, modulos=modulos, permisos=permisos_dict)
