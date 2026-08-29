async def finalize_ticket_creation(query, context, user_id):
    ticket = USER_TICKETS.get(user_id)
    if not ticket:
        await query.edit_message_text("❌ Session expirée. Veuillez recommencer.")
        return
        
    # --- AJOUT AGILE : Feedback visuel immédiat ---
    await query.edit_message_text("⏳ **Génération de votre ticket en cours...**\nVeuillez patienter.", parse_mode="Markdown")

    predictions = ticket["predictions"]

    # --- Rejoindre un duel existant ---
    if ticket.get("mode") == "join":
        session_id = ticket["session_id"]
        session, msg = database.join_duel_session(session_id, user_id, predictions)
        del USER_TICKETS[user_id]

        if not session:
            await query.edit_message_text(f"❌ Erreur : {msg}")
            return

        await query.edit_message_text(
            "✅ **Duel accepté avec succès !**\n\nVotre ticket est enregistré. Vous recevrez une notification dès que tous les matchs seront terminés.",
            parse_mode="Markdown",
        )
        try:
            await context.bot.send_message(
                chat_id=session["creator_id"],
                text=(
                    "⚔️ **Un adversaire a rejoint votre duel !**\n\n"
                    f"🏆 **Cagnotte en jeu :** `{session['net_entry_fee'] * 2}` Coins\n"
                    "Le résultat sera calculé dès que les matchs seront terminés."
                ),
                parse_mode="Markdown",
            )
        except Exception:
            logging.exception("Impossible de notifier le créateur du duel")
        return

    # --- Création d'un nouveau duel ---
    stake = ticket["stake"]
    match_ids = [m["api_match_id"] for m in ticket["matches"]]
    session, msg = database.create_duel_session(user_id, stake, match_ids)
    
    if not session:
        await query.edit_message_text(f"❌ Erreur lors de la création : {msg}")
        return

    database.save_ticket(session["id"], user_id, predictions)

    bot_username = (await context.bot.get_me()).username
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
    del USER_TICKETS[user_id]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

