import asyncio
import os
import asyncpg
import time
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton

from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext


TOKEN = os.getenv("BOT_TOKEN")
DB_URL = os.getenv("DATABASE_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

bot = Bot(TOKEN)
dp = Dispatcher()

pool = None

pending_users = {}
last_cards = {}

# ===== SIMPLE RATE LIMIT =====
user_last_action = {}
RATE_LIMIT = 1.0  # sec


def check_spam(user_id):
    now = time.time()
    last = user_last_action.get(user_id, 0)

    if now - last < RATE_LIMIT:
        return True

    user_last_action[user_id] = now
    return False


# ===== FSM ADMIN =====
class AdminState(StatesGroup):
    menu = State()
    add_code = State()
    add_email = State()
    add_domain = State()
    add_access = State()
    add_manual = State()
    delete_mode = State()


# ===== WEB FOR RENDER =====
async def health(request):
    return web.Response(text="OK")


async def web():
    app = web.Application()
    app.router.add_get("/", health)

    port = int(os.getenv("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


# ===== DB =====
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
            id SERIAL,
            value TEXT,
            category TEXT,
            used BOOLEAN DEFAULT FALSE
        );

        CREATE TABLE IF NOT EXISTS accesses(name TEXT);
        CREATE TABLE IF NOT EXISTS manuals(name TEXT);
        """)


# ===== KEYBOARDS =====
def kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("📧 Почта"), KeyboardButton("🌐 Домен")],
            [KeyboardButton("🃏 Карты"), KeyboardButton("👤 Профиль")],
            [KeyboardButton("🔑 Доступы"), KeyboardButton("📚 Мануалы")]
        ],
        resize_keyboard=True
    )


def admin_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            ["➕ Код", "📧 Email"],
            ["🌐 Домен", "🔑 Доступ"],
            ["📚 Мануал", "🗑 Удаление"],
            ["♻️ Сброс карт", "❌ Выход"]
        ],
        resize_keyboard=True
    )


# ===== START =====
@dp.message(F.text == "/start")
async def start(msg: Message):
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE id=$1", msg.from_user.id)

    if not user:
        pending_users[msg.from_user.id] = True
        return await msg.answer("🔐 код доступа")

    await msg.answer("бот активен", reply_markup=kb())


# ===== MAIN HANDLER =====
@dp.message()
async def handler(msg: Message, state: FSMContext):

    if check_spam(msg.from_user.id):
        return await msg.answer("слишком быстро")

    uid = msg.from_user.id

    # ===== AUTH =====
    if uid in pending_users:
        async with pool.acquire() as conn:
            code = await conn.fetchrow(
                "SELECT * FROM access_codes WHERE code=$1",
                msg.text
            )

            if not code:
                return await msg.answer("❌ неверный код")

            await conn.execute(
                "INSERT INTO users(id, role) VALUES($1,'user')",
                uid
            )

        del pending_users[uid]
        return await msg.answer("✔ доступ открыт", reply_markup=kb())


    # ===== PROFILE =====
    if msg.text == "👤 Профиль":
        async with pool.acquire() as conn:
            user = await conn.fetchrow("SELECT * FROM users WHERE id=$1", uid)

        return await msg.answer(f"ID: {uid}\nRole: {user['role']}")


    # ===== CARDS (QUEUE SYSTEM) =====
    if msg.text == "🃏 Карты":
        kb_cat = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton("Первая"), KeyboardButton("Вторая")]],
            resize_keyboard=True
        )
        return await msg.answer("категория", reply_markup=kb_cat)


    if msg.text in ["Первая", "Вторая"]:
        category = msg.text.lower()

        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT * FROM cards
                WHERE category=$1 AND used=false
                ORDER BY id ASC
                LIMIT 1
            """, category)

            if not row:
                return await msg.answer("нет карт")

            await conn.execute(
                "UPDATE cards SET used=true WHERE id=$1",
                row["id"]
            )

        last_cards[uid] = category
        return await msg.answer(f"🃏 {row['value']}")


    # ===== MORE CARDS =====
    if msg.text == "🔁 Ещё":
        category = last_cards.get(uid)
        if not category:
            return await msg.answer("сначала выбери")

        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT * FROM cards
                WHERE category=$1 AND used=false
                ORDER BY id ASC
                LIMIT 1
            """, category)

            if not row:
                return await msg.answer("карты закончились")

            await conn.execute("UPDATE cards SET used=true WHERE id=$1", row["id"])

        return await msg.answer(row["value"])


    # ===== ADMIN =====
    if msg.text == "🛠 Админ" and uid == ADMIN_ID:
        await state.set_state(AdminState.menu)
        return await msg.answer("админ панель", reply_markup=admin_kb())


# ===== ADMIN MENU =====
@dp.message(AdminState.menu)
async def admin(msg: Message, state: FSMContext):

    if msg.text == "❌ Выход":
        await state.clear()
        return await msg.answer("выход", reply_markup=kb())

    if msg.text == "➕ Код":
        await state.set_state(AdminState.add_code)
        return await msg.answer("код:")

    if msg.text == "📧 Email":
        await state.set_state(AdminState.add_email)
        return await msg.answer("email:")

    if msg.text == "🌐 Домен":
        await state.set_state(AdminState.add_domain)
        return await msg.answer("домен:")

    if msg.text == "🔑 Доступ":
        await state.set_state(AdminState.add_access)
        return await msg.answer("доступ:")

    if msg.text == "📚 Мануал":
        await state.set_state(AdminState.add_manual)
        return await msg.answer("мануал:")


# ===== SIMPLE ADD SYSTEM =====
async def insert(table, column, value):
    async with pool.acquire() as conn:
        await conn.execute(f"INSERT INTO {table}({column}) VALUES($1)", value)


@dp.message(AdminState.add_email)
async def add_email(msg: Message, state: FSMContext):
    await insert("emails", "value", msg.text)
    await state.set_state(AdminState.menu)
    await msg.answer("ok")


@dp.message(AdminState.add_domain)
async def add_domain(msg: Message, state: FSMContext):
    await insert("domains", "value", msg.text)
    await state.set_state(AdminState.menu)
    await msg.answer("ok")


@dp.message(AdminState.add_access)
async def add_access(msg: Message, state: FSMContext):
    await insert("accesses", "name", msg.text)
    await state.set_state(AdminState.menu)
    await msg.answer("ok")


@dp.message(AdminState.add_manual)
async def add_manual(msg: Message, state: FSMContext):
    await insert("manuals", "name", msg.text)
    await state.set_state(AdminState.menu)
    await msg.answer("ok")


# ===== MAIN =====
async def main():
    await init_db()
    asyncio.create_task(web())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
