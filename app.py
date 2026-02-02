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
    """
    wa.me NO quiere el '+'. Además: encode/quote correcto en UTF-8 bytes
    para evitar caracteres � en WhatsApp.
    """
    phone = (phone_e164 or "").replace("+", "")

    # Fuerza UTF-8 válido (si truena aquí, hay un bug en tu string)
    msg_bytes = message_text.encode("utf-8", "strict")

    # URL-encode sobre bytes UTF-8 (más robusto que quote(str))
    msg_q = urllib.parse.quote_from_bytes(msg_bytes)

    return f"https://wa.me/{phone}?text={msg_q}"


def make_bar(balance: int, goal: int) -> str:
    if goal <= 0:
        return ""
    prog = balance % goal
    if prog == 0 and balance > 0:
        prog = goal
    filled = min(prog, goal)
    return (BAR_ON * filled) + (BAR_OFF * (goal - filled))


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
    row = cursor.fetchone()
    return row["totopos_balance"] if row else 0


# ================== ICONOS ASCII (100% SEGUROS) ==================
E = {
    "title": "*",     # título
    "receipt": "#",   # pedido
    "pay": "$",       # pago
    "check": "OK",    # check
    "pin": "-",       # bullet
    "gift": "*",      # sección
    "drink": "Una bebida gratis", # bebida
    "plate": "Un plato fuerte gratis", # plato
    "arrow": "->",    # flecha
}

BAR_ON = "#"     # progreso
BAR_OFF = "-"    # restante


def make_bar(balance: int, goal: int) -> str:
    if goal <= 0:
        return ""
    prog = balance % goal
    if prog == 0 and balance > 0:
        prog = goal
    filled = min(prog, goal)
    return (BAR_ON * filled) + (BAR_OFF * (goal - filled))


def loyalty_message(balance: int, earned: int, pedido_id: int, total: Decimal) -> str:
    bar5 = make_bar(balance, 5)
    bar10 = make_bar(balance, 10)
    f5 = faltan_para(balance, 5)
    f10 = faltan_para(balance, 10)

    canje = []
    if f5 == 0:
        canje.append(f"{E['check']} Ya puedes canjear una bebida (excepto chai).")
    if f10 == 0:
        canje.append(f"{E['check']} Ya puedes canjear un plato fuerte.")
    canje_txt = "\n".join(canje) if canje else "Sigue acumulando totopos :)"

    # TODO ASCII => cero �
    return (
        f"{E['title']} SENOR CHILAQUIL - TOTOPOS {E['title']}\n\n"
        f"{E['receipt']} Pedido {pedido_id}   {E['pay']} Total: {float(total):.2f}\n"
        f"{E['check']} Ganaste hoy: +{earned} totopo(s)\n"
        f"{E['pin']} Totopos acumulados: {balance}\n\n"
        f"{E['gift']} Recompensas\n"
        f"{E['drink']}: {balance}/5  {bar5}\n"
        f"{E['plate']}: {balance}/10 {bar10}\n\n"
        "Te faltan:\n"
        f"{E['arrow']} {f5} para una bebida gratis\n"
        f"{E['arrow']} {f10} para un platofuerte \n\n"
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
                       pi.salsa_id, pi.proteina_id,
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

                # legacy (texto para cocina)
                proteinas_sel = request.form.getlist("proteina[]")
                sin_sel = request.form.getlist("sin[]")
                notas_sel = request.form.getlist("nota[]")

                # ✅ nuevos (IDs)
                proteinas_id_sel = request.form.getlist("proteina_id[]")
                salsas_id_sel = request.form.getlist("salsa_id[]")

                def safe_get(lst, i, default=""):
                    return lst[i] if i < len(lst) else default

                def safe_int_or_none(val):
                    v = (val or "").strip()
                    if not v:
                        return None
                    return int(v) if v.isdigit() else None

                total_agregado = Decimal("0")

                for i, prod_id in enumerate(productos_ids):
                    cant = int(cantidades[i])
                    if cant <= 0:
                        continue

                    prot_txt = safe_get(proteinas_sel, i, "")
                    sin_txt = safe_get(sin_sel, i, "")
                    nota = safe_get(notas_sel, i, "")

                    proteina_id = safe_int_or_none(safe_get(proteinas_id_sel, i, ""))
                    salsa_id = safe_int_or_none(safe_get(salsas_id_sel, i, ""))

                    cursor.execute("SELECT precio FROM productos WHERE id = %s", (prod_id,))
                    row = cursor.fetchone()
                    if not row:
                        continue

                    precio = Decimal(row["precio"])

                    # ✅ IMPORTANTÍSIMO: comparar también IDs para no mezclar líneas distintas
                    cursor.execute("""
                        SELECT id, cantidad
                        FROM pedido_items
                        WHERE pedido_id = %s
                          AND producto_id = %s
                          AND (proteina <=> %s)
                          AND (sin <=> %s)
                          AND (nota <=> %s)
                          AND (proteina_id <=> %s)
                          AND (salsa_id <=> %s)
                    """, (pedido_id, prod_id, prot_txt, sin_txt, nota, proteina_id, salsa_id))
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
                            (pedido_id, producto_id, proteina, sin, salsa_id, proteina_id, nota, cantidad, precio_unitario, subtotal)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """, (
                            pedido_id, int(prod_id),
                            prot_txt, sin_txt,
                            salsa_id, proteina_id,
                            nota,
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
            # 1) Validar estado
            cursor.execute("SELECT estado FROM pedidos WHERE id=%s", (pedido_id,))
            row = cursor.fetchone()
            if not row:
                flash("Pedido no encontrado", "error")
                return redirect(url_for("pedidos_abiertos"))

            if row["estado"] != "abierto":
                flash("Este pedido ya está cerrado", "error")
                return redirect(url_for("pedidos_abiertos"))

            # 2) Cerrar
            cursor.execute("""
                UPDATE pedidos
                SET estado = 'cerrado'
                WHERE id = %s
            """, (pedido_id,))

            # 3) Descontar inventario (MISMO cursor)
            descontar_stock_por_pedido_cursor(cursor, pedido_id)

            conn.commit()
            flash("Pedido cerrado correctamente (inventario actualizado)", "success")
            return redirect(url_for("pedidos_abiertos"))
    finally:
        conn.close()



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

            # 1) Cerrar pedido
            cursor.execute("UPDATE pedidos SET estado='cerrado' WHERE id=%s", (pedido_id,))

            # 2) Totopos si hay teléfono
            earned = 0
            balance = None
            if phone:
                earned = 1
                customer_id = loyalty_get_or_create_customer(cursor, phone)
                balance = loyalty_add_totopos_for_purchase(cursor, customer_id, pedido_id, earned)

            # 3) Descontar inventario (MISMO cursor)
            descontar_stock_por_pedido_cursor(cursor, pedido_id)

            # 4) Armar mensaje WhatsApp (si aplica)
            if phone:
                ticket_text = generar_ticket_texto(pedido_id, cursor)  # usa el cursor actual
                msg_loyalty = loyalty_message(balance, earned, pedido_id, Decimal(pedido["total"]))
                full_message = ticket_text + "\n\n" + msg_loyalty

                conn.commit()
                return redirect(wa_me_link(phone, full_message))

            conn.commit()
            flash("Pedido cerrado. (Sin WhatsApp porque no hay teléfono)", "success")
            return redirect(url_for("pedidos_abiertos"))
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

                # legacy (texto)
                proteinas_sel = request.form.getlist("proteina[]")
                sin_sel = request.form.getlist("sin[]")
                notas_sel = request.form.getlist("nota[]")

                # ✅ nuevos (IDs)
                proteinas_id_sel = request.form.getlist("proteina_id[]")
                salsas_id_sel = request.form.getlist("salsa_id[]")

                def safe_get(lst, i, default=""):
                    return lst[i] if i < len(lst) else default

                def safe_int_or_none(val):
                    v = (val or "").strip()
                    if not v:
                        return None
                    return int(v) if v.isdigit() else None

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
                        "producto_id": int(prod_id),
                        "cantidad": cant,
                        "precio_unitario": precio_unit,
                        "subtotal": subtotal,

                        # legacy (texto)
                        "proteina": safe_get(proteinas_sel, i, ""),
                        "sin": safe_get(sin_sel, i, ""),
                        "nota": safe_get(notas_sel, i, ""),

                        # ✅ nuevos (IDs)
                        "proteina_id": safe_int_or_none(safe_get(proteinas_id_sel, i, "")),
                        "salsa_id": safe_int_or_none(safe_get(salsas_id_sel, i, "")),
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
                        (pedido_id, producto_id, proteina, sin, salsa_id, proteina_id, nota, cantidad, precio_unitario, subtotal)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """, (
                        pedido_id,
                        it["producto_id"],
                        it["proteina"],
                        it["sin"],
                        it["salsa_id"],
                        it["proteina_id"],
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
        (fecha, lugar, cantidad, unidad, concepto, costo, tipo_costo, nota, insumo_id, cantidad_base, unidad_base, costo_unitario, es_insumo)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        request.form["fecha"],
        request.form["lugar"],
        request.form["cantidad"],
        request.form["unidad"],
        request.form["concepto"],
        request.form["costo"],
        request.form["tipo_costo"],
        request.form.get("nota", ""),
        request.form.get("insumo_id") or None,
        request.form.get("cantidad_base") or None,
        request.form.get("unidad_base") or None,
        request.form.get("costo_unitario") or None,
        1 if (request.form.get("es_insumo") == "1") else 0,
    ))
    compra_id = cursor.lastrowid

    # ✅ Si es insumo, crea entrada_compra
    if request.form.get("es_insumo") == "1" and request.form.get("insumo_id") and request.form.get("cantidad_base"):
        cursor.execute("""
            INSERT IGNORE INTO inventario_movimientos
                (insumo_id, cantidad_base, tipo, ref_tabla, ref_id, nota)
            VALUES
                (%s, %s, 'entrada_compra', 'insumos_compras', %s, %s)
        """, (
            int(request.form["insumo_id"]),
            str(Decimal(request.form["cantidad_base"])),
            compra_id,
            f"Entrada por compra #{compra_id}"
        ))

    conn.commit()
    flash("Compra registrada correctamente", "success")


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
            cursor.execute("SELECT id, estado FROM pedidos WHERE id = %s", (pedido_id,))
            pedido = cursor.fetchone()

            if not pedido:
                flash("Pedido no encontrado", "error")
                return redirect(url_for("borrar_pedidos"))

            # ✅ Si tienes loyalty_tx referenciando pedido_id, hay que borrar eso primero
            cursor.execute("DELETE FROM loyalty_tx WHERE pedido_id = %s", (pedido_id,))

            # Borra items primero (FK safety)
            cursor.execute("DELETE FROM pedido_items WHERE pedido_id = %s", (pedido_id,))
            cursor.execute("DELETE FROM pedidos WHERE id = %s", (pedido_id,))

            conn.commit()
            flash(f"Pedido #{pedido_id} eliminado correctamente (abierto o cerrado).", "success")

    except Exception as e:
        conn.rollback()
        flash(f"Error eliminando pedido #{pedido_id}: {e}", "error")
    finally:
        conn.close()

    # ✅ Regresa a la pantalla de borrar pedidos
    return redirect(url_for("borrar_pedidos"))


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


# ================== generar ticket whats (SIN QUOTE) ==================
def generar_ticket_whatsapp(pedido_id, cursor):
    return generar_ticket_texto(pedido_id, cursor)


# ================== enviar ticket a whats (LEGACY) ==================
@app.route("/pedido/<int:pedido_id>/whatsapp")
def enviar_ticket_whatsapp(pedido_id):
    tel_raw = request.args.get("tel", "").strip()
    telefono_e164 = normalize_phone_mx(tel_raw)

    if not telefono_e164:
        flash("Número no válido", "error")
        return redirect(url_for("ver_pedido", pedido_id=pedido_id))

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            texto = generar_ticket_whatsapp(pedido_id, cursor)  # TEXTO CRUDO
    finally:
        conn.close()

    return redirect(wa_me_link(telefono_e164, texto))


# ================== Preview ticket ==================
@app.route("/pedido/<int:pedido_id>/ticket_preview")
def ticket_preview(pedido_id):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            texto = generar_ticket_texto(pedido_id, cursor)
    finally:
        conn.close()

    # URL para WhatsApp sin número (solo preview)
    msg_q = urllib.parse.quote_from_bytes(texto.encode("utf-8", "strict"))

    return jsonify({
        "texto": texto,
        "whatsapp_url": f"https://wa.me/?text={msg_q}"
    })


# ================== BORRAR PEDIDOS (UI + ACCIONES) ==================
# Pega TODO este bloque en app.py (por ejemplo, antes de "# ================== RUN ==================")

@app.route("/borrar_pedidos", methods=["GET"])
def borrar_pedidos():
    """
    Pantalla para listar pedidos y permitir borrarlos (individual o bulk).
    Filtros por querystring:
      estado, origen, mesero, pedido_id, desde, hasta
    """
    estado = (request.args.get("estado") or "").strip().lower()
    origen = (request.args.get("origen") or "").strip().lower()
    mesero = (request.args.get("mesero") or "").strip()
    pedido_id = (request.args.get("pedido_id") or "").strip()
    desde = (request.args.get("desde") or "").strip()  # YYYY-MM-DD
    hasta = (request.args.get("hasta") or "").strip()  # YYYY-MM-DD

    where = []
    params = []

    if estado in ("abierto", "cerrado"):
        where.append("estado = %s")
        params.append(estado)

    if origen:
        where.append("LOWER(origen) LIKE %s")
        params.append(f"%{origen}%")

    if mesero:
        where.append("mesero LIKE %s")
        params.append(f"%{mesero}%")

    if pedido_id.isdigit():
        where.append("id = %s")
        params.append(int(pedido_id))

    if desde:
        where.append("DATE(fecha) >= %s")
        params.append(desde)

    if hasta:
        where.append("DATE(fecha) <= %s")
        params.append(hasta)

    filtro_sql = ("WHERE " + " AND ".join(where)) if where else ""

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"""
                SELECT id, fecha, origen, mesero, total, estado
                FROM pedidos
                {filtro_sql}
                ORDER BY fecha DESC
                LIMIT 300
            """, params)
            pedidos = cursor.fetchall()
    finally:
        conn.close()

    return render_template(
        "borrar_pedidos.html",
        pedidos=pedidos,
        estado=estado or "",
        origen=origen or "",
        mesero=mesero or "",
        pedido_id=pedido_id or "",
        desde=desde or "",
        hasta=hasta or "",
    )


@app.route("/borrar_pedidos_bulk", methods=["POST"])
def borrar_pedidos_bulk():
    """
    POST handler para:
      - borrar seleccionados (pedido_ids[])
      - borrar todos los abiertos
    """
    modo = (request.form.get("modo") or "").strip()

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            if modo == "borrar_todos_abiertos":
                # 1) borra items de pedidos abiertos
                cursor.execute("""
                    DELETE pi
                    FROM pedido_items pi
                    JOIN pedidos pe ON pe.id = pi.pedido_id
                    WHERE pe.estado = 'abierto'
                """)
                # 2) borra pedidos abiertos
                cursor.execute("DELETE FROM pedidos WHERE estado = 'abierto'")
                conn.commit()
                flash("Se borraron TODOS los pedidos abiertos.", "success")
                return redirect(url_for("borrar_pedidos", estado="abierto"))

            # default: borrar seleccionados
            ids = request.form.getlist("pedido_ids[]")
            ids_int = []
            for x in ids:
                x = (x or "").strip()
                if x.isdigit():
                    ids_int.append(int(x))

            if not ids_int:
                flash("No seleccionaste pedidos para borrar.", "error")
                return redirect(url_for("borrar_pedidos"))

            # Borra items primero (FK safety)
            placeholders = ",".join(["%s"] * len(ids_int))

            cursor.execute(
                f"DELETE FROM pedido_items WHERE pedido_id IN ({placeholders})",
                ids_int
            )
            cursor.execute(
                f"DELETE FROM pedidos WHERE id IN ({placeholders})",
                ids_int
            )

            conn.commit()
            flash(f"Se borraron {len(ids_int)} pedido(s).", "success")
            return redirect(url_for("borrar_pedidos"))
    finally:
        conn.close()



import pymysql
from decimal import Decimal
from db import get_connection

def descontar_stock_por_pedido(pedido_id: int) -> None:
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            conn.begin()

            # Traer items vendidos + platillo_id + proteina_id
            cur.execute("""
                SELECT
                    pi.cantidad AS cantidad_vendida,
                    p.platillo_id,
                    pi.proteina_id
                FROM pedido_items pi
                JOIN productos p ON p.id = pi.producto_id
                WHERE pi.pedido_id = %s
            """, (pedido_id,))
            items = cur.fetchall()

            if not items:
                conn.commit()
                return

            consumo = {}  # insumo_id -> Decimal total

            for it in items:
                qty = Decimal(str(it["cantidad_vendida"] or 0))
                platillo_id = it["platillo_id"]
                proteina_id = it["proteina_id"]

                if not platillo_id:
                    continue

                # (1) Receta base
                cur.execute("""
                    SELECT r.insumo_id, r.cantidad_base
                    FROM recetas r
                    JOIN insumos i ON i.id = r.insumo_id
                    WHERE r.platillo_id = %s
                      AND i.descuenta_stock = 1
                """, (platillo_id,))
                receta_base = cur.fetchall()

                for r in receta_base:
                    insumo_id = r["insumo_id"]
                    cant_base = Decimal(str(r["cantidad_base"]))
                    consumo[insumo_id] = consumo.get(insumo_id, Decimal("0")) + (cant_base * qty)

                # (2) Receta por proteína (si aplica)
                if proteina_id:
                    cur.execute("""
                        SELECT cantidad_base
                        FROM receta_proteinas
                        WHERE platillo_id = %s AND proteina_id = %s
                        LIMIT 1
                    """, (platillo_id, proteina_id))
                    rp = cur.fetchone()

                    if rp:
                        cant_prot = Decimal(str(rp["cantidad_base"]))

                        cur.execute("""
                            SELECT insumo_id
                            FROM proteinas
                            WHERE id = %s
                            LIMIT 1
                        """, (proteina_id,))
                        pr = cur.fetchone()

                        if pr and pr["insumo_id"]:
                            insumo_prot = int(pr["insumo_id"])

                            cur.execute("SELECT descuenta_stock FROM insumos WHERE id=%s", (insumo_prot,))
                            si = cur.fetchone()
                            if si and int(si["descuenta_stock"]) == 1:
                                consumo[insumo_prot] = consumo.get(insumo_prot, Decimal("0")) + (cant_prot * qty)

            if not consumo:
                conn.commit()
                return

            rows = []
            for insumo_id, total_salida in consumo.items():
                rows.append((
                    insumo_id,
                    str(-total_salida),
                    "salida_venta",
                    "pedidos",
                    pedido_id,
                    f"Salida automática por pedido #{pedido_id}"
                ))

            cur.executemany("""
                INSERT IGNORE INTO inventario_movimientos
                    (insumo_id, cantidad_base, tipo, ref_tabla, ref_id, nota)
                VALUES
                    (%s, %s, %s, %s, %s, %s)
            """, rows)

            conn.commit()

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
















import pymysql
from flask import render_template, request
from db import get_connection

@app.route("/inventario/stock")
def ver_stock():
    q = (request.args.get("q") or "").strip()

    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("""
                SELECT insumo_id, nombre, unidad_base, stock_actual
                FROM vw_stock_actual
                WHERE (%s = '' OR nombre LIKE %s)
                ORDER BY nombre
            """, (q, f"%{q}%"))

            rows = cur.fetchall()

        return render_template("stock.html", rows=rows, q=q)

    finally:
        conn.close()

import pymysql
from decimal import Decimal, InvalidOperation
from flask import request, redirect, url_for, flash
from db import get_connection

@app.post("/inventario/stock/agregar")
def agregar_stock():
    insumo_id = (request.form.get("insumo_id") or "").strip()
    cantidad_txt = (request.form.get("cantidad") or "").strip()
    q = (request.form.get("q") or "").strip()  # para conservar filtro

    if not insumo_id.isdigit():
        flash("Insumo inválido.", "error")
        return redirect(url_for("ver_stock", q=q))

    try:
        cantidad = Decimal(cantidad_txt)
    except (InvalidOperation, TypeError):
        flash("Cantidad inválida.", "error")
        return redirect(url_for("ver_stock", q=q))

    if cantidad <= 0:
        flash("La cantidad debe ser mayor a 0.", "error")
        return redirect(url_for("ver_stock", q=q))

    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            conn.start_transaction()

            # Seguridad: no permitir agregar a insumos desactivados
            cur.execute("SELECT activo, unidad_base FROM insumos WHERE id=%s", (int(insumo_id),))
            ins = cur.fetchone()
            if not ins or int(ins["activo"]) != 1:
                conn.rollback()
                flash("El insumo no está activo.", "error")
                return redirect(url_for("ver_stock", q=q))

            # Insertar movimiento (entrada manual, positivo)
            cur.execute("""
                INSERT INTO inventario_movimientos
                    (insumo_id, cantidad_base, tipo, ref_tabla, ref_id, nota)
                VALUES
                    (%s, %s, 'entrada_manual', 'stock_ui', NULL, 'Entrada manual desde /inventario/stock')
            """, (int(insumo_id), str(cantidad)))

            conn.commit()

        flash(f"Stock agregado ✅ (+{cantidad} {ins['unidad_base']})", "success")
        return redirect(url_for("ver_stock", q=q))

    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


# ================== RUN ==================
if __name__ == "__main__":
    app.run(debug=True)
