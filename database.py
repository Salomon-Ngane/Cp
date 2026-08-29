from supabase import create_client, Client
import config
from datetime import datetime
import time

supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)

VALID_PICKS = {"HOME", "DRAW", "AWAY"}


def _check_response(res):
    """Vérifie la réponse Supabase et lève une exception simple en cas d'erreur.
    Retourne res.data ou [] si absent.
    """
    if res is None:
        raise RuntimeError("Supabase response is None")
    if getattr(res, "error", None):
        raise RuntimeError(f"Supabase error: {res.error}")
    return getattr(res, "data", []) or []


# --- UTILISATEURS ---


def get_or_create_user(telegram_id: int, username: str, referred_by: int = None):
    """Récupère l'utilisateur ou le crée avec un solde initial de 0 (upsert pour éviter les races)."""
    res = supabase.table("users").select("*").eq("telegram_id", telegram_id).execute()
    data = _check_response(res)
    if data:
        return data[0]

    new_user = {
        "telegram_id": telegram_id,
        "username": username,
        "coins_balance": 0,
        "referred_by": referred_by,
    }
    # upsert pour éviter les doublons en cas d'appels concurrents
    insert_res = supabase.table("users").upsert(new_user, on_conflict="telegram_id").execute()
    inserted = _check_response(insert_res)
    return inserted[0]


def credit_balance(telegram_id: int, amount: float):
    """Ajoute `amount` au solde d'un utilisateur (recharge admin ou paiement de gain)."""
    user = get_or_create_user(telegram_id, "")
    new_balance = user.get("coins_balance", 0) + amount
    res = supabase.table("users").update({"coins_balance": new_balance}).eq("telegram_id", telegram_id).execute()
    _check_response(res)
    return new_balance


# --- MATCHS ---


def get_schema_migration_sql() -> str:
    """Retourne les instructions SQL proposées pour mettre à jour le schéma de la base.

    NOTE: supabase-py n'exécute pas d'instructions SQL arbitraires via l'API REST publique.
    Exécutez ces instructions manuellement dans l'éditeur SQL de Supabase ou via psql
    en utilisant la clé/service role appropriée.
    """
    return """
-- 1. Ajouter le sport et les cotes dans la table 'matches'
ALTER TABLE matches 
ADD COLUMN IF NOT EXISTS sport TEXT DEFAULT 'FOOTBALL',
ADD COLUMN IF NOT EXISTS home_odds NUMERIC DEFAULT 2.0,
ADD COLUMN IF NOT EXISTS draw_odds NUMERIC DEFAULT 3.0,
ADD COLUMN IF NOT EXISTS away_odds NUMERIC DEFAULT 2.5;

-- 2. Ajouter le nombre de matchs requis dans la table 'sessions'
ALTER TABLE sessions 
ADD COLUMN IF NOT EXISTS match_count INT DEFAULT 5;

-- 3. S'assurer que la colonne result existe bien dans matches
ALTER TABLE matches 
ADD COLUMN IF NOT EXISTS result TEXT;
"""


def print_schema_migration_sql():
    """Affiche la SQL de migration (utile pour copier-coller dans Supabase).
    Retourne aussi la chaîne pour usage programmatique.
    """
    sql = get_schema_migration_sql()
    print(sql)
    return sql


def get_active_matches():
    """Récupère tous les matchs à venir/non démarrés."""
    response = supabase.table("matches").select("*").eq("status", "NS").execute()
    return _check_response(response)


def get_matches_by_ids(match_ids: list):
    """Récupère des matchs précis (sert à figer la grille d'un duel), dans l'ordre demandé."""
    if not match_ids:
        return []

    # Conversion de la liste d'identifiants en entiers pour la requête SQL
    clean_ids = [int(mid) for mid in match_ids if str(mid).isdigit()]
    if not clean_ids:
        return []

    response = supabase.table("matches").select("*").in_("api_match_id", clean_ids).execute()
    rows = _check_response(response)
    # Normaliser les clefs en int pour éviter les problèmes de type
    by_id = {int(m["api_match_id"]): m for m in rows}
    return [by_id[mid] for mid in clean_ids if mid in by_id]


def create_sample_match(
    api_match_id=None,
    home_team: str = "Home FC",
    away_team: str = "Away FC",
    start_time=None,
    sport: str = "FOOTBALL",
    home_odds: float = 2.0,
    draw_odds: float = 3.0,
    away_odds: float = 2.5,
    status: str = "NS",
):
    """Crée et insère un match factice dans la table `matches` et retourne l'enregistrement inséré.

    - Si `api_match_id` n'est pas fourni, on génère un identifiant basé sur l'horodatage.
    - `start_time` peut être un datetime; s'il est absent on prend maintenant.
    """
    if api_match_id is None:
        # identifiant simple unique pour les tests (changez la stratégie si besoin)
        api_match_id = int(time.time())

    ts = (start_time or datetime.utcnow()).replace(microsecond=0).isoformat() + "Z"

    record = {
        "api_match_id": api_match_id,
        "home_team": home_team,
        "away_team": away_team,
        "start_time": ts,
        "sport": sport,
        "home_odds": home_odds,
        "draw_odds": draw_odds,
        "away_odds": away_odds,
        "status": status,
        "result": None,
    }

    res = supabase.table("matches").insert(record).execute()
    rows = _check_response(res)
    return rows[0] if rows else None
def create_sample_matches():
    """Crée plusieurs matchs de test multi-sports."""
    matches = [
        {"api_match_id": 101, "home_team": "Real Madrid", "away_team": "Barcelona", "sport": "FOOTBALL", "home_odds": 2.10, "draw_odds": 3.40, "away_odds": 3.10, "status": "NS"},
        {"api_match_id": 102, "home_team": "PSG", "away_team": "Marseille", "sport": "FOOTBALL", "home_odds": 1.50, "draw_odds": 4.20, "away_odds": 6.00, "status": "NS"},
        {"api_match_id": 201, "home_team": "Lakers", "away_team": "Celtics", "sport": "BASKETBALL", "home_odds": 1.85, "draw_odds": 15.00, "away_odds": 1.95, "status": "NS"},
        {"api_match_id": 301, "home_team": "Alcaraz", "away_team": "Sinner", "sport": "TENNIS", "home_odds": 1.90, "draw_odds": 20.00, "away_odds": 1.90, "status": "NS"},
    ]
    res = supabase.table("matches").upsert(matches, on_conflict="api_match_id").execute()
    return _check_response(res)


def set_match_result(api_match_id: int, result: str):
    """Enregistre le résultat d'un match (HOME / DRAW / AWAY) et le marque comme terminé."""
    if result not in VALID_PICKS:
        raise ValueError("Result must be one of HOME, DRAW, AWAY")
    res = supabase.table("matches").update({
        "status": "FINISHED",
        "result": result,
    }).eq("api_match_id", api_match_id).execute()
    _check_response(res)


# --- DUELS (SESSIONS) ---


def create_duel_session(creator_id: int, gross_fee: float, match_ids: list, creator_predictions: list = None):
    """Crée une session de duel 1v1 : prélève la mise brute et fige la liste des matchs jouée.

    Note: pour limiter les races, l'opération de débit est conditionnelle sur le solde actuel
    (on ajoute une clause .eq("coins_balance", <old_balance>) pour échouer proprement en cas de concurrence).
    """
    user = get_or_create_user(creator_id, "")
    if user.get("coins_balance", 0) < gross_fee:
        return None, "Solde insuffisant"

    rake = gross_fee * config.RAKE_PERCENTAGE
    net_fee = gross_fee - rake

    new_balance = user["coins_balance"] - gross_fee
    # Tentative d'update conditionnelle pour éviter la concurrence
    upd = supabase.table("users").update({"coins_balance": new_balance}).eq("telegram_id", creator_id).eq("coins_balance", user["coins_balance"]).execute()
    updated_rows = _check_response(upd)
    if not updated_rows:
        return None, "Échec du débit (conflit de concurrence). Réessayez."

    session_data = {
        "creator_id": creator_id,
        "type": "DUEL",
        "gross_entry_fee": gross_fee,
        "net_entry_fee": net_fee,
        "max_players": 2,
        "status": "WAITING",
        "match_ids": match_ids,
        "match_count": len(match_ids) if match_ids is not None else None,
    }
    session = supabase.table("sessions").insert(session_data).execute()
    session_rows = _check_response(session)
    session_row = session_rows[0]

    # Si le créateur a fourni ses prédictions lors de la création du duel, on enregistre son ticket.
    if creator_predictions:
        save_ticket(session_row["id"], creator_id, creator_predictions)

    return session_row, "Succès"


def get_session(session_id: str):
    """Récupère une session par son id."""
    res = supabase.table("sessions").select("*").eq("id", session_id).execute()
    rows = _check_response(res)
    return rows[0] if rows else None


def get_open_duels(exclude_creator_id: int = None):
    """Récupère les duels en attente d'adversaire (optionnellement en excluant ses propres duels)."""
    query = supabase.table("sessions").select("*").eq("status", "WAITING").eq("type", "DUEL")
    if exclude_creator_id is not None:
        query = query.neq("creator_id", exclude_creator_id)
    res = query.execute()
    return _check_response(res)


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
    if joiner.get("coins_balance", 0) < gross_fee:
        return None, f"Solde insuffisant (il vous faut {gross_fee} Coins)."

    new_balance = joiner["coins_balance"] - gross_fee
    # Update conditionnel pour éviter les races
    upd = supabase.table("users").update({"coins_balance": new_balance}).eq("telegram_id", joiner_id).eq("coins_balance", joiner["coins_balance"]).execute()
    updated_rows = _check_response(upd)
    if not updated_rows:
        return None, "Échec du débit (conflit de concurrence). Réessayez."

    # Le .eq("status", "WAITING") protège contre une double-jointure concurrente :
    updated = (
        supabase.table("sessions")
        .update({"opponent_id": joiner_id, "status": "IN_PROGRESS"})
        .eq("id", session_id)
        .eq("status", "WAITING")
        .execute()
    )

    updated_data = _check_response(updated)
    if not updated_data:
        # Remboursement : quelqu'un d'autre a rejoint entre-temps
        supabase.table("users").update({"coins_balance": joiner["coins_balance"]}).eq("telegram_id", joiner_id).execute()
        return None, "Un autre joueur vient de rejoindre ce duel juste avant vous."

    # Valider et enregistrer le ticket du joiner
    save_ticket(session_id, joiner_id, predictions)
    return updated_data[0], "Succès"


# --- TICKETS ---


def validate_predictions(preds, expected_match_ids=None):
    if not isinstance(preds, list):
        return False
    for p in preds:
        if not isinstance(p, dict):
            return False
        if "match_id" not in p or "pick" not in p:
            return False
        try:
            mid = int(p["match_id"])
        except Exception:
            return False
        if expected_match_ids is not None and mid not in expected_match_ids:
            return False
        if p["pick"] not in VALID_PICKS:
            return False
    return True


def save_ticket(session_id: str, user_id: int, predictions: list):
    """Enregistre le ticket de pronostics d'un joueur pour une session donnée."""
    # On valide la structure minimale des prédictions
    session = get_session(session_id)
    expected_ids = None
    if session:
        m_ids = session.get("match_ids") or []
        expected_ids = [int(x) for x in m_ids if str(x).isdigit()]

    if not validate_predictions(predictions, expected_match_ids=expected_ids):
        raise ValueError("Predictions invalides ou ne correspondent pas aux match_ids de la session")

    ticket_data = {
        "session_id": session_id,
        "user_id": user_id,
        "predictions": predictions,
        "status": "PENDING",
    }
    res = supabase.table("tickets").insert(ticket_data).execute()
    rows = _check_response(res)
    return rows[0]


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

    rows = _check_response(res)
    resolvable = []
    target_id = int(api_match_id)

    for session in rows:
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
    if not session or session.get("status") != "IN_PROGRESS":
        return None

    tickets_res = supabase.table("tickets").select("*").eq("session_id", session_id).execute()
    tickets = _check_response(tickets_res)
    if len(tickets) != 2:
        return None  # sécurité : un duel doit avoir exactement 2 tickets pour être résolu

    matches = get_matches_by_ids(session.get("match_ids") or [])
    results_by_match = {int(m["api_match_id"]): m.get("result") for m in matches}

    scores = {}
    for ticket in tickets:
        correct = 0
        for p in ticket.get("predictions", []):
            try:
                mid = int(p.get("match_id"))
            except Exception:
                continue
            if results_by_match.get(mid) == p.get("pick"):
                correct += 1
        scores[ticket["user_id"]] = correct

    creator_id = session.get("creator_id")
    opponent_id = session.get("opponent_id")
    creator_score = scores.get(creator_id, 0)
    opponent_score = scores.get(opponent_id, 0)
    pot = (session.get("net_entry_fee") or 0) * 2

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
