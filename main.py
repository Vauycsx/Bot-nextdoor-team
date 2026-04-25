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
    CallbackQuery
)

from fastapi import FastAPI
import uvicorn
import threading

# ===== ENV (Render) =====
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7062911219"))
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", 10000))

bot = Bot(TOKEN)
dp = Dispatcher()
router = Router()

app = FastAPI()

@app.get("/")
def home():
    return {"status": "bot running"}

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
            value TEXT
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
        await conn.execute(
            "INSERT INTO users(id, role) VALUES($1,$2) ON CONFLICT (id) DO NOTHING",
            uid, role
        )

async def get_card(uid, second=False):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT last_used FROM card_usage WHERE user_id=$1", uid)
        now = datetime.utcnow()

        if row and row["last_used"]:
            if now - row["last_used"] < timedelta(minutes=random.randint(10, 30)) and not second:
                return None, "⏳ подожди"

        cards = await conn.fetch("SELECT value FROM cards")
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

# ===== BOT HANDLER =====

@router.message()
async def handler(msg: Message):
    uid = msg.from_user.id
    text = msg.text

    user = await get_user(uid)

    if text == "/start":
        if not user and uid != ADMIN_ID:
            return await msg.answer("🔐 код")
        await set_user(uid, "admin" if uid == ADMIN_ID else "user")
        return await msg.answer("welcome", reply_markup=get_menu(uid))

    if text == "💳 Карты":
        return await msg.answer("Выбери карту", reply_markup=cards_kb)

# ===== CALLBACK =====

@router.callback_query()
async def cb(c: CallbackQuery):
    await c.answer()
    uid = c.from_user.id

    if c.data == "card_1":
        card, err = await get_card(uid)
        return await c.message.answer(err or f"🎴 {card}")

    if c.data == "card_2":
        card, err = await get_card(uid, second=True)
        return await c.message.answer(err or f"🎴 {card}")

# ===== RUN BOT =====

async def run_bot():
    await init_db()
    dp.include_router(router)
    print("BOT RUNNING")
    await dp.start_polling(bot)

# ===== RUN WEB =====

def run_web():
    uvicorn.run(app, host="0.0.0.0", port=PORT)

# ===== MAIN =====

async def main():
    threading.Thread(target=run_web).start()
    await run_bot()

if __name__ == "__main__":
    asyncio.run(main())
