import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database.cart import clear_draft, replace_cart_from_predictions, update_draft_settings
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

HISTORY_LIMIT = 4


def _session_status_label(session: dict, user_id: int) -> tuple[str, str]:
    """Retourne (emoji, libellé) du ticket vu par son propriétaire."""
    status = session.get("status")

    if status == "WAITING":
        return "⏳", "En attente"
    if status == "IN_PROGRESS":
        return "🔴", "En cours"
    if status == "CANCELLED":
        return "🟡", "Remboursé"

    if status == "COMPLETED":
        # Un Duel terminé sans winner_id correspond au remboursement d'un nul.
        if session.get("type") == "DUEL" and session.get("winner_id") is None:
            return "🟡", "Remboursé"

        if str(session.get("winner_id")) == str(user_id):
            return "🟢", "Gagné"

        # Pour l'Arena, winner_id=None signifie qu'aucun prix n'a été attribué
        # au joueur : selon la règle métier validée, on l'affiche comme perdu.
        return "🔴", "Perdu"

    return "⚪", str(status or "Inconnu")


def _ticket_short_label(session: dict, user_id: int) -> str:
    emoji, label = _session_status_label(session, user_id)
    session_type = "Duel" if session.get("type") == "DUEL" else "Arena"
    fee = session.get("gross_entry_fee", 0)
    return f"{emoji} {session_type} — {fee} Coins — {label}"


async def user_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /tickets : affiche les tickets actifs et les 4 derniers terminés."""
    user_id = update.effective_user.id

    try:
        sessions = get_user_sessions(user_id, history_limit=HISTORY_LIMIT)
    except Exception:
        logger.exception("Erreur lors de la récupération des tickets pour %s", user_id)
        await update.message.reply_text(
            "⚠️ Impossible de charger tes tickets pour le moment.",
            reply_markup=main_menu_keyboard(),
        )
        return

    if not sessions:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Créer un nouveau ticket", callback_data="menu_duel")],
            [InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")],
        ])
        await update.message.reply_text(
            "📭 Vous n'avez aucun ticket pour l'instant.",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text(
        "📋 **Mes Tickets Clashsport**",
        reply_markup=_tickets_keyboard(sessions, user_id),
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


def _tickets_keyboard(sessions, user_id=None):
    """Organise l'écran en tickets actifs puis historique récent."""
    user_id = user_id if user_id is not None else 0
    active = [
        s for s in sessions
        if s.get("status") in ("WAITING", "IN_PROGRESS")
    ]
    history = [
        s for s in sessions
        if s.get("status") in ("COMPLETED", "CANCELLED")
    ][:HISTORY_LIMIT]

    keyboard = []

    if active:
        keyboard.append([
            InlineKeyboardButton("🔴 TICKETS EN COURS", callback_data="ignore")
        ])
        for session in active:
            keyboard.append([
                InlineKeyboardButton(
                    _ticket_short_label(session, user_id),
                    callback_data=f"ticket_{session['id']}_mine",
                )
            ])

    if history:
        keyboard.append([
            InlineKeyboardButton("📜 HISTORIQUE — 4 DERNIERS", callback_data="ignore")
        ])
        for session in history:
            keyboard.append([
                InlineKeyboardButton(
                    _ticket_short_label(session, user_id),
                    callback_data=f"ticket_{session['id']}_mine",
                )
            ])

    keyboard.append([
        InlineKeyboardButton("➕ Créer un nouveau ticket", callback_data="menu_duel")
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


async def replay_ticket_to_cart(query, session_id: str):
    """Reconstruit le panier depuis le ticket du joueur.

    Les matchs non disponibles (statut différent de NS) sont ignorés. Le panier
    existant est remplacé uniquement après validation de la sélection restaurable.
    """
    user_id = query.from_user.id

    try:
        session = get_session(session_id)
        if not session:
            await _show_ticket_error(query, "Ticket introuvable.")
            return

        tickets = get_tickets_for_session(session_id)
        my_ticket = next(
            (
                ticket for ticket in tickets
                if str(ticket.get("user_id")) == str(user_id)
            ),
            None,
        )
        if not my_ticket:
            await _show_ticket_error(query, "Tu n'es pas propriétaire de ce ticket.")
            return

        predictions = my_ticket.get("predictions") or []
        if not predictions:
            await _show_ticket_error(query, "Aucune sélection à rejouer dans ce ticket.")
            return

        match_ids = [str(p.get("match_id")) for p in predictions if p.get("match_id") is not None]
        matches = get_matches_by_ids(match_ids)
        active_by_id = {
            str(match.get("api_match_id")): match
            for match in matches
            if str(match.get("status", "")).upper() == "NS"
        }

        available = [
            p for p in predictions
            if str(p.get("match_id")) in active_by_id
        ]
        unavailable_count = len(predictions) - len(available)

        if not available:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Créer un nouveau ticket", callback_data="menu_duel")],
                [InlineKeyboardButton("⬅️ Retour aux tickets", callback_data="my_tickets")],
            ])
            await query.edit_message_text(
                "⚠️ Aucun des matchs de ce ticket n'est encore disponible.\n\n"
                "Tu peux créer un nouveau ticket avec les matchs actuels.",
                reply_markup=keyboard,
            )
            return

        # Le replay remplace volontairement le panier actuel : on prépare
        # ensuite un nouveau ticket indépendant de l'ancien.
        clear_draft(user_id)
        replace_cart_from_predictions(user_id, available)

        update_draft_settings(
            user_id,
            {
                "session_type": session.get("type", "DUEL"),
                "gross_fee": int(session.get("gross_entry_fee", 0)),
                "max_participants": int(session.get("max_participants", 2)),
                "prize_mode": session.get("prize_mode", "WINNER_TAKES_ALL"),
            },
        )

        warning = ""
        if unavailable_count:
            warning = (
                f"\n\n⚠️ {unavailable_count} match(s) ne sont plus disponibles "
                "et n'ont pas été remis dans le panier."
            )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Voir mon panier", callback_data="validate_ticket")],
            [InlineKeyboardButton("➕ Ajouter / modifier des matchs", callback_data="back_to_sports")],
            [InlineKeyboardButton("⬅️ Retour aux tickets", callback_data="my_tickets")],
        ])

        await query.edit_message_text(
            "🔄 **Sélection restaurée dans ton panier !**\n\n"
            f"⚽ {len(available)} match(s) restauré(s)."
            f"{warning}\n\n"
            "Tu peux modifier tes choix avant de créer un nouveau ticket.",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )

    except Exception:
        logger.exception(
            "Erreur lors de la restauration du ticket %s pour %s",
            session_id,
            user_id,
        )
        await _show_ticket_error(
            query,
            "Impossible de restaurer cette sélection pour le moment.",
        )


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

    # Sécurité : l'utilisateur doit posséder un ticket dans cette session.
    try:
        all_tickets = get_tickets_for_session(session_id)
    except Exception:
        logger.exception(
            "Erreur lors de la récupération des tickets de la session %s",
            session_id,
        )
        await _show_ticket_error(query, "Impossible de charger les tickets.")
        return

    my_ticket = next(
        (
            ticket for ticket in all_tickets
            if str(ticket.get("user_id")) == str(user_id)
        ),
        None,
    )
    if not my_ticket:
        await _show_ticket_error(query, "Tu n'as pas accès à ce ticket.")
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
    status_emoji, status_label = _session_status_label(session, user_id)

    text = (
        "🎫 **Détail du Ticket**\n\n"
        f"{status_emoji} **{status_label}**\n"
        f"📅 **Créé le :** `{date_creation}`\n"
        f"🏁 **Fin estimée :** `{date_fin}`\n"
        f"🏷️ **Type :** `{session_type}`\n"
        f"💰 **Mise :** `{gross_fee} Coins`\n\n"
    )

    match_dict = {}
    for match in all_matches:
        match_id = match.get("api_match_id")
        if match_id is not None:
            match_dict[str(match_id)] = match

    if tab == "mine":
        text += "🎯 **Mes Pronostics :**\n\n"
        for prediction in my_ticket.get("predictions", []):
            match = match_dict.get(str(prediction.get("match_id")), {})
            home = match.get("home_team", "Équipe A")
            away = match.get("away_team", "Équipe B")
            pick = prediction.get("pick")
            pick_str = "1" if pick == "HOME" else "N" if pick == "DRAW" else "2"
            prediction_status = prediction.get("status", "PENDING")
            status_emoji_prediction = (
                "⏳" if prediction_status == "PENDING"
                else "✅" if prediction_status == "WON"
                else "❌"
            )
            odds = prediction.get("odds", 1.0)

            text += (
                f"🔹 {home} vs {away}\n"
                f"👉 {status_emoji_prediction} **{pick_str}** "
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

    # Replay demandé pour les tickets déjà en cours.
    if session.get("status") == "IN_PROGRESS":
        keyboard.append([
            InlineKeyboardButton(
                "🔄 Rejouer cette sélection",
                callback_data=f"ticket_{session_id}_replay",
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "➕ Créer un nouveau ticket",
            callback_data="menu_duel",
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
        await _show_ticket_error(query, "Impossible d'afficher le ticket.")


