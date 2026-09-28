import urllib.parse
import re
import pymysql
import json
import os

from flask import Flask, request, redirect, url_for, flash, render_template, jsonify, session, send_from_directory
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta
from werkzeug.security import check_password_hash

from db import get_connection

# --- Importamos los Blueprints ---
from costeo import costeo_bp
from dashboard import dashboard_bp
from gastos import gastos_bp
from rh import rh_bp
from cocina import cocina_bp
from seguridad import seguridad_bp

# --- Importamos nuestros Candados desde auth.py ---
from auth import login_requerido, requiere_permiso

app = Flask(__name__)
app.secret_key = "super_secret_key"

# --- Registramos todos los Blueprints ---
app.register_blueprint(costeo_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(gastos_bp)
app.register_blueprint(rh_bp)
app.register_blueprint(cocina_bp)
app.register_blueprint(seguridad_bp)


@app.post("/productos/<int:producto_id>/editar_precio")
@requiere_permiso("menu_admin")
def editar_precio_producto(producto_id):
    nuevo_precio = request.form.get("nuevo_precio")
    if not nuevo_precio:
        flash("El precio no puede estar vacío.", "error")
        return redirect(url_for("productos"))

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE productos SET precio = %s WHERE id = %s", (nuevo_precio, producto_id))
            conn.commit()
            flash("Precio actualizado correctamente en el Punto de Venta 💰", "success")
    except Exception as e:
        flash(f"Error al actualizar el precio: {e}", "error")
    finally:
        conn.close()
        
    return redirect(url_for("productos"))


# =========================================================
# ================== LOGIN Y LOGOUT =======================
# =========================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = get_connection()
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("SELECT * FROM usuarios WHERE username = %s AND activo = 1", (username,))
                usuario = cursor.fetchone()

                if usuario and check_password_hash(usuario['password_hash'], password):
                    session['usuario_id'] = usuario['id']
                    session['nombre'] = usuario['nombre']
                    session['rol_id'] = usuario['rol_id']
                    
                    session['permisos'] = []
                    if usuario['rol_id'] == 1:
                        session['permisos'] = ['all']
                    else:
                        cursor.execute("""
                            SELECT m.nombre FROM rol_modulo rm
                            JOIN modulos m ON rm.modulo_id = m.id
                            WHERE rm.rol_id = %s
                        """, (usuario['rol_id'],))
                        modulos_db = cursor.fetchall()
                        session['permisos'] = [mod['nombre'] for mod in modulos_db]

                    flash(f"Bienvenido {usuario['nombre']}", "success")
                    return redirect(url_for('hub'))
                else:
                    flash("Usuario o contraseña incorrectos.", "error")
        finally:
            conn.close()

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("Sesión cerrada correctamente.", "success")
    return redirect(url_for('login'))

@app.route('/privacy', methods=['GET'])
def privacy_policy():
    return """
    <html>
        <head><title>Politica de Privacidad - Senor Chilaquil</title></head>
        <body>
            <h1>Politica de Privacidad</h1>
            <p>Esta aplicacion respeta la privacidad de sus usuarios y solo procesa datos con fines de comunicacion via WhatsApp.</p>
        </body>
    </html>
    """, 200

@app.route('/menu')
def menu():
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            # 1. Traemos los productos de venta regular unidos con sus imágenes de receta
            cursor.execute("""
                SELECT p.*, pl.imagen_url, pl.instrucciones 
                FROM productos p 
                LEFT JOIN platillos pl ON p.platillo_id = pl.id 
                WHERE p.activo = 1 
                ORDER BY p.categoria, p.nombre
            """)
            productos_db = cursor.fetchall()
            
            # 2. Traemos TODOS los platillos para extraer las Salsas y Proteínas con sus fotos
            cursor.execute("SELECT id, nombre, imagen_url, instrucciones FROM platillos ORDER BY nombre")
            platillos_db = cursor.fetchall()

            # 3. Traemos el catálogo de proteínas para saber cuáles platillos son proteínas
            cursor.execute("SELECT nombre FROM proteinas")
            nombres_proteinas = [row['nombre'].lower().strip() for row in cursor.fetchall()]
            
    finally:
        conn.close()
        
    return render_template('menu.html', 
                           productos=productos_db, 
                           platillos=platillos_db, 
                           nombres_proteinas=nombres_proteinas)

@app.route('/carta')
def mostrar_carta():
    return send_from_directory(app.static_folder, 'carta.pdf')

@app.route('/ver-pdf')
def ver_pdf():
    return send_from_directory(app.static_folder, 'menu_Mayo.pdf')

# =========================================================
# ================== RUTAS GENERALES PROTEGIDAS ===========
# =========================================================

@app.route("/")
@login_requerido
def index():
    return render_template("index.html")

@app.route("/hub")
@login_requerido
def hub():
    return render_template("hub.html")


# =========================================================
# ================== HELPERS Y UTILIDADES =================
# =========================================================

def get_previous_month(yyyy_mm: str) -> str | None:
    if not yyyy_mm or "-" not in yyyy_mm:
        return None
    try:
        year_str, month_str = yyyy_mm.split("-")
        year = int(year_str)
        month = int(month_str)
        month -= 1
        if month == 0:
            month = 12
            year -= 1
        return f"{year}-{month:02d}"
    except ValueError:
        return None

def calc_var(current: float, previous: float) -> float:
    if previous == 0:
        if current == 0:
            return 0.0
        return 100.0
    return ((current - previous) / previous) * 100.0

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

@app.template_filter("money")
def money_format(value):
    try:
        return "${:,.2f}".format(float(value))
    except Exception:
        return value


# =========================================================
# ================== CRM & LEALTAD (CLIENTES) =============
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
        lines.append(f"Con esta compra sumas {earned} totopo(s) a tu cuenta! Promocion activa.")
    else:
        lines.append(f"Tienes {balance} totopos acumulados en tu cuenta.")

    f5 = faltan_para(balance, 5)
    f10 = faltan_para(balance, 10)

    if f5 == 0 or f10 == 0:
        lines.append("")
        if f10 == 0:
            lines.append("Ya puedes canjear un plato fuerte gratis!")
        elif f5 == 0:
            lines.append("Ya puedes canjear una bebida gratis!")

    lines.append("\nConsulta tus puntos y recompensas aqui:")
    lines.append(f"Link de Perfil: {url_perfil}\n")
    lines.append("¡Gracias por tu preferencia!")

    return "\n".join(lines)


@app.route("/api/buscar_cliente")
@requiere_permiso("pedidos") 
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


@app.route("/clientes", methods=["GET", "POST"])
@requiere_permiso("clientes")
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
@requiere_permiso("clientes")
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

@app.route("/promociones", methods=["GET", "POST"])
@requiere_permiso("clientes")
def promociones():
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            # Magia: Creamos la tabla de promociones automáticamente si no existe
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS promociones (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    nombre VARCHAR(100) NOT NULL,
                    mensaje TEXT NOT NULL,
                    activo INT DEFAULT 1
                )
            """)
            conn.commit()
            
            if request.method == "POST":
                nombre = request.form.get("nombre", "").strip()
                mensaje = request.form.get("mensaje", "").strip()
                if nombre and mensaje:
                    cursor.execute("INSERT INTO promociones (nombre, mensaje) VALUES (%s, %s)", (nombre, mensaje))
                    conn.commit()
                    flash("Promoción creada con éxito.", "success")
                else:
                    flash("El nombre y el mensaje son obligatorios.", "error")
                return redirect(url_for("promociones"))
                
            cursor.execute("SELECT * FROM promociones WHERE activo = 1 ORDER BY id DESC")
            promos = cursor.fetchall()
    finally:
        conn.close()
        
    return render_template("promociones.html", promociones=promos)

@app.route("/promociones/<int:promo_id>/eliminar", methods=["POST"])
@requiere_permiso("clientes")
def eliminar_promocion(promo_id):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE promociones SET activo = 0 WHERE id = %s", (promo_id,))
            conn.commit()
            flash("Promoción eliminada.", "success")
    finally:
        conn.close()
    return redirect(url_for("promociones"))

@app.route("/campanas")
@requiere_permiso("clientes")
def campanas():
    dias_str = request.args.get("dias", "30")
    dias = int(dias_str) if dias_str.isdigit() else 30

    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            if not table_has_column(cursor, "loyalty_customers", "ultimo_mensaje_campana"):
                cursor.execute("ALTER TABLE loyalty_customers ADD COLUMN ultimo_mensaje_campana DATE NULL")
                conn.commit()
                
            # Por si entran primero a campañas antes que a promociones
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS promociones (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    nombre VARCHAR(100) NOT NULL,
                    mensaje TEXT NOT NULL,
                    activo INT DEFAULT 1
                )
            """)

            cursor.execute("""
                SELECT 
                    c.id, c.nombre, c.phone_e164, c.ultimo_mensaje_campana,
                    a.totopos_balance,
                    MAX(p.fecha) as ultima_compra,
                    DATEDIFF(CURRENT_DATE, MAX(p.fecha)) as dias_ausente,
                    DATEDIFF(CURRENT_DATE, c.ultimo_mensaje_campana) as dias_desde_mensaje
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
            
            # Traer las promos activas
            cursor.execute("SELECT * FROM promociones WHERE activo = 1 ORDER BY nombre")
            promociones_db = cursor.fetchall()
    finally:
        conn.close()

    hoy = datetime.now().date()
    for c in clientes_inactivos:
        fecha_ultimo = c.get("ultimo_mensaje_campana")
        c["contactado_hoy"] = (fecha_ultimo == hoy)
        
        # Limpiamos los datos para JS
        c["telefono_limpio"] = (c["phone_e164"] or "").replace("+", "")
        c["primer_nombre"] = (c["nombre"] or "amigo").split()[0]

    return render_template("campanas.html", clientes=clientes_inactivos, dias=dias, promociones=promociones_db)

# =========================================================
# === RUTA PARA EL CLIC SILENCIOSO DE CAMPAÑAS ===
# =========================================================
@app.route("/api/campanas/marcar/<int:customer_id>", methods=["POST"])
@requiere_permiso("clientes")
def marcar_campana(customer_id):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE loyalty_customers SET ultimo_mensaje_campana = CURRENT_DATE WHERE id = %s", (customer_id,))
            conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})
    finally:
        conn.close()


# =========================================================
# ================== INVENTARIO Y STOCK ===================
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
            f"Salida automatica por pedido #{pedido_id}"
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
        try: conn.rollback()
        except Exception: pass
        raise
    finally:
        conn.close()


@app.route("/inventario/stock")
@requiere_permiso("inventario")
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
@requiere_permiso("inventario")
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
        try: conn.rollback()
        except Exception: pass
        raise
    finally:
        conn.close()


# =========================================================
# ================== OPERACIÓN DE PEDIDOS =================
# =========================================================

@app.route("/pedidos_abiertos")
@requiere_permiso("pedidos")
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


@app.route("/nuevo_pedido", methods=["GET", "POST"])
@requiere_permiso("pedidos")
def nuevo_pedido():
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:

            cursor.execute("SELECT * FROM productos WHERE activo = 1 ORDER BY categoria, nombre")
            productos = cursor.fetchall()
            cursor.execute("SELECT * FROM salsas ORDER BY nombre")
            salsas = cursor.fetchall()
            cursor.execute("SELECT * FROM proteinas ORDER BY nombre")
            top_productos = cursor.fetchall()

            if request.method == "POST":
                fecha = request.form.get("fecha")
                if not fecha:
                    cursor.execute("SELECT NOW() AS ahora")
                    fecha = cursor.fetchone()["ahora"]

                origen = (request.form.get("origen") or "").strip().lower()
                
                # --- CAMBIO IMPORTANTE AQUÍ ---
                # Tomamos el nombre del usuario desde la sesión, ignorando lo que venga en el form
                mesero = session.get('nombre', 'Usuario Desconocido')
                
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

                for it in items:
                    es_extra = (it["padre_index"] is not None)

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

                if telefono_e164:
                    customer_id = loyalty_get_or_create_customer(cursor, telefono_e164)
                    loyalty_add_totopos_for_purchase(cursor, customer_id, pedido_id, 1)

                enviar_wa = request.form.get("enviar_wa") == "1"

                if enviar_wa and telefono_e164:
                    conn.commit()
                    ticket_text = generar_ticket_texto(pedido_id, cursor)
                    
                    cursor.execute("SELECT totopos_balance FROM loyalty_accounts WHERE customer_id=%s", (customer_id,))
                    row_totopos = cursor.fetchone()
                    balance = row_totopos["totopos_balance"] if row_totopos else 0

                    msg_loyalty = loyalty_message(balance, 1, pedido_id, total_final, telefono_e164)
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

    return render_template("nuevo_pedido.html", productos=productos, salsas=salsas, proteinas=top_productos)


@app.route("/pedido/<int:pedido_id>", methods=["GET", "POST"])
@requiere_permiso("pedidos")
def ver_pedido(pedido_id):
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT * FROM pedidos WHERE id = %s", (pedido_id,))
            pedido = cursor.fetchone()

            if not pedido:
                flash("Pedido no disponible", "error")
                return redirect(url_for("pedidos_abiertos"))

            cursor.execute("SELECT * FROM salsas ORDER BY nombre")
            salsas = cursor.fetchall()
            cursor.execute("SELECT * FROM proteinas ORDER BY nombre")
            proteinas = cursor.fetchall()
            cursor.execute("SELECT * FROM productos WHERE activo = 1 ORDER BY categoria, nombre")
            productos = cursor.fetchall()

            has_prot_id = table_has_column(cursor, "pedido_items", "proteina_id")
            has_salsa_id = table_has_column(cursor, "pedido_items", "salsa_id")
            has_padre_id = table_has_column(cursor, "pedido_items", "item_padre_id")

            if request.method == "POST":
                enviar_wa = request.form.get("enviar_wa") == "1"
                tel_raw = (request.form.get("telefono_whatsapp") or "").strip()
                telefono_e164 = normalize_phone_mx(tel_raw) if tel_raw else pedido.get("telefono_whatsapp")

                if enviar_wa and request.form.get("solo_enviar_wa") == "1":
                    if not telefono_e164:
                        return jsonify({"status": "error", "message": "Ingresa un número válido para enviar el WhatsApp."})

                    if telefono_e164 != pedido.get("telefono_whatsapp"):
                        cursor.execute("UPDATE pedidos SET telefono_whatsapp = %s WHERE id = %s", (telefono_e164, pedido_id))
                        conn.commit()

                    ticket_text = generar_ticket_texto(pedido_id, cursor)

                    balance = 0
                    cursor.execute("SELECT id FROM loyalty_customers WHERE phone_e164 = %s", (telefono_e164,))
                    c_row = cursor.fetchone()
                    if c_row:
                        cursor.execute("SELECT totopos_balance FROM loyalty_accounts WHERE customer_id = %s", (c_row["id"],))
                        acc = cursor.fetchone()
                        if acc: balance = acc["totopos_balance"]

                    msg_loyalty = loyalty_message(balance, 0, pedido_id, Decimal(str(pedido["total"])), telefono_e164)
                    full_message = ticket_text + "\n\n" + msg_loyalty
                    wa_link = wa_me_link(telefono_e164, full_message)

                    return jsonify({
                        "status": "success",
                        "wa_link": wa_link,
                        "redirect_url": url_for("ver_pedido", pedido_id=pedido_id)
                    })

                conn.begin()

                if pedido.get("estado") == "cerrado":
                    cursor.execute("""
                        DELETE FROM inventario_movimientos
                        WHERE tipo = 'salida_venta'
                          AND ref_tabla = 'pedidos'
                          AND ref_id = %s
                    """, (pedido_id,))

                fecha = request.form.get("fecha") or pedido.get("fecha")
                origen = (request.form.get("origen") or "").strip().lower()
                
                # --- CAMBIO IMPORTANTE AQUÍ TAMBIÉN ---
                # Tomamos el nombre del usuario desde la sesión, ignorando lo que venga en el form
                mesero = request.form.get("mesero") or pedido.get("mesero")
                
                metodo_pago = request.form.get("metodo_pago", "")
                monto_uber = Decimal(request.form.get("monto_uber", "0") or "0")
                mesa = request.form.get("mesa", "Envío/Recoger")

                try:
                    descuento = Decimal(request.form.get("descuento", "0") or "0")
                except Exception:
                    descuento = Decimal("0")
                if descuento < 0: descuento = Decimal("0")

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
                    conn.rollback()
                    flash("El carrito no puede quedarse vacío al actualizar.", "error")
                    return redirect(url_for("ver_pedido", pedido_id=pedido_id))

                if descuento > total_bruto: descuento = total_bruto
                total_final = total_bruto - descuento
                neto = total_final + monto_uber

                cursor.execute("DELETE FROM pedido_items WHERE pedido_id = %s", (pedido_id,))

                index_to_db_id = {}
                extras_to_insert = []

                for it in items_a_insertar:
                    es_extra = (it["padre_index"] is not None)

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

                if pedido.get("estado") == "cerrado":
                    descontar_stock_por_pedido_cursor(cursor, pedido_id)

                if telefono_e164:
                    customer_id = loyalty_get_or_create_customer(cursor, telefono_e164)
                    loyalty_add_totopos_for_purchase(cursor, customer_id, pedido_id, 1)

                conn.commit()

                if enviar_wa and telefono_e164:
                    ticket_text = generar_ticket_texto(pedido_id, cursor)
                    
                    cursor.execute("SELECT totopos_balance FROM loyalty_accounts WHERE customer_id=%s", (customer_id,))
                    row_totopos = cursor.fetchone()
                    balance = row_totopos["totopos_balance"] if row_totopos else 0

                    msg_loyalty = loyalty_message(balance, 1, pedido_id, total_final, telefono_e164)
                    full_message = ticket_text + "\n\n" + msg_loyalty
                    wa_link = wa_me_link(telefono_e164, full_message)

                    return jsonify({
                        "status": "success",
                        "wa_link": wa_link,
                        "redirect_url": url_for("ver_pedido", pedido_id=pedido_id)
                    })
                else:
                    flash(f"Pedido #{pedido_id} actualizado con éxito.", "success")
                    return redirect(url_for("ver_pedido", pedido_id=pedido_id))

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

            items = []
            id_to_index_map = {}
            
            for idx, row in enumerate(items_raw):
                id_to_index_map[row["id"]] = idx

            for row in items_raw:
                p_id = row.get("item_padre_id")
                row["padre_index"] = id_to_index_map.get(p_id) if p_id else None
                
                if row.get("precio_unitario") is not None:
                    row["precio_unitario"] = float(row["precio_unitario"])
                if row.get("subtotal") is not None:
                    row["subtotal"] = float(row["subtotal"])
                
                if row.get("nota"):
                    row["nota"] = str(row["nota"]).replace("\n", " ").replace("\r", "").replace('"', '\\"').replace("'", "\\'")

                items.append(row)

            pedido["cliente_nombre"] = None
            if pedido.get("telefono_whatsapp"):
                cursor.execute("SELECT nombre FROM loyalty_customers WHERE phone_e164 = %s LIMIT 1", (pedido["telefono_whatsapp"],))
                c_row = cursor.fetchone()
                if c_row: pedido["cliente_nombre"] = c_row["nombre"]

            pedido["total"] = float(pedido["total"] or 0)
            if "descuento" in pedido and pedido["descuento"] is not None:
                pedido["descuento"] = float(pedido["descuento"])
            if "monto_uber" in pedido and pedido["monto_uber"] is not None:
                pedido["monto_uber"] = float(pedido["monto_uber"])
            if "neto" in pedido and pedido["neto"] is not None:
                pedido["neto"] = float(pedido["neto"])

    except Exception:
        try: conn.rollback()
        except Exception: pass
        raise
    finally:
        conn.close()

    return render_template(
        "pedido.html",
        pedido=pedido,
        pedido_detalles=items,
        productos=productos,
        salsas=salsas,
        proteinas=proteinas
    )


@app.route("/cerrar_pedido/<int:pedido_id>", methods=["POST"])
@requiere_permiso("pedidos")
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
@requiere_permiso("pedidos")
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

            balance = 0
            if phone:
                customer_id = loyalty_get_or_create_customer(cursor, phone)
                balance = loyalty_add_totopos_for_purchase(cursor, customer_id, pedido_id, 1)

            descontar_stock_por_pedido_cursor(cursor, pedido_id)

            if phone:
                ticket_text = generar_ticket_texto(pedido_id, cursor)
                msg_loyalty = loyalty_message(balance, 1, pedido_id, Decimal(str(pedido["total"])), phone)
                full_message = ticket_text + "\n\n" + msg_loyalty
                conn.commit()
                return redirect(wa_me_link(phone, full_message))

            conn.commit()
            flash("Pedido cerrado. No se envió WhatsApp porque no hay teléfono.", "success")
            return redirect(url_for("pedidos_abiertos"))
    finally:
        conn.close()


@app.route("/pedido/<int:pedido_id>/actualizar_whatsapp", methods=["POST"])
@requiere_permiso("pedidos")
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


@app.route("/api/item/<int:item_id>/toggle_cocina", methods=["POST"])
@requiere_permiso("cocina")
def toggle_item_cocina(item_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE pedido_items SET entregado = NOT entregado WHERE id = %s", (item_id,))
            conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        conn.close()


@app.route("/pedido/<int:pedido_id>/eliminar_item/<int:item_id>", methods=["POST"])
@requiere_permiso("pedidos")
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
@requiere_permiso("finanzas")
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
        try: conn.rollback()
        except Exception: pass
        flash(f"Error eliminando pedido #{pedido_id}: {e}", "error")
    finally:
        conn.close()

    return redirect(url_for("borrar_pedidos"))


@app.route("/borrar_pedidos", methods=["GET"])
@requiere_permiso("finanzas")
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
@requiere_permiso("finanzas")
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
# ================== RAW DATA (TABLAS SIN PROCESAR) =======
# =========================================================

@app.route("/raw-data")
@requiere_permiso("finanzas")
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
# ================== RUN APP ==============================
# =========================================================
if __name__ == "__main__":
    app.run(debug=True)
