import httpx
from datetime import datetime, timezone, timedelta
from database.connection import supabase
from database.users import credit_balance, get_user_by_id, update_api_quota
import config
import odds_api

# --- MATCHS ---

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

def create_session(creator_id: int, session_type: str, gross_fee: int, match_count: int, max_participants: int, prize_mode: str, predictions: list):
    user = get_user_by_id(creator_id)
    if not user or int(user["coins_balance"]) < gross_fee:
        return None, "Solde insuffisant"

    rake_rate = config.RAKE_1V1 if session_type == "DUEL" else config.RAKE_ARENA
    net_fee = gross_fee - int(round(gross_fee * rake_rate))

    supabase.table("users").update({"coins_balance": int(user["coins_balance"]) - gross_fee}).eq("telegram_id", creator_id).execute()

    session_data = {
        "creator_id": creator_id,
        "type": session_type,
        "gross_entry_fee": gross_fee,
        "net_entry_fee": net_fee,
        "match_count": match_count,
        "max_participants": max_participants,
        "prize_mode": prize_mode,
        "status": "WAITING"
    }
    session = supabase.table("sessions").insert(session_data).execute().data[0]
    save_ticket(session["id"], creator_id, predictions)
    return session, "Succès"

def join_session(session_id: str, joiner_id: int, predictions: list):
    session = get_session(session_id)
    if not session or session["status"] != "WAITING":
        return None, "Session fermée ou introuvable."

    gross_fee = int(session["gross_entry_fee"])
    joiner = get_user_by_id(joiner_id)
    if not joiner or int(joiner["coins_balance"]) < gross_fee:
        return None, "Solde insuffisant."

    tickets = get_tickets_for_session(session_id)
    if len(tickets) >= session.get("max_participants", 2):
        return None, "Le salon est déjà plein."

    supabase.table("users").update({"coins_balance": int(joiner["coins_balance"]) - gross_fee}).eq("telegram_id", joiner_id).execute()
    save_ticket(session_id, joiner_id, predictions)

    if len(tickets) + 1 == session.get("max_participants", 2):
        update_data = {"status": "IN_PROGRESS"}
        if session["type"] == "DUEL": update_data["opponent_id"] = joiner_id
        supabase.table("sessions").update(update_data).eq("id", session_id).execute()
        return get_session(session_id), "Succès"
    
    return session, "En attente de joueurs"

def save_ticket(session_id: str, user_id: int, predictions: list):
    formatted = [{"match_id": str(p["match_id"]), "pick": p["pick"], "odds": float(p.get("odds", 1.0))} for p in predictions]
    return supabase.table("tickets").insert({"session_id": session_id, "user_id": user_id, "predictions": formatted, "status": "PENDING"}).execute().data[0]

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

def resolve_session(session_id: str):
    session = get_session(session_id)
    if not session or session["status"] != "IN_PROGRESS": return None

    tickets = get_tickets_for_session(session_id)
    all_match_ids = set(str(p["match_id"]) for t in tickets for p in t["predictions"])
    
    matches = get_matches_by_ids(list(all_match_ids))
    results_by_match = {str(m["api_match_id"]): m.get("result") for m in matches}

    scores = []
    for t in tickets:
        correct, valid_odds = 0, 1.0
        for p in t["predictions"]:
            match_res = results_by_match.get(str(p["match_id"]))
            if match_res == "CANCEL": continue
            elif match_res == p["pick"]:
                correct += 1
                valid_odds *= float(p.get("odds", 1.0))
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
            for i in range(3):
                if scores[i]["correct"] > 0: credit_balance(scores[i]["user_id"], int(payouts[i]))
            outcomes["winner_id"] = scores[0]["user_id"]
        else:
            if scores[0]["correct"] > 0: credit_balance(scores[0]["user_id"], pot_total)
            outcomes["winner_id"] = scores[0]["user_id"]

    supabase.table("sessions").update({"status": "COMPLETED", "winner_id": outcomes.get("winner_id")}).eq("id", session_id).execute()
    supabase.table("tickets").update({"status": "RESOLVED"}).eq("session_id", session_id).execute()
    return outcomes

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

LIVE_CACHE_MAX_AGE_SECONDS = 180
async def get_live_scores_for_matches(match_ids: list) -> dict:
    matches = get_matches_by_ids(match_ids)
    now = datetime.now(timezone.utc)
    sports_to_refresh = set()

    for m in matches:
        last_check = m.get("last_score_check")
        if not last_check:
            sports_to_refresh.add(m["sport"])
            continue
        last = datetime.fromisoformat(last_check.replace("Z", "+00:00"))
        if (now - last).total_seconds() > LIVE_CACHE_MAX_AGE_SECONDS:
            sports_to_refresh.add(m["sport"])

    if sports_to_refresh:
        async with httpx.AsyncClient() as client:
            for sport in sports_to_refresh:
                scores = await odds_api.fetch_live_scores(client, config.ODDS_API_KEY, sport)
                for s in scores:
                    supabase.table("matches").update({
                        "live_score_home": s["home_score"],
                        "live_score_away": s["away_score"],
                        "live_status": s["status"],
                        "last_score_check": now.isoformat(),
                    }).eq("api_match_id", s["api_match_id"]).execute()
        matches = get_matches_by_ids(match_ids)

    return {str(m["api_match_id"]): m for m in matches}

