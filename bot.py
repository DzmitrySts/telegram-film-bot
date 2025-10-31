#!/usr/bin/env python3
import os
import json
import logging
import base64
import requests
import datetime
from pathlib import Path
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

# ✅ Отключаем шумные логгеры, которые пишут ERROR при 409-Conflict
logging.getLogger("httpx").setLevel(logging.CRITICAL)
logging.getLogger("telegram").setLevel(logging.CRITICAL)
logging.getLogger("telegram.ext").setLevel(logging.CRITICAL)
logging.getLogger("telegram.ext.Updater").setLevel(logging.CRITICAL)
logging.getLogger("telegram.ext.Application").setLevel(logging.CRITICAL)

# ========== Настройки ==========
TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "481076515"))

FILMS_FILE = "films.json"
USERS_FILE = "users.json"

GITHUB_REPO = os.environ.get("GITHUB_REPO")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

# Обязательный канал
REQUIRED_CHANNELS = [
    ("@offmatch", "Offmatch")
]

# ========== Работа с JSON-файлами ==========
def load_json(filename):
    try:
        p = Path(filename)
        if not p.exists():
            p.write_text("{}", encoding="utf-8")
            return {}
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        logger.exception(f"Ошибка чтения {filename}")
        return {}

def save_json(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except:
        logger.exception(f"Ошибка записи {filename}")
        return
    commit_to_github(filename)

# ========== Коммит файлов ==========
def commit_to_github(filename):
    if not all([GITHUB_REPO, GITHUB_TOKEN]):
        return

    try:
        with open(filename, "r", encoding="utf-8") as f:
            content = f.read()

        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}?ref={GITHUB_BRANCH}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}"}

        r = requests.get(url, headers=headers)
        sha = r.json().get("sha") if r.status_code == 200 else None

        payload = {
            "message": f"Обновление {filename} через бот",
            "content": base64.b64encode(content.encode()).decode(),
            "branch": GITHUB_BRANCH
        }
        if sha:
            payload["sha"] = sha

        requests.put(url, headers=headers, json=payload)
    except Exception:
        logger.exception(f"Ошибка коммита {filename}")

# ========== Работа с пользователями ==========
def load_users():
    return load_json(USERS_FILE)

def save_users(users):
    save_json(USERS_FILE, users)

def add_user(user_id, username, first_name):
    users = load_users()
    uid = str(user_id)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    if uid not in users:
        users[uid] = {
            "username": username,
            "first_name": first_name,
            "first_seen": now
        }
    else:
        users[uid]["username"] = username
        users[uid]["first_name"] = first_name

    save_users(users)

# ========== Хендлеры ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    add_user(u.id, u.username, u.first_name)

    kb = [[InlineKeyboardButton("🔍 Поиск по коду", callback_data="search_code")]]
    await update.message.reply_text(
        "Привет! 👋\nНажмите кнопку «🔍 Поиск по коду» для поиска фильма.",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    users = load_users()
    await update.message.reply_text(f"👥 Уникальных пользователей: {len(users)}")

async def list_films(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    films = load_json(FILMS_FILE)
    if not films:
        return await update.message.reply_text("Пусто.")

    txt = "\n".join([f"{k} — {v['title']}" for k, v in sorted(films.items())])
    await update.message.reply_text(txt)

async def add_command(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    args = context.args
    if len(args) < 2:
        return await update.message.reply_text("Использование: /add <код> <название>")

    code = args[0]
    if not code.isdigit() or not 3 <= len(code) <= 5:
        return await update.message.reply_text("Код должен быть от 3 до 5 цифр!")

    films = load_json(FILMS_FILE)
    if code in films:
        return await update.message.reply_text("Такой код уже существует!")

    context.user_data["add_code"] = code
    context.user_data["add_title"] = " ".join(args[1:])
    await update.message.reply_text("Ок, отправьте видео.")

async def del_command(update, context):
    if update.effective_user.id != ADMIN_ID:
        return
    code = context.args[0]

    films = load_json(FILMS_FILE)
    if code in films:
        del films[code]
        save_json(FILMS_FILE, films)
        return await update.message.reply_text("✅ Удалено.")

    await update.message.reply_text("❌ Кода нет.")

async def edit_name(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    args = context.args
    if len(args) < 2:
        return await update.message.reply_text("Использование: /editn <код> <новое название>")

    code = args[0]
    new_title = " ".join(args[1:])
    films = load_json(FILMS_FILE)

    if code not in films:
        return await update.message.reply_text("Нет такого кода.")

    films[code]["title"] = new_title
    save_json(FILMS_FILE, films)
    await update.message.reply_text("✅ Название обновлено.")

async def edit_media(update, context):
    if update.effective_user.id != ADMIN_ID:
        return
    args = context.args

    if not args:
        return await update.message.reply_text("Использование: /editm <код>")

    code = args[0]
    films = load_json(FILMS_FILE)
    if code not in films:
        return await update.message.reply_text("❌ Кода нет.")

    context.user_data["edit_code"] = code
    await update.message.reply_text("Отправьте видео.")

async def handle_video(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    films = load_json(FILMS_FILE)

    # Редактирование
    if "edit_code" in context.user_data:
        code = context.user_data["edit_code"]
        films[code]["file_id"] = update.message.video.file_id
        save_json(FILMS_FILE, films)
        context.user_data.clear()
        return await update.message.reply_text("✅ Видео обновлено.")

    # Добавление
    if "add_code" in context.user_data:
        code = context.user_data["add_code"]
        title = context.user_data["add_title"]
        films[code] = {"title": title, "file_id": update.message.video.file_id}
        save_json(FILMS_FILE, films)
        context.user_data.clear()
        return await update.message.reply_text("✅ Фильм добавлен.")

async def handle_text(update, context):
    add_user(update.effective_user.id, update.effective_user.username, update.effective_user.first_name)

    txt = update.message.text.strip()
    if not context.user_data.get("waiting_code"):
        kb = [[InlineKeyboardButton("🔍 Поиск по коду", callback_data="search_code")]]
        return await update.message.reply_text(
            "❗ Сначала нажмите кнопку «🔍 Поиск по коду»",
            reply_markup=InlineKeyboardMarkup(kb)
        )

    if txt.isdigit() and 3 <= len(txt) <= 5:
        return await send_film_by_code(update, context, txt)
    elif txt.isdigit():
        return await update.message.reply_text("Код должен быть от 3 до 5 цифр!")
    else:
        return await update.message.reply_text("Код должен содержать только цифры!")

async def send_film_by_code(update, context, code):
    films = load_json(FILMS_FILE)
    film = films.get(code)

    if not film:
        return await update.message.reply_text("Нет фильма с таким кодом.")

    if "file_id" in film:
        await update.message.reply_video(film["file_id"], caption=film["title"])
    else:
        await update.message.reply_text("❌ У фильма нет файла.")

    # Сбрасываем флаг ожидания
    context.user_data.pop("waiting_code", None)

    # Отправляем отдельное сообщение с кнопкой поиска
    kb = [[InlineKeyboardButton("🔍 Поиск по коду", callback_data="search_code")]]
    await update.message.reply_text(
        "Чтобы продолжить, нажмите кнопку «🔍 Поиск по коду».",
        reply_markup=InlineKeyboardMarkup(kb)
    )

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

# ========== Suppress всех ошибок Conflict ==========
async def error_handler(update, context):
    if isinstance(context.error, Conflict):
        return  # Полностью подавляем
    logger.exception("Ошибка:", exc_info=context.error)

# ========== Точка входа ==========
def main():
    if not TOKEN:
        logger.error("Нет TELEGRAM_TOKEN")
        return

    app = ApplicationBuilder().token(TOKEN).build()

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
        return  # Полностью скрываем
    except Exception as e:
        logger.exception("Ошибка при запуске:", exc_info=e)


if __name__ == "__main__":
    main()
