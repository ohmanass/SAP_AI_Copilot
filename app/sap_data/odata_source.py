"""
SAP S/4HANA Cloud data source (OData V4, API_PURCHASEORDER_2), read-only.

Defaults to the SAP Business Accelerator Hub sandbox. Any S/4HANA Cloud
system exposing the same API can be used by changing SAP_API_BASE_URL.
"""

import os
import re
import time
from datetime import date, timedelta

from app.sap_data.odata_client import ODataClient, SAPODataError

DEFAULT_BASE_URL = "https://sandbox.api.sap.com/s4hanacloud"
PURCHASE_ORDER_SERVICE = "sap/opu/odata4/sap/api_purchaseorder_2/srvd_a2x/sap/purchaseorder/0001/"

# OData V4 requires a unit when filtering on a quantity (SAP rule /IWBEP/CM_V4S_RUN/031)
DEFAULT_QUANTITY_UNITS = "PC,EA"

MAX_LIMIT = 50


def _literal(value: str) -> str:
    """OData string literal with escaped quotes."""
    return "'" + str(value).replace("'", "''") + "'"


def _valid_date(value: str | None) -> str | None:
    if value and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    return None


class S4HanaODataSource:

    key = "odata"

    def __init__(self):
        base_url = os.getenv("SAP_API_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        self.base_url = base_url
        self.is_sandbox = "sandbox.api.sap.com" in base_url
        self.client = ODataClient(f"{base_url}/{PURCHASE_ORDER_SERVICE}")
        self.units = [
            unit.strip() for unit in
            os.getenv("SAP_QUANTITY_UNITS", DEFAULT_QUANTITY_UNITS).split(",") if unit.strip()
        ]

    @property
    def label(self):
        return "SAP S/4HANA Cloud sandbox" if self.is_sandbox else "SAP S/4HANA Cloud"

    def describe(self):
        return {
            "key": self.key,
            "label": self.label,
            "system": self.base_url,
            "api": "API_PURCHASEORDER_2 (OData V4)",
            "configured": self.client.configured
        }

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def get_purchase_order(self, po_number: str):

        po_number = str(po_number).strip()
        if not re.fullmatch(r"\d{1,10}", po_number):
            return {"error": f"Invalid purchase order number: {po_number}"}

        try:
            order = self.client.get(f"PurchaseOrder({_literal(po_number)})", {
                "$select": "PurchaseOrder,PurchaseOrderType,PurchaseOrderDate,Supplier,CompanyCode,"
                           "PurchasingOrganization,PurchasingGroup,DocumentCurrency,PaymentTerms,"
                           "IncotermsClassification",
                "$expand": "_SupplierAddress($select=OrganizationName1,CityName,Country),"
                           "_PurchaseOrderItem($select=PurchaseOrderItem,Material,PurchaseOrderItemText,"
                           "Plant,OrderQuantity,PurchaseOrderQuantityUnit,NetPriceAmount,NetAmount,"
                           "IsCompletelyDelivered,PurchasingDocumentDeletionCode;"
                           "$expand=_PurchaseOrderScheduleLineTP($select=ScheduleLine,"
                           "ScheduleLineDeliveryDate,ScheduleLineOrderQuantity,OpenPurchaseOrderQuantity))"
            })
        except SAPODataError as error:
            if "404" in str(error):
                return {"error": f"Purchase order {po_number} not found"}
            return {"error": str(error)}

        today = date.today().isoformat()
        items = []
        for item in order.get("_PurchaseOrderItem", []):
            lines = item.get("_PurchaseOrderScheduleLineTP", [])
            open_quantity = sum(line.get("OpenPurchaseOrderQuantity") or 0 for line in lines)
            delivery_dates = sorted(
                line["ScheduleLineDeliveryDate"] for line in lines if line.get("ScheduleLineDeliveryDate")
            )
            items.append({
                "item": item.get("PurchaseOrderItem"),
                "material": item.get("Material"),
                "description": item.get("PurchaseOrderItemText"),
                "plant": item.get("Plant"),
                "quantity": item.get("OrderQuantity"),
                "unit": item.get("PurchaseOrderQuantityUnit"),
                "unit_price": item.get("NetPriceAmount"),
                "net_amount": item.get("NetAmount"),
                "delivery_date": delivery_dates[0] if delivery_dates else None,
                "open_quantity": open_quantity,
                "completely_delivered": bool(item.get("IsCompletelyDelivered")),
                "deleted": bool(item.get("PurchasingDocumentDeletionCode"))
            })

        active = [i for i in items if not i["deleted"]]
        status = self._order_status(active, today)
        supplier = order.get("_SupplierAddress") or {}
        all_dates = [i["delivery_date"] for i in active if i["delivery_date"]]

        return {
            "source": self.label,
            "po_number": order.get("PurchaseOrder"),
            "order_type": order.get("PurchaseOrderType"),
            "order_date": order.get("PurchaseOrderDate"),
            "delivery_date": min(all_dates) if all_dates else None,
            "status": status,
            "supplier_id": order.get("Supplier"),
            "supplier": supplier.get("OrganizationName1"),
            "supplier_city": supplier.get("CityName"),
            "supplier_country": supplier.get("Country"),
            "company_code": order.get("CompanyCode"),
            "purchasing_organization": order.get("PurchasingOrganization"),
            "purchasing_group": order.get("PurchasingGroup"),
            "currency": order.get("DocumentCurrency"),
            "payment_terms": order.get("PaymentTerms"),
            "incoterms": order.get("IncotermsClassification"),
            "total_net_amount": round(sum(i["net_amount"] or 0 for i in active), 2),
            "items": items
        }

    @staticmethod
    def _order_status(items, today):
        if not items:
            return "NO_ACTIVE_ITEMS"
        if all(i["completely_delivered"] or i["open_quantity"] == 0 for i in items):
            return "RECEIVED"
        overdue = any(
            i["open_quantity"] > 0 and i["delivery_date"] and i["delivery_date"] < today for i in items
        )
        partially = any(i["open_quantity"] < (i["quantity"] or 0) for i in items)
        if overdue:
            return "OVERDUE"
        return "PARTIALLY_RECEIVED" if partially else "OPEN"

    def get_overdue_orders(self, days_back: int = 30, limit: int = 10):

        days_back = max(1, min(int(days_back or 30), 365))
        limit = max(1, min(int(limit or 10), MAX_LIMIT))
        today = date.today()
        since = today - timedelta(days=days_back)

        open_filter = " or ".join(
            f"(PurchaseOrderQuantityUnit eq {_literal(unit)} and OpenPurchaseOrderQuantity gt 0)"
            for unit in self.units
        )

        try:
            lines = self.client.get("PurchaseOrderScheduleLine", {
                "$filter": f"ScheduleLineDeliveryDate lt {today.isoformat()} and "
                           f"ScheduleLineDeliveryDate ge {since.isoformat()} and ({open_filter})",
                "$select": "PurchaseOrder,PurchaseOrderItem,ScheduleLineDeliveryDate,"
                           "OpenPurchaseOrderQuantity,PurchaseOrderQuantityUnit",
                "$orderby": "ScheduleLineDeliveryDate asc",
                "$count": "true",
                "$top": 500
            })
        except SAPODataError as error:
            return {"error": str(error)}

        # Group schedule lines by purchase order
        orders = {}
        for line in lines.get("value", []):
            po = line["PurchaseOrder"]
            entry = orders.setdefault(po, {
                "po_number": po,
                "delivery_date": line["ScheduleLineDeliveryDate"],
                "open_items": set(),
                "open_quantity": 0,
                "unit": line.get("PurchaseOrderQuantityUnit")
            })
            entry["delivery_date"] = min(entry["delivery_date"], line["ScheduleLineDeliveryDate"])
            entry["open_items"].add(line["PurchaseOrderItem"])
            entry["open_quantity"] += line.get("OpenPurchaseOrderQuantity") or 0

        selected = sorted(orders.values(), key=lambda o: o["delivery_date"])[:limit]
        suppliers = self._supplier_names([o["po_number"] for o in selected])

        result_orders = []
        for order in selected:
            info = suppliers.get(order["po_number"], {})
            result_orders.append({
                "po_number": order["po_number"],
                "supplier_id": info.get("supplier_id"),
                "supplier": info.get("supplier"),
                "delivery_date": order["delivery_date"],
                "days_overdue": (today - date.fromisoformat(order["delivery_date"])).days,
                "open_items": len(order["open_items"]),
                "open_quantity": order["open_quantity"],
                "unit": order["unit"],
                "status": "OVERDUE"
            })

        return {
            "source": self.label,
            "window": f"delivery dates from {since.isoformat()} to {today.isoformat()}",
            "overdue_schedule_lines_in_window": lines.get("@odata.count"),
            "orders_returned": len(result_orders),
            "quantity_units_checked": self.units,
            "orders": result_orders
        }

    def _supplier_names(self, po_numbers):
        if not po_numbers:
            return {}
        try:
            body = self.client.get("PurchaseOrder", {
                "$filter": " or ".join(f"PurchaseOrder eq {_literal(po)}" for po in po_numbers),
                "$select": "PurchaseOrder,Supplier",
                "$expand": "_SupplierAddress($select=OrganizationName1)",
                "$top": len(po_numbers)
            })
        except SAPODataError:
            return {}
        return {
            row["PurchaseOrder"]: {
                "supplier_id": row.get("Supplier"),
                "supplier": (row.get("_SupplierAddress") or {}).get("OrganizationName1")
            }
            for row in body.get("value", [])
        }

    def search_purchase_orders(self, supplier: str | None = None, date_from: str | None = None,
                               date_to: str | None = None, limit: int = 10):

        limit = max(1, min(int(limit or 10), MAX_LIMIT))
        filters = []

        if supplier:
            supplier = supplier.strip()
            if re.fullmatch(r"[A-Z0-9_\-]{4,12}", supplier):
                filters.append(f"(Supplier eq {_literal(supplier)} or "
                               f"contains(_SupplierAddress/OrganizationName1,{_literal(supplier)}))")
            else:
                filters.append(f"contains(_SupplierAddress/OrganizationName1,{_literal(supplier)})")
        if _valid_date(date_from):
            filters.append(f"PurchaseOrderDate ge {date_from}")
        if _valid_date(date_to):
            filters.append(f"PurchaseOrderDate le {date_to}")

        params = {
            "$select": "PurchaseOrder,PurchaseOrderType,PurchaseOrderDate,Supplier,CompanyCode,DocumentCurrency",
            "$expand": "_SupplierAddress($select=OrganizationName1,Country)",
            "$orderby": "PurchaseOrderDate desc,PurchaseOrder desc",
            "$count": "true",
            "$top": limit
        }
        if filters:
            params["$filter"] = " and ".join(filters)

        try:
            body = self.client.get("PurchaseOrder", params)
        except SAPODataError as error:
            return {"error": str(error)}

        return {
            "source": self.label,
            "total_matching": body.get("@odata.count"),
            "orders_returned": len(body.get("value", [])),
            "orders": [
                {
                    "po_number": row.get("PurchaseOrder"),
                    "order_type": row.get("PurchaseOrderType"),
                    "order_date": row.get("PurchaseOrderDate"),
                    "supplier_id": row.get("Supplier"),
                    "supplier": (row.get("_SupplierAddress") or {}).get("OrganizationName1"),
                    "supplier_country": (row.get("_SupplierAddress") or {}).get("Country"),
                    "company_code": row.get("CompanyCode"),
                    "currency": row.get("DocumentCurrency")
                }
                for row in body.get("value", [])
            ]
        }

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def status(self):
        info = self.describe()
        if not self.client.configured:
            return {**info, "available": False, "error": "SAP_API_KEY missing", "latency_ms": None}

        started = time.perf_counter()
        try:
            body = self.client.get(
                "PurchaseOrder", {"$top": 1, "$select": "PurchaseOrder", "$count": "true"}, use_cache=False
            )
            return {
                **info,
                "available": True,
                "error": None,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "purchase_orders": body.get("@odata.count")
            }
        except SAPODataError as error:
            return {**info, "available": False, "error": str(error), "latency_ms": None}
