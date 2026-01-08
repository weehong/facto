import logging
import sys

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from facto.config import Config
from facto.services.ai_service import AIService
from facto.services.memory import MemoryManager
from facto.bot.handlers import TelegramBotHandlers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    # 1. Load Configuration
    try:
        config = Config.from_env()
        logger.info("Configuration loaded successfully")
    except ValueError as e:
        logger.error(f"Configuration Error: {e}")
        sys.exit(1)

    # 2. Initialize Services
    ai_service = AIService(config)
    memory_manager = MemoryManager(mongodb_uri=config.mongodb_uri)

    # 3. Initialize Bot Handlers
    handlers = TelegramBotHandlers(ai_service, memory_manager)

    # 4. Build Application
    application = Application.builder().token(config.telegram_bot_token).build()

    # 5. Register Handlers
    # Journal command
    application.add_handler(CommandHandler("journal", handlers.journal_command))

    # Topic management
    application.add_handler(CommandHandler("done", handlers.done_command))
    application.add_handler(CommandHandler("delete", handlers.delete_topic))

    # Conversation flow (replies in topics)
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
        handlers.handle_conversation_flow
    ))

    # 6. Start Bot
    logger.info("Facto Journal Bot starting...")
    logger.info("Commands: /journal, /done, /delete")

    application.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
