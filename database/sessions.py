import logging
from telegram import Update
from telegram.ext import ContextTypes
import config
from database.connection import supabase
from database.users import get_user_by_id, get_user_by_code, update_user_balance
from database.sessions import (
    get_session, 
    cancel_expired_sessions, 
    get_top_leaderboard, 
    distribute_top_rewards,
    get_tickets_for_session
)

logger = logging.getLogger(__name__)

def is_admin(user_id: int) -> bool:
    """Vérifie si l'utilisateur est l'administrateur configuré."""
    return user_id == config.ADMIN_TELEGRAM_ID

async def admin_give(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /give [ID_ou_Code] [Montant] : Crédite des Coins à un joueur."""
    if not is_admin(update.effective_user.id):
        return

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
    await update.message.reply_text(
        f"✅ `{amount}` Coins ajoutés au compte de **{user.get('username', 'Joueur')}** (`{user['user_code']}`).",
        parse_mode="Markdown"
    )

async def admin_take(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /take [ID_ou_Code] [Montant] : Prélève des Coins à un joueur."""
    if not is_admin(update.effective_user.id):
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text("⚠️ Usage : `/take [telegram_id_ou_code] [montant]`", parse_mode="Markdown")
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

    update_user_balance(user["telegram_id"], -amount)
    await update.message.reply_text(
        f"✅ `{amount}` Coins retirés du compte de **{user.get('username', 'Joueur')}** (`{user['user_code']}`).",
        parse_mode="Markdown"
    )

async def admin_reward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /reward [Montant_Total] : Distribue la cagnotte du Top 10 automatisé."""
    if not is_admin(update.effective_user.id):
        return

    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("⚠️ Usage : `/reward [montant_total_cagnotte]`\nExemple : `/reward 10000`", parse_mode="Markdown")
        return

    total_pool = int(args[0])
    success, summary = distribute_top_rewards(total_pool)

    if not success:
        await update.message.reply_text(f"❌ Erreur de distribution : {summary}")
        return

    text = f"🏆 **DISTRIBUTION DU TOP 10 EFFECTUÉE !**\n\nCagnotte totale distribuée : `{total_pool}` Coins\n\n{summary}"
    await update.message.reply_text(text, parse_mode="Markdown")

async def admin_sweep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /sweep : Force l'annulation et le remboursement des salons expirer (+24h)."""
    if not is_admin(update.effective_user.id):
        return

    cancel_expired_sessions()
    await update.message.reply_text("🧹 Nettoyage des salons expirés effectué. Les créateurs ont été remboursés.")

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /stats : Tableau de bord de la plateforme."""
    if not is_admin(update.effective_user.id):
        return

    users_count = len(supabase.table("users").select("telegram_id").execute().data or [])
    active_sessions = len(supabase.table("sessions").select("id").eq("status", "IN_PROGRESS").execute().data or [])
    waiting_sessions = len(supabase.table("sessions").select("id").eq("status", "WAITING").execute().data or [])
    
    # Compte Don ❤️
    don_account = get_user_by_id(0)
    don_balance = don_account.get("coins_balance", 0) if don_account else 0

    text = (
        "📊 **Statistiques Générales Clashsport**\n\n"
        f"👤 Joueurs inscrits : `{users_count}`\n"
        f"🔴 Sessions en cours : `{active_sessions}`\n"
        f"⏳ Salons en attente : `{waiting_sessions}`\n"
        f"❤️ Solde Fonds Don Solidaire : `{don_balance}` Coins"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def admin_resolve_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /resolve_session [Code_Session_7_char] [WINNER_ID_ou_DRAW] : Résolution manuelle d'un litige."""
    if not is_admin(update.effective_user.id):
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text("⚠️ Usage : `/resolve_session [code_session_7_char] [winner_telegram_id|DRAW]`", parse_mode="Markdown")
        return

    session_code, outcome = args[0].upper(), args[1].upper()
    session = get_session(session_code)

    if not session:
        await update.message.reply_text("❌ Session introuvable.")
        return

    tickets = get_tickets_for_session(session["id"])
    pot = session["gross_entry_fee"] * len(tickets)

    if outcome == "DRAW":
        # Remboursement égal
        refund = session["gross_entry_fee"]
        for t in tickets:
            update_user_balance(t["user_id"], refund)
            supabase.table("tickets").update({"status": "CANCELLED"}).eq("id", t["id"]).execute()
        supabase.table("sessions").update({"status": "CANCELLED"}).eq("id", session["id"]).execute()
        await update.message.reply_text(f"⚖️ Session `{session_code}` annulée. Joueurs remboursés.")
    else:
        winner_id = int(outcome)
        # Rake de 8%
        net_pot = int(pot * 0.92)
        update_user_balance(winner_id, net_pot)
        
        for t in tickets:
            status = "WON" if t["user_id"] == winner_id else "LOST"
            supabase.table("tickets").update({"status": status}).eq("id", t["id"]).execute()
            
        supabase.table("sessions").update({"status": "COMPLETED"}).eq("id", session["id"]).execute()
        await update.message.reply_text(f"✅ Session `{session_code}` tranchée. Vainqueur `{winner_id}` crédité de `{net_pot}` Coins.")

async def admin_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /alert [Message] : Diffusion d'une annonce à tous les utilisateurs."""
    if not is_admin(update.effective_user.id):
        return

    msg_text = " ".join(context.args)
    if not msg_text:
        await update.message.reply_text("⚠️ Usage : `/alert [Votre message ici]`", parse_mode="Markdown")
        return

    all_users = supabase.table("users").select("telegram_id").execute().data or []
    sent_count = 0

    for u in all_users:
        uid = u["telegram_id"]
        if uid == 0:
            continue
        try:
            await context.bot.send_message(chat_id=uid, text=f"📢 **ANNONCE CLASHSPORT**\n\n{msg_text}", parse_mode="Markdown")
            sent_count += 1
        except Exception:
            pass

    await update.message.reply_text(f"✅ Annonce envoyée à `{sent_count}` joueurs.")
