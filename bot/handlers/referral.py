import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from services.user_service import get_user_by_id, get_user_grade
from services.session_service import get_user_sessions, get_tickets_for_session, get_matches_by_ids

from bot.ui import main_menu_keyboard

logger = logging.getLogger(__name__)

async def user_referral_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche le tableau de bord de parrainage, les grades et les statistiques du réseau."""
    user_id = update.effective_user.id
    user = get_user_by_id(user_id)
    
    if not user:
        return

    active_refs = user.get("active_referrals_count", 0)
    grade_info = get_user_grade(active_refs)
    user_code = user.get("user_code", "N/A")
item_boosts = user.get("item_1_count", 0)

    # Récupération du solde Don ❤️ Solidaire (ID 0)
    don_account = get_user_by_id(0)
    don_balance = don_account.get("coins_balance", 0) if don_account else 0

    bot_username = (await context.bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}"

    text = (
        f"👥 **MON RÉSEAU & PARRAINAGE**\n\n"
        f"👑 **Votre Grade :** `{grade_info['name']}`\n"
        f"🔓 **Niveaux débloqués :** `{grade_info['unlocked_levels']}/5`\n"
        f"👥 **Filleuls actifs :** `{active_refs}`\n"
        f"🎁 **Boosts Cote (+0.5) disponibles :** `{item_boosts}`\n\n"
        f"❤️ **Fonds Solidaire (Don ❤️) :** `{don_balance}` Coins cumulés par la communauté\n\n"
        f"🔗 **Votre lien de parrainage unique :**\n`{ref_link}`\n\n"
        f"ℹ️ *Partagez votre lien pour grimper les grades et débloquer jusqu'à 5 niveaux de commissions sur le rake de vos filleuls !*"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Partager mon lien", url=f"https://t.me/share/url?url={ref_link}&text=Rejoins-moi%20sur%20Clashsport%20et%20défie-moi%20en%20duel%20!")],
        [InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")]
    ])

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")

async def user_my_ids(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /myids : Permet à l'utilisateur d'obtenir les codes à 7 caractères de ses tickets actifs pour réclamation."""
    user_id = update.effective_user.id
    sessions = get_user_sessions(user_id, history_limit=20)
    active_sessions = [s for s in sessions if s["status"] in ("WAITING", "IN_PROGRESS")]

    if not active_sessions:
        await update.message.reply_text("📭 Aucun ticket actif en cours nécessitant un ID de réclamation.", parse_mode="Markdown")
        return

    text = "🔍 **VOS CODES DE RÉCLAMATION (TICKETS ACTIFS)**\n\n"
    text += "En cas de litige ou de problème sur un match, fournissez le code de session à l'administrateur :\n\n"

    for s in active_sessions:
        stype = "🥊 Duel" if s["type"] == "DUEL" else "🏟️ Arena"
        scode = s.get("session_code", "N/A")
        text += f"🔹 **{stype} ({s['gross_entry_fee']} Coins)**\n"
        text += f"👉 Code Session : `{scode}`\n"
        
        # Liste des Match IDs associés
        tickets = get_tickets_for_session(s["id"])
        my_t = next((t for t in tickets if t["user_id"] == user_id), None)
        if my_t:
            match_ids = [str(p["match_id"]) for p in my_t.get("predictions", [])]
            text += f"   Match IDs : `{', '.join(match_ids)}`\n\n"

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")]])
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
