import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

telegram_app = None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = "Welcome to Bank Mirror Bot! 🏦\nI will instantly notify you of all transactions."
    await update.message.reply_text(welcome_text)

async def notify_channel(message: str):
    global telegram_app
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if telegram_app and chat_id:
        try:
            await telegram_app.bot.send_message(chat_id=chat_id, text=message)
        except Exception as e:
            print(f"Failed to send Telegram alert: {e}")

async def start_telegram_bot():
    global telegram_app
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("No Telegram Token found in .env")
        return
        
    telegram_app = ApplicationBuilder().token(token).build()
    telegram_app.add_handler(CommandHandler("start", start))
    
    # Initialize and start the application in the existing event loop
    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.updater.start_polling()
    print("Telegram Bot Polling Started!")

async def stop_telegram_bot():
    global telegram_app
    if telegram_app:
        await telegram_app.updater.stop()
        await telegram_app.stop()
        await telegram_app.shutdown()
        print("Telegram Bot Polling Stopped.")
