import os
import asyncio
import asyncpg

from aiohttp import web
from aiogram import Bot, Dispatcher, Router
from aiogram.types import Update, Message

# ================== ENV ==================
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")
BASE_URL = os.getenv("WEBHOOK_URL")

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = BASE_URL + WEBHOOK_PATH

# ================== BOT ==================
bot = Bot(TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# ================== DB ==================
pool = None

async def init_db():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL)

    async with pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users(
            id BIGINT PRIMARY KEY,
            role TEXT
        );
        """)

# ================== HANDLERS ==================
@router.message()
async def all_messages(msg: Message):
    await msg.answer("✅ бот работает")

# ================== WEBHOOK ==================
async def webhook(request):
    data = await request.json()

    update = Update.model_validate(data)
    await dp.feed_update(bot, update)

    return web.Response(text="ok")

# ================== STARTUP ==================
async def on_startup(app):
    await init_db()

    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(WEBHOOK_URL)

    print("WEBHOOK SET:", WEBHOOK_URL)

# ================== APP ==================
app = web.Application()
app.router.add_post(WEBHOOK_PATH, webhook)

app.on_startup.append(on_startup)

# ================== RUN ==================
if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
