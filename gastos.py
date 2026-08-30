from flask import Blueprint, render_template, request, redirect, url_for, flash
from decimal import Decimal
from db import get_connection

gastos_bp = Blueprint("gastos", __name__, url_prefix="/admin")

# Helpers (puedes importarlos de db.py si los tienes ahí, o copiarlos temporalmente)
def query_all(sql, params=None):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params or ())
            return cursor.fetchall()
    finally:
        conn.close()

def execute(sql, params=None):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params or ())
        conn.commit()
    finally:
        conn.close()

# =========================================================
# ================== MÓDULO DE GASTOS =====================
# =========================================================

@gastos_bp.route("/gastos", methods=["GET"])
def gastos_index():
    # Obtener categorías para el formulario
    categorias = query_all("SELECT id, nombre FROM categorias_gastos ORDER BY nombre")
    
    # Obtener historial de gastos del mes actual (puedes ajustar la consulta después)
    gastos = query_all("""
        SELECT g.id, g.fecha, c.nombre as categoria, g.concepto, g.monto 
        FROM gastos g
        JOIN categorias_gastos c ON g.categoria_id = c.id
        ORDER BY g.fecha DESC
        LIMIT 50
    """)
    
    return render_template("admin/gastos.html", categorias=categorias, gastos=gastos)

@gastos_bp.route("/gastos", methods=["POST"])
def gastos_create():
    fecha = request.form.get("fecha")
    categoria_id = request.form.get("categoria_id")
    concepto = request.form.get("concepto", "").strip()
    monto = request.form.get("monto")
    nota = request.form.get("nota", "").strip()

    if not all([fecha, categoria_id, concepto, monto]):
        flash("Por favor llena todos los campos obligatorios.", "error")
        return redirect(url_for("gastos.gastos_index"))

    try:
        monto_decimal = Decimal(monto)
        execute(
            "INSERT INTO gastos (fecha, categoria_id, concepto, monto, nota) VALUES (%s, %s, %s, %s, %s)",
            (fecha, categoria_id, concepto, monto_decimal, nota)
        )
        flash("Gasto registrado con éxito.", "success")
    except Exception as e:
        flash(f"Error al registrar el gasto: {e}", "error")

    return redirect(url_for("gastos.gastos_index"))
