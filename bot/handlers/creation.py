import math
import logging
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import config
from database.connection import supabase
from database.cart import get_draft_settings, update_draft_settings, clear_draft, toggle_cart_item, get_cart
from services.session_service import get_matches_by_sport, get_matches_by_ids, create_session, join_session, get_session, get_tickets_for_session
from services.user_service import get_user_by_id

from bot.ui import main_menu_keyboard
from bot.handlers.tickets_view import show_ticket_detail

logger = logging.getLogger(__name__)

def _safe_float(val, default=0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

async def handle_creation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "ignore":
        return

    if data == "menu_main":
        context.user_data.pop("creation_state", None)
        await query.edit_message_text("🏠 **Menu Principal**", reply_markup=main_menu_keyboard(), parse_mode="Markdown")

    elif data == "menu_duel":
        context.user_data.pop("creation_state", None)
        text = "⚔️ **Mode de jeu**\n\nChoisissez comment vous souhaitez parier :"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🥊 Duel 1v1", callback_data="type_DUEL"), InlineKeyboardButton("🏟️ Mode Arena", callback_data="type_ARENA")],
            [InlineKeyboardButton("🔍 Rejoindre un salon public", callback_data="list_public")],
            [InlineKeyboardButton("⬅️ Retour", callback_data="menu_main")]
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")

    elif data.startswith("type_"):
        stype = data.split("_")[1]
        update_draft_settings(user_id, {"session_type": stype})
        if stype == "DUEL":
            update_draft_settings(user_id, {"max_participants": 2, "prize_mode": "WINNER_TAKES_ALL"})
            await _ask_entry_fee(update, user_id, context)
        else:
            context.user_data["creation_state"] = "awaiting_arena_max"
            text = "🏟️ **Configuration Arena**\n\nEntrez dans le chat le **nombre maximum de joueurs** pour ce salon (entre 3 et 15) :"
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Retour", callback_data="menu_duel")]])
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")

    elif data.startswith("arena_prize_"):
        mode = "TOP_3" if data.split("_")[2] == "TOP3" else "WINNER_TAKES_ALL"
        update_draft_settings(user_id, {"prize_mode": mode})
        await _ask_entry_fee(update, user_id, context)

    elif data.startswith("fee_"):
        fee = int(data.split("_")[1])
        update_draft_settings(user_id, {"gross_fee": fee})
        context.user_data.pop("creation_state", None)
        await _show_sports_selection(update)

    elif data.startswith("sport_"):
        sport = data.split("_")[1]
        await _show_competitions_for_sport(query, sport)

    elif data.startswith("comp_"):
        comp_prefix = data.split("_", 1)[1]
        await _show_matches_for_comp(query, comp_prefix)

    elif data == "back_to_sports":
        await _show_sports_selection(update)

    elif data == "start_join_draft":
        await _show_sports_selection(update)

    elif data.startswith("bet_"):
        _, mid, pick, odds = data.split("_")
        draft = get_draft_settings(user_id)
        joining_sid = draft.get("joining_session_id")
        
        if joining_sid:
            session = get_session(joining_sid)
            target_count = session.get("match_count", 1) if session else 1
            success = toggle_cart_item(user_id, mid, pick, float(odds), max_count=target_count)
            if not success:
                await query.answer(f"⚠️ Ce salon exige exactement {target_count} pronostic(s).", show_alert=True)
                return
        else:
            toggle_cart_item(user_id, mid, pick, float(odds), max_count=None)
            
        matches_db = get_matches_by_ids([mid])
        if matches_db:
            m = matches_db[0]
            comp = m.get("sport_title") or m.get("sport") or "Compétition"
            await _show_matches_for_comp(query, comp[:40])
        else:
            await _show_competitions_for_sport(query, draft.get("current_sport", "soccer"))

    elif data == "validate_ticket":
        await _show_ticket_summary(query, user_id)

    elif data == "confirm_ticket":
        await _process_ticket_creation(query, user_id, context)

    # --- NOUVEAU SOUS-MENU POUR LA LISTE PUBLIQUE ---
    elif data == "list_public":
        text = "🔍 **Rejoindre un salon public**\n\nQuel mode de jeu cherchez-vous ?"
        keyboard = [
            [InlineKeyboardButton("🥊 Voir les Duels 1v1", callback_data="list_pubtype_DUEL")],
            [InlineKeyboardButton("🏟️ Voir les Arenas", callback_data="list_pubtype_ARENA")],
            [InlineKeyboardButton("⬅️ Retour", callback_data="menu_duel")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("list_pubtype_"):
        stype = data.split("_")[2]
        await _show_public_duels(query, user_id, stype)
    # --------------------------------------------------

    elif data.startswith("join_pub_"):
        sid = data.split("_")[2]
        await _prompt_join_session(query, user_id, sid)

    elif data.startswith("ticket_"):
        parts = data.split("_")
        sid, tab = parts[1], parts[2]
        await show_ticket_detail(query, sid, tab)

    elif data == "my_tickets":
    from services.session_service import cancel_expired_sessions, get_user_sessions

        from bot.handlers.tickets_view import _tickets_keyboard
        cancel_expired_sessions()
        sessions = get_user_sessions(user_id)
        if not sessions:
            await query.edit_message_text("📭 Aucun ticket pour l'instant.", reply_markup=main_menu_keyboard(), parse_mode="Markdown")
        else:
            await query.edit_message_text("📋 **Tes Tickets Clashsport**", reply_markup=_tickets_keyboard(sessions), parse_mode="Markdown")

elif data == "live_all":
    from services.session_service import get_user_sessions

        sessions = [s for s in get_user_sessions(user_id, history_limit=0) if s["status"] in ("WAITING", "IN_PROGRESS")]
        if not sessions:
            await query.edit_message_text("📭 Aucun duel en cours à suivre.", reply_markup=main_menu_keyboard(), parse_mode="Markdown")
        else:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="menu_main")]])
            await query.edit_message_text("🔴 Les matchs en direct sont accessibles dans chaque ticket.", reply_markup=keyboard, parse_mode="Markdown")


async def handle_creation_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = context.user_data.get("creation_state")
    
    if not state:
        return

    text_input = update.message.text.strip()
    
    if state == "awaiting_arena_max":
        if not text_input.isdigit():
            await update.message.reply_text("❌ Veuillez entrer un nombre valide.")
            return
        mx = int(text_input)
        if not (3 <= mx <= 15):
            await update.message.reply_text("❌ Le nombre de joueurs en Arena doit être compris entre 3 et 15.")
            return
        
        update_draft_settings(user_id, {"max_participants": mx})
        context.user_data.pop("creation_state", None)
        
        text = "🏆 **Mode de Distribution des Prix (Arena)**"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🥇 Vainqueur unique (100%)", callback_data="arena_prize_WINNER")],
            [InlineKeyboardButton("🥉 Top 3 (50% / 38% / 12%)", callback_data="arena_prize_TOP3")],
            [InlineKeyboardButton("⬅️ Annuler", callback_data="menu_duel")]
        ])
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
        return

    if state == "awaiting_fee":
        if not text_input.isdigit() or int(text_input) <= 0:
            await update.message.reply_text("❌ Veuillez entrer un montant valide supérieur à 0.")
            return
        fee = int(text_input)
        update_draft_settings(user_id, {"gross_fee": fee})
        context.user_data.pop("creation_state", None)
        await _show_sports_selection(update)
        return


async def _ask_entry_fee(update: Update, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["creation_state"] = "awaiting_fee"
    text = "💰 **Mise d'entrée (Coins)**\n\nSélectionnez une mise rapide ci-dessous, **ou tapez manuellement le montant** de votre choix dans le chat :"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("100 Coins", callback_data="fee_100"), InlineKeyboardButton("500 Coins", callback_data="fee_500")],
        [InlineKeyboardButton("1000 Coins", callback_data="fee_1000"), InlineKeyboardButton("5000 Coins", callback_data="fee_5000")],
        [InlineKeyboardButton("⬅️ Retour", callback_data="menu_duel")]
    ])
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def _show_sports_selection(update: Update):
    text = "⚽ **Sélection des Matchs**\n\nChoisissez un sport pour composer votre ticket :"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Football", callback_data="sport_soccer"), InlineKeyboardButton("🏀 Basketball", callback_data="sport_basketball")],
        [InlineKeyboardButton("🎾 Tennis", callback_data="sport_tennis")],
        [InlineKeyboardButton("⬅️ Retour", callback_data="menu_duel")]
    ])
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def _show_competitions_for_sport(query, sport):
    user_id = query.from_user.id
    update_draft_settings(user_id, {"current_sport": sport})
    matches = get_matches_by_sport(sport)
    
    if not matches:
        await query.answer("Aucun match disponible pour ce sport aujourd'hui.", show_alert=True)
        return
    
    competitions = set()
    for m in matches:
        comp = m.get("sport_title") or m.get("sport") or "Compétition"
        competitions.add(comp)

    emoji = "🏀" if "basket" in sport.lower() else "🎾" if "tennis" in sport.lower() else "⚽"
    text = f"{emoji} **Compétitions ({sport.capitalize()})**\n\nChoisissez une ligue ou un tournoi :"
    keyboard = []
    
    for comp in sorted(competitions):
        safe_comp = comp[:40]
        keyboard.append([InlineKeyboardButton(f"🏆 {comp}", callback_data=f"comp_{safe_comp}")])

    keyboard.append([InlineKeyboardButton("⬅️ Retour aux sports", callback_data="back_to_sports")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def _show_matches_for_comp(query, comp_prefix):
    user_id = query.from_user.id
    draft = get_draft_settings(user_id)
    sport = draft.get("current_sport", "soccer")
    
    matches = get_matches_by_sport(sport)
    comp_matches = []
    
    for m in (matches or []):
        comp = m.get("sport_title") or m.get("sport") or "Compétition"
        if comp.startswith(comp_prefix):
            comp_matches.append(m)

    if not comp_matches:
        await query.answer("Aucun match disponible pour cette compétition.", show_alert=True)
        return
        
    cart = {str(item["match_id"]): item["pick"] for item in get_cart(user_id)}
    
    joining_sid = draft.get("joining_session_id")
    if joining_sid:
        session = get_session(joining_sid)
        target_count = session.get("match_count", 1) if session else 1
        header_text = f"🎯 <b>{comp_matches[0].get('sport_title', sport.capitalize())}</b>\nPronostics ({len(cart)}/{target_count}) :\n\n"
        can_validate = (len(cart) == target_count)
    else:
        header_text = f"🎯 <b>{comp_matches[0].get('sport_title', sport.capitalize())}</b>\nMatchs sélectionnés : <code>{len(cart)}</code>\n\n"
        can_validate = (len(cart) >= 1)

    keyboard = []
    button_count = 0
    
    for m in comp_matches:
        if button_count >= 85:
            break
            
        try:
            mid = str(m["api_match_id"])
            home = str(m.get("home_team", "Équipe A"))
            away = str(m.get("away_team", "Équipe B"))
            
            odds_h = _safe_float(m.get("odds_home"), 1.9)
            odds_d = _safe_float(m.get("odds_draw"), 0.0)
            odds_a = _safe_float(m.get("odds_away"), 1.9)

            keyboard.append([InlineKeyboardButton(f"⚔️ {home} - {away}", callback_data="ignore")])
            button_count += 1
            
            row = []
            for label, pick, odd in [("1", "HOME", odds_h), ("N", "DRAW", odds_d), ("2", "AWAY", odds_a)]:
                if odd <= 1.0: 
                    continue
                is_selected = (cart.get(mid) == pick)
                prefix = "✅ " if is_selected else ""
                row.append(InlineKeyboardButton(f"{prefix}{label} ({odd})", callback_data=f"bet_{mid}_{pick}_{odd}"))
            
            if row:
                keyboard.append(row)
                button_count += len(row)
                
        except Exception as e:
            logger.error(f"Erreur d'affichage du match {m.get('api_match_id')}: {e}")
            continue

    if can_validate:
        keyboard.append([InlineKeyboardButton("🚀 Valider mon Ticket", callback_data="validate_ticket")])
    keyboard.append([InlineKeyboardButton("⬅️ Retour aux compétitions", callback_data=f"sport_{sport}")])

    await query.edit_message_text(header_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def _show_ticket_summary(query, user_id):
    draft = get_draft_settings(user_id)
    cart = get_cart(user_id)
    
    if not cart:
        await query.answer("Votre panier est vide !", show_alert=True)
        return

    match_ids = [c["match_id"] for c in cart]
    matches = get_matches_by_ids(match_ids)
    match_dict = {str(m["api_match_id"]): m for m in matches}

    text = "📋 **RÉSUMÉ DE VOTRE TICKET**\n\n"
    text += f"🏷️ **Type :** `{draft.get('session_type', 'DUEL')}`\n"
    text += f"💰 **Mise :** `{draft.get('gross_fee', 100)} Coins`\n"
    text += f"🎯 **Sélection ({len(cart)} matchs) :**\n\n"

    for item in cart:
        m = match_dict.get(str(item["match_id"]), {})
        home = m.get("home_team", "Équipe A")
        away = m.get("away_team", "Équipe B")
        pick_str = "1" if item["pick"] == "HOME" else "N" if item["pick"] == "DRAW" else "2"
        
        text += f"🔹 {home} vs {away}\n"
        text += f"👉 **Pronostic : {pick_str}** (Cote: {item['odds']})\n\n"

    text += "Êtes-vous sûr de vouloir valider ce ticket ?"
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirmer la création", callback_data="confirm_ticket")],
        [InlineKeyboardButton("⬅️ Modifier mes choix", callback_data="back_to_sports")]
    ])
    
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def _process_ticket_creation(query, user_id, context):
    draft = get_draft_settings(user_id)
    cart = get_cart(user_id)
    joining_session_id = draft.get("joining_session_id")

    if not cart:
        await query.answer("Votre panier est vide !", show_alert=True)
        return

    predictions = [{"match_id": item["match_id"], "pick": item["pick"], "odds": item["odds"]} for item in cart]
    match_count = len(predictions)

    if joining_session_id:
        session, msg = join_session(joining_session_id, user_id, predictions)
        if not session:
            await query.answer(f"Erreur : {msg}", show_alert=True)
            return
        clear_draft(user_id)
        db_user = get_user_by_id(user_id)
        text = (
            "⚔️ **TICKET VALIDÉ !**\n\n"
            f"💰 Nouveau solde : `{db_user['coins_balance']}` Coins\n\n"
            "Le défi est accepté, tu peux suivre l'avancée dans Mes Tickets."
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Voir mon ticket", callback_data=f"ticket_{joining_session_id}_mine")],
            [InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")]
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
        return

    session, msg = create_session(
        creator_id=user_id,
        session_type=draft.get("session_type", "DUEL"),
        gross_fee=draft.get("gross_fee", 100),
        match_count=match_count,
        max_participants=draft.get("max_participants", 2),
        prize_mode=draft.get("prize_mode", "WINNER_TAKES_ALL"),
        predictions=predictions
    )

    if not session:
        await query.answer(f"Erreur : {msg}", show_alert=True)
        return

    clear_draft(user_id)
    sid = session["id"]
    bot_user = (await context.bot.get_me()).username
    invite_link = f"https://t.me/{bot_user}?start=join_{sid}"

    text = (
        f"✅ **Salon créé avec succès !**\n\n"
        f"Type : `{session['type']}`\n"
        f"Mise : `{session['gross_entry_fee']}` Coins\n"
        f"Matchs : `{session['match_count']}`\n\n"
        f"🔗 **Lien d'invitation à partager :**\n`{invite_link}`\n\n"
        f"⏳ *Note : Votre salon restera privé pendant 5 minutes. Passé ce délai, il apparaîtra dans la liste publique pour que d'autres joueurs puissent vous affronter.*"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Voir mon ticket", callback_data=f"ticket_{sid}_mine")],
        [InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")]
    ])
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")

# --- MODIFICATION DE LA LISTE PUBLIQUE (GRACE PERIOD) ---
async def _show_public_duels(query, user_id, stype):
    """Affiche uniquement la liste filtrée selon le type choisi et exclut ceux dans la période de grâce de 5 minutes."""
    
    # 1. On récupère les sessions
    res = supabase.table("sessions").select("*").eq("status", "WAITING").eq("type", stype).neq("creator_id", user_id).execute().data
    
    if not res:
        await query.answer(f"Aucun salon {stype} public disponible pour le moment.", show_alert=True)
        return

    # 2. Filtrage des 5 minutes de grâce
    now = datetime.now(timezone.utc)
    public_sessions = []
    
    for s in res:
        created_at_str = s.get("created_at")
        if created_at_str:
            try:
                created_dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                # On ajoute 5 minutes à la date de création. Si on est après cette heure-là, le salon est public.
                if now >= (created_dt + timedelta(minutes=5)):
                    public_sessions.append(s)
            except ValueError:
                # Si erreur de format, on l'affiche par défaut
                public_sessions.append(s)
        else:
            public_sessions.append(s)

    if not public_sessions:
        await query.answer(f"Aucun salon {stype} public n'est disponible (certains sont en période d'attente privée).", show_alert=True)
        return

    lbl_type = "Duel 1v1" if stype == "DUEL" else "Arena"
    text = f"🔍 **Salons Publics ({lbl_type})**\nChoisissez un salon à rejoindre :"
    keyboard = []
    
    for s in public_sessions:
        creator = get_user_by_id(s["creator_id"]) or {}
        uname = creator.get("username", "Joueur")
        if stype == "DUEL":
            btn_lbl = f"🥊 {uname} — {s['gross_entry_fee']} Coins ({s['match_count']}m)"
        else:
            btn_lbl = f"🏟️ {uname} — {s['gross_entry_fee']} Coins ({s['match_count']}m) [Max {s.get('max_participants', 4)}j]"
        
        keyboard.append([InlineKeyboardButton(btn_lbl, callback_data=f"join_pub_{s['id']}")])

    keyboard.append([InlineKeyboardButton("⬅️ Retour aux modes", callback_data="list_public")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
# --------------------------------------------------------


async def _prompt_join_session(query, user_id, session_id):
    session = get_session(session_id)
    if not session or session["status"] != "WAITING":
        await query.answer("Ce salon n'est plus disponible.", show_alert=True)
        return

    tickets = get_tickets_for_session(session_id)
    current_players = len(tickets)
    max_players = session.get("max_participants", 2)

    if current_players >= max_players:
        await query.answer("Ce salon est déjà plein.", show_alert=True)
        return
    
    update_draft_settings(user_id, {"joining_session_id": session_id, "match_count": session["match_count"]})
    
    stype = "🥊 Duel 1v1" if session["type"] == "DUEL" else "🏟️ Arena"
    text = (
        f"ℹ️ **Informations du Salon**\n\n"
        f"**Type :** {stype}\n"
        f"**Mise :** `{session['gross_entry_fee']}` Coins\n"
        f"**Matchs à pronostiquer :** `{session['match_count']}`\n"
        f"**Joueurs actuels :** `{current_players}/{max_players}`\n\n"
        "Prêt à relever le défi ?"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Composer mon ticket", callback_data="start_join_draft")],
        [InlineKeyboardButton("⬅️ Retour aux salons", callback_data="list_public")]
    ])
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def propose_join_duel(message, user_id, session_id, context):
    session = get_session(session_id)
    if not session or session["status"] != "WAITING":
        await message.reply_text("❌ Ce salon n'est plus disponible ou a déjà débuté.")
        return

    tickets = get_tickets_for_session(session_id)
    current_players = len(tickets)
    max_players = session.get("max_participants", 2)

    if current_players >= max_players:
        await message.reply_text("❌ Ce salon est déjà plein.")
        return
    
    update_draft_settings(user_id, {"joining_session_id": session_id, "match_count": session["match_count"]})
    
    stype = "🥊 Duel 1v1" if session["type"] == "DUEL" else "🏟️ Arena"
    text = (
        f"ℹ️ **Vous avez été invité à rejoindre un salon !**\n\n"
        f"**Type :** {stype}\n"
        f"**Mise :** `{session['gross_entry_fee']}` Coins\n"
        f"**Matchs à pronostiquer :** `{session['match_count']}`\n"
        f"**Joueurs actuels :** `{current_players}/{max_players}`\n\n"
        "Veuillez sélectionner un sport pour composer votre ticket :"
    )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Football", callback_data="sport_soccer")],
        [InlineKeyboardButton("🏀 Basketball", callback_data="sport_basketball")],
        [InlineKeyboardButton("🎾 Tennis", callback_data="sport_tennis")]
    ])
    await message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
