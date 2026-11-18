"""
Home dashboard: live SAP indicators shown before the first question.

Each indicator is read with the same SAP tools as the agent, in parallel, and
kept 5 minutes (the sandbox is slow and the figures change slowly). An
indicator that cannot be read is returned with an error, never breaks the others.
The frontend sends the indicator's "prompt" key when the user clicks it.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from app.tools import sap_data_tools

logger = logging.getLogger("sap_ai_copilot.dashboard")

CACHE_SECONDS = 300
DAYS_BACK = 30

# key, reader, field holding the count, icon, workspace of the analysis
INDICATORS = (
    ("overdue_purchase_orders", lambda: sap_data_tools.get_overdue_orders(DAYS_BACK, 1),
     "overdue_schedule_lines_in_window", "sap-icon://shipping-status", "mm"),
    ("late_sales_orders", lambda: sap_data_tools.get_late_sales_orders(DAYS_BACK, 1),
     "late_orders_in_window", "sap-icon://past", "sd"),
    ("blocked_supplier_invoices", lambda: sap_data_tools.search_supplier_invoices(blocked_only=True, limit=1),
     "total_matching", "sap-icon://locked", "fi"),
)

_cache = {"at": 0.0, "data": None}
_lock = threading.Lock()


def _read(indicator):
    key, reader, field, icon, module = indicator
    item = {"key": key, "icon": icon, "module": module, "value": None, "source": None, "error": None}
    try:
        result = reader()
        if not isinstance(result, dict) or result.get("error") or field not in result:
            item["error"] = (result or {}).get("error") or "unavailable"
        else:
            item.update(value=int(result[field]), source=result.get("source"))
    except Exception as error:
        logger.warning("Dashboard indicator %s failed: %s", key, error)
        item["error"] = type(error).__name__
    return item


def indicators(refresh: bool = False):
    with _lock:
        if not refresh and _cache["data"] and time.time() - _cache["at"] < CACHE_SECONDS:
            return _cache["data"]
        with ThreadPoolExecutor(max_workers=len(INDICATORS)) as pool:
            items = list(pool.map(_read, INDICATORS))
        data = {"days": DAYS_BACK, "indicators": items,
                "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        _cache.update(at=time.time(), data=data)
        return data
