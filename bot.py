import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler
import config
import database

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    referred_by = int(args[0]) if args and args[0].isdigit() else None
    
    # Enregistrement / Récupération en BDD
    db_user = database.get_or_create_user(user.id, user.username or user.first_name, referred_by)
    
    text = (
        f"👋 Welcome **{user.first_name}** dans le Bot Duel Sports !\n\n"
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
    """Commande Admin pour créditer des coins : /addcoins <telegram_id> <montant>"""
    if update.effective_user.id != config.ADMIN_TELEGRAM_ID:
        return
    
    try:
        target_id = int(context.args[0])
        amount = float(context.args[1])
        
        target_user = database.get_or_create_user(target_id, "")
        new_balance = target_user["coins_balance"] + amount
        database.supabase.table("users").update({"coins_balance": new_balance}).eq("telegram_id", target_id).execute()
        
        await update.message.reply_text(f"✅ {amount} Coins ajoutés au compte {target_id}. Nouveau solde : {new_balance}")
    except Exception as e:
        await update.message.reply_text("❌ Usage: /addcoins <telegram_id> <montant>")

def main():
    app = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addcoins", admin_add_coins))
    
    print("🤖 Bot démarré avec succès...")
    app.run_polling()

if __name__ == "__main__":
    main()
