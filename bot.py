#!/usr/bin/env python3
import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
import asyncpg
from HdRezkaApi.search import HdRezkaSearch
from HdRezkaApi import HdRezkaApi
import urllib.parse

# ====== Логирование ======
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("telegram").setLevel(logging.CRITICAL)
logging.getLogger("telegram.ext").setLevel(logging.CRITICAL)

# ====== Настройки ======
TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "481076515"))
DATABASE_URL = os.environ.get("DATABASE_URL")

# ====== Подключение к БД ======
async def get_db_pool():
    return await asyncpg.create_pool(DATABASE_URL)

# ====== Работа с пользователями ======
async def add_user(pool, user_id, username, first_name):
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users(id, username, first_name)
            VALUES($1, $2, $3)
            ON CONFLICT (id) DO UPDATE
            SET username = $2, first_name = $3
        """, user_id, username, first_name)

# ====== Работа с фильмами ======
async def get_film(pool, code):
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM films WHERE code=$1", code)

# ====== HdRezka ======
async def search_hdrezka(query: str):
    search = HdRezkaSearch("https://hdrezka.ag/")
    results = search(query, find_all=False)
    if not results:
        return None
    return results[0]  # {'title':..., 'url':..., 'image':..., 'rating':...}

async def get_hdrezka_film(url: str):
    rezka = HdRezkaApi(url)
    if not rezka.ok:
        return None
    return rezka

async def send_hdrezka_film(update, context, rezka_obj, translator=None, quality='720p'):
    try:
        stream = rezka_obj.getStream(translation=translator) if translator else rezka_obj.getStream()
        video_url = stream(quality)
    except Exception:
        await update.message.reply_text("❌ Не удалось получить видео.")
        return
    caption = f"{rezka_obj.name}\n⭐ Рейтинг: {rezka_obj.rating.value}"
    await update.message.reply_video(video_url, caption=caption)

def build_hdrezka_buttons(rezka_obj):
    kb = []
    # озвучки
    for t_name in rezka_obj.translators_names.keys():
        kb.append([InlineKeyboardButton(t_name, callback_data=f"hd_translator_{urllib.parse.quote(t_name)}")])
    # качества (для фильма берем первый поток)
    try:
        first_stream = rezka_obj.getStream()
        kb.append([InlineKeyboardButton(q, callback_data=f"hd_quality_{q}") for q in first_stream.videos.keys()])
    except Exception:
        pass
    return InlineKeyboardMarkup(kb)

# ====== Хендлеры ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pool = context.bot_data["pool"]
    u = update.effective_user
    await add_user(pool, u.id, u.username, u.first_name)
    kb = [[InlineKeyboardButton("🔍 Поиск фильма", callback_data="search_hd")]]
    await update.message.reply_text(
        "Привет! 👋\nНажмите кнопку «🔍 Поиск фильма».",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pool = context.bot_data["pool"]
    await add_user(pool, update.effective_user.id, update.effective_user.username, update.effective_user.first_name)
    txt = update.message.text.strip()
    # Если это код (число 3-5 цифр)
    if txt.isdigit() and 3 <= len(txt) <= 5:
        film = await get_film(pool, txt)
        if film and film['file_id']:
            await update.message.reply_video(film['file_id'], caption=film['title'])
        else:
            await update.message.reply_text("❌ Нет фильма с таким кодом. Попробуй название.")
        return
    # Иначе ищем по HdRezka
    rezka_result = await search_hdrezka(txt)
    if not rezka_result:
        await update.message.reply_text("❌ Фильм не найден.")
        return
    rezka_obj = await get_hdrezka_film(rezka_result['url'])
    if not rezka_obj:
        await update.message.reply_text("❌ Ошибка при получении фильма.")
        return
    context.user_data['rezka_obj'] = rezka_obj
    await update.message.reply_text(
        f"🎬 {rezka_obj.name}\n⭐ Рейтинг: {rezka_obj.rating.value}\nВыберите озвучку и качество ниже:",
        reply_markup=build_hdrezka_buttons(rezka_obj)
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if 'hd_translator_' in data:
        translator = urllib.parse.unquote(data.split('_', 2)[2])
        context.user_data['translator'] = translator
        await query.message.reply_text(f"Выбрана озвучка: {translator}\nТеперь выберите качество.")
        return
    if 'hd_quality_' in data:
        quality = data.split('_')[2]
        rezka_obj = context.user_data.get('rezka_obj')
        translator = context.user_data.get('translator')
        if rezka_obj:
            await send_hdrezka_film(query.message, context, rezka_obj, translator, quality)
        else:
            await query.message.reply_text("❌ Объект фильма не найден.")

async def error_handler(update, context):
    logger.exception("Ошибка:", exc_info=context.error)

# ====== MAIN ======
def main():
    if not TOKEN or not DATABASE_URL:
        logger.error("Нет TELEGRAM_TOKEN или DATABASE_URL")
        return

    async def on_startup(app):
        app.bot_data["pool"] = await get_db_pool()

    app = ApplicationBuilder().token(TOKEN).post_init(on_startup).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_error_handler(error_handler)

    logger.info("✅ Бот запущен.")
    app.run_polling()

if __name__ == "__main__":
    main()
