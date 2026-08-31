from flask import Blueprint, render_template, request
from decimal import Decimal
from datetime import datetime
import json
from db import get_connection

dashboard_bp = Blueprint("dashboard_bp", __name__)

# =========================================================
# ================== HELPERS LOCALES ======================
# =========================================================

def get_previous_month(yyyy_mm: str) -> str | None:
    if not yyyy_mm or "-" not in yyyy_mm: return None
    try:
        year, month = map(int, yyyy_mm.split("-"))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
        return f"{year}-{month:02d}"
    except ValueError:
        return None

def calc_var(current: float, previous: float) -> float:
    if previous == 0: return 0.0 if current == 0 else 100.0
    return ((current - previous) / previous) * 100.0


# =========================================================
# ================== RUTAS DEL DASHBOARD ==================
# =========================================================

@dashboard_bp.route("/dashboard")
def dashboard():
    meses_seleccionados = request.args.getlist("mes")
    fecha_inicio_seleccionada = request.args.get("fecha_inicio", "")
    fecha_fin_seleccionada = request.args.get("fecha_fin", "")
    dias_seleccionados = request.args.getlist("dia_semana")
    origen_seleccionado = request.args.get("origen", "")

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            # 1. Filtros
            cursor.execute("SELECT DISTINCT DATE_FORMAT(fecha, '%Y-%m') AS mes FROM pedidos ORDER BY mes DESC")
            meses_disponibles = [m["mes"] for m in cursor.fetchall()]

            conds_general = []
            params_general = []

            if meses_seleccionados:
                conds_general.append(f"DATE_FORMAT({{campo_fecha}}, '%%Y-%%m') IN ({','.join(['%s']*len(meses_seleccionados))})")
                params_general.extend(meses_seleccionados)
            elif not fecha_inicio_seleccionada and not fecha_fin_seleccionada and meses_disponibles:
                conds_general.append("DATE_FORMAT({campo_fecha}, '%%Y-%%m') = %s")
                params_general.append(meses_disponibles[0])

            if fecha_inicio_seleccionada:
                conds_general.append("DATE({campo_fecha}) >= %s")
                params_general.append(fecha_inicio_seleccionada)
            if fecha_fin_seleccionada:
                conds_general.append("DATE({campo_fecha}) <= %s")
                params_general.append(fecha_fin_seleccionada)

            if dias_seleccionados:
                mapa_dias = {'Domingo': 1, 'Lunes': 2, 'Martes': 3, 'Miércoles': 4, 'Jueves': 5, 'Viernes': 6, 'Sábado': 7}
                dias_num = [str(mapa_dias[d]) for d in dias_seleccionados if d in mapa_dias]
                if dias_num:
                    conds_general.append(f"DAYOFWEEK({{campo_fecha}}) IN ({','.join(dias_num)})")

            conds_pedidos = list(conds_general)
            params_pedidos = list(params_general)
            if origen_seleccionado:
                conds_pedidos.append("{campo_origen} = %s")
                params_pedidos.append(origen_seleccionado)

            conds_compras = list(conds_general)
            params_compras = list(params_general)
            conds_compras.append("LOWER(COALESCE(concepto, '')) NOT LIKE %s")
            params_compras.append('%personal%')

            def build_where(conds, c_fecha, c_origen="origen"):
                if not conds: return ""
                return "WHERE " + " AND ".join([c.replace("{campo_fecha}", c_fecha).replace("{campo_origen}", c_origen) for c in conds])

            filtro_pedidos = build_where(conds_pedidos, "fecha", "origen")
            filtro_compras = build_where(conds_compras, "fecha")
            filtro_gastos = build_where(conds_general, "fecha") 
            filtro_gastos_g = build_where(conds_general, "g.fecha") 

            # ---> VISIÓN INVERSIONISTA (P&L Base)
            conds_inv = list(conds_pedidos)
            conds_inv.append("estado NOT IN ('abierto', 'cancelado')")
            cursor.execute(f"""
                SELECT SUM(total + COALESCE(descuento, 0)) AS venta_bruta, SUM(COALESCE(descuento, 0)) AS descuentos,
                       SUM(total / 1.16) AS venta_neta, SUM(total - (total / 1.16)) AS iva
                FROM pedidos {build_where(conds_inv, "fecha", "origen")}
            """, params_pedidos)
            inv_data = cursor.fetchone()
            inv_venta_neta = float((inv_data and inv_data["venta_neta"]) or 0)
            inv_iva = float((inv_data and inv_data["iva"]) or 0)

            # Métricas de Totales
            cursor.execute(f"SELECT COUNT(DISTINCT DATE(fecha)) AS dias FROM pedidos {filtro_pedidos}", params_pedidos)
            dias_totales = int(cursor.fetchone()["dias"] or 1)
            
            cursor.execute(f"SELECT SUM(total) AS total FROM pedidos {filtro_pedidos}", params_pedidos)
            total_ingresos = Decimal(str(cursor.fetchone()["total"] or 0)) 

            cursor.execute(f"SELECT SUM(costo) AS total FROM insumos_compras {filtro_compras}", params_compras)
            total_food_cost = Decimal(str(cursor.fetchone()["total"] or 0))

            # INTEGRACIÓN COMPLETA DE OPEX (GASTOS)
            cursor.execute(f"SELECT SUM(monto) AS total_opex FROM gastos {filtro_gastos}", params_general)
            total_opex = Decimal(str(cursor.fetchone()["total_opex"] or 0))

            conds_nomina = list(conds_general)
            conds_nomina.append("c.nombre = 'Nómina'")
            cursor.execute(f"""
                SELECT SUM(g.monto) as total_nomina
                FROM gastos g
                JOIN categorias_gastos c ON g.categoria_id = c.id
                {build_where(conds_nomina, "g.fecha")}
            """, params_general)
            total_nomina = Decimal(str(cursor.fetchone()["total_nomina"] or 0))

            # Cálculos Maestros
            prime_cost_pct = ((total_food_cost + total_nomina) / Decimal(str(inv_venta_neta))) * 100 if inv_venta_neta > 0 else Decimal(0)
            utilidad = Decimal(str(inv_venta_neta)) - total_food_cost - total_opex
            gross_margin_pct = (utilidad / Decimal(str(inv_venta_neta)) * 100) if inv_venta_neta > 0 else 0

            # GRÁFICAS ACTUALIZADAS CON OPEX
            query_hist_gastos = f"""
                SELECT f, SUM(total) as total FROM (
                    SELECT DATE(fecha) as f, costo as total FROM insumos_compras {filtro_compras}
                    UNION ALL
                    SELECT DATE(fecha) as f, monto as total FROM gastos {filtro_gastos}
                ) sub GROUP BY f ORDER BY f
            """
            params_hist_gastos = params_compras + params_general
            cursor.execute(query_hist_gastos, params_hist_gastos)
            historico_gastos = [{"fecha": str(r["f"]), "total": float(r["total"] or 0)} for r in cursor.fetchall()]

            cursor.execute(f"SELECT DATE(fecha) as f, SUM(total) as total FROM pedidos {filtro_pedidos} GROUP BY DATE(fecha) ORDER BY f", params_pedidos)
            historico_ingresos = [{"fecha": str(r["f"]), "total": float(r["total"] or 0)} for r in cursor.fetchall()]

            cursor.execute(f"""
                SELECT c.nombre AS concepto, SUM(g.monto) AS total
                FROM gastos g
                JOIN categorias_gastos c ON g.categoria_id = c.id
                {filtro_gastos_g}
                GROUP BY c.nombre
                ORDER BY total DESC
            """, params_general)
            gastos_por_concepto = [{"concepto": str(r["concepto"]), "total": float(r["total"] or 0), "promedio": float(r["total"] or 0) / dias_totales} for r in cursor.fetchall()]

            cursor.execute(f"""
                SELECT concepto, 'Insumo' AS tipo_costo, SUM(costo) AS total_gastado 
                FROM insumos_compras {filtro_compras} GROUP BY concepto 
                UNION ALL
                SELECT c.nombre AS concepto, 'Operativo' AS tipo_costo, SUM(g.monto) AS total_gastado 
                FROM gastos g JOIN categorias_gastos c ON g.categoria_id = c.id {filtro_gastos_g} GROUP BY c.nombre
                ORDER BY total_gastado DESC LIMIT 10
            """, params_compras + params_general)
            top_gastos = cursor.fetchall()

            filtro_bcg = build_where(conds_pedidos, "pe.fecha", "pe.origen")
            cursor.execute(f"""
                SELECT p.nombre, SUM(pi.cantidad) AS cantidad, SUM(pi.subtotal) AS ingreso_total,
                       ((SUM(pi.subtotal) / SUM(pi.cantidad)) - COALESCE(p.costo, 0)) AS margen_unitario
                FROM pedido_items pi JOIN pedidos pe ON pe.id = pi.pedido_id JOIN productos p ON p.id = pi.producto_id
                {filtro_bcg} GROUP BY p.id, p.nombre, p.costo ORDER BY ingreso_total DESC
            """, params_pedidos)
            bcg_raw = cursor.fetchall()
            menu_engineering_data = [{"nombre": i["nombre"], "x": float(i["cantidad"]), "x_promedio": float(i["cantidad"] or 0)/dias_totales, "y": float(i["margen_unitario"]), "y_promedio": float(i["margen_unitario"])} for i in bcg_raw]

            cursor.execute(f"SELECT dia_num, nombre, ROUND(AVG(total_del_dia), 2) AS promedio, SUM(total_del_dia) AS total FROM (SELECT DAYOFWEEK(fecha) AS dia_num, CASE DAYOFWEEK(fecha) WHEN 1 THEN 'Dom' WHEN 2 THEN 'Lun' WHEN 3 THEN 'Mar' WHEN 4 THEN 'Mie' WHEN 5 THEN 'Jue' WHEN 6 THEN 'Vie' WHEN 7 THEN 'Sab' END AS nombre, DATE(fecha) AS f, SUM(total) AS total_del_dia FROM pedidos {filtro_pedidos} GROUP BY DATE(fecha), dia_num, nombre) t GROUP BY dia_num, nombre ORDER BY dia_num", params_pedidos)
            ventas_semana = [{"nombre": v["nombre"], "promedio": float(v["promedio"] or 0), "total": float(v["total"] or 0)} for v in cursor.fetchall()]

    finally:
        conn.close()

    return render_template(
        "dashboard.html",
        inv_venta_neta=inv_venta_neta,
        inv_iva=inv_iva,
        meses_seleccionados=meses_seleccionados, 
        meses_disponibles=meses_disponibles,
        total_ingresos=float(total_ingresos), 
        dias_totales=dias_totales,
        total_costos=float(total_food_cost),
        total_opex=float(total_opex),
        prime_cost_pct=float(prime_cost_pct),
        utilidad=float(utilidad), 
        gross_margin_pct=round(float(gross_margin_pct), 1),
        menu_engineering_data=json.dumps(menu_engineering_data),
        top_productos=bcg_raw[:10], 
        top_gastos=top_gastos, 
        ventas_por_dia_semana=ventas_semana, 
        historico_ingresos=historico_ingresos,
        historico_gastos=historico_gastos,
        gastos_por_concepto=gastos_por_concepto,     
        fecha_inicio_seleccionada=fecha_inicio_seleccionada,
        fecha_fin_seleccionada=fecha_fin_seleccionada,
        dias_seleccionados=dias_seleccionados,
        origen_seleccionado=origen_seleccionado
    )


# =========================================================
# ================== ESTADO DE RESULTADOS =================
# =========================================================

@dashboard_bp.route("/estado-resultados")
def estado_resultados():
    conn = get_connection()
    
    # Año seleccionado o año actual por defecto
    anio_seleccionado = request.args.get("anio", str(datetime.now().year))
    
    try:
        with conn.cursor() as cursor:
            # 1. Obtener años disponibles para el selector
            cursor.execute("SELECT DISTINCT YEAR(fecha) AS anio FROM pedidos ORDER BY anio DESC")
            anios_disponibles = [str(r["anio"]) for r in cursor.fetchall()]
            if anio_seleccionado not in anios_disponibles and anios_disponibles:
                anio_seleccionado = anios_disponibles[0]

            # 2. Inicializar estructura de meses
            nombres_meses = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
            data_meses = {str(i).zfill(2): {
                "venta_bruta": Decimal("0"), "descuentos": Decimal("0"), "venta_neta": Decimal("0"), "iva": Decimal("0"),
                "food_cost": Decimal("0"), "opex_total": Decimal("0"), "categorias_opex": {}
            } for i in range(1, 13)}

            # 3. Obtener Ventas por mes
            cursor.execute("""
                SELECT DATE_FORMAT(fecha, '%m') AS mes,
                       SUM(total + COALESCE(descuento, 0)) AS venta_bruta,
                       SUM(COALESCE(descuento, 0)) AS descuentos,
                       SUM(total / 1.16) AS venta_neta,
                       SUM(total - (total / 1.16)) AS iva
                FROM pedidos
                WHERE YEAR(fecha) = %s AND estado NOT IN ('abierto', 'cancelado')
                GROUP BY mes
            """, (anio_seleccionado,))
            for r in cursor.fetchall():
                m = r["mes"]
                if m in data_meses:
                    data_meses[m]["venta_bruta"] = Decimal(str(r["venta_bruta"] or 0))
                    data_meses[m]["descuentos"] = Decimal(str(r["descuentos"] or 0))
                    data_meses[m]["venta_neta"] = Decimal(str(r["venta_neta"] or 0))
                    data_meses[m]["iva"] = Decimal(str(r["iva"] or 0))

            # 4. Obtener Food Cost (Insumos) por mes
            cursor.execute("""
                SELECT DATE_FORMAT(fecha, '%m') AS mes, SUM(costo) AS food_cost
                FROM insumos_compras
                WHERE YEAR(fecha) = %s AND (LOWER(COALESCE(concepto, '')) NOT LIKE '%personal%')
                GROUP BY mes
            """, (anio_seleccionado,))
            for r in cursor.fetchall():
                m = r["mes"]
                if m in data_meses:
                    data_meses[m]["food_cost"] = Decimal(str(r["food_cost"] or 0))

            # 5. Obtener OPEX por categoría y mes
            cursor.execute("SELECT id, nombre FROM categorias_gastos ORDER BY nombre")
            todas_categorias = [c["nombre"] for c in cursor.fetchall()]
            for m in data_meses.values():
                m["categorias_opex"] = {cat: Decimal("0") for cat in todas_categorias}

            cursor.execute("""
                SELECT DATE_FORMAT(g.fecha, '%m') AS mes, c.nombre AS categoria, SUM(g.monto) AS total
                FROM gastos g
                JOIN categorias_gastos c ON g.categoria_id = c.id
                WHERE YEAR(g.fecha) = %s
                GROUP BY mes, categoria
            """, (anio_seleccionado,))
            for r in cursor.fetchall():
                m = r["mes"]
                if m in data_meses:
                    data_meses[m]["categorias_opex"][r["categoria"]] = Decimal(str(r["total"] or 0))
                    data_meses[m]["opex_total"] += Decimal(str(r["total"] or 0))

    finally:
        conn.close()

    # Calcular Totales Anuales
    totales_anio = {
        "venta_bruta": sum(m["venta_bruta"] for m in data_meses.values()),
        "descuentos": sum(m["descuentos"] for m in data_meses.values()),
        "venta_neta": sum(m["venta_neta"] for m in data_meses.values()),
        "iva": sum(m["iva"] for m in data_meses.values()),
        "food_cost": sum(m["food_cost"] for m in data_meses.values()),
        "opex_total": sum(m["opex_total"] for m in data_meses.values()),
        "categorias_opex": {cat: sum(m["categorias_opex"].get(cat, Decimal("0")) for m in data_meses.values()) for cat in todas_categorias}
    }

    return render_template(
        "estado_resultados.html",
        anio_seleccionado=anio_seleccionado,
        anios_disponibles=anios_disponibles,
        data_meses=data_meses,
        nombres_meses=nombres_meses,
        todas_categorias=todas_categorias,
        totales_anio=totales_anio
    )
