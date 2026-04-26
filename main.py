# ================== ЧАСТЬ 2 ==================
app = web.Application()

dp.include_router(router)

# webhook
async def webhook(request):
    update = await request.json()
    await dp.feed_update(bot, update)
    return web.Response()

app.router.add_post(WEBHOOK_PATH, webhook)

app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)


# ================== MAIN HANDLER ==================
@router.message()
async def main(msg: Message):
    uid = msg.from_user.id
    text = msg.text

    user = await get_user(uid)

    # ===== EMAIL =====
    if text == "📧 Почта":
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT id,value FROM emails ORDER BY RANDOM() LIMIT 1")

            if not row:
                return await msg.answer("нет почт")

            await conn.execute("DELETE FROM emails WHERE id=$1", row["id"])

        last_item[uid] = ("email", row["value"])
        await log(uid, "take_email", row["value"])

        return await msg.answer(row["value"])

    # ===== DOMAIN =====
    if text == "🌐 Домен":
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT id,value FROM domains ORDER BY RANDOM() LIMIT 1")

            if not row:
                return await msg.answer("нет доменов")

            await conn.execute("DELETE FROM domains WHERE id=$1", row["id"])

        last_item[uid] = ("domain", row["value"])
        await log(uid, "take_domain", row["value"])

        return await msg.answer(row["value"])

    # ===== ACCESS =====
    if text == "🔑 Доступы":
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT value FROM accesses")

        return await msg.answer("\n".join([r["value"] for r in rows]) or "нет")

    # ===== MANUALS =====
    if text == "📚 Мануалы":
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT value FROM manuals")

        return await msg.answer("\n".join([r["value"] for r in rows]) or "нет")

    # ===== ADMIN PANEL =====
    if text == "🛠 Админ" and uid == ADMIN_ID:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Код", callback_data="code")],
            [InlineKeyboardButton(text="➕ Почта", callback_data="email")],
            [InlineKeyboardButton(text="➕ Домен", callback_data="domain")],
            [InlineKeyboardButton(text="➕ Доступ", callback_data="access")],
            [InlineKeyboardButton(text="➕ Мануал", callback_data="manual")]
        ])

        return await msg.answer("⚙️ админ", reply_markup=kb)


# ================== RUN ==================
if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
