import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import config
import database

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

telegram_app = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).build()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    referred_by = int(args[0]) if args and args[0].isdigit() else None
    
    db_user = database.get_or_create_user(user.id, user.username or user.first_name, referred_by)
    
    text = (
        f"👋 Bienvenue **{user.first_name}** dans le Bot Duel Sports !\n\n"
        f"💰 **Votre Solde :** {db_user['coins_balance']} Coins\n\n"
        "Affrontez d'autres joueurs sur des grilles de pronostics et raflez la mise !"
    )
    
    keyboard = [
        [InlineKeyboardButton("⚔️ Créer / Rejoindre un Duel", callback_data="menu_duel")],
        [InlineKeyboardButton("🏟️ Mode Arena (Top 3)", callback_data="menu_arena")],
        [InlineKeyboardButton("🏆 Ligue Quotidienne", callback_data="menu_ligue")],
        [InlineKeyboardButton("💳 Mon Compte / Retrait", callback_data="menu_account")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

async def admin_add_coins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != config.ADMIN_TELEGRAM_ID:
        return
    try:
        target_id = int(context.args[0])
        amount = float(context.args[1])
        target_user = database.get_or_create_user(target_id, "")
        new_balance = target_user["coins_balance"] + amount
        database.supabase.table("users").update({"coins_balance": new_balance}).eq("telegram_id", target_id).execute()
        await update.message.reply_text(f"✅ {amount} Coins ajoutés au compte {target_id}. Nouveau solde : {new_balance}")
    except Exception:
        await update.message.reply_text("❌ Usage: /addcoins <telegram_id> <montant>")

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("addcoins", admin_add_coins))

# URL publique fournie par Render (ex: https://mon-bot.onrender.com)
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")

@asynccontextmanager
async def lifespan(app: FastAPI):
    await telegram_app.initialize()
    await telegram_app.start()
    
    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
        await telegram_app.bot.set_webhook(url=webhook_url, drop_pending_updates=True)
        logging.info(f"🔗 Webhook configuré sur : {webhook_url}")
    yield
    await telegram_app.stop()
    await telegram_app.shutdown()

app = FastAPI(lifespan=lifespan)

@app.post("/webhook")
async def process_webhook(request: Request):
    req_data = await request.json()
    update = Update.de_json(req_data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"status": "ok"}

@app.get("/")
def health_check():
    return {"status": "ok", "message": "Le Bot Duel Sports est en ligne !"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    import uvicorn
    uvicorn.run("bot:app", host="0.0.0.0", port=port)
