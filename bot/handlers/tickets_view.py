import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from services.session_service import (
    get_user_sessions,
    get_session,
    get_tickets_for_session,
    get_matches_by_ids,
    get_estimated_end_time
)
from services.user_service import get_user_by_id
from bot.ui import main_menu_keyboard

logger = logging.getLogger(__name__)

async def user_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /tickets : Affiche la liste des sessions de l'utilisateur."""
    user_id = update.effective_user.id
    try:
        sessions = get_user_sessions(user_id)
    except Exception:
        logger.exception("Erreur lors de la récupération des tickets")
        await update.message.reply_text(
            "⚠️ Impossible de charger tes tickets pour le moment.",
            reply_markup=main_menu_keyboard()
        )
        return

    if not sessions:
        await update.message.reply_text(
            "📭 Vous n'avez aucun ticket pour l'instant.",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text(
        "📋 **Tes Tickets Clashsport**",
        reply_markup=_tickets_keyboard(sessions),
        parse_mode="Markdown"
    )

async def user_live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /live : Affiche les sessions actives de l'utilisateur."""
    user_id = update.effective_user.id

    try:
        sessions = get_user_sessions(user_id, history_limit=0)
        sessions = [
            s for s in sessions
            if s.get("status") in ("WAITING", "IN_PROGRESS")
        ]
    except Exception:
        logger.exception("Erreur lors de la récupération des matchs en direct")
        await update.message.reply_text(
            "⚠️ Impossible de charger les matchs en direct pour le moment.",
            reply_markup=main_menu_keyboard()
        )
        return

    if not sessions:
        await update.message.reply_text(
            "📭 Aucun duel en cours à suivre.",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown"
        )
        return

    keyboard = []
    for session in sessions:
        stype = "🥊 Duel" if session.get("type") == "DUEL" else "🏟️ Arena"
        status = "⏳ En attente" if session.get("status") == "WAITING" else "🔴 En cours"
        label = f"{status} — {stype} — {session.get('gross_entry_fee', 0)} Coins"
        keyboard.append([
            InlineKeyboardButton(
                label,
                callback_data=f"ticket_{session['id']}_mine"
            )
        ])

    keyboard.append([
        InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")
    ])

    await update.message.reply_text(
        "🔴 **Mes matchs en direct**

Sélectionne un ticket pour suivre son évolution.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

def _tickets_keyboard(sessions):
    """Génère les boutons de la liste des tickets."""
    keyboard = []

    for s in sessions:
        status = s.get("status")
        status_icon = (
            "⏳" if status == "WAITING"
            else "🔴" if status == "IN_PROGRESS"
            else "✅" if status == "COMPLETED"
            else "❌"
        )
        stype = "Duel" if s.get("type") == "DUEL" else "Arena"
        label = f"{status_icon} {stype} - {s.get('gross_entry_fee', 0)} Coins"

        keyboard.append([
            InlineKeyboardButton(
                label,
                callback_data=f"ticket_{s['id']}_mine"
            )
        ])

    keyboard.append([
        InlineKeyboardButton("⬅️ Retour", callback_data="menu_main")
    ])

    return InlineKeyboardMarkup(keyboard)

async def show_ticket_detail(query, session_id, tab="mine"):
    """Affiche le détail complet d'un ticket."""
    user_id = query.from_user.id
    session = get_session(session_id)

    if not session:
        await query.answer("Ticket introuvable.", show_alert=True)
        return

    created_at_str = session.get("created_at")
    if created_at_str:
        try:
            created_dt = datetime.fromisoformat(
                created_at_str.replace("Z", "+00:00")
            )
            date_creation = created_dt.strftime("%d/%m/%Y à %H:%M")
        except ValueError:
            date_creation = "Inconnue"
    else:
        date_creation = "Inconnue"

    all_tickets = get_tickets_for_session(session_id)

    all_match_ids = set()
    for ticket in all_tickets:
        for prediction in ticket.get("predictions", []):
            all_match_ids.add(str(prediction["match_id"]))

    all_matches = get_matches_by_ids(list(all_match_ids))
    end_dt = get_estimated_end_time(all_matches)
    date_fin = end_dt.strftime("%d/%m/%Y à %H:%M")

    text = "🎫 **Détail du Ticket**

"
    text += f"📅 **Créé le :** `{date_creation}`
"
    text += f"🏁 **Fin estimée :** `{date_fin}`
"
    text += f"🏷️ **Type :** `{session.get('type', 'N/A')}`
"
    text += f"💰 **Mise :** `{session.get('gross_entry_fee', 0)} Coins`
"
    text += f"📊 **Statut :** `{session.get('status', 'N/A')}`

"

    match_dict = {
        str(m["api_match_id"]): m for m in all_matches
    }

    my_ticket = next(
        (t for t in all_tickets if t.get("user_id") == user_id),
        None
    )

    if tab == "mine" and my_ticket:
        text += "🎯 **Mes Pronostics :**

"

        for prediction in my_ticket.get("predictions", []):
            match = match_dict.get(
                str(prediction["match_id"]), {}
            )
            home = match.get("home_team", "Équipe A")
            away = match.get("away_team", "Équipe B")

            pick = prediction.get("pick")
            pick_str = (
                "1" if pick == "HOME"
                else "N" if pick == "DRAW"
                else "2"
            )

            status = prediction.get("status", "PENDING")
            status_emoji = (
                "⏳" if status == "PENDING"
                else "✅" if status == "WON"
                else "❌"
            )

            text += f"🔹 {home} vs {away}
"
            text += (
                f"👉 {status_emoji} **{pick_str}** "
                f"(Cote: {prediction.get('odds', 1.0)})

"
            )

    elif tab == "opponents":
        opponents = [
            t for t in all_tickets
            if t.get("user_id") != user_id
        ]

        if not opponents:
            text += "⏳ En attente d'adversaires..."
        else:
            text += "👥 **Adversaires :**

"

            for ticket in opponents:
                opponent = get_user_by_id(ticket["user_id"])
                username = (
                    opponent.get("username", "Joueur")
                    if opponent else "Joueur"
                )

                text += f"👤 **{username}**
"

                for prediction in ticket.get("predictions", []):
                    match = match_dict.get(
                        str(prediction["match_id"]), {}
                    )
                    home = match.get("home_team", "A")
                    away = match.get("away_team", "B")

                    pick = prediction.get("pick")
                    pick_str = (
                        "1" if pick == "HOME"
                        else "N" if pick == "DRAW"
                        else "2"
                    )

                    text += (
                        f"  • {home[:10]} - {away[:10]} "
                        f"👉 **{pick_str}**
"
                    )

                text += "
"

    keyboard = []

    if tab == "mine":
        keyboard.append([
            InlineKeyboardButton(
                "👥 Voir les adversaires",
                callback_data=f"ticket_{session_id}_opponents"
            )
        ])
    else:
        keyboard.append([
            InlineKeyboardButton(
                "🎯 Voir mes pronostics",
                callback_data=f"ticket_{session_id}_mine"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "⬅️ Retour aux tickets",
            callback_data="my_tickets"
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

