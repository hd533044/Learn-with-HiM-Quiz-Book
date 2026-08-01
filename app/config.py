import os
import logging
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
USER_PROFILES_DIR = os.path.join(DATA_DIR, "user_profiles")
QUESTION_BANK_DIR = os.path.join(DATA_DIR, "question_bank")

# Ensure required directory structures exist on Render/Linux
os.makedirs(DATA_DIR, mode=0o777, exist_ok=True)
os.makedirs(USER_PROFILES_DIR, mode=0o777, exist_ok=True)
os.makedirs(QUESTION_BANK_DIR, mode=0o777, exist_ok=True)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    logging.warning("⚠️ BOT_TOKEN is empty! Please set it in environment variables or .env file.")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@LearnwithHiMQuiz").strip()
YOUTUBE_CHANNEL_URL = os.getenv("YOUTUBE_CHANNEL_URL", "https://www.youtube.com/@learnwithhim").strip()

DAILY_QUESTION_LIMIT = int(os.getenv("DAILY_QUESTION_LIMIT", "40"))
DB_FILE = os.getenv("DB_FILE", os.path.join(DATA_DIR, "quiz_bot.db"))

PRIMARY_ADMIN_ID = int(os.getenv("PRIMARY_ADMIN_ID", "1091057353"))
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "1091057353"))

WELCOME_CARD_TEXT = (
    "❤️ **Welcome to Learn with HiM Quiz Book** ❤️\n"
    "**The Best-in-Class Quiz Book by Himanshu Sir**\n\n"
    "Sharpen your skills with high-quality, exam-focused quizzes designed to help you learn faster, practice smarter, and score higher.\n\n"
    "📚 **Curated Questions** • ⚡ **Instant Results** • 🔒 **Secure Account Access** • 🏆 **Daily Improvement**\n\n"
    "**Practice. Learn. Achieve.**"
)