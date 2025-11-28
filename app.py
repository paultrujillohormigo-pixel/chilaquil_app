from flask import Flask, render_template, request, redirect, url_for, flash
from decimal import Decimal
from db import get_connection

app = Flask(__name__)
app.secret_key = "super_secret_key"  # cámbiala en prod


# ================== FILTRO DE MONEDA ==================
@app.template_filter("money")
def money_format(value):
    try:
        return "${:,.2f}".format(float(value))
    except:
        return value


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
                flash("Producto creado correctamente", "success")

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

            cursor.execute("SELECT * FROM productos WHERE activo = 1 ORDER BY categoria, nombre")
            productos = cursor.fetchall()

            cursor.execute("SELECT * FROM salsas")
            salsas = cursor.fetchall()

            cursor.execute("SELECT * FROM proteinas")
            proteinas = cursor.fetchall()

            if request.method == "POST":
                fecha = request.form["fecha"]  # ✅ FECHA SELECCIONADA
                origen = request.form["origen"].strip().lower()
                mesero = request.form["mesero"]
                metodo_pago = request.form["metodo_pago"]
                monto_uber = Decimal(request.form.get("monto_uber", "0") or "0")

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

                    cursor.execute("""
                        SELECT 
                            CASE 
                                WHEN %s = 'uber' AND precio_uber IS NOT NULL
                                    THEN precio_uber
                                ELSE precio
                            END AS precio_final
                        FROM productos
                        WHERE id = %s
                    """, (origen, prod_id))

                    row = cursor.fetchone()
                    if not row:
                        continue

                    precio_unit = Decimal(row["precio_final"])
                    subtotal = precio_unit * cant
                    total += subtotal

                    items.append({
                        "producto_id": prod_id,
                        "cantidad": cant,
                        "precio_unitario": precio_unit,
                        "subtotal": subtotal,
                        "salsa_id": salsas_ids[i] or None,
                        "proteina_id": proteinas_ids[i] or None,
                    })

                neto = total + monto_uber

                cursor.execute("""
                    INSERT INTO pedidos
                    (fecha, origen, mesero, metodo_pago, total, monto_uber, neto)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (
                    fecha,          # ✅ USAMOS LA FECHA DEL FORM
                    origen,
                    mesero,
                    metodo_pago,
                    total,
                    monto_uber,
                    neto
                ))

                pedido_id = cursor.lastrowid

                for it in items:
                    cursor.execute("""
                        INSERT INTO pedido_items
                        (pedido_id, producto_id, salsa_id, proteina_id,
                         cantidad, precio_unitario, subtotal)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """, (
                        pedido_id,
                        it["producto_id"],
                        it["salsa_id"],
                        it["proteina_id"],
                        it["cantidad"],
                        it["precio_unitario"],
                        it["subtotal"],
                    ))

                conn.commit()
                flash(f"Pedido #{pedido_id} registrado correctamente", "success")
                return redirect(url_for("nuevo_pedido"))

    finally:
        conn.close()

    return render_template(
        "nuevo_pedido.html",
        productos=productos,
        salsas=salsas,
        proteinas=proteinas,
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
                flash("Compra registrada correctamente", "success")

            cursor.execute("""
                SELECT *
                FROM insumos_compras
                ORDER BY fecha DESC, id DESC
            """)
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

            filtro = ""
            params = []

            if mes:
                filtro = "WHERE DATE_FORMAT(fecha, '%%Y-%%m') = %s"
                params.append(mes)

            cursor.execute(f"""
                SELECT DATE_FORMAT(fecha, '%%Y-%%m') AS mes,
                       SUM(total) AS total
                FROM pedidos
                {filtro}
                GROUP BY mes
                ORDER BY mes
            """, params)
            ingresos = cursor.fetchall()

            cursor.execute(f"""
                SELECT DATE_FORMAT(fecha, '%%Y-%%m') AS mes,
                       SUM(costo) AS costo
                FROM insumos_compras
                {filtro}
                GROUP BY mes
                ORDER BY mes
            """, params)
            costos = cursor.fetchall()

            cursor.execute(f"""
                SELECT tipo_costo,
                       SUM(costo) AS total
                FROM insumos_compras
                {filtro}
                GROUP BY tipo_costo
            """, params)
            costos_tipo = cursor.fetchall()

            total_ingresos = sum(i["total"] for i in ingresos if i["total"])
            total_costos = sum(c["costo"] for c in costos if c["costo"])
            utilidad = total_ingresos - total_costos
            margen = (utilidad / total_ingresos * 100) if total_ingresos else 0

            cursor.execute(f"""
                SELECT DATE(fecha) AS dia,
                       COUNT(*) AS pedidos,
                       SUM(total) AS total,
                       SUM(neto) AS neto
                FROM pedidos
                {filtro}
                GROUP BY DATE(fecha)
                ORDER BY dia DESC
            """, params)
            ventas_dia = cursor.fetchall()

            cursor.execute("""
                SELECT DISTINCT DATE_FORMAT(fecha, '%Y-%m') AS mes
                FROM pedidos
                ORDER BY mes DESC
            """)
            meses_disponibles = [m["mes"] for m in cursor.fetchall()]

            cursor.execute(f"""
                SELECT p.nombre,
                       SUM(pi.cantidad) AS cantidad,
                       SUM(pi.subtotal) AS ingreso
                FROM pedido_items pi
                JOIN pedidos pe ON pe.id = pi.pedido_id
                JOIN productos p ON p.id = pi.producto_id
                {filtro}
                GROUP BY p.id
                ORDER BY ingreso DESC
                LIMIT 10
            """, params)
            top_productos = cursor.fetchall()

    finally:
        conn.close()

    return render_template(
        "dashboard.html",
        ingresos=ingresos,
        costos=costos,
        costos_tipo=costos_tipo,
        ventas_dia=ventas_dia,
        top_productos=top_productos,
        total_ingresos=total_ingresos,
        total_costos=total_costos,
        utilidad=utilidad,
        margen=round(margen, 2),
        meses_disponibles=meses_disponibles,
        mes=mes,
    )


# ================== RUN ==================
if __name__ == "__main__":
    app.run(debug=True)
