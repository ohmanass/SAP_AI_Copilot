from datetime import date

from app.database.connection import get_connection


def get_overdue_orders():

    connection = get_connection()

    today = date.today().isoformat()

    query = """
        SELECT
            po.po_number,
            s.name AS supplier,
            po.delivery_date,
            po.status
        FROM purchase_orders po
        JOIN suppliers s
            ON po.supplier_id = s.supplier_id
        WHERE po.delivery_date < ?
          AND po.status != 'RECEIVED'
        ORDER BY po.delivery_date ASC
    """

    cursor = connection.execute(query, (today,))

    results = [
        dict(row)
        for row in cursor.fetchall()
    ]

    connection.close()

    return results