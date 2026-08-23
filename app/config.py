import os
import logging
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
USER_PROFILES_DIR = os.path.join(DATA_DIR, "user_profiles")
QUESTION_BANK_DIR = os.path.join(DATA_DIR, "question_bank")
TOPICS_DIR = os.path.join(DATA_DIR, "topics")
SHORTCUT_KEYS_DIR = os.path.join(QUESTION_BANK_DIR, "shortcut_keys")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(USER_PROFILES_DIR, exist_ok=True)
os.makedirs(QUESTION_BANK_DIR, exist_ok=True)
os.makedirs(TOPICS_DIR, exist_ok=True)
os.makedirs(SHORTCUT_KEYS_DIR, exist_ok=True)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    logging.warning("⚠️ BOT_TOKEN is empty! Please set it in your .env file or environment variables.")

# Groq API Configuration (Fast LPU Inference)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()

CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@LEARNWITHHIM")
YOUTUBE_CHANNEL_URL = os.getenv("YOUTUBE_CHANNEL_URL", "https://www.youtube.com/@learnwithhim")

DAILY_QUESTION_LIMIT = int(os.getenv("DAILY_QUESTION_LIMIT", "20"))
DB_FILE = os.getenv("DB_FILE", os.path.join(DATA_DIR, "quiz_bot.db"))

PRIMARY_ADMIN_ID = 1091057353
ADMIN_IDS = [1091057353, 2070531704]

# ---------------------------------------------------------
# Razorpay Credentials Configuration
# ---------------------------------------------------------
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "rzp_test_TMB1bZp7hh2k2R").strip()
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "MPgns1qhndStgrXNwWEGfkjq").strip()
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "learnwithhim_secret_123").strip()

RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "https://learnwithhimquiz.onrender.com").strip()

# Exact Subscription Plan Matrix
PLAN_TIERS = {
    "FREE_DEMO": {"name": "🎁 FREE DEMO TRIAL", "price": 0, "days": 2, "daily_limit": 20},
    "BRONZE": {"name": "📦 BRONZE PACK", "price": 5, "days": 3, "daily_limit": 80},
    "SILVER": {"name": "📦 SILVER PACK", "price": 10, "days": 7, "daily_limit": 100},
    "GOLD": {"name": "📦 GOLD PACK", "price": 15, "days": 12, "daily_limit": 120},
    "DIAMOND": {"name": "📦 DIAMOND PACK", "price": 20, "days": 18, "daily_limit": 150},
    "LEARNWITHHIM": {"name": "📦 LEARNWITHHIM PACK", "price": 25, "days": 30, "daily_limit": 250},
    "PLATINUM": {"name": "📦 PLATINUM PACK", "price": 40, "days": 60, "daily_limit": 300},
    "RUBY": {"name": "📦 RUBY PACK", "price": 50, "days": 90, "daily_limit": 400},
    "MEGA": {"name": "📦 MEGA PACK", "price": 80, "days": 180, "daily_limit": 500},
}

WELCOME_CARD_TEXT = (
    "💖 **WELCOME TO QUIZ WITH HIM** 💖\n"
    "👑 **THE BEST-IN-CLASS QUIZ BOOK BY HIMANSHU SIR** 👑\n\n"
    "🎯 Sharpen your skills with high-quality, exam-focused quizzes designed to help you learn faster, practice smarter, and achieve your goals.\n"
    "✨ **Curated Questions** • ⚡ **Instant Results** • 📈 **Performance Tracking** • 🏆 **Daily Improvement**\n\n"
    "🎯 **PRACTICE. LEARN. ACHIEVE.** 🎯\n\n"
    "✨ Welcome to the Learn with HiM family. Wishing you ultimate success in every exam! 💖"
)