import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
import uvicorn
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import config
import database

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Initialisation de l'application Telegram Bot
telegram_app = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).build()

# Handler /start
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

# Handler /addcoins (Admin)
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

# Enregistrement des commandes
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("addcoins", admin_add_coins))

# Démarrage sécurisé sans doublon
@asynccontextmanager
async def lifespan(app: FastAPI):
    await telegram_app.initialize()
    await telegram_app.start()
    # On lance le polling en tâche de fond pour ne pas bloquer FastAPI
    polling_task = asyncio.create_task(telegram_app.updater.start_polling(drop_pending_updates=True))
    logging.info("🤖 Bot Telegram démarré avec succès !")
    yield
    # Arrêt propre
    await telegram_app.updater.stop()
    await telegram_app.stop()
    await telegram_app.shutdown()

app = FastAPI(lifespan=lifespan)

@app.get("/")
def health_check():
    return {"status": "ok", "message": "Le Bot Duel Sports est en ligne !"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    # Desactivation du reload pour éviter la double execution
    uvicorn.run("bot:app", host="0.0.0.0", port=port, reload=False)
