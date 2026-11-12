"""
SAP S/4HANA Cloud sales orders (OData V4, API_SALESORDER, Sales Order A2X), read-only.

Uses the same system and API key as the purchase order source
(SAP_API_BASE_URL, SAP_API_KEY). Sales data is only available with
SAP_DATA_SOURCE=odata: there is no SD demo dataset.
"""

import os
import re
import time
from datetime import date, timedelta

from app.sap_data.odata_client import ODataClient, SAPODataError
from app.sap_data.odata_source import DEFAULT_BASE_URL, MAX_LIMIT, _literal, _valid_date

SALES_ORDER_SERVICE = "sap/opu/odata4/sap/api_salesorder/srvd_a2x/sap/salesorder/0001/"

# SAP partner function codes (internal, German-based): AG = sold-to party
SOLD_TO = "AG"

CUSTOMER_CACHE_SECONDS = 600

DELIVERY_STATUS = {
    "A": "NOT_DELIVERED",
    "B": "PARTIALLY_DELIVERED",
    "C": "DELIVERED",
    "": "NOT_RELEVANT"
}

PROCESS_STATUS = {
    "A": "OPEN",
    "B": "IN_PROCESS",
    "C": "COMPLETED",
    "": "NOT_RELEVANT"
}


class S4HanaSalesSource:

    def __init__(self):
        base_url = os.getenv("SAP_API_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        self.is_sandbox = "sandbox.api.sap.com" in base_url
        self.label = "SAP S/4HANA Cloud sandbox" if self.is_sandbox else "SAP S/4HANA Cloud"
        self.client = ODataClient(f"{base_url}/{SALES_ORDER_SERVICE}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sold_to(order):
        for partner in order.get("_Partner") or []:
            if partner.get("PartnerFunction") == SOLD_TO:
                return partner
        return {}

    def _customer_names(self, sales_orders):
        """Sold-to party names for a list of sales order numbers."""
        if not sales_orders:
            return {}
        try:
            body = self.client.get("SalesOrderPartner", {
                "$filter": f"PartnerFunction eq '{SOLD_TO}' and (" +
                           " or ".join(f"SalesOrder eq {_literal(so)}" for so in sales_orders) + ")",
                "$select": "SalesOrder,Customer,BusinessPartnerName1,Country",
                "$top": len(sales_orders)
            })
        except SAPODataError:
            return {}
        return {row["SalesOrder"]: row for row in body.get("value", [])}

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def get_sales_order(self, sales_order: str):

        sales_order = str(sales_order).strip()
        if not re.fullmatch(r"\d{1,10}", sales_order):
            return {"error": f"Invalid sales order number: {sales_order}"}

        try:
            order = self.client.get(f"SalesOrder({_literal(sales_order)})", {
                "$select": "SalesOrder,SalesOrderType,SoldToParty,SalesOrganization,DistributionChannel,"
                           "OrganizationDivision,SalesOrderDate,RequestedDeliveryDate,PurchaseOrderByCustomer,"
                           "TotalNetAmount,TransactionCurrency,OverallSDProcessStatus,OverallDeliveryStatus,"
                           "DeliveryBlockReason,HeaderBillingBlockReason",
                "$expand": "_Partner($select=PartnerFunction,Customer,BusinessPartnerName1,CityName,Country),"
                           "_Item($select=SalesOrderItem,Product,SalesOrderItemText,RequestedQuantity,"
                           "RequestedQuantitySAPUnit,NetAmount,DeliveryStatus,SDDocumentRejectionStatus;"
                           "$expand=_ScheduleLine($select=ScheduleLine,RequestedDeliveryDate,ConfirmedDeliveryDate,"
                           "ScheduleLineOrderQuantity,DeliveredQtyInOrderQtyUnit,OpenConfdDelivQtyInOrdQtyUnit))"
            })
        except SAPODataError as error:
            if "404" in str(error):
                return {"error": f"Sales order {sales_order} not found"}
            return {"error": str(error)}

        items = []
        for item in order.get("_Item") or []:
            lines = item.get("_ScheduleLine") or []
            confirmed = sorted(l["ConfirmedDeliveryDate"] for l in lines if l.get("ConfirmedDeliveryDate"))
            items.append({
                "item": item.get("SalesOrderItem"),
                "product": item.get("Product"),
                "description": item.get("SalesOrderItemText"),
                "quantity": item.get("RequestedQuantity"),
                "unit": item.get("RequestedQuantitySAPUnit"),
                "net_amount": item.get("NetAmount"),
                "delivery_status": DELIVERY_STATUS.get(item.get("DeliveryStatus") or "", item.get("DeliveryStatus")),
                "confirmed_delivery_date": confirmed[0] if confirmed else None,
                "delivered_quantity": sum(l.get("DeliveredQtyInOrderQtyUnit") or 0 for l in lines),
                "open_confirmed_quantity": sum(l.get("OpenConfdDelivQtyInOrdQtyUnit") or 0 for l in lines)
            })

        customer = self._sold_to(order)
        today = date.today().isoformat()
        delivery_status = DELIVERY_STATUS.get(order.get("OverallDeliveryStatus") or "", order.get("OverallDeliveryStatus"))
        requested = order.get("RequestedDeliveryDate")

        return {
            "source": self.label,
            "sales_order": order.get("SalesOrder"),
            "order_type": order.get("SalesOrderType"),
            "order_date": order.get("SalesOrderDate"),
            "requested_delivery_date": requested,
            "is_late": bool(requested and requested < today and delivery_status in ("NOT_DELIVERED", "PARTIALLY_DELIVERED")),
            "customer_id": order.get("SoldToParty"),
            "customer": customer.get("BusinessPartnerName1"),
            "customer_city": customer.get("CityName"),
            "customer_country": customer.get("Country"),
            "customer_reference": order.get("PurchaseOrderByCustomer"),
            "sales_organization": order.get("SalesOrganization"),
            "distribution_channel": order.get("DistributionChannel"),
            "division": order.get("OrganizationDivision"),
            "processing_status": PROCESS_STATUS.get(order.get("OverallSDProcessStatus") or "", order.get("OverallSDProcessStatus")),
            "delivery_status": delivery_status,
            "delivery_block": order.get("DeliveryBlockReason") or None,
            "billing_block": order.get("HeaderBillingBlockReason") or None,
            "total_net_amount": order.get("TotalNetAmount"),
            "currency": order.get("TransactionCurrency"),
            "items": items
        }

    def search_sales_orders(self, customer: str | None = None, date_from: str | None = None,
                            date_to: str | None = None, limit: int = 10):

        limit = max(1, min(int(limit or 10), MAX_LIMIT))
        filters = []
        matched_customers = None

        if customer:
            customer = customer.strip()
            if re.fullmatch(r"[A-Z0-9_\-]{4,12}", customer):
                ids = [customer]
            else:
                # The API cannot filter on partner names (HTTP 501): resolve them to IDs first
                matched_customers = self._resolve_customers(customer)
                ids = list(matched_customers)
                if not ids:
                    return {
                        "source": self.label,
                        "total_matching": 0,
                        "orders_returned": 0,
                        "orders": [],
                        "note": f"No customer matching '{customer}' among recent sales orders."
                    }
            filters.append("(" + " or ".join(f"SoldToParty eq {_literal(i)}" for i in ids) + ")")
        if _valid_date(date_from):
            filters.append(f"SalesOrderDate ge {date_from}")
        if _valid_date(date_to):
            filters.append(f"SalesOrderDate le {date_to}")

        params = {
            "$select": "SalesOrder,SalesOrderType,SoldToParty,SalesOrderDate,RequestedDeliveryDate,"
                       "TotalNetAmount,TransactionCurrency,OverallDeliveryStatus",
            "$orderby": "SalesOrderDate desc,SalesOrder desc",
            "$count": "true",
            "$top": limit
        }
        if filters:
            params["$filter"] = " and ".join(filters)

        try:
            body = self.client.get("SalesOrder", params)
        except SAPODataError as error:
            return {"error": str(error)}

        rows = body.get("value", [])
        names = self._customer_names([row["SalesOrder"] for row in rows])

        result = {
            "source": self.label,
            "total_matching": body.get("@odata.count"),
            "orders_returned": len(rows),
            "orders": [
                {
                    "sales_order": row.get("SalesOrder"),
                    "order_type": row.get("SalesOrderType"),
                    "order_date": row.get("SalesOrderDate"),
                    "requested_delivery_date": row.get("RequestedDeliveryDate"),
                    "customer_id": row.get("SoldToParty"),
                    "customer": names.get(row["SalesOrder"], {}).get("BusinessPartnerName1"),
                    "net_amount": row.get("TotalNetAmount"),
                    "currency": row.get("TransactionCurrency"),
                    "delivery_status": DELIVERY_STATUS.get(row.get("OverallDeliveryStatus") or "",
                                                           row.get("OverallDeliveryStatus"))
                }
                for row in rows
            ]
        }
        if matched_customers:
            result["matched_customers"] = [
                {"customer_id": cid, "customer": name} for cid, name in matched_customers.items()
            ]
        return result

    def _resolve_customers(self, name, max_matches=10):
        """
        Customer IDs whose sold-to name contains `name` (case-insensitive),
        among the sold-to parties of the most recent sales orders. Cached 10 min.
        """
        now = time.monotonic()
        cache = getattr(self, "_customer_cache", None)
        if not cache or now - cache[0] > CUSTOMER_CACHE_SECONDS:
            try:
                body = self.client.get("SalesOrderPartner", {
                    "$filter": f"PartnerFunction eq '{SOLD_TO}'",
                    "$select": "Customer,BusinessPartnerName1",
                    "$orderby": "SalesOrder desc",
                    "$top": 2000
                })
            except SAPODataError:
                return {}
            customers = {}
            for row in body.get("value", []):
                if row.get("Customer") and row.get("BusinessPartnerName1"):
                    customers[row["Customer"]] = row["BusinessPartnerName1"]
            self._customer_cache = (now, customers)

        needle = name.lower()
        matches = {cid: cname for cid, cname in self._customer_cache[1].items() if needle in cname.lower()}
        return dict(sorted(matches.items())[:max_matches])

    def get_late_sales_orders(self, days_back: int = 30, limit: int = 10):

        days_back = max(1, min(int(days_back or 30), 365))
        limit = max(1, min(int(limit or 10), MAX_LIMIT))
        today = date.today()
        since = today - timedelta(days=days_back)

        try:
            body = self.client.get("SalesOrder", {
                "$filter": f"RequestedDeliveryDate lt {today.isoformat()} and "
                           f"RequestedDeliveryDate ge {since.isoformat()} and "
                           "(OverallDeliveryStatus eq 'A' or OverallDeliveryStatus eq 'B')",
                "$select": "SalesOrder,SoldToParty,SalesOrderDate,RequestedDeliveryDate,"
                           "TotalNetAmount,TransactionCurrency,OverallDeliveryStatus",
                "$orderby": "RequestedDeliveryDate asc,SalesOrder asc",
                "$count": "true",
                "$top": limit
            })
        except SAPODataError as error:
            return {"error": str(error)}

        rows = body.get("value", [])
        names = self._customer_names([row["SalesOrder"] for row in rows])

        return {
            "source": self.label,
            "window": f"requested delivery dates from {since.isoformat()} to {today.isoformat()}",
            "late_orders_in_window": body.get("@odata.count"),
            "orders_returned": len(rows),
            "orders": [
                {
                    "sales_order": row.get("SalesOrder"),
                    "customer_id": row.get("SoldToParty"),
                    "customer": names.get(row["SalesOrder"], {}).get("BusinessPartnerName1"),
                    "order_date": row.get("SalesOrderDate"),
                    "requested_delivery_date": row.get("RequestedDeliveryDate"),
                    "days_late": (today - date.fromisoformat(row["RequestedDeliveryDate"])).days,
                    "net_amount": row.get("TotalNetAmount"),
                    "currency": row.get("TransactionCurrency"),
                    "delivery_status": DELIVERY_STATUS.get(row.get("OverallDeliveryStatus") or "",
                                                           row.get("OverallDeliveryStatus"))
                }
                for row in rows
            ]
        }

    def status(self):
        started = time.perf_counter()
        try:
            body = self.client.get("SalesOrder", {"$top": 1, "$select": "SalesOrder", "$count": "true"},
                                   use_cache=False)
            return {"available": True, "latency_ms": round((time.perf_counter() - started) * 1000),
                    "sales_orders": body.get("@odata.count"), "error": None}
        except SAPODataError as error:
            return {"available": False, "latency_ms": None, "sales_orders": None, "error": str(error)}
