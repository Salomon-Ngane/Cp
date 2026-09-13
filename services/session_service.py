import config
import string
import random
from datetime import datetime, timezone, timedelta
from database.connection import supabase
from services.user_service import get_user_by_id, credit_balance


def get_session(session_id: str):
    res = supabase.table("sessions").select("*").eq("id", session_id).execute()
    return res.data[0] if res.data else None

def get_tickets_for_session(session_id: str) -> list:
    return supabase.table("tickets").select("*").eq("session_id", session_id).execute().data

def save_ticket(session_id: str, user_id: int, predictions: list):
    # Les prédictions doivent inclure {match_id, pick, odds, item_used}
    res = supabase.table("tickets").insert({
        "session_id": session_id, 
        "user_id": user_id, 
        "predictions": predictions, 
        "status": "WAITING"
    }).execute()
    return res.data[0]

def create_session(creator_id: int, session_type: str, gross_fee: int, match_count: int, max_participants: int, prize_mode: str, predictions: list):
    user = get_user_by_id(creator_id)
    if not user or int(user["coins_balance"]) < gross_fee:
        return None, "Solde insuffisant."

    # Rake standardisé à 8% (selon le cahier des charges)
    net_fee = gross_fee - int(round(gross_fee * 0.08))
    session_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=7))

    session_data = {
        "creator_id": creator_id,
        "type": session_type,
        "gross_entry_fee": gross_fee,
        "net_entry_fee": net_fee,
        "match_count": match_count,
        "max_participants": max_participants,
        "prize_mode": prize_mode,
        "status": "WAITING",
        "session_code": session_code
    }
    
    try:
        session = supabase.table("sessions").insert(session_data).execute().data[0]
        save_ticket(session["id"], creator_id, predictions)
        new_balance = int(user["coins_balance"]) - gross_fee
        supabase.table("users").update({"coins_balance": new_balance}).eq("telegram_id", creator_id).execute()
        
        # Injection du rake solidaire
        donation_share = int(round(gross_fee * config.DON_SHARE_FROM_RAKE))
        if donation_share > 0:
            credit_balance(0, donation_share) # telegram_id 0 = Don Solidaire
            
        return session, "Succès"
    except Exception as e:
        return None, f"Erreur système : {str(e)}"

def get_matches_by_sport(sport: str):
    return supabase.table("matches").select("*").eq("status", "NS").ilike("sport", f"%{sport}%").execute().data

def get_matches_by_ids(match_ids: list):
    if not match_ids: return []
    ids = [str(mid) for mid in match_ids]
    response = supabase.table("matches").select("*").in_("api_match_id", ids).execute()
    return response.data
    def cancel_expired_sessions():
    """Scanne et annule les sessions expirées en attente (>24h) et rembourse les participants."""
    expiration_date = (datetime.now(timezone.utc) - timedelta(hours=config.SESSION_EXPIRATION_HOURS)).isoformat()
    expired = supabase.table("sessions").select("*").eq("status", "WAITING").lte("created_at", expiration_date).execute().data
    
    for session in expired:
        tickets = get_tickets_for_session(session["id"])
        for t in tickets:
            credit_balance(t["user_id"], session["gross_entry_fee"])
        supabase.table("sessions").update({"status": "CANCELLED"}).eq("id", session["id"]).execute()
        supabase.table("tickets").update({"status": "CANCELLED"}).eq("session_id", session["id"]).execute()


def get_weekly_leaderboard(limit: int = 10) -> list:
    """Récupère le classement hebdomadaire basé sur les victoires et les gains."""
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    sessions = supabase.table("sessions").select("*").eq("status", "COMPLETED").gte("created_at", week_ago).not_.is_("winner_id", "null").execute().data
    tally = {}
    for s in sessions:
        winner = s["winner_id"]
        pot = s["net_entry_fee"] * s.get("max_participants", 2)
        entry = tally.setdefault(winner, {"wins": 0, "coins_won": 0})
        entry["wins"] += 1
        entry["coins_won"] += pot

    ranked = sorted(tally.items(), key=lambda x: (-x[1]["wins"], -x[1]["coins_won"]))[:limit]
    leaderboard = []
    for telegram_id, stats in ranked:
        user = get_user_by_id(telegram_id)
        username = (user["username"] if user and user.get("username") else None) or f"Joueur {telegram_id}"
        leaderboard.append({"telegram_id": telegram_id, "username": username, "wins": stats["wins"], "coins_won": stats["coins_won"]})
    return leaderboard

