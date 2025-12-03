from flask import Flask, render_template, request, redirect, url_for, flash
from decimal import Decimal
from db import get_connection

app = Flask(__name__)
app.secret_key = "super_secret_key"


# ================== FILTRO MONEDA ==================
@app.template_filter("money")
def money(value):
    try:
        return "${:,.2f}".format(float(value))
    except:
        return "$0.00"


# ================== HOME ==================
@app.route("/")
def index():
    return redirect(url_for("nuevo_pedido"))


# ================== PRODUCTOS ==================
@app.route("/productos", methods=["GET", "POST"])
def productos():
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            if request.method == "POST":
                cursor.execute("""
                    INSERT INTO productos (nombre, categoria, costo, precio)
                    VALUES (%s,%s,%s,%s)
                """, (
                    request.form["nombre"],
                    request.form["categoria"],
                    request.form["costo"],
                    request.form["precio"],
                ))
                conn.commit()
                flash("Producto creado", "success")

            cursor.execute("""
                SELECT *
                FROM productos
                WHERE activo = 1
                ORDER BY categoria, nombre
            """)
            productos = cursor.fetchall()
    finally:
        conn.close()

    return render_template("productos.html", productos=productos)


# ================== NUEVO PEDIDO ==================
@app.route("/nuevo_pedido", methods=["GET", "POST"])
def nuevo_pedido():
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM productos WHERE activo=1 ORDER BY categoria,nombre")
            productos = cursor.fetchall()

            cursor.execute("SELECT * FROM salsas")
            salsas = cursor.fetchall()

            cursor.execute("SELECT * FROM proteinas")
            proteinas = cursor.fetchall()

            if request.method == "POST":
                fecha = request.form["fecha"]
                origen = request.form["origen"].lower().strip()
                metodo_pago = request.form["metodo_pago"]
                mesero = request.form["mesero"]
                monto_uber = Decimal(request.form.get("monto_uber", 0) or 0)

                productos_ids = request.form.getlist("producto_id")
                cantidades = request.form.getlist("cantidad")

                total = Decimal("0")
                items = []

                for i, pid in enumerate(productos_ids):
                    if not pid:
                        continue

                    cantidad = int(cantidades[i] or 0)
                    if cantidad <= 0:
                        continue

                    cursor.execute("""
                        SELECT CASE
                            WHEN %s='uber' AND precio_uber IS NOT NULL
                            THEN precio_uber
                            ELSE precio
                        END AS precio
                        FROM productos
                        WHERE id=%s
                    """, (origen, pid))

                    precio = Decimal(cursor.fetchone()["precio"])
                    subtotal = precio * cantidad
                    total += subtotal

                    items.append((pid, cantidad, precio, subtotal))

                neto = total + monto_uber

                cursor.execute("""
                    INSERT INTO pedidos
                    (fecha, origen, mesero, metodo_pago, total, monto_uber, neto)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                """, (fecha, origen, mesero, metodo_pago, total, monto_uber, neto))

                pedido_id = cursor.lastrowid

                for pid, cantidad, precio, subtotal in items:
                    cursor.execute("""
                        INSERT INTO pedido_items
                        (pedido_id, producto_id, cantidad, precio_unitario, subtotal)
                        VALUES (%s,%s,%s,%s,%s)
                    """, (pedido_id, pid, cantidad, precio, subtotal))

                conn.commit()
                flash("Pedido registrado", "success")
                return redirect(url_for("nuevo_pedido"))
    finally:
        conn.close()

    return render_template(
        "nuevo_pedido.html",
        productos=productos,
        salsas=salsas,
        proteinas=proteinas
    )


# ================== COMPRAS ==================
@app.route("/compras", methods=["GET", "POST"])
def compras():
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            if request.method == "POST":
                cursor.execute("""
                    INSERT INTO insumos_compras
                    (fecha, lugar, cantidad, unidad, concepto, costo, tipo_costo, nota)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """, (
                    request.form["fecha"],
                    request.form["lugar"],
                    request.form["cantidad"],
                    request.form["unidad"],
                    request.form["concepto"],
                    request.form["costo"],
                    request.form["tipo_costo"],
                    request.form.get("nota", ""),
                ))
                conn.commit()
                flash("Compra registrada", "success")

            cursor.execute("SELECT * FROM insumos_compras ORDER BY fecha DESC")
            compras = cursor.fetchall()
    finally:
        conn.close()

    return render_template("compras.html", compras=compras)


# ================== DASHBOARD ==================
@app.route("/dashboard")
def dashboard():
    mes = request.args.get("mes")
    conn = get_connection()

    try:
        with conn.cursor() as cursor:

            # -------- INGRESOS --------
            if mes:
                cursor.execute("""
                    SELECT DATE_FORMAT(fecha,'%Y-%m') mes, SUM(total) total
                    FROM pedidos
                    WHERE DATE_FORMAT(fecha,'%%Y-%%m')=%s
                    GROUP BY mes
                """, (mes,))
            else:
                cursor.execute("""
                    SELECT DATE_FORMAT(fecha,'%Y-%m') mes, SUM(total) total
                    FROM pedidos
                    GROUP BY mes
                """)
            ingresos = cursor.fetchall()

            # -------- COSTOS VARIABLES --------
            if mes:
                cursor.execute("""
                    SELECT DATE_FORMAT(fecha,'%Y-%m') mes, SUM(costo) costo
                    FROM insumos_compras
                    WHERE tipo_costo='variable'
                      AND DATE_FORMAT(fecha,'%%Y-%%m')=%s
                    GROUP BY mes
                """, (mes,))
            else:
                cursor.execute("""
                    SELECT DATE_FORMAT(fecha,'%Y-%m') mes, SUM(costo) costo
                    FROM insumos_compras
                    WHERE tipo_costo='variable'
                    GROUP BY mes
                """)
            costos_db = cursor.fetchall()

            costos_map = {c["mes"]: float(c["costo"] or 0) for c in costos_db}
            costos = [{"mes": i["mes"], "costo": costos_map.get(i["mes"], 0)} for i in ingresos]

            # -------- KPIS --------
            total_ingresos = sum(i["total"] for i in ingresos if i["total"])
            total_costos = sum(c["costo"] for c in costos)
            utilidad = total_ingresos - total_costos
            margen = (utilidad / total_ingresos * 100) if total_ingresos else 0

            # -------- MESES --------
            cursor.execute("""
                SELECT DISTINCT DATE_FORMAT(fecha,'%Y-%m') mes
                FROM pedidos
                ORDER BY mes DESC
            """)
            meses_disponibles = [m["mes"] for m in cursor.fetchall()]

    finally:
        conn.close()

    return render_template(
        "dashboard.html",
        ingresos=ingresos,
        costos=costos,
        total_ingresos=total_ingresos,
        total_costos=total_costos,
        utilidad=utilidad,
        margen=round(margen,2),
        meses_disponibles=meses_disponibles,
        mes=mes
    )


# ================== RUN ==================
if __name__ == "__main__":
    app.run(debug=True)
