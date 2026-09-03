import os
import re
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)
import config
import database

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

telegram_app = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).build()
WAITING_CUSTOM_STAKE = 1

def _clean_number(raw: str) -> str:
    return re.sub(r"[^\d.]", "", raw)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.error("❌ Exception levée :", exc_info=context.error)

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚔️ Créer / Rejoindre (Duel & Arena)", callback_data="menu_duel")],
        [InlineKeyboardButton("📋 Mes Tickets", callback_data="my_tickets"), InlineKeyboardButton("🔴 Live", callback_data="live_all")],
        [InlineKeyboardButton("🏆 Classement", callback_data="menu_top")],
        [InlineKeyboardButton("💳 Mon Compte", callback_data="menu_account")],
    ])

def is_admin(user_id: int) -> bool:
    return user_id == config.ADMIN_TELEGRAM_ID

# ==========================================
# COMMANDES UTILISATEUR
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    database.cancel_expired_sessions()
    user = update.effective_user
    args = context.args

    if args and args[0].startswith("join_"):
        database.get_or_create_user(user.id, user.username or user.first_name)
        session_id = args[0][len("join_"):]
        await propose_join_duel(update.message, user.id, session_id, context)
        return

    db_user = database.get_or_create_user(user.id, user.username or user.first_name)
    
    # 📢 Notification admin : nouvel utilisateur
    try:
        admin_msg = (
            f"👤 **NOUVEL UTILISATEUR INSCRIT**\n\n"
            f"Nom : {db_user['username']}\n"
            f"🆔 Code Joueur : `{db_user['player_code']}`\n"
            f"Telegram ID : `{user.id}`"
        )
        await telegram_app.bot.send_message(chat_id=config.ADMIN_TELEGRAM_ID, text=admin_msg, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Erreur envoi notif admin inscription : {e}")
    
    text = (
        f"👋 Bienvenue **{user.first_name}** sur **Clashsport** !\n\n"
        f"💰 **Votre Solde :** `{db_user['coins_balance']}` Coins\n\n"
        "L'arène ultime de pronostics sportifs. Choisissez une option ci-dessous :"
    )
    await update.message.reply_text(text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")

async def user_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    database.cancel_expired_sessions()
    sessions = database.get_user_sessions(user_id)
    if not sessions:
        await update.message.reply_text("📭 Aucun ticket pour l'instant.", reply_markup=main_menu_keyboard())
        return
    await update.message.reply_text("📋 **Tes Tickets Clashsport**", reply_markup=_tickets_keyboard(sessions, user_id), parse_mode="Markdown")

async def user_live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    sessions = [s for s in database.get_user_sessions(user_id, history_limit=0) if s["status"] in ("WAITING", "IN_PROGRESS")]
    if not sessions:
        await update.message.reply_text("📭 Aucun duel en cours à suivre.", reply_markup=main_menu_keyboard())
        return
    await update.message.reply_text("🔴 Les matchs en direct sont accessibles dans chaque ticket.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="menu_main")]]))

async def propose_join_duel(message, user_id, session_id, context):
    session = database.get_session(session_id)
    if not session or session["status"] != "WAITING":
        await message.reply_text("❌ Ce salon n'est plus disponible ou est complet.")
        return
    if session["creator_id"] == user_id:
        await message.reply_text("⚠️ C'est votre propre salon, vous ne pouvez pas le rejoindre vous-même.")
        return

    s_type = "Duel 1v1" if session["type"] == "DUEL" else f"Arena ({session.get('max_participants')} joueurs)"
    text = (
        f"⚔️ **Invitation à un {s_type} !**\n\n"
        f"💰 **Mise requise :** `{session['gross_entry_fee']}` Coins\n"
        f"🎯 **Condition :** Composer un ticket autonome de **`{session['match_count']}` match(s)**.\n\n"
        "Voulez-vous accepter et composer votre ticket ?"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚔️ Accepter & Composer mon ticket", callback_data=f"start_join_{session_id}")],
        [InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")]
    ])
    await message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")

async def user_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    leaderboard = database.get_weekly_leaderboard()
    if not leaderboard:
        await update.message.reply_text("🏆 **Classement Hebdomadaire**\n\nAucune victoire enregistrée cette semaine.")
        return
    text = "🏆 **CLASSEMENT HEBDOMADAIRE**\n\n"
    for idx, item in enumerate(leaderboard, 1):
        text += f"{idx}. **{item['username']}** — {item['wins']} victoire(s) ({item['coins_won']} Coins)\n"
    await update.message.reply_text(text, parse_mode="Markdown")

# ==========================================
# COMMANDES ADMINISTRATEUR COMPLÈTES
# ==========================================

async def admin_resolve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    args = context.args
    if len(args) != 2 or args[1].upper() not in ("HOME", "DRAW", "AWAY", "CANCEL"):
        await update.message.reply_text("❌ Usage : /resolve [api_match_id] [HOME|DRAW|AWAY|CANCEL]")
        return

    api_match_id = _clean_number(args[0])
    result = args[1].upper()
    database.set_match_result(api_match_id, result)

    resolvable = database.find_resolvable_sessions(api_match_id)
    resolved_count = 0
    for session in resolvable:
        outcome = database.resolve_session(session["id"])
        if outcome:
            resolved_count += 1
            for notif in outcome.get("notifications", []):
                try: await context.bot.send_message(chat_id=notif["user_id"], text=notif["text"])
                except Exception: pass

            if outcome.get("is_draw_refund"):
                for s_score in outcome.get("scores", []):
                    try: await context.bot.send_message(chat_id=s_score["user_id"], text="🤝 Égalité parfaite ! Votre mise vous a été intégralement remboursée.")
                    except Exception: pass

    await update.message.reply_text(f"✅ Résultat enregistré. 🏁 {resolved_count} session(s) tranchée(s).")

async def admin_give(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("❌ Usage : /give [telegram_id_ou_player_code] [montant]")
        return
    
    target_raw, amount_str = args[0], _clean_number(args[1])
    try:
        amount = int(amount_str)
        if target_raw.isdigit() and len(target_raw) == 5:
            res = database.supabase.table("users").select("*").eq("player_code", target_raw).execute().data
            user = res[0] if res else None
        else:
            user = database.get_user_by_id(int(target_raw))

        if not user:
            await update.message.reply_text("❌ Utilisateur introuvable.")
            return

        new_bal = database.admin_give_coins(user["telegram_id"], amount)
        await update.message.reply_text(f"✅ `{amount}` Coins ajoutés à **{user['username']}**. Nouveau solde : `{new_bal}` Coins.")
    except Exception as e:
        await update.message.reply_text(f"❌ Erreur : {str(e)}")

async def admin_take(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("❌ Usage : /take [telegram_id_ou_player_code] [montant]")
        return
    try:
        target_raw, amount = args[0], int(_clean_number(args[1]))
        if target_raw.isdigit() and len(target_raw) == 5:
            res = database.supabase.table("users").select("*").eq("player_code", target_raw).execute().data
            user = res[0] if res else None
        else:
            user = database.get_user_by_id(int(target_raw))

        if not user:
            await update.message.reply_text("❌ Utilisateur introuvable.")
            return

        new_bal = database.admin_take_coins(user["telegram_id"], amount)
        await update.message.reply_text(f"✅ Remise effectuée pour **{user['username']}**. Nouveau solde : `{new_bal}` Coins.")
    except Exception as e:
        await update.message.reply_text(f"❌ Erreur : {str(e)}")

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    stats = database.get_detailed_stats()
    text = (
        "📊 **STATISTIQUES DE LA PLATEFORME**\n\n"
        f"👥 Joueurs inscrits : `{stats['total_users']}`\n"
        f"💰 Coins en circulation : `{stats['total_coins']}`\n"
        f"🎟️ Tickets créés : `{stats['total_tickets']}`\n"
        f"🟡 Salons en attente : `{stats['waiting_sessions']}`\n"
        f"🔵 Duels / Arenas en cours : `{stats['active_sessions']}`\n"
        f"🏁 Sessions terminées : `{stats['completed_sessions']}`\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def admin_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await update.message.reply_text("⏳ Synchronisation avec Odds-API en cours...")
    count, msg = await database.sync_matches_from_api_async()
    await update.message.reply_text(f"🔄 Résultat : {msg}")

async def admin_sweep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    database.cancel_expired_sessions()
    await update.message.reply_text("🧹 Nettoyage des sessions expirées (>24h) effectué avec succès.")

async def admin_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    msg_text = " ".join(context.args)
    if not msg_text:
        await update.message.reply_text("❌ Usage : /alert [votre message aux joueurs]")
        return
    
    users = database.get_all_users()
    sent = 0
    for u in users:
        try:
            await context.bot.send_message(chat_id=u["telegram_id"], text=f"📢 **ANNONCE CLASHSPORT**\n\n{msg_text}", parse_mode="Markdown")
            sent += 1
        except Exception: pass
    await update.message.reply_text(f"📢 Message diffusé à {sent}/{len(users)} joueurs.")

# ==========================================
# FLUX INTERACTIF UTILISATEUR
# ==========================================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "menu_main":
        db_user = database.get_or_create_user(user_id, query.from_user.username or query.from_user.first_name)
        text = f"👋 Bienvenue sur **Clashsport** !\n\n💰 **Votre Solde :** `{db_user['coins_balance']}` Coins"
        await query.edit_message_text(text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")
        return ConversationHandler.END

    elif data == "menu_duel":
        text = "⚔️ **MODE JEU — CLASHSPORT**\n\nChoisissez le format de votre partie :"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("👤 Créer Duel 1v1", callback_data="create_type_duel")],
            [InlineKeyboardButton("🏟️ Créer Arena (Multi)", callback_data="create_type_arena")],
            [InlineKeyboardButton("🔍 Liste des Duels Ouverts", callback_data="duel_list_public")],
            [InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")],
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
        return ConversationHandler.END

    elif data == "create_type_duel":
        context.user_data["draft_duel"] = {"mode": "create", "type": "DUEL", "max_participants": 2, "prize_mode": "TOP_1"}
        await prompt_stake(query)

    elif data == "create_type_arena":
        context.user_data["draft_duel"] = {"mode": "create", "type": "ARENA"}
        text = "🏟️ **ARENA : Combien de participants au maximum ?**"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("3 Joueurs", callback_data="arena_p_3"), InlineKeyboardButton("5 Joueurs", callback_data="arena_p_5")],
            [InlineKeyboardButton("10 Joueurs", callback_data="arena_p_10"), InlineKeyboardButton("🔙 Retour", callback_data="menu_duel")]
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")

    elif data.startswith("arena_p_"):
        count = int(data.split("_")[2])
        context.user_data["draft_duel"]["max_participants"] = count
        text = "🏆 **ARENA : Comment répartir la cagnotte ?**"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Winner Takes All (Top 1)", callback_data="arena_prize_TOP_1")],
            [InlineKeyboardButton("Top 3 (50% / 38% / 12%)", callback_data="arena_prize_TOP_3")],
            [InlineKeyboardButton("🔙 Annuler", callback_data="menu_duel")]
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")

    elif data.startswith("arena_prize_"):
        mode = data.replace("arena_prize_", "")
        context.user_data["draft_duel"]["prize_mode"] = mode
        await prompt_stake(query)

    elif data == "duel_create_stake":
        await prompt_stake(query)

    elif data.startswith("stake_") and data != "stake_custom":
        stake = int(_clean_number(data.split("_")[1]))
        await init_draft_duel(query, context, user_id, stake)

    elif data == "duel_list_public":
        duels = database.get_open_duels(exclude_creator_id=user_id)
        if not duels:
            await query.edit_message_text(
                "📭 Aucun salon ouvert pour le moment. Créez le vôtre !",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")]]),
            )
            return ConversationHandler.END
        keyboard = [[InlineKeyboardButton(f"{'🏟️ Arena' if d['type'] == 'ARENA' else '⚔️ 1v1'} — {d['gross_entry_fee']} Coins ({d['match_count']} matchs)", callback_data=f"start_join_{d['id']}")] for d in duels]
        keyboard.append([InlineKeyboardButton("🔙 Retour", callback_data="menu_duel")])
        await query.edit_message_text("🔍 **Salons ouverts sur Clashsport :**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("start_join_"):
        session_id = data[len("start_join_"):]
        session = database.get_session(session_id)
        if not session or session["status"] != "WAITING":
            await query.edit_message_text("❌ Ce salon n'est plus disponible.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="menu_main")]]))
            return ConversationHandler.END

        db_user = database.get_or_create_user(user_id, "")
        if db_user["coins_balance"] < session["gross_entry_fee"]:
            await query.edit_message_text(f"❌ **Solde insuffisant !**\nIl vous faut `{session['gross_entry_fee']}` Coins.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")]]))
            return ConversationHandler.END

        context.user_data["draft_duel"] = {"mode": "join", "session_id": session_id, "stake": session["gross_entry_fee"], "match_count": session["match_count"], "selected_matches": {}}
        await show_sport_selection_menu(query, context)

    elif data in ["select_sports", "back_to_sports"]:
        await show_sport_selection_menu(query, context)
    elif data.startswith("sport_"):
        await show_leagues_for_sport(query, context, data.split("_")[1])
    elif data.startswith("league_"):
        await show_matches_for_league(query, context, data[len("league_"):])
    elif data.startswith("pick_"):
        _, m_id, pick = data.split("_")
        draft = context.user_data.get("draft_duel", {})
        selected = draft.setdefault("selected_matches", {})
        
        match_obj = next((m for m in database.get_active_matches() if str(m["api_match_id"]) == m_id), None)
        if match_obj:
            if m_id in selected and selected[m_id]["pick"] == pick:
                del selected[m_id]
            else:
                odds = match_obj.get("odds_home") if pick == "HOME" else (match_obj.get("odds_away") if pick == "AWAY" else match_obj.get("odds_draw"))
                selected[m_id] = {"match": match_obj, "pick": pick, "odds": float(odds) if odds else 1.0}
        await show_matches_for_league(query, context, draft.get("current_league"))

    elif data == "review_ticket":
        await show_ticket_review(query, context)
    elif data == "confirm_duel_creation":
        await confirm_duel_final(query, context, user_id)
    elif data == "cancel_creation":
        context.user_data.pop("draft_duel", None)
        await query.edit_message_text("❌ Création annulée.", reply_markup=main_menu_keyboard())

    elif data == "my_tickets":
        database.cancel_expired_sessions()
        sessions = database.get_user_sessions(user_id)
        if not sessions:
            await query.edit_message_text("📭 Aucun ticket pour l'instant.", reply_markup=main_menu_keyboard())
            return ConversationHandler.END
        await query.edit_message_text("📋 **Tes Tickets Clashsport**", reply_markup=_tickets_keyboard(sessions, user_id), parse_mode="Markdown")

    elif data.startswith("ticket_"):
        parts = data.split("_")
        session_id = parts[1]
        tab = parts[2] if len(parts) > 2 else "mine"
        await show_ticket_detail(query, context, session_id, tab)

    elif data == "menu_top":
        leaderboard = database.get_weekly_leaderboard()
        text = "🏆 **CLASSEMENT HEBDOMADAIRE**\n\n"
        if not leaderboard: text += "Aucune victoire enregistrée cette semaine."
        else:
            for idx, item in enumerate(leaderboard, 1):
                text += f"{idx}. **{item['username']}** — {item['wins']} victoires ({item['coins_won']} Coins)\n"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="menu_main")]]), parse_mode="Markdown")

    elif data == "live_all":
        sessions = [s for s in database.get_user_sessions(user_id, history_limit=0) if s["status"] in ("WAITING", "IN_PROGRESS")]
        if not sessions:
            await query.edit_message_text("📭 Aucun duel en cours à suivre.", reply_markup=main_menu_keyboard())
            return ConversationHandler.END
        await query.edit_message_text("🔴 Les matchs en direct sont accessibles dans chaque ticket.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="menu_main")]]))

    elif data == "menu_account":
        db_user = database.get_or_create_user(user_id, query.from_user.username or query.from_user.first_name)
        admin_username = "clashsportadmin"  # À remplacer par le vrai username de l'admin
        recharge_link = f"https://t.me/{admin_username}?text=Recharge%20pour%20mon%20ID%20:%20{db_user['player_code']}"
        
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

async def prompt_stake(query):
    text = "💰 **Sélectionnez le montant de la mise :**"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("100 Coins", callback_data="stake_100"), InlineKeyboardButton("500 Coins", callback_data="stake_500")],
        [InlineKeyboardButton("1 000 Coins", callback_data="stake_1000")],
        [InlineKeyboardButton("✏️ Montant personnalisé", callback_data="stake_custom")],
        [InlineKeyboardButton("🔙 Annuler", callback_data="menu_duel")]
    ])
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")

async def ask_custom_stake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "✏️ **Entrez le montant (entier) :**",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Annuler", callback_data="duel_create_stake")]]),
        parse_mode="Markdown"
    )
    return WAITING_CUSTOM_STAKE

async def receive_custom_stake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        stake = int(_clean_number(text))
        if stake <= 0: raise ValueError()
        dummy_msg = await update.message.reply_text("⏳ Validation...")
        class DummyQuery:
            def __init__(self, message): self.message = message
            async def edit_message_text(self, t, reply_markup=None, parse_mode=None):
                return await self.message.edit_text(t, reply_markup=reply_markup, parse_mode=parse_mode)
        await init_draft_duel(DummyQuery(dummy_msg), context, update.effective_user.id, stake)
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ Montant invalide.")
        return WAITING_CUSTOM_STAKE

async def cancel_custom_stake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await button_handler(update, context)
    return ConversationHandler.END

stake_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_custom_stake, pattern="^stake_custom$")],
    states={WAITING_CUSTOM_STAKE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_custom_stake)]},
    fallbacks=[CallbackQueryHandler(cancel_custom_stake, pattern="^duel_create_stake$")]
)

async def init_draft_duel(query, context, user_id, stake):
    db_user = database.get_or_create_user(user_id, "")
    if db_user["coins_balance"] < stake:
        await query.edit_message_text(f"❌ **Solde insuffisant !**\nMise requise : `{stake}` Coins.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="menu_main")]]))
        return
    draft = context.user_data.get("draft_duel", {})
    draft["stake"] = stake
    draft.setdefault("selected_matches", {})
    await show_sport_selection_menu(query, context)

async def show_sport_selection_menu(query, context):
    draft = context.user_data.get("draft_duel", {})
    selected = draft.get("selected_matches", {})
    text = f"🏟️ **Ticket Clashsport**\n💰 Mise : `{draft.get('stake')}` Coins\n🎯 Matchs : `{len(selected)}`\n\nChoisissez une discipline :"
    keyboard = [
        [InlineKeyboardButton("⚽ Football", callback_data="sport_soccer"), InlineKeyboardButton("🏀 Basketball", callback_data="sport_basketball")],
        [InlineKeyboardButton("🎾 Tennis", callback_data="sport_tennis")],
    ]
    if len(selected) >= 1:
        keyboard.append([InlineKeyboardButton(f"✅ Voir / Valider ({len(selected)} match(s))", callback_data="review_ticket")])
    keyboard.append([InlineKeyboardButton("❌ Annuler", callback_data="cancel_creation")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def show_leagues_for_sport(query, context, sport):
    draft = context.user_data.get("draft_duel", {})
    draft["current_sport"] = sport
    matches = database.get_matches_by_sport(sport)
    leagues = sorted(list(set(m.get("league", "Général") for m in matches if m.get("league"))))
    selected = draft.get("selected_matches", {})
    
    keyboard = []
    if len(selected) >= 1: keyboard.append([InlineKeyboardButton(f"✅ Voir Récapitulatif", callback_data="review_ticket")])
    keyboard.append([InlineKeyboardButton("🔙 Changer de Sport", callback_data="select_sports")])

    for league in leagues:
        keyboard.append([InlineKeyboardButton(f"🏅 {league}", callback_data=f"league_{league}")])

    keyboard.append([InlineKeyboardButton("❌ Annuler", callback_data="cancel_creation")])
    
    if not leagues:
        text = f"🏆 **Championnats — {sport.upper()}**\n\n⏳ Aucun match disponible pour le moment."
    else:
        text = f"🏆 **Championnats — {sport.upper()}**\n\nSélectionnez une compétition :"
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def show_matches_for_league(query, context, league):
    draft = context.user_data.get("draft_duel", {})
    sport = draft.get("current_sport", "soccer")
    draft["current_league"] = league
    selected = draft.get("selected_matches", {})
    league_matches = [m for m in database.get_matches_by_sport(sport) if m.get("league") == league]
    
    keyboard = []
    if len(selected) >= 1: keyboard.append([InlineKeyboardButton("✅ Voir Récapitulatif", callback_data="review_ticket")])
    keyboard.append([InlineKeyboardButton("🔙 Retour", callback_data=f"sport_{sport}")])

    for m in league_matches:
        m_id = str(m["api_match_id"])
        current_pick = selected.get(m_id, {}).get("pick")
        btn_h = f"1 ({m.get('odds_home', 1.0)})" + (" ✅" if current_pick == "HOME" else "")
        btn_a = f"2 ({m.get('odds_away', 1.0)})" + (" ✅" if current_pick == "AWAY" else "")
        
        keyboard.append([InlineKeyboardButton(f"⚽ {m['home_team']} vs {m['away_team']}", callback_data="ignore")])
        if m.get("odds_draw"):
            btn_d = f"N ({m.get('odds_draw')})" + (" ✅" if current_pick == "DRAW" else "")
            keyboard.append([InlineKeyboardButton(btn_h, callback_data=f"pick_{m_id}_HOME"), InlineKeyboardButton(btn_d, callback_data=f"pick_{m_id}_DRAW"), InlineKeyboardButton(btn_a, callback_data=f"pick_{m_id}_AWAY")])
        else:
            keyboard.append([InlineKeyboardButton(btn_h, callback_data=f"pick_{m_id}_HOME"), InlineKeyboardButton(btn_a, callback_data=f"pick_{m_id}_AWAY")])

    keyboard.append([InlineKeyboardButton("❌ Annuler", callback_data="cancel_creation")])
    await query.edit_message_text(f"🏟️ **{league}**\n📊 Panier : **{len(selected)}**\n", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def show_ticket_review(query, context):
    draft = context.user_data.get("draft_duel", {})
    selected = draft.get("selected_matches", {})
    stake = draft.get("stake", 100)
    mode = draft.get("mode", "create")
    req_match = draft.get("match_count")
    
    text = f"📋 **Récapitulatif Ticket**\n💰 Mise : `{stake}` Coins\n🎯 Matchs : `{len(selected)}`\n\n"
    for m_id, item in selected.items():
        m = item["match"]
        p_label = m["home_team"] if item["pick"] == "HOME" else (m["away_team"] if item["pick"] == "AWAY" else "Nul")
        text += f"• {m['home_team']} vs {m['away_team']} ➔ **{p_label}** (Cote: {item['odds']})\n"

    keyboard = []
    if mode == "join" and len(selected) != req_match:
        text += f"\n⚠️ *Vous devez sélectionner EXACTEMENT {req_match} matchs pour rejoindre (Actuellement {len(selected)}).* "
    elif len(selected) >= 1:
        keyboard.append([InlineKeyboardButton("🚀 Valider le Ticket", callback_data="confirm_duel_creation")])
        
    keyboard.append([InlineKeyboardButton("➕ Ajouter / Modifier", callback_data="back_to_sports")])
    keyboard.append([InlineKeyboardButton("❌ Annuler", callback_data="cancel_creation")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def confirm_duel_final(query, context, user_id):
    draft = context.user_data.get("draft_duel", {})
    mode, stake = draft.get("mode", "create"), draft.get("stake", 100)
    predictions = [{"match_id": m_id, "pick": v["pick"], "odds": v["odds"]} for m_id, v in draft.get("selected_matches", {}).items()]

    if mode == "join":
        session, msg = database.join_session(draft["session_id"], user_id, predictions)
        if not session:
            await query.edit_message_text(f"❌ Erreur : {msg}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="menu_main")]]))
            return
        db_user = database.get_user_by_id(user_id)
        context.user_data.pop("draft_duel", None)
        text = (
            "⚔️ **TICKET VALIDÉ !** ⚔️\n\n"
            f"💰 Ton nouveau solde : `{db_user['coins_balance']}` Coins\n\n"
            "Le défi est accepté. Tu peux suivre l'avancée dans Mes Tickets."
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Retour au Menu Principal", callback_data="menu_main")]])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
        
        # 📢 Notification aux participants : lancement du duel/arène
        session = database.get_session(draft["session_id"])
        await _notify_session_launch(session, user_id)
    else:
        s_type = draft.get("type", "DUEL")
        max_p = draft.get("max_participants", 2)
        p_mode = draft.get("prize_mode", "TOP_1")
        session, msg = database.create_session(user_id, s_type, stake, len(predictions), max_p, p_mode, predictions)
        
        if not session:
            await query.edit_message_text(f"❌ Erreur : {msg}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="menu_main")]]))
            return
            
        context.user_data.pop("draft_duel", None)
        db_user = database.get_user_by_id(user_id)
        bot_username = (await telegram_app.bot.get_me()).username
        share_link = f"https://t.me/{bot_username}?start=join_{session['id']}"
        
        text = (
            "🔥 **SALON CRÉÉ !** 🔥\n\n"
            f"💰 Mise : `{stake}` Coins | Solde restant : `{db_user['coins_balance']}`\n"
            f"🎯 Ticket verrouillé : `{len(predictions)}` matchs\n\n"
            f"🔗 **Lien d'invitation à partager :**\n`{share_link}`"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Partager", url=f"https://t.me/share/url?url={share_link}&text=Rejoins-moi%20sur%20Clashsport%20!")],
            [InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")]
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")

async def _notify_session_launch(session, new_joiner_id):
    """Notifie tous les participants qu'une session a démarré."""
    if not session or session["status"] != "IN_PROGRESS":
        return
    
    tickets = database.get_tickets_for_session(session["id"])
    user_ids = set(t["user_id"] for t in tickets)
    
    s_type = "Duel 1v1" if session["type"] == "DUEL" else f"Arena ({session.get('max_participants')} joueurs)"
    msg = (
        f"🚀 **{s_type} LANCÉ !** 🚀\n\n"
        f"💰 Mise : `{session['gross_entry_fee']}` Coins\n"
        f"🎯 Matchs : `{session['match_count']}`\n\n"
        "Tous les tickets sont verrouillés. Le compte à rebours a commencé !"
    )
    
    for user_id in user_ids:
        try:
            await telegram_app.bot.send_message(chat_id=user_id, text=msg, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Erreur notification lancement session à {user_id} : {e}")

def _tickets_keyboard(sessions, user_id):
    keyboard = []
    for s in sessions:
        if s["status"] == "WAITING": icon = "⚪"
        elif s["status"] == "IN_PROGRESS": icon = "🟢"
        else: icon = "🔵"
        
        t_label = "Arena" if s["type"] == "ARENA" else "Duel"
        keyboard.append([InlineKeyboardButton(f"{icon} {t_label} {s['gross_entry_fee']} C ({s['match_count']}m)", callback_data=f"ticket_{s['id']}_mine")])
    keyboard.append([InlineKeyboardButton("⬅️ Retour au Menu Principal", callback_data="menu_main")])
    return InlineKeyboardMarkup(keyboard)

async def show_ticket_detail(query, context, session_id, tab):
    user_id = query.from_user.id
    session = database.get_session(session_id)
    if not session:
        await query.edit_message_text("❌ Session introuvable.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Mes Tickets", callback_data="my_tickets")]]))
        return

    tickets = database.get_tickets_for_session(session_id)
    my_ticket = next((t for t in tickets if t["user_id"] == user_id), None)
    
    status_icon = {"WAITING": "⚪", "IN_PROGRESS": "🟢", "COMPLETED": "🔵"}.get(session['status'], "⚪")
    text = f"⚔️ **Session {session['type']} — {session['gross_entry_fee']} Coins**\n{status_icon} Statut: {session['status']}\n\n"
    
    if tab == "mine":
        if my_ticket:
            match_ids = [str(p["match_id"]) for p in my_ticket["predictions"]]
            matches = {str(m["api_match_id"]): m for m in database.get_matches_by_ids(match_ids)}
            my_correct, total = 0, len(my_ticket["predictions"])
            for p in my_ticket["predictions"]:
                m = matches.get(str(p["match_id"]))
                if not m: continue
                icon = "⏳"
                if m.get("result"):
                    if m["result"] == "CANCEL": icon = "🚫 (Annulé)"
                    elif m["result"] == p["pick"]:
                        icon = "👍"
                        my_correct += 1
                    else: icon = "😢"
                text += f"{icon} {m['home_team']} vs {m['away_team']}\n"
            text += f"\n🟢 **Mon Score : {my_correct}/{total}**"
            
            # Afficher le verdict final si complété
            if session['status'] == 'COMPLETED':
                winner_id = session.get('winner_id')
                if winner_id == user_id:
                    text += "\n\n✨ **VICTOIRE !** ✨"
                elif winner_id is None:
                    text += "\n\n🤝 **ÉGALITÉ PARFAITE**"
                else:
                    text += "\n\n😔 Vous avez perdu."
        else: 
            text += "Vous n'avez pas de ticket ici."
    
    elif tab == "opp":
        text += "👥 **Progression des Adversaires :**\n\n"
        for t in tickets:
            if t["user_id"] == user_id: continue
            opp_user = database.get_user_by_id(t["user_id"])
            match_ids = [str(p["match_id"]) for p in t["predictions"]]
            matches = {str(m["api_match_id"]): m for m in database.get_matches_by_ids(match_ids)}
            
            finished, won, total = 0, 0, len(t["predictions"])
            for p in t["predictions"]:
                m = matches.get(str(p["match_id"]))
                if m and m.get("result"):
                    if m["result"] != "CANCEL":
                        finished += 1
                        if m["result"] == p["pick"]: won += 1
            
            text += f"👤 {opp_user.get('username', 'Joueur')} : `{finished}/{total}` — {won}G / {finished - won}P\n"
        
        if len(tickets) <= 1: text += "⏳ *En attente d'adversaires...*"

    btn_mine = InlineKeyboardButton("📍 Mon Ticket" + (" 🔹" if tab == "mine" else ""), callback_data=f"ticket_{session_id}_mine")
    btn_opp = InlineKeyboardButton("👥 Adversaires" + (" 🔹" if tab == "opp" else ""), callback_data=f"ticket_{session_id}_opp")
    
    keyboard = [[btn_mine, btn_opp]]
    keyboard.append([InlineKeyboardButton("⬅️ Retour Mes Tickets", callback_data="my_tickets")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# Enregistrement des Handlers Utilisateurs & Admin
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("top", user_top))
telegram_app.add_handler(CommandHandler("tickets", user_tickets))
telegram_app.add_handler(CommandHandler("live", user_live))

telegram_app.add_handler(CommandHandler("resolve", admin_resolve))
telegram_app.add_handler(CommandHandler("give", admin_give))
telegram_app.add_handler(CommandHandler("take", admin_take))
telegram_app.add_handler(CommandHandler("stats", admin_stats))
telegram_app.add_handler(CommandHandler("sync", admin_sync))
telegram_app.add_handler(CommandHandler("sweep", admin_sweep))
telegram_app.add_handler(CommandHandler("alert", admin_alert))

telegram_app.add_handler(stake_conv_handler)
telegram_app.add_handler(CallbackQueryHandler(button_handler))
telegram_app.add_error_handler(error_handler)

RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")

@asynccontextmanager
async def lifespan(app: FastAPI):
    await telegram_app.initialize()
    await telegram_app.start()
    if RENDER_EXTERNAL_URL:
        await telegram_app.bot.set_webhook(url=f"{RENDER_EXTERNAL_URL}/webhook", drop_pending_updates=True)
    yield
    await telegram_app.stop()
    await telegram_app.shutdown()

app = FastAPI(lifespan=lifespan)

@app.post("/webhook")
async def process_webhook(request: Request):
    req_data = await request.json()
    update = Update.de_json(req_data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"status": "ok"}

@app.get("/cron/sweep")
async def cron_sweep_endpoint():
    database.cancel_expired_sessions()
    return {"status": "success", "message": "Expired sessions cleaned"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("bot:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
