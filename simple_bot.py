import os
import sys
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes

# 確保日誌實時輸出
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout,
    force=True
)
logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info(f"Start command received from {update.effective_chat.id}")
    keyboard = [
        [InlineKeyboardButton("按鈕 1", callback_data="btn1")],
        [InlineKeyboardButton("按鈕 2", callback_data="btn2")],
        [InlineKeyboardButton("按鈕 3", callback_data="btn3")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("選擇一個按鈕：", reply_markup=reply_markup)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    logger.info(f"Button pressed: {query.data}")
    await query.answer()
    await query.edit_message_text(text=f"你點擊了：{query.data}")

def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")
    
    logger.info("=" * 50)
    logger.info("Starting Telegram Bot")
    logger.info(f"Token: {token[:20]}...")
    logger.info("=" * 50)
    
    try:
        application = ApplicationBuilder().token(token).build()
        logger.info("Application created")
        
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CallbackQueryHandler(button_callback))
        logger.info("Handlers registered")
        
        logger.info("Starting polling...")
        sys.stdout.flush()
        sys.stderr.flush()
        
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    main()
