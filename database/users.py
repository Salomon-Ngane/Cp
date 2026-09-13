import string
import random
import logging
from database.connection import supabase

logger = logging.getLogger(__name__)

# ID spécial du compte solidaire "Don ❤️"
DONATION_ACCOUNT_ID = "DON_COMMUNITY_ACCOUNT"

def generate_short_code(length=7) -> str:
    """Génère un identifiant alphanumérique unique à 7 caractères (ex: K7M9X2P)."""
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choices(chars, k=length))

def get_user_by_id(telegram_id: int):
    """Récupère un utilisateur via son ID Telegram."""
    res = supabase.table("users").select("*").eq("telegram_id", telegram_id).execute().data
    return res[0] if res else None

def get_user_by_code(user_code: str):
    """Récupère un utilisateur via son code court à 7 caractères."""
    res = supabase.table("users").select("*").eq("user_code", user_code).execute().data
    return res[0] if res else None

def create_user_if_not_exists(telegram_id: int, username: str = None, referrer_id: int = None):
    """Crée un nouvel utilisateur s'il n'existe pas, enregistre son parrain et génère son code court."""
    existing = get_user_by_id(telegram_id)
    if existing:
        return existing, False

    user_code = generate_short_code(7)
    
    # Empêcher le s'auto-parrainage
    if referrer_id and int(referrer_id) == int(telegram_id):
        referrer_id = None

    data = {
        "telegram_id": telegram_id,
        "username": username or f"User_{telegram_id}",
        "user_code": user_code,
        "coins_balance": 1000,  # Solde initial de bienvenue
        "referrer_id": referrer_id,
        "active_referrals_count": 0,
        "item_boost_count": 0 # Quantité d'Item 1 (Boost Cote +0.5)
    }
    
    res = supabase.table("users").insert(data).execute().data
    
    # Création automatique du compte Don ❤️ si inexistant
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
    """Calcule le grade de l'utilisateur et les niveaux de parrainage débloqués selon l'activité de son réseau."""
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

def process_rake_and_referrals(total_staked: float):
    """
    Distribue les 4.0% de commission sur 5 niveaux et alimente le compte Don ❤️ (0.2%).
    
    Répartition :
    - Don ❤️ : 0.2%
    - Niveau 1 : 2.0% (Débloqué Grade 1+)
    - Niveau 2 : 1.0% (Débloqué Grade 2+)
    - Niveau 3 : 0.5% (Débloqué Grade 3+)
    - Niveau 4 : 0.2% (Débloqué Grade 4+)
    - Niveau 5 : 0.1% (Débloqué Grade 5)
    """
    rates = [0.02, 0.01, 0.005, 0.002, 0.001]
    
    # 1. Versement automatique de 0.2% au compte Don ❤️
    don_amount = int(total_staked * 0.002)
    if don_amount > 0:
        update_user_balance(0, don_amount) # ID 0 réservé au compte Don
