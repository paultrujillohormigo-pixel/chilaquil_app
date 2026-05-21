import urllib.parse
import re
import pymysql
import json

from flask import Flask, request, redirect, url_for, flash, render_template, jsonify, send_from_directory
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta
from db import get_connection
from costeo import costeo_bp

app = Flask(__name__)
app.secret_key = "super_secret_key"  # cámbiala en prod

# ================== COSTEO ==================
app.register_blueprint(costeo_bp)

@app.route("/")
def index():
    return render_template("index.html")

@app.route('/menu')
def menu():
    return render_template('menu.html')

@app.route('/carta')
def mostrar_carta():
    return send_from_directory(app.static_folder, 'carta.pdf')

@app.route('/ver-pdf')
def ver_pdf():
    return send_from_directory(app.static_folder, 'menu_Mayo.pdf')

# =========================================================
# ================== HELPERS ==============================
# =========================================================

def normalize_phone_mx(raw: str) -> str | None:
    if not raw: return None
    s = re.sub(r"[^\d+]", "", raw).strip()
    s_digits = re.sub(r"\D", "", s)
    if len(s_digits) == 10: return "+52" + s_digits
    if len(s_digits) == 12 and s_digits.startswith("52"): return "+" + s_digits
    if len(s_digits) == 13 and s_digits.startswith("521"): return "+" + s_digits
    return None

def table_has_column(cursor, table_name: str, col_name: str) -> bool:
    cursor.execute("""
        SELECT 1 FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s LIMIT 1
    """, (table_name, col_name))
    return cursor.fetchone() is not None

def wa_me_link(phone_e164: str, message_text: str) -> str:
    phone = (phone_e164 or "").replace("+", "")
    msg_bytes = message_text.encode("utf-8", "strict")
    msg_q = urllib.parse.quote_from_bytes(msg_bytes)
    return f"https://wa.me/{phone}?text={msg_q}"

def parse_decimal_mx(val: str | None) -> Decimal | None:
    if val is None: return None
    s = str(val).strip()
    if s == "" or s.lower() in {"na", "nan", "n/a", "none", "null", "-"}: return None
    s = re.sub(r"\s+", "", s)
    if "," in s and "." not in s: s = s.replace(",", ".")
    else: s = s.replace(",", "")
    try: return Decimal(s)
    except (InvalidOperation, ValueError): return None

@app.template_filter("money")
def money_format(value):
    try: return "${:,.2f}".format(float(value))
    except Exception: return value

# =========================================================
# ================== LOYALTY (TOTOPOS) ====================
# =========================================================

E = {"title": "*", "receipt": "#", "pay": "$", "check": "OK", "pin": "-", "gift": "*", "drink": "Una bebida gratis", "plate": "Un plato fuerte gratis", "arrow": "->"}
BAR_ON = "#"
BAR_OFF = "-"

def make_bar(balance: int, goal: int) -> str:
    if goal <= 0: return ""
    prog = balance % goal
    if prog == 0 and balance > 0: prog = goal
    filled = min(prog, goal)
    return (BAR_ON * filled) + (BAR_OFF * (goal - filled))

def faltan_para(balance: int, goal: int) -> int:
    if goal <= 0: return 0
    r = balance % goal
    return 0 if (r == 0 and balance > 0) else (goal - r)

def loyalty_get_or_create_customer(cursor, phone_e164: str) -> int:
    cursor.execute("SELECT id FROM loyalty_customers WHERE phone_e164=%s", (phone_e164,))
    row = cursor.fetchone()
    if row: return row["id"]
    cursor.execute("INSERT INTO loyalty_customers (phone_e164) VALUES (%s)", (phone_e164,))
    customer_id = cursor.lastrowid
    cursor.execute("INSERT INTO loyalty_accounts (customer_id, totopos_balance, totopos_lifetime) VALUES (%s,0,0)", (customer_id,))
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

    cursor.execute("UPDATE loyalty_accounts SET totopos_balance = totopos_balance + %s, totopos_lifetime = totopos_lifetime + %s WHERE customer_id=%s", (earned, earned, customer_id))
    cursor.execute("INSERT INTO loyalty_tx (customer_id, pedido_id, delta, reason) VALUES (%s,%s,%s,'purchase')", (customer_id, pedido_id, earned))
    cursor.execute("SELECT totopos_balance FROM loyalty_accounts WHERE customer_id=%s", (customer_id,))
    row = cursor.fetchone()
    return row["totopos_balance"] if row else 0

def loyalty_message(balance: int, earned: int, pedido_id: int, total: Decimal, phone: str) -> str:
    bar5 = make_bar(balance, 5)
    bar10 = make_bar(balance, 10)
    f5 = faltan_para(balance, 5)
    f10 = faltan_para(balance, 10)
    canje = []
    if f5 == 0: canje.append(f"{E['check']} Ya puedes canjear una bebida (excepto chai).")
    if f10 == 0: canje.append(f"{E['check']} Ya puedes canjear un plato fuerte.")
    canje_txt = "\n".join(canje) if canje else "Sigue acumulando totopos :)"
    phone_clean = phone.replace("+", "") if phone else ""
    url_perfil = url_for('mi_perfil', phone=phone_clean, _external=True)
    link_perfil = f"\nConsulta tus puntos aquí:\n👉 {url_perfil}\n"
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
        f"{canje_txt}\n{link_perfil}"
    )

# =========================================================
# ================== DASHBOARD AVANZADO ===================
# =========================================================

# Función de ayuda para sacar el mes anterior (formato YYYY-MM)
def get_previous_month(ym_str):
    try:
        dt = datetime.strptime(ym_str, "%Y-%m")
        first_day = dt.replace(day=1)
        prev_month = first_day - timedelta(days=1)
        return prev_month.strftime("%Y-%m")
    except:
        return None

# Función de cálculo de variación porcentual segura
def calc_var(current, prev):
    if not prev or prev == 0: return 0
    return round(((current - prev) / prev) * 100, 1)

@app.route("/dashboard")
def dashboard():
    meses_seleccionados = request.args.getlist("mes")
    conn = get_connection()

    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            # 1. Filtros y Meses Previos (Para comparar)
            filtro = ""
            params = []
            filtro_prev = ""
            params_prev = []
            
            cursor.execute("SELECT DISTINCT DATE_FORMAT(fecha, '%Y-%m') AS mes FROM pedidos ORDER BY mes DESC")
            meses_disp_raw = cursor.fetchall()
            meses_disponibles = [m["mes"] for m in meses_disp_raw]
            
            # Lógica para comparar con el periodo anterior
            if meses_seleccionados:
                placeholders = ",".join(["%s"] * len(meses_seleccionados))
                filtro = f"WHERE DATE_FORMAT(fecha, '%%Y-%%m') IN ({placeholders})"
                params.extend(meses_seleccionados)
                
                # Si solo seleccionó 1 mes, sacamos el anterior exacto para comparar
                if len(meses_seleccionados) == 1:
                    prev_m = get_previous_month(meses_seleccionados[0])
                    if prev_m:
                        filtro_prev = f"WHERE DATE_FORMAT(fecha, '%%Y-%%m') = %s"
                        params_prev.append(prev_m)
            else:
                # Si no hay filtro, tomamos el mes más reciente vs el anterior
                if meses_disponibles:
                    last_m = meses_disponibles[0]
                    filtro = f"WHERE DATE_FORMAT(fecha, '%%Y-%%m') = %s"
                    params.append(last_m)
                    
                    prev_m = get_previous_month(last_m)
                    if prev_m:
                        filtro_prev = f"WHERE DATE_FORMAT(fecha, '%%Y-%%m') = %s"
                        params_prev.append(prev_m)

            # Días reales trabajados en el periodo
            cursor.execute(f"SELECT COUNT(DISTINCT DATE(fecha)) AS dias FROM pedidos {filtro}", params)
            dias_totales = int(cursor.fetchone()["dias"] or 1)
            meses_con_venta = len(meses_seleccionados) if meses_seleccionados else 1

            # === CÁLCULOS PERIODO ACTUAL ===
            # Ingresos
            cursor.execute(f"SELECT SUM(total) AS total FROM pedidos {filtro}", params)
            total_ingresos = Decimal(str(cursor.fetchone()["total"] or 0))
            
            # Costos
            cursor.execute(f"SELECT SUM(costo) AS total FROM insumos_compras {filtro}", params)
            total_costos = Decimal(str(cursor.fetchone()["total"] or 0))
            
            utilidad = total_ingresos - total_costos
            gross_margin_pct = ((total_ingresos - total_costos) / total_ingresos * 100) if total_ingresos > 0 else 0

            # === CÁLCULOS PERIODO ANTERIOR (Para variaciones) ===
            var_ingresos = var_costos = var_utilidad = 0
            if filtro_prev:
                cursor.execute(f"SELECT SUM(total) AS total FROM pedidos {filtro_prev}", params_prev)
                prev_ingresos = Decimal(str(cursor.fetchone()["total"] or 0))
                
                cursor.execute(f"SELECT SUM(costo) AS total FROM insumos_compras {filtro_prev}", params_prev)
                prev_costos = Decimal(str(cursor.fetchone()["total"] or 0))
                
                prev_utilidad = prev_ingresos - prev_costos
                
                var_ingresos = calc_var(float(total_ingresos), float(prev_ingresos))
                var_costos = calc_var(float(total_costos), float(prev_costos))
                var_utilidad = calc_var(float(utilidad), float(prev_utilidad))

            # === NUEVO: ANÁLISIS DE LEALTAD Y CLIENTES ===
            # Total Ventas de clientes registrados (Loyalty) vs no registrados
            cursor.execute(f"""
                SELECT 
                    COUNT(DISTINCT p.id) as pedidos_loyalty,
                    SUM(p.total) as ventas_loyalty
                FROM pedidos p
                JOIN loyalty_tx tx ON p.id = tx.pedido_id
                {filtro.replace("fecha", "p.fecha")} AND tx.reason = 'purchase'
            """, params)
            loyalty_data = cursor.fetchone()
            
            ventas_loyalty = Decimal(str(loyalty_data["ventas_loyalty"] or 0))
            pedidos_loyalty = int(loyalty_data["pedidos_loyalty"] or 0)
            
            ventas_casual = total_ingresos - ventas_loyalty
            
            # Promedios de ticket
            cursor.execute(f"SELECT COUNT(id) as total_pedidos FROM pedidos {filtro}", params)
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

            # Top 5 Mejores Clientes (RFM Básico)
            cursor.execute(f"""
                SELECT c.nombre, c.phone_e164 as telefono, COUNT(tx.pedido_id) as visitas, SUM(p.total) as gastado
                FROM loyalty_customers c
                JOIN loyalty_tx tx ON c.id = tx.customer_id
                JOIN pedidos p ON tx.pedido_id = p.id
                {filtro.replace("fecha", "p.fecha")} AND tx.reason = 'purchase'
                GROUP BY c.id
                ORDER BY gastado DESC LIMIT 5
            """, params)
            top_clientes_raw = cursor.fetchall()
            top_clientes = []
            for c in top_clientes_raw:
                c["ticket_promedio"] = float(c["gastado"]) / float(c["visitas"]) if c["visitas"] > 0 else 0
                top_clientes.append(c)

            # === INGENIERÍA DE MENÚ ===
            filtro_bcg = filtro.replace("fecha", "pe.fecha")
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
            """, params)
            bcg_raw = cursor.fetchall()

            for item in bcg_raw:
                item["cantidad_promedio"] = float(item["cantidad"] or 0) / dias_totales
                item["ingreso_promedio"] = float(item["ingreso_total"] or 0) / dias_totales

            menu_engineering_data = [{"nombre": i["nombre"], "x": float(i["cantidad"]), "x_promedio": float(i["cantidad"] or 0)/dias_totales, "y": float(i["margen_unitario"]), "y_promedio": float(i["margen_unitario"])} for i in bcg_raw]

            # === HORAS Y DÍAS ===
            cursor.execute(f"SELECT HOUR(fecha) AS hora_num, COUNT(*) AS total_pedidos, SUM(total) AS total_dinero FROM pedidos {filtro} GROUP BY HOUR(fecha) ORDER BY hora_num", params)
            ventas_hora = [{"hora": f"{v['hora_num']}:00", "total": float(v["total_dinero"] or 0), "promedio": float(v["total_dinero"] or 0) / dias_totales} for v in cursor.fetchall()]

            cursor.execute(f"""
                SELECT dia_num, nombre, ROUND(AVG(total_del_dia), 2) AS promedio, SUM(total_del_dia) AS total
                FROM (
                    SELECT DAYOFWEEK(fecha) AS dia_num,
                           CASE DAYOFWEEK(fecha) WHEN 1 THEN 'Dom' WHEN 2 THEN 'Lun' WHEN 3 THEN 'Mar' WHEN 4 THEN 'Mie' WHEN 5 THEN 'Jue' WHEN 6 THEN 'Vie' WHEN 7 THEN 'Sab' END AS nombre,
                           DATE(fecha) AS f, SUM(total) AS total_del_dia
                    FROM pedidos {filtro} GROUP BY DATE(fecha), dia_num, nombre
                ) t
                GROUP BY dia_num, nombre ORDER BY dia_num
            """, params)
            ventas_semana = [{"nombre": v["nombre"], "promedio": float(v["promedio"] or 0), "total": float(v["total"] or 0)} for v in cursor.fetchall()]

            # === GASTOS Y TABLAS ===
            top_productos = bcg_raw[:10]
            cursor.execute(f"SELECT concepto, tipo_costo, COUNT(*) AS veces, SUM(costo) AS total_gastado FROM insumos_compras {filtro} GROUP BY concepto, tipo_costo ORDER BY total_gastado DESC LIMIT 10", params)
            top_gastos = cursor.fetchall()
            for g in top_gastos: g["promedio_gastado"] = float(g["total_gastado"] or 0) / meses_con_venta

            cursor.execute(f"SELECT DATE(fecha) AS dia, DAYNAME(fecha) AS dia_semana, COUNT(*) AS pedidos, SUM(total) AS total, SUM(neto) AS neto FROM pedidos {filtro} GROUP BY DATE(fecha), DAYNAME(fecha) ORDER BY dia DESC", params)
            ventas_dia = cursor.fetchall()
            for v in ventas_dia:
                peds = int(v["pedidos"] or 1)
                v["pedidos_promedio"] = peds
                v["total_promedio"] = float(v["total"] or 0) / peds
                v["neto_promedio"] = float(v["neto"] or 0) / peds

    finally:
        conn.close()

    return render_template(
        "dashboard.html",
        meses_seleccionados=meses_seleccionados, 
        meses_disponibles=meses_disponibles,
        total_ingresos=float(total_ingresos), promedio_ingresos=float(total_ingresos)/meses_con_venta, var_ingresos=var_ingresos,
        total_costos=float(total_costos), promedio_costos=float(total_costos)/meses_con_venta, var_costos=var_costos,
        utilidad=float(utilidad), promedio_utilidad=float(utilidad)/meses_con_venta, var_utilidad=var_utilidad,
        gross_margin_pct=round(float(gross_margin_pct), 1),
        menu_engineering_data=json.dumps(menu_engineering_data),
        loyalty_stats=loyalty_stats, top_clientes=top_clientes,
        ventas_hora=ventas_hora, ventas_por_dia_semana=ventas_semana, 
        top_productos=top_productos, top_gastos=top_gastos, ventas_dia=ventas_dia
    )

# =========================================================
# ================== RAW DATA Y DEMÁS =====================
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
            cursor.execute(f"SELECT id, fecha, DATE(fecha) as dia, origen, mesero, total, neto, estado, metodo_pago FROM pedidos {filtro} ORDER BY fecha DESC, id DESC", params)
            todos_pedidos = cursor.fetchall()
            pedidos_agrupados = {}
            for p in todos_pedidos:
                dia_str = str(p['dia'])
                if dia_str not in pedidos_agrupados: pedidos_agrupados[dia_str] = []
                pedidos_agrupados[dia_str].append(p)
            cursor.execute("SELECT DISTINCT DATE_FORMAT(fecha, '%Y-%m') AS mes FROM pedidos ORDER BY mes DESC")
            meses_disponibles = [m["mes"] for m in cursor.fetchall()]
    finally:
        conn.close()
    return render_template("raw_data.html", pedidos_agrupados=pedidos_agrupados, meses_disponibles=meses_disponibles, mes=mes)

@app.route("/api/buscar_cliente")
def buscar_cliente():
    query = request.args.get("q", "").strip()
    if len(query) < 3: return jsonify([])
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            search_val = f"%{query}%"
            cursor.execute("SELECT id, nombre, phone_e164 FROM loyalty_customers WHERE nombre LIKE %s OR phone_e164 LIKE %s LIMIT 5", (search_val, search_val))
            resultados = cursor.fetchall()
    finally:
        conn.close()
    return jsonify(resultados)

# =========================================================
# =============== INVENTARIO: DESCONTAR ===================
# =========================================================

def descontar_stock_por_pedido_cursor(cur, pedido_id: int) -> None:
    cur.execute("""
        SELECT pi.cantidad AS cantidad_vendida, p.platillo_id, pi.proteina_id
        FROM pedido_items pi JOIN productos p ON p.id = pi.producto_id WHERE pi.pedido_id = %s
    """, (pedido_id,))
    items = cur.fetchall()
    if not items: return

    consumo = {}
    for it in items:
        platillo_id = it.get("platillo_id")
        proteina_id = it.get("proteina_id")
        qty = Decimal(str(it.get("cantidad_vendida") or 0))

        if not platillo_id or qty <= 0: continue

        cur.execute("""
            SELECT r.insumo_id, r.cantidad_base FROM recetas r JOIN insumos i ON i.id = r.insumo_id
            WHERE r.platillo_id = %s AND i.descuenta_stock = 1
        """, (platillo_id,))
        for r in cur.fetchall():
            insumo_id = int(r["insumo_id"])
            consumo[insumo_id] = consumo.get(insumo_id, Decimal("0")) + (Decimal(str(r["cantidad_base"])) * qty)

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

    if not consumo: return
    rows = [(ins_id, str(-tot), "salida_venta", "pedidos", pedido_id, f"Salida automática por pedido #{pedido_id}") for ins_id, tot in consumo.items()]
    cur.executemany("INSERT IGNORE INTO inventario_movimientos (insumo_id, cantidad_base, tipo, ref_tabla, ref_id, nota) VALUES (%s, %s, %s, %s, %s, %s)", rows)

def descontar_stock_por_pedido(pedido_id: int) -> None:
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            conn.begin()
            descontar_stock_por_pedido_cursor(cur, pedido_id)
            conn.commit()
    except Exception:
        try: conn.rollback()
        except: pass
        raise
    finally:
        conn.close()

# ================== PEDIDOS ABIERTOS ==================
@app.route("/pedidos_abiertos")
def pedidos_abiertos():
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, fecha, origen, mesero, total FROM pedidos WHERE estado = 'abierto' ORDER BY fecha DESC")
            pedidos = cursor.fetchall()
            for p in pedidos:
                cursor.execute("""
                    SELECT pr.nombre, pi.cantidad, pi.proteina, pi.sin, pi.nota FROM pedido_items pi
                    JOIN productos pr ON pr.id = pi.producto_id WHERE pi.pedido_id = %s ORDER BY pi.id DESC LIMIT 4
                """, (p["id"],))
                p["items_preview"] = cursor.fetchall()
    finally:
        conn.close()
    return render_template("pedidos_abiertos.html", pedidos=pedidos)

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
                fecha = request.form.get("fecha") or cursor.execute("SELECT NOW() AS ahora") or cursor.fetchone()["ahora"]
                origen = (request.form.get("origen") or "").strip().lower()
                mesero = request.form.get("mesero", "")
                metodo_pago = request.form.get("metodo_pago", "")
                monto_uber = Decimal(request.form.get("monto_uber", "0") or "0")
                descuento = max(Decimal(request.form.get("descuento", "0") or "0"), Decimal("0"))
                tel_raw = (request.form.get("telefono_whatsapp") or "").strip()
                telefono_e164 = normalize_phone_mx(tel_raw) if tel_raw else None
                totopos_ganados = request.form.get("totopos_ganados")

                productos_ids, cantidades = request.form.getlist("producto_id[]"), request.form.getlist("cantidad[]")
                proteinas_sel, sin_sel, notas_sel = request.form.getlist("proteina[]"), request.form.getlist("sin[]"), request.form.getlist("nota[]")
                proteinas_id_sel, salsas_id_sel = request.form.getlist("proteina_id[]"), request.form.getlist("salsa_id[]")

                def safe_get(lst, i, default=""): return lst[i] if i < len(lst) else default
                def safe_int_or_none(val):
                    v = (val or "").strip()
                    return int(v) if v and v.lower() != "null" and v != "0" else None

                total_bruto = Decimal("0")
                items = []

                for i, prod_id in enumerate(productos_ids):
                    if not str(prod_id).isdigit(): continue
                    cant = int(safe_get(cantidades, i, "0")) if str(safe_get(cantidades, i, "0")).strip().isdigit() else 0
                    if cant <= 0: continue

                    if table_has_column(cursor, "productos", "precio_uber"):
                        cursor.execute("SELECT CASE WHEN %s = 'uber' AND precio_uber IS NOT NULL THEN precio_uber ELSE precio END AS precio_final FROM productos WHERE id = %s", (origen, int(prod_id)))
                    else:
                        cursor.execute("SELECT precio AS precio_final FROM productos WHERE id=%s", (int(prod_id),))

                    row = cursor.fetchone()
                    if not row or row.get("precio_final") is None: continue

                    precio_unit = Decimal(str(row["precio_final"]))
                    subtotal = precio_unit * cant
                    total_bruto += subtotal

                    items.append({
                        "producto_id": int(prod_id), "cantidad": cant, "precio_unitario": precio_unit, "subtotal": subtotal,
                        "proteina": safe_get(proteinas_sel, i, ""), "sin": safe_get(sin_sel, i, ""), "nota": safe_get(notas_sel, i, ""),
                        "proteina_id": safe_int_or_none(safe_get(proteinas_id_sel, i, "")), "salsa_id": safe_int_or_none(safe_get(salsas_id_sel, i, "")),
                    })

                if not items:
                    flash("No hay productos en el carrito.", "error")
                    return redirect(url_for("nuevo_pedido"))

                descuento = min(descuento, total_bruto)
                total_final = total_bruto - descuento
                neto = total_final + monto_uber

                has_desc = table_has_column(cursor, "pedidos", "descuento")
                cols = ["fecha", "origen", "mesero", "telefono_whatsapp", "metodo_pago", "total", "monto_uber", "neto", "estado"]
                vals = [fecha, origen, mesero, telefono_e164, metodo_pago, total_final, monto_uber, neto, "abierto"]

                if has_desc:
                    cols.insert(6, "descuento")
                    vals.insert(6, descuento)

                cursor.execute(f"INSERT INTO pedidos ({','.join(cols)}) VALUES ({','.join(['%s']*len(cols))})", tuple(vals))
                pedido_id = cursor.lastrowid

                has_prot_id = table_has_column(cursor, "pedido_items", "proteina_id")
                has_salsa_id = table_has_column(cursor, "pedido_items", "salsa_id")

                for it in items:
                    cols_it = ["pedido_id", "producto_id", "proteina", "sin", "nota", "cantidad", "precio_unitario", "subtotal"]
                    vals_it = [pedido_id, it["producto_id"], it["proteina"], it["sin"], it["nota"], it["cantidad"], it["precio_unitario"], it["subtotal"]]
                    if has_prot_id: cols_it.append("proteina_id"); vals_it.append(it["proteina_id"])
                    if has_salsa_id: cols_it.append("salsa_id"); vals_it.append(it["salsa_id"])
                    cursor.execute(f"INSERT INTO pedido_items ({','.join(cols_it)}) VALUES ({','.join(['%s']*len(cols_it))})", tuple(vals_it))
                
                if totopos_ganados and str(totopos_ganados).isdigit() and telefono_e164:
                    if int(totopos_ganados) > 0:
                        loyalty_add_totopos_for_purchase(cursor, loyalty_get_or_create_customer(cursor, telefono_e164), pedido_id, int(totopos_ganados))

                conn.commit()
                flash(f"Pedido #{pedido_id} creado y abierto", "success")
                return redirect(url_for("ver_pedido", pedido_id=pedido_id))
    finally:
        conn.close()
    return render_template("nuevo_pedido.html", productos=productos, salsas=salsas, proteinas=proteinas)

@app.route("/clientes", methods=["GET", "POST"])
def lista_clientes():
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            if request.method == "POST":
                nombre = request.form.get("nombre", "").strip()
                telefono = normalize_phone_mx(request.form.get("telefono", "").strip())
                if not telefono: flash("Número inválido.", "error")
                else:
                    cursor.execute("SELECT id FROM loyalty_customers WHERE phone_e164 = %s", (telefono,))
                    if cursor.fetchone(): flash("Cliente ya existe.", "warning")
                    else:
                        cursor.execute("INSERT INTO loyalty_customers (nombre, phone_e164) VALUES (%s, %s)", (nombre, telefono))
                        cursor.execute("INSERT INTO loyalty_accounts (customer_id, totopos_balance, totopos_lifetime) VALUES (%s, 0, 0)", (cursor.lastrowid,))
                        conn.commit()
                        flash(f"Cliente {nombre} registrado.", "success")
                return redirect(url_for("lista_clientes"))

            cursor.execute("""
                SELECT c.id, c.nombre, c.phone_e164, a.totopos_balance, a.totopos_lifetime,
                    (SELECT MAX(p.fecha) FROM pedidos p JOIN loyalty_tx tx ON p.id = tx.pedido_id WHERE tx.customer_id = c.id) as ultima_compra
                FROM loyalty_customers c LEFT JOIN loyalty_accounts a ON c.id = a.customer_id ORDER BY a.totopos_balance DESC
            """)
            clientes = cursor.fetchall()
    finally:
        conn.close()
    return render_template("clientes.html", clientes=clientes)

@app.route("/mi-perfil", methods=["GET", "POST"])
@app.route("/mi-perfil/<phone>", methods=["GET"])
def mi_perfil(phone=None):
    if not phone: phone = request.args.get("phone")
    if request.method == "POST":
        solo_numeros = re.sub(r"\D", "", request.form.get("telefono", ""))
        if len(solo_numeros) < 10:
            flash("Ingresa un número de al menos 10 dígitos.", "error")
            return render_template("mi_perfil.html", cliente=None)
        return redirect(url_for("mi_perfil", phone=solo_numeros[-10:]))

    if phone:
        solo_numeros = re.sub(r"\D", "", phone)
        ultimos_10 = solo_numeros[-10:] if len(solo_numeros) >= 10 else solo_numeros
        telefono_mexico = f"+52{ultimos_10}"
        conn = get_connection()
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("""
                    SELECT c.nombre, c.phone_e164, a.totopos_balance FROM loyalty_customers c
                    LEFT JOIN loyalty_accounts a ON c.id = a.customer_id
                    WHERE c.phone_e164 IN (%s, %s) OR REPLACE(c.phone_e164, ' ', '') LIKE %s
                """, (telefono_mexico, ultimos_10, f"%{ultimos_10}%"))
                cliente = cursor.fetchone()
        finally:
            conn.close()

        if not cliente:
            flash(f"No encontramos la cuenta terminada en {ultimos_10}.", "error")
            return render_template("mi_perfil.html", cliente=None)
            
        balance = int(cliente.get("totopos_balance") or 0)
        return render_template("mi_perfil.html", cliente=cliente, f5=faltan_para(balance, 5), f10=faltan_para(balance, 10))
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
                cursor.execute("UPDATE loyalty_customers SET nombre=%s, phone_e164=%s WHERE id=%s", (nombre, telefono, customer_id))
                if ajuste != 0:
                    cursor.execute("UPDATE loyalty_accounts SET totopos_balance = totopos_balance + %s, totopos_lifetime = totopos_lifetime + %s WHERE customer_id = %s", (ajuste, max(ajuste, 0), customer_id))
                    cursor.execute("INSERT INTO loyalty_tx (customer_id, delta, reason) VALUES (%s, %s, %s)", (customer_id, ajuste, request.form.get("motivo", "Ajuste manual")))
                conn.commit()
                flash("Información actualizada.", "success")
                return redirect(url_for("detalle_cliente", customer_id=customer_id))

            cursor.execute("SELECT c.*, a.totopos_balance, a.totopos_lifetime FROM loyalty_customers c LEFT JOIN loyalty_accounts a ON c.id = a.customer_id WHERE c.id = %s", (customer_id,))
            cliente = cursor.fetchone()
            cursor.execute("SELECT tx.*, p.fecha FROM loyalty_tx tx LEFT JOIN pedidos p ON tx.pedido_id = p.id WHERE tx.customer_id = %s ORDER BY tx.id DESC LIMIT 30", (customer_id,))
            historial = cursor.fetchall()
    finally:
        conn.close()
    if not cliente: return redirect(url_for("lista_clientes"))
    return render_template("cliente_detalle.html", cliente=cliente, historial=historial)

@app.route("/pedido/<int:pedido_id>", methods=["GET", "POST"])
def ver_pedido(pedido_id):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM pedidos WHERE id = %s", (pedido_id,))
            pedido = cursor.fetchone()
            if not pedido: return redirect(url_for("pedidos_abiertos"))

            cursor.execute("SELECT * FROM salsas ORDER BY nombre")
            salsas = cursor.fetchall()
            cursor.execute("SELECT * FROM proteinas ORDER BY nombre")
            proteinas = cursor.fetchall()
            cursor.execute("SELECT * FROM productos WHERE activo = 1 ORDER BY categoria, nombre")
            productos = cursor.fetchall()

            has_prot_id = table_has_column(cursor, "pedido_items", "proteina_id")
            has_salsa_id = table_has_column(cursor, "pedido_items", "salsa_id")
            s_cols = ["pi.id", "pi.cantidad", "pi.precio_unitario", "pi.subtotal", "pi.proteina", "pi.sin", "pi.nota", "p.nombre", "pi.salsa_id" if has_salsa_id else "NULL AS salsa_id", "pi.proteina_id" if has_prot_id else "NULL AS proteina_id"]

            cursor.execute(f"SELECT {', '.join(s_cols)} FROM pedido_items pi JOIN productos p ON p.id = pi.producto_id WHERE pi.pedido_id = %s ORDER BY pi.id DESC", (pedido_id,))
            items = cursor.fetchall()

            if request.method == "POST":
                if pedido.get("estado") != "abierto":
                    flash("No se puede modificar un pedido cerrado", "error")
                    return redirect(url_for("ver_pedido", pedido_id=pedido_id))

                productos_ids, cantidades = request.form.getlist("producto_id[]"), request.form.getlist("cantidad[]")
                proteinas_sel, sin_sel, notas_sel = request.form.getlist("proteina[]"), request.form.getlist("sin[]"), request.form.getlist("nota[]")
                proteinas_id_sel, salsas_id_sel = request.form.getlist("proteina_id[]"), request.form.getlist("salsa_id[]")

                def safe_get(lst, i, default=""): return lst[i] if i < len(lst) else default
                total_agregado = Decimal("0")

                for i, prod_id in enumerate(productos_ids):
                    if not str(prod_id).isdigit(): continue
                    cant = int(cantidades[i]) if i < len(cantidades) and str(cantidades[i]).strip().isdigit() else 0
                    if cant <= 0: continue

                    cursor.execute("SELECT precio FROM productos WHERE id = %s", (int(prod_id),))
                    row = cursor.fetchone()
                    if not row: continue

                    precio = Decimal(str(row["precio"]))
                    p_txt, s_txt, n_txt = safe_get(proteinas_sel, i, ""), safe_get(sin_sel, i, ""), safe_get(notas_sel, i, "")
                    pid, sid = safe_get(proteinas_id_sel, i, "") or None, safe_get(salsas_id_sel, i, "") or None
                    
                    query = "SELECT id, cantidad FROM pedido_items WHERE pedido_id=%s AND producto_id=%s AND (proteina<=>%s) AND (sin<=>%s) AND (nota<=>%s)"
                    params = [pedido_id, int(prod_id), p_txt, s_txt, n_txt]
                    if has_prot_id: query += " AND (proteina_id<=>%s)"; params.append(pid)
                    if has_salsa_id: query += " AND (salsa_id<=>%s)"; params.append(sid)

                    cursor.execute(query, tuple(params))
                    existente = cursor.fetchone()

                    if existente:
                        n_cant = int(existente["cantidad"]) + cant
                        cursor.execute("UPDATE pedido_items SET cantidad=%s, subtotal=%s WHERE id=%s", (n_cant, Decimal(str(n_cant))*precio, existente["id"]))
                    else:
                        cols = ["pedido_id", "producto_id", "proteina", "sin", "nota", "cantidad", "precio_unitario", "subtotal"]
                        vals = [pedido_id, int(prod_id), p_txt, s_txt, n_txt, cant, precio, precio*cant]
                        if has_prot_id: cols.append("proteina_id"); vals.append(pid)
                        if has_salsa_id: cols.append("salsa_id"); vals.append(sid)
                        cursor.execute(f"INSERT INTO pedido_items ({','.join(cols)}) VALUES ({','.join(['%s']*len(cols))})", tuple(vals))
                    
                    total_agregado += precio * cant

                cursor.execute("UPDATE pedidos SET total=total+%s, neto=neto+%s WHERE id=%s", (total_agregado, total_agregado, pedido_id))
                conn.commit()
                return redirect(url_for("ver_pedido", pedido_id=pedido_id))
    finally:
        conn.close()
    return render_template("pedido.html", pedido=pedido, items=items, productos=productos, salsas=salsas, proteinas=proteinas)

@app.route("/pedido/<int:pedido_id>/actualizar_whatsapp", methods=["POST"])
def actualizar_whatsapp_pedido(pedido_id):
    telefono_limpio = normalize_phone_mx(request.form.get("telefono_whatsapp", "").strip())
    if not telefono_limpio:
        flash("Número inválido.", "error")
        return redirect(url_for("ver_pedido", pedido_id=pedido_id))
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT estado FROM pedidos WHERE id = %s", (pedido_id,))
            pedido = cursor.fetchone()
            if not pedido or pedido["estado"] != "abierto": flash("Pedido cerrado.", "error")
            else:
                cursor.execute("UPDATE pedidos SET telefono_whatsapp = %s WHERE id = %s", (telefono_limpio, pedido_id))
                conn.commit()
                flash("WhatsApp guardado.", "success")
    finally:
        conn.close()
    return redirect(url_for("ver_pedido", pedido_id=pedido_id))

@app.route("/cerrar_pedido/<int:pedido_id>", methods=["POST"])
def cerrar_pedido(pedido_id):
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT estado FROM pedidos WHERE id=%s", (pedido_id,))
            if cursor.fetchone().get("estado") == "abierto":
                cursor.execute("UPDATE pedidos SET estado='cerrado' WHERE id=%s", (pedido_id,))
                descontar_stock_por_pedido_cursor(cursor, pedido_id)
                conn.commit()
                flash("Pedido cerrado", "success")
    finally:
        conn.close()
    return redirect(url_for("pedidos_abiertos"))

@app.route("/cerrar_pedido_whatsapp/<int:pedido_id>", methods=["POST"])
def cerrar_pedido_whatsapp(pedido_id):
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT id, total, telefono_whatsapp, estado FROM pedidos WHERE id=%s", (pedido_id,))
            pedido = cursor.fetchone()
            if pedido and pedido["estado"] == "abierto":
                cursor.execute("UPDATE pedidos SET estado='cerrado' WHERE id=%s", (pedido_id,))
                phone = pedido.get("telefono_whatsapp")
                balance, earned = 0, 1
                if phone:
                    customer_id = loyalty_get_or_create_customer(cursor, phone)
                    balance = loyalty_add_totopos_for_purchase(cursor, customer_id, pedido_id, earned)
                
                descontar_stock_por_pedido_cursor(cursor, pedido_id)
                
                if phone:
                    msg = generar_ticket_texto(pedido_id, cursor) + "\n\n" + loyalty_message(balance, earned, pedido_id, Decimal(str(pedido["total"])), phone)
                    conn.commit()
                    return redirect(wa_me_link(phone, msg))
                conn.commit()
    finally:
        conn.close()
    return redirect(url_for("pedidos_abiertos"))

@app.route("/productos", methods=["GET", "POST"])
def productos():
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT id, nombre FROM platillos ORDER BY nombre")
            platillos = cursor.fetchall()
            if request.method == "POST":
                nombre, categoria, precio_txt, plat_id_txt = request.form.get("nombre","").strip(), request.form.get("categoria","").strip(), request.form.get("precio","").strip(), request.form.get("platillo_id","").strip()
                try: precio = Decimal(precio_txt)
                except: flash("Precio inválido.", "error"); return redirect(url_for("productos"))
                platillo_id = int(plat_id_txt) if plat_id_txt.isdigit() else None
                costo = calcular_costo_platillo(cursor, platillo_id) if platillo_id else Decimal(request.form.get("costo", "0").strip())
                cursor.execute("INSERT INTO productos (nombre, categoria, costo, precio, platillo_id, activo) VALUES (%s,%s,%s,%s,%s,1)", (nombre, categoria, str(costo), str(precio), platillo_id))
                conn.commit()
                flash("Producto agregado", "success")
                return redirect(url_for("productos"))
            
            cursor.execute("SELECT pr.id, pr.nombre, pr.categoria, pr.costo, pr.precio, pr.platillo_id, pl.nombre AS platillo_nombre FROM productos pr LEFT JOIN platillos pl ON pl.id = pr.platillo_id WHERE pr.activo = 1 ORDER BY pr.categoria, pr.nombre")
            productos_rows = cursor.fetchall()
    finally:
        conn.close()
    return render_template("productos.html", productos=productos_rows, platillos=platillos)

@app.post("/productos/<int:producto_id>/actualizar_platillo")
def actualizar_platillo_producto(producto_id):
    pid = request.form.get("platillo_id")
    platillo_id = int(pid) if pid and pid.isdigit() else None
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            costo = calcular_costo_platillo(cursor, platillo_id) if platillo_id else Decimal("0")
            cursor.execute("UPDATE productos SET platillo_id=%s, costo=%s WHERE id=%s", (platillo_id, str(costo), producto_id))
            conn.commit()
    finally:
        conn.close()
    return redirect(url_for("productos"))

@app.post("/productos/<int:producto_id>/set_platillo")
def productos_set_platillo(producto_id):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE productos SET platillo_id=%s WHERE id=%s", (request.form.get("platillo_id") or None, producto_id))
            conn.commit()
    finally:
        conn.close()
    return redirect(url_for("productos"))

@app.route("/compras", methods=["GET", "POST"])
def compras():
    conn = get_connection()
    conn.ping(reconnect=True)
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT id, nombre, unidad_base FROM insumos WHERE activo = 1 ORDER BY nombre")
            insumos = cursor.fetchall()

            if request.method == "POST":
                sumar_stock = (request.form.get("es_insumo") == "1")
                cant_txt, uni_txt = request.form.get("cantidad","").strip(), request.form.get("unidad","").strip()
                if sumar_stock:
                    cant_txt = cant_txt or request.form.get("cantidad_base","").strip()
                    uni_txt = uni_txt or request.form.get("unidad_base","").strip()

                costo_dec, cant_dec = parse_decimal_mx(request.form.get("costo")), parse_decimal_mx(cant_txt)
                if not costo_dec or costo_dec < 0 or not cant_dec or cant_dec <= 0:
                    flash("Datos inválidos", "error")
                else:
                    iid = request.form.get("insumo_id")
                    cursor.execute("""
                        INSERT INTO insumos_compras (fecha, lugar, cantidad, unidad, concepto, costo, tipo_costo, nota, insumo_id, cantidad_base, unidad_base, costo_unitario, es_insumo)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """, (request.form["fecha"], request.form["lugar"], str(cant_dec), uni_txt, request.form["concepto"], str(costo_dec), request.form["tipo_costo"], request.form.get("nota",""), int(iid) if iid and iid.isdigit() else None, str(cant_dec) if sumar_stock else None, request.form.get("unidad_base") if sumar_stock else None, str(parse_decimal_mx(request.form.get("costo_unitario"))) if sumar_stock else None, 1 if sumar_stock else 0))
                    
                    if sumar_stock and iid and cant_dec:
                        cursor.execute("INSERT IGNORE INTO inventario_movimientos (insumo_id, cantidad_base, tipo, ref_tabla, ref_id) VALUES (%s, %s, 'entrada_compra', 'insumos_compras', %s)", (int(iid), str(cant_dec), cursor.lastrowid))
                    conn.commit()
                    flash("Compra registrada", "success")
                return redirect(url_for("compras"))

            cursor.execute("SELECT id, fecha, lugar, concepto, costo, tipo_costo, es_insumo FROM insumos_compras ORDER BY fecha DESC LIMIT 200")
            compras_rows = cursor.fetchall()
    finally:
        conn.close()
    return render_template("compras.html", compras=compras_rows, insumos=insumos, form_data={})

@app.route("/pedido/<int:pedido_id>/eliminar_item/<int:item_id>", methods=["POST"])
def eliminar_item_pedido(pedido_id, item_id):
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT pe.estado, pi.subtotal FROM pedidos pe JOIN pedido_items pi ON pi.pedido_id = pe.id WHERE pe.id = %s AND pi.id = %s", (pedido_id, item_id))
            row = cursor.fetchone()
            if row and row["estado"] == "abierto":
                cursor.execute("DELETE FROM pedido_items WHERE id=%s AND pedido_id=%s", (item_id, pedido_id))
                cursor.execute("UPDATE pedidos SET total=total-%s, neto=neto-%s WHERE id=%s", (row["subtotal"], row["subtotal"], pedido_id))
                conn.commit()
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
            if pedido:
                if pedido["estado"] == "cerrado":
                    cursor.execute("DELETE FROM inventario_movimientos WHERE tipo='salida_venta' AND ref_tabla='pedidos' AND ref_id=%s", (pedido_id,))
                if table_has_column(cursor, "loyalty_tx", "pedido_id"):
                    cursor.execute("DELETE FROM loyalty_tx WHERE pedido_id=%s", (pedido_id,))
                cursor.execute("DELETE FROM pedido_items WHERE pedido_id=%s", (pedido_id,))
                cursor.execute("DELETE FROM pedidos WHERE id=%s", (pedido_id,))
                conn.commit()
                flash("Pedido eliminado", "success")
    finally:
        conn.close()
    return redirect(url_for("borrar_pedidos"))

def generar_ticket_texto(pedido_id, cursor) -> str:
    cursor.execute("SELECT p.nombre, pi.cantidad, pi.precio_unitario, pi.proteina, pi.sin, pi.nota FROM pedido_items pi JOIN productos p ON p.id = pi.producto_id WHERE pi.pedido_id = %s ORDER BY pi.id ASC", (pedido_id,))
    items = cursor.fetchall()
    cursor.execute("SELECT total FROM pedidos WHERE id = %s", (pedido_id,))
    pedido = cursor.fetchone()

    lines = ["SEÑOR CHILAQUIL", "------------------------"]
    for it in items:
        lines.append(f'{it["cantidad"]} {it["nombre"]} - ${float(Decimal(str(it["cantidad"])) * Decimal(str(it["precio_unitario"]))):.2f}')
        if it.get("proteina"): lines.append(f'  PROT: {it["proteina"]}')
        if it.get("sin"): lines.append(f'  SIN: {it["sin"]}')
        if it.get("nota"): lines.append(f'  NOTA: {it["nota"]}')
    lines.append("------------------------")
    lines.append(f'TOTAL: ${float(pedido["total"] or 0):.2f}')
    lines.append("\n¡Gracias por tu compra!")
    return "\n".join(lines)

@app.route("/pedido/<int:pedido_id>/whatsapp")
def enviar_ticket_whatsapp(pedido_id):
    t = normalize_phone_mx(request.args.get("tel", "").strip())
    if not t: return redirect(url_for("ver_pedido", pedido_id=pedido_id))
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            return redirect(wa_me_link(t, generar_ticket_texto(pedido_id, cursor)))
    finally:
        conn.close()

@app.route("/pedido/<int:pedido_id>/ticket_preview")
def ticket_preview(pedido_id):
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            texto = generar_ticket_texto(pedido_id, cursor)
            return jsonify({"texto": texto, "whatsapp_url": f"https://wa.me/?text={urllib.parse.quote_from_bytes(texto.encode('utf-8'))}"})
    finally:
        conn.close()

@app.route("/borrar_pedidos", methods=["GET"])
def borrar_pedidos():
    estado, origen, mesero, pid, desde, hasta = request.args.get("estado","").lower(), request.args.get("origen","").lower(), request.args.get("mesero",""), request.args.get("pedido_id",""), request.args.get("desde",""), request.args.get("hasta","")
    w, p = [], []
    if estado in ("abierto", "cerrado"): w.append("estado = %s"); p.append(estado)
    if origen: w.append("LOWER(origen) LIKE %s"); p.append(f"%{origen}%")
    if mesero: w.append("mesero LIKE %s"); p.append(f"%{mesero}%")
    if pid.isdigit(): w.append("id = %s"); p.append(int(pid))
    if desde: w.append("DATE(fecha) >= %s"); p.append(desde)
    if hasta: w.append("DATE(fecha) <= %s"); p.append(hasta)
    
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(f"SELECT id, fecha, origen, mesero, total, estado FROM pedidos {'WHERE '+' AND '.join(w) if w else ''} ORDER BY fecha DESC LIMIT 300", p)
            pedidos = cursor.fetchall()
    finally:
        conn.close()
    return render_template("borrar_pedidos.html", pedidos=pedidos, estado=estado, origen=origen, mesero=mesero, pedido_id=pid, desde=desde, hasta=hasta)

@app.route("/borrar_pedidos_bulk", methods=["POST"])
def borrar_pedidos_bulk():
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            if request.form.get("modo") == "borrar_todos_abiertos":
                cursor.execute("DELETE pi FROM pedido_items pi JOIN pedidos pe ON pe.id = pi.pedido_id WHERE pe.estado = 'abierto'")
                cursor.execute("DELETE FROM pedidos WHERE estado='abierto'")
                conn.commit()
                flash("Borrados TODOS los pedidos abiertos.", "success")
                return redirect(url_for("borrar_pedidos", estado="abierto"))

            ids = [int(x) for x in request.form.getlist("pedido_ids[]") if x.strip().isdigit()]
            if ids:
                ph = ",".join(["%s"] * len(ids))
                cursor.execute(f"SELECT id FROM pedidos WHERE id IN ({ph}) AND estado = 'cerrado'", ids)
                cerrados = [r["id"] for r in cursor.fetchall()]
                if cerrados:
                    cursor.execute(f"DELETE FROM inventario_movimientos WHERE tipo='salida_venta' AND ref_tabla='pedidos' AND ref_id IN ({','.join(['%s']*len(cerrados))})", cerrados)
                cursor.execute(f"DELETE FROM pedido_items WHERE pedido_id IN ({ph})", ids)
                cursor.execute(f"DELETE FROM pedidos WHERE id IN ({ph})", ids)
                conn.commit()
                flash(f"Borrados {len(ids)} pedido(s).", "success")
    finally:
        conn.close()
    return redirect(url_for("borrar_pedidos"))

@app.route("/inventario/stock")
def ver_stock():
    q = request.args.get("q","").strip()
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("SELECT insumo_id, nombre, unidad_base, stock_actual FROM vw_stock_actual WHERE (%s = '' OR nombre LIKE %s) ORDER BY nombre", (q, f"%{q}%"))
            rows = cur.fetchall()
        return render_template("stock.html", rows=rows, q=q)
    finally:
        conn.close()

@app.post("/inventario/stock/agregar")
def agregar_stock():
    iid, ctxt, q = request.form.get("insumo_id",""), request.form.get("cantidad",""), request.form.get("q","")
    if not iid.isdigit() or parse_decimal_mx(ctxt) is None or parse_decimal_mx(ctxt) <= 0:
        flash("Datos inválidos.", "error")
        return redirect(url_for("ver_stock", q=q))
    
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("SELECT activo, unidad_base FROM insumos WHERE id=%s", (int(iid),))
            ins = cur.fetchone()
            if ins and int(ins["activo"]) == 1:
                cur.execute("INSERT INTO inventario_movimientos (insumo_id, cantidad_base, tipo, ref_tabla, ref_id, nota) VALUES (%s, %s, 'entrada_manual', 'stock_ui', NULL, 'Entrada manual desde /inventario/stock')", (int(iid), str(parse_decimal_mx(ctxt))))
                conn.commit()
                flash(f"Stock agregado ✅ (+{ctxt} {ins['unidad_base']})", "success")
            else:
                flash("El insumo no está activo.", "error")
    finally:
        conn.close()
    return redirect(url_for("ver_stock", q=q))

@app.post("/productos/<int:producto_id>/eliminar")
def eliminar_producto_producto(producto_id):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE productos SET activo = 0 WHERE id = %s", (producto_id,))
            conn.commit()
    finally:
        conn.close()
    return redirect(url_for("productos"))

def calcular_costo_platillo(cursor, platillo_id: int) -> Decimal:
    cursor.execute("""
        SELECT COALESCE(SUM((r.cantidad_base * (1 + (i.merma_pct / 100))) * 
               (CASE WHEN r.usa_precio_manual = 1 AND r.precio_manual IS NOT NULL THEN r.precio_manual 
                     ELSE COALESCE((SELECT ic.costo_unitario FROM insumos_compras ic WHERE ic.insumo_id = r.insumo_id AND ic.costo_unitario IS NOT NULL ORDER BY ic.fecha DESC, ic.id DESC LIMIT 1), 0) END)), 0) AS costo_platillo
        FROM recetas r JOIN insumos i ON i.id = r.insumo_id WHERE r.platillo_id = %s
    """, (platillo_id,))
    return Decimal(str(cursor.fetchone()["costo_platillo"] or 0))

@app.get("/api/platillos/<int:platillo_id>/costo")
def api_platillo_costo(platillo_id):
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            return jsonify({"platillo_id": platillo_id, "costo": float(calcular_costo_platillo(cursor, platillo_id))})
    finally:
        conn.close()

@app.post("/platillos/<int:platillo_id>/proteina_qty")
def platillo_set_proteina_qty(platillo_id):
    pid, cant = request.form.get("proteina_id",""), request.form.get("cantidad_base","")
    if not pid.isdigit() or parse_decimal_mx(cant) is None or parse_decimal_mx(cant) <= 0:
        flash("Datos inválidos.", "error")
        return redirect(request.referrer or url_for("productos"))

    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("SELECT insumo_id, nombre FROM proteinas WHERE id=%s", (int(pid),))
            pr = cur.fetchone()
            if pr and pr.get("insumo_id"):
                cur.execute("SELECT descuenta_stock, unidad_base FROM insumos WHERE id=%s", (int(pr["insumo_id"]),))
                ins = cur.fetchone()
                if ins and int(ins.get("descuenta_stock") or 0) == 1:
                    cur.execute("INSERT INTO recetas_proteina (platillo_id, proteina_id, insumo_id, cantidad_base) VALUES (%s, %s, %s, %s) ON DUPLICATE KEY UPDATE insumo_id=VALUES(insumo_id), cantidad_base=VALUES(cantidad_base)", (platillo_id, int(pid), int(pr["insumo_id"]), str(parse_decimal_mx(cant))))
                    conn.commit()
                    flash(f"Proteína guardada.", "success")
                else: flash("Insumo no descuenta stock.", "error")
            else: flash("Proteína sin insumo ligado.", "error")
    finally:
        conn.close()
    return redirect(request.referrer or url_for("productos"))

if __name__ == "__main__":
    app.run(debug=True)
