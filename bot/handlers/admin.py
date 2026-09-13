import logging
from telegram import Update
from telegram.ext import ContextTypes
import config
from database.connection import supabase
from services.user_service import get_user_by_id, get_user_by_code, update_user_balance
from services.session_service import (
    get_session, 
    cancel_expired_sessions, 
    distribute_top_rewards,
    get_tickets_for_session
)

logger = logging.getLogger(__name__)
# (Le reste du code de admin.py ne change pas)


logger = logging.getLogger(__name__)

def is_admin(user_id: int) -> bool:
    """Vérifie si l'utilisateur est l'administrateur configuré."""
    return user_id == config.ADMIN_TELEGRAM_ID

async def admin_give(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /give [ID_ou_Code] [Montant] : Crédite des Coins à un joueur."""
    if not is_admin(update.effective_user.id): return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text("⚠️ Usage : `/give [telegram_id_ou_code] [montant]`", parse_mode="Markdown")
        return

    target_input, amount_str = args[0], args[1]
    if not amount_str.isdigit():
        await update.message.reply_text("❌ Le montant doit être un nombre entier positif.")
        return

    amount = int(amount_str)
    user = get_user_by_code(target_input.upper()) if not target_input.isdigit() else get_user_by_id(int(target_input))

    if not user:
        await update.message.reply_text("❌ Utilisateur introuvable.")
        return

    update_user_balance(user["telegram_id"], amount)
    await update.message.reply_text(f"✅ `{amount}` Coins ajoutés au compte de **{user.get('username', 'Joueur')}**.", parse_mode="Markdown")

async def admin_take(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /take [ID_ou_Code] [Montant] : Prélève des Coins à un joueur."""
    if not is_admin(update.effective_user.id): return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text("⚠️ Usage : `/take [telegram_id_ou_code] [montant]`", parse_mode="Markdown")
        return

    target_input, amount_str = args[0], args[1]
    if not amount_str.isdigit():
        await update.message.reply_text("❌ Le montant doit être un nombre entier.")
        return

    amount = int(amount_str)
    user = get_user_by_code(target_input.upper()) if not target_input.isdigit() else get_user_by_id(int(target_input))

    if not user:
        await update.message.reply_text("❌ Utilisateur introuvable.")
        return

    update_user_balance(user["telegram_id"], -amount)
    await update.message.reply_text(f"✅ `{amount}` Coins retirés du compte de **{user.get('username', 'Joueur')}**.", parse_mode="Markdown")

async def admin_reward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Commande /reward [categorie] [periode] [montant] [+ item X]
    Exemple: /reward network week 10000 + item 2
    """
    if not is_admin(update.effective_user.id): return

    args = context.args
    if len(args) < 3:
        usage_text = (
            "⚠️ **Usage :** `/reward [categorie] [periode] [montant] [+ item X]`\n"
            "🔹 *Catégories :* `winrate`, `network`, `volume`\n"
            "🔹 *Périodes :* `day`, `week`, `month`\n"
            "🔹 *Exemple :* `/reward network week 10000 + item 2`"
        )
        await update.message.reply_text(usage_text, parse_mode="Markdown")
        return

    category = args[0].lower()
    period = args[1].lower()
    amount_str = args[2]

    if category not in ["winrate", "network", "volume"] or period not in ["day", "week", "month"] or not amount_str.isdigit():
        await update.message.reply_text("❌ Paramètres invalides. Vérifiez la catégorie, la période et le montant.")
        return

    amount = int(amount_str)
    bonus_item = None

    # Détection de l'ajout d'un Item (ex: "+ item 2")
    rest_of_args = " ".join(args[3:]).lower()
    if "+ item 1" in rest_of_args: bonus_item = 1
    elif "+ item 2" in rest_of_args: bonus_item = 2
    elif "+ item 3" in rest_of_args: bonus_item = 3

    # Lancement de la distribution
    success, summary = distribute_top_rewards(category, period, amount, bonus_item)

    if not success:
        await update.message.reply_text(summary) # Affiche le message d'erreur (ex: volume insuffisant)
        return

    text = f"🏆 **DISTRIBUTION EFFECTUÉE !**\n\n🎯 Catégorie : `{category.upper()}` | 📅 Période : `{period.upper()}`\n💰 Cagnotte totale : `{amount}` Coins\n\n{summary}"
    await update.message.reply_text(text, parse_mode="Markdown")

async def admin_sweep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /sweep : Nettoie les salons expirés (+24h)."""
    if not is_admin(update.effective_user.id): return
    cancel_expired_sessions()
    await update.message.reply_text("🧹 Nettoyage des salons expirés effectué. Les créateurs ont été remboursés.")

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /stats : Tableau de bord."""
    if not is_admin(update.effective_user.id): return

    users_count = len(supabase.table("users").select("telegram_id").execute().data or [])
    active_sessions = len(supabase.table("sessions").select("id").eq("status", "IN_PROGRESS").execute().data or [])
    don_account = get_user_by_id(0)
    don_balance = don_account.get("coins_balance", 0) if don_account else 0

    text = (
        "📊 **Statistiques Générales Clashsport**\n\n"
        f"👤 Joueurs inscrits : `{users_count}`\n"
        f"🔴 Sessions en cours : `{active_sessions}`\n"
        f"❤️ Solde Don Solidaire : `{don_balance}` Coins"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def admin_resolve_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /resolve_session [code_session_7_char] [WINNER_ID_ou_DRAW]"""
    if not is_admin(update.effective_user.id): return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text("⚠️ Usage : `/resolve_session [code_session] [winner_id|DRAW]`", parse_mode="Markdown")
        return

    session_code, outcome = args[0].upper(), args[1].upper()
    session = get_session(session_code)

    if not session:
        await update.message.reply_text("❌ Session introuvable.")
        return

    tickets = get_tickets_for_session(session["id"])
    pot = session["gross_entry_fee"] * len(tickets)

    if outcome == "DRAW":
        refund = session["gross_entry_fee"]
        for t in tickets:
            update_user_balance(t["user_id"], refund)
            supabase.table("tickets").update({"status": "CANCELLED"}).eq("id", t["id"]).execute()
        supabase.table("sessions").update({"status": "CANCELLED"}).eq("id", session["id"]).execute()
        await update.message.reply_text(f"⚖️ Session `{session_code}` annulée. Joueurs remboursés.")
    else:
        winner_id = int(outcome)
        net_pot = int(pot * 0.92) # 8% de rake
        update_user_balance(winner_id, net_pot)
        
        for t in tickets:
            status = "WON" if t["user_id"] == winner_id else "LOST"
            supabase.table("tickets").update({"status": status}).eq("id", t["id"]).execute()
            
        supabase.table("sessions").update({"status": "COMPLETED"}).eq("id", session["id"]).execute()
        await update.message.reply_text(f"✅ Session `{session_code}` tranchée. Vainqueur `{winner_id}` crédité de `{net_pot}` Coins.")

async def admin_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /alert [Message]"""
    if not is_admin(update.effective_user.id): return
    msg_text = " ".join(context.args)
    if not msg_text:
        await update.message.reply_text("⚠️ Usage : `/alert [Votre message ici]`", parse_mode="Markdown")
        return

    all_users = supabase.table("users").select("telegram_id").execute().data or []
    sent_count = 0
    for u in all_users:
        uid = u["telegram_id"]
        if uid == 0: continue
        try:
            await context.bot.send_message(chat_id=uid, text=f"📢 **ANNONCE CLASHSPORT**\n\n{msg_text}", parse_mode="Markdown")
            sent_count += 1
        except Exception: pass

    await update.message.reply_text(f"✅ Annonce envoyée à `{sent_count}` joueurs.")
