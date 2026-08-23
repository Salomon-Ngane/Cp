import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
import config
import database

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

telegram_app = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).build()
USER_TICKETS = {}

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.error("❌ Exception levée :", exc_info=context.error)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    
    referred_by = None
    duel_to_join = None
    
    if args:
        if args[0].startswith("duel_"):
            duel_to_join = args[0].replace("duel_", "")
        elif args[0].isdigit():
            referred_by = int(args[0])
            
    db_user = database.get_or_create_user(user.id, user.username or user.first_name, referred_by)
    
    if duel_to_join:
        session = database.get_duel_session(duel_to_join)
        if session and session["status"] == "WAITING":
            if session["creator_id"] == user.id:
                await update.message.reply_text("ℹ️ Vous êtes le créateur de ce duel en attente.")
            else:
                return await prompt_join_duel(update.message.reply_text, user.id, session)
        else:
            await update.message.reply_text("❌ Ce duel n'existe plus ou est déjà en cours.")

    await send_main_menu(update.message.reply_text, db_user)

async def send_main_menu(reply_method, db_user):
    text = (
        f"👋 Bienvenue **{db_user['username']}** dans le Bot Duel Sports !\n\n"
        f"💰 **Votre Solde :** `{db_user['coins_balance']}` Coins\n\n"
        "Choisissez un mode de jeu :"
    )
    keyboard = [
        [InlineKeyboardButton("⚔️ Créer / Rejoindre un Duel", callback_data="menu_duel")],
        [InlineKeyboardButton("🏟️ Mode Arena (Top 3)", callback_data="wip_arena")],
        [InlineKeyboardButton("🏆 Ligue Quotidienne", callback_data="wip_ligue")],
        [InlineKeyboardButton("💳 Mon Compte / Badges", callback_data="wip_account")]
    ]
    await reply_method(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def prompt_join_duel(reply_method, user_id, session):
    text = (
        f"⚔️ **Défi Reçu !**\n\n"
        f"Mise requise : `{session['gross_entry_fee']}` Coins\n"
        f"Cagnotte : `{session['net_entry_fee'] * 2}` Coins à gagner.\n\n"
        "Voulez-vous affronter ce joueur ?"
    )
    keyboard = [
        [InlineKeyboardButton("✅ Accepter et Pronostiquer", callback_data=f"startjoin_{session['id']}")],
        [InlineKeyboardButton("❌ Refuser", callback_data="menu_main")]
    ]
    await reply_method(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    if data.startswith("wip_"):
        await query.answer("🚧 Fonctionnalité en cours de développement !", show_alert=True)
        return
        
    await query.answer()

    if data == "menu_main":
        db_user = database.get_or_create_user(user_id, "")
        await send_main_menu(query.edit_message_text, db_user)

    elif data == "menu_duel":
        text = "⚔️ **MODE DUEL 1v1**"
        keyboard = [
            [InlineKeyboardButton("➕ Créer un nouveau Duel", callback_data="duel_create_stake")],
            [InlineKeyboardButton("🔍 Liste des Duels Ouverts", callback_data="duel_list_public")],
            [InlineKeyboardButton("🔙 Menu Principal", callback_data="menu_main")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "duel_list_public":
        duels = database.get_open_duels()
        if not duels:
            await query.edit_message_text("❌ Aucun duel ouvert actuellement.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Retour", callback_data="menu_duel")]]))
            return
            
        keyboard = []
        for d in duels:
            # Sécurité pour ne pas s'affronter soi-même
            if d['creator_id'] != user_id:
                keyboard.append([InlineKeyboardButton(f"⚔️ Duel - Mise: {d['gross_entry_fee']} Coins", callback_data=f"startjoin_{d['id']}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Retour", callback_data="menu_duel")])
        await query.edit_message_text("🔍 **Marché des Duels :**\nChoisissez un adversaire :", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "duel_create_stake":
        text = "💰 **Sélectionnez la mise brute pour ce Duel :**"
        keyboard = [
            [InlineKeyboardButton("100 Coins", callback_data="stake_100"), InlineKeyboardButton("500 Coins", callback_data="stake_500")],
            [InlineKeyboardButton("🔙 Annuler", callback_data="menu_duel")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("stake_") or data.startswith("startjoin_"):
        is_joining = data.startswith("startjoin_")
        session_id_to_join = data.split("_")[1] if is_joining else None
        stake = 0
        
        db_user = database.get_or_create_user(user_id, "")
        
        if is_joining:
            session = database.get_duel_session(session_id_to_join)
            if not session or session["status"] != "WAITING":
                await query.edit_message_text("❌ Duel expiré.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Retour", callback_data="menu_duel")]]))
                return
            stake = float(session["gross_entry_fee"])
        else:
            stake = float(data.split("_")[1])

        if db_user["coins_balance"] < stake:
            await query.edit_message_text(f"❌ **Solde Insuffisant !** ({db_user['coins_balance']} Coins)", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Retour", callback_data="menu_duel")]]))
            return

        matches = database.get_active_matches()
        if not matches:
            await query.edit_message_text("❌ Aucun match actif.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Retour", callback_data="menu_duel")]]))
            return

        USER_TICKETS[user_id] = {
            "stake": stake, 
            "predictions": [], 
            "current_match_index": 0, 
            "matches": matches,
            "join_session_id": session_id_to_join
        }
        await show_match_selection(query, user_id)

    elif data.startswith("pick_"):
        pick = data.split("_")[1]
        ticket = USER_TICKETS.get(user_id)
        if not ticket:
            await query.edit_message_text("❌ Session expirée.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu", callback_data="menu_main")]]))
            return

        idx = ticket["current_match_index"]
        ticket["predictions"].append({"match_id": ticket["matches"][idx]["api_match_id"], "pick": pick})
        ticket["current_match_index"] += 1

        if ticket["current_match_index"] >= len(ticket["matches"]):
            await finalize_ticket_creation(query, user_id)
        else:
            await show_match_selection(query, user_id)

async def show_match_selection(query, user_id):
    ticket = USER_TICKETS[user_id]
    idx = ticket["current_match_index"]
    match = ticket["matches"][idx]

    text = f"📝 **Pronostic** ({idx + 1}/{len(ticket['matches'])})\n\n⚽ **{match['home_team']}** VS **{match['away_team']}**"
    keyboard = [
        [
            InlineKeyboardButton(f"1 ({match['home_team']})", callback_data="pick_HOME"),
            InlineKeyboardButton("N", callback_data="pick_DRAW"),
            InlineKeyboardButton(f"2 ({match['away_team']})", callback_data="pick_AWAY")
        ]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def finalize_ticket_creation(query, user_id):
    ticket = USER_TICKETS.get(user_id)
    join_session_id = ticket["join_session_id"]
    
    if join_session_id:
        success, msg = database.join_duel_session(join_session_id, user_id)
        if not success:
            await query.edit_message_text(f"❌ Erreur : {msg}")
            return
        database.save_ticket(join_session_id, user_id, ticket["predictions"])
        text = "✅ **Duel rejoint avec succès !**\nVos pronostics sont enregistrés. Que le meilleur gagne !"
    else:
        session, msg = database.create_duel_session(user_id, ticket["stake"])
        if not session:
            await query.edit_message_text(f"❌ Erreur : {msg}")
            return
        database.save_ticket(session["id"], user_id, ticket["predictions"])
        bot_username = (await telegram_app.bot.get_me()).username
        link = f"https://t.me/{bot_username}?start=duel_{session['id']}"
        text = f"✅ **Duel créé !**\n💰 Mise : `{ticket['stake']}`\n🔗 Partagez ce lien à votre adversaire :\n{link}"

    del USER_TICKETS[user_id]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu Principal", callback_data="menu_main")]]), parse_mode="Markdown")

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CallbackQueryHandler(button_handler))
telegram_app.add_error_handler(error_handler)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await telegram_app.initialize()
    await telegram_app.start()
    webhook_url = f"{os.getenv('RENDER_EXTERNAL_URL')}/webhook"
    await telegram_app.bot.set_webhook(url=webhook_url, drop_pending_updates=True)
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
