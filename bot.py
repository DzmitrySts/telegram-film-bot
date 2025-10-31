#!/usr/bin/env python3
import os
import logging
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN")
RAILWAY_DOMAIN = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
WEBHOOK_URL = f"https://{RAILWAY_DOMAIN}/webhook" if RAILWAY_DOMAIN else None

# ======== Хендлеры ========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"/start received from {update.effective_user.id}")
    await update.message.reply_text("Привет! Бот работает через webhook ✅")

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Message received: {update.message.text}")
    await update.message.reply_text(f"Вы написали: {update.message.text}")

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"CallbackQuery: {update.callback_query.data}")
    await update.callback_query.answer("Кнопка нажата!")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Ошибка:", exc_info=context.error)

# ======== Точка входа ========
def main():
    if not TOKEN:
        logger.error("TELEGRAM_TOKEN не задан.")
        return

    app = ApplicationBuilder().token(TOKEN).build()

    # Хендлеры
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    app.add_handler(CallbackQueryHandler(button))
    app.add_error_handler(error_handler)

    if WEBHOOK_URL:
        logger.info(f"🔗 Устанавливаю Webhook: {WEBHOOK_URL}")
        # Запуск webhook
        app.run_webhook(
            listen="0.0.0.0",
            port=int(os.environ.get("PORT", 8080)),
            webhook_url=WEBHOOK_URL
        )
    else:
        logger.info("❗ WEBHOOK_URL не задан, запускаем polling")
        app.run_polling()

if __name__ == "__main__":
    main()
