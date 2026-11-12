"""
Offline demo data source: the local SQLite database with fictitious SAP MM data.
Reuses the existing tool functions so their behaviour stays unchanged.
"""

from app.database.connection import DB_PATH, get_connection
from app.tools.delivery_analysis import get_overdue_orders as sqlite_overdue_orders
from app.tools.purchase_orders import get_purchase_order as sqlite_purchase_order

MAX_LIMIT = 50


class SQLiteDemoSource:

    key = "sqlite"
    label = "Demo data (SQLite)"

    def describe(self):
        return {
            "key": self.key,
            "label": self.label,
            "system": "data/sap.db",
            "api": None,
            "configured": DB_PATH.exists()
        }

    def get_purchase_order(self, po_number: str):
        result = sqlite_purchase_order(str(po_number).strip())
        if "error" not in result:
            result["source"] = self.label
        return result

    def get_overdue_orders(self, days_back: int = 30, limit: int = 10):
        # The demo dataset is tiny: no time window needed
        orders = sqlite_overdue_orders()[:max(1, min(int(limit or 10), MAX_LIMIT))]
        return {
            "source": self.label,
            "orders_returned": len(orders),
            "orders": orders
        }

    def search_purchase_orders(self, supplier: str | None = None, date_from: str | None = None,
                               date_to: str | None = None, limit: int = 10):
        conditions, params = [], []
        if supplier:
            conditions.append("(s.name LIKE ? OR s.supplier_id = ?)")
            params += [f"%{supplier.strip()}%", supplier.strip()]
        if date_from:
            conditions.append("po.order_date >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("po.order_date <= ?")
            params.append(date_to)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        connection = get_connection()
        try:
            rows = connection.execute(
                f"""
                SELECT po.po_number, po.order_date, po.delivery_date, po.status,
                       s.supplier_id, s.name AS supplier, s.country AS supplier_country
                FROM purchase_orders po
                JOIN suppliers s ON po.supplier_id = s.supplier_id
                {where}
                ORDER BY po.order_date DESC
                LIMIT ?
                """,
                (*params, max(1, min(int(limit or 10), MAX_LIMIT)))
            ).fetchall()
        finally:
            connection.close()

        orders = [dict(row) for row in rows]
        return {"source": self.label, "orders_returned": len(orders), "orders": orders}

    def status(self):
        info = self.describe()
        if not DB_PATH.exists():
            return {**info, "available": False, "error": "DatabaseNotFound", "latency_ms": None}

        connection = get_connection()
        try:
            tables = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("suppliers", "purchase_orders", "purchase_order_items")
            }
            return {**info, "available": True, "error": None, "latency_ms": None,
                    "purchase_orders": tables["purchase_orders"], "tables": tables}
        except Exception as error:
            return {**info, "available": False, "error": type(error).__name__, "latency_ms": None}
        finally:
            connection.close()
