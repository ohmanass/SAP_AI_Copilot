"""
SAP S/4HANA Cloud supplier invoices and three-way match (SAP FI / MM invoice verification), read-only.

APIs (same SAP connection as the purchase order source):
  API_SUPPLIERINVOICE_PROCESS_SRV  supplier invoices and their purchase order items (OData V2)
  API_MATERIAL_DOCUMENT_SRV        goods receipts posted against purchase orders (OData V2)
  API_BUSINESS_PARTNER             supplier names (OData V2)
  API_PURCHASEORDER_2              ordered quantities and prices (OData V4, via the purchase order source)

Finance data is only available with SAP_DATA_SOURCE=odata: there is no FI demo dataset.
"""

import re
from datetime import datetime, timezone

from app.sap_data.odata_client import ODataClient, SAPODataError
from app.sap_data.connection import get_connection
from app.sap_data.odata_source import MAX_LIMIT, _literal, _valid_date

INVOICE_SERVICE = "sap/opu/odata/sap/API_SUPPLIERINVOICE_PROCESS_SRV/"
MATERIAL_DOCUMENT_SERVICE = "sap/opu/odata/sap/API_MATERIAL_DOCUMENT_SRV/"
BUSINESS_PARTNER_SERVICE = "sap/opu/odata/sap/API_BUSINESS_PARTNER/"

# Supplier invoice document status (RBKP-RBSTAT). Other codes are returned as they are.
INVOICE_STATUS = {"5": "POSTED", "A": "PARKED", "D": "HELD"}

# Price variance tolerance of the three-way match: 0.5 % of the expected amount, at least 0.01
PRICE_TOLERANCE_PERCENT = 0.5

INVOICE_FIELDS = (
    "SupplierInvoice,FiscalYear,CompanyCode,DocumentDate,PostingDate,SupplierInvoiceIDByInvcgParty,"
    "InvoicingParty,DocumentCurrency,InvoiceGrossAmount,PaymentTerms,DueCalculationBaseDate,"
    "PaymentBlockingReason,SupplierInvoiceStatus,SupplierInvoiceIsCreditMemo,IsReversal,IsReversed,"
    "ReverseDocument,ReverseDocumentFiscalYear"
)


def _v2_date(value):
    """OData V2 date '/Date(1794700800000)/' -> 'YYYY-MM-DD' (None when empty)."""
    match = re.search(r"/Date\((-?\d+)", value or "")
    if not match:
        return None
    return datetime.fromtimestamp(int(match.group(1)) / 1000, tz=timezone.utc).date().isoformat()


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _clean(value):
    """Integer-valued floats as int, other floats rounded: readable for the model and the UI."""
    value = round(value, 3)
    return int(value) if value == int(value) else value


def _item_key(value):
    """Item numbers differ between APIs ('1', '00001', '10'): compare them as integers."""
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value)


def _rows(body):
    return (body.get("d") or {}).get("results", [])


class S4HanaFinanceSource:

    def __init__(self, purchase_order_source):
        self.connection = get_connection()
        self.invoices = ODataClient(INVOICE_SERVICE)
        self.material_documents = ODataClient(MATERIAL_DOCUMENT_SERVICE)
        self.partners = ODataClient(BUSINESS_PARTNER_SERVICE)
        self.purchase_orders = purchase_order_source

    @property
    def label(self):
        return self.connection.label

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get(self, client, path, params):
        return client.get(path, {**params, "$format": "json"})

    def _supplier_names(self, supplier_ids):
        ids = sorted({s for s in supplier_ids if s})
        if not ids:
            return {}
        try:
            body = self._get(self.partners, "A_Supplier", {
                "$filter": " or ".join(f"Supplier eq {_literal(s)}" for s in ids),
                "$select": "Supplier,SupplierName",
                "$top": len(ids)
            })
        except SAPODataError:
            return {}
        return {row["Supplier"]: row.get("SupplierName") for row in _rows(body)}

    def _resolve_suppliers(self, name, max_matches=10):
        """Supplier IDs whose name contains `name` (the invoice API only filters on IDs)."""
        try:
            body = self._get(self.partners, "A_Supplier", {
                "$filter": f"substringof({_literal(name)},SupplierName)",
                "$select": "Supplier,SupplierName",
                "$top": max_matches
            })
        except SAPODataError:
            return {}
        return {row["Supplier"]: row.get("SupplierName") for row in _rows(body)}

    @staticmethod
    def _invoice_summary(row, names):
        status = row.get("SupplierInvoiceStatus") or ""
        is_credit_memo = bool(row.get("SupplierInvoiceIsCreditMemo"))
        return {
            "invoice": row.get("SupplierInvoice"),
            "fiscal_year": row.get("FiscalYear"),
            "document_type": "CREDIT_MEMO" if is_credit_memo else "INVOICE",
            "supplier_reference": row.get("SupplierInvoiceIDByInvcgParty") or None,
            "supplier_id": row.get("InvoicingParty"),
            "supplier": names.get(row.get("InvoicingParty")),
            "company_code": row.get("CompanyCode"),
            "invoice_date": _v2_date(row.get("DocumentDate")),
            "posting_date": _v2_date(row.get("PostingDate")),
            # Baseline date for payment: the due date follows from it and the payment terms
            "baseline_date": _v2_date(row.get("DueCalculationBaseDate")),
            "gross_amount": _number(row.get("InvoiceGrossAmount")),
            "currency": row.get("DocumentCurrency"),
            "payment_terms": row.get("PaymentTerms") or None,
            "payment_block": row.get("PaymentBlockingReason") or None,
            "status": INVOICE_STATUS.get(status, status or None),
            "reversed": bool(row.get("IsReversed")),
            "is_reversal": bool(row.get("IsReversal"))
        }

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def get_supplier_invoice(self, invoice: str, fiscal_year: str | None = None):

        invoice = str(invoice).strip()
        if not re.fullmatch(r"\d{1,10}", invoice):
            return {"error": f"Invalid supplier invoice number: {invoice}"}
        fiscal_year = str(fiscal_year).strip() if fiscal_year else None
        if fiscal_year and not re.fullmatch(r"\d{4}", fiscal_year):
            return {"error": f"Invalid fiscal year: {fiscal_year}"}

        # The same number can exist in several fiscal years: take the most recent one by default
        filters = [f"SupplierInvoice eq {_literal(invoice)}"]
        if fiscal_year:
            filters.append(f"FiscalYear eq {_literal(fiscal_year)}")
        try:
            body = self._get(self.invoices, "A_SupplierInvoice", {
                "$filter": " and ".join(filters),
                "$select": INVOICE_FIELDS,
                "$orderby": "FiscalYear desc",
                "$top": 1
            })
        except SAPODataError as error:
            return {"error": str(error)}

        rows = _rows(body)
        if not rows:
            return {"error": f"Supplier invoice {invoice} not found"}
        header = rows[0]
        key = f"SupplierInvoice={_literal(header['SupplierInvoice'])},FiscalYear={_literal(header['FiscalYear'])}"

        try:
            items = _rows(self._get(self.invoices, f"A_SupplierInvoice({key})/to_SuplrInvcItemPurOrdRef", {
                "$select": "SupplierInvoiceItem,PurchaseOrder,PurchaseOrderItem,Plant,QuantityInPurchaseOrderUnit,"
                           "PurchaseOrderQuantityUnit,SupplierInvoiceItemAmount,DocumentCurrency,"
                           "IsSubsequentDebitCredit,IsFinallyInvoiced,ReferenceDocument",
                "$top": 200
            }))
            # Payment status is only computed when a single invoice is read
            single = self._get(self.invoices, f"A_SupplierInvoice({key})", {
                "$select": "SupplierInvoicePaymentStatus"
            })
        except SAPODataError as error:
            return {"error": str(error)}

        names = self._supplier_names([header.get("InvoicingParty")])
        result = self._invoice_summary(header, names)
        result = {"source": self.label, **result}
        result["payment_status"] = (single.get("d") or {}).get("SupplierInvoicePaymentStatus") or None
        result["purchase_orders"] = sorted({i["PurchaseOrder"] for i in items if i.get("PurchaseOrder")})
        result["items"] = [self._invoice_item(item, result["currency"]) for item in items]
        result["note"] = (
            "Goods receipts are not part of the invoice: use match_purchase_order_invoices "
            "to compare the invoiced quantities with the goods received."
        )
        return result

    @staticmethod
    def _invoice_item(item, currency):
        entry = {
            "item": item.get("SupplierInvoiceItem"),
            "po_number": item.get("PurchaseOrder"),
            "po_item": item.get("PurchaseOrderItem"),
            "plant": item.get("Plant") or None,
            "quantity": _clean(_number(item.get("QuantityInPurchaseOrderUnit"))),
            "unit": item.get("PurchaseOrderQuantityUnit"),
            "amount": _number(item.get("SupplierInvoiceItemAmount")),
            "currency": item.get("DocumentCurrency") or currency
        }
        # Optional flags, only when set: an empty value is not a fact about the goods receipt
        if item.get("ReferenceDocument"):
            entry["goods_receipt"] = item["ReferenceDocument"]
        if item.get("IsSubsequentDebitCredit"):
            entry["subsequent_debit_credit"] = True
        if item.get("IsFinallyInvoiced"):
            entry["final_invoice"] = True
        return entry

    def search_supplier_invoices(self, supplier: str | None = None, date_from: str | None = None,
                                 date_to: str | None = None, blocked_only: bool = False, limit: int = 10):

        limit = max(1, min(int(limit or 10), MAX_LIMIT))
        filters = []
        matched_suppliers = None

        if supplier:
            supplier = supplier.strip()
            if re.fullmatch(r"[A-Z0-9_\-]{4,12}", supplier):
                ids = [supplier]
            else:
                matched_suppliers = self._resolve_suppliers(supplier)
                ids = list(matched_suppliers)
                if not ids:
                    return {
                        "source": self.label,
                        "total_matching": 0,
                        "invoices_returned": 0,
                        "invoices": [],
                        "note": f"No supplier matching '{supplier}'."
                    }
            filters.append("(" + " or ".join(f"InvoicingParty eq {_literal(i)}" for i in ids) + ")")
        if _valid_date(date_from):
            filters.append(f"PostingDate ge datetime'{date_from}T00:00:00'")
        if _valid_date(date_to):
            filters.append(f"PostingDate le datetime'{date_to}T00:00:00'")
        if blocked_only:
            filters.append("PaymentBlockingReason ne ''")

        params = {
            "$select": INVOICE_FIELDS,
            "$orderby": "PostingDate desc,SupplierInvoice desc",
            "$inlinecount": "allpages",
            "$top": limit
        }
        if filters:
            params["$filter"] = " and ".join(filters)

        try:
            body = self._get(self.invoices, "A_SupplierInvoice", params)
        except SAPODataError as error:
            return {"error": str(error)}

        rows = _rows(body)
        names = matched_suppliers or {}
        names = {**self._supplier_names([r.get("InvoicingParty") for r in rows if r.get("InvoicingParty") not in names]),
                 **names}
        total = (body.get("d") or {}).get("__count")

        result = {
            "source": self.label,
            "total_matching": int(total) if total is not None else None,
            "invoices_returned": len(rows),
            "invoices": [self._invoice_summary(row, names) for row in rows]
        }
        if blocked_only:
            result["filter"] = "invoices blocked for payment"
        if matched_suppliers:
            result["matched_suppliers"] = [
                {"supplier_id": sid, "supplier": name} for sid, name in matched_suppliers.items()
            ]
        return result

    def match_purchase_order_invoices(self, po_number: str):
        """
        Three-way match of a purchase order: ordered vs received (goods receipts)
        vs invoiced (supplier invoices), item by item, with price checks.
        """

        po_number = str(po_number).strip()
        order = self.purchase_orders.get_purchase_order(po_number)
        if "error" in order:
            return order

        try:
            receipts = _rows(self._get(self.material_documents, "A_MaterialDocumentItem", {
                "$filter": f"PurchaseOrder eq {_literal(po_number)}",
                "$select": "MaterialDocument,MaterialDocumentYear,MaterialDocumentItem,PurchaseOrderItem,"
                           "GoodsMovementType,DebitCreditCode,QuantityInEntryUnit,EntryUnit",
                "$top": 1000
            }))
            invoice_items = _rows(self._get(self.invoices, "A_SuplrInvcItemPurOrdRef", {
                "$filter": f"PurchaseOrder eq {_literal(po_number)}",
                "$select": "SupplierInvoice,FiscalYear,SupplierInvoiceItem,PurchaseOrderItem,"
                           "QuantityInPurchaseOrderUnit,SupplierInvoiceItemAmount,IsSubsequentDebitCredit",
                "$top": 1000
            }))
            invoice_keys = sorted({(i["SupplierInvoice"], i["FiscalYear"]) for i in invoice_items})
            headers = {}
            if invoice_keys:
                body = self._get(self.invoices, "A_SupplierInvoice", {
                    "$filter": " or ".join(
                        f"(SupplierInvoice eq {_literal(inv)} and FiscalYear eq {_literal(fy)})"
                        for inv, fy in invoice_keys[:50]
                    ),
                    "$select": INVOICE_FIELDS,
                    "$top": 50
                })
                headers = {(h["SupplierInvoice"], h["FiscalYear"]): h for h in _rows(body)}
        except SAPODataError as error:
            return {"error": str(error)}

        # Goods receipts: debit (S, e.g. 101) adds, credit (H, e.g. 102 reversal, 122 return) subtracts
        received = {}
        receipt_documents = set()
        for line in receipts:
            sign = -1 if line.get("DebitCreditCode") == "H" else 1
            key = _item_key(line.get("PurchaseOrderItem"))
            received[key] = received.get(key, 0.0) + sign * _number(line.get("QuantityInEntryUnit"))
            receipt_documents.add(line.get("MaterialDocument"))

        # Invoices: reversed invoices and their reversal documents cancel out, so both are ignored.
        # Credit memos subtract. Subsequent debits/credits change the amount, not the quantity.
        invoiced_qty, invoiced_amount = {}, {}
        counted, ignored = set(), set()
        for line in invoice_items:
            invoice_key = (line["SupplierInvoice"], line["FiscalYear"])
            header = headers.get(invoice_key, {})
            if header.get("IsReversed") or header.get("IsReversal"):
                ignored.add(line["SupplierInvoice"])
                continue
            counted.add(line["SupplierInvoice"])
            sign = -1 if header.get("SupplierInvoiceIsCreditMemo") else 1
            key = _item_key(line.get("PurchaseOrderItem"))
            invoiced_amount[key] = invoiced_amount.get(key, 0.0) + sign * _number(line.get("SupplierInvoiceItemAmount"))
            if not line.get("IsSubsequentDebitCredit"):
                invoiced_qty[key] = invoiced_qty.get(key, 0.0) + sign * _number(line.get("QuantityInPurchaseOrderUnit"))

        items = []
        for po_item in order.get("items", []):
            if po_item.get("deleted"):
                continue
            key = _item_key(po_item.get("item"))
            ordered = _number(po_item.get("quantity"))
            net_amount = _number(po_item.get("net_amount"))
            unit_price = net_amount / ordered if ordered else _number(po_item.get("unit_price"))
            qty_received = received.get(key, 0.0)
            qty_invoiced = invoiced_qty.get(key, 0.0)
            amount_invoiced = invoiced_amount.get(key, 0.0)
            expected_amount = qty_invoiced * unit_price
            price_difference = round(amount_invoiced - expected_amount, 2)
            tolerance = max(0.01, abs(expected_amount) * PRICE_TOLERANCE_PERCENT / 100)

            if qty_invoiced > qty_received + 1e-6:
                status = "INVOICED_NOT_RECEIVED" if qty_received <= 0 else "QUANTITY_VARIANCE"
            elif qty_invoiced > 0 and abs(price_difference) > tolerance:
                status = "PRICE_VARIANCE"
            elif qty_received <= 0 and qty_invoiced <= 0:
                status = "NOT_RECEIVED"
            elif qty_invoiced <= 0:
                status = "NOT_INVOICED"
            elif qty_invoiced < qty_received - 1e-6:
                status = "PARTIALLY_INVOICED"
            else:
                status = "MATCHED"

            items.append({
                "item": po_item.get("item"),
                "material": po_item.get("material"),
                "description": po_item.get("description"),
                "unit": po_item.get("unit"),
                "ordered_quantity": _clean(ordered),
                "received_quantity": _clean(qty_received),
                "invoiced_quantity": _clean(qty_invoiced),
                "unit_price": round(unit_price, 4),
                "invoiced_amount": round(amount_invoiced, 2),
                "price_difference": price_difference if qty_invoiced > 0 else None,
                # GR/IR balance: value received not yet invoiced (positive) or invoiced not yet received (negative)
                "gr_ir_balance": round(qty_received * unit_price - amount_invoiced, 2),
                "currency": order.get("currency"),
                "match_status": status
            })

        statuses = {i["match_status"] for i in items}
        if statuses & {"QUANTITY_VARIANCE", "PRICE_VARIANCE", "INVOICED_NOT_RECEIVED"}:
            overall = "VARIANCE"
        elif statuses == {"MATCHED"} and all(i["received_quantity"] >= i["ordered_quantity"] for i in items):
            overall = "FULLY_MATCHED"
        elif statuses <= {"NOT_RECEIVED"}:
            overall = "NOT_RECEIVED"
        else:
            overall = "IN_PROGRESS"

        return {
            "source": self.label,
            "po_number": po_number,
            "supplier_id": order.get("supplier_id"),
            "supplier": order.get("supplier"),
            "currency": order.get("currency"),
            "match_status": overall,
            "price_tolerance_percent": PRICE_TOLERANCE_PERCENT,
            "ordered_amount": round(sum(_number(i.get("net_amount")) for i in order.get("items", [])
                                        if not i.get("deleted")), 2),
            "invoiced_amount": round(sum(i["invoiced_amount"] for i in items), 2),
            "gr_ir_balance": round(sum(i["gr_ir_balance"] for i in items), 2),
            "goods_receipts": sorted(d for d in receipt_documents if d),
            "invoices": sorted(counted),
            "reversed_invoices_ignored": sorted(ignored),
            "items": items
        }
