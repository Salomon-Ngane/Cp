from supabase import create_client, Client
import config

supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)

def get_or_create_user(telegram_id: int, username: str, referred_by: int = None):
    """Récupère l'utilisateur ou le crée avec un solde initial de 0."""
    res = supabase.table("users").select("*").eq("telegram_id", telegram_id).execute()
    if res.data:
        return res.data[0]
    
    new_user = {
        "telegram_id": telegram_id,
        "username": username,
        "coins_balance": 0,
        "referred_by": referred_by
    }
    insert_res = supabase.table("users").insert(new_user).execute()
    return insert_res.data[0]

def create_duel_session(creator_id: int, gross_fee: float):
    """Crée une session de duel 1v1 en prélevant la mise brute et en calculant les frais."""
    user = get_or_create_user(creator_id, "")
    if user["coins_balance"] < gross_fee:
        return None, "Solde insuffisant"
    
    rake = gross_fee * config.RAKE_PERCENTAGE
    net_fee = gross_fee - rake
    
    # Débit du solde du créateur
    new_balance = user["coins_balance"] - gross_fee
    supabase.table("users").update({"coins_balance": new_balance}).eq("telegram_id", creator_id).execute()
    
    # Création de la session
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
    """Récupère tous les matchs à venir/non démarrés."""
    response = supabase.table("matches").select("*").eq("status", "NS").execute()
    return response.data
    import uuid

def create_duel_session(creator_id: int, gross_fee: float):
    """Crée une session de duel 1v1 et déduit la mise brute du créateur."""
    user = get_or_create_user(creator_id, "")
    if user["coins_balance"] < gross_fee:
        return None, "Solde insuffisant"
    
    rake = gross_fee * config.RAKE_PERCENTAGE
    net_fee = gross_fee - rake
    
    # Débit de la mise
    new_balance = user["coins_balance"] - gross_fee
    supabase.table("users").update({"coins_balance": new_balance}).eq("telegram_id", creator_id).execute()
    
    # Insertion de la session
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

def save_ticket(session_id: str, user_id: int, predictions: list):
    """Enregistre le ticket de pronostics d'un joueur pour une session donnée."""
    ticket_data = {
        "session_id": session_id,
        "user_id": user_id,
        "predictions": predictions,
        "status": "PENDING"
    }
    res = supabase.table("tickets").insert(ticket_data).execute()
    return res.data[0]

def get_open_duels():
    """Récupère les duels en attente d'adversaire."""
    res = supabase.table("sessions").select("*, users(username)").eq("status", "WAITING").eq("type", "DUEL").execute()
    return res.data


