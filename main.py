import os
import asyncpg
from datetime import datetime

from aiohttp import web
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

# ================== ENV ==================
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")

WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = os.getenv("WEBHOOK_URL") + WEBHOOK_PATH

# ================== BOT ==================
bot = Bot(TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# ================== DB ==================
pool: asyncpg.Pool = None

async def init_db():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL)

    async with pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users(
            id BIGINT PRIMARY KEY,
            role TEXT
        );

        CREATE TABLE IF NOT EXISTS codes(code TEXT);

        CREATE TABLE IF NOT EXISTS emails(id SERIAL PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS domains(id SERIAL PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS accesses(value TEXT);
        CREATE TABLE IF NOT EXISTS manuals(value TEXT);

        CREATE TABLE IF NOT EXISTS cards(
            id SERIAL PRIMARY KEY,
            value TEXT,
            category TEXT
        );
        """)

# ================== STATE ==================
last_item = {}
last_card = {}
admin_mode = {}

# ================== KEYBOARDS ==================
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

card_kb = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="🟢 Обычные", callback_data="card_normal"),
        InlineKeyboardButton(text="🟣 Генерки", callback_data="card_gen")
    ]
])

again_normal = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🔁 Ещё раз", callback_data="again_normal")]
])

again_gen = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🔁 Ещё раз", callback_data="again_gen")]
])

# ================== HELPERS ==================
async def get_user(uid):
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE id=$1", uid)

async def log(uid, action, item):
    with open("logs.txt", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now()} | {uid} | {action} | {item}\n")

# ================== START ==================
@router.message(F.text == "/start")
async def start(msg: Message):
    user = await get_user(msg.from_user.id)

    if not user and msg.from_user.id != ADMIN_ID:
        return await msg.answer("🔐 Введи код доступа")

    await msg.answer("🚀 CRM готова", reply_markup=menu(msg.from_user.id))

# ================== AUTH ==================
@router.message()
async def auth(msg: Message):
    uid = msg.from_user.id
    text = msg.text

    user = await get_user(uid)

    if not user and uid != ADMIN_ID:
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM codes WHERE code=$1", text)

            if not row:
                return await msg.answer("❌ неверный код")

            await conn.execute("DELETE FROM codes WHERE code=$1", text)
            await conn.execute("INSERT INTO users VALUES ($1,$2)", uid, "user")

        return await msg.answer("✅ доступ открыт", reply_markup=menu(uid))

    if text == "👤 Профиль":
        role = "admin" if uid == ADMIN_ID else user["role"]
        return await msg.answer(f"👤 ID: {uid}\n🎭 Роль: {role}")

    if text == "💳 Карты":
        return await msg.answer("Выбери категорию:", reply_markup=card_kb)

# ================== CARDS ==================
async def get_card(cat, exclude=None):
    async with pool.acquire() as conn:
        if exclude:
            return await conn.fetchrow("""
                SELECT * FROM cards
                WHERE category=$1 AND id != $2
                ORDER BY RANDOM()
                LIMIT 1
            """, cat, exclude)

        return await conn.fetchrow("""
            SELECT * FROM cards
            WHERE category=$1
            ORDER BY RANDOM()
            LIMIT 1
        """, cat)

async def send_card(cb, uid, cat):
    card = await get_card(cat)

    if not card:
        return await cb.message.answer("❌ нет карт")

    last_card.setdefault(uid, {})[cat] = card["id"]

    kb = again_normal if cat == "normal" else again_gen

    await cb.message.answer(f"💳 Карта:\n{card['value']}", reply_markup=kb)

# ================== CALLBACK ==================
@router.callback_query()
async def cb(cb: CallbackQuery):
    await cb.answer()
    uid = cb.from_user.id

    if cb.data == "card_normal":
        return await send_card(cb, uid, "normal")

    if cb.data == "card_gen":
        return await send_card(cb, uid, "gen")

    if cb.data == "again_normal":
        prev = last_card.get(uid, {}).get("normal")
        card = await get_card("normal", prev)

        if not card:
            return await cb.message.answer("❌ нет карт")

        last_card[uid]["normal"] = card["id"]
        return await cb.message.answer(card["value"], reply_markup=again_normal)

    if cb.data == "again_gen":
        prev = last_card.get(uid, {}).get("gen")
        card = await get_card("gen", prev)

        if not card:
            return await cb.message.answer("❌ нет карт")

        last_card[uid]["gen"] = card["id"]
        return await cb.message.answer(card["value"], reply_markup=again_gen)

# ================== STARTUP ==================
async def on_startup(app):
    await init_db()
    await bot.set_webhook(WEBHOOK_URL)
    print("BOT STARTED")

async def on_shutdown(app):
    await bot.delete_webhook()
    await pool.close()

# ================== WEB ==================
async def webhook(request):
    update = await request.json()
    await dp.feed_update(bot, update)
    return web.Response()

app = web.Application()
app.router.add_post(WEBHOOK_PATH, webhook)

app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

# ================== RUN ==================
if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
