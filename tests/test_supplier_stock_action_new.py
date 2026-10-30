import supplier_alerts


def test_supplier_low_is_compared_to_shop_inventory():
    assert supplier_alerts.level_for(10, shop_stock=20) == 'low'
    assert supplier_alerts.level_for(5, shop_stock=5) == 'ok'
    assert supplier_alerts.level_for(0, shop_stock=0) == 'out'
    assert supplier_alerts.level_for(1, flagged=True, shop_stock=20) == 'ok'


def test_variant_readings_compare_to_variant_inventory():
    assert supplier_alerts.supplier_status_for([('red', 12, False)], option_stock={'red': 15}) == 'low_stock'
    assert supplier_alerts.supplier_status_for([('red', 5, False)], option_stock={'red': 5}) == 'ok'
