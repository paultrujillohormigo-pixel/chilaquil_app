from flask import Flask, render_template, request, redirect, url_for, flash
from db import get_connection
from decimal import Decimal

app = Flask(__name__)
app.secret_key = "super_secret_key"  # cambia en producción


# ================== FILTRO MONEDA ==================
@app.template_filter("money")
def money_format(value):
    try:
        return "${:,.2f}".format(float(value))
    except:
        return value


# ================== HOME ==================
@app.route("/")
def index():
    return redirect(url_for("dashboard"))


# ================== DASHBOARD ==================
@app.route("/dashboard")
def dashboard():
    mes = request.args.get("mes")  # YYYY-MM o None

    conn = get_connection()
    try:
        with conn.cursor() as cursor:

            # ========= MESES DISPONIBLES =========
            cursor.execute("""
                SELECT DISTINCT DATE_FORMAT(fecha, '%%Y-%%m') AS valor
                FROM pedidos
                ORDER BY valor
            """)
            meses_disponibles = cursor.fetchall()

            filtro = ""
            params = []

            if mes:
                filtro = "WHERE DATE_FORMAT(fecha, '%%Y-%%m') = %s"
                params.append(mes)

            # ========= INGRESOS =========
            cursor.execute("""
                SELECT DATE_FORMAT(fecha, '%%Y-%%m') AS mes,
                       SUM(total) AS total
                FROM pedidos
                """ + filtro + """
                GROUP BY mes
                ORDER BY mes
            """, params)
            ingresos = cursor.fetchall()

            # ========= COSTOS =========
            cursor.execute("""
                SELECT DATE_FORMAT(fecha, '%%Y-%%m') AS mes,
                       SUM(costo) AS costo
                FROM insumos_compras
                """ + filtro + """
                GROUP BY mes
                ORDER BY mes
            """, params)
            costos = cursor.fetchall()

            # ========= COSTOS POR TIPO =========
            cursor.execute("""
                SELECT tipo_costo, SUM(costo) AS total
                FROM insumos_compras
                """ + filtro + """
                GROUP BY tipo_costo
            """, params)
            costos_tipo = cursor.fetchall()

            # ========= KPI =========
            total_ingresos = sum(i["total"] for i in ingresos if i["total"])
            total_costos = sum(c["costo"] for c in costos if c["costo"])
            utilidad = total_ingresos - total_costos
            margen = round((utilidad / total_ingresos) * 100, 2) if total_ingresos else 0

            # ========= VENTAS POR DIA =========
            cursor.execute("""
                SELECT DATE(fecha) AS dia,
                       COUNT(*) AS pedidos,
                       SUM(total) AS total,
                       SUM(neto) AS neto
                FROM pedidos
                """ + filtro + """
                GROUP BY DATE(fecha)
                ORDER BY dia DESC
                LIMIT 15
            """, params)
            ventas_dia = cursor.fetchall()

            # ========= TOP PRODUCTOS =========
            cursor.execute("""
                SELECT p.nombre,
                       SUM(pi.cantidad) AS cantidad,
                       SUM(pi.subtotal) AS ingreso
                FROM pedido_items pi
                JOIN pedidos pe ON pe.id = pi.pedido_id
                JOIN productos p ON p.id = pi.producto_id
                """ + filtro + """
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
        total_ingresos=total_ingresos,
        total_costos=total_costos,
        utilidad=utilidad,
        margen=margen,
        ventas_dia=ventas_dia,
        top_productos=top_productos,
        meses_disponibles=meses_disponibles,
        mes=mes
    )


# ================== RUN ==================
if __name__ == "__main__":
    app.run(debug=True)
