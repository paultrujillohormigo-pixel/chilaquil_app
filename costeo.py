from flask import Blueprint, render_template, request, redirect, url_for, flash
from decimal import Decimal, InvalidOperation
from db import get_connection

# IMPORTAMOS EL CANDADO DESDE auth.py
from auth import requiere_permiso

costeo_bp = Blueprint("costeo", __name__, url_prefix="/admin")

# =========================================================
# ================== HELPERS DE BASE DE DATOS =============
# =========================================================

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


# =========================================================
# ================== CATÁLOGO DE INSUMOS ==================
# =========================================================

@costeo_bp.get("/insumos")
@requiere_permiso("menu_admin")
def insumos_index():
    insumos = query_all("SELECT id, nombre, unidad_base, merma_pct, activo FROM insumos ORDER BY nombre")
    return render_template("admin/insumos_index.html", insumos=insumos)

@costeo_bp.post("/insumos")
@requiere_permiso("menu_admin")
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


# =========================================================
# ================== GESTIÓN DE RECETAS (BOM) =============
# =========================================================

@costeo_bp.route("/recetas", methods=["GET", "POST"])
@requiere_permiso("menu_admin")
def recetas_index():
    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        if not nombre:
            flash("El nombre de la receta es obligatorio.", "error")
        else:
            try:
                execute("INSERT INTO platillos (nombre) VALUES (%s)", (nombre,))
                flash("Receta base creada. Ahora selecciona 'Ficha Técnica' para agregarle ingredientes.", "success")
            except Exception as e:
                flash(f"Error al crear: {e}", "error")
        return redirect(url_for("costeo.recetas_index"))

    platillos = query_all("SELECT id, nombre FROM platillos ORDER BY nombre")
    return render_template("admin/recetas_index.html", platillos=platillos)

@costeo_bp.get("/recetas/<int:platillo_id>")
@requiere_permiso("menu_admin")
def recetas_edit(platillo_id):
    # Traemos todos los campos visuales para inyectarlos en el HTML
    platillo = query_one("SELECT id, nombre, proteina_cantidad_base, imagen_url, tiempo_prep_min, equipo_necesario, instrucciones FROM platillos WHERE id=%s", (platillo_id,))
    if not platillo:
        flash("Platillo no encontrado.", "error")
        return redirect(url_for("costeo.recetas_index"))

    insumos = query_all("SELECT i.id, i.nombre, i.unidad_base, i.merma_pct, cv.costo_unitario AS ultimo_costo FROM insumos i LEFT JOIN v_insumo_costo_vigente cv ON cv.insumo_id = i.id WHERE i.activo=1 ORDER BY i.nombre")

    # Receta: simplificada para usar solo compras reales
    receta = query_all("SELECT r.id AS receta_id, r.insumo_id, i.nombre AS insumo_nombre, i.unidad_base, i.merma_pct, r.cantidad_base, cv.costo_unitario AS costo_unitario_usado, ROUND(r.cantidad_base * cv.costo_unitario * (1 + (i.merma_pct/100)), 2) AS subtotal FROM recetas r JOIN insumos i ON i.id = r.insumo_id LEFT JOIN v_insumo_costo_vigente cv ON cv.insumo_id = r.insumo_id WHERE r.platillo_id=%s ORDER BY i.nombre", (platillo_id,))

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

    return render_template(
        "admin/recetas_edit.html",
        platillo=platillo,
        insumos=insumos,
        receta=receta,
        costeo=costeo,
        costeo_compras=costeo_compras
    )

@costeo_bp.post("/recetas/<int:platillo_id>")
@requiere_permiso("menu_admin")
def recetas_save(platillo_id):
    # Ya no guardamos proteina_cantidad_base porque quitamos esa tarjeta
    imagen_url = (request.form.get("imagen_url") or "").strip() or None
    tiempo_prep_txt = (request.form.get("tiempo_prep_min") or "").strip()
    equipo_necesario = (request.form.get("equipo_necesario") or "").strip() or None
    instrucciones = (request.form.get("instrucciones") or "").strip() or None
    
    tiempo_prep_val = None
    if tiempo_prep_txt:
        try:
            tiempo_prep_val = int(tiempo_prep_txt)
        except ValueError:
            pass

    # 3. Recuperar los insumos (BOM)
    insumo_ids = request.form.getlist("insumo_id[]")
    cantidades = request.form.getlist("cantidad_base[]")

    rows = []
    keep_insumo_ids = []
    costo_materia_prima = Decimal("0.0")

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            for insumo_id, cant in zip(insumo_ids, cantidades):
                if not insumo_id:
                    continue

                try:
                    insumo_id_int = int(insumo_id)
                    c = Decimal(cant)
                except Exception:
                    continue

                if c <= 0:
                    continue
                
                # CÁLCULO DE COSTO EN TIEMPO REAL CON LA ÚLTIMA COMPRA
                cursor.execute("""
                    SELECT i.merma_pct, cv.costo_unitario 
                    FROM insumos i 
                    LEFT JOIN v_insumo_costo_vigente cv ON i.id = cv.insumo_id 
                    WHERE i.id = %s
                """, (insumo_id_int,))
                insumo_data = cursor.fetchone()
                
                if insumo_data and insumo_data["costo_unitario"]:
                    costo_unitario = Decimal(str(insumo_data["costo_unitario"]))
                    merma = Decimal(str(insumo_data["merma_pct"] or 0))
                    
                    cantidad_con_merma = c * (1 + (merma / 100))
                    costo_materia_prima += (cantidad_con_merma * costo_unitario)

                rows.append((platillo_id, insumo_id_int, c, 0, None))
                keep_insumo_ids.append(insumo_id_int)

            if not rows and not imagen_url and not instrucciones:
                flash("No hay ingredientes válidos ni datos que guardar.", "warning")
                return redirect(url_for("costeo.recetas_edit", platillo_id=platillo_id))

            if rows:
                cursor.executemany("INSERT INTO recetas (platillo_id, insumo_id, cantidad_base, usa_precio_manual, precio_manual) VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE cantidad_base = VALUES(cantidad_base), usa_precio_manual = VALUES(usa_precio_manual), precio_manual = VALUES(precio_manual)", rows)

            if keep_insumo_ids:
                placeholders = ",".join(["%s"] * len(keep_insumo_ids))
                cursor.execute(
                    f"DELETE FROM recetas WHERE platillo_id=%s AND insumo_id NOT IN ({placeholders})",
                    (platillo_id, *keep_insumo_ids)
                )
            else:
                cursor.execute("DELETE FROM recetas WHERE platillo_id=%s", (platillo_id,))

            # Actualizamos la ficha visual
            cursor.execute(
                """
                UPDATE platillos 
                SET imagen_url=%s,
                    tiempo_prep_min=%s,
                    equipo_necesario=%s,
                    instrucciones=%s
                WHERE id=%s
                """,
                (imagen_url, tiempo_prep_val, equipo_necesario, instrucciones, platillo_id)
            )
            
            # GUARDADO FINAL EN TABLA PRODUCTOS PARA LA MATRIZ BCG
            costo_operativo = costo_materia_prima * Decimal("0.75") 
            costo_total_real = costo_materia_prima + costo_operativo
            
            cursor.execute("""
                UPDATE productos 
                SET costo_total_real = %s 
                WHERE platillo_id = %s
            """, (costo_total_real, platillo_id))

            conn.commit()
            
    except Exception as e:
        conn.rollback()
        flash(f"Error al guardar la receta: {e}", "error")
        return redirect(url_for("costeo.recetas_edit", platillo_id=platillo_id))
    finally:
        conn.close()

    flash("Receta y ficha técnica guardadas correctamente.", "success")
    return redirect(url_for("costeo.recetas_edit", platillo_id=platillo_id))


# =========================================================
# ================== BORRADO TOTAL DE RECETA ==============
# =========================================================

@costeo_bp.route('/receta/<int:platillo_id>/delete', methods=['POST'])
@requiere_permiso("menu_admin")
def receta_delete(platillo_id):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            # 1. Borramos dependencias de la receta
            cursor.execute("DELETE FROM recetas WHERE platillo_id = %s", (platillo_id,))
            cursor.execute("DELETE FROM recetas_proteina WHERE platillo_id = %s", (platillo_id,))
            
            # 2. Desvinculamos del POS (Productos) para no romper el historial de ventas
            cursor.execute("UPDATE productos SET platillo_id = NULL WHERE platillo_id = %s", (platillo_id,))

            # 3. Borramos el contenedor de la receta (platillo)
            cursor.execute("DELETE FROM platillos WHERE id = %s", (platillo_id,))
            
        conn.commit()
        flash("Receta eliminada con éxito.", "success")
        
    except Exception as e:
        try: conn.rollback()
        except: pass
        flash(f"Error al eliminar la receta: {e}", "error")
    finally:
        conn.close()

    return redirect(url_for('costeo.recetas_index'))


# =========================================================
# ================== REPORTE DE COSTEO ====================
# =========================================================

@costeo_bp.get("/costeo")
@requiere_permiso("menu_admin")
def costeo_index():
    try:
        data = query_all("SELECT * FROM v_costeo_platillos_compras ORDER BY platillo")
    except Exception:
        data = []
        flash("No se pudo cargar el costeo dinámico: verifica tus compras.", "error")

    return render_template("admin/costeo_index.html", data=data)
