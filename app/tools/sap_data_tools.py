"""Agent tools for SAP transactional data, delegating to the configured data source."""

from app.sap_data import get_data_source


def get_purchase_order(po_number: str):
    return get_data_source().get_purchase_order(po_number)


def get_overdue_orders(days_back: int = 30, limit: int = 10):
    return get_data_source().get_overdue_orders(days_back=days_back, limit=limit)


def search_purchase_orders(supplier: str | None = None, date_from: str | None = None,
                           date_to: str | None = None, limit: int = 10):
    return get_data_source().search_purchase_orders(
        supplier=supplier, date_from=date_from, date_to=date_to, limit=limit
    )
