from telegram import Update
from telegram.ext import ContextTypes
import config
from database.connection import supabase

# --- NOUVEAUX IMPORTS (Architecture Services) ---
from services.user_service import admin_take_coins, get_user_by_id, get_user_by_code, get_all_users, get_detailed_stats, credit_balance, award_item
from services.session_service import set_match_result, find_resolvable_sessions, resolve_session, sync_matches_from_api_async, fetch_and_update_scores_for_resolution, get_api_quota

def is_admin(user_id: int) -> bool:
    return user_id == config.ADMIN_TELEGRAM_ID

async def _notify_normal_outcome(context: ContextTypes.DEFAULT_TYPE, outcome: dict):
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
        return

    ranked = sorted(scores, key=lambda x: (-x["correct"], -x["valid_odds"]))
    rank_by_user = {s["user_id"]: i + 1 for i, s in enumerate(ranked)}
    payout_pct = {1: "50%", 2: "38%", 3: "12%"}
    for s in scores:
        rank = rank_by_user[s["user_id"]]
        if rank <= 3 and s["correct"] > 0:
            text = f"🏆 **PODIUM ! Tu termines #{rank}** 🏆\n\n`{s['correct']}` bons pronostics — ta part de la cagnotte ({payout_pct.get(rank, '')}) a été créditée. 🎉"
        elif rank <= 3 and s["correct"] == 0:
            continue
        else:
            text = f"📊 **Résultat de l'Arène**\n\nTu termines #{rank} avec `{s['correct']}` bons pronostics. Retente ta chance ! 💪"
        try:
            await context.bot.send_message(chat_id=s["user_id"], text=text, parse_mode="Markdown")
        except Exception:
            pass

async def admin_resolve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    args = context.args
    if len(args) != 2 or args[1].upper() not in ("HOME", "DRAW", "AWAY", "CANCEL"):
        await update.message.reply_text("❌ Usage : /resolve [api_match_id] [HOME|DRAW|AWAY|CANCEL]")
        return

    api_match_id = args[0].strip()
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
    
    target_raw = args[0].strip().upper()
    try:
        amount = int(args[1])
        # Détection dynamique : code alphanumérique (7) vs Telegram ID
        user = get_user_by_code(target_raw) if len(target_raw) == 7 else get_user_by_id(int(target_raw))

        if not user:
            await update.message.reply_text("❌ Utilisateur introuvable.")
            return

        new_bal = credit_balance(user["telegram_id"], amount)
        await update.message.reply_text(f"✅ `{amount}` Coins ajoutés à **{user['username']}**. Nouveau solde : `{new_bal}`.")
    except ValueError:
        await update.message.reply_text("❌ Le montant ou l'ID est invalide.")
    except Exception as e:
        await update.message.reply_text(f"❌ Erreur : {str(e)}")

async def admin_take(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("❌ Usage : /take [id_ou_code] [montant]")
        return
        
    target_raw = args[0].strip().upper()
    try:
        amount = int(args[1])
        # Détection dynamique : code alphanumérique (7) vs Telegram ID
        user = get_user_by_code(target_raw) if len(target_raw) == 7 else get_user_by_id(int(target_raw))

        if not user:
            await update.message.reply_text("❌ Utilisateur introuvable.")
            return

        new_bal = admin_take_coins(user["telegram_id"], amount)
        await update.message.reply_text(f"✅ Prélèvement effectué. Nouveau solde : `{new_bal}`.")
    except ValueError:
        await update.message.reply_text("❌ Le montant ou l'ID est invalide.")
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
    # Mise à jour de l'import interne pour correspondre à la nouvelle architecture
    from services.session_service import cancel_expired_sessions
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

async def admin_reward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Usage: /reward [winrate|network|volume] [24h|7d|30d] [pot_amount] [item_id_optional]
    Distribue la cagnotte au Top 5 et les items au Top 10.
    """
    if not is_admin(update.effective_user.id): return
    args = context.args
    if len(args) < 3:
        await update.message.reply_text("❌ Usage : /reward [category] [period] [amount] [item_id_optional]")
        return
        
    category, period = args[0].lower(), args[1].lower()
    try:
        pot_amount = int(args[2])
        bonus_item_id = int(args[3]) if len(args) == 4 else None
    except ValueError:
        await update.message.reply_text("❌ Le montant et l'ID de l'item doivent être des nombres entiers.")
        return

    from services.session_service import get_dynamic_leaderboard
    leaderboard = get_dynamic_leaderboard(category, period, limit=10)
    
    if not leaderboard:
        await update.message.reply_text("⚠️ Aucun joueur éligible pour cette période/catégorie.")
        return

    payouts = [0.40, 0.25, 0.18, 0.10, 0.07]
    report = f"🏆 **RÉCOMPENSES DISTRIBUÉES** ({category.upper()} - {period.upper()})\n\n"
    
    for idx, player in enumerate(leaderboard):
        user_id = player["telegram_id"]
        rank = idx + 1
        
        # 1. Distribution des Coins (Top 5)
        if rank <= 5:
            coins_won = int(pot_amount * payouts[idx])
            credit_balance(user_id, coins_won)
            report += f"#{rank} {player['username']} : `{coins_won}` Coins\n"
        
        # 2. Distribution de l'Item 1 (+0.5 Cote) pour les rangs 6 à 10
        if 6 <= rank <= 10:
            award_item(user_id, item_type=1)
            report += f"#{rank} {player['username']} : Item 1 (+0.5 Cote)\n"
            
        # 3. Distribution de l'Item Bonus (Top 10 global) si spécifié
        if bonus_item_id:
            award_item(user_id, item_type=bonus_item_id)
            
        # Notification individuelle
        try:
            msg = f"🎉 **RÉCOMPENSE CLASSEMENT** 🎉\nTu as terminé #{rank} de la catégorie {category.upper()} !"
            if rank <= 5: msg += f"\n💰 Tu as reçu {coins_won} Coins."
            if 6 <= rank <= 10: msg += "\n🎒 Un Item 1 (+0.5 Cote) a été ajouté à ton sac."
            if bonus_item_id: msg += f"\n🎁 Objet spécial (Item {bonus_item_id}) reçu !"
            await context.bot.send_message(chat_id=user_id, text=msg)
        except Exception: pass
            
    await update.message.reply_text(report)
