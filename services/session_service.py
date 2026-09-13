import httpx
import string
import random
from datetime import datetime, timezone, timedelta
from database.connection import supabase
import config
from services import odds_api
from services.user_service import get_user_by_id, credit_balance

# --- GESTION DES MATCHS ---

def get_estimated_end_time(matches: list) -> datetime:
    max_end = datetime.now(timezone.utc)
    has_future = False
    for m in matches:
        start_str = m.get("commence_time")
        if not start_str: continue
        try:
            start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            sport = str(m.get("sport", "")).lower()
            if "basketball" in sport: duration = 150
            elif "tennis" in sport: duration = 180
            else: duration = 120
            end_time = start_time + timedelta(minutes=duration)
            if not has_future or end_time > max_end:
                max_end = end_time
                has_future = True
        except: pass
        
    return max_end

def get_active_matches():
    return supabase.table("matches").select("*").eq("status", "NS").execute().data

def get_matches_by_sport(sport: str):
    return supabase.table("matches").select("*").eq("status", "NS").ilike("sport", f"%{sport}%").execute().data

def get_matches_by_ids(match_ids: list):
    if not match_ids: return []
    ids = [str(mid) for mid in match_ids]
    response = supabase.table("matches").select("*").in_("api_match_id", ids).execute()
    by_id = {str(m["api_match_id"]): m for m in response.data}
    return [by_id[mid] for mid in ids if mid in by_id]

def set_match_result(api_match_id, result: str):
    supabase.table("matches").update({"status": "FINISHED", "result": result}).eq("api_match_id", str(api_match_id)).execute()

# --- SESSIONS ET TICKETS ---

def get_session(session_id: str):
    res = supabase.table("sessions").select("*").eq("id", session_id).execute()
    return res.data[0] if res.data else None

def get_open_duels(exclude_creator_id: int = None):
    query = supabase.table("sessions").select("*").eq("status", "WAITING")
    if exclude_creator_id is not None:
        query = query.neq("creator_id", exclude_creator_id)
    return query.execute().data

def save_ticket(session_id: str, user_id: int, predictions: list):
    formatted = [{"match_id": str(p["match_id"]), "pick": p["pick"], "odds": float(p.get("odds", 1.0)), "item_used": p.get("item_used")} for p in predictions]
    res = supabase.table("tickets").insert({
        "session_id": session_id, 
        "user_id": user_id, 
        "predictions": formatted, 
        "status": "PENDING"
    }).execute()
    return res.data[0]

def create_session(creator_id: int, session_type: str, gross_fee: int, match_count: int, max_participants: int, prize_mode: str, predictions: list):
    user = get_user_by_id(creator_id)
    if not user or int(user["coins_balance"]) < gross_fee:
        return None, "Solde insuffisant pour créer ce duel."

    # Rake standardisé à 8%
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
        
        donation_share = int(round(gross_fee * config.DON_SHARE_FROM_RAKE))
        if donation_share > 0:
            credit_balance(0, donation_share)
            
        return session, "Succès"
    except Exception as e:
        return None, f"Erreur système lors de la création : {str(e)}"

def join_session(session_id: str, joiner_id: int, predictions: list):
    session = get_session(session_id)
    if not session or session["status"] != "WAITING":
        return None, "Session fermée ou introuvable."

    required = session["match_count"]
    if len(predictions) != required:
        return None, f"Ton ticket doit contenir exactement {required} pronostic(s)."

    gross_fee = int(session["gross_entry_fee"])
    joiner = get_user_by_id(joiner_id)
    if not joiner or int(joiner["coins_balance"]) < gross_fee:
        return None, "Solde insuffisant."

    tickets = get_tickets_for_session(session_id)
    if len(tickets) >= session.get("max_participants", 2):
        return None, "Le salon est déjà plein."

    supabase.table("users").update({"coins_balance": int(joiner["coins_balance"]) - gross_fee}).eq("telegram_id", joiner_id).execute()
    save_ticket(session_id, joiner_id, predictions)
    
    donation_share = int(round(gross_fee * config.DON_SHARE_FROM_RAKE))
    if donation_share > 0:
        credit_balance(0, donation_share)

    if len(tickets) + 1 == session.get("max_participants", 2):
        update_data = {"status": "IN_PROGRESS"}
        if session["type"] == "DUEL": update_data["opponent_id"] = joiner_id
        supabase.table("sessions").update(update_data).eq("id", session_id).execute()
        return get_session(session_id), "Succès"
    
    return session, "En attente de joueurs"

def get_tickets_for_session(session_id: str) -> list:
    return supabase.table("tickets").select("*").eq("session_id", session_id).execute().data

def get_user_sessions(user_id: int, history_limit: int = 3) -> list:
    user_tickets = supabase.table("tickets").select("session_id").eq("user_id", user_id).execute().data
    session_ids = [t["session_id"] for t in user_tickets]
    if not session_ids: return []

    active = supabase.table("sessions").select("*").in_("id", session_ids).in_("status", ["WAITING", "IN_PROGRESS"]).execute().data
    completed = supabase.table("sessions").select("*").in_("id", session_ids).eq("status", "COMPLETED").order("created_at", desc=True).limit(history_limit).execute().data
    return active + completed

def cancel_expired_sessions():
    expiration_date = (datetime.now(timezone.utc) - timedelta(hours=config.SESSION_EXPIRATION_HOURS)).isoformat()
    expired = supabase.table("sessions").select("*").eq("status", "WAITING").lte("created_at", expiration_date).execute().data
    
    for session in expired:
        tickets = get_tickets_for_session(session["id"])
        for t in tickets: credit_balance(t["user_id"], session["gross_entry_fee"])
        supabase.table("sessions").update({"status": "CANCELLED"}).eq("id", session["id"]).execute()
        supabase.table("tickets").update({"status": "CANCELLED"}).eq("session_id", session["id"]).execute()

def find_resolvable_sessions(api_match_id) -> list:
    sessions = supabase.table("sessions").select("*").eq("status", "IN_PROGRESS").execute().data
    target = str(api_match_id)
    resolvable = []

    for session in sessions:
        tickets = get_tickets_for_session(session["id"])
        if not tickets: continue
        all_match_ids = set(str(p["match_id"]) for t in tickets for p in t["predictions"])
        if target not in all_match_ids: continue
        matches = get_matches_by_ids(list(all_match_ids))
        if len(matches) == len(all_match_ids) and all(m.get("result") for m in matches):
            resolvable.append(session)

    return resolvable

def resolve_session(session_id: str):
    session = get_session(session_id)
    if not session or session["status"] != "IN_PROGRESS": return None

    tickets = get_tickets_for_session(session_id)
    all_match_ids = set(str(p["match_id"]) for t in tickets for p in t["predictions"])
    matches = get_matches_by_ids(list(all_match_ids))
    results_by_match = {str(m["api_match_id"]): m.get("result") for m in matches}

    scores = []
    for t in tickets:
        correct = 0
        valid_odds = 0.0
        for p in t["predictions"]:
            match_res = results_by_match.get(str(p["match_id"]))
            if match_res == "CANCEL": continue
            elif match_res == p["pick"]:
                correct += 1
                valid_odds += float(p.get("odds", 1.0))
        scores.append({"user_id": t["user_id"], "correct": correct, "valid_odds": round(valid_odds, 2)})

    scores.sort(key=lambda x: (x["correct"], x["valid_odds"]), reverse=True)
    pot_total = session["net_entry_fee"] * len(tickets)
    outcomes = {"session_id": session_id, "type": session["type"], "scores": scores, "pot": pot_total, "notifications": []}

    if session["type"] == "DUEL":
        if scores[0]["correct"] == scores[1]["correct"] and scores[0]["valid_odds"] == scores[1]["valid_odds"]:
            gross_fee = session["gross_entry_fee"]
            credit_balance(scores[0]["user_id"], gross_fee)
            credit_balance(scores[1]["user_id"], gross_fee)
            outcomes["winner_id"] = None
            outcomes["is_draw_refund"] = True
        else:
            credit_balance(scores[0]["user_id"], pot_total)
            outcomes["winner_id"] = scores[0]["user_id"]
            
    elif session["type"] == "ARENA":
        if session.get("prize_mode") == "TOP_3" and len(scores) >= 3:
            payouts = [pot_total * 0.50, pot_total * 0.38, pot_total * 0.12]
            unclaimed_pot = 0
            
            for i in range(3):
                if scores[i]["correct"] > 0: credit_balance(scores[i]["user_id"], int(payouts[i]))
                else: unclaimed_pot += int(payouts[i])
            
            if unclaimed_pot > 0 and scores[0]["correct"] > 0:
                credit_balance(scores[0]["user_id"], unclaimed_pot)
                
            outcomes["winner_id"] = scores[0]["user_id"] if scores[0]["correct"] > 0 else None
        else:
            if scores[0]["correct"] > 0: 
                credit_balance(scores[0]["user_id"], pot_total)
                outcomes["winner_id"] = scores[0]["user_id"]
            else:
                outcomes["winner_id"] = None

    supabase.table("sessions").update({"status": "COMPLETED", "winner_id": outcomes.get("winner_id")}).eq("id", session_id).execute()
    supabase.table("tickets").update({"status": "RESOLVED"}).eq("session_id", session_id).execute()
    return outcomes

# --- API EXTERNES ---

async def sync_matches_from_api_async():
    try:
        matches, quota, calls_used = await odds_api.sync_today_matches(config.ODDS_API_KEY)
        update_api_quota(quota)
        if not matches: return 0, "Aucun match."
        saved = 0
        for match in matches:
            try:
                supabase.table("matches").upsert(match, on_conflict="api_match_id").execute()
                saved += 1
            except Exception: pass
        return saved, f"Succès : {saved} matchs importés."
    except Exception as e: return 0, str(e)

async def fetch_and_update_scores_for_resolution(session_id: str):
    tickets = get_tickets_for_session(session_id)
    if not tickets: return False, "Aucun ticket trouvé."
    
    match_ids = set(str(p["match_id"]) for t in tickets for p in t["predictions"])
    matches = get_matches_by_ids(list(match_ids))
    sports = set(m["sport"] for m in matches)
    
    quota = None
    updated_count = 0
    
    async with httpx.AsyncClient() as client:
        for sport in sports:
            url = f"https://api.the-odds-api.com/v4/sports/{sport}/scores/?apiKey={config.ODDS_API_KEY}&daysFrom=3"
            resp = await client.get(url, timeout=15)
            
            if resp.status_code == 200:
                quota = resp.headers.get("x-requests-remaining", quota)
                for event in resp.json():
                    if str(event["id"]) in match_ids and event.get("completed"):
                        scores = event.get("scores")
                        home_score = away_score = 0
                        if scores:
                            for s in scores:
                                try: score_val = int(s.get("score") or 0)
                                except: score_val = 0
                                if s.get("name") == event.get("home_team"): home_score = score_val
                                elif s.get("name") == event.get("away_team"): away_score = score_val
                        
                        if home_score > away_score: res = "HOME"
                        elif away_score > home_score: res = "AWAY"
                        else: res = "DRAW"
                        
                        set_match_result(str(event["id"]), res)
                        updated_count += 1
                        
    if quota: update_api_quota(quota)
    return True, f"{updated_count} matchs terminés mis à jour."

# --- LEADERBOARDS & REGLAGES ---

def get_weekly_leaderboard(limit: int = 10) -> list:
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

def get_dynamic_leaderboard(category: str, period: str, limit: int = 10):
    """Fonction en attente pour le classement réseau/volume/winrate"""
    return []

def update_api_quota(quota_str: str):
    if quota_str:
        try: supabase.table("app_settings").upsert({"setting_key": "api_quota", "setting_value": str(quota_str)}).execute()
        except Exception: pass

def get_api_quota() -> str:
    try:
        res = supabase.table("app_settings").select("setting_value").eq("setting_key", "api_quota").execute()
        if res.data and res.data[0].get("setting_value"): return str(res.data[0]["setting_value"])
    except Exception: pass
    return "Inconnu"
