from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from decimal import Decimal
from db import get_connection
from costeo import costeo_bp

import urllib.parse
import re


app = Flask(__name__)
app.secret_key = "super_secret_key"  # cámbiala en prod

# ================== COSTEO ==================
app.register_blueprint(costeo_bp)


# =========================================================
# ================== LOYALTY (TOTOPOS) ====================
# =========================================================

def normalize_phone_mx(raw: str) -> str | None:
    """
    Acepta: '449 741 9166', '524497419166', '+52 4497419166'
    Devuelve: '+524497419166' o None si no es válido.
    """
    if not raw:
        return None

    s = re.sub(r"[^\d+]", "", raw).strip()

    if s.startswith("+"):
        s_digits = re.sub(r"\D", "", s)
    else:
        s_digits = re.sub(r"\D", "", s)

    # 10 dígitos -> +52
    if len(s_digits) == 10:
        return "+52" + s_digits

    # 12 dígitos empezando con 52 -> +52...
    if len(s_digits) == 12 and s_digits.startswith("52"):
        return "+" + s_digits

    # 13 dígitos empezando con 521 (a veces)
    if len(s_digits) == 13 and s_digits.startswith("521"):
        return "+" + s_digits

    return None




def wa_me_link(phone_e164: str, message_text: str) -> str:
    phone = phone_e164.replace("+", "")

    # 1) Fuerza UTF-8 real (si hay algo inválido, truena aquí y lo detectas)
    msg_bytes = message_text.encode("utf-8", "strict")

    # 2) URL-encode sobre BYTES UTF-8
    msg_q = urllib.parse.quote_from_bytes(msg_bytes)

    return f"https://wa.me/{phone}?text={msg_q}"



def make_bar(balance: int, goal: int) -> str:
    if goal <= 0:
        return ""
    prog = balance % goal
    if prog == 0 and balance > 0:
        prog = goal  # listo
    filled = min(prog, goal)
    return "🟨" * filled + "⬜" * (goal - filled)


def faltan_para(balance: int, goal: int) -> int:
    if goal <= 0:
        return 0
    r = balance % goal
    return 0 if (r == 0 and balance > 0) else (goal - r)


def loyalty_get_or_create_customer(cursor, phone_e164: str) -> int:
    cursor.execute("SELECT id FROM loyalty_customers WHERE phone_e164=%s", (phone_e164,))
    row = cursor.fetchone()

    if row:
        return row["id"]

    cursor.execute(
        "INSERT INTO loyalty_customers (phone_e164) VALUES (%s)",
        (phone_e164,)
    )
    customer_id = cursor.lastrowid

    cursor.execute(
        "INSERT INTO loyalty_accounts (customer_id, totopos_balance, totopos_lifetime) VALUES (%s,0,0)",
        (customer_id,)
    )
    return customer_id


def loyalty_add_totopos_for_purchase(cursor, customer_id: int, pedido_id: int, earned: int) -> int:
    if earned <= 0:
        cursor.execute("SELECT totopos_balance FROM loyalty_accounts WHERE customer_id=%s", (customer_id,))
        row = cursor.fetchone()
        return row["totopos_balance"] if row else 0

    cursor.execute("""
        UPDATE loyalty_accounts
        SET totopos_balance = totopos_balance + %s,
            totopos_lifetime = totopos_lifetime + %s
        WHERE customer_id=%s
    """, (earned, earned, customer_id))

    cursor.execute("""
        INSERT INTO loyalty_tx (customer_id, pedido_id, delta, reason)
        VALUES (%s,%s,%s,'purchase')
    """, (customer_id, pedido_id, earned))

    cursor.execute("SELECT totopos_balance FROM loyalty_accounts WHERE customer_id=%s", (customer_id,))
    return cursor.fetchone()["totopos_balance"]


def loyalty_message(balance: int, earned: int, pedido_id: int, total: Decimal) -> str:
    bar5 = make_bar(balance, 5)
    bar10 = make_bar(balance, 10)
    f5 = faltan_para(balance, 5)
    f10 = faltan_para(balance, 10)

    canje = []
    if f5 == 0:
        canje.append("🥤 Ya puedes canjear una bebida (excepto chai).")
    if f10 == 0:
        canje.append("🍽️ Ya puedes canjear un plato fuerte.")
    canje_txt = "\n".join(canje) if canje else "Sigue acumulando totopos 😉"

    return (
        "🌶️ Señor Chilaquil — Totopos 🟨🟥\n\n"
        f"🧾 Pedido #{pedido_id}   💳 Total: ${float(total):.2f}\n"
        f"✅ Ganaste hoy: +{earned} totopo(s) 🟨\n"
        f"📌 Totopos acumulados: {balance} 🟨\n\n"
        "🎁 Recompensas\n"
        f"🥤 Bebida (excepto chai): {balance}/5  {bar5}\n"
        f"🍽️ Plato fuerte:          {balance}/10 {bar10}\n\n"
        "Te faltan:\n"
        f"➡️ {f5} para bebida 🥤\n"
        f"➡️ {f10} para plato 🍽️\n\n"
        f"{canje_txt}\n"
    )


# ================== FILTRO DE MONEDA ==================
@app.template_filter("money")
def money_format(value):
    try:
        return "${:,.2f}".format(float(value))
    except:
        return value


# ================== PEDIDOS ABIERTOS ==================
@app.route("/pedidos_abiertos")
def pedidos_abiertos():
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT id, fecha, origen, mesero, total
                FROM pedidos
                WHERE estado = 'abierto'
                ORDER BY fecha DESC
            """)
            pedidos = cursor.fetchall()

            for p in pedidos:
                cursor.execute("""
                    SELECT pr.nombre, pi.cantidad, pi.proteina, pi.sin, pi.nota
                    FROM pedido_items pi
                    JOIN productos pr ON pr.id = pi.producto_id
                    WHERE pi.pedido_id = %s
                    ORDER BY pi.id DESC
                    LIMIT 4
                """, (p["id"],))
                p["items_preview"] = cursor.fetchall()
    finally:
        conn.close()

    return render_template("pedidos_abiertos.html", pedidos=pedidos)


# ================== HOME ==================
@app.route("/")
def index():
    return redirect(url_for("nuevo_pedido"))


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
                SELECT pi.id, pi.cantidad, pi.precio_unitario, pi.subtotal,
                       pi.proteina, pi.sin, pi.nota,
                       p.nombre
                FROM pedido_items pi
                JOIN productos p ON p.id = pi.producto_id
                WHERE pi.pedido_id = %s
                ORDER BY pi.id DESC
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

                proteinas_sel = request.form.getlist("proteina[]")
                sin_sel = request.form.getlist("sin[]")
                notas_sel = request.form.getlist("nota[]")

                def safe_get(lst, i, default=""):
                    return lst[i] if i < len(lst) else default

                total_agregado = Decimal("0")

                for i, prod_id in enumerate(productos_ids):
                    cant = int(cantidades[i])
                    if cant <= 0:
                        continue

                    prot = safe_get(proteinas_sel, i, "")
                    sin_txt = safe_get(sin_sel, i, "")
                    nota = safe_get(notas_sel, i, "")

                    cursor.execute("SELECT precio FROM productos WHERE id = %s", (prod_id,))
                    row = cursor.fetchone()
                    if not row:
                        continue

                    precio = Decimal(row["precio"])

                    cursor.execute("""
                        SELECT id, cantidad
                        FROM pedido_items
                        WHERE pedido_id = %s
                          AND producto_id = %s
                          AND (proteina <=> %s)
                          AND (sin <=> %s)
                          AND (nota <=> %s)
                    """, (pedido_id, prod_id, prot, sin_txt, nota))
                    existente = cursor.fetchone()

                    if existente:
                        nueva_cantidad = existente["cantidad"] + cant
                        nuevo_subtotal = nueva_cantidad * precio

                        cursor.execute("""
                            UPDATE pedido_items
                            SET cantidad = %s,
                                subtotal = %s
                            WHERE id = %s
                        """, (nueva_cantidad, nuevo_subtotal, existente["id"]))

                        total_agregado += precio * cant
                    else:
                        subtotal = precio * cant
                        total_agregado += subtotal

                        cursor.execute("""
                            INSERT INTO pedido_items
                            (pedido_id, producto_id, proteina, sin, nota, cantidad, precio_unitario, subtotal)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                        """, (
                            pedido_id, prod_id, prot, sin_txt, nota,
                            cant, precio, subtotal
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

    return render_template("pedido.html", pedido=pedido, items=items, productos=productos)


# ================== CERRAR PEDIDO (NORMAL) ==================
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


# ================== CERRAR PEDIDO + WHATSAPP + TOTOPOS ==================
@app.route("/cerrar_pedido_whatsapp/<int:pedido_id>", methods=["POST"])
def cerrar_pedido_whatsapp(pedido_id):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT id, total, telefono_whatsapp, estado
                FROM pedidos
                WHERE id=%s
            """, (pedido_id,))
            pedido = cursor.fetchone()

            if not pedido:
                flash("Pedido no encontrado", "error")
                return redirect(url_for("pedidos_abiertos"))

            if pedido["estado"] != "abierto":
                flash("Este pedido ya está cerrado", "error")
                return redirect(url_for("pedidos_abiertos"))

            phone = pedido.get("telefono_whatsapp")
            if not phone:
                cursor.execute("UPDATE pedidos SET estado='cerrado' WHERE id=%s", (pedido_id,))
                conn.commit()
                flash("Pedido cerrado. (Sin WhatsApp porque no hay teléfono)", "success")
                return redirect(url_for("pedidos_abiertos"))

            # 1) Cerrar pedido
            cursor.execute("UPDATE pedidos SET estado='cerrado' WHERE id=%s", (pedido_id,))

            # 2) Sumar totopos (MVP: +1 por compra)
            earned = 1
            customer_id = loyalty_get_or_create_customer(cursor, phone)
            balance = loyalty_add_totopos_for_purchase(cursor, customer_id, pedido_id, earned)

            # 3) Ticket + totopos
            ticket_text = generar_ticket_texto(pedido_id, cursor)
            msg_loyalty = loyalty_message(balance, earned, pedido_id, Decimal(pedido["total"]))
            full_message = ticket_text + "\n\n" + msg_loyalty

            conn.commit()
            return redirect(wa_me_link(phone, full_message))
    finally:
        conn.close()


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

                tel_raw = request.form.get("telefono_whatsapp", "").strip()
                telefono_e164 = normalize_phone_mx(tel_raw) if tel_raw else None

                productos_ids = request.form.getlist("producto_id[]")
                cantidades = request.form.getlist("cantidad[]")

                proteinas_sel = request.form.getlist("proteina[]")
                sin_sel = request.form.getlist("sin[]")
                notas_sel = request.form.getlist("nota[]")

                def safe_get(lst, i, default=""):
                    return lst[i] if i < len(lst) else default

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
                        "proteina": safe_get(proteinas_sel, i, ""),
                        "sin": safe_get(sin_sel, i, ""),
                        "nota": safe_get(notas_sel, i, ""),
                    })

                neto = total + monto_uber

                cursor.execute("""
                    INSERT INTO pedidos
                    (fecha, origen, mesero, telefono_whatsapp, metodo_pago, total, monto_uber, neto, estado)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'abierto')
                """, (
                    fecha, origen, mesero, telefono_e164,
                    metodo_pago, total, monto_uber, neto
                ))

                pedido_id = cursor.lastrowid

                for it in items:
                    cursor.execute("""
                        INSERT INTO pedido_items
                        (pedido_id, producto_id, proteina, sin, nota, cantidad, precio_unitario, subtotal)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    """, (
                        pedido_id,
                        it["producto_id"],
                        it["proteina"],
                        it["sin"],
                        it["nota"],
                        it["cantidad"],
                        it["precio_unitario"],
                        it["subtotal"],
                    ))

                conn.commit()
                flash(f"Pedido #{pedido_id} creado y abierto", "success")
                return redirect(url_for("ver_pedido", pedido_id=pedido_id))
    finally:
        conn.close()

    return render_template("nuevo_pedido.html", productos=productos, salsas=salsas, proteinas=proteinas)


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


# ================== Eliminar item de pedido ==================
@app.route("/pedido/<int:pedido_id>/eliminar_item/<int:item_id>", methods=["POST"])
def eliminar_item_pedido(pedido_id, item_id):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
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

            cursor.execute("""
                DELETE FROM pedido_items
                WHERE id = %s AND pedido_id = %s
            """, (item_id, pedido_id))

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
            cursor.execute("SELECT estado FROM pedidos WHERE id = %s", (pedido_id,))
            pedido = cursor.fetchone()

            if not pedido:
                flash("Pedido no encontrado", "error")
                return redirect(url_for("pedidos_abiertos"))

            if pedido["estado"] != "abierto":
                flash("No se puede eliminar un pedido cerrado", "error")
                return redirect(url_for("pedidos_abiertos"))

            cursor.execute("DELETE FROM pedido_items WHERE pedido_id = %s", (pedido_id,))
            cursor.execute("DELETE FROM pedidos WHERE id = %s", (pedido_id,))

            conn.commit()
            flash(f"Pedido #{pedido_id} eliminado correctamente", "success")
    finally:
        conn.close()

    return redirect(url_for("pedidos_abiertos"))


# ================== generar_ticket_texto ==================
def generar_ticket_texto(pedido_id, cursor):
    cursor.execute("""
        SELECT p.nombre, pi.cantidad, pi.precio_unitario, pi.proteina, pi.sin, pi.nota
        FROM pedido_items pi
        JOIN productos p ON p.id = pi.producto_id
        WHERE pi.pedido_id = %s
        ORDER BY pi.id ASC
    """, (pedido_id,))
    items = cursor.fetchall()

    cursor.execute("SELECT total FROM pedidos WHERE id = %s", (pedido_id,))
    pedido = cursor.fetchone()

    lines = []
    lines.append("SEÑOR CHILAQUIL")
    lines.append("------------------------")

    for it in items:
        subtotal = it["cantidad"] * it["precio_unitario"]
        lines.append(f'{it["cantidad"]} {it["nombre"]} - ${subtotal:.2f}')

        if it.get("proteina"):
            lines.append(f'  PROT: {it["proteina"]}')
        if it.get("sin"):
            lines.append(f'  SIN: {it["sin"]}')
        if it.get("nota"):
            lines.append(f'  NOTA: {it["nota"]}')

    lines.append("------------------------")
    lines.append(f'TOTAL: ${pedido["total"]:.2f}')
    lines.append("")
    lines.append("¡Gracias por tu compra!")

    return "\n".join(lines)


# ================== generar ticket whats ==================
def generar_ticket_whatsapp(pedido_id, cursor):
    texto = generar_ticket_texto(pedido_id, cursor)
    return urllib.parse.quote(texto)


# ================== enviar ticket a whats (LEGACY) ==================
@app.route("/pedido/<int:pedido_id>/whatsapp")
def enviar_ticket_whatsapp(pedido_id):
    telefono = request.args.get("tel")

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
