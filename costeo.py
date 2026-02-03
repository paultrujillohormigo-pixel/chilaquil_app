from flask import Blueprint, render_template, request, redirect, url_for, flash
from decimal import Decimal, InvalidOperation
from db import get_connection

costeo_bp = Blueprint("costeo", __name__, url_prefix="/admin")


# =========================
# Helpers DB
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


def safe_int(val):
    v = (val or "").strip()
    return int(v) if v.isdigit() else None


# =========================
# Platillos
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
    except Exception:
        flash("Precio inválido.", "error")
        return redirect(url_for("costeo.platillos_index"))

    try:
        execute("INSERT INTO platillos (nombre, precio_actual) VALUES (%s,%s)", (nombre, precio_val))
        flash("Platillo guardado.", "success")
    except Exception as e:
        flash(f"No se pudo guardar el platillo: {e}", "error")

    return redirect(url_for("costeo.platillos_index"))


@costeo_bp.post("/platillos/<int:platillo_id>/precio")
def platillo_precio_update(platillo_id):
    precio_txt = (request.form.get("precio_pos") or "").strip()

    try:
        precio_pos = Decimal(precio_txt)
    except (InvalidOperation, TypeError):
        flash("Precio inválido.", "error")
        return redirect(url_for("costeo.platillos_index"))

    if precio_pos < 0:
        flash("El precio no puede ser negativo.", "error")
        return redirect(url_for("costeo.platillos_index"))

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            conn.begin()

            # 1) Actualiza precio_actual en platillos (costeo)
            cursor.execute("""
                UPDATE platillos
                SET precio_actual = %s
                WHERE id = %s
            """, (precio_pos, platillo_id))

            # 2) Actualiza precio en productos (POS) por platillo_id
            cursor.execute("""
                UPDATE productos
                SET precio = %s
                WHERE platillo_id = %s
            """, (precio_pos, platillo_id))

            conn.commit()
            flash("Precio actualizado ✅", "success")

    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        flash(f"Error actualizando precio: {e}", "error")
    finally:
        conn.close()

    return redirect(url_for("costeo.platillos_index"))


# =========================
# Insumos
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
    except Exception:
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

    # Catálogo de insumos (con costo compras como referencia)
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

    # Receta base: costo usado = manual si aplica, si no compras.
    receta = query_all("""
        SELECT
          r.id AS receta_id,
          r.insumo_id,
          i.nombre AS insumo_nombre,
          i.unidad_base,
          i.merma_pct,
          r.cantidad_base,

          r.usa_precio_manual,
          r.precio_manual,

          cv.costo_unitario AS costo_unitario_compra,

          CASE
            WHEN r.usa_precio_manual=1 AND r.precio_manual IS NOT NULL THEN r.precio_manual
            ELSE cv.costo_unitario
          END AS costo_unitario_usado,

          ROUND(
            r.cantidad_base
            * (CASE
                WHEN r.usa_precio_manual=1 AND r.precio_manual IS NOT NULL THEN r.precio_manual
                ELSE cv.costo_unitario
              END)
            * (1 + (i.merma_pct/100)),
            2
          ) AS subtotal
        FROM recetas r
        JOIN insumos i ON i.id = r.insumo_id
        LEFT JOIN v_insumo_costo_vigente cv ON cv.insumo_id = r.insumo_id
        WHERE r.platillo_id=%s
        ORDER BY i.nombre
    """, (platillo_id,))

    # Resúmenes:
    costeo = None
    costeo_compras = None
    try:
        costeo = query_one("SELECT * FROM v_costeo_platillos WHERE platillo_id=%s", (platillo_id,))
    except Exception:
        costeo = None

    try:
        costeo_compras = query_one("SELECT * FROM v_costeo_platillos_compras WHERE platillo_id=%s", (platillo_id,))
    except Exception:
        costeo_compras = None

    # ===== NUEVO: Proteínas para inventario (recetas_proteina) =====
    # En tu BD SÍ existe proteinas.insumo_id (lo confirmaste), pero lo validamos por seguridad.
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            proteinas_tienen_insumo = table_has_column(cursor, "proteinas", "insumo_id")
    finally:
        conn.close()

    if proteinas_tienen_insumo:
        proteinas = query_all("""
            SELECT p.id, p.nombre, p.insumo_id, i.unidad_base
            FROM proteinas p
            LEFT JOIN insumos i ON i.id = p.insumo_id
            ORDER BY p.nombre
        """)
    else:
        proteinas = query_all("SELECT id, nombre FROM proteinas ORDER BY nombre")

    receta_proteinas = query_all("""
        SELECT
            rp.proteina_id,
            p.nombre AS proteina_nombre,
            rp.insumo_id,
            i.unidad_base,
            rp.cantidad_base
        FROM recetas_proteina rp
        JOIN proteinas p ON p.id = rp.proteina_id
        JOIN insumos i ON i.id = rp.insumo_id
        WHERE rp.platillo_id = %s
        ORDER BY p.nombre
    """, (platillo_id,))

    return render_template(
        "admin/recetas_edit.html",
        platillo=platillo,
        insumos=insumos,
        receta=receta,
        costeo=costeo,
        costeo_compras=costeo_compras,
        proteinas=proteinas,
        receta_proteinas=receta_proteinas,
        proteinas_tienen_insumo=proteinas_tienen_insumo
    )


@costeo_bp.post("/recetas/<int:platillo_id>")
def recetas_save(platillo_id):
    insumo_ids = request.form.getlist("insumo_id[]")
    cantidades = request.form.getlist("cantidad_base[]")
    usar_manual_vals = request.form.getlist("usa_precio_manual[]")  # '0'/'1'
    precios_manual = request.form.getlist("precio_manual[]")

    rows = []
    keep_insumo_ids = []

    for insumo_id, cant, um, pm in zip(insumo_ids, cantidades, usar_manual_vals, precios_manual):
        if not insumo_id:
            continue

        try:
            insumo_id_int = int(insumo_id)
            c = Decimal(cant)
        except Exception:
            continue

        if c <= 0:
            continue

        usa_manual = 1 if str(um) == "1" else 0

        precio_manual_val = None
        if pm not in (None, "", " "):
            try:
                precio_manual_val = Decimal(pm)
            except Exception:
                precio_manual_val = None

        # si marca manual pero no puso precio, desactívalo
        if usa_manual == 1 and precio_manual_val is None:
            usa_manual = 0

        rows.append((platillo_id, insumo_id_int, c, usa_manual, precio_manual_val))
        keep_insumo_ids.append(insumo_id_int)

    if not rows:
        flash("No hay ingredientes válidos.", "warning")
        return redirect(url_for("costeo.recetas_edit", platillo_id=platillo_id))

    # UPSERT por uq_receta_platillo_insumo
    execute_many("""
        INSERT INTO recetas (platillo_id, insumo_id, cantidad_base, usa_precio_manual, precio_manual)
        VALUES (%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          cantidad_base = VALUES(cantidad_base),
          usa_precio_manual = VALUES(usa_precio_manual),
          precio_manual = VALUES(precio_manual)
    """, rows)

    # Borra lo que ya no viene
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            placeholders = ",".join(["%s"] * len(keep_insumo_ids))
            cursor.execute(
                f"DELETE FROM recetas WHERE platillo_id=%s AND insumo_id NOT IN ({placeholders})",
                (platillo_id, *keep_insumo_ids)
            )
        conn.commit()
    finally:
        conn.close()

    flash("Receta guardada correctamente.", "success")
    return redirect(url_for("costeo.recetas_edit", platillo_id=platillo_id))


# =========================
# NUEVO: Proteínas por platillo (inventario)
# =========================
@costeo_bp.post("/recetas/<int:platillo_id>/proteinas")
def receta_proteina_guardar(platillo_id):
    proteina_id = safe_int(request.form.get("proteina_id"))
    cantidad_txt = (request.form.get("cantidad_base") or "").strip()

    if not proteina_id:
        flash("Selecciona una proteína.", "error")
        return redirect(url_for("costeo.recetas_edit", platillo_id=platillo_id))

    try:
        cantidad = Decimal(cantidad_txt)
    except Exception:
        flash("Cantidad inválida.", "error")
        return redirect(url_for("costeo.recetas_edit", platillo_id=platillo_id))

    if cantidad <= 0:
        flash("La cantidad debe ser mayor a 0.", "error")
        return redirect(url_for("costeo.recetas_edit", platillo_id=platillo_id))

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            has_insumo = table_has_column(cursor, "proteinas", "insumo_id")

            insumo_id = None
            if has_insumo:
                cursor.execute("SELECT insumo_id FROM proteinas WHERE id=%s", (proteina_id,))
                row = cursor.fetchone()
                insumo_id = (row or {}).get("insumo_id")

            if not insumo_id:
                flash("Esa proteína no tiene insumo_id ligado. Ve a tu tabla proteinas.", "error")
                return redirect(url_for("costeo.recetas_edit", platillo_id=platillo_id))

            # Validar que el insumo exista y esté activo
            cursor.execute("SELECT id FROM insumos WHERE id=%s AND activo=1", (insumo_id,))
            if not cursor.fetchone():
                flash("El insumo ligado no existe o no está activo.", "error")
                return redirect(url_for("costeo.recetas_edit", platillo_id=platillo_id))

            # UPSERT por uq_rp (platillo_id, proteina_id)
            cursor.execute("""
                INSERT INTO recetas_proteina (platillo_id, proteina_id, insumo_id, cantidad_base)
                VALUES (%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    insumo_id = VALUES(insumo_id),
                    cantidad_base = VALUES(cantidad_base)
            """, (platillo_id, proteina_id, insumo_id, str(cantidad)))

        conn.commit()
        flash("Proteína guardada para inventario ✅", "success")
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        flash(f"Error guardando proteína: {e}", "error")
    finally:
        conn.close()

    return redirect(url_for("costeo.recetas_edit", platillo_id=platillo_id))


@costeo_bp.post("/recetas/<int:platillo_id>/proteinas/<int:proteina_id>/borrar")
def receta_proteina_borrar(platillo_id, proteina_id):
    try:
        execute("DELETE FROM recetas_proteina WHERE platillo_id=%s AND proteina_id=%s", (platillo_id, proteina_id))
        flash("Proteína eliminada ✅", "success")
    except Exception as e:
        flash(f"No se pudo borrar: {e}", "error")

    return redirect(url_for("costeo.recetas_edit", platillo_id=platillo_id))


# =========================
# Costeo (listado)
# =========================
@costeo_bp.get("/costeo")
def costeo_index():
    try:
        data = query_all("SELECT * FROM v_costeo_platillos ORDER BY platillo")
    except Exception:
        data = []
        flash("No se pudo cargar el costeo: falta crear v_costeo_platillos o falta costo vigente.", "error")

    return render_template("admin/costeo_index.html", data=data)
