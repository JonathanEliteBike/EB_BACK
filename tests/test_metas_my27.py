from decimal import Decimal

from utils.metas_my27 import calcular_metas_my27


def test_metas_base_una_sucursal():
    casos = {
        "Distribuidor": ("510000.00", "325500.00", "450000.00", "60000.00"),
        "Partner": ("1765000.00", "1118750.00", "1575000.00", "190000.00"),
        "Partner Elite": ("2750000.00", "1738000.00", "2420000.00", "330000.00"),
        "Partner Elite Plus": ("7520000.00", "4795000.00", "6900000.00", "620000.00"),
    }

    for nivel, esperado in casos.items():
        metas = calcular_metas_my27(nivel, 1)
        assert metas["compra_minima_anual"] == Decimal(esperado[0])
        assert metas["compra_minima_inicial"] == Decimal(esperado[1])
        assert metas["compromiso_scott"] == Decimal(esperado[2])
        assert metas["compromiso_apparel_syncros_vittoria"] == Decimal(esperado[3])


def test_integral_pep_dos_sucursales():
    metas = calcular_metas_my27("Partner Elite Plus", 2)

    assert metas["compra_minima_anual"] == Decimal("11280000.00")
    assert metas["compra_minima_inicial"] == Decimal("7192500.00")
    assert metas["compromiso_scott"] == Decimal("10350000.00")
    assert metas["compromiso_jul_ago"] == Decimal("2220075.00")
    assert metas["compromiso_sep_oct"] == Decimal("2354625.00")
    assert metas["compromiso_nov_dic"] == Decimal("2152800.00")
    assert metas["compromiso_ene_feb"] == Decimal("1449000.00")
    assert metas["compromiso_mar_abr"] == Decimal("1630125.00")
    assert metas["compromiso_may_jun"] == Decimal("543375.00")
    assert metas["compromiso_apparel_syncros_vittoria"] == Decimal("930000.00")
    assert metas["compromiso_jul_ago_app"] == Decimal("153450.00")
    assert metas["compromiso_sep_oct_app"] == Decimal("162750.00")
    assert metas["compromiso_nov_dic_app"] == Decimal("148800.00")
    assert metas["compromiso_ene_feb_app"] == Decimal("153450.00")
    assert metas["compromiso_mar_abr_app"] == Decimal("162750.00")
    assert metas["compromiso_may_jun_app"] == Decimal("148800.00")
