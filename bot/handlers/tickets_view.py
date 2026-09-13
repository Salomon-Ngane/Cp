import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from services.session_service import (
    get_user_sessions, 
    get_session, 
    get_tickets_for_session, 
    get_matches_by_ids, 
    get_estimated_end_time
)
from database.users import get_user_by_id
from bot.ui import main_menu_keyboard

logger = logging.getLogger(__name__)

async def user_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /tickets : Affiche la liste des sessions de l'utilisateur."""
    user_id = update.effective_user.id
    sessions = get_user_sessions(user_id)
    if not sessions:
        await update.message.reply_text("📭 Vous n'avez aucun ticket pour l'instant.", reply_markup=main_menu_keyboard(), parse_mode="Markdown")
        return
    
    await update.message.reply_text("📋 **Tes Tickets Clashsport**", reply_markup=_tickets_keyboard(sessions), parse_mode="Markdown")

async def user_live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /live : Raccourci vers les sessions en cours."""
    user_id = update.effective_user.id
    sessions = [s for s in get_user_sessions(user_id, history_limit=0) if s["status"] in ("WAITING", "IN_PROGRESS")]
    if not sessions:
        await update.message.reply_text("📭 Aucun duel en cours à suivre.", reply_markup=main_menu_keyboard(), parse_mode="Markdown")
        return
    
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")]])
    await update.message.reply_text("🔴 Les matchs en direct sont accessibles dans chaque ticket.", reply_markup=keyboard, parse_mode="Markdown")

def _tickets_keyboard(sessions):
    """Génère les boutons de la liste des tickets."""
    keyboard = []
    for s in sessions:
        # Icône dynamique selon le statut de la session
        status_icon = "⏳" if s["status"] == "WAITING" else "🔴" if s["status"] == "IN_PROGRESS" else "✅" if s["status"] == "COMPLETED" else "❌"
        stype = "Duel" if s["type"] == "DUEL" else "Arena"
        label = f"{status_icon} {stype} - {s['gross_entry_fee']} Coins"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"ticket_{s['id']}_mine")])
        
    keyboard.append([InlineKeyboardButton("⬅️ Retour", callback_data="menu_main")])
    return InlineKeyboardMarkup(keyboard)

async def show_ticket_detail(query, session_id, tab="mine"):
    """Affiche le détail complet d'un ticket (Mes pronostics vs Adversaires)."""
    user_id = query.from_user.id
    session = get_session(session_id)
    if not session:
        await query.answer("Ticket introuvable.", show_alert=True)
        return
    
    # 1. Formatage de la date de création
    created_at_str = session.get("created_at")
    if created_at_str:
        created_dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        date_creation = created_dt.strftime("%d/%m/%Y à %H:%M")
    else:
        date_creation = "Inconnue"

    # 2. Récupération de TOUS les matchs de cette session
    all_tickets = get_tickets_for_session(session_id)
    all_match_ids = set()
    for t in all_tickets:
        for p in t["predictions"]:
            all_match_ids.add(str(p["match_id"]))
            
    all_matches = get_matches_by_ids(list(all_match_ids))
    
    # 3. Calcul de la date de fin estimée avec la nouvelle fonction
    end_dt = get_estimated_end_time(all_matches)
    date_fin = end_dt.strftime("%d/%m/%Y à %H:%M")

    # 4. En-tête du ticket avec les dates
    text = f"🎫 **Détail du Ticket**\n\n"
    text += f"📅 **Créé le :** `{date_creation}`\n"
    text += f"🏁 **Fin estimée :** `{date_fin}`\n"
    text += f"🏷️ **Type :** `{session['type']}`\n"
    text += f"💰 **Mise :** `{session['gross_entry_fee']} Coins`\n"
    text += f"📊 **Statut :** `{session['status']}`\n\n"
    
    # Dictionnaire des matchs pour affichage rapide
    match_dict = {str(m["api_match_id"]): m for m in all_matches}
    
    # Identification du ticket de l'utilisateur
    my_ticket = next((t for t in all_tickets if t["user_id"] == user_id), None)
    
    # --- ONGLET 1 : MES PRONOSTICS ---
    if tab == "mine" and my_ticket:
        text += "🎯 **Mes Pronostics :**\n\n"
        for p in my_ticket["predictions"]:
            m = match_dict.get(str(p["match_id"]), {})
            home = m.get("home_team", "Équipe A")
            away = m.get("away_team", "Équipe B")
            pick_str = "1" if p["pick"] == "HOME" else "N" if p["pick"] == "DRAW" else "2"
            
            # Statut du pronostic (si le match est terminé)
            status = p.get("status", "PENDING")
            status_emoji = "⏳" if status == "PENDING" else "✅" if status == "WON" else "❌"
            
            text += f"🔹 {home} vs {away}\n"
            text += f"👉 {status_emoji} **{pick_str}** (Cote: {p['odds']})\n\n"
            
    # --- ONGLET 2 : LES ADVERSAIRES ---
    elif tab == "opponents":
        opponents = [t for t in all_tickets if t["user_id"] != user_id]
        if not opponents:
            text += "⏳ En attente d'adversaires..."
        else:
            text += "👥 **Adversaires :**\n\n"
            for t in opponents:
                opp = get_user_by_id(t["user_id"])
                uname = opp.get("username", "Joueur") if opp else "Joueur"
                text += f"👤 **{uname}**\n"
                for p in t["predictions"]:
                    m = match_dict.get(str(p["match_id"]), {})
                    home = m.get("home_team", "A")
                    away = m.get("away_team", "B")
                    pick_str = "1" if p["pick"] == "HOME" else "N" if p["pick"] == "DRAW" else "2"
                    text += f"  • {home[:10]} - {away[:10]} 👉 **{pick_str}**\n"
                text += "\n"

    # Construction du clavier
    keyboard = []
    if tab == "mine":
        keyboard.append([InlineKeyboardButton("👥 Voir les adversaires", callback_data=f"ticket_{session_id}_opponents")])
    else:
        keyboard.append([InlineKeyboardButton("🎯 Voir mes pronostics", callback_data=f"ticket_{session_id}_mine")])
        
    keyboard.append([InlineKeyboardButton("⬅️ Retour aux tickets", callback_data="my_tickets")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
