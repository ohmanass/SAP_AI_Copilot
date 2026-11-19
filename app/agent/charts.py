"""
Charts shown with the SAP data tables.

Like the tables (app.agent.data_tables), a chart is built from the rows the SAP
tools returned, never from the model's text: every bar is a value read in SAP.
Only charts that tell something are built: at least two bars (or the three
quantities of a three-way match), a value other than zero, one currency.

Chart:
    {
        "type": "bars" | "grouped",       one value per bar, or several series side by side
        "value_type": "days" | "amount" | "quantity",
        "series": [column key, ...],      what each value is (i18n: dataCol.<key>)
        "sorted": bool,                   bars ordered by value (largest first)
        "points": [{"label", "detail", "key", "values": [...], "unit"}],
        "action": str | None,             row action of the table: a bar press asks for "key"
        "shown": int, "total": int        bars shown / rows available
    }
"""

MAX_BARS = 10

# Table kind -> (series, value type, sorted by value, label column, detail column, unit column)
SPECS = {
    "overdue_purchase_orders": (("days_overdue",), "days", True, "po_number", "supplier", None),
    "late_sales_orders": (("days_late",), "days", True, "sales_order", "customer", None),
    "sales_order_search": (("net_amount",), "amount", True, "sales_order", "customer", "currency"),
    "supplier_invoice_search": (("gross_amount",), "amount", True, "invoice", "supplier", "currency"),
    "blocked_supplier_invoices": (("gross_amount",), "amount", True, "invoice", "supplier", "currency"),
    "purchase_order": (("net_amount",), "amount", False, "item", "material", "currency"),
    "sales_order": (("net_amount",), "amount", False, "item", "product", "currency"),
    "supplier_invoice": (("amount",), "amount", False, "item", "po_number", "currency"),
    "three_way_match": (("ordered_quantity", "received_quantity", "invoiced_quantity"), "quantity", False,
                        "item", "description", "unit"),
}


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_chart(table):
    """Chart for a data table, or None when there is nothing worth drawing."""

    spec = SPECS.get(table.get("kind"))
    if not spec:
        return None
    series, value_type, by_value, label_key, detail_key, unit_key = spec

    points = []
    for row in table.get("rows", []):
        values = [_number(row.get(key)) for key in series]
        if any(value is None for value in values):
            continue
        points.append({
            "label": str(row.get(label_key) or ""),
            "detail": str(row.get(detail_key) or ""),
            "key": str(row[table["row_key"]]) if table.get("row_key") and row.get(table["row_key"]) else None,
            "values": values,
            "unit": row.get(unit_key) if unit_key else None,
        })

    grouped = len(series) > 1
    if len(points) < (1 if grouped else 2) or not any(any(p["values"]) for p in points):
        return None
    # Bars all equal (ten orders all 30 days late): the table already says it
    if not grouped and len({p["values"][0] for p in points}) == 1:
        return None
    # One scale: amounts must share their currency (quantities are compared within an item)
    if value_type == "amount" and len({p["unit"] for p in points}) > 1:
        return None

    total = len(points)
    if by_value:
        points.sort(key=lambda p: p["values"][0], reverse=True)
    points = points[:MAX_BARS]
    return {
        "type": "grouped" if grouped else "bars",
        "value_type": value_type,
        "series": list(series),
        "sorted": by_value,
        "points": points,
        "action": table.get("row_action"),
        "shown": len(points),
        "total": total,
    }
