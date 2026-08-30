from supabase import create_client, Client
import config
import odds_api

supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)

# --- UTILISATEURS ---

def get_or_create_user(telegram_id: int, username: str, referred_by: int = None):
    res = supabase.table("users").select("*").eq("telegram_id", telegram_id).execute()
    if res.data:
        return res.data[0]

    new_user = {
        "telegram_id": telegram_id,
        "username": username,
        "coins_balance": 1000,
        "referred_by": referred_by,
    }
    insert_res = supabase.table("users").insert(new_user).execute()
    return insert_res.data[0]

def credit_balance(telegram_id: int, amount: int):
    user = get_or_create_user(telegram_id, "")
    new_balance = int(user["coins_balance"]) + amount
    supabase.table("users").update({"coins_balance": new_balance}).eq("telegram_id", telegram_id).execute()
    return new_balance

def get_user_by_id(telegram_id: int):
    res = supabase.table("users").select("*").eq("telegram_id", telegram_id).execute()
    return res.data[0] if res.data else None

def get_all_users():
    try:
        response = supabase.table("users").select("*").execute()
        return response.data
    except Exception:
        return []

def get_platform_stats():
    users = get_all_users()
    sessions = supabase.table("sessions").select("*").execute().data
    total_coins = sum(u.get("coins_balance", 0) for u in users)
    waiting_duels = len([s for s in sessions if s.get("status") == "WAITING"])
    active_duels = len([s for s in sessions if s.get("status") == "IN_PROGRESS"])
    return {
        "total_users": len(users),
        "total_coins": total_coins,
        "waiting_duels": waiting_duels,
        "active_duels": active_duels
    }

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

def create_duel_session(creator_id: int, gross_fee: int, match_count: int, predictions: list):
    user = get_or_create_user(creator_id, "")
    if user["coins_balance"] < gross_fee:
        return None, "Solde insuffisant"

    rake = int(round(gross_fee * config.RAKE_PERCENTAGE))
    net_fee = gross_fee - rake

    supabase.table("users").update({"coins_balance": int(user["coins_balance"]) - gross_fee}).eq("telegram_id", creator_id).execute()

    session_data = {
        "creator_id": creator_id,
        "type": "DUEL",
        "gross_entry_fee": gross_fee,
        "net_entry_fee": net_fee,
        "match_count": match_count,
        "status": "WAITING"
    }
    session = supabase.table("sessions").insert(session_data).execute().data[0]
    save_ticket(session["id"], creator_id, predictions)
    return session, "Succès"

def join_duel_session(session_id: str, joiner_id: int, predictions: list):
    session = get_session(session_id)
    if not session or session["status"] != "WAITING":
        return None, "Session fermée ou indisponible."

    gross_fee = int(session["gross_entry_fee"])
    joiner = get_or_create_user(joiner_id, "")
    
    if joiner["coins_balance"] < gross_fee:
        return None, "Solde insuffisant."

    # 1. Verrouillage optimiste de la session d'abord, pour éviter les courses
    updated = (
        supabase.table("sessions")
        .update({"opponent_id": joiner_id, "status": "IN_PROGRESS"})
        .eq("id", session_id)
        .eq("status", "WAITING")
        .execute()
    )

    if not updated.data:
        return None, "Un autre joueur a déjà rejoint ce duel !"

    # 2. Une fois la session acquise, on débite
    supabase.table("users").update({"coins_balance": int(joiner["coins_balance"]) - gross_fee}).eq("telegram_id", joiner_id).execute()
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

# --- ADMIN FUNCTIONS ---

async def sync_matches_from_api_async():
    try:
        matches, quota = await odds_api.sync_today_matches(config.ODDS_API_KEY)
        if not matches:
            return 0, "Aucun match trouvé pour aujourd'hui."
        
        for match in matches:
            try:
                supabase.table("matches").upsert(match, on_conflict="api_match_id").execute()
            except Exception:
                try:
                    supabase.table("matches").insert(match).execute()
                except Exception:
                    pass
        
        return len(matches), f"✅ {len(matches)} matchs synchronisés. Quota API restant : {quota}"
    except Exception as e:
        return 0, f"❌ Erreur lors de la synchronisation : {str(e)}"
