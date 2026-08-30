from flask import Blueprint, render_template, request
from decimal import Decimal
import json
from db import get_connection

dashboard_bp = Blueprint("dashboard_bp", __name__)

# --- HELPERS LOCALES PARA EL DASHBOARD ---
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


@dashboard_bp.route("/dashboard")
def dashboard():
    meses_seleccionados = request.args.getlist("mes")
    fecha_inicio_seleccionada = request.args.get("fecha_inicio", "")
    fecha_fin_seleccionada = request.args.get("fecha_fin", "")
    dias_seleccionados = request.args.getlist("dia_semana")
    origen_seleccionado = request.args.get("origen", "")

    conn = get_connection()

    try:
        with conn.cursor() as cursor: # Usamos cursor normal o dict_cursor dependiendo de tu db.py, asumo que usas DictCursor en las globales
            # 1. Filtros Disponibles
            cursor.execute("SELECT DISTINCT DATE_FORMAT(fecha, '%Y-%m') AS mes FROM pedidos ORDER BY mes DESC")
            meses_disp_raw = cursor.fetchall()
            meses_disponibles = [m["mes"] for m in meses_disp_raw]

            # 2. Construcción de Filtros Dinámicos
            conds_general = []
            params_general = []

            if meses_seleccionados:
                placeholders = ",".join(["%s"] * len(meses_seleccionados))
                conds_general.append(f"DATE_FORMAT({{campo_fecha}}, '%%Y-%%m') IN ({placeholders})")
                params_general.extend(meses_seleccionados)
            elif not fecha_inicio_seleccionada and not fecha_fin_seleccionada:
                if meses_disponibles:
                    last_m = meses_disponibles[0]
                    conds_general.append("DATE_FORMAT({campo_fecha}, '%%Y-%%m') = %s")
                    params_general.append(last_m)

            if fecha_inicio_seleccionada:
                conds_general.append("DATE({campo_fecha}) >= %s")
                params_general.append(fecha_inicio_seleccionada)
            if fecha_fin_seleccionada:
                conds_general.append("DATE({campo_fecha}) <= %s")
                params_general.append(fecha_fin_seleccionada)

            p_dias = ""
            if dias_seleccionados:
                mapa_dias = {'Domingo': 1, 'Lunes': 2, 'Martes': 3, 'Miércoles': 4, 'Jueves': 5, 'Viernes': 6, 'Sábado': 7}
                dias_num = [str(mapa_dias[d]) for d in dias_seleccionados if d in mapa_dias]
                if dias_num:
                    p_dias = ",".join(dias_num)
                    conds_general.append(f"DAYOFWEEK({{campo_fecha}}) IN ({p_dias})")

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
            filtro_ads = build_where(conds_general, "dia")
            filtro_org = build_where(conds_general, "hora_publicacion")

            filtro_tx_p = build_where(conds_pedidos, "p.fecha", "p.origen")
            if filtro_tx_p.startswith("WHERE"):
                filtro_tx_p = "AND " + filtro_tx_p[5:]

            filtro_bcg = build_where(conds_pedidos, "pe.fecha", "pe.origen")

            # Prev Mes Filtros
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
                if p_dias:
                    conds_prev.append(f"DAYOFWEEK({{campo_fecha}}) IN ({p_dias})")

                conds_prev_pedidos = list(conds_prev)
                params_prev_pedidos = list(params_prev_base)
                if origen_seleccionado:
                    conds_prev_pedidos.append("{campo_origen} = %s")
                    params_prev_pedidos.append(origen_seleccionado)

                conds_prev_compras = list(conds_prev)
                params_prev_compras = list(params_prev_base)
                conds_prev_compras.append("LOWER(COALESCE(concepto, '')) NOT LIKE %s")
                params_prev_compras.append('%personal%')

                filtro_prev_pedidos = build_where(conds_prev_pedidos, "fecha", "origen")
                filtro_prev_compras = build_where(conds_prev_compras, "fecha")

            # ---> VISIÓN INVERSIONISTA
            conds_inv = list(conds_pedidos)
            conds_inv.append("estado NOT IN ('abierto', 'cancelado')")
            filtro_inv = build_where(conds_inv, "fecha", "origen")

            cursor.execute(f"""
                SELECT 
                    SUM(total + COALESCE(descuento, 0)) AS venta_bruta,
                    SUM(COALESCE(descuento, 0)) AS descuentos,
                    SUM(total / 1.16) AS venta_neta,
                    SUM(total - (total / 1.16)) AS iva
                FROM pedidos
                {filtro_inv}
            """, params_pedidos)
            
            inv_data = cursor.fetchone()
            inv_venta_bruta = float((inv_data and inv_data["venta_bruta"]) or 0)
            inv_descuentos = float((inv_data and inv_data["descuentos"]) or 0)
            inv_venta_neta = float((inv_data and inv_data["venta_neta"]) or 0)
            inv_iva = float((inv_data and inv_data["iva"]) or 0)

            # Métricas Generales
            cursor.execute(f"SELECT COUNT(DISTINCT DATE(fecha)) AS dias FROM pedidos {filtro_pedidos}", params_pedidos)
            dias_totales = int(cursor.fetchone()["dias"] or 1)
            meses_con_venta = len(meses_seleccionados) if meses_seleccionados else 1

            cursor.execute(f"SELECT SUM(total) AS total FROM pedidos {filtro_pedidos}", params_pedidos)
            total_ingresos = Decimal(str(cursor.fetchone()["total"] or 0)) 

            cursor.execute(f"SELECT SUM(costo) AS total FROM insumos_compras {filtro_compras}", params_compras)
            total_costos = Decimal(str(cursor.fetchone()["total"] or 0))

            # =========================================================
            # ---> NUEVO: CÁLCULO DE NÓMINA Y COSTO PRIMO
            # =========================================================
            conds_nomina = list(conds_general)
            conds_nomina.append("c.nombre = 'Nómina'")
            filtro_nomina = build_where(conds_nomina, "g.fecha")

            cursor.execute(f"""
                SELECT SUM(g.monto) as total_nomina
                FROM gastos g
                JOIN categorias_gastos c ON g.categoria_id = c.id
                {filtro_nomina}
            """, params_general)
            row_nomina = cursor.fetchone()
            total_nomina = Decimal(str(row_nomina["total_nomina"] if row_nomina and row_nomina["total_nomina"] else 0))

            # Costo Primo = (Food Cost + Nomina) / Venta Neta * 100
            if inv_venta_neta > 0:
                prime_cost_pct = ((total_costos + total_nomina) / Decimal(str(inv_venta_neta))) * 100
            else:
                prime_cost_pct = Decimal(0)
            # =========================================================

            utilidad = Decimal(str(inv_venta_neta)) - total_costos - total_nomina
            gross_margin_pct = (utilidad / Decimal(str(inv_venta_neta)) * 100) if inv_venta_neta > 0 else 0

            # Variaciones vs Periodo Anterior
            var_ingresos = var_costos = var_utilidad = 0
            if filtro_prev_pedidos:
                cursor.execute(f"SELECT SUM(total) AS total, SUM(total / 1.16) AS venta_neta FROM pedidos {filtro_prev_pedidos}", params_prev_pedidos)
                prev_data = cursor.fetchone()
                prev_ingresos = Decimal(str(prev_data["total"] or 0))
                prev_venta_neta = Decimal(str(prev_data["venta_neta"] or 0))

                cursor.execute(f"SELECT SUM(costo) AS total FROM insumos_compras {filtro_prev_compras}", params_prev_compras)
                prev_costos = Decimal(str(cursor.fetchone()["total"] or 0))

                # Faltaría sumar Nómina previa para precisión 100%, lo simplificamos por ahora
                prev_utilidad = prev_venta_neta - prev_costos 

                var_ingresos = calc_var(float(total_ingresos), float(prev_ingresos))
                var_costos = calc_var(float(total_costos), float(prev_costos))
                var_utilidad = calc_var(float(utilidad), float(prev_utilidad))

            # CRM y Lealtad
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

            # Tendencias Históricas
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
            """, params_compras)
            gastos_comp_raw = cursor.fetchall()
            gastos_comparativas = {}
            for r in gastos_comp_raw:
                mes = r["mes"]
                if mes not in gastos_comparativas: gastos_comparativas[mes] = {}
                gastos_comparativas[mes][r["dia_num"]] = float(r["total"] or 0)

            cursor.execute(f"SELECT DATE(fecha) as f, SUM(total) as total FROM pedidos {filtro_pedidos} GROUP BY DATE(fecha) ORDER BY f", params_pedidos)
            historico_ingresos = [{"fecha": str(r["f"]), "total": float(r["total"] or 0)} for r in cursor.fetchall()]

            cursor.execute(f"SELECT DATE(fecha) as f, SUM(costo) as total FROM insumos_compras {filtro_compras} GROUP BY f ORDER BY f", params_compras)
            historico_gastos = [{"fecha": str(r["f"]), "total": float(r["total"] or 0)} for r in cursor.fetchall()]


            # Operativa: BCG y Horarios
            cursor.execute(f"""
                SELECT p.nombre, SUM(pi.cantidad) AS cantidad, SUM(pi.subtotal) AS ingreso_total,
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

            top_productos = bcg_raw[:10]
            cursor.execute(f"SELECT concepto, tipo_costo, COUNT(*) AS veces, SUM(costo) AS total_gastado FROM insumos_compras {filtro_compras} GROUP BY concepto, tipo_costo ORDER BY total_gastado DESC LIMIT 10", params_compras)
            top_gastos = cursor.fetchall()
            for g in top_gastos: 
                g["promedio_gastado"] = float(g["total_gastado"] or 0) / meses_con_venta

            # Marketing (Se envían los datos para no romper tu lógica, pero ya lo limpiaremos luego)
            total_gasto_ads = 0
            total_alcance = 0
            total_impresiones = 0
            cac_global = 0
            roas_global = 0
            ads_vs_ventas = []
            org_vs_ventas = []

    finally:
        conn.close()

    return render_template(
        "dashboard.html",
        inv_venta_bruta=inv_venta_bruta,
        inv_descuentos=inv_descuentos,
        inv_venta_neta=inv_venta_neta,
        inv_iva=inv_iva,
        meses_seleccionados=meses_seleccionados, 
        meses_disponibles=meses_disponibles,
        total_ingresos=float(total_ingresos), 
        dias_totales=dias_totales,
        var_ingresos=var_ingresos,
        total_costos=float(total_costos), 
        total_nomina=float(total_nomina),       # NÓMINA
        prime_cost_pct=float(prime_cost_pct),   # COSTO PRIMO KPI
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
        ultimos_pedidos=[],
        ventas_comparativas=ventas_comparativas,
        gastos_comparativas=gastos_comparativas,
        historico_ingresos=historico_ingresos,
        historico_gastos=historico_gastos,
        ingresos_por_concepto=[], 
        gastos_por_concepto=[],     
        total_gasto_ads=total_gasto_ads,
        total_alcance=total_alcance,
        total_impresiones=total_impresiones,
        cac_global=cac_global,
        roas_global=roas_global,
        ads_vs_ventas=ads_vs_ventas,
        org_vs_ventas=org_vs_ventas,
        fecha_inicio_seleccionada=fecha_inicio_seleccionada,
        fecha_fin_seleccionada=fecha_fin_seleccionada,
        dias_seleccionados=dias_seleccionados,
        origen_seleccionado=origen_seleccionado
    )
