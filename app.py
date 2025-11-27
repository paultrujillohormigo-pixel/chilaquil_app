from flask import Flask, render_template, request, redirect, url_for, flash
from db import get_connection
from decimal import Decimal

app = Flask(__name__)

@app.template_filter("money")
def money_format(value):
    try:
        return "${:,.2f}".format(float(value))
    except:
        return value
        
app.secret_key = "super_secret_key"  # cámbiala en prod


# ------------------ HOME ------------------
@app.route("/")
def index():
    return redirect(url_for("nuevo_pedido"))


# ------------------ PRODUCTOS ------------------
@app.route("/productos", methods=["GET", "POST"])
def productos():
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            if request.method == "POST":
                cursor.execute(
                    """
                    INSERT INTO productos (nombre, categoria, costo, precio)
                    VALUES (%s,%s,%s,%s)
                    """,
                    (
                        request.form["nombre"],
                        request.form["categoria"],
                        request.form["costo"],
                        request.form["precio"],
                    ),
                )
                conn.commit()
                flash("Producto creado correctamente", "success")

            cursor.execute(
                "SELECT * FROM productos WHERE activo = 1 ORDER BY categoria, nombre"
            )
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
            cursor.execute(
                "SELECT * FROM productos WHERE activo = 1 ORDER BY categoria, nombre"
            )
            productos = cursor.fetchall()

            cursor.execute("SELECT * FROM salsas")
            salsas = cursor.fetchall()

            cursor.execute("SELECT * FROM proteinas")
            proteinas = cursor.fetchall()

            if request.method == "POST":
                origen = request.form["origen"]
                mesero = request.form["mesero"]
                metodo_pago = request.form["metodo_pago"]
                monto_uber = Decimal(request.form.get("monto_uber", "0") or "0")

                fecha_pedido = request.form.get("fecha_pedido")
                if fecha_pedido:
                    fecha_pedido = fecha_pedido.replace("T", " ")

                productos_ids = request.form.getlist("producto_id")
                cantidades = request.form.getlist("cantidad")
                salsas_ids = request.form.getlist("salsa_id")
                proteinas_ids = request.form.getlist("proteina_id")

                total = Decimal("0")
                items = []

                for i, prod_id in enumerate(productos_ids):
                    if not prod_id:
                        continue

                    cant = int(cantidades[i] or 0)
                    if cant <= 0:
                        continue

                    cursor.execute(
                        "SELECT precio FROM productos WHERE id = %s", (prod_id,)
                    )
                    row = cursor.fetchone()
                    if not row:
                        continue

                    precio_unit = Decimal(row["precio"])
                    subtotal = precio_unit * cant
                    total += subtotal

                    items.append(
                        {
                            "producto_id": prod_id,
                            "cantidad": cant,
                            "precio_unitario": precio_unit,
                            "subtotal": subtotal,
                            "salsa_id": salsas_ids[i] or None,
                            "proteina_id": proteinas_ids[i] or None,
                        }
                    )

                neto = total + monto_uber

                if fecha_pedido:
                    cursor.execute(
                        """
                        INSERT INTO pedidos
                        (fecha, origen, mesero, metodo_pago, total, monto_uber, neto)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            fecha_pedido,
                            origen,
                            mesero,
                            metodo_pago,
                            total,
                            monto_uber,
                            neto,
                        ),
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO pedidos
                        (origen, mesero, metodo_pago, total, monto_uber, neto)
                        VALUES (%s,%s,%s,%s,%s,%s)
                        """,
                        (origen, mesero, metodo_pago, total, monto_uber, neto),
                    )

                pedido_id = cursor.lastrowid

                for it in items:
                    cursor.execute(
                        """
                        INSERT INTO pedido_items
                        (pedido_id, producto_id, salsa_id, proteina_id,
                         cantidad, precio_unitario, subtotal)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            pedido_id,
                            it["producto_id"],
                            it["salsa_id"],
                            it["proteina_id"],
                            it["cantidad"],
                            it["precio_unitario"],
                            it["subtotal"],
                        ),
                    )

                conn.commit()
                flash(
                    f"Pedido {pedido_id} registrado. Total: ${total}",
                    "success",
                )
                return redirect(url_for("nuevo_pedido"))

    finally:
        conn.close()

    return render_template(
        "nuevo_pedido.html",
        productos=productos,
        salsas=salsas,
        proteinas=proteinas,
    )


# ------------------ COMPRAS ------------------
@app.route("/compras", methods=["GET", "POST"])
def compras():
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            if request.method == "POST":
                cursor.execute(
                    """
                    INSERT INTO insumos_compras
                    (fecha, lugar, cantidad, unidad, concepto, costo, tipo_costo, nota)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        request.form["fecha"],
                        request.form["lugar"],
                        request.form["cantidad"],
                        request.form["unidad"],
                        request.form["concepto"],
                        request.form["costo"],
                        request.form["tipo_costo"],
                        request.form.get("nota", ""),
                    ),
                )
                conn.commit()
                flash("Compra registrada correctamente", "success")

            cursor.execute(
                "SELECT * FROM insumos_compras ORDER BY fecha DESC, id DESC"
            )
            compras = cursor.fetchall()
    finally:
        conn.close()

    return render_template("compras.html", compras=compras)


# ------------------ DASHBOARD ------------------
@app.route("/dashboard")
def dashboard():
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            
            cursor.execute("""
                SELECT DATE_FORMAT(fecha, '%Y-%m') mes, SUM(total) total
                FROM pedidos
                GROUP BY mes
                ORDER BY mes
            """)
            ingresos = cursor.fetchall()

            cursor.execute("""
                SELECT DATE_FORMAT(fecha, '%Y-%m') mes, SUM(costo) costo
                FROM insumos_compras
                GROUP BY mes
                ORDER BY mes
            """)
            costos = cursor.fetchall()

            cursor.execute("""
                SELECT tipo_costo, SUM(costo) total
                FROM insumos_compras
                GROUP BY tipo_costo
            """)
            costos_tipo = cursor.fetchall()

            total_ingresos = sum(i["total"] for i in ingresos) if ingresos else 0
            total_costos = sum(c["costo"] for c in costos) if costos else 0
            utilidad = total_ingresos - total_costos
            margen = round((utilidad / total_ingresos) * 100, 2) if total_ingresos else 0

            cursor.execute("""
                SELECT p.nombre,
                       SUM(pi.cantidad) cantidad,
                       SUM(pi.subtotal) ingreso
                FROM pedido_items pi
                JOIN productos p ON p.id = pi.producto_id
                GROUP BY p.id
                ORDER BY ingreso DESC
                LIMIT 10
            """)
            top_productos = cursor.fetchall()

    finally:
        conn.close()

    return render_template(
        "dashboard.html",
        ingresos=ingresos,
        costos=costos,
        costos_tipo=costos_tipo,
        total_ingresos=total_ingresos,
        total_costos=total_costos,
        utilidad=utilidad,
        margen=margen,
        top_productos=top_productos,
    )

@app.route("/dashboard")
def dashboard():
    mes = request.args.get("mes")  # YYYY-MM

    conn = get_connection()
    try:
        with conn.cursor() as cursor:

            filtro = ""
            params = []

            if mes:
                filtro = "WHERE DATE_FORMAT(fecha, '%Y-%m') = %s"
                params.append(mes)

            # ---------- INGRESOS ----------
            cursor.execute(f"""
                SELECT DATE_FORMAT(fecha, '%Y-%m') AS mes,
                       SUM(total) AS total
                FROM pedidos
                {filtro}
                GROUP BY mes
                ORDER BY mes
            """, params)
            ingresos = cursor.fetchall()

            # ---------- COSTOS ----------
            cursor.execute(f"""
                SELECT DATE_FORMAT(fecha, '%Y-%m') AS mes,
                       SUM(costo) AS costo
                FROM insumos_compras
                {filtro}
                GROUP BY mes
                ORDER BY mes
            """, params)
            costos = cursor.fetchall()

            # ---------- COSTOS POR TIPO ----------
            cursor.execute(f"""
                SELECT tipo_costo, SUM(costo) AS total
                FROM insumos_compras
                {filtro}
                GROUP BY tipo_costo
            """, params)
            costos_tipo = cursor.fetchall()

            # ---------- KPI ----------
            total_ingresos = sum(i["total"] for i in ingresos if i["total"])
            total_costos = sum(c["costo"] for c in costos if c["costo"])
            utilidad = total_ingresos - total_costos
            margen = (utilidad / total_ingresos * 100) if total_ingresos else 0

    finally:
        conn.close()

    return render_template(
        "dashboard.html",
        ingresos=ingresos,
        costos=costos,
        costos_tipo=costos_tipo,
        total_ingresos=total_ingresos,
        total_costos=total_costos,
        utilidad=utilidad,
        margen=round(margen, 2),
        mes=mes
    )



# ------------------ RUN ------------------
if __name__ == "__main__":
    app.run(debug=True)
