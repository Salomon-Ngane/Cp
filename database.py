import os
import uuid
from supabase import create_client, Client
import config

if not config.SUPABASE_URL or not config.SUPABASE_KEY:
    raise ValueError("❌ SUPABASE_URL ou SUPABASE_KEY manquante dans les variables d'environnement !")

supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)

def get_or_create_user(telegram_id: int, username: str, referred_by: int = None):
    res = supabase.table("users").select("*").eq("telegram_id", telegram_id).execute()
    if res.data:
        return res.data[0]
    
    # On donne 1000 Coins de départ pour faciliter les tests
    new_user = {
        "telegram_id": telegram_id,
        "username": username,
        "coins_balance": 1000, 
        "referred_by": referred_by
    }
    insert_res = supabase.table("users").insert(new_user).execute()
    return insert_res.data[0]

def create_sample_matches():
    """Génère 5 matchs de test pour essayer le système de pronostics."""
    sample_matches = [
        {"api_match_id": 101, "home_team": "Real Madrid", "away_team": "Barcelona", "status": "NS"},
        {"api_match_id": 102, "home_team": "PSG", "away_team": "Marseille", "status": "NS"},
        {"api_match_id": 103, "home_team": "Arsenal", "away_team": "Chelsea", "status": "NS"},
        {"api_match_id": 104, "home_team": "Bayern Munich", "away_team": "Dortmund", "status": "NS"},
        {"api_match_id": 105, "home_team": "Inter Milan", "away_team": "AC Milan", "status": "NS"}
    ]
    for match in sample_matches:
        supabase.table("matches").upsert(match, on_conflict="api_match_id").execute()
    return get_active_matches()

def get_active_matches():
    response = supabase.table("matches").select("*").eq("status", "NS").execute()
    return response.data

def create_duel_session(creator_id: int, gross_fee: float):
    user = get_or_create_user(creator_id, "")
    if user["coins_balance"] < gross_fee:
        return None, "Solde insuffisant"
    
    # Sécurité au cas où RAKE_PERCENTAGE n'est pas dans config
    rake_pct = getattr(config, 'RAKE_PERCENTAGE', 0.10)
    rake = gross_fee * rake_pct
    net_fee = gross_fee - rake
    
    new_balance = float(user["coins_balance"]) - float(gross_fee)
    supabase.table("users").update({"coins_balance": new_balance}).eq("telegram_id", creator_id).execute()
    
    session_data = {
        "creator_id": creator_id,
        "type": "DUEL",
        "gross_entry_fee": gross_fee,
        "net_entry_fee": net_fee,
        "max_players": 2,
        "status": "WAITING"
    }
    session = supabase.table("sessions").insert(session_data).execute()
    return session.data[0], "Succès"

def get_duel_session(session_id: str):
    res = supabase.table("sessions").select("*").eq("id", session_id).execute()
    return res.data[0] if res.data else None

def join_duel_session(session_id: str, user_id: int):
    """Déduit la mise du joueur 2 et passe la session en ACTIVE."""
    session = get_duel_session(session_id)
    if not session or session["status"] != "WAITING":
        return False, "Session invalide ou déjà commencée."
    
    gross_fee = float(session["gross_entry_fee"])
    user = get_or_create_user(user_id, "")
    
    if user["coins_balance"] < gross_fee:
        return False, "Solde insuffisant."
        
    new_balance = float(user["coins_balance"]) - gross_fee
    supabase.table("users").update({"coins_balance": new_balance}).eq("telegram_id", user_id).execute()
    supabase.table("sessions").update({"status": "ACTIVE"}).eq("id", session_id).execute()
    return True, "Succès"

def save_ticket(session_id: str, user_id: int, predictions: list):
    ticket_data = {
        "session_id": session_id,
        "user_id": user_id,
        "predictions": predictions,
        "status": "PENDING"
    }
    res = supabase.table("tickets").insert(ticket_data).execute()
    return res.data[0]

def get_open_duels():
    res = supabase.table("sessions").select("*").eq("status", "WAITING").eq("type", "DUEL").limit(10).execute()
    return res.data
