#!/usr/bin/env python3
import os
import logging
import asyncpg
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from telegram.error import Conflict
from HdRezkaApi.search import HdRezkaSearch
from HdRezkaApi import HdRezkaApi
import urllib.parse

# ===== Логирование =====
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("telegram").setLevel(logging.CRITICAL)
logging.getLogger("telegram.ext").setLevel(logging.CRITICAL)

# ===== Настройки =====
TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "481076515"))
DATABASE_URL = os.environ.get("DATABASE_URL")

REQUIRED_CHANNELS = [
    ("@offmatch", "Offmatch")
]

# ===== Подключение к БД =====
async def get_db_pool():
    return await asyncpg.create_pool(DATABASE_URL)

# ===== Работа с пользователями =====
async def add_user(pool, user_id, username, first_name):
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users(id, username, first_name)
            VALUES($1, $2, $3)
            ON CONFLICT (id) DO UPDATE
            SET username = $2, first_name = $3
        """, user_id, username, first_name)

# ===== Работа с фильмами =====
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

# ===== HdRezka =====
async def search_hdrezka(query: str):
    search = HdRezkaSearch("https://hdrezka.ag/")
    results = search(query, find_all=False)
    if not results:
        return None
    return results[0]

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
    # качества (берем первый поток)
    try:
        first_stream = rezka_obj.getStream()
        kb.append([InlineKeyboardButton(q, callback_data=f"hd_quality_{q}") for q in first_stream.videos.keys()])
    except Exception:
        pass
    return InlineKeyboardMarkup(kb)

# ===== Кнопка поиска =====
async def send_search_button(update, context):
    kb = [[InlineKeyboardButton("🔍 Поиск по коду", callback_data="search_code")],
          [InlineKeyboardButton("🎬 Поиск по названию", callback_data="search_hd")]]
    await update.message.reply_text("Выберите способ поиска:", reply_markup=InlineKeyboardMarkup(kb))

# ===== Хендлеры =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pool = context.bot_data["pool"]
    u = update.effective_user
    await add_user(pool, u.id, u.username, u.first_name)
    await send_search_button(update, context)

# ===== Текстовые сообщения =====
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pool = context.bot_data["pool"]
    await add_user(pool, update.effective_user.id, update.effective_user.username, update.effective_user.first_name)
    txt = update.message.text.strip()

    # ===== Поиск по коду =====
    if context.user_data.get("waiting_code"):
        if txt.isdigit() and 3 <= len(txt) <= 5:
            film = await get_film(pool, txt)
            if film and film['file_id']:
                await update.message.reply_video(film['file_id'], caption=film['title'])
            else:
                await update.message.reply_text("❌ Нет фильма с таким кодом. Попробуй название.")
            context.user_data.pop("waiting_code", None)
            await send_search_button(update, context)
            return
        else:
            await update.message.reply_text("❌ Код должен содержать только 3–5 цифр!")
            return

    # ===== HdRezka поиск =====
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

# ===== CallbackQuery =====
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    # ===== Проверка подписок =====
    not_sub = []
    for chan, name in REQUIRED_CHANNELS:
        try:
            member = await context.bot.get_chat_member(chan, user_id)
            if member.status not in ("member", "creator", "administrator"):
                not_sub.append(name)
        except:
            not_sub.append(name)

    # ===== Кнопки поиска =====
    if data == "search_code":
        if not_sub:
            buttons = [[InlineKeyboardButton(name, url=f"https://t.me/{chan[1:]}")] for chan, name in REQUIRED_CHANNELS]
            buttons.append([InlineKeyboardButton("✅ Подписался", callback_data="subscribed")])
            markup = InlineKeyboardMarkup(buttons)
            await query.message.reply_text("📢 Подпишитесь на канал:", reply_markup=markup)
            return
        context.user_data["waiting_code"] = True
        await query.message.reply_text("Введите код фильма (3–5 цифр):")
        return

    if data == "search_hd":
        await query.message.reply_text("Введите название фильма для поиска:")
        return

    if data == "subscribed":
        if not_sub:
            await query.message.reply_text("❌ Вы ещё не подписались.")
            return
        context.user_data["waiting_code"] = True
        await query.message.reply_text("Введите код фильма (3–5 цифр):")
        return

    # ===== Кнопки HdRezka =====
    if 'hd_translator_' in data:
        translator = urllib.parse.unquote(data.split('_', 2)[2])
        context.user_data['translator'] = translator
        await query.message.reply_text(f"Выбрана озвучка: {translator}\nТеперь выберите качество.")
        return
    if 'hd_quality_' in data:
        quality = data.split('_', 2)[2]
        rezka_obj = context.user_data.get('rezka_obj')
        translator = context.user_data.get('translator')
        if rezka_obj:
            await send_hdrezka_film(query.message, context, rezka_obj, translator, quality)
        else:
            await query.message.reply_text("❌ Объект фильма не найден.")

# ===== Админ-команды =====
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

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = context.args
    if len(args) < 2:
        return await update.message.reply_text("Использование: /add <код> <название>")
    code = args[0]
    if not code.isdigit() or not 3 <= len(code) <= 5:
        return await update.message.reply_text("❌ Код должен быть от 3 до 5 цифр!")
    pool = context.bot_data["pool"]
    film = await get_film(pool, code)
    if film:
        return await update.message.reply_text("❌ Такой код уже существует!")
    context.user_data["add_code"] = code
    context.user_data["add_title"] = " ".join(args[1:])
    await update.message.reply_text("Ок, отправьте видео.")

async def del_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    code = context.args[0]
    pool = context.bot_data["pool"]
    result = await delete_film(pool, code)
    if "DELETE 0" in result:
        await update.message.reply_text("❌ Кода нет.")
    else:
        await update.message.reply_text("✅ Удалено.")

async def edit_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = context.args
    if len(args) < 2:
        return await update.message.reply_text("Использование: /editn <код> <новое название>")
    code = args[0]
    new_title = " ".join(args[1:])
    pool = context.bot_data["pool"]
    await update_film_title(pool, code, new_title)
    await update.message.reply_text("✅ Название обновлено.")

async def edit_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = context.args
    if not args:
        return await update.message.reply_text("Использование: /editm <код>")
    context.user_data["edit_code"] = args[0]
    await update.message.reply_text("Отправьте видео.")

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    pool = context.bot_data["pool"]
    if "edit_code" in context.user_data:
        code = context.user_data["edit_code"]
        await update_film_file(pool, code, update.message.video.file_id)
        context.user_data.clear()
        return await update.message.reply_text("✅ Видео обновлено.")
    if "add_code" in context.user_data:
        code = context.user_data["add_code"]
        title = context.user_data["add_title"]
        await add_film(pool, code, title, update.message.video.file_id)
        context.user_data.clear()
        return await update.message.reply_text("✅ Фильм добавлен.")

# ===== Ошибки =====
async def error_handler(update, context):
    if isinstance(context.error, Conflict):
        return
    logger.exception("Ошибка:", exc_info=context.error)

# ===== MAIN =====
def main():
    if not TOKEN or not DATABASE_URL:
        logger.error("Нет TELEGRAM_TOKEN или DATABASE_URL")
        return

    async def on_startup(app):
        app.bot_data["pool"] = await get_db_pool()

    app = ApplicationBuilder().token(TOKEN).post_init(on_startup).build()

    # ===== Основные =====
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))
    app.add_handler(CallbackQueryHandler(button_callback))

    # ===== Админ =====
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("list", list_films))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("del", del_command))
    app.add_handler(CommandHandler("editn", edit_name))
    app.add_handler(CommandHandler("editm", edit_media))

    # ===== Ошибки =====
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
