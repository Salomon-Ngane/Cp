import string
import random
import logging
from datetime import datetime, timezone, timedelta
from database.connection import supabase
from services.user_service import get_user_by_id, update_user_balance

logger = logging.getLogger(__name__)

def generate_short_code(length=7) -> str:
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choices(chars, k=length))

def get_session(session_id: str):
    res = supabase.table("sessions").select("*").eq("id", session_id).execute().data
    if res: return res[0]
    res_code = supabase.table("sessions").select("*").eq("session_code", str(session_id).upper()).execute().data
    return res_code[0] if res_code else None

def create_session(creator_id: int, session_type: str, gross_fee: int, match_count: int, max_participants: int = 2, prize_mode: str = "WINNER_TAKES_ALL", predictions: list = None):
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
    if not session_res: return None, "Erreur création."

    session = session_res[0]
    ticket_data = {
        "session_id": session["id"],
        "user_id": creator_id,
        "predictions": predictions or [],
        "status": "WAITING"
    }
    supabase.table("tickets").insert(ticket_data).execute()
    update_user_balance(creator_id, -gross_fee)

    return session, "Session créée."

def join_session(session_id: str, user_id: int, predictions: list):
    session = get_session(session_id)
    if not session or session["status"] != "WAITING":
        return None, "Salon indisponible."

    user = get_user_by_id(user_id)
    fee = session["gross_entry_fee"]
    if not user or user.get("coins_balance", 0) < fee:
        return None, "Solde insuffisant."

    existing_tickets = supabase.table("tickets").select("*").eq("session_id", session["id"]).execute().data or []
    if len(existing_tickets) >= session["max_participants"]:
        return None, "Salon complet."

    update_user_balance(user_id, -fee)

    ticket_data = {
        "session_id": session["id"],
        "user_id": user_id,
        "predictions": predictions,
        "status": "WAITING"
    }
    supabase.table("tickets").insert(ticket_data).execute()

    if len(existing_tickets) + 1 >= session["max_participants"]:
        supabase.table("sessions").update({"status": "IN_PROGRESS"}).eq("id", session["id"]).execute()

    return session, "Vous avez rejoint le salon."

def get_tickets_for_session(session_id: str):
    return supabase.table("tickets").select("*").eq("session_id", session_id).execute().data or []

def get_user_sessions(user_id: int, history_limit: int = 50):
    user_tickets = supabase.table("tickets").select("session_id").eq("user_id", user_id).execute().data or []
    if not user_tickets: return []
    session_ids = [t["session_id"] for t in user_tickets]
    query = supabase.table("sessions").select("*").in_("id", session_ids).order("created_at", desc=True)
    if history_limit > 0: query = query.limit(history_limit)
    return query.execute().data or []

def cancel_expired_sessions():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    expired = supabase.table("sessions").select("*").eq("status", "WAITING").lt("created_at", cutoff.isoformat()).execute().data or []
    for s in expired:
        tickets = get_tickets_for_session(s["id"])
        for t in tickets:
            update_user_balance(t["user_id"], s["gross_entry_fee"])
        supabase.table("sessions").update({"status": "CANCELLED"}).eq("id", s["id"]).execute()

# --- RÉSOLUTIONS AUTOMATIQUES DES MATCHS ET BONS ---

def set_match_result(match_id: str, winning_outcome: str):
    """Enregistre le résultat final d'un match et déclenche la mise à jour des pronostics."""
    supabase.table("matches").update({"winner": winning_outcome, "status": "FINISHED"}).eq("id", match_id).execute()
    evaluate_pending_tickets()

def evaluate_pending_tickets():
    """Vérifie tous les tickets en cours et les clôture automatiquement si tous leurs matchs sont finis."""
    sessions_in_progress = supabase.table("sessions").select("*").eq("status", "IN_PROGRESS").execute().data or []
    
    for s in sessions_in_progress:
        tickets = get_tickets_for_session(s["id"])
        all_resolved = True
        scores = {}

        for t in tickets:
            preds = t.get("predictions", [])
            correct_count = 0
            ticket_finished = True

            for p in preds:
                m_id = p.get("match_id")
                match = supabase.table("matches").select("*").eq("id", m_id).execute().data
                if not match or match[0].get("status") != "FINISHED":
                    ticket_finished = False
                    break
                if match[0].get("winner") == p.get("choice"):
                    correct_count += 1

            if not ticket_finished:
                all_resolved = False
                break
            
            scores[t["id"]] = {"user_id": t["user_id"], "correct": correct_count}

        if all_resolved and len(scores) == len(tickets):
            # Tous les pronostics du salon sont vérifiés
            sorted_tickets = sorted(scores.values(), key=lambda x: x["correct"], reverse=True)
            pot = s["gross_entry_fee"] * len(tickets)
            net_pot = int(pot * 0.92)

            if len(sorted_tickets) >= 2 and sorted_tickets[0]["correct"] == sorted_tickets[1]["correct"]:
                # Égalité : Remboursement des participants
                for t in tickets:
                    update_user_balance(t["user_id"], s["gross_entry_fee"])
                    supabase.table("tickets").update({"status": "CANCELLED"}).eq("id", t["id"]).execute()
                supabase.table("sessions").update({"status": "CANCELLED"}).eq("id", s["id"]).execute()
            else:
                # Vainqueur unique
                winner_id = sorted_tickets[0]["user_id"]
                update_user_balance(winner_id, net_pot)
                for t in tickets:
                    st = "WON" if t["user_id"] == winner_id else "LOST"
                    supabase.table("tickets").update({"status": st}).eq("id", t["id"]).execute()
                supabase.table("sessions").update({"status": "COMPLETED"}).eq("id", s["id"]).execute()

# --- CLASSEMENT DYNAMIQUE ET DISSÉMINATION DES RÉCOMPENSES TOP ---

def get_timeframe_details(period: str):
    now = datetime.now(timezone.utc)
    if period == "day": return now - timedelta(days=1), 500
    elif period == "week": return now - timedelta(days=7), 2000
    elif period == "month": return now - timedelta(days=30), 5000
    return now - timedelta(days=1), 500

def calculate_leaderboards(category: str, period: str):
    cutoff, min_volume = get_timeframe_details(period)
    
    recent_tickets = supabase.table("tickets").select("user_id, status, session_id").gte("created_at", cutoff.isoformat()).execute().data or []
    recent_sessions = supabase.table("sessions").select("id, gross_entry_fee").gte("created_at", cutoff.isoformat()).execute().data or []
    session_fee_map = {s["id"]: s["gross_entry_fee"] for s in recent_sessions}
    
    user_stats = {}
    for t in recent_tickets:
        uid = t["user_id"]
        if uid not in user_stats:
            user_stats[uid] = {"volume": 0, "wins": 0, "completed": 0, "network": 0}
        
        user_stats[uid]["volume"] += session_fee_map.get(t["session_id"], 0)
        
        if t["status"] in ("WON", "LOST"):
            user_stats[uid]["completed"] += 1
            if t["status"] == "WON":
                user_stats[uid]["wins"] += 1

    recent_users = supabase.table("users").select("referrer_id").gte("created_at", cutoff.isoformat()).execute().data or []
    for u in recent_users:
        ref = u.get("referrer_id")
        if ref:
            if ref not in user_stats:
                user_stats[ref] = {"volume": 0, "wins": 0, "completed": 0, "network": 0}
            user_stats[ref]["network"] += 1

    all_uids = list(user_stats.keys())
    users_data = supabase.table("users").select("telegram_id, username, user_code").in_("telegram_id", all_uids).execute().data or [] if all_uids else []
    user_map = {u["telegram_id"]: u for u in users_data}
    
    board = []
    for uid, stats in user_stats.items():
        if uid == 0: continue
        u = user_map.get(uid)
        if not u: continue
        
        vol = stats["volume"]
        winrate = (stats["wins"] / stats["completed"] * 100) if stats["completed"] > 0 else 0
        
        board.append({
            "telegram_id": uid,
            "username": u.get("username", f"User_{uid}"),
            "user_code": u.get("user_code", "N/A"),
            "volume": vol,
            "winrate": round(winrate, 2),
            "network": stats["network"],
            "qualified": vol >= min_volume
        })

    if category == "winrate":
        board.sort(key=lambda x: (x["winrate"], x["volume"]), reverse=True)
    elif category == "network":
        board.sort(key=lambda x: (x["network"], x["volume"]), reverse=True)
    elif category == "volume":
        board.sort(key=lambda x: x["volume"], reverse=True)
        
    return board, min_volume

def safe_add_item(telegram_id: int, item_id: int):
    u = get_user_by_id(telegram_id)
    if not u: return
    key = f"item_{item_id}_count"
    new_qty = min(u.get(key, 0) + 1, 3)
    supabase.table("users").update({key: new_qty}).eq("telegram_id", telegram_id).execute()

def distribute_top_rewards(category: str, period: str, total_prize_pool: int, bonus_item: int = None):
    board, _ = calculate_leaderboards(category, period)
    qualified_players = [p for p in board if p["qualified"]]
    
    if not qualified_players:
        return False, "❌ Aucun joueur n'a atteint le volume de jeu minimum requis pour cette période."

    top_10 = qualified_players[:10]
    shares = [0.40, 0.25, 0.18, 0.10, 0.07]
    summary = []

    for idx, player in enumerate(top_10):
        uid = player["telegram_id"]
        uname = player["username"]
        rewards_text = []

        if idx < 5:
            coins = int(total_prize_pool * shares[idx])
            update_user_balance(uid, coins)
            rewards_text.append(f"{coins} Coins")
        
        if 5 <= idx < 10:
            safe_add_item(uid, 1)
            rewards_text.append("1x Item 1")

        if bonus_item in [1, 2, 3]:
            safe_add_item(uid, bonus_item)
            rewards_text.append(f"1x Item {bonus_item}")

        medal = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"][idx] if idx < 5 else f"#{idx+1}"
        summary.append(f"{medal} **{uname}** : " + " + ".join(rewards_text))

    cat_code = category[:3].upper()
    per_code = period[:2].upper()
    payout_id = f"TOP-{cat_code}-{per_code}-{generate_short_code(4)}"

    final_text = f"🔖 **ID de Distribution :** `{payout_id}`\n\n" + "\n".join(summary)
    return True, final_text
