import asyncio
import os
import asyncpg
import time
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext


# ================= CONFIG =================
TOKEN = os.getenv("BOT_TOKEN")
DB_URL = os.getenv("DATABASE_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PORT = int(os.getenv("PORT", 8080))

bot = Bot(TOKEN)
dp = Dispatcher()

pool = None

pending_users = {}
last_cards = {}
admin_mode = {}


# ================= WEB =================
async def health(request):
    return web.Response(text="OK")


async def start_web():
    app = web.Application()
    app.router.add_get("/", health)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()


# ================= DB =================
async def init_db():
    global pool
    pool = await asyncpg.create_pool(DB_URL)

    async with pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users(
            id BIGINT PRIMARY KEY,
            role TEXT DEFAULT 'user'
        );

        CREATE TABLE IF NOT EXISTS access_codes(code TEXT);

        CREATE TABLE IF NOT EXISTS emails(value TEXT);
        CREATE TABLE IF NOT EXISTS domains(value TEXT);

        CREATE TABLE IF NOT EXISTS cards(
            value TEXT,
            category TEXT,
            used BOOLEAN DEFAULT FALSE
        );

        CREATE TABLE IF NOT EXISTS accesses(name TEXT);
        CREATE TABLE IF NOT EXISTS manuals(name TEXT);

        CREATE TABLE IF NOT EXISTS logs(
            id SERIAL,
            user_id BIGINT,
            action TEXT,
            item TEXT,
            time TIMESTAMP DEFAULT NOW()
        );
        """)


# ================= KEYBOARDS =================
def main_kb(is_admin=False):
    kb = [
        [KeyboardButton("📧 Почта"), KeyboardButton("🌐 Домен")],
        [KeyboardButton("🃏 Карты"), KeyboardButton("👤 Профиль")],
        [KeyboardButton("🔑 Доступы"), KeyboardButton("📚 Мануалы")]
    ]

    if is_admin:
        kb.append([KeyboardButton("🛠 Админ панель")])

    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def admin_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("➕ Добавить"), KeyboardButton("🗑 Удалить")],
            [KeyboardButton("📊 Статистика"), KeyboardButton("♻️ Сброс карт")],
            [KeyboardButton("⬅ Выход")]
        ],
        resize_keyboard=True
    )


def add_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("Email"), KeyboardButton("Домен")],
            [KeyboardButton("Доступ"), KeyboardButton("Мануал")],
            [KeyboardButton("Код"), KeyboardButton("⬅ Назад")]
        ],
        resize_keyboard=True
    )


# ================= START =================
@dp.message(F.text == "/start")
async def start(msg: Message):
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE id=$1", msg.from_user.id)

        if not user:
            await conn.execute("INSERT INTO users(id, role) VALUES($1,'user')", msg.from_user.id)

    await msg.answer("бот запущен", reply_markup=main_kb(msg.from_user.id == ADMIN_ID))


# ================= PROFILE =================
@dp.message(F.text == "👤 Профиль")
async def profile(msg: Message):
    await msg.answer(f"ID: {msg.from_user.id}")


# ================= EMAIL =================
@dp.message(F.text == "📧 Почта")
async def email(msg: Message):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM emails LIMIT 1")

        if not row:
            return await msg.answer("нет почт")

        await conn.execute("DELETE FROM emails WHERE value=$1", row["value"])

    await msg.answer(row["value"])


# ================= DOMAIN =================
@dp.message(F.text == "🌐 Домен")
async def domain(msg: Message):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM domains LIMIT 1")

        if not row:
            return await msg.answer("нет доменов")

        await conn.execute("DELETE FROM domains WHERE value=$1", row["value"])

    await msg.answer(row["value"])


# ================= ACCESS =================
@dp.message(F.text == "🔑 Доступы")
async def accesses(msg: Message):
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM accesses")

    await msg.answer("\n".join([r["name"] for r in rows]) or "нет")


# ================= MANUALS =================
@dp.message(F.text == "📚 Мануалы")
async def manuals(msg: Message):
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM manuals")

    await msg.answer("\n".join([r["name"] for r in rows]) or "нет")


# ================= CARDS =================
@dp.message(F.text == "🃏 Карты")
async def cards_menu(msg: Message):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("Обычные"), KeyboardButton("Генерки")]
        ],
        resize_keyboard=True
    )
    await msg.answer("выбери", reply_markup=kb)


async def get_card(user_id, category):
    async with pool.acquire() as conn:

        user = await conn.fetchrow("SELECT * FROM users WHERE id=$1", user_id)
        last = user.get("last_category") if user else None

        if last == category:
            return None

        row = await conn.fetchrow("""
            SELECT * FROM cards
            WHERE category=$1 AND used=false
            ORDER BY id ASC
            LIMIT 1
        """, category)

        if not row:
            return None

        await conn.execute("UPDATE cards SET used=true WHERE id=$1", row["id"])
        await conn.execute("UPDATE users SET last_category=$1 WHERE id=$2", category, user_id)

        return row["value"]


@dp.message(F.text.in_(["Обычные", "Генерки"]))
async def cards(msg: Message):
    cat = "normal" if msg.text == "Обычные" else "gen"

    value = await get_card(msg.from_user.id, cat)

    if not value:
        return await msg.answer("нет карт или нельзя подряд")

    await msg.answer(value)


# ================= ADMIN =================
@dp.message(F.text == "🛠 Админ панель")
async def admin(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return await msg.answer("нет доступа")

    admin_mode[msg.from_user.id] = True
    await msg.answer("админ панель", reply_markup=admin_kb())


@dp.message(F.text == "⬅ Выход")
async def exit_admin(msg: Message):
    admin_mode[msg.from_user.id] = False
    await msg.answer("вышел", reply_markup=main_kb(True))


# ================= ADMIN ADD =================
pending_add = {}

@dp.message(F.text == "➕ Добавить")
async def add_menu(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return

    await msg.answer("что добавить?", reply_markup=add_kb())


@dp.message(F.text.in_(["Email", "Домен", "Доступ", "Мануал", "Код"]))
async def choose_add(msg: Message, state: FSMContext):
    pending_add[msg.from_user.id] = msg.text
    await msg.answer("введи значение")


@dp.message()
async def add_value(msg: Message):
    uid = msg.from_user.id

    if uid != ADMIN_ID:
        return

    if uid not in pending_add:
        return

    t = pending_add[uid]

    async with pool.acquire() as conn:

        if t == "Email":
            await conn.execute("INSERT INTO emails(value) VALUES($1)", msg.text)

        elif t == "Домен":
            await conn.execute("INSERT INTO domains(value) VALUES($1)", msg.text)

        elif t == "Доступ":
            await conn.execute("INSERT INTO accesses(name) VALUES($1)", msg.text)

        elif t == "Мануал":
            await conn.execute("INSERT INTO manuals(name) VALUES($1)", msg.text)

        elif t == "Код":
            await conn.execute("INSERT INTO access_codes(code) VALUES($1)", msg.text)

    del pending_add[uid]
    await msg.answer("добавлено")


# ================= MAIN =================
async def main():
    await init_db()
    await bot.delete_webhook(drop_pending_updates=True)

    asyncio.create_task(start_web())

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
