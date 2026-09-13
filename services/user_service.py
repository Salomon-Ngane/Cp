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
        "referrer_id": referrer_id
    }
    
    res = supabase.table("users").insert(new_user).execute()
    
    if referrer_id:
        # Incrémenter le compteur de parrainage du parrain
        referrer = get_user_by_id(referrer_id)
        if referrer:
            supabase.table("users").update({
                "active_referrals_count": referrer.get("active_referrals_count", 0) + 1
            }).eq("telegram_id", referrer_id).execute()
            
    return res.data[0]

def get_user_by_id(telegram_id: int):
    res = supabase.table("users").select("*").eq("telegram_id", telegram_id).execute()
    return res.data[0] if res.data else None

def get_user_grade(active_referrals_count: int) -> dict:
    if active_referrals_count >= 75: return {"name": "Légende 👑", "level": 5}
    if active_referrals_count >= 55: return {"name": "Élite 🥇", "level": 4}
    if active_referrals_count >= 30: return {"name": "Expert 🥇", "level": 3}
    if active_referrals_count >= 10: return {"name": "Pro 🥈", "level": 2}
    return {"name": "Recrue 🥉", "level": 1}

def credit_balance(telegram_id: int, amount: int):
    user = get_user_by_id(telegram_id)
    if user:
        new_balance = int(user["coins_balance"]) + amount
        supabase.table("users").update({"coins_balance": new_balance}).eq("telegram_id", telegram_id).execute()
        return new_balance
    return 0
