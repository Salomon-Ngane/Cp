import logging
from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database.connection import supabase
from database.cart import (
    get_draft_settings,
    update_draft_settings,
    clear_draft,
    toggle_cart_item,
    get_cart,
)
from services.session_service import (
    get_matches_by_sport,
    get_matches_by_ids,
    create_session,
    join_session,
    get_session,
    get_tickets_for_session,
    get_user_sessions,
    cancel_expired_sessions,
)
from services.user_service import get_user_by_id
from bot.ui import main_menu_keyboard
from bot.handlers.tickets_view import show_ticket_detail, replay_ticket_to_cart

logger = logging.getLogger(__name__)


def _safe_float(val, default=0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


async def _show_callback_error(query, message, back_callback="menu_duel"):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Retour", callback_data=back_callback)],
        [InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")],
    ])
    try:
        # Pas de Markdown ici : message peut contenir des caractères Telegram
        # réservés (noms d'équipes, ligues, erreurs SQL, etc.).
        await query.edit_message_text(
            f"⚠️ {message}",
            reply_markup=keyboard,
        )
    except Exception:
        logger.exception("Impossible d'afficher l'erreur callback")


async def handle_creation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    # IMPORTANT : un seul query.answer() ici.
    # Aucun sous-traitement de creation.py/tickets_view.py ne doit
    # rappeler query.answer() pour le même callback.
    try:
        await query.answer()
    except Exception:
        pass

    data = query.data or ""
    user_id = query.from_user.id

    if data == "ignore":
        return

    if data == "menu_main":
        context.user_data.pop("creation_state", None)
        await query.edit_message_text(
            "🏠 **Menu Principal**",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )

    elif data == "menu_duel":
        context.user_data.pop("creation_state", None)
        text = "⚔️ **Mode de jeu**\n\nChoisissez comment vous souhaitez parier :"
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🥊 Duel 1v1", callback_data="type_DUEL"),
                InlineKeyboardButton("🏟️ Mode Arena", callback_data="type_ARENA"),
            ],
            [InlineKeyboardButton("🔍 Rejoindre un salon public", callback_data="list_public")],
            [InlineKeyboardButton("⬅️ Retour", callback_data="menu_main")],
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")

    elif data.startswith("type_"):
        stype = data.split("_", 1)[1]
        if stype not in ("DUEL", "ARENA"):
            await _show_callback_error(query, "Type de salon invalide.")
            return

        update_draft_settings(user_id, {"session_type": stype})

        if stype == "DUEL":
            update_draft_settings(
                user_id,
                {"max_participants": 2, "prize_mode": "WINNER_TAKES_ALL"},
            )
            await _ask_entry_fee(update, user_id, context)
        else:
            context.user_data["creation_state"] = "awaiting_arena_max"
            text = (
                "🏟️ **Configuration Arena**\n\n"
                "Entrez dans le chat le **nombre maximum de joueurs** "
                "pour ce salon (entre 3 et 15) :"
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Retour", callback_data="menu_duel")]
            ])
            await query.edit_message_text(
                text,
                reply_markup=keyboard,
                parse_mode="Markdown",
            )

    elif data.startswith("arena_prize_"):
        mode = "TOP_3" if data.split("_", 2)[2] == "TOP3" else "WINNER_TAKES_ALL"
        update_draft_settings(user_id, {"prize_mode": mode})
        await _ask_entry_fee(update, user_id, context)

    elif data.startswith("fee_"):
        try:
            fee = int(data.split("_", 1)[1])
        except (ValueError, IndexError):
            await _show_callback_error(query, "Montant de mise invalide.")
            return

        if fee <= 0:
            await _show_callback_error(query, "Le montant doit être supérieur à 0.")
            return

        update_draft_settings(user_id, {"gross_fee": fee})
        context.user_data.pop("creation_state", None)
        await _show_sports_selection(update)

    elif data.startswith("sport_"):
        sport = data.split("_", 1)[1]
        await _show_competitions_for_sport(query, sport)

    elif data.startswith("comp_"):
        comp_prefix = data.split("_", 1)[1]
        await _show_matches_for_comp(query, comp_prefix)

    elif data == "back_to_sports":
        await _show_sports_selection(update)

    elif data == "start_join_draft":
        await _show_sports_selection(update)

    elif data.startswith("bet_"):
        parts = data.split("_", 3)
        if len(parts) != 4:
            await _show_callback_error(query, "Pronostic invalide.", "menu_duel")
            return

        _, mid, pick, odds_text = parts
        try:
            odds = float(odds_text)
        except ValueError:
            await _show_callback_error(query, "Cote invalide.", "menu_duel")
            return

        draft = get_draft_settings(user_id)
        joining_sid = draft.get("joining_session_id")

        try:
            if joining_sid:
                session = get_session(joining_sid)
                if not session:
                    await _show_callback_error(query, "Le salon à rejoindre est introuvable.", "list_public")
                    return

                target_count = int(session.get("match_count", 1))
                success = toggle_cart_item(
                    user_id,
                    mid,
                    pick,
                    odds,
                    max_count=target_count,
                )
                if not success:
                    await _show_callback_error(
                        query,
                        f"Ce salon exige exactement {target_count} pronostic(s).",
                        "start_join_draft",
                    )
                    return
            else:
                toggle_cart_item(user_id, mid, pick, odds, max_count=None)
        except Exception:
            logger.exception("Erreur panier pour %s", user_id)
            await _show_callback_error(
                query,
                "Impossible d'enregistrer ce pronostic. Réessaie.",
                "back_to_sports",
            )
            return

        matches_db = get_matches_by_ids([mid])
        if matches_db:
            match = matches_db[0]
            comp = match.get("sport_title") or match.get("sport") or "Compétition"
            await _show_matches_for_comp(query, str(comp)[:40])
        else:
            await _show_competitions_for_sport(
                query,
                draft.get("current_sport", "soccer"),
            )

    elif data == "validate_ticket":
        await _show_ticket_summary(query, user_id)

    elif data == "confirm_ticket":
        await _process_ticket_creation(query, user_id, context)

    elif data == "list_public":
        text = (
            "🔍 **Rejoindre un salon public**\n\n"
            "Quel mode de jeu cherchez-vous ?"
        )
        keyboard = [
            [InlineKeyboardButton("🥊 Voir les Duels 1v1", callback_data="list_pubtype_DUEL")],
            [InlineKeyboardButton("🏟️ Voir les Arenas", callback_data="list_pubtype_ARENA")],
            [InlineKeyboardButton("⬅️ Retour", callback_data="menu_duel")],
        ]
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )

    elif data.startswith("list_pubtype_"):
        stype = data.split("_", 2)[2]
        if stype not in ("DUEL", "ARENA"):
            await _show_callback_error(query, "Type de salon invalide.", "list_public")
            return
        await _show_public_duels(query, user_id, stype)

    elif data.startswith("join_pub_"):
        sid = data.split("_", 2)[2]
        await _prompt_join_session(query, user_id, sid)

    elif data.startswith("ticket_"):
        parts = data.split("_", 2)
        if len(parts) != 3:
            await _show_callback_error(query, "Ticket invalide.", "my_tickets")
            return
        sid, tab = parts[1], parts[2]

        if tab == "replay":
            await replay_ticket_to_cart(query, sid)
        else:
            await show_ticket_detail(query, sid, tab)

    elif data in ("my_tickets", "menu_tickets"):
        try:
            cancel_expired_sessions()
            sessions = get_user_sessions(user_id)
        except Exception:
            logger.exception("Erreur chargement tickets %s", user_id)
            await _show_callback_error(
                query,
                "Impossible de charger tes tickets pour le moment.",
                "menu_main",
            )
            return

        if not sessions:
            await query.edit_message_text(
                "📭 Aucun ticket pour l'instant.",
                reply_markup=main_menu_keyboard(),
                parse_mode="Markdown",
            )
        else:
            await query.edit_message_text(
                "📋 **Tes Tickets Clashsport**",
                reply_markup=_tickets_keyboard(sessions, user_id),
                parse_mode="Markdown",
            )

    elif data in ("live_all", "menu_live"):
        try:
            sessions = [
                s for s in get_user_sessions(user_id, history_limit=0)
                if s.get("status") in ("WAITING", "IN_PROGRESS")
            ]
        except Exception:
            logger.exception("Erreur chargement live %s", user_id)
            await _show_callback_error(
                query,
                "Impossible de charger les matchs en direct.",
                "menu_main",
            )
            return

        if not sessions:
            await query.edit_message_text(
                "📭 Aucun duel en cours à suivre.",
                reply_markup=main_menu_keyboard(),
                parse_mode="Markdown",
            )
        else:
            keyboard = [
                [
                    InlineKeyboardButton(
                        "🏠 Menu Principal",
                        callback_data="menu_main",
                    )
                ]
            ]
            await query.edit_message_text(
                "🔴 Les matchs en direct sont accessibles dans chaque ticket.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
            )


async def handle_creation_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = context.user_data.get("creation_state")

    if not state or not update.message or not update.message.text:
        return

    text_input = update.message.text.strip()

    if state == "awaiting_arena_max":
        if not text_input.isdigit():
            await update.message.reply_text("❌ Veuillez entrer un nombre valide.")
            return

        mx = int(text_input)
        if not 3 <= mx <= 15:
            await update.message.reply_text(
                "❌ Le nombre de joueurs en Arena doit être compris entre 3 et 15."
            )
            return

        update_draft_settings(user_id, {"max_participants": mx})
        context.user_data.pop("creation_state", None)

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🥇 Vainqueur unique (100%)",
                callback_data="arena_prize_WINNER",
            )],
            [InlineKeyboardButton(
                "🥉 Top 3 (50% / 38% / 12%)",
                callback_data="arena_prize_TOP3",
            )],
            [InlineKeyboardButton("⬅️ Annuler", callback_data="menu_duel")],
        ])
        await update.message.reply_text(
            "🏆 **Mode de Distribution des Prix (Arena)**",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        return

    if state == "awaiting_fee":
        if not text_input.isdigit() or int(text_input) <= 0:
            await update.message.reply_text(
                "❌ Veuillez entrer un montant valide supérieur à 0."
            )
            return

        fee = int(text_input)
        update_draft_settings(user_id, {"gross_fee": fee})
        context.user_data.pop("creation_state", None)
        await _show_sports_selection(update)


async def _ask_entry_fee(update: Update, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["creation_state"] = "awaiting_fee"
    text = (
        "💰 **Mise d'entrée (Coins)**\n\n"
        "Sélectionnez une mise rapide ci-dessous, **ou tapez manuellement "
        "le montant** de votre choix dans le chat :"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("100 Coins", callback_data="fee_100"),
            InlineKeyboardButton("500 Coins", callback_data="fee_500"),
        ],
        [
            InlineKeyboardButton("1000 Coins", callback_data="fee_1000"),
            InlineKeyboardButton("5000 Coins", callback_data="fee_5000"),
        ],
        [InlineKeyboardButton("⬅️ Retour", callback_data="menu_duel")],
    ])

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=keyboard,
            parse_mode="Markdown",
        )


async def _show_sports_selection(update: Update):
    text = "⚽ **Sélection des Matchs**\n\nChoisissez un sport pour composer votre ticket :"
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚽ Football", callback_data="sport_soccer"),
            InlineKeyboardButton("🏀 Basketball", callback_data="sport_basketball"),
        ],
        [InlineKeyboardButton("🎾 Tennis", callback_data="sport_tennis")],
        [InlineKeyboardButton("⬅️ Retour", callback_data="menu_duel")],
    ])

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=keyboard,
            parse_mode="Markdown",
        )


async def _show_competitions_for_sport(query, sport):
    user_id = query.from_user.id
    try:
        update_draft_settings(user_id, {"current_sport": sport})
        matches = get_matches_by_sport(sport)
    except Exception:
        logger.exception("Erreur récupération matchs sport %s", sport)
        await _show_callback_error(
            query,
            "Impossible de charger les matchs pour ce sport.",
            "back_to_sports",
        )
        return

    if not matches:
        await _show_callback_error(
            query,
            "Aucun match disponible pour ce sport aujourd'hui.",
            "back_to_sports",
        )
        return

    competitions = set()
    for match in matches:
        comp = match.get("sport_title") or match.get("sport") or "Compétition"
        competitions.add(comp)

    emoji = (
        "🏀" if "basket" in sport.lower()
        else "🎾" if "tennis" in sport.lower()
        else "⚽"
    )
    text = (
        f"{emoji} **Compétitions ({sport.capitalize()})**\n\n"
        "Choisissez une ligue ou un tournoi :"
    )

    keyboard = []
    for comp in sorted(competitions):
        safe_comp = str(comp)[:40]
        keyboard.append([
            InlineKeyboardButton(
                f"🏆 {comp}",
                callback_data=f"comp_{safe_comp}",
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "⬅️ Retour aux sports",
            callback_data="back_to_sports",
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def _show_matches_for_comp(query, comp_prefix):
    user_id = query.from_user.id
    draft = get_draft_settings(user_id)
    sport = draft.get("current_sport", "soccer")

    try:
        matches = get_matches_by_sport(sport)
    except Exception:
        logger.exception("Erreur récupération matchs %s", sport)
        await _show_callback_error(
            query,
            "Impossible de charger les matchs.",
            f"sport_{sport}",
        )
        return

    comp_matches = []
    for match in (matches or []):
        comp = match.get("sport_title") or match.get("sport") or "Compétition"
        if str(comp).startswith(comp_prefix):
            comp_matches.append(match)

    if not comp_matches:
        await _show_callback_error(
            query,
            "Aucun match disponible pour cette compétition.",
            f"sport_{sport}",
        )
        return

    cart = {
        str(item["match_id"]): item["pick"]
        for item in get_cart(user_id)
    }

    joining_sid = draft.get("joining_session_id")
    if joining_sid:
        session = get_session(joining_sid)
        target_count = int(session.get("match_count", 1)) if session else 1
        header_text = (
            f"🎯 <b>{comp_matches[0].get('sport_title', sport.capitalize())}</b>\n"
            f"Pronostics ({len(cart)}/{target_count}) :\n\n"
        )
        can_validate = len(cart) == target_count
    else:
        header_text = (
            f"🎯 <b>{comp_matches[0].get('sport_title', sport.capitalize())}</b>\n"
            f"Matchs sélectionnés : <code>{len(cart)}</code>\n\n"
        )
        can_validate = len(cart) >= 1

    keyboard = []
    button_count = 0

    for match in comp_matches:
        if button_count >= 85:
            break

        try:
            mid = str(match["api_match_id"])
            home = str(match.get("home_team", "Équipe A"))
            away = str(match.get("away_team", "Équipe B"))

            odds_h = _safe_float(match.get("odds_home"), 1.9)
            odds_d = _safe_float(match.get("odds_draw"), 0.0)
            odds_a = _safe_float(match.get("odds_away"), 1.9)

            keyboard.append([
                InlineKeyboardButton(
                    f"⚔️ {home} - {away}",
                    callback_data="ignore",
                )
            ])
            button_count += 1

            row = []
            for label, pick, odd in (
                ("1", "HOME", odds_h),
                ("N", "DRAW", odds_d),
                ("2", "AWAY", odds_a),
            ):
                if odd <= 1.0:
                    continue

                is_selected = cart.get(mid) == pick
                prefix = "✅ " if is_selected else ""
                row.append(InlineKeyboardButton(
                    f"{prefix}{label} ({odd})",
                    callback_data=f"bet_{mid}_{pick}_{odd}",
                ))

            if row:
                keyboard.append(row)
                button_count += len(row)

        except Exception as exc:
            logger.error(
                "Erreur d'affichage du match %s: %s",
                match.get("api_match_id"),
                exc,
            )
            continue

    if can_validate:
        keyboard.append([
            InlineKeyboardButton(
                "🚀 Valider mon Ticket",
                callback_data="validate_ticket",
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "⬅️ Retour aux compétitions",
            callback_data=f"sport_{sport}",
        )
    ])

    await query.edit_message_text(
        header_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )


async def _show_ticket_summary(query, user_id):
    draft = get_draft_settings(user_id)
    cart = get_cart(user_id)

    if not cart:
        await _show_callback_error(query, "Votre panier est vide !", "back_to_sports")
        return

    joining_sid = draft.get("joining_session_id")
    if joining_sid:
        session = get_session(joining_sid)
        if not session or session.get("status") != "WAITING":
            await _show_callback_error(query, "Ce salon n'est plus disponible.", "list_public")
            return

        expected_count = int(session.get("match_count", 1))
        if len(cart) != expected_count:
            await _show_callback_error(
                query,
                f"Ce salon exige exactement {expected_count} pronostic(s).",
                "back_to_sports",
            )
            return

    match_ids = [str(item["match_id"]) for item in cart]
    try:
        matches = get_matches_by_ids(match_ids)
    except Exception:
        logger.exception("Erreur récupération résumé ticket %s", user_id)
        await _show_callback_error(query, "Impossible de charger le résumé du ticket.", "back_to_sports")
        return

    # Ne jamais confirmer un ticket si un match sélectionné n'existe plus
    # ou n'est plus disponible dans la base.
    found_ids = {str(match.get("api_match_id")) for match in matches}
    missing = [mid for mid in match_ids if mid not in found_ids]
    if missing:
        await _show_callback_error(
            query,
            "Un ou plusieurs matchs sélectionnés ne sont plus disponibles. Actualise ton ticket.",
            "back_to_sports",
        )
        return

    match_dict = {str(match["api_match_id"]): match for match in matches}

    # Aucun Markdown : les noms d'équipes/ligues provenant de l'API peuvent
    # contenir _, *, [, ], etc. et faire échouer edit_message_text().
    text = "📋 RÉSUMÉ DE VOTRE TICKET\n\n"
    text += f"🏷️ Type : {draft.get('session_type', 'DUEL')}\n"
    text += f"💰 Mise : {draft.get('gross_fee', 100)} Coins\n"
    text += f"🎯 Sélection ({len(cart)} matchs) :\n\n"

    for item in cart:
        match = match_dict[str(item["match_id"])]
        home = str(match.get("home_team") or "Équipe A")
        away = str(match.get("away_team") or "Équipe B")
        pick_str = "1" if item["pick"] == "HOME" else "N" if item["pick"] == "DRAW" else "2"
        text += f"🔹 {home} vs {away}\n"
        text += f"👉 Pronostic : {pick_str} (Cote : {item['odds']})\n\n"

    text += "Es-tu sûr de vouloir valider ce ticket ?"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirmer la création", callback_data="confirm_ticket")],
        [InlineKeyboardButton("⬅️ Modifier mes choix", callback_data="back_to_sports")],
    ])

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
    )


async def _process_ticket_creation(query, user_id, context):
    draft = get_draft_settings(user_id)
    cart = get_cart(user_id)
    joining_session_id = draft.get("joining_session_id")

    if not cart:
        await _show_callback_error(
            query,
            "Votre panier est vide !",
            "back_to_sports",
        )
        return

    if not draft.get("gross_fee") or int(draft.get("gross_fee", 0)) <= 0:
        await _show_callback_error(
            query,
            "La mise du ticket est invalide.",
            "menu_duel",
        )
        return

    predictions = [
        {
            "match_id": item["match_id"],
            "pick": item["pick"],
            "odds": item["odds"],
        }
        for item in cart
    ]
    match_count = len(predictions)

    try:
        live_matches = get_matches_by_ids([p["match_id"] for p in predictions])
    except Exception:
        logger.exception("Erreur revalidation matchs avant confirmation %s", user_id)
        await _show_callback_error(query, "Impossible de vérifier les matchs. Réessaie.", "back_to_sports")
        return

    live_ids = {str(m.get("api_match_id")) for m in live_matches}
    if any(str(p["match_id"]) not in live_ids for p in predictions):
        await _show_callback_error(
            query,
            "Un ou plusieurs matchs ne sont plus disponibles. Ton ticket n'a pas été débité.",
            "back_to_sports",
        )
        return

    if joining_session_id:
        session = get_session(joining_session_id)
        if not session or session.get("status") != "WAITING":
            await _show_callback_error(
                query,
                "Ce salon n'est plus disponible.",
                "list_public",
            )
            return

        expected_count = int(session.get("match_count", 1))
        if match_count != expected_count:
            await _show_callback_error(
                query,
                f"Ce salon exige exactement {expected_count} pronostic(s).",
                "back_to_sports",
            )
            return

        try:
            session_result, msg = join_session(
                joining_session_id,
                user_id,
                predictions,
            )
        except Exception:
            logger.exception(
                "Exception lors de la jonction au salon %s par %s",
                joining_session_id,
                user_id,
            )
            session_result, msg = None, "Impossible de rejoindre le salon."

        if not session_result:
            await _show_callback_error(
                query,
                f"Erreur : {msg}",
                "list_public",
            )
            return

        clear_draft(user_id)
        db_user = get_user_by_id(user_id) or {}
        text = (
            "⚔️ **TICKET VALIDÉ !**\n\n"
            f"💰 Nouveau solde : `{db_user.get('coins_balance', 0)}` Coins\n\n"
            "Le défi est accepté, tu peux suivre l'avancée dans Mes Tickets."
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📋 Voir mon ticket",
                    callback_data=f"ticket_{joining_session_id}_mine",
                )
            ],
            [InlineKeyboardButton(
                "🏠 Menu Principal",
                callback_data="menu_main",
            )],
        ])
        await query.edit_message_text(
            text,
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        return

    session_type = draft.get("session_type", "DUEL")
    max_participants = int(draft.get("max_participants", 2))
    prize_mode = draft.get("prize_mode", "WINNER_TAKES_ALL")

    try:
        session, msg = create_session(
            creator_id=user_id,
            session_type=session_type,
            gross_fee=draft.get("gross_fee", 100),
            match_count=match_count,
            max_participants=max_participants,
            prize_mode=prize_mode,
            predictions=predictions,
        )
    except Exception:
        logger.exception("Exception lors de la création du salon par %s", user_id)
        session, msg = None, "Impossible de créer le salon pour le moment."

    if not session:
        # NE PAS utiliser query.answer() ici : le callback a déjà été acquitté.
        # On affiche l'erreur dans le message pour que la confirmation ne paraisse
        # plus bloquée.
        await _show_callback_error(
            query,
            f"Erreur : {msg}",
            "back_to_sports",
        )
        return

    clear_draft(user_id)
    sid = session["id"]

    try:
        bot_user = (await context.bot.get_me()).username
    except Exception:
        bot_user = None

    invite_link = (
        f"https://t.me/{bot_user}?start=join_{sid}"
        if bot_user
        else f"join_{sid}"
    )

    text = (
        "✅ **Salon créé avec succès !**\n\n"
        f"Type : `{session['type']}`\n"
        f"Mise : `{session['gross_entry_fee']}` Coins\n"
        f"Matchs : `{session['match_count']}`\n\n"
        "🔗 **Lien d'invitation à partager :**\n"
        f"`{invite_link}`\n\n"
        "⏳ *Note : Votre salon restera privé pendant 5 minutes. "
        "Passé ce délai, il apparaîtra dans la liste publique pour que "
        "d'autres joueurs puissent vous affronter.*"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "📋 Voir mon ticket",
            callback_data=f"ticket_{sid}_mine",
        )],
        [InlineKeyboardButton(
            "🏠 Menu Principal",
            callback_data="menu_main",
        )],
    ])

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


async def _show_public_duels(query, user_id, stype):
    """Affiche les salons publics après la période de grâce de 5 minutes."""
    try:
        result = (
            supabase.table("sessions")
            .select("*")
            .eq("status", "WAITING")
            .eq("type", stype)
            .neq("creator_id", user_id)
            .execute()
        )
        sessions = result.data or []
    except Exception:
        logger.exception("Erreur récupération salons publics")
        await _show_callback_error(
            query,
            "Impossible de charger les salons publics.",
            "list_public",
        )
        return

    if not sessions:
        await _show_callback_error(
            query,
            f"Aucun salon {stype} public disponible pour le moment.",
            "list_public",
        )
        return

    now = datetime.now(timezone.utc)
    public_sessions = []

    for session in sessions:
        created_at_str = session.get("created_at")
        if created_at_str:
            try:
                created_dt = datetime.fromisoformat(
                    str(created_at_str).replace("Z", "+00:00")
                )
                if now >= created_dt + timedelta(minutes=5):
                    public_sessions.append(session)
            except (ValueError, TypeError):
                public_sessions.append(session)
        else:
            public_sessions.append(session)

    if not public_sessions:
        await _show_callback_error(
            query,
            f"Aucun salon {stype} public n'est disponible "
            "(certains sont encore en période d'attente privée).",
            "list_public",
        )
        return

    lbl_type = "Duel 1v1" if stype == "DUEL" else "Arena"
    text = (
        f"🔍 **Salons Publics ({lbl_type})**\n"
        "Choisissez un salon à rejoindre :"
    )
    keyboard = []

    for session in public_sessions:
        creator = get_user_by_id(session["creator_id"]) or {}
        username = creator.get("username", "Joueur")

        if stype == "DUEL":
            label = (
                f"🥊 {username} — "
                f"{session['gross_entry_fee']} Coins "
                f"({session['match_count']}m)"
            )
        else:
            label = (
                f"🏟️ {username} — "
                f"{session['gross_entry_fee']} Coins "
                f"({session['match_count']}m) "
                f"[Max {session.get('max_participants', 4)}j]"
            )

        keyboard.append([
            InlineKeyboardButton(
                label,
                callback_data=f"join_pub_{session['id']}",
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "⬅️ Retour aux modes",
            callback_data="list_public",
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def _prompt_join_session(query, user_id, session_id):
    try:
        session = get_session(session_id)
    except Exception:
        logger.exception("Erreur récupération salon %s", session_id)
        await _show_callback_error(
            query,
            "Impossible de charger ce salon.",
            "list_public",
        )
        return

    if not session or session.get("status") != "WAITING":
        await _show_callback_error(
            query,
            "Ce salon n'est plus disponible.",
            "list_public",
        )
        return

    try:
        tickets = get_tickets_for_session(session_id)
    except Exception:
        logger.exception("Erreur récupération tickets salon %s", session_id)
        await _show_callback_error(
            query,
            "Impossible de vérifier les places disponibles.",
            "list_public",
        )
        return

    current_players = len(tickets)
    max_players = int(session.get("max_participants", 2))

    if current_players >= max_players:
        await _show_callback_error(
            query,
            "Ce salon est déjà plein.",
            "list_public",
        )
        return

    update_draft_settings(
        user_id,
        {
            "joining_session_id": session_id,
            "match_count": session["match_count"],
            "session_type": session["type"],
            "gross_fee": session["gross_entry_fee"],
            "max_participants": max_players,
            "prize_mode": session.get("prize_mode", "WINNER_TAKES_ALL"),
        },
    )

    stype = "🥊 Duel 1v1" if session["type"] == "DUEL" else "🏟️ Arena"
    text = (
        "ℹ️ **Informations du Salon**\n\n"
        f"**Type :** {stype}\n"
        f"**Mise :** `{session['gross_entry_fee']}` Coins\n"
        f"**Matchs à pronostiquer :** `{session['match_count']}`\n"
        f"**Joueurs actuels :** `{current_players}/{max_players}`\n\n"
        "Prêt à relever le défi ?"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "✅ Composer mon ticket",
            callback_data="start_join_draft",
        )],
        [InlineKeyboardButton(
            "⬅️ Retour aux salons",
            callback_data="list_public",
        )],
    ])

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


async def propose_join_duel(message, user_id, session_id, context):
    session = get_session(session_id)

    if not session or session.get("status") != "WAITING":
        await message.reply_text(
            "❌ Ce salon n'est plus disponible ou a déjà débuté."
        )
        return

    tickets = get_tickets_for_session(session_id)
    current_players = len(tickets)
    max_players = int(session.get("max_participants", 2))

    if current_players >= max_players:
        await message.reply_text("❌ Ce salon est déjà plein.")
        return

    update_draft_settings(
        user_id,
        {
            "joining_session_id": session_id,
            "match_count": session["match_count"],
            "session_type": session["type"],
            "gross_fee": session["gross_entry_fee"],
            "max_participants": max_players,
            "prize_mode": session.get("prize_mode", "WINNER_TAKES_ALL"),
        },
    )

    stype = "🥊 Duel 1v1" if session["type"] == "DUEL" else "🏟️ Arena"
    text = (
        "ℹ️ **Vous avez été invité à rejoindre un salon !**\n\n"
        f"**Type :** {stype}\n"
        f"**Mise :** `{session['gross_entry_fee']}` Coins\n"
        f"**Matchs à pronostiquer :** `{session['match_count']}`\n"
        f"**Joueurs actuels :** `{current_players}/{max_players}`\n\n"
        "Veuillez sélectionner un sport pour composer votre ticket :"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Football", callback_data="sport_soccer")],
        [InlineKeyboardButton("🏀 Basketball", callback_data="sport_basketball")],
        [InlineKeyboardButton("🎾 Tennis", callback_data="sport_tennis")],
    ])

    await message.reply_text(
        text,
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


def _tickets_keyboard(sessions, user_id=None):
    user_id = user_id if user_id is not None else 0
    active = [
        s for s in sessions
        if s.get("status") in ("WAITING", "IN_PROGRESS")
    ]
    history = [
        s for s in sessions
        if s.get("status") in ("COMPLETED", "CANCELLED")
    ][:4]

    def status_label(session):
        status = session.get("status")
        if status == "WAITING":
            return "⏳ En attente"
        if status == "IN_PROGRESS":
            return "🔴 En cours"
        if status == "CANCELLED":
            return "🟡 Remboursé"
        if status == "COMPLETED":
            if session.get("type") == "DUEL" and session.get("winner_id") is None:
                return "🟡 Remboursé"
            if str(session.get("winner_id")) == str(user_id):
                return "🟢 Gagné"
            return "🔴 Perdu"
        return "⚪ " + str(status or "Inconnu")

    keyboard = []

    if active:
        keyboard.append([
            InlineKeyboardButton("🔴 TICKETS EN COURS", callback_data="ignore")
        ])
        for session in active:
            stype = "Duel" if session.get("type") == "DUEL" else "Arena"
            fee = session.get("gross_entry_fee", 0)
            keyboard.append([
                InlineKeyboardButton(
                    f"{status_label(session)} — {stype} — {fee} Coins",
                    callback_data=f"ticket_{session['id']}_mine",
                )
            ])

    if history:
        keyboard.append([
            InlineKeyboardButton("📜 HISTORIQUE — 4 DERNIERS", callback_data="ignore")
        ])
        for session in history:
            stype = "Duel" if session.get("type") == "DUEL" else "Arena"
            fee = session.get("gross_entry_fee", 0)
            keyboard.append([
                InlineKeyboardButton(
                    f"{status_label(session)} — {stype} — {fee} Coins",
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



