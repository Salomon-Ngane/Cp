import logging
import config
from telegram.ext import Application, CommandHandler, CallbackQueryHandler
from telegram import Update

from bot.handlers.start_menu import start, user_top, handle_account_menu
from bot.handlers.admin import (
    admin_give, admin_take, admin_stats, admin_sync, 
    admin_sweep, admin_alert, admin_resolve, admin_resolve_session
)
from bot.handlers.tickets_view import user_tickets, user_live
from bot.handlers.creation import handle_creation_callback

# Configuration des logs
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    # Initialisation de l'application Telegram Bot en mode Polling
    telegram_app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).read_timeout(30).write_timeout(30).build()

    # Enregistrement des commandes utilisateurs
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("top", user_top))
    telegram_app.add_handler(CommandHandler("tickets", user_tickets))
    telegram_app.add_handler(CommandHandler("live", user_live))

    # Commandes Administrateur
    telegram_app.add_handler(CommandHandler("give", admin_give))
    telegram_app.add_handler(CommandHandler("take", admin_take))
    telegram_app.add_handler(CommandHandler("stats", admin_stats))
    telegram_app.add_handler(CommandHandler("sync", admin_sync))
    telegram_app.add_handler(CommandHandler("sweep", admin_sweep))
    telegram_app.add_handler(CommandHandler("alert", admin_alert))
    telegram_app.add_handler(CommandHandler("resolve", admin_resolve))
    telegram_app.add_handler(CommandHandler("resolve_session", admin_resolve_session))

    # Routeur global pour tous les boutons tactiles (Callbacks)
    async def global_callback_router(update: Update, context):
        query = update.callback_query
        data = query.data
        if data == "menu_account":
            await handle_account_menu(query, query.from_user.id)
        else:
            await handle_creation_callback(update, context)

    telegram_app.add_handler(CallbackQueryHandler(global_callback_router))

    logger.info("🤖 Démarrage du bot Clashsport en mode Polling continu...")
    telegram_app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
