import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database.users import get_user_by_id, get_user_grade, buy_item_from_shop

logger = logging.getLogger(__name__)

async def shop_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, message_text: str = None):
    """Affiche la boutique et l'inventaire du joueur."""
    user_id = update.effective_user.id
    user = get_user_by_id(user_id)
    
    if not user:
        return

    # Infos Joueur
    balance = user.get("coins_balance", 0)
    grade_info = get_user_grade(user.get("active_referrals_count", 0))
    
    # Inventaire
    item_1 = user.get("item_1_count", 0)
    item_2 = user.get("item_2_count", 0)
    item_3 = user.get("item_3_count", 0)

    # Déblocages
    item_2_status = "✅ Débloqué" if grade_info["level"] >= 2 else "🔒 Requis : Grade Pro 🥈"

    text = f"🛒 **BOUTIQUE & SAC CLASHSPORT**\n\n"
    if message_text:
        text += f"*{message_text}*\n\n"

    text += (
        f"💰 **Solde :** `{balance}` Coins\n"
        f"👑 **Grade :** `{grade_info['name']}`\n\n"
        f"--- 🛍️ **ARTICLES DISPONIBLES** ---\n"
        f"📦 **Item 1 (+0.5 Cote)** ── `500 Coins`\n"
        f"   └ ✅ Débloqué pour tous.\n"
        f"💎 **Item 2 (+1.1 Cote)** ── `1000 Coins`\n"
        f"   └ {item_2_status}\n"
        f"🔥 **Item 3 (+1.5 Cote)** ── 🔒 *Réservé Événements & Tops*\n\n"
        f"--- 🎒 **VOTRE INVENTAIRE (Max 3/type)** ---\n"
        f"📦 Item 1 : `{item_1}/3`\n"
        f"💎 Item 2 : `{item_2}/3`\n"
        f"🔥 Item 3 : `{item_3}/3`\n"
    )

    keyboard = [
        [InlineKeyboardButton("📦 Acheter Item 1 (500)", callback_data="buy_item_1")],
        [InlineKeyboardButton("💎 Acheter Item 2 (1000)", callback_data="buy_item_2")],
        [InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

async def handle_shop_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Traite l'achat d'un item via le bouton de la boutique."""
    query = update.callback_query
    user_id = update.effective_user.id
    
    # Extraction de l'ID de l'item (buy_item_1 ou buy_item_2)
    item_id_str = query.data.split("_")[-1]
    if not item_id_str.isdigit():
        await query.answer("❌ Action invalide.", show_alert=True)
        return
        
    item_id = int(item_id_str)
    
    # Tentative d'achat
    success, message = buy_item_from_shop(user_id, item_id)
    
    # Affichage d'une popup furtive (Toast)
    await query.answer(message, show_alert=not success)
    
    # Rafraîchissement de la boutique si achat réussi (pour mettre à jour le solde et l'inventaire)
    if success:
        await shop_menu(update, context, message_text=message)
