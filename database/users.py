import random
from database.connection import supabase

def get_or_create_user(telegram_id: int, username: str, referred_by: int = None):
    res = supabase.table("users").select("*").eq("telegram_id", telegram_id).execute()
    if res.data:
        return res.data[0]

    while True:
        code = str(random.randint(10000, 99999))
        if not supabase.table("users").select("telegram_id").eq("player_code", code).execute().data:
            break

    new_user = {
        "telegram_id": telegram_id,
        "username": username,
        "coins_balance": 1000,
        "player_code": code,
        "referred_by": referred_by,
    }
    return supabase.table("users").insert(new_user).execute().data[0]

def get_user_by_id(telegram_id: int):
    res = supabase.table("users").select("*").eq("telegram_id", telegram_id).execute()
    return res.data[0] if res.data else None

def get_all_users():
    try:
        return supabase.table("users").select("*").execute().data
    except Exception:
        return []

def credit_balance(telegram_id: int, amount: int):
    user = get_or_create_user(telegram_id, "")
    new_balance = int(user["coins_balance"]) + amount
    supabase.table("users").update({"coins_balance": new_balance}).eq("telegram_id", telegram_id).execute()
    return new_balance

def admin_take_coins(telegram_id: int, amount: int):
    user = get_user_by_id(telegram_id)
    if not user: return None
    new_balance = max(0, int(user["coins_balance"]) - amount)
    supabase.table("users").update({"coins_balance": new_balance}).eq("telegram_id", telegram_id).execute()
    return new_balance

def get_detailed_stats():
    """Récupère les statistiques globales de façon blindée."""
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

def update_api_quota(quota_str: str):
    if quota_str:
        try:
            supabase.table("app_settings").upsert({"setting_key": "api_quota", "setting_value": str(quota_str)}).execute()
        except Exception: pass

def get_api_quota() -> str:
    try:
        res = supabase.table("app_settings").select("setting_value").eq("setting_key", "api_quota").execute()
        if res.data and res.data[0].get("setting_value"):
            return str(res.data[0]["setting_value"])
    except Exception: pass
    return "Inconnu"

