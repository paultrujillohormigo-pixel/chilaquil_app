from flask import Blueprint, render_template, request, redirect, url_for, flash
from db import get_connection
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
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
                        # 1. Registramos la salida y calculamos minutos
                        cursor.execute("""
                            UPDATE rh_asistencias 
                            SET hora_salida = NOW(), 
                                minutos_trabajados = TIMESTAMPDIFF(MINUTE, hora_entrada, NOW()) 
                            WHERE id = %s
                        """, (registro["id"],))
                        
                        # 2. Obtenemos los datos para calcular su pago de este turno
                        cursor.execute("""
                            SELECT a.minutos_trabajados, e.nombre, e.salario_base, e.tipo_pago 
                            FROM rh_asistencias a 
                            JOIN rh_empleados e ON a.empleado_id = e.id 
                            WHERE a.id = %s
                        """, (registro["id"],))
                        datos = cursor.fetchone()
                        
                        minutos = datos["minutos_trabajados"] or 0
                        salario = float(datos["salario_base"])
                        tipo = datos["tipo_pago"]
                        
                        # 3. Calculamos cuánto le toca hoy dependiendo su esquema
                        monto_turno = 0
                        if tipo == "dia":
                            monto_turno = salario
                        elif tipo == "hora":
                            monto_turno = (minutos / 60.0) * salario
                        elif tipo == "semana":
                            monto_turno = salario / 7.0
                        elif tipo == "quincena":
                            monto_turno = salario / 15.0
                            
                        # 4. Inyectamos este turno directo a los Gastos (Categoría 1 = Nómina)
                        if monto_turno > 0:
                            concepto = f"Provisión Sueldo {datos['nombre']}"
                            cursor.execute("""
                                INSERT INTO gastos (fecha, categoria_id, concepto, monto, nota)
                                VALUES (CURRENT_DATE, 1, %s, %s, 'Gasto generado automático al marcar salida')
                            """, (concepto, round(monto_turno, 2)))
                            
                        conn.commit()
                        flash("👋 Salida registrada y gasto sumado a la operación diaria. ¡Buen descanso!", "success")

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
@rh_bp.route("/nomina", methods=["GET", "POST"])
def nomina():
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            if request.method == "POST":
                empleado_id = request.form.get("empleado_id")
                periodo_inicio = request.form.get("periodo_inicio")
                periodo_fin = request.form.get("periodo_fin")
                monto = request.form.get("monto")
                nota = request.form.get("nota", "Pago de nómina")

                if not all([empleado_id, periodo_inicio, periodo_fin, monto]):
                    flash("Faltan datos para procesar el pago.", "error")
                    return redirect(url_for("rh_bp.nomina"))

                # 1. Traer datos del empleado para armar el concepto
                cursor.execute("SELECT nombre FROM rh_empleados WHERE id = %s", (empleado_id,))
                empleado = cursor.fetchone()
                concepto_gasto = f"Sueldo {empleado['nombre']}"

                # 2. Insertar directamente en la tabla de OPEX (gastos)
                # categoria_id = 1 asumiendo que 1 es 'Nómina' en tu tabla categorias_gastos
                cursor.execute("""
                    INSERT INTO gastos (fecha, categoria_id, concepto, monto, nota)
                    VALUES (CURRENT_DATE, 1, %s, %s, %s)
                """, (concepto_gasto, monto, nota))
                gasto_id = cursor.lastrowid # Obtenemos el ID del OPEX generado

                # 3. Guardar el registro en el historial de RH
                cursor.execute("""
                    INSERT INTO rh_nominas (empleado_id, fecha_pago, periodo_inicio, periodo_fin, monto_pagado, gasto_id)
                    VALUES (%s, CURRENT_DATE, %s, %s, %s, %s)
                """, (empleado_id, periodo_inicio, periodo_fin, monto, gasto_id))

                conn.commit()
                flash(f"✅ Se pagaron ${monto} a {empleado['nombre']} y se registró en el OPEX automáticamente.", "success")
                return redirect(url_for("rh_bp.nomina"))

            # --- VISTA GET ---
            # Traer empleados activos para el panel de pago
            cursor.execute("""
                SELECT e.*, 
                       (SELECT MAX(fecha_pago) FROM rh_nominas WHERE empleado_id = e.id) as ultimo_pago
                FROM rh_empleados e 
                WHERE e.activo = 1 
                ORDER BY e.nombre ASC
            """)
            empleados = cursor.fetchall()

            # Traer los últimos 15 pagos realizados
            cursor.execute("""
                SELECT n.*, e.nombre 
                FROM rh_nominas n 
                JOIN rh_empleados e ON n.empleado_id = e.id 
                ORDER BY n.fecha_pago DESC, n.id DESC LIMIT 15
            """)
            ultimos_pagos = cursor.fetchall()
            
    finally:
        conn.close()

    return render_template("rh_nomina.html", empleados=empleados, ultimos_pagos=ultimos_pagos)
@rh_bp.route("/api/calcular-pago", methods=["GET"])
def api_calcular_pago():
    empleado_id = request.args.get("empleado_id")
    inicio = request.args.get("inicio")
    fin = request.args.get("fin")
    
    if not all([empleado_id, inicio, fin]):
        return jsonify({"monto": 0, "dias": 0})
        
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            # 1. Obtener salario y esquema del empleado
            cursor.execute("SELECT salario_base, tipo_pago FROM rh_empleados WHERE id = %s", (empleado_id,))
            emp = cursor.fetchone()
            if not emp: return jsonify({"monto": 0, "dias": 0})
            
            salario_base = float(emp["salario_base"])
            tipo_pago = emp["tipo_pago"]
            
            # 2. Contar días distintos que vino a trabajar en el periodo
            cursor.execute("""
                SELECT COUNT(DISTINCT fecha) as dias_trabajados, SUM(minutos_trabajados) as total_minutos
                FROM rh_asistencias
                WHERE empleado_id = %s AND fecha >= %s AND fecha <= %s AND hora_entrada IS NOT NULL
            """, (empleado_id, inicio, fin))
            asistencias = cursor.fetchone()
            
            dias = int(asistencias["dias_trabajados"] or 0)
            minutos = int(asistencias["total_minutos"] or 0)
            
            # 3. Calcular monto según su esquema de pago
            monto = 0
            if tipo_pago == "dia":
                monto = dias * salario_base
            elif tipo_pago == "hora":
                monto = (minutos / 60.0) * salario_base
            elif tipo_pago == "semana":
                monto = (salario_base / 7) * dias
            elif tipo_pago == "quincena":
                monto = (salario_base / 15) * dias
            else:
                monto = dias * salario_base
                
            return jsonify({"monto": round(monto, 2), "dias": dias, "tipo": tipo_pago})
    finally:
        conn.close()
