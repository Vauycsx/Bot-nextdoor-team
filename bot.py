import asyncio
import os
import tempfile
from datetime import datetime
from contextlib import asynccontextmanager

import asyncpg
from aiohttp import web
from aiogram import Bot, Dispatcher, Router, types
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# ------------------- КОНФИГ -------------------
TOKEN = os.getenv("BOT_TOKEN", "ТВОЙ_ТОКЕН")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7062911219"))
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost/db")

# Webhook settings (для Render)
WEBHOOK_HOST = os.getenv("RENDER_EXTERNAL_URL", "https://your-app.onrender.com")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

# ------------------- ИНИЦИАЛИЗАЦИЯ -------------------
bot = Bot(TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()

# Глобальный пул соединений с БД
db_pool = None

# ------------------- РАБОТА С БАЗОЙ ДАННЫХ -------------------
async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)

    async with db_pool.acquire() as conn:
        # Пользователи
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id BIGINT PRIMARY KEY,
                role TEXT
            )
        """)
        # Коды доступа
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS codes (
                code TEXT PRIMARY KEY
            )
        """)
        # Почты
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS emails (
                id SERIAL PRIMARY KEY,
                value TEXT
            )
        """)
        # Домены
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS domains (
                id SERIAL PRIMARY KEY,
                value TEXT
            )
        """)
        # Доступы (логин:пароль и т.п.)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS accesses (
                id SERIAL PRIMARY KEY,
                value TEXT
            )
        """)
        # Мануалы (текстовые ссылки или инструкции)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS manuals (
                id SERIAL PRIMARY KEY,
                value TEXT
            )
        """)
        # Обычные карты
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS cards_common (
                id SERIAL PRIMARY KEY,
                value TEXT
            )
        """)
        # Генераторные карты
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS cards_gen (
                id SERIAL PRIMARY KEY,
                value TEXT
            )
        """)
        # Логи действий
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                action TEXT,
                item TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

async def log(uid: int, action: str, item: str):
    """Запись лога в БД"""
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO logs (user_id, action, item) VALUES ($1, $2, $3)",
            uid, action, item
        )

async def get_logs_text() -> str:
    """Выгружает все логи в текстовый формат"""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT created_at, user_id, action, item FROM logs ORDER BY id"
        )
    lines = [f"{r['created_at']} | {r['user_id']} | {r['action']} | {r['item']}" for r in rows]
    return "\n".join(lines) or "Нет логов"

async def count_user(uid: int, action: str) -> int:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) as cnt FROM logs WHERE user_id=$1 AND action=$2",
            uid, action
        )
        return row['cnt'] if row else 0

async def count_all(action: str) -> int:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) as cnt FROM logs WHERE action=$1",
            action
        )
        return row['cnt'] if row else 0

async def get_random_card(category: str):
    """Возвращает случайную карту из указанной таблицы"""
    table = "cards_common" if category == "common" else "cards_gen"
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(f"SELECT value FROM {table} ORDER BY RANDOM() LIMIT 1")
        return row['value'] if row else None

# ------------------- UI -------------------
def get_menu(uid: int):
    if uid == ADMIN_ID:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="👤 Профиль")],
                [KeyboardButton(text="📧 Почта"), KeyboardButton(text="🌐 Домен")],
                [KeyboardButton(text="🔑 Доступы"), KeyboardButton(text="📚 Мануалы")],
                [KeyboardButton(text="💳 Карты")],
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
                [KeyboardButton(text="🔑 Доступы"), KeyboardButton(text="📚 Мануалы")],
                [KeyboardButton(text="💳 Карты")]
            ],
            resize_keyboard=True
        )

# Инлайн-меню выбора категории карт
cards_category_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🃏 Обычные", callback_data="cards_common")],
    [InlineKeyboardButton(text="🎲 Генерки", callback_data="cards_gen")],
    [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")]
])

# Кнопка "Ещё раз"
def get_more_kb(category: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Ещё раз", callback_data=f"more_{category}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")]
    ])

# Админ-меню (добавление / удаление)
admin_menu = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="➕ Код", callback_data="code")],
    [InlineKeyboardButton(text="➕ Почта", callback_data="email")],
    [InlineKeyboardButton(text="➕ Домен", callback_data="domain")],
    [InlineKeyboardButton(text="➕ Доступ", callback_data="access")],
    [InlineKeyboardButton(text="➕ Мануал", callback_data="manual")],
    [InlineKeyboardButton(text="➕ Карта (обыч)", callback_data="card_common")],
    [InlineKeyboardButton(text="➕ Карта (генер)", callback_data="card_gen")],
    [InlineKeyboardButton(text="🗑 Доступы", callback_data="show_access")],
    [InlineKeyboardButton(text="🗑 Мануалы", callback_data="show_manual")],
    [InlineKeyboardButton(text="🗑 Карты (обыч)", callback_data="show_cards_common")],
    [InlineKeyboardButton(text="🗑 Карты (генер)", callback_data="show_cards_gen")],
    [InlineKeyboardButton(text="📤 Логи", callback_data="logs")]
])

rate_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="✅ ОК", callback_data="ok"),
     InlineKeyboardButton(text="❌ БАН", callback_data="ban")]
])

# Функция построения клавиатуры удаления для произвольной таблицы
def build_delete_kb(rows, prefix: str):
    """rows: список кортежей (id, value)"""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=row[1][:50], callback_data=f"{prefix}_{row[0]}")]
        for row in rows[:50]  # ограничим, чтобы не сломать сообщение
    ])
    return kb

# ------------------- СОСТОЯНИЯ (для ввода данных админом) -------------------
admin_input_state = {}   # {uid: (mode, table_name?)}

# ------------------- ОБРАБОТЧИКИ СООБЩЕНИЙ -------------------
@router.message()
async def handle_all_messages(msg: Message):
    uid = msg.from_user.id
    text = msg.text

    # ----- Админ вводит данные -----
    if uid == ADMIN_ID and uid in admin_input_state:
        mode = admin_input_state[uid]   # "code", "email", "domain", "access", "manual", "card_common", "card_gen"
        # Определяем таблицу
        table_map = {
            "code": "codes",
            "email": "emails",
            "domain": "domains",
            "access": "accesses",
            "manual": "manuals",
            "card_common": "cards_common",
            "card_gen": "cards_gen"
        }
        table = table_map.get(mode)
        if table:
            async with db_pool.acquire() as conn:
                await conn.execute(f"INSERT INTO {table} (value) VALUES ($1)", text)
            await msg.answer("✅ Добавлено")
        else:
            await msg.answer("❌ Неизвестный режим")
        del admin_input_state[uid]
        return

    # ----- Проверка пользователя -----
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE id=$1", uid)

    # ----- Старт или авторизация по коду -----
    if text == "/start":
        if not user and uid != ADMIN_ID:
            return await msg.answer("🔐 Введите код доступа")
        return await msg.answer("🚀 CRM готова", reply_markup=get_menu(uid))

    if not user and uid != ADMIN_ID:
        async with db_pool.acquire() as conn:
            exists = await conn.fetchrow("SELECT * FROM codes WHERE code=$1", text)
            if not exists:
                return await msg.answer("❌ Неверный код")
            await conn.execute("DELETE FROM codes WHERE code=$1", text)
            await conn.execute("INSERT INTO users (id, role) VALUES ($1, $2)", uid, "user")
        return await msg.answer("✅ Доступ открыт", reply_markup=get_menu(uid))

    # ----- Обработка обычных кнопок -----
    if text == "👤 Профиль":
        role = "admin" if uid == ADMIN_ID else "user"
        email_cnt = await count_user(uid, "take_email")
        domain_cnt = await count_user(uid, "take_domain")
        ok_cnt = await count_user(uid, "ok")
        ban_cnt = await count_user(uid, "ban")
        await msg.answer(
            f"👤 ID: {uid}\n"
            f"🎭 Роль: {role}\n"
            f"📧 почт: {email_cnt}\n"
            f"🌐 доменов: {domain_cnt}\n"
            f"✅ OK: {ok_cnt}\n"
            f"❌ BAN: {ban_cnt}"
        )
        return

    if text == "📧 Почта":
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT id, value FROM emails LIMIT 1")
            if not row:
                return await msg.answer("❌ Нет почт")
            await conn.execute("DELETE FROM emails WHERE id=$1", row['id'])
        await log(uid, "take_email", row['value'])
        # сохраним последний выданный item для кнопок ОК/БАН
        if not hasattr(handle_all_messages, "last_item"):
            handle_all_messages.last_item = {}
        handle_all_messages.last_item[uid] = ("email", row['value'])
        await msg.answer(row['value'], reply_markup=rate_kb)
        return

    if text == "🌐 Домен":
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT id, value FROM domains LIMIT 1")
            if not row:
                return await msg.answer("❌ Нет доменов")
            await conn.execute("DELETE FROM domains WHERE id=$1", row['id'])
        await log(uid, "take_domain", row['value'])
        if not hasattr(handle_all_messages, "last_item"):
            handle_all_messages.last_item = {}
        handle_all_messages.last_item[uid] = ("domain", row['value'])
        await msg.answer(row['value'], reply_markup=rate_kb)
        return

    if text == "🔑 Доступы":
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT value FROM accesses")
        if rows:
            await msg.answer("\n".join(r['value'] for r in rows))
        else:
            await msg.answer("нет доступов")
        return

    if text == "📚 Мануалы":
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT value FROM manuals")
        if rows:
            await msg.answer("\n".join(r['value'] for r in rows))
        else:
            await msg.answer("нет мануалов")
        return

    if text == "💳 Карты":
        await msg.answer("Выберите категорию карт:", reply_markup=cards_category_kb)
        return

    if text == "📊 Дашборд" and uid == ADMIN_ID:
        email_all = await count_all("take_email")
        domain_all = await count_all("take_domain")
        ok_all = await count_all("ok")
        ban_all = await count_all("ban")
        await msg.answer(
            f"📊 Статистика:\n"
            f"📧 почты: {email_all}\n"
            f"🌐 домены: {domain_all}\n"
            f"✅ OK: {ok_all}\n"
            f"❌ BAN: {ban_all}"
        )
        return

    if text == "🛠 Админ" and uid == ADMIN_ID:
        await msg.answer("⚙️ Панель управления", reply_markup=admin_menu)
        return

    # Неизвестная команда
    await msg.answer("Используйте кнопки меню")

# ------------------- ОБРАБОТЧИКИ CALLBACK -------------------
@router.callback_query()
async def handle_callbacks(call: types.CallbackQuery):
    uid = call.from_user.id
    data = call.data

    # ----- OK / BAN -----
    if data in ["ok", "ban"]:
        last_item_dict = getattr(handle_all_messages, "last_item", {})
        if uid in last_item_dict:
            item_type, value = last_item_dict[uid]
            await log(uid, data, value)
            del last_item_dict[uid]
            await call.message.edit_text(f"{data.upper()} сохранено")
        else:
            await call.answer("Нет активного элемента для оценки", show_alert=True)
        await call.answer()
        return

    # ----- Карты: выбор категории -----
    if data == "cards_common" or data == "cards_gen":
        category = "common" if data == "cards_common" else "gen"
        card = await get_random_card(category)
        if not card:
            await call.message.edit_text("❌ В этой категории пока нет карт", reply_markup=cards_category_kb)
        else:
            await call.message.edit_text(
                f"🎴 Ваша карта:\n\n{card}",
                reply_markup=get_more_kb(category)
            )
        await call.answer()
        return

    # ----- Ещё раз -----
    if data.startswith("more_"):
        category = data.split("_")[1]  # "common" или "gen"
        card = await get_random_card(category)
        if not card:
            await call.message.edit_text("❌ Карт больше нет", reply_markup=cards_category_kb)
        else:
            await call.message.edit_text(
                f"🎴 Ваша карта:\n\n{card}",
                reply_markup=get_more_kb(category)
            )
        await call.answer()
        return

    if data == "back_to_menu":
        await call.message.delete()
        await call.message.answer("Главное меню", reply_markup=get_menu(uid))
        await call.answer()
        return

    # ----- Далее только для админа -----
    if uid != ADMIN_ID:
        await call.answer("Нет прав", show_alert=True)
        return

    # ----- Логи -----
    if data == "logs":
        logs_text = await get_logs_text()
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".txt", delete=False, encoding="utf-8") as tmp:
            tmp.write(logs_text)
            tmp_path = tmp.name
        try:
            await call.message.answer_document(FSInputFile(tmp_path, filename="logs.txt"))
        except Exception as e:
            await call.message.answer(f"Ошибка отправки логов: {e}")
        finally:
            os.unlink(tmp_path)
        await call.answer()
        return

    # ----- Просмотр для удаления (доступы, мануалы, карты) -----
    if data == "show_access":
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, value FROM accesses")
        if not rows:
            await call.message.answer("Нет доступов")
        else:
            await call.message.answer("Выберите доступ для удаления:", reply_markup=build_delete_kb(rows, "del_access"))
        await call.answer()
        return

    if data == "show_manual":
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, value FROM manuals")
        if not rows:
            await call.message.answer("Нет мануалов")
        else:
            await call.message.answer("Выберите мануал для удаления:", reply_markup=build_delete_kb(rows, "del_manual"))
        await call.answer()
        return

    if data == "show_cards_common":
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, value FROM cards_common")
        if not rows:
            await call.message.answer("Нет обычных карт")
        else:
            await call.message.answer("Выберите карту для удаления:", reply_markup=build_delete_kb(rows, "del_card_common"))
        await call.answer()
        return

    if data == "show_cards_gen":
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, value FROM cards_gen")
        if not rows:
            await call.message.answer("Нет генераторных карт")
        else:
            await call.message.answer("Выберите карту для удаления:", reply_markup=build_delete_kb(rows, "del_card_gen"))
        await call.answer()
        return

    # ----- Удаление записей -----
    if data.startswith("del_access_"):
        record_id = int(data.split("_")[2])
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM accesses WHERE id=$1", record_id)
        await call.message.edit_text("✅ Доступ удалён")
        await call.answer()
        return

    if data.startswith("del_manual_"):
        record_id = int(data.split("_")[2])
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM manuals WHERE id=$1", record_id)
        await call.message.edit_text("✅ Мануал удалён")
        await call.answer()
        return

    if data.startswith("del_card_common_"):
        record_id = int(data.split("_")[3])
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM cards_common WHERE id=$1", record_id)
        await call.message.edit_text("✅ Карта удалена")
        await call.answer()
        return

    if data.startswith("del_card_gen_"):
        record_id = int(data.split("_")[3])
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM cards_gen WHERE id=$1", record_id)
        await call.message.edit_text("✅ Карта удалена")
        await call.answer()
        return

    # ----- Админ ввод (добавление) -----
    if data in ["code", "email", "domain", "access", "manual", "card_common", "card_gen"]:
        admin_input_state[uid] = data
        await call.message.answer(f"Введите текст для {data}:")
        await call.answer()
        return

    await call.answer("Неизвестный callback")

# ------------------- НАСТРОЙКА ВЕБХУКА И ЗАПУСК -------------------
async def on_startup():
    await init_db()
    await bot.set_webhook(WEBHOOK_URL)

async def on_shutdown():
    await bot.delete_webhook()
    if db_pool:
        await db_pool.close()

def main():
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    port = int(os.environ.get("PORT", 8080))
    web.run_app(app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
