"""Charts of the SAP data tables: built from the SAP rows, only when they tell something."""

from app.agent.charts import MAX_BARS, build_chart
from app.agent.data_tables import build_table


def invoices(*amounts, currency="USD"):
    return {"source": "S4", "total_matching": len(amounts), "filter": "invoices blocked for payment",
            "invoices": [{"invoice": f"51000{i:05d}", "supplier": "Infinity Inc", "gross_amount": amount,
                          "currency": currency} for i, amount in enumerate(amounts)]}


def test_blocked_invoices_largest_amounts_first_with_their_action():
    chart = build_table("search_supplier_invoices", invoices(100.0, 5000.0, 250.5))["chart"]
    assert chart["type"] == "bars" and chart["value_type"] == "amount" and chart["series"] == ["gross_amount"]
    assert [p["values"][0] for p in chart["points"]] == [5000.0, 250.5, 100.0]
    first = chart["points"][0]
    assert first == {"label": "5100000001", "detail": "Infinity Inc", "key": "5100000001",
                     "values": [5000.0], "unit": "USD"}
    assert chart["action"] == "supplier_invoice"


def test_only_the_largest_bars_are_drawn():
    chart = build_table("search_supplier_invoices", invoices(*range(1, 26)))["chart"]
    assert chart["shown"] == MAX_BARS and chart["total"] == 25
    assert chart["points"][0]["values"] == [25.0]


def test_no_chart_when_it_would_tell_nothing():
    # one bar, all equal, all zero, several currencies, a table without chart
    assert build_table("search_supplier_invoices", invoices(100.0))["chart"] is None
    assert build_table("search_supplier_invoices", invoices(30.0, 30.0, 30.0))["chart"] is None
    assert build_table("search_supplier_invoices", invoices(0.0, 0.0))["chart"] is None
    mixed = invoices(100.0, 200.0)
    mixed["invoices"][1]["currency"] = "EUR"
    assert build_table("search_supplier_invoices", mixed)["chart"] is None
    assert build_chart({"kind": "purchase_order_search", "rows": [{"po_number": "1"}, {"po_number": "2"}]}) is None


def test_overdue_orders_by_days_late():
    result = {"source": "S4", "overdue_schedule_lines_in_window": 3,
              "orders": [{"po_number": "4500000010", "supplier": "EV Parts", "days_overdue": 28},
                         {"po_number": "4500000011", "supplier": "Bike Parts", "days_overdue": 5},
                         {"po_number": "4500000012", "supplier": "Bike Parts", "days_overdue": None}]}
    chart = build_table("get_overdue_orders", result)["chart"]
    assert chart["value_type"] == "days" and chart["action"] == "purchase_order"
    assert [(p["label"], p["values"]) for p in chart["points"]] == [("4500000010", [28.0]), ("4500000011", [5.0])]


def test_three_way_match_compares_ordered_received_invoiced_per_item():
    result = {"source": "S4", "po_number": "4500043954", "supplier": "Infinity Inc", "match_status": "VARIANCE",
              "items": [{"item": "10", "description": "Bike", "ordered_quantity": 300, "received_quantity": 295,
                         "invoiced_quantity": 826, "unit": "PC", "match_status": "QUANTITY_VARIANCE"}]}
    chart = build_table("match_purchase_order_invoices", result)["chart"]
    assert chart["type"] == "grouped" and chart["value_type"] == "quantity"
    assert chart["series"] == ["ordered_quantity", "received_quantity", "invoiced_quantity"]
    assert chart["points"] == [{"label": "10", "detail": "Bike", "key": None, "values": [300.0, 295.0, 826.0],
                                "unit": "PC"}]
    assert chart["action"] is None and chart["sorted"] is False


def test_document_items_keep_their_order():
    result = {"source": "S4", "po_number": "4500000010", "supplier": "EV Parts", "currency": "EUR",
              "items": [{"item": "10", "material": "A", "net_amount": 100},
                        {"item": "20", "material": "B", "net_amount": 900}]}
    chart = build_table("get_purchase_order", result)["chart"]
    assert [p["label"] for p in chart["points"]] == ["10", "20"] and chart["points"][1]["unit"] == "EUR"
