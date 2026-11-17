"""Three-way match (purchase order / goods receipt / supplier invoice) on simulated SAP data."""

import pytest

from app.sap_data.finance_source import S4HanaFinanceSource, _clean, _item_key, _v2_date
from app.sap_data.odata_client import SAPODataError

PO = "4500000001"


# ---------------------------------------------------------------------------
# Simulated SAP data
# ---------------------------------------------------------------------------

def po_item(item="10", quantity=100, net_amount=1000.0, deleted=False):
    return {"item": item, "material": f"MAT-{item}", "description": f"Item {item}", "unit": "PC",
            "quantity": quantity, "net_amount": net_amount, "deleted": deleted}


def receipt(item="10", quantity=100, debit_credit="S", movement="101", document="5000000001"):
    return {"MaterialDocument": document, "MaterialDocumentYear": "2026", "MaterialDocumentItem": "1",
            "PurchaseOrderItem": item, "GoodsMovementType": movement, "DebitCreditCode": debit_credit,
            "QuantityInEntryUnit": str(quantity), "EntryUnit": "PC"}


def invoice_line(invoice="5100000001", item="10", quantity=100, amount=1000.0, subsequent=False):
    return {"SupplierInvoice": invoice, "FiscalYear": "2026", "SupplierInvoiceItem": "1",
            "PurchaseOrderItem": item, "QuantityInPurchaseOrderUnit": str(quantity),
            "SupplierInvoiceItemAmount": str(amount), "IsSubsequentDebitCredit": subsequent}


def invoice_header(invoice="5100000001", credit_memo=False, reversed_=False, reversal=False):
    return {"SupplierInvoice": invoice, "FiscalYear": "2026", "SupplierInvoiceIsCreditMemo": credit_memo,
            "IsReversed": reversed_, "IsReversal": reversal}


class FakePurchaseOrders:

    def __init__(self, order):
        self.order = order

    def get_purchase_order(self, po_number):
        return self.order


def run_match(items, receipts=(), invoice_lines=(), headers=None, error=None, order=None):
    """Run the match with simulated OData answers."""

    order = order or {"po_number": PO, "supplier_id": "17300001", "supplier": "Test Supplier",
                      "currency": "EUR", "items": list(items)}
    source = S4HanaFinanceSource(FakePurchaseOrders(order))
    lines = list(invoice_lines)
    if headers is None:
        headers = [invoice_header(i) for i in sorted({line["SupplierInvoice"] for line in lines})]
    answers = {
        "A_MaterialDocumentItem": list(receipts),
        "A_SuplrInvcItemPurOrdRef": lines,
        "A_SupplierInvoice": list(headers)
    }

    def fake_get(client, path, params):
        if error:
            raise SAPODataError(error)
        return {"d": {"results": answers[path]}}

    source._get = fake_get
    return source.match_purchase_order_invoices(PO)


def only_item(result):
    assert len(result["items"]) == 1
    return result["items"][0]


# ---------------------------------------------------------------------------
# Item statuses
# ---------------------------------------------------------------------------

def test_fully_received_and_invoiced_is_matched():
    result = run_match([po_item()], [receipt()], [invoice_line()])
    item = only_item(result)
    assert item["match_status"] == "MATCHED"
    assert item["price_difference"] == 0
    assert item["gr_ir_balance"] == 0
    assert result["match_status"] == "FULLY_MATCHED"
    assert result["invoices"] == ["5100000001"]
    assert result["goods_receipts"] == ["5000000001"]


def test_part_of_the_receipt_not_invoiced():
    result = run_match([po_item()], [receipt(quantity=100)], [invoice_line(quantity=60, amount=600)])
    item = only_item(result)
    assert item["match_status"] == "PARTIALLY_INVOICED"
    assert item["gr_ir_balance"] == 400          # 40 received but not invoiced, at 10.00
    assert result["match_status"] == "IN_PROGRESS"


def test_received_without_invoice():
    result = run_match([po_item()], [receipt()])
    item = only_item(result)
    assert item["match_status"] == "NOT_INVOICED"
    assert item["price_difference"] is None
    assert item["gr_ir_balance"] == 1000


def test_nothing_received_nor_invoiced():
    result = run_match([po_item()])
    assert only_item(result)["match_status"] == "NOT_RECEIVED"
    assert result["match_status"] == "NOT_RECEIVED"


def test_invoiced_more_than_received():
    result = run_match([po_item()], [receipt(quantity=50)], [invoice_line(quantity=80, amount=800)])
    item = only_item(result)
    assert item["match_status"] == "QUANTITY_VARIANCE"
    assert item["gr_ir_balance"] == -300         # 30 invoiced but not received
    assert result["match_status"] == "VARIANCE"


def test_invoiced_without_any_receipt():
    result = run_match([po_item()], [], [invoice_line()])
    assert only_item(result)["match_status"] == "INVOICED_NOT_RECEIVED"
    assert result["match_status"] == "VARIANCE"


def test_price_above_tolerance():
    # Expected 1000.00, tolerance 0.5 % = 5.00: 1010.00 is a variance
    result = run_match([po_item()], [receipt()], [invoice_line(amount=1010)])
    item = only_item(result)
    assert item["match_status"] == "PRICE_VARIANCE"
    assert item["price_difference"] == 10
    assert result["match_status"] == "VARIANCE"


def test_price_within_tolerance():
    result = run_match([po_item()], [receipt()], [invoice_line(amount=1004.99)])
    item = only_item(result)
    assert item["match_status"] == "MATCHED"
    assert item["price_difference"] == 4.99


def test_partial_receipt_fully_invoiced_is_not_fully_matched():
    # Matched so far, but the order is not completely delivered yet
    result = run_match([po_item(quantity=100)], [receipt(quantity=60)], [invoice_line(quantity=60, amount=600)])
    assert only_item(result)["match_status"] == "MATCHED"
    assert result["match_status"] == "IN_PROGRESS"


# ---------------------------------------------------------------------------
# Goods movements and invoice documents
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("movement", ["102", "122"])
def test_credit_movements_reduce_the_received_quantity(movement):
    receipts = [receipt(quantity=100), receipt(quantity=30, debit_credit="H", movement=movement, document="5000000002")]
    result = run_match([po_item()], receipts, [invoice_line(quantity=70, amount=700)])
    item = only_item(result)
    assert item["received_quantity"] == 70
    assert item["match_status"] == "MATCHED"


def test_credit_memo_is_subtracted():
    lines = [invoice_line("5100000001", quantity=100, amount=1000),
             invoice_line("5100000002", quantity=20, amount=200)]
    headers = [invoice_header("5100000001"), invoice_header("5100000002", credit_memo=True)]
    result = run_match([po_item()], [receipt(quantity=80)], lines, headers)
    item = only_item(result)
    assert item["invoiced_quantity"] == 80
    assert item["invoiced_amount"] == 800
    assert item["match_status"] == "MATCHED"


def test_reversed_invoice_and_its_reversal_are_ignored():
    lines = [invoice_line("5100000001", quantity=100, amount=1000),
             invoice_line("5100000002", quantity=100, amount=1000),
             invoice_line("5100000003", quantity=100, amount=1000)]
    headers = [invoice_header("5100000001", reversed_=True),
               invoice_header("5100000002", reversal=True),
               invoice_header("5100000003")]
    result = run_match([po_item()], [receipt()], lines, headers)
    assert only_item(result)["invoiced_quantity"] == 100
    assert result["invoices"] == ["5100000003"]
    assert result["reversed_invoices_ignored"] == ["5100000001", "5100000002"]
    assert result["match_status"] == "FULLY_MATCHED"


def test_subsequent_debit_changes_the_amount_not_the_quantity():
    lines = [invoice_line("5100000001", quantity=100, amount=1000),
             invoice_line("5100000002", quantity=100, amount=50, subsequent=True)]
    result = run_match([po_item()], [receipt()], lines)
    item = only_item(result)
    assert item["invoiced_quantity"] == 100
    assert item["invoiced_amount"] == 1050
    assert item["match_status"] == "PRICE_VARIANCE"


def test_item_numbers_are_compared_as_integers():
    # API_PURCHASEORDER_2 returns "10", the material document "00010", the invoice "000010"
    result = run_match([po_item(item="10")], [receipt(item="00010")], [invoice_line(item="000010")])
    assert only_item(result)["match_status"] == "MATCHED"


def test_deleted_items_are_skipped():
    result = run_match([po_item("10"), po_item("20", deleted=True)], [receipt("10")], [invoice_line(item="10")])
    assert [i["item"] for i in result["items"]] == ["10"]
    assert result["ordered_amount"] == 1000
    assert result["match_status"] == "FULLY_MATCHED"


def test_several_items_any_variance_makes_the_order_a_variance():
    items = [po_item("10"), po_item("20", quantity=10, net_amount=500)]
    receipts = [receipt("10"), receipt("20", quantity=10)]
    lines = [invoice_line(item="10"), invoice_line(item="20", quantity=12, amount=600)]
    result = run_match(items, receipts, lines)
    statuses = {i["item"]: i["match_status"] for i in result["items"]}
    assert statuses == {"10": "MATCHED", "20": "QUANTITY_VARIANCE"}
    assert result["match_status"] == "VARIANCE"
    assert result["invoiced_amount"] == 1600
    assert result["gr_ir_balance"] == -100


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

def test_purchase_order_error_is_returned_as_is():
    order = {"error": "Purchase order 4500000001 not found."}
    source = S4HanaFinanceSource(FakePurchaseOrders(order))
    assert source.match_purchase_order_invoices(PO) == order


def test_odata_error_is_returned_as_a_message():
    result = run_match([po_item()], error="The SAP system did not answer in time.")
    assert result == {"error": "The SAP system did not answer in time."}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def test_helpers():
    assert _v2_date("/Date(1794700800000)/") == "2026-11-15"
    assert _v2_date(None) is None
    assert _item_key("00010") == "10"
    assert _item_key("A1") == "A1"
    assert _clean(5.0) == 5 and isinstance(_clean(5.0), int)
    assert _clean(2.34567) == 2.346
