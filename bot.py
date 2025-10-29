#!/usr/bin/env python3
import os
import json
import logging
import base64
import requests
from pathlib import Path
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ========== Настройки ==========
TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "481076515"))
FILMS_FILE = "films.json"

GITHUB_REPO = os.environ.get("GITHUB_REPO")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

# ========== Логирование ==========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== Работа с JSON ==========
def load_films():
    try:
        p = Path(FILMS_FILE)
        if not p.exists():
            p.write_text("{}", encoding="utf-8")
            return {}
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.exception("Ошибка чтения films.json")
        return {}

def save_films(films: dict):
    try:
        with open(FILMS_FILE, "w", encoding="utf-8") as f:
            json.dump(films, f, ensure_ascii=False, indent=2)
    except Exception:
        logger.exception("Ошибка записи films.json")
        return
    commit_films_to_github()

def commit_films_to_github():
    if not all([GITHUB_REPO, GITHUB_TOKEN]):
        logger.warning("GitHub параметры не заданы — коммит пропущен")
        return
    try:
        with open(FILMS_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{FILMS_FILE}?ref={GITHUB_BRANCH}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}"}
        r = requests.get(url, headers=headers)
        sha = r.json().get("sha") if r.status_code == 200 else None
        payload = {
            "message": "Обновление films.json через бот",
            "content": base64.b64encode(content.encode()).decode(),
            "branch": GITHUB_BRANCH,
        }
        if sha:
            payload["sha"] = sha
        put_resp = requests.put(url, headers=headers, json=payload)
        if put_resp.status_code in (200, 201):
            logger.info("✅ Коммит films.json на GitHub выполнен.")
        else:
            logger.error("❌ Ошибка коммита на GitHub: %s", put_resp.text)
    except Exception:
        logger.exception("Ошибка при коммите films.json на GitHub")

# ========== Команды ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["🔍 Поиск по коду"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "Привет! 👋\nНажми «🔍 Поиск по коду» и введи код фильма (3–5 цифр).",
        reply_markup=reply_markup
    )

async def list_films(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    films = load_films()
    if not films:
        await update.message.reply_text("🎞 В базе пока нет фильмов.")
        return

    sorted_films = dict(sorted(films.items(), key=lambda x: int(x[0])))
    lines = [f"{code} — {data.get('title','Без названия')}" for code, data in sorted_films.items()]
    await update.message.reply_text("🎬 Список фильмов (по возрастанию кода):\n\n" + "\n".join(lines))

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Использование: /add <код> <название>")
        return
    code = args[0]
    if not code.isdigit() or not (3 <= len(code) <= 5):
        await update.message.reply_text("❌ Код должен состоять из 3–5 цифр.")
        return
    films = load_films()
    if code in films:
        await update.message.reply_text(f"❌ Код {code} уже существует. Используйте другой код.")
        return
    title = " ".join(args[1:])
    context.user_data["add_code"] = code
    context.user_data["add_title"] = title
    await update.message.reply_text(f"ОК. Теперь отправьте видео для фильма: {title} (код {code})")

async def del_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = context.args
    if not args:
        return
    code = args[0]
    films = load_films()
    if code in films:
        films.pop(code)
        save_films(films)
        await update.message.reply_text(f"Фильм с кодом {code} удалён ✅")

async def edit_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Использование: /editn <код> <новое название>")
        return
    code = args[0]
    new_title = " ".join(args[1:])
    films = load_films()
    if code not in films:
        await update.message.reply_text(f"❌ Код {code} не найден.")
        return
    films[code]["title"] = new_title
    save_films(films)
    await update.message.reply_text(f"Название фильма с кодом {code} обновлено на '{new_title}' ✅")

async def edit_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = context.args
    if not args:
        await update.message.reply_text("Использование: /editm <код> и отправьте новый файл видео.")
        return
    code = args[0]
    films = load_films()
    if code not in films:
        await update.message.reply_text(f"❌ Код {code} не найден.")
        return
    context.user_data["edit_video_code"] = code
    await update.message.reply_text(f"Теперь отправьте новый видеофайл для кода {code}.")

# ========== Пользовательские сообщения ==========
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()

    # Если нажал кнопку — включаем режим поиска
    if txt == "🔍 Поиск по коду":
        context.user_data["waiting_code"] = True
        await update.message.reply_text("Введите код фильма (3–5 цифр):")
        return

    # Если пользователь не нажал кнопку, но вводит что-то
    if not context.user_data.get("waiting_code"):
        await update.message.reply_text("❗ Сначала нажмите кнопку «🔍 Поиск по коду», чтобы начать поиск фильма.")
        return

    # Если нажал кнопку и теперь вводит код
    if not txt.isdigit():
        await update.message.reply_text("❌ Допускаются только цифры (3–5 цифр).")
        return
    if not (3 <= len(txt) <= 5):
        await update.message.reply_text("❌ Код должен состоять из 3–5 цифр.")
        return

    films = load_films()
    film = films.get(txt)
    if not film:
        await update.message.reply_text("😕 Фильм с таким кодом не найден. Попробуйте другой код.")
        return  # остаётся в режиме поиска

    # Код найден — отправляем фильм и выходим из режима поиска
    context.user_data.pop("waiting_code", None)
    await send_film_by_code(update, context, txt)

async def send_film_by_code(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str):
    films = load_films()
    film = films.get(code)
    if not film:
        await update.message.reply_text("😕 Фильм с таким кодом не найден.")
        return
    title = film.get("title", "")
    file_id = film.get("file_id")
    url = film.get("url")
    caption = title or f"Фильм {code}"
    try:
        if file_id:
            await update.message.reply_video(video=file_id, caption=caption)
        elif url:
            await update.message.reply_text(f"{caption}\n{url}")
        else:
            await update.message.reply_text("❌ У этого фильма нет файла или ссылки.")
    except Exception:
        logger.exception("Ошибка при отправке фильма")
        await update.message.reply_text("Ошибка при отправке фильма, попробуйте позже.")

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    code = context.user_data.get("add_code")
    title = context.user_data.get("add_title")
    if code and title:
        file_id = None
        if update.message.video:
            file_id = update.message.video.file_id
        elif update.message.document and update.message.document.mime_type and "video" in update.message.document.mime_type:
            file_id = update.message.document.file_id
        else:
            await update.message.reply_text("Пожалуйста, отправьте видео-файл (MP4).")
            return
        films = load_films()
        films[code] = {"title": title, "file_id": file_id}
        save_films(films)
        await update.message.reply_text(f"Фильм '{title}' с кодом {code} добавлен ✅")
        context.user_data.pop("add_code", None)
        context.user_data.pop("add_title", None)
        return

    edit_code = context.user_data.get("edit_video_code")
    if edit_code:
        file_id = None
        if update.message.video:
            file_id = update.message.video.file_id
        elif update.message.document and update.message.document.mime_type and "video" in update.message.document.mime_type:
            file_id = update.message.document.file_id
        else:
            await update.message.reply_text("Пожалуйста, отправьте видео-файл (MP4).")
            return
        films = load_films()
        films[edit_code]["file_id"] = file_id
        save_films(films)
        await update.message.reply_text(f"Видео для фильма с кодом {edit_code} обновлено ✅")
        context.user_data.pop("edit_video_code", None)

# ========== Точка входа ==========
def main():
    if not TOKEN:
        logger.error("TELEGRAM_TOKEN не задан.")
        return

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_films))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("del", del_command))
    app.add_handler(CommandHandler("editn", edit_name))
    app.add_handler(CommandHandler("editm", edit_video))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))

    logger.info("Бот запущен.")
    app.run_polling()

if __name__ == "__main__":
    main()
