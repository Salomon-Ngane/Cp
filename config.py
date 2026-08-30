import os
from dotenv import load_dotenv

load_dotenv()

# Tokens & Clés
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY")  # obsolète, remplacé par ODDS_API_KEY
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

# Paramètres de l'Économie
RAKE_PERCENTAGE = 0.10  # 10% de frais de participation
MIN_WITHDRAWAL_COINS = 10000  # Seuil de retrait minimum
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))