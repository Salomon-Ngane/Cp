import os
from dotenv import load_dotenv

load_dotenv()

# Tokens & Clés
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
CELEBRATION_STICKER_ID = os.getenv("CELEBRATION_STICKER_ID")

# Paramètres de l'Économie
RAKE_1V1 = 0.10      # 10% de frais pour le 1v1
RAKE_ARENA = 0.075   # 7.5% de frais pour le mode Arena
DON_SHARE_FROM_RAKE = 0.026 # 2.6% du rake reversé au fond Don (❤️)
MIN_WITHDRAWAL_COINS = 10000 
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))

# Expiration des sessions en attente (heures)
SESSION_EXPIRATION_HOURS = 24
