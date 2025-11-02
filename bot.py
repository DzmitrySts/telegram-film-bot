#!/usr/bin/env python3
import os
import logging
import asyncpg
import hashlib
import asyncio
from typing import Optional, Dict, List, Tuple

import httpx

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

# Optional: список JSON-страниц Alloha (через запятую) для поиска imdb/tmdb по названию.
# Пример: "https://.../page-1.json,https://.../page-2.json"
ALLOHA_PAGES_ENV = os.environ.get("ALLOHA_PAGES", "")

# Kodik public API базовый URL (используем token=free как в моих инструкциях)
KODIK_SEARCH_URL = "https://kodikapi.com/search"

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

# ========== Работа с фильмами (DB) ==========
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

# ========== Хелперы для Callback Data ==========
def make_callback(data: str) -> str:
    """Хешируем полезную payload в callback_data, чтобы не превышать лимит длины."""
    h = hashlib.md5(data.encode('utf-8')).hexdigest()
    return f"hd_{h}"

def callback_to_payload_map_store(context, key: str, mapping: dict):
    """Сохраняем в bot_data маппинг cb -> payload (используем уникальный ключ)."""
    # context.bot_data может жить долго, но мы положим в user_data тоже — проще очистка
    context.user_data[key] = mapping

# ========== Кнопка поиска (обновлённая) ==========
async def send_search_button(update, context):
    kb = [
        [InlineKeyboardButton("🔍 Поиск по коду", callback_data="search_code")],
        [InlineKeyboardButton("🎬 Поиск по названию", callback_data="search_name")]
    ]
    # при использовании в callback иногда update.message может быть None — используем безопасно
    if getattr(update, "message", None):
        await update.message.reply_text(
            "Чтобы продолжить, выберите вариант:",
            reply_markup=InlineKeyboardMarkup(kb)
        )
    elif getattr(update, "callback_query", None):
        await update.callback_query.message.reply_text(
            "Чтобы продолжить, выберите вариант:",
            reply_markup=InlineKeyboardMarkup(kb)
        )

# ========== Хендлеры ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pool = context.bot_data["pool"]
    u = update.effective_user
    await add_user(pool, u.id, u.username, u.first_name)

    kb = [
        [InlineKeyboardButton("🔍 Поиск по коду", callback_data="search_code")],
        [InlineKeyboardButton("🎬 Поиск по названию", callback_data="search_name")]
    ]
    await update.message.reply_text(
        "Привет! 👋\nНажми кнопку для поиска фильма по коду или по названию.",
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
        # При обработке видео - используем file_id от Telegram
        await update_film_file(pool, code, update.message.video.file_id)
        context.user_data.clear()
        return await update.message.reply_text("✅ Видео обновлено.")
    if "add_code" in context.user_data:
        code = context.user_data["add_code"]
        title = context.user_data["add_title"]
        await add_film(pool, code, title, update.message.video.file_id)
        context.user_data.clear()
        return await update.message.reply_text("✅ Фильм добавлен.")

# ========== Поиск в Alloha (опционально) ==========
async def find_in_alloha(title: str) -> Optional[Tuple[Optional[str], Optional[int]]]:
    """
    Ищем фильм по названию в наборах Alloha (если переданы через ALLOHA_PAGES env).
    Возвращаем (id_imdb, id_tmdb) или None если не найден.
    Поиск нечувствителен к регистру и ищет в полях 'name' и 'original_name'.
    """
    pages_env = ALLOHA_PAGES_ENV.strip()
    if not pages_env:
        return None
    urls = [u.strip() for u in pages_env.split(",") if u.strip()]
    async with httpx.AsyncClient(timeout=20.0) as client:
        for url in urls:
            try:
                r = await client.get(url)
                if r.status_code != 200:
                    continue
                data = r.json()
                # data может быть список объектов
                for item in data:
                    n = (item.get("name") or "").lower()
                    on = (item.get("original_name") or "").lower()
                    if title.lower() == n or title.lower() == on or title.lower() in n or title.lower() in on:
                        imdb = item.get("id_imdb")
                        tmdb = item.get("id_tmdb")
                        return (imdb, tmdb)
            except Exception as e:
                logger.debug("Alloha page fetch error %s: %s", url, e)
                continue
    return None

# ========== Kodik API интеграция ==========
async def kodik_search_by_imdb(imdb_id: str) -> Optional[Dict[str, List[str]]]:
    """
    Делает запрос к Kodik (public) по imdb_id и возвращает dict: {quality: [mp4_urls...]},
    или None если ничего не найдено.
    Использует token=free.
    """
    params = {"imdb_id": imdb_id, "token": "free"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            r = await client.get(KODIK_SEARCH_URL, params=params)
            if r.status_code != 200:
                logger.debug("Kodik search status %s: %s", r.status_code, r.text[:200])
                return None
            js = r.json()
            results = js.get("results") or js.get("data") or []
            if not results:
                return None
            # Берём первый релевантный элемент, пробуем найти 'links' с mp4
            for item in results:
                links = item.get("links") or item.get("link") or item.get("sources") or {}
                # links может быть dict quality->url или list
                if isinstance(links, dict) and links:
                    # Нормализуем: values -> list of urls
                    normalized = {}
                    for k, v in links.items():
                        if isinstance(v, list):
                            normalized[k] = v
                        elif isinstance(v, str):
                            normalized[k] = [v]
                    if normalized:
                        return normalized
                # Иногда поле 'link' содержит m3u8: попробуем 'link' и 'links' в item
                if "link" in item and item["link"]:
                    # попытаемся вернуть под названием 'default'
                    return {"default": [item["link"]]}
            return None
        except Exception as e:
            logger.exception("Kodik request failed: %s", e)
            return None

async def kodik_search_by_tmdb(tmdb_id: int) -> Optional[Dict[str, List[str]]]:
    params = {"tmdb_id": tmdb_id, "token": "free"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            r = await client.get(KODIK_SEARCH_URL, params=params)
            if r.status_code != 200:
                logger.debug("Kodik search status %s: %s", r.status_code, r.text[:200])
                return None
            js = r.json()
            results = js.get("results") or js.get("data") or []
            if not results:
                return None
            for item in results:
                links = item.get("links") or item.get("link") or item.get("sources") or {}
                if isinstance(links, dict) and links:
                    normalized = {}
                    for k, v in links.items():
                        if isinstance(v, list):
                            normalized[k] = v
                        elif isinstance(v, str):
                            normalized[k] = [v]
                    if normalized:
                        return normalized
                if "link" in item and item["link"]:
                    return {"default": [item["link"]]}
            return None
        except Exception as e:
            logger.exception("Kodik request failed: %s", e)
            return None

# ========== HdRezka/old flows kept intact ==========
# (всю предыдущую логику поиска по коду мы не трогаем — оставляем как есть)

# ========== Обработка текстовых сообщений (добавлена логика поиска по названию) ==========
async def handle_text(update, context):
    pool = context.bot_data["pool"]
    await add_user(pool, update.effective_user.id, update.effective_user.username, update.effective_user.first_name)

    txt = update.message.text.strip()

    # Если ожидаем код фильма (существующая логика)
    if context.user_data.get("waiting_code"):
        if txt.isdigit() and 3 <= len(txt) <= 5:
            return await send_film_by_code(update, context, txt)
        elif txt.isdigit():
            return await update.message.reply_text("❌ Код должен быть от 3 до 5 цифр!")
        else:
            return await update.message.reply_text("❌ Код должен содержать только цифры!")

    # Если ожидаем поиск по названию
    if context.user_data.get("waiting_name"):
        title = txt
        await update.message.reply_text("🔎 Ищу фильм по названию...")

        # 1) Пробуем найти в Alloha (если настроено)
        found = await find_in_alloha(title)
        imdb_id = None
        tmdb_id = None
        if found:
            imdb_id, tmdb_id = found
            logger.info("Found in Alloha: imdb=%s, tmdb=%s", imdb_id, tmdb_id)

        # 2) Если есть imdb — пробуем Kodik по imdb
        links = None
        if imdb_id:
            links = await kodik_search_by_imdb(imdb_id)
        # 3) если не найдено и есть tmdb — пробуем по tmdb
        if not links and tmdb_id:
            links = await kodik_search_by_tmdb(tmdb_id)

        # 4) если всё ещё нет, пробуем "по названию" как запасной план (Kodik не гарантирует такой метод, но попробуем)
        if not links:
            # Пытаемся вызвать search?q=title (некоторые инстансы поддерживают)
            params = {"q": title, "token": "free"}
            async with httpx.AsyncClient(timeout=20.0) as client:
                try:
                    r = await client.get(KODIK_SEARCH_URL, params=params)
                    if r.status_code == 200:
                        js = r.json()
                        results = js.get("results") or js.get("data") or []
                        for item in results:
                            links_candidate = item.get("links") or item.get("link") or item.get("sources") or {}
                            if isinstance(links_candidate, dict) and links_candidate:
                                normalized = {}
                                for k, v in links_candidate.items():
                                    if isinstance(v, list):
                                        normalized[k] = v
                                    elif isinstance(v, str):
                                        normalized[k] = [v]
                                if normalized:
                                    links = normalized
                                    break
                            if "link" in item and item["link"]:
                                links = {"default": [item["link"]]}
                                break
                except Exception as e:
                    logger.debug("Kodik free text search failed: %s", e)

        if not links:
            await update.message.reply_text("❌ Фильм не найден в базе Kodik/Alloha.")
            context.user_data.pop("waiting_name", None)
            return await send_search_button(update, context)

        # У нас есть links: dict quality -> list(urls)
        # Собираем клавиатуру по качествам (первые 3)
        qualities = list(links.keys())
        if not qualities:
            await update.message.reply_text("❌ Нет доступных ссылок.")
            context.user_data.pop("waiting_name", None)
            return await send_search_button(update, context)

        kb = []
        quality_map = {}
        for q in qualities[:3]:
            cb = make_callback(q)
            kb.append([InlineKeyboardButton(q, callback_data=cb)])
            quality_map[cb] = (q, links[q])  # сохраняем качество и список url'ов

        # Сохраняем в user_data текущий контекст: title и map
        context.user_data["kodik_title"] = title
        context.user_data["kodik_quality_map"] = quality_map

        await update.message.reply_text("Выберите качество:", reply_markup=InlineKeyboardMarkup(kb))
        return

    # Иначе — показываем кнопку поиска
    await send_search_button(update, context)

# ========== Отправка фильма по коду (DB) ==========
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

# ========== Callback (обновлённый) ==========
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
        except Exception:
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

    # Выбор качества (kodik flow)
    if "kodik_quality_map" in context.user_data:
        qm = context.user_data["kodik_quality_map"]
        if data in qm:
            q, urls = qm[data]
            # берем первую рабочую ссылку (mp4 предпочтительно)
            chosen_url = None
            for u in urls:
                # простая проверка: mp4 или m3u8
                if u.endswith(".mp4") or ".mp4" in u:
                    chosen_url = u
                    break
            if not chosen_url:
                chosen_url = urls[0]  # fallback

            title = context.user_data.get("kodik_title", "Фильм")
            try:
                # Отправляем видео по URL — Telegram сам скачает файл (до 2 ГБ у ботов).
                # Используем reply_video на сообщении с колбэком
                await query.message.reply_video(chosen_url, caption=title)
            except Exception as e:
                logger.exception("Ошибка при отправке видео по ссылке: %s", e)
                await query.message.reply_text(f"❌ Ошибка при отправке видео: {e}")
            finally:
                # Очистка состояния
                context.user_data.pop("kodik_quality_map", None)
                context.user_data.pop("kodik_title", None)
                await send_search_button(update, context)
            return

    # Поведение по кнопке "Подписался"
    if data == "subscribed":
        # тут просто подтверждаем и переводим к вводу кода/названия (оставляем выбор)
        context.user_data["waiting_code"] = True
        return await query.message.reply_text("Введите код фильма (3–5 цифр):")

# ========== Error handler ==========
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
