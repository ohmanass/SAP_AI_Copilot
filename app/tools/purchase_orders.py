from app.database.connection import get_connection


def get_purchase_order(po_number: str):

    connection = get_connection()

    query = """
        SELECT
            po.po_number,
            po.order_date,
            po.delivery_date,
            po.status,
            s.name AS supplier,
            s.country AS supplier_country
        FROM purchase_orders po
        JOIN suppliers s
            ON po.supplier_id = s.supplier_id
        WHERE po.po_number = ?
    """

    cursor = connection.execute(query, (po_number,))
    order = cursor.fetchone()

    if order is None:
        connection.close()
        return {"error": f"Purchase order {po_number} not found"}

    items_query = """
        SELECT material, quantity, unit_price
        FROM purchase_order_items
        WHERE po_number = ?
    """

    items = connection.execute(
        items_query, (po_number,)
    ).fetchall()

    result = dict(order)
    result["items"] = [dict(item) for item in items]

    connection.close()

    return result