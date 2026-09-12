import re
from telegram import Update
from telegram.ext import ContextTypes
import config
from database.connection import supabase
from database.users import admin_take_coins, get_user_by_id, get_all_users, get_detailed_stats, get_api_quota
from database.sessions import set_match_result, find_resolvable_sessions, resolve_session, sync_matches_from_api_async, fetch_and_update_scores_for_resolution

def is_admin(user_id: int) -> bool:
    return user_id == config.ADMIN_TELEGRAM_ID

def _clean_number(raw: str) -> str:
    return re.sub(r"[^\d.]", "", raw)

async def _notify_normal_outcome(context: ContextTypes.DEFAULT_TYPE, outcome: dict):
    """Notifie chaque participant du résultat dans le cas normal (gagnant clair / podium).
    Les cas marginaux (égalité, redistribution) restent gérés séparément par les appelants."""
    if outcome.get("is_draw_refund"):
        return

    session_type = outcome["type"]
    scores = outcome["scores"]
    pot = outcome["pot"]
    winner_id = outcome.get("winner_id")

    if session_type == "DUEL":
        for s in scores:
            other = next((x for x in scores if x["user_id"] != s["user_id"]), {"correct": 0})
            if s["user_id"] == winner_id:
                text = f"🏆 **VICTOIRE !** 🏆\n\n`{s['correct']}` bons pronostics contre `{other['correct']}` — tu rafles la mise !\n💰 `+{pot}` Coins."
            else:
                text = f"💥 **DÉFAITE...** 💥\n\n`{s['correct']}` contre `{other['correct']}`. La revanche t'attend ! 🔁"
            try:
                await context.bot.send_message(chat_id=s["user_id"], text=text, parse_mode="Markdown")
            except Exception:
                pass
def save_ticket(session_id: str, user_id: int, predictions: list):
    formatted = [{"match_id": str(p["match_id"]), "pick": p["pick"], "odds": float(p.get("odds", 1.0))} for p in predictions]
    res = supabase.table("tickets").insert({
        "session_id": session_id, 
        "user_id": user_id, 
        "predictions": formatted, 
        "status": "PENDING"
    }).execute()
    return res.data[0]

def create_session(creator_id: int, session_type: str, gross_fee: int, match_count: int, max_participants: int, prize_mode: str, predictions: list):
    user = get_user_by_id(creator_id)
    if not user or int(user["coins_balance"]) < gross_fee:
        return None, "Solde insuffisant pour créer ce duel."

    rake_rate = config.RAKE_1V1 if session_type == "DUEL" else config.RAKE_ARENA
    net_fee = gross_fee - int(round(gross_fee * rake_rate))

    session_data = {
        "creator_id": creator_id,
        "type": session_type,
        "gross_entry_fee": gross_fee,
        "net_entry_fee": net_fee,
        "match_count": match_count,
        "max_participants": max_participants,
        "prize_mode": prize_mode,
        "status": "WAITING"
    }
    
    try:
        # Étape 1 : On crée la session
        session = supabase.table("sessions").insert(session_data).execute().data[0]
        
        # Étape 2 : On sauvegarde le ticket associé
        save_ticket(session["id"], creator_id, predictions)
        
        # Étape 3 : SÉCURITÉ FINANCIÈRE -> On débite seulement si les étapes 1 et 2 ont réussi
        new_balance = int(user["coins_balance"]) - gross_fee
        supabase.table("users").update({"coins_balance": new_balance}).eq("telegram_id", creator_id).execute()
        
        return session, "Succès"
    except Exception as e:
        # Si la base de données plante, la fonction s'arrête et le joueur garde son argent
        return None, f"Erreur système lors de la création : {str(e)}"

    if not is_admin(update.effective_user.id): return
    args = context.args
    if len(args) != 2 or args[1].upper() not in ("HOME", "DRAW", "AWAY", "CANCEL"):
        await update.message.reply_text("❌ Usage : /resolve [api_match_id] [HOME|DRAW|AWAY|CANCEL]")
        return

    api_match_id = _clean_number(args[0])
    result = args[1].upper()
    set_match_result(api_match_id, result)

    resolvable = find_resolvable_sessions(api_match_id)
    resolved_count = 0
    for session in resolvable:
        outcome = resolve_session(session["id"])
        if outcome:
            resolved_count += 1
            await _notify_normal_outcome(context, outcome)
            for notif in outcome.get("notifications", []):
                try: await context.bot.send_message(chat_id=notif["user_id"], text=notif["text"])
                except Exception: pass

            if outcome.get("is_draw_refund"):
                for s_score in outcome.get("scores", []):
                    try: await context.bot.send_message(chat_id=s_score["user_id"], text="🤝 Égalité parfaite ! Votre mise vous a été intégralement remboursée.")
                    except Exception: pass

    await update.message.reply_text(f"✅ Résultat enregistré. 🏁 {resolved_count} session(s) tranchée(s).")

async def admin_resolve_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("❌ Usage : /resolve_session [id_de_la_session]")
        return
    
    session_id = context.args[0]
    session = supabase.table("sessions").select("*").eq("id", session_id).execute()
    if not session.data:
        await update.message.reply_text("❌ Session introuvable.")
        return
    
    sess_obj = session.data[0]
    if sess_obj["status"] != "IN_PROGRESS":
        await update.message.reply_text(f"⚠️ La session doit être 'IN_PROGRESS' (Actuelle : {sess_obj['status']}).")
        return

    msg = await update.message.reply_text("⏳ Récupération des scores finaux depuis The Odds API (jusqu'à J-3)...")
    success, info_msg = await fetch_and_update_scores_for_resolution(session_id)
    
    outcome = resolve_session(session_id)
    if outcome:
        await _notify_normal_outcome(context, outcome)
        for notif in outcome.get("notifications", []):
            try: await context.bot.send_message(chat_id=notif["user_id"], text=notif["text"])
            except Exception: pass
            
        if outcome.get("is_draw_refund"):
            for s_score in outcome.get("scores", []):
                try: await context.bot.send_message(chat_id=s_score["user_id"], text="🤝 Égalité parfaite ! Remboursement effectué.")
                except Exception: pass
                
        await msg.edit_text(f"✅ Session tranchée !\nℹ️ {info_msg}\n💰 Cagnotte distribuée.")
    else:
        await msg.edit_text(f"⚠️ {info_msg}\nTous les matchs ne sont pas terminés.")

async def admin_give(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("❌ Usage : /give [id_ou_code] [montant]")
        return
    
    target_raw, amount_str = args[0], _clean_number(args[1])
    try:
        amount = int(amount_str)
        if target_raw.isdigit() and len(target_raw) == 5:
            res = supabase.table("users").select("*").eq("player_code", target_raw).execute().data
            user = res[0] if res else None
        else:
            user = get_user_by_id(int(target_raw))

        if not user:
            await update.message.reply_text("❌ Utilisateur introuvable.")
            return

        from database.users import credit_balance
        new_bal = credit_balance(user["telegram_id"], amount)
        await update.message.reply_text(f"✅ `{amount}` Coins ajoutés à **{user['username']}**. Nouveau solde : `{new_bal}`.")
    except Exception as e:
        await update.message.reply_text(f"❌ Erreur : {str(e)}")

async def admin_take(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("❌ Usage : /take [id_ou_code] [montant]")
        return
    try:
        target_raw, amount = args[0], int(_clean_number(args[1]))
        if target_raw.isdigit() and len(target_raw) == 5:
            res = supabase.table("users").select("*").eq("player_code", target_raw).execute().data
            user = res[0] if res else None
        else:
            user = get_user_by_id(int(target_raw))

        if not user:
            await update.message.reply_text("❌ Utilisateur introuvable.")
            return

        new_bal = admin_take_coins(user["telegram_id"], amount)
        await update.message.reply_text(f"✅ Prélèvement effectué. Nouveau solde : `{new_bal}`.")
    except Exception as e:
        await update.message.reply_text(f"❌ Erreur : {str(e)}")

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    stats = get_detailed_stats()
    quota = get_api_quota()
    
    text = (
        "📊 **STATISTIQUES DE LA PLATEFORME**\n\n"
        f"👥 Joueurs inscrits : `{stats.get('total_users', 0)}`\n"
        f"💰 Coins en circulation : `{stats.get('total_coins', 0)}`\n"
        f"🎟️ Tickets créés : `{stats.get('total_tickets', 0)}`\n"
        f"🟡 Salons en attente : `{stats.get('waiting_sessions', 0)}`\n"
        f"🔵 Duels / Arenas en cours : `{stats.get('active_sessions', 0)}`\n"
        f"🏁 Sessions terminées : `{stats.get('completed_sessions', 0)}`\n\n"
        f"🔌 **Quota The Odds API restant :** `{quota}`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def admin_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await update.message.reply_text("⏳ Synchronisation avec Odds-API en cours...")
    count, msg = await sync_matches_from_api_async()
    await update.message.reply_text(f"🔄 Résultat : {msg}")

async def admin_sweep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    from database.sessions import cancel_expired_sessions
    cancel_expired_sessions()
    await update.message.reply_text("🧹 Nettoyage des sessions expirées (>24h) effectué.")

async def admin_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    msg_text = " ".join(context.args)
    if not msg_text:
        await update.message.reply_text("❌ Usage : /alert [message]")
        return
    
    users = get_all_users()
    sent = 0
    for u in users:
        try:
            await context.bot.send_message(chat_id=u["telegram_id"], text=f"📢 **ANNONCE CLASHSPORT**\n\n{msg_text}", parse_mode="Markdown")
            sent += 1
        except Exception: pass
    await update.message.reply_text(f"📢 Diffusé à {sent}/{len(users)} joueurs.")
