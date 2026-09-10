import logging
from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler

import config
from bot.handlers.start_menu import start, user_top, handle_account_menu
from bot.handlers.admin import admin_give, admin_take, admin_stats, admin_sync, admin_sweep, admin_alert, admin_resolve, admin_resolve_session
from bot.handlers.tickets_view import user_tickets, user_live
from bot.handlers.creation import handle_creation_callback

# Configuration des logs
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Initialisation de l'application Telegram Bot
telegram_app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).read_timeout(30).write_timeout(30).build()

# Enregistrement des commandes
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("top", user_top))
telegram_app.add_handler(CommandHandler("tickets", user_tickets))
telegram_app.add_handler(CommandHandler("live", user_live))

# Commandes Admin
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

@app.on_event("startup")
async def startup_event():
    await telegram_app.initialize()
    # Configuration du Webhook Telegram vers Render
    webhook_url = f"https://{os_env_render_url()}/webhook" if os_env_render_url() else None
    if webhook_url:
        await telegram_app.bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook configuré sur : {webhook_url}")
    else:
        logger.warning("Aucune URL de webhook détectée, démarrage en mode polling ou attente.")
    await telegram_app.start()

def os_env_render_url():
    import os
    # Render fournit généralement l'URL externe ou vous pouvez l'ajuster
    return os.getenv("RENDER_EXTERNAL_URL", "").replace("https://", "").replace("http://", "")

@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return Response(status_code=200)

@app.get("/")
async def health_check():
    return {"status": "Clashsport Bot is running securely (Modular Architecture v0.2)"}
