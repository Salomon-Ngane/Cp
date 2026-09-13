import string
import random
import logging
from datetime import datetime, timezone, timedelta
from database.connection import supabase
from database.users import get_user_by_id, update_user_balance, process_rake_and_referrals

logger = logging.getLogger(__name__)

def generate_short_code(length=7) -> str:
    """Génère un identifiant court à 7 caractères (ex: S7K9M2P)."""
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choices(chars, k=length))

def get_estimated_end_time(matches: list) -> datetime:
    """Calcule l'heure de fin estimée en se basant sur le coup d'envoi le plus tardif + la durée du sport."""
    max_end = datetime.now(timezone.utc)
    has_future = False
    
    for m in matches:
        start_str = m.get("commence_time")
        if not start_str: 
            continue
        try:
            start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            sport = str(m.get("sport", "")).lower()
            
            if "basketball" in sport: 
                duration = 150
            elif "tennis" in sport: 
                duration = 180
            else: 
                duration = 120  # Football par défaut
            
            end_time = start_time + timedelta(minutes=duration)
            if not has_future or end_time > max_end:
                max_end = end_time
                has_future = True
        except Exception:
            pass
            
    return max_end

def get_session(session_id: str):
    """Récupère une session par son UUID ou par son code court à 7 caractères."""
    res = supabase.table("sessions").select("*").eq("id", session_id).execute().data
    if res:
        return res[0]
    
    # Recherche par code court à 7 caractères
    res_code = supabase.table("sessions").select("*").eq("session_code", session_id.upper()).execute().data
    return res_code[0] if res_code else None

def get_matches_by_sport(sport: str):
    """Récupère tous les matchs à venir pour un sport donné."""
    res = supabase.table("matches").select("*").ilike("sport", f"%{sport}%").execute().data
    return res or []

def get_matches_by_ids(match_ids: list):
    """Récupère les détails des matchs par leurs identifiants API."""
    if not match_ids:
        return []
    res = supabase.table("matches").select("*").in_("api_match_id", match_ids).execute().data
    return res or []

def create_session(creator_id: int, session_type: str, gross_fee: int, match_count: int, max_participants: int = 2, prize_mode: str = "WINNER_TAKES_ALL", predictions: list = None):
    """Crée une nouvelle session de duel/arena et débite la mise du créateur."""
    user = get_user_by_id(creator_id)
    if not user or user.get("coins_balance", 0) < gross_fee:
        return None, "Solde insuffisant pour créer ce salon."

    session_code = generate_short_code(7)
    
    session_data = {
        "session_code": session_code,
        "creator_id": creator_id,
        "type": session_type,
        "gross_entry_fee": gross_fee,
        "match_count": match_count,
        "max_participants": max_participants if session_type == "ARENA" else 2,
        "prize_mode": prize_mode,
        "status": "WAITING"
    }

    session_res = supabase.table("sessions").insert(session_data).execute().data
    if not session_res:
        return None, "Erreur lors de la création de la session."

    session = session_res[0]
    session_id = session["id"]

    # Inscription du ticket du créateur
    ticket_data = {
        "session_id": session_id,
        "user_id": creator_id,
        "predictions": predictions or [],
        "status": "WAITING"
    }
    supabase.table("tickets").insert(ticket_data).execute()

    # Débit de la mise
    update_user_balance(creator_id, -gross_fee)

    return session, "Session créée avec succès."

def join_session(session_id: str, user_id: int, predictions: list):
    """Permet à un utilisateur de rejoindre une session existante."""
    session = get_session(session_id)
    if not session or session["status"] != "WAITING":
        return None, "Salon indisponible."

    user = get_user_by_id(user_id)
    fee = session["gross_entry_fee"]
    if not user or user.get("coins_balance", 0) < fee:
        return None, "Solde insuffisant pour rejoindre ce salon."

    existing_tickets = supabase.table("tickets").select("*").eq("session_id", session["id"]).execute().data or []
    if len(existing_tickets) >= session["max_participants"]:
        return None, "Ce salon est déjà complet."

    # Débit de la mise
    update_user_balance(user_id, -fee)

    # Création du ticket du challenger
    ticket_data = {
        "session_id": session["id"],
        "user_id": user_id,
        "predictions": predictions,
        "status": "WAITING"
    }
    supabase.table("tickets").insert(ticket_data).execute()

    # Si le quota de joueurs est atteint, la session passe en cours
    new_count = len(existing_tickets) + 1
    if new_count >= session["max_participants"]:
        supabase.table("sessions").update({"status": "IN_PROGRESS"}).eq("id", session["id"]).execute()

    return session, "Vous avez rejoint le salon avec succès."

def get_tickets_for_session(session_id: str):
    """Récupère tous les tickets soumis dans une session."""
    res = supabase.table("tickets").select("*").eq("session_id", session_id).execute().data
    return res or []

def get_user_sessions(user_id: int, history_limit: int = 50):
    """Récupère la liste des sessions dans lesquelles l'utilisateur est inscrit."""
    user_tickets = supabase.table("tickets").select("session_id").eq("user_id", user_id).execute().data or []
    if not user_tickets:
        return []
    
    session_ids = [t["session_id"] for t in user_tickets]
    query = supabase.table("sessions").select("*").in_("id", session_ids).order("created_at", desc=True)
    if history_limit > 0:
        query = query.limit(history_limit)
    return query.execute().data or []

def cancel_expired_sessions():
    """Rembourse et annule les sessions en attente créées depuis plus de 24 heures."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    
    expired = supabase.table("sessions").select("*").eq("status", "WAITING").lt("created_at", cutoff.isoformat()).execute().data or []
    for s in expired:
        tickets = get_tickets_for_session(s["id"])
        for t in tickets:
            update_user_balance(t["user_id"], s["gross_entry_fee"])
        supabase.table("sessions").update({"status": "CANCELLED"}).eq("id", s["id"]).execute()

# --- GESTION DU LEADERBOARD (TOP DAY / WEEK / MONTH) ---

def get_top_leaderboard(limit: int = 10):
    """
    Calcule le classement des meilleurs pronostiqueurs sur la base du taux de réussite (Taux de victoires).
    """
    all_users = supabase.table("users").select("*").execute().data or []
    leaderboard = []

    for u in all_users:
        if u["telegram_id"] == 0:  # Ignorer le compte Don
            continue
            
        tickets = supabase.table("tickets").select("*").eq("user_id", u["telegram_id"]).execute().data or []
        completed_tickets = [t for t in tickets if t.get("status") in ("WON", "LOST")]
        
        if not completed_tickets:
            continue
            
        wins = sum(1 for t in completed_tickets if t.get("status") == "WON")
        total = len(completed_tickets)
        success_rate = (wins / total) * 100.0

        leaderboard.append({
            "telegram_id": u["telegram_id"],
            "username": u.get("username", f"User_{u['telegram_id']}"),
            "user_code": u.get("user_code", "N/A"),
            "wins": wins,
            "total": total,
            "success_rate": round(success_rate, 2)
        })

    # Tri par taux de réussite décroissant, puis par nombre de victoires
    leaderboard.sort(key=lambda x: (x["success_rate"], x["wins"]), reverse=True)
    return leaderboard[:limit]

def distribute_top_rewards(total_prize_pool: int):
    """
    Distribue la cagnotte du classement Top aux 10 premiers :
    - Top 1: 40%
    - Top 2: 25%
    - Top 3: 18%
    - Top 4: 10%
    - Top 5: 7%
    - Top 6 à 10: +1 Item Boost (+0.5 cote)
    """
    top_players = get_top_leaderboard(limit=10)
    if not top_players:
        return False, "Aucun joueur dans le classement."

    shares = [0.40, 0.25, 0.18, 0.10, 0.07]
    summary = []

    for index, player in enumerate(top_players):
        uid = player["telegram_id"]
        uname = player["username"]
        
        if index < 5:
            # Distribution des Coins au Top 5
            reward_coins = int(total_prize_pool * shares[index])
            update_user_balance(uid, reward_coins)
            summary.append(f"🥇 #{index+1} {uname}: +{reward_coins} Coins")
        else:
            # Attribution de l'Item 1 (Boost Cote +0.5) pour les rangs 6 à 10
            u = get_user_by_id(uid)
            cur_boosts = u.get("item_boost_count", 0) if u else 0
            supabase.table("users").update({"item_boost_count": cur_boosts + 1}).eq("telegram_id", uid).execute()
            summary.append(f"🎁 #{index+1} {uname}: +1 Item Boost (+0.5 Cote)")

    return True, "\n".join(summary)
