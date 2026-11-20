import sys
from pathlib import Path

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from app.services.pricing_engine import (
    redondear_centena_cercana,
    calcular_costos_item_factura,
    recalcular_liquidacion,
)
from app.services.dian_unit_service import DIANUnitService


def test_redondear_centena_cercana_comprehensive():
    """Valida la regla de redondeo comercial a centenas ($100 COP) y bases dinámicas."""
    # Valores exactos
    assert redondear_centena_cercana(100) == 100
    assert redondear_centena_cercana(2500) == 2500
    assert redondear_centena_cercana(0) == 0
    assert redondear_centena_cercana(-50) == 0

    # Límite de corte (>= 50 hacia arriba, < 50 hacia abajo)
    assert redondear_centena_cercana(249) == 200
    assert redondear_centena_cercana(250) == 300
    assert redondear_centena_cercana(251) == 300

    assert redondear_centena_cercana(1201) == 1200
    assert redondear_centena_cercana(1249.99) == 1200
    assert redondear_centena_cercana(1250) == 1300
    assert redondear_centena_cercana(1250.01) == 1300

    # Base personalizada (base=50)
    assert redondear_centena_cercana(124, base=50) == 100
    assert redondear_centena_cercana(125, base=50) == 150

    # Base personalizada (base=1000)
    assert redondear_centena_cercana(499, base=1000) == 0
    assert redondear_centena_cercana(500, base=1000) == 1000


def test_calcular_costos_item_sin_impuestos_con_margen():
    """Valida el cálculo estándar de costo de compra, margen del 30% y precio de venta."""
    # Costo $10.000, margen 30% -> $13.000
    res = calcular_costos_item_factura(
        costo_original=10000.0,
        cantidad=1.0,
        porcentaje_margen=30.0,
        base_redondeo=100
    )
    assert res["costo_compra_original"] == 10000.0
    assert res["costo_neto_presentacion"] == 10000.0
    assert res["costo_unitario_inventario"] == 10000.0
    assert res["precio_venta_calculado"] == 13000.0
    assert res["precio_venta_final"] == 13000


def test_calcular_costos_con_descuento_porcentual_y_monetario():
    """Valida descuentos comerciales por porcentaje (%) y por valor absoluto en pesos ($)."""
    # 1. Descuento porcentual del 10% sobre $20.000 -> Costo base $18.000
    res_pct = calcular_costos_item_factura(
        costo_original=20000.0,
        cantidad=2.0,
        descuento=10.0,
        modo_descuento="porcentaje",
        porcentaje_margen=25.0
    )
    assert res_pct["descuento_porcentaje"] == 10.0
    assert res_pct["costo_base_presentacion"] == 18000.0
    # Margen 25% sobre 18000 -> 18000 * 1.25 = 22500
    assert res_pct["precio_venta_final"] == 22500

    # 2. Descuento monetario de $2.000 sobre costo $10.000 -> Costo base $8.000
    res_val = calcular_costos_item_factura(
        costo_original=10000.0,
        cantidad=1.0,
        descuento=2000.0,
        descuento_valor=2000.0,
        modo_descuento="valor",
        porcentaje_margen=30.0
    )
    assert res_val["modo_descuento"] == "valor"
    assert res_val["costo_base_presentacion"] == 8000.0
    # Margen 30% sobre 8000 -> 8000 * 1.30 = 10400
    assert res_val["precio_venta_final"] == 10400


def test_calcular_costos_con_operacion_sumar_recargo():
    """Valida la opción de SUMAR el descuento como recargo o flete al costo base."""
    # 1. Recargo porcentual del 10% sobre $20.000 -> Costo base $22.000
    res_pct = calcular_costos_item_factura(
        costo_original=20000.0,
        cantidad=1.0,
        descuento=10.0,
        modo_descuento="porcentaje",
        operacion_descuento="sumar",
        porcentaje_margen=25.0
    )
    assert res_pct["operacion_descuento"] == "sumar"
    assert res_pct["costo_base_presentacion"] == 22000.0
    # Margen 25% sobre 22000 -> 22000 * 1.25 = 27500
    assert res_pct["precio_venta_final"] == 27500

    # 2. Recargo monetario de $2.000 sobre costo $10.000 -> Costo base $12.000
    res_val = calcular_costos_item_factura(
        costo_original=10000.0,
        cantidad=1.0,
        descuento=2000.0,
        descuento_valor=2000.0,
        modo_descuento="valor",
        operacion_descuento="sumar",
        porcentaje_margen=30.0
    )
    assert res_val["operacion_descuento"] == "sumar"
    assert res_val["costo_base_presentacion"] == 12000.0
    # Margen 30% sobre 12000 -> 12000 * 1.30 = 15600
    assert res_val["precio_venta_final"] == 15600



def test_calcular_costos_con_iva_agregado_e_incluido():
    """Valida el manejo de impuestos directos (adicionados) e inversos (IVA incluido)."""
    impuesto_iva_19 = [{"nombre": "IVA 19%", "tasa": 19.0, "aplicado": True}]

    # 1. Impuesto IVA 19% no incluido (se suma al costo base)
    # Costo base $10.000 + 19% IVA ($1.900) = Costo neto $11.900
    res_add = calcular_costos_item_factura(
        costo_original=10000.0,
        iva_incluido=False,
        conceptos_impuestos=impuesto_iva_19,
        porcentaje_margen=20.0
    )
    assert res_add["total_impuestos_aplicados"] == 1900.0
    assert res_add["costo_neto_presentacion"] == 11900.0
    # 11.900 * 1.20 = 14.280 -> redondeado a centenas: 14.300
    assert res_add["precio_venta_final"] == 14300

    # 2. Impuesto IVA 19% ya incluido en el precio
    # Costo neto $11.900 con IVA incluido -> Costo base $10.000
    res_inc = calcular_costos_item_factura(
        costo_original=11900.0,
        iva_incluido=True,
        conceptos_impuestos=impuesto_iva_19,
        porcentaje_margen=20.0
    )
    assert round(res_inc["costo_base_presentacion"], 0) == 10000.0
    assert res_inc["costo_neto_presentacion"] == 11900.0


def test_multipack_unidades_inventario():
    """Valida la conversión de empaques múltiples a unidades unitarias de inventario."""
    # Ejemplo: 2 Cajas de Galletas por 24 unidades cada una a $48.000 la caja
    # Total unidades inventario: 2 * 24 = 48 unidades
    # Costo unitario por galleta: 48000 / 24 = $2.000
    res = calcular_costos_item_factura(
        costo_original=48000.0,
        cantidad=2.0,
        presentacion="Caja",
        unidades_por_presentacion=24.0,
        porcentaje_margen=30.0
    )
    assert res["total_unidades_inventario"] == 48.0
    assert res["costo_unitario_inventario"] == 2000.0
    # 2000 * 1.30 = 2600
    assert res["precio_venta_final"] == 2600


def test_recalcular_liquidacion_fruver():
    """Valida la liquidación financiera con prorrateo de fletes/gastos operativos por kilo."""
    productos = [
        {"kilos": 50.0, "costo_compra_kilo": 2000.0, "porcentaje_utilidad": 30.0},
        {"kilos": 50.0, "costo_compra_kilo": 4000.0, "porcentaje_utilidad": 30.0},
    ]
    # Total kilos: 100 kg
    # Gasto de flete: $50.000 -> $500 por kilo
    gastos = [
        {"nombre": "Flete camión", "valor": 50000.0, "aplicado": True}
    ]

    res = recalcular_liquidacion(productos, gastos, utilidad_global=30.0)

    # 1. Gasto por kilo: 50.000 / 100 = 500 COP
    assert res["resumen"]["gasto_por_kilo"] == 500
    assert res["resumen"]["total_kilos"] == 100.0

    # 2. Producto 1: Costo base 2000 + 500 = 2500 costo real
    # Venta: 2500 * 1.30 = 3250 -> Redondeado a centenas: 3300 COP
    item0 = res["items"][0]
    assert item0["costo_real_kilo"] == 2500
    assert item0["precio_venta_kilo"] == 3300

    # 3. Producto 2: Costo base 4000 + 500 = 4500 costo real
    # Venta: 4500 * 1.30 = 5850 -> Redondeado a centenas: 5900 COP
    item1 = res["items"][1]
    assert item1["costo_real_kilo"] == 4500
    assert item1["precio_venta_kilo"] == 5900

    # 4. Totales financieros
    # Compras: (50 * 2000) + (50 * 4000) = 100.000 + 200.000 = 300.000
    assert res["resumen"]["total_compras"] == 300000.0
    assert res["resumen"]["total_gastos"] == 50000.0
    # Ventas estimadas: (50 * 3300) + (50 * 5900) = 165.000 + 295.000 = 460.000
    assert res["resumen"]["total_ventas"] == 460000.0
    # Ganancia: 460.000 - (300.000 + 50.000) = 110.000
    assert res["resumen"]["ganancia"] == 110000.0
    assert res["resumen"]["margen_global_porcentaje"] > 20.0


def test_dian_unit_multipack_and_cross_conversions():
    """Valida la detección heurística de multipacks DIAN y conversión entre unidades."""
    # 1. Detección de empaque en descripción
    res_pack = DIANUnitService.resolve_unit("WSD", "CHOCOLATINA JET DISPLAY X 12 UND 54GR")
    assert res_pack["es_multipack"] is True
    assert res_pack["unidades_por_presentacion"] == 12.0

    # 2. Conversión Gramos a Kilogramos: 500g a $8/g ($4.000 total) -> 1kg equivale a $8.000
    conv_grm_kg = DIANUnitService.calculate_cross_unit_conversion("GRM", "KG", 500.0, 8.0)
    assert conv_grm_kg["conversion_requerida"] is True
    assert conv_grm_kg["cantidad_convertida"] == 0.5
    assert conv_grm_kg["costo_convertido"] == 8000.0
