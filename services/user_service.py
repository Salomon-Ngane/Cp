import string
import random
import logging
from database.connection import supabase

logger = logging.getLogger(__name__)

# ID spécial du compte solidaire "Don ❤️"
DONATION_ACCOUNT_ID = "DON_COMMUNITY_ACCOUNT"

# Configuration de la Boutique
SHOP_PRICES = {1: 500, 2: 1000}
REQUIRED_GRADES = {1: 1, 2: 2}

def generate_short_code(length=7) -> str:
    """Génère un identifiant alphanumérique unique à 7 caractères."""
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choices(chars, k=length))

def get_user_by_id(telegram_id: int):
    """Récupère un utilisateur via son ID Telegram."""
    res = supabase.table("users").select("*").eq("telegram_id", telegram_id).execute().data
    return res[0] if res else None

def get_user_by_code(user_code: str):
    """Récupère un utilisateur via son code court à 7 caractères."""
    res = supabase.table("users").select("*").eq("user_code", user_code.upper()).execute().data
    return res[0] if res else None

def create_user_if_not_exists(telegram_id: int, username: str = None, referrer_id: int = None):
    """Crée un nouvel utilisateur, enregistre son parrain et initialise son inventaire."""
    existing = get_user_by_id(telegram_id)
    if existing:
        return existing, False

    user_code = generate_short_code(7)
    
    if referrer_id and int(referrer_id) == int(telegram_id):
        referrer_id = None

    data = {
        "telegram_id": telegram_id,
        "username": username or f"User_{telegram_id}",
        "user_code": user_code,
        "coins_balance": 1000,
        "referrer_id": referrer_id,
        "active_referrals_count": 0,
        "item_1_count": 0,
        "item_2_count": 0,
        "item_3_count": 0
    }
    
    res = supabase.table("users").insert(data).execute().data
    _ensure_donation_account_exists()
    
    return res[0] if res else None, True

def _ensure_donation_account_exists():
    """Garantit l'existence du compte Don ❤️ dans la base de données."""
    res = supabase.table("users").select("*").eq("telegram_id", 0).execute().data
    if not res:
        supabase.table("users").insert({
            "telegram_id": 0,
            "username": "Don ❤️ Solidaire",
            "user_code": "DON0000",
            "coins_balance": 0
        }).execute()

def update_user_balance(telegram_id: int, amount_change: int):
    """Ajoute ou retire des Coins au solde d'un joueur."""
    user = get_user_by_id(telegram_id)
    if not user:
        return False
    new_bal = max(0, user.get("coins_balance", 0) + amount_change)
    supabase.table("users").update({"coins_balance": new_bal}).eq("telegram_id", telegram_id).execute()
    return True

def get_user_grade(active_referrals: int) -> dict:
    """Calcule le grade de l'utilisateur et les niveaux débloqués."""
    if active_referrals >= 75:
        return {"name": "Légende 👑", "level": 5, "unlocked_levels": 5}
    elif active_referrals >= 55:
        return {"name": "Élite 🥇", "level": 4, "unlocked_levels": 4}
    elif active_referrals >= 30:
        return {"name": "Expert 🥇", "level": 3, "unlocked_levels": 3}
    elif active_referrals >= 10:
        return {"name": "Pro 🥈", "level": 2, "unlocked_levels": 2}
    else:
        return {"name": "Recrue 🥉", "level": 1, "unlocked_levels": 1}

def buy_item_from_shop(telegram_id: int, item_id: int) -> tuple[bool, str]:
    """Gère l'achat d'un Item dans la boutique avec vérification des limites et grades."""
    user = get_user_by_id(telegram_id)
    if not user:
        return False, "Utilisateur introuvable."

    if item_id not in SHOP_PRICES:
        return False, "Cet Item n'est pas disponible à la vente."

    price = SHOP_PRICES[item_id]
    if user.get("coins_balance", 0) < price:
        return False, f"Fonds insuffisants. Il vous faut {price} Coins."

    grade_info = get_user_grade(user.get("active_referrals_count", 0))
    if grade_info["level"] < REQUIRED_GRADES[item_id]:
        return False, f"Grade insuffisant. L'Item {item_id} requiert le grade de niveau {REQUIRED_GRADES[item_id]}."

    item_key = f"item_{item_id}_count"
    current_qty = user.get(item_key, 0)
    
    if current_qty >= 3:
        return False, f"Votre sac est plein ! Vous possédez déjà 3x Item {item_id}."

    new_balance = user["coins_balance"] - price
    new_qty = current_qty + 1
    
    supabase.table("users").update({
        "coins_balance": new_balance,
        item_key: new_qty
    }).eq("telegram_id", telegram_id).execute()
    
    return True, f"✅ Achat réussi ! Vous avez ajouté 1x Item {item_id} à votre sac."

def get_referral_lineage(telegram_id: int, max_depth: int = 5) -> list:
    """Remonte la chaîne des 5 parrains d'un joueur."""
    lineage = []
    current_id = telegram_id
    for _ in range(max_depth):
        user = get_user_by_id(current_id)
        if not user or not user.get("referrer_id"):
            break
        ref_id = user["referrer_id"]
        referrer = get_user_by_id(ref_id)
        if not referrer:
            break
        lineage.append(referrer)
        current_id = ref_id
    return lineage
