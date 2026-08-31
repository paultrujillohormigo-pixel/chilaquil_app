from flask import Blueprint, render_template, request, redirect, url_for, flash
from db import get_connection

# Creamos el módulo (Blueprint) con el prefijo /rh
rh_bp = Blueprint("rh_bp", __name__, url_prefix="/rh")

@rh_bp.route("/empleados", methods=["GET", "POST"])
def directorio():
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            # Si se envía el formulario para registrar un nuevo empleado
            if request.method == "POST":
                nombre = request.form.get("nombre", "").strip()
                puesto = request.form.get("puesto", "Staff").strip()
                telefono = request.form.get("telefono", "").strip()
                salario_base = request.form.get("salario_base", "0")
                tipo_pago = request.form.get("tipo_pago", "dia")
                fecha_ingreso = request.form.get("fecha_ingreso")

                if not nombre:
                    flash("El nombre del empleado es obligatorio.", "error")
                else:
                    cursor.execute("""
                        INSERT INTO rh_empleados (nombre, puesto, telefono, salario_base, tipo_pago, fecha_ingreso)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (nombre, puesto, telefono, salario_base, tipo_pago, fecha_ingreso))
                    conn.commit()
                    flash(f"Empleado {nombre} registrado exitosamente.", "success")
                    return redirect(url_for("rh_bp.directorio"))

            # Obtener todos los empleados activos
            cursor.execute("SELECT * FROM rh_empleados WHERE activo = 1 ORDER BY nombre ASC")
            empleados = cursor.fetchall()
            
    finally:
        conn.close()

    return render_template("rh_empleados.html", empleados=empleados)

@rh_bp.route("/empleados/<int:empleado_id>/baja", methods=["POST"])
def dar_de_baja(empleado_id):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            # En lugar de borrarlo, lo marcamos como inactivo (para no perder el historial de nóminas)
            cursor.execute("UPDATE rh_empleados SET activo = 0, fecha_baja = CURRENT_DATE WHERE id = %s", (empleado_id,))
            conn.commit()
            flash("Empleado dado de baja correctamente.", "success")
    finally:
        conn.close()
    return redirect(url_for("rh_bp.directorio"))
from datetime import datetime

@rh_bp.route("/checador", methods=["GET", "POST"])
def checador():
    conn = get_connection()
    hoy = datetime.now().date()
    
    try:
        with conn.cursor() as cursor:
            if request.method == "POST":
                empleado_id = request.form.get("empleado_id")
                accion = request.form.get("accion") # 'entrada' o 'salida'

                if not empleado_id:
                    flash("Por favor selecciona un empleado.", "error")
                    return redirect(url_for("rh_bp.checador"))

                # Revisar si el empleado ya tiene un registro el día de hoy
                cursor.execute("SELECT id, hora_entrada, hora_salida FROM rh_asistencias WHERE empleado_id = %s AND fecha = %s", (empleado_id, hoy))
                registro = cursor.fetchone()

                if accion == "entrada":
                    if registro and registro.get("hora_entrada"):
                        flash("Ya tienes una entrada registrada el día de hoy.", "warning")
                    else:
                        cursor.execute("INSERT INTO rh_asistencias (empleado_id, fecha, hora_entrada) VALUES (%s, %s, NOW())", (empleado_id, hoy))
                        conn.commit()
                        flash("✅ Entrada registrada con éxito. ¡Buen turno!", "success")

                elif accion == "salida":
                    if not registro or not registro.get("hora_entrada"):
                        flash("No puedes registrar salida sin haber registrado tu entrada primero.", "error")
                    elif registro.get("hora_salida"):
                        flash("Ya tienes una salida registrada el día de hoy.", "warning")
                    else:
                        # Registramos la salida y calculamos los minutos trabajados con TIMESTAMPDIFF de SQL
                        cursor.execute("""
                            UPDATE rh_asistencias 
                            SET hora_salida = NOW(), 
                                minutos_trabajados = TIMESTAMPDIFF(MINUTE, hora_entrada, NOW()) 
                            WHERE id = %s
                        """, (registro["id"],))
                        conn.commit()
                        flash("👋 Salida registrada con éxito. ¡Buen descanso!", "success")
                
                return redirect(url_for("rh_bp.checador"))

            # --- VISTA GET (Cargar la pantalla) ---
            # 1. Traer empleados activos para el selector
            cursor.execute("SELECT id, nombre, puesto FROM rh_empleados WHERE activo = 1 ORDER BY nombre")
            empleados = cursor.fetchall()

            # 2. Traer los registros de hoy para la tabla
            cursor.execute("""
                SELECT a.*, e.nombre, e.puesto 
                FROM rh_asistencias a 
                JOIN rh_empleados e ON a.empleado_id = e.id 
                WHERE a.fecha = %s
                ORDER BY a.hora_entrada DESC
            """, (hoy,))
            asistencias_hoy = cursor.fetchall()
            
    finally:
        conn.close()

    return render_template("rh_checador.html", empleados=empleados, asistencias_hoy=asistencias_hoy)
