import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes
)

import config
from database.users import create_user_if_not_exists, get_user_by_id
from database.sessions import get_top_leaderboard
from bot.ui import main_menu_keyboard
from bot.handlers.creation import handle_creation_callback, handle_creation_text_input, propose_join_duel
from bot.handlers.tickets_view import user_tickets, user_live, show_ticket_detail
from bot.handlers.referral import user_referral_menu, user_my_ids
from bot.handlers.admin import (
    admin_give,
    admin_take,
    admin_reward,
    admin_sweep,
    admin_stats,
    admin_resolve_session,
    admin_alert
)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère la commande /start avec support des liens profonds (Parrainage & Invitation Salon)."""
    telegram_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
    
    args = context.args
    referrer_id = None
    join_session_id = None

    if args:
        param = args[0]
        if param.startswith("ref_"):
            try:
                referrer_id = int(param.split("_")[1])
            except ValueError:
                pass
        elif param.startswith("join_"):
            join_session_id = param.split("_", 1)[1]

    # Création du compte si nouveau + notification ADMIN UNIQUE
    user, is_new = create_user_if_not_exists(telegram_id, username, referrer_id)
    
    if is_new:
        try:
            await context.bot.send_message(
                chat_id=config.ADMIN_TELEGRAM_ID,
                text=f"🆕 **NOUVEAU JOUEUR INSCRIT !**\n\n👤 Nom : {username}\n🆔 ID : `{telegram_id}`\n🎫 Code : `{user['user_code']}`",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Erreur lors de la notification admin pour le nouvel inscrit : {e}")

    # Si l'utilisateur rejoint via un lien d'invitation de salon
    if join_session_id:
        await propose_join_duel(update.message, telegram_id, join_session_id, context)
        return

    welcome_text = (
        f"🏆 **Bienvenue sur Clashsport, {username} !**\n\n"
        f"Défiez d'autres parieurs en Duels 1v1 ou en Arenas multi-joueurs, "
        f"grimpez le classement et débloquez jusqu'à 5 niveaux de commissions de parrainage !\n\n"
        f"💰 Solde actuel : `{user['coins_balance']}` Coins\n"
        f"🔑 Votre Code Joueur : `{user['user_code']}`"
    )
    
    await update.message.reply_text(welcome_text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")

async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /top : Affiche le classement des 10 meilleurs pronostiqueurs."""
    top_players = get_top_leaderboard(limit=10)
    
    if not top_players:
        await update.message.reply_text("📭 Aucun joueur classé pour le moment.", parse_mode="Markdown")
        return

    text = "🏆 **CLASSEMENT GÉNÉRAL (TOP 10)**\n\n"
    text += "Le classement est calculé sur le taux de réussite (%) des pronostics :\n\n"

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    
    for idx, p in enumerate(top_players):
        medal = medals[idx] if idx < len(medals) else f"#{idx+1}"
        text += f"{medal} **{p['username']}** (`{p['user_code']}`)\n"
        text += f"   👉 Taux de réussite : `{p['success_rate']}%` ({p['wins']}/{p['total']} victoires)\n\n"

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")]])
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Routage principal des callbacks d'inline keyboards."""
    query = update.callback_query
    data = query.data

    if data == "menu_network":
        await user_referral_menu(update, context)
        await query.answer()
    elif data == "menu_top":
        await top_command(update, context)
        await query.answer()
    else:
        await handle_creation_callback(update, context)

def main():
    app = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).build()

    # --- COMMANDES JOUEURS ---
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("top", top_command))
    app.add_handler(CommandHandler("tickets", user_tickets))
    app.add_handler(CommandHandler("live", user_live))
    app.add_handler(CommandHandler("network", user_referral_menu))
    app.add_handler(CommandHandler("myids", user_my_ids))

    # --- COMMANDES ADMINISTRATEUR ---
    app.add_handler(CommandHandler("give", admin_give))
    app.add_handler(CommandHandler("take", admin_take))
    app.add_handler(CommandHandler("reward", admin_reward))
    app.add_handler(CommandHandler("sweep", admin_sweep))
    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(CommandHandler("resolve_session", admin_resolve_session))
    app.add_handler(CommandHandler("alert", admin_alert))

    # --- GESTION DES CALLBACKS ET TEXTES ---
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_creation_text_input))

    logger.info("🤖 Bot Clashsport démarré avec succès !")
    app.run_polling()

if __name__ == "__main__":
    main()
