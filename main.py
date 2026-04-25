import asyncio
import asyncpg
import os
import random

from aiogram import Bot, Dispatcher, Router
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)

from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

# ================= CONFIG =================
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7062911219"))
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", 10000))

bot = Bot(TOKEN)
dp = Dispatcher()
router = Router()

pool = None

admin_mode = {}
user_index = {}  # карты

# ================= KEEP ALIVE =================
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def web():
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

# ================= DB =================
async def db_init():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL)

    async with pool.acquire() as c:
        await c.execute("CREATE TABLE IF NOT EXISTS users(id BIGINT PRIMARY KEY, role TEXT)")
        await c.execute("CREATE TABLE IF NOT EXISTS codes(code TEXT)")
        await c.execute("CREATE TABLE IF NOT EXISTS emails(value TEXT)")
        await c.execute("CREATE TABLE IF NOT EXISTS domains(value TEXT)")
        await c.execute("CREATE TABLE IF NOT EXISTS accesses(value TEXT)")
        await c.execute("CREATE TABLE IF NOT EXISTS manuals(value TEXT)")
        await c.execute("""
            CREATE TABLE IF NOT EXISTS cards(
                id SERIAL PRIMARY KEY,
                value TEXT,
                category TEXT
            )
        """)

# ================= UI =================
def menu(uid):
    kb = [
        [KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="📧 Почта"), KeyboardButton(text="🌐 Домен")],
        [KeyboardButton(text="🔑 Доступы"), KeyboardButton(text="📚 Мануалы")],
        [KeyboardButton(text="💳 Карты")]
    ]

    if uid == ADMIN_ID:
        kb.append([KeyboardButton(text="🛠 Админ")])

    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

admin_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
    [InlineKeyboardButton(text="➕ Почта", callback_data="add_email")],
    [InlineKeyboardButton(text="➕ Домен", callback_data="add_domain")],
    [InlineKeyboardButton(text="➕ Доступ", callback_data="add_access")],
    [InlineKeyboardButton(text="➕ Мануал", callback_data="add_manual")],
    [InlineKeyboardButton(text="➕ Карта 1", callback_data="add_card1")],
    [InlineKeyboardButton(text="➕ Карта 2", callback_data="add_card2")]
])

cards_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Карта 1", callback_data="card1")],
    [InlineKeyboardButton(text="Карта 2", callback_data="card2")]
])

# ================= CARDS =================
def get_card(uid, cat):
    async def fetch():
        async with pool.acquire() as c:
            rows = await c.fetch("SELECT value FROM cards WHERE category=$1 ORDER BY id", cat)

        if not rows:
            return None

        key = (uid, cat)
        idx = user_index.get(key, 0)

        card = rows[idx % len(rows)][0]
        user_index[key] = idx + 1

        return card

    return asyncio.get_event_loop().run_until_complete(fetch())

# ================= MESSAGE =================
@router.message()
async def msg(m: Message):
    uid = m.from_user.id
    text = m.text

    if text == "/start":
        return await m.answer("бот готов", reply_markup=menu(uid))

    if text == "💳 Карты":
        return await m.answer("выбор", reply_markup=cards_kb)

    if text == "🛠 Админ" and uid == ADMIN_ID:
        return await m.answer("админка", reply_markup=admin_kb)

    # ===== PROFILE =====
    if text == "👤 Профиль":
        async with pool.acquire() as c:
            role = await c.fetchval("SELECT role FROM users WHERE id=$1", uid)

        return await m.answer(f"ID: {uid}\nРоль: {role or 'нет'}")

# ================= ADMIN INPUT =================
@router.message()
async def admin_input(m: Message):
    uid = m.from_user.id
    if uid != ADMIN_ID or uid not in admin_mode:
        return

    mode = admin_mode[uid]
    del admin_mode[uid]

    data = [x.strip() for x in m.text.split() if x.strip()]

    async with pool.acquire() as c:

        if mode == "add_email":
            for i in data:
                await c.execute("INSERT INTO emails VALUES($1)", i)

        elif mode == "add_domain":
            for i in data:
                await c.execute("INSERT INTO domains VALUES($1)", i)

        elif mode == "add_access":
            for i in data:
                await c.execute("INSERT INTO accesses VALUES($1)", i)

        elif mode == "add_manual":
            for i in data:
                await c.execute("INSERT INTO manuals VALUES($1)", i)

        elif mode == "add_card1":
            for i in data:
                await c.execute("INSERT INTO cards(value,category) VALUES($1,'1')", i)

        elif mode == "add_card2":
            for i in data:
                await c.execute("INSERT INTO cards(value,category) VALUES($1,'2')", i)

    await m.answer("✅ готово")

# ================= CALLBACK =================
@router.callback_query()
async def cb(c: CallbackQuery):
    await c.answer()
    uid = c.from_user.id

    async with pool.acquire() as db:

        # ===== CARDS =====
        if c.data == "card1":
            card = get_card(uid, "1")
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ не подошла", callback_data="retry1")]
            ])
            return await c.message.answer(card or "нет", reply_markup=kb)

        if c.data == "card2":
            card = get_card(uid, "2")
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ не подошла", callback_data="retry2")]
            ])
            return await c.message.answer(card or "нет", reply_markup=kb)

        if c.data == "retry1":
            card = get_card(uid, "1")
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ не подошла", callback_data="retry1")]
            ])
            return await c.message.answer(card or "нет", reply_markup=kb)

        if c.data == "retry2":
            card = get_card(uid, "2")
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ не подошла", callback_data="retry2")]
            ])
            return await c.message.answer(card or "нет", reply_markup=kb)

        # ===== ADMIN =====
        if uid == ADMIN_ID:

            admin_mode[uid] = c.data

            if c.data.startswith("add_"):
                return await c.message.answer("отправь текст")

            if c.data == "stats":
                emails = await db.fetchval("SELECT COUNT(*) FROM emails")
                domains = await db.fetchval("SELECT COUNT(*) FROM domains")
                access = await db.fetchval("SELECT COUNT(*) FROM accesses")
                manuals = await db.fetchval("SELECT COUNT(*) FROM manuals")
                c1 = await db.fetchval("SELECT COUNT(*) FROM cards WHERE category='1'")
                c2 = await db.fetchval("SELECT COUNT(*) FROM cards WHERE category='2'")

                return await c.message.answer(
f"""📊 СТАТИСТИКА

📧 {emails}
🌐 {domains}
🔑 {access}
📚 {manuals}
💳1 {c1}
💳2 {c2}"""
                )

# ================= START =================
async def main():
    await db_init()
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    threading.Thread(target=web).start()
    asyncio.run(main())
