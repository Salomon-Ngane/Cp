from supabase import create_client, Client
import config

supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)


# --- UTILISATEURS ---

def get_or_create_user(telegram_id: int, username: str, referred_by: int = None):
    """Récupère l'utilisateur ou le crée avec un solde initial de 0."""
    res = supabase.table("users").select("*").eq("telegram_id", telegram_id).execute()
    if res.data:
        return res.data[0]

    new_user = {
        "telegram_id": telegram_id,
        "username": username,
        "coins_balance": 0,
        "referred_by": referred_by,
    }
    insert_res = supabase.table("users").insert(new_user).execute()
    return insert_res.data[0]


def credit_balance(telegram_id: int, amount: float):
    """Ajoute `amount` au solde d'un utilisateur (recharge admin ou paiement de gain)."""
    user = get_or_create_user(telegram_id, "")
    new_balance = user["coins_balance"] + amount
    supabase.table("users").update({"coins_balance": new_balance}).eq("telegram_id", telegram_id).execute()
    return new_balance


# --- MATCHS ---

def create_sample_matches():
    """Génère 5 matchs de test pour essayer le système de pronostics."""
    sample_matches = [
        {"api_match_id": 101, "home_team": "Real Madrid", "away_team": "Barcelona", "status": "NS"},
        {"api_match_id": 102, "home_team": "PSG", "away_team": "Marseille", "status": "NS"},
        {"api_match_id": 103, "home_team": "Arsenal", "away_team": "Chelsea", "status": "NS"},
        {"api_match_id": 104, "home_team": "Bayern Munich", "away_team": "Dortmund", "status": "NS"},
        {"api_match_id": 105, "home_team": "Inter Milan", "away_team": "AC Milan", "status": "NS"},
    ]
    for match in sample_matches:
        supabase.table("matches").upsert(match, on_conflict="api_match_id").execute()
    return get_active_matches()


def get_active_matches():
    """Récupère tous les matchs à venir/non démarrés."""
    response = supabase.table("matches").select("*").eq("status", "NS").execute()
    return response.data


def get_matches_by_ids(match_ids: list):
    """Récupère des matchs précis (sert à figer la grille d'un duel), dans l'ordre demandé."""
    if not match_ids:
        return []
    
    # Conversion de la liste d'identifiants en entiers pour la requête SQL
    clean_ids = [int(mid) for mid in match_ids if str(mid).isdigit()]
    if not clean_ids:
        return []

    response = supabase.table("matches").select("*").in_("api_match_id", clean_ids).execute()
    by_id = {m["api_match_id"]: m for m in response.data}
    return [by_id[mid] for mid in clean_ids if mid in by_id]


def set_match_result(api_match_id: int, result: str):
    """Enregistre le résultat d'un match (HOME / DRAW / AWAY) et le marque comme terminé."""
    supabase.table("matches").update({
        "status": "FINISHED",
        "result": result,
    }).eq("api_match_id", api_match_id).execute()


# --- DUELS (SESSIONS) ---

def create_duel_session(creator_id: int, gross_fee: float, match_ids: list):
    """Crée une session de duel 1v1 : prélève la mise brute et fige la liste des matchs jouée."""
    user = get_or_create_user(creator_id, "")
    if user["coins_balance"] < gross_fee:
        return None, "Solde insuffisant"

    rake = gross_fee * config.RAKE_PERCENTAGE
    net_fee = gross_fee - rake

    new_balance = user["coins_balance"] - gross_fee
    supabase.table("users").update({"coins_balance": new_balance}).eq("telegram_id", creator_id).execute()

    session_data = {
        "creator_id": creator_id,
        "type": "DUEL",
        "gross_entry_fee": gross_fee,
        "net_entry_fee": net_fee,
        "max_players": 2,
        "status": "WAITING",
        "match_ids": match_ids,
    }
    session = supabase.table("sessions").insert(session_data).execute()
    return session.data[0], "Succès"


def get_session(session_id: str):
    """Récupère une session par son id."""
    res = supabase.table("sessions").select("*").eq("id", session_id).execute()
    return res.data[0] if res.data else None


def get_open_duels(exclude_creator_id: int = None):
    """Récupère les duels en attente d'adversaire (optionnellement en excluant ses propres duels)."""
    query = supabase.table("sessions").select("*").eq("status", "WAITING").eq("type", "DUEL")
    if exclude_creator_id is not None:
        query = query.neq("creator_id", exclude_creator_id)
    res = query.execute()
    return res.data


def join_duel_session(session_id: str, joiner_id: int, predictions: list):
    """
    Un second joueur rejoint un duel WAITING : prélève sa mise (identique à celle du créateur),
    verrouille la session et enregistre son ticket.
    """
    session = get_session(session_id)
    if not session:
        return None, "Ce duel n'existe plus."
    if session["status"] != "WAITING":
        return None, "Ce duel n'est plus disponible (déjà rejoint ou annulé)."
    if session["creator_id"] == joiner_id:
        return None, "Vous ne pouvez pas rejoindre votre propre duel."

    gross_fee = session["gross_entry_fee"]
    joiner = get_or_create_user(joiner_id, "")
    if joiner["coins_balance"] < gross_fee:
        return None, f"Solde insuffisant (il vous faut {gross_fee} Coins)."

    new_balance = joiner["coins_balance"] - gross_fee
    supabase.table("users").update({"coins_balance": new_balance}).eq("telegram_id", joiner_id).execute()

    # Le .eq("status", "WAITING") protège contre une double-jointure concurrente :
    # si un autre joueur a rejoint entre notre lecture et notre écriture, 0 ligne est mise à jour.
    updated = (
        supabase.table("sessions")
        .update({"opponent_id": joiner_id, "status": "IN_PROGRESS"})
        .eq("id", session_id)
        .eq("status", "WAITING")
        .execute()
    )

    if not updated.data:
        # Remboursement : quelqu'un d'autre a rejoint entre-temps
        supabase.table("users").update({"coins_balance": joiner["coins_balance"]}).eq("telegram_id", joiner_id).execute()
        return None, "Un autre joueur vient de rejoindre ce duel juste avant vous."

    save_ticket(session_id, joiner_id, predictions)
    return updated.data[0], "Succès"


# --- TICKETS ---

def save_ticket(session_id: str, user_id: int, predictions: list):
    """Enregistre le ticket de pronostics d'un joueur pour une session donnée."""
    ticket_data = {
        "session_id": session_id,
        "user_id": user_id,
        "predictions": predictions,
        "status": "PENDING",
    }
    res = supabase.table("tickets").insert(ticket_data).execute()
    return res.data[0]


# --- RÉSOLUTION & PAIEMENT ---

def find_resolvable_sessions(api_match_id: int):
    """
    Parcourt toutes les sessions IN_PROGRESS et retourne celles qui contiennent
    le match résolu, SI tous les matchs de leur grille sont désormais FINISHED.
    """
    res = (
        supabase.table("sessions")
        .select("*")
        .eq("status", "IN_PROGRESS")
        .eq("type", "DUEL")
        .execute()
    )

    resolvable = []
    target_id = int(api_match_id)

    for session in res.data:
        m_ids = session.get("match_ids") or []

        # Convertit la liste en int pour éviter les incompatibilités de types (str vs int)
        normalized_ids = [int(mid) for mid in m_ids if str(mid).isdigit()]

        # Si le match qu'on vient de résoudre fait partie de ce duel
        if target_id in normalized_ids:
            # On vérifie si TOUS les matchs du duel ont un résultat
            matches = get_matches_by_ids(normalized_ids)
            if matches and all(m.get("result") for m in matches):
                resolvable.append(session)

    return resolvable


def resolve_duel(session_id: str):
    """Compare les deux tickets d'un duel, désigne un gagnant (ou partage en cas d'égalité) et paie."""
    session = get_session(session_id)
    if not session or session["status"] != "IN_PROGRESS":
        return None

    tickets_res = supabase.table("tickets").select("*").eq("session_id", session_id).execute()
    tickets = tickets_res.data
    if len(tickets) != 2:
        return None  # sécurité : un duel doit avoir exactement 2 tickets pour être résolu

    matches = get_matches_by_ids(session["match_ids"])
    results_by_match = {m["api_match_id"]: m.get("result") for m in matches}

    scores = {}
    for ticket in tickets:
        correct = sum(
            1 for p in ticket["predictions"]
            if results_by_match.get(p["match_id"]) == p["pick"]
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
        winner_id = None  # égalité : partage
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
