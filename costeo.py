from flask import Blueprint, render_template, request, redirect, url_for, flash
from decimal import Decimal

# IMPORTA aquí tus helpers DB reales
# from db import query_one, query_all, execute, execute_many

costeo_bp = Blueprint("costeo", __name__, url_prefix="/admin")

@costeo_bp.get("/recetas")
def recetas_index():
    platillos = query_all(
        "SELECT id, nombre FROM platillos ORDER BY nombre"
    )
    return render_template("admin/recetas_index.html", platillos=platillos)


@costeo_bp.get("/recetas/<int:platillo_id>")
def recetas_edit(platillo_id):
    platillo = query_one(
        "SELECT id, nombre FROM platillos WHERE id=%s", (platillo_id,)
    )

    insumos = query_all(
        "SELECT id, nombre, unidad_base FROM insumos WHERE activo=1 ORDER BY nombre"
    )

    receta = query_all("""
        SELECT r.id, r.insumo_id, i.nombre AS insumo_nombre,
               i.unidad_base, r.cantidad_base
        FROM recetas r
        JOIN insumos i ON i.id = r.insumo_id
        WHERE r.platillo_id=%s
        ORDER BY i.nombre
    """, (platillo_id,))

    costeo = query_one(
        "SELECT * FROM v_costeo_platillos WHERE platillo_id=%s",
        (platillo_id,)
    )

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
        try:
            c = Decimal(cant)
            if c > 0:
                rows.append((platillo_id, int(insumo_id), c))
        except:
            pass

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


@costeo_bp.get("/costeo")
def costeo_index():
    data = query_all(
        "SELECT * FROM v_costeo_platillos ORDER BY platillo"
    )
    return render_template("admin/costeo_index.html", data=data)
