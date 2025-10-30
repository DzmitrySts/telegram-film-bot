#!/usr/bin/env python3
import os
import json
import logging
import base64
import requests
from pathlib import Path
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
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

# ========== Работа с films.json ==========
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
    # Попытка закоммитить на GitHub (если настроено)
    try:
        commit_films_to_github()
    except Exception:
        logger.exception("Ошибка при попытке коммита films.json на GitHub")

# ========== Коммит в GitHub ==========
def commit_films_to_github():
    if not all([GITHUB_REPO, GITHUB_TOKEN]):
        logger.debug("GitHub параметры не заданы — коммит пропущен")
        return
    try:
        with open(FILMS_FILE, "r", encoding="utf-8") as f:
            content = f.read()

        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{FILMS_FILE}?ref={GITHUB_BRANCH}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}"}

        # Получаем SHA, если файл уже существует
        r = requests.get(url, headers=headers, timeout=10)
        sha = r.json().get("sha") if r.status_code == 200 else None

        payload = {
            "message": "Обновление films.json через бот",
            "content": base64.b64encode(content.encode()).decode(),
            "branch": GITHUB_BRANCH,
        }
        if sha:
            payload["sha"] = sha

        put_resp = requests.put(url, headers=headers, json=payload, timeout=10)
        if put_resp.status_code in (200, 201):
            logger.info("✅ Коммит films.json на GitHub выполнен.")
        else:
            logger.error("❌ Ошибка коммита на GitHub: %s", put_resp.text)
    except Exception:
        logger.exception("Ошибка при коммите films.json на GitHub")

# ========== Утилиты UI ==========
def inline_search_button():
    kb = [[InlineKeyboardButton("🔍 Поиск по коду", callback_data="search_code")]]
    return InlineKeyboardMarkup(kb)

# ========== Хендлеры ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Приветствие — кнопка присутствует под сообщением.
    """
    await update.message.reply_text(
        "Привет! 👋\nНажмите кнопку «🔍 Поиск по коду», чтобы начать поиск фильма.",
        reply_markup=inline_search_button(),
    )

# --- Админ: список фильмов (отсортированно) ---
async def list_films(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    films = load_films()
    if not films:
        await update.message.reply_text("🎞 В базе пока нет фильмов.")
        return
    lines = [f"{k} — {films[k].get('title','Без названия')}" for k in sorted(films.keys())]
    await update.message.reply_text("🎬 Список фильмов:\n\n" + "\n".join(lines))

# --- Админ: добавить (устанавливает код+название, ждет видео) ---
async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Использование: /add <код> <название>")
        return
    code = args[0]
    if not code.isdigit() or not 3 <= len(code) <= 5:
        await update.message.reply_text("❌ Код должен состоять из 3–5 цифр.")
        return
    films = load_films()
    if code in films:
        await update.message.reply_text(f"❌ Код {code} уже существует.")
        return
    title = " ".join(args[1:])
    context.user_data["add_code"] = code
    context.user_data["add_title"] = title
    await update.message.reply_text(f"ОК. Теперь отправьте видео для фильма: {title} (код {code})")

# --- Админ: удалить код ---
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

# --- Админ: поменять название ---
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
        await update.message.reply_text(f"Код {code} не найден.")
        return
    films[code]["title"] = new_title
    save_films(films)
    await update.message.reply_text(f"Название фильма с кодом {code} изменено на '{new_title}' ✅")

# --- Админ: ждать новое видео для кода (editm) ---
async def edit_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /editm <код> — админский режим: бот ждёт следующее пришедшее видео и заменит file_id.
    """
    if update.effective_user.id != ADMIN_ID:
        return
    args = context.args
    if not args:
        await update.message.reply_text("Использование: /editm <код> — затем отправьте новое видео")
        return
    code = args[0]
    films = load_films()
    if code not in films:
        await update.message.reply_text(f"Код {code} не найден в базе.")
        return
    context.user_data["edit_code"] = code
    await update.message.reply_text(f"Ожидаю новое видео для фильма '{films[code].get('title','(без названия)')}' (код {code}). Отправьте видео как обычное видео или документ.")

# ========== Кнопки (inline) ==========
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Нажатие на inline-кнопку запускает режим ожидания кода.
    При этом бот присылает *текст* "Введите код..." без кнопки.
    """
    query = update.callback_query
    await query.answer()
    if query.data == "search_code":
        context.user_data["waiting_code"] = True
        # Отправляем ПРИГЛАШЕНИЕ ВВЕСТИ КОД — без кнопки (по запросу)
        await query.message.reply_text("Введите код фильма (3–5 цифр):")

# ========== Обработка текстовых сообщений (поиск) ==========
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    if not txt:
        return

    waiting = context.user_data.get("waiting_code", False)

    # Если пользователь ещё не нажал кнопку — показываем подсказку с кнопкой
    if not waiting:
        await update.message.reply_text(
            "❗ Сначала нажмите кнопку «🔍 Поиск по коду», чтобы начать поиск фильма.",
            reply_markup=inline_search_button(),
        )
        return

    # Теперь пользователь в режиме ввода кода
    # Валидация: только цифры, длина 3..5
    if not txt.isdigit():
        await update.message.reply_text("❌ Допускаются только цифры (3–5 цифр). Введите код снова:")
        return
    if not 3 <= len(txt) <= 5:
        await update.message.reply_text("❌ Код должен быть от 3 до 5 цифр. Введите код снова:")
        return

    # Валидный формат — отправляем фильм
    await send_film_by_code(update, context, txt)

# ========== Отправка фильма по коду ==========
async def send_film_by_code(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str):
    films = load_films()
    film = films.get(code)

    # Если фильма нет — сообщаем и *оставляем режим ожидания активным* (пользователь сможет ввести новый код)
    if not film:
        await update.message.reply_text("Фильм с таким кодом не найден 😕")
        # остаёмся в состоянии waiting_code=True (пользователь может ввести новый код без нажатия кнопки)
        context.user_data["waiting_code"] = True
        return

    # Фильм найден — отправляем
    title = film.get("title", "")
    file_id = film.get("file_id")
    url = film.get("url") or film.get("source")
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
        await update.message.reply_text("Ошибка при отправке фильма, попробуй позже.")
        # После ошибки оставим режим ожидания = False (безопасно)
        context.user_data["waiting_code"] = False
        return

    # После успешной отправки — показываем подсказку с кнопкой и выключаем режим ожидания
    await update.message.reply_text(
        "🎬 Чтобы найти другой фильм, нажмите снова кнопку «🔍 Поиск по коду»",
        reply_markup=inline_search_button(),
    )
    context.user_data["waiting_code"] = False

# ========== Обработка видео (для админа: /add или /editm) ==========
async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Только админ может загружать или менять видео
    if update.effective_user.id != ADMIN_ID:
        return

    add_code = context.user_data.get("add_code")
    edit_code = context.user_data.get("edit_code")
    code = add_code or edit_code
    if not code:
        # нет запроса на добавление/изменение — игнорируем
        return

    # Получаем file_id от video или document
    file_id = None
    if update.message.video:
        file_id = update.message.video.file_id
    elif update.message.document and getattr(update.message.document, "mime_type", ""):
        if "video" in update.message.document.mime_type:
            file_id = update.message.document.file_id

    if not file_id:
        await update.message.reply_text("Пожалуйста, отправьте видео (как обычное видео или файл/документ с mime_type video).")
        return

    films = load_films()

    # Если пришло после /add — добавляем новый фильм
    if add_code:
        title = context.user_data.get("add_title", "")
        # защита от гонки: если код внезапно появился — не перезаписываем
        if add_code in films:
            await update.message.reply_text(f"Код {add_code} уже занят — отменяю добавление. Выберите другой код.")
            context.user_data.pop("add_code", None)
            context.user_data.pop("add_title", None)
            return
        films[add_code] = {"title": title, "file_id": file_id}
        save_films(films)
        await update.message.reply_text(f"Фильм '{title}' с кодом {add_code} добавлен ✅")
        context.user_data.pop("add_code", None)
        context.user_data.pop("add_title", None)
        return

    # Если пришло после /editm — заменяем файл для существующего кода
    if edit_code:
        if edit_code not in films:
            await update.message.reply_text(f"Код {edit_code} не найден в базе. Отмена.")
            context.user_data.pop("edit_code", None)
            return
        films[edit_code]["file_id"] = file_id
        save_films(films)
        await update.message.reply_text(f"Видео для фильма {edit_code} обновлено ✅")
        context.user_data.pop("edit_code", None)
        return

# ========== Точка входа ==========
def main():
    if not TOKEN:
        logger.error("TELEGRAM_TOKEN не задан.")
        return

    app = ApplicationBuilder().token(TOKEN).build()

    # Регистрация обработчиков
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))

    # Админские команды (работают, но не будут показаны в меню)
    app.add_handler(CommandHandler("list", list_films))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("del", del_command))
    app.add_handler(CommandHandler("editn", edit_name))
    app.add_handler(CommandHandler("editm", edit_movie))

    # Основные хендлеры
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))

    # Устанавливаем *в меню* только команду /start (она запускает поиск)
    # Запускаем установку как задачу, чтобы выполнить в event loop приложения
    menu_commands = [BotCommand("start", "Начало и поиск фильма")]
    # schedule coroutine to set commands when the app loop runs
    app.create_task(app.bot.set_my_commands(menu_commands))

    logger.info("Бот запущен.")
    app.run_polling()

if __name__ == "__main__":
    main()