from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚔️ Créer / Rejoindre (Duel & Arena)", callback_data="menu_duel")],
        [InlineKeyboardButton("📋 Mes Tickets", callback_data="my_tickets"), InlineKeyboardButton("🔴 Live", callback_data="live_all")],
        [InlineKeyboardButton("🏆 Classement", callback_data="menu_top")],
        [InlineKeyboardButton("💳 Mon Compte", callback_data="menu_account")],
    ])
