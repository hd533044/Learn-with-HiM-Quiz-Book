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
    "❤️ **Welcome to Learn with HiM Quiz Book** ❤️\n"
    "**The Best-in-Class Quiz Book by Himanshu Sir**\n\n"
    "Sharpen your skills with high-quality, exam-focused quizzes designed to help you learn faster, practice smarter, and score higher.\n\n"
    "📚 **Curated Questions** • ⚡ **Instant Results** • 📈 **Performance Tracking** • 🏆 **Daily Improvement**\n\n"
    "**Practice. Learn. Achieve.**\n\n"
    "Welcome to the Learn with HiM family. Wishing you success in every exam! ❤️"
)