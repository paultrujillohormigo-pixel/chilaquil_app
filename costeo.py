from flask import Blueprint, render_template, request, redirect, url_for, flash
from decimal import Decimal
from db import get_connection

costeo_bp = Blueprint("costeo", __name__, url_prefix="/admin")


# =========================
# Helpers DB (usa tu get_connection)
# =========================
def query_all(sql, params=None):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params or ())
            return cursor.fetchall()
    finally:
        conn.close()


def query_one(sql, params=None):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params or ())
            return cursor.fetchone()
    finally:
        conn.close()


def execute(sql, params=None):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params or ())
        conn.commit()
    finally:
        conn.close()


def execute_many(sql, rows):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.executemany(sql, rows)
        conn.commit()
    finally:
        conn.close()


# =========================
# Platillos (alta + lista)
# =========================
@costeo_bp.get("/platillos")
def platillos_index():
    platillos = query_all("SELECT id, nombre, precio_actual FROM platillos ORDER BY nombre")
    return render_template("admin/platillos_index.html", platillos=platillos)


@costeo_bp.post("/platillos")
def platillos_create():
    nombre = (request.form.get("nombre") or "").strip()
    precio = request.form.get("precio_actual")

    if not nombre:
        flash("El nombre del platillo es obligatorio.", "error")
        return redirect(url_for("costeo.platillos_index"))

    precio_val = None
    try:
        if precio not in (None, "", " "):
            precio_val = Decimal(precio)
    except:
        flash("Precio inválido.", "error")
        return redirect(url_for("costeo.platillos_index"))

    try:
        execute("INSERT INTO platillos (nombre, precio_actual) VALUES (%s,%s)", (nombre, precio_val))
        flash("Platillo guardado.", "success")
    except Exception as e:
        flash(f"No se pudo guardar el platillo: {e}", "error")

    return redirect(url_for("costeo.platillos_index"))


# =========================
# Insumos (alta + lista)
# =========================
@costeo_bp.get("/insumos")
def insumos_index():
    insumos = query_all("""
        SELECT id, nombre, unidad_base, merma_pct, activo
        FROM insumos
        ORDER BY nombre
    """)
    return render_template("admin/insumos_index.html", insumos=insumos)


@costeo_bp.post("/insumos")
def insumos_create():
    nombre = (request.form.get("nombre") or "").strip()
    unidad_base = (request.form.get("unidad_base") or "").strip()
    merma = request.form.get("merma_pct")

    if not nombre:
        flash("El nombre del insumo es obligatorio.", "error")
        return redirect(url_for("costeo.insumos_index"))

    if unidad_base not in ("g", "ml", "pza"):
        flash("Unidad base inválida (g, ml, pza).", "error")
        return redirect(url_for("costeo.insumos_index"))

    merma_val = Decimal("0")
    try:
        if merma not in (None, "", " "):
            merma_val = Decimal(merma)
    except:
        flash("Merma inválida. Usa un número (ej. 5 o 12.5).", "error")
        return redirect(url_for("costeo.insumos_index"))

    try:
        execute(
            "INSERT INTO insumos (nombre, unidad_base, merma_pct, activo) VALUES (%s,%s,%s,1)",
            (nombre, unidad_base, merma_val)
        )
        flash("Insumo guardado.", "success")
    except Exception as e:
        flash(f"No se pudo guardar el insumo: {e}", "error")

    return redirect(url_for("costeo.insumos_index"))


# =========================
# Recetas
# =========================
@costeo_bp.get("/recetas")
def recetas_index():
    platillos = query_all("SELECT id, nombre FROM platillos ORDER BY nombre")
    return render_template("admin/recetas_index.html", platillos=platillos)


@costeo_bp.get("/recetas/<int:platillo_id>")
def recetas_edit(platillo_id):
    platillo = query_one("SELECT id, nombre FROM platillos WHERE id=%s", (platillo_id,))
    if not platillo:
        flash("Platillo no encontrado.", "error")
        return redirect(url_for("costeo.recetas_index"))

    # Trae catálogo de insumos con costo vigente
    insumos = query_all("""
        SELECT
          i.id,
          i.nombre,
          i.unidad_base,
          i.merma_pct,
          cv.costo_unitario
        FROM insumos i
        LEFT JOIN v_insumo_costo_vigente cv ON cv.insumo_id = i.id
        WHERE i.activo=1
        ORDER BY i.nombre
    """)

    # Trae receta con costo unitario + subtotal por ingrediente (incluye merma)
    receta = query_all("""
        SELECT
          r.id,
          r.insumo_id,
          i.nombre AS insumo_nombre,
          i.unidad_base,
          i.merma_pct,
          r.cantidad_base,
          cv.costo_unitario,
          ROUND(
            r.cantidad_base * cv.costo_unitario * (1 + (i.merma_pct/100)),
            2
          ) AS subtotal
        FROM recetas r
        JOIN insumos i ON i.id = r.insumo_id
        LEFT JOIN v_insumo_costo_vigente cv ON cv.insumo_id = r.insumo_id
        WHERE r.platillo_id=%s
        ORDER BY i.nombre
    """, (platillo_id,))

    # Costeo total (si existe la vista)
    costeo = None
    try:
        costeo = query_one("SELECT * FROM v_costeo_platillos WHERE platillo_id=%s", (platillo_id,))
    except Exception:
        costeo = None

    return render_template(
        "admin/recetas_edit.html",
        platillo=platillo,
        insumos=insumos,
        receta=receta,
        costeo=costeo
    )


@costeo_bp.post("/recetas/<int:platillo_id>")
def recetas_save(platillo_id):
    insumo_ids = request.form.getlist("insumo_id[]")
    cantidades = request.form.getlist("cantidad_base[]")

    rows = []
    for insumo_id, cant in zip(insumo_ids, cantidades):
        if not insumo_id:
            continue
        try:
            c = Decimal(cant)
        except:
            continue
        if c <= 0:
            continue
        rows.append((platillo_id, int(insumo_id), c))

    if not rows:
        flash("No hay ingredientes válidos.", "warning")
        return redirect(url_for("costeo.recetas_edit", platillo_id=platillo_id))

    execute("DELETE FROM recetas WHERE platillo_id=%s", (platillo_id,))
    execute_many(
        "INSERT INTO recetas (platillo_id, insumo_id, cantidad_base) VALUES (%s,%s,%s)",
        rows
    )

    flash("Receta guardada correctamente.", "success")
    return redirect(url_for("costeo.recetas_edit", platillo_id=platillo_id))


# =========================
# Costeo
# =========================
@costeo_bp.get("/costeo")
def costeo_index():
    try:
        data = query_all("SELECT * FROM v_costeo_platillos ORDER BY platillo")
    except Exception:
        data = []
        flash("No se pudo cargar el costeo: falta crear v_costeo_platillos o falta costo vigente.", "error")

    return render_template("admin/costeo_index.html", data=data)
