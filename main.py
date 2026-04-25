import asyncio
import asyncpg
import os
import threading

from aiogram import Bot, Dispatcher, Router
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery
)

from http.server import BaseHTTPRequestHandler, HTTPServer

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

# ================= WEB (Render fix) =================
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_web():
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

# ================= DB =================
async def init_db():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL)

    async with pool.acquire() as c:
        await c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id BIGINT PRIMARY KEY,
            role TEXT
        );
        """)

        await c.execute("CREATE TABLE IF NOT EXISTS codes (code TEXT)")
        await c.execute("CREATE TABLE IF NOT EXISTS emails (value TEXT)")
        await c.execute("CREATE TABLE IF NOT EXISTS domains (value TEXT)")
        await c.execute("CREATE TABLE IF NOT EXISTS accesses (value TEXT)")
        await c.execute("CREATE TABLE IF NOT EXISTS manuals (value TEXT)")

        await c.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            id SERIAL PRIMARY KEY,
            value TEXT,
            category TEXT
        );
        """)

# ================= STATE =================
user_index = {}  # (uid, category) -> index

# ================= UI =================
def menu(uid):
    kb = [
        [KeyboardButton(text="👤 Профиль")],
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
    [InlineKeyboardButton(text="➕ Карты 1", callback_data="add_card1")],
    [InlineKeyboardButton(text="➕ Карты 2", callback_data="add_card2")],
])

cards_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Карта 1", callback_data="card1")],
    [InlineKeyboardButton(text="Карта 2", callback_data="card2")]
])

# ================= CARDS LOGIC =================
def get_next_card(uid, cat):
    cur = conn.cursor()
    cur.execute("SELECT value FROM cards WHERE category=? ORDER BY id", (cat,))
    rows = [r[0] for r in cur.fetchall()]

    if not rows:
        return None

    key = (uid, cat)
    idx = user_index.get(key, 0)

    card = rows[idx % len(rows)]
    user_index[key] = idx + 1

    return card

# ================= MESSAGE =================
@router.message()
async def msg(m: Message):
    uid = m.from_user.id
    text = m.text

    if text == "/start":
        return await m.answer("бот активен", reply_markup=menu(uid))

    if text == "💳 Карты":
        return await m.answer("выбор:", reply_markup=cards_kb)

    if text == "🛠 Админ" and uid == ADMIN_ID:
        return await m.answer("админ", reply_markup=admin_kb)

# ================= ADMIN INPUT =================
@router.message()
async def admin_input(m: Message):
    uid = m.from_user.id

    if uid != ADMIN_ID or uid not in admin_mode:
        return

    mode = admin_mode[uid]
    del admin_mode[uid]

    items = [x.strip() for x in m.text.split() if x.strip()]

    async with pool.acquire() as c:

        # ===== ADD SIMPLE =====
        if mode == "add_email":
            for i in items:
                await c.execute("INSERT INTO emails VALUES($1)", i)

        if mode == "add_domain":
            for i in items:
                await c.execute("INSERT INTO domains VALUES($1)", i)

        if mode == "add_access":
            for i in items:
                await c.execute("INSERT INTO accesses VALUES($1)", i)

        if mode == "add_manual":
            for i in items:
                await c.execute("INSERT INTO manuals VALUES($1)", i)

        # ===== ADD CARDS =====
        if mode in ("add_card1", "add_card2"):
            cat = "1" if mode == "add_card1" else "2"
            for i in items:
                await c.execute("INSERT INTO cards(value,category) VALUES($1,$2)", i, cat)

    await m.answer(f"✅ добавлено: {len(items)}")

# ================= CALLBACK =================
@router.callback_query()
async def cb(c: CallbackQuery):
    await c.answer()
    uid = c.from_user.id

    async with pool.acquire() as conn:

        # ================= CARDS =================
        if c.data == "card1":
            card = get_next_card(uid, "1")

            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ не подошла", callback_data="retry1")]
            ])

            return await c.message.answer(card or "нет карт", reply_markup=kb)

        if c.data == "card2":
            card = get_next_card(uid, "2")

            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ не подошла", callback_data="retry2")]
            ])

            return await c.message.answer(card or "нет карт", reply_markup=kb)

        # ================= RETRY =================
        if c.data == "retry1":
            card = get_next_card(uid, "1")

            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ не подошла", callback_data="retry1")]
            ])

            return await c.message.answer(card or "нет карт", reply_markup=kb)

        if c.data == "retry2":
            card = get_next_card(uid, "2")

            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ не подошла", callback_data="retry2")]
            ])

            return await c.message.answer(card or "нет карт", reply_markup=kb)

        # ================= ADMIN =================
        if uid == ADMIN_ID:

            admin_mode[uid] = c.data

            if c.data in ("add_email", "add_domain", "add_access", "add_manual", "add_card1", "add_card2"):
                return await c.message.answer("отправь данные")

            if c.data == "stats":
                emails = await conn.fetchval("SELECT COUNT(*) FROM emails")
                domains = await conn.fetchval("SELECT COUNT(*) FROM domains")
                access = await conn.fetchval("SELECT COUNT(*) FROM accesses")
                manuals = await conn.fetchval("SELECT COUNT(*) FROM manuals")
                cards1 = await conn.fetchval("SELECT COUNT(*) FROM cards WHERE category='1'")
                cards2 = await conn.fetchval("SELECT COUNT(*) FROM cards WHERE category='2'")

                return await c.message.answer(
f"""📊 СТАТИСТИКА

📧 почты: {emails}
🌐 домены: {domains}
🔑 доступы: {access}
📚 мануалы: {manuals}

💳 карты 1: {cards1}
💳 карты 2: {cards2}"""
                )

# ================= RUN =================
async def main():
    global conn
    await init_db()
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    threading.Thread(target=run_web).start()
    asyncio.run(main())
