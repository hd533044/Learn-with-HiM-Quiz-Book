import sqlite3
import json
import os
import logging
from datetime import datetime, timedelta
import pytz
from app.config import DB_FILE, USER_PROFILES_DIR, PLAN_TIERS, DAILY_QUESTION_LIMIT

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

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            student_id TEXT UNIQUE,
            full_name TEXT,
            username TEXT,
            phone_number TEXT,
            target_exam TEXT,
            dob TEXT,
            age INTEGER,
            gender TEXT,
            country TEXT DEFAULT 'India',
            state TEXT DEFAULT 'N/A',
            pin TEXT,
            security_question TEXT,
            security_answer TEXT,
            referred_by INTEGER,
            referral_count INTEGER DEFAULT 0,
            bonus_quota INTEGER DEFAULT 0,
            paid_question_balance INTEGER DEFAULT 0,
            vip_pass_expiry TEXT,
            demo_used INTEGER DEFAULT 0,
            last_profile_edit TEXT,
            last_active TEXT,
            last_activity_epoch INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            is_verified INTEGER DEFAULT 1,
            created_at TEXT
        )
    ''')
    
    cursor.execute("PRAGMA table_info(users)")
    columns = [row[1] for row in cursor.fetchall()]
    if 'student_id' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN student_id TEXT")
    if 'dob' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN dob TEXT")
    if 'country' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN country DEFAULT 'India'")
    if 'state' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN state DEFAULT 'N/A'")
    if 'pin' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN pin TEXT")
    if 'security_question' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN security_question TEXT")
    if 'security_answer' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN security_answer TEXT")
    if 'paid_question_balance' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN paid_question_balance INTEGER DEFAULT 0")
    if 'vip_pass_expiry' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN vip_pass_expiry TEXT")
    if 'demo_used' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN demo_used INTEGER DEFAULT 0")
    if 'last_profile_edit' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN last_profile_edit TEXT")
    if 'last_active' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN last_active TEXT")
    if 'last_activity_epoch' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN last_activity_epoch INTEGER DEFAULT 0")
    if 'is_banned' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS quiz_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
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
            details_json TEXT,
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
        CREATE TABLE IF NOT EXISTS saved_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            question_text TEXT,
            options_json TEXT,
            correct_option INTEGER,
            explanation TEXT,
            saved_at TEXT
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

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_activity_time (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            date_str TEXT,
            seconds_spent INTEGER DEFAULT 0,
            UNIQUE(user_id, date_str)
        )
    ''')
    
    cursor.execute("INSERT OR IGNORE INTO bot_settings (key, value) VALUES ('maintenance_until', '0')")
    
    conn.commit()
    conn.close()

def generate_student_id(full_name: str, dob_str: str) -> str:
    clean_name = "".join(filter(str.isalpha, full_name))
    if len(clean_name) >= 2:
        prefix = clean_name[:2].capitalize()
    elif len(clean_name) == 1:
        prefix = clean_name.ljust(2, 'X').capitalize()
    else:
        prefix = "ST"
        
    try:
        parts = dob_str.split("-")
        day = parts[0]
        month = parts[1]
        year_full = parts[2]
        year_short = year_full[-2:]
        dob_code = f"{day}{month}{year_short}"
    except Exception:
        dob_code = "010100"
        
    base_id = f"{prefix}{dob_code}"
    
    conn = get_db()
    cursor = conn.cursor()
    student_id = base_id
    counter = 1
    while True:
        cursor.execute("SELECT 1 FROM users WHERE student_id = ?", (student_id,))
        if not cursor.fetchone():
            break
        student_id = f"{base_id}_{counter}"
        counter += 1
    conn.close()
    
    return student_id

def get_user_by_student_id(student_id: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE LOWER(student_id) = LOWER(?)", (student_id.strip(),))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def update_user_pin(user_id: int, new_pin: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET pin = ? WHERE user_id = ?", (new_pin, user_id))
    conn.commit()
    conn.close()
    sync_user_json_profile(user_id)

def check_and_update_inactivity(user_id: int) -> tuple[bool, int]:
    conn = get_db()
    cursor = conn.cursor()
    now_epoch = int(get_ist_now().timestamp())
    
    cursor.execute("SELECT last_activity_epoch, pin FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    
    if not row or not row['pin']:
        conn.close()
        return False, 0

    last_epoch = row['last_activity_epoch'] or 0
    diff = now_epoch - last_epoch if last_epoch > 0 else 0

    if last_epoch > 0 and diff > 300:
        conn.close()
        return True, diff

    cursor.execute("UPDATE users SET last_activity_epoch = ?, last_active = ? WHERE user_id = ?", (now_epoch, get_ist_timestamp_str(), user_id))
    conn.commit()
    conn.close()
    return False, diff

def refresh_user_activity_epoch(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    now_epoch = int(get_ist_now().timestamp())
    cursor.execute("UPDATE users SET last_activity_epoch = ?, last_active = ? WHERE user_id = ?", (now_epoch, get_ist_timestamp_str(), user_id))
    conn.commit()
    conn.close()

def log_user_activity_time(user_id: int, seconds: int = 15):
    conn = get_db()
    cursor = conn.cursor()
    today_date = get_ist_date_str()
    now_str = get_ist_timestamp_str()
    now_epoch = int(get_ist_now().timestamp())
    
    cursor.execute('''
        INSERT INTO user_activity_time (user_id, date_str, seconds_spent)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, date_str) DO UPDATE SET
            seconds_spent = seconds_spent + excluded.seconds_spent
    ''', (user_id, today_date, seconds))

    cursor.execute("UPDATE users SET last_active = ?, last_activity_epoch = ? WHERE user_id = ?", (now_str, now_epoch, user_id))
    conn.commit()
    conn.close()

def toggle_user_ban_status(user_id: int) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    current_status = row['is_banned'] if row and row['is_banned'] else 0
    new_status = 0 if current_status == 1 else 1
    cursor.execute("UPDATE users SET is_banned = ? WHERE user_id = ?", (new_status, user_id))
    conn.commit()
    conn.close()
    sync_user_json_profile(user_id)
    return bool(new_status)

def admin_update_user_name(user_id: int, new_name: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET full_name = ? WHERE user_id = ?", (new_name.strip(), user_id))
    conn.commit()
    conn.close()
    sync_user_json_profile(user_id)

def admin_delete_user_account(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT student_id FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    sid = row['student_id'] if row and row['student_id'] else f"USER_{user_id}"

    cursor.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    cursor.execute("DELETE FROM quiz_attempts WHERE user_id = ?", (user_id,))
    cursor.execute("DELETE FROM seen_questions WHERE user_id = ?", (user_id,))
    cursor.execute("DELETE FROM saved_questions WHERE user_id = ?", (user_id,))
    cursor.execute("DELETE FROM student_feedback WHERE user_id = ?", (user_id,))
    cursor.execute("DELETE FROM paused_quizzes WHERE user_id = ?", (user_id,))
    cursor.execute("DELETE FROM user_activity_time WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

    json_path = os.path.join(USER_PROFILES_DIR, f"{sid}.json")
    if os.path.exists(json_path):
        try:
            os.remove(json_path)
        except Exception as e:
            logger.error(f"Error removing JSON profile on deletion: {e}")

def get_paid_users():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE paid_question_balance > 0 ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def sync_user_json_profile(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user_row = cursor.fetchone()
    if not user_row:
        conn.close()
        return

    user_dict = dict(user_row)
    student_id = user_dict.get("student_id") or f"USER_{user_id}"

    cursor.execute("SELECT * FROM quiz_attempts WHERE user_id = ? ORDER BY id DESC", (user_id,))
    attempts_rows = cursor.fetchall()
    
    cursor.execute("SELECT * FROM saved_questions WHERE user_id = ? ORDER BY id DESC", (user_id,))
    saved_rows = cursor.fetchall()

    cursor.execute("SELECT * FROM student_feedback WHERE user_id = ? ORDER BY id DESC", (user_id,))
    feedback_rows = cursor.fetchall()

    cursor.execute("SELECT date_str, seconds_spent FROM user_activity_time WHERE user_id = ? ORDER BY date_str DESC", (user_id,))
    time_rows = cursor.fetchall()
    conn.close()

    formatted_attempts = []
    datewise_quiz_summary = {}
    daily_questions_count = {}

    for a in attempts_rows:
        ad = dict(a)
        if ad.get("details_json"):
            try:
                ad["question_details"] = json.loads(ad["details_json"])
            except Exception:
                ad["question_details"] = []
            del ad["details_json"]
        formatted_attempts.append(ad)

        dt = ad.get("attempt_date", "Unknown")
        qs = ad.get("questions_attempted", 0)

        daily_questions_count[dt] = daily_questions_count.get(dt, 0) + qs

        if dt not in datewise_quiz_summary:
            datewise_quiz_summary[dt] = {
                "total_quizzes": 0,
                "total_questions": 0,
                "total_correct": 0,
                "total_wrong": 0,
                "total_score": 0.0,
                "total_time_seconds": 0
            }
        
        datewise_quiz_summary[dt]["total_quizzes"] += 1
        datewise_quiz_summary[dt]["total_questions"] += qs
        datewise_quiz_summary[dt]["total_correct"] += ad.get("correct_answers", 0)
        datewise_quiz_summary[dt]["total_wrong"] += ad.get("wrong_answers", 0)
        datewise_quiz_summary[dt]["total_score"] += ad.get("score", 0.0)
        datewise_quiz_summary[dt]["total_time_seconds"] += ad.get("time_taken", 0)

    formatted_saved_qs = []
    datewise_saved_summary = {}
    for s in saved_rows:
        sd = dict(s)
        if sd.get("options_json"):
            try:
                sd["options"] = json.loads(sd["options_json"])
            except Exception:
                sd["options"] = []
            del sd["options_json"]
        formatted_saved_qs.append(sd)

        s_date = sd.get("saved_at", "").split(" ")[0] if sd.get("saved_at") else "Unknown"
        datewise_saved_summary[s_date] = datewise_saved_summary.get(s_date, 0) + 1

    activity_log = {r["date_str"]: f"{r['seconds_spent']} seconds ({round(r['seconds_spent']/60, 2)} mins)" for r in time_rows}
    total_time_seconds = sum([r["seconds_spent"] for r in time_rows])

    paid_balance = user_dict.get("paid_question_balance", 0)
    vip_expiry = user_dict.get("vip_pass_expiry")
    
    sub_status = "FREE_TIER"
    for p_key, p_val in PLAN_TIERS.items():
        if p_val.get("daily_limit") == paid_balance and paid_balance > DAILY_QUESTION_LIMIT:
            sub_status = p_key
            break

    profile_data = {
        "student_id": student_id,
        "registration_info": user_dict,
        "bot_engagement_metrics": {
            "last_login_timestamp": user_dict.get("last_active") or user_dict.get("created_at"),
            "total_time_spent_overall": f"{total_time_seconds} seconds ({round(total_time_seconds/60, 2)} mins)",
            "daily_spent_time_breakdown": activity_log,
            "questions_attempted_per_day": daily_questions_count
        },
        "academic_summary": {
            "total_quizzes_attempted": len(formatted_attempts),
            "total_questions_attempted": sum([a.get("questions_attempted", 0) for a in formatted_attempts]),
            "total_correct": sum([a.get("correct_answers", 0) for a in formatted_attempts]),
            "total_wrong": sum([a.get("wrong_answers", 0) for a in formatted_attempts]),
            "total_skipped": sum([a.get("skipped_count", 0) for a in formatted_attempts]),
            "datewise_quiz_summary": datewise_quiz_summary
        },
        "saved_questions_ledger": {
            "total_saved": len(formatted_saved_qs),
            "datewise_saved_summary": datewise_saved_summary,
            "saved_questions": formatted_saved_qs
        },
        "student_reviews_given": [dict(f) for f in feedback_rows],
        "subscription_ledger": {
            "status": sub_status,
            "paid_question_balance": paid_balance,
            "vip_pass_expiry": vip_expiry
        },
        "badges_and_achievements": {
            "earned_badges": ["Early Learner", "Registered Scholar"],
            "streak_days": len(daily_questions_count)
        },
        "full_quiz_history": formatted_attempts,
        "last_synced": get_ist_timestamp_str()
    }

    filepath = os.path.join(USER_PROFILES_DIR, f"{student_id}.json")
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(profile_data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Failed to sync JSON profile for student {student_id}: {e}")

def save_user_profile(user_id, full_name, username, phone, target_exam, dob, age, gender, pin, sec_q, sec_a, country="India", state="N/A", referred_by=None):
    conn = get_db()
    cursor = conn.cursor()
    now_str = get_ist_timestamp_str()
    now_epoch = int(get_ist_now().timestamp())
    
    student_id = generate_student_id(full_name, dob)

    # AUTO-GRANT FREE DEMO PLAN UPON REGISTRATION (2 Days / 20 Qs/Day)
    demo_plan = PLAN_TIERS.get("FREE_DEMO", {"days": 2, "daily_limit": 20})
    demo_expiry = (datetime.now(IST) + timedelta(days=demo_plan["days"])).strftime("%Y-%m-%d %H:%M:%S IST")

    cursor.execute('''
        INSERT INTO users (user_id, student_id, full_name, username, phone_number, target_exam, dob, age, gender, pin, security_question, security_answer, country, state, referred_by, paid_question_balance, vip_pass_expiry, demo_used, last_profile_edit, last_active, last_activity_epoch, is_banned, is_verified, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, 0, 1, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            student_id=excluded.student_id,
            full_name=excluded.full_name,
            username=excluded.username,
            phone_number=excluded.phone_number,
            target_exam=excluded.target_exam,
            dob=excluded.dob,
            age=excluded.age,
            gender=excluded.gender,
            pin=excluded.pin,
            security_question=excluded.security_question,
            security_answer=excluded.security_answer,
            country=excluded.country,
            state=excluded.state,
            last_profile_edit=?,
            last_active=?,
            last_activity_epoch=?,
            is_verified=1
    ''', (user_id, student_id, full_name, username, phone, target_exam, dob, age, gender, pin, sec_q, sec_a, country, state, referred_by, demo_plan["daily_limit"], demo_expiry, now_str, now_str, now_epoch, now_str, now_str, now_epoch, now_str))
    
    if referred_by and referred_by != user_id:
        cursor.execute("UPDATE users SET referral_count = referral_count + 1 WHERE user_id = ?", (referred_by,))
        cursor.execute("SELECT referral_count FROM users WHERE user_id = ?", (referred_by,))
        row = cursor.fetchone()
        if row and row['referral_count'] >= 4:
            cursor.execute("UPDATE users SET bonus_quota = bonus_quota + 10 WHERE user_id = ?", (referred_by,))
            
    conn.commit()
    conn.close()
    
    sync_user_json_profile(user_id)
    if referred_by:
        sync_user_json_profile(referred_by)

def can_user_edit_profile(user_id: int) -> tuple[bool, int]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT last_profile_edit FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()

    if not row or not row['last_profile_edit']:
        return True, 0

    try:
        last_edit_date = datetime.strptime(row['last_profile_edit'].split(" ")[0], "%Y-%m-%d")
        days_passed = (datetime.now() - last_edit_date).days
        if days_passed >= 30:
            return True, 0
        return False, 30 - days_passed
    except Exception:
        return True, 0

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

def record_quiz_result(user_id, quiz_id="computer_awareness_mock", score=0.0, total_questions=0, correct_count=0, wrong_count=0, skipped_count=0, time_taken=0, question_details=None):
    conn = get_db()
    cursor = conn.cursor()
    today_date = get_ist_date_str()
    timestamp_str = get_ist_timestamp_str()
    details_str = json.dumps(question_details) if question_details else json.dumps([])
    
    cursor.execute('''
        INSERT INTO quiz_attempts (user_id, quiz_id, questions_attempted, total_questions, correct_answers, wrong_answers, skipped_count, score, time_taken, attempt_timestamp, attempt_date, details_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, quiz_id, total_questions, total_questions, correct_count, wrong_count, skipped_count, score, time_taken, timestamp_str, today_date, details_str))
    conn.commit()
    conn.close()
    
    sync_user_json_profile(user_id)

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

def save_question_to_db(user_id: int, q_text: str, options: list, correct_option: int, explanation: str):
    conn = get_db()
    cursor = conn.cursor()
    now_str = get_ist_timestamp_str()
    try:
        cursor.execute('''
            INSERT INTO saved_questions (user_id, question_text, options_json, correct_option, explanation, saved_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, q_text, json.dumps(options), correct_option, explanation, now_str))
        conn.commit()
        success = True
    except Exception:
        success = False
    conn.close()
    sync_user_json_profile(user_id)
    return success

def get_saved_questions(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM saved_questions WHERE user_id = ? ORDER BY id DESC", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

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
    sync_user_json_profile(user_id)

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