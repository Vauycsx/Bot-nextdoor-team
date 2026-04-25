import asyncio
import asyncpg
import random
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery,
    FSInputFile
)

TOKEN = "8703185901:AAHdywgKf48ed-NCxXY1yrF0u1zWWPH9sJw"
ADMIN_ID = 7062911219

bot = Bot(TOKEN)
dp = Dispatcher()
router = Router()

# ===== POSTGRES CONFIG =====
DB_CONFIG = {
    "user": "postgres",
    "password": "password",
    "database": "botdb",
    "host": "localhost"
}

pool: asyncpg.Pool = None

# ===== STATE =====
admin_mode = {}

# ===== INIT DB =====
async def init_db():
    global pool
    pool = await asyncpg.create_pool(**DB_CONFIG)

    async with pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id BIGINT PRIMARY KEY,
            role TEXT
        );
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS codes (
            code TEXT
        );
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS emails (
            value TEXT
        );
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS domains (
            value TEXT
        );
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS accesses (
            value TEXT
        );
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS manuals (
            value TEXT
        );
        """)

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

admin_menu = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="➕ Код", callback_data="code")],
    [InlineKeyboardButton(text="➕ Почта", callback_data="email")],
    [InlineKeyboardButton(text="➕ Домен", callback_data="domain")],
    [InlineKeyboardButton(text="➕ Доступ", callback_data="access")],
    [InlineKeyboardButton(text="➕ Мануал", callback_data="manual")],
    [InlineKeyboardButton(text="➕ Карты", callback_data="card")],
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

async def get_card(uid, second=False):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT last_used FROM card_usage WHERE user_id=$1", uid)

        now = datetime.utcnow()

        if row:
            last = row["last_used"]
            if last and now - last < timedelta(minutes=random.randint(10,30)) and not second:
                return None, "⏳ подожди"

        cards = await conn.fetch("SELECT * FROM cards")
        if not cards:
            return None, "нет карт"

        card = random.choice(cards)["value"]

        await conn.execute("INSERT INTO card_usage(user_id,last_used) VALUES($1,$2) ON CONFLICT (user_id) DO UPDATE SET last_used=$2", uid, now)

        return card, None

# ===== MAIN =====

@router.message()
async def handler(msg: Message):
    uid = msg.from_user.id
    text = msg.text

    user = await get_user(uid)

    # AUTH
    if text == "/start":
        if not user and uid != ADMIN_ID:
            return await msg.answer("🔐 код")
        await set_user(uid, "admin" if uid == ADMIN_ID else "user")
        return await msg.answer("welcome", reply_markup=get_menu(uid))

    if not user and uid != ADMIN_ID:
        async with pool.acquire() as conn:
            valid = await conn.fetchrow("SELECT * FROM codes WHERE code=$1", text)
            if not valid:
                return await msg.answer("❌ неверно")
            await conn.execute("DELETE FROM codes WHERE code=$1", text)
        await set_user(uid)
        return await msg.answer("ok", reply_markup=get_menu(uid))

    # CARDS MENU
    if text == "💳 Карты":
        return await msg.answer("Выбери карту", reply_markup=cards_kb)

    # ADMIN
    if text == "🛠 Админ" and uid == ADMIN_ID:
        return await msg.answer("admin", reply_markup=admin_menu)

# ===== CALLBACK =====

@router.callback_query()
async def cb(c: CallbackQuery):
    uid = c.from_user.id
    data = c.data

    await c.answer()

    # ADMIN BULK CARDS
    if uid == ADMIN_ID and uid in admin_mode:
        table = admin_mode[uid]

        async with pool.acquire() as conn:
            for item in c.message.text.split():
                await conn.execute("INSERT INTO cards(value) VALUES($1)", item)

        del admin_mode[uid]
        return await c.message.answer("cards added")

    if data == "card_1":
        card, err = await get_card(uid)
        if err:
            return await c.message.answer(err)
        return await c.message.answer(f"🎴 {card}")

    if data == "card_2":
        card, err = await get_card(uid, second=True)
        if err:
            return await c.message.answer(err)
        return await c.message.answer(f"🎴 {card}")

    if data == "card":
        admin_mode[uid] = "cards"
        return await c.message.answer("send cards (space separated)")

# ===== RUN =====

async def main():
    await init_db()
    dp.include_router(router)
    print("BOT READY")
    await dp.start_polling(bot)

asyncio.run(main())
