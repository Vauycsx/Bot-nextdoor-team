import asyncio
import asyncpg
import random
import os
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, Router
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery,
    FSInputFile
)

from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

# ===== ENV =====
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7062911219"))
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", 10000))

bot = Bot(TOKEN)
dp = Dispatcher()
router = Router()

# ===== WEB SERVER =====
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"status":"bot running"}')

def run_web():
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()

# ===== STATE =====
admin_mode = {}
pool: asyncpg.Pool = None

# ===== DB =====
async def init_db():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL)

    async with pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id BIGINT PRIMARY KEY,
            role TEXT
        );
        """)

        await conn.execute("CREATE TABLE IF NOT EXISTS codes (code TEXT);")
        await conn.execute("CREATE TABLE IF NOT EXISTS emails (value TEXT);")
        await conn.execute("CREATE TABLE IF NOT EXISTS domains (value TEXT);")
        await conn.execute("CREATE TABLE IF NOT EXISTS accesses (value TEXT);")
        await conn.execute("CREATE TABLE IF NOT EXISTS manuals (value TEXT);")

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            id SERIAL PRIMARY KEY,
            value TEXT,
            category TEXT
        );
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS card_usage (
            user_id BIGINT PRIMARY KEY,
            last_used TIMESTAMP
        );
        """)

# ===== UI =====

def get_menu(uid):
    base = [
        [KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="📧 Почта"), KeyboardButton(text="🌐 Домен")],
        [KeyboardButton(text="🔑 Доступы"), KeyboardButton(text="📚 Мануалы")],
        [KeyboardButton(text="💳 Карты")]
    ]
    if uid == ADMIN_ID:
        base.append([KeyboardButton(text="🛠 Админ")])
    return ReplyKeyboardMarkup(keyboard=base, resize_keyboard=True)

admin_menu = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="➕ Код", callback_data="code")],
    [InlineKeyboardButton(text="➕ Почта", callback_data="email")],
    [InlineKeyboardButton(text="➕ Домен", callback_data="domain")],
    [InlineKeyboardButton(text="➕ Доступ", callback_data="access")],
    [InlineKeyboardButton(text="➕ Мануал", callback_data="manual")],
    [InlineKeyboardButton(text="💳 Карты 1", callback_data="cards_1")],
    [InlineKeyboardButton(text="💳 Карты 2", callback_data="cards_2")],
    [InlineKeyboardButton(text="📤 Логи", callback_data="logs")]
])

cards_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Первая карта", callback_data="card_1")],
    [InlineKeyboardButton(text="Вторая карта", callback_data="card_2")]
])

# ===== HELPERS =====

async def get_user(uid):
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE id=$1", uid)

async def set_user(uid, role="user"):
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO users(id, role) VALUES($1,$2) ON CONFLICT (id) DO NOTHING", uid, role)

async def get_card(uid, category, second=False):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT last_used FROM card_usage WHERE user_id=$1", uid)
        now = datetime.utcnow()

        if row and row["last_used"]:
            if now - row["last_used"] < timedelta(minutes=random.randint(10,30)) and not second:
                return None, "⏳ подожди"

        cards = await conn.fetch("SELECT value FROM cards WHERE category=$1", category)
        if not cards:
            return None, "нет карт"

        card = random.choice(cards)["value"]

        await conn.execute("""
            INSERT INTO card_usage(user_id,last_used)
            VALUES($1,$2)
            ON CONFLICT (user_id)
            DO UPDATE SET last_used=$2
        """, uid, now)

        return card, None

# ===== HANDLER =====

@router.message()
async def handler(msg: Message):
    uid = msg.from_user.id
    text = msg.text

    user = await get_user(uid)

    if uid == ADMIN_ID and uid in admin_mode:
        mode = admin_mode[uid]
        items = [x.strip() for x in text.split() if x.strip()]

        async with pool.acquire() as conn:
            for item in items:
                await conn.execute("INSERT INTO cards(value, category) VALUES($1,$2)", item, mode)

        del admin_mode[uid]
        return await msg.answer(f"✅ добавлено карт: {len(items)}")

    if text == "/start":
        if not user and uid != ADMIN_ID:
            return await msg.answer("🔐 код")
        await set_user(uid, "admin" if uid == ADMIN_ID else "user")
        return await msg.answer("welcome", reply_markup=get_menu(uid))

    if text == "💳 Карты":
        return await msg.answer("выбери", reply_markup=cards_kb)

    if text == "🛠 Админ" and uid == ADMIN_ID:
        return await msg.answer("admin", reply_markup=admin_menu)

# ===== CALLBACK =====

@router.callback_query()
async def cb(c: CallbackQuery):
    await c.answer()
    uid = c.from_user.id

    if uid != ADMIN_ID:
        return

    if c.data == "card_1":
        card, err = await get_card(uid, "1")
        return await c.message.answer(err or f"🎴 {card}")

    if c.data == "card_2":
        card, err = await get_card(uid, "2", second=True)
        return await c.message.answer(err or f"🎴 {card}")

    if c.data in ["cards_1", "cards_2"]:
        admin_mode[uid] = "1" if c.data == "cards_1" else "2"
        return await c.message.answer("отправь карты (через пробел или перенос строки)")

    if c.data in ["code","email","domain","access","manual"]:
        admin_mode[uid] = c.data + "s"
        return await c.message.answer("введи значение")

    if c.data == "logs":
        try:
            return await c.message.answer_document(FSInputFile("logs.txt"))
        except:
            return await c.message.answer("логов нет")

# ===== RUN =====

async def run_bot():
    await init_db()
    dp.include_router(router)
    await dp.start_polling(bot)


def main():
    threading.Thread(target=run_web).start()
    asyncio.run(run_bot())

if __name__ == "__main__":
    main()
