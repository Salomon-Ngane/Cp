"""
Intégration TheOddsAPI (Asynchrone) : synchronise les matchs du jour et
récupère les scores en direct, à budget de quota strict et prévisible.

Coût observé chez TheOddsAPI : 1 crédit par appel réussi à /odds ou /scores
avec regions=eu&markets=h2h (peu importe le nombre de matchs renvoyés dans la
réponse — le coût est par APPEL, pas par match). L'endpoint /sports (liste)
est gratuit. Hypothèse basée sur la documentation, à confirmer via les
en-têtes x-requests-remaining au premier run réel.
"""
import httpx
from datetime import datetime, timezone

ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Championnats fixes, les plus suivis, dans l'ordre de priorité.
# = le coût maximal garanti d'une synchro (hors tennis dynamique ci-dessous).
PRIORITY_SOCCER = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_uefa_champs_league",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga",
    "soccer_france_ligue_one",
]
PRIORITY_BASKETBALL = [
    "basketball_nba",
]

MAX_TENNIS_SLOTS = 2   # tournois découverts dynamiquement (clés changeantes)
MAX_TOTAL_CALLS = 10   # plafond dur, quoi qu'il arrive


def _is_today(commence_time_str: str) -> bool:
    commence = datetime.fromisoformat(commence_time_str.replace("Z", "+00:00"))
    return commence.date() == datetime.now(timezone.utc).date()


def _extract_odds(event: dict) -> dict:
    for bookmaker in event.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            if market.get("key") == "h2h":
                odds = {"home": None, "draw": None, "away": None}
                for outcome in market.get("outcomes", []):
                    name, price = outcome.get("name"), outcome.get("price")
                    if name == event.get("home_team"):
                        odds["home"] = price
                    elif name == event.get("away_team"):
                        odds["away"] = price
                    elif name and name.lower() == "draw":
                        odds["draw"] = price
                return odds
    return {"home": None, "draw": None, "away": None}


async def _discover_top_tennis_keys(client: httpx.AsyncClient, api_key: str, limit: int) -> list:
    """Appel GRATUIT (liste des sports, hors quota) : tournois ATP/WTA actuellement
    actifs — les clés tennis tournent en permanence, impossible de les figer."""
    resp = await client.get(f"{ODDS_API_BASE}/sports", params={"apiKey": api_key}, timeout=15)
    resp.raise_for_status()
    sports = resp.json()
    tennis = [
        s["key"] for s in sports
        if s.get("active") and s.get("group") == "Tennis"
        and ("atp" in s["key"] or "wta" in s["key"])
    ]
    return tennis[:limit]


async def sync_today_matches(api_key: str):
    """
    Synchro à budget fixe : 6 championnats de foot majeurs + NBA (liste figée,
    7 appels) + jusqu'à 2 tournois de tennis ATP/WTA détectés dynamiquement.
    Coût maximal garanti : MAX_TOTAL_CALLS crédits.
    Renvoie (matchs_normalisés, quota_restant, nb_appels_effectués).
    """
    normalized = []
    calls_used = 0
    quota_remaining = None

    async with httpx.AsyncClient() as client:
        tennis_keys = await _discover_top_tennis_keys(client, api_key, MAX_TENNIS_SLOTS)
        sport_keys = (PRIORITY_SOCCER + PRIORITY_BASKETBALL + tennis_keys)[:MAX_TOTAL_CALLS]

        for sport_key in sport_keys:
            resp = await client.get(
                f"{ODDS_API_BASE}/sports/{sport_key}/odds",
                params={"apiKey": api_key, "regions": "eu", "markets": "h2h", "oddsFormat": "decimal"},
                timeout=15,
            )
            calls_used += 1
            quota_remaining = resp.headers.get("x-requests-remaining", quota_remaining)
            if resp.status_code != 200:
                continue

            for event in resp.json():
                if not event.get("commence_time") or not _is_today(event["commence_time"]):
                    continue
                odds = _extract_odds(event)
                normalized.append({
                    "api_match_id": str(event["id"]),
                    "sport": sport_key,
                    "home_team": event["home_team"],
                    "away_team": event["away_team"],
                    "commence_time": event["commence_time"],
                    "odds_home": odds["home"],
                    "odds_draw": odds["draw"],
                    "odds_away": odds["away"],
                    "status": "NS",
                })

    return normalized, quota_remaining, calls_used


async def fetch_live_scores(client: httpx.AsyncClient, api_key: str, sport_key: str) -> list:
    """1 appel = 1 crédit, couvre TOUS les matchs du jour de ce sport (comme /odds)."""
    resp = await client.get(
        f"{ODDS_API_BASE}/sports/{sport_key}/scores",
        params={"apiKey": api_key, "daysFrom": 1},
        timeout=15,
    )
    if resp.status_code != 200:
        return []

    results = []
    for event in resp.json():
        scores = event.get("scores")
        home_score = away_score = None
        if scores:
            for s in scores:
                if s.get("name") == event.get("home_team"):
                    home_score = s.get("score")
                elif s.get("name") == event.get("away_team"):
                    away_score = s.get("score")

        if event.get("completed"):
            status = "final"
        elif scores:
            status = "live"
        else:
            status = "not_started"

        results.append({
            "api_match_id": str(event["id"]),
            "home_score": home_score,
            "away_score": away_score,
            "status": status,
        })

    return results
