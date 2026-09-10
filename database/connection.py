from supabase import create_client, Client
import config

# Instance unique exportée pour tout le projet
supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)

