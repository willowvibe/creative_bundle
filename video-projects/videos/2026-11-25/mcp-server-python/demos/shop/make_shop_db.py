"""Build the demo shop database (shop.db) used in the MCP use case. Fictional data, fixed seed, so every run is identical.

usage: python3 make_shop_db.py
"""
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DB = Path(__file__).parent / "shop.db"
PRODUCTS = [("Masala Chai 250g", 180), ("Filter Coffee 500g", 420), ("Green Tea 100g", 260), ("Cold Brew Kit", 950),
            ("Ginger Honey 300g", 340), ("Cardamom Pods 100g", 520), ("Steel Tumbler Set", 799), ("Coffee Grinder", 2450)]
CUSTOMERS = ["Aarav Mehta", "Diya Rao", "Kabir Singh", "Meera Iyer", "Rohan Das", "Sara Khan", "Vikram Nair",
             "Ananya Gupta", "Ishaan Reddy", "Priya Menon", "Arjun Patel", "Neha Joshi"]


def main() -> None:
    DB.unlink(missing_ok=True)
    rnd = random.Random(2026)
    db = sqlite3.connect(DB)
    db.executescript("""
        CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, price_inr INTEGER);
        CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, city TEXT);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, product_id INTEGER, qty INTEGER,
                             order_date TEXT, status TEXT);
    """)
    db.executemany("INSERT INTO products (name, price_inr) VALUES (?, ?)", PRODUCTS)
    cities = ["Hyderabad", "Bengaluru", "Chennai", "Pune", "Mumbai", "Delhi"]
    db.executemany("INSERT INTO customers (name, city) VALUES (?, ?)", [(c, rnd.choice(cities)) for c in CUSTOMERS])
    start = date(2026, 9, 1)
    rows = []
    for _ in range(360):
        day = start + timedelta(days=rnd.randrange(0, 85))
        status = rnd.choices(["delivered", "shipped", "cancelled"], [80, 12, 8])[0]
        rows.append((rnd.randint(1, len(CUSTOMERS)), rnd.choices(range(1, 9), [22, 18, 12, 6, 10, 8, 7, 3])[0],
                     rnd.choice([1, 1, 1, 2, 2, 3]), day.isoformat(), status))
    db.executemany("INSERT INTO orders (customer_id, product_id, qty, order_date, status) VALUES (?, ?, ?, ?, ?)", rows)
    db.commit()
    print(DB.name, db.execute("SELECT COUNT(*) FROM orders").fetchone()[0], "orders")


if __name__ == "__main__":
    main()
