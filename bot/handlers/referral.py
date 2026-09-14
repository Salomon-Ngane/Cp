import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from services.user_service import get_user_by_id, get_user_grade
from services.session_service import get_user_sessions, get_tickets_for_session

logger = logging.getLogger(__name__)


async def user_referral_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche le tableau de bord de parrainage."""
    query = update.callback_query

    if query:
        try:
            await query.answer()
        except Exception:
            pass

    user_id = update.effective_user.id
    user = get_user_by_id(user_id)

    if not user:
        text = "⚠️ Impossible de retrouver votre compte."
        if query:
            await query.edit_message_text(text)
        elif update.message:
            await update.message.reply_text(text)
        return

    active_refs = int(user.get("active_referrals_count") or 0)
    grade_info = get_user_grade(active_refs)
    user_code = user.get("user_code", "N/A")
    item_boosts = int(user.get("item_1_count") or 0)

    don_account = get_user_by_id(0)
    don_balance = int(don_account.get("coins_balance") or 0) if don_account else 0

    try:
        bot_username = (await context.bot.get_me()).username or ""
    except Exception:
        logger.exception("Impossible de récupérer le username du bot.")
        bot_username = ""

    ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    unlocked_level = int(grade_info.get("level", 1))

    text = (
        "👥 **MON RÉSEAU & PARRAINAGE**\n\n"
        f"👑 **Votre Grade :** `{grade_info.get('name', 'Recrue 🥉')}`\n"
        f"🔓 **Niveaux débloqués :** `{unlocked_level}/5`\n"
        f"👥 **Filleuls actifs :** `{active_refs}`\n"
        f"🎁 **Boosts Cote (+0.5) disponibles :** `{item_boosts}`\n\n"
        f"❤️ **Fonds Solidaire (Don ❤️) :** `{don_balance}` Coins cumulés par la communauté\n\n"
        f"🆔 **Code Joueur :** `{user_code}`\n\n"
        f"🔗 **Votre lien de parrainage unique :**\n`{ref_link}`\n\n"
        "ℹ️ *Partagez votre lien pour grimper les grades et débloquer "
        "jusqu'à 5 niveaux de commissions sur le rake de vos filleuls !*"
    )

    share_url = (
        "https://t.me/share/url"
        f"?url={ref_link}"
        "&text=Rejoins-moi%20sur%20Clashsport%20et%20défie-moi%20en%20duel%20!"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Partager mon lien", url=share_url)],
        [InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")],
    ])

    if query:
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def user_my_ids(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /myids : affiche les codes des tickets actifs."""
    user_id = update.effective_user.id
    sessions = get_user_sessions(user_id, history_limit=20)

    active_sessions = [
        session for session in sessions
        if session.get("status") in ("WAITING", "IN_PROGRESS")
    ]

    if not active_sessions:
        await update.message.reply_text(
            "📭 Aucun ticket actif en cours nécessitant un ID de réclamation.",
            parse_mode="Markdown",
        )
        return

    text = (
        "🔍 **VOS CODES DE RÉCLAMATION (TICKETS ACTIFS)**\n\n"
        "En cas de litige ou de problème sur un match, "
        "fournissez le code de session à l'administrateur :\n\n"
    )

    for session in active_sessions:
        stype = "🥊 Duel" if session.get("type") == "DUEL" else "🏟️ Arena"
        code = session.get("session_code", "N/A")
        fee = session.get("gross_entry_fee", 0)

        text += f"🔹 **{stype} ({fee} Coins)**\n👉 Code Session : `{code}`\n"

        tickets = get_tickets_for_session(session["id"])
        my_ticket = next(
            (
                ticket for ticket in tickets
                if str(ticket.get("user_id")) == str(user_id)
            ),
            None,
        )

        if my_ticket:
            match_ids = [
                str(prediction.get("match_id"))
                for prediction in my_ticket.get("predictions", [])
                if prediction.get("match_id") is not None
            ]
            if match_ids:
                text += f"   Match IDs : `{', '.join(match_ids)}`\n"

        text += "\n"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")]
    ])

    await update.message.reply_text(
        text,
        reply_markup=keyboard,
        parse_mode="Markdown",
    )








