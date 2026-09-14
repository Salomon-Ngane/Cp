import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, HTTPException
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters

import config
from bot.handlers.start_menu import start, user_top, handle_account_menu
from bot.handlers.admin import (
    admin_give, admin_take, admin_stats, admin_sync,
    admin_sweep, admin_alert, admin_resolve,
    admin_resolve_session, admin_reward,
)
from bot.handlers.tickets_view import user_tickets, user_live
from bot.handlers.creation import handle_creation_callback, handle_creation_text_input
from bot.handlers.referral import user_referral_menu, user_my_ids
from bot.handlers.shop import shop_menu, handle_shop_buy
from bot.handlers.top import top_command, handle_top_callbacks
from services.session_service import cancel_expired_sessions

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

telegram_app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("top", top_command))
telegram_app.add_handler(CommandHandler("tickets", user_tickets))
telegram_app.add_handler(CommandHandler("live", user_live))
telegram_app.add_handler(CommandHandler("myids", user_my_ids))

telegram_app.add_handler(CommandHandler("give", admin_give))
telegram_app.add_handler(CommandHandler("take", admin_take))
telegram_app.add_handler(CommandHandler("stats", admin_stats))
telegram_app.add_handler(CommandHandler("reward", admin_reward))
telegram_app.add_handler(CommandHandler("sync", admin_sync))
telegram_app.add_handler(CommandHandler("sweep", admin_sweep))
telegram_app.add_handler(CommandHandler("alert", admin_alert))
telegram_app.add_handler(CommandHandler("resolve", admin_resolve))
telegram_app.add_handler(CommandHandler("resolve_session", admin_resolve_session))

telegram_app.add_handler(
    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_creation_text_input)
)

CREATION_CALLBACK_PREFIXES = (
    "create_duel", "create_arena", "menu_main", "menu_duel", "type_",
    "arena_prize_", "fee_", "sport_", "comp_", "back_to_sports",
    "start_join_draft", "bet_", "validate_ticket", "confirm_ticket",
    "list_public", "list_pubtype_", "join_pub_", "ticket_",
    "my_tickets", "menu_tickets", "live_all", "menu_live", "ignore",
)

TOP_CALLBACK_PREFIXES = (
    "topcat_",
    "topshow_",
    "top_back",
)


async def global_callback_router(update: Update, context):
    query = update.callback_query
    if not query:
        return

    data = query.data or ""

    # creation.py acquitte déjà ses callbacks.
    if data.startswith(CREATION_CALLBACK_PREFIXES):
        await handle_creation_callback(update, context)
        return

    # Le bouton « Classements » du menu principal utilise menu_top.
    # Il doit être routé vers top.py, et non vers creation.py.
    if data == "menu_top" or data.startswith(TOP_CALLBACK_PREFIXES):
        await handle_top_callbacks(update, context)
        return

    if data == "menu_account":
        try:
            await query.answer()
        except Exception:
            pass
        await handle_account_menu(query, query.from_user.id)
        return

    if data == "menu_network":
        await user_referral_menu(update, context)
        return

    if data == "menu_shop":
        await shop_menu(update, context)
        return

    if data.startswith("buy_item_"):
        await handle_shop_buy(update, context)
        return

    # Compatibilité avec d'anciens callbacks.
    await handle_creation_callback(update, context)


telegram_app.add_handler(CallbackQueryHandler(global_callback_router))


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    await telegram_app.initialize()
    await telegram_app.start()

    render_url = os.getenv("RENDER_EXTERNAL_URL")
    if render_url:
        webhook_url = f"{render_url.rstrip('/')}/webhook"
        await telegram_app.bot.set_webhook(url=webhook_url)
        logger.info("Webhook configuré sur : %s", webhook_url)
    else:
        logger.warning("RENDER_EXTERNAL_URL non défini.")

    yield

    await telegram_app.stop()
    await telegram_app.shutdown()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def health_check():
    return {"status": "Clashsport Webhook Server OK"}


@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return Response(status_code=200)


@app.get("/cron/sweep")
async def cron_sweep(token: str = None):
    expected_token = os.getenv("CRON_SECRET") or "clashsport_cron_secret_2026"
    if token != expected_token:
        raise HTTPException(status_code=401, detail="Unauthorized")

    cancel_expired_sessions()
    return {"status": "success", "message": "Sessions expirées nettoyées."}
