# 🛍 Kiyim do'koni Telegram boti

Mini App vitrina (rasmli kartochkalar), savat, buyurtma berish va to'lov (naqd yoki kartaga o'tkazma + chek).

## Qanday ishlaydi
1. Mijoz botdagi **Do'konni ochish** tugmasini bosadi, mahsulot, o'lcham va rang tanlab savatga qo'shadi.
2. Ism, telefon, manzil va to'lov turini kiritib buyurtma beradi.
3. Admin buyurtmani Telegramda tugmalar bilan boshqaradi: qabul qilish, to'lovni tasdiqlash, yetkazildi, bekor qilish.
4. Kartaga o'tkazmada mijoz chek rasmini botga yuboradi, admin tasdiqlaydi.

Narxlar mijoz telefonidan emas, `docs/products.json` katalogidan serverda hisoblanadi.

## Mahsulot qo'shish
`docs/products.json` ni tahrirlang (`id`, `name`, `cat`, `price`, `sizes`, `colors`). Rasm uchun `"img": "img/p1.jpg"` qo'shib, rasmni `docs/img/` ga joylang.

## Ishga tushirish
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # tokenni, ADMIN_IDS va rekvizitlarni yozing
python bot.py
```
GitHub Pages: Settings -> Pages -> `main` / `/docs`.

## Texnologiyalar
Python 3, aiogram 3, SQLite, Telegram Mini App (HTML/CSS/JS).
