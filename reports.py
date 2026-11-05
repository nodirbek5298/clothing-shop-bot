"""Klientlar bazasi va buyurtmalar hisoboti (Excel): qo'lda va avtomatik (kunlik/haftalik)."""
import asyncio
import io
import json
import logging
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from typing import Set

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, Message
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

import db

router = Router()
_task = None

STATUS = {"new": "Yangi", "awaiting_receipt": "Chek kutilmoqda", "receipt_sent": "Chek tekshirilmoqda",
          "confirmed": "Tasdiqlangan", "delivered": "Yetkazilgan", "cancelled": "Bekor qilingan"}
PAY = {"cash": "Naqd", "card": "Kartaga o'tkazma"}
PERIODS = {"day": ("bugun", 0), "week": ("oxirgi 7 kun", 6), "all": ("barcha vaqt", None)}
ALIASES = {"day": "day", "kun": "day", "bugun": "day", "week": "week", "hafta": "week",
           "all": "all", "hammasi": "all"}
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# Har bir klient bitta qator: oxirgi buyurtmadagi ma'lumotlari + umumiy va davr statistikasi
CLIENTS_SQL = """
SELECT o.user_id, o.username, o.name, o.phone, o.address,
       s.orders, s.spent, s.first, s.last, s.p_orders, s.p_spent
FROM orders o JOIN (
  SELECT MAX(id) AS last_id, user_id,
    SUM(status != 'cancelled') AS orders,
    SUM(CASE WHEN status != 'cancelled' THEN total ELSE 0 END) AS spent,
    MIN(created_at) AS first, MAX(created_at) AS last,
    SUM(CASE WHEN status != 'cancelled' AND created_at >= ? AND created_at < ? THEN 1 ELSE 0 END) AS p_orders,
    SUM(CASE WHEN status != 'cancelled' AND created_at >= ? AND created_at < ? THEN total ELSE 0 END) AS p_spent
  FROM orders GROUP BY user_id) s ON o.id = s.last_id
ORDER BY s.p_orders DESC, s.spent DESC
"""


def admin_ids() -> Set[int]:
    return {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()}


def fmt(n: int) -> str:
    return f"{n:,}".replace(",", " ") + " so'm"


def _range(period: str):
    label, back = PERIODS[period]
    today = datetime.now(db.TZ).date()
    start = (today - timedelta(days=back)).strftime("%Y-%m-%d") if back is not None else "2000-01-01"
    return label, start, (today + timedelta(days=1)).strftime("%Y-%m-%d")


def _sheet(ws, headers, rows, money_cols=()) -> None:
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="7C3AED")
        cell.alignment = Alignment(vertical="center")
    for row in rows:
        ws.append(row)
        for cell in ws[ws.max_row]:
            if isinstance(cell.value, str) and cell.value.startswith("="):
                cell.data_type = "s"  # matn formula bo'lib ketmasligi uchun
            if cell.column in money_cols:
                cell.number_format = "#,##0"
    for i, h in enumerate(headers, 1):
        width = max([len(str(h))] + [len(str(r[i - 1])) for r in rows]) + 2
        ws.column_dimensions[get_column_letter(i)].width = min(width, 45)
    ws.freeze_panes = "A2"


def build_report(period: str):
    """(xlsx baytlari, izoh matni). Ikki varaq: Klientlar (yagona baza) va Buyurtmalar (davr)."""
    label, start, end = _range(period)
    with closing(sqlite3.connect(db.DB_PATH)) as c:
        c.row_factory = sqlite3.Row
        clients = c.execute(CLIENTS_SQL, (start, end, start, end)).fetchall()
        orders = c.execute(
            "SELECT * FROM orders WHERE created_at >= ? AND created_at < ? ORDER BY id", (start, end)
        ).fetchall()
    wb = Workbook()
    _sheet(
        wb.active,
        ["№", "Ism", "Telefon", "Manzil", "Telegram", "Shu davrda buyurtma", "Shu davrda summa",
         "Jami buyurtma", "Jami summa", "Birinchi buyurtma", "Oxirgi buyurtma"],
        [[i, c["name"], c["phone"], c["address"], f"@{c['username']}" if c["username"] else "",
          c["p_orders"], c["p_spent"], c["orders"], c["spent"], c["first"], c["last"]]
         for i, c in enumerate(clients, 1)],
        money_cols={7, 9},
    )
    wb.active.title = "Klientlar"
    rows = []
    for i, o in enumerate(orders, 1):
        lines = json.loads(o["items"])["lines"]
        items = "; ".join(" ".join(filter(None, [l["name"], l["size"], l["color"], f"x{l['qty']}"])) for l in lines)
        rows.append([i, o["created_at"], o["name"], o["phone"], o["address"], items, o["total"],
                     PAY.get(o["pay"], o["pay"]), STATUS.get(o["status"], o["status"])])
    _sheet(wb.create_sheet("Buyurtmalar"),
           ["№", "Sana", "Klient", "Telefon", "Manzil", "Mahsulotlar", "Summa", "To'lov", "Holat"],
           rows, money_cols={7})
    active = [o for o in orders if o["status"] != "cancelled"]
    caption = (f"📊 Hisobot: {label}\nBuyurtmalar: {len(active)}\n"
               f"Tushum: {fmt(sum(o['total'] for o in active))}\n"
               f"Yangi klientlar: {sum(1 for c in clients if c['first'] >= start)}\n"
               f"Jami klientlar: {len(clients)}")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), caption


async def send_report(bot: Bot, chat_id: int, period: str) -> None:
    data, caption = await asyncio.to_thread(build_report, period)
    name = f"hisobot_{period}_{datetime.now(db.TZ):%Y-%m-%d}.xlsx"
    await bot.send_document(chat_id, BufferedInputFile(data, filename=name), caption=caption)


@router.message(Command("report"))
async def cmd_report(message: Message, command: CommandObject, bot: Bot) -> None:
    if message.from_user.id not in admin_ids():
        return
    period = ALIASES.get((command.args or "day").strip().lower())
    if period is None:
        await message.answer("Masalan: /report day (bugun), /report week (7 kun) yoki /report all (hammasi)")
        return
    await send_report(bot, message.chat.id, period)


def _meta(key: str, value=None):
    with closing(sqlite3.connect(db.DB_PATH)) as c:
        c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        if value is None:
            row = c.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return row[0] if row else None
        c.execute("INSERT INTO meta (key, value) VALUES (?, ?) "
                  "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
        c.commit()


def _due(period: str, now: datetime) -> bool:
    """DAILY_REPORT_AT='21:00', WEEKLY_REPORT_AT='mon 09:00' (Toshkent vaqti)."""
    if period == "day":
        day, spec = None, os.getenv("DAILY_REPORT_AT", "").strip()
    else:
        parts = os.getenv("WEEKLY_REPORT_AT", "").lower().split()
        if len(parts) != 2 or parts[0] not in WEEKDAYS:
            return False
        day, spec = parts
    if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", spec):
        return False
    if day and WEEKDAYS[now.weekday()] != day:
        return False
    hour, minute = map(int, spec.split(":"))
    return (now.hour, now.minute) >= (hour, minute)


async def run_jobs(bot: Bot) -> None:
    now = datetime.now(db.TZ)
    today = now.strftime("%Y-%m-%d")
    for period in ("day", "week"):
        if _meta(f"report_{period}") == today or not _due(period, now):
            continue
        _meta(f"report_{period}", today)  # kuniga bir marta
        for admin_id in admin_ids():
            try:
                await send_report(bot, admin_id, period)
            except Exception:
                logging.exception("Avtomatik hisobot yuborilmadi: %s", admin_id)


async def _loop(bot: Bot) -> None:
    while True:
        try:
            await run_jobs(bot)
        except Exception:
            logging.exception("Hisobot vazifasida xato")
        await asyncio.sleep(60)


@router.startup()
async def on_startup(bot: Bot) -> None:
    global _task
    _task = asyncio.create_task(_loop(bot))


@router.shutdown()
async def on_shutdown() -> None:
    if _task:
        _task.cancel()
