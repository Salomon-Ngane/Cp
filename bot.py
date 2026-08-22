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

# Stockage temporaire en mémoire pour les tickets en cours de composition
USER_TICKETS = {}

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.error("❌ Exception levée :", exc_info=context.error)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    referred_by = int(args[0]) if args and args[0].isdigit() else None
    
    db_user = database.get_or_create_user(user.id, user.username or user.first_name, referred_by)
    
    text = (
        f"👋 Bienvenue **{user.first_name}** dans le Bot Duel Sports !\n\n"
        f"💰 **Votre Solde :** `{db_user['coins_balance']}` Coins\n\n"
        "Choisissez un mode de jeu pour démarrer :"
    )
    
    keyboard = [
        [InlineKeyboardButton("⚔️ Créer / Rejoindre un Duel", callback_data="menu_duel")],
        [InlineKeyboardButton("🏟️ Mode Arena (Top 3)", callback_data="menu_arena")],
        [InlineKeyboardButton("🏆 Ligue Quotidienne", callback_data="menu_ligue")],
        [InlineKeyboardButton("💳 Mon Compte / Retrait", callback_data="menu_account")]
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    # --- MENU DUEL ---
    if data == "menu_duel":
        text = "⚔️ **MODE DUEL 1v1**\n\nQue souhaitez-vous faire ?"
        keyboard = [
            [InlineKeyboardButton("➕ Créer un nouveau Duel", callback_data="duel_create_stake")],
            [InlineKeyboardButton("🔍 Liste des Duels Ouverts", callback_data="duel_list_public")],
            [InlineKeyboardButton("🔙 Retour au Menu", callback_data="menu_main")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    # --- SELECTION DE LA MISE ---
    elif data == "duel_create_stake":
        text = "💰 **Sélectionnez la mise brute pour ce Duel :**"
        keyboard = [
            [InlineKeyboardButton("100 Coins", callback_data="stake_100"), InlineKeyboardButton("500 Coins", callback_data="stake_500")],
            [InlineKeyboardButton("1 000 Coins", callback_data="stake_1000"), InlineKeyboardButton("5 000 Coins", callback_data="stake_5000")],
            [InlineKeyboardButton("🔙 Annuler", callback_data="menu_duel")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("stake_"):
        stake = float(data.split("_")[1])
        db_user = database.get_or_create_user(user_id, "")
        
        if db_user["coins_balance"] < stake:
            await query.edit_message_text(f"❌ **Solde Insuffisant !**\n\nVotre solde actuel est de `{db_user['coins_balance']}` Coins. Il vous faut `{stake}` Coins pour ce duel.", 
                                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Choisir une autre mise", callback_data="duel_create_stake")]]), parse_mode="Markdown")
            return

        # Récupération des matchs disponibles
        matches = database.get_active_matches()
        if not matches:
            await query.edit_message_text("❌ Aucun match n'est disponible pour le moment. Réessayez plus tard.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Retour", callback_data="menu_duel")]]))
            return

        # Initialisation du ticket temporaire pour cet utilisateur
        USER_TICKETS[user_id] = {
            "stake": stake,
            "predictions": [],
            "current_match_index": 0,
            "matches": matches
        }
        await show_match_selection(query, user_id)

    # --- PRONOSTIC D'UN MATCH (1 / N / 2) ---
    elif data.startswith("pick_"):
        pick = data.split("_")[1] # HOME, DRAW, AWAY
        ticket = USER_TICKETS.get(user_id)
        if not ticket:
            await query.edit_message_text("Session expirée. Veuillez recommencer.")
            return

        idx = ticket["current_match_index"]
        current_match = ticket["matches"][idx]
        
        # Sauvegarde du choix
        ticket["predictions"].append({
            "match_id": current_match["api_match_id"],
            "pick": pick
        })
        
        ticket["current_match_index"] += 1

        # Si tous les matchs sont pronostiqués
        if ticket["current_match_index"] >= len(ticket["matches"]):
            await finalize_ticket_creation(query, user_id)
        else:
            await show_match_selection(query, user_id)

    # --- RETOUR AU MENU PRINCIPAL ---
    elif data == "menu_main":
        db_user = database.get_or_create_user(user_id, query.from_user.username or query.from_user.first_name)
        text = f"👋 Bienvenue dans le Bot Duel Sports !\n\n💰 **Votre Solde :** `{db_user['coins_balance']}` Coins"
        keyboard = [
            [InlineKeyboardButton("⚔️ Créer / Rejoindre un Duel", callback_data="menu_duel")],
            [InlineKeyboardButton("🏟️ Mode Arena (Top 3)", callback_data="menu_arena")],
            [InlineKeyboardButton("🏆 Ligue Quotidienne", callback_data="menu_ligue")],
            [InlineKeyboardButton("💳 Mon Compte / Retrait", callback_data="menu_account")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def show_match_selection(query, user_id):
    ticket = USER_TICKETS[user_id]
    idx = ticket["current_match_index"]
    total = len(ticket["matches"])
    match = ticket["matches"][idx]

    text = (
        f"📝 **Composition du Ticket** ({idx + 1}/{total})\n\n"
        f"⚽ **{match['home_team']}** VS **{match['away_team']}**\n\n"
        "Faites votre pronostic :"
    )
    keyboard = [
        [
            InlineKeyboardButton(f"1 ({match['home_team']})", callback_data="pick_HOME"),
            InlineKeyboardButton("N (Nul)", callback_data="pick_DRAW"),
            InlineKeyboardButton(f"2 ({match['away_team']})", callback_data="pick_AWAY")
        ]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def finalize_ticket_creation(query, user_id):
    ticket = USER_TICKETS.get(user_id)
    stake = ticket["stake"]
    predictions = ticket["predictions"]

    # Création de la session BDD et prélèvement du solde
    session, msg = database.create_duel_session(user_id, stake)
    if not session:
        await query.edit_message_text(f"❌ Erreur : {msg}")
        return

    # Enregistrement du ticket
    database.save_ticket(session["id"], user_id, predictions)

    # Lien de partage direct pour inviter un ami
    bot_username = (await telegram_app.bot.get_me()).username
    share_link = f"https://t.me/{bot_username}?start=duel_{session['id']}"

    text = (
        "✅ **Duel créé avec succès !**\n\n"
        f"💰 **Mise :** `{stake}` Coins\n"
        f"🏆 **Cagnotte Nette à gagner :** `{session['net_entry_fee'] * 2}` Coins\n"
        f"📊 **Nombre de matchs dans la grille :** `{len(predictions)}` matchs\n\n"
        f"🔗 **Partagez ce lien à votre adversaire pour l'affronter :**\n{share_link}"
    )
    
    keyboard = [
        [InlineKeyboardButton("📤 Defier un Ami sur Telegram", url=f"https://t.me/share/url?url={share_link}&text=Je%20te%20défie%20en%20duel%20sur%20les%20matchs%20du%20jour%20!")],
        [InlineKeyboardButton("🔙 Menu Principal", callback_data="menu_main")]
    ]
    
    # Nettoyage de la mémoire temporaire
    del USER_TICKETS[user_id]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# Enregistrement des handlers
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CallbackQueryHandler(button_handler))
telegram_app.add_error_handler(error_handler)

RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")

@asynccontextmanager
async def lifespan(app: FastAPI):
    await telegram_app.initialize()
    await telegram_app.start()
    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
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

@app.get("/")
def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    import uvicorn
    uvicorn.run("bot:app", host="0.0.0.0", port=port)
