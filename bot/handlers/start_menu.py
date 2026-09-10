from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import config
from database.users import get_or_create_user
from database.sessions import cancel_expired_sessions, get_weekly_leaderboard
from bot.ui import main_menu_keyboard

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cancel_expired_sessions()
    user = update.effective_user
    args = context.args

    if args and args[0].startswith("join_"):
        get_or_create_user(user.id, user.username or user.first_name)
        session_id = args[0][len("join_"):]
        from bot.handlers.creation import propose_join_duel
        await propose_join_duel(update.message, user.id, session_id, context)
        return

    db_user = get_or_create_user(user.id, user.username or user.first_name)
    
    try:
        admin_msg = (
            f"👤 **NOUVEL UTILISATEUR INSCRIT**\n\n"
            f"Nom : {db_user['username']}\n"
            f"🆔 Code Joueur : `{db_user['player_code']}`\n"
            f"Telegram ID : `{user.id}`"
        )
        await context.bot.send_message(chat_id=config.ADMIN_TELEGRAM_ID, text=admin_msg, parse_mode="Markdown")
    except Exception: pass
    
    text = (
        f"👋 Bienvenue **{user.first_name}** sur **Clashsport** !\n\n"
        f"💰 **Votre Solde :** `{db_user['coins_balance']}` Coins\n\n"
        "L'arène ultime de pronostics sportifs. Choisissez une option ci-dessous :"
    )
    await update.message.reply_text(text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")

async def user_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    leaderboard = get_weekly_leaderboard()
    if not leaderboard:
        await update.message.reply_text("🏆 **Classement Hebdomadaire**\n\nAucune victoire enregistrée cette semaine.")
        return
    text = "🏆 **CLASSEMENT HEBDOMADAIRE**\n\n"
    for idx, item in enumerate(leaderboard, 1):
        text += f"{idx}. **{item['username']}** — {item['wins']} victoire(s) ({item['coins_won']} Coins)\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def handle_account_menu(query, user_id):
    db_user = get_or_create_user(user_id, query.from_user.username or query.from_user.first_name)
    recharge_link = f"https://t.me/{config.ADMIN_USERNAME}?text=Recharge%20pour%20mon%20ID%20:%20{db_user['player_code']}"
    
    text = (
        f"💳 **Mon Compte — Clashsport**\n\n"
        f"👤 Utilisateur : {db_user['username']}\n"
        f"🆔 Code Joueur (ID) : **`{db_user.get('player_code', 'N/A')}`**\n"
        f"💰 Solde : `{db_user['coins_balance']}` Coins\n\n"
        "Communiquez votre Code Joueur pour vos transactions et recharges."
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Recharger mon compte", url=recharge_link)],
        [InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")]
    ])
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
