import json
import logging
import base64
import os
import requests
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# === НАСТРОЙКИ ===
TOKEN = "8295792965:AAFCOTaWj0vDhS1XfTP8MQ0Ip9gMundUxKw"
ADMIN_ID = 481076515
FILMS_FILE = "films.json"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === ЗАГРУЗКА/СОХРАНЕНИЕ ФИЛЬМОВ ===
def load_films():
    try:
        with open(FILMS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_films(films):
    with open(FILMS_FILE, "w", encoding="utf-8") as f:
        json.dump(films, f, ensure_ascii=False, indent=2)
    commit_films_to_github()  # коммитим сразу после изменения

# === КОММИТ НА GITHUB ===
def commit_films_to_github():
    repo = os.environ.get("GITHUB_REPO")
    branch = os.environ.get("GITHUB_BRANCH", "main")
    token = os.environ.get("GITHUB_TOKEN")
    if not all([repo, token]):
        logger.warning("GitHub данные не заданы, коммит пропущен")
        return

    with open(FILMS_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    url_get = f"https://api.github.com/repos/{repo}/contents/{FILMS_FILE}?ref={branch}"
    headers = {"Authorization": f"token {token}"}
    r = requests.get(url_get, headers=headers)
    sha = r.json().get("sha") if r.status_code == 200 else None

    data = {
        "message": "Обновление films.json через бота",
        "content": base64.b64encode(content.encode()).decode(),
        "branch": branch
    }
    if sha:
        data["sha"] = sha

    r2 = requests.put(url_get, headers=headers, json=data)
    if r2.status_code in [200, 201]:
        logger.info("Фильмы успешно закоммичены на GitHub")
    else:
        logger.error(f"Ошибка коммита: {r2.text}")

# === /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["🔍 Поиск по коду"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "Привет! 👋\nОтправь код фильма (например, 777), чтобы получить видео.",
        reply_markup=reply_markup
    )

# === /list для админа ===
async def list_films(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    films = load_films()
    if not films:
        await update.message.reply_text("🎞 В базе пока нет фильмов.")
        return
    msg = "🎬 Список фильмов:\n\n"
    for code, film in films.items():
        msg += f"{code} — {film.get('title', 'Без названия')}\n"
    await update.message.reply_text(msg)

# === /add <код> <название> ===
async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Использование: /add <код> <название>")
        return
    code = args[0]
    title = " ".join(args[1:])
    context.user_data["add_code"] = code
    context.user_data["add_title"] = title
    await update.message.reply_text(f"Отправьте видео для фильма '{title}'")

# === /del <код> ===
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

# === Обработка текста ===
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    films = load_films()

    if text == "🔍 Поиск по коду":
        await update.message.reply_text("Введи код фильма (3–5 цифр):")
        return

    if text.isdigit():
        if text in films:
            film = films[text]
            source = film.get("file_id") or film.get("url")
            caption = film.get("title", f"Фильм {text}")
            if not source:
                return  # Игнорируем, если нет файла
            try:
                await update.message.reply_video(video=source, caption=caption)
            except Exception as e:
                logger.exception("Ошибка при отправке фильма")
        else:
            await update.message.reply_text("Фильм с таким кодом не найден 😢")
    else:
        # Не отвечаем на нечисловой текст
        return

# === Обработка видео (для /add) ===
async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    code = context.user_data.get("add_code")
    title = context.user_data.get("add_title")
    if not code or not title:
        return
    file_id = update.message.video.file_id
    films = load_films()
    films[code] = {"title": title, "file_id": file_id}
    save_films(films)
    await update.message.reply_text(f"Фильм '{title}' с кодом {code} добавлен ✅")
    context.user_data.pop("add_code")
    context.user_data.pop("add_title")

# === ЗАПУСК ===
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("list", list_films))
app.add_handler(CommandHandler("add", add_command))
app.add_handler(CommandHandler("del", del_command))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
app.add_handler(MessageHandler(filters.VIDEO, handle_video))

if __name__ == "__main__":
    print("Бот запущен...")
    app.run_polling()
