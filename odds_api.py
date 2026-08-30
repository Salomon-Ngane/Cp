"""
Intégration TheOddsAPI : synchronise les matchs du jour (Football / Basket / Tennis)
avec leurs cotes 1N2 (h2h) dans notre table `matches`.

Non testé contre l'API réelle depuis cet environnement (pas d'accès réseau sortant
vers api.the-odds-api.com dans ce sandbox) — à valider avec une vraie clé avant mise en prod.
"""
import requests
from datetime import datetime, timezone

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ALLOWED_GROUPS = {"Soccer", "Basketball", "Tennis"}


def _fetch_active_sport_keys(api_key: str) -> list:
    """Découvre dynamiquement les sport_key actifs dans les groupes autorisés.
    Nécessaire car les clés de tournois (tennis notamment) changent en permanence."""
    resp = requests.get(f"{ODDS_API_BASE}/sports", params={"apiKey": api_key}, timeout=15)
    resp.raise_for_status()
    sports = resp.json()
    return [s["key"] for s in sports if s.get("active") and s.get("group") in ALLOWED_GROUPS]


def _is_today(commence_time_str: str) -> bool:
    """Règle du jour même (J+0), comparaison en UTC."""
    commence = datetime.fromisoformat(commence_time_str.replace("Z", "+00:00"))
    return commence.date() == datetime.now(timezone.utc).date()


def _extract_odds(event: dict) -> dict:
    """Prend le premier bookmaker disposant d'un marché h2h (1N2 / vainqueur)."""
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


def sync_today_matches(api_key: str):
    """
    Interroge TheOddsAPI pour tous les sports actifs des groupes autorisés,
    ne garde que les rencontres du jour même (UTC), et renvoie une liste
    prête pour database.upsert_matches(), ainsi que le quota restant estimé.
    """
    normalized = []
    quota_remaining = None
    sport_keys = _fetch_active_sport_keys(api_key)

    for sport_key in sport_keys:
        resp = requests.get(
            f"{ODDS_API_BASE}/sports/{sport_key}/odds",
            params={"apiKey": api_key, "regions": "eu", "markets": "h2h", "oddsFormat": "decimal"},
            timeout=15,
        )
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

    return normalized, quota_remaining