from flask import Flask, render_template, request, redirect, url_for, flash
from db import get_connection
from decimal import Decimal

app = Flask(__name__)
app.secret_key = "super_secret_key"  # cámbiala

# ------------------ HOME ------------------
@app.route("/")
def index():
    return redirect(url_for("nuevo_pedido"))

# ------------------ PRODUCTOS (ADMIN) ------------------
@app.route("/productos", methods=["GET", "POST"])
def productos():
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            if request.method == "POST":
                nombre = request.form["nombre"]
                categoria = request.form["categoria"]
                costo = request.form["costo"]
                precio = request.form["precio"]

                sql = """
                    INSERT INTO productos (nombre, categoria, costo, precio)
                    VALUES (%s, %s, %s, %s)
                """
                cursor.execute(sql, (nombre, categoria, costo, precio))
                conn.commit()
                flash("Producto creado correctamente", "success")

            cursor.execute("SELECT * FROM productos WHERE activo = 1 ORDER BY categoria, nombre")
            productos = cursor.fetchall()
    finally:
        conn.close()

    return render_template("productos.html", productos=productos)

# ------------------ NUEVO PEDIDO ------------------
@app.route("/nuevo_pedido", methods=["GET", "POST"])
def nuevo_pedido():
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM productos WHERE activo = 1 ORDER BY categoria, nombre")
            productos = cursor.fetchall()

            cursor.execute("SELECT * FROM salsas")
            salsas = cursor.fetchall()

            cursor.execute("SELECT * FROM proteinas")
            proteinas = cursor.fetchall()

            if request.method == "POST":
                origen = request.form["origen"]       # mostrador / uber
                mesero = request.form["mesero"]
                metodo_pago = request.form["metodo_pago"]
                monto_uber = Decimal(request.form.get("monto_uber", "0") or "0")

                productos_ids = request.form.getlist("producto_id")
                cantidades = request.form.getlist("cantidad")
                salsas_ids = request.form.getlist("salsa_id")
                proteinas_ids = request.form.getlist("proteina_id")

                items = []
                total = Decimal("0")

                for i, prod_id in enumerate(productos_ids):
                    if not prod_id:
                        continue
                    cant = int(cantidades[i] or 0)
                    if cant <= 0:
                        continue

                    # precio del producto
                    cursor.execute("SELECT precio FROM productos WHERE id=%s", (prod_id,))
                    row = cursor.fetchone()
                    if not row:
                        continue

                    precio_unit = Decimal(row["precio"])
                    subtotal = precio_unit * cant
                    total += subtotal

                    items.append({
                        "producto_id": prod_id,
                        "cantidad": cant,
                        "precio_unitario": precio_unit,
                        "subtotal": subtotal,
                        "salsa_id": salsas_ids[i] or None,
                        "proteina_id": proteinas_ids[i] or None
                    })

                neto = total + monto_uber  # si monto_uber es negativo, resta

                # Inserta pedido
                sql_pedido = """
                    INSERT INTO pedidos (origen, mesero, metodo_pago, total, monto_uber, neto)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """
                cursor.execute(sql_pedido, (origen, mesero, metodo_pago, total, monto_uber, neto))
                pedido_id = cursor.lastrowid

                # Inserta items
                sql_item = """
                    INSERT INTO pedido_items
                    (pedido_id, producto_id, salsa_id, proteina_id, cantidad, precio_unitario, subtotal)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """
                for it in items:
                    cursor.execute(
                        sql_item,
                        (
                            pedido_id,
                            it["producto_id"],
                            it["salsa_id"],
                            it["proteina_id"],
                            it["cantidad"],
                            it["precio_unitario"],
                            it["subtotal"]
                        )
                    )

                conn.commit()
                flash(f"Pedido {pedido_id} registrado. Total: {total}, Neto: {neto}", "success")
                return redirect(url_for("nuevo_pedido"))

    finally:
        conn.close()

    return render_template(
        "nuevo_pedido.html",
        productos=productos,
        salsas=salsas,
        proteinas=proteinas
    )

# ------------------ DASHBOARD SIMPLE ------------------
@app.route("/dashboard")
def dashboard():
    conn = get_connection()
    data = {}
    try:
        with conn.cursor() as cursor:
            # Ventas por día (últimos 7 días)
            cursor.execute("""
                SELECT DATE(fecha) AS dia, SUM(total) AS total_dia, SUM(neto) AS neto_dia
                FROM pedidos
                GROUP BY DATE(fecha)
                ORDER BY dia DESC
                LIMIT 7;
            """)
            data["por_dia"] = cursor.fetchall()

            # TOP 10 productos
            cursor.execute("""
                SELECT p.nombre, SUM(pi.cantidad) AS cantidad_vendida, SUM(pi.subtotal) AS ingreso
                FROM pedido_items pi
                JOIN productos p ON p.id = pi.producto_id
                GROUP BY pi.producto_id
                ORDER BY ingreso DESC
                LIMIT 10;
            """)
            data["top_productos"] = cursor.fetchall()

            # Ventas por origen (mostrador, uber, etc.)
            cursor.execute("""
                SELECT origen, COUNT(*) AS pedidos, SUM(neto) AS neto
                FROM pedidos
                GROUP BY origen;
            """)
            data["por_origen"] = cursor.fetchall()

    finally:
        conn.close()

    return render_template("dashboard.html", data=data)


if __name__ == "__main__":
    app.run(debug=True)
