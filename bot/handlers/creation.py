import math
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import config
from database.connection import supabase
from database.cart import get_draft_settings, update_draft_settings, clear_draft, toggle_cart_item, get_cart
from database.sessions import get_active_matches, get_matches_by_sport, get_matches_by_ids, create_session, join_session, get_session
from database.users import get_user_by_id
from bot.ui import main_menu_keyboard
from bot.handlers.tickets_view import show_ticket_detail

async def handle_creation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "menu_main":
        await query.edit_message_text("🏠 **Menu Principal**", reply_markup=main_menu_keyboard(), parse_mode="Markdown")

    elif data == "menu_duel":
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
            # On passe directement au choix de la mise (le nombre de matchs se déduira du panier)
            await _ask_entry_fee(query)
        else:
            text = "🏟️ **Configuration Arena**\n\nCombien de joueurs maximum pour ce salon ?"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("4 Joueurs", callback_data="arena_max_4"), InlineKeyboardButton("8 Joueurs", callback_data="arena_max_8")],
                [InlineKeyboardButton("⬅️ Retour", callback_data="menu_duel")]
            ])
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")

    elif data.startswith("arena_max_"):
        mx = int(data.split("_")[2])
        update_draft_settings(user_id, {"max_participants": mx})
        text = "🏆 **Mode de Distribution des Prix (Arena)**"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🥇 Vainqueur unique (100%)", callback_data="arena_prize_WINNER")],
            [InlineKeyboardButton("🥉 Top 3 (50% / 38% / 12%)", callback_data="arena_prize_TOP3")],
            [InlineKeyboardButton("⬅️ Retour", callback_data="menu_duel")]
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")

    elif data.startswith("arena_prize_"):
        mode = "TOP_3" if data.split("_")[2] == "TOP3" else "WINNER_TAKES_ALL"
        update_draft_settings(user_id, {"prize_mode": mode})
        # Après configuration Arena, on passe directement au choix de la mise
        await _ask_entry_fee(query)

    elif data.startswith("fee_"):
        fee = int(data.split("_")[1])
        update_draft_settings(user_id, {"gross_fee": fee})
        await _show_sports_selection(query)

    elif data.startswith("sport_"):
        sport = data.split("_")[1]
        await _show_matches_for_sport(query, sport)

    elif data.startswith("bet_"):
        _, mid, pick, odds = data.split("_")
        draft = get_draft_settings(user_id)
        joining_sid = draft.get("joining_session_id")
        
        # Si on rejoint un salon existant, on respecte STRICTEMENT le nombre de matchs requis par le créateur
        if joining_sid:
            session = get_session(joining_sid)
            target_count = session.get("match_count", 1) if session else 1
            success = toggle_cart_item(user_id, mid, pick, float(odds), max_count=target_count)
            if not success:
                await query.answer(f"⚠️ Ce salon exige exactement {target_count} pronostic(s).", show_alert=True)
                return
        else:
            # Si on crée, pas de limite stricte en amont, on ajoute/retire librement du panier
            toggle_cart_item(user_id, mid, pick, float(odds), max_count=None)
            
        await _refresh_match_selection_view(query, user_id)

    elif data == "validate_ticket":
        await _process_ticket_creation(query, user_id, context)

    elif data == "list_public":
        await _show_public_duels(query, user_id)

    elif data.startswith("join_pub_"):
        sid = data.split("_")[2]
        await _prompt_join_session(query, user_id, sid)

    elif data.startswith("ticket_"):
        parts = data.split("_")
        sid, tab = parts[1], parts[2]
        await show_ticket_detail(query, sid, tab)

    elif data == "my_tickets":
        from database.sessions import cancel_expired_sessions, get_user_sessions
        from bot.handlers.tickets_view import _tickets_keyboard
        cancel_expired_sessions()
        sessions = get_user_sessions(user_id)
        if not sessions:
            await query.edit_message_text("📭 Aucun ticket pour l'instant.", reply_markup=main_menu_keyboard(), parse_mode="Markdown")
        else:
            await query.edit_message_text("📋 **Tes Tickets Clashsport**", reply_markup=_tickets_keyboard(sessions), parse_mode="Markdown")

    elif data == "live_all":
        from database.sessions import get_user_sessions
        sessions = [s for s in get_user_sessions(user_id, history_limit=0) if s["status"] in ("WAITING", "IN_PROGRESS")]
        if not sessions:
            await query.edit_message_text("📭 Aucun duel en cours à suivre.", reply_markup=main_menu_keyboard(), parse_mode="Markdown")
        else:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="menu_main")]])
            await query.edit_message_text("🔴 Les matchs en direct sont accessibles dans chaque ticket.", reply_markup=keyboard, parse_mode="Markdown")


async def _ask_entry_fee(query):
    text = "💰 **Mise d'entrée (Coins)**\n\nChoisissez le montant de la mise pour ce salon :"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("100 Coins", callback_data="fee_100"), InlineKeyboardButton("500 Coins", callback_data="fee_500")],
        [InlineKeyboardButton("1000 Coins", callback_data="fee_1000"), InlineKeyboardButton("5000 Coins", callback_data="fee_5000")],
        [InlineKeyboardButton("⬅️ Retour", callback_data="menu_duel")]
    ])
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def _show_sports_selection(query):
    text = "⚽ **Sélection des Matchs**\n\nChoisissez un sport pour composer votre ticket :"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Football", callback_data="sport_Soccer"), InlineKeyboardButton("🏀 Basketball", callback_data="sport_Basketball")],
        [InlineKeyboardButton("🎾 Tennis", callback_data="sport_Tennis")],
        [InlineKeyboardButton("⬅️ Retour", callback_data="menu_duel")]
    ])
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def _show_matches_for_sport(query, sport):
    user_id = query.from_user.id
    update_draft_settings(user_id, {"current_sport": sport})
    matches = get_matches_by_sport(sport)
    if not matches:
        await query.answer("Aucun match disponible pour ce sport aujourd'hui.", show_alert=True)
        return
    
    draft = get_draft_settings(user_id)
    cart = {str(item["match_id"]): item["pick"] for item in get_cart(user_id)}
    
    joining_sid = draft.get("joining_session_id")
    if joining_sid:
        session = get_session(joining_sid)
        target_count = session.get("match_count", 1) if session else 1
        header_text = f"⚽ **Matchs disponibles ({sport})**\nSélectionnez vos pronostics ({len(cart)}/{target_count}) :\n\n"
        can_validate = (len(cart) == target_count)
    else:
        # En mode création, le nombre de matchs s'adapte dynamiquement au panier (minimum 1 match)
        header_text = f"⚽ **Matchs disponibles ({sport})**\nMatchs sélectionnés : `{len(cart)}` (Cliquez sur Valider quand vous avez fini)\n\n"
        can_validate = (len(cart) >= 1)

    text = header_text
    keyboard = []
    
    for m in matches:
        mid = str(m["api_match_id"])
        home = str(m["home_team"])
        away = str(m["away_team"])
        odds_h = float(m.get("odds_home", 1.9))
        odds_d = float(m.get("odds_draw", 3.0))
        odds_a = float(m.get("odds_away", 1.9))

        text += f"🔹 <b>{home} vs {away}</b>\n"
        
        row = []
        for label, pick, odd in [("1", "HOME", odds_h), ("N", "DRAW", odds_d), ("2", "AWAY", odds_a)]:
            if odd <= 1.0: continue
            is_selected = (cart.get(mid) == pick)
            prefix = "✅ " if is_selected else ""
            row.append(InlineKeyboardButton(f"{prefix}{label} ({odd})", callback_data=f"bet_{mid}_{pick}_{odd}"))
        keyboard.append(row)

    if can_validate:
        keyboard.append([InlineKeyboardButton("🚀 Valider mon Ticket", callback_data="validate_ticket")])
    keyboard.append([InlineKeyboardButton("⬅️ Changer de sport", callback_data="sport_Soccer")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def _refresh_match_selection_view(query, user_id):
    draft = get_draft_settings(user_id)
    sport = draft.get("current_sport", "Soccer")
    await _show_matches_for_sport(query, sport)


async def _process_ticket_creation(query, user_id, context):
    draft = get_draft_settings(user_id)
    cart = get_cart(user_id)
    joining_session_id = draft.get("joining_session_id")

    if not cart:
        await query.answer("Votre panier est vide !", show_alert=True)
        return

    predictions = [{"match_id": item["match_id"], "pick": item["pick"], "odds": item["odds"]} for item in cart]
    match_count = len(predictions) # Le nombre de matchs est calé dynamiquement sur le panier !

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

    # Création du salon avec le match_count calculé à partir du panier
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
        f"🔗 **Lien d'invitation à partager :**\n`{invite_link}`"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Voir mon ticket", callback_data=f"ticket_{sid}_mine")],
        [InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")]
    ])
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def _show_public_duels(query, user_id):
    res = supabase.table("sessions").select("*").eq("status", "WAITING").neq("creator_id", user_id).execute().data
    if not res:
        await query.answer("Aucun salon public disponible pour le moment.", show_alert=True)
        return

    text = "🔍 **Salons Publics Disponibles**\nChoisissez un salon à rejoindre :"
    keyboard = []
    for s in res:
        creator = get_user_by_id(s["creator_id"]) or {}
        uname = creator.get("username", "Joueur")
        keyboard.append([InlineKeyboardButton(f"⚔️ {uname} — {s['gross_entry_fee']} Coins ({s['match_count']}m)", callback_data=f"join_pub_{s['id']}")])
    keyboard.append([InlineKeyboardButton("⬅️ Retour", callback_data="menu_duel")])
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def _prompt_join_session(query, user_id, session_id):
    session = get_session(session_id)
    if not session:
        await query.answer("Ce salon n'existe plus.", show_alert=True)
        return
    
    update_draft_settings(user_id, {"joining_session_id": session_id, "match_count": session["match_count"]})
    await _show_sports_selection(query)


async def propose_join_duel(message, user_id, session_id, context):
    session = get_session(session_id)
    if not session or session["status"] != "WAITING":
        await message.reply_text("❌ Ce salon n'est plus disponible ou a déjà débuté.")
        return
    
    update_draft_settings(user_id, {"joining_session_id": session_id, "match_count": session["match_count"]})
    text = f"⚔️ Vous rejoignez un salon de `{session['gross_entry_fee']}` Coins !\nVeuillez sélectionner vos `{session['match_count']}` pronostics :"
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Choisir des matchs (Football)", callback_data="sport_Soccer")],
        [InlineKeyboardButton("🏀 Basketball", callback_data="sport_Basketball")]
    ])
    await message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
