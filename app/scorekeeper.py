import os
import json
import math
import asyncio
import logging
import traceback
import unicodedata
import xml.sax.saxutils as saxutils
from datetime import datetime, timedelta
import pytz
from psycopg2.extras import RealDictCursor

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ContextTypes, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters
)

from app.config import BASE_DIR, USER_PROFILES_DIR
from app.database import (
    get_db, release_db, get_user_profile, get_ist_date_str,
    get_ist_timestamp_str, clean_decimals, sync_user_json_profile
)

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

HAS_WEASYPRINT = False
try:
    import weasyprint
    HAS_WEASYPRINT = True
except ImportError:
    HAS_WEASYPRINT = False

# Conversation States
(
    SK_SELECT_EXAM,
    SK_SELECT_TEST_TYPE,
    SK_SELECT_SOURCE,
    SK_CUSTOM_SOURCE,
    SK_SELECT_DIFF,
    SK_SELECT_DATE,
    SK_CUSTOM_DATE,
    SK_INPUT_MARKS,
    SK_INPUT_WRONG,
    SK_EDIT_TARGET_POST
) = range(200, 210)

SCORES_PER_PAGE = 6
CATEGORIES = ["UR", "OBC", "EWS", "SC", "ST", "ESM", "PwD"]
MOCK_SOURCES = ["Testbook", "Oliveboard", "RBE", "Pinnacle", "Official PYP", "Other"]
DIFFICULTIES = ["Easy", "Moderate", "Hard"]

# ==============================================================================
# 🎯 OFFICIAL MAINS SCHEMES & MULTI-EXAM TEMPLATES
# ==============================================================================
# Prescribed Syllabus & Marking Pattern:
# SSC CGL Mains (Tier-II):
#  - Section I: Module-I (Maths 30Q = 90M), Module-II (Reasoning 30Q = 90M) -> Total: 60Q = 180M
#  - Section II: Module-I (English 45Q = 135M), Module-II (GA 25Q = 75M) -> Total: 70Q = 210M
#  - Full Merit: 390 Marks (130 Questions). Negative Marking: -1 mark per wrong answer (+3 / -1).
#  - Section III Module-I: Computer Knowledge (20Q = 60M, Qualifying). Negative: -1 mark.
#
# SSC CHSL Mains (Tier-II):
#  - Section I: Module-I (Maths 30Q = 90M), Module-II (Reasoning 30Q = 90M) -> Total: 60Q = 180M
#  - Section II: Module-I (English 40Q = 120M), Module-II (GA 20Q = 60M) -> Total: 60Q = 180M
#  - Full Merit: 360 Marks (120 Questions). Negative Marking: -1 mark per wrong answer (+3 / -1).
#  - Section III Module-I: Computer Knowledge (15Q = 45M, Qualifying). Negative: -1 mark.
# ==============================================================================

MAINS_SUBTEST_DEFINITIONS = {
    "SSC_CGL_MAINS": {
        "FULL": {
            "name": "Full Mock (Sec I + Sec II + Computer)",
            "short": "Full Mock",
            "max_marks": 390.0,
            "total_qs": 130,
            "neg_mark": 1.0,
            "sections": [
                ("math", "Maths (30Q)", 90.0),
                ("reas", "Reasoning (30Q)", 90.0),
                ("eng", "English (45Q)", 135.0),
                ("ga", "GA (25Q)", 75.0)
            ],
            "qualifying": ("comp", "Computer Knowledge (20Q)", 60.0)
        },
        "SEC_1": {
            "name": "Section-I (Maths 30Q + Reasoning 30Q)",
            "short": "Section-I",
            "max_marks": 180.0,
            "total_qs": 60,
            "neg_mark": 1.0,
            "sections": [
                ("math", "Maths (30Q)", 90.0),
                ("reas", "Reasoning (30Q)", 90.0)
            ],
            "qualifying": None
        },
        "SEC_2": {
            "name": "Section-II (English 45Q + GA 25Q)",
            "short": "Section-II",
            "max_marks": 210.0,
            "total_qs": 70,
            "neg_mark": 1.0,
            "sections": [
                ("eng", "English (45Q)", 135.0),
                ("ga", "GA (25Q)", 75.0)
            ],
            "qualifying": None
        },
        "MOD_MATH": {
            "name": "Module-I: Mathematical Abilities (30Q)",
            "short": "Maths Module",
            "max_marks": 90.0,
            "total_qs": 30,
            "neg_mark": 1.0,
            "sections": [("math", "Maths (30Q)", 90.0)],
            "qualifying": None
        },
        "MOD_REAS": {
            "name": "Module-II: Reasoning & General Intelligence (30Q)",
            "short": "Reasoning Module",
            "max_marks": 90.0,
            "total_qs": 30,
            "neg_mark": 1.0,
            "sections": [("reas", "Reasoning (30Q)", 90.0)],
            "qualifying": None
        },
        "MOD_ENG": {
            "name": "Module-III: English Language & Comprehension (45Q)",
            "short": "English Module",
            "max_marks": 135.0,
            "total_qs": 45,
            "neg_mark": 1.0,
            "sections": [("eng", "English (45Q)", 135.0)],
            "qualifying": None
        },
        "MOD_GA": {
            "name": "Module-IV: General Awareness (25Q)",
            "short": "GA Module",
            "max_marks": 75.0,
            "total_qs": 25,
            "neg_mark": 1.0,
            "sections": [("ga", "GA (25Q)", 75.0)],
            "qualifying": None
        },
        "MOD_COMP": {
            "name": "Module-V: Computer Knowledge Test (20Q)",
            "short": "Computer Module",
            "max_marks": 60.0,
            "total_qs": 20,
            "neg_mark": 1.0,
            "sections": [("comp", "Computer Knowledge (20Q)", 60.0)],
            "qualifying": ("comp", "Computer Knowledge (20Q)", 60.0)
        }
    },
    "SSC_CHSL_MAINS": {
        "FULL": {
            "name": "Full Mock (Sec I + Sec II + Computer)",
            "short": "Full Mock",
            "max_marks": 360.0,
            "total_qs": 120,
            "neg_mark": 1.0,
            "sections": [
                ("math", "Maths (30Q)", 90.0),
                ("reas", "Reasoning (30Q)", 90.0),
                ("eng", "English (40Q)", 120.0),
                ("ga", "GA (20Q)", 60.0)
            ],
            "qualifying": ("comp", "Computer Knowledge (15Q)", 45.0)
        },
        "SEC_1": {
            "name": "Section-I (Maths 30Q + Reasoning 30Q)",
            "short": "Section-I",
            "max_marks": 180.0,
            "total_qs": 60,
            "neg_mark": 1.0,
            "sections": [
                ("math", "Maths (30Q)", 90.0),
                ("reas", "Reasoning (30Q)", 90.0)
            ],
            "qualifying": None
        },
        "SEC_2": {
            "name": "Section-II (English 40Q + GA 20Q)",
            "short": "Section-II",
            "max_marks": 180.0,
            "total_qs": 60,
            "neg_mark": 1.0,
            "sections": [
                ("eng", "English (40Q)", 120.0),
                ("ga", "GA (20Q)", 60.0)
            ],
            "qualifying": None
        },
        "MOD_MATH": {
            "name": "Module-I: Mathematical Abilities (30Q)",
            "short": "Maths Module",
            "max_marks": 90.0,
            "total_qs": 30,
            "neg_mark": 1.0,
            "sections": [("math", "Maths (30Q)", 90.0)],
            "qualifying": None
        },
        "MOD_REAS": {
            "name": "Module-II: Reasoning & General Intelligence (30Q)",
            "short": "Reasoning Module",
            "max_marks": 90.0,
            "total_qs": 30,
            "neg_mark": 1.0,
            "sections": [("reas", "Reasoning (30Q)", 90.0)],
            "qualifying": None
        },
        "MOD_ENG": {
            "name": "Module-III: English Language & Comprehension (40Q)",
            "short": "English Module",
            "max_marks": 120.0,
            "total_qs": 40,
            "neg_mark": 1.0,
            "sections": [("eng", "English (40Q)", 120.0)],
            "qualifying": None
        },
        "MOD_GA": {
            "name": "Module-IV: General Awareness (20Q)",
            "short": "GA Module",
            "max_marks": 60.0,
            "total_qs": 20,
            "neg_mark": 1.0,
            "sections": [("ga", "GA (20Q)", 60.0)],
            "qualifying": None
        },
        "MOD_COMP": {
            "name": "Module-V: Computer Knowledge Test (15Q)",
            "short": "Computer Module",
            "max_marks": 45.0,
            "total_qs": 15,
            "neg_mark": 1.0,
            "sections": [("comp", "Computer Knowledge (15Q)", 45.0)],
            "qualifying": ("comp", "Computer Knowledge (15Q)", 45.0)
        }
    }
}

EXAM_TEMPLATES = {
    "SSC_CGL_MAINS": {
        "name": "SSC CGL 2026 Mains (Tier-II)",
        "short": "CGL Mains",
        "max_marks": 390.0,
        "neg_mark": 1.0,
        "sections": [
            ("math", "Maths (30Q)", 90.0),
            ("reas", "Reasoning (30Q)", 90.0),
            ("eng", "English (45Q)", 135.0),
            ("ga", "General Awareness (25Q)", 75.0)
        ],
        "qualifying": ("comp", "Computer Knowledge (20Q)", 60.0),
        "qual_cutoffs": {"UR": 18.0, "OBC": 15.0, "EWS": 15.0, "SC": 12.0, "ST": 12.0, "ESM": 12.0, "PwD": 12.0},
        "cpt_cutoffs": {"UR": 27.0, "OBC": 24.0, "EWS": 24.0, "SC": 21.0, "ST": 21.0, "ESM": 18.0, "PwD": 18.0},
        "cutoffs": {
            "UR": {"pa_sa": 292.0, "gp_2800": 304.0, "gp_4200": 316.0, "gp_4600": 324.0, "top_aso": 336.0},
            "OBC": {"pa_sa": 284.0, "gp_2800": 296.0, "gp_4200": 308.0, "gp_4600": 316.0, "top_aso": 328.0},
            "EWS": {"pa_sa": 282.0, "gp_2800": 294.0, "gp_4200": 306.0, "gp_4600": 315.0, "top_aso": 326.0},
            "SC": {"pa_sa": 262.0, "gp_2800": 274.0, "gp_4200": 286.0, "gp_4600": 295.0, "top_aso": 308.0},
            "ST": {"pa_sa": 250.0, "gp_2800": 262.0, "gp_4200": 275.0, "gp_4600": 284.0, "top_aso": 296.0},
            "ESM": {"pa_sa": 225.0, "gp_2800": 242.0, "gp_4200": 260.0, "gp_4600": 270.0, "top_aso": 285.0},
            "PwD": {"pa_sa": 230.0, "gp_2800": 245.0, "gp_4200": 262.0, "gp_4600": 274.0, "top_aso": 288.0}
        }
    },
    "SSC_CGL_PRE": {
        "name": "SSC CGL Tier-I (Pre)",
        "short": "CGL Pre",
        "max_marks": 200.0,
        "neg_mark": 0.50,
        "sections": [
            ("math", "Maths", 50.0),
            ("reas", "Reasoning", 50.0),
            ("eng", "English", 50.0),
            ("ga", "General Awareness", 50.0)
        ],
        "qualifying": None,
        "cutoffs": {"UR": 145.0, "OBC": 138.0, "EWS": 136.0, "SC": 120.0, "ST": 110.0, "ESM": 95.0, "PwD": 100.0}
    },
    "SSC_CHSL_MAINS": {
        "name": "SSC CHSL Mains (Tier-II)",
        "short": "CHSL Mains",
        "max_marks": 360.0,
        "neg_mark": 1.0,
        "sections": [
            ("math", "Maths (30Q)", 90.0),
            ("reas", "Reasoning (30Q)", 90.0),
            ("eng", "English (40Q)", 120.0),
            ("ga", "General Awareness (20Q)", 60.0)
        ],
        "qualifying": ("comp", "Computer Knowledge (15Q)", 45.0),
        "qual_cutoffs": {"UR": 13.5, "OBC": 11.25, "EWS": 11.25, "SC": 9.0, "ST": 9.0, "ESM": 9.0, "PwD": 9.0},
        "cpt_cutoffs": {"UR": 21.0, "OBC": 18.0, "EWS": 18.0, "SC": 15.0, "ST": 15.0, "ESM": 15.0, "PwD": 15.0},
        "cutoffs": {"UR": 295.0, "OBC": 288.0, "EWS": 286.0, "SC": 268.0, "ST": 255.0, "ESM": 230.0, "PwD": 235.0}
    },
    "SSC_CHSL_PRE": {
        "name": "SSC CHSL Tier-I (Pre)",
        "short": "CHSL Pre",
        "max_marks": 200.0,
        "neg_mark": 0.50,
        "sections": [
            ("math", "Maths", 50.0),
            ("reas", "Reasoning", 50.0),
            ("eng", "English", 50.0),
            ("ga", "General Awareness", 50.0)
        ],
        "qualifying": None,
        "cutoffs": {"UR": 152.0, "OBC": 146.0, "EWS": 144.0, "SC": 130.0, "ST": 120.0, "ESM": 102.0, "PwD": 108.0}
    },
    "SSC_CPO_PRE": {
        "name": "SSC CPO Paper-I (Pre)",
        "short": "CPO Pre",
        "max_marks": 200.0,
        "neg_mark": 0.25,
        "sections": [
            ("math", "Maths", 50.0),
            ("reas", "Reasoning", 50.0),
            ("eng", "English", 50.0),
            ("ga", "General Awareness", 50.0)
        ],
        "qualifying": None,
        "cutoffs": {"UR": 138.0, "OBC": 131.0, "EWS": 130.0, "SC": 112.0, "ST": 105.0, "ESM": 85.0, "PwD": 90.0}
    },
    "SSC_CPO_MAINS": {
        "name": "SSC CPO Paper-II (Mains)",
        "short": "CPO Mains",
        "max_marks": 200.0,
        "neg_mark": 0.25,
        "sections": [
            ("eng", "English Language & Comprehension", 200.0)
        ],
        "qualifying": None,
        "cutoffs": {"UR": 168.0, "OBC": 162.0, "EWS": 160.0, "SC": 145.0, "ST": 138.0, "ESM": 120.0, "PwD": 125.0}
    },
    "SSC_GD": {
        "name": "SSC GD Constable",
        "short": "SSC GD",
        "max_marks": 160.0,
        "neg_mark": 0.25,
        "sections": [
            ("math", "Maths", 40.0),
            ("reas", "Reasoning", 40.0),
            ("ga", "GK / GS", 40.0),
            ("eng", "English / Hindi", 40.0)
        ],
        "qualifying": None,
        "cutoffs": {"UR": 132.0, "OBC": 128.0, "EWS": 125.0, "SC": 115.0, "ST": 108.0, "ESM": 85.0, "PwD": 90.0}
    },
    "DEFENCE_EXAM": {
        "name": "Defence / Police (CAPF, DP HCM, CDS)",
        "short": "Defence/HCM",
        "max_marks": 100.0,
        "neg_mark": 0.25,
        "sections": [
            ("math", "Maths / Quant", 25.0),
            ("reas", "Reasoning", 25.0),
            ("eng", "English / Hindi", 25.0),
            ("ga", "GK / Computer", 25.0)
        ],
        "qualifying": None,
        "cutoffs": {"UR": 78.0, "OBC": 74.0, "EWS": 72.0, "SC": 66.0, "ST": 62.0, "ESM": 55.0, "PwD": 58.0}
    }
}


def clean_str_xml(text) -> str:
    if text is None:
        return "N/A"
    val_str = str(text).strip()
    if not val_str:
        return "N/A"
    normalized_str = unicodedata.normalize("NFC", val_str)
    return saxutils.escape(" ".join(normalized_str.split()))


# ==============================================================================
# 🗄️ DATABASE INITIALIZATION & CRUD OPERATIONS
# ==============================================================================
def init_scorekeeper_db():
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scorekeeper_profiles (
                user_id BIGINT PRIMARY KEY,
                category TEXT DEFAULT 'UR',
                active_exam TEXT DEFAULT 'SSC_CGL_MAINS',
                target_post TEXT DEFAULT 'Inspector / ASO (4600 GP)',
                target_score REAL DEFAULT 330.0,
                updated_at TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scorekeeper_logs (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                exam_code TEXT NOT NULL,
                exam_name TEXT,
                exam_date TEXT NOT NULL,
                mock_source TEXT DEFAULT 'Mock Test',
                difficulty TEXT DEFAULT 'Moderate',
                total_score REAL NOT NULL,
                max_marks REAL NOT NULL,
                wrong_qs INTEGER DEFAULT 0,
                negative_lost REAL DEFAULT 0.0,
                sectional_json TEXT DEFAULT '{}',
                qualifying_score REAL DEFAULT 0.0,
                passed_qualifying INTEGER DEFAULT 1,
                created_at TEXT
            )
        """)
        # Backward-compatible migrations for test types
        cursor.execute("ALTER TABLE scorekeeper_logs ADD COLUMN IF NOT EXISTS test_type TEXT DEFAULT 'FULL';")
        cursor.execute("ALTER TABLE scorekeeper_logs ADD COLUMN IF NOT EXISTS test_scope_name TEXT DEFAULT 'Full Mock';")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_sk_logs_user_exam
            ON scorekeeper_logs(user_id, exam_code, id DESC)
        """)
        conn.commit()
        cursor.close()
        release_db(conn)
    except Exception as e:
        if conn:
            release_db(conn)
        logger.error(f"[SCOREKEEPER DB INIT ERROR] {e}")


def get_or_create_sk_profile(user_id: int) -> dict:
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT * FROM scorekeeper_profiles WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        if row:
            return clean_decimals(dict(row))

        u_prof = get_user_profile(user_id) or {}
        reg_exam = str(u_prof.get("target_exam", "")).upper()
        default_exam = "SSC_CGL_MAINS"
        if "CHSL" in reg_exam:
            default_exam = "SSC_CHSL_MAINS"
        elif "CPO" in reg_exam:
            default_exam = "SSC_CPO_PRE"
        elif "GD" in reg_exam:
            default_exam = "SSC_GD"
        elif any(k in reg_exam for k in ("CAPF", "HCM", "BSF", "CISF", "DP")):
            default_exam = "DEFENCE_EXAM"

        now_str = get_ist_timestamp_str()
        cursor.execute("""
            INSERT INTO scorekeeper_profiles (user_id, category, active_exam, target_post, target_score, updated_at)
            VALUES (%s, 'UR', %s, 'Inspector / ASO (4600 GP)', 330.0, %s)
            ON CONFLICT (user_id) DO NOTHING
            RETURNING *
        """, (user_id, default_exam, now_str))
        new_row = cursor.fetchone()
        conn.commit()
        if new_row:
            return clean_decimals(dict(new_row))
        return {
            "user_id": user_id,
            "category": "UR",
            "active_exam": default_exam,
            "target_post": "Inspector / ASO (4600 GP)",
            "target_score": 330.0,
            "updated_at": now_str
        }
    finally:
        cursor.close()
        release_db(conn)


def update_sk_profile(user_id: int, category: str = None, active_exam: str = None, target_post: str = None):
    prof = get_or_create_sk_profile(user_id)
    cat = category or prof.get("category", "UR")
    ex = active_exam or prof.get("active_exam", "SSC_CGL_MAINS")
    post = target_post or prof.get("target_post", "Inspector / ASO (4600 GP)")
    now_str = get_ist_timestamp_str()

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE scorekeeper_profiles
            SET category = %s, active_exam = %s, target_post = %s, updated_at = %s
            WHERE user_id = %s
        """, (cat, ex, post, now_str, user_id))
        conn.commit()
    finally:
        cursor.close()
        release_db(conn)


def insert_score_log(
    user_id: int, exam_code: str, exam_date: str, mock_source: str,
    difficulty: str, total_score: float, max_marks: float,
    wrong_qs: int, negative_lost: float, sectional_dict: dict,
    qualifying_score: float, passed_qualifying: bool,
    test_type: str = "FULL", test_scope_name: str = "Full Mock"
) -> int:
    tmpl = EXAM_TEMPLATES.get(exam_code, EXAM_TEMPLATES["SSC_CGL_MAINS"])
    now_str = get_ist_timestamp_str()
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO scorekeeper_logs (
                user_id, exam_code, exam_name, exam_date, mock_source,
                difficulty, total_score, max_marks, wrong_qs, negative_lost,
                sectional_json, qualifying_score, passed_qualifying, created_at,
                test_type, test_scope_name
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            user_id, exam_code, tmpl["name"], exam_date, mock_source,
            difficulty, round(total_score, 2), max_marks, wrong_qs, round(negative_lost, 2),
            json.dumps(sectional_dict), round(qualifying_score, 2),
            1 if passed_qualifying else 0, now_str,
            test_type, test_scope_name
        ))
        log_id = cursor.fetchone()[0]
        conn.commit()
    finally:
        cursor.close()
        release_db(conn)

    sync_scorekeeper_to_json(user_id)
    return log_id


def get_user_score_logs(user_id: int, exam_code: str = None, limit: int = 200) -> list:
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        if exam_code:
            cursor.execute("""
                SELECT * FROM scorekeeper_logs
                WHERE user_id = %s AND exam_code = %s
                ORDER BY exam_date DESC, id DESC LIMIT %s
            """, (user_id, exam_code, limit))
        else:
            cursor.execute("""
                SELECT * FROM scorekeeper_logs
                WHERE user_id = %s
                ORDER BY exam_date DESC, id DESC LIMIT %s
            """, (user_id, limit))
        rows = cursor.fetchall()
        result = []
        for r in rows:
            d = clean_decimals(dict(r))
            try:
                d["sectional"] = json.loads(d.get("sectional_json") or "{}")
            except Exception:
                d["sectional"] = {}
            if not d.get("test_type"):
                d["test_type"] = "FULL"
            if not d.get("test_scope_name"):
                d["test_scope_name"] = "Full Mock"
            result.append(d)
        return result
    finally:
        cursor.close()
        release_db(conn)


def delete_last_score_log(user_id: int, exam_code: str) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            DELETE FROM scorekeeper_logs
            WHERE id = (
                SELECT id FROM scorekeeper_logs
                WHERE user_id = %s AND exam_code = %s
                ORDER BY id DESC LIMIT 1
            )
            RETURNING id
        """, (user_id, exam_code))
        deleted = cursor.fetchone() is not None
        conn.commit()
    finally:
        cursor.close()
        release_db(conn)

    if deleted:
        sync_scorekeeper_to_json(user_id)
    return deleted


def calculate_exam_community_percentile(user_id: int, exam_code: str, user_ema_score: float) -> tuple[float, int]:
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT user_id, AVG(total_score) as avg_s
            FROM scorekeeper_logs
            WHERE exam_code = %s AND (test_type = 'FULL' OR test_type IS NULL)
            GROUP BY user_id
        """, (exam_code,))
        rows = cursor.fetchall()
        total_peers = len(rows)
        if total_peers <= 1:
            return 99.0, max(1, total_peers)
        below = sum(1 for r in rows if float(r["avg_s"] or 0) < user_ema_score)
        pct = round((below / max(1, total_peers)) * 100.0, 1)
        return min(99.9, max(1.0, pct)), total_peers
    except Exception:
        return 95.0, 1
    finally:
        cursor.close()
        release_db(conn)


def sync_scorekeeper_to_json(user_id: int):
    """Appends scorekeeper profile and logs into the student's master JSON profile ledger."""
    try:
        sync_user_json_profile(user_id)
        u = get_user_profile(user_id)
        if not u:
            return
        sid = u.get("student_id") or f"USER_{user_id}"
        filepath = os.path.join(USER_PROFILES_DIR, f"{sid}.json")
        if not os.path.exists(filepath):
            return

        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        sk_prof = get_or_create_sk_profile(user_id)
        all_logs = get_user_score_logs(user_id, limit=100)
        data["scorekeeper_ledger"] = {
            "profile": sk_prof,
            "total_logged_mocks": len(all_logs),
            "logs": all_logs,
            "updated_at": get_ist_timestamp_str()
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(clean_decimals(data), f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.error(f"[SCOREKEEPER JSON SYNC ERROR] {e}")


# ==============================================================================
# 🔮 AI PREDICTION, POST-ALLOTMENT & ANALYTICS ENGINE
# ==============================================================================
def analyze_user_scores(user_id: int, exam_code: str = None) -> dict:
    sk_prof = get_or_create_sk_profile(user_id)
    exam_code = exam_code or sk_prof.get("active_exam", "SSC_CGL_MAINS")
    category = sk_prof.get("category", "UR")
    tmpl = EXAM_TEMPLATES.get(exam_code, EXAM_TEMPLATES["SSC_CGL_MAINS"])
    logs_desc = get_user_score_logs(user_id, exam_code, limit=100)

    if not logs_desc:
        return {
            "has_data": False,
            "exam_code": exam_code,
            "exam_name": tmpl["name"],
            "category": category,
            "target_post": sk_prof.get("target_post", "Inspector / ASO (4600 GP)"),
            "max_marks": tmpl["max_marks"],
            "full_mock_count": 0,
            "sectional_count": 0,
            "module_count": 0
        }

    # Chronological order (oldest -> newest)
    logs_asc = list(reversed(logs_desc))

    # Segment tests by Scope for optimized telemetry
    full_logs = [l for l in logs_asc if l.get("test_type", "FULL") == "FULL"]
    sec_logs = [l for l in logs_asc if str(l.get("test_type", "")).startswith("SEC")]
    mod_logs = [l for l in logs_asc if str(l.get("test_type", "")).startswith("MOD")]

    # Primary analysis focuses on full mocks for exam-day cutoffs; fallback to all if none
    eval_pool = full_logs if full_logs else logs_asc
    scores = [float(l["total_score"]) for l in eval_pool]
    n = len(scores)
    max_marks = float(eval_pool[-1]["max_marks"]) if eval_pool else float(tmpl["max_marks"])

    latest_log = logs_desc[0]
    latest_score = float(latest_log["total_score"])
    prev_score = float(logs_desc[1]["total_score"]) if len(logs_desc) >= 2 else latest_score
    last_delta = round(latest_score - prev_score, 2)
    best_score = max([float(l["total_score"]) for l in eval_pool])
    lowest_score = min([float(l["total_score"]) for l in eval_pool])
    avg_score = round(sum(scores) / n, 2)

    # 1. Exponential Moving Average (EMA) Score Projection
    alpha = 0.38
    ema = scores[0]
    for s in scores[1:]:
        ema = alpha * s + (1.0 - alpha) * ema
    ema_score = round(ema, 2)

    # 2. Shift-Difficulty Normalization Adjustment
    norm_scores = []
    for l in eval_pool[-5:]:
        raw_s = float(l["total_score"])
        cur_max = float(l.get("max_marks") or max_marks)
        diff = str(l.get("difficulty", "Moderate")).capitalize()
        if diff == "Hard":
            boost = cur_max * 0.035
        elif diff == "Moderate":
            boost = cur_max * 0.012
        else:
            boost = -cur_max * 0.008
        norm_scores.append(min(cur_max, raw_s + boost))
    predicted_final_score = round((ema_score * 0.45) + ((sum(norm_scores) / len(norm_scores)) * 0.55), 2)
    predicted_final_score = min(max_marks, max(0.0, predicted_final_score))

    # 3. Growth Velocity & Consistency Index
    if n >= 2:
        recent_slice = scores[-min(6, n):]
        growth_per_mock = round((recent_slice[-1] - recent_slice[0]) / max(1, len(recent_slice) - 1), 2)
        mean_s = sum(scores) / n
        variance = sum((x - mean_s) ** 2 for x in scores) / n
        std_dev = round(math.sqrt(variance), 2)
        stability_pct = round(max(35.0, min(99.0, 100.0 - (std_dev / max_marks) * 220.0)), 1)
    else:
        growth_per_mock = 0.0
        std_dev = 0.0
        stability_pct = 90.0

    # 4. Negative Marking Bleed Analysis (aggregates all tests)
    total_logs_count = len(logs_desc)
    avg_wrong = round(sum(int(l.get("wrong_qs") or 0) for l in logs_desc) / total_logs_count, 1)
    avg_neg_lost = round(sum(float(l.get("negative_lost") or 0.0) for l in logs_desc) / total_logs_count, 2)
    recoverable_marks = round(avg_neg_lost * 0.45, 2)

    # 5. Sectional & Module Efficiency Aggregation Across All Logs
    sec_totals = {k: 0.0 for k, _, _ in tmpl["sections"]}
    sec_counts = {k: 0 for k, _, _ in tmpl["sections"]}
    for l in logs_desc:
        sec_map = l.get("sectional") or {}
        # Also map single module scores if logged as standalone module
        t_type = l.get("test_type", "")
        if t_type == "MOD_MATH" and "math" not in sec_map:
            sec_map["math"] = l["total_score"]
        elif t_type == "MOD_REAS" and "reas" not in sec_map:
            sec_map["reas"] = l["total_score"]
        elif t_type == "MOD_ENG" and "eng" not in sec_map:
            sec_map["eng"] = l["total_score"]
        elif t_type == "MOD_GA" and "ga" not in sec_map:
            sec_map["ga"] = l["total_score"]

        for k, _, _ in tmpl["sections"]:
            if k in sec_map and sec_map[k] is not None:
                sec_totals[k] += float(sec_map[k])
                sec_counts[k] += 1

    sectional_breakdown = []
    weakest_sec = None
    lowest_sec_pct = 101.0

    for k, label, s_max in tmpl["sections"]:
        cnt = sec_counts[k]
        s_avg = round(sec_totals[k] / cnt, 1) if cnt > 0 else 0.0
        s_pct = round((s_avg / s_max) * 100.0, 1) if (cnt > 0 and s_max > 0) else 0.0
        sectional_breakdown.append({
            "key": k,
            "label": label,
            "avg": s_avg,
            "max": s_max,
            "pct": s_pct,
            "has_logs": cnt > 0,
            "count": cnt
        })
        if cnt > 0 and s_pct < lowest_sec_pct:
            lowest_sec_pct = s_pct
            weakest_sec = {"key": k, "label": label, "pct": s_pct, "avg": s_avg, "max": s_max}

    # 6. Qualifying Computer Section Check (CGL Mains / CHSL Mains)
    qual_info = None
    if tmpl.get("qualifying"):
        q_key, q_label, q_max = tmpl["qualifying"]
        q_scores = []
        for l in logs_desc:
            sc = float(l.get("qualifying_score") or 0.0)
            if sc > 0:
                q_scores.append(sc)
            elif l.get("test_type") == "MOD_COMP":
                q_scores.append(float(l.get("total_score") or 0.0))

        q_avg = round(sum(q_scores) / len(q_scores), 1) if q_scores else 0.0
        min_cut = tmpl["qual_cutoffs"].get(category, 18.0)
        cpt_cut = tmpl["cpt_cutoffs"].get(category, 27.0)
        if not q_scores:
            q_status = "ℹ️ Not Logged"
        elif q_avg >= cpt_cut:
            q_status = f"✅ CPT Safe ({q_avg}/{int(q_max)})"
        elif q_avg >= min_cut:
            q_status = f"⚠️ Borderline CPT ({q_avg}/{int(q_max)} — Aim {int(cpt_cut)}+)"
        else:
            q_status = f"🚨 DANGER: Below Qualifying ({q_avg}/{int(q_max)} < {min_cut})"
        qual_info = {
            "label": q_label,
            "avg": q_avg,
            "max": q_max,
            "min_cutoff": min_cut,
            "cpt_cutoff": cpt_cut,
            "status": q_status,
            "attempts": len(q_scores)
        }

    # 7. Category Cutoff & Post Allotment Prediction (Calibrated to Full Mocks)
    # If the user only has Sectional / Module tests, we project full equivalent score
    projected_full_score = predicted_final_score
    if not full_logs and max_marks < tmpl["max_marks"]:
        # Extrapolate equivalent percentage to full exam scale
        projected_full_score = round((predicted_final_score / max_marks) * tmpl["max_marks"], 1)

    if exam_code == "SSC_CGL_MAINS":
        c_map = tmpl["cutoffs"].get(category, tmpl["cutoffs"]["UR"])
        if projected_full_score >= c_map["top_aso"]:
            post_zone = "🏆 TOP RANKER ZONE: ASO in MEA / CSS / Income Tax Inspector (4600 GP)"
            next_goal_text = f"Maintain consistency! You are +{round(projected_full_score - c_map['top_aso'], 1)} marks above Top ASO cutoff ({c_map['top_aso']})."
        elif projected_full_score >= c_map["gp_4600"]:
            post_zone = "🎖️ 4600 GP INSPECTOR ZONE: GST Inspector / Preventive Officer / Examiner / ASO"
            gap = round(c_map["top_aso"] - projected_full_score, 1)
            next_goal_text = f"Need +{gap} marks to enter Top-100 ASO (MEA/ITI) zone ({c_map['top_aso']})."
        elif projected_full_score >= c_map["gp_4200"]:
            post_zone = "⭐ 4200 GP ZONE: Divisional Accountant (CAG) / Sub-Inspector (CBN/NIA)"
            gap = round(c_map["gp_4600"] - projected_full_score, 1)
            next_goal_text = f"Need +{gap} marks to unlock 4600 GP Inspector/ASO posts ({c_map['gp_4600']})."
        elif projected_full_score >= c_map["gp_2800"]:
            post_zone = "✅ 2800 GP ZONE: Auditor (CAG/CGDA) / Junior Accountant"
            gap = round(c_map["gp_4600"] - projected_full_score, 1)
            next_goal_text = f"Need +{gap} marks to reach 4600 GP Inspector zone ({c_map['gp_4600']})."
        elif projected_full_score >= c_map["pa_sa"]:
            post_zone = "🟢 2400 GP SELECTION ZONE: Tax Assistant (CBDT/CBIC) / PA/SA / UDC"
            gap = round(c_map["gp_2800"] - projected_full_score, 1)
            next_goal_text = f"Need +{gap} marks for 2800 GP Auditor ({c_map['gp_2800']}) & +{round(c_map['gp_4600'] - projected_full_score, 1)} for 4600 GP."
        else:
            gap = round(c_map["pa_sa"] - projected_full_score, 1)
            post_zone = f"⚠️ BELOW SAFE CUTOFF ({category} Safe Cutoff: {c_map['pa_sa']})"
            next_goal_text = f"Need +{gap} marks to cross the minimum post selection line ({c_map['pa_sa']}) and +{round(c_map['gp_4600'] - projected_full_score, 1)} for 4600 GP."
        base_cutoff = c_map["pa_sa"]
        dream_cutoff = c_map["gp_4600"]
    else:
        base_cutoff = float(tmpl["cutoffs"].get(category, 140.0))
        dream_cutoff = round(min(tmpl["max_marks"], base_cutoff * 1.08), 1)
        margin = round(projected_full_score - base_cutoff, 1)
        if margin >= 12.0:
            post_zone = f"🏆 HIGH MERIT SAFE ZONE (+{margin} above {category} cutoff {base_cutoff})"
            next_goal_text = "Strong merit position! Focus on speed and eliminating silly negative errors."
        elif margin >= 0.0:
            post_zone = f"✅ QUALIFYING / SELECTION ZONE (+{margin} above {category} cutoff {base_cutoff})"
            next_goal_text = f"Add +{round(12.0 - margin, 1)} marks to build a 100% Top-Rank safety buffer."
        else:
            post_zone = f"⚠️ BELOW PROJECTED CUTOFF ({category} Cutoff: {base_cutoff})"
            next_goal_text = f"Need +{abs(margin)} marks to clear the expected {category} cutoff ({base_cutoff})."

    comm_pct, comm_peers = calculate_exam_community_percentile(user_id, exam_code, ema_score)

    return {
        "has_data": True,
        "exam_code": exam_code,
        "exam_name": tmpl["name"],
        "short_name": tmpl["short"],
        "category": category,
        "target_post": sk_prof.get("target_post", "Inspector / ASO (4600 GP)"),
        "max_marks": tmpl["max_marks"],
        "total_mocks": len(logs_desc),
        "full_mock_count": len(full_logs),
        "sectional_count": len(sec_logs),
        "module_count": len(mod_logs),
        "latest_score": latest_score,
        "latest_log": latest_log,
        "last_delta": last_delta,
        "best_score": best_score,
        "lowest_score": lowest_score,
        "avg_score": avg_score,
        "ema_score": ema_score,
        "predicted_final_score": projected_full_score,
        "growth_per_mock": growth_per_mock,
        "std_dev": std_dev,
        "stability_pct": stability_pct,
        "avg_wrong": avg_wrong,
        "avg_neg_lost": avg_neg_lost,
        "recoverable_marks": recoverable_marks,
        "sectional_breakdown": sectional_breakdown,
        "weakest_sec": weakest_sec,
        "qual_info": qual_info,
        "post_zone": post_zone,
        "next_goal_text": next_goal_text,
        "base_cutoff": base_cutoff,
        "dream_cutoff": dream_cutoff,
        "comm_percentile": comm_pct,
        "comm_peers": comm_peers,
        "logs_desc": logs_desc,
        "logs_asc": logs_asc
    }


# ==============================================================================
# 📄 WEASYPRINT OFFICIAL PDF REPORT GENERATOR (WITH SVG CHARTS)
# ==============================================================================
def generate_scorekeeper_pdf(user_id: int, exam_code: str = None) -> str:
    try:
        u = get_user_profile(user_id)
        if not u:
            return "ERROR: User profile not found."

        stats = analyze_user_scores(user_id, exam_code)
        if not stats.get("has_data"):
            return "NO_LOGS"

        now_str = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")
        sid = clean_str_xml(u.get("student_id") or f"USER_{user_id}")
        full_name = clean_str_xml(u.get("full_name", "Student"))
        category = clean_str_xml(stats["category"])
        exam_name = clean_str_xml(stats["exam_name"])
        target_post = clean_str_xml(stats["target_post"])
        max_marks = stats["max_marks"]

        logo_left_path = os.path.abspath(os.path.join(BASE_DIR, "assets", "logo.png"))
        logo_right_path = os.path.abspath(os.path.join(BASE_DIR, "assets", "logohim.png"))
        target_link = "https://t.me/learnwithhim"

        left_logo_html = f'<a href="{target_link}"><img src="file://{logo_left_path}" style="width: 60px; height: 60px; object-fit: contain;" /></a>' if os.path.exists(logo_left_path) else "<b>Quiz with HiM</b>"
        right_logo_html = f'<a href="{target_link}"><img src="file://{logo_right_path}" style="width: 60px; height: 60px; object-fit: contain;" /></a>' if os.path.exists(logo_right_path) else "<b>@LearnwithHiM</b>"

        # 1. Build SVG Score Trajectory Line Chart (Last 12 tests)
        chart_logs = stats["logs_asc"][-12:]
        svg_w, svg_h = 460, 150
        pad_l, pad_r, pad_t, pad_b = 38, 20, 20, 28
        plot_w = svg_w - pad_l - pad_r
        plot_h = svg_h - pad_t - pad_b

        cutoff_y = pad_t + plot_h - int((min(max_marks, stats["base_cutoff"]) / max_marks) * plot_h)
        points = []
        circles_svg = []
        n_pts = len(chart_logs)

        for idx, l in enumerate(chart_logs):
            cur_max = float(l.get("max_marks") or max_marks)
            sc = float(l["total_score"])
            # Normalized height against full scale
            norm_pct = min(1.0, max(0.0, sc / cur_max)) if cur_max > 0 else 0.5
            x = pad_l + (int((idx / max(1, n_pts - 1)) * plot_w) if n_pts > 1 else plot_w // 2)
            y = pad_t + plot_h - int(norm_pct * plot_h)
            points.append(f"{x},{y}")
            dt_short = str(l.get("exam_date", ""))[-5:]
            circles_svg.append(
                f"<circle cx='{x}' cy='{y}' r='3.5' fill='#1E3A8A' stroke='#ffffff' stroke-width='1'/>"
                f"<text x='{x}' y='{ max(12, y - 6) }' text-anchor='middle' font-size='8' font-weight='bold' fill='#0F172A'>{sc:.0f}</text>"
                f"<text x='{x}' y='{svg_h - 8}' text-anchor='middle' font-size='7.5' fill='#64748B'>{clean_str_xml(dt_short)}</text>"
            )

        polyline_str = " ".join(points)
        trajectory_svg = f"""
        <svg width='{svg_w}' height='{svg_h}' viewBox='0 0 {svg_w} {svg_h}'>
            <line x1='{pad_l}' y1='{pad_t + plot_h}' x2='{svg_w - pad_r}' y2='{pad_t + plot_h}' stroke='#CBD5E1' stroke-width='1'/>
            <line x1='{pad_l}' y1='{cutoff_y}' x2='{svg_w - pad_r}' y2='{cutoff_y}' stroke='#DC2626' stroke-width='1' stroke-dasharray='4'/>
            <text x='{svg_w - pad_r - 2}' y='{max(10, cutoff_y - 4)}' text-anchor='end' font-size='8' font-weight='bold' fill='#DC2626'>Cutoff ({stats['base_cutoff']:.0f})</text>
            <polyline fill='none' stroke='#0284C7' stroke-width='2.2' points='{polyline_str}'/>
            {''.join(circles_svg)}
        </svg>
        """

        # 2. Build Sectional Efficiency Table Rows
        sec_rows_html = []
        for s in stats["sectional_breakdown"]:
            bar_w = max(2, min(100, int(s["pct"])))
            bar_color = "#16A34A" if s["pct"] >= 78 else ("#0284C7" if s["pct"] >= 62 else "#EF4444")
            sec_rows_html.append(f"""
                <tr>
                    <td><b>{clean_str_xml(s['label'])}</b></td>
                    <td style='text-align:center;'>{s['avg']} / {int(s['max'])}</td>
                    <td style='text-align:center;'><b>{s['pct']}%</b></td>
                    <td>
                        <div style='width:100%; background:#E2E8F0; height:10px; border-radius:4px;'>
                            <div style='width:{bar_w}%; background:{bar_color}; height:10px; border-radius:4px;'></div>
                        </div>
                    </td>
                </tr>
            """)
        if stats.get("qual_info"):
            qi = stats["qual_info"]
            sec_rows_html.append(f"""
                <tr style='background:#F8FAFC;'>
                    <td><b>🖥️ {clean_str_xml(qi['label'])} (Qualifying Gate)</b></td>
                    <td style='text-align:center;'>{qi['avg']} / {int(qi['max'])}</td>
                    <td colspan='2'><b>{clean_str_xml(qi['status'])}</b></td>
                </tr>
            """)

        # 3. Build Complete Chronological Score Ledger Rows (with Scope badges)
        ledger_rows_html = []
        logs_desc = stats["logs_desc"]
        for i, l in enumerate(logs_desc):
            prev_sc = float(logs_desc[i + 1]["total_score"]) if (i + 1) < len(logs_desc) else float(l["total_score"])
            delta = round(float(l["total_score"]) - prev_sc, 1)
            delta_str = f"+{delta}" if delta > 0 else (f"{delta}" if delta < 0 else "0.0")
            delta_col = "#16A34A" if delta > 0 else ("#DC2626" if delta < 0 else "#64748B")

            sec_d = l.get("sectional") or {}
            sec_parts = [f"{k.upper()[:1]}:{v}" for k, v in sec_d.items()]
            if float(l.get("qualifying_score") or 0) > 0:
                sec_parts.append(f"C:{l['qualifying_score']}")
            sec_summary = " | ".join(sec_parts) if sec_parts else "Direct Total"

            scope_name = l.get("test_scope_name") or ("Full Mock" if l.get("test_type") == "FULL" else l.get("test_type", "Test"))

            ledger_rows_html.append(f"""
                <tr>
                    <td style='text-align:center;'>{len(logs_desc) - i}</td>
                    <td><b>{clean_str_xml(l['exam_date'])}</b><br/><small style='color:#0284C7;'>{clean_str_xml(scope_name)}</small></td>
                    <td>{clean_str_xml(l['mock_source'])} ({clean_str_xml(l['difficulty'])})</td>
                    <td style='text-align:center; font-weight:bold; color:#1E3A8A;'>{l['total_score']} / {int(l['max_marks'])}</td>
                    <td style='text-align:center; font-weight:bold; color:{delta_col};'>{delta_str}</td>
                    <td>{clean_str_xml(sec_summary)}</td>
                    <td style='text-align:center; color:#DC2626;'>-{l.get('negative_lost', 0.0)} ({l.get('wrong_qs', 0)}W)</td>
                </tr>
            """)

        html_str = f"""
        <!DOCTYPE html>
        <html>
        <head>
        <meta charset='utf-8'/>
        <style>
        @page {{ size: letter; margin: 16mm 13mm 18mm 13mm; @bottom-right {{ content: 'Page ' counter(page); font-size: 8.5px; color: #64748B; }} }}
        body {{ font-family: 'Times New Roman', Helvetica, Arial, sans-serif; color: #1E293B; font-size: 11px; line-height: 1.4; margin: 0; }}
        .header-table {{ width: 100%; border-collapse: collapse; margin-bottom: 10px; border-bottom: 1.5px solid #1E3A8A; padding-bottom: 6px; }}
        .header-title {{ text-align: center; color: #1E3A8A; font-size: 20px; font-weight: bold; }}
        .sub-title {{ color: #16A34A; font-size: 11px; text-align: center; font-weight: bold; margin-top: 3px; }}
        h3 {{ font-size: 12px; color: #0F172A; text-transform: uppercase; margin: 14px 0 6px 0; border-left: 3.5px solid #0284C7; padding-left: 7px; }}
        .data-table {{ width: 100%; border-collapse: collapse; margin-bottom: 10px; }}
        .data-table th, .data-table td {{ border: 0.5px solid #CBD5E1; padding: 6px 8px; text-align: left; font-size: 10.5px; }}
        .data-table th {{ background-color: #E0F2FE; color: #0F172A; font-weight: bold; }}
        .kpi-card {{ background: #F8FAFC; border: 1px solid #CBD5E1; padding: 8px; text-align: center; width: 25%; }}
        .pred-box {{ background: #F0F9FF; border: 1px solid #7DD3FC; padding: 10px 12px; border-radius: 6px; margin-bottom: 12px; }}
        .watermark-container {{ position: fixed; top: 0; left: 0; width: 100%; height: 100%; z-index: -1000; overflow: hidden; pointer-events: none; }}
        .wm-text {{ position: absolute; font-weight: bold; font-size: 26px; color: #94A3B8; opacity: 0.11; transform: rotate(30deg); white-space: nowrap; }}
        .pdf-footer {{ position: fixed; bottom: -12mm; left: 0; width: 100%; text-align: center; border-top: 0.5px solid #CBD5E1; padding-top: 4px; font-size: 8.5pt; color: #0284C7; font-weight: bold; }}
        </style>
        </head>
        <body>
        <div class='watermark-container'>
            <div class='wm-text' style='top: 15%; left: 12%;'>Quiz with HiM</div>
            <div class='wm-text' style='top: 22%; left: 60%;'>Learn with HiM</div>
            <div class='wm-text' style='top: 52%; left: 18%;'>Scorekeeper AI Report</div>
            <div class='wm-text' style='top: 58%; left: 62%;'>Quiz with HiM</div>
            <div class='wm-text' style='top: 85%; left: 20%;'>Learn with HiM</div>
        </div>

        <table class='header-table'>
            <tr>
                <td style='width:14%; text-align:left;'>{left_logo_html}</td>
                <td class='header-title'>Quiz with HiM — Official Scorekeeper & AI Predictor<div class='sub-title'> Track Marks • Predict Post Allotment • Full / Sectional / Module Analytics • Generated: {now_str}</div></td>
                <td style='width:14%; text-align:right;'>{right_logo_html}</td>
            </tr>
        </table>

        <table class='data-table'>
            <tr>
                <td><b>Candidate Name:</b> {full_name} ({sid})</td>
                <td><b>Category:</b> {category}</td>
                <td><b>Target Exam:</b> {exam_name}</td>
                <td><b>Dream Post:</b> {target_post}</td>
            </tr>
        </table>

        <table class='data-table'>
            <tr>
                <td class='kpi-card'><div style='font-size:9px; color:#64748B;'>PREDICTED FULL SCORE</div><div style='font-size:15px; font-weight:bold; color:#0284C7;'>{stats['predicted_final_score']} / {int(max_marks)}</div></td>
                <td class='kpi-card'><div style='font-size:9px; color:#64748B;'>TEST COUNT BREAKDOWN</div><div style='font-size:12px; font-weight:bold; color:#1E3A8A;'>Full: {stats['full_mock_count']} | Sec: {stats['sectional_count']} | Mod: {stats['module_count']}</div></td>
                <td class='kpi-card'><div style='font-size:9px; color:#64748B;'>BEST / AVG ({stats['total_mocks']} Tests)</div><div style='font-size:15px; font-weight:bold; color:#16A34A;'>{stats['best_score']} / {stats['avg_score']}</div></td>
                <td class='kpi-card'><div style='font-size:9px; color:#64748B;'>STABILITY & PERCENTILE</div><div style='font-size:15px; font-weight:bold; color:#0F172A;'>{stats['stability_pct']}% | {stats['comm_percentile']}%ile</div></td>
            </tr>
        </table>

        <div class='pred-box'>
            <div style='font-size:12px; font-weight:bold; color:#0369A1; margin-bottom:4px;'>🔮 AI POST ALLOTMENT & SELECTION PREDICTION</div>
            <div><b>Projected Zone:</b> {clean_str_xml(stats['post_zone'])}</div>
            <div><b>Next Milestone:</b> {clean_str_xml(stats['next_goal_text'])}</div>
            <div><b>Negative Bleed Recovery:</b> Avg Marks Lost to Wrong Qs: <b>-{stats['avg_neg_lost']} marks/test</b>. Eliminating wild guesses recovers <b>+{stats['recoverable_marks']} marks</b>.</div>
        </div>

        <h3>📈 SCORE TRAJECTORY vs. CATEGORY CUTOFF ({category})</h3>
        <div style='text-align:center; border:0.5px solid #CBD5E1; padding:8px; background:#FFFFFF; margin-bottom:10px;'>
            {trajectory_svg}
        </div>

        <h3>📊 SECTIONAL & MODULE EFFICIENCY BREAKDOWN</h3>
        <table class='data-table'>
            <thead>
                <tr>
                    <th style='width:32%;'>Subject / Module</th>
                    <th style='width:20%; text-align:center;'>Avg Score</th>
                    <th style='width:15%; text-align:center;'>Efficiency %</th>
                    <th style='width:33%;'>Visual Meter</th>
                </tr>
            </thead>
            <tbody>
                {''.join(sec_rows_html)}
            </tbody>
        </table>

        <h3>📋 CHRONOLOGICAL TEST LEDGER ({stats['total_mocks']} Tests Logged)</h3>
        <table class='data-table'>
            <thead>
                <tr>
                    <th style='width:6%; text-align:center;'>#</th>
                    <th style='width:16%;'>Date & Scope</th>
                    <th style='width:20%;'>Source & Shift</th>
                    <th style='width:15%; text-align:center;'>Score</th>
                    <th style='width:9%; text-align:center;'>Change</th>
                    <th style='width:22%;'>Sectional Split</th>
                    <th style='width:12%; text-align:center;'>Neg Bleed</th>
                </tr>
            </thead>
            <tbody>
                {''.join(ledger_rows_html)}
            </tbody>
        </table>

        <div class='pdf-footer'>
            🏛 Official Scorekeeper Report • Telegram: @learnwithhim • Bot: @quizwithhimbot • YouTube: @LearnwithHiM
        </div>
        </body>
        </html>
        """

        filename = f"Scorekeeper_{sid}_{stats['exam_code']}_{int(datetime.now(IST).timestamp())}.pdf"
        pdf_path = os.path.join(USER_PROFILES_DIR, filename)

        if HAS_WEASYPRINT:
            weasyprint.HTML(string=html_str).write_pdf(pdf_path)
            if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 500:
                return pdf_path
        return "ERROR: WeasyPrint engine unavailable on server."
    except Exception:
        logger.error(f"[SCOREKEEPER PDF ERROR] {traceback.format_exc()}")
        return f"ERROR_DETAILS:\n{traceback.format_exc()}"


# ==============================================================================
# 🤖 TELEGRAM BOT UI, DASHBOARD, ONE-TAP VAULT & CONVERSATION HANDLERS
# ==============================================================================
async def _sk_access_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    from app.telegram_bot import maintenance_guard, check_user_registration, log_command_usage
    if not await maintenance_guard(update, context):
        return False
    if not await check_user_registration(update):
        return False
    user = update.effective_user
    if user:
        asyncio.create_task(asyncio.to_thread(log_command_usage, user.id, "/scorekeeper"))
    return True


def build_scorekeeper_dashboard_Valid(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    u_prof = get_user_profile(user_id) or {}
    sk_prof = get_or_create_sk_profile(user_id)
    exam_code = sk_prof.get("active_exam", "SSC_CGL_MAINS")
    stats = analyze_user_scores(user_id, exam_code)

    name = u_prof.get("full_name", "Scholar")
    cat = sk_prof.get("category", "UR")
    target_post = sk_prof.get("target_post", "Inspector / ASO (4600 GP)")

    if not stats.get("has_data"):
        msg = (
            f"📊 **QUIZ WITH HIM — SCOREKEEPER & AI PREDICTOR** 📊\n"
            f"• • • ✧ • • •\n"
            f"👤 **Candidate:** {name} | 🏷 **Category:** `{cat}`\n"
            f"🎯 **Active Exam Track:** `{stats['exam_name']}`\n"
            f"🎖️ **Target Post:** `{target_post}`\n"
            f"• • • ✧ • • •\n"
            f"ℹ️ *No mock scores logged for `{stats['exam_name']}` yet!*\n\n"
            f"👉 Tap **➕ Log Score** below to record:\n"
            f" • **Full Mocks** (Full Exam Pattern)\n"
            f" • **Sectional Tests** (Section-I / Section-II)\n"
            f" • **Module Tests** (Maths / Reasoning / English / GA / Computer)"
        )
    else:
        delta_icon = "📈" if stats["last_delta"] >= 0 else "📉"
        delta_sign = f"+{stats['last_delta']}" if stats["last_delta"] > 0 else f"{stats['last_delta']}"
        qual_line = f"🖥️ **Computer Gate:** `{stats['qual_info']['status']}`\n" if stats.get("qual_info") else ""
        weak_line = f"⚠️ **Focus Area:** `{stats['weakest_sec']['label']} ({stats['weakest_sec']['pct']}%)`\n" if stats.get("weakest_sec") else ""

        latest_scope = stats["latest_log"].get("test_scope_name", "Full Mock")
        test_mix_line = f"📊 **Tests Mix:** Full: `{stats['full_mock_count']}` | Sec: `{stats['sectional_count']}` | Mod: `{stats['module_count']}`\n"

        msg = (
            f"📊 **QUIZ WITH HIM — SCOREKEEPER & AI PREDICTOR** 📊\n"
            f"• • • ✧ • • •\n"
            f"👤 **Candidate:** {name} | 🏷 **Category:** `{cat}`\n"
            f"🎯 **Active Track:** `{stats['exam_name']}`\n"
            f"🎖️ **Dream Post:** `{target_post}`\n"
            f"• • • ✧ • • •\n"
            f"📌 **Latest Score:** `{stats['latest_score']} / {int(stats['latest_log'].get('max_marks', stats['max_marks']))}` ({latest_scope})\n"
            f"📈 **Recent Trend:** `{delta_sign} marks {delta_icon}`\n"
            f"{test_mix_line}"
            f"🔮 **Projected Full Exam Score:** `{stats['predicted_final_score']} / {int(stats['max_marks'])}`\n"
            f"🏆 **Best Full:** `{stats['best_score']}` | 📊 **EMA Potential:** `{stats['ema_score']}`\n"
            f"⚖️ **Consistency:** `{stats['stability_pct']}%` | 👥 **Percentile:** `{stats['comm_percentile']}%ile`\n"
            f"{qual_line}"
            f"{weak_line}"
            f"• • • ✧ • • •\n"
            f"🎖️ **Prediction:** {stats['post_zone']}\n"
            f"💡 *{stats['next_goal_text']}*"
        )

    keyboard = [
        [
            InlineKeyboardButton("➕ Log Score (Full/Sec/Mod)", callback_data="sk_start_log"),
            InlineKeyboardButton("📋 View Score Vault", callback_data="sk_view_all_0")
        ],
        [
            InlineKeyboardButton("🔮 Detailed AI Prediction", callback_data="sk_detailed_pred"),
            InlineKeyboardButton("📄 Download PDF Dossier", callback_data="sk_download_pdf")
        ],
        [
            InlineKeyboardButton("🔄 Switch Exam Track", callback_data="sk_switch_exam_menu"),
            InlineKeyboardButton(f"🏷 Category ({cat}) / Target", callback_data="sk_profile_settings")
        ],
        [
            InlineKeyboardButton("🚀 Launch Practice Quiz", callback_data="cmd_quiz"),
            InlineKeyboardButton("⚡ Calc Booster", callback_data="cmd_calc_booster")
        ]
    ]
    return msg, InlineKeyboardMarkup(keyboard)


async def scorekeeper_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main entry point for /scorekeeper command and callback button."""
    if not await _sk_access_guard(update, context):
        return ConversationHandler.END

    user_id = update.effective_user.id
    msg, markup = await asyncio.to_thread(build_scorekeeper_dashboard_Valid, user_id)

    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(msg, reply_markup=markup, parse_mode="Markdown")
        except Exception:
            await update.callback_query.message.reply_text(msg, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, reply_markup=markup, parse_mode="Markdown")
    return ConversationHandler.END


async def quick_log_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    1-Line Quick Logger:
    /log 82 86 118 44 39 Testbook
    Automatically logs into user's active exam track.
    """
    if not await _sk_access_guard(update, context):
        return

    user_id = update.effective_user.id
    args = context.args if context.args else []
    sk_prof = await asyncio.to_thread(get_or_create_sk_profile, user_id)
    exam_code = sk_prof.get("active_exam", "SSC_CGL_MAINS")
    tmpl = EXAM_TEMPLATES.get(exam_code, EXAM_TEMPLATES["SSC_CGL_MAINS"])

    if not args:
        sec_names = " ".join([f"<{s[1].split('(')[0].strip()}>" for s in tmpl["sections"]])
        qual_str = f" <{tmpl['qualifying'][1].split('(')[0].strip()}>" if tmpl.get("qualifying") else ""
        await update.message.reply_text(
            f"⚡ **1-LINE QUICK SCORE LOGGER**\n"
            f"• • • ✧ • • •\n"
            f"Active Exam: `{tmpl['name']}`\n"
            f"Usage: `/log {sec_names}{qual_str} [Platform]`\n\n"
            f"Example:\n`/log 82 85 118 42 38 Testbook`\n\n"
            f"Or use **/scorekeeper** for the interactive Full/Sectional/Module logger!",
            parse_mode="Markdown"
        )
        return

    nums = []
    source_parts = []
    for token in args:
        clean_t = token.replace(",", "").strip()
        try:
            nums.append(float(clean_t))
        except ValueError:
            source_parts.append(token)

    if not nums:
        await update.message.reply_text("⚠️ Please include numeric scores. Example: `/log 82 85 118 42 38 Testbook`", parse_mode="Markdown")
        return

    source = " ".join(source_parts) if source_parts else "Mock Test"
    sec_defs = tmpl["sections"]
    qual_def = tmpl.get("qualifying")
    sectional_dict = {}
    qual_score = 0.0

    if len(nums) >= len(sec_defs):
        for idx, (s_key, _, s_max) in enumerate(sec_defs):
            sectional_dict[s_key] = min(s_max, max(0.0, nums[idx]))
        total_score = sum(sectional_dict.values())
        if qual_def and len(nums) > len(sec_defs):
            qual_score = min(qual_def[2], max(0.0, nums[len(sec_defs)]))
    else:
        total_score = min(tmpl["max_marks"], max(0.0, nums[0]))
        if qual_def and len(nums) >= 2:
            qual_score = min(qual_def[2], max(0.0, nums[1]))

    cat = sk_prof.get("category", "UR")
    passed_qual = True
    if qual_def and qual_score > 0:
        passed_qual = qual_score >= tmpl["qual_cutoffs"].get(cat, 18.0)

    today_str = get_ist_date_str()
    await asyncio.to_thread(
        insert_score_log,
        user_id, exam_code, today_str, source, "Moderate",
        total_score, tmpl["max_marks"], 0, 0.0,
        sectional_dict, qual_score, passed_qual,
        test_type="FULL", test_scope_name="Full Mock"
    )

    msg, markup = await asyncio.to_thread(build_scorekeeper_dashboard_Valid, user_id)
    await update.message.reply_text(
        f"✅ **Quick-Logged `{total_score}/{int(tmpl['max_marks'])}` ({source})!**\n\n{msg}",
        reply_markup=markup,
        parse_mode="Markdown"
    )


# ==============================================================================
# ➕ GUIDED CONVERSATION WIZARD (FULL / SECTIONAL / MODULE OPTIMIZED)
# ==============================================================================
async def sk_start_log_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    sk_prof = await asyncio.to_thread(get_or_create_sk_profile, user_id)
    active_exam = sk_prof.get("active_exam", "SSC_CGL_MAINS")

    keyboard = []
    row = []
    for code, info in EXAM_TEMPLATES.items():
        prefix = "🎯 " if code == active_exam else "📘 "
        row.append(InlineKeyboardButton(f"{prefix}{info['short']}", callback_data=f"sklog_exam_{code}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="sk_cancel_wizard")])

    await query.edit_message_text(
        "➕ **LOG MOCK / SECTIONAL / MODULE SCORE (STEP 1/6)**\n"
        "• • • ✧ • • •\n"
        "Select the **Target Exam** for this test:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return SK_SELECT_EXAM


async def sk_wizard_exam_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    exam_code = query.data.replace("sklog_exam_", "")
    context.user_data["sk_log_exam"] = exam_code
    asyncio.create_task(asyncio.to_thread(update_sk_profile, query.from_user.id, active_exam=exam_code))

    # For SSC CGL Mains and SSC CHSL Mains, offer Full vs Sectional vs Module test selection
    if exam_code in MAINS_SUBTEST_DEFINITIONS:
        keyboard = [
            [InlineKeyboardButton("🏆 Full Mock (All Sections + Computer)", callback_data="sklog_scope_FULL")],
            [
                InlineKeyboardButton("📂 Section-I (Maths + Reas)", callback_data="sklog_scope_SEC_1"),
                InlineKeyboardButton("📂 Section-II (English + GA)", callback_data="sklog_scope_SEC_2")
            ],
            [
                InlineKeyboardButton("📐 Module: Maths", callback_data="sklog_scope_MOD_MATH"),
                InlineKeyboardButton("🧩 Module: Reasoning", callback_data="sklog_scope_MOD_REAS")
            ],
            [
                InlineKeyboardButton("🔤 Module: English", callback_data="sklog_scope_MOD_ENG"),
                InlineKeyboardButton("🌍 Module: GA", callback_data="sklog_scope_MOD_GA")
            ],
            [InlineKeyboardButton("🖥️ Module: Computer Knowledge", callback_data="sklog_scope_MOD_COMP")],
            [InlineKeyboardButton("❌ Cancel", callback_data="sk_cancel_wizard")]
        ]
        tmpl = EXAM_TEMPLATES[exam_code]
        await query.edit_message_text(
            f"🎯 **Target Exam:** `{tmpl['name']}`\n"
            f"• • • ✧ • • •\n"
            f"📊 **SELECT TEST TYPE (STEP 2/6):**\n"
            f"Choose whether this is a Full Mock, Sectional Test, or Single Module Test:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return SK_SELECT_TEST_TYPE
    else:
        # Pre or Defence exams default to Full
        context.user_data["sk_test_type"] = "FULL"
        context.user_data["sk_test_scope_name"] = "Full Mock"
        return await _prompt_mock_source_step(query, exam_code)


async def sk_wizard_test_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    scope_key = query.data.replace("sklog_scope_", "")
    exam_code = context.user_data.get("sk_log_exam", "SSC_CGL_MAINS")

    sub_def = MAINS_SUBTEST_DEFINITIONS.get(exam_code, {}).get(scope_key)
    if not sub_def:
        scope_key = "FULL"
        sub_def = MAINS_SUBTEST_DEFINITIONS[exam_code]["FULL"]

    context.user_data["sk_test_type"] = scope_key
    context.user_data["sk_test_scope_name"] = sub_def["name"]
    return await _prompt_mock_source_step(query, exam_code)


async def _prompt_mock_source_step(query, exam_code: str):
    tmpl = EXAM_TEMPLATES[exam_code]
    keyboard = []
    row = []
    for src in MOCK_SOURCES:
        row.append(InlineKeyboardButton(f"🏷 {src}", callback_data=f"sklog_src_{src}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="sk_cancel_wizard")])

    await query.edit_message_text(
        f"🎯 **Selected Exam:** `{tmpl['name']}`\n\n"
        f"🏷 **SELECT MOCK PLATFORM / SOURCE (STEP 3/6):**",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return SK_SELECT_SOURCE


async def sk_wizard_source_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    src = query.data.replace("sklog_src_", "")
    if src == "Other":
        await query.edit_message_text(
            "✍️ **CUSTOM MOCK / TEST NAME**\n\nPlease type the name of the Mock / Test Series (e.g., `Testbook Live Sectional #02`):",
            parse_mode="Markdown"
        )
        return SK_CUSTOM_SOURCE

    context.user_data["sk_log_source"] = src
    return await _prompt_difficulty_step(query)


async def sk_wizard_custom_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["sk_log_source"] = update.message.text.strip()[:45] or "Mock Test"
    return await _prompt_difficulty_step(update)


async def _prompt_difficulty_step(target):
    keyboard = [
        [
            InlineKeyboardButton("🟢 Easy Shift", callback_data="sklog_diff_Easy"),
            InlineKeyboardButton("🟡 Moderate Shift", callback_data="sklog_diff_Moderate"),
            InlineKeyboardButton("🔴 Hard Shift", callback_data="sklog_diff_Hard")
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="sk_cancel_wizard")]
    ]
    text = (
        "📊 **PAPER DIFFICULTY LEVEL (STEP 4/6)**\n"
        "• • • ✧ • • •\n"
        "Select the difficulty level of this test/shift (used for Normalization Prediction):"
    )
    if hasattr(target, "edit_message_text"):
        await target.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await target.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return SK_SELECT_DIFF


async def sk_wizard_diff_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    diff = query.data.replace("sklog_diff_", "")
    context.user_data["sk_log_diff"] = diff

    now_ist = datetime.now(IST)
    today_s = now_ist.strftime("%Y-%m-%d")
    yest_s = (now_ist - timedelta(days=1)).strftime("%Y-%m-%d")

    keyboard = [
        [InlineKeyboardButton(f"📅 Today ({today_s})", callback_data=f"sklog_date_{today_s}")],
        [InlineKeyboardButton(f"🗓 Yesterday ({yest_s})", callback_data=f"sklog_date_{yest_s}")],
        [InlineKeyboardButton("✍️ Enter Custom Date (DD-MM-YYYY)", callback_data="sklog_date_custom")],
        [InlineKeyboardButton("❌ Cancel", callback_data="sk_cancel_wizard")]
    ]
    await query.edit_message_text(
        "📅 **SELECT MOCK / EXAM DATE (STEP 5/6):**",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return SK_SELECT_DATE


async def sk_wizard_date_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    d_val = query.data.replace("sklog_date_", "")
    if d_val == "custom":
        await query.edit_message_text(
            "📅 **CUSTOM EXAM DATE**\n\nPlease reply with the exam date in `DD-MM-YYYY` or `YYYY-MM-DD` format (e.g., `28-10-2026`):",
            parse_mode="Markdown"
        )
        return SK_CUSTOM_DATE

    context.user_data["sk_log_date"] = d_val
    return await _prompt_marks_entry(query, context)


async def sk_wizard_custom_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_d = update.message.text.strip()
    parsed_date = get_ist_date_str()
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            parsed_date = datetime.strptime(raw_d, fmt).strftime("%Y-%m-%d")
            break
        except ValueError:
            continue
    context.user_data["sk_log_date"] = parsed_date
    return await _prompt_marks_entry(update, context)


async def _prompt_marks_entry(target, context: ContextTypes.DEFAULT_TYPE):
    exam_code = context.user_data.get("sk_log_exam", "SSC_CGL_MAINS")
    test_type = context.user_data.get("sk_test_type", "FULL")

    if exam_code in MAINS_SUBTEST_DEFINITIONS and test_type in MAINS_SUBTEST_DEFINITIONS[exam_code]:
        sub_info = MAINS_SUBTEST_DEFINITIONS[exam_code][test_type]
        sec_list = sub_info["sections"]
        qual = sub_info.get("qualifying")
        max_marks = sub_info["max_marks"]
        scope_title = sub_info["name"]
    else:
        tmpl = EXAM_TEMPLATES[exam_code]
        sec_list = tmpl["sections"]
        qual = tmpl.get("qualifying")
        max_marks = tmpl["max_marks"]
        scope_title = tmpl["name"]

    sec_guide = ", ".join([f"**{s[1]}** (/{int(s[2])})" for s in sec_list])
    example_nums = " ".join([str(int(s[2] * 0.85)) for s in sec_list])
    qual_note = ""
    if qual and test_type == "FULL":
        qual_note = f"\n🖥️ Include **{qual[1]}** (/{int(qual[2])}) at the end!"
        example_nums += f" {int(qual[2] * 0.65)}"

    text = (
        f"✍️ **ENTER YOUR MARKS (STEP 6/6)**\n"
        f"• • • ✧ • • •\n"
        f"🎯 **Test:** `{scope_title}` (Max: `{int(max_marks)}` Marks)\n"
        f"📚 **Sections:** {sec_guide}{qual_note}\n\n"
        f"👉 **Option A (Sectional / Multi-Module):**\n"
        f"Send space-separated marks:\n"
        f"`{example_nums}`\n\n"
        f"👉 **Option B (Direct Total Score):**\n"
        f"Send just your total score (e.g., `{int(max_marks * 0.82)}`):"
    )
    if hasattr(target, "edit_message_text"):
        await target.edit_message_text(text, parse_mode="Markdown")
    else:
        await target.message.reply_text(text, parse_mode="Markdown")
    return SK_INPUT_MARKS


async def sk_wizard_marks_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = update.message.text.replace(",", " ").replace("/", " ").strip()
    tokens = raw_text.split()
    nums = []
    for t in tokens:
        try:
            nums.append(float(t))
        except ValueError:
            pass

    if not nums:
        await update.message.reply_text("⚠️ Invalid format! Please enter numeric marks (e.g., `82 85 118 42 38` or `327`):", parse_mode="Markdown")
        return SK_INPUT_MARKS

    exam_code = context.user_data.get("sk_log_exam", "SSC_CGL_MAINS")
    test_type = context.user_data.get("sk_test_type", "FULL")

    if exam_code in MAINS_SUBTEST_DEFINITIONS and test_type in MAINS_SUBTEST_DEFINITIONS[exam_code]:
        sub_info = MAINS_SUBTEST_DEFINITIONS[exam_code][test_type]
        sec_defs = sub_info["sections"]
        qual_def = sub_info.get("qualifying")
        max_marks = sub_info["max_marks"]
    else:
        tmpl = EXAM_TEMPLATES[exam_code]
        sec_defs = tmpl["sections"]
        qual_def = tmpl.get("qualifying")
        max_marks = tmpl["max_marks"]

    sectional_dict = {}
    qual_score = 0.0

    if len(nums) >= len(sec_defs):
        for idx, (s_key, _, s_max) in enumerate(sec_defs):
            sectional_dict[s_key] = min(s_max, max(-s_max, nums[idx]))
        total_score = sum(sectional_dict.values())
        if qual_def and len(nums) > len(sec_defs):
            qual_score = min(qual_def[2], max(0.0, nums[len(sec_defs)]))
    else:
        total_score = min(max_marks, max(-50.0, nums[0]))
        if qual_def and len(nums) >= 2:
            qual_score = min(qual_def[2], max(0.0, nums[1]))

    # If it is standalone Computer Module, total score is the qualifying score
    if test_type == "MOD_COMP":
        qual_score = total_score
        sectional_dict["comp"] = total_score

    context.user_data["sk_log_total"] = round(total_score, 2)
    context.user_data["sk_log_max_marks"] = max_marks
    context.user_data["sk_log_sectional"] = sectional_dict
    context.user_data["sk_log_qual"] = round(qual_score, 2)

    skip_btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Skip (0 Wrong Questions)", callback_data="sklog_wrong_skip")]
    ])
    await update.message.reply_text(
        f"✅ **Score Recorded:** `{round(total_score, 2)} / {int(max_marks)}`\n\n"
        f"❓ **How many total WRONG questions did you attempt in this test?**\n"
        f"*(Reply with a number like `8` to compute negative marks lost, or tap Skip below)*:",
        reply_markup=skip_btn,
        parse_mode="Markdown"
    )
    return SK_INPUT_WRONG


async def sk_wizard_wrong_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wrong_qs = 0
    if update.callback_query:
        await update.callback_query.answer()
        target_reply = update.callback_query.edit_message_text
        user_id = update.callback_query.from_user.id
    else:
        raw_w = update.message.text.strip()
        if raw_w.isdigit():
            wrong_qs = int(raw_w)
        target_reply = update.message.reply_text
        user_id = update.effective_user.id

    exam_code = context.user_data.get("sk_log_exam", "SSC_CGL_MAINS")
    test_type = context.user_data.get("sk_test_type", "FULL")
    tmpl = EXAM_TEMPLATES[exam_code]
    neg_rate = float(tmpl.get("neg_mark", 1.0))
    neg_lost = round(wrong_qs * neg_rate, 2)

    sk_prof = await asyncio.to_thread(get_or_create_sk_profile, user_id)
    cat = sk_prof.get("category", "UR")
    qual_score = context.user_data.get("sk_log_qual", 0.0)
    passed_qual = True
    if tmpl.get("qualifying") and qual_score > 0:
        passed_qual = qual_score >= tmpl["qual_cutoffs"].get(cat, 18.0)

    max_marks = context.user_data.get("sk_log_max_marks", tmpl["max_marks"])
    scope_name = context.user_data.get("sk_test_scope_name", "Full Mock")

    await asyncio.to_thread(
        insert_score_log,
        user_id=user_id,
        exam_code=exam_code,
        exam_date=context.user_data.get("sk_log_date", get_ist_date_str()),
        mock_source=context.user_data.get("sk_log_source", "Mock Test"),
        difficulty=context.user_data.get("sk_log_diff", "Moderate"),
        total_score=context.user_data.get("sk_log_total", 0.0),
        max_marks=max_marks,
        wrong_qs=wrong_qs,
        negative_lost=neg_lost,
        sectional_dict=context.user_data.get("sk_log_sectional", {}),
        qualifying_score=qual_score,
        passed_qualifying=passed_qual,
        test_type=test_type,
        test_scope_name=scope_name
    )

    msg, markup = await asyncio.to_thread(build_scorekeeper_dashboard_Valid, user_id)
    await target_reply(
        f"🎉 **{scope_name.upper()} SCORE SAVED!** 🎉\n\n{msg}",
        reply_markup=markup,
        parse_mode="Markdown"
    )
    return ConversationHandler.END


async def sk_edit_target_post_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🎖️ **SET YOUR DREAM POST & TARGET**\n"
        "• • • ✧ • • •\n"
        "Please reply with your target post (e.g., `ASO in CSS / Income Tax Inspector 4600 GP`):",
        parse_mode="Markdown"
    )
    return SK_EDIT_TARGET_POST


async def sk_save_target_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    new_post = update.message.text.strip()[:60] or "Inspector / ASO (4600 GP)"
    await asyncio.to_thread(update_sk_profile, user_id, target_post=new_post)
    msg, markup = await asyncio.to_thread(build_scorekeeper_dashboard_Valid, user_id)
    await update.message.reply_text(
        f"✅ **Dream Post Updated to:** `{new_post}`\n\n{msg}",
        reply_markup=markup,
        parse_mode="Markdown"
    )
    return ConversationHandler.END


# ==============================================================================
# 📋 1-TAP SCORE VAULT, DETAILED PREDICTION & CALLBACK ROUTER
# ==============================================================================
async def scorekeeper_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id

    if not await _sk_access_guard(update, context):
        return

    # 1. Return to Main Scorekeeper Dashboard
    if data == "sk_home":
        await scorekeeper_command(update, context)
        return

    # 2. One-Tap View All Scores Vault (Paginated)
    elif data.startswith("sk_view_all_"):
        await query.answer()
        page = int(data.replace("sk_view_all_", "") or 0)
        sk_prof = await asyncio.to_thread(get_or_create_sk_profile, user_id)
        exam_code = sk_prof.get("active_exam", "SSC_CGL_MAINS")
        stats = await asyncio.to_thread(analyze_user_scores, user_id, exam_code)

        if not stats.get("has_data"):
            btn = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Log First Score", callback_data="sk_start_log")],
                [InlineKeyboardButton("🔙 Back to Dashboard", callback_data="sk_home")]
            ])
            await query.edit_message_text(
                f"📋 **ALL LOGGED SCORES — {stats['exam_name']}**\n\nNo scores logged yet for this exam track!",
                reply_markup=btn,
                parse_mode="Markdown"
            )
            return

        logs_desc = stats["logs_desc"]
        total_logs = len(logs_desc)
        total_pages = max(1, math.ceil(total_logs / SCORES_PER_PAGE))
        page = max(0, min(page, total_pages - 1))
        slice_logs = logs_desc[page * SCORES_PER_PAGE : (page + 1) * SCORES_PER_PAGE]

        lines = [
            f"📋 **ALL LOGGED SCORES — {stats['exam_name'].upper()}**",
            f"• • • ✧ • • •"
        ]

        for idx, l in enumerate(slice_logs):
            abs_idx = page * SCORES_PER_PAGE + idx
            prev_sc = float(logs_desc[abs_idx + 1]["total_score"]) if (abs_idx + 1) < total_logs else float(l["total_score"])
            delta = round(float(l["total_score"]) - prev_sc, 1)
            d_str = f"+{delta} 📈" if delta > 0 else (f"{delta} 📉" if delta < 0 else "0.0 ⚖️")

            sec_map = l.get("sectional") or {}
            sec_str = " ".join([f"{k.upper()[:1]}:{v:g}" for k, v in sec_map.items()])
            qual_s = float(l.get("qualifying_score") or 0)
            qual_tag = f" | 🖥️ C:{qual_s:g}" if qual_s > 0 else ""

            scope_badge = f"[{l.get('test_scope_name', 'Full')}]"

            lines.append(
                f"**#{total_logs - abs_idx}.** 📅 `{l['exam_date']}` {scope_badge}\n"
                f"   🏷 **{l['mock_source']}** ({l['difficulty'][:3]})\n"
                f"   🎯 **{l['total_score']} / {int(l['max_marks'])}** (`{d_str}`) | {sec_str or 'Total'}{qual_tag}"
            )

        lines.append("• • • ✧ • • •")
        lines.append(
            f"🏆 **Best Full:** `{stats['best_score']}` | 📊 **Avg:** `{stats['avg_score']}` | "
            f"📋 **Total Tests:** `{total_logs}`"
        )

        keyboard = []
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"sk_view_all_{page - 1}"))
        nav_row.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="sk_noop"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"sk_view_all_{page + 1}"))
        keyboard.append(nav_row)

        keyboard.append([
            InlineKeyboardButton("➕ Log New Score", callback_data="sk_start_log"),
            InlineKeyboardButton("📄 Download PDF Report", callback_data="sk_download_pdf")
        ])
        keyboard.append([
            InlineKeyboardButton("🗑 Delete Last Entry", callback_data="sk_del_last_confirm"),
            InlineKeyboardButton("🔙 Dashboard", callback_data="sk_home")
        ])

        await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    # 3. Delete Last Entry Confirmation & Execution
    elif data == "sk_del_last_confirm":
        await query.answer()
        btn = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Yes, Delete Last Score", callback_data="sk_del_last_exec"),
                InlineKeyboardButton("❌ Cancel", callback_data="sk_view_all_0")
            ]
        ])
        await query.edit_message_text(
            "⚠️ **CONFIRM DELETE LAST SCORE**\n\nAre you sure you want to delete your most recently logged mock/sectional score?",
            reply_markup=btn,
            parse_mode="Markdown"
        )
        return

    elif data == "sk_del_last_exec":
        sk_prof = await asyncio.to_thread(get_or_create_sk_profile, user_id)
        deleted = await asyncio.to_thread(delete_last_score_log, user_id, sk_prof["active_exam"])
        await query.answer("🗑 Last score deleted!" if deleted else "ℹ️ No score to delete.", show_alert=True)
        await scorekeeper_command(update, context)
        return

    # 4. Detailed AI Prediction & Subject Bridge
    elif data == "sk_detailed_pred":
        await query.answer()
        sk_prof = await asyncio.to_thread(get_or_create_sk_profile, user_id)
        stats = await asyncio.to_thread(analyze_user_scores, user_id, sk_prof["active_exam"])

        if not stats.get("has_data"):
            await query.answer("⚠️ Log at least 1 score first!", show_alert=True)
            return

        sec_lines = []
        for s in stats["sectional_breakdown"]:
            status_ic = "🟢" if s["pct"] >= 78 else ("🟡" if s["pct"] >= 62 else "🔴")
            sec_lines.append(f"• {status_ic} **{s['label']}:** `{s['avg']} / {int(s['max'])}` (`{s['pct']}%` across {s['count']} tests)")

        qual_block = ""
        if stats.get("qual_info"):
            qi = stats["qual_info"]
            qual_block = f"\n🖥️ **Computer Qualifying Gate:** `{qi['status']}`\n"

        vel_sign = f"+{stats['growth_per_mock']}" if stats["growth_per_mock"] >= 0 else f"{stats['growth_per_mock']}"

        pred_msg = (
            f"🔮 **AI DEEP PREDICTION & ANALYTICS REPORT** 🔮\n"
            f"• • • ✧ • • •\n"
            f"🎯 **Exam:** `{stats['exam_name']}` | **Category:** `{stats['category']}`\n"
            f"📊 **Tests Mix:** Full: `{stats['full_mock_count']}` | Sec: `{stats['sectional_count']}` | Mod: `{stats['module_count']}`\n"
            f"📈 **EMA True Potential:** `{stats['ema_score']} / {int(stats['max_marks'])}`\n"
            f"🚀 **Projected Full Exam Score:** `{stats['predicted_final_score']} / {int(stats['max_marks'])}`\n"
            f"⚡ **Growth Velocity:** `{vel_sign} marks / test`\n"
            f"⚖️ **Consistency Index:** `{stats['stability_pct']}%` (Std Dev: `±{stats['std_dev']}`)\n"
            f"👥 **Live Community Standing:** `{stats['comm_percentile']}%ile` ({stats['comm_peers']} peers)\n"
            f"• • • ✧ • • •\n"
            f"📊 **SECTIONAL / MODULE EFFICIENCY:**\n" + "\n".join(sec_lines) +
            f"{qual_block}\n"
            f"• • • ✧ • • •\n"
            f"❌ **NEGATIVE MARKING BLEED ANALYSIS:**\n"
            f"• Avg Wrong Attempts: `{stats['avg_wrong']} Qs/test` (`-{stats['avg_neg_lost']} marks lost`)\n"
            f"• **Instant ROI Boost:** Eliminating wild guesses recovers **+{stats['recoverable_marks']} marks** immediately.\n\n"
            f"🎖️ **POST ALLOTMENT VERDICT:**\n"
            f"{stats['post_zone']}\n"
            f"👉 *{stats['next_goal_text']}*"
        )

        practice_cb = "cmd_quiz"
        practice_label = "🚀 Practice Weakest Section"
        if stats.get("weakest_sec"):
            wk = stats["weakest_sec"]["key"]
            if wk == "math":
                practice_cb = "cmd_calc_booster"
                practice_label = "⚡ Boost Maths Speed (/booster)"
            elif wk == "eng":
                practice_cb = "qsubj_english"
                practice_label = "🔤 Practice English Quiz"
            elif wk == "ga":
                practice_cb = "qsubj_gk"
                practice_label = "🌍 Practice GK Quiz"

        keyboard = [
            [InlineKeyboardButton(practice_label, callback_data=practice_cb)],
            [
                InlineKeyboardButton("📄 Download PDF Dossier", callback_data="sk_download_pdf"),
                InlineKeyboardButton("📋 View Score Vault", callback_data="sk_view_all_0")
            ],
            [InlineKeyboardButton("🔙 Back to Scorekeeper Dashboard", callback_data="sk_home")]
        ]
        await query.edit_message_text(pred_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    # 5. Switch Active Exam Track
    elif data == "sk_switch_exam_menu":
        await query.answer()
        sk_prof = await asyncio.to_thread(get_or_create_sk_profile, user_id)
        curr = sk_prof.get("active_exam", "SSC_CGL_MAINS")

        keyboard = []
        row = []
        for code, info in EXAM_TEMPLATES.items():
            mark = "✅ " if code == curr else "📘 "
            row.append(InlineKeyboardButton(f"{mark}{info['short']}", callback_data=f"sk_setexam_{code}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("🔙 Back to Dashboard", callback_data="sk_home")])

        await query.edit_message_text(
            "🔄 **SWITCH ACTIVE EXAM TRACK**\n"
            "• • • ✧ • • •\n"
            "Each exam has its own separate score vault, sectional scheme, and cutoff predictor. Select an exam:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return

    elif data.startswith("sk_setexam_"):
        exam_code = data.replace("sk_setexam_", "")
        await asyncio.to_thread(update_sk_profile, user_id, active_exam=exam_code)
        await query.answer(f"Switched to {EXAM_TEMPLATES[exam_code]['short']}!")
        await scorekeeper_command(update, context)
        return

    # 6. Category & Target Post Settings
    elif data == "sk_profile_settings":
        await query.answer()
        sk_prof = await asyncio.to_thread(get_or_create_sk_profile, user_id)
        curr_cat = sk_prof.get("category", "UR")

        cat_btns = []
        row = []
        for c in CATEGORIES:
            lbl = f"✅ {c}" if c == curr_cat else c
            row.append(InlineKeyboardButton(lbl, callback_data=f"sk_setcat_{c}"))
            if len(row) == 4:
                cat_btns.append(row)
                row = []
        if row:
            cat_btns.append(row)

        cat_btns.append([InlineKeyboardButton("🎖️ Edit Dream Post / Target", callback_data="sk_edit_post")])
        cat_btns.append([InlineKeyboardButton("🔙 Back to Dashboard", callback_data="sk_home")])

        await query.edit_message_text(
            f"⚙️ **SCOREKEEPER PROFILE SETTINGS**\n"
            f"• • • ✧ • • •\n"
            f"• **Current Category:** `{curr_cat}`\n"
            f"• **Dream Post:** `{sk_prof.get('target_post')}`\n\n"
            f"Tap your category below to calibrate cutoff & post predictions:",
            reply_markup=InlineKeyboardMarkup(cat_btns),
            parse_mode="Markdown"
        )
        return

    elif data.startswith("sk_setcat_"):
        new_cat = data.replace("sk_setcat_", "")
        await asyncio.to_thread(update_sk_profile, user_id, category=new_cat)
        await query.answer(f"Category updated to {new_cat}!")
        await scorekeeper_command(update, context)
        return

    # 7. Generate & Deliver WeasyPrint PDF Report
    elif data == "sk_download_pdf":
        await query.answer("⏳ Compiling Official Scorekeeper PDF...", show_alert=False)
        wait_msg = await query.edit_message_text(
            "⏳ **Generating Official Scorekeeper & AI Prediction PDF Report...**\n"
            "Building SVG trajectory charts, sectional meters, and chronological score ledger...",
            parse_mode="Markdown"
        )

        from app.telegram_bot import log_pdf_generation_event
        sk_prof = await asyncio.to_thread(get_or_create_sk_profile, user_id)
        exam_code = sk_prof.get("active_exam", "SSC_CGL_MAINS")
        asyncio.create_task(asyncio.to_thread(log_pdf_generation_event, user_id, f"scorekeeper_{exam_code.lower()}"))

        pdf_path = await asyncio.to_thread(generate_scorekeeper_pdf, user_id, exam_code)

        if pdf_path == "NO_LOGS":
            nav = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Log First Score", callback_data="sk_start_log")],
                [InlineKeyboardButton("🔙 Back to Dashboard", callback_data="sk_home")]
            ])
            await wait_msg.edit_text("ℹ️ **No scores logged yet!** Please log at least one test score to generate a PDF report.", reply_markup=nav, parse_mode="Markdown")
            return

        if pdf_path and os.path.exists(pdf_path) and not str(pdf_path).startswith("ERROR"):
            u = await asyncio.to_thread(get_user_profile, user_id) or {}
            stats = await asyncio.to_thread(analyze_user_scores, user_id, exam_code)
            caption = (
                f"📄 **OFFICIAL SCOREKEEPER & AI PREDICTION REPORT**\n"
                f"• • • ✧ • • •\n"
                f"👤 **Candidate:** {u.get('full_name', 'Scholar')} (`{u.get('student_id', user_id)}`)\n"
                f"🎯 **Exam:** `{stats['exam_name']}` ({stats['category']})\n"
                f"🔮 **Projected Score:** `{stats['predicted_final_score']} / {int(stats['max_marks'])}`\n"
                f"🎖️ **Projected Zone:** {stats['post_zone']}"
            )
            with open(pdf_path, "rb") as doc:
                await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=doc,
                    filename=os.path.basename(pdf_path),
                    caption=caption,
                    parse_mode="Markdown"
                )
            try:
                await wait_msg.delete()
            except Exception:
                pass

            msg, markup = await asyncio.to_thread(build_scorekeeper_dashboard_Valid, user_id)
            await context.bot.send_message(chat_id=query.message.chat_id, text=msg, reply_markup=markup, parse_mode="Markdown")
        else:
            nav = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Dashboard", callback_data="sk_home")]])
            await wait_msg.edit_text(f"⚠️ **PDF Generation Error:**\n`{pdf_path}`", reply_markup=nav, parse_mode="Markdown")
        return

    elif data == "sk_noop":
        await query.answer()
        return


def get_scorekeeper_conv_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(sk_start_log_wizard, pattern="^sk_start_log$"),
            CallbackQueryHandler(sk_edit_target_post_prompt, pattern="^sk_edit_post$")
        ],
        states={
            SK_SELECT_EXAM: [CallbackQueryHandler(sk_wizard_exam_selected, pattern="^sklog_exam_")],
            SK_SELECT_TEST_TYPE: [CallbackQueryHandler(sk_wizard_test_type_selected, pattern="^sklog_scope_")],
            SK_SELECT_SOURCE: [CallbackQueryHandler(sk_wizard_source_selected, pattern="^sklog_src_")],
            SK_CUSTOM_SOURCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, sk_wizard_custom_source)],
            SK_SELECT_DIFF: [CallbackQueryHandler(sk_wizard_diff_selected, pattern="^sklog_diff_")],
            SK_SELECT_DATE: [CallbackQueryHandler(sk_wizard_date_selected, pattern="^sklog_date_")],
            SK_CUSTOM_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, sk_wizard_custom_date)],
            SK_INPUT_MARKS: [MessageHandler(filters.TEXT & ~filters.COMMAND, sk_wizard_marks_received)],
            SK_INPUT_WRONG: [
                CallbackQueryHandler(sk_wizard_wrong_received, pattern="^sklog_wrong_skip$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, sk_wizard_wrong_received)
            ],
            SK_EDIT_TARGET_POST: [MessageHandler(filters.TEXT & ~filters.COMMAND, sk_save_target_post)]
        },
        fallbacks=[
            CallbackQueryHandler(scorekeeper_command, pattern="^sk_cancel_wizard$"),
            CommandHandler("scorekeeper", scorekeeper_command),
            CommandHandler("cancel", scorekeeper_command)
        ],
        allow_reentry=True,
        per_user=True,
        per_chat=True
    )


def register_scorekeeper_handlers(app):
    """Initializes Scorekeeper DB tables and registers all commands/callbacks cleanly."""
    init_scorekeeper_db()
    app.add_handler(get_scorekeeper_conv_handler())
    app.add_handler(CommandHandler("scorekeeper", scorekeeper_command))
    app.add_handler(CommandHandler("log", quick_log_command))
    app.add_handler(CallbackQueryHandler(scorekeeper_callback_router, pattern="^sk_"))