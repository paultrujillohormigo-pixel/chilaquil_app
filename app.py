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

# ================== PEDIDO ==================


@app.route("/pedido/<int:pedido_id>", methods=["GET", "POST"])
def ver_pedido(pedido_id):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:

            cursor.execute("""
                SELECT * FROM pedidos
                WHERE id = %s AND estado = 'abierto'
            """, (pedido_id,))
            pedido = cursor.fetchone()

            if not pedido:
                flash("Pedido no disponible", "error")
                return redirect(url_for("pedidos_abiertos"))

            cursor.execute("""
                SELECT pi.id, pi.cantidad, pi.precio_unitario, pi.subtotal, p.nombre
                    FROM pedido_items pi
                    JOIN productos p ON p.id = pi.producto_id
                    WHERE pi.pedido_id = %s

            """, (pedido_id,))
            items = cursor.fetchall()

            cursor.execute("""
                SELECT * FROM productos
                WHERE activo = 1
                ORDER BY categoria, nombre
            """)
            productos = cursor.fetchall()

            if request.method == "POST":

                productos_ids = request.form.getlist("producto_id[]")
                cantidades = request.form.getlist("cantidad[]")

                total_agregado = Decimal("0")

                for i, prod_id in enumerate(productos_ids):
                cant = int(cantidades[i])
                if cant <= 0:
                    continue
            
                # Precio actual
                cursor.execute("""
                    SELECT precio FROM productos WHERE id = %s
                """, (prod_id,))
                row = cursor.fetchone()
                if not row:
                    continue
            
                precio = Decimal(row["precio"])
            
                # ¿Ya existe el producto en el pedido?
                cursor.execute("""
                    SELECT id, cantidad
                    FROM pedido_items
                    WHERE pedido_id = %s AND producto_id = %s
                """, (pedido_id, prod_id))
            
                existente = cursor.fetchone()
            
                if existente:
                    nueva_cantidad = existente["cantidad"] + cant
                    nuevo_subtotal = nueva_cantidad * precio
            
                    cursor.execute("""
                        UPDATE pedido_items
                        SET cantidad = %s,
                            subtotal = %s
                        WHERE id = %s
                    """, (
                        nueva_cantidad,
                        nuevo_subtotal,
                        existente["id"]
                    ))
            
                    total_agregado += precio * cant
            
                else:
                    subtotal = precio * cant
                    total_agregado += subtotal
            
                    cursor.execute("""
                        INSERT INTO pedido_items
                        (pedido_id, producto_id, cantidad, precio_unitario, subtotal)
                        VALUES (%s,%s,%s,%s,%s)
                    """, (
                        pedido_id,
                        prod_id,
                        cant,
                        precio,
                        subtotal
                    ))


                cursor.execute("""
                    UPDATE pedidos
                    SET total = total + %s,
                        neto = neto + %s
                    WHERE id = %s
                """, (total_agregado, total_agregado, pedido_id))

                conn.commit()
                return redirect(url_for("ver_pedido", pedido_id=pedido_id))

    finally:
        conn.close()

    return render_template(
        "pedido.html",
        pedido=pedido,
        items=items,
        productos=productos
    )


# ================== PEDIDOS ABIERTOS ==================

@app.route("/pedidos_abiertos")
def pedidos_abiertos():
    conn = get_connection()
    try:
        with conn.cursor() as cursor:

            # Pedidos abiertos
            cursor.execute("""
                SELECT id, mesa, mesero, total, fecha
                FROM pedidos
                WHERE estado = 'abierto'
                ORDER BY fecha
            """)
            pedidos = cursor.fetchall()

            # Preview de items por pedido (máx 4)
            for p in pedidos:
                cursor.execute("""
                    SELECT pr.nombre, pi.cantidad
                    FROM pedido_items pi
                    JOIN productos pr ON pr.id = pi.producto_id
                    WHERE pi.pedido_id = %s
                    LIMIT 4
                """, (p["id"],))
                p["items_preview"] = cursor.fetchall()

    finally:
        conn.close()

    return render_template("pedidos_abiertos.html", pedidos=pedidos)




# ================== CERRAR PEDIDOS ==================


@app.route("/cerrar_pedido/<int:pedido_id>", methods=["POST"])
def cerrar_pedido(pedido_id):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE pedidos
                SET estado = 'cerrado'
                WHERE id = %s
            """, (pedido_id,))
            conn.commit()
            flash("Pedido cerrado correctamente", "success")
    finally:
        conn.close()

    return redirect(url_for("pedidos_abiertos"))






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

            cursor.execute("""
                SELECT * FROM productos
                WHERE activo = 1
                ORDER BY categoria, nombre
            """)
            productos = cursor.fetchall()

            cursor.execute("SELECT * FROM salsas")
            salsas = cursor.fetchall()

            cursor.execute("SELECT * FROM proteinas")
            proteinas = cursor.fetchall()

            if request.method == "POST":

                fecha = request.form.get("fecha")
                if not fecha:
                    cursor.execute("SELECT NOW() AS ahora")
                    fecha = cursor.fetchone()["ahora"]

                origen = request.form["origen"].strip().lower()
                mesero = request.form.get("mesero", "")
                metodo_pago = request.form["metodo_pago"]
                monto_uber = Decimal(request.form.get("monto_uber", "0") or "0")

                productos_ids = request.form.getlist("producto_id[]")
                cantidades = request.form.getlist("cantidad[]")

                total = Decimal("0")
                items = []

                for i, prod_id in enumerate(productos_ids):
                    cant = int(cantidades[i])
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
                    })

                neto = total + monto_uber

                cursor.execute("""
                    INSERT INTO pedidos
                    (fecha, origen, mesero, metodo_pago, total, monto_uber, neto, estado)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'abierto')
                """, (
                    fecha,
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
                        (pedido_id, producto_id, cantidad, precio_unitario, subtotal)
                        VALUES (%s,%s,%s,%s,%s)
                    """, (
                        pedido_id,
                        it["producto_id"],
                        it["cantidad"],
                        it["precio_unitario"],
                        it["subtotal"],
                    ))

                conn.commit()
                flash(f"Pedido #{pedido_id} creado y abierto", "success")
                return redirect(url_for("ver_pedido", pedido_id=pedido_id))

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

            # Ingresos
            cursor.execute(f"""
                SELECT DATE_FORMAT(fecha, '%%Y-%%m') AS mes,
                       SUM(total) AS total
                FROM pedidos
                {filtro}
                GROUP BY mes
                ORDER BY mes
            """, params)
            ingresos = cursor.fetchall()

            # Costos
            cursor.execute(f"""
                SELECT DATE_FORMAT(fecha, '%%Y-%%m') AS mes,
                       SUM(costo) AS costo
                FROM insumos_compras
                {filtro}
                GROUP BY mes
                ORDER BY mes
            """, params)
            costos = cursor.fetchall()

            # Costos por tipo
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

            # Ventas por día
            cursor.execute(f"""
                    SELECT
                        DATE(fecha) AS dia,
                        DAYNAME(fecha) AS dia_semana,
                        COUNT(*) AS pedidos,
                        SUM(total) AS total,
                        SUM(neto) AS neto
                    FROM pedidos
                    {filtro}
                    GROUP BY DATE(fecha), DAYNAME(fecha)
                    ORDER BY dia DESC
                """, params)

            ventas_dia = cursor.fetchall()

            # Meses disponibles
            cursor.execute("""
                SELECT DISTINCT DATE_FORMAT(fecha, '%Y-%m') AS mes
                FROM pedidos
                ORDER BY mes DESC
            """)
            meses_disponibles = [m["mes"] for m in cursor.fetchall()]

            # Top productos
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

            # Top gastos
            cursor.execute("""
                SELECT concepto,
                       tipo_costo,
                       COUNT(*) AS veces,
                       SUM(costo) AS total_gastado
                FROM insumos_compras
                WHERE (%s IS NULL OR DATE_FORMAT(fecha, '%%Y-%%m') = %s)
                GROUP BY concepto, tipo_costo
                ORDER BY total_gastado DESC
                LIMIT 10
            """, (mes, mes))
            top_gastos = cursor.fetchall()

            # Promedios
            cursor.execute("""
                SELECT
                    AVG(pedidos) AS avg_pedidos,
                    AVG(total) AS avg_total,
                    AVG(neto) AS avg_neto
                FROM (
                    SELECT DATE(fecha),
                           COUNT(*) AS pedidos,
                           SUM(total) AS total,
                           SUM(neto) AS neto
                    FROM pedidos
                    WHERE (%s IS NULL OR DATE_FORMAT(fecha, '%%Y-%%m') = %s)
                    GROUP BY DATE(fecha)
                ) t
            """, (mes, mes))
            promedios_dia = cursor.fetchone()

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
        promedios_dia=promedios_dia,
        top_gastos=top_gastos,
    )


# ================== DASHBOARD ==================



@app.route("/pedido/<int:pedido_id>/eliminar_item/<int:item_id>", methods=["POST"])
def eliminar_item_pedido(pedido_id, item_id):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:

            # Verificar pedido abierto y obtener subtotal
            cursor.execute("""
                SELECT pe.estado, pi.subtotal
                FROM pedidos pe
                JOIN pedido_items pi ON pi.pedido_id = pe.id
                WHERE pe.id = %s AND pi.id = %s
            """, (pedido_id, item_id))

            row = cursor.fetchone()
            if not row:
                flash("Item no encontrado", "error")
                return redirect(url_for("ver_pedido", pedido_id=pedido_id))

            if row["estado"] != "abierto":
                flash("No se puede modificar un pedido cerrado", "error")
                return redirect(url_for("ver_pedido", pedido_id=pedido_id))

            subtotal = Decimal(row["subtotal"])

            # Eliminar item
            cursor.execute("""
                DELETE FROM pedido_items
                WHERE id = %s AND pedido_id = %s
            """, (item_id, pedido_id))

            # Recalcular totales
            cursor.execute("""
                UPDATE pedidos
                SET total = total - %s,
                    neto = neto - %s
                WHERE id = %s
            """, (subtotal, subtotal, pedido_id))

            conn.commit()
            flash("Producto eliminado del pedido", "success")

    finally:
        conn.close()

    return redirect(url_for("ver_pedido", pedido_id=pedido_id))



# ================== Eliminar pedido ==================



@app.route("/eliminar_pedido/<int:pedido_id>", methods=["POST"])
def eliminar_pedido(pedido_id):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:

            # Verificar que el pedido exista y esté abierto
            cursor.execute("""
                SELECT estado
                FROM pedidos
                WHERE id = %s
            """, (pedido_id,))
            pedido = cursor.fetchone()

            if not pedido:
                flash("Pedido no encontrado", "error")
                return redirect(url_for("pedidos_abiertos"))

            if pedido["estado"] != "abierto":
                flash("No se puede eliminar un pedido cerrado", "error")
                return redirect(url_for("pedidos_abiertos"))

            # Eliminar items
            cursor.execute("""
                DELETE FROM pedido_items
                WHERE pedido_id = %s
            """, (pedido_id,))

            # Eliminar pedido
            cursor.execute("""
                DELETE FROM pedidos
                WHERE id = %s
            """, (pedido_id,))

            conn.commit()
            flash(f"Pedido #{pedido_id} eliminado correctamente", "success")

    finally:
        conn.close()

    return redirect(url_for("pedidos_abiertos"))


# ================== generar_ticket_texto ==================



def generar_ticket_texto(pedido_id, cursor):
    cursor.execute("""
        SELECT p.nombre, pi.cantidad, pi.precio_unitario
        FROM pedido_items pi
        JOIN productos p ON p.id = pi.producto_id
        WHERE pi.pedido_id = %s
    """, (pedido_id,))
    items = cursor.fetchall()

    cursor.execute("""
        SELECT total
        FROM pedidos
        WHERE id = %s
    """, (pedido_id,))
    pedido = cursor.fetchone()

    lines = []
    lines.append("SEÑOR CHILAQUIL")
    lines.append("------------------------")

    for it in items:
        subtotal = it["cantidad"] * it["precio_unitario"]
        lines.append(f'{it["cantidad"]} {it["nombre"]} - ${subtotal:.2f}')

    lines.append("------------------------")
    lines.append(f'TOTAL: ${pedido["total"]:.2f}')
    lines.append("")
    lines.append("¡Gracias por tu compra!")

    return "\n".join(lines)



# ================== generar ticket whats ==================


import urllib.parse

def generar_ticket_whatsapp(pedido_id, cursor):
    cursor.execute("""
        SELECT p.nombre, pi.cantidad, pi.precio_unitario
        FROM pedido_items pi
        JOIN productos p ON p.id = pi.producto_id
        WHERE pi.pedido_id = %s
    """, (pedido_id,))
    items = cursor.fetchall()

    cursor.execute("""
        SELECT total
        FROM pedidos
        WHERE id = %s
    """, (pedido_id,))
    pedido = cursor.fetchone()

    lines = []
    lines.append("Señor Chilaquil")
    lines.append("--------------------")

    for it in items:
        subtotal = it["cantidad"] * it["precio_unitario"]
        lines.append(f'{it["cantidad"]} {it["nombre"]} - ${subtotal:.2f}')

    lines.append("--------------------")
    lines.append(f'Total: ${pedido["total"]:.2f}')
    lines.append("")
    lines.append("¡Gracias por tu compra!")

    mensaje = "\n".join(lines)
    return urllib.parse.quote(mensaje)



# ================== enviar ticekt a whats ==================


@app.route("/pedido/<int:pedido_id>/whatsapp")
def enviar_ticket_whatsapp(pedido_id):
    telefono = request.args.get("tel")  # 👈 viene del modal

    if not telefono:
        flash("Número no válido", "error")
        return redirect(url_for("ver_pedido", pedido_id=pedido_id))

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            mensaje = generar_ticket_whatsapp(pedido_id, cursor)
    finally:
        conn.close()

    return redirect(f"https://wa.me/{telefono}?text={mensaje}")




# ================== Preview ticket ==================


from flask import jsonify
import urllib.parse

@app.route("/pedido/<int:pedido_id>/ticket_preview")
def ticket_preview(pedido_id):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            texto = generar_ticket_texto(pedido_id, cursor)
            mensaje = urllib.parse.quote(texto)
    finally:
        conn.close()

    return jsonify({
        "texto": texto,
        "whatsapp_url": f"https://wa.me/?text={mensaje}"
    })


# ================== RUN ==================
if __name__ == "__main__":
    app.run(debug=True)
