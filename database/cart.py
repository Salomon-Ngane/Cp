from database.connection import supabase

def get_draft_settings(user_id: int) -> dict:
    """Récupère les paramètres actuels du ticket en cours."""
    try:
        res = supabase.table("draft_settings").select("*").eq("user_id", user_id).execute()
        return res.data[0] if res.data else {}
    except Exception:
        return {}

def update_draft_settings(user_id: int, new_data: dict):
    """Fusionne les nouvelles données de création (type, mise, matchs) avec les anciennes."""
    current_settings = get_draft_settings(user_id)
    current_settings.update(new_data)
    current_settings["user_id"] = user_id
    supabase.table("draft_settings").upsert(current_settings).execute()

def clear_draft(user_id: int):
    """Vide intégralement le brouillon après une création ou une annulation."""
    supabase.table("draft_settings").delete().eq("user_id", user_id).execute()
    supabase.table("cart").delete().eq("user_id", user_id).execute()

def get_cart(user_id: int) -> list:
    try:
        return supabase.table("cart").select("*").eq("user_id", user_id).execute().data or []
    except Exception:
        return []

def toggle_cart_item(user_id: int, match_id: str, pick: str, odds: float, max_count: int = None) -> bool:
    """
    Ajoute, modifie ou retire un pronostic. 
    Retourne True si l'action a réussi, False si le panier est déjà plein.
    """
    existing = supabase.table("cart").select("*").eq("user_id", user_id).eq("match_id", str(match_id)).execute()
    
    if existing.data:
        if existing.data[0]["pick"] == pick:
            # Même choix cliqué = on décoche (retrait du panier)
            supabase.table("cart").delete().eq("user_id", user_id).eq("match_id", str(match_id)).execute()
        else:
            # Choix différent sur le même match = on met à jour le pronostic
            supabase.table("cart").update({"pick": pick, "odds": float(odds)}).eq("user_id", user_id).eq("match_id", str(match_id)).execute()
        return True
    else:
        # Nouveau match. Vérification de la limite du panier !
        if max_count is not None:
            current_cart = get_cart(user_id)
            if len(current_cart) >= max_count:
                return False # Rejet : Panier plein
                
        supabase.table("cart").insert({
            "user_id": user_id, 
            "match_id": str(match_id), 
            "pick": pick, 
            "odds": float(odds)
        }).execute()
        return True
