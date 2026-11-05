"""Buyurtmalar bazasi va katalog. Narxlar har doim serverda katalogdan hisoblanadi."""
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

DB_PATH = os.getenv("DB_PATH", "shop.db")
CATALOG_PATH = Path(os.getenv("CATALOG_PATH") or Path(__file__).parent / "docs" / "products.json")
TZ = ZoneInfo("Asia/Tashkent")
MAX_ITEMS, MAX_QTY = 20, 10


def now() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with closing(_conn()) as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL, username TEXT, name TEXT NOT NULL, phone TEXT NOT NULL, "
            "address TEXT NOT NULL, note TEXT, items TEXT NOT NULL, total INTEGER NOT NULL, "
            "pay TEXT NOT NULL, status TEXT NOT NULL, receipt TEXT, created_at TEXT NOT NULL)"
        )
        c.commit()


def load_catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def build_order(cart, catalog: dict) -> dict:
    """Savatni katalog bo'yicha tekshirib, narxlarni o'zi hisoblaydi. Xato bo'lsa ValueError."""
    if not isinstance(cart, list) or not 0 < len(cart) <= MAX_ITEMS:
        raise ValueError("cart")
    by_id = {p["id"]: p for p in catalog["products"]}
    lines, subtotal = [], 0
    for item in cart:
        p = by_id.get(str(item["id"]))
        if p is None:
            raise ValueError("product")
        size, color = str(item.get("size", "")), str(item.get("color", ""))
        if p.get("sizes") and size not in p["sizes"]:
            raise ValueError("size")
        if p.get("colors") and color not in p["colors"]:
            raise ValueError("color")
        qty = int(item.get("qty", 1))
        if not 1 <= qty <= MAX_QTY:
            raise ValueError("qty")
        lines.append({"id": p["id"], "name": p["name"], "size": size, "color": color,
                      "qty": qty, "price": p["price"]})
        subtotal += p["price"] * qty
    d = catalog.get("delivery", {})
    fee = d.get("fee", 0) if subtotal < d.get("free_from", 0) else 0
    return {"lines": lines, "subtotal": subtotal, "fee": fee, "total": subtotal + fee}


def create_order(user_id, username, name, phone, address, note, built, pay, status) -> int:
    items = json.dumps({k: built[k] for k in ("lines", "subtotal", "fee")}, ensure_ascii=False)
    with closing(_conn()) as c:
        cur = c.execute(
            "INSERT INTO orders (user_id, username, name, phone, address, note, items, total, pay, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, username, name, phone, address, note, items, built["total"], pay, status, now()),
        )
        c.commit()
        return int(cur.lastrowid)


def get_order(order_id: int) -> Optional[sqlite3.Row]:
    with closing(_conn()) as c:
        return c.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()


def set_status(order_id: int, status: str) -> None:
    with closing(_conn()) as c:
        c.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
        c.commit()


def set_receipt(order_id: int, file_id: str) -> None:
    with closing(_conn()) as c:
        c.execute("UPDATE orders SET receipt = ?, status = 'receipt_sent' WHERE id = ?", (file_id, order_id))
        c.commit()


def pending_receipt(user_id: int) -> Optional[sqlite3.Row]:
    with closing(_conn()) as c:
        return c.execute(
            "SELECT * FROM orders WHERE user_id = ? AND status = 'awaiting_receipt' ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()


def recent(limit: int = 10, user_id: Optional[int] = None) -> List[sqlite3.Row]:
    sql, params = "SELECT * FROM orders", []
    if user_id is not None:
        sql, params = sql + " WHERE user_id = ?", [user_id]
    with closing(_conn()) as c:
        return c.execute(sql + " ORDER BY id DESC LIMIT ?", params + [limit]).fetchall()
