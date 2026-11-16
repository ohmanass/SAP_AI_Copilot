"""Agent tools for SAP transactional data, delegating to the configured data source."""

from app.sap_data import get_data_source

_sales_source = None

SALES_UNAVAILABLE = {
    "error": "Sales order data is only available with SAP_DATA_SOURCE=odata "
             "(SAP S/4HANA Cloud). The local demo dataset has no sales orders."
}


def get_sales_source():
    """Sales order source, or None when SAP S/4HANA is not the configured data source."""
    global _sales_source

    if get_data_source().key != "odata":
        return None
    if _sales_source is None:
        from app.sap_data.sales_source import S4HanaSalesSource
        _sales_source = S4HanaSalesSource()
    return _sales_source


def get_sales_order(sales_order: str):
    source = get_sales_source()
    return source.get_sales_order(sales_order) if source else SALES_UNAVAILABLE


def search_sales_orders(customer: str | None = None, date_from: str | None = None,
                        date_to: str | None = None, limit: int = 10):
    source = get_sales_source()
    if not source:
        return SALES_UNAVAILABLE
    return source.search_sales_orders(customer=customer, date_from=date_from, date_to=date_to, limit=limit)


def get_late_sales_orders(days_back: int = 30, limit: int = 10):
    source = get_sales_source()
    return source.get_late_sales_orders(days_back=days_back, limit=limit) if source else SALES_UNAVAILABLE


def get_purchase_order(po_number: str):
    return get_data_source().get_purchase_order(po_number)


def get_overdue_orders(days_back: int = 30, limit: int = 10):
    return get_data_source().get_overdue_orders(days_back=days_back, limit=limit)


def search_purchase_orders(supplier: str | None = None, date_from: str | None = None,
                           date_to: str | None = None, limit: int = 10):
    return get_data_source().search_purchase_orders(
        supplier=supplier, date_from=date_from, date_to=date_to, limit=limit
    )


# ----------------------------------------------------------------------
# Finance (SAP FI): supplier invoices and three-way match
# ----------------------------------------------------------------------

_finance_source = None

FINANCE_UNAVAILABLE = {
    "error": "Supplier invoice data is only available with SAP_DATA_SOURCE=odata "
             "(SAP S/4HANA Cloud). The local demo dataset has no invoices or goods receipts."
}


def get_finance_source():
    """Supplier invoice source, or None when SAP S/4HANA is not the configured data source."""
    global _finance_source

    if get_data_source().key != "odata":
        return None
    if _finance_source is None:
        from app.sap_data.finance_source import S4HanaFinanceSource
        _finance_source = S4HanaFinanceSource(get_data_source())
    return _finance_source


def get_supplier_invoice(invoice: str, fiscal_year: str | None = None):
    source = get_finance_source()
    return source.get_supplier_invoice(invoice, fiscal_year) if source else FINANCE_UNAVAILABLE


def search_supplier_invoices(supplier: str | None = None, date_from: str | None = None,
                             date_to: str | None = None, blocked_only: bool = False, limit: int = 10):
    source = get_finance_source()
    if not source:
        return FINANCE_UNAVAILABLE
    return source.search_supplier_invoices(supplier=supplier, date_from=date_from, date_to=date_to,
                                           blocked_only=blocked_only, limit=limit)


def match_purchase_order_invoices(po_number: str):
    source = get_finance_source()
    return source.match_purchase_order_invoices(po_number) if source else FINANCE_UNAVAILABLE
