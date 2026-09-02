from datetime import datetime, timezone, timedelta
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

def get_matches_by_ids(match_ids: list):
    """Récupère des matchs précis à partir d'une liste d'ids (texte, format TheOddsAPI)."""
    if not match_ids:
        return []
    ids = [str(mid) for mid in match_ids]
    response = supabase.table("matches").select("*").in_("api_match_id", ids).execute()
    by_id = {str(m["api_match_id"]): m for m in response.data}
    return [by_id[mid] for mid in ids if mid in by_id]

def set_match_result(api_match_id, result: str):
    """Enregistre le résultat d'un match (HOME / DRAW / AWAY) et le marque comme terminé."""
    supabase.table("matches").update({
        "status": "FINISHED",
        "result": result,
    }).eq("api_match_id", str(api_match_id)).execute()

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

def get_tickets_for_session(session_id: str) -> list:
    return supabase.table("tickets").select("*").eq("session_id", session_id).execute().data

def get_user_sessions(user_id: int, history_limit: int = 3) -> list:
    """Duels actifs (sans limite) + historique des `history_limit` derniers duels terminés."""
    filter_str = f"creator_id.eq.{user_id},opponent_id.eq.{user_id}"

    active = (
        supabase.table("sessions").select("*")
        .in_("status", ["WAITING", "IN_PROGRESS"])
        .or_(filter_str)
        .execute().data
    )
    completed = (
        supabase.table("sessions").select("*")
        .eq("status", "COMPLETED")
        .or_(filter_str)
        .order("created_at", desc=True)
        .limit(history_limit)
        .execute().data
    )
    return active + completed


# --- RÉSOLUTION & PAIEMENT ---
# NB : depuis la refonte multi-sport, chaque joueur a sa PROPRE grille (match_count
# les matche seulement en nombre, pas en contenu). "Tous les matchs d'une session"
# n'existe donc plus au niveau de la session — on le reconstruit à partir de l'union
# des deux tickets à chaque résolution.

def find_resolvable_sessions(api_match_id) -> list:
    """Sessions IN_PROGRESS contenant ce match (dans l'un ou l'autre ticket),
    et dont TOUS les matchs des deux grilles ont désormais un résultat."""
    sessions = (
        supabase.table("sessions").select("*")
        .eq("status", "IN_PROGRESS").eq("type", "DUEL").execute().data
    )
    target = str(api_match_id)
    resolvable = []

    for session in sessions:
        tickets = supabase.table("tickets").select("*").eq("session_id", session["id"]).execute().data
        if len(tickets) != 2:
            continue

        all_match_ids = set()
        for t in tickets:
            all_match_ids.update(str(p["match_id"]) for p in t["predictions"])

        if target not in all_match_ids:
            continue

        matches = get_matches_by_ids(list(all_match_ids))
        if len(matches) == len(all_match_ids) and all(m.get("result") for m in matches):
            resolvable.append(session)

    return resolvable


def resolve_duel(session_id: str):
    """Compare les deux grilles (indépendantes) d'un duel, désigne un vainqueur
    (ou partage en cas d'égalité) et paie la cagnotte."""
    session = get_session(session_id)
    if not session or session["status"] != "IN_PROGRESS":
        return None

    tickets = supabase.table("tickets").select("*").eq("session_id", session_id).execute().data
    if len(tickets) != 2:
        return None

    all_match_ids = set()
    for t in tickets:
        all_match_ids.update(str(p["match_id"]) for p in t["predictions"])
    matches = get_matches_by_ids(list(all_match_ids))
    results_by_match = {str(m["api_match_id"]): m.get("result") for m in matches}

    scores = {}
    for ticket in tickets:
        correct = sum(
            1 for p in ticket["predictions"]
            if results_by_match.get(str(p["match_id"])) == p["pick"]
        )
        scores[ticket["user_id"]] = correct

    creator_id = session["creator_id"]
    opponent_id = session["opponent_id"]
    creator_score = scores.get(creator_id, 0)
    opponent_score = scores.get(opponent_id, 0)
    pot = session["net_entry_fee"] * 2

    if creator_score > opponent_score:
        winner_id = creator_id
        credit_balance(creator_id, pot)
    elif opponent_score > creator_score:
        winner_id = opponent_id
        credit_balance(opponent_id, pot)
    else:
        winner_id = None
        credit_balance(creator_id, pot / 2)
        credit_balance(opponent_id, pot / 2)

    supabase.table("sessions").update({"status": "COMPLETED", "winner_id": winner_id}).eq("id", session_id).execute()
    supabase.table("tickets").update({"status": "RESOLVED"}).eq("session_id", session_id).execute()

    return {
        "session_id": session_id,
        "creator_id": creator_id,
        "opponent_id": opponent_id,
        "creator_score": creator_score,
        "opponent_score": opponent_score,
        "winner_id": winner_id,
        "pot": pot,
    }


# --- ADMIN FUNCTIONS ---

async def sync_matches_from_api_async():
    try:
        matches, quota, calls_used = await odds_api.sync_today_matches(config.ODDS_API_KEY)
        if not matches:
            return 0, f"🔍 Aucun match trouvé pour aujourd'hui ({calls_used} appels effectués). Réessaie plus tard, le calendrier se remplit au fil de la journée !"

        saved = 0
        last_error = None
        for match in matches:
            try:
                supabase.table("matches").upsert(match, on_conflict="api_match_id").execute()
                saved += 1
            except Exception as e:
                last_error = str(e)

        if saved == 0:
            return 0, (
                f"❌ {len(matches)} matchs récupérés depuis l'API, mais AUCUN n'a pu être écrit en base.\n"
                f"Dernière erreur Supabase : `{last_error}`\n\n"
                "Piste probable : `api_match_id` n'a plus de contrainte UNIQUE après son passage en texte "
                "(vérifie dans Supabase), ou la clé utilisée par le bot n'est plus `service_role` (RLS)."
            )
        if saved < len(matches):
            return saved, f"⚠️ {saved}/{len(matches)} matchs enregistrés, le reste a échoué.\nDernière erreur : `{last_error}`"

        return saved, (
            f"🎉 **{saved} matchs sont sur le terrain et prêts à jouer !**\n"
            "🏅 Foot, Basket, Tennis — la journée est chargée.\n"
            f"📞 Appels API utilisés : `{calls_used}`\n"
            f"📊 Requêtes API restantes ce mois-ci : `{quota}`"
        )
    except Exception as e:
        return 0, f"❌ Erreur pendant la synchronisation : `{str(e)}`"


# --- SCORES EN DIRECT (cache partagé entre joueurs, 3 min) ---

LIVE_CACHE_MAX_AGE_SECONDS = 180

# --- CLASSEMENT HEBDOMADAIRE ---

def get_weekly_leaderboard(limit: int = 10) -> list:
    """Classement des joueurs par nombre de duels gagnés sur les 7 derniers jours
    (basé sur created_at des sessions, faute de resolved_at dédié — approximation
    raisonnable tant que les duels se résolvent le jour même)."""
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    sessions = (
        supabase.table("sessions").select("*")
        .eq("status", "COMPLETED")
        .gte("created_at", week_ago)
        .not_.is_("winner_id", "null")
        .execute().data
    )

    tally = {}
    for s in sessions:
        winner = s["winner_id"]
        pot = s["net_entry_fee"] * 2
        entry = tally.setdefault(winner, {"wins": 0, "coins_won": 0})
        entry["wins"] += 1
        entry["coins_won"] += pot

    ranked = sorted(tally.items(), key=lambda x: (-x[1]["wins"], -x[1]["coins_won"]))[:limit]

    leaderboard = []
    for telegram_id, stats in ranked:
        user = get_user_by_id(telegram_id)
        username = (user["username"] if user and user.get("username") else None) or f"Joueur {telegram_id}"
        leaderboard.append({
            "telegram_id": telegram_id,
            "username": username,
            "wins": stats["wins"],
            "coins_won": stats["coins_won"],
        })
    return leaderboard


async def get_live_scores_for_matches(match_ids: list) -> dict:
    """Renvoie le statut live des matchs demandés. Ne rappelle l'API que pour les
    sports dont le cache local a plus de 3 minutes — mutualisé entre tous les joueurs."""
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
        import httpx
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
        matches = get_matches_by_ids(match_ids)  # relire les valeurs fraîches

    return {str(m["api_match_id"]): m for m in matches}
