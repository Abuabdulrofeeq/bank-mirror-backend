import os
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes

telegram_app = None

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
    
    # Initialize the application so bot.send_message works, but do NOT start polling!
    # This prevents the 'Conflict: terminated by other getUpdates request' error
    # allowing both local and Render servers to run simultaneously in send-only mode.
    try:
        await telegram_app.initialize()
        await telegram_app.start()
        print("Telegram Bot initialized in Send-Only mode (Polling disabled to prevent conflicts).")
    except Exception as e:
        print(f"Failed to initialize Telegram Bot: {e}")

async def stop_telegram_bot():
    global telegram_app
    if telegram_app:
        try:
            await telegram_app.stop()
            await telegram_app.shutdown()
        except Exception:
            pass
        print("Telegram Bot shutdown complete.")
