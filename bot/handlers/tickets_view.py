from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database.sessions import cancel_expired_sessions, get_user_sessions, get_session, get_tickets_for_session, get_matches_by_ids
from database.users import get_user_by_id
from bot.ui import main_menu_keyboard

async def user_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cancel_expired_sessions()
    sessions = get_user_sessions(user_id)
    if not sessions:
        await update.message.reply_text("📭 Aucun ticket pour l'instant.", reply_markup=main_menu_keyboard())
        return
    await update.message.reply_text("📋 **Tes Tickets Clashsport**", reply_markup=_tickets_keyboard(sessions), parse_mode="Markdown")

async def user_live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    sessions = [s for s in get_user_sessions(user_id, history_limit=0) if s["status"] in ("WAITING", "IN_PROGRESS")]
    if not sessions:
        await update.message.reply_text("📭 Aucun duel en cours à suivre.", reply_markup=main_menu_keyboard())
        return
    await update.message.reply_text("🔴 Les matchs en direct sont accessibles dans chaque ticket.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="menu_main")]]))

def _tickets_keyboard(sessions):
    keyboard = []
    for s in sessions:
        icon = "⚪" if s["status"] == "WAITING" else ("🟢" if s["status"] == "IN_PROGRESS" else "🔵")
        t_label = "Arena" if s["type"] == "ARENA" else "Duel"
        keyboard.append([InlineKeyboardButton(f"{icon} {t_label} {s['gross_entry_fee']} C ({s['match_count']}m)", callback_data=f"ticket_{s['id']}_mine")])
    keyboard.append([InlineKeyboardButton("⬅️ Retour au Menu Principal", callback_data="menu_main")])
    return InlineKeyboardMarkup(keyboard)

async def show_ticket_detail(query, session_id, tab):
    user_id = query.from_user.id
    session = get_session(session_id)
    if not session:
        await query.edit_message_text("❌ Session introuvable.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Mes Tickets", callback_data="my_tickets")]]))
        return

    tickets = get_tickets_for_session(session_id)
    my_ticket = next((t for t in tickets if t["user_id"] == user_id), None)
    
    status_icon = {"WAITING": "⚪", "IN_PROGRESS": "🟢", "COMPLETED": "🔵"}.get(session['status'], "⚪")
    text = f"⚔️ <b>Session {session['type']} — {session['gross_entry_fee']} Coins</b>\n{status_icon} Statut: {session['status']}\n\n"
    
    if tab == "mine":
        if my_ticket:
            match_ids = [str(p["match_id"]) for p in my_ticket["predictions"]]
            matches = {str(m["api_match_id"]): m for m in get_matches_by_ids(match_ids)}
            my_correct, total = 0, len(my_ticket["predictions"])
            for p in my_ticket["predictions"]:
                m = matches.get(str(p["match_id"]))
                if not m: continue
                icon = "⏳"
                if m.get("result"):
                    if m["result"] == "CANCEL": icon = "🚫 (Annulé)"
                    elif m["result"] == p["pick"]:
                        icon = "👍"
                        my_correct += 1
                    else: icon = "😢"
                
                home = str(m['home_team']).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                away = str(m['away_team']).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                text += f"{icon} {home} vs {away}\n"
            text += f"\n🟢 <b>Mon Score : {my_correct}/{total}</b>"
            
            if session['status'] == 'COMPLETED':
                winner_id = session.get('winner_id')
                if winner_id == user_id:
                    text += "\n\n✨ <b>VICTOIRE !</b> ✨"
                elif winner_id is None:
                    text += "\n\n🤝 <b>ÉGALITÉ PARFAITE</b>"
                else:
                    text += "\n\n😔 Vous avez perdu."
        else: 
            text += "Vous n'avez pas de ticket ici."
    
    elif tab == "opp":
        text += "👥 <b>Progression des Adversaires :</b>\n\n"
        for t in tickets:
            if t["user_id"] == user_id: continue
            opp_user = get_user_by_id(t["user_id"]) or {}
            match_ids = [str(p["match_id"]) for p in t["predictions"]]
            matches = {str(m["api_match_id"]): m for m in get_matches_by_ids(match_ids)}
            
            finished, won, total = 0, 0, len(t["predictions"])
            for p in t["predictions"]:
                m = matches.get(str(p["match_id"]))
                if m and m.get("result"):
                    if m["result"] != "CANCEL":
                        finished += 1
                        if m["result"] == p["pick"]: won += 1
            
            username = str(opp_user.get('username', 'Joueur')).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            text += f"👤 {username} : <code>{finished}/{total}</code> — {won}G / {finished - won}P\n"
        
        if len(tickets) <= 1: text += "\n⏳ <i>En attente d'adversaires...</i>"

    btn_mine = InlineKeyboardButton("📍 Mon Ticket" + (" 🔹" if tab == "mine" else ""), callback_data=f"ticket_{session_id}_mine")
    btn_opp = InlineKeyboardButton("👥 Adversaires" + (" 🔹" if tab == "opp" else ""), callback_data=f"ticket_{session_id}_opp")
    
    keyboard = [[btn_mine, btn_opp]]
    keyboard.append([InlineKeyboardButton("⬅️ Retour Mes Tickets", callback_data="my_tickets")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
