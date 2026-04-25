import asyncio
import asyncpg
import random
import os
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, Router
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, FSInputFile
)

from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

# ===== CONFIG =====
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7062911219"))
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", 10000))

bot = Bot(TOKEN)
dp = Dispatcher()
router = Router()

pool = None
admin_mode = {}

# ===== WEB (Render fix) =====
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_web():
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

# ===== DB INIT =====
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

# ===== UI =====
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
    [InlineKeyboardButton("📊 Статистика", callback_data="stats")],
    [InlineKeyboardButton("🧹 Очистка карт", callback_data="clear_cards")],
    [InlineKeyboardButton("🧹 Очистка почт", callback_data="clear_emails")],
    [InlineKeyboardButton("🧹 Очистка доменов", callback_data="clear_domains")],
    [InlineKeyboardButton("🧹 Очистка доступов", callback_data="clear_access")],
    [InlineKeyboardButton("📤 Выгрузка карт", callback_data="dump_cards")],
    [InlineKeyboardButton("➕ Карты 1", callback_data="add_1")],
    [InlineKeyboardButton("➕ Карты 2", callback_data="add_2")]
])

cards_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton("Первая карта", callback_data="card_1")],
    [InlineKeyboardButton("Вторая карта", callback_data="card_2")]
])

# ===== HELPERS =====
def get_one(table):
    cur = conn.cursor()
    cur.execute(f"SELECT rowid, value FROM {table} LIMIT 1")
    return cur.fetchone()

def count(table):
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    return cur.fetchone()[0]

def get_card(cat):
    cur = conn.cursor()
    cur.execute("SELECT value FROM cards WHERE category=?", (cat,))
    rows = cur.fetchall()
    return random.choice(rows)[0] if rows else None

# ===== MESSAGE =====
@router.message()
async def msg(m: Message):
    uid = m.from_user.id
    text = m.text

    if text == "/start":
        return await m.answer("бот", reply_markup=menu(uid))

    if text == "💳 Карты":
        return await m.answer("выбор", reply_markup=cards_kb)

    if text == "🛠 Админ" and uid == ADMIN_ID:
        return await m.answer("админ панель", reply_markup=admin_kb)

# ===== ADMIN INPUT =====
@router.message()
async def admin_input(m: Message):
    uid = m.from_user.id
    if uid != ADMIN_ID or uid not in admin_mode:
        return

    mode = admin_mode[uid]
    del admin_mode[uid]

    items = [x for x in m.text.split() if x.strip()]

    async with pool.acquire() as c:
        for i in items:
            await c.execute("INSERT INTO cards(value,category) VALUES($1,$2)", i, mode)

    await m.answer(f"добавлено: {len(items)}")

# ===== CALLBACK =====
@router.callback_query()
async def cb(c: CallbackQuery):
    await c.answer()
    uid = c.from_user.id

    async with pool.acquire() as conn:
        # ===== CARDS =====
        if c.data == "card_1":
            return await c.message.answer(get_card("1") or "нет")

        if c.data == "card_2":
            return await c.message.answer(get_card("2") or "нет")

        # ===== ADMIN ADD =====
        if uid == ADMIN_ID:
            if c.data == "add_1":
                admin_mode[uid] = "1"
                return await c.message.answer("карты 1")

            if c.data == "add_2":
                admin_mode[uid] = "2"
                return await c.message.answer("карты 2")

            # ===== STATS =====
            if c.data == "stats":
                emails = await conn.fetchval("SELECT COUNT(*) FROM emails")
                domains = await conn.fetchval("SELECT COUNT(*) FROM domains")
                access = await conn.fetchval("SELECT COUNT(*) FROM accesses")
                cards1 = await conn.fetchval("SELECT COUNT(*) FROM cards WHERE category='1'")
                cards2 = await conn.fetchval("SELECT COUNT(*) FROM cards WHERE category='2'")

                return await c.message.answer(
                    f"""📊 СТАТИСТИКА
📧 почты: {emails}
🌐 домены: {domains}
🔑 доступы: {access}
💳 карты1: {cards1}
💳 карты2: {cards2}"""
                )

            # ===== CLEAR =====
            clears = {
                "clear_emails": "emails",
                "clear_domains": "domains",
                "clear_access": "accesses",
                "clear_cards": "cards"
            }

            if c.data in clears:
                await conn.execute(f"DELETE FROM {clears[c.data]}")
                return await c.message.answer("очищено")

            # ===== DUMP CARDS =====
            if c.data == "dump_cards":
                rows = await conn.fetch("SELECT value,category FROM cards")
                txt = "\n".join([f"{r['value']} | {r['category']}" for r in rows])

                with open("cards.txt", "w", encoding="utf-8") as f:
                    f.write(txt)

                return await c.message.answer_document(FSInputFile("cards.txt"))

# ===== RUN =====
async def main():
    await init_db()
    dp.include_router(router)
    await dp.start_polling(bot)

def start():
    threading.Thread(target=run_web).start()
    asyncio.run(main())

if __name__ == "__main__":
    start()
