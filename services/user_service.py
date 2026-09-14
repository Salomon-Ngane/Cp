import random
import string
from database.connection import supabase

def generate_code(prefix="U"):
    """Génère un code unique de 7 caractères (ex: U8A3F9K)"""
    chars = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"{prefix}{chars}"

def get_or_create_user(telegram_id: int, username: str, referrer_id: int = None):
    res = supabase.table("users").select("*").eq("telegram_id", telegram_id).execute()
    if res.data:
        return res.data[0]

    code = generate_code("U")
    new_user = {
        "telegram_id": telegram_id,
        "username": username,
        "coins_balance": 1000,
        "user_code": code,
        "referrer_id": referrer_id,
        "active_referrals_count": 0,
        "item_1_count": 0,
        "item_2_count": 0,
        "item_3_count": 0
    }
    
    res = supabase.table("users").insert(new_user).execute()
    
    if referrer_id:
        referrer = get_user_by_id(referrer_id)
        if referrer:
            supabase.table("users").update({
                "active_referrals_count": referrer.get("active_referrals_count", 0) + 1
            }).eq("telegram_id", referrer_id).execute()
            
    return res.data[0]

def get_user_by_id(telegram_id: int):
    res = supabase.table("users").select("*").eq("telegram_id", telegram_id).execute()
    return res.data[0] if res.data else None

def get_user_by_code(user_code: str):
    res = supabase.table("users").select("*").eq("user_code", user_code).execute()
    if not res.data:
        # Fallback pour les anciens comptes
        res = supabase.table("users").select("*").eq("player_code", user_code).execute()
    return res.data[0] if res.data else None

def get_all_users():
    try:
        return supabase.table("users").select("*").execute().data
    except Exception:
        return []

def get_user_grade(active_referrals_count: int) -> dict:
    if active_referrals_count >= 75: return {"name": "Légende 👑", "level": 5}
    if active_referrals_count >= 55: return {"name": "Élite 🥇", "level": 4}
    if active_referrals_count >= 30: return {"name": "Expert 🥇", "level": 3}
    if active_referrals_count >= 10: return {"name": "Pro 🥈", "level": 2}
    return {"name": "Recrue 🥉", "level": 1}

def credit_balance(telegram_id: int, amount: int):
    user = get_or_create_user(telegram_id, "")
    if user:
        new_balance = int(user["coins_balance"]) + amount
        supabase.table("users").update({"coins_balance": new_balance}).eq("telegram_id", telegram_id).execute()
        return new_balance
    return 0

def admin_take_coins(telegram_id: int, amount: int):
    user = get_user_by_id(telegram_id)
    if not user: return None
    new_balance = max(0, int(user["coins_balance"]) - amount)
    supabase.table("users").update({"coins_balance": new_balance}).eq("telegram_id", telegram_id).execute()
    return new_balance

def get_detailed_stats():
    try:
        users = supabase.table("users").select("coins_balance").execute().data or []
        sessions = supabase.table("sessions").select("status").execute().data or []
        tickets = supabase.table("tickets").select("session_id").execute().data or []
        
        total_coins = sum(int(u.get("coins_balance") or 0) for u in users)
        completed_sessions = [s for s in sessions if s.get("status") == "COMPLETED"]
        
        return {
            "total_users": len(users),
            "total_coins": total_coins,
            "total_tickets": len(tickets),
            "waiting_sessions": len([s for s in sessions if s.get("status") == "WAITING"]),
            "active_sessions": len([s for s in sessions if s.get("status") == "IN_PROGRESS"]),
            "completed_sessions": len(completed_sessions),
        }
    except Exception:
        return {}

def award_item(telegram_id: int, item_type: int):
    user = get_user_by_id(telegram_id)
    if not user: return False
    field = f"item_{item_type}_count"
    current = user.get(field, 0)
    if current < 3:
        supabase.table("users").update({field: current + 1}).eq("telegram_id", telegram_id).execute()
        return True
    return False
    # --- BOUTIQUE ---
SHOP_PRICES = {1: 500, 2: 1000}
REQUIRED_GRADES = {1: 1, 2: 2}

def buy_item_from_shop(telegram_id: int, item_id: int) -> tuple[bool, str]:
    user = get_user_by_id(telegram_id)
    if not user:
        return False, "Utilisateur introuvable."
    if item_id not in SHOP_PRICES:
        return False, "Cet Item n'est pas disponible à la vente."

    price = SHOP_PRICES[item_id]
    if int(user.get("coins_balance", 0)) < price:
        return False, f"Fonds insuffisants. Il vous faut {price} Coins."

    grade_info = get_user_grade(user.get("active_referrals_count", 0))
    if grade_info["level"] < REQUIRED_GRADES[item_id]:
        return False, f"Grade insuffisant. L'Item {item_id} requiert le grade de niveau {REQUIRED_GRADES[item_id]}."

    item_key = f"item_{item_id}_count"
    current_qty = user.get(item_key, 0)
    if current_qty >= 3:
        return False, f"Votre sac est plein ! Vous possédez déjà 3x Item {item_id}."

    new_balance = int(user["coins_balance"]) - price
    supabase.table("users").update({
        "coins_balance": new_balance,
        item_key: current_qty + 1
    }).eq("telegram_id", telegram_id).execute()
    
    return True, f"✅ Achat réussi ! 1x Item {item_id} ajouté à votre sac."

