import os
import logging
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
USER_PROFILES_DIR = os.path.join(DATA_DIR, "user_profiles")
QUESTION_BANK_DIR = os.path.join(DATA_DIR, "question_bank")

# Guarantee necessary directory structures exist
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(USER_PROFILES_DIR, exist_ok=True)
os.makedirs(QUESTION_BANK_DIR, exist_ok=True)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    logging.warning("⚠️ BOT_TOKEN is empty! Please set it in your .env file or environment variables.")

CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@LEARNWITHHIM")
YOUTUBE_CHANNEL_URL = os.getenv("YOUTUBE_CHANNEL_URL", "https://www.youtube.com/@learnwithhim")

DAILY_QUESTION_LIMIT = int(os.getenv("DAILY_QUESTION_LIMIT", "40"))
DB_FILE = os.getenv("DB_FILE", os.path.join(DATA_DIR, "quiz_bot.db"))

# Primary Admin ID (Himanshu Sir)
PRIMARY_ADMIN_ID = 1091057353
ADMIN_IDS = [1091057353, 2070531704]

WELCOME_CARD_TEXT = (
    "❤️ **𝒲𝑒𝓁𝒸𝑜𝓂𝑒 𝓉𝑜 𝐿𝑒𝒶𝓇𝓃 𝓌𝒾𝓉𝒽 𝐻𝒾𝑀 𝒬𝓊𝒾𝓏 𝐵𝑜𝑜𝓀** ❤️\n"
    "**𝒯𝒽𝑒 𝐵𝑒𝓈𝓉-𝒾𝓃-𝒞𝓁𝒶𝓈𝓈 𝒬𝓊𝒾𝓏 𝐵𝑜𝑜𝓀 𝒷𝓎 𝐻𝒾𝓂𝒶𝓃𝓈𝒽𝓊 𝒮𝒾𝓇**\n\n"
    "𝒮𝒽𝒶𝓇𝓅𝑒𝓃 𝓎𝑜𝓊𝓇 𝓈𝓀𝒾𝓁𝓁𝓈 𝓌𝒾𝓉𝒽 𝒽𝒾𝑔𝒽-𝓆𝓊𝒶𝓁𝒾𝓉𝓎, 𝑒𝓍𝒶𝓂-𝒻𝑜𝒸𝓊𝓈𝑒𝒹 𝓆𝓊𝒾𝓏𝓏𝑒𝓈 𝒹𝑒𝓈𝒾𝑔𝓃𝑒𝒹 𝓉𝑜 𝒽𝑒𝓁𝓅 𝓎𝑜𝓊 𝓁𝑒𝒶𝓇𝓃 𝒻𝒶𝓈𝓉𝑒𝓇, 𝓅𝓇𝒶𝒸𝓉𝒾𝒸𝑒 𝓈𝓂𝒶𝓇𝓉𝑒𝓇, 𝒶𝓃𝒹 𝓈𝒸𝑜𝓇𝑒 𝒽𝒾𝑔𝒽𝑒𝓇.\n\n"
    "📚 **𝒞𝓊𝓇𝒶𝓉𝑒𝒹 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈** • ⚡ **𝐼𝓃𝓈𝓉𝒶𝓃𝓉 𝑅𝑒𝓈𝓊𝓁𝓉𝓈** • 📈 **𝒫𝑒𝓇𝒻𝑜𝓇𝓂𝒶𝓃𝒸𝑒 𝒯𝓇𝒶𝒸𝓀𝒾𝓃𝑔** • 🏆 **𝒟𝒶𝒾𝓁𝓎 𝐼𝓂𝓅𝓇𝑜𝓋𝑒𝓂𝑒𝓃𝓉**\n\n"
    "**𝒫𝓇𝒶𝒸𝓉𝒾𝒸𝑒. 𝐿𝑒𝒶𝓇𝓃. 𝒜𝒸𝒽𝒾𝑒𝓋𝑒.**\n\n"
    "𝒲𝑒𝓁𝒸𝑜𝓂𝑒 𝓉𝑜 𝓉𝒽𝑒 𝐿𝑒𝒶𝓇𝓃 𝓌𝒾𝓉𝒽 𝐻𝒾𝑀 𝒻𝒶𝓂𝒾𝓁𝓎. 𝒲𝒾𝓈𝒽𝒾𝓃𝑔 𝓎𝑜𝓊 𝓈𝓊𝒸𝒸𝑒𝓈𝓈 𝒾𝓃 𝑒𝓋𝑒𝓇𝓎 𝑒𝓍𝒶𝓂! ❤️"
)