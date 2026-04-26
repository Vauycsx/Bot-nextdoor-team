import asyncio
import os
import random
from datetime import datetime, timedelta

import asyncpg
from aiogram import Bot, Dispatcher, Router
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile,
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# ===== CONFIG =====
print("BOT_TOKEN:", os.getenv("BOT_TOKEN"))
print("DATABASE_URL:", os.getenv("DATABASE_URL"))
print("WEBHOOK_URL:", os.getenv("WEBHOOK_URL"))

PORT = int(os.getenv("PORT", "10000"))
WEBHOOK_BASE = os.getenv("WEBHOOK_BASE", "")  # https://my-bot.onrender.com
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
WEBHOOK_PATH = f"/tg/{TOKEN}"

CARD_COOLDOWN_MIN = int(os.getenv("CARD_COOLDOWN_MIN", "10"))
CARD_COOLDOWN_MAX = int(os.getenv("CARD_COOLDOWN_MAX", "30"))

bot = Bot(TOKEN)
dp = Dispatcher()
router = Router()

pool: asyncpg.Pool | None = None

# ===== STATE =====
admin_mode = {}
last_item = {}

CARD_CATEGORIES = {
    "regular": ("Обычные", "cards_regular"),
    "generated": ("Генерки", "cards_generated"),
}

# ===== DB =====
async def init_db():
    global pool
    # Render иногда даёт URL вида postgres:// — asyncpg хочет postgresql://
    dsn = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    # SSL для Render Postgres
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5, ssl="require")

    async with pool.acquire() as c:
        await c.execute("CREATE TABLE IF NOT EXISTS users (id BIGINT PRIMARY KEY, role TEXT)")
        await c.execute("CREATE TABLE IF NOT EXISTS codes (id SERIAL PRIMARY KEY, code TEXT)")
        await c.execute("CREATE TABLE IF NOT EXISTS emails (id SERIAL PRIMARY KEY, value TEXT)")
        await c.execute("CREATE TABLE IF NOT EXISTS domains (id SERIAL PRIMARY KEY, value TEXT)")
        await c.execute("CREATE TABLE IF NOT EXISTS accesses (id SERIAL PRIMARY KEY, value TEXT)")
        await c.execute("CREATE TABLE IF NOT EXISTS manuals (id SERIAL PRIMARY KEY, value TEXT)")
        await c.execute("CREATE TABLE IF NOT EXISTS cards_regular (id SERIAL PRIMARY KEY, value TEXT)")
        await c.execute("CREATE TABLE IF NOT EXISTS cards_generated (id SERIAL PRIMARY KEY, value TEXT)")
        await c.execute("""CREATE TABLE IF NOT EXISTS card_cooldown (
            uid BIGINT, category TEXT, card_id INTEGER, until_ts TIMESTAMPTZ,
            PRIMARY KEY (uid, category, card_id)
        )""")
        await c.execute("""CREATE TABLE IF NOT EXISTS logs (
            id SERIAL PRIMARY KEY,
            ts TIMESTAMPTZ DEFAULT NOW(),
            uid BIGINT,
            action TEXT,
            item TEXT
        )""")

# ===== UI =====
def get_menu(uid):
    if uid == ADMIN_ID:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="👤 Профиль")],
                [KeyboardButton(text="📧 Почта"), KeyboardButton(text="🌐 Домен")],
                [KeyboardButton(text="🔑 Доступы"), KeyboardButton(text="📚 Мануалы")],
                [KeyboardButton(text="🎴 Карты")],
                [KeyboardButton(text="📊 Дашборд")],
                [KeyboardButton(text="🛠 Админ")],
            ],
            resize_keyboard=True,
        )
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👤 Профиль")],
            [KeyboardButton(text="📧 Почта"), KeyboardButton(text="🌐 Домен")],
            [KeyboardButton(text="🔑 Доступы"), KeyboardButton(text="📚 Мануалы")],
            [KeyboardButton(text="🎴 Карты")],
        ],
        resize_keyboard=True,
    )

admin_menu = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="➕ Код", callback_data="code")],
    [InlineKeyboardButton(text="➕ Почта", callback_data="email")],
    [InlineKeyboardButton(text="➕ Домен", callback_data="domain")],
    [InlineKeyboardButton(text="➕ Доступ", callback_data="access")],
    [InlineKeyboardButton(text="➕ Мануал", callback_data="manual")],
    [InlineKeyboardButton(text="➕ Карта обычн.", callback_data="card_regular")],
    [InlineKeyboardButton(text="➕ Карта генерка", callback_data="card_generated")],
    [InlineKeyboardButton(text="🗑 Доступы", callback_data="show_access")],
    [InlineKeyboardButton(text="🗑 Мануалы", callback_data="show_manual")],
    [InlineKeyboardButton(text="🗑 Карты обычн.", callback_data="show_card_regular")],
    [InlineKeyboardButton(text="🗑 Карты генерки", callback_data="show_card_generated")],
    [InlineKeyboardButton(text="📤 Логи", callback_data="logs")],
])

rate_kb = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="✅ ОК", callback_data="ok"),
        InlineKeyboardButton(text="❌ БАН", callback_data="ban"),
    ]
])

cards_menu = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🃏 Обычные", callback_data="pick_regular")],
    [InlineKeyboardButton(text="✨ Генерки", callback_data="pick_generated")],
])

def again_kb(category: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔁 Ещё раз", callback_data=f"again_{category}")]
    ])

def build_delete_kb(rows, prefix):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=str(r["value"])[:60], callback_data=f"{prefix}_{r['id']}")]
            for r in rows
        ]
    )

# ===== LOG / STATS =====
async def log(uid, action, item):
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO logs(uid, action, item) VALUES($1, $2, $3)",
            uid, action, item,
        )

async def get_user(uid):
    async with pool.acquire() as c:
        return await c.fetchrow("SELECT * FROM users WHERE id=$1", uid)

async def count_user(uid, action) -> int:
    async with pool.acquire() as c:
        v = await c.fetchval(
            "SELECT COUNT(*) FROM logs WHERE uid=$1 AND action=$2", uid, action
        )
        return int(v or 0)

async def count_all(action) -> int:
    async with pool.acquire() as c:
        v = await c.fetchval("SELECT COUNT(*) FROM logs WHERE action=$1", action)
        return int(v or 0)

async def export_logs() -> bytes:
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT ts, uid, action, item FROM logs ORDER BY id")
    lines = [f"{r['ts']} | {r['uid']} | {r['action']} | {r['item']}" for r in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")

# ===== CARDS =====
async def pick_card(uid: int, category: str):
    if category not in CARD_CATEGORIES:
        return None
    table = CARD_CATEGORIES[category][1]
    async with pool.acquire() as c:
        await c.execute("DELETE FROM card_cooldown WHERE until_ts < NOW()")
        rows = await c.fetch(f"""
            SELECT id, value FROM {table}
            WHERE id NOT IN (
                SELECT card_id FROM card_cooldown
                WHERE uid=$1 AND category=$2 AND until_ts > NOW()
            )
        """, uid, category)
        if not rows:
            return None
        row = random.choice(rows)
        minutes = random.randint(CARD_COOLDOWN_MIN, CARD_COOLDOWN_MAX)
        until = datetime.utcnow() + timedelta(minutes=minutes)
        await c.execute("""
            INSERT INTO card_cooldown(uid, category, card_id, until_ts)
            VALUES($1, $2, $3, $4)
            ON CONFLICT (uid, category, card_id)
            DO UPDATE SET until_ts = EXCLUDED.until_ts
        """, uid, category, row["id"], until)
        return row

# ===== MAIN =====
@router.message()
async def all_handler(msg: Message):
    uid = msg.from_user.id
    text = msg.text or ""
    user = await get_user(uid)

    # ===== ADMIN INPUT =====
    if uid == ADMIN_ID and uid in admin_mode:
        mode = admin_mode[uid]
        table = {
            "code": "codes",
            "email": "emails",
            "domain": "domains",
            "access": "accesses",
            "manual": "manuals",
            "card_regular": "cards_regular",
            "card_generated": "cards_generated",
        }.get(mode)
        if table:
            col = "code" if mode == "code" else "value"
            async with pool.acquire() as c:
                await c.execute(f"INSERT INTO {table}({col}) VALUES($1)", text)
            del admin_mode[uid]
            return await msg.answer("✅ добавлено")

    # ===== START =====
    if text == "/start":
        if not user and uid != ADMIN_ID:
            return await msg.answer("🔐 Введи код доступа")
        return await msg.answer("🚀 CRM готова", reply_markup=get_menu(uid))

    # ===== AUTH =====
    if not user and uid != ADMIN_ID:
        async with pool.acquire() as c:
            row = await c.fetchrow("SELECT id FROM codes WHERE code=$1", text)
            if not row:
                return await msg.answer("❌ Неверный код")
            await c.execute("DELETE FROM codes WHERE id=$1", row["id"])
            await c.execute(
                "INSERT INTO users(id, role) VALUES($1, $2) ON CONFLICT (id) DO NOTHING",
                uid, "user",
            )
        return await msg.answer("✅ Доступ открыт", reply_markup=get_menu(uid))

    # ===== PROFILE =====
    if text == "👤 Профиль":
        role = "admin" if uid == ADMIN_ID else (user["role"] if user else "user")
        return await msg.answer(
            f"👤 ID: {uid}\n"
            f"🎭 Роль: {role}\n"
            f"📧 почт: {await count_user(uid, 'take_email')}\n"
            f"🌐 доменов: {await count_user(uid, 'take_domain')}\n"
            f"🎴 карт: {await count_user(uid, 'take_card')}\n"
            f"✅ OK: {await count_user(uid, 'ok')}\n"
            f"❌ BAN: {await count_user(uid, 'ban')}"
        )

    # ===== EMAIL =====
    if text == "📧 Почта":
        async with pool.acquire() as c:
            row = await c.fetchrow("SELECT id, value FROM emails ORDER BY id LIMIT 1")
            if not row:
                return await msg.answer("❌ нет почт")
            await c.execute("DELETE FROM emails WHERE id=$1", row["id"])
        last_item[uid] = ("email", row["value"])
        await log(uid, "take_email", row["value"])
        return await msg.answer(row["value"], reply_markup=rate_kb)

    # ===== DOMAIN =====
    if text == "🌐 Домен":
        async with pool.acquire() as c:
            row = await c.fetchrow("SELECT id, value FROM domains ORDER BY id LIMIT 1")
            if not row:
                return await msg.answer("❌ нет доменов")
            await c.execute("DELETE FROM domains WHERE id=$1", row["id"])
        last_item[uid] = ("domain", row["value"])
        await log(uid, "take_domain", row["value"])
        return await msg.answer(row["value"], reply_markup=rate_kb)

    # ===== ACCESS =====
    if text == "🔑 Доступы":
        async with pool.acquire() as c:
            rows = await c.fetch("SELECT value FROM accesses ORDER BY id")
        return await msg.answer("\n".join(r["value"] for r in rows) or "нет")

    # ===== MANUALS =====
    if text == "📚 Мануалы":
        async with pool.acquire() as c:
            rows = await c.fetch("SELECT value FROM manuals ORDER BY id")
        return await msg.answer("\n".join(r["value"] for r in rows) or "нет")

    # ===== CARDS =====
    if text == "🎴 Карты":
        return await msg.answer("Выбери категорию карт:", reply_markup=cards_menu)

    # ===== DASHBOARD =====
    if text == "📊 Дашборд" and uid == ADMIN_ID:
        return await msg.answer(
            f"📊 Статистика:\n"
            f"📧 почты: {await count_all('take_email')}\n"
            f"🌐 домены: {await count_all('take_domain')}\n"
            f"🎴 карты: {await count_all('take_card')}\n"
            f"✅ OK: {await count_all('ok')}\n"
            f"❌ BAN: {await count_all('ban')}"
        )

    # ===== ADMIN =====
    if text == "🛠 Админ" and uid == ADMIN_ID:
        return await msg.answer("⚙️ Панель", reply_markup=admin_menu)

# ===== CALLBACK =====
@router.callback_query()
async def cb(c: CallbackQuery):
    await c.answer()
    uid = c.from_user.id
    data = c.data or ""

    # ===== OK / BAN =====
    if data in ("ok", "ban"):
        if uid in last_item:
            item_type, value = last_item[uid]
            await log(uid, data, value)
            del last_item[uid]
            return await c.message.answer(f"{data.upper()} сохранено")
        return

    # ===== CARDS PICK / AGAIN =====
    if data.startswith("pick_") or data.startswith("again_"):
        category = data.split("_", 1)[1]
        if category not in CARD_CATEGORIES:
            return
        if uid != ADMIN_ID and not await get_user(uid):
            return await c.message.answer("🔐 Введи код доступа")
        row = await pick_card(uid, category)
        cat_name = CARD_CATEGORIES[category][0]
        if not row:
            return await c.message.answer(f"❌ нет доступных карт ({cat_name})")
        await log(uid, "take_card", f"{category}:{row['value']}")
        return await c.message.answer(
            f"🎴 {cat_name}\n\n{row['value']}",
            reply_markup=again_kb(category),
        )

    if uid != ADMIN_ID:
        return

    # ===== LOGS =====
    if data == "logs":
        data_bytes = await export_logs()
        if not data_bytes.strip():
            return await c.message.answer("нет логов")
        return await c.message.answer_document(
            BufferedInputFile(data_bytes, filename="logs.txt")
        )

    # ===== SHOW DELETE =====
    if data == "show_access":
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, value FROM accesses ORDER BY id")
        if not rows:
            return await c.message.answer("нет доступов")
        return await c.message.answer("выбери:", reply_markup=build_delete_kb(rows, "del_access"))

    if data == "show_manual":
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, value FROM manuals ORDER BY id")
        if not rows:
            return await c.message.answer("нет мануалов")
        return await c.message.answer("выбери:", reply_markup=build_delete_kb(rows, "del_manual"))

    if data == "show_card_regular":
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, value FROM cards_regular ORDER BY id")
        if not rows:
            return await c.message.answer("нет карт (обычные)")
        return await c.message.answer("выбери:", reply_markup=build_delete_kb(rows, "del_cardr"))

    if data == "show_card_generated":
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, value FROM cards_generated ORDER BY id")
        if not rows:
            return await c.message.answer("нет карт (генерки)")
        return await c.message.answer("выбери:", reply_markup=build_delete_kb(rows, "del_cardg"))

    # ===== DELETE =====
    if data.startswith("del_access_"):
        rid = int(data.split("_")[2])
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM accesses WHERE id=$1", rid)
        return await c.message.answer("✅ удалено")

    if data.startswith("del_manual_"):
        rid = int(data.split("_")[2])
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM manuals WHERE id=$1", rid)
        return await c.message.answer("✅ удалено")

    if data.startswith("del_cardr_"):
        rid = int(data.split("_")[2])
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM cards_regular WHERE id=$1", rid)
            await conn.execute(
                "DELETE FROM card_cooldown WHERE category='regular' AND card_id=$1", rid
            )
        return await c.message.answer("✅ удалено")

    if data.startswith("del_cardg_"):
        rid = int(data.split("_")[2])
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM cards_generated WHERE id=$1", rid)
            await conn.execute(
                "DELETE FROM card_cooldown WHERE category='generated' AND card_id=$1", rid
            )
        return await c.message.answer("✅ удалено")

    # ===== ADD =====
    if data in ("code", "email", "domain", "access", "manual",
                "card_regular", "card_generated"):
        admin_mode[uid] = data
        return await c.message.answer(f"введи {data}")

# ===== WEB / WEBHOOK =====
async def on_startup(app: web.Application):
    await init_db()
    if not WEBHOOK_BASE:
        print("⚠️ WEBHOOK_BASE не задан — webhook не выставлен")
        return
    url = WEBHOOK_BASE.rstrip("/") + WEBHOOK_PATH
    await bot.set_webhook(
        url=url,
        secret_token=WEBHOOK_SECRET or None,
        drop_pending_updates=True,
        allowed_updates=dp.resolve_used_update_types(),
    )
    print(f"✅ Webhook установлен: {url}")

async def on_shutdown(app: web.Application):
    try:
        await bot.delete_webhook()
    finally:
        await bot.session.close()
        if pool:
            await pool.close()

async def health(request: web.Request):
    return web.Response(text="ok")

def main():
    dp.include_router(router)

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=WEBHOOK_SECRET or None,
    ).register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    print(f"FINAL CRM READY on :{PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()
