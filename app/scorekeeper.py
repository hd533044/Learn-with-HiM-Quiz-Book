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

SCORES_PER_PAGE = 5
CATEGORIES = ["UR", "OBC", "EWS", "SC", "ST", "ESM", "PwD"]

# Exact requested mock platforms
MOCK_SOURCES = [
    ("Testbook", "Testbook"),
    ("Oliveboard", "Oliveboard"),
    ("PTS (Parmar Test Series)", "PTS"),
    ("The Pundits", "The Pundits"),
    ("RBE Mock", "RBE Mock"),
    ("English Madhyam", "English Madhyam"),
    ("Other Platform", "Other")
]

DIFFICULTIES = ["Easy", "Moderate", "Hard"]

# ==============================================================================
# 🎯 OFFICIAL MAINS SCHEMES & SUBTEST DEFINITIONS
# ==============================================================================
MAINS_SUBTEST_DEFINITIONS = {
    "SSC_CGL_MAINS": {
        "FULL": {
            "name": "Full Mock (Sec I + Sec II + Computer)",
            "short": "Full Mock",
            "badge": "Full",
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
            "name": "Section-I (Maths + Reasoning)",
            "short": "Section-I",
            "badge": "Sec-I",
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
            "name": "Section-II (English + GA)",
            "short": "Section-II",
            "badge": "Sec-II",
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
            "name": "Module-I: Maths (30Q)",
            "short": "Maths",
            "badge": "Maths Mod",
            "max_marks": 90.0,
            "total_qs": 30,
            "neg_mark": 1.0,
            "sections": [("math", "Maths (30Q)", 90.0)],
            "qualifying": None
        },
        "MOD_REAS": {
            "name": "Module-II: Reasoning (30Q)",
            "short": "Reasoning",
            "badge": "Reas Mod",
            "max_marks": 90.0,
            "total_qs": 30,
            "neg_mark": 1.0,
            "sections": [("reas", "Reasoning (30Q)", 90.0)],
            "qualifying": None
        },
        "MOD_ENG": {
            "name": "Module-III: English (45Q)",
            "short": "English",
            "badge": "English Mod",
            "max_marks": 135.0,
            "total_qs": 45,
            "neg_mark": 1.0,
            "sections": [("eng", "English (45Q)", 135.0)],
            "qualifying": None
        },
        "MOD_GA": {
            "name": "Module-IV: GA (25Q)",
            "short": "GA",
            "badge": "GA Mod",
            "max_marks": 75.0,
            "total_qs": 25,
            "neg_mark": 1.0,
            "sections": [("ga", "GA (25Q)", 75.0)],
            "qualifying": None
        },
        "MOD_COMP": {
            "name": "Module-V: Computer Knowledge (20Q)",
            "short": "Computer",
            "badge": "Comp Mod",
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
            "badge": "Full",
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
            "name": "Section-I (Maths + Reasoning)",
            "short": "Section-I",
            "badge": "Sec-I",
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
            "name": "Section-II (English + GA)",
            "short": "Section-II",
            "badge": "Sec-II",
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
            "name": "Module-I: Maths (30Q)",
            "short": "Maths",
            "badge": "Maths Mod",
            "max_marks": 90.0,
            "total_qs": 30,
            "neg_mark": 1.0,
            "sections": [("math", "Maths (30Q)", 90.0)],
            "qualifying": None
        },
        "MOD_REAS": {
            "name": "Module-II: Reasoning (30Q)",
            "short": "Reasoning",
            "badge": "Reas Mod",
            "max_marks": 90.0,
            "total_qs": 30,
            "neg_mark": 1.0,
            "sections": [("reas", "Reasoning (30Q)", 90.0)],
            "qualifying": None
        },
        "MOD_ENG": {
            "name": "Module-III: English (40Q)",
            "short": "English",
            "badge": "English Mod",
            "max_marks": 120.0,
            "total_qs": 40,
            "neg_mark": 1.0,
            "sections": [("eng", "English (40Q)", 120.0)],
            "qualifying": None
        },
        "MOD_GA": {
            "name": "Module-IV: GA (20Q)",
            "short": "GA",
            "badge": "GA Mod",
            "max_marks": 60.0,
            "total_qs": 20,
            "neg_mark": 1.0,
            "sections": [("ga", "GA (20Q)", 60.0)],
            "qualifying": None
        },
        "MOD_COMP": {
            "name": "Module-V: Computer Knowledge (15Q)",
            "short": "Computer",
            "badge": "Comp Mod",
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
        "name": "SSC CGL Mains",
        "short": "CGL Mains",
        "max_marks": 390.0,
        "neg_mark": 1.0,
        "sections": [
            ("math", "Maths (30Q)", 90.0),
            ("reas", "Reasoning (30Q)", 90.0),
            ("eng", "English (45Q)", 135.0),
            ("ga", "GA (25Q)", 75.0)
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
            ("ga", "GA", 50.0)
        ],
        "qualifying": None,
        "cutoffs": {"UR": 145.0, "OBC": 138.0, "EWS": 136.0, "SC": 120.0, "ST": 110.0, "ESM": 95.0, "PwD": 100.0}
    },
    "SSC_CHSL_MAINS": {
        "name": "SSC CHSL Mains",
        "short": "CHSL Mains",
        "max_marks": 360.0,
        "neg_mark": 1.0,
        "sections": [
            ("math", "Maths (30Q)", 90.0),
            ("reas", "Reasoning (30Q)", 90.0),
            ("eng", "English (40Q)", 120.0),
            ("ga", "GA (20Q)", 60.0)
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
            ("ga", "GA", 50.0)
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
            ("ga", "GA", 50.0)
        ],
        "qualifying": None,
        "cutoffs": {"UR": 138.0, "OBC": 131.0, "EWS": 130.0, "SC": 112.0, "ST": 105.0, "ESM": 85.0, "PwD": 90.0}
    },
    "SSC_CPO_MAINS": {
        "name": "SSC CPO Paper-II (Mains)",
        "short": "CPO Mains",
        "max_marks": 200.0,
        "neg_mark": 0.25,
        "sections": [("eng", "English Language", 200.0)],
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
        "name": "Defence / Police (CAPF, DP HCM)",
        "short": "Defence",
        "max_marks": 100.0,
        "neg_mark": 0.25,
        "sections": [
            ("math", "Maths", 25.0),
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
# 🗄️ DATABASE CRUD OPERATIONS
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
# 🔮 AI PREDICTION & ANALYTICS ENGINE
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

    logs_asc = list(reversed(logs_desc))

    full_logs = [l for l in logs_asc if l.get("test_type", "FULL") == "FULL"]
    sec_logs = [l for l in logs_asc if str(l.get("test_type", "")).startswith("SEC")]
    mod_logs = [l for l in logs_asc if str(l.get("test_type", "")).startswith("MOD")]

    eval_pool = full_logs if full_logs else logs_asc
    scores = [float(l["total_score"]) for l in eval_pool]
    n = len(scores)
    max_marks = float(eval_pool[-1]["max_marks"]) if eval_pool else float(tmpl["max_marks"])

    latest_log = logs_desc[0]
    latest_score = float(latest_log["total_score"])

    # Scope-aware Delta Calculation
    curr_type = latest_log.get("test_type", "FULL")
    matching_older = [l for l in logs_desc[1:] if l.get("test_type", "FULL") == curr_type]
    if matching_older:
        prev_score = float(matching_older[0]["total_score"])
        last_delta = round(latest_score - prev_score, 1)
        has_matched_delta = True
    else:
        last_delta = 0.0
        has_matched_delta = False

    best_score = max([float(l["total_score"]) for l in eval_pool])
    lowest_score = min([float(l["total_score"]) for l in eval_pool])
    avg_score = round(sum(scores) / n, 2)

    # 1. EMA Score Projection
    alpha = 0.38
    ema = scores[0]
    for s in scores[1:]:
        ema = alpha * s + (1.0 - alpha) * ema
    ema_score = round(ema, 2)

    # 2. Shift Normalization
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

    # 3. Growth & Stability
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

    # 4. Negative Bleed
    total_logs_count = len(logs_desc)
    avg_wrong = round(sum(int(l.get("wrong_qs") or 0) for l in logs_desc) / total_logs_count, 1)
    avg_neg_lost = round(sum(float(l.get("negative_lost") or 0.0) for l in logs_desc) / total_logs_count, 2)
    recoverable_marks = round(avg_neg_lost * 0.45, 2)

    # 5. Sectional Performance
    sec_totals = {k: 0.0 for k, _, _ in tmpl["sections"]}
    sec_counts = {k: 0 for k, _, _ in tmpl["sections"]}
    for l in logs_desc:
        sec_map = l.get("sectional") or {}
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

    # 6. Computer Qualifying Check
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
            q_status = "Not Logged"
        elif q_avg >= cpt_cut:
            q_status = f"CPT Safe ({q_avg}/{int(q_max)})"
        elif q_avg >= min_cut:
            q_status = f"Borderline CPT ({q_avg}/{int(q_max)})"
        else:
            q_status = f"Danger: Below Cutoff ({q_avg}/{int(q_max)})"
        qual_info = {
            "label": q_label,
            "avg": q_avg,
            "max": q_max,
            "min_cutoff": min_cut,
            "cpt_cutoff": cpt_cut,
            "status": q_status,
            "attempts": len(q_scores)
        }

    # 7. Cutoffs & Post Prediction
    projected_full_score = predicted_final_score
    if not full_logs and max_marks < tmpl["max_marks"]:
        projected_full_score = round((predicted_final_score / max_marks) * tmpl["max_marks"], 1)

    if exam_code == "SSC_CGL_MAINS":
        c_map = tmpl["cutoffs"].get(category, tmpl["cutoffs"]["UR"])
        if projected_full_score >= c_map["top_aso"]:
            post_zone = "Top Rank Zone (ASO MEA / ITI / CSS)"
            next_goal_text = f"+{round(projected_full_score - c_map['top_aso'], 1)} marks above Top ASO cutoff."
        elif projected_full_score >= c_map["gp_4600"]:
            post_zone = "4600 GP Inspector Zone (GST / PO / Examiner / ASO)"
            next_goal_text = f"+{round(c_map['top_aso'] - projected_full_score, 1)} marks needed for Top ASO."
        elif projected_full_score >= c_map["gp_4200"]:
            post_zone = "4200 GP Zone (Div. Accountant / Sub-Inspector)"
            next_goal_text = f"+{round(c_map['gp_4600'] - projected_full_score, 1)} marks needed for 4600 GP."
        elif projected_full_score >= c_map["gp_2800"]:
            post_zone = "2800 GP Zone (Auditor / Jr. Accountant)"
            next_goal_text = f"+{round(c_map['gp_4600'] - projected_full_score, 1)} marks needed for 4600 GP."
        elif projected_full_score >= c_map["pa_sa"]:
            post_zone = "2400 GP Selection Zone (Tax Assistant / PA / SA)"
            next_goal_text = f"+{round(c_map['gp_4600'] - projected_full_score, 1)} marks needed for 4600 GP Inspector."
        else:
            post_zone = f"Below Safe Cutoff ({category} Safe: {c_map['pa_sa']})"
            next_goal_text = f"+{round(c_map['pa_sa'] - projected_full_score, 1)} marks needed for minimum cutoff."
        base_cutoff = c_map["pa_sa"]
        dream_cutoff = c_map["gp_4600"]
    else:
        base_cutoff = float(tmpl["cutoffs"].get(category, 140.0))
        dream_cutoff = round(min(tmpl["max_marks"], base_cutoff * 1.08), 1)
        margin = round(projected_full_score - base_cutoff, 1)
        if margin >= 12.0:
            post_zone = f"High Merit Safe (+{margin} above cutoff)"
            next_goal_text = "Strong rank buffer maintained."
        elif margin >= 0.0:
            post_zone = f"Selection Zone (+{margin} above cutoff)"
            next_goal_text = f"+{round(12.0 - margin, 1)} marks needed for top-rank safety."
        else:
            post_zone = f"Below Cutoff (Cutoff: {base_cutoff})"
            next_goal_text = f"+{abs(margin)} marks needed to clear cutoff."

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
        "has_matched_delta": has_matched_delta,
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
# 📄 WEASYPRINT PDF GENERATOR
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
                    <td><b>{clean_str_xml(qi['label'])} (Qualifying)</b></td>
                    <td style='text-align:center;'>{qi['avg']} / {int(qi['max'])}</td>
                    <td colspan='2'><b>{clean_str_xml(qi['status'])}</b></td>
                </tr>
            """)

        ledger_rows_html = []
        logs_desc = stats["logs_desc"]
        for i, l in enumerate(logs_desc):
            sec_d = l.get("sectional") or {}
            sec_parts = [f"{k.upper()[:1]}:{v}" for k, v in sec_d.items()]
            if float(l.get("qualifying_score") or 0) > 0:
                sec_parts.append(f"C:{l['qualifying_score']}")
            sec_summary = " | ".join(sec_parts) if sec_parts else "Total"
            scope_name = l.get("test_scope_name") or "Test"

            ledger_rows_html.append(f"""
                <tr>
                    <td style='text-align:center;'>{len(logs_desc) - i}</td>
                    <td><b>{clean_str_xml(l['exam_date'])}</b><br/><small style='color:#0284C7;'>{clean_str_xml(scope_name)}</small></td>
                    <td>{clean_str_xml(l['mock_source'])} ({clean_str_xml(l['difficulty'])})</td>
                    <td style='text-align:center; font-weight:bold; color:#1E3A8A;'>{l['total_score']} / {int(l['max_marks'])}</td>
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
            <div class='wm-text' style='top: 52%; left: 18%;'>Scorekeeper Report</div>
            <div class='wm-text' style='top: 58%; left: 62%;'>Quiz with HiM</div>
        </div>

        <table class='header-table'>
            <tr>
                <td style='width:14%; text-align:left;'>{left_logo_html}</td>
                <td class='header-title'>Quiz with HiM — Scorekeeper Dossier<div class='sub-title'>Official Marks Tracker & Prediction • Generated: {now_str}</div></td>
                <td style='width:14%; text-align:right;'>{right_logo_html}</td>
            </tr>
        </table>

        <table class='data-table'>
            <tr>
                <td><b>Candidate:</b> {full_name} ({sid})</td>
                <td><b>Category:</b> {category}</td>
                <td><b>Target Exam:</b> {exam_name}</td>
                <td><b>Target Post:</b> {target_post}</td>
            </tr>
        </table>

        <table class='data-table'>
            <tr>
                <td class='kpi-card'><div style='font-size:9px; color:#64748B;'>PROJECTED SCORE</div><div style='font-size:15px; font-weight:bold; color:#0284C7;'>{stats['predicted_final_score']} / {int(max_marks)}</div></td>
                <td class='kpi-card'><div style='font-size:9px; color:#64748B;'>TESTS LOGGED</div><div style='font-size:12px; font-weight:bold; color:#1E3A8A;'>Full: {stats['full_mock_count']} | Sec: {stats['sectional_count']} | Mod: {stats['module_count']}</div></td>
                <td class='kpi-card'><div style='font-size:9px; color:#64748B;'>BEST / AVG</div><div style='font-size:15px; font-weight:bold; color:#16A34A;'>{stats['best_score']} / {stats['avg_score']}</div></td>
                <td class='kpi-card'><div style='font-size:9px; color:#64748B;'>PERCENTILE</div><div style='font-size:15px; font-weight:bold; color:#0F172A;'>{stats['comm_percentile']}%ile</div></td>
            </tr>
        </table>

        <div class='pred-box'>
            <div style='font-size:12px; font-weight:bold; color:#0369A1; margin-bottom:4px;'>AI SELECTION ANALYSIS</div>
            <div><b>Status:</b> {clean_str_xml(stats['post_zone'])}</div>
            <div><b>Target:</b> {clean_str_xml(stats['next_goal_text'])}</div>
        </div>

        <h3>SCORE TRAJECTORY</h3>
        <div style='text-align:center; border:0.5px solid #CBD5E1; padding:8px; background:#FFFFFF; margin-bottom:10px;'>
            {trajectory_svg}
        </div>

        <h3>SECTIONAL & MODULE BREAKDOWN</h3>
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

        <h3>TEST LEDGER ({stats['total_mocks']} Tests)</h3>
        <table class='data-table'>
            <thead>
                <tr>
                    <th style='width:6%; text-align:center;'>#</th>
                    <th style='width:18%;'>Date & Scope</th>
                    <th style='width:22%;'>Source & Shift</th>
                    <th style='width:18%; text-align:center;'>Score</th>
                    <th style='width:24%;'>Sectional Split</th>
                    <th style='width:12%; text-align:center;'>Neg Bleed</th>
                </tr>
            </thead>
            <tbody>
                {''.join(ledger_rows_html)}
            </tbody>
        </table>

        <div class='pdf-footer'>
            Learn with HiM Platform • Telegram: @learnwithhim • Bot: @quizwithhimbot
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
# 🤖 CLEAN TELEGRAM UI & DASHBOARD
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
            f"📊 **SCOREKEEPER DASHBOARD**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 **Student:** {name} | **Category:** `{cat}`\n"
            f"🎯 **Exam Track:** `{stats['exam_name']}`\n"
            f"🎖️ **Target:** `{target_post}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"ℹ️ *No mock scores logged yet.*\n\n"
            f"Tap **➕ Log Score** below to record:\n"
            f"• Full Mock Tests\n"
            f"• Sectional Tests (Sec-I / Sec-II)\n"
            f"• Module Tests (Maths, Reasoning, English, GA, Computer)"
        )
    else:
        latest = stats["latest_log"]
        latest_scope = latest.get("test_scope_name", "Full Mock")
        latest_max = int(latest.get("max_marks", stats["max_marks"]))

        delta_str = ""
        if stats["has_matched_delta"]:
            d_val = stats["last_delta"]
            delta_str = f" ({'+' if d_val > 0 else ''}{d_val})"

        qual_str = f"• **Computer:** `{stats['qual_info']['status']}`\n" if stats.get("qual_info") else ""

        msg = (
            f"📊 **SCOREKEEPER DASHBOARD**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 **Student:** {name} | **Category:** `{cat}`\n"
            f"🎯 **Active Track:** `{stats['exam_name']}`\n"
            f"🎖️ **Target:** `{target_post}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **Latest:** `{stats['latest_score']} / {latest_max}` `[{latest_scope}]`{delta_str}\n"
            f"• **Projected Exam Score:** `{stats['predicted_final_score']} / {int(stats['max_marks'])}`\n"
            f"• **Best Full Mock:** `{stats['best_score']}` | **EMA Avg:** `{stats['ema_score']}`\n"
            f"• **Tests Logged:** Full: `{stats['full_mock_count']}` | Sec: `{stats['sectional_count']}` | Mod: `{stats['module_count']}`\n"
            f"{qual_str}"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎖️ **Status:** `{stats['post_zone']}`\n"
            f"👉 *{stats['next_goal_text']}*"
        )

    keyboard = [
        [
            InlineKeyboardButton("➕ Log Score", callback_data="sk_start_log"),
            InlineKeyboardButton("📋 View Score Vault", callback_data="sk_view_all_0")
        ],
        [
            InlineKeyboardButton("🔮 AI Prediction", callback_data="sk_detailed_pred"),
            InlineKeyboardButton("📄 PDF Report", callback_data="sk_download_pdf")
        ],
        [
            InlineKeyboardButton("🔄 Switch Exam", callback_data="sk_switch_exam_menu"),
            InlineKeyboardButton(f"🏷 Category: {cat}", callback_data="sk_profile_settings")
        ]
    ]
    return msg, InlineKeyboardMarkup(keyboard)


async def scorekeeper_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Active: `{tmpl['name']}`\n"
            f"Format: `/log {sec_names}{qual_str} [Source]`\n\n"
            f"Example:\n`/log 82 85 118 42 38 Testbook`\n\n"
            f"Or type **/scorekeeper** to log sectional or module tests step-by-step.",
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
        await update.message.reply_text("⚠️ Please enter numeric marks. Example: `/log 82 85 118 42 38 Testbook`", parse_mode="Markdown")
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
        f"✅ **Score Saved: `{total_score}/{int(tmpl['max_marks'])}` ({source})**\n\n{msg}",
        reply_markup=markup,
        parse_mode="Markdown"
    )


# ==============================================================================
# ➕ FAST & CLUTTER-FREE LOGGING WIZARD
# ==============================================================================
async def sk_start_log_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    sk_prof = await asyncio.to_thread(get_or_create_sk_profile, user_id)
    active_exam = sk_prof.get("active_exam", "SSC_CGL_MAINS")
    context.user_data["sk_log_exam"] = active_exam

    if active_exam in MAINS_SUBTEST_DEFINITIONS:
        keyboard = [
            [InlineKeyboardButton("🏆 Full Mock Test", callback_data="sklog_scope_FULL")],
            [
                InlineKeyboardButton("Section-I (Maths + Reas)", callback_data="sklog_scope_SEC_1"),
                InlineKeyboardButton("Section-II (English + GA)", callback_data="sklog_scope_SEC_2")
            ],
            [
                InlineKeyboardButton("Maths Mod", callback_data="sklog_scope_MOD_MATH"),
                InlineKeyboardButton("Reasoning Mod", callback_data="sklog_scope_MOD_REAS"),
                InlineKeyboardButton("English Mod", callback_data="sklog_scope_MOD_ENG")
            ],
            [
                InlineKeyboardButton("GA Module", callback_data="sklog_scope_MOD_GA"),
                InlineKeyboardButton("Computer Module", callback_data="sklog_scope_MOD_COMP")
            ],
            [
                InlineKeyboardButton("🔄 Change Exam", callback_data="sk_wizard_pick_other_exam"),
                InlineKeyboardButton("❌ Cancel", callback_data="sk_cancel_wizard")
            ]
        ]
        tmpl = EXAM_TEMPLATES[active_exam]
        await query.edit_message_text(
            f"📝 **LOG SCORE: {tmpl['short'].upper()}**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Select test type to record:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return SK_SELECT_TEST_TYPE
    else:
        context.user_data["sk_test_type"] = "FULL"
        context.user_data["sk_test_scope_name"] = "Full Mock"
        return await _prompt_mock_source_step(query, active_exam)


async def sk_wizard_pick_other_exam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = []
    row = []
    for code, info in EXAM_TEMPLATES.items():
        row.append(InlineKeyboardButton(info["short"], callback_data=f"sklog_exam_{code}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="sk_cancel_wizard")])

    await query.edit_message_text(
        "🎯 **Select Exam:**",
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

    if exam_code in MAINS_SUBTEST_DEFINITIONS:
        keyboard = [
            [InlineKeyboardButton("🏆 Full Mock Test", callback_data="sklog_scope_FULL")],
            [
                InlineKeyboardButton("Section-I (Maths + Reas)", callback_data="sklog_scope_SEC_1"),
                InlineKeyboardButton("Section-II (English + GA)", callback_data="sklog_scope_SEC_2")
            ],
            [
                InlineKeyboardButton("Maths", callback_data="sklog_scope_MOD_MATH"),
                InlineKeyboardButton("Reasoning", callback_data="sklog_scope_MOD_REAS"),
                InlineKeyboardButton("English", callback_data="sklog_scope_MOD_ENG")
            ],
            [
                InlineKeyboardButton("GA", callback_data="sklog_scope_MOD_GA"),
                InlineKeyboardButton("Computer", callback_data="sklog_scope_MOD_COMP")
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data="sk_cancel_wizard")]
        ]
        tmpl = EXAM_TEMPLATES[exam_code]
        await query.edit_message_text(
            f"📝 **LOG SCORE: {tmpl['short'].upper()}**\n\nSelect test type:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return SK_SELECT_TEST_TYPE
    else:
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
    context.user_data["sk_test_scope_name"] = sub_def["short"]
    return await _prompt_mock_source_step(query, exam_code)


async def _prompt_mock_source_step(query, exam_code: str):
    keyboard = []
    # Build clean buttons using the updated list of platforms
    row = []
    for label, code in MOCK_SOURCES:
        row.append(InlineKeyboardButton(label, callback_data=f"sklog_src_{code}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="sk_cancel_wizard")])

    await query.edit_message_text(
        "🏷 **Select Test Platform / Source:**",
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
            "✍️ Enter Mock/Series Name (e.g. `PTS Mock 01`):",
            parse_mode="Markdown"
        )
        return SK_CUSTOM_SOURCE

    # Map code back to clean display name
    code_map = dict([(code, label) for label, code in MOCK_SOURCES])
    context.user_data["sk_log_source"] = code_map.get(src, src)
    return await _prompt_quick_date_and_diff_step(query, context)


async def sk_wizard_custom_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["sk_log_source"] = update.message.text.strip()[:35] or "Mock Test"
    return await _prompt_quick_date_and_diff_step(update, context)


async def _prompt_quick_date_and_diff_step(target, context: ContextTypes.DEFAULT_TYPE):
    now_ist = datetime.now(IST)
    today_s = now_ist.strftime("%Y-%m-%d")

    keyboard = [
        [
            InlineKeyboardButton("📅 Today (Moderate)", callback_data=f"skfast_{today_s}_Moderate"),
            InlineKeyboardButton("📅 Today (Hard)", callback_data=f"skfast_{today_s}_Hard")
        ],
        [
            InlineKeyboardButton("📅 Today (Easy)", callback_data=f"skfast_{today_s}_Easy"),
            InlineKeyboardButton("🗓 Other Date...", callback_data="sklog_date_custom")
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="sk_cancel_wizard")]
    ]
    text = "📅 **Date & Difficulty:**"
    if hasattr(target, "edit_message_text"):
        await target.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await target.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return SK_SELECT_DATE


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
        [InlineKeyboardButton("✍️ Custom Date", callback_data="sklog_date_custom")],
        [InlineKeyboardButton("❌ Cancel", callback_data="sk_cancel_wizard")]
    ]
    await query.edit_message_text(
        "📅 **Select Date:**",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return SK_SELECT_DATE


async def sk_wizard_fast_date_diff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.replace("skfast_", "").split("_")
    context.user_data["sk_log_date"] = parts[0]
    context.user_data["sk_log_diff"] = parts[1]
    return await _prompt_marks_entry(query, context)


async def sk_wizard_date_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    d_val = query.data.replace("sklog_date_", "")
    if d_val == "custom":
        await query.edit_message_text(
            "📅 Reply with date (`DD-MM-YYYY`):",
            parse_mode="Markdown"
        )
        return SK_CUSTOM_DATE

    context.user_data["sk_log_date"] = d_val
    if "sk_log_diff" not in context.user_data:
        context.user_data["sk_log_diff"] = "Moderate"
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
    if "sk_log_diff" not in context.user_data:
        context.user_data["sk_log_diff"] = "Moderate"
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

    example_nums = " ".join([str(int(s[2] * 0.8)) for s in sec_list])
    sec_guide = ", ".join([f"{s[1]}" for s in sec_list])

    if len(sec_list) == 1:
        text = (
            f"✍️ **Enter Marks for {scope_title}** (Max: `{int(max_marks)}`)\n\n"
            f"Type your marks below (e.g. `{int(max_marks * 0.75)}`):"
        )
    else:
        text = (
            f"✍️ **Enter Marks for {scope_title}** (Max: `{int(max_marks)}`)\n"
            f"Order: `{sec_guide}`\n\n"
            f"Send space-separated marks:\n`{example_nums}`"
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
        await update.message.reply_text("⚠️ Please send numbers (e.g. `92` or `75 80`):", parse_mode="Markdown")
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
        if len(sec_defs) == 1:
            sectional_dict[sec_defs[0][0]] = total_score
        if qual_def and len(nums) >= 2:
            qual_score = min(qual_def[2], max(0.0, nums[1]))

    if test_type == "MOD_COMP":
        qual_score = total_score
        sectional_dict["comp"] = total_score

    context.user_data["sk_log_total"] = round(total_score, 2)
    context.user_data["sk_log_max_marks"] = max_marks
    context.user_data["sk_log_sectional"] = sectional_dict
    context.user_data["sk_log_qual"] = round(qual_score, 2)

    quick_btn = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Done (0 Wrong)", callback_data="sklog_wrong_skip"),
            InlineKeyboardButton("❌ Cancel", callback_data="sk_cancel_wizard")
        ]
    ])
    await update.message.reply_text(
        f"🎯 **Score: {round(total_score, 1)} / {int(max_marks)}**\n\n"
        f"Reply with number of **Wrong Questions** (or tap below if 0):",
        reply_markup=quick_btn,
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
        f"✅ **Saved:** `{scope_name}` — `{context.user_data.get('sk_log_total')}/{int(max_marks)}`\n\n{msg}",
        reply_markup=markup,
        parse_mode="Markdown"
    )
    return ConversationHandler.END


async def sk_edit_target_post_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🎖️ **Set Dream Post:**\n\nReply with your target post (e.g. `ASO in CSS / ITI 4600 GP`):",
        parse_mode="Markdown"
    )
    return SK_EDIT_TARGET_POST


async def sk_save_target_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    new_post = update.message.text.strip()[:60] or "Inspector / ASO (4600 GP)"
    await asyncio.to_thread(update_sk_profile, user_id, target_post=new_post)
    msg, markup = await asyncio.to_thread(build_scorekeeper_dashboard_Valid, user_id)
    await update.message.reply_text(
        f"✅ Target updated to: `{new_post}`\n\n{msg}",
        reply_markup=markup,
        parse_mode="Markdown"
    )
    return ConversationHandler.END


# ==============================================================================
# 📋 CLUTTER-FREE SCORE VAULT & CALLBACK ROUTER
# ==============================================================================
async def scorekeeper_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id

    if not await _sk_access_guard(update, context):
        return

    # 1. Main Dashboard
    if data == "sk_home":
        await scorekeeper_command(update, context)
        return

    # 2. Clutter-Free Score Vault (Paginated)
    elif data.startswith("sk_view_all_"):
        await query.answer()
        page = int(data.replace("sk_view_all_", "") or 0)
        sk_prof = await asyncio.to_thread(get_or_create_sk_profile, user_id)
        exam_code = sk_prof.get("active_exam", "SSC_CGL_MAINS")
        stats = await asyncio.to_thread(analyze_user_scores, user_id, exam_code)

        if not stats.get("has_data"):
            btn = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Log Score", callback_data="sk_start_log")],
                [InlineKeyboardButton("🔙 Dashboard", callback_data="sk_home")]
            ])
            await query.edit_message_text(
                f"📋 **LOGGED SCORES — {stats['exam_name']}**\n\nNo scores recorded yet.",
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
            f"📋 **SCORE VAULT — {stats['short_name'].upper()}**",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ]

        for idx, l in enumerate(slice_logs):
            abs_idx = page * SCORES_PER_PAGE + idx
            sc = float(l["total_score"])
            max_sc = int(l.get("max_marks") or stats["max_marks"])
            pct = round((sc / max_sc) * 100, 1) if max_sc > 0 else 0

            cur_scope = l.get("test_type", "FULL")
            prior_same_scope = [x for x in logs_desc[abs_idx + 1:] if x.get("test_type", "FULL") == cur_scope]
            delta_str = ""
            if prior_same_scope:
                diff = round(sc - float(prior_same_scope[0]["total_score"]), 1)
                delta_str = f" (`{'+' if diff > 0 else ''}{diff}`)"

            scope_badge = l.get("test_scope_name", "Full")
            if scope_badge == "Full Mock":
                scope_badge = "Full"

            sec_d = l.get("sectional") or {}
            sec_summary = ""
            if len(sec_d) > 1:
                sec_summary = " • " + " ".join([f"{k.upper()[:1]}:{int(round(float(v)))}" for k, v in sec_d.items()])

            qual_badge = ""
            if float(l.get("qualifying_score") or 0) > 0 and cur_scope == "FULL":
                qual_badge = f" • C:{int(round(float(l['qualifying_score'])))}"

            lines.append(
                f"**#{total_logs - abs_idx}.** `{l['exam_date']}` • **[{scope_badge}]**\n"
                f"    `{sc:g}/{max_sc}` ({pct}%){delta_str} • {l['mock_source']}{sec_summary}{qual_badge}"
            )
            if idx < len(slice_logs) - 1:
                lines.append("    ────────────────────────")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"🏆 **Best Full:** `{stats['best_score']}` | 📊 **Avg:** `{stats['avg_score']}` | **Total:** `{total_logs}`")

        keyboard = []
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"sk_view_all_{page - 1}"))
        nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="sk_noop"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"sk_view_all_{page + 1}"))
        if nav_row:
            keyboard.append(nav_row)

        keyboard.append([
            InlineKeyboardButton("➕ Log Score", callback_data="sk_start_log"),
            InlineKeyboardButton("📄 PDF Dossier", callback_data="sk_download_pdf")
        ])
        keyboard.append([
            InlineKeyboardButton("🗑 Delete Last", callback_data="sk_del_last_confirm"),
            InlineKeyboardButton("🔙 Dashboard", callback_data="sk_home")
        ])

        await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    # 3. Delete Confirmation
    elif data == "sk_del_last_confirm":
        await query.answer()
        btn = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🗑 Yes, Delete", callback_data="sk_del_last_exec"),
                InlineKeyboardButton("❌ Cancel", callback_data="sk_view_all_0")
            ]
        ])
        await query.edit_message_text(
            "⚠️ **Delete most recently logged score?**",
            reply_markup=btn,
            parse_mode="Markdown"
        )
        return

    elif data == "sk_del_last_exec":
        sk_prof = await asyncio.to_thread(get_or_create_sk_profile, user_id)
        deleted = await asyncio.to_thread(delete_last_score_log, user_id, sk_prof["active_exam"])
        await query.answer("Deleted!" if deleted else "No score found.", show_alert=True)
        await scorekeeper_command(update, context)
        return

    # 4. Detailed Prediction
    elif data == "sk_detailed_pred":
        await query.answer()
        sk_prof = await asyncio.to_thread(get_or_create_sk_profile, user_id)
        stats = await asyncio.to_thread(analyze_user_scores, user_id, sk_prof["active_exam"])

        if not stats.get("has_data"):
            await query.answer("Log a score first!", show_alert=True)
            return

        sec_lines = []
        for s in stats["sectional_breakdown"]:
            sec_lines.append(f"• **{s['label']}:** `{s['avg']}/{int(s['max'])}` ({s['pct']}%)")

        qual_block = f"• **Computer Gate:** `{stats['qual_info']['status']}`\n" if stats.get("qual_info") else ""

        pred_msg = (
            f"🔮 **AI PREDICTION & EFFICIENCY**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 **Track:** `{stats['exam_name']}` | **Category:** `{stats['category']}`\n"
            f"• **Projected Score:** `{stats['predicted_final_score']} / {int(stats['max_marks'])}`\n"
            f"• **EMA Potential:** `{stats['ema_score']} / {int(stats['max_marks'])}`\n"
            f"• **Percentile:** `{stats['comm_percentile']}%ile`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 **Sectional Efficiency:**\n" + "\n".join(sec_lines) + "\n" +
            f"{qual_block}"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎖️ **Verdict:** `{stats['post_zone']}`\n"
            f"👉 *{stats['next_goal_text']}*"
        )

        keyboard = [
            [
                InlineKeyboardButton("📄 PDF Dossier", callback_data="sk_download_pdf"),
                InlineKeyboardButton("📋 Vault", callback_data="sk_view_all_0")
            ],
            [InlineKeyboardButton("🔙 Dashboard", callback_data="sk_home")]
        ]
        await query.edit_message_text(pred_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    # 5. Switch Exam
    elif data == "sk_switch_exam_menu":
        await query.answer()
        sk_prof = await asyncio.to_thread(get_or_create_sk_profile, user_id)
        curr = sk_prof.get("active_exam", "SSC_CGL_MAINS")

        keyboard = []
        row = []
        for code, info in EXAM_TEMPLATES.items():
            mark = "✅ " if code == curr else ""
            row.append(InlineKeyboardButton(f"{mark}{info['short']}", callback_data=f"sk_setexam_{code}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("🔙 Dashboard", callback_data="sk_home")])

        await query.edit_message_text(
            "🔄 **Select Active Exam Track:**",
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

    # 6. Category Setting
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

        cat_btns.append([InlineKeyboardButton("🎖️ Edit Dream Post", callback_data="sk_edit_post")])
        cat_btns.append([InlineKeyboardButton("🔙 Dashboard", callback_data="sk_home")])

        await query.edit_message_text(
            f"⚙️ **Category & Target Settings**\n"
            f"Current: `{curr_cat}` | Target: `{sk_prof.get('target_post')}`\n\n"
            f"Select your reservation category:",
            reply_markup=InlineKeyboardMarkup(cat_btns),
            parse_mode="Markdown"
        )
        return

    elif data.startswith("sk_setcat_"):
        new_cat = data.replace("sk_setcat_", "")
        await asyncio.to_thread(update_sk_profile, user_id, category=new_cat)
        await query.answer(f"Category set to {new_cat}!")
        await scorekeeper_command(update, context)
        return

    # 7. PDF Report
    elif data == "sk_download_pdf":
        await query.answer("Compiling PDF...", show_alert=False)
        wait_msg = await query.edit_message_text(
            "⏳ *Generating Official PDF Report...*",
            parse_mode="Markdown"
        )

        from app.telegram_bot import log_pdf_generation_event
        sk_prof = await asyncio.to_thread(get_or_create_sk_profile, user_id)
        exam_code = sk_prof.get("active_exam", "SSC_CGL_MAINS")
        asyncio.create_task(asyncio.to_thread(log_pdf_generation_event, user_id, f"scorekeeper_{exam_code.lower()}"))

        pdf_path = await asyncio.to_thread(generate_scorekeeper_pdf, user_id, exam_code)

        if pdf_path == "NO_LOGS":
            nav = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Dashboard", callback_data="sk_home")]])
            await wait_msg.edit_text("ℹ️ Log at least one score first.", reply_markup=nav, parse_mode="Markdown")
            return

        if pdf_path and os.path.exists(pdf_path) and not str(pdf_path).startswith("ERROR"):
            u = await asyncio.to_thread(get_user_profile, user_id) or {}
            stats = await asyncio.to_thread(analyze_user_scores, user_id, exam_code)
            caption = (
                f"📄 **SCOREKEEPER DOSSIER**\n"
                f"👤 {u.get('full_name', 'Student')} | 🎯 {stats['exam_name']} ({stats['category']})\n"
                f"🔮 Projected: `{stats['predicted_final_score']} / {int(stats['max_marks'])}`"
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
            nav = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Dashboard", callback_data="sk_home")]])
            await wait_msg.edit_text(f"⚠️ PDF generation failed.", reply_markup=nav, parse_mode="Markdown")
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
            SK_SELECT_EXAM: [
                CallbackQueryHandler(sk_wizard_exam_selected, pattern="^sklog_exam_"),
                CallbackQueryHandler(sk_wizard_pick_other_exam, pattern="^sk_wizard_pick_other_exam$")
            ],
            SK_SELECT_TEST_TYPE: [
                CallbackQueryHandler(sk_wizard_test_type_selected, pattern="^sklog_scope_"),
                CallbackQueryHandler(sk_wizard_pick_other_exam, pattern="^sk_wizard_pick_other_exam$")
            ],
            SK_SELECT_SOURCE: [CallbackQueryHandler(sk_wizard_source_selected, pattern="^sklog_src_")],
            SK_CUSTOM_SOURCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, sk_wizard_custom_source)],
            SK_SELECT_DIFF: [CallbackQueryHandler(sk_wizard_diff_selected, pattern="^sklog_diff_")],
            SK_SELECT_DATE: [
                CallbackQueryHandler(sk_wizard_fast_date_diff, pattern="^skfast_"),
                CallbackQueryHandler(sk_wizard_date_selected, pattern="^sklog_date_")
            ],
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
    init_scorekeeper_db()
    app.add_handler(get_scorekeeper_conv_handler())
    app.add_handler(CommandHandler("scorekeeper", scorekeeper_command))
    app.add_handler(CommandHandler("log", quick_log_command))
    app.add_handler(CallbackQueryHandler(scorekeeper_callback_router, pattern="^sk_"))