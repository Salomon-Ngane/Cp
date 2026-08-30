import os
import re
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
import config
import database
import odds_api

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

telegram_app = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).build()

# Stockage temporaire en mémoire pour les tickets en cours de composition
USER_TICKETS = {}


def _clean_number(raw: str) -> str:
    """Retire les caractères invisibles que le clavier mobile peut injecter (ex: U+2060 WORD JOINER)."""
    return re.sub(r"[^\d.]", "", raw)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.error("❌ Exception levée :", exc_info=context.error)


def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚔️ Créer / Rejoindre un Duel", callback_data="menu_duel")],
        [InlineKeyboardButton("🏟️ Mode Arena (Top 3)", callback_data="menu_arena")],
        [InlineKeyboardButton("🏆 Ligue Quotidienne", callback_data="menu_ligue")],
        [InlineKeyboardButton("💳 Mon Compte / Retrait", callback_data="menu_account")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    # Lien de défi direct : /start duel_<session_id>
    if args and args[0].startswith("duel_"):
        database.get_or_create_user(user.id, user.username or user.first_name)
        session_id = args[0][len("duel_"):]
        await propose_join_duel(update.message, user.id, session_id)
        return

    referred_by = int(_clean_number(args[0])) if args and args[0].isdigit() else None
    db_user = database.get_or_create_user(user.id, user.username or user.first_name, referred_by)

    text = (
        f"👋 Bienvenue **{user.first_name}** dans le Bot Duel Sports !\n\n"
        f"💰 **Votre Solde :** `{db_user['coins_balance']}` Coins\n\n"
        "Choisissez un mode de jeu pour démarrer :"
    )
    await update.message.reply_text(text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")


async def propose_join_duel(message, user_id, session_id):
    """Affiche la confirmation d'acceptation d'un duel reçu par lien direct."""
    session = database.get_session(session_id)
    if not session or session["status"] != "WAITING":
        await message.reply_text("❌ Ce duel n'est plus disponible.")
        return
    if session["creator_id"] == user_id:
        await message.reply_text("⚠️ C'est votre propre duel, vous ne pouvez pas le rejoindre vous-même.")
        return

    text = (
        "⚔️ **Défi reçu !**\n\n"
        f"💰 **Mise :** `{session['gross_entry_fee']}` Coins\n"
        f"🏆 **Cagnotte en jeu :** `{session['net_entry_fee'] * 2}` Coins\n\n"
        "Acceptez-vous ce duel ?"
    )
    keyboard = [
        [InlineKeyboardButton("✅ Rejoindre le Duel", callback_data=f"join_{session_id}")],
        [InlineKeyboardButton("🔙 Annuler", callback_data="menu_main")],
    ]
    await message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


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
            [InlineKeyboardButton("🔙 Retour au Menu", callback_data="menu_main")],
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    # --- SÉLECTION DE LA MISE ---
    elif data == "duel_create_stake":
        text = "💰 **Sélectionnez la mise brute pour ce Duel :**"
        keyboard = [
            [InlineKeyboardButton("100 Coins", callback_data="stake_100"), InlineKeyboardButton("500 Coins", callback_data="stake_500")],
            [InlineKeyboardButton("1 000 Coins", callback_data="stake_1000"), InlineKeyboardButton("5 000 Coins", callback_data="stake_5000")],
            [InlineKeyboardButton("🔙 Annuler", callback_data="menu_duel")],
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("stake_"):
        stake = float(_clean_number(data.split("_")[1]))
        db_user = database.get_or_create_user(user_id, "")

        if db_user["coins_balance"] < stake:
            await query.edit_message_text(
                f"❌ **Solde Insuffisant !**\n\nVotre solde actuel est de `{db_user['coins_balance']}` Coins. Il vous faut `{stake}` Coins pour ce duel.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Choisir une autre mise", callback_data="duel_create_stake")]]),
                parse_mode="Markdown",
            )
            return

        matches = database.get_active_matches()
        if not matches:
            await query.edit_message_text(
                "❌ Aucun match n'est disponible pour le moment. Réessayez plus tard.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Retour", callback_data="menu_duel")]]),
            )
            return

        USER_TICKETS[user_id] = {
            "mode": "create",
            "stake": stake,
            "predictions": [],
            "current_match_index": 0,
            "matches": matches,
        }
        await show_match_selection(query, user_id)

    # --- LISTE DES DUELS OUVERTS ---
    elif data == "duel_list_public":
        duels = database.get_open_duels(exclude_creator_id=user_id)
        if not duels:
            await query.edit_message_text(
                "📭 Aucun duel ouvert pour le moment. Créez le vôtre !",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Retour", callback_data="menu_duel")]]),
            )
            return
        keyboard = [
            [InlineKeyboardButton(f"⚔️ Duel — {d['gross_entry_fee']} Coins", callback_data=f"join_{d['id']}")]
            for d in duels
        ]
        keyboard.append([InlineKeyboardButton("🔙 Retour", callback_data="menu_duel")])
        await query.edit_message_text("🔍 **Duels ouverts :**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    # --- REJOINDRE UN DUEL ---
    elif data.startswith("join_"):
        session_id = data[len("join_"):]
        session = database.get_session(session_id)
        if not session or session["status"] != "WAITING":
            await query.edit_message_text("❌ Ce duel n'est plus disponible.")
            return
        if session["creator_id"] == user_id:
            await query.answer("⚠️ C'est votre propre duel.", show_alert=True)
            return

        matches = database.get_matches_by_ids(session["match_ids"])
        if not matches:
            await query.edit_message_text("❌ Les matchs de ce duel ne sont plus disponibles.")
            return

        USER_TICKETS[user_id] = {
            "mode": "join",
            "session_id": session_id,
            "predictions": [],
            "current_match_index": 0,
            "matches": matches,
        }
        await show_match_selection(query, user_id)

    # --- PRONOSTIC D'UN MATCH (1 / N / 2) ---
    elif data.startswith("pick_"):
        pick = data.split("_")[1]  # HOME, DRAW, AWAY
        ticket = USER_TICKETS.get(user_id)
        if not ticket:
            await query.edit_message_text("Session expirée. Veuillez recommencer.")
            return

        idx = ticket["current_match_index"]
        current_match = ticket["matches"][idx]

        ticket["predictions"].append({
            "match_id": current_match["api_match_id"],
            "pick": pick,
        })
        ticket["current_match_index"] += 1

        if ticket["current_match_index"] >= len(ticket["matches"]):
            await finalize_ticket_creation(query, context, user_id)
        else:
            await show_match_selection(query, user_id)

    # --- RETOUR AU MENU PRINCIPAL ---
    elif data == "menu_main":
        db_user = database.get_or_create_user(user_id, query.from_user.username or query.from_user.first_name)
        text = f"👋 Bienvenue dans le Bot Duel Sports !\n\n💰 **Votre Solde :** `{db_user['coins_balance']}` Coins"
        await query.edit_message_text(text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")


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
    keyboard = [[
        InlineKeyboardButton(f"1 ({match['home_team']})", callback_data="pick_HOME"),
        InlineKeyboardButton("N (Nul)", callback_data="pick_DRAW"),
        InlineKeyboardButton(f"2 ({match['away_team']})", callback_data="pick_AWAY"),
    ]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def finalize_ticket_creation(query, context, user_id):
    ticket = USER_TICKETS.get(user_id)
    if not ticket:
        await query.edit_message_text("❌ Session expirée. Veuillez recommencer.")
        return

    # Feedback visuel immédiat
    await query.edit_message_text("⏳ **Génération de votre ticket en cours...\n**Veuillez patienter.", parse_mode="Markdown")

    predictions = ticket["predictions"]

    try:
        # --- Rejoindre un duel existant ---
        if ticket.get("mode") == "join":
            session_id = ticket["session_id"]
            session, msg = database.join_duel_session(session_id, user_id, predictions)

            if user_id in USER_TICKETS:
                del USER_TICKETS[user_id]

            if not session:
                await query.edit_message_text(f"❌ Erreur lors de la jonction au duel : {msg}")
                return

            await query.edit_message_text(
                "✅ **Duel accepté !**\n\nVotre ticket est enregistré. Vous serez notifié dès que tous les matchs seront terminés.",
                parse_mode="Markdown",
            )
            try:
                await context.bot.send_message(
                    chat_id=session["creator_id"],
                    text=(
                        "⚔️ **Votre duel a été accepté !**\n\n"
                        f"🏆 **Cagnotte en jeu :** `{session['net_entry_fee'] * 2}` Coins\n"
                        "Résultat dès que tous les matchs seront terminés."
                    ),
                    parse_mode="Markdown",
                )
            except Exception:
                logging.exception("Impossible de notifier le créateur du duel")
            return

        # --- Création d'un nouveau duel ---
        stake = ticket["stake"]
        match_ids = [m["api_match_id"] for m in ticket["matches"]]

        # 1. Création de la session dans Supabase
        session, msg = database.create_duel_session(user_id, stake, match_ids)
        if not session:
            await query.edit_message_text(f"❌ Erreur lors de la création de la session : {msg}")
            return

        # 2. Sauvegarde des pronostics du ticket
        database.save_ticket(session["id"], user_id, predictions)

        bot_username = (await telegram_app.bot.get_me()).username
        share_link = f"https://t.me/{bot_username}?start=duel_{session['id']}"

        text = (
            "✅ **Duel créé avec succès !**\n\n"
            f"💰 **Mise :** `{stake}` Coins\n"
            f"🏆 **Cagnotte Nette à gagner :** `{session['net_entry_fee'] * 2}` Coins\n"
            f"📊 **Nombre de matchs dans la grille :** `{len(predictions)}` matchs\n\n"
            f"🔗 **Partagez ce lien à votre adversaire pour l'affronter :**\n`{share_link}`"
        )
        keyboard = [
            [InlineKeyboardButton("📤 Défier un Ami sur Telegram", url=f"https://t.me/share/url?url={share_link}&text=Je%20te%20défie%20en%20duel%20sur%20les%20matchs%20du%20jour%20!")],
            [InlineKeyboardButton("🔙 Menu Principal", callback_data="menu_main")],
        ]

        if user_id in USER_TICKETS:
            del USER_TICKETS[user_id]

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    except Exception as e:
        logging.error(f"❌ Erreur critique dans finalize_ticket_creation: {e}", exc_info=True)
        await query.edit_message_text(f"❌ **Erreur lors de la sauvegarde :**\n`{str(e)}`", parse_mode="Markdown")


# --- COMMANDES ADMIN ---

def _is_admin(user_id: int) -> bool:
    return config.ADMIN_TELEGRAM_ID != 0 and user_id == config.ADMIN_TELEGRAM_ID


async def seed_matches_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Commande réservée à l'admin.")
        return
    matches = database.create_sample_matches()
    await update.message.reply_text(f"✅ {len(matches)} match(s) de test disponibles.")


async def recharge_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Commande réservée à l'admin.")
        return
    args = context.args
    if len(args) != 2:
        await update.message.reply_text("Usage : `/recharge <telegram_id> <montant>`", parse_mode="Markdown")
        return

    try:
        target_id = int(_clean_number(args[0]))
        amount = float(_clean_number(args[1]))
    except ValueError:
        await update.message.reply_text("❌ Identifiant ou montant invalide.")
        return

    database.credit_balance(target_id, amount)
    await update.message.reply_text(f"✅ `{amount}` Coins ajoutés au solde de `{target_id}`.", parse_mode="Markdown")


async def sync_matches_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Commande réservée à l'admin.")
        return
    if not config.ODDS_API_KEY:
        await update.message.reply_text("❌ `ODDS_API_KEY` n'est pas configurée sur Render.", parse_mode="Markdown")
        return

    await update.message.reply_text("⏳ Synchronisation avec TheOddsAPI en cours (Football / Basket / Tennis)...")
    try:
        matches, quota_remaining = odds_api.sync_today_matches(config.ODDS_API_KEY)
        count = database.upsert_matches(matches)
        sports = sorted(set(m["sport"] for m in matches))
        quota_line = f"\n📊 Requêtes restantes (quota) : `{quota_remaining}`" if quota_remaining else ""
        await update.message.reply_text(
            f"✅ {count} match(s) du jour synchronisés.\n"
            f"🏅 Sports couverts : {', '.join(sports) if sports else 'aucun'}"
            f"{quota_line}",
            parse_mode="Markdown",
        )
    except Exception as e:
        logging.exception("Échec de la synchronisation TheOddsAPI")
        await update.message.reply_text(f"❌ Erreur pendant la synchro : `{e}`", parse_mode="Markdown")


async def resolve_match_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Commande réservée à l'admin.")
        return
    args = context.args
    if len(args) != 2 or args[1].upper() not in ("HOME", "DRAW", "AWAY"):
        await update.message.reply_text("Usage : `/resolve <api_match_id> <HOME|DRAW|AWAY>`", parse_mode="Markdown")
        return

    try:
        api_match_id = int(_clean_number(args[0]))
    except ValueError:
        await update.message.reply_text("❌ L'ID du match doit être un nombre.")
        return

    result = args[1].upper()
    database.set_match_result(api_match_id, result)

    resolvable = database.find_resolvable_sessions(api_match_id)
    resolved_count = 0
    for session in resolvable:
        outcome = database.resolve_duel(session["id"])
        if outcome:
            resolved_count += 1
            await notify_duel_result(context, outcome)

    await update.message.reply_text(
        f"✅ Résultat enregistré pour le match `{api_match_id}` : **{result}**\n"
        f"🏁 {resolved_count} duel(s) résolu(s) et payé(s).",
        parse_mode="Markdown",
    )


async def notify_duel_result(context: ContextTypes.DEFAULT_TYPE, outcome: dict):
    creator_id = outcome["creator_id"]
    opponent_id = outcome["opponent_id"]
    creator_score = outcome["creator_score"]
    opponent_score = outcome["opponent_score"]
    winner_id = outcome["winner_id"]
    pot = outcome["pot"]

    for player_id, my_score, other_score in (
        (creator_id, creator_score, opponent_score),
        (opponent_id, opponent_score, creator_score),
    ):
        if winner_id is None:
            verdict = f"🤝 **Égalité !** ({my_score} pronostics justes chacun)\nCagnotte partagée : `{pot / 2}` Coins récupérés."
        elif winner_id == player_id:
            verdict = f"🏆 **Vous avez gagné !** ({my_score} contre {other_score})\nVous remportez `{pot}` Coins."
        else:
            verdict = f"❌ **Défaite.** ({my_score} contre {other_score})\nMeilleure chance la prochaine fois !"

        try:
            await context.bot.send_message(chat_id=player_id, text=f"⚔️ **Résultat du Duel**\n\n{verdict}", parse_mode="Markdown")
        except Exception:
            logging.exception(f"Impossible de notifier {player_id}")


# --- ENREGISTREMENT DES HANDLERS ---
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("seed_matches", seed_matches_command))
telegram_app.add_handler(CommandHandler("recharge", recharge_command))
telegram_app.add_handler(CommandHandler("sync_matches", sync_matches_command))
telegram_app.add_handler(CommandHandler("resolve", resolve_match_command))
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