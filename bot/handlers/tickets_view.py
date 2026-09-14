import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from services.session_service import (
    get_user_sessions,
    get_session,
    get_tickets_for_session,
    get_matches_by_ids,
    get_estimated_end_time,
)
from services.user_service import get_user_by_id
from bot.ui import main_menu_keyboard

logger = logging.getLogger(__name__)


async def user_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /tickets : affiche les tickets de l'utilisateur."""
    user_id = update.effective_user.id

    try:
        sessions = get_user_sessions(user_id)
    except Exception:
        logger.exception("Erreur lors de la récupération des tickets pour %s", user_id)
        await update.message.reply_text(
            "⚠️ Impossible de charger tes tickets pour le moment.",
            reply_markup=main_menu_keyboard(),
        )
        return

    if not sessions:
        await update.message.reply_text(
            "📭 Vous n'avez aucun ticket pour l'instant.",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text(
        "📋 **Tes Tickets Clashsport**",
        reply_markup=_tickets_keyboard(sessions),
        parse_mode="Markdown",
    )


async def user_live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /live : affiche les sessions actives de l'utilisateur."""
    user_id = update.effective_user.id

    try:
        sessions = get_user_sessions(user_id, history_limit=0)
        sessions = [
            session for session in sessions
            if session.get("status") in ("WAITING", "IN_PROGRESS")
        ]
    except Exception:
        logger.exception(
            "Erreur lors de la récupération des matchs en direct pour %s",
            user_id,
        )
        await update.message.reply_text(
            "⚠️ Impossible de charger les matchs en direct pour le moment.",
            reply_markup=main_menu_keyboard(),
        )
        return

    if not sessions:
        await update.message.reply_text(
            "📭 Aucun duel en cours à suivre.",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )
        return

    keyboard = []
    for session in sessions:
        session_type = "🥊 Duel" if session.get("type") == "DUEL" else "🏟️ Arena"
        status = session.get("status")
        status_text = "⏳ En attente" if status == "WAITING" else "🔴 En cours"
        fee = session.get("gross_entry_fee", 0)

        keyboard.append([
            InlineKeyboardButton(
                f"{status_text} — {session_type} — {fee} Coins",
                callback_data=f"ticket_{session['id']}_mine",
            )
        ])

    keyboard.append([
        InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")
    ])

    await update.message.reply_text(
        "🔴 **Mes matchs en direct**\n\n"
        "Sélectionne un ticket pour suivre son évolution.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


def _tickets_keyboard(sessions):
    keyboard = []

    for session in sessions:
        status = session.get("status")
        status_icon = (
            "⏳" if status == "WAITING"
            else "🔴" if status == "IN_PROGRESS"
            else "✅" if status == "COMPLETED"
            else "❌"
        )
        session_type = "Duel" if session.get("type") == "DUEL" else "Arena"
        fee = session.get("gross_entry_fee", 0)

        keyboard.append([
            InlineKeyboardButton(
                f"{status_icon} {session_type} - {fee} Coins",
                callback_data=f"ticket_{session['id']}_mine",
            )
        ])

    keyboard.append([
        InlineKeyboardButton("⬅️ Retour", callback_data="menu_main")
    ])
    return InlineKeyboardMarkup(keyboard)


async def _show_ticket_error(query, message):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Retour aux tickets", callback_data="my_tickets")],
        [InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")],
    ])
    try:
        await query.edit_message_text(
            f"⚠️ {message}",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
    except Exception:
        logger.exception("Impossible d'afficher l'erreur ticket")


async def show_ticket_detail(query, session_id, tab="mine"):
    """Affiche le détail complet d'un ticket.

    Cette fonction n'appelle volontairement pas query.answer().
    creation.py acquitte le callback une seule fois avant de l'appeler.
    """
    user_id = query.from_user.id

    try:
        session = get_session(session_id)
    except Exception:
        logger.exception("Erreur lors de la récupération de la session %s", session_id)
        await _show_ticket_error(query, "Impossible de charger ce ticket.")
        return

    if not session:
        await _show_ticket_error(query, "Ticket introuvable.")
        return

    created_at_str = session.get("created_at")
    if created_at_str:
        try:
            created_dt = datetime.fromisoformat(
                str(created_at_str).replace("Z", "+00:00")
            )
            date_creation = created_dt.strftime("%d/%m/%Y à %H:%M")
        except (ValueError, TypeError):
            date_creation = "Inconnue"
    else:
        date_creation = "Inconnue"

    try:
        all_tickets = get_tickets_for_session(session_id)
    except Exception:
        logger.exception(
            "Erreur lors de la récupération des tickets de la session %s",
            session_id,
        )
        await _show_ticket_error(query, "Impossible de charger les tickets.")
        return

    all_match_ids = set()
    for ticket in all_tickets:
        for prediction in ticket.get("predictions", []):
            match_id = prediction.get("match_id")
            if match_id is not None:
                all_match_ids.add(str(match_id))

    try:
        all_matches = get_matches_by_ids(list(all_match_ids))
    except Exception:
        logger.exception(
            "Erreur lors de la récupération des matchs de la session %s",
            session_id,
        )
        all_matches = []

    try:
        end_dt = get_estimated_end_time(all_matches)
        date_fin = end_dt.strftime("%d/%m/%Y à %H:%M")
    except Exception:
        logger.exception(
            "Erreur lors du calcul de la fin estimée de la session %s",
            session_id,
        )
        date_fin = "Inconnue"

    session_type = session.get("type", "N/A")
    gross_fee = session.get("gross_entry_fee", 0)
    session_status = session.get("status", "N/A")

    text = (
        "🎫 **Détail du Ticket**\n\n"
        f"📅 **Créé le :** `{date_creation}`\n"
        f"🏁 **Fin estimée :** `{date_fin}`\n"
        f"🏷️ **Type :** `{session_type}`\n"
        f"💰 **Mise :** `{gross_fee} Coins`\n"
        f"📊 **Statut :** `{session_status}`\n\n"
    )

    match_dict = {}
    for match in all_matches:
        match_id = match.get("api_match_id")
        if match_id is not None:
            match_dict[str(match_id)] = match

    my_ticket = next(
        (
            ticket for ticket in all_tickets
            if str(ticket.get("user_id")) == str(user_id)
        ),
        None,
    )

    if tab == "mine":
        if not my_ticket:
            text += "📭 **Aucun pronostic trouvé pour ce ticket.**"
        else:
            text += "🎯 **Mes Pronostics :**\n\n"
            for prediction in my_ticket.get("predictions", []):
                match = match_dict.get(str(prediction.get("match_id")), {})
                home = match.get("home_team", "Équipe A")
                away = match.get("away_team", "Équipe B")
                pick = prediction.get("pick")
                pick_str = "1" if pick == "HOME" else "N" if pick == "DRAW" else "2"
                prediction_status = prediction.get("status", "PENDING")
                status_emoji = (
                    "⏳" if prediction_status == "PENDING"
                    else "✅" if prediction_status == "WON"
                    else "❌"
                )
                odds = prediction.get("odds", 1.0)

                text += (
                    f"🔹 {home} vs {away}\n"
                    f"👉 {status_emoji} **{pick_str}** "
                    f"(Cote: {odds})\n\n"
                )

    elif tab == "opponents":
        opponents = [
            ticket for ticket in all_tickets
            if str(ticket.get("user_id")) != str(user_id)
        ]

        if not opponents:
            text += "⏳ **En attente d'adversaires...**"
        else:
            text += "👥 **Adversaires :**\n\n"
            for ticket in opponents:
                opponent_id = ticket.get("user_id")
                try:
                    opponent = get_user_by_id(opponent_id)
                except Exception:
                    logger.exception(
                        "Erreur lors de la récupération du joueur %s",
                        opponent_id,
                    )
                    opponent = None

                username = (
                    opponent.get("username", "Joueur")
                    if opponent else "Joueur"
                )
                text += f"👤 **{username}**\n"

                for prediction in ticket.get("predictions", []):
                    match = match_dict.get(str(prediction.get("match_id")), {})
                    home = match.get("home_team", "A")
                    away = match.get("away_team", "B")
                    pick = prediction.get("pick")
                    pick_str = "1" if pick == "HOME" else "N" if pick == "DRAW" else "2"
                    text += (
                        f"  • {home[:10]} - {away[:10]} "
                        f"👉 **{pick_str}**\n"
                    )
                text += "\n"

    keyboard = []
    if tab == "mine":
        keyboard.append([
            InlineKeyboardButton(
                "👥 Voir les adversaires",
                callback_data=f"ticket_{session_id}_opponents",
            )
        ])
    else:
        keyboard.append([
            InlineKeyboardButton(
                "🎯 Voir mes pronostics",
                callback_data=f"ticket_{session_id}_mine",
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "⬅️ Retour aux tickets",
            callback_data="my_tickets",
        )
    ])

    try:
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
    except Exception:
        logger.exception("Erreur lors de l'affichage du ticket %s", session_id)
        await _show_ticket_error(query, "Impossible d'afficher ce ticket.")

