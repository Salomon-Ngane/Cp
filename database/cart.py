from database.connection import supabase

def set_draft_settings(user_id: int, settings: dict):
    """Écrase les paramètres actuels par les nouveaux (idéal pour initialiser)."""
    settings["user_id"] = user_id
    supabase.table("draft_settings").upsert(settings).execute()

def get_draft_settings(user_id: int) -> dict:
    """Récupère les paramètres actuels du ticket en cours."""
    res = supabase.table("draft_settings").select("*").eq("user_id", user_id).execute()
    return res.data[0] if res.data else {}

def update_draft_settings(user_id: int, new_data: dict):
    """
    CORRECTIF ARENA : Fusionne les nouvelles données avec les anciennes 
    sans effacer les étapes précédentes (ex: nombre de joueurs + mode de prix).
    """
    current_settings = get_draft_settings(user_id)
    current_settings.update(new_data) # On fusionne
    current_settings["user_id"] = user_id
    supabase.table("draft_settings").upsert(current_settings).execute()

def clear_draft(user_id: int):
    """Vide intégralement le panier et les paramètres du ticket."""
    supabase.table("draft_settings").delete().eq("user_id", user_id).execute()
    supabase.table("cart").delete().eq("user_id", user_id).execute()

def toggle_cart_item(user_id: int, match_id: str, pick: str, odds: float):
    """Ajoute, modifie ou retire un pronostic du panier."""
    existing = supabase.table("cart").select("*").eq("user_id", user_id).eq("match_id", str(match_id)).execute()
    
    if existing.data:
        if existing.data[0]["pick"] == pick:
            # Même choix = on décoche
            supabase.table("cart").delete().eq("user_id", user_id).eq("match_id", str(match_id)).execute()
        else:
            # Choix différent = on met à jour
            supabase.table("cart").update({"pick": pick, "odds": float(odds)}).eq("user_id", user_id).eq("match_id", str(match_id)).execute()
    else:
        # Nouveau match au panier
        supabase.table("cart").insert({
            "user_id": user_id, 
            "match_id": str(match_id), 
            "pick": pick, 
            "odds": float(odds)
        }).execute()

def get_cart(user_id: int):
    return supabase.table("cart").select("*").eq("user_id", user_id).execute().data

