"""Home dashboard: indicators read in parallel, cached, one failure never breaks the others."""

from fastapi.testclient import TestClient

from app import dashboard
from app.tools import sap_data_tools


def test_indicators_cache_and_failures(monkeypatch):
    calls = []
    monkeypatch.setattr(sap_data_tools, "get_overdue_orders",
                        lambda days, limit: calls.append("po") or {"source": "S4", "overdue_schedule_lines_in_window": 12})
    monkeypatch.setattr(sap_data_tools, "get_late_sales_orders",
                        lambda days, limit: {"error": "SAP S/4HANA required"})

    def broken(**kwargs):
        raise TimeoutError("sandbox down")

    monkeypatch.setattr(sap_data_tools, "search_supplier_invoices", broken)
    monkeypatch.setattr(dashboard, "_cache", {"at": 0.0, "data": None})

    from app import api
    client = TestClient(api.app)
    items = {i["key"]: i for i in client.get("/api/dashboard").json()["indicators"]}
    assert items["overdue_purchase_orders"]["value"] == 12 and items["overdue_purchase_orders"]["module"] == "mm"
    assert items["late_sales_orders"]["value"] is None and items["late_sales_orders"]["error"]
    assert items["blocked_supplier_invoices"]["error"] == "TimeoutError"

    client.get("/api/dashboard")
    assert calls == ["po"]                      # cached
    client.get("/api/dashboard", params={"refresh": True})
    assert calls == ["po", "po"]
