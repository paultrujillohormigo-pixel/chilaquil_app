from flask import Flask, render_template, request, redirect, url_for, flash
from db import get_connection
from decimal import Decimal

app = Flask(__name__)
app.secret_key = "super_secret_key"


# ---------------- FILTRO MONEDA ----------------
@app.template_filter("money")
def money(value):
    try:
        return "${:,.2f}".format(float(value))
    except:
        return value


# ---------------- HOME ----------------
@app.route("/")
def index():
    return redirect(url_for("dashboard"))


# ---------------- DASHBOARD ----------------
@app.route("/dashboard")
def dashboard():
    mes = request.args.get("mes")  # formato YYYY-MM

    conn = get_connection()
    try:
        with conn.cursor() as cursor:

            # ====== MESES DISPONIBLES ======
            cursor.execute("""
                SELECT DISTINCT DATE_FORMAT(fecha, '%%Y-%%m') AS valor
                FROM pedidos
                ORDER BY valor DESC
            """)
            meses_disponibles = cursor.fetchall()

            # ====== INGRESOS ======
            sql_ingresos = """
                SELECT DATE_FORMAT(fecha, '%%Y-%%m') AS mes,
                       SUM(total) AS total
                FROM pedidos
            """
            params = ()

            if mes:
                sql_ingresos += " WHERE DATE_FORMAT(fecha, '%%Y-%%m') = %s "
                params = (mes,)

            sql_ingresos += " GROUP BY mes ORDER BY mes"
            cursor.execute(sql_ingresos, params)
            ingresos = cursor.fetchall()

            # ====== COSTOS ======
            sql_costos = """
                SELECT DATE_FORMAT(fecha, '%%Y-%%m') AS mes,
                       SUM(costo) AS costo
                FROM insumos_compras
            """
            if mes:
                sql_costos += " WHERE DATE_FORMAT(fecha, '%%Y-%%m') = %s "
            sql_costos += " GROUP BY mes ORDER BY mes"

            cursor.execute(sql_costos, params)
            costos = cursor.fetchall()

            # ====== COSTOS POR TIPO ======
            sql_costos_tipo = """
                SELECT tipo_costo, SUM(costo) AS total
                FROM insumos_compras
            """
            if mes:
                sql_costos_tipo += " WHERE DATE_FORMAT(fecha, '%%Y-%%m') = %s "
            sql_costos_tipo += " GROUP BY tipo_costo"

            cursor.execute(sql_costos_tipo, params)
            costos_tipo = cursor.fetchall()

            # ====== KPIs ======
            total_ingresos = sum(i["total"] or 0 for i in ingresos)
            total_costos = sum(c["costo"] or 0 for c in costos)
            utilidad = total_ingresos - total_costos
            margen = round((utilidad / total_ingresos) * 100, 2) if total_ingresos else 0

            # ====== VENTAS POR DIA ======
            sql_ventas_dia = """
                SELECT DATE(fecha) AS dia,
                       COUNT(*) AS pedidos,
                       SUM(total) AS total,
                       SUM(neto) AS neto
                FROM pedidos
            """
            if mes:
                sql_ventas_dia += " WHERE DATE_FORMAT(fecha, '%%Y-%%m') = %s "
            sql_ventas_dia += " GROUP BY DATE(fecha) ORDER BY dia DESC LIMIT 15"

            cursor.execute(sql_ventas_dia, params)
            ventas_dia = cursor.fetchall()

            # ====== TOP PRODUCTOS ======
            sql_top = """
                SELECT p.nombre,
                       SUM(pi.cantidad) AS cantidad,
                       SUM(pi.subtotal) AS ingreso
                FROM pedido_items pi
                JOIN productos p ON p.id = pi.producto_id
                JOIN pedidos pe ON pe.id = pi.pedido_id
            """
            if mes:
                sql_top += " WHERE DATE_FORMAT(pe.fecha, '%%Y-%%m') = %s "
            sql_top += """
                GROUP BY p.id
                ORDER BY ingreso DESC
                LIMIT 5
            """

            cursor.execute(sql_top, params)
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
        ventas_dia=ventas_dia,
        top_productos=top_productos,
        meses_disponibles=meses_disponibles,
        mes=mes
    )


# ---------------- RUN LOCAL ----------------
if __name__ == "__main__":
    app.run(debug=True)
