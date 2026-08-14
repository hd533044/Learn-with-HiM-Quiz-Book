import json
import os
import logging
from datetime import datetime, timedelta
import pytz
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool
from app.config import USER_PROFILES_DIR, PLAN_TIERS, DAILY_QUESTION_LIMIT

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

DATABASE_URL = os.getenv("DATABASE_URL", "postgres://user:password@localhost:5432/dbname")

db_pool = None

def init_pool():
    global db_pool
    if db_pool is None:
        try:
            db_pool = psycopg2.pool.SimpleConnectionPool(1, 20, DATABASE_URL)
        except Exception as e:
            logger.error(f"Failed to initialize database pool: {e}")

def get_db():
    global db_pool
    if db_pool is None:
        init_pool()
    return db_pool.getconn()

def release_db(conn):
    global db_pool
    if db_pool and conn:
        db_pool.putconn(conn)

def get_ist_now():
    return datetime.now(IST)

def get_ist_date_str():
    return get_ist_now().strftime("%Y-%m-%d")

def get_ist_timestamp_str():
    return get_ist_now().strftime("%Y-%m-%d %H:%M:%S IST")

def init_db():
    init_pool()
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
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
            referred_by BIGINT,
            referral_count INTEGER DEFAULT 0,
            bonus_quota INTEGER DEFAULT 0,
            paid_question_balance INTEGER DEFAULT 0,
            vip_pass_expiry TEXT,
            demo_used INTEGER DEFAULT 0,
            last_profile_edit TEXT,
            last_active TEXT,
            last_activity_epoch BIGINT DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            is_verified INTEGER DEFAULT 1,
            created_at TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS quiz_attempts (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
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
            FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS seen_questions (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            question_id TEXT,
            seen_at TEXT,
            UNIQUE(user_id, question_id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS saved_questions (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            question_text TEXT,
            options_json TEXT,
            correct_option INTEGER,
            explanation TEXT,
            saved_at TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS student_feedback (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            full_name TEXT,
            feedback_text TEXT,
            submitted_at TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS student_queries (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            student_name TEXT,
            query_text TEXT,
            photo_file_id TEXT,
            admin_reply TEXT,
            status TEXT DEFAULT 'PENDING',
            created_at TEXT,
            replied_at TEXT
        )
    ''')

    cursor.execute("ALTER TABLE student_queries ADD COLUMN IF NOT EXISTS photo_file_id TEXT;")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS paused_quizzes (
            user_id BIGINT PRIMARY KEY,
            quiz_state TEXT,
            saved_at TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_activity_time (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            date_str TEXT,
            seconds_spent INTEGER DEFAULT 0,
            UNIQUE(user_id, date_str)
        )
    ''')

    # Promo Codes Master Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS promo_codes (
            id SERIAL PRIMARY KEY,
            code VARCHAR(50) UNIQUE NOT NULL,
            discount_type VARCHAR(10) NOT NULL,
            discount_value NUMERIC(10, 2) NOT NULL,
            valid_until TIMESTAMP NOT NULL,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_by BIGINT
        )
    ''')

    # Promo Code Redemptions Tracking Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS promo_redemptions (
            id SERIAL PRIMARY KEY,
            promo_id INT REFERENCES promo_codes(id) ON DELETE CASCADE,
            user_id BIGINT NOT NULL,
            redeemed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(promo_id, user_id)
        )
    ''')

    # Scheduled Announcements Master Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scheduled_announcements (
            id SERIAL PRIMARY KEY,
            message_text TEXT,
            media_file_id VARCHAR(255),
            media_type VARCHAR(20) DEFAULT 'text',
            scheduled_time TIMESTAMP NOT NULL,
            status VARCHAR(20) DEFAULT 'PENDING',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_by BIGINT
        )
    ''')

    # Broadcast Message Delivery Tracking Table (For Live Edit & Live Unsend from User Chats)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS broadcast_deliveries (
            id SERIAL PRIMARY KEY,
            announcement_id INT,
            user_id BIGINT NOT NULL,
            message_id BIGINT NOT NULL,
            delivered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute("INSERT INTO bot_settings (key, value) VALUES ('maintenance_until', '0') ON CONFLICT (key) DO NOTHING")
    
    conn.commit()
    cursor.close()
    release_db(conn)

def generate_student_id(full_name: str, dob_str: str) -> str:
    clean_name = "".join(filter(str.isalpha, full_name))
    prefix = (clean_name[:2].capitalize() if len(clean_name) >= 2 else (clean_name.ljust(2, 'X').capitalize() if len(clean_name) == 1 else "ST"))
        
    try:
        parts = dob_str.split("-")
        dob_code = f"{parts[0]}{parts[1]}{parts[2][-2:]}"
    except Exception:
        dob_code = "010100"
        
    base_id = f"{prefix}{dob_code}"
    
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    student_id = base_id
    counter = 1
    while True:
        cursor.execute("SELECT 1 FROM users WHERE student_id = %s", (student_id,))
        if not cursor.fetchone():
            break
        student_id = f"{base_id}_{counter}"
        counter += 1
    cursor.close()
    release_db(conn)
    return student_id

def get_user_by_student_id(student_id: str):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM users WHERE LOWER(student_id) = LOWER(%s)", (student_id.strip(),))
    row = cursor.fetchone()
    cursor.close()
    release_db(conn)
    return dict(row) if row else None

def update_user_pin(user_id: int, new_pin: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET pin = %s WHERE user_id = %s", (new_pin, user_id))
    conn.commit()
    cursor.close()
    release_db(conn)
    sync_user_json_profile(user_id)

def check_and_update_inactivity(user_id: int) -> tuple[bool, int]:
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    now_epoch = int(get_ist_now().timestamp())
    
    cursor.execute("SELECT last_activity_epoch, pin FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    
    if not row or not row['pin']:
        cursor.close()
        release_db(conn)
        return False, 0

    last_epoch = row['last_activity_epoch'] or 0
    diff = now_epoch - last_epoch if last_epoch > 0 else 0

    if last_epoch > 0 and diff > 300:
        cursor.close()
        release_db(conn)
        return True, diff

    cursor.execute("UPDATE users SET last_activity_epoch = %s, last_active = %s WHERE user_id = %s", (now_epoch, get_ist_timestamp_str(), user_id))
    conn.commit()
    cursor.close()
    release_db(conn)
    return False, diff

def refresh_user_activity_epoch(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    now_epoch = int(get_ist_now().timestamp())
    cursor.execute("UPDATE users SET last_activity_epoch = %s, last_active = %s WHERE user_id = %s", (now_epoch, get_ist_timestamp_str(), user_id))
    conn.commit()
    cursor.close()
    release_db(conn)

def log_user_activity_time(user_id: int, seconds: int = 15):
    conn = get_db()
    cursor = conn.cursor()
    today_date = get_ist_date_str()
    now_str = get_ist_timestamp_str()
    now_epoch = int(get_ist_now().timestamp())
    
    cursor.execute('''
        INSERT INTO user_activity_time (user_id, date_str, seconds_spent)
        VALUES (%s, %s, %s)
        ON CONFLICT(user_id, date_str) DO UPDATE SET
            seconds_spent = user_activity_time.seconds_spent + EXCLUDED.seconds_spent
    ''', (user_id, today_date, seconds))

    cursor.execute("UPDATE users SET last_active = %s, last_activity_epoch = %s WHERE user_id = %s", (now_str, now_epoch, user_id))
    conn.commit()
    cursor.close()
    release_db(conn)

def toggle_user_ban_status(user_id: int) -> bool:
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT is_banned FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    current_status = row['is_banned'] if row and row['is_banned'] else 0
    new_status = 0 if current_status == 1 else 1
    cursor.execute("UPDATE users SET is_banned = %s WHERE user_id = %s", (new_status, user_id))
    conn.commit()
    cursor.close()
    release_db(conn)
    sync_user_json_profile(user_id)
    return bool(new_status)

def admin_update_user_name(user_id: int, new_name: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET full_name = %s WHERE user_id = %s", (new_name.strip(), user_id))
    conn.commit()
    cursor.close()
    release_db(conn)
    sync_user_json_profile(user_id)

def admin_delete_user_account(user_id: int):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT student_id FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    sid = row['student_id'] if row and row['student_id'] else f"USER_{user_id}"

    cursor.execute("DELETE FROM users WHERE user_id = %s", (user_id,))
    cursor.execute("DELETE FROM quiz_attempts WHERE user_id = %s", (user_id,))
    cursor.execute("DELETE FROM seen_questions WHERE user_id = %s", (user_id,))
    cursor.execute("DELETE FROM saved_questions WHERE user_id = %s", (user_id,))
    cursor.execute("DELETE FROM student_feedback WHERE user_id = %s", (user_id,))
    cursor.execute("DELETE FROM paused_quizzes WHERE user_id = %s", (user_id,))
    cursor.execute("DELETE FROM user_activity_time WHERE user_id = %s", (user_id,))
    cursor.execute("DELETE FROM student_queries WHERE user_id = %s", (user_id,))
    conn.commit()
    cursor.close()
    release_db(conn)

    json_path = os.path.join(USER_PROFILES_DIR, f"{sid}.json")
    if os.path.exists(json_path):
        try:
            os.remove(json_path)
        except Exception as e:
            logger.error(f"Error removing JSON profile on deletion: {e}")

def get_paid_users():
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM users WHERE paid_question_balance > 0 ORDER BY created_at DESC")
    rows = cursor.fetchall()
    cursor.close()
    release_db(conn)
    return [dict(r) for r in rows]

def sync_user_json_profile(user_id: int):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    cursor.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
    user_row = cursor.fetchone()
    if not user_row:
        cursor.close()
        release_db(conn)
        return

    user_dict = dict(user_row)
    student_id = user_dict.get("student_id") or f"USER_{user_id}"

    cursor.execute("SELECT * FROM quiz_attempts WHERE user_id = %s ORDER BY id DESC", (user_id,))
    attempts_rows = cursor.fetchall()
    
    cursor.execute("SELECT * FROM saved_questions WHERE user_id = %s ORDER BY id DESC", (user_id,))
    saved_rows = cursor.fetchall()

    cursor.execute("SELECT * FROM student_feedback WHERE user_id = %s ORDER BY id DESC", (user_id,))
    feedback_rows = cursor.fetchall()

    cursor.execute("SELECT date_str, seconds_spent FROM user_activity_time WHERE user_id = %s ORDER BY date_str DESC", (user_id,))
    time_rows = cursor.fetchall()
    cursor.close()
    release_db(conn)

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
                "total_quizzes": 0, "total_questions": 0, "total_correct": 0,
                "total_wrong": 0, "total_score": 0.0, "total_time_seconds": 0
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
            "vip_pass_expiry": user_dict.get("vip_pass_expiry")
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
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    now_str = get_ist_timestamp_str()
    now_epoch = int(get_ist_now().timestamp())
    
    student_id = generate_student_id(full_name, dob)
    demo_plan = PLAN_TIERS.get("FREE_DEMO", {"days": 2, "daily_limit": 20})
    demo_expiry = (datetime.now(IST) + timedelta(days=demo_plan["days"])).strftime("%Y-%m-%d %H:%M:%S IST")

    cursor.execute('''
        INSERT INTO users (user_id, student_id, full_name, username, phone_number, target_exam, dob, age, gender, pin, security_question, security_answer, country, state, referred_by, paid_question_balance, vip_pass_expiry, demo_used, last_profile_edit, last_active, last_activity_epoch, is_banned, is_verified, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s, 0, 1, %s)
        ON CONFLICT (user_id) DO UPDATE SET
            student_id=EXCLUDED.student_id,
            full_name=EXCLUDED.full_name,
            username=EXCLUDED.username,
            phone_number=EXCLUDED.phone_number,
            target_exam=EXCLUDED.target_exam,
            dob=EXCLUDED.dob,
            age=EXCLUDED.age,
            gender=EXCLUDED.gender,
            pin=EXCLUDED.pin,
            security_question=EXCLUDED.security_question,
            security_answer=EXCLUDED.security_answer,
            country=EXCLUDED.country,
            state=EXCLUDED.state,
            last_profile_edit=EXCLUDED.last_profile_edit,
            last_active=EXCLUDED.last_active,
            last_activity_epoch=EXCLUDED.last_activity_epoch,
            is_verified=1
    ''', (user_id, student_id, full_name, username, phone, target_exam, dob, age, gender, pin, sec_q, sec_a, country, state, referred_by, demo_plan["daily_limit"], demo_expiry, now_str, now_str, now_epoch, now_str))
    
    if referred_by and referred_by != user_id:
        cursor.execute("UPDATE users SET referral_count = referral_count + 1 WHERE user_id = %s", (referred_by,))
        cursor.execute("SELECT referral_count FROM users WHERE user_id = %s", (referred_by,))
        row = cursor.fetchone()
        if row and row['referral_count'] >= 4:
            cursor.execute("UPDATE users SET bonus_quota = bonus_quota + 10 WHERE user_id = %s", (referred_by,))
            
    conn.commit()
    cursor.close()
    release_db(conn)
    
    sync_user_json_profile(user_id)
    if referred_by:
        sync_user_json_profile(referred_by)

def can_user_edit_profile(user_id: int) -> tuple[bool, int]:
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT last_profile_edit FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    cursor.close()
    release_db(conn)

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
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    cursor.close()
    release_db(conn)
    return dict(row) if row else None

def get_all_users():
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM users ORDER BY created_at DESC")
    rows = cursor.fetchall()
    cursor.close()
    release_db(conn)
    return [dict(r) for r in rows]

def get_today_attempts(user_id):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    today_date = get_ist_date_str()
    cursor.execute('''
        SELECT SUM(questions_attempted) as total 
        FROM quiz_attempts 
        WHERE user_id = %s AND attempt_date = %s
    ''', (user_id, today_date))
    row = cursor.fetchone()
    cursor.close()
    release_db(conn)
    return row['total'] if row and row['total'] else 0

def record_quiz_result(user_id, quiz_id="computer_awareness_mock", score=0.0, total_questions=0, correct_count=0, wrong_count=0, skipped_count=0, time_taken=0, question_details=None):
    conn = get_db()
    cursor = conn.cursor()
    today_date = get_ist_date_str()
    timestamp_str = get_ist_timestamp_str()
    details_str = json.dumps(question_details) if question_details else json.dumps([])
    
    cursor.execute('''
        INSERT INTO quiz_attempts (user_id, quiz_id, questions_attempted, total_questions, correct_answers, wrong_answers, skipped_count, score, time_taken, attempt_timestamp, attempt_date, details_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ''', (user_id, quiz_id, total_questions, total_questions, correct_count, wrong_count, skipped_count, score, time_taken, timestamp_str, today_date, details_str))
    conn.commit()
    cursor.close()
    release_db(conn)
    sync_user_json_profile(user_id)

def get_seen_question_ids(user_id):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT question_id FROM seen_questions WHERE user_id = %s", (user_id,))
    rows = cursor.fetchall()
    cursor.close()
    release_db(conn)
    return {str(r['question_id']) for r in rows}

def mark_questions_as_seen(user_id, question_ids):
    conn = get_db()
    cursor = conn.cursor()
    now_str = get_ist_timestamp_str()
    for qid in question_ids:
        cursor.execute("INSERT INTO seen_questions (user_id, question_id, seen_at) VALUES (%s, %s, %s) ON CONFLICT (user_id, question_id) DO NOTHING", (user_id, str(qid), now_str))
    conn.commit()
    cursor.close()
    release_db(conn)

def save_question_to_db(user_id: int, q_text: str, options: list, correct_option: int, explanation: str):
    conn = get_db()
    cursor = conn.cursor()
    now_str = get_ist_timestamp_str()
    try:
        cursor.execute('''
            INSERT INTO saved_questions (user_id, question_text, options_json, correct_option, explanation, saved_at)
            VALUES (%s, %s, %s, %s, %s, %s)
        ''', (user_id, q_text, json.dumps(options), correct_option, explanation, now_str))
        conn.commit()
        success = True
    except Exception:
        success = False
    cursor.close()
    release_db(conn)
    sync_user_json_profile(user_id)
    return success

def get_saved_questions(user_id: int):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM saved_questions WHERE user_id = %s ORDER BY id DESC", (user_id,))
    rows = cursor.fetchall()
    cursor.close()
    release_db(conn)
    return [dict(r) for r in rows]

def save_student_feedback(user_id: int, full_name: str, feedback_text: str):
    conn = get_db()
    cursor = conn.cursor()
    now_str = get_ist_timestamp_str()
    cursor.execute('''
        INSERT INTO student_feedback (user_id, full_name, feedback_text, submitted_at)
        VALUES (%s, %s, %s, %s)
    ''', (user_id, full_name, feedback_text, now_str))
    conn.commit()
    cursor.close()
    release_db(conn)
    sync_user_json_profile(user_id)

def get_all_student_feedbacks(limit: int = 15):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT full_name, feedback_text, submitted_at FROM student_feedback ORDER BY id DESC LIMIT %s", (limit,))
    rows = cursor.fetchall()
    cursor.close()
    release_db(conn)
    return [dict(r) for r in rows]

def set_maintenance_until(epoch_timestamp: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE bot_settings SET value = %s WHERE key = 'maintenance_until'", (str(epoch_timestamp),))
    conn.commit()
    cursor.close()
    release_db(conn)

def get_maintenance_until() -> int:
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT value FROM bot_settings WHERE key = 'maintenance_until'")
    row = cursor.fetchone()
    cursor.close()
    release_db(conn)
    return int(row['value']) if row and str(row['value']).isdigit() else 0

def save_paused_quiz_state(user_id: int, quiz_state: dict):
    conn = get_db()
    cursor = conn.cursor()
    now_str = get_ist_timestamp_str()
    cursor.execute('''
        INSERT INTO paused_quizzes (user_id, quiz_state, saved_at)
        VALUES (%s, %s, %s)
        ON CONFLICT(user_id) DO UPDATE SET
            quiz_state = EXCLUDED.quiz_state,
            saved_at = EXCLUDED.saved_at
    ''', (user_id, json.dumps(quiz_state), now_str))
    conn.commit()
    cursor.close()
    release_db(conn)

def get_paused_quiz_state(user_id: int):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT quiz_state FROM paused_quizzes WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    cursor.close()
    release_db(conn)
    return json.loads(row['quiz_state']) if row and row['quiz_state'] else None

def clear_paused_quiz_state(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM paused_quizzes WHERE user_id = %s", (user_id,))
    conn.commit()
    cursor.close()
    release_db(conn)

# ==========================================
# 🎟️ PROMO CODE GENERATOR DATABASE SERVICES
# ==========================================

def create_promo_code(code: str, discount_type: str, discount_value: float, days_valid: int, created_by: int) -> dict:
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        clean_code = code.strip().upper()
        valid_until = datetime.now(IST) + timedelta(days=days_valid)
        cursor.execute("""
            INSERT INTO promo_codes (code, discount_type, discount_value, valid_until, created_by)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *;
        """, (clean_code, discount_type.upper(), discount_value, valid_until, created_by))
        new_code = cursor.fetchone()
        conn.commit()
        return dict(new_code)
    except Exception as e:
        conn.rollback()
        logger.error(f"Error creating promo code: {e}")
        return {"error": str(e)}
    finally:
        cursor.close()
        release_db(conn)

def apply_promo_code(user_id: int, code: str, original_price: float) -> dict:
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        clean_code = code.strip().upper()
        cursor.execute("SELECT * FROM promo_codes WHERE code = %s AND is_active = TRUE", (clean_code,))
        promo = cursor.fetchone()
        if not promo:
            return {"success": False, "reason": "INVALID_CODE"}
        
        if promo['valid_until'] < datetime.now(IST):
            return {"success": False, "reason": "EXPIRED"}
        
        cursor.execute("SELECT id FROM promo_redemptions WHERE promo_id = %s AND user_id = %s", (promo['id'], user_id))
        if cursor.fetchone():
            return {"success": False, "reason": "ALREADY_USED"}
        
        disc_type = promo['discount_type']
        disc_val = float(promo['discount_value'])
        if disc_type == "PERCENT":
            discount_amount = round((original_price * disc_val) / 100.0, 2)
        else:
            discount_amount = disc_val
            
        final_price = max(0.0, round(original_price - discount_amount, 2))
        return {
            "success": True,
            "promo_id": promo['id'],
            "code": clean_code,
            "discount_amount": discount_amount,
            "final_price": final_price
        }
    finally:
        cursor.close()
        release_db(conn)

def record_promo_redemption(promo_id: int, user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO promo_redemptions (promo_id, user_id)
            VALUES (%s, %s) ON CONFLICT DO NOTHING;
        """, (promo_id, user_id))
        conn.commit()
    finally:
        cursor.close()
        release_db(conn)

# ==========================================
# 📢 ANNOUNCEMENT & BROADCAST ADVANCED SERVICES
# ==========================================

def schedule_announcement(message_text: str, media_file_id: str, media_type: str, scheduled_time: datetime, created_by: int) -> dict:
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            INSERT INTO scheduled_announcements (message_text, media_file_id, media_type, scheduled_time, created_by)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *;
        """, (message_text, media_file_id, media_type, scheduled_time, created_by))
        annc = cursor.fetchone()
        conn.commit()
        return dict(annc)
    except Exception as e:
        conn.rollback()
        logger.error(f"Error scheduling announcement: {e}")
        return {"error": str(e)}
    finally:
        cursor.close()
        release_db(conn)

def fetch_pending_announcements() -> list:
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        now_ist_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            SELECT * FROM scheduled_announcements 
            WHERE status = 'PENDING' AND scheduled_time <= %s
            ORDER BY scheduled_time ASC;
        """, (now_ist_str,))
        return [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Error fetching pending announcements: {e}")
        return []
    finally:
        cursor.close()
        release_db(conn)

def update_announcement_status(announcement_id: int, status: str):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE scheduled_announcements SET status = %s WHERE id = %s", (status, announcement_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Error updating announcement status: {e}")
    finally:
        cursor.close()
        release_db(conn)

def update_announcement_content(announcement_id: int, new_text: str, media_file_id: str = None, media_type: str = "text") -> bool:
    conn = get_db()
    cursor = conn.cursor()
    try:
        if media_file_id:
            cursor.execute("""
                UPDATE scheduled_announcements 
                SET message_text = %s, media_file_id = %s, media_type = %s 
                WHERE id = %s
            """, (new_text, media_file_id, media_type, announcement_id))
        else:
            cursor.execute("""
                UPDATE scheduled_announcements 
                SET message_text = %s 
                WHERE id = %s
            """, (new_text, announcement_id))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"Error updating announcement content: {e}")
        return False
    finally:
        cursor.close()
        release_db(conn)

def update_announcement_time(announcement_id: int, new_time: datetime) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE scheduled_announcements 
            SET scheduled_time = %s 
            WHERE id = %s
        """, (new_time, announcement_id))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"Error updating announcement time: {e}")
        return False
    finally:
        cursor.close()
        release_db(conn)

def delete_scheduled_announcement(announcement_id: int) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM broadcast_deliveries WHERE announcement_id = %s", (announcement_id,))
        cursor.execute("DELETE FROM scheduled_announcements WHERE id = %s", (announcement_id,))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"Error deleting scheduled announcement: {e}")
        return False
    finally:
        cursor.close()
        release_db(conn)

def create_instant_broadcast_record(message_text: str, media_file_id: str = None, media_type: str = "text", created_by: int = None) -> int:
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        now_dt = datetime.now(IST)
        cursor.execute("""
            INSERT INTO scheduled_announcements (message_text, media_file_id, media_type, scheduled_time, status, created_by)
            VALUES (%s, %s, %s, %s, 'SENT', %s)
            RETURNING id;
        """, (message_text, media_file_id, media_type, now_dt, created_by))
        row = cursor.fetchone()
        conn.commit()
        return row['id'] if row else 0
    except Exception as e:
        conn.rollback()
        logger.error(f"Error recording instant broadcast: {e}")
        return 0
    finally:
        cursor.close()
        release_db(conn)

def record_broadcast_delivery(announcement_id: int, user_id: int, message_id: int):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO broadcast_deliveries (announcement_id, user_id, message_id)
            VALUES (%s, %s, %s)
        """, (announcement_id, user_id, message_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Error recording broadcast delivery: {e}")
    finally:
        cursor.close()
        release_db(conn)

def get_broadcast_deliveries(announcement_id: int) -> list:
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT user_id, message_id FROM broadcast_deliveries WHERE announcement_id = %s", (announcement_id,))
        return [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Error getting broadcast deliveries: {e}")
        return []
    finally:
        cursor.close()
        release_db(conn)

def get_pending_announcements_list() -> list:
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT * FROM scheduled_announcements 
            WHERE status = 'PENDING' 
            ORDER BY scheduled_time ASC;
        """)
        return [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Error getting pending announcements: {e}")
        return []
    finally:
        cursor.close()
        release_db(conn)

def get_sent_announcements_list(limit: int = 20) -> list:
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT a.*, COUNT(b.id) as delivery_count 
            FROM scheduled_announcements a
            LEFT JOIN broadcast_deliveries b ON a.id = b.announcement_id
            WHERE a.status = 'SENT'
            GROUP BY a.id
            ORDER BY a.id DESC LIMIT %s;
        """, (limit,))
        return [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Error getting sent announcements: {e}")
        return []
    finally:
        cursor.close()
        release_db(conn)

def get_announcement_by_id(announcement_id: int) -> dict:
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT * FROM scheduled_announcements WHERE id = %s", (announcement_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"Error getting announcement by ID: {e}")
        return None
    finally:
        cursor.close()
        release_db(conn)