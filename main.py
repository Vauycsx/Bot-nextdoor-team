import asyncio
import asyncpg
import random
import os
from datetime import datetime

from aiogram import Bot, Dispatcher, Router
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

# ================= CONFIG =================
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7062911219"))

DB_CONFIG = {
    "user": os.getenv("PGUSER"),
    "password": os.getenv("PGPASSWORD"),
    "database": os.getenv("PGDATABASE"),
    "host": os.getenv("PGHOST")
}

bot = Bot(TOKEN)
dp = Dispatcher()
router = Router()
pool = None

admin_state = {}
card_index = {}

# ================= KEEP ALIVE =================
PORT = int(os.getenv("PORT", 10000))

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def web():
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

# ================= DB =================
async def init_db():
    global pool
    pool = await asyncpg.create_pool(**DB_CONFIG)

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
        [KeyboardButton("👤 Профиль")],
        [KeyboardButton("📧 Почта"), KeyboardButton("🌐 Домен")],
        [KeyboardButton("🔑 Доступы"), KeyboardButton("📚 Мануалы")],
        [KeyboardButton("💳 Карты")]
    ]
    if uid == ADMIN_ID:
        kb.append([KeyboardButton("🛠 Админ")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

admin_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton("➕ Почта", callback_data="email")],
    [InlineKeyboardButton("➕ Домен", callback_data="domain")],
    [InlineKeyboardButton("➕ Доступ", callback_data="access")],
    [InlineKeyboardButton("➕ Мануал", callback_data="manual")],
    [InlineKeyboardButton("➕ Карты 1", callback_data="cards1")],
    [InlineKeyboardButton("➕ Карты 2", callback_data="cards2")],
    [InlineKeyboardButton("📊 Статистика", callback_data="stats")]
])

cards_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton("Первая карта", callback_data="card_1")],
    [InlineKeyboardButton("Вторая карта", callback_data="card_2")]
])

retry1 = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton("❌ не подошла", callback_data="retry_1")]
])

retry2 = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton("❌ не подошла", callback_data="retry_2")]
])

# ================= USERS =================
async def get_user(uid):
    async with pool.acquire() as c:
        return await c.fetchrow("SELECT * FROM users WHERE id=$1", uid)

async def set_user(uid, role="user"):
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO users(id,role) VALUES($1,$2) ON CONFLICT DO NOTHING",
            uid, role
        )

# ================= CARDS =================
async def get_card(uid, cat):
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT value FROM cards WHERE category=$1", cat)

        if not rows:
            return None, "❌ нет карт"

        key = (uid, cat)
        i = card_index.get(key, 0)

        card = rows[i % len(rows)]["value"]
        card_index[key] = i + 1

        return card, None

# ================= MESSAGE =================
@router.message()
async def msg(m: Message):
    uid = m.from_user.id
    text = m.text

    user = await get_user(uid)

    # START
    if text == "/start":
        if not user and uid != ADMIN_ID:
            return await m.answer("🔐 код")

        await set_user(uid, "admin" if uid == ADMIN_ID else "user")
        return await m.answer("🚀 готово", reply_markup=menu(uid))

    # AUTH
    if not user and uid != ADMIN_ID:
        async with pool.acquire() as c:
            code = await c.fetchrow("SELECT * FROM codes WHERE code=$1", text)
            if not code:
                return await m.answer("❌ неверный код")

            await c.execute("DELETE FROM codes WHERE code=$1", text)

        return await m.answer("✅ доступ открыт", reply_markup=menu(uid))

    # CARDS
    if text == "💳 Карты":
        return await m.answer("выбор:", reply_markup=cards_kb)

    # PROFILE (ЧИСТЫЙ)
    if text == "👤 Профиль":
        async with pool.acquire() as c:
            role = await c.fetchval("SELECT role FROM users WHERE id=$1", uid)

            emails = await c.fetchval("SELECT COUNT(*) FROM emails")
            domains = await c.fetchval("SELECT COUNT(*) FROM domains")
            cards = await c.fetchval("SELECT COUNT(*) FROM cards")

        return await m.answer(
f"""👤 Профиль

🆔 ID: {uid}
🎭 Роль: {role}

📧 Почты: {emails}
🌐 Домены: {domains}
💳 Карты: {cards}"""
        )

    # EMAIL
    if text == "📧 Почта":
        async with pool.acquire() as c:
            row = await c.fetchrow("SELECT value FROM emails LIMIT 1")
            if not row:
                return await m.answer("нет")

            await c.execute("DELETE FROM emails WHERE value=$1", row["value"])
            return await m.answer(row["value"])

    # DOMAIN
    if text == "🌐 Домен":
        async with pool.acquire() as c:
            row = await c.fetchrow("SELECT value FROM domains LIMIT 1")
            if not row:
                return await m.answer("нет")

            await c.execute("DELETE FROM domains WHERE value=$1", row["value"])
            return await m.answer(row["value"])

    # ACCESS (общий список)
    if text == "🔑 Доступы":
        async with pool.acquire() as c:
            rows = await c.fetch("SELECT value FROM accesses")

        return await m.answer("\n".join(r["value"] for r in rows) or "нет")

    # MANUALS
    if text == "📚 Мануалы":
        async with pool.acquire() as c:
            rows = await c.fetch("SELECT value FROM manuals")

        return await m.answer("\n".join(r["value"] for r in rows) or "нет")

    # ADMIN
    if text == "🛠 Админ" and uid == ADMIN_ID:
        return await m.answer("⚙️", reply_markup=admin_kb)

# ================= CALLBACK =================
@router.callback_query()
async def cb(c: CallbackQuery):
    uid = c.from_user.id
    data = c.data
    await c.answer()

    if data == "card_1":
        card, err = await get_card(uid, "1")
        return await c.message.answer(err or card, reply_markup=retry1)

    if data == "card_2":
        card, err = await get_card(uid, "2")
        return await c.message.answer(err or card, reply_markup=retry2)

    if data == "retry_1":
        card, err = await get_card(uid, "1")
        return await c.message.answer(err or card, reply_markup=retry1)

    if data == "retry_2":
        card, err = await get_card(uid, "2")
        return await c.message.answer(err or card, reply_markup=retry2)

    if uid == ADMIN_ID:
        admin_state[uid] = data
        return await c.message.answer("отправь данные")

# ================= RUN =================
async def main():
    await init_db()
    dp.include_router(router)
    print("BOT READY")
    await dp.start_polling(bot)

if __name__ == "__main__":
    threading.Thread(target=web).start()
    asyncio.run(main())
