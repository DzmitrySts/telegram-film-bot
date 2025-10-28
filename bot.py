import json
import logging
from pathlib import Path
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Токен бота и админ
TOKEN = "8295792965:AAFCOTaWj0vDhS1XfTP8MQ0Ip9gMundUxKw"
ADMIN_ID = 481076515
FILMS_PATH = Path("films.json")

# Загрузка/сохранение фильмов
def load_films():
    if FILMS_PATH.exists():
        return json.loads(FILMS_PATH.read_text(encoding="utf-8"))
    return {}

def save_films(data):
    FILMS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

FILMS = load_films()

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Введи код из 3–5 цифр, я пришлю фильм 🎬")

# Список всех фильмов
async def list_codes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not FILMS:
        await update.message.reply_text("Фильмы не загружены.")
        return
    lines = [f"{k}: {v.get('title','')}" for k,v in FILMS.items()]
    await update.message.reply_text("\n".join(lines))

# Удаление фильма по коду
async def del_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Только админ может это делать.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Использование: /del <код>")
        return
    code = args[0]
    if code in FILMS:
        FILMS.pop(code)
        save_films(FILMS)
        await update.message.reply_text(f"Код {code} удалён ✅")
    else:
        await update.message.reply_text(f"Код {code} не найден 😕")

# Получение фильма по коду
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or not (3 <= len(text) <= 5):
        await update.message.reply_text("Отправь код из 3–5 цифр.")
        return
    film = FILMS.get(text)
    if not film:
        await update.message.reply_text("Фильм с таким кодом не найден 😕")
        return
    title = film.get("title", "")
    ftype = film.get("type")
    source = film.get("source")
    caption = f"{title}".strip()
    try:
        if ftype == "url":
            await update.message.reply_text(f"{caption}\n{source}")
        elif ftype == "telegram_file":
            await context.bot.send_video(chat_id=update.effective_chat.id, video=source, caption=caption)
        elif ftype == "local":
            if Path(source).exists():
                with open(source, "rb") as f:
                    await context.bot.send_video(chat_id=update.effective_chat.id, video=InputFile(f), caption=caption)
            else:
                await update.message.reply_text("Файл локально не найден.")
        else:
            await update.message.reply_text("Неизвестный тип источника.")
    except Exception:
        logger.exception("Ошибка при отправке фильма")
        await update.message.reply_text("Ошибка при отправке фильма, попробуй позже.")

# Обработка видео с подписью "<код> <название фильма>"
async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Только админ может добавлять фильмы.")
        return

    if not update.message.caption:
        await update.message.reply_text("Добавьте код и название фильма в подписи к видео: <код> <название>")
        return

    parts = update.message.caption.strip().split(maxsplit=1)
    if len(parts) < 1 or not parts[0].isdigit():
        await update.message.reply_text("Код должен быть цифрами в начале подписи.")
        return

    code = parts[0]
    title = parts[1] if len(parts) > 1 else ""
    file_id = update.message.video.file_id

    FILMS[code] = {"title": title, "type": "telegram_file", "source": file_id}
    save_films(FILMS)
    await update.message.reply_text(f"Фильм с кодом {code} добавлен ✅")

# Запуск бота
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_codes))
    app.add_handler(CommandHandler("del", del_code))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))  # Обработка видео
    print("✅ Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()