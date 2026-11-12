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
