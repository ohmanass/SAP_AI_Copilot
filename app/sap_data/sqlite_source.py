"""
Offline demo data source: the local SQLite database with fictitious SAP MM data
(app/database/seed.py), used when SAP_DATA_SOURCE=sqlite.
"""

from datetime import date

from app.database.connection import DB_PATH, get_connection

MAX_LIMIT = 50


def sqlite_purchase_order(po_number: str):
    connection = get_connection()
    try:
        order = connection.execute(
            """
            SELECT po.po_number, po.order_date, po.delivery_date, po.status,
                   s.name AS supplier, s.country AS supplier_country
            FROM purchase_orders po
            JOIN suppliers s ON po.supplier_id = s.supplier_id
            WHERE po.po_number = ?
            """,
            (po_number,)
        ).fetchone()
        if order is None:
            return {"error": f"Purchase order {po_number} not found"}
        items = connection.execute(
            "SELECT material, quantity, unit_price FROM purchase_order_items WHERE po_number = ?",
            (po_number,)
        ).fetchall()
        return dict(order, items=[dict(item) for item in items])
    finally:
        connection.close()


def sqlite_overdue_orders():
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT po.po_number, s.name AS supplier, po.delivery_date, po.status
            FROM purchase_orders po
            JOIN suppliers s ON po.supplier_id = s.supplier_id
            WHERE po.delivery_date < ? AND po.status != 'RECEIVED'
            ORDER BY po.delivery_date ASC
            """,
            (date.today().isoformat(),)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


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
