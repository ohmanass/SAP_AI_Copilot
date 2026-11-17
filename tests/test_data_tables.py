"""Fiori data tables built from transactional tool results."""

from app.agent.data_tables import MAX_ROWS, build_table, collect_table


def purchase_order(**overrides):
    result = {
        "source": "SAP S/4HANA Cloud sandbox",
        "po_number": "4500000001",
        "supplier": "Test Supplier",
        "order_date": "2026-11-01",
        "currency": "EUR",
        "total_net_amount": 1000.0,
        "items": [{"item": "10", "material": "MAT-1", "description": "Bolt", "quantity": 100,
                   "unit": "PC", "net_amount": 1000.0, "delivery_date": "2026-11-20"}]
    }
    result.update(overrides)
    return result


def keys(table):
    return [column["key"] for column in table["columns"]]


def test_other_tools_and_errors_give_no_table():
    assert build_table("search_sap_documentation", {"results": []}) is None
    assert build_table("get_purchase_order", {"error": "not found"}) is None
    assert build_table("get_purchase_order", "not a dict") is None
    assert build_table("get_purchase_order", {"items": []}) is None


def test_purchase_order_table():
    table = build_table("get_purchase_order", purchase_order())
    assert table["kind"] == "purchase_order"
    assert table["title_params"] == ["4500000001", "Test Supplier"]
    assert table["source"] == "SAP S/4HANA Cloud sandbox"
    # Columns without any value are dropped (plant, open quantity, unit price)
    assert keys(table) == ["item", "material", "description", "quantity", "net_amount", "delivery_date"]
    # Item amounts carry the document currency
    assert table["rows"][0]["currency"] == "EUR"
    attributes = {a["key"]: a for a in table["attributes"]}
    assert attributes["total_net_amount"]["unit"] == "EUR"
    assert "status" not in attributes


def test_rows_are_limited():
    items = [{"item": str(i), "quantity": 1, "unit": "PC"} for i in range(MAX_ROWS + 20)]
    table = build_table("get_purchase_order", purchase_order(items=items))
    assert len(table["rows"]) == MAX_ROWS


def test_overdue_orders_count_schedule_lines():
    result = {"orders": [{"po_number": "4500000001", "supplier": "A", "days_overdue": 3}],
              "overdue_schedule_lines_in_window": 12, "window": "last 90 days"}
    table = build_table("get_overdue_orders", result)
    assert table["kind"] == "overdue_purchase_orders"
    assert (table["total"], table["total_unit"]) == (12, "schedule_lines")
    assert table["window"] == "last 90 days"
    assert (table["row_action"], table["row_key"]) == ("purchase_order", "po_number")


def test_purchase_order_search_counts_orders():
    result = {"orders": [{"po_number": "4500000001"}], "total_matching": 40}
    table = build_table("search_purchase_orders", result)
    assert (table["total"], table["total_unit"]) == (40, "orders")


def test_blocked_invoice_list():
    invoices = [{"invoice": "5100000001", "supplier": "A", "gross_amount": 10, "currency": "EUR",
                 "payment_block": "A", "status": "POSTED"}]
    blocked = build_table("search_supplier_invoices", {"invoices": invoices, "filter": "blocked", "total_matching": 46})
    assert blocked["kind"] == "blocked_supplier_invoices"
    assert (blocked["total"], blocked["total_unit"]) == (46, "invoices")
    assert (blocked["row_action"], blocked["row_key"]) == ("supplier_invoice", "invoice")

    search = build_table("search_supplier_invoices", {"invoices": invoices})
    assert search["kind"] == "supplier_invoice_search"


def test_supplier_invoice_rows_open_the_three_way_match():
    result = {"invoice": "5100000001", "supplier": "A", "gross_amount": 1000, "currency": "EUR",
              "items": [{"item": "1", "po_number": "4500000001", "po_item": "10", "quantity": 100,
                         "unit": "PC", "amount": 1000, "currency": "EUR"}]}
    table = build_table("get_supplier_invoice", result)
    assert (table["row_action"], table["row_key"]) == ("three_way_match", "po_number")
    assert "goods_receipt" not in keys(table)


def test_three_way_match_table():
    result = {"po_number": "4500000001", "supplier": "A", "currency": "EUR", "match_status": "VARIANCE",
              "ordered_amount": 1000, "invoiced_amount": 1010, "gr_ir_balance": -10,
              "items": [{"item": "10", "description": "Bolt", "ordered_quantity": 100, "received_quantity": 100,
                         "invoiced_quantity": 100, "invoiced_amount": 1010, "price_difference": 10,
                         "gr_ir_balance": -10, "unit": "PC", "currency": "EUR", "match_status": "PRICE_VARIANCE"}]}
    table = build_table("match_purchase_order_invoices", result)
    assert table["kind"] == "three_way_match"
    assert table["rows"][0]["match_status"] == "PRICE_VARIANCE"
    assert [a["key"] for a in table["attributes"]] == [
        "supplier", "match_status", "ordered_amount", "invoiced_amount", "gr_ir_balance"]


def test_identical_tables_are_collected_once():
    tables = []
    collect_table("get_purchase_order", purchase_order(), tables)
    collect_table("get_purchase_order", purchase_order(), tables)
    collect_table("get_purchase_order", purchase_order(po_number="4500000002"), tables)
    collect_table("get_purchase_order", {"error": "not found"}, tables)
    assert [t["title_params"][0] for t in tables] == ["4500000001", "4500000002"]
