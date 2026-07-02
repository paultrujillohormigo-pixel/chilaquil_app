import urllib.parse
import re
import pymysql
import json

from flask import Flask, request, redirect, url_for, flash, render_template, jsonify, send_from_directory
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta
from db import get_connection
from costeo import costeo_bp
import os  # <-- AGREGA ESTE IMPORT AL INICIO
import requests

# =========================================================
# CONFIGURACIÓN DE META WHATSAPP API DESDE VARIABLES DE ENTORNO
# =========================================================
WA_PHONE_NUMBER_ID = os.environ.get("WA_PHONE_NUMBER_ID")
WA_ACCESS_TOKEN = os.environ.get("WA_ACCESS_TOKEN")
WA_VERSION = os.environ.get("WA_VERSION", "v20.0")  # Si no está, toma v20.0 por defecto
app = Flask(__name__)
app.secret_key = "super_secret_key"  # cámbiala en prod

def enviar_ticket_meta_api(telefono_e164: str, pedido_id: int, cursor) -> bool:
    """
    Envía una notificación de ticket automatizada usando la API Cloud de WhatsApp.
    """
    # CONTROL DE SEGURIDAD: Si no hay tokens configurados en Railway, no dispares la petición
    if not WA_PHONE_NUMBER_ID or not WA_ACCESS_TOKEN:
        print("⚠️ ERROR: Falta configurar WA_PHONE_NUMBER_ID o WA_ACCESS_TOKEN en las variables de Railway.")
        return False

    if not telefono_e164:
        return False
        
    phone_clean = telefono_e164.replace("+", "")
    
    # 1. Recuperamos los datos del pedido que necesitamos
    cursor.execute("SELECT total FROM pedidos WHERE id = %s", (pedido_id,))
    pedido = cursor.fetchone()
    if not pedido:
        return False

    # 2. Buscamos el nombre del cliente
    cursor.execute("SELECT nombre FROM loyalty_customers WHERE phone_e164 = %s LIMIT 1", (telefono_e164,))
    c_row = cursor.fetchone()
    nombre_cliente = c_row["nombre"].split()[0] if c_row and c_row["nombre"] else "cliente"

    # 3. Armamos la petición para los servidores de Meta
    url = f"https://graph.facebook.com/{WA_VERSION}/{WA_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WA_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "messaging_product": "whatsapp",
        "to": phone_clean,
        "type": "template",
        "template": {
            "name": "ticket_compra",
            "language": { "code": "es_MX" },
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        { "type": "text", "text": str(nombre_cliente) },         # Variable {{1}}
                        { "type": "text", "text": f"#{pedido_id}" },             # Variable {{2}}
                        { "type": "text", "text": f"${float(pedido['total']):.2f}" } # Variable {{3}}
                    ]
                }
            ]
        }
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        return response.status_code in [200, 201]
    except Exception as e:
        print(f"❌ Error al conectar con la API de Meta: {e}")
        return False


# ================== COSTEO ==================
app.register_blueprint(costeo_bp)

@app.route("/")
def index():
    return render_template("index.html")

@app.route('/menu')
def menu():
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT * FROM productos WHERE activo = 1 ORDER BY categoria, nombre")
            productos_db = cursor.fetchall()
            
            cursor.execute("SELECT * FROM salsas ORDER BY nombre")
            salsas_db = cursor.fetchall()
            
    finally:
        conn.close()
        
    return render_template('menu.html', productos=productos_db, salsas=salsas_db)
    
@app.route('/carta')
def mostrar_carta():
    return send_from_directory(app.static_folder, 'carta.pdf')

@app.route('/ver-pdf')
def ver_pdf():
    return send_from_directory(app.static_folder, 'menu_Mayo.pdf')

# =========================================================
# ================== Raw Data ===================
# =========================================================

@app.route("/raw-data")
def raw_data():
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
                SELECT id, fecha, DATE(fecha) as dia, 
                       origen, mesero, total, neto, estado, metodo_pago
                FROM pedidos
                {filtro}
                ORDER BY fecha DESC, id DESC
            """, params)
            todos_pedidos = cursor.fetchall()

            pedidos_agrupados = {}
            for p in todos_pedidos:
                dia_str = str(p['dia'])
                if dia_str not in pedidos_agrupados:
                    pedidos_agrupados[dia_str] = []
                pedidos_agrupados[dia_str].append(p)

            cursor.execute("SELECT DISTINCT DATE_FORMAT(fecha, '%Y-%m') AS mes FROM pedidos ORDER BY mes DESC")
            meses_disponibles = [m["mes"] for m in cursor.fetchall()]

    finally:
        conn.close()

    return render_template("raw_data.html", 
                           pedidos_agrupados=pedidos_agrupados, 
                           meses_disponibles=meses_disponibles, 
                           mes=mes)

# =========================================================
# ================== HELPERS ==============================
# =========================================================

def normalize_phone_mx(raw: str) -> str | None:
    if not raw:
        return None
    s = re.sub(r"[^\d+]", "", raw).strip()
    s_digits = re.sub(r"\D", "", s)
    if len(s_digits) == 10:
        return "+52" + s_digits
    if len(s_digits) == 12 and s_digits.startswith("52"):
        return "+" + s_digits
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
    phone = (phone_e164 or "").replace("+", "")
    msg_bytes = message_text.encode("utf-8", "strict")
    msg_q = urllib.parse.quote_from_bytes(msg_bytes)
    return f"https://wa.me/{phone}?text={msg_q}"

def parse_decimal_mx(val: str | None) -> Decimal | None:
    if val is None:
        return None
    s = str(val).strip()
    if s == "" or s.lower() in {"na", "nan", "n/a", "none", "null", "-"}:
        return None
    s = re.sub(r"\s+", "", s)
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None

# =========================================================
# ================== LOYALTY (TOTOPOS) ====================
# =========================================================

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

    cursor.execute("SELECT id FROM loyalty_tx WHERE customer_id=%s AND pedido_id=%s AND reason='purchase'", (customer_id, pedido_id))
    if cursor.fetchone():
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

def loyalty_message(balance: int, earned: int, pedido_id: int, total: Decimal, phone: str) -> str:
    phone_clean = phone.replace("+", "") if phone else ""
    url_perfil = url_for('mi_perfil', phone=phone_clean, _external=True)
    
    lines = []
    
    if earned > 0:
        lines.append(f"\U0001F381 ¡Con esta compra sumas {earned} totopo(s) a tu cuenta! \U0001F32E\u2728")
    else:
        lines.append(f"\U0001F32E Tienes {balance} totopos acumulados en tu cuenta.")

    f5 = faltan_para(balance, 5)
    f10 = faltan_para(balance, 10)
    
    if f5 == 0 or f10 == 0:
        lines.append("")
        if f10 == 0:
            lines.append("\U0001F31F ¡Ya puedes canjear un plato fuerte gratis!")
        elif f5 == 0:
            lines.append("\U0001F964 ¡Ya puedes canjear una bebida gratis!")

    lines.append("\nConsulta tus puntos y recompensas aquí:")
    lines.append(f"\U0001F449 {url_perfil}\n")
    lines.append("¡Gracias por tu preferencia! \U0001F373\U0001F525")
    
    return "\n".join(lines)


@app.template_filter("money")
def money_format(value):
    try:
        return "${:,.2f}".format(float(value))
    except Exception:
        return value

@app.route("/api/buscar_cliente")
def buscar_cliente():
    query = request.args.get("q", "").strip()
    if len(query) < 3:
        return jsonify([])
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            search_val = f"%{query}%"
            cursor.execute("""
                SELECT id, nombre, phone_e164 
                FROM loyalty_customers 
                WHERE nombre LIKE %s OR phone_e164 LIKE %s
                LIMIT 5
            """, (search_val, search_val))
            resultados = cursor.fetchall()
    finally:
        conn.close()
    return jsonify(resultados)


# =========================================================
# =============== INVENTARIO: DESCONTAR ===================
# =========================================================

def descontar_stock_por_pedido_cursor(cur, pedido_id: int) -> None:
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

    consumo = {}
    for it in items:
        platillo_id = it.get("platillo_id")
        proteina_id = it.get("proteina_id")
        qty = Decimal(str(it.get("cantidad_vendida") or 0))

        if not platillo_id or qty <= 0:
            continue

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

        if proteina_id is not None:
            cur.execute("SELECT proteina_cantidad_base FROM platillos WHERE id = %s LIMIT 1", (platillo_id,))
            pr = cur.fetchone()
            prot_qty_base = Decimal(str((pr or {}).get("proteina_cantidad_base") or 0))

            if prot_qty_base > 0:
                cur.execute("SELECT insumo_id FROM proteinas WHERE id = %s LIMIT 1", (proteina_id,))
                prow = cur.fetchone()
                insumo_prot = (prow or {}).get("insumo_id")

                if insumo_prot:
                    cur.execute("SELECT descuenta_stock FROM insumos WHERE id = %s LIMIT 1", (insumo_prot,))
                    irow = cur.fetchone()
                    if irow and int(irow.get("descuenta_stock") or 0) == 1:
                        insumo_id = int(insumo_prot)
                        consumo[insumo_id] = consumo.get(insumo_id, Decimal("0")) + (prot_qty_base * qty)

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
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


# ================== PEDIDOS ABIERTOS ==================
@app.route("/pedidos_abiertos")
def pedidos_abiertos():
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            has_mesa = table_has_column(cursor, "pedidos", "mesa")
            col_mesa = ", mesa" if has_mesa else ""

            cursor.execute(f"""
                SELECT id, fecha, origen, mesero, total {col_mesa}
                FROM pedidos
                WHERE estado = 'abierto'
                ORDER BY fecha DESC
            """)
            pedidos = cursor.fetchall()

            has_salsa_id = table_has_column(cursor, "pedido_items", "salsa_id")
            has_padre_id = table_has_column(cursor, "pedido_items", "item_padre_id")
            
            # MAGIA 1: Mandamos el item_padre_id a la vista (si existe)
            col_padre = "pi.item_padre_id" if has_padre_id else "NULL AS item_padre_id"
            col_salsa = "s.nombre AS salsa" if has_salsa_id else "NULL AS salsa"
            join_salsa = "LEFT JOIN salsas s ON pi.salsa_id = s.id" if has_salsa_id else ""

            for p in pedidos:
                cursor.execute(f"""
                    SELECT 
                        pi.id, 
                        pr.nombre, 
                        pi.cantidad, 
                        pi.proteina, 
                        pi.sin AS modificadores, 
                        pi.nota AS notas,
                        {col_salsa},
                        COALESCE(pi.entregado, 0) AS entregado,
                        {col_padre}
                    FROM pedido_items pi
                    JOIN productos pr ON pr.id = pi.producto_id
                    {join_salsa}
                    WHERE pi.pedido_id = %s
                    ORDER BY pi.id ASC
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
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:

            cursor.execute("SELECT * FROM productos WHERE activo = 1 ORDER BY categoria, nombre")
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
                mesa = request.form.get("mesa", "Envío/Recoger")

                try:
                    descuento = Decimal(request.form.get("descuento", "0") or "0")
                except Exception:
                    descuento = Decimal("0")
                if descuento < 0: descuento = Decimal("0")

                tel_raw = (request.form.get("telefono_whatsapp") or "").strip()
                telefono_e164 = normalize_phone_mx(tel_raw) if tel_raw else None
                totopos_ganados = request.form.get("totopos_ganados")

                productos_ids = request.form.getlist("producto_id[]")
                cantidades = request.form.getlist("cantidad[]")
                proteinas_sel = request.form.getlist("proteina[]")
                sin_sel = request.form.getlist("sin[]")
                notas_sel = request.form.getlist("nota[]")
                proteinas_id_sel = request.form.getlist("proteina_id[]")
                salsas_id_sel = request.form.getlist("salsa_id[]")
                
                # Leemos el índice padre temporal del Frontend
                padre_index_sel = request.form.getlist("padre_index[]")

                def safe_get(lst, i, default=""): return lst[i] if i < len(lst) else default
                def safe_int_or_none(val):
                    v = (val or "").strip()
                    return int(v) if v and v.lower() != "null" and v != "0" and v.isdigit() else None

                total_bruto = Decimal("0")
                items = []

                for i, prod_id in enumerate(productos_ids):
                    if not str(prod_id).isdigit(): continue

                    cant_raw = safe_get(cantidades, i, "0")
                    cant = int(cant_raw) if str(cant_raw).strip().isdigit() else 0
                    if cant <= 0: continue

                    if table_has_column(cursor, "productos", "precio_uber"):
                        cursor.execute("""
                            SELECT CASE
                                WHEN %s = 'uber' AND precio_uber IS NOT NULL THEN precio_uber
                                ELSE precio END AS precio_final
                            FROM productos WHERE id = %s
                        """, (origen, int(prod_id)))
                    else:
                        cursor.execute("SELECT precio AS precio_final FROM productos WHERE id=%s", (int(prod_id),))

                    row = cursor.fetchone()
                    if not row or row.get("precio_final") is None: continue

                    precio_unit = Decimal(str(row["precio_final"]))
                    subtotal = precio_unit * cant
                    total_bruto += subtotal

                    # CORRECCIÓN: Leemos el índice del padre permitiendo que sea 0
                    p_idx_raw = safe_get(padre_index_sel, i, "").strip()
                    padre_idx = int(p_idx_raw) if p_idx_raw.isdigit() else None

                    items.append({
                        "original_index": i,
                        "producto_id": int(prod_id),
                        "cantidad": cant,
                        "precio_unitario": precio_unit,
                        "subtotal": subtotal,
                        "proteina": safe_get(proteinas_sel, i, ""),
                        "sin": safe_get(sin_sel, i, ""),
                        "nota": safe_get(notas_sel, i, ""),
                        "proteina_id": safe_int_or_none(safe_get(proteinas_id_sel, i, "")),
                        "salsa_id": safe_int_or_none(safe_get(salsas_id_sel, i, "")),
                        "padre_index": padre_idx
                    })

                if not items:
                    flash("No hay productos en el carrito.", "error")
                    return redirect(url_for("nuevo_pedido"))

                if descuento > total_bruto: descuento = total_bruto
                total_final = total_bruto - descuento
                neto = total_final + monto_uber

                has_desc = table_has_column(cursor, "pedidos", "descuento")
                cols = ["fecha", "origen", "mesero", "telefono_whatsapp", "metodo_pago", "total", "monto_uber", "neto", "estado", "mesa"]
                vals = [fecha, origen, mesero, telefono_e164, metodo_pago, total_final, monto_uber, neto, "abierto", mesa]
                
                if has_desc:
                    cols.insert(6, "descuento")
                    vals.insert(6, descuento)

                placeholders = ",".join(["%s"] * len(cols))
                colsql = ",".join(cols)

                cursor.execute(f"INSERT INTO pedidos ({colsql}) VALUES ({placeholders})", tuple(vals))
                pedido_id = cursor.lastrowid

                has_prot_id = table_has_column(cursor, "pedido_items", "proteina_id")
                has_salsa_id = table_has_column(cursor, "pedido_items", "salsa_id")
                has_padre_id = table_has_column(cursor, "pedido_items", "item_padre_id")

                index_to_db_id = {}
                extras_to_insert = []

                # MAGIA 2: Guardamos PRIMERO los platillos padre y recordamos sus IDs
                for it in items:
                    # Es extra si tiene padre_index o temporalmente si su nota dice "Para:" (Compatibilidad hacia atrás)
                    es_extra = (it["padre_index"] is not None) or ("Para:" in it["nota"])

                    if not es_extra:
                        cols_it = ["pedido_id", "producto_id", "proteina", "sin", "nota", "cantidad", "precio_unitario", "subtotal"]
                        vals_it = [pedido_id, it["producto_id"], it["proteina"], it["sin"], it["nota"], it["cantidad"], it["precio_unitario"], it["subtotal"]]

                        if has_prot_id: cols_it.append("proteina_id"); vals_it.append(it["proteina_id"])
                        if has_salsa_id: cols_it.append("salsa_id"); vals_it.append(it["salsa_id"])

                        placeholders_it = ",".join(["%s"] * len(cols_it))
                        cursor.execute(f"INSERT INTO pedido_items ({','.join(cols_it)}) VALUES ({placeholders_it})", tuple(vals_it))
                        
                        # Recordamos el ID real de este platillo
                        index_to_db_id[it["original_index"]] = cursor.lastrowid
                    else:
                        extras_to_insert.append(it)

                # MAGIA 3: Guardamos los EXTRAS y los amarramos al ID real de su padre
                for it in extras_to_insert:
                    cols_it = ["pedido_id", "producto_id", "proteina", "sin", "nota", "cantidad", "precio_unitario", "subtotal"]
                    vals_it = [pedido_id, it["producto_id"], it["proteina"], it["sin"], it["nota"], it["cantidad"], it["precio_unitario"], it["subtotal"]]

                    if has_prot_id: cols_it.append("proteina_id"); vals_it.append(it["proteina_id"])
                    if has_salsa_id: cols_it.append("salsa_id"); vals_it.append(it["salsa_id"])

                    if has_padre_id:
                        db_padre_id = index_to_db_id.get(it["padre_index"]) if it["padre_index"] is not None else None
                        if db_padre_id:
                            cols_it.append("item_padre_id")
                            vals_it.append(db_padre_id)

                    placeholders_it = ",".join(["%s"] * len(cols_it))
                    cursor.execute(f"INSERT INTO pedido_items ({','.join(cols_it)}) VALUES ({placeholders_it})", tuple(vals_it))
                
                if totopos_ganados and str(totopos_ganados).isdigit() and telefono_e164:
                    totopos_int = int(totopos_ganados)
                    if totopos_int > 0:
                        customer_id = loyalty_get_or_create_customer(cursor, telefono_e164)
                        loyalty_add_totopos_for_purchase(cursor, customer_id, pedido_id, totopos_int)

                enviar_wa = request.form.get("enviar_wa") == "1"
                
                if enviar_wa and telefono_e164:
                    conn.commit()
                    ticket_text = generar_ticket_texto(pedido_id, cursor)
                    
                    totopos_int = int(totopos_ganados) if totopos_ganados and str(totopos_ganados).isdigit() else 0
                    balance = 0
                    if totopos_int > 0:
                        cursor.execute("SELECT totopos_balance FROM loyalty_accounts WHERE customer_id=%s", (customer_id,))
                        row_totopos = cursor.fetchone()
                        if row_totopos: balance = row_totopos["totopos_balance"]
                            
                    msg_loyalty = loyalty_message(balance, totopos_int, pedido_id, total_final, telefono_e164)
                    full_message = ticket_text + "\n\n" + msg_loyalty
                    wa_link = wa_me_link(telefono_e164, full_message)
                    
                    return jsonify({
                        "status": "success",
                        "wa_link": wa_link,
                        "redirect_url": url_for("ver_pedido", pedido_id=pedido_id)
                    })
                else:
                    conn.commit()
                    flash(f"Pedido #{pedido_id} creado y abierto", "success")
                    return redirect(url_for("ver_pedido", pedido_id=pedido_id))

    finally:
        conn.close()

    return render_template("nuevo_pedido.html", productos=productos, salsas=salsas, proteinas=proteinas)

# =========================================================
# GESTIÓN DE CLIENTES Y LEALTAD (TOTOPOS)
# =========================================================

@app.route("/clientes", methods=["GET", "POST"])
def lista_clientes():
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            if request.method == "POST":
                nombre = request.form.get("nombre", "").strip()
                telefono_raw = request.form.get("telefono", "").strip()
                telefono = normalize_phone_mx(telefono_raw)

                if not telefono:
                    flash("Número de teléfono inválido.", "error")
                else:
                    cursor.execute("SELECT id FROM loyalty_customers WHERE phone_e164 = %s", (telefono,))
                    if cursor.fetchone():
                        flash("Este cliente (teléfono) ya existe.", "warning")
                    else:
                        cursor.execute("INSERT INTO loyalty_customers (nombre, phone_e164) VALUES (%s, %s)", (nombre, telefono))
                        new_id = cursor.lastrowid
                        cursor.execute("INSERT INTO loyalty_accounts (customer_id, totopos_balance, totopos_lifetime) VALUES (%s, 0, 0)", (new_id,))
                        conn.commit()
                        flash(f"Cliente {nombre} registrado con éxito.", "success")
                return redirect(url_for("lista_clientes"))

            cursor.execute("""
                SELECT 
                    c.id, c.nombre, c.phone_e164, 
                    a.totopos_balance, a.totopos_lifetime,
                    (SELECT MAX(p.fecha) 
                     FROM pedidos p 
                     JOIN loyalty_tx tx ON p.id = tx.pedido_id 
                     WHERE tx.customer_id = c.id) as ultima_compra
                FROM loyalty_customers c
                LEFT JOIN loyalty_accounts a ON c.id = a.customer_id
                ORDER BY a.totopos_balance DESC
            """)
            clientes = cursor.fetchall()
    finally:
        conn.close()
    return render_template("clientes.html", clientes=clientes)


@app.route("/mi-perfil", methods=["GET", "POST"])
@app.route("/mi-perfil/<phone>", methods=["GET"])
def mi_perfil(phone=None):
    if not phone:
        phone = request.args.get("phone")

    if request.method == "POST":
        telefono_raw = request.form.get("telefono", "")
        solo_numeros = re.sub(r"\D", "", telefono_raw)
        if len(solo_numeros) < 10:
            flash("Por favor ingresa un número de al menos 10 dígitos.", "error")
            return render_template("mi_perfil.html", cliente=None)
        ultimos_10 = solo_numeros[-10:]
        return redirect(url_for("mi_perfil", phone=ultimos_10))

    if phone:
        solo_numeros = re.sub(r"\D", "", phone)
        ultimos_10 = solo_numeros[-10:] if len(solo_numeros) >= 10 else solo_numeros
        telefono_mexico = f"+52{ultimos_10}"
        
        conn = get_connection()
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("""
                    SELECT c.nombre, c.phone_e164, a.totopos_balance 
                    FROM loyalty_customers c
                    LEFT JOIN loyalty_accounts a ON c.id = a.customer_id
                    WHERE c.phone_e164 = %s 
                       OR c.phone_e164 = %s 
                       OR REPLACE(c.phone_e164, ' ', '') LIKE %s
                """, (telefono_mexico, ultimos_10, f"%{ultimos_10}%"))
                cliente = cursor.fetchone()
        finally:
            conn.close()

        if not cliente:
            flash(f"No encontramos la cuenta con el número terminado en {ultimos_10}. Revisa que sea el mismo con el que haces tus pedidos.", "error")
            return render_template("mi_perfil.html", cliente=None)
            
        balance = int(cliente.get("totopos_balance") or 0)
        f5 = faltan_para(balance, 5)
        f10 = faltan_para(balance, 10)
        
        return render_template("mi_perfil.html", cliente=cliente, f5=f5, f10=f10)

    return render_template("mi_perfil.html", cliente=None)


@app.route("/cliente/<int:customer_id>", methods=["GET", "POST"])
def detalle_cliente(customer_id):
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            if request.method == "POST":
                nombre = request.form.get("nombre")
                telefono = normalize_phone_mx(request.form.get("telefono"))
                ajuste = int(request.form.get("ajuste_puntos", 0) or 0)
                motivo = request.form.get("motivo", "Ajuste manual")

                cursor.execute("UPDATE loyalty_customers SET nombre=%s, phone_e164=%s WHERE id=%s", (nombre, telefono, customer_id))

                if ajuste != 0:
                    cursor.execute("""
                        UPDATE loyalty_accounts 
                        SET totopos_balance = totopos_balance + %s,
                            totopos_lifetime = totopos_lifetime + %s
                        WHERE customer_id = %s
                    """, (ajuste, max(ajuste, 0), customer_id))
                    
                    cursor.execute("INSERT INTO loyalty_tx (customer_id, delta, reason) VALUES (%s, %s, %s)", (customer_id, ajuste, motivo))
                
                conn.commit()
                flash("Información actualizada correctamente.", "success")
                return redirect(url_for("detalle_cliente", customer_id=customer_id))

            cursor.execute("""
                SELECT c.*, a.totopos_balance, a.totopos_lifetime 
                FROM loyalty_customers c
                LEFT JOIN loyalty_accounts a ON c.id = a.customer_id
                WHERE c.id = %s
            """, (customer_id,))
            cliente = cursor.fetchone()

            cursor.execute("""
                SELECT tx.*, p.fecha 
                FROM loyalty_tx tx
                LEFT JOIN pedidos p ON tx.pedido_id = p.id
                WHERE tx.customer_id = %s
                ORDER BY tx.id DESC LIMIT 30
            """, (customer_id,))
            historial = cursor.fetchall()
    finally:
        conn.close()

    if not cliente:
        flash("Cliente no encontrado", "error")
        return redirect(url_for("lista_clientes"))

    return render_template("cliente_detalle.html", cliente=cliente, historial=historial)


# =========================================================
# ================== VER / EDITAR PEDIDO ==================
# =========================================================

@app.route("/pedido/<int:pedido_id>", methods=["GET", "POST"])
def ver_pedido(pedido_id):
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            # 1. Obtener los datos del pedido principal
            cursor.execute("SELECT * FROM pedidos WHERE id = %s", (pedido_id,))
            pedido = cursor.fetchone()

            if not pedido:
                flash("Pedido no disponible", "error")
                return redirect(url_for("pedidos_abiertos"))

            # 2. Cargar catálogos requeridos por la UI
            cursor.execute("SELECT * FROM salsas ORDER BY nombre")
            salsas = cursor.fetchall()
            cursor.execute("SELECT * FROM proteinas ORDER BY nombre")
            proteinas = cursor.fetchall()
            cursor.execute("SELECT * FROM productos WHERE activo = 1 ORDER BY categoria, nombre")
            productos = cursor.fetchall()

            has_prot_id = table_has_column(cursor, "pedido_items", "proteina_id")
            has_salsa_id = table_has_column(cursor, "pedido_items", "salsa_id")
            has_padre_id = table_has_column(cursor, "pedido_items", "item_padre_id")

            # 3. Procesar la actualización del pedido completo (POST)
            if request.method == "POST":
                enviar_wa = request.form.get("enviar_wa") == "1"
                tel_raw = (request.form.get("telefono_whatsapp") or "").strip()
                telefono_e164 = normalize_phone_mx(tel_raw) if tel_raw else pedido.get("telefono_whatsapp")

                # =========================================================
                # EXCEPCIÓN: Si está cerrado pero quieren reenviar WhatsApp
                # =========================================================
                if pedido.get("estado") != "abierto":
                    if enviar_wa:
                        if not telefono_e164:
                            return jsonify({"status": "error", "message": "Ingresa un número válido para enviar el WhatsApp."})
                        
                        # Actualizamos el teléfono en caso de que lo hayan corregido en la interfaz
                        if telefono_e164 != pedido.get("telefono_whatsapp"):
                            cursor.execute("UPDATE pedidos SET telefono_whatsapp = %s WHERE id = %s", (telefono_e164, pedido_id))
                            conn.commit()

                        ticket_text = generar_ticket_texto(pedido_id, cursor)
                        
                        # Obtenemos los totopos actuales sin sumar nuevos (porque el pedido ya está cerrado)
                        balance = 0
                        cursor.execute("SELECT id FROM loyalty_customers WHERE phone_e164 = %s", (telefono_e164,))
                        c_row = cursor.fetchone()
                        if c_row:
                            cursor.execute("SELECT totopos_balance FROM loyalty_accounts WHERE customer_id = %s", (c_row["id"],))
                            acc = cursor.fetchone()
                            if acc: balance = acc["totopos_balance"]
                            
                        # earned=0 porque no sumamos puntos extra en reenvíos
                        msg_loyalty = loyalty_message(balance, 0, pedido_id, Decimal(str(pedido["total"])), telefono_e164)
                        full_message = ticket_text + "\n\n" + msg_loyalty
                        wa_link = wa_me_link(telefono_e164, full_message)
                        
                        return jsonify({
                            "status": "success",
                            "wa_link": wa_link,
                            "redirect_url": url_for("ver_pedido", pedido_id=pedido_id)
                        })
                    else:
                        flash("No se puede modificar un pedido que ya está cerrado.", "error")
                        return redirect(url_for("ver_pedido", pedido_id=pedido_id))

                # =========================================================
                # --- Lógica normal de pedido abierto sigue a partir de aquí ---
                # =========================================================
                fecha = request.form.get("fecha") or pedido.get("fecha")
                origen = (request.form.get("origen") or "").strip().lower()
                mesero = request.form.get("mesero", "")
                metodo_pago = request.form.get("metodo_pago", "")
                monto_uber = Decimal(request.form.get("monto_uber", "0") or "0")
                mesa = request.form.get("mesa", "Envío/Recoger")

                try:
                    descuento = Decimal(request.form.get("descuento", "0") or "0")
                except Exception:
                    descuento = Decimal("0")
                if descuento < 0: descuento = Decimal("0")

                totopos_ganados = request.form.get("totopos_ganados")

                # Recolección de las listas dinámicas del carrito
                productos_ids = request.form.getlist("producto_id[]")
                cantidades = request.form.getlist("cantidad[]")
                proteinas_sel = request.form.getlist("proteina[]")
                sin_sel = request.form.getlist("sin[]")
                notas_sel = request.form.getlist("nota[]")
                proteinas_id_sel = request.form.getlist("proteina_id[]") if "proteina_id[]" in request.form else []
                salsas_id_sel = request.form.getlist("salsa_id[]")
                padre_index_sel = request.form.getlist("padre_index[]")

                def safe_get(lst, i, default=""): return lst[i] if i < len(lst) else default
                def safe_int_or_none(val):
                    v = (val or "").strip()
                    return int(v) if v and v.lower() != "null" and v != "0" and v.isdigit() else None

                total_bruto = Decimal("0")
                items_a_insertar = []

                # Calcular subtotales y validar ítems del formulario
                for i, prod_id in enumerate(productos_ids):
                    if not str(prod_id).isdigit(): continue

                    cant_raw = safe_get(cantidades, i, "0")
                    cant = int(cant_raw) if str(cant_raw).strip().isdigit() else 0
                    if cant <= 0: continue

                    if table_has_column(cursor, "productos", "precio_uber"):
                        cursor.execute("""
                            SELECT CASE
                                WHEN %s = 'uber' AND precio_uber IS NOT NULL THEN precio_uber
                                ELSE precio END AS precio_final
                            FROM productos WHERE id = %s
                        """, (origen, int(prod_id)))
                    else:
                        cursor.execute("SELECT precio AS precio_final FROM productos WHERE id=%s", (int(prod_id),))

                    row_prod = cursor.fetchone()
                    if not row_prod or row_prod.get("precio_final") is None: continue

                    precio_unit = Decimal(str(row_prod["precio_final"]))
                    subtotal = precio_unit * cant
                    total_bruto += subtotal

                    p_idx_raw = safe_get(padre_index_sel, i, "").strip()
                    padre_idx = int(p_idx_raw) if p_idx_raw.isdigit() else None

                    items_a_insertar.append({
                        "original_index": i,
                        "producto_id": int(prod_id),
                        "cantidad": cant,
                        "precio_unitario": precio_unit,
                        "subtotal": subtotal,
                        "proteina": safe_get(proteinas_sel, i, ""),
                        "sin": safe_get(sin_sel, i, ""),
                        "nota": safe_get(notas_sel, i, ""),
                        "proteina_id": safe_int_or_none(safe_get(proteinas_id_sel, i, "")) if i < len(proteinas_id_sel) else None,
                        "salsa_id": safe_int_or_none(safe_get(salsas_id_sel, i, "")),
                        "padre_index": padre_idx
                    })

                if not items_a_insertar:
                    flash("El carrito no puede quedarse vacío al actualizar.", "error")
                    return redirect(url_for("ver_pedido", pedido_id=pedido_id))

                if descuento > total_bruto: descuento = total_bruto
                total_final = total_bruto - descuento
                neto = total_final + monto_uber

                # === REEMPLAZO ATÓMICO: Limpiar items anteriores para evitar duplicación ===
                cursor.execute("DELETE FROM pedido_items WHERE pedido_id = %s", (pedido_id,))

                index_to_db_id = {}
                extras_to_insert = []

                # Insertar primero los platillos principales (Padres)
                for it in items_a_insertar:
                    es_extra = (it["padre_index"] is not None) or ("Para:" in it["nota"])

                    if not es_extra:
                        cols_it = ["pedido_id", "producto_id", "proteina", "sin", "nota", "cantidad", "precio_unitario", "subtotal"]
                        vals_it = [pedido_id, it["producto_id"], it["proteina"], it["sin"], it["nota"], it["cantidad"], it["precio_unitario"], it["subtotal"]]

                        if has_prot_id: cols_it.append("proteina_id"); vals_it.append(it["proteina_id"])
                        if has_salsa_id: cols_it.append("salsa_id"); vals_it.append(it["salsa_id"])

                        placeholders_it = ",".join(["%s"] * len(cols_it))
                        cursor.execute(f"INSERT INTO pedido_items ({','.join(cols_it)}) VALUES ({placeholders_it})", tuple(vals_it))
                        index_to_db_id[it["original_index"]] = cursor.lastrowid
                    else:
                        extras_to_insert.append(it)

                # Insertar los extras asociados a sus respectivos padres
                for it in extras_to_insert:
                    cols_it = ["pedido_id", "producto_id", "proteina", "sin", "nota", "cantidad", "precio_unitario", "subtotal"]
                    vals_it = [pedido_id, it["producto_id"], it["proteina"], it["sin"], it["nota"], it["cantidad"], it["precio_unitario"], it["subtotal"]]

                    if has_prot_id: cols_it.append("proteina_id"); vals_it.append(it["proteina_id"])
                    if has_salsa_id: cols_it.append("salsa_id"); vals_it.append(it["salsa_id"])

                    if has_padre_id:
                        db_padre_id = index_to_db_id.get(it["padre_index"]) if it["padre_index"] is not None else None
                        if db_padre_id:
                            cols_it.append("item_padre_id")
                            vals_it.append(db_padre_id)

                    placeholders_it = ",".join(["%s"] * len(cols_it))
                    cursor.execute(f"INSERT INTO pedido_items ({','.join(cols_it)}) VALUES ({placeholders_it})", tuple(vals_it))

                # Actualizar metadatos y totales del pedido raíz
                has_desc = table_has_column(cursor, "pedidos", "descuento")
                update_query = """
                    UPDATE pedidos 
                    SET fecha=%s, origen=%s, mesero=%s, telefono_whatsapp=%s, metodo_pago=%s, 
                        total=%s, monto_uber=%s, neto=%s, mesa=%s {comma_desc}
                    WHERE id=%s
                """
                update_vals = [fecha, origen, mesero, telefono_e164, metodo_pago, total_final, monto_uber, neto, mesa]
                
                if has_desc:
                    update_query = update_query.replace("{comma_desc}", ", descuento=%s")
                    update_vals.append(descuento)
                else:
                    update_query = update_query.replace("{comma_desc}", "")
                
                update_vals.append(pedido_id)
                cursor.execute(update_query, tuple(update_vals))

                # Gestión del sistema de puntos (Totopos de lealtad)
                if totopos_ganados and str(totopos_ganados).isdigit() and telefono_e164:
                    totopos_int = int(totopos_ganados)
                    if totopos_int > 0:
                        customer_id = loyalty_get_or_create_customer(cursor, telefono_e164)
                        loyalty_add_totopos_for_purchase(cursor, customer_id, pedido_id, totopos_int)

                if enviar_wa and telefono_e164:
                    conn.commit()
                    ticket_text = generar_ticket_texto(pedido_id, cursor)
                    
                    totopos_int = int(totopos_ganados) if totopos_ganados and str(totopos_ganados).isdigit() else 0
                    balance = 0
                    if totopos_int > 0:
                        cursor.execute("SELECT totopos_balance FROM loyalty_accounts WHERE customer_id=%s", (customer_id,))
                        row_totopos = cursor.fetchone()
                        if row_totopos: balance = row_totopos["totopos_balance"]

                    msg_loyalty = loyalty_message(balance, totopos_int, pedido_id, total_final, telefono_e164)
                    full_message = ticket_text + "\n\n" + msg_loyalty
                    wa_link = wa_me_link(telefono_e164, full_message)

                    return jsonify({
                        "status": "success",
                        "wa_link": wa_link,
                        "redirect_url": url_for("ver_pedido", pedido_id=pedido_id)
                    })
                else:
                    conn.commit()
                    flash(f"Pedido #{pedido_id} actualizado con éxito.", "success")
                    return redirect(url_for("ver_pedido", pedido_id=pedido_id))

            # 4. Operación GET: Recuperar y estructurar ítems para inicializar el carrito del Front-End
            select_cols = [
                "pi.id", "pi.producto_id", "pi.cantidad", "pi.precio_unitario", "pi.subtotal",
                "pi.proteina", "pi.sin", "pi.nota", "p.nombre", "p.categoria"
            ]
            if has_salsa_id: select_cols.append("pi.salsa_id")
            else: select_cols.append("NULL AS salsa_id")
            if has_prot_id: select_cols.append("pi.proteina_id")
            else: select_cols.append("NULL AS proteina_id")
            if has_padre_id: select_cols.append("pi.item_padre_id")
            else: select_cols.append("NULL AS item_padre_id")

            cursor.execute(f"""
                SELECT {", ".join(select_cols)}, s.nombre as salsa_nombre
                FROM pedido_items pi
                JOIN productos p ON p.id = pi.producto_id
                LEFT JOIN salsas s ON pi.salsa_id = s.id
                WHERE pi.pedido_id = %s
                ORDER BY pi.id ASC
            """, (pedido_id,))
            items_raw = cursor.fetchall()

            # Mapeamos item_padre_id al índice absoluto del arreglo (padre_index) esperado por el JS
            items = []
            id_to_index_map = {}
            
            # Primero registramos la posición absoluta de TODOS los elementos
            for idx, row in enumerate(items_raw):
                id_to_index_map[row["id"]] = idx

            # Armamos el listado definitivo inyectando el valor correcto de padre_index
            for row in items_raw:
                p_id = row.get("item_padre_id")
                row["padre_index"] = id_to_index_map.get(p_id) if p_id else None
                items.append(row)

            # Obtener nombre de cliente si existe para la tarjeta informativa de la UI
            pedido["cliente_nombre"] = None
            if pedido.get("telefono_whatsapp"):
                cursor.execute("SELECT nombre FROM loyalty_customers WHERE phone_e164 = %s LIMIT 1", (pedido["telefono_whatsapp"],))
                c_row = cursor.fetchone()
                if c_row: pedido["cliente_nombre"] = c_row["nombre"]

    finally:
        conn.close()

    return render_template(
        "pedido.html",
        pedido=pedido,
        pedido_detalles=items,  # Mapeado exacto para reconstrucción por JS
        productos=productos,
        salsas=salsas,
        proteinas=proteinas
    )

@app.route("/pedido/<int:pedido_id>/actualizar_whatsapp", methods=["POST"])
def actualizar_whatsapp_pedido(pedido_id):
    telefono_recibido = request.form.get("telefono_whatsapp", "").strip()
    telefono_limpio = normalize_phone_mx(telefono_recibido)

    if not telefono_limpio:
        flash("Número de WhatsApp inválido. Debe tener al menos 10 dígitos.", "error")
        return redirect(url_for("ver_pedido", pedido_id=pedido_id))

    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT estado FROM pedidos WHERE id = %s", (pedido_id,))
            pedido = cursor.fetchone()
            
            if not pedido or pedido["estado"] != "abierto":
                flash("Pedido no encontrado o ya está cerrado.", "error")
                return redirect(url_for("pedidos_abiertos"))

            conn.begin()
            cursor.execute("UPDATE pedidos SET telefono_whatsapp = %s WHERE id = %s", (telefono_limpio, pedido_id))
            conn.commit()
            
            flash("Número de WhatsApp guardado correctamente.", "success")
            return redirect(url_for("ver_pedido", pedido_id=pedido_id))
            
    except Exception as e:
        try: conn.rollback()
        except Exception: pass
        flash("Hubo un error al guardar el número en la base de datos.", "error")
        return redirect(url_for("ver_pedido", pedido_id=pedido_id))
    finally:
        conn.close()


# ================== CERRAR PEDIDO =========================

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
            cursor.execute("SELECT id, total, telefono_whatsapp, estado FROM pedidos WHERE id=%s", (pedido_id,))
            pedido = cursor.fetchone()

            if not pedido or pedido["estado"] != "abierto":
                flash("Pedido no disponible o ya cerrado", "error")
                return redirect(url_for("pedidos_abiertos"))

            phone = pedido.get("telefono_whatsapp")
            cursor.execute("UPDATE pedidos SET estado='cerrado' WHERE id=%s", (pedido_id,))

            earned = 0
            balance = 0
            if phone:
                customer_id = loyalty_get_or_create_customer(cursor, phone)
                earned = 1 
                balance = loyalty_add_totopos_for_purchase(cursor, customer_id, pedido_id, earned)

            descontar_stock_por_pedido_cursor(cursor, pedido_id)

            if phone:
                ticket_text = generar_ticket_texto(pedido_id, cursor)
                msg_loyalty = loyalty_message(balance, earned, pedido_id, Decimal(str(pedido["total"])), phone)
                full_message = ticket_text + "\n\n" + msg_loyalty
                conn.commit()
                return redirect(wa_me_link(phone, full_message))

            conn.commit()
            flash("Pedido cerrado. No se envió WhatsApp porque no hay teléfono.", "success")
            return redirect(url_for("pedidos_abiertos"))
    finally:
        conn.close()

# =========================================================
# ================== PRODUCTOS =============================
# =========================================================

@app.route("/productos", methods=["GET", "POST"])
def productos():
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:

            cursor.execute("SELECT id, nombre FROM platillos ORDER BY nombre")
            platillos = cursor.fetchall()

            if request.method == "POST":
                nombre = (request.form.get("nombre") or "").strip()
                categoria = (request.form.get("categoria") or "").strip()
                precio_txt = (request.form.get("precio") or "").strip()
                platillo_id_txt = (request.form.get("platillo_id") or "").strip()

                if not nombre or not categoria or not precio_txt:
                    flash("Faltan campos requeridos.", "error")
                    return redirect(url_for("productos"))

                try: precio = Decimal(precio_txt)
                except Exception:
                    flash("Precio inválido.", "error")
                    return redirect(url_for("productos"))

                platillo_id = int(platillo_id_txt) if platillo_id_txt.isdigit() else None

                if platillo_id:
                    costo = calcular_costo_platillo(cursor, platillo_id)
                else:
                    costo_txt = (request.form.get("costo") or "0").strip()
                    try: costo = Decimal(costo_txt)
                    except Exception:
                        flash("Costo inválido.", "error")
                        return redirect(url_for("productos"))

                cursor.execute("""
                    INSERT INTO productos (nombre, categoria, costo, precio, platillo_id, activo)
                    VALUES (%s,%s,%s,%s,%s,1)
                """, (nombre, categoria, str(costo), str(precio), platillo_id))

                conn.commit()
                flash("Producto agregado correctamente", "success")
                return redirect(url_for("productos"))

            cursor.execute("""
                SELECT
                    pr.id, pr.nombre, pr.categoria, pr.costo, pr.precio, pr.platillo_id,
                    pl.nombre AS platillo_nombre
                FROM productos pr
                LEFT JOIN platillos pl ON pl.id = pr.platillo_id
                WHERE pr.activo = 1
                ORDER BY pr.categoria, pr.nombre
            """)
            productos_rows = cursor.fetchall()

    finally:
        conn.close()

    return render_template("productos.html", productos=productos_rows, platillos=platillos)


@app.post("/productos/<int:producto_id>/actualizar_platillo")
def actualizar_platillo_producto(producto_id):
    platillo_id_txt = (request.form.get("platillo_id") or "").strip()
    platillo_id = int(platillo_id_txt) if platillo_id_txt.isdigit() else None

    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT id FROM productos WHERE id=%s AND activo=1", (producto_id,))
            if not cursor.fetchone():
                flash("Producto no encontrado.", "error")
                return redirect(url_for("productos"))

            costo = calcular_costo_platillo(cursor, platillo_id) if platillo_id else Decimal("0")
            cursor.execute("UPDATE productos SET platillo_id=%s, costo=%s WHERE id=%s", (platillo_id, str(costo), producto_id))
            conn.commit()
            flash("Producto actualizado (platillo + costo).", "success")
            return redirect(url_for("productos"))
    finally:
        conn.close()

@app.post("/productos/<int:producto_id>/set_platillo")
def productos_set_platillo(producto_id):
    platillo_id = request.form.get("platillo_id") or None
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("UPDATE productos SET platillo_id = %s WHERE id = %s", (platillo_id, producto_id))
            conn.commit()
            flash("Relación producto → platillo actualizada ✅", "success")
    finally:
        conn.close()
    return redirect(url_for("productos"))


# =========================================================
# ================== COMPRAS ===============================
# =========================================================

@app.route("/compras", methods=["GET", "POST"])
def compras():
    conn = get_connection()
    conn.ping(reconnect=True)

    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            # Obtenemos los insumos activos
            cursor.execute("SELECT id, nombre, unidad_base FROM insumos WHERE activo = 1 ORDER BY nombre")
            insumos = cursor.fetchall()

            # --- Autoconfiguración para ocultar conceptos sin borrar data ---
            if not table_has_column(cursor, "insumos_compras", "oculto"):
                cursor.execute("ALTER TABLE insumos_compras ADD COLUMN oculto INT DEFAULT 0")
                conn.commit()

            # --- Extraemos los conceptos "Otros" que has guardado antes ---
            cursor.execute("""
                SELECT DISTINCT concepto 
                FROM insumos_compras 
                WHERE (insumo_id IS NULL OR es_insumo = 0)
                  AND concepto IS NOT NULL 
                  AND concepto != '' 
                  AND oculto = 0
                ORDER BY concepto
            """)
            conceptos_otros = [row["concepto"] for row in cursor.fetchall()]

            def render_with_data():
                cursor.execute("SELECT id, fecha, lugar, concepto, costo, tipo_costo, es_insumo FROM insumos_compras ORDER BY fecha DESC LIMIT 200")
                return render_template(
                    "compras.html", 
                    compras=cursor.fetchall(), 
                    insumos=insumos, 
                    conceptos_otros=conceptos_otros,
                    form_data=request.form
                )

            if request.method == "POST":
                cantidad_txt = (request.form.get("cantidad") or "").strip()
                unidad_txt = (request.form.get("unidad") or "").strip()
                sumar_stock = (request.form.get("es_insumo") == "1")

                if sumar_stock:
                    if not cantidad_txt: cantidad_txt = (request.form.get("cantidad_base") or "").strip()
                    if not unidad_txt: unidad_txt = (request.form.get("unidad_base") or "").strip()

                if not request.form.get("fecha"): flash("Fecha requerida.", "error"); return render_with_data()
                if not (request.form.get("lugar") or "").strip(): flash("Lugar requerido.", "error"); return render_with_data()
                if not (request.form.get("concepto") or "").strip(): flash("Concepto requerido.", "error"); return render_with_data()

                costo_dec = parse_decimal_mx(request.form.get("costo"))
                if costo_dec is None or costo_dec < 0: flash("Costo total inválido.", "error"); return render_with_data()

                cantidad_dec = parse_decimal_mx(cantidad_txt)
                if cantidad_dec is None or cantidad_dec <= 0: flash("Cantidad inválida.", "error"); return render_with_data()

                if not unidad_txt: flash("Unidad requerida.", "error"); return render_with_data()

                insumo_id_val = request.form.get("insumo_id") or None
                cantidad_base_val = request.form.get("cantidad_base") or None
                unidad_base_val = request.form.get("unidad_base") or None
                costo_unitario_val = request.form.get("costo_unitario") or None
                cant_base_dec = None

                if sumar_stock:
                    if not (insumo_id_val or "").strip().isdigit():
                        flash("Para sumar stock debes seleccionar un insumo válido.", "error")
                        return render_with_data()
                    cant_base_dec = parse_decimal_mx(cantidad_base_val)
                    if cant_base_dec is None or cant_base_dec <= 0:
                        flash("Cantidad base inválida (> 0).", "error")
                        return render_with_data()
                    cu_dec = parse_decimal_mx(costo_unitario_val)
                    costo_unitario_val = str(cu_dec) if cu_dec is not None else None
                    cantidad_txt = str(cant_base_dec)
                    cantidad_base_val = str(cant_base_dec)
                else:
                    cantidad_txt = str(cantidad_dec)

                cursor.execute("""
                    INSERT INTO insumos_compras
                    (fecha, lugar, cantidad, unidad, concepto, costo, tipo_costo, nota, insumo_id, cantidad_base, unidad_base, costo_unitario, es_insumo)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (
                    request.form["fecha"], request.form["lugar"], cantidad_txt, unidad_txt, request.form["concepto"], str(costo_dec), request.form["tipo_costo"],
                    request.form.get("nota", ""), int(insumo_id_val) if (insumo_id_val and str(insumo_id_val).isdigit()) else None,
                    cantidad_base_val, unidad_base_val, costo_unitario_val, 1 if sumar_stock else 0,
                ))

                compra_id = cursor.lastrowid
                if sumar_stock and insumo_id_val and cant_base_dec is not None:
                    cursor.execute("""
                        INSERT IGNORE INTO inventario_movimientos (insumo_id, cantidad_base, tipo, ref_tabla, ref_id, nota)
                        VALUES (%s, %s, 'entrada_compra', 'insumos_compras', %s, %s)
                    """, (int(insumo_id_val), str(cant_base_dec), compra_id, f"Entrada por compra #{compra_id}"))

                conn.commit()
                flash("Compra registrada correctamente", "success")
                return redirect(url_for("compras"))

            cursor.execute("SELECT id, fecha, lugar, concepto, costo, tipo_costo, es_insumo FROM insumos_compras ORDER BY fecha DESC LIMIT 200")
            compras_rows = cursor.fetchall()
    finally:
        conn.close()

    return render_template(
        "compras.html", 
        compras=compras_rows, 
        insumos=insumos, 
        conceptos_otros=conceptos_otros,
        form_data={}
    )


@app.route("/compras/eliminar_concepto", methods=["POST"])
def eliminar_concepto_compras():
    concepto = request.form.get("concepto")
    
    if not concepto:
        flash("Concepto no válido.", "error")
        return redirect(url_for("compras"))
        
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            if not table_has_column(cursor, "insumos_compras", "oculto"):
                cursor.execute("ALTER TABLE insumos_compras ADD COLUMN oculto INT DEFAULT 0")
            
            # Solo OCULTA la tarjeta en la interfaz, mantiene el historial financiero.
            cursor.execute("""
                UPDATE insumos_compras 
                SET oculto = 1
                WHERE concepto = %s 
                  AND (insumo_id IS NULL OR es_insumo = 0)
            """, (concepto,))
            conn.commit()
            
            flash(f"El concepto '{concepto}' se ocultó de tu panel (tu historial financiero sigue intacto).", "success")
    except Exception as e:
        try:
            conn.rollback()
        except:
            pass
        flash(f"Hubo un error al ocultar el concepto: {e}", "error")
    finally:
        conn.close()
        
    return redirect(url_for("compras"))


@app.route("/compras/eliminar_insumo", methods=["POST"])
def eliminar_insumo_compras():
    insumo_id = request.form.get("insumo_id")
    
    if not insumo_id or not str(insumo_id).isdigit():
        flash("ID de insumo no válido.", "error")
        return redirect(url_for("compras"))
        
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            # Desactiva el insumo sin borrar dependencias de la receta
            cursor.execute("UPDATE insumos SET activo = 0 WHERE id = %s", (int(insumo_id),))
            conn.commit()
            
            flash("Insumo eliminado de la lista exitosamente.", "success")
    except Exception as e:
        try:
            conn.rollback()
        except:
            pass
        flash(f"Hubo un error al eliminar el insumo: {e}", "error")
    finally:
        conn.close()
        
    return redirect(url_for("compras"))

# =========================================================
# ============ DASHBOARD AVANZADO (LÓGICA NUEVA) ===========
# =========================================================

from datetime import datetime, timedelta
from decimal import Decimal
import json
import pymysql

def get_previous_month(ym_str):
    try:
        dt = datetime.strptime(ym_str, "%Y-%m")
        first_day = dt.replace(day=1)
        prev_month = first_day - timedelta(days=1)
        return prev_month.strftime("%Y-%m")
    except:
        return None

def calc_var(current, prev):
    if not prev or prev == 0: return 0
    return round(((current - prev) / prev) * 100, 1)

@app.route("/dashboard")
def dashboard():
    # 1. Capturar todos los filtros del GET (HTML)
    meses_seleccionados = request.args.getlist("mes")
    fecha_inicio_seleccionada = request.args.get("fecha_inicio", "")
    fecha_fin_seleccionada = request.args.get("fecha_fin", "")
    dias_seleccionados = request.args.getlist("dia_semana")
    origen_seleccionado = request.args.get("origen", "")

    conn = get_connection()

    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            # Obtener meses disponibles para el selector
            cursor.execute("SELECT DISTINCT DATE_FORMAT(fecha, '%Y-%m') AS mes FROM pedidos ORDER BY mes DESC")
            meses_disp_raw = cursor.fetchall()
            meses_disponibles = [m["mes"] for m in meses_disp_raw]
            
            # Listas para construir el WHERE dinámico
            conds_general = []
            params_general = []
            
            # --- FILTRO 1: Meses o Default ---
            if meses_seleccionados:
                placeholders = ",".join(["%s"] * len(meses_seleccionados))
                conds_general.append(f"DATE_FORMAT({{campo_fecha}}, '%%Y-%%m') IN ({placeholders})")
                params_general.extend(meses_seleccionados)
            elif not fecha_inicio_seleccionada and not fecha_fin_seleccionada:
                # Default: tomar el mes más reciente si no se seleccionó ni mes ni fechas específicas
                if meses_disponibles:
                    last_m = meses_disponibles[0]
                    conds_general.append("DATE_FORMAT({campo_fecha}, '%%Y-%%m') = %s")
                    params_general.append(last_m)

            # --- FILTRO 2: Rango de Fechas ---
            if fecha_inicio_seleccionada:
                conds_general.append("DATE({campo_fecha}) >= %s")
                params_general.append(fecha_inicio_seleccionada)
            if fecha_fin_seleccionada:
                conds_general.append("DATE({campo_fecha}) <= %s")
                params_general.append(fecha_fin_seleccionada)
                
            # --- FILTRO 3: Días de la Semana ---
            p_dias = ""
            if dias_seleccionados:
                mapa_dias = {'Domingo': 1, 'Lunes': 2, 'Martes': 3, 'Miércoles': 4, 'Jueves': 5, 'Viernes': 6, 'Sábado': 7}
                dias_num = [str(mapa_dias[d]) for d in dias_seleccionados if d in mapa_dias]
                if dias_num:
                    p_dias = ",".join(dias_num)
                    conds_general.append(f"DAYOFWEEK({{campo_fecha}}) IN ({p_dias})")
                    
            # --- FILTRO 4: Origen (Solo aplica a Pedidos) ---
            conds_pedidos = list(conds_general)
            params_pedidos = list(params_general)
            
            if origen_seleccionado:
                conds_pedidos.append("{campo_origen} = %s")
                params_pedidos.append(origen_seleccionado)
                
            # Función constructora para inyectar los campos correctos en el string
            def build_where(conds, c_fecha, c_origen="origen"):
                if not conds: return ""
                return "WHERE " + " AND ".join([c.replace("{campo_fecha}", c_fecha).replace("{campo_origen}", c_origen) for c in conds])

            # Filtros Finales para las consultas
            filtro_pedidos = build_where(conds_pedidos, "fecha", "origen")
            filtro_compras = build_where(conds_general, "fecha") # Insumos no tiene "origen"
            filtro_ads = build_where(conds_general, "dia")
            filtro_org = build_where(conds_general, "hora_publicacion")
            
            filtro_tx_p = build_where(conds_pedidos, "p.fecha", "p.origen")
            if filtro_tx_p.startswith("WHERE"):
                filtro_tx_p = "AND " + filtro_tx_p[5:] # Quitar el WHERE para usar dentro del JOIN
                
            filtro_bcg = build_where(conds_pedidos, "pe.fecha", "pe.origen")
            
            # --- LÓGICA DE MES PREVIO (Para calcular Variaciones %) ---
            filtro_prev_pedidos = ""
            filtro_prev_compras = ""
            params_prev_pedidos = []
            params_prev_compras = []
            
            prev_m = None
            if meses_seleccionados and len(meses_seleccionados) == 1 and not fecha_inicio_seleccionada and not fecha_fin_seleccionada:
                prev_m = get_previous_month(meses_seleccionados[0])
            elif not meses_seleccionados and not fecha_inicio_seleccionada and not fecha_fin_seleccionada and meses_disponibles:
                prev_m = get_previous_month(meses_disponibles[0])
                
            if prev_m:
                conds_prev = ["DATE_FORMAT({campo_fecha}, '%%Y-%%m') = %s"]
                params_prev_base = [prev_m]
                
                if p_dias: # Mantenemos el filtro de días de semana para el mes previo
                    conds_prev.append(f"DAYOFWEEK({{campo_fecha}}) IN ({p_dias})")
                    
                conds_prev_pedidos = list(conds_prev)
                params_prev_pedidos = list(params_prev_base)
                
                if origen_seleccionado:
                    conds_prev_pedidos.append("{campo_origen} = %s")
                    params_prev_pedidos.append(origen_seleccionado)
                    
                filtro_prev_pedidos = build_where(conds_prev_pedidos, "fecha", "origen")
                filtro_prev_compras = build_where(conds_prev, "fecha")
                params_prev_compras = list(params_prev_base)


            # Días reales trabajados en el periodo (Cuenta solo los días con ventas registradas)
            cursor.execute(f"SELECT COUNT(DISTINCT DATE(fecha)) AS dias FROM pedidos {filtro_pedidos}", params_pedidos)
            dias_totales = int(cursor.fetchone()["dias"] or 1)
            meses_con_venta = len(meses_seleccionados) if meses_seleccionados else 1

            # === CÁLCULOS PERIODO ACTUAL ===
            cursor.execute(f"SELECT SUM(total) AS total FROM pedidos {filtro_pedidos}", params_pedidos)
            total_ingresos = Decimal(str(cursor.fetchone()["total"] or 0))
            
            cursor.execute(f"SELECT SUM(costo) AS total FROM insumos_compras {filtro_compras}", params_general)
            total_costos = Decimal(str(cursor.fetchone()["total"] or 0))
            
            utilidad = total_ingresos - total_costos
            gross_margin_pct = ((total_ingresos - total_costos) / total_ingresos * 100) if total_ingresos > 0 else 0

            # === CÁLCULOS PERIODO ANTERIOR (Para variaciones) ===
            var_ingresos = var_costos = var_utilidad = 0
            if filtro_prev_pedidos:
                cursor.execute(f"SELECT SUM(total) AS total FROM pedidos {filtro_prev_pedidos}", params_prev_pedidos)
                prev_ingresos = Decimal(str(cursor.fetchone()["total"] or 0))
                
                cursor.execute(f"SELECT SUM(costo) AS total FROM insumos_compras {filtro_prev_compras}", params_prev_compras)
                prev_costos = Decimal(str(cursor.fetchone()["total"] or 0))
                
                prev_utilidad = prev_ingresos - prev_costos
                
                var_ingresos = calc_var(float(total_ingresos), float(prev_ingresos))
                var_costos = calc_var(float(total_costos), float(prev_costos))
                var_utilidad = calc_var(float(utilidad), float(prev_utilidad))

            # === ANÁLISIS DE LEALTAD Y CLIENTES ===
            cursor.execute(f"""
                SELECT 
                    COUNT(DISTINCT p.id) as pedidos_loyalty,
                    SUM(p.total) as ventas_loyalty
                FROM pedidos p
                JOIN loyalty_tx tx ON p.id = tx.pedido_id
                WHERE tx.reason = 'purchase' {filtro_tx_p}
            """, params_pedidos)
            loyalty_data = cursor.fetchone()
            
            ventas_loyalty = Decimal(str(loyalty_data["ventas_loyalty"] or 0))
            pedidos_loyalty = int(loyalty_data["pedidos_loyalty"] or 0)
            
            ventas_casual = total_ingresos - ventas_loyalty
            
            cursor.execute(f"SELECT COUNT(id) as total_pedidos FROM pedidos {filtro_pedidos}", params_pedidos)
            total_pedidos_gral = int(cursor.fetchone()["total_pedidos"] or 0)
            pedidos_casuales = total_pedidos_gral - pedidos_loyalty

            tp_loyalty = (ventas_loyalty / pedidos_loyalty) if pedidos_loyalty > 0 else 0
            tp_casual = (ventas_casual / pedidos_casuales) if pedidos_casuales > 0 else 0

            loyalty_stats = {
                "ventas_loyalty": float(ventas_loyalty),
                "ventas_casual": float(ventas_casual),
                "ticket_promedio_loyalty": float(tp_loyalty),
                "ticket_promedio_casual": float(tp_casual)
            }

            # Top 5 Mejores Clientes
            cursor.execute(f"""
                SELECT c.nombre, c.phone_e164 as telefono, COUNT(DISTINCT p.id) as visitas, SUM(p.total) as gastado
                FROM loyalty_customers c
                JOIN loyalty_tx tx ON c.id = tx.customer_id
                JOIN pedidos p ON tx.pedido_id = p.id
                WHERE tx.reason = 'purchase' {filtro_tx_p}
                GROUP BY c.id
                ORDER BY gastado DESC LIMIT 5
            """, params_pedidos)
            top_clientes_raw = cursor.fetchall()
            top_clientes = []
            for c in top_clientes_raw:
                c["ticket_promedio"] = float(c["gastado"]) / float(c["visitas"]) if c["visitas"] > 0 else 0
                top_clientes.append(c)

            # === COMPARATIVAS DÍA A DÍA POR MES SELECCIONADO ===
            cursor.execute(f"""
                SELECT DAY(fecha) as dia_num, DATE_FORMAT(fecha, '%%Y-%%m') as mes, SUM(total) as total
                FROM pedidos
                {filtro_pedidos}
                GROUP BY mes, dia_num
            """, params_pedidos)
            ventas_comp_raw = cursor.fetchall()
            ventas_comparativas = {}
            for r in ventas_comp_raw:
                mes = r["mes"]
                if mes not in ventas_comparativas: ventas_comparativas[mes] = {}
                ventas_comparativas[mes][r["dia_num"]] = float(r["total"] or 0)

            cursor.execute(f"""
                SELECT DAY(fecha) as dia_num, DATE_FORMAT(fecha, '%%Y-%%m') as mes, SUM(costo) as total
                FROM insumos_compras
                {filtro_compras}
                GROUP BY mes, dia_num
            """, params_general)
            gastos_comp_raw = cursor.fetchall()
            gastos_comparativas = {}
            for r in gastos_comp_raw:
                mes = r["mes"]
                if mes not in gastos_comparativas: gastos_comparativas[mes] = {}
                gastos_comparativas[mes][r["dia_num"]] = float(r["total"] or 0)

            # === HISTÓRICOS GLOBALES CONTINUOS === (Se mantienen completos por definición)
            cursor.execute("""
                SELECT DATE(fecha) as f, SUM(total) as total 
                FROM pedidos 
                GROUP BY f ORDER BY f
            """)
            historico_ingresos = [{"fecha": str(r["f"]), "total": float(r["total"] or 0)} for r in cursor.fetchall()]

            cursor.execute("""
                SELECT DATE(fecha) as f, SUM(costo) as total 
                FROM insumos_compras 
                GROUP BY f ORDER BY f
            """)
            historico_gastos = [{"fecha": str(r["f"]), "total": float(r["total"] or 0)} for r in cursor.fetchall()]

            # === INGENIERÍA DE MENÚ ===
            cursor.execute(f"""
                SELECT p.nombre,
                       SUM(pi.cantidad) AS cantidad,
                       SUM(pi.subtotal) AS ingreso_total,
                       ((SUM(pi.subtotal) / SUM(pi.cantidad)) - COALESCE(p.costo, 0)) AS margen_unitario
                FROM pedido_items pi
                JOIN pedidos pe ON pe.id = pi.pedido_id
                JOIN productos p ON p.id = pi.producto_id
                {filtro_bcg}
                GROUP BY p.id, p.nombre, p.costo
                ORDER BY ingreso_total DESC
            """, params_pedidos)
            bcg_raw = cursor.fetchall()
            
            for item in bcg_raw:
                item["cantidad_promedio"] = float(item["cantidad"] or 0) / dias_totales
                item["ingreso_promedio"] = float(item["ingreso_total"] or 0) / dias_totales

            menu_engineering_data = [{"nombre": i["nombre"], "x": float(i["cantidad"]), "x_promedio": float(i["cantidad"] or 0)/dias_totales, "y": float(i["margen_unitario"]), "y_promedio": float(i["margen_unitario"])} for i in bcg_raw]

            # === HORAS Y DÍAS DE LA SEMANA ===
            cursor.execute(f"SELECT HOUR(fecha) AS hora_num, COUNT(*) AS total_pedidos, SUM(total) AS total_dinero FROM pedidos {filtro_pedidos} GROUP BY HOUR(fecha) ORDER BY hora_num", params_pedidos)
            ventas_hora = [{"hora": f"{v['hora_num']}:00", "total": float(v["total_dinero"] or 0), "promedio": float(v["total_dinero"] or 0) / dias_totales} for v in cursor.fetchall()]

            cursor.execute(f"""
                SELECT dia_num, nombre, ROUND(AVG(total_del_dia), 2) AS promedio, SUM(total_del_dia) AS total
                FROM (
                    SELECT DAYOFWEEK(fecha) AS dia_num,
                           CASE DAYOFWEEK(fecha) WHEN 1 THEN 'Dom' WHEN 2 THEN 'Lun' WHEN 3 THEN 'Mar' WHEN 4 THEN 'Mie' WHEN 5 THEN 'Jue' WHEN 6 THEN 'Vie' WHEN 7 THEN 'Sab' END AS nombre,
                           DATE(fecha) AS f, SUM(total) AS total_del_dia
                    FROM pedidos {filtro_pedidos} GROUP BY DATE(fecha), dia_num, nombre
                ) t
                GROUP BY dia_num, nombre ORDER BY dia_num
            """, params_pedidos)
            ventas_semana = [{"nombre": v["nombre"], "promedio": float(v["promedio"] or 0), "total": float(v["total"] or 0)} for v in cursor.fetchall()]

            # === TABLAS DE APOYO Y ÚLTIMOS PEDIDOS ===
            top_productos = bcg_raw[:10]
            cursor.execute(f"SELECT concepto, tipo_costo, COUNT(*) AS veces, SUM(costo) AS total_gastado FROM insumos_compras {filtro_compras} GROUP BY concepto, tipo_costo ORDER BY total_gastado DESC LIMIT 10", params_general)
            top_gastos = cursor.fetchall()
            for g in top_gastos: 
                g["promedio_gastado"] = float(g["total_gastado"] or 0) / meses_con_venta

            # =======================================================
            # === NUEVO: DESGLOSE POR CONCEPTO (INGRESOS Y GASTOS) ==
            # =======================================================
            
            # 1. Ingresos por Concepto (agrupados por 'categoria' de producto)
            cursor.execute(f"""
                SELECT COALESCE(p.categoria, 'Otros') AS concepto, SUM(pi.subtotal) AS total
                FROM pedido_items pi
                JOIN pedidos pe ON pe.id = pi.pedido_id
                JOIN productos p ON p.id = pi.producto_id
                {filtro_bcg}
                GROUP BY p.categoria
                ORDER BY total DESC
            """, params_pedidos)
            
            ingresos_por_concepto = [
                {
                    "concepto": r["concepto"],
                    "total": float(r["total"] or 0),
                    "promedio": float(r["total"] or 0) / dias_totales
                } for r in cursor.fetchall()
            ]

            # 2. Gastos por Concepto (agrupados por 'concepto')
            cursor.execute(f"""
                SELECT COALESCE(concepto, 'Otros') AS concepto, SUM(costo) AS total
                FROM insumos_compras
                {filtro_compras}
                GROUP BY concepto
                ORDER BY total DESC
            """, params_general)
            
            gastos_por_concepto = [
                {
                    "concepto": str(r["concepto"]).capitalize(),
                    "total": float(r["total"] or 0),
                    "promedio": float(r["total"] or 0) / dias_totales
                } for r in cursor.fetchall()
            ]

            # ÚLTIMOS PEDIDOS
            cursor.execute(f"SELECT id, DATE_FORMAT(fecha, '%%Y-%%m-%%d %%H:%%i') as fecha, origen, mesero, estado, total FROM pedidos {filtro_pedidos} ORDER BY fecha DESC LIMIT 15", params_pedidos)
            ultimos_pedidos = cursor.fetchall()

            # =======================================================
            # === DATOS DE MARKETING (INSTAGRAM ADS) ================
            # =======================================================
            cursor.execute(f"""
                SELECT 
                    SUM(importe_gastado) AS total_ads,
                    SUM(alcance) AS total_alcance,
                    SUM(impresiones) AS total_impresiones
                FROM ads_instagram_performance {filtro_ads}
            """, params_general)
            ads_totals = cursor.fetchone()
            
            total_gasto_ads = Decimal(str(ads_totals["total_ads"] or 0)) if ads_totals["total_ads"] else Decimal(0)
            total_alcance = int(ads_totals["total_alcance"] or 0)
            total_impresiones = int(ads_totals["total_impresiones"] or 0)
            
            # CAC y ROAS
            cac_global = float(total_gasto_ads) / total_pedidos_gral if total_pedidos_gral > 0 else 0
            roas_global = float(total_ingresos) / float(total_gasto_ads) if total_gasto_ads > 0 else 0
            
            # Gráficas combinadas diarias (Ventas, Ads, Alcance, Impresiones)
            cursor.execute(f"SELECT DATE(fecha) as f, SUM(total) as total FROM pedidos {filtro_pedidos} GROUP BY DATE(fecha)", params_pedidos)
            ventas_dict = {str(r["f"]): float(r["total"] or 0) for r in cursor.fetchall()}
            
            cursor.execute(f"""
                SELECT dia as f, SUM(importe_gastado) as total_gasto, SUM(alcance) as alcance, SUM(impresiones) as impresiones 
                FROM ads_instagram_performance {filtro_ads} 
                GROUP BY dia
            """, params_general)
            ads_dict = {str(r["f"]): r for r in cursor.fetchall()}
            
            todas_las_fechas = sorted(list(set(ventas_dict.keys()).union(set(ads_dict.keys()))))
            ads_vs_ventas = [
                {
                    "fecha": f, 
                    "ventas_reales": ventas_dict.get(f, 0.0), 
                    "gasto_ads": float(ads_dict.get(f, {}).get("total_gasto", 0) if ads_dict.get(f) else 0),
                    "alcance": int(ads_dict.get(f, {}).get("alcance", 0) if ads_dict.get(f) else 0),
                    "impresiones": int(ads_dict.get(f, {}).get("impresiones", 0) if ads_dict.get(f) else 0)
                } 
                for f in todas_las_fechas
            ]
            
            # =======================================================
            # === DATOS ORGÁNICOS (INSTAGRAM) =======================
            # =======================================================
            cursor.execute(f"""
                SELECT 
                    DATE(hora_publicacion) as f, 
                    SUM(COALESCE(alcance, 0)) as alcance, 
                    SUM(COALESCE(visualizaciones, 0)) as impresiones,
                    SUM(COALESCE(me_gusta, 0) + COALESCE(comentarios, 0) + COALESCE(veces_compartido, 0) + COALESCE(veces_guardado, 0)) as interacciones
                FROM organic_instagram_performance 
                {filtro_org} 
                GROUP BY DATE(hora_publicacion)
            """, params_general)
            org_rows = cursor.fetchall()
            
            # Filtramos para que solo tome fechas válidas y no choque con nulos
            org_dict = {str(r["f"]): r for r in org_rows if r["f"] is not None}
            todas_las_fechas_extendidas = sorted(list(set(todas_las_fechas).union(set(org_dict.keys()))))
            
            org_vs_ventas = [
                {
                    "fecha": f, 
                    "ventas_reales": ventas_dict.get(f, 0.0), 
                    "alcance_org": int(org_dict.get(f, {}).get("alcance") or 0),
                    "interacciones_org": int(org_dict.get(f, {}).get("interacciones") or 0)
                } 
                for f in todas_las_fechas_extendidas
            ]
    finally:
        conn.close()

    # Retornamos todo inyectando los filtros nuevos al final para que el HTML guarde su estado seleccionado
    return render_template(
        "dashboard.html",
        meses_seleccionados=meses_seleccionados, 
        meses_disponibles=meses_disponibles,
        total_ingresos=float(total_ingresos), 
        dias_totales=dias_totales,
        var_ingresos=var_ingresos,
        total_costos=float(total_costos), 
        var_costos=var_costos,
        utilidad=float(utilidad), 
        var_utilidad=var_utilidad,
        gross_margin_pct=round(float(gross_margin_pct), 1),
        menu_engineering_data=json.dumps(menu_engineering_data),
        loyalty_stats=loyalty_stats, 
        top_clientes=top_clientes,
        ventas_hora=ventas_hora, 
        ventas_por_dia_semana=ventas_semana, 
        top_productos=top_productos, 
        top_gastos=top_gastos, 
        ultimos_pedidos=ultimos_pedidos,
        ventas_comparativas=ventas_comparativas,
        gastos_comparativas=gastos_comparativas,
        historico_ingresos=historico_ingresos,
        historico_gastos=historico_gastos,
        ingresos_por_concepto=ingresos_por_concepto, # NUEVA VARIABLE
        gastos_por_concepto=gastos_por_concepto,     # NUEVA VARIABLE
        total_gasto_ads=float(total_gasto_ads),
        total_alcance=total_alcance,
        total_impresiones=total_impresiones,
        cac_global=cac_global,
        roas_global=round(roas_global, 2),
        ads_vs_ventas=ads_vs_ventas,
        org_vs_ventas=org_vs_ventas,
        # Nuevos filtros conservados para el front-end
        fecha_inicio_seleccionada=fecha_inicio_seleccionada,
        fecha_fin_seleccionada=fecha_fin_seleccionada,
        dias_seleccionados=dias_seleccionados,
        origen_seleccionado=origen_seleccionado
    )




# =========================================================
# ================== CONTROL DE COCINA ====================
# =========================================================

@app.route("/api/item/<int:item_id>/toggle_cocina", methods=["POST"])
def toggle_item_cocina(item_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Invierte el estado: si era 0 lo hace 1, si era 1 lo hace 0
            cur.execute("UPDATE pedido_items SET entregado = NOT entregado WHERE id = %s", (item_id,))
            conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        conn.close()




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
            cursor.execute("SELECT id, estado FROM pedidos WHERE id=%s", (pedido_id,))
            pedido = cursor.fetchone()

            if not pedido:
                flash("Pedido no encontrado", "error")
                return redirect(url_for("borrar_pedidos"))

            if (pedido.get("estado") or "") == "cerrado":
                cursor.execute("""
                    DELETE FROM inventario_movimientos
                    WHERE tipo='salida_venta'
                      AND ref_tabla='pedidos'
                      AND ref_id=%s
                """, (pedido_id,))

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
    has_salsa = table_has_column(cursor, "pedido_items", "salsa_id")
    
    if has_salsa:
        cursor.execute("""
            SELECT p.nombre, pi.cantidad, pi.precio_unitario, pi.proteina, pi.sin, pi.nota, s.nombre AS salsa
            FROM pedido_items pi
            JOIN productos p ON p.id = pi.producto_id
            LEFT JOIN salsas s ON pi.salsa_id = s.id
            WHERE pi.pedido_id = %s
            ORDER BY pi.id ASC
        """, (pedido_id,))
    else:
        cursor.execute("""
            SELECT p.nombre, pi.cantidad, pi.precio_unitario, pi.proteina, pi.sin, pi.nota, NULL AS salsa
            FROM pedido_items pi
            JOIN productos p ON p.id = pi.producto_id
            WHERE pi.pedido_id = %s
            ORDER BY pi.id ASC
        """, (pedido_id,))
        
    items = cursor.fetchall()

    cursor.execute("SELECT total FROM pedidos WHERE id = %s", (pedido_id,))
    pedido = cursor.fetchone()

    lines = []
    # \U0001F44B = Manita saludando 👋
    lines.append("¡Hola! \U0001F44B Aquí tienes el resumen de tu pedido:\n")

    subtotal_items = Decimal("0")

    for it in items:
        subtotal = Decimal(str(it["cantidad"])) * Decimal(str(it["precio_unitario"]))
        subtotal_items += subtotal
        
        # \u25AA\uFE0F = Cuadrito negro ▪️
        lines.append(f'\u25AA\uFE0F {it["cantidad"]}x {it["nombre"]} (${float(subtotal):.2f})')

        if it.get("proteina") and it.get("proteina") != "Sin proteina":
            # \U0001F373 = Sartén con huevo 🍳
            lines.append(f'   \U0001F373 {it["proteina"]}')
        if it.get("salsa"):
            # \U0001F336\uFE0F = Chile 🌶️
            lines.append(f'   \U0001F336\uFE0F {it["salsa"]}')
        if it.get("sin"):
            # \U0001F6AB = Señal prohibido 🚫
            lines.append(f'   \U0001F6AB Sin {it["sin"]}')
        
        nota = it.get("nota")
        if nota:
            # Reemplazamos la mano (👉) por el más (➕)
            if "👉 Para:" in nota or "\U0001F449 Para:" in nota:
                nota = nota.replace("👉 Para:", "\u2795 Extra para:").replace("\U0001F449 Para:", "\u2795 Extra para:")
            # \U0001F4DD = Papel con lápiz 📝
            lines.append(f'   \U0001F4DD {nota}')

    total = Decimal(str(pedido["total"] or 0)) if pedido else Decimal("0")
    
    lines.append(f"\nSubtotal: ${float(subtotal_items):.2f}")
    if subtotal_items != total:
        descuento = subtotal_items - total
        lines.append(f"Descuento: -${float(descuento):.2f}")
        
    lines.append(f"Total a pagar: ${float(total):.2f}")

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
    desde = (request.args.get("desde") or "").strip()  
    hasta = (request.args.get("hasta") or "").strip()  

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

            cursor.execute(f"""
                SELECT id
                FROM pedidos
                WHERE id IN ({placeholders})
                  AND estado = 'cerrado'
            """, ids_int)
            cerrados = [r["id"] for r in cursor.fetchall()]

            if cerrados:
                ph2 = ",".join(["%s"] * len(cerrados))
                cursor.execute(f"""
                    DELETE FROM inventario_movimientos
                    WHERE tipo='salida_venta'
                      AND ref_tabla='pedidos'
                      AND ref_id IN ({ph2})
                """, cerrados)

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


@app.post("/productos/<int:producto_id>/eliminar")
def eliminar_producto_producto(producto_id):  
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                UPDATE productos
                SET activo = 0
                WHERE id = %s
            """, (producto_id,))
            conn.commit()
            flash("Producto eliminado (desactivado).", "success")
            return redirect(url_for("productos"))
    finally:
        conn.close()



def calcular_costo_platillo(cursor, platillo_id: int) -> Decimal:
    cursor.execute("""
        SELECT
            COALESCE(SUM(
                (r.cantidad_base * (1 + (i.merma_pct / 100))) *
                (
                    CASE
                        WHEN r.usa_precio_manual = 1 AND r.precio_manual IS NOT NULL
                            THEN r.precio_manual
                        ELSE COALESCE((
                            SELECT ic.costo_unitario
                            FROM insumos_compras ic
                            WHERE ic.insumo_id = r.insumo_id
                              AND ic.costo_unitario IS NOT NULL
                            ORDER BY ic.fecha DESC, ic.id DESC
                            LIMIT 1
                        ), 0)
                    END
                )
            ), 0) AS costo_platillo
        FROM recetas r
        JOIN insumos i ON i.id = r.insumo_id
        WHERE r.platillo_id = %s
    """, (platillo_id,))
    row = cursor.fetchone()
    return Decimal(str(row["costo_platillo"] or 0))


@app.get("/api/platillos/<int:platillo_id>/costo")
def api_platillo_costo(platillo_id):
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            costo = calcular_costo_platillo(cursor, platillo_id)
            return jsonify({"platillo_id": platillo_id, "costo": float(costo)})
    finally:
        conn.close()


@app.post("/platillos/<int:platillo_id>/proteina_qty")
def platillo_set_proteina_qty(platillo_id):
    proteina_id_txt = (request.form.get("proteina_id") or "").strip()
    cantidad_txt = (request.form.get("cantidad_base") or "").strip()

    if not proteina_id_txt.isdigit():
        flash("Proteína inválida.", "error")
        return redirect(request.referrer or url_for("productos"))

    try:
        cantidad_base = Decimal(cantidad_txt)
    except Exception:
        flash("Cantidad inválida.", "error")
        return redirect(request.referrer or url_for("productos"))

    if cantidad_base <= 0:
        flash("La cantidad debe ser mayor a 0.", "error")
        return redirect(request.referrer or url_for("productos"))

    proteina_id = int(proteina_id_txt)

    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            conn.begin()

            cur.execute("SELECT insumo_id, nombre FROM proteinas WHERE id=%s", (proteina_id,))
            pr = cur.fetchone()
            if not pr or not pr.get("insumo_id"):
                conn.rollback()
                flash("Esa proteína no está ligada a ningún insumo (proteinas.insumo_id).", "error")
                return redirect(request.referrer or url_for("productos"))

            insumo_id = int(pr["insumo_id"])

            cur.execute("SELECT descuenta_stock, unidad_base FROM insumos WHERE id=%s", (insumo_id,))
            ins = cur.fetchone()
            if not ins:
                conn.rollback()
                flash("El insumo ligado a la proteína no existe.", "error")
                return redirect(request.referrer or url_for("productos"))

            if int(ins.get("descuenta_stock") or 0) != 1:
                conn.rollback()
                flash("Ese insumo no descuenta stock (insumos.descuenta_stock=0).", "error")
                return redirect(request.referrer or url_for("productos"))

            cur.execute("""
                INSERT INTO recetas_proteina (platillo_id, proteina_id, insumo_id, cantidad_base)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    insumo_id = VALUES(insumo_id),
                    cantidad_base = VALUES(cantidad_base)
            """, (platillo_id, proteina_id, insumo_id, str(cantidad_base)))

            conn.commit()

            ub = ins.get("unidad_base") or ""
            flash(f"Guardado ✅ Proteína {pr.get('nombre','')} = {cantidad_base} {ub} para este platillo.", "success")
            return redirect(request.referrer or url_for("productos"))

    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

# =========================================================
# ================== CAMPAÑAS DE RETENCIÓN ==================
# =========================================================

@app.route("/campanas")
def campanas():
    # Por defecto busca clientes que no han venido en 30 días
    dias_str = request.args.get("dias", "30")
    dias = int(dias_str) if dias_str.isdigit() else 30
    
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            # Buscamos clientes cuya última compra supere X días
            cursor.execute("""
                SELECT 
                    c.id, c.nombre, c.phone_e164, 
                    a.totopos_balance,
                    MAX(p.fecha) as ultima_compra,
                    DATEDIFF(NOW(), MAX(p.fecha)) as dias_ausente
                FROM loyalty_customers c
                JOIN loyalty_tx tx ON c.id = tx.customer_id
                JOIN pedidos p ON tx.pedido_id = p.id
                LEFT JOIN loyalty_accounts a ON c.id = a.customer_id
                WHERE tx.reason = 'purchase'
                GROUP BY c.id
                HAVING dias_ausente >= %s
                ORDER BY dias_ausente DESC
            """, (dias,))
            clientes_inactivos = cursor.fetchall()
    finally:
        conn.close()

    # Construimos el mensaje de WhatsApp personalizado para cada uno
    for c in clientes_inactivos:
        telefono = (c["phone_e164"] or "").replace("+", "")
        nombre_completo = c["nombre"] or "amigo"
        nombre = nombre_completo.split()[0] # Tomamos solo el primer nombre
        
        mensaje = f"¡Hola {nombre}! 👋 Te extrañamos en Señor Chilaquil.\n\nHace un rato que no nos visitas y queremos consentirte. 🌶️ En tu próximo pedido, muéstranos este mensaje y te regalamos un *Totopo extra* 🌮✨ a tu cuenta.\n\n¡Te esperamos pronto!"
        msg_q = urllib.parse.quote(mensaje)
        
        c["wa_link"] = f"https://wa.me/{telefono}?text={msg_q}" if telefono else None

    return render_template("campanas.html", clientes=clientes_inactivos, dias=dias)



# =========================================================
# ================== CORTE DE CAJA ========================
# =========================================================

@app.route("/corte_caja", methods=["GET", "POST"])
def corte_caja():
    fecha_str = request.args.get("fecha")
    if not fecha_str:
        fecha_str = datetime.now().strftime("%Y-%m-%d")

    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            
            # 1. Verificar si hay pedidos ABIERTOS
            cursor.execute("""
                SELECT COUNT(*) as abiertos FROM pedidos 
                WHERE DATE(fecha) = %s AND estado = 'abierto'
            """, (fecha_str,))
            pedidos_abiertos = cursor.fetchone()["abiertos"]

            # 2. Resumen de ventas por método de pago (solo cerrados)
            cursor.execute("""
                SELECT COALESCE(metodo_pago, 'Otro') as metodo_pago, SUM(total) as total_ventas 
                FROM pedidos 
                WHERE DATE(fecha) = %s AND estado = 'cerrado'
                GROUP BY metodo_pago
            """, (fecha_str,))
            ventas_dia = cursor.fetchall()

            # 3. CORREGIDO: Obtener el TOTAL de gastos de la fecha (sin filtrar por texto 'efectivo')
            cursor.execute("""
                SELECT SUM(costo) as total_gastos 
                FROM insumos_compras 
                WHERE DATE(fecha) = %s
            """, (fecha_str,))
            gastos_row = cursor.fetchone()
            total_gastos = Decimal(str(gastos_row["total_gastos"] or 0))

            # Organizar variables del sistema
            ventas_totales = Decimal("0")
            efectivo_sistema = Decimal("0")
            tarjeta_sistema = Decimal("0")
            transferencia_sistema = Decimal("0")
            otros_sistema = Decimal("0")

            for v in ventas_dia:
                monto = Decimal(str(v["total_ventas"] or 0))
                ventas_totales += monto
                metodo = v["metodo_pago"].lower()
                
                if "efectivo" in metodo:
                    efectivo_sistema += monto
                elif "tarjeta" in metodo:
                    tarjeta_sistema += monto
                elif "transferencia" in metodo:
                    transferencia_sistema += monto
                else:
                    otros_sistema += monto

            # Banco Esperado Sistema = Tarjeta + Transferencia
            banco_esperado_sistema = tarjeta_sistema + transferencia_sistema

            # Ver si ya existe un corte guardado para hoy
            cursor.execute("SELECT * FROM cortes_caja WHERE fecha_corte = %s", (fecha_str,))
            corte_guardado = cursor.fetchone()

            # --- PROCESAR EL GUARDADO DEL CORTE (POST) ---
            if request.method == "POST":
                if pedidos_abiertos > 0:
                    flash(f"¡Cuidado! Hay {pedidos_abiertos} pedido(s) abierto(s). Ciérralos primero.", "error")
                    return redirect(url_for("corte_caja", fecha=fecha_str))

                fondo_caja = parse_decimal_mx(request.form.get("fondo_caja", "0")) or Decimal("0")
                efectivo_fisico = parse_decimal_mx(request.form.get("efectivo_fisico", "0")) or Decimal("0")
                tarjeta_fisico = parse_decimal_mx(request.form.get("tarjeta_fisico", "0")) or Decimal("0")
                notas = request.form.get("notas", "")
                
                # Cálculos de diferencias
                efectivo_esperado = fondo_caja + efectivo_sistema - total_gastos
                diferencia_efectivo = efectivo_fisico - efectivo_esperado
                diferencia_tarjeta = tarjeta_fisico - banco_esperado_sistema

                if corte_guardado:
                    cursor.execute("""
                        UPDATE cortes_caja 
                        SET fondo_caja=%s, ventas_totales=%s, efectivo_sistema=%s, tarjeta_sistema=%s, 
                            transferencia_sistema=%s, otros_sistema=%s, gastos_dia=%s, efectivo_fisico=%s, 
                            tarjeta_fisico=%s, diferencia=%s, diferencia_tarjeta=%s, notas=%s
                        WHERE fecha_corte=%s
                    """, (str(fondo_caja), str(ventas_totales), str(efectivo_sistema), str(tarjeta_sistema), 
                          str(transferencia_sistema), str(otros_sistema), str(total_gastos), str(efectivo_fisico), 
                          str(tarjeta_fisico), str(diferencia_efectivo), str(diferencia_tarjeta), notas, fecha_str))
                    flash("Corte de caja actualizado correctamente.", "success")
                else:
                    cursor.execute("""
                        INSERT INTO cortes_caja (fecha_corte, fondo_caja, ventas_totales, efectivo_sistema, 
                                                 tarjeta_sistema, transferencia_sistema, otros_sistema, 
                                                 gastos_dia, efectivo_fisico, tarjeta_fisico, diferencia, diferencia_tarjeta, notas)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (fecha_str, str(fondo_caja), str(ventas_totales), str(efectivo_sistema), 
                          str(tarjeta_sistema), str(transferencia_sistema), str(otros_sistema), 
                          str(total_gastos), str(efectivo_fisico), str(tarjeta_fisico), str(diferencia_efectivo), str(diferencia_tarjeta), notas))
                    flash("Corte de caja guardado exitosamente.", "success")
                
                conn.commit()
                return redirect(url_for("corte_caja", fecha=fecha_str))

            # 4. NUEVO: Traer el historial de los últimos 15 cortes realizados para mostrar abajo
            cursor.execute("""
                SELECT fecha_corte AS fecha, fondo_caja, efectivo_fisico, tarjeta_fisico, 
                       diferencia, diferencia_tarjeta, notas 
                FROM cortes_caja 
                ORDER BY fecha_corte DESC LIMIT 15
            """)
            historial_cortes = cursor.fetchall()

    finally:
        conn.close()

    fondo_mostrar = Decimal(str(corte_guardado["fondo_caja"])) if corte_guardado else Decimal("0")
    efectivo_esperado = fondo_mostrar + efectivo_sistema - total_gastos

    return render_template(
        "corte_caja.html",
        fecha=fecha_str,
        pedidos_abiertos=pedidos_abiertos,
        ventas_totales=ventas_totales,
        efectivo_sistema=efectivo_sistema,
        tarjeta_sistema=tarjeta_sistema,
        transferencia_sistema=transferencia_sistema,
        banco_esperado_sistema=banco_esperado_sistema,
        otros_sistema=otros_sistema,
        total_gastos=total_gastos,
        efectivo_esperado=efectivo_esperado,
        corte_guardado=corte_guardado,
        cortes=historial_cortes # Enviamos la lista a la vista
    )

# ================== RUN ==================
if __name__ == "__main__":
    app.run(debug=True)
