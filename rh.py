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
