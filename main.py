import asyncio
import os
import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton
)

TOKEN = os.getenv("BOT_TOKEN")
DB_URL = os.getenv("DATABASE_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

bot = Bot(TOKEN)
dp = Dispatcher()

pool = None

# ===== STATE =====
pending_users = {}
last_cards = {}

# ===== DB INIT =====
async def init_db():
    global pool
    pool = await asyncpg.create_pool(DB_URL)

    async with pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users(
            id BIGINT PRIMARY KEY,
            role TEXT DEFAULT 'user',
            banned BOOLEAN DEFAULT FALSE
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
            status TEXT,
            time TIMESTAMP DEFAULT NOW()
        );
        """)


# ===== KEYBOARD =====
def get_kb(is_admin=False):
    kb = [
        [KeyboardButton(text="📧 Почта"), KeyboardButton(text="🌐 Домен")],
        [KeyboardButton(text="🃏 Карты"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="🔑 Доступы"), KeyboardButton(text="📚 Мануалы")]
    ]

    if is_admin:
        kb.append([KeyboardButton(text="🛠 Админ")])

    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


# ===== START / AUTH =====
@dp.message(F.text == "/start")
async def start(msg: Message):
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE id=$1", msg.from_user.id)

    if not user:
        pending_users[msg.from_user.id] = True
        return await msg.answer("🔐 Введи код доступа")

    await msg.answer("Бот работает", reply_markup=get_kb(msg.from_user.id == ADMIN_ID))


@dp.message()
async def handle_all(msg: Message):
    user_id = msg.from_user.id

    # ===== AUTH =====
    if user_id in pending_users:
        async with pool.acquire() as conn:
            code = await conn.fetchrow("SELECT * FROM access_codes WHERE code=$1", msg.text)

            if not code:
                return await msg.answer("❌ Неверный код")

            role = "admin" if user_id == ADMIN_ID else "user"
            await conn.execute("INSERT INTO users(id, role) VALUES($1,$2)", user_id, role)

        del pending_users[user_id]
        return await msg.answer("✅ Доступ разрешен", reply_markup=get_kb(user_id == ADMIN_ID))

    # ===== PROFILE =====
    if msg.text == "👤 Профиль":
        async with pool.acquire() as conn:
            user = await conn.fetchrow("SELECT * FROM users WHERE id=$1", user_id)
            logs = await conn.fetchval("SELECT COUNT(*) FROM logs WHERE user_id=$1", user_id)

        return await msg.answer(
            f"👤 ID: {user_id}\n"
            f"Role: {user['role']}\n"
            f"Действий: {logs}"
        )

    # ===== EMAIL =====
    if msg.text == "📧 Почта":
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM emails LIMIT 1")

            if not row:
                return await msg.answer("нет почт")

            await conn.execute("DELETE FROM emails WHERE value=$1", row["value"])
            await conn.execute(
                "INSERT INTO logs(user_id,action,item) VALUES($1,'email',$2)",
                user_id, row["value"]
            )

        return await msg.answer(row["value"])

    # ===== DOMAIN =====
    if msg.text == "🌐 Домен":
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM domains LIMIT 1")

            if not row:
                return await msg.answer("нет доменов")

            await conn.execute("DELETE FROM domains WHERE value=$1", row["value"])
            await conn.execute(
                "INSERT INTO logs(user_id,action,item) VALUES($1,'domain',$2)",
                user_id, row["value"]
            )

        return await msg.answer(row["value"])

    # ===== CARDS MENU =====
    if msg.text == "🃏 Карты":
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Первая"), KeyboardButton(text="Вторая")],
                [KeyboardButton(text="⬅ Назад")]
            ],
            resize_keyboard=True
        )
        return await msg.answer("Выбери категорию", reply_markup=kb)

    # ===== CARD CATEGORY =====
    if msg.text in ["Первая", "Вторая"]:
        category = msg.text.lower()

        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT * FROM cards WHERE category=$1 LIMIT 1
            """, category)

        if not row:
            return await msg.answer("нет карт")

        last_cards[user_id] = category

        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="🔁 Ещё")]],
            resize_keyboard=True
        )

        return await msg.answer(f"🃏 {row['value']}", reply_markup=kb)

    # ===== MORE =====
    if msg.text == "🔁 Ещё":
        category = last_cards.get(user_id)

        if not category:
            return await msg.answer("сначала выбери категорию")

        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT * FROM cards WHERE category=$1 LIMIT 1
            """, category)

        if not row:
            return await msg.answer("нет карт")

        return await msg.answer(f"🃏 {row['value']}")

    # ===== ACCESSES =====
    if msg.text == "🔑 Доступы":
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM accesses")

        if not rows:
            return await msg.answer("нет доступов")

        return await msg.answer("\n".join([r["name"] for r in rows]))

    # ===== MANUALS =====
    if msg.text == "📚 Мануалы":
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM manuals")

        if not rows:
            return await msg.answer("нет мануалов")

        return await msg.answer("\n".join([r["name"] for r in rows]))

    # ===== ADMIN PANEL =====
    if msg.text == "🛠 Админ" and user_id == ADMIN_ID:
        return await msg.answer(
            "🛠 Админ панель:\n"
            "/addcode\n/addemail\n/adddomain\n/addaccess\n/addmanual\n"
            "/delaccess\n/delman"
        )

    # ===== ADMIN COMMANDS =====
    if user_id == ADMIN_ID:

        if msg.text.startswith("/addcode"):
            return await msg.answer("введи код")

        if msg.text.startswith("/addemail"):
            return await msg.answer("введи email")

        if msg.text.startswith("/adddomain"):
            return await msg.answer("введи домен")

        if msg.text.startswith("/addaccess"):
            return await msg.answer("введи доступ")

        if msg.text.startswith("/addmanual"):
            return await msg.answer("введи мануал")

        if msg.text.startswith("/delaccess"):
            return await msg.answer("напиши название доступа")

        if msg.text.startswith("/delman"):
            return await msg.answer("напиши название мануала")

        # универсальное добавление
        async with pool.acquire() as conn:

            await conn.execute("INSERT INTO emails(value) VALUES($1)", msg.text)
            return await msg.answer("добавлено")

# ===== RUN =====
async def main():
    await init_db()
    print("PRO BOT STARTED")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
