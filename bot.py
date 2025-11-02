#!/usr/bin/env python3
import os
import logging
import asyncpg
import hashlib
import re
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import Conflict
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# ========== Логирование ==========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("telegram").setLevel(logging.CRITICAL)
logging.getLogger("telegram.ext").setLevel(logging.CRITICAL)

# ========== Настройки ==========
TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "481076515"))
DATABASE_URL = os.environ.get("DATABASE_URL")

REQUIRED_CHANNELS = [
    ("@offmatch", "Offmatch")
]

# ========== Подключение к БД ==========
async def get_db_pool():
    return await asyncpg.create_pool(DATABASE_URL)

# ========== Работа с пользователями ==========
async def add_user(pool, user_id, username, first_name):
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users(id, username, first_name)
            VALUES($1, $2, $3)
            ON CONFLICT (id) DO UPDATE
            SET username = $2, first_name = $3
        """, user_id, username, first_name)

# ========== Работа с фильмами ==========
async def add_film(pool, code, title, file_id):
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO films(code, title, file_id)
            VALUES($1, $2, $3)
            ON CONFLICT (code) DO NOTHING
        """, code, title, file_id)

async def update_film_file(pool, code, file_id):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE films SET file_id=$1 WHERE code=$2", file_id, code)

async def update_film_title(pool, code, new_title):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE films SET title=$1 WHERE code=$2", new_title, code)

async def delete_film(pool, code):
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM films WHERE code=$1", code)
        return result

async def get_film(pool, code):
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM films WHERE code=$1", code)

async def list_all_films(pool):
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT code, title FROM films ORDER BY code")

# ========== Callback Data Helper ==========
def make_callback(data: str) -> str:
    h = hashlib.md5(data.encode('utf-8')).hexdigest()
    return f"vidsrc_{h}"

# ========== Кнопка поиска ==========
async def send_search_button(update, context):
    kb = [[InlineKeyboardButton("🔍 Поиск по названию", callback_data="search_name")]]
    await update.message.reply_text(
        "Чтобы продолжить, выберите вариант:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# ========== Хендлеры ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pool = context.bot_data["pool"]
    u = update.effective_user
    await add_user(pool, u.id, u.username, u.first_name)
    await send_search_button(update, context)

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    pool = context.bot_data["pool"]
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM users")
    await update.message.reply_text(f"👥 Уникальных пользователей: {count}")

async def list_films(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    pool = context.bot_data["pool"]
    rows = await list_all_films(pool)
    if not rows:
        return await update.message.reply_text("Пусто.")
    txt = "\n".join([f"{r['code']} — {r['title']}" for r in rows])
    await update.message.reply_text(txt)

# ========== Новый источник: vidsrc-embed.ru ==========
async def fetch_mp4(embed_url):
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(embed_url)
        resp.raise_for_status()
        # ищем mp4 в embed странице
        matches = re.findall(r'source src="(https?://[^"]+\.mp4)"', resp.text)
        if matches:
            return matches[0]
        return None

async def handle_text(update, context):
    txt = update.message.text.strip()
    if context.user_data.get("waiting_name"):
        search_query = txt
        # Поиск на vidsrc
        async with httpx.AsyncClient(timeout=30) as client:
            search_url = f"https://vidsrc-embed.ru/movies/latest/page-1.json"
            r = await client.get(search_url)
            r.raise_for_status()
            movies = r.json()
            results = [m for m in movies if search_query.lower() in m['title'].lower()][:5]

        if not results:
            return await update.message.reply_text("❌ Фильмы не найдены.")

        kb = []
        url_map = {}
        for m in results:
            cb = make_callback(m['url'])
            kb.append([InlineKeyboardButton(m['title'], callback_data=cb)])
            url_map[cb] = m['url']

        context.user_data['url_map'] = url_map
        await update.message.reply_text("Выберите фильм:", reply_markup=InlineKeyboardMarkup(kb))
        return

    await send_search_button(update, context)

async def button_callback(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data

    if 'url_map' in context.user_data and data in context.user_data['url_map']:
        embed_url = context.user_data['url_map'][data]
        await query.message.reply_text("🔎 Получаем видео…")
        try:
            mp4_url = await fetch_mp4(embed_url)
            if mp4_url:
                await query.message.reply_video(video=mp4_url, caption="Ваш фильм 🎬")
            else:
                await query.message.reply_text(f"❌ Не удалось получить mp4. Смотрите через embed:\n{embed_url}")
        except Exception as e:
            await query.message.reply_text(f"❌ Ошибка при получении видео: {e}")
        context.user_data.clear()
        await send_search_button(update, context)
        return

    if data == "search_name":
        context.user_data["waiting_name"] = True
        return await query.message.reply_text("Введите название фильма:")

# ========== Ошибки ==========
async def error_handler(update, context):
    if isinstance(context.error, Conflict):
        return
    logger.exception("Ошибка:", exc_info=context.error)

# ========== MAIN ==========
def main():
    if not TOKEN:
        logger.error("Нет TELEGRAM_TOKEN")
        return

    async def on_startup(app):
        app.bot_data["pool"] = await get_db_pool()

    app = ApplicationBuilder().token(TOKEN).post_init(on_startup).build()

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("list", list_films))

    # Сообщения
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Callback
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_error_handler(error_handler)

    logger.info("✅ Бот запущен.")
    try:
        app.run_polling()
    except Conflict:
        return
    except Exception as e:
        logger.exception("Ошибка при запуске:", exc_info=e)

if __name__ == "__main__":
    main()
