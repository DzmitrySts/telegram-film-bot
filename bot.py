#!/usr/bin/env python3
import os
import logging
import asyncpg
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

# ========== Кнопка поиска ==========
async def send_search_button(update, context):
    kb = [[InlineKeyboardButton("🔍 Поиск по коду", callback_data="search_code")]]
    await update.message.reply_text(
        "Чтобы продолжить, нажмите кнопку «🔍 Поиск по коду».",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# ========== Хендлеры ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pool = context.bot_data["pool"]
    u = update.effective_user
    await add_user(pool, u.id, u.username, u.first_name)

    kb = [[InlineKeyboardButton("🔍 Поиск по коду", callback_data="search_code")]]
    await update.message.reply_text(
        "Привет! 👋\nНажмите кнопку «🔍 Поиск по коду» для поиска фильма.",
        reply_markup=InlineKeyboardMarkup(kb)
    )

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

async def add_command(update, context):
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

async def del_command(update, context):
    if update.effective_user.id != ADMIN_ID:
        return
    code = context.args[0]
    pool = context.bot_data["pool"]
    result = await delete_film(pool, code)
    if "DELETE 0" in result:
        await update.message.reply_text("❌ Кода нет.")
    else:
        await update.message.reply_text("✅ Удалено.")

async def edit_name(update, context):
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

async def edit_media(update, context):
    if update.effective_user.id != ADMIN_ID:
        return
    args = context.args
    if not args:
        return await update.message.reply_text("Использование: /editm <код>")
    context.user_data["edit_code"] = args[0]
    await update.message.reply_text("Отправьте видео.")

async def handle_video(update, context):
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

async def handle_text(update, context):
    pool = context.bot_data["pool"]
    await add_user(pool, update.effective_user.id, update.effective_user.username, update.effective_user.first_name)

    txt = update.message.text.strip()
    if not context.user_data.get("waiting_code"):
        return await send_search_button(update, context)

    if txt.isdigit() and 3 <= len(txt) <= 5:
        return await send_film_by_code(update, context, txt)
    elif txt.isdigit():
        return await update.message.reply_text("❌ Код должен быть от 3 до 5 цифр!")
    else:
        return await update.message.reply_text("❌ Код должен содержать только цифры!")

async def send_film_by_code(update, context, code):
    pool = context.bot_data["pool"]
    film = await get_film(pool, code)
    if not film:
        return await update.message.reply_text("❌ Нет фильма с таким кодом. Попробуй ввести другой код.")
    if film["file_id"] is not None:
        await update.message.reply_video(film["file_id"], caption=film["title"])
        user_id = update.effective_user.id
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO user_films(user_id, film_code)
                VALUES($1, $2)
            """, user_id, code)
    else:
        await update.message.reply_text("❌ У фильма нет файла.")
    context.user_data.pop("waiting_code", None)
    await send_search_button(update, context)

async def button_callback(update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    not_sub = []
    for chan, name in REQUIRED_CHANNELS:
        try:
            member = await context.bot.get_chat_member(chan, user_id)
            if member.status not in ("member", "creator", "administrator"):
                not_sub.append(name)
        except:
            not_sub.append(name)

    buttons = [[InlineKeyboardButton(name, url=f"https://t.me/{chan[1:]}")] for chan, name in REQUIRED_CHANNELS]
    buttons.append([InlineKeyboardButton("✅ Подписался", callback_data="subscribed")])
    markup = InlineKeyboardMarkup(buttons)

    if query.data == "search_code":
        if not_sub:
            return await query.message.reply_text("📢 Подпишитесь на канал:", reply_markup=markup)
        context.user_data["waiting_code"] = True
        return await query.message.reply_text("Введите код фильма (3–5 цифр):")

    if query.data == "subscribed":
        if not_sub:
            return await query.message.reply_text("❌ Вы ещё не подписались.")
        context.user_data["waiting_code"] = True
        return await query.message.reply_text("Введите код фильма (3–5 цифр):")

async def error_handler(update, context):
    if isinstance(context.error, Conflict):
        return
    logger.exception("Ошибка:", exc_info=context.error)

# ========== MAIN ==========
def main():
    if not TOKEN or not DATABASE_URL:
        logger.error("Нет TELEGRAM_TOKEN или DATABASE_URL")
        return

    async def on_startup(app):
        app.bot_data["pool"] = await get_db_pool()

    app = ApplicationBuilder().token(TOKEN).post_init(on_startup).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("list", list_films))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("del", del_command))
    app.add_handler(CommandHandler("editn", edit_name))
    app.add_handler(CommandHandler("editm", edit_media))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
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
