"""Kiyim do'koni boti: Mini App'dan buyurtma qabul qiladi, to'lovni (naqd / kartaga o'tkazma) boshqaradi."""
import asyncio
import json
import logging
import os
import re
from typing import Optional, Set

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup, WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

import db

load_dotenv()
router = Router()

STATUS = {
    "new": "🆕 Yangi",
    "awaiting_receipt": "💳 Chek kutilmoqda",
    "receipt_sent": "🧾 Chek tekshirilmoqda",
    "confirmed": "✅ Tasdiqlandi",
    "delivered": "🚚 Yetkazildi",
    "cancelled": "❌ Bekor qilindi",
}
PAY = {"cash": "💵 Naqd (yetkazganda)", "card": "💳 Kartaga o'tkazma"}

# amal -> (qaysi holatlardan, yangi holat, mijozga xabar)
ACTIONS = {
    "confirm": ({"new"}, "confirmed", "✅ Buyurtmangiz #{id} qabul qilindi. Tez orada yetkazamiz!"),
    "paid": ({"receipt_sent"}, "confirmed", "✅ To'lovingiz tasdiqlandi. Buyurtma #{id} qabul qilindi!"),
    "reject": ({"receipt_sent"}, "awaiting_receipt",
               "❌ #{id} uchun chek tasdiqlanmadi. Iltimos, to'g'ri chekni qayta yuboring."),
    "deliver": ({"confirmed"}, "delivered", "🚚 Buyurtma #{id} yetkazildi. Xaridingiz uchun rahmat!"),
    "cancel": ({"new", "awaiting_receipt", "receipt_sent", "confirmed"}, "cancelled",
               "❌ Buyurtma #{id} bekor qilindi."),
}


def admin_ids() -> Set[int]:
    return {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()}


def fmt(n: int) -> str:
    return f"{n:,}".replace(",", " ") + " so'm"


def order_text(o) -> str:
    data = json.loads(o["items"])
    lines = [f"🛍 Buyurtma #{o['id']}  {STATUS[o['status']]}", ""]
    for it in data["lines"]:
        opts = " / ".join(x for x in (it["size"], it["color"]) if x)
        suffix = f" ({opts})" if opts else ""
        lines.append(f"• {it['name']}{suffix} × {it['qty']} = {fmt(it['price'] * it['qty'])}")
    if data["fee"]:
        lines.append(f"🚚 Yetkazib berish: {fmt(data['fee'])}")
    lines += [f"💰 Jami: {fmt(o['total'])}", f"To'lov: {PAY[o['pay']]}", "",
              f"👤 {o['name']}", f"📞 {o['phone']}", f"📍 {o['address']}"]
    if o["note"]:
        lines.append(f"📝 {o['note']}")
    if o["username"]:
        lines.append(f"Telegram: @{o['username']}")
    return "\n".join(lines)


def admin_kb(o) -> InlineKeyboardMarkup:
    b, oid, s = InlineKeyboardBuilder(), o["id"], o["status"]
    if s == "receipt_sent":
        b.button(text="✅ To'lov keldi", callback_data=f"o:{oid}:paid")
        b.button(text="❌ Chekni rad etish", callback_data=f"o:{oid}:reject")
    elif s == "new":
        b.button(text="✅ Qabul qilish", callback_data=f"o:{oid}:confirm")
    elif s == "confirmed":
        b.button(text="🚚 Yetkazildi", callback_data=f"o:{oid}:deliver")
    if s not in ("delivered", "cancelled"):
        b.button(text="🗑 Bekor qilish", callback_data=f"o:{oid}:cancel")
    b.adjust(2)
    return b.as_markup()


async def send_admins(bot: Bot, text: str, kb: Optional[InlineKeyboardMarkup] = None) -> None:
    for admin_id in admin_ids():
        try:
            await bot.send_message(admin_id, text, reply_markup=kb)
        except Exception:
            logging.warning("Adminga xabar yuborilmadi: %s", admin_id)


def shop_keyboard() -> Optional[ReplyKeyboardMarkup]:
    url = os.getenv("WEBAPP_URL")
    if not url:
        return None
    button = KeyboardButton(text="🛍 Do'konni ochish", web_app=WebAppInfo(url=url))
    return ReplyKeyboardMarkup(keyboard=[[button]], resize_keyboard=True, is_persistent=True)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Assalomu alaykum! 👋\nPastdagi tugma orqali do'konni oching, tanlang va buyurtma bering.\n\n"
        "/myorders - buyurtmalarim",
        reply_markup=shop_keyboard(),
    )


@router.message(Command("myorders"))
async def cmd_myorders(message: Message) -> None:
    rows = db.recent(5, message.from_user.id)
    if not rows:
        await message.answer("Hali buyurtma yo'q.")
        return
    await message.answer("\n".join(f"#{r['id']}  {STATUS[r['status']]}  {fmt(r['total'])}" for r in rows))


@router.message(Command("orders"))
async def cmd_orders(message: Message) -> None:
    if message.from_user.id not in admin_ids():
        return
    rows = db.recent(10)
    text = "\n".join(
        f"#{r['id']}  {STATUS[r['status']]}  {r['name']}  {fmt(r['total'])}" for r in rows
    ) or "Hali buyurtma yo'q."
    await message.answer(text)


@router.message(F.web_app_data)
async def on_order(message: Message, bot: Bot) -> None:
    try:
        p = json.loads(message.web_app_data.data)
        if p.get("a") != "order" or p.get("pay") not in PAY:
            raise ValueError("order")
        built = db.build_order(p["items"], db.load_catalog())
        name, address = str(p["name"]).strip()[:60], str(p["address"]).strip()[:200]
        phone, note = str(p["phone"]).strip()[:20], str(p.get("note", "")).strip()[:200]
        if not name or not address or len(re.sub(r"\D", "", phone)) < 9:
            raise ValueError("fields")
    except (ValueError, KeyError, TypeError, AttributeError):
        await message.answer("Buyurtmani o'qib bo'lmadi. Iltimos, qaytadan urinib ko'ring.")
        return
    pay = p["pay"]
    status = "awaiting_receipt" if pay == "card" else "new"
    user = message.from_user
    oid = db.create_order(user.id, user.username, name, phone, address, note, built, pay, status)
    order = db.get_order(oid)

    text = f"✅ Buyurtma #{oid} qabul qilindi!\nJami: {fmt(built['total'])}"
    if pay == "card":
        card, holder = os.getenv("CARD_NUMBER"), os.getenv("CARD_HOLDER", "")
        if card:
            text += f"\n\n💳 {fmt(built['total'])} ni shu kartaga o'tkazing:\n{card}"
            text += f"\n{holder}" if holder else ""
            text += "\n\nTo'lovdan so'ng chekni (skrinshot) shu chatga yuboring."
        else:
            text += "\n\nTo'lov rekvizitlarini admin sizga yuboradi."
    else:
        text += "\nTez orada siz bilan bog'lanamiz."
    await message.answer(text, reply_markup=shop_keyboard())
    await send_admins(bot, "🆕 Yangi buyurtma!\n\n" + order_text(order), admin_kb(order))


@router.message(F.photo | F.document)
async def on_receipt(message: Message, bot: Bot) -> None:
    pending = db.pending_receipt(message.from_user.id)
    if pending is None:
        await message.answer("Chek kutilayotgan buyurtma yo'q.")
        return
    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    db.set_receipt(pending["id"], file_id)
    order = db.get_order(pending["id"])
    await message.answer("🧾 Chek qabul qilindi, admin tekshiradi.")
    for admin_id in admin_ids():
        try:
            await message.copy_to(admin_id, caption=f"🧾 Buyurtma #{order['id']} cheki")
        except Exception:
            logging.warning("Chek adminga yuborilmadi: %s", admin_id)
    await send_admins(bot, order_text(order), admin_kb(order))


@router.callback_query(F.data.startswith("o:"))
async def on_action(call: CallbackQuery, bot: Bot) -> None:
    if call.from_user.id not in admin_ids():
        await call.answer("Ruxsat yo'q", show_alert=True)
        return
    _, oid, action = call.data.split(":")
    order, rule = db.get_order(int(oid)), ACTIONS.get(action)
    if order is None or rule is None:
        await call.answer("Topilmadi")
        return
    allowed, new_status, customer_msg = rule
    if order["status"] not in allowed:
        await call.answer("Holat allaqachon o'zgargan", show_alert=True)
        return
    db.set_status(order["id"], new_status)
    order = db.get_order(order["id"])
    try:
        await call.message.edit_text(order_text(order), reply_markup=admin_kb(order))
    except Exception:
        pass
    try:
        await bot.send_message(order["user_id"], customer_msg.format(id=order["id"]))
    except Exception:
        logging.warning("Mijozga xabar yuborilmadi: %s", order["user_id"])
    await call.answer("Bajarildi")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN topilmadi. .env faylini tekshiring.")
    if not admin_ids():
        raise SystemExit("ADMIN_IDS topilmadi: buyurtmalar kimga borishini .env da ko'rsating.")
    db.init()
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(Bot(token=token))


if __name__ == "__main__":
    asyncio.run(main())
