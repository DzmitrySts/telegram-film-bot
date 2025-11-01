#!/usr/bin/env python3
import os
import logging
import asyncpg
import hashlib
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
from HdRezkaApi import HdRezkaSearch, HdRezkaApi

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
    return f"hd_{h}"

# ========== Кнопка поиска ==========
async def send_search_button(update, context):
    kb = [[InlineKeyboardButton("🔍 Поиск по коду", callback_data="search_code")],
          [InlineKeyboardButton("🎬 Поиск по названию", callback_data="search_name")]]
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

# ========== HdRezka Поиск ==========
async def handle_text(update, context):
    pool = context.bot_data["pool"]
    await add_user(pool, update.effective_user.id, update.effective_user.username, update.effective_user.first_name)
    txt = update.message.text.strip()

    # Если ожидаем код фильма
    if context.user_data.get("waiting_code"):
        if txt.isdigit() and 3 <= len(txt) <= 5:
            return await send_film_by_code(update, context, txt)
        elif txt.isdigit():
            return await update.message.reply_text("❌ Код должен быть от 3 до 5 цифр!")
        else:
            return await update.message.reply_text("❌ Код должен содержать только цифры!")

    # Если ожидаем поиск по названию
    if context.user_data.get("waiting_name"):
        search = HdRezkaSearch("https://hdrezka.ag/")(txt)
        if not search:
            return await update.message.reply_text("❌ Фильмы не найдены.")
        results = search[:5]  # максимум 5 вариантов
        kb = []
        name_map = {}
        for r in results:
            cb = make_callback(r['url'])
            kb.append([InlineKeyboardButton(r['title'], callback_data=cb)])
            name_map[cb] = r['url']
        context.user_data['name_map'] = name_map
        await update.message.reply_text("Выберите фильм:", reply_markup=InlineKeyboardMarkup(kb))
        return

    await send_search_button(update, context)

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

# ========== Callback ==========
async def button_callback(update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    # Проверка подписки на каналы
    not_sub = []
    for chan, name in REQUIRED_CHANNELS:
        try:
            member = await context.bot.get_chat_member(chan, user_id)
            if member.status not in ("member", "creator", "administrator"):
                not_sub.append(name)
        except:
            not_sub.append(name)
    if not_sub:
        buttons = [[InlineKeyboardButton(name, url=f"https://t.me/{chan[1:]}")] for chan, name in REQUIRED_CHANNELS]
        buttons.append([InlineKeyboardButton("✅ Подписался", callback_data="subscribed")])
        markup = InlineKeyboardMarkup(buttons)
        if data in ("search_code", "search_name", "subscribed"):
            return await query.message.reply_text("📢 Подпишитесь на канал:", reply_markup=markup)

    # Поиск по коду
    if data == "search_code":
        context.user_data["waiting_code"] = True
        return await query.message.reply_text("Введите код фильма (3–5 цифр):")

    # Поиск по названию
    if data == "search_name":
        context.user_data["waiting_name"] = True
        return await query.message.reply_text("Введите название фильма:")

    # Выбор фильма из поиска по названию
    if 'name_map' in context.user_data:
        url = context.user_data['name_map'].get(data)
        if url:
            rezka_obj = HdRezkaApi(url)
            # Выбираем первую озвучку
            translators = list(rezka_obj.translators_names.keys())[:5]
            kb = []
            trans_map = {}
            for t in translators:
                cb = make_callback(t)
                kb.append([InlineKeyboardButton(t, callback_data=cb)])
                trans_map[cb] = t
            context.user_data['trans_map'] = trans_map
            context.user_data['rezka_obj'] = rezka_obj
            await query.message.reply_text("Выберите озвучку:", reply_markup=InlineKeyboardMarkup(kb))
            return

    # ========== Исправленный блок выбора озвучки ==========
    if 'trans_map' in context.user_data:
        t_name = context.user_data['trans_map'].get(data)
        if t_name:
            context.user_data['selected_translator'] = t_name
            rezka_obj = context.user_data['rezka_obj']

            # Получаем ID переводчика по имени
            translator_id = rezka_obj.translators_names.get(t_name, {}).get("id")
            if translator_id is None:
                return await query.message.reply_text("❌ Не удалось найти перевод.")

            # Получаем поток по ID переводчика
            stream = rezka_obj.getStream(translation=translator_id)
            if not stream.videos:
                return await query.message.reply_text("❌ Видео недоступно.")

            # Выбор качества (первые 3)
            kb = []
            for q in list(stream.videos.keys())[:3]:
                cb = make_callback(q)
                kb.append([InlineKeyboardButton(q, callback_data=cb)])
            context.user_data['stream'] = stream
            context.user_data['quality_map'] = {make_callback(k): k for k in stream.videos.keys()}
            await query.message.reply_text("Выберите качество:", reply_markup=InlineKeyboardMarkup(kb))
            return

    # Выбор качества
    if 'quality_map' in context.user_data:
        q = context.user_data['quality_map'].get(data)
        if q:
            stream = context.user_data['stream']
            url = stream(q)
            await query.message.reply_text(f"Вот ваша ссылка на видео ({q}):\n{url}")
            context.user_data.clear()
            await send_search_button(update, context)
            return

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

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("list", list_films))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("del", del_command))
    app.add_handler(CommandHandler("editn", edit_name))
    app.add_handler(CommandHandler("editm", edit_media))

    # Сообщения
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))
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
