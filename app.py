# app.py
from __future__ import annotations

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from decimal import Decimal, InvalidOperation
from db import get_connection
from costeo import costeo_bp

import urllib.parse
import re
import pymysql


app = Flask(__name__)
app.secret_key = "super_secret_key"  # cámbiala en prod

# ================== COSTEO ==================
app.register_blueprint(costeo_bp)


# =========================================================
# ================== HELPERS ==============================
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


def table_has_column(cursor, table_name: str, col_name: str) -> bool:
    cursor.execute("""
        SELECT 1
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND COLUMN_NAME = %s
        LIMIT 1
    """, (table_name, col_name))
    return cursor.fetchone() is not None


def wa_me_link(phone_e164: str, message_text: str) -> str:
    """
    wa.me NO quiere el '+'. Además: encode/quote correcto en UTF-8 bytes
    para evitar caracteres � en WhatsApp.
    """
    phone = (phone_e164 or "").replace("+", "")
    msg_bytes = message_text.encode("utf-8", "strict")
    msg_q = urllib.parse.quote_from_bytes(msg_bytes)
    return f"https://wa.me/{phone}?text={msg_q}"


# =========================================================
# ================== LOYALTY (TOTOPOS) ====================
# =========================================================

# ================== ICONOS ASCII (100% SEGUROS) ==================
E = {
    "title": "*",
    "receipt": "#",
    "pay": "$",
    "check": "OK",
    "pin": "-",
    "gift": "*",
    "drink": "Una bebida gratis",
    "plate": "Un plato fuerte gratis",
    "arrow": "->",
}

BAR_ON = "#"
BAR_OFF = "-"


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

    cursor.execute("INSERT INTO loyalty_customers (phone_e164) VALUES (%s)", (phone_e164,))
    customer_id = cursor.lastrowid

    cursor.execute("""
        INSERT INTO loyalty_accounts (customer_id, totopos_balance, totopos_lifetime)
        VALUES (%s,0,0)
    """, (customer_id,))
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
    except Exception:
        return value


# =========================================================
# =============== INVENTARIO: DESCONTAR ===================
# =========================================================

from decimal import Decimal
import pymysql

def descontar_stock_por_pedido_cursor(cur, pedido_id: int) -> None:
    # 0) items del pedido con platillo_id + proteina_id
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
        return

    consumo = {}  # insumo_id -> Decimal(total_salida)

    for it in items:
        platillo_id = it["platillo_id"]
        proteina_id = it["proteina_id"]
        qty = Decimal(str(it["cantidad_vendida"] or 0))

        if not platillo_id or qty <= 0:
            continue

        # 1A) receta base
        cur.execute("""
            SELECT r.insumo_id, r.cantidad_base
            FROM recetas r
            JOIN insumos i ON i.id = r.insumo_id
            WHERE r.platillo_id = %s
              AND i.descuenta_stock = 1
        """, (platillo_id,))
        base_rows = cur.fetchall()

        for r in base_rows:
            insumo_id = int(r["insumo_id"])
            cant_base = Decimal(str(r["cantidad_base"]))
            consumo[insumo_id] = consumo.get(insumo_id, Decimal("0")) + (cant_base * qty)

        # 1B) receta por proteína
        if proteina_id:
            cur.execute("""
                SELECT rp.insumo_id, rp.cantidad_base
                FROM recetas_proteina rp
                JOIN insumos i ON i.id = rp.insumo_id
                WHERE rp.platillo_id = %s
                  AND rp.proteina_id = %s
                  AND i.descuenta_stock = 1
            """, (platillo_id, proteina_id))
            prot_rows = cur.fetchall()

            for r in prot_rows:
                insumo_id = int(r["insumo_id"])
                cant_base = Decimal(str(r["cantidad_base"]))
                consumo[insumo_id] = consumo.get(insumo_id, Decimal("0")) + (cant_base * qty)

    if not consumo:
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


def descontar_stock_por_pedido(pedido_id: int) -> None:
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            conn.begin()
            descontar_stock_por_pedido_cursor(cur, pedido_id)
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def descontar_stock_por_pedido(pedido_id: int) -> None:
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            conn.begin()
            descontar_stock_por_pedido_cursor(cur, pedido_id)
            conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


# =========================================================
# ================== HOME ================================
# =========================================================

@app.route("/")
def index():
    return redirect(url_for("nuevo_pedido"))


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


# =========================================================
# ================== NUEVO PEDIDO =========================
# =========================================================

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

            cursor.execute("SELECT * FROM salsas ORDER BY nombre")
            salsas = cursor.fetchall()

            cursor.execute("SELECT * FROM proteinas ORDER BY nombre")
            proteinas = cursor.fetchall()

            if request.method == "POST":
                fecha = request.form.get("fecha")
                if not fecha:
                    cursor.execute("SELECT NOW() AS ahora")
                    fecha = cursor.fetchone()["ahora"]

                origen = (request.form.get("origen") or "").strip().lower()
                mesero = request.form.get("mesero", "")
                metodo_pago = request.form.get("metodo_pago", "")
                monto_uber = Decimal(request.form.get("monto_uber", "0") or "0")

                tel_raw = (request.form.get("telefono_whatsapp") or "").strip()
                telefono_e164 = normalize_phone_mx(tel_raw) if tel_raw else None

                productos_ids = request.form.getlist("producto_id[]")
                cantidades = request.form.getlist("cantidad[]")

                # legacy (texto)
                proteinas_sel = request.form.getlist("proteina[]")
                sin_sel = request.form.getlist("sin[]")
                notas_sel = request.form.getlist("nota[]")

                # nuevos (IDs)
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
                    if not str(prod_id).isdigit():
                        continue

                    cant = int(cantidades[i]) if i < len(cantidades) and str(cantidades[i]).strip().isdigit() else 0
                    if cant <= 0:
                        continue

                    # precio con uber si aplica (requiere columna precio_uber, si no existe, cae a precio)
                    if table_has_column(cursor, "productos", "precio_uber"):
                        cursor.execute("""
                            SELECT
                                CASE
                                    WHEN %s = 'uber' AND precio_uber IS NOT NULL
                                        THEN precio_uber
                                    ELSE precio
                                END AS precio_final
                            FROM productos
                            WHERE id = %s
                        """, (origen, int(prod_id)))
                    else:
                        cursor.execute("SELECT precio AS precio_final FROM productos WHERE id=%s", (int(prod_id),))

                    row = cursor.fetchone()
                    if not row:
                        continue

                    precio_unit = Decimal(str(row["precio_final"]))
                    subtotal = precio_unit * cant
                    total += subtotal

                    items.append({
                        "producto_id": int(prod_id),
                        "cantidad": cant,
                        "precio_unitario": precio_unit,
                        "subtotal": subtotal,

                        "proteina": safe_get(proteinas_sel, i, ""),
                        "sin": safe_get(sin_sel, i, ""),
                        "nota": safe_get(notas_sel, i, ""),

                        "proteina_id": safe_int_or_none(safe_get(proteinas_id_sel, i, "")),
                        "salsa_id": safe_int_or_none(safe_get(salsas_id_sel, i, "")),
                    })

                neto = total + monto_uber

                cursor.execute("""
                    INSERT INTO pedidos
                    (fecha, origen, mesero, telefono_whatsapp, metodo_pago, total, monto_uber, neto, estado)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'abierto')
                """, (fecha, origen, mesero, telefono_e164, metodo_pago, total, monto_uber, neto))

                pedido_id = cursor.lastrowid

                # columnas opcionales en pedido_items
                has_prot_id = table_has_column(cursor, "pedido_items", "proteina_id")
                has_salsa_id = table_has_column(cursor, "pedido_items", "salsa_id")

                for it in items:
                    cols = ["pedido_id", "producto_id", "proteina", "sin", "nota", "cantidad", "precio_unitario", "subtotal"]
                    vals = [pedido_id, it["producto_id"], it["proteina"], it["sin"], it["nota"], it["cantidad"], it["precio_unitario"], it["subtotal"]]

                    if has_prot_id:
                        cols.append("proteina_id")
                        vals.append(it["proteina_id"])

                    if has_salsa_id:
                        cols.append("salsa_id")
                        vals.append(it["salsa_id"])

                    placeholders = ",".join(["%s"] * len(cols))
                    colsql = ",".join(cols)

                    cursor.execute(f"""
                        INSERT INTO pedido_items ({colsql})
                        VALUES ({placeholders})
                    """, tuple(vals))

                conn.commit()
                flash(f"Pedido #{pedido_id} creado y abierto", "success")
                return redirect(url_for("ver_pedido", pedido_id=pedido_id))

    finally:
        conn.close()

    return render_template("nuevo_pedido.html", productos=productos, salsas=salsas, proteinas=proteinas)


# =========================================================
# ================== VER / EDITAR PEDIDO ==================
# =========================================================

@app.route("/pedido/<int:pedido_id>", methods=["GET", "POST"])
def ver_pedido(pedido_id):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT * FROM pedidos
                WHERE id = %s
            """, (pedido_id,))
            pedido = cursor.fetchone()

            if not pedido:
                flash("Pedido no disponible", "error")
                return redirect(url_for("pedidos_abiertos"))

            # catálogo para UI/JS
            cursor.execute("SELECT * FROM salsas ORDER BY nombre")
            salsas = cursor.fetchall()

            cursor.execute("SELECT * FROM proteinas ORDER BY nombre")
            proteinas = cursor.fetchall()

            cursor.execute("""
                SELECT * FROM productos
                WHERE activo = 1
                ORDER BY categoria, nombre
            """)
            productos = cursor.fetchall()

            # items del pedido (si columnas existen, tráelas; si no, ignora)
            has_prot_id = table_has_column(cursor, "pedido_items", "proteina_id")
            has_salsa_id = table_has_column(cursor, "pedido_items", "salsa_id")

            select_cols = [
                "pi.id", "pi.cantidad", "pi.precio_unitario", "pi.subtotal",
                "pi.proteina", "pi.sin", "pi.nota",
                "p.nombre",
            ]
            if has_salsa_id:
                select_cols.append("pi.salsa_id")
            else:
                select_cols.append("NULL AS salsa_id")
            if has_prot_id:
                select_cols.append("pi.proteina_id")
            else:
                select_cols.append("NULL AS proteina_id")

            cursor.execute(f"""
                SELECT {", ".join(select_cols)}
                FROM pedido_items pi
                JOIN productos p ON p.id = pi.producto_id
                WHERE pi.pedido_id = %s
                ORDER BY pi.id DESC
            """, (pedido_id,))
            items = cursor.fetchall()

            # si está cerrado, solo mostrar
            if request.method == "POST":
                if pedido.get("estado") != "abierto":
                    flash("No se puede modificar un pedido cerrado", "error")
                    return redirect(url_for("ver_pedido", pedido_id=pedido_id))

                productos_ids = request.form.getlist("producto_id[]")
                cantidades = request.form.getlist("cantidad[]")

                proteinas_sel = request.form.getlist("proteina[]")
                sin_sel = request.form.getlist("sin[]")
                notas_sel = request.form.getlist("nota[]")

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
                    if not str(prod_id).isdigit():
                        continue

                    cant = int(cantidades[i]) if i < len(cantidades) and str(cantidades[i]).strip().isdigit() else 0
                    if cant <= 0:
                        continue

                    prot_txt = safe_get(proteinas_sel, i, "")
                    sin_txt = safe_get(sin_sel, i, "")
                    nota = safe_get(notas_sel, i, "")

                    proteina_id = safe_int_or_none(safe_get(proteinas_id_sel, i, ""))
                    salsa_id = safe_int_or_none(safe_get(salsas_id_sel, i, ""))

                    cursor.execute("SELECT precio FROM productos WHERE id = %s", (int(prod_id),))
                    row = cursor.fetchone()
                    if not row:
                        continue

                    precio = Decimal(str(row["precio"]))
                    subtotal_nuevo = precio * cant

                    # buscar línea idéntica (incluye IDs si existen)
                    query = """
                        SELECT id, cantidad
                        FROM pedido_items
                        WHERE pedido_id = %s
                          AND producto_id = %s
                          AND (proteina <=> %s)
                          AND (sin <=> %s)
                          AND (nota <=> %s)
                    """
                    params = [pedido_id, int(prod_id), prot_txt, sin_txt, nota]

                    if has_prot_id:
                        query += " AND (proteina_id <=> %s)"
                        params.append(proteina_id)

                    if has_salsa_id:
                        query += " AND (salsa_id <=> %s)"
                        params.append(salsa_id)

                    cursor.execute(query, tuple(params))
                    existente = cursor.fetchone()

                    if existente:
                        nueva_cantidad = int(existente["cantidad"]) + cant
                        nuevo_subtotal = Decimal(str(nueva_cantidad)) * precio

                        cursor.execute("""
                            UPDATE pedido_items
                            SET cantidad = %s,
                                subtotal = %s
                            WHERE id = %s
                        """, (nueva_cantidad, nuevo_subtotal, existente["id"]))

                        total_agregado += precio * cant
                    else:
                        cols = ["pedido_id", "producto_id", "proteina", "sin", "nota", "cantidad", "precio_unitario", "subtotal"]
                        vals = [pedido_id, int(prod_id), prot_txt, sin_txt, nota, cant, precio, subtotal_nuevo]

                        if has_prot_id:
                            cols.append("proteina_id")
                            vals.append(proteina_id)

                        if has_salsa_id:
                            cols.append("salsa_id")
                            vals.append(salsa_id)

                        placeholders = ",".join(["%s"] * len(cols))
                        cursor.execute(f"""
                            INSERT INTO pedido_items ({",".join(cols)})
                            VALUES ({placeholders})
                        """, tuple(vals))

                        total_agregado += subtotal_nuevo

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
        productos=productos,
        salsas=salsas,
        proteinas=proteinas
    )


# =========================================================
# ================== CERRAR PEDIDO =========================
# =========================================================

@app.route("/cerrar_pedido/<int:pedido_id>", methods=["POST"])
def cerrar_pedido(pedido_id):
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT estado FROM pedidos WHERE id=%s", (pedido_id,))
            row = cursor.fetchone()
            if not row:
                flash("Pedido no encontrado", "error")
                return redirect(url_for("pedidos_abiertos"))

            if row["estado"] != "abierto":
                flash("Este pedido ya está cerrado", "error")
                return redirect(url_for("pedidos_abiertos"))

            cursor.execute("UPDATE pedidos SET estado='cerrado' WHERE id=%s", (pedido_id,))

            # descontar inventario en la MISMA transacción
            descontar_stock_por_pedido_cursor(cursor, pedido_id)

            conn.commit()
            flash("Pedido cerrado correctamente (inventario actualizado)", "success")
            return redirect(url_for("pedidos_abiertos"))
    finally:
        conn.close()


@app.route("/cerrar_pedido_whatsapp/<int:pedido_id>", methods=["POST"])
def cerrar_pedido_whatsapp(pedido_id):
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
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

            # cerrar
            cursor.execute("UPDATE pedidos SET estado='cerrado' WHERE id=%s", (pedido_id,))

            # totopos si hay teléfono
            earned = 0
            balance = 0
            if phone:
                earned = 1
                customer_id = loyalty_get_or_create_customer(cursor, phone)
                balance = loyalty_add_totopos_for_purchase(cursor, customer_id, pedido_id, earned)

            # inventario
            descontar_stock_por_pedido_cursor(cursor, pedido_id)

            # whatsapp
            if phone:
                ticket_text = generar_ticket_texto(pedido_id, cursor)
                msg_loyalty = loyalty_message(balance, earned, pedido_id, Decimal(str(pedido["total"])))
                full_message = ticket_text + "\n\n" + msg_loyalty

                conn.commit()
                return redirect(wa_me_link(phone, full_message))

            conn.commit()
            flash("Pedido cerrado. (Sin WhatsApp porque no hay teléfono)", "success")
            return redirect(url_for("pedidos_abiertos"))
    finally:
        conn.close()


# =========================================================
# ================== PRODUCTOS =============================
# =========================================================

@app.route("/productos", methods=["GET", "POST"])
def productos_view():
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


# =========================================================
# ================== COMPRAS ===============================
# =========================================================

@app.route("/compras", methods=["GET", "POST"])
def compras():
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            if request.method == "POST":
                cursor.execute("""
                    INSERT INTO insumos_compras
                    (fecha, lugar, cantidad, unidad, concepto, costo, tipo_costo, nota,
                     insumo_id, cantidad_base, unidad_base, costo_unitario, es_insumo)
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
                        f"Entrada por compra #{compra_id}",
                    ))

                conn.commit()
                flash("Compra registrada correctamente", "success")

            # Si quieres listar compras aquí, agrega tu SELECT.
            # Por ahora, lo dejo vacío para no inventar tu esquema.
            compras_rows = []

    finally:
        conn.close()

    return render_template("compras.html", compras=compras_rows)


# =========================================================
# ================== DASHBOARD =============================
# =========================================================

@app.route("/dashboard")
def dashboard():
    mes = request.args.get("mes")

    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
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

            total_ingresos = sum(Decimal(str(i["total"] or 0)) for i in ingresos)
            total_costos = sum(Decimal(str(c["costo"] or 0)) for c in costos)
            utilidad = total_ingresos - total_costos
            margen = (utilidad / total_ingresos * 100) if total_ingresos else Decimal("0")

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
                    SELECT DATE(fecha) AS d,
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
        total_ingresos=float(total_ingresos),
        total_costos=float(total_costos),
        utilidad=float(utilidad),
        margen=round(float(margen), 2),
        meses_disponibles=meses_disponibles,
        mes=mes,
        promedios_dia=promedios_dia,
        top_gastos=top_gastos,
    )


# =========================================================
# ============ ELIMINAR ITEM / ELIMINAR PEDIDO =============
# =========================================================

@app.route("/pedido/<int:pedido_id>/eliminar_item/<int:item_id>", methods=["POST"])
def eliminar_item_pedido(pedido_id, item_id):
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
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

            subtotal = Decimal(str(row["subtotal"] or 0))

            cursor.execute("DELETE FROM pedido_items WHERE id=%s AND pedido_id=%s", (item_id, pedido_id))

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


@app.route("/eliminar_pedido/<int:pedido_id>", methods=["POST"])
def eliminar_pedido(pedido_id):
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT id FROM pedidos WHERE id=%s", (pedido_id,))
            pedido = cursor.fetchone()

            if not pedido:
                flash("Pedido no encontrado", "error")
                return redirect(url_for("borrar_pedidos"))

            # si tienes loyalty_tx referenciando pedido_id, borrar primero
            if table_has_column(cursor, "loyalty_tx", "pedido_id"):
                cursor.execute("DELETE FROM loyalty_tx WHERE pedido_id=%s", (pedido_id,))

            cursor.execute("DELETE FROM pedido_items WHERE pedido_id=%s", (pedido_id,))
            cursor.execute("DELETE FROM pedidos WHERE id=%s", (pedido_id,))

            conn.commit()
            flash(f"Pedido #{pedido_id} eliminado correctamente.", "success")

    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        flash(f"Error eliminando pedido #{pedido_id}: {e}", "error")
    finally:
        conn.close()

    return redirect(url_for("borrar_pedidos"))


# =========================================================
# ================== TICKETS ===============================
# =========================================================

def generar_ticket_texto(pedido_id, cursor) -> str:
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
        subtotal = Decimal(str(it["cantidad"])) * Decimal(str(it["precio_unitario"]))
        lines.append(f'{it["cantidad"]} {it["nombre"]} - ${float(subtotal):.2f}')

        if it.get("proteina"):
            lines.append(f'  PROT: {it["proteina"]}')
        if it.get("sin"):
            lines.append(f'  SIN: {it["sin"]}')
        if it.get("nota"):
            lines.append(f'  NOTA: {it["nota"]}')

    lines.append("------------------------")
    total = Decimal(str(pedido["total"] or 0)) if pedido else Decimal("0")
    lines.append(f'TOTAL: ${float(total):.2f}')
    lines.append("")
    lines.append("¡Gracias por tu compra!")

    return "\n".join(lines)


@app.route("/pedido/<int:pedido_id>/whatsapp")
def enviar_ticket_whatsapp(pedido_id):
    tel_raw = (request.args.get("tel") or "").strip()
    telefono_e164 = normalize_phone_mx(tel_raw)

    if not telefono_e164:
        flash("Número no válido", "error")
        return redirect(url_for("ver_pedido", pedido_id=pedido_id))

    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            texto = generar_ticket_texto(pedido_id, cursor)
    finally:
        conn.close()

    return redirect(wa_me_link(telefono_e164, texto))


@app.route("/pedido/<int:pedido_id>/ticket_preview")
def ticket_preview(pedido_id):
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            texto = generar_ticket_texto(pedido_id, cursor)
    finally:
        conn.close()

    msg_q = urllib.parse.quote_from_bytes(texto.encode("utf-8", "strict"))
    return jsonify({
        "texto": texto,
        "whatsapp_url": f"https://wa.me/?text={msg_q}"
    })


# =========================================================
# ================== BORRAR PEDIDOS (UI) ===================
# =========================================================

@app.route("/borrar_pedidos", methods=["GET"])
def borrar_pedidos():
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
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
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
    modo = (request.form.get("modo") or "").strip()

    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            if modo == "borrar_todos_abiertos":
                cursor.execute("""
                    DELETE pi
                    FROM pedido_items pi
                    JOIN pedidos pe ON pe.id = pi.pedido_id
                    WHERE pe.estado = 'abierto'
                """)
                cursor.execute("DELETE FROM pedidos WHERE estado='abierto'")
                conn.commit()
                flash("Se borraron TODOS los pedidos abiertos.", "success")
                return redirect(url_for("borrar_pedidos", estado="abierto"))

            ids = request.form.getlist("pedido_ids[]")
            ids_int = [int(x) for x in ids if (x or "").strip().isdigit()]

            if not ids_int:
                flash("No seleccionaste pedidos para borrar.", "error")
                return redirect(url_for("borrar_pedidos"))

            placeholders = ",".join(["%s"] * len(ids_int))
            cursor.execute(f"DELETE FROM pedido_items WHERE pedido_id IN ({placeholders})", ids_int)
            cursor.execute(f"DELETE FROM pedidos WHERE id IN ({placeholders})", ids_int)

            conn.commit()
            flash(f"Se borraron {len(ids_int)} pedido(s).", "success")
            return redirect(url_for("borrar_pedidos"))
    finally:
        conn.close()


# =========================================================
# ================== INVENTARIO: STOCK UI ==================
# =========================================================

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


@app.post("/inventario/stock/agregar")
def agregar_stock():
    insumo_id = (request.form.get("insumo_id") or "").strip()
    cantidad_txt = (request.form.get("cantidad") or "").strip()
    q = (request.form.get("q") or "").strip()

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
            conn.begin()

            cur.execute("SELECT activo, unidad_base FROM insumos WHERE id=%s", (int(insumo_id),))
            ins = cur.fetchone()
            if not ins or int(ins["activo"]) != 1:
                conn.rollback()
                flash("El insumo no está activo.", "error")
                return redirect(url_for("ver_stock", q=q))

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
