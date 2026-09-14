import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from services.session_service import calculate_leaderboards

logger = logging.getLogger(__name__)

CAT_NAMES = {
    "winrate": "🎯 Taux de Réussite",
    "network": "👥 Nouveaux Filleuls",
    "volume": "🔥 Volume de Jeu",
}

PERIOD_NAMES = {
    "day": "24 Heures",
    "week": "7 Jours",
    "month": "30 Jours",
}


async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_category_menu(update)


async def show_category_menu(update: Update):
    text = (
        "🏆 **CLASSEMENTS CLASHSPORT**\n\n"
        "Choisissez la catégorie de classement que vous souhaitez consulter :"
    )

    keyboard = [
        [InlineKeyboardButton("🎯 Taux de Réussite", callback_data="topcat_winrate")],
        [InlineKeyboardButton("👥 Nouveaux Filleuls (Parrains)", callback_data="topcat_network")],
        [InlineKeyboardButton("🔥 Volume de Jeu (Gros parieurs)", callback_data="topcat_volume")],
        [InlineKeyboardButton("🏠 Menu Principal", callback_data="menu_main")],
    ]

    markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=markup, parse_mode="Markdown"
        )
    elif update.message:
        await update.message.reply_text(
            text, reply_markup=markup, parse_mode="Markdown"
        )


async def show_period_menu(update: Update, category: str):
    cat_name = CAT_NAMES.get(category, "Classement")
    text = f"🏆 **{cat_name}**\n\nChoisissez la période :"

    keyboard = [
        [InlineKeyboardButton("⏳ 24 Heures", callback_data=f"topshow_{category}_day")],
        [InlineKeyboardButton("📅 7 Jours", callback_data=f"topshow_{category}_week")],
        [InlineKeyboardButton("🗓️ 30 Jours", callback_data=f"topshow_{category}_month")],
        [InlineKeyboardButton("🔙 Retour aux catégories", callback_data="top_back")],
    ]

    await update.callback_query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def handle_top_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    try:
        await query.answer()
    except Exception:
        pass

    data = query.data or ""

    if data == "menu_top":
        await show_category_menu(update)
        return

    if data == "top_back":
        await show_category_menu(update)
        return

    if data.startswith("topcat_"):
        category = data[len("topcat_"):]
        if category not in CAT_NAMES:
            await query.edit_message_text("❌ Catégorie de classement invalide.")
            return
        await show_period_menu(update, category)
        return

    if data.startswith("topshow_"):
        parts = data.split("_")
        if len(parts) != 3:
            await query.edit_message_text("❌ Paramètres de classement invalides.")
            return

        _, category, period = parts

        if category not in CAT_NAMES or period not in PERIOD_NAMES:
            await query.edit_message_text("❌ Paramètres de classement invalides.")
            return

        try:
            board, min_volume = calculate_leaderboards(
                category, period, limit=10
            )
        except Exception:
            logger.exception(
                "Erreur lors du chargement du classement %s/%s",
                category,
                period,
            )
            await query.edit_message_text(
                "⚠️ Impossible de charger le classement pour le moment.",
                parse_mode="Markdown",
            )
            return

        text = (
            f"🏆 **TOP 10 — {CAT_NAMES[category]} ({PERIOD_NAMES[period]})**\n"
            f"⚠️ *Volume de jeu minimum requis : {min_volume} Coins*\n\n"
        )

        if not board:
            text += "📭 Aucun joueur classé pour cette période."
        else:
            medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

            for index, player in enumerate(board[:10]):
                medal = medals[index] if index < len(medals) else f"#{index + 1}"
                username = player.get("username") or "Joueur"
                user_code = player.get(
                    "user_code",
                    f"U{player.get('telegram_id', '?')}",
                )
                qualified = "✅ Qualifié" if player.get("qualified") else "❌ Volume Insuffisant"

                text += f"{medal} **{username}** (`{user_code}`)\n"

                if category == "winrate":
                    text += (
                        f"   👉 `{player.get('winrate', 0)}%` de réussite | "
                        f"Vol: {player.get('volume', 0)}\n"
                    )
                elif category == "network":
                    text += (
                        f"   👉 `{player.get('network', 0)}` filleuls | "
                        f"Vol: {player.get('volume', 0)}\n"
                    )
                else:
                    text += f"   👉 `{player.get('volume', 0)}` Coins misés\n"

                text += f"   Statut : {qualified}\n\n"

        keyboard = [
            [InlineKeyboardButton(
                "🔙 Retour aux périodes",
                callback_data=f"topcat_{category}",
            )],
            [InlineKeyboardButton(
                "🏠 Menu Principal",
                callback_data="menu_main",
            )],
        ]

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
        return

    await query.edit_message_text("❌ Action de classement inconnue.")



