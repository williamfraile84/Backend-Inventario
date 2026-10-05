import math
from typing import Dict, Any, List, Optional
from app.core.config import settings

def redondear_centena_cercana(valor: float, base: int = None) -> int:
    """
    Redondea al múltiplo de base más cercano (por defecto 100).
    Regla estándar de negocio idéntica a sistema_fruver_actual:
    - 251 se redondea a 300
    - 249 se redondea a 200
    - 250 se redondea a 300
    """
    if base is None:
        base = settings.ROUNDING_BASE or 100
    if not valor or valor <= 0:
        return 0
    return int(math.floor((float(valor) / float(base)) + 0.5)) * base


def calcular_costos_item_factura(
    costo_original: float,
    cantidad: float = 1.0,
    presentacion: str = "Und",
    unidades_por_presentacion: float = 1.0,
    descuento: float = 0.0,
    iva_incluido: bool = False,
    conceptos_impuestos: Optional[List[Dict[str, Any]]] = None,
    porcentaje_margen: Optional[float] = None,
    precio_venta_manual: Optional[float] = None,
    base_redondeo: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Cálculo dinámico, transparente y desacoplado del costo de un producto proveniente de factura:
    1. Resuelve si los impuestos detectados ya están incluidos en el costo base o deben adicionarse.
    2. Permite activar/desactivar conceptos tributarios individuales (IVA 19%, IVA 5%, ICUI, etc.).
    3. Normaliza la cantidad de presentación a unidades individuales de inventario (ej. 5 cajas x 24 und = 120 und).
    4. Determina el costo neto por presentación y el costo unitario de inventario.
    5. Aplica el margen de utilidad y realiza el redondeo a centenas centralizado.
    1. Aplica descuentos comerciales otorgados en factura sobre el costo bruto.
    2. Resuelve si los impuestos detectados ya están incluidos en el costo base o deben adicionarse.
    3. Permite activar/desactivar conceptos tributarios individuales (IVA 19%, IVA 5%, ICUI, etc.).
    4. Normaliza la cantidad de presentación a unidades individuales de inventario (ej. 5 cajas x 24 und = 120 und).
    5. Determina el costo neto por presentación y el costo unitario de inventario.
    6. Aplica el margen de utilidad y realiza el redondeo a centenas centralizado.
    """
    if base_redondeo is None:
        base_redondeo = settings.ROUNDING_BASE or 100

    if porcentaje_margen is None:
        porcentaje_margen = settings.DEFAULT_PROFIT_MARGIN

    cantidad = max(0.001, float(cantidad or 1.0))
    unidades_por_presentacion = max(1.0, float(unidades_por_presentacion or 1.0))
    costo_original = max(0.0, float(costo_original or 0.0))
    descuento = max(0.0, float(descuento or 0.0))

    # Aplicación de descuento comercial
    if 0.0 < descuento <= 100.0:
        costo_base_compra = costo_original * (1.0 - (descuento / 100.0))
    elif descuento > 100.0:
        costo_base_compra = max(0.0, costo_original - descuento)
    else:
        costo_base_compra = costo_original

    if conceptos_impuestos is None:
        conceptos_impuestos = []

    # Filtrar conceptos tributarios que el usuario tiene activos/seleccionados
    impuestos_procesados = []
    total_tasa_porcentual_activa = 0.0
    total_impuestos_fijos_activos = 0.0

    for imp in conceptos_impuestos:
        nombre = str(imp.get("nombre", "IVA")).strip().upper()
        tasa = float(imp.get("tasa", 0.0) or 0.0)
        aplicado = bool(imp.get("aplicado", True))
        valor_fijo = float(imp.get("valor_fijo", 0.0) or 0.0)

        if aplicado:
            total_tasa_porcentual_activa += tasa
            total_impuestos_fijos_activos += valor_fijo

        impuestos_procesados.append({
            "nombre": nombre,
            "tasa": tasa,
            "valor_fijo": valor_fijo,
            "aplicado": aplicado,
            "valor_calculado": 0.0
        })

    # Cálculo del costo base y neto según modalidad de impuestos
    if iva_incluido:
        # El precio original ya tiene los impuestos incluidos:
        # Costo Base = Costo Original / (1 + (TasaTotal / 100))
        divisor = 1.0 + (total_tasa_porcentual_activa / 100.0) if total_tasa_porcentual_activa > 0 else 1.0
        costo_base_presentacion = (costo_original - total_impuestos_fijos_activos) / divisor if divisor > 0 else costo_original
        costo_base_presentacion = (costo_base_compra - total_impuestos_fijos_activos) / divisor if divisor > 0 else costo_base_compra
        costo_base_presentacion = max(0.0, costo_base_presentacion)

        total_impuestos_calculados = 0.0
        for imp in impuestos_procesados:
            if imp["aplicado"]:
                val = (costo_base_presentacion * (imp["tasa"] / 100.0)) + imp["valor_fijo"]
                imp["valor_calculado"] = round(val, 2)
                total_impuestos_calculados += val

        costo_neto_presentacion = costo_original
        costo_neto_presentacion = costo_base_compra
    else:
        # Los impuestos no están incluidos y deben sumarse al costo
        costo_base_presentacion = costo_original
        costo_base_presentacion = costo_base_compra
        total_impuestos_calculados = 0.0
        for imp in impuestos_procesados:
            if imp["aplicado"]:
                val = (costo_base_presentacion * (imp["tasa"] / 100.0)) + imp["valor_fijo"]
                imp["valor_calculado"] = round(val, 2)
                total_impuestos_calculados += val

        costo_neto_presentacion = costo_base_presentacion + total_impuestos_calculados

    # Conversión de unidades de presentación a inventario
    total_unidades_inventario = round(cantidad * unidades_por_presentacion, 3)
    costo_unitario_inventario = costo_neto_presentacion / unidades_por_presentacion if unidades_por_presentacion > 0 else costo_neto_presentacion

    # Cálculo del precio de venta final
    if precio_venta_manual is not None and float(precio_venta_manual) > 0:
        precio_venta_calculado = float(precio_venta_manual)
        precio_venta_final = redondear_centena_cercana(precio_venta_calculado, base_redondeo)
        margen_real = ((precio_venta_final - costo_unitario_inventario) / costo_unitario_inventario * 100.0) if costo_unitario_inventario > 0 else 0.0
    else:
        precio_venta_calculado = costo_unitario_inventario * (1.0 + (porcentaje_margen / 100.0))
        precio_venta_final = redondear_centena_cercana(precio_venta_calculado, base_redondeo)
        margen_real = porcentaje_margen

    return {
        "costo_compra_original": round(costo_original, 2),
        "descuento": round(descuento, 2),
        "cantidad_presentacion": cantidad,
        "presentacion": presentacion,
        "unidades_por_presentacion": unidades_por_presentacion,
        "total_unidades_inventario": total_unidades_inventario,
        "iva_incluido": iva_incluido,
        "conceptos_impuestos": impuestos_procesados,
        "total_impuestos_aplicados": round(total_impuestos_calculados, 2),
        "costo_base_presentacion": round(costo_base_presentacion, 2),
        "costo_neto_presentacion": round(costo_neto_presentacion, 2),
        "costo_unitario_inventario": round(costo_unitario_inventario, 2),
        "porcentaje_margen": round(margen_real, 2),
        "precio_venta_calculado": round(precio_venta_calculado, 2),
        "precio_venta_final": int(precio_venta_final),
        "base_redondeo": base_redondeo
    }


def recalcular_liquidacion(
    productos: List[Dict[str, Any]],
    gastos: List[Dict[str, Any]],
    utilidad_global: Optional[float] = None
) -> Dict[str, Any]:
    """
    Ejecuta el cálculo financiero Fruver:
    1. Prorrateo de gastos operativos por kilo con redondeo a centena.
    2. Asignación del gasto por kilo a cada ítem.
    3. Cálculo del costo real por kilo redondeado a centena.
    4. Cálculo del precio de venta por producto y redondeo a centena.
    5. Balance general de compras, gastos y margen de ganancia.
    """
    if utilidad_global is None:
        utilidad_global = settings.DEFAULT_PROFIT_MARGIN

    total_kilos = sum(
        float(p.get("kilos", 0) or p.get("cantidad", 0))
        for p in productos
        if float(p.get("kilos", 0) or p.get("cantidad", 0)) > 0
    ) or 1.0

    total_gastos_aplicados = sum(
        float(g.get("valor", 0))
        for g in gastos
        if g.get("aplicado", True)
    )

    gasto_por_kilo_exacto = total_gastos_aplicados / total_kilos if total_kilos > 0 else 0.0
    gasto_por_kilo = redondear_centena_cercana(gasto_por_kilo_exacto)

    total_compras = 0.0
    total_ventas = 0.0
    items_calculados = []

    for p in productos:
        kilos = float(p.get("kilos", 0) or p.get("cantidad", 0))
        raw_costo = p.get("costo_compra_kilo")
        raw_total_compra = p.get("valor_compra_total")

        if raw_costo is not None and str(raw_costo).strip() != "" and float(raw_costo) > 0:
            costo_compra_kilo = redondear_centena_cercana(float(raw_costo))
            valor_compra_total = redondear_centena_cercana(costo_compra_kilo * kilos) if (raw_total_compra is None or str(raw_total_compra).strip() == "") else redondear_centena_cercana(float(raw_total_compra))
        elif raw_total_compra is not None and str(raw_total_compra).strip() != "" and float(raw_total_compra) > 0 and kilos > 0:
            valor_compra_total = redondear_centena_cercana(float(raw_total_compra))
            costo_compra_kilo = redondear_centena_cercana(valor_compra_total / kilos)
        else:
            costo_compra_kilo = 0.0
            valor_compra_total = 0.0

        costo_real_kilo = redondear_centena_cercana(costo_compra_kilo + gasto_por_kilo) if kilos > 0 else 0
        util_item = int(round(float(p.get("porcentaje_utilidad", utilidad_global) if p.get("porcentaje_utilidad") is not None else utilidad_global)))

        precio_venta_directo = p.get("precio_venta_kilo")
        if precio_venta_directo is not None and str(precio_venta_directo).strip() != "" and float(precio_venta_directo) > 0:
            precio_venta_kilo = redondear_centena_cercana(float(precio_venta_directo))
        elif costo_real_kilo > 0:
            precio_venta_kilo = redondear_centena_cercana(costo_real_kilo * (1 + (util_item / 100.0)))
        else:
            precio_venta_kilo = 0

        raw_total_venta = p.get("valor_total_venta")
        if bool(p.get("precio_venta_manual")) and raw_total_venta is not None and str(raw_total_venta).strip() != "" and float(raw_total_venta) > 0:
            valor_total_venta = redondear_centena_cercana(float(raw_total_venta))
        else:
            valor_total_venta = redondear_centena_cercana(precio_venta_kilo * kilos)

        total_compras += valor_compra_total
        total_ventas += valor_total_venta

        item_dict = dict(p)
        item_dict.update({
            "kilos": kilos,
            "costo_compra_kilo": float(costo_compra_kilo),
            "valor_compra_total": float(valor_compra_total),
            "gasto_asignado_kilo": int(gasto_por_kilo),
            "gasto_exacto_kilo": round(gasto_por_kilo_exacto, 2),
            "costo_real_kilo": int(costo_real_kilo),
            "precio_venta_kilo": int(precio_venta_kilo),
            "valor_total_venta": float(valor_total_venta),
            "porcentaje_utilidad": util_item
        })
        items_calculados.append(item_dict)

    ganancia = redondear_centena_cercana(total_ventas - (total_compras + total_gastos_aplicados))
    margen_global = (ganancia / total_ventas * 100.0) if total_ventas > 0 else 0.0

    return {
        "items": items_calculados,
        "resumen": {
            "total_kilos": round(total_kilos, 2),
            "total_compras": float(redondear_centena_cercana(total_compras)),
            "total_gastos": float(total_gastos_aplicados),
            "gasto_por_kilo": int(gasto_por_kilo),
            "gasto_por_kilo_exacto": round(gasto_por_kilo_exacto, 2),
            "total_ventas": float(redondear_centena_cercana(total_ventas)),
            "ganancia": float(ganancia),
            "margen_global_porcentaje": round(margen_global, 2)
        }
    }

