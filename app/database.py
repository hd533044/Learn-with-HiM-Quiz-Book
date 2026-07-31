import sqlite3
import os
import time
import json
import random

DB_PATH = os.path.join("data", "quiz_bot.db")

def get_db_connection():
    """Establishes and returns a connection to the SQLite database with Row factory."""
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes all necessary database tables on application launch."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # User Profiles Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id INTEGER PRIMARY KEY,
            full_name TEXT,
            username TEXT,
            phone_number TEXT,
            target_exam TEXT,
            age INTEGER,
            gender TEXT,
            country TEXT,
            state TEXT,
            is_verified INTEGER DEFAULT 1,
            bonus_quota INTEGER DEFAULT 0,
            referral_count INTEGER DEFAULT 0,
            referred_by INTEGER,
            last_profile_update INTEGER DEFAULT 0,
            registration_date DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Quiz Attempts Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quiz_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            score INTEGER,
            total_questions INTEGER,
            correct_count INTEGER,
            wrong_count INTEGER,
            skipped_count INTEGER,
            attempt_date DATE DEFAULT (DATE('now')),
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Student Feedback / Reviews Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS student_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            full_name TEXT,
            feedback_text TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # System Configuration & Maintenance Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    conn.commit()
    conn.close()

def save_user_profile(user_id, full_name, username, phone, target_exam, age, gender, country, state, referred_by=None):
    """Saves or updates a user's verified student profile and handles referral limits."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now_ts = int(time.time())

    cursor.execute("SELECT user_id, referral_count FROM user_profiles WHERE user_id = ?", (user_id,))
    existing = cursor.fetchone()

    if existing:
        cursor.execute("""
            UPDATE user_profiles
            SET full_name=?, username=?, phone_number=?, target_exam=?, age=?, gender=?, country=?, state=?, last_profile_update=?
            WHERE user_id=?
        """, (full_name, username, phone, target_exam, age, gender, country, state, now_ts, user_id))
    else:
        cursor.execute("""
            INSERT INTO user_profiles (user_id, full_name, username, phone_number, target_exam, age, gender, country, state, referred_by, last_profile_update)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, full_name, username, phone, target_exam, age, gender, country, state, referred_by, now_ts))

        # Grant referral bonus to referrer (every 4 referrals = +10 question quota)
        if referred_by and referred_by != user_id:
            cursor.execute("SELECT referral_count, bonus_quota FROM user_profiles WHERE user_id = ?", (referred_by,))
            ref_row = cursor.fetchone()
            if ref_row:
                new_ref_count = ref_row["referral_count"] + 1
                new_bonus = ref_row["bonus_quota"] + 10 if new_ref_count % 4 == 0 else ref_row["bonus_quota"]
                cursor.execute("UPDATE user_profiles SET referral_count=?, bonus_quota=? WHERE user_id=?", (new_ref_count, new_bonus, referred_by))

    conn.commit()
    conn.close()

def can_user_edit_profile(user_id: int):
    """Checks if a user can update their profile (1 edit per 30 days restriction)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT last_profile_update FROM user_profiles WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()

    if not row or not row["last_profile_update"]:
        return True, 0

    elapsed = int(time.time()) - row["last_profile_update"]
    thirty_days = 30 * 24 * 3600
    if elapsed >= thirty_days:
        return True, 0
    
    days_left = max(1, (thirty_days - elapsed) // (24 * 3600))
    return False, days_left

def get_user_profile(user_id):
    """Retrieves a single user's profile details."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM user_profiles WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_all_users_full():
    """Retrieves all registered student profiles with full details for admin directory."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_id, full_name, username, target_exam, phone_number, age, gender, state, country, registration_date, bonus_quota, referral_count
        FROM user_profiles
        ORDER BY registration_date DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_all_users():
    """Alias function to get all user records."""
    return get_all_users_full()

def get_today_attempts(user_id):
    """Calculates how many quiz questions a user has attempted today."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(total_questions) as total FROM quiz_attempts WHERE user_id = ? AND attempt_date = DATE('now')", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row["total"] if row and row["total"] else 0

def record_quiz_result(user_id, score, total_questions, correct_count, wrong_count, skipped_count):
    """Records the outcome of a completed quiz session."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO quiz_attempts (user_id, score, total_questions, correct_count, wrong_count, skipped_count)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, score, total_questions, correct_count, wrong_count, skipped_count))
    conn.commit()
    conn.close()

def save_student_feedback(user_id: int, full_name: str, feedback_text: str):
    """Saves a student review/feedback into the database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO student_feedback (user_id, full_name, feedback_text)
        VALUES (?, ?, ?)
    """, (user_id, full_name, feedback_text))
    conn.commit()
    conn.close()

def get_all_student_feedbacks(limit: int = 15):
    """Retrieves student reviews for the /reviews command."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT full_name, feedback_text, timestamp 
        FROM student_feedback 
        ORDER BY id DESC 
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def set_maintenance_until(timestamp: int):
    """Sets system maintenance pause state timestamp."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO system_config (key, value) VALUES ('maintenance_until', ?)", (str(timestamp),))
    conn.commit()
    conn.close()

def get_maintenance_until() -> int:
    """Gets system maintenance pause state timestamp."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM system_config WHERE key = 'maintenance_until'")
    row = cursor.fetchone()
    conn.close()
    return int(row["value"]) if row else 0

def get_questions_by_count(count: int):
    """Loads and shuffles questions from all JSON files in data/question_bank/."""
    q_folder = os.path.join("data", "question_bank")
    if not os.path.exists(q_folder):
        return []

    all_qs = []
    for f in os.listdir(q_folder):
        if f.endswith(".json"):
            try:
                with open(os.path.join(q_folder, f), "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                    if isinstance(data, list):
                        all_qs.extend(data)
            except Exception:
                pass

    if not all_qs:
        return []
    
    random.shuffle(all_qs)
    return all_qs[:count]