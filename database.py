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
