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
        [InlineKeyboardButton("⚔️ Créer / Rejoindre un Duel", callback_data="menu_duel")],
        [InlineKeyboardButton("📋 Mes Tickets", callback_data="my_tickets"), InlineKeyboardButton("🔴 Live", callback_data="live_all")],
        [InlineKeyboardButton("💳 Mon Compte", callback_data="menu_account")],
    ])

def is_admin(user_id: int) -> bool:
    return user_id == config.ADMIN_TELEGRAM_ID

# ==========================================
# COMMANDES UTILISATEUR
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    if args and args[0].startswith("join_"):
        database.get_or_create_user(user.id, user.username or user.first_name)
        session_id = args[0][len("join_"):]
        await propose_join_duel(update.message, user.id, session_id, context)
        return

    db_user = database.get_or_create_user(user.id, user.username or user.first_name)
    text = (
        f"👋 Bienvenue **{user.first_name}** sur **Clashsport** !\n\n"
        f"💰 **Votre Solde :** `{db_user['coins_balance']}` Coins\n\n"
        "L'arène ultime de pronostics sportifs en duel 1v1. Choisissez une option ci-dessous :"
    )
    await update.message.reply_text(text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")

async def propose_join_duel(message, user_id, session_id, context):
    session = database.get_session(session_id)
    if not session or session["status"] != "WAITING":
        await message.reply_text("❌ Ce duel n'est plus disponible.")
        return
    if session["creator_id"] == user_id:
        await message.reply_text("⚠️ C'est votre propre duel, vous ne pouvez pas le rejoindre vous-même.")
        return

    text = (
        "⚔️ **Invitation à un Duel Clashsport !**\n\n"
        f"💰 **Mise requise :** `{session['gross_entry_fee']}` Coins\n"
        f"🎯 **Condition :** Composer un ticket autonome de `{session['match_count']}` match(s).\n\n"
        "Voulez-vous accepter et composer votre ticket ?"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚔️ Accepter & Composer mon ticket", callback_data=f"start_join_{session_id}")],
        [InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")]
    ])
    await message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


# ==========================================
# COMMANDES ADMINISTRATEUR
# ==========================================

async def admin_resolve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    args = context.args
    if len(args) != 2 or args[1].upper() not in ("HOME", "DRAW", "AWAY"):
        await update.message.reply_text("❌ Usage : /resolve [api_match_id] [HOME|DRAW|AWAY]")
        return

    api_match_id = _clean_number(args[0])
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
        f"✅ Résultat enregistré pour `{api_match_id}` : **{result}**\n"
        f"🏁 {resolved_count} duel(s) tranché(s) et payé(s).",
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
            text = (
                "🤝 **ÉGALITÉ PARFAITE !** 🤝\n\n"
                f"`{my_score}` partout ! La cagnotte est partagée : `+{pot / 2}` Coins tombent dans ta poche.\n"
                "Il faudra un tie-break la prochaine fois... ⚖️"
            )
        elif winner_id == player_id:
            text = (
                "🏆 **VICTOIRE !** 🏆\n\n"
                f"`{my_score}` bons pronostics contre `{other_score}` — tu rafles toute la mise !\n"
                f"💰 `+{pot}` Coins viennent d'atterrir sur ton compte. GG ! 🎉"
            )
        else:
            text = (
                "💥 **DÉFAITE...** 💥\n\n"
                f"`{my_score}` contre `{other_score}`, ton adversaire l'emporte cette fois-ci.\n"
                "La revanche t'attend sur Clashsport, ne lâche rien ! 🔁"
            )

        try:
            sent = await context.bot.send_message(chat_id=player_id, text=f"⚔️ **RÉSULTAT DU DUEL CLASHSPORT** ⚔️\n\n{text}", parse_mode="Markdown")
            if winner_id == player_id:
                try:
                    await context.bot.set_message_reaction(chat_id=player_id, message_id=sent.message_id, reaction="🔥")
                except Exception:
                    pass
                if config.CELEBRATION_STICKER_ID:
                    try:
                        await context.bot.send_sticker(chat_id=player_id, sticker=config.CELEBRATION_STICKER_ID)
                    except Exception:
                        pass
        except Exception:
            logging.exception(f"Impossible de notifier {player_id}")


async def tickets_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    sessions = database.get_user_sessions(user_id)
    if not sessions:
        await update.message.reply_text("📭 Aucun ticket pour l'instant. Lance ton premier duel avec /start !")
        return
    await update.message.reply_text("📋 **Tes Tickets Clashsport**", reply_markup=_tickets_keyboard(sessions, user_id), parse_mode="Markdown")


def _tickets_keyboard(sessions, user_id):
    keyboard = []
    for s in sessions:
        if s["status"] == "WAITING":
            icon = "⏳"
        elif s["status"] == "IN_PROGRESS":
            icon = "🔴"
        elif s.get("winner_id") == user_id:
            icon = "🏆"
        elif s.get("winner_id"):
            icon = "💀"
        else:
            icon = "🤝"
        keyboard.append([InlineKeyboardButton(f"{icon} Duel — {s['gross_entry_fee']} Coins ({s['match_count']} matchs)", callback_data=f"ticket_{s['id']}")])
    return InlineKeyboardMarkup(keyboard)


async def show_ticket_detail(query, context, session_id):
    user_id = query.from_user.id
    session = database.get_session(session_id)
    if not session:
        await query.edit_message_text("❌ Duel introuvable.")
        return

    opponent_id = session["opponent_id"] if session["creator_id"] == user_id else session["creator_id"]
    tickets = database.get_tickets_for_session(session_id)
    my_ticket = next((t for t in tickets if t["user_id"] == user_id), None)
    opp_ticket = next((t for t in tickets if opponent_id and t["user_id"] == opponent_id), None)

    lines = [f"⚔️ **Duel Clashsport — {session['gross_entry_fee']} Coins**\n"]

    if my_ticket:
        match_ids = [str(p["match_id"]) for p in my_ticket["predictions"]]
        matches = {str(m["api_match_id"]): m for m in database.get_matches_by_ids(match_ids)}
        my_correct = 0
        for p in my_ticket["predictions"]:
            m = matches.get(str(p["match_id"]))
            if not m:
                continue
            if m.get("result"):
                icon = "👍" if m["result"] == p["pick"] else "😢"
                if m["result"] == p["pick"]:
                    my_correct += 1
            else:
                icon = "⏳"
            lines.append(f"{icon} {m['home_team']} vs {m['away_team']}")
        lines.append(f"\n🟢 **Toi : {my_correct}/{len(my_ticket['predictions'])}**")

    if opp_ticket:
        opp_matches = {str(m["api_match_id"]): m for m in database.get_matches_by_ids([str(p["match_id"]) for p in opp_ticket["predictions"]])}
        opp_correct = sum(1 for p in opp_ticket["predictions"] if opp_matches.get(str(p["match_id"]), {}).get("result") == p["pick"])
        lines.append(f"🔴 **Adversaire : {opp_correct}/{len(opp_ticket['predictions'])}**")
    elif session["status"] == "WAITING":
        lines.append("🔴 En attente d'un adversaire...")

    keyboard = [
        [InlineKeyboardButton("🔴 Voir en Direct", callback_data=f"live_{session_id}")],
        [InlineKeyboardButton("🔙 Mes Tickets", callback_data="my_tickets")],
    ]
    await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


def _live_verdict(pick, home_score, away_score):
    if home_score is None or away_score is None:
        return "⚪ pas encore commencé"
    if home_score > away_score:
        leading = "HOME"
    elif away_score > home_score:
        leading = "AWAY"
    else:
        leading = "DRAW"
    return "🟢 en tête" if leading == pick else "🔻 mené"


async def _build_live_text(session_ids, user_id):
    all_match_ids = set()
    my_tickets = {}
    for sid in session_ids:
        tickets = database.get_tickets_for_session(sid)
        my_ticket = next((t for t in tickets if t["user_id"] == user_id), None)
        my_tickets[sid] = my_ticket
        if my_ticket:
            all_match_ids.update(str(p["match_id"]) for p in my_ticket["predictions"])

    if not all_match_ids:
        return "📭 Aucun match à suivre pour l'instant."

    live_data = await database.get_live_scores_for_matches(list(all_match_ids))

    lines = ["🔴 **EN DIRECT — CLASHSPORT**\n"]
    for sid in session_ids:
        my_ticket = my_tickets.get(sid)
        if not my_ticket:
            continue
        lines.append("⚔️ **Duel :**")
        for p in my_ticket["predictions"]:
            m = live_data.get(str(p["match_id"]))
            if not m:
                continue
            home, away = m.get("live_score_home"), m.get("live_score_away")
            status = m.get("live_status")
            status_icon = {"live": "🔴 LIVE", "final": "🏁 TERMINÉ"}.get(status, "⚪")
            score_txt = f"{home} - {away}" if home is not None else "à venir"
            verdict = _live_verdict(p["pick"], home, away)
            lines.append(f"{status_icon} {m['home_team']} {score_txt} {m['away_team']} — {verdict}")
        lines.append("")

    return "\n".join(lines)


async def live_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    sessions = [s for s in database.get_user_sessions(user_id, history_limit=0) if s["status"] in ("WAITING", "IN_PROGRESS")]
    if not sessions:
        await update.message.reply_text("📭 Aucun duel en cours à suivre en direct.")
        return
    await update.message.reply_text("🔴 Récupération des scores en direct...")
    text = await _build_live_text([s["id"] for s in sessions], user_id)
    await update.message.reply_text(text, parse_mode="Markdown")


async def admin_give(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try:
        target_id, amount = int(context.args[0]), int(context.args[1])
        new_balance = database.credit_balance(target_id, amount)
        await update.message.reply_text(f"✅ Ajout de {amount} Coins au joueur {target_id}. Nouveau solde : {new_balance}.")
        await context.bot.send_message(chat_id=target_id, text=f"🏦 **Notification Clashsport :**\nUn administrateur a crédité votre compte de `{amount}` Coins.", parse_mode="Markdown")
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Usage: /give [telegram_id] [montant]")

async def admin_take(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try:
        target_id, amount = int(context.args[0]), int(context.args[1])
        new_balance = database.credit_balance(target_id, -amount)
        await update.message.reply_text(f"✅ Retrait de {amount} Coins au joueur {target_id}. Nouveau solde : {new_balance}.")
        await context.bot.send_message(chat_id=target_id, text=f"🏦 **Notification Clashsport :**\nUn administrateur a retiré `{amount}` Coins de votre compte.", parse_mode="Markdown")
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Usage: /take [telegram_id] [montant]")

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    stats = database.get_platform_stats()
    text = (
        "📊 **Statistiques Clashsport**\n\n"
        f"👥 Utilisateurs totaux : `{stats['total_users']}`\n"
        f"💰 Coins en circulation : `{stats['total_coins']}`\n"
        f"⚔️ Duels en attente : `{stats['waiting_duels']}`\n"
        f"🔥 Duels en cours : `{stats['active_duels']}`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def admin_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    msg = await update.message.reply_text("⏳ Lancement de la synchronisation TheOddsAPI...")
    count, result = await database.sync_matches_from_api_async()
    await msg.edit_text(f"{result}")

async def admin_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    message = " ".join(context.args)
    if not message:
        await update.message.reply_text("❌ Usage: /alert [message]")
        return
    
    users = database.get_all_users()
    sent = 0
    for u in users:
        try:
            await context.bot.send_message(chat_id=u["telegram_id"], text=f"📢 **Annonce Clashsport :**\n\n{message}", parse_mode="Markdown")
            sent += 1
        except Exception:
            pass
    await update.message.reply_text(f"✅ Alerte envoyée à {sent} utilisateurs.")

# ==========================================
# FLUX DE NAVIGATION ET CREATION
# ==========================================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "menu_duel":
        text = "⚔️ **MODE DUEL 1v1 — CLASHSPORT**\n\nQue souhaitez-vous faire ?"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Créer un nouveau Duel", callback_data="duel_create_stake")],
            [InlineKeyboardButton("🔍 Liste des Duels Ouverts", callback_data="duel_list_public")],
            [InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")],
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
        return ConversationHandler.END

    elif data == "duel_create_stake":
        text = "💰 **Sélectionnez le montant de la mise :**"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("100 Coins", callback_data="stake_100"), InlineKeyboardButton("500 Coins", callback_data="stake_500")],
            [InlineKeyboardButton("1 000 Coins", callback_data="stake_1000")],
            [InlineKeyboardButton("✏️ Montant personnalisé", callback_data="stake_custom")],
            [InlineKeyboardButton("🔙 Retour", callback_data="menu_duel")]
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
        return ConversationHandler.END

    elif data.startswith("stake_") and data != "stake_custom":
        stake = int(_clean_number(data.split("_")[1]))
        await init_draft_duel(query, context, user_id, stake)
        return ConversationHandler.END

    elif data == "duel_list_public":
        duels = database.get_open_duels(exclude_creator_id=user_id)
        if not duels:
            await query.edit_message_text(
                "📭 Aucun duel ouvert pour le moment. Créez le vôtre !",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Retour", callback_data="menu_duel")]]),
            )
            return ConversationHandler.END
        keyboard = [[InlineKeyboardButton(f"⚔️ Duel — {d['gross_entry_fee']} Coins ({d['match_count']} matchs)", callback_data=f"start_join_{d['id']}")] for d in duels]
        keyboard.append([InlineKeyboardButton("🔙 Retour", callback_data="menu_duel")])
        await query.edit_message_text("🔍 **Duels ouverts sur Clashsport :**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return ConversationHandler.END

    elif data.startswith("start_join_"):
        session_id = data[len("start_join_"):]
        session = database.get_session(session_id)
        if not session or session["status"] != "WAITING":
            await query.edit_message_text("❌ Ce duel n'est plus disponible.")
            return ConversationHandler.END

        db_user = database.get_or_create_user(user_id, "")
        if db_user["coins_balance"] < session["gross_entry_fee"]:
            await query.edit_message_text(
                f"❌ **Solde insuffisant !**\n\nIl vous faut `{session['gross_entry_fee']}` Coins.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")]]), parse_mode="Markdown"
            )
            return ConversationHandler.END

        context.user_data["draft_duel"] = {"mode": "join", "session_id": session_id, "stake": session["gross_entry_fee"], "match_count": session["match_count"], "selected_matches": {}}
        await show_sport_selection_menu(query, context)
        return ConversationHandler.END

    elif data in ["select_sports", "back_to_sports"]:
        await show_sport_selection_menu(query, context)

    elif data.startswith("sport_"):
        sport = data.split("_")[1]
        await show_leagues_for_sport(query, context, sport)

    elif data.startswith("league_"):
        league = data[len("league_"):]
        await show_matches_for_league(query, context, league)

    elif data.startswith("pick_"):
        _, m_id, pick = data.split("_")
        draft = context.user_data.get("draft_duel", {})
        selected = draft.get("selected_matches", {})
        
        matches = database.get_active_matches()
        match_obj = next((m for m in matches if str(m["api_match_id"]) == m_id), None)

        if match_obj:
            if m_id in selected and selected[m_id]["pick"] == pick:
                del selected[m_id]
            else:
                selected[m_id] = {"match": match_obj, "pick": pick}
        await show_matches_for_league(query, context, draft.get("current_league"))

    elif data == "review_ticket":
        await show_ticket_review(query, context)

    elif data == "confirm_duel_creation":
        await confirm_duel_final(query, context, user_id)

    elif data == "cancel_creation":
        context.user_data.pop("draft_duel", None)
        await query.edit_message_text("❌ Création annulée.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    elif data == "menu_main":
        db_user = database.get_or_create_user(user_id, query.from_user.username or query.from_user.first_name)
        text = f"👋 Bienvenue sur **Clashsport** !\n\n💰 **Votre Solde :** `{db_user['coins_balance']}` Coins"
        await query.edit_message_text(text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")
        return ConversationHandler.END

    elif data == "my_tickets":
        user = database.get_or_create_user(user_id, query.from_user.username or query.from_user.first_name)
        sessions = database.get_user_sessions(user_id)
        if not sessions:
            await query.edit_message_text("📭 Aucun ticket pour l'instant.", reply_markup=main_menu_keyboard())
            return ConversationHandler.END
        await query.edit_message_text("📋 **Tes Tickets Clashsport**", reply_markup=_tickets_keyboard(sessions, user_id), parse_mode="Markdown")
        return ConversationHandler.END

    elif data.startswith("ticket_"):
        session_id = data[len("ticket_"):]
        await show_ticket_detail(query, context, session_id)
        return ConversationHandler.END

    elif data == "live_all":
        sessions = [s for s in database.get_user_sessions(user_id, history_limit=0) if s["status"] in ("WAITING", "IN_PROGRESS")]
        if not sessions:
            await query.edit_message_text("📭 Aucun duel en cours à suivre en direct.", reply_markup=main_menu_keyboard())
            return ConversationHandler.END
        await query.edit_message_text("🔴 Récupération des scores en direct...")
        text = await _build_live_text([s["id"] for s in sessions], user_id)
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")]])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
        return ConversationHandler.END

    elif data.startswith("live_"):
        session_id = data[len("live_"):]
        await query.edit_message_text("🔴 Récupération des scores en direct...")
        text = await _build_live_text([session_id], user_id)
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Retour au Ticket", callback_data=f"ticket_{session_id}")]])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
        return ConversationHandler.END

    elif data == "menu_account":
        db_user = database.get_or_create_user(user_id, query.from_user.username or query.from_user.first_name)
        text = (
            f"💳 **Mon Compte — Clashsport**\n\n"
            f"👤 Utilisateur : {db_user['username']}\n"
            f"🆔 ID Telegram : `{db_user['telegram_id']}`\n"
            f"💰 Solde : `{db_user['coins_balance']}` Coins\n\n"
            "Pour recharger vos Coins ou demander de l'aide, contactez notre support ci-dessous."
        )
        # N'OUBLIEZ PAS de remplacer 'votre_username_admin' dans config par votre vrai username Telegram
        admin_url = f"https://t.me/{getattr(config, 'ADMIN_USERNAME', 'votre_username_admin')}"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Support / Recharger mes Coins", url=admin_url)],
            [InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")]
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


# ==========================================
# CONVERSATION HANDLER (MISE CUSTOM)
# ==========================================

async def ask_custom_stake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "✏️ **Entrez le montant de votre mise au clavier (en entier) :**\n\n(Exemple: Tapez `250` puis envoyez)",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Annuler", callback_data="duel_create_stake")]]),
        parse_mode="Markdown"
    )
    return WAITING_CUSTOM_STAKE

async def receive_custom_stake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    try:
        stake = int(_clean_number(text))
        if stake <= 0: raise ValueError()
        
        dummy_msg = await update.message.reply_text("⏳ Validation du montant...")
        class DummyQuery:
            def __init__(self, message): self.message = message
            async def edit_message_text(self, text, reply_markup=None, parse_mode=None):
                return await self.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)

        await init_draft_duel(DummyQuery(dummy_msg), context, user_id, stake)
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ Montant invalide. Veuillez entrer un nombre entier positif.")
        return WAITING_CUSTOM_STAKE

async def cancel_custom_stake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await button_handler(update, context)
    return ConversationHandler.END

# ==========================================
# FONCTIONS UTILITAIRES TICKET & FILTRES
# ==========================================

async def init_draft_duel(query, context, user_id, stake):
    db_user = database.get_or_create_user(user_id, "")
    if db_user["coins_balance"] < stake:
        await query.edit_message_text(
            f"❌ **Solde insuffisant !**\n\nVotre solde : `{db_user['coins_balance']}` Coins.\nMise requise : `{stake}` Coins.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Modifier la mise", callback_data="duel_create_stake")]]), parse_mode="Markdown"
        )
        return

    context.user_data["draft_duel"] = {"mode": "create", "stake": stake, "selected_matches": {}}
    await show_sport_selection_menu(query, context)

async def show_sport_selection_menu(query, context):
    draft = context.user_data.get("draft_duel", {})
    selected = draft.get("selected_matches", {})
    text = (
        f"🏟️ **Sélection de Sport pour votre Ticket Clashsport**\n\n"
        f"💰 **Mise :** `{draft.get('stake', 100)}` Coins\n"
        f"🎯 **Matchs sélectionnés :** `{len(selected)}` match(s)\n\nChoisissez une discipline :"
    )
    keyboard = [
        [InlineKeyboardButton("⚽ Football", callback_data="sport_soccer"), InlineKeyboardButton("🏀 Basketball", callback_data="sport_basketball")],
        [InlineKeyboardButton("🎾 Tennis", callback_data="sport_tennis")],
    ]
    if len(selected) >= 1:
        keyboard.append([InlineKeyboardButton(f"✅ Voir / Valider mon Ticket ({len(selected)} match(s))", callback_data="review_ticket")])
    keyboard.append([InlineKeyboardButton("❌ Annuler", callback_data="cancel_creation")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def show_leagues_for_sport(query, context, sport):
    draft = context.user_data.get("draft_duel", {})
    draft["current_sport"] = sport
    
    matches = database.get_matches_by_sport(sport)
    leagues = sorted(list(set(m.get("league", "Championnat Général") for m in matches if m.get("league"))))
    
    selected = draft.get("selected_matches", {})
    text = (
        f"🏆 **Championnats — {sport.upper()}**\n"
        f"📊 Matchs au panier : **{len(selected)}**\n\n"
        "Sélectionnez une compétition :"
    )
    
    keyboard = []
    if len(selected) >= 1:
        keyboard.append([InlineKeyboardButton(f"✅ Voir Récapitulatif ({len(selected)} match(s))", callback_data="review_ticket")])
    keyboard.append([InlineKeyboardButton("🔙 Changer de Sport", callback_data="select_sports")])

    if not leagues:
        text += "\n\n❌ Aucun championnat trouvé pour le moment."
    else:
        for league in leagues:
            keyboard.append([InlineKeyboardButton(f"🏅 {league}", callback_data=f"league_{league}")])

    keyboard.append([InlineKeyboardButton("❌ Annuler", callback_data="cancel_creation")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def show_matches_for_league(query, context, league):
    draft = context.user_data.get("draft_duel", {})
    sport = draft.get("current_sport", "soccer")
    draft["current_league"] = league
    selected = draft.get("selected_matches", {})
    
    matches = database.get_matches_by_sport(sport)
    league_matches = [m for m in matches if m.get("league") == league]
    
    text = f"🏟️ **{league}**\n📊 Panier : **{len(selected)}** match(s)\n\n"
    keyboard = []

    if len(selected) >= 1:
        keyboard.append([InlineKeyboardButton(f"✅ Voir Récapitulatif ({len(selected)} match(s))", callback_data="review_ticket")])
    keyboard.append([InlineKeyboardButton("🔙 Retour aux Championnats", callback_data=f"sport_{sport}")])

    for m in league_matches:
        m_id = str(m["api_match_id"])
        current_pick = selected[m_id]["pick"] if m_id in selected else None
        btn_h = f"1 ({m.get('odds_home', 1.0)})" + (" ✅" if current_pick == "HOME" else "")
        btn_a = f"2 ({m.get('odds_away', 1.0)})" + (" ✅" if current_pick == "AWAY" else "")
        
        keyboard.append([InlineKeyboardButton(f"⚽ {m['home_team']} vs {m['away_team']}", callback_data="ignore")])
        if m.get("odds_draw"):
            btn_d = f"N ({m.get('odds_draw')})" + (" ✅" if current_pick == "DRAW" else "")
            keyboard.append([InlineKeyboardButton(btn_h, callback_data=f"pick_{m_id}_HOME"), InlineKeyboardButton(btn_d, callback_data=f"pick_{m_id}_DRAW"), InlineKeyboardButton(btn_a, callback_data=f"pick_{m_id}_AWAY")])
        else:
            keyboard.append([InlineKeyboardButton(btn_h, callback_data=f"pick_{m_id}_HOME"), InlineKeyboardButton(btn_a, callback_data=f"pick_{m_id}_AWAY")])

    keyboard.append([InlineKeyboardButton("🔙 Retour aux Championnats", callback_data=f"sport_{sport}")])
    keyboard.append([InlineKeyboardButton("❌ Annuler", callback_data="cancel_creation")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def show_ticket_review(query, context):
    draft = context.user_data.get("draft_duel", {})
    selected = draft.get("selected_matches", {})
    stake = draft.get("stake", 100)
    total_count = len(selected)

    text = f"📋 **Récapitulatif de votre Ticket Clashsport**\n\n💰 **Mise engagée :** `{stake}` Coins\n🎯 **Matchs retenus :** `{total_count}`\n\n--- **Vos Choix** ---\n"
    for m_id, item in selected.items():
        m = item["match"]
        pick = item["pick"]
        pick_label = m["home_team"] if pick == "HOME" else (m["away_team"] if pick == "AWAY" else "Nul")
        text += f"• **[{m.get('sport', 'SPORT').upper()}]** {m['home_team']} vs {m['away_team']} ➔ **{pick_label}**\n"

    keyboard = []
    if total_count >= 1:
        keyboard.append([InlineKeyboardButton(f"🚀 Valider le Duel ({total_count} match(s))", callback_data="confirm_duel_creation")])
    else:
        text += "\n⚠️ *Veuillez choisir au moins 1 match pour valider votre ticket.*"
    keyboard.append([InlineKeyboardButton("➕ Ajouter d'autres matchs", callback_data="back_to_sports"), InlineKeyboardButton("❌ Annuler", callback_data="cancel_creation")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def confirm_duel_final(query, context, user_id):
    draft = context.user_data.get("draft_duel", {})
    mode = draft.get("mode", "create")
    stake = draft.get("stake", 100)
    selected = draft.get("selected_matches", {})
    predictions = [{"match_id": m_id, "pick": item["pick"]} for m_id, item in selected.items()]

    if mode == "join":
        session, msg = database.join_duel_session(draft["session_id"], user_id, predictions)
        if not session:
            await query.edit_message_text(f"❌ Erreur : {msg}")
            return
        context.user_data.pop("draft_duel", None)
        await query.edit_message_text(
            "⚔️ **CHALLENGE ACCEPTÉ SUR CLASHSPORT !** ⚔️\n\n"
            "Ton ticket est en jeu, la mise est sur la table. Plus rien à faire qu'attendre le coup de sifflet final...\n"
            "Que le meilleur pronostiqueur l'emporte ! 🍀",
            parse_mode="Markdown",
        )
        try:
            await context.bot.send_dice(chat_id=user_id, emoji="🎰")
        except Exception:
            pass
        try:
            await context.bot.send_message(
                chat_id=session["creator_id"],
                text=(
                    "🚨 **UN ADVERSAIRE EST ENTRÉ SUR LE RING CLASHSPORT !** 🚨\n\n"
                    f"Ton duel est officiellement lancé — cagnotte de `{session['net_entry_fee'] * 2}` Coins à la clé.\n"
                    "Serre les dents, ça commence maintenant ! 🔥"
                ),
                parse_mode="Markdown"
            )
        except Exception:
            pass
    else:
        session, msg = database.create_duel_session(user_id, stake, len(predictions), predictions)
        if not session:
            await query.edit_message_text(f"❌ Erreur : {msg}")
            return
        context.user_data.pop("draft_duel", None)
        bot_username = (await telegram_app.bot.get_me()).username
        share_link = f"https://t.me/{bot_username}?start=join_{session['id']}"
        text = (
            "🔥 **DÉFI LANCÉ SUR CLASHSPORT !** 🔥\n\n"
            f"💰 Mise sur la table : `{stake}` Coins\n"
            f"🎯 Ticket verrouillé : `{len(predictions)}` pronostics\n\n"
            "Trouve un adversaire assez courageux pour relever le défi 👇\n\n"
            f"🔗 **Lien d'invitation :**\n`{share_link}`"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Partager le Défi", url=f"https://t.me/share/url?url={share_link}&text=Rejoins-moi%20sur%20ce%20duel%20Clashsport%20!")],
            [InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")]
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
        try:
            await context.bot.send_dice(chat_id=user_id, emoji="🎰")
        except Exception:
            pass


# --- ENREGISTREMENT DES HANDLERS ---
stake_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(ask_custom_stake, pattern="^stake_custom$")],
    states={WAITING_CUSTOM_STAKE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_custom_stake)]},
    fallbacks=[CallbackQueryHandler(cancel_custom_stake, pattern="^duel_create_stake$")]
)

telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("tickets", tickets_command))
telegram_app.add_handler(CommandHandler("live", live_command))
telegram_app.add_handler(CommandHandler("give", admin_give))
telegram_app.add_handler(CommandHandler("take", admin_take))
telegram_app.add_handler(CommandHandler("stats", admin_stats))
telegram_app.add_handler(CommandHandler("sync", admin_sync))
telegram_app.add_handler(CommandHandler("resolve", admin_resolve))
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

@app.get("/")
def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("bot:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
