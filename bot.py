import json
import logging
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# === НАСТРОЙКИ ===
TOKEN = "8295792965:AAFCOTaWj0vDhS1XfTP8MQ0Ip9gMundUxKw"
ADMIN_ID = 481076515
FILMS_FILE = "films.json"

logging.basicConfig(level=logging.INFO)

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

# === КОМАНДЫ ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["🔍 Поиск по коду"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "Привет! 👋\nОтправь код фильма (например, 777), чтобы получить видео.",
        reply_markup=reply_markup
    )

# === СПИСОК ФИЛЬМОВ (только для админа) ===
async def list_films(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        # просто игнорируем без ответа
        return

    films = load_films()
    if not films:
        await update.message.reply_text("🎞 В базе пока нет фильмов.")
        return

    msg = "🎬 Список фильмов:\n\n"
    for code, film in films.items():
        title = film.get("title", "Без названия")
        msg += f"{code} — {title}\n"

    await update.message.reply_text(msg)

# === ОБРАБОТКА ТЕКСТА ===
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    films = load_films()

    # Обработка кнопки
    if text == "🔍 Поиск по коду":
        await update.message.reply_text("Введи код фильма (3–5 цифр):")
        return

    # Проверка — введён код
    if text.isdigit():
        if text in films:
            film = films[text]
            source = film.get("file_id") or film.get("url")
            caption = film.get("title", f"Фильм {text}")

            if not source:
                await update.message.reply_text("❌ Ошибка: у фильма нет файла или ссылки.")
                return

            try:
                await update.message.reply_video(video=source, caption=caption)
            except Exception as e:
                logging.error(f"Ошибка при отправке фильма: {e}")
                await update.message.reply_text("Ошибка при отправке фильма, попробуй позже.")
        else:
            await update.message.reply_text("Фильм с таким кодом не найден 😢")
    else:
        await update.message.reply_text("Отправь только числовой код (3–5 цифр).")

# === ЗАПУСК ===
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("list", list_films))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

if __name__ == "__main__":
    print("Бот запущен...")
    app.run_polling()
