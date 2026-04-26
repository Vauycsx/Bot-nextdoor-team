import os
import asyncio
import asyncpg
from datetime import datetime

from aiohttp import web
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile

# ================== ENV ==================
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")
WEBHOOK_PATH = f"/webhook/{TOKEN}"
WEBHOOK_URL = os.getenv("WEBHOOK_URL") + WEBHOOK_PATH

bot = Bot(TOKEN)
dp = Dispatcher()
router = Router()

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

        CREATE TABLE IF NOT EXISTS codes(
            code TEXT
        );

        CREATE TABLE IF NOT EXISTS emails(value TEXT);
        CREATE TABLE IF NOT EXISTS domains(value TEXT);
        CREATE TABLE IF NOT EXISTS accesses(value TEXT);
        CREATE TABLE IF NOT EXISTS manuals(value TEXT);

        CREATE TABLE IF NOT EXISTS cards(
            id SERIAL PRIMARY KEY,
            value TEXT,
            category TEXT
        );
        """)

# ================== STATE ==================
last_item = {}         # user -> (type, value)
last_card = {}         # user -> {category: card_id}

# ================== KEYBOARDS ==================
def get_menu(uid):
    if uid == ADMIN_ID:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="👤 Профиль")],
                [KeyboardButton(text="📧 Почта"), KeyboardButton(text="🌐 Домен")],
                [KeyboardButton(text="🔑 Доступы"), KeyboardButton(text="📚 Мануалы")],
                [KeyboardButton(text="💳 Карты")],
                [KeyboardButton(text="🛠 Админ")]
            ],
            resize_keyboard=True
        )
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👤 Профиль")],
            [KeyboardButton(text="📧 Почта"), KeyboardButton(text="🌐 Домен")],
            [KeyboardButton(text="🔑 Доступы"), KeyboardButton(text="📚 Мануалы")],
            [KeyboardButton(text="💳 Карты")]
        ],
        resize_keyboard=True
    )

card_kb = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="🟢 Обычные", callback_data="card_normal"),
        InlineKeyboardButton(text="🟣 Генерки", callback_data="card_gen")
    ]
])

again_kb_normal = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🔁 Ещё раз", callback_data="again_normal")]
])

again_kb_gen = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🔁 Ещё раз", callback_data="again_gen")]
])

# ================== HELPERS ==================
async def get_user(uid):
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE id=$1", uid)

async def log(uid, action, item):
    with open("logs.txt", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now()} | {uid} | {action} | {item}\n")

# ================== AUTH ==================
@router.message(F.text == "/start")
async def start(msg: Message):
    user = await get_user(msg.from_user.id)

    if not user and msg.from_user.id != ADMIN_ID:
        return await msg.answer("🔐 Введи код доступа")

    await msg.answer("🚀 CRM готова", reply_markup=get_menu(msg.from_user.id))


@router.message()
async def auth_and_main(msg: Message):
    uid = msg.from_user.id
    text = msg.text

    user = await get_user(uid)

    # ===== AUTH CODE =====
    if not user and uid != ADMIN_ID:
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM codes WHERE code=$1", text)

            if not row:
                return await msg.answer("❌ Неверный код")

            await conn.execute("DELETE FROM codes WHERE code=$1", text)
            await conn.execute("INSERT INTO users VALUES ($1,$2)", uid, "user")

        return await msg.answer("✅ Доступ открыт", reply_markup=get_menu(uid))

    # ===== PROFILE =====
    if text == "👤 Профиль":
        role = "admin" if uid == ADMIN_ID else user["role"]

        return await msg.answer(
            f"👤 ID: {uid}\n🎭 Роль: {role}"
        )

    # ===== CARDS MENU =====
    if text == "💳 Карты":
        return await msg.answer("Выбери категорию:", reply_markup=card_kb)


# ================== CARDS ==================
async def get_random_card(category: str, exclude_id=None):
    async with pool.acquire() as conn:
        if exclude_id:
            row = await conn.fetchrow("""
                SELECT * FROM cards
                WHERE category=$1 AND id != $2
                ORDER BY RANDOM()
                LIMIT 1
            """, category, exclude_id)
        else:
            row = await conn.fetchrow("""
                SELECT * FROM cards
                WHERE category=$1
                ORDER BY RANDOM()
                LIMIT 1
            """, category)

        return row


async def send_card(msg_or_cb, uid, category):
    row = await get_random_card(category)

    if not row:
        return await msg_or_cb.message.answer("❌ Нет карт")

    last_card.setdefault(uid, {})[category] = row["id"]

    kb = again_kb_normal if category == "normal" else again_kb_gen

    await msg_or_cb.message.answer(f"💳 Карта:\n{row['value']}", reply_markup=kb)


# ================== CALLBACKS ==================
@router.callback_query()
async def callbacks(cb: CallbackQuery):
    await cb.answer()
    uid = cb.from_user.id

    # ===== CARDS FIRST TIME =====
    if cb.data == "card_normal":
        return await send_card(cb, uid, "normal")

    if cb.data == "card_gen":
        return await send_card(cb, uid, "gen")

    # ===== AGAIN NORMAL =====
    if cb.data == "again_normal":
        prev = last_card.get(uid, {}).get("normal")
        row = await get_random_card("normal", exclude_id=prev)

        if not row:
            return await cb.message.answer("❌ Нет карт")

        last_card[uid]["normal"] = row["id"]
        return await cb.message.answer(f"💳 Карта:\n{row['value']}", reply_markup=again_kb_normal)

    # ===== AGAIN GEN =====
    if cb.data == "again_gen":
        prev = last_card.get(uid, {}).get("gen")
        row = await get_random_card("gen", exclude_id=prev)

        if not row:
            return await cb.message.answer("❌ Нет карт")

        last_card[uid]["gen"] = row["id"]
        return await cb.message.answer(f"💳 Карта:\n{row['value']}", reply_markup=again_kb_gen)


# ================== APP ==================
async def on_startup(app):
    await init_db()
    await bot.set_webhook(WEBHOOK_URL)
    print("BOT STARTED (Render webhook mode)")


async def on_shutdown(app):
    await bot.delete_webhook()
    await pool.close()


app = web.Application()
app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

dp.include_router(router)
# ================== ADMIN STATE ==================
admin_mode = {}

# ================== HELPERS (ADMIN ADD) ==================
TABLE_MAP = {
    "code": "codes",
    "email": "emails",
    "domain": "domains",
    "access": "accesses",
    "manual": "manuals"
}

# ================== PROFILE COUNT ==================
async def count_user(uid, action):
    try:
        with open("logs.txt", "r", encoding="utf-8") as f:
            return sum(1 for line in f if f"| {uid} | {action} |" in line)
    except:
        return 0


async def count_all(action):
    try:
        with open("logs.txt", "r", encoding="utf-8") as f:
            return sum(1 for line in f if f"| {action} |" in line)
    except:
        return 0


# ================== EMAIL / DOMAIN / ETC ==================
async def take_one(table):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(f"SELECT id, value FROM {table} ORDER BY RANDOM() LIMIT 1")
        if not row:
            return None

        await conn.execute(f"DELETE FROM {table} WHERE id=$1", row["id"])
        return row["value"]


# ================== MAIN HANDLER EXTENSION ==================
@router.message()
async def main_extended(msg: Message):
    uid = msg.from_user.id
    text = msg.text

    user = await get_user(uid)

    # ===== ADMIN INPUT MODE =====
    if uid == ADMIN_ID and uid in admin_mode:
        mode = admin_mode[uid]

        table = TABLE_MAP[mode]

        async with pool.acquire() as conn:
            await conn.execute(f"INSERT INTO {table}(value) VALUES ($1)", text)

        del admin_mode[uid]
        return await msg.answer("✅ добавлено")


    # ===== EMAIL =====
    if text == "📧 Почта":
        value = await take_one("emails")

        if not value:
            return await msg.answer("❌ нет почт")

        last_item[uid] = ("email", value)
        await log(uid, "take_email", value)

        return await msg.answer(value, reply_markup=rate_kb)


    # ===== DOMAIN =====
    if text == "🌐 Домен":
        value = await take_one("domains")

        if not value:
            return await msg.answer("❌ нет доменов")

        last_item[uid] = ("domain", value)
        await log(uid, "take_domain", value)

        return await msg.answer(value, reply_markup=rate_kb)


    # ===== ACCESS =====
    if text == "🔑 Доступы":
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT value FROM accesses")

        data = "\n".join([r["value"] for r in rows]) if rows else "нет"
        return await msg.answer(data)


    # ===== MANUALS =====
    if text == "📚 Мануалы":
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT value FROM manuals")

        data = "\n".join([r["value"] for r in rows]) if rows else "нет"
        return await msg.answer(data)


    # ===== DASHBOARD =====
    if text == "📊 Дашборд" and uid == ADMIN_ID:
        return await msg.answer(
            f"📊 Статистика:\n"
            f"📧 почты: {await count_all('take_email')}\n"
            f"🌐 домены: {await count_all('take_domain')}\n"
            f"✅ OK: {await count_all('ok')}\n"
            f"❌ BAN: {await count_all('ban')}"
        )


    # ===== ADMIN PANEL =====
    if text == "🛠 Админ" and uid == ADMIN_ID:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Код", callback_data="code")],
            [InlineKeyboardButton(text="➕ Почта", callback_data="email")],
            [InlineKeyboardButton(text="➕ Домен", callback_data="domain")],
            [InlineKeyboardButton(text="➕ Доступ", callback_data="access")],
            [InlineKeyboardButton(text="➕ Мануал", callback_data="manual")],
            [InlineKeyboardButton(text="🗑 Доступы", callback_data="show_access")],
            [InlineKeyboardButton(text="🗑 Мануалы", callback_data="show_manual")],
            [InlineKeyboardButton(text="📤 Логи", callback_data="logs")]
        ])

        return await msg.answer("⚙️ Админ панель", reply_markup=kb)


# ================== CALLBACKS EXTENDED ==================
@router.callback_query()
async def cb_extended(cb: CallbackQuery):
    await cb.answer()
    uid = cb.from_user.id

    # ===== OK / BAN =====
    if cb.data in ["ok", "ban"]:
        if uid in last_item:
            item_type, value = last_item[uid]
            await log(uid, cb.data, value)
            del last_item[uid]
            return await cb.message.answer(f"{cb.data.upper()} сохранено")


    # ===== ADMIN ONLY =====
    if uid != ADMIN_ID:
        return


    # ===== ADD MODE =====
    if cb.data in ["code", "email", "domain", "access", "manual"]:
        admin_mode[uid] = cb.data
        return await cb.message.answer(f"✍️ Введи {cb.data}")


    # ===== LOGS =====
    if cb.data == "logs":
        try:
            await cb.message.answer_document(FSInputFile("logs.txt"))
        except:
            await cb.message.answer("нет логов")
        return


    # ===== SHOW DELETE ACCESS =====
    if cb.data == "show_access":
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, value FROM accesses")

        if not rows:
            return await cb.message.answer("нет доступов")

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=r["value"], callback_data=f"del_access_{r['id']}")]
            for r in rows
        ])

        return await cb.message.answer("выбери:", reply_markup=kb)


    # ===== SHOW DELETE MANUAL =====
    if cb.data == "show_manual":
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, value FROM manuals")

        if not rows:
            return await cb.message.answer("нет мануалов")

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=r["value"], callback_data=f"del_manual_{r['id']}")]
            for r in rows
        ])

        return await cb.message.answer("выбери:", reply_markup=kb)


    # ===== DELETE ACCESS =====
    if cb.data.startswith("del_access_"):
        rid = int(cb.data.split("_")[2])

        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM accesses WHERE id=$1", rid)

        return await cb.message.answer("✅ удалено")


    # ===== DELETE MANUAL =====
    if cb.data.startswith("del_manual_"):
        rid = int(cb.data.split("_")[2])

        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM manuals WHERE id=$1", rid)

        return await cb.message.answer("✅ удалено")


# ================== RUN APP ==================
async def main():
    dp.include_router(router)

    app = web.Application()
    app.router.add_post(WEBHOOK_PATH, webhook)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))


asyncio.run(main())

# webhook handler
async def webhook(request):
    update = await request.json()
    await dp.feed_update(bot, update)
    return web.Response()

app.router.add_post(WEBHOOK_PATH, webhook)


if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
