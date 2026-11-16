"""
Data tables built from transactional tool results.

The frontend shows them as SAP Fiori tables under the answer. They are built
deterministically from what the SAP tools returned, never from the model's
text, so every value displayed is a value read from the SAP data source.

Table:
    {
        "kind": str,                       title key for the frontend (i18n)
        "title_params": [str, ...],        values inserted in the title
        "source": str | None,              system the data comes from
        "attributes": [{"key", "type", "value", "unit"?}],   document header
        "columns": [{"key", "type", "importance", "unit_key"?}],
        "rows": [{...}],
        "total": int | None,               total matching records, when known
        "total_unit": str,                 what "total" counts: "orders", "schedule_lines" or "invoices"
        "window": str | None,              time window of the query, when limited
        "row_action": str | None           "purchase_order", "sales_order", "supplier_invoice" or
                                           "three_way_match": what a row press asks for
        "row_key": str | None              row field holding the document number
    }

Column types: id, text, date, number, quantity, amount, status, days, block (payment block code).
Columns absent from every row are dropped: the demo dataset and SAP S/4HANA
do not return the same fields.
"""

MAX_ROWS = 50


def _col(key, type_="text", importance="high", unit_key=None):
    column = {"key": key, "type": type_, "importance": importance}
    if unit_key:
        column["unit_key"] = unit_key
    return column


# Column layouts per tool. "importance: low" columns move into a popin on small screens.
PO_ITEM_COLUMNS = (
    _col("item", "id"),
    _col("material", "id"),
    _col("description"),
    _col("plant", "id", "low"),
    _col("quantity", "quantity", unit_key="unit"),
    _col("open_quantity", "quantity", unit_key="unit"),
    _col("unit_price", "amount", "low", unit_key="currency"),
    _col("net_amount", "amount", unit_key="currency"),
    _col("delivery_date", "date")
)

PO_LIST_COLUMNS = (
    _col("po_number", "id"),
    _col("supplier"),
    _col("supplier_country", "text", "low"),
    _col("order_date", "date"),
    _col("delivery_date", "date"),
    _col("days_overdue", "days"),
    _col("open_items", "number", "low"),
    _col("open_quantity", "quantity", unit_key="unit"),
    _col("status", "status")
)

SO_ITEM_COLUMNS = (
    _col("item", "id"),
    _col("product", "id"),
    _col("description"),
    _col("quantity", "quantity", unit_key="unit"),
    _col("delivered_quantity", "quantity", "low", unit_key="unit"),
    _col("open_confirmed_quantity", "quantity", "low", unit_key="unit"),
    _col("net_amount", "amount", unit_key="currency"),
    _col("confirmed_delivery_date", "date"),
    _col("delivery_status", "status")
)

SO_LIST_COLUMNS = (
    _col("sales_order", "id"),
    _col("customer"),
    _col("order_type", "id", "low"),
    _col("order_date", "date", "low"),
    _col("requested_delivery_date", "date"),
    _col("days_late", "days"),
    _col("net_amount", "amount", unit_key="currency"),
    _col("delivery_status", "status")
)


INVOICE_ITEM_COLUMNS = (
    _col("item", "id"),
    _col("po_number", "id"),
    _col("po_item", "id"),
    _col("plant", "id", "low"),
    _col("quantity", "quantity", unit_key="unit"),
    _col("amount", "amount", unit_key="currency"),
    _col("goods_receipt", "id", "low")
)

INVOICE_LIST_COLUMNS = (
    _col("invoice", "id"),
    _col("supplier"),
    _col("supplier_reference", "id", "low"),
    _col("invoice_date", "date", "low"),
    _col("posting_date", "date"),
    _col("gross_amount", "amount", unit_key="currency"),
    _col("payment_block", "block"),
    _col("status", "status")
)

MATCH_COLUMNS = (
    _col("item", "id"),
    _col("material", "id", "low"),
    _col("description"),
    _col("ordered_quantity", "quantity", unit_key="unit"),
    _col("received_quantity", "quantity", unit_key="unit"),
    _col("invoiced_quantity", "quantity", unit_key="unit"),
    _col("invoiced_amount", "amount", "low", unit_key="currency"),
    _col("price_difference", "amount", unit_key="currency"),
    _col("gr_ir_balance", "amount", "low", unit_key="currency"),
    _col("match_status", "status")
)


def _present(value):
    return value is not None and value != ""


def _columns_with_data(columns, rows):
    return [dict(c) for c in columns if any(_present(row.get(c["key"])) for row in rows)]


def _rows(columns, records, extra=None):
    keys = {c["key"] for c in columns} | {c.get("unit_key") for c in columns if c.get("unit_key")}
    rows = []
    for record in records[:MAX_ROWS]:
        row = {key: record.get(key) for key in keys if key in record}
        row.update(extra or {})
        rows.append(row)
    return rows


def _attributes(result, specs):
    attributes = []
    for key, type_, unit_key in specs:
        value = result.get(key)
        if _present(value):
            attribute = {"key": key, "type": type_, "value": value}
            if unit_key and _present(result.get(unit_key)):
                attribute["unit"] = result.get(unit_key)
            attributes.append(attribute)
    return attributes


def _table(kind, title_params, result, columns, records, *, attributes=None, total=None,
           total_unit="orders", row_action=None, row_key=None, extra=None):
    rows = _rows(columns, records, extra)
    return {
        "kind": kind,
        "title_params": [str(p) for p in title_params if _present(p)],
        "source": result.get("source"),
        "attributes": attributes or [],
        "columns": _columns_with_data(columns, rows),
        "rows": rows,
        "total": total,
        "total_unit": total_unit,
        "window": result.get("window"),
        "row_action": row_action,
        "row_key": row_key
    }


def _purchase_order(result):
    items = result.get("items") or []
    # Item amounts are in the document currency
    extra = {"currency": result["currency"]} if _present(result.get("currency")) else None
    return _table(
        "purchase_order", [result.get("po_number"), result.get("supplier")], result,
        PO_ITEM_COLUMNS, items, extra=extra,
        attributes=_attributes(result, (
            ("supplier", "text", None),
            ("supplier_country", "text", None),
            ("order_date", "date", None),
            ("delivery_date", "date", None),
            ("status", "status", None),
            ("total_net_amount", "amount", "currency")
        ))
    )


def _purchase_order_list(kind):
    def build(result):
        orders = result.get("orders") or []
        # The overdue analysis counts schedule lines, not orders
        if "overdue_schedule_lines_in_window" in result:
            total, unit = result["overdue_schedule_lines_in_window"], "schedule_lines"
        else:
            total, unit = result.get("total_matching"), "orders"
        return _table(kind, [], result, PO_LIST_COLUMNS, orders, total=total, total_unit=unit,
                      row_action="purchase_order", row_key="po_number")
    return build


def _sales_order(result):
    items = result.get("items") or []
    extra = {"currency": result["currency"]} if _present(result.get("currency")) else None
    return _table(
        "sales_order", [result.get("sales_order"), result.get("customer")], result,
        SO_ITEM_COLUMNS, items, extra=extra,
        attributes=_attributes(result, (
            ("customer", "text", None),
            ("customer_reference", "id", None),
            ("order_date", "date", None),
            ("requested_delivery_date", "date", None),
            ("processing_status", "status", None),
            ("delivery_status", "status", None),
            ("delivery_block", "text", None),
            ("billing_block", "text", None),
            ("total_net_amount", "amount", "currency")
        ))
    )


def _sales_order_list(kind):
    def build(result):
        orders = result.get("orders") or []
        total = result.get("total_matching", result.get("late_orders_in_window"))
        return _table(kind, [], result, SO_LIST_COLUMNS, orders, total=total,
                      row_action="sales_order", row_key="sales_order")
    return build


def _supplier_invoice(result):
    return _table(
        "supplier_invoice", [result.get("invoice"), result.get("supplier")], result,
        INVOICE_ITEM_COLUMNS, result.get("items") or [],
        row_action="three_way_match", row_key="po_number",
        attributes=_attributes(result, (
            ("supplier", "text", None),
            ("supplier_reference", "id", None),
            ("invoice_date", "date", None),
            ("posting_date", "date", None),
            ("baseline_date", "date", None),
            ("payment_terms", "id", None),
            ("status", "status", None),
            ("payment_status", "text", None),
            ("payment_block", "block", None),
            ("gross_amount", "amount", "currency")
        ))
    )


def _supplier_invoice_list(result):
    kind = "blocked_supplier_invoices" if result.get("filter") else "supplier_invoice_search"
    return _table(kind, [], result, INVOICE_LIST_COLUMNS, result.get("invoices") or [],
                  total=result.get("total_matching"), total_unit="invoices",
                  row_action="supplier_invoice", row_key="invoice")


def _three_way_match(result):
    return _table(
        "three_way_match", [result.get("po_number"), result.get("supplier")], result,
        MATCH_COLUMNS, result.get("items") or [],
        attributes=_attributes(result, (
            ("supplier", "text", None),
            ("match_status", "status", None),
            ("ordered_amount", "amount", "currency"),
            ("invoiced_amount", "amount", "currency"),
            ("gr_ir_balance", "amount", "currency")
        ))
    )


BUILDERS = {
    "get_purchase_order": _purchase_order,
    "get_overdue_orders": _purchase_order_list("overdue_purchase_orders"),
    "search_purchase_orders": _purchase_order_list("purchase_order_search"),
    "get_sales_order": _sales_order,
    "search_sales_orders": _sales_order_list("sales_order_search"),
    "get_late_sales_orders": _sales_order_list("late_sales_orders"),
    "get_supplier_invoice": _supplier_invoice,
    "search_supplier_invoices": _supplier_invoice_list,
    "match_purchase_order_invoices": _three_way_match
}


def build_table(tool_name, result):
    """Table for a transactional tool result, or None (other tool, error, nothing to show)."""

    builder = BUILDERS.get(tool_name)
    if not builder or not isinstance(result, dict) or result.get("error"):
        return None

    table = builder(result)
    if not table["rows"] and not table["attributes"]:
        return None
    return table


def collect_table(tool_name, result, tables):
    """Append the table for a tool result, skipping an identical one already shown."""

    table = build_table(tool_name, result)
    if table and table not in tables:
        tables.append(table)
