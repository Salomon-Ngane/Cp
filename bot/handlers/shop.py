import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from services.user_service import (
    get_user_by_id,
    get_user_grade,
    buy_item_from_shop,
)

logger = logging.getLogger(__name__)


async def shop_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    message_text: str = None,
):
    """Affiche la boutique et l'inventaire du joueur."""
    user_id = update.effective_user.id
    user = get_user_by_id(user_id)

    if not user:
        if update.callback_query:
            try:
                await update.callback_query.answer(
                    "⚠️ Utilisateur introuvable.",
                    show_alert=True,
                )
            except Exception:
                pass
        return

    balance = int(user.get("coins_balance") or 0)
    grade_info = get_user_grade(
        int(user.get("active_referrals_count") or 0)
    )

    item_1 = int(user.get("item_1_count") or 0)
    item_2 = int(user.get("item_2_count") or 0)
    item_3 = int(user.get("item_3_count") or 0)

    item_2_status = (
        "✅ Débloqué"
        if grade_info.get("level", 1) >= 2
        else "🔒 Requis : Grade Pro 🥈"
    )

    text = "🛒 **BOUTIQUE & SAC CLASHSPORT**\n\n"

    if message_text:
        text += f"*{message_text}*\n\n"

    text += (
        f"💰 **Solde :** `{balance}` Coins\n"
        f"👑 **Grade :** `{grade_info.get('name', 'Recrue 🥉')}`\n\n"
        "--- 🛍️ **ARTICLES DISPONIBLES** ---\n"
        "📦 **Item 1 (+0.5 Cote)** ── `500 Coins`\n"
        "   └ ✅ Débloqué pour tous.\n"
        "💎 **Item 2 (+1.1 Cote)** ── `1000 Coins`\n"
        f"   └ {item_2_status}\n"
        "🔥 **Item 3 (+1.5 Cote)** ── 🔒 *Réservé Événements & Tops*\n\n"
        "--- 🎒 **VOTRE INVENTAIRE (Max 3/type)** ---\n"
        f"📦 Item 1 : `{item_1}/3`\n"
        f"💎 Item 2 : `{item_2}/3`\n"
        f"🔥 Item 3 : `{item_3}/3`\n"
    )

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Acheter Item 1 (500)", callback_data="buy_item_1")],
        [InlineKeyboardButton("💎 Acheter Item 2 (1000)", callback_data="buy_item_2")],
        [InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")],
    ])

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            reply_markup=markup,
            parse_mode="Markdown",
        )
    elif update.message:
        await update.message.reply_text(
            text,
            reply_markup=markup,
            parse_mode="Markdown",
        )


async def handle_shop_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Traite l'achat d'un item."""
    query = update.callback_query
    if not query:
        return

    item_id_str = (query.data or "").split("_")[-1]

    if not item_id_str.isdigit():
        await query.answer("❌ Action invalide.", show_alert=True)
        return

    item_id = int(item_id_str)

    try:
        success, message = buy_item_from_shop(
            update.effective_user.id,
            item_id,
        )
    except Exception:
        logger.exception("Erreur pendant l'achat de l'item %s", item_id)
        await query.answer(
            "⚠️ Une erreur est survenue pendant l'achat.",
            show_alert=True,
        )
        return

    await query.answer(message, show_alert=not success)

    if success:
        await shop_menu(
            update,
            context,
            message_text=message,
        )

