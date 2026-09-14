from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Génère le clavier du menu principal de Clashsport."""
    keyboard = [
        [InlineKeyboardButton("⚔️ Créer un Duel / une Arena", callback_data="menu_duel")],
        [
            InlineKeyboardButton("🎫 Mes Tickets", callback_data="menu_tickets"),
            InlineKeyboardButton("🔴 En Direct", callback_data="menu_live"),
        ],
        [InlineKeyboardButton("🛒 Boutique & Sac", callback_data="menu_shop")],
        [InlineKeyboardButton("👥 Mon Réseau (Parrainage)", callback_data="menu_network")],
        [InlineKeyboardButton("🏆 Classements (Top)", callback_data="menu_top")],
    ]
    return InlineKeyboardMarkup(keyboard)


def back_to_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Génère un bouton de retour au menu principal."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")]
    ])
