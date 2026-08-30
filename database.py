from supabase import create_client, Client
import config

supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)

# --- UTILISATEURS ---

def get_or_create_user(telegram_id: int, username: str, referred_by: int = None):
    res = supabase.table("users").select("*").eq("telegram_id", telegram_id).execute()
    if res.data:
        return res.data[0]

    new_user = {
        "telegram_id": telegram_id,
        "username": username,
        "coins_balance": 1000,  # Solde offert pour tester
        "referred_by": referred_by,
    }
    insert_res = supabase.table("users").insert(new_user).execute()
    return insert_res.data[0]

def credit_balance(telegram_id: int, amount: float):
    user = get_or_create_user(telegram_id, "")
    new_balance = user["coins_balance"] + amount
    supabase.table("users").update({"coins_balance": new_balance}).eq("telegram_id", telegram_id).execute()
    return new_balance

# --- MATCHS ---

def get_active_matches():
    response = supabase.table("matches").select("*").eq("status", "NS").execute()
    return response.data

def get_matches_by_sport(sport: str):
    response = supabase.table("matches").select("*").eq("status", "NS").ilike("sport", f"%{sport}%").execute()
    return response.data

def get_session(session_id: str):
    res = supabase.table("sessions").select("*").eq("id", session_id).execute()
    return res.data[0] if res.data else None

def get_open_duels(exclude_creator_id: int = None):
    query = supabase.table("sessions").select("*").eq("status", "WAITING").eq("type", "DUEL")
    if exclude_creator_id is not None:
        query = query.neq("creator_id", exclude_creator_id)
    return query.execute().data

# --- DUELS ET TICKETS ---

def create_duel_session(creator_id: int, gross_fee: float, match_count: int, predictions: list):
    user = get_or_create_user(creator_id, "")
    if user["coins_balance"] < gross_fee:
        return None, "Solde insuffisant"

    rake = gross_fee * config.RAKE_PERCENTAGE
    net_fee = gross_fee - rake

    # Débit du solde
    supabase.table("users").update({"coins_balance": user["coins_balance"] - gross_fee}).eq("telegram_id", creator_id).execute()

    session_data = {
        "creator_id": creator_id,
        "type": "DUEL",
        "gross_entry_fee": gross_fee,
        "net_entry_fee": net_fee,
        "match_count": match_count,
        "status": "WAITING"
    }
    session = supabase.table("sessions").insert(session_data).execute().data[0]
    
    # Enregistrement du ticket indépendant
    save_ticket(session["id"], creator_id, predictions)
    return session, "Succès"


def join_duel_session(session_id: str, joiner_id: int, predictions: list):
    session = get_session(session_id)
    if not session or session["status"] != "WAITING":
        return None, "Session fermée ou indisponible."

    gross_fee = session["gross_entry_fee"]
    joiner = get_or_create_user(joiner_id, "")
    if joiner["coins_balance"] < gross_fee:
        return None, "Solde insuffisant."

    # Débit
    supabase.table("users").update({"coins_balance": joiner["coins_balance"] - gross_fee}).eq("telegram_id", joiner_id).execute()

    updated = (
        supabase.table("sessions")
        .update({"opponent_id": joiner_id, "status": "IN_PROGRESS"})
        .eq("id", session_id)
        .eq("status", "WAITING")
        .execute()
    )

    if not updated.data:
        # Remboursement en cas d'accès concurrent
        supabase.table("users").update({"coins_balance": joiner["coins_balance"]}).eq("telegram_id", joiner_id).execute()
        return None, "Un autre joueur vous a devancé."

    save_ticket(session_id, joiner_id, predictions)
    return updated.data[0], "Succès"


def save_ticket(session_id: str, user_id: int, predictions: list):
    ticket_data = {
        "session_id": session_id,
        "user_id": user_id,
        "predictions": predictions,
        "status": "PENDING",
    }
    return supabase.table("tickets").insert(ticket_data).execute().data[0]
