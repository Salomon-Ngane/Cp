import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, HTTPException
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler

import config
from bot.handlers.start_menu import start, user_top, handle_account_menu
from bot.handlers.admin import (
    admin_give, admin_take, admin_stats, admin_sync, 
    admin_sweep, admin_alert, admin_resolve, admin_resolve_session
)
from bot.handlers.tickets_view import user_tickets, user_live
from bot.handlers.creation import handle_creation_callback
from database.sessions import cancel_expired_sessions

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialisation du bot Telegram
telegram_app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("top", user_top))
telegram_app.add_handler(CommandHandler("tickets", user_tickets))
telegram_app.add_handler(CommandHandler("live", user_live))

telegram_app.add_handler(CommandHandler("give", admin_give))
telegram_app.add_handler(CommandHandler("take", admin_take))
telegram_app.add_handler(CommandHandler("stats", admin_stats))
telegram_app.add_handler(CommandHandler("sync", admin_sync))
telegram_app.add_handler(CommandHandler("sweep", admin_sweep))
telegram_app.add_handler(CommandHandler("alert", admin_alert))
telegram_app.add_handler(CommandHandler("resolve", admin_resolve))
telegram_app.add_handler(CommandHandler("resolve_session", admin_resolve_session))

async def global_callback_router(update: Update, context):
    query = update.callback_query
    if query.data == "menu_account":
        await handle_account_menu(query, query.from_user.id)
    else:
        await handle_creation_callback(update, context)

telegram_app.add_handler(CallbackQueryHandler(global_callback_router))

# Configuration du cycle de vie FastAPI
@asynccontextmanager
async def lifespan(app: FastAPI):
    await telegram_app.initialize()
    await telegram_app.start()
    
    # Configuration du webhook (Render external URL)
    render_url = os.getenv("RENDER_EXTERNAL_URL")
    if render_url:
        webhook_url = f"{render_url}/webhook"
        await telegram_app.bot.set_webhook(url=webhook_url)
        logger.info(f"✅ Webhook configuré sur : {webhook_url}")
    else:
        logger.warning("⚠️ RENDER_EXTERNAL_URL non défini. Le webhook n'a pas pu être configuré.")
    
    yield
    
    await telegram_app.stop()
    await telegram_app.shutdown()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def health_check():
    """Satisfait le scan de port de Render."""
    return {"status": "Clashsport Webhook Server is running (Option A)"}

@app.post("/webhook")
async def telegram_webhook(request: Request):
    """Reçoit les updates de Telegram."""
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return Response(status_code=200)

@app.get("/cron/sweep")
async def cron_sweep(token: str = None):
    """Endpoint protégé pour cron-job.org (URL: /cron/sweep?token=VOTRE_SECRET)."""
    # Utilisez une clé basique pour éviter les déclenchements publics non autorisés
    if token != "clashsport_cron_secret_2026":
        raise HTTPException(status_code=401, detail="Unauthorized")
    cancel_expired_sessions()
    return {"status": "success", "message": "Sessions expirées nettoyées."}
