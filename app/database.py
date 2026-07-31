import os
import time
import sqlite3
import json
import logging

logger = logging.getLogger(__name__)

# Resilient Database Path Resolution
try:
    from app.config import DB_PATH
    SQLITE_DB_PATH = DB_PATH
except ImportError:
    try:
        from app.config import SQLITE_DB_PATH
    except ImportError:
        BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        SQLITE_DB_PATH = os.path.join(BASE_DIR, "data", "quiz_bot.db")

def get_db_connection():
    conn = sqlite3.connect(SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            target_exam TEXT,
            age INTEGER,
            gender TEXT,
            phone_number TEXT,
            state TEXT,
            country TEXT DEFAULT 'India',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            referral_code TEXT,
            referred_by INTEGER,
            referral_count INTEGER DEFAULT 0,
            bonus_quota INTEGER DEFAULT 0,
            last_edit_timestamp INTEGER DEFAULT 0
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quiz_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            score REAL,
            questions_attempted INTEGER,
            correct_answers INTEGER,
            wrong_answers INTEGER,
            attempt_date DATE DEFAULT (date('now')),
            attempt_timestamp INTEGER
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS student_feedbacks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            full_name TEXT,
            feedback_text TEXT,
            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS paused_quizzes (
            user_id INTEGER PRIMARY KEY,
            quiz_state TEXT,
            saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()

def save_user_profile(profile_data: dict):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO users (
            user_id, username, full_name, target_exam, age, gender, phone_number, state, country, referral_code, referred_by, last_edit_timestamp
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            full_name = excluded.full_name,
            target_exam = excluded.target_exam,
            age = excluded.age,
            gender = excluded.gender,
            phone_number = excluded.phone_number,
            state = excluded.state,
            country = excluded.country,
            last_edit_timestamp = excluded.last_edit_timestamp
    """, (
        profile_data['user_id'],
        profile_data.get('username', ''),
        profile_data['full_name'],
        profile_data['target_exam'],
        profile_data['age'],
        profile_data['gender'],
        profile_data['phone_number'],
        profile_data.get('state', 'N/A'),
        profile_data.get('country', 'India'),
        profile_data.get('referral_code', ''),
        profile_data.get('referred_by'),
        profile_data.get('last_edit_timestamp', int(time.time()))
    ))

    conn.commit()
    conn.close()

def get_user_profile(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def can_user_edit_profile(user_id: int) -> tuple:
    """Checks if a student is allowed to edit their profile details (Limit: Once every 30 days)."""
    profile = get_user_profile(user_id)
    if not profile:
        return True, 0

    last_edit = profile.get('last_edit_timestamp', 0) or 0
    now = int(time.time())
    thirty_days_sec = 30 * 24 * 3600

    if now - last_edit >= thirty_days_sec:
        return True, 0
    else:
        remaining_sec = thirty_days_sec - (now - last_edit)
        days_left = max(1, (remaining_sec + 86399) // 86400)
        return False, days_left

def get_all_users():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def save_quiz_attempt(user_id: int, score: float, questions_attempted: int, correct: int, wrong: int, timestamp: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO quiz_attempts (user_id, score, questions_attempted, correct_answers, wrong_answers, attempt_timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, score, questions_attempted, correct, wrong, timestamp))
    conn.commit()
    conn.close()

def get_today_attempts(user_id: int) -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT SUM(questions_attempted) as total_today 
        FROM quiz_attempts 
        WHERE user_id = ? AND attempt_date = date('now')
    """, (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row['total_today'] if row and row['total_today'] else 0

def save_student_feedback(user_id: int, full_name: str, feedback_text: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO student_feedbacks (user_id, full_name, feedback_text)
        VALUES (?, ?, ?)
    """, (user_id, full_name, feedback_text))
    conn.commit()
    conn.close()

def get_all_student_feedbacks(limit: int = 15):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM student_feedbacks ORDER BY submitted_at DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_maintenance_until() -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM system_config WHERE key = 'maintenance_until'")
    row = cursor.fetchone()
    conn.close()
    return int(row['value']) if row else 0

def set_maintenance_until(timestamp: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO system_config (key, value) VALUES ('maintenance_until', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (str(timestamp),))
    conn.commit()
    conn.close()

def save_paused_quiz(user_id: int, quiz_state: dict):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO paused_quizzes (user_id, quiz_state)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET quiz_state = excluded.quiz_state, saved_at = CURRENT_TIMESTAMP
    """, (user_id, json.dumps(quiz_state)))
    conn.commit()
    conn.close()

def get_paused_quiz(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT quiz_state FROM paused_quizzes WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return json.loads(row['quiz_state']) if row else None

def clear_paused_quiz(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM paused_quizzes WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()