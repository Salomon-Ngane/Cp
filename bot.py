import os
import re
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
    await query.edit_message_text("⏳ **Génération de votre ticket en cours...**\nVeuillez patienter.", parse_mode="Markdown")

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


# ==========================================
# 1. INITIALISATION DU PANIER (CREATION DUEL)
# ==========================================

async def start_create_duel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initialise le panier de sélection dans context.user_data."""
    query = update.callback_query
    if query:
        await query.answer()

    # Initialisation de la structure du panier temporaire
    context.user_data["draft_duel"] = {
        "match_count": 3,       # Nombre par défaut de matchs à sélectionner
        "stake": 100,           # Mise par défaut en Coins
        "selected_matches": {}, # Format : { match_id: {"match": dict, "pick": "HOME"|"DRAW"|"AWAY"} },
        "current_sport": "FOOTBALL"
    }
    
    await show_sport_selection_menu(update, context)


async def show_sport_selection_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche le menu de sélection des sports avec l'avancement du panier."""
    draft = context.user_data.get("draft_duel", {})
    selected_count = len(draft.get("selected_matches", {}))
    target_count = draft.get("match_count", 3)
    stake = draft.get("stake", 100)

    text = (
        f"🏆 **Création de Duel Multi-Sports**\n\n"
        f"📊 Progression du panier : **{selected_count} / {target_count} match(s)**\n"
        f"💰 Mise configurée : **{stake} Coins**\n\n"
        f"Choisissez une discipline ci-dessous pour composer votre ticket :"
    )

    keyboard = [
        [
            InlineKeyboardButton("⚽ Football", callback_data="sport_FOOTBALL"),
            InlineKeyboardButton("🏀 Basket", callback_data="sport_BASKETBALL"),
            InlineKeyboardButton("🎾 Tennis", callback_data="sport_TENNIS"),
        ],
        [
            InlineKeyboardButton(f"⚙️ Ajuster taille ({target_count} matchs)", callback_data="config_count"),
            InlineKeyboardButton(f"💵 Ajuster mise ({stake} coins)", callback_data="config_stake")
        ]
    ]

    # Bouton de confirmation affiché uniquement si le panier est complet
    if selected_count == target_count:
        keyboard.append([InlineKeyboardButton("✅ Valider & Récapitulatif", callback_data="review_ticket")])
    
    keyboard.append([InlineKeyboardButton("❌ Annuler", callback_data="cancel_creation")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")


# ==========================================
# 2. AFFICHAGE ET SELECTION DES MATCHS
# ==========================================

async def handle_sport_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche la liste des matchs d'un sport avec leur état de sélection et la barre d'action."""
    query = update.callback_query
    await query.answer()

    # Extraction du sport sélectionné
    sport = query.data.split("_")[1]
    if "draft_duel" not in context.user_data:
        context.user_data["draft_duel"] = {
            "match_count": 3,
            "stake": 100,
            "selected_matches": {},
            "current_sport": sport
        }
    else:
        context.user_data["draft_duel"]["current_sport"] = sport

    draft = context.user_data.get("draft_duel", {})
    selected_matches = draft.get("selected_matches", {})
    target_count = draft.get("match_count", 3)
    selected_count = len(selected_matches)

    matches = database.get_matches_by_sport(sport)

    keyboard = []

    if not matches:
        text = f"❌ Aucun match disponible actuellement pour le **{sport}**."
    else:
        text = (
            f"🏟️ **Matchs — {sport}**\n"
            f"📊 Progression : **{selected_count} / {target_count} match(s)**\n\n"
            f"Cliquez sur 1, N ou 2 pour ajouter/retirer un choix :\n\n"
        )

        for m in matches:
            m_id = int(m["api_match_id"])
            is_selected = m_id in selected_matches
            current_pick = selected_matches[m_id]["pick"] if is_selected else None

            # Boutons 1 - N - 2 avec coche de confirmation visuelle
            btn_h = f"1 ({m.get('home_odds', 1.0)})" + (" ✅" if current_pick == "HOME" else "")
            btn_d = f"N ({m.get('draw_odds', 1.0)})" + (" ✅" if current_pick == "DRAW" else "")
            btn_a = f"2 ({m.get('away_odds', 1.0)})" + (" ✅" if current_pick == "AWAY" else "")

            # Intitulé du match
            keyboard.append([InlineKeyboardButton(f"⚽ {m['home_team']} vs {m['away_team']}", callback_data=f"ignore_{m_id}")])
            # Ligne des cotes
            keyboard.append([
                InlineKeyboardButton(btn_h, callback_data=f"pick_{m_id}_HOME"),
                InlineKeyboardButton(btn_d, callback_data=f"pick_{m_id}_DRAW"),
                InlineKeyboardButton(btn_a, callback_data=f"pick_{m_id}_AWAY"),
            ])

    # --- BARRE DE NAVIGATION EN BAS DU MENU ---
    navigation_row = []

    # Le bouton de récapitulatif s'affiche dès que le quota est atteint
    if selected_count == target_count:
        keyboard.append([InlineKeyboardButton("✅ Valider & Récapitulatif", callback_data="review_ticket")])

    # Bouton d'annulation et de retour au menu des sports toujours présents
    keyboard.append([
        InlineKeyboardButton("🔙 Menu Sports", callback_data="back_to_sports"),
        InlineKeyboardButton("❌ Annuler", callback_data="cancel_creation")
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def handle_pick_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère l'ajout/suppression ou la modification d'un choix dans le panier."""
    query = update.callback_query
    await query.answer()

    _, match_id_str, pick = query.data.split("_")
    match_id = int(match_id_str)

    draft = context.user_data.get("draft_duel", {})
    selected = draft.get("selected_matches", {})
    target_count = draft.get("match_count", 3)

    # Si le choix existait déjà pour ce match, on le décoche (toggle)
    if match_id in selected and selected[match_id]["pick"] == pick:
        del selected[match_id]
    else:
        # Vérification si le quota maximum est déjà atteint
        if len(selected) >= target_count and match_id not in selected:
            await query.answer(f"⚠️ Vous avez déjà sélectionné vos {target_count} matchs !", show_alert=True)
            return

        # Récupération des données du match et stockage
        matches = database.get_matches_by_ids([match_id])
        if matches:
            selected[match_id] = {
                "match": matches[0],
                "pick": pick
            }

    # Rafrachit la liste des matchs pour mettre à jour l'affichage des boutons
    await handle_sport_selection(update, context)


async def handle_ignore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ignore les clics sur les en-têtes de matchs et affiche le détail rapide du match + actions."""
    query = update.callback_query
    await query.answer()

    # Récupère l'id de match depuis le callback data ignore_<id>
    try:
        m_id = int(query.data.split("_")[1])
    except Exception:
        await query.answer("Identifiant de match invalide.", show_alert=True)
        return

    matches = database.get_matches_by_ids([m_id])
    if not matches:
        await query.edit_message_text("❌ Match introuvable ou expiré.")
        return

    m = matches[0]
    text = (
        f"🏟️ **Détail du match**\n\n"
        f"**{m['home_team']}** vs **{m['away_team']}**\n"
        f"Sport: {m.get('sport', 'N/A')}\n"
        f"Heure: {m.get('start_time', 'N/A')}\n"
        f"Cotes: 1 ({m.get('home_odds', 1.0)}) — N ({m.get('draw_odds', 1.0)}) — 2 ({m.get('away_odds', 1.0)})\n\n"
        "Choisissez un pronostic ci-dessous :"
    )

    keyboard = [
        [
            InlineKeyboardButton(f"1 ({m.get('home_odds', 1.0)})", callback_data=f"pick_{m_id}_HOME"),
            InlineKeyboardButton(f"N ({m.get('draw_odds', 1.0)})", callback_data=f"pick_{m_id}_DRAW"),
            InlineKeyboardButton(f"2 ({m.get('away_odds', 1.0)})", callback_data=f"pick_{m_id}_AWAY"),
        ],
        [InlineKeyboardButton("🔙 Retour aux matchs", callback_data=f"sport_{m.get('sport','FOOTBALL')}")]
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def cancel_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Annule la création et vide le panier temporaire."""
    query = update.callback_query
    await query.answer("Création annulée.")
    context.user_data.pop("draft_duel", None)
    await query.edit_message_text("❌ Création du duel annulée.")


# ==========================================
# 3. RECAPITULATIF ET VALIDATION FINALE
# ==========================================

async def show_ticket_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche le récapitulatif complet du duel avant confirmation."""
    query = update.callback_query
    await query.answer()

    draft = context.user_data.get("draft_duel", {})
    selected = draft.get("selected_matches", {})
    stake = draft.get("stake", 100)

    text = f"📋 **Récapitulatif de votre Ticket**\n\n"
    text += f"💰 **Mise :** {stake} Coins\n"
    text += f"🎯 **Nombre de sélections :** {len(selected)}\n\n"
    text += "--- **Vos Pronostics** ---\n"

    for m_id, item in selected.items():
        m = item["match"]
        pick = item["pick"]
        pick_label = m["home_team"] if pick == "HOME" else (m["away_team"] if pick == "AWAY" else "Nul")
        text += f"• **[{m['sport']}]** {m['home_team']} vs {m['away_team']} ➔ **{pick_label}**\n"

    keyboard = [
        [InlineKeyboardButton("🚀 Confirmer & Débiter", callback_data="confirm_duel_creation")],
        [InlineKeyboardButton("✏️ Modifier mes sélections", callback_data="back_to_sports")],
        [InlineKeyboardButton("❌ Annuler tout", callback_data="cancel_creation")]
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def confirm_duel_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Valide la création du duel en base de données et débite le joueur."""
    query = update.callback_query
    await query.answer()

    telegram_id = update.effective_user.id
    user = database.get_user(telegram_id)
    draft = context.user_data.get("draft_duel", {})
    stake = draft.get("stake", 100)
    selected = draft.get("selected_matches", {})

    # 1. Vérification du solde utilisateur
    if user.get("coins_balance", 0) < stake:
        await query.edit_message_text(
            # Assurez-vous que les accolades et guillemets sont bien appairés :
f"❌ Solde insuffisant ! Vous avez **{user.get('coins_balance', 0)} Coins** mais la mise est de **{stake} Coins}."

            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Retour au récapitulatif", callback_data="review_ticket")]])
        )
        return

    # 2. Préparation du payload de prédictions pour database.py
    creator_predictions = {
        str(m_id): item["pick"] for m_id, item in selected.items()
    }

    try:
        # Création de la session en base
        session = database.create_duel_session(
            creator_telegram_id=telegram_id,
            stake=stake,
            match_count=len(selected),
            creator_predictions=creator_predictions
        )
        
        # Nettoyage du panier
        context.user_data.pop("draft_duel", None)

        text = (
            f"🎉 **Duel créé avec succès !**\n\n"
            f"🆔 ID du Duel : `{session['id']}`\n"
            f"💰 Stake engagé : **{stake} Coins**\n\n"
            f"Partagez cet ID à votre adversaire pour qu'il le rejoigne via `/join {session['id']}` !"
        )
        await query.edit_message_text(text, parse_mode="Markdown")

    except Exception as e:
        await query.edit_message_text(f"❌ Erreur lors de la création du duel : {str(e)}")


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
telegram_app.add_handler(CommandHandler("resolve", resolve_match_command))
telegram_app.add_handler(CallbackQueryHandler(button_handler))
# Handlers ajoutés pour la création multi-sports
telegram_app.add_handler(CallbackQueryHandler(start_create_duel, pattern="^create_duel$"))
telegram_app.add_handler(CallbackQueryHandler(show_sport_selection_menu, pattern="^back_to_sports$"))
telegram_app.add_handler(CallbackQueryHandler(handle_sport_selection, pattern="^sport_"))
telegram_app.add_handler(CallbackQueryHandler(handle_pick_selection, pattern="^pick_"))
telegram_app.add_handler(CallbackQueryHandler(show_ticket_review, pattern="^review_ticket$"))
telegram_app.add_handler(CallbackQueryHandler(confirm_duel_creation, pattern="^confirm_duel_creation$"))
# Handler pour l'en-tête des matchs (ignore) et annulation
telegram_app.add_handler(CallbackQueryHandler(handle_ignore, pattern="^ignore_"))
telegram_app.add_handler(CallbackQueryHandler(cancel_creation, pattern="^cancel_creation$"))

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
