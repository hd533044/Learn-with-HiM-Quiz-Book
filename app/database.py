import sqlite3
import json
import os
import random
import string
import logging
import re
import time
from datetime import datetime
import pytz
from app.config import DB_FILE, USER_PROFILES_DIR

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

def get_ist_now():
    return datetime.now(IST)

def get_ist_date_str():
    return get_ist_now().strftime("%Y-%m-%d")

def get_ist_timestamp_str():
    return get_ist_now().strftime("%Y-%m-%d %H:%M:%S IST")

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def sanitize_filename(name_str: str) -> str:
    clean = re.sub(r'[^a-zA-Z0-9]', '_', str(name_str).strip())
    return clean if clean else "Candidate"

def generate_unique_student_id() -> str:
    conn = get_db()
    cursor = conn.cursor()
    while True:
        five_digits = "".join(random.choices(string.digits, k=5))
        one_letter = random.choice(string.ascii_uppercase)
        candidate = f"{five_digits}{one_letter}"

        cursor.execute("SELECT 1 FROM student_id_registry WHERE student_id = ?", (candidate,))
        if not cursor.fetchone():
            cursor.execute('''
                INSERT INTO student_id_registry (student_id, issued_at)
                VALUES (?, ?)
            ''', (candidate, get_ist_timestamp_str()))
            conn.commit()
            conn.close()
            return candidate

def generate_4digit_pass() -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=4))

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS student_id_registry (
            student_id TEXT PRIMARY KEY,
            user_id INTEGER,
            issued_at TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            student_id TEXT UNIQUE,
            login_pass TEXT,
            full_name TEXT,
            username TEXT,
            phone_number TEXT,
            target_exam TEXT,
            age INTEGER,
            gender TEXT,
            country TEXT DEFAULT 'India',
            state TEXT DEFAULT 'N/A',
            referred_by INTEGER,
            referral_count INTEGER DEFAULT 0,
            bonus_quota INTEGER DEFAULT 0,
            last_profile_edit TEXT,
            last_active_epoch INTEGER DEFAULT 0,
            is_verified INTEGER DEFAULT 1,
            created_at TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS quiz_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            student_id TEXT,
            quiz_id TEXT DEFAULT 'computer_awareness_mock',
            questions_attempted INTEGER DEFAULT 0,
            total_questions INTEGER DEFAULT 0,
            correct_answers INTEGER DEFAULT 0,
            wrong_answers INTEGER DEFAULT 0,
            skipped_count INTEGER DEFAULT 0,
            score REAL DEFAULT 0.0,
            time_taken INTEGER DEFAULT 0,
            attempt_timestamp TEXT,
            attempt_date TEXT,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS seen_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            question_id TEXT,
            seen_at TEXT,
            UNIQUE(user_id, question_id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS student_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            full_name TEXT,
            feedback_text TEXT,
            submitted_at TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS paused_quizzes (
            user_id INTEGER PRIMARY KEY,
            quiz_state TEXT,
            saved_at TEXT
        )
    ''')
    
    cursor.execute("INSERT OR IGNORE INTO bot_settings (key, value) VALUES ('maintenance_until', '0')")
    conn.commit()
    conn.close()

def can_user_edit_profile(user_id: int) -> tuple:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT last_profile_edit FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()

    if not row or not row['last_profile_edit']:
        return True, 0

    try:
        edit_str = row['last_profile_edit'].split(" IST")[0].strip()
        last_edit_dt = datetime.strptime(edit_str, "%Y-%m-%d %H:%M:%S")
        days_passed = (datetime.now() - last_edit_dt).days
        if days_passed >= 30:
            return True, 0
        days_remaining = 30 - days_passed
        return False, max(1, days_remaining)
    except Exception as e:
        return True, 0

def sync_user_unique_file(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user_row = cursor.fetchone()
    if not user_row:
        conn.close()
        return

    cursor.execute("SELECT * FROM quiz_attempts WHERE user_id = ? ORDER BY id DESC", (user_id,))
    attempts_rows = cursor.fetchall()
    conn.close()

    user_dict = dict(user_row)
    attempts_list = [dict(a) for a in attempts_rows]
    
    student_id = user_dict.get('student_id') or str(user_id)
    cand_name = sanitize_filename(user_dict.get('full_name', 'Student'))

    profile_data = {
        "student_identity": {
            "student_id": student_id,
            "login_password": user_dict.get('login_pass'),
            "telegram_id": user_dict.get('user_id'),
            "username": user_dict.get('username')
        },
        "personal_details": {
            "full_name": user_dict.get('full_name'),
            "phone_number": user_dict.get('phone_number'),
            "target_exam": user_dict.get('target_exam'),
            "age": user_dict.get('age'),
            "gender": user_dict.get('gender'),
            "country": user_dict.get('country'),
            "state": user_dict.get('state')
        },
        "system_status": {
            "referral_count": user_dict.get('referral_count'),
            "bonus_quota": user_dict.get('bonus_quota'),
            "last_profile_edit": user_dict.get('last_profile_edit'),
            "created_at": user_dict.get('created_at'),
            "last_active": get_ist_timestamp_str()
        },
        "quiz_history": attempts_list
    }

    filename = f"{cand_name}_{student_id}.json"
    filepath = os.path.join(USER_PROFILES_DIR, filename)

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(profile_data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Failed to write candidate file: {e}")

def save_user_profile(user_id, full_name, username, phone, target_exam, age, gender, country="India", state="N/A", referred_by=None):
    conn = get_db()
    cursor = conn.cursor()
    now_str = get_ist_timestamp_str()
    now_epoch = int(time.time())
    
    cursor.execute("SELECT student_id, login_pass FROM users WHERE user_id = ?", (user_id,))
    existing = cursor.fetchone()
    
    if existing and existing['student_id']:
        student_id = existing['student_id']
        login_pass = existing['login_pass']
    else:
        student_id = generate_unique_student_id()
        login_pass = generate_4digit_pass()

    cursor.execute("UPDATE student_id_registry SET user_id = ? WHERE student_id = ?", (user_id, student_id))

    cursor.execute('''
        INSERT INTO users (user_id, student_id, login_pass, full_name, username, phone_number, target_exam, age, gender, country, state, referred_by, last_profile_edit, last_active_epoch, is_verified, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            full_name=excluded.full_name,
            username=excluded.username,
            phone_number=excluded.phone_number,
            target_exam=excluded.target_exam,
            age=excluded.age,
            gender=excluded.gender,
            country=excluded.country,
            state=excluded.state,
            last_profile_edit=?,
            last_active_epoch=?,
            is_verified=1
    ''', (user_id, student_id, login_pass, full_name, username, phone, target_exam, age, gender, country, state, referred_by, now_str, now_epoch, now_str, now_str, now_epoch))
    
    if referred_by and referred_by != user_id:
        cursor.execute("UPDATE users SET referral_count = referral_count + 1 WHERE user_id = ?", (referred_by,))
        cursor.execute("SELECT referral_count FROM users WHERE user_id = ?", (referred_by,))
        row = cursor.fetchone()
        if row and row['referral_count'] >= 4:
            cursor.execute("UPDATE users SET bonus_quota = bonus_quota + 10 WHERE user_id = ?", (referred_by,))
            
    conn.commit()
    conn.close()
    
    sync_user_unique_file(user_id)
    return student_id, login_pass

def touch_user_activity(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    now_epoch = int(time.time())
    cursor.execute("UPDATE users SET last_active_epoch = ? WHERE user_id = ?", (now_epoch, user_id))
    conn.commit()
    conn.close()

def get_user_profile(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_all_users():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_today_attempts(user_id):
    conn = get_db()
    cursor = conn.cursor()
    today_date = get_ist_date_str()
    cursor.execute('''
        SELECT SUM(questions_attempted) as total 
        FROM quiz_attempts 
        WHERE user_id = ? AND attempt_date = ?
    ''', (user_id, today_date))
    row = cursor.fetchone()
    conn.close()
    return row['total'] if row and row['total'] else 0

def record_quiz_result(user_id, quiz_id="computer_awareness_mock", score=0.0, total_questions=0, correct_count=0, wrong_count=0, skipped_count=0, time_taken=0):
    conn = get_db()
    cursor = conn.cursor()
    today_date = get_ist_date_str()
    timestamp_str = get_ist_timestamp_str()
    
    cursor.execute("SELECT student_id FROM users WHERE user_id = ?", (user_id,))
    u_row = cursor.fetchone()
    s_id = u_row['student_id'] if u_row else ''

    cursor.execute('''
        INSERT INTO quiz_attempts (user_id, student_id, quiz_id, questions_attempted, total_questions, correct_answers, wrong_answers, skipped_count, score, time_taken, attempt_timestamp, attempt_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, s_id, quiz_id, total_questions, total_questions, correct_count, wrong_count, skipped_count, score, time_taken, timestamp_str, today_date))
    conn.commit()
    conn.close()
    
    sync_user_unique_file(user_id)

def get_seen_question_ids(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT question_id FROM seen_questions WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return {str(r['question_id']) for r in rows}

def mark_questions_as_seen(user_id, question_ids):
    conn = get_db()
    cursor = conn.cursor()
    now_str = get_ist_timestamp_str()
    for qid in question_ids:
        cursor.execute("INSERT OR IGNORE INTO seen_questions (user_id, question_id, seen_at) VALUES (?, ?, ?)", (user_id, str(qid), now_str))
    conn.commit()
    conn.close()

def save_student_feedback(user_id: int, full_name: str, feedback_text: str):
    conn = get_db()
    cursor = conn.cursor()
    now_str = get_ist_timestamp_str()
    cursor.execute('''
        INSERT INTO student_feedback (user_id, full_name, feedback_text, submitted_at)
        VALUES (?, ?, ?, ?)
    ''', (user_id, full_name, feedback_text, now_str))
    conn.commit()
    conn.close()

def get_all_student_feedbacks(limit: int = 15):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT full_name, feedback_text, submitted_at FROM student_feedback ORDER BY id DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def set_maintenance_until(epoch_timestamp: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE bot_settings SET value = ? WHERE key = 'maintenance_until'", (str(epoch_timestamp),))
    conn.commit()
    conn.close()

def get_maintenance_until() -> int:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM bot_settings WHERE key = 'maintenance_until'")
    row = cursor.fetchone()
    conn.close()
    return int(row['value']) if row and row['value'].isdigit() else 0

def save_paused_quiz_state(user_id: int, quiz_state: dict):
    conn = get_db()
    cursor = conn.cursor()
    now_str = get_ist_timestamp_str()
    cursor.execute('''
        INSERT INTO paused_quizzes (user_id, quiz_state, saved_at)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            quiz_state = excluded.quiz_state,
            saved_at = excluded.saved_at
    ''', (user_id, json.dumps(quiz_state), now_str))
    conn.commit()
    conn.close()

def get_paused_quiz_state(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT quiz_state FROM paused_quizzes WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return json.loads(row['quiz_state']) if row and row['quiz_state'] else None

def clear_paused_quiz_state(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM paused_quizzes WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()