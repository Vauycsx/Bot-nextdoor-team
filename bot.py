import asyncio
import psycopg2
from datetime import datetime
import os

from aiogram import Bot, Dispatcher, Router
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile
)

TOKEN = "TOKEN_BOT"
ADMIN_ID = 6752278578

bot = Bot(TOKEN)
dp = Dispatcher()
router = Router()

# ===== DB (POSTGRESQL) =====
DATABASE_URL = os.getenv("DATABASE_URL")

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

cur.execute("CREATE TABLE IF NOT EXISTS users (id BIGINT PRIMARY KEY, role TEXT)")
cur.execute("CREATE TABLE IF NOT EXISTS codes (id SERIAL PRIMARY KEY, code TEXT)")
cur.execute("CREATE TABLE IF NOT EXISTS emails (id SERIAL PRIMARY KEY, value TEXT)")
cur.execute("CREATE TABLE IF NOT EXISTS domains (id SERIAL PRIMARY KEY, value TEXT)")
cur.execute("CREATE TABLE IF NOT EXISTS accesses (id SERIAL PRIMARY KEY, value TEXT)")
cur.execute("CREATE TABLE IF NOT EXISTS manuals (id SERIAL PRIMARY KEY, value TEXT)")
conn.commit()

# ===== STATE =====
admin_mode = {}
last_item = {}

# ===== UI =====
def get_menu(uid):
    if uid == ADMIN_ID:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="👤 Профиль")],
                [KeyboardButton(text="📧 Почта"), KeyboardButton(text="🌐 Домен")],
                [KeyboardButton(text="🔑 Доступы"), KeyboardButton(text="📚 Мануалы")],
                [KeyboardButton(text="📊 Дашборд")],
                [KeyboardButton(text="🛠 Админ")]
            ],
            resize_keyboard=True
        )
    else:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="👤 Профиль")],
                [KeyboardButton(text="📧 Почта"), KeyboardButton(text="🌐 Домен")],
                [KeyboardButton(text="🔑 Доступы"), KeyboardButton(text="📚 Мануалы")]
            ],
            resize_keyboard=True
        )

admin_menu = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="➕ Код", callback_data="code")],
    [InlineKeyboardButton(text="➕ Почта", callback_data="email")],
    [InlineKeyboardButton(text="➕ Домен", callback_data="domain")],
    [InlineKeyboardButton(text="➕ Доступ", callback_data="access")],
    [InlineKeyboardButton(text="➕ Мануал", callback_data="manual")],
    [InlineKeyboardButton(text="🗑 Доступы", callback_data="show_access")],
    [InlineKeyboardButton(text="🗑 Мануалы", callback_data="show_manual")],
    [InlineKeyboardButton(text="📤 Логи", callback_data="logs")]
])

rate_kb = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="✅ ОК", callback_data="ok"),
        InlineKeyboardButton(text="❌ БАН", callback_data="ban")
    ]
])

# ===== LOG =====
def log(uid, action, item):
    with open("logs.txt", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now()} | {uid} | {action} | {item}\n")

# ===== HELP =====
def get_user(uid):
    cur.execute("SELECT * FROM users WHERE id=%s", (uid,))
    return cur.fetchone()

def count_user(uid, action):
    count = 0
    try:
        with open("logs.txt", "r", encoding="utf-8") as f:
            for line in f:
                if f"| {uid} | {action} |" in line:
                    count += 1
    except:
        pass
    return count

def count_all(action):
    count = 0
    try:
        with open("logs.txt", "r", encoding="utf-8") as f:
            for line in f:
                if f"| {action} |" in line:
                    count += 1
    except:
        pass
    return count

def build_delete_kb(rows, prefix):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=row[1], callback_data=f"{prefix}_{row[0]}")]
            for row in rows
        ]
    )

# ===== MAIN =====
@router.message()
async def all(msg: Message):

    uid = msg.from_user.id
    text = msg.text
    user = get_user(uid)

    # ===== ADMIN INPUT =====
    if uid == ADMIN_ID and uid in admin_mode:
        mode = admin_mode[uid]

        table = {
            "code": "codes",
            "email": "emails",
            "domain": "domains",
            "access": "accesses",
            "manual": "manuals"
        }[mode]

        cur.execute(f"INSERT INTO {table} (value) VALUES (%s)", (text,))
        conn.commit()

        del admin_mode[uid]
        return await msg.answer("✅ добавлено")

    # ===== START =====
    if text == "/start":
        if not user and uid != ADMIN_ID:
            return await msg.answer("🔐 Введи код доступа")
        return await msg.answer("🚀 CRM готова", reply_markup=get_menu(uid))

    # ===== AUTH =====
    if not user and uid != ADMIN_ID:
        cur.execute("SELECT * FROM codes WHERE code=%s", (text,))
        if not cur.fetchone():
            return await msg.answer("❌ Неверный код")

        cur.execute("DELETE FROM codes WHERE code=%s", (text,))
        cur.execute("INSERT INTO users (id, role) VALUES (%s,%s)", (uid, "user"))
        conn.commit()

        return await msg.answer("✅ Доступ открыт", reply_markup=get_menu(uid))

    # ===== PROFILE =====
    if text == "👤 Профиль":
        role = "admin" if uid == ADMIN_ID else user[1]

        return await msg.answer(
            f"👤 ID: {uid}\n"
            f"🎭 Роль: {role}\n"
            f"📧 почт: {count_user(uid, 'take_email')}\n"
            f"🌐 доменов: {count_user(uid, 'take_domain')}\n"
            f"✅ OK: {count_user(uid, 'ok')}\n"
            f"❌ BAN: {count_user(uid, 'ban')}"
        )

    # ===== EMAIL =====
    if text == "📧 Почта":
        cur.execute("SELECT id, value FROM emails LIMIT 1")
        row = cur.fetchone()

        if not row:
            return await msg.answer("❌ нет почт")

        cur.execute("DELETE FROM emails WHERE id=%s", (row[0],))
        conn.commit()

        last_item[uid] = ("email", row[1])
        log(uid, "take_email", row[1])

        return await msg.answer(row[1], reply_markup=rate_kb)

    # ===== DOMAIN =====
    if text == "🌐 Домен":
        cur.execute("SELECT id, value FROM domains LIMIT 1")
        row = cur.fetchone()

        if not row:
            return await msg.answer("❌ нет доменов")

        cur.execute("DELETE FROM domains WHERE id=%s", (row[0],))
        conn.commit()

        last_item[uid] = ("domain", row[1])
        log(uid, "take_domain", row[1])

        return await msg.answer(row[1], reply_markup=rate_kb)

    # ===== ACCESS =====
    if text == "🔑 Доступы":
        cur.execute("SELECT value FROM accesses")
        rows = cur.fetchall()
        return await msg.answer("\n".join([r[0] for r in rows]) or "нет")

    # ===== MANUALS =====
    if text == "📚 Мануалы":
        cur.execute("SELECT value FROM manuals")
        rows = cur.fetchall()
        return await msg.answer("\n".join([r[0] for r in rows]) or "нет")

    # ===== DASHBOARD =====
    if text == "📊 Дашборд" and uid == ADMIN_ID:
        return await msg.answer(
            f"📊 Статистика:\n"
            f"📧 почты: {count_all('take_email')}\n"
            f"🌐 домены: {count_all('take_domain')}\n"
            f"✅ OK: {count_all('ok')}\n"
            f"❌ BAN: {count_all('ban')}"
        )

    # ===== ADMIN =====
    if text == "🛠 Админ" and uid == ADMIN_ID:
        return await msg.answer("⚙️ Панель", reply_markup=admin_menu)

# ===== CALLBACK =====
@router.callback_query()
async def cb(c):
    await c.answer()
    uid = c.from_user.id

    if c.data in ["ok", "ban"]:
        if uid in last_item:
            item_type, value = last_item[uid]
            log(uid, c.data, value)
            del last_item[uid]
            return await c.message.answer(f"{c.data.upper()} сохранено")

    if uid != ADMIN_ID:
        return

    if c.data == "logs":
        try:
            await c.message.answer_document(FSInputFile("logs.txt"))
        except:
            await c.message.answer("нет логов")
        return

    if c.data == "show_access":
        cur.execute("SELECT id, value FROM accesses")
        rows = cur.fetchall()
        return await c.message.answer("выбери:", reply_markup=build_delete_kb(rows, "del_access"))

    if c.data == "show_manual":
        cur.execute("SELECT id, value FROM manuals")
        rows = cur.fetchall()
        return await c.message.answer("выбери:", reply_markup=build_delete_kb(rows, "del_manual"))

    if c.data.startswith("del_access_"):
        rid = c.data.split("_")[2]
        cur.execute("DELETE FROM accesses WHERE id=%s", (rid,))
        conn.commit()
        return await c.message.answer("✅ удалено")

    if c.data.startswith("del_manual_"):
        rid = c.data.split("_")[2]
        cur.execute("DELETE FROM manuals WHERE id=%s", (rid,))
        conn.commit()
        return await c.message.answer("✅ удалено")

    admin_mode[uid] = c.data
    await c.message.answer(f"введи {c.data}")

async def main():
    dp.include_router(router)
    print("FINAL CRM READY")
    await dp.start_polling(bot)

asyncio.run(main())
