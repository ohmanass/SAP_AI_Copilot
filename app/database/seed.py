from datetime import date, timedelta

from app.database.connection import (
    get_connection,
    initialize_database
)


def seed_database():

    initialize_database()

    connection = get_connection()

    today = date.today()

    suppliers = [
        ("SUP001", "TechParts GmbH", "Germany"),
        ("SUP002", "Global Components", "France"),
        ("SUP003", "Industrial Solutions", "Italy")
    ]

    connection.executemany(
        """
        INSERT OR IGNORE INTO suppliers
        (supplier_id, name, country)
        VALUES (?, ?, ?)
        """,
        suppliers
    )

    orders = [
        (
            "4500000010", "SUP001",
            (today - timedelta(days=20)).isoformat(),
            (today - timedelta(days=5)).isoformat(),
            "OPEN"
        ),
        (
            "4500000011", "SUP002",
            (today - timedelta(days=5)).isoformat(),
            (today + timedelta(days=5)).isoformat(),
            "OPEN"
        ),
        (
            "4500000012", "SUP001",
            (today - timedelta(days=30)).isoformat(),
            (today - timedelta(days=10)).isoformat(),
            "RECEIVED"
        ),
        (
            "4500000013", "SUP003",
            (today - timedelta(days=15)).isoformat(),
            (today - timedelta(days=2)).isoformat(),
            "OPEN"
        )
    ]

    connection.executemany(
        """
        INSERT OR IGNORE INTO purchase_orders
        (po_number, supplier_id, order_date, delivery_date, status)
        VALUES (?, ?, ?, ?, ?)
        """,
        orders
    )

    items = [
        ("4500000010", "MAT-100", 100, 25.50),
        ("4500000010", "MAT-200", 50, 40.00),
        ("4500000011", "MAT-300", 200, 12.00),
        ("4500000012", "MAT-100", 75, 25.50),
        ("4500000013", "MAT-400", 30, 120.00)
    ]

    connection.executemany(
        """
        INSERT INTO purchase_order_items
        (po_number, material, quantity, unit_price)
        SELECT ?, ?, ?, ?
        WHERE NOT EXISTS (
            SELECT 1
            FROM purchase_order_items
            WHERE po_number = ?
              AND material = ?
        )
        """,
        [item + (item[0], item[1]) for item in items]
    )

    connection.commit()
    connection.close()

    print("SAP MM database initialized successfully!")


if __name__ == "__main__":
    seed_database()