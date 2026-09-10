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
            await _ask_match_count(query, stype)
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
        await _ask_match_count(query, "ARENA")

    elif data.startswith("count_"):
        cnt = int(data.split("_")[1])
        update_draft_settings(user_id, {"match_count": cnt})
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
        toggle_cart_item(user_id, mid, pick, float(odds))
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
        from bot.handlers.tickets_view import user_tickets
        # Redirection vers la vue des tickets sous forme de message classique
        await query.message.delete()
        await user_tickets(update, context)

    elif data == "live_all":
        from bot.handlers.tickets_view import user_live
        await query.message.delete()
        await user_live(update, context)


async def _ask_match_count(query, stype):
    text = f"🎯 **Création {stype}**\n\nCombien de matchs voulez-vous mettre sur votre ticket ?"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("1 Match", callback_data="count_1"), InlineKeyboardButton("2 Matchs", callback_data="count_2")],
        [InlineKeyboardButton("3 Matchs", callback_data="count_3"), InlineKeyboardButton("5 Matchs", callback_data="count_5")],
        [InlineKeyboardButton("⬅️ Retour", callback_data="menu_duel")]
    ])
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")

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
    matches = get_matches_by_sport(sport)
    if not matches:
        await query.answer("Aucun match disponible pour ce sport aujourd'hui.", show_alert=True)
        return
    
    user_id = query.from_user.id
    draft = get_draft_settings(user_id)
    cart = {str(item["match_id"]): item["pick"] for item in get_cart(user_id)}
    target_count = draft.get("match_count", 1)

    text = f"⚽ **Matchs disponibles ({sport})**\nSélectionnez vos pronostics ({len(cart)}/{target_count}) :\n\n"
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

    if len(cart) == target_count:
        keyboard.append([InlineKeyboardButton("🚀 Valider mon Ticket", callback_data="validate_ticket")])
    keyboard.append([InlineKeyboardButton("⬅️ Changer de sport", callback_data="sport_Soccer")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def _refresh_match_selection_view(query, user_id):
    draft = get_draft_settings(user_id)
    cart = get_cart(user_id)
    target_count = draft.get("match_count", 1)
    
    if len(cart) > 0:
        # On recharge les matchs du premier sport trouvé dans le panier ou par défaut
        matches = get_active_matches()
        if matches:
            await _show_matches_for_sport(query, "Soccer")
            return
    await _show_sports_selection(query)

async def _process_ticket_creation(query, user_id, context):
    draft = get_draft_settings(user_id)
    cart = get_cart(user_id)
    
    if len(cart) != draft.get("match_count", 1):
        await query.answer("Nombre de pronostics invalide !", show_alert=True)
        return

    predictions = [{"match_id": item["match_id"], "pick": item["pick"], "odds": item["odds"]} for item in cart]
    
    session, msg = create_session(
        creator_id=user_id,
        session_type=draft.get("session_type", "DUEL"),
        gross_fee=draft.get("gross_fee", 100),
        match_count=draft.get("match_count", 1),
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
