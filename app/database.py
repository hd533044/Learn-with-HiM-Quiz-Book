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

def calculate_discounted_price(original_price, discount_percent) -> int:
    try:
        orig = float(original_price)
        disc = float(discount_percent)
        if disc <= 0 or orig <= 0:
            return int(orig)
        discount_amount = (orig * disc) / 100.0
        final_price = max(1, int(round(orig - discount_amount)))
        return final_price
    except Exception:
        return int(original_price) if original_price else 1

def infer_plan_key_from_amount(amount: float) -> str:
    try:
        amt = float(amount)
        if amt in (5.0, 4.0):
            return "BRONZE"
        elif amt in (10.0, 8.0, 9.0, 7.0):
            return "SILVER"
        elif amt in (15.0, 12.0, 11.0, 13.0):
            return "GOLD"
        elif amt in (20.0, 16.0, 17.0, 18.0):
            if amt == 20.0:
                return "LEARNWITHHIM"
            return "DIAMOND"
        elif amt in (25.0,):
            return "LEARNWITHHIM"
        elif amt in (40.0, 32.0, 30.0, 36.0):
            return "PLATINUM"
        elif amt in (50.0, 40.0, 45.0, 35.0):
            return "RUBY"
        elif amt in (80.0, 64.0, 60.0, 72.0):
            return "MEGA"
    except Exception:
        pass
    return "LEARNWITHHIM"

def recalculate_and_restore_user_plans(user_id: int) -> dict:
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
        user_curr = cursor.fetchone()
        if not user_curr:
            return {"updated": False, "has_paid_plan": False}

        cursor.execute("""
            SELECT * FROM payment_transactions 
            WHERE user_id = %s 
              AND payment_id LIKE 'pay_%%' 
              AND amount_paid > 0 
              AND plan_key != 'FREE_DEMO'
            ORDER BY id ASC
        """, (user_id,))
        paid_txns = cursor.fetchall()

        now_ist = datetime.now(IST)
        active_paid_txns = []
        total_paid_quota = 0
        max_expiry_dt = None
        latest_txn = None

        for txn in paid_txns:
            exp_str = txn.get("expiry_at")
            exp_dt = None
            if exp_str:
                try:
                    clean_exp = exp_str.replace(" IST", "").strip()
                    exp_dt = datetime.strptime(clean_exp, "%Y-%m-%d %H:%M:%S")
                    exp_dt = IST.localize(exp_dt) if exp_dt.tzinfo is None else exp_dt
                except Exception:
                    pass
            
            if not exp_dt:
                c_str = txn.get("created_at", "")
                v_days = txn.get("validity_days") or 30
                try:
                    c_dt = datetime.strptime(c_str.replace(" IST", "").strip(), "%d %b %Y, %I:%M %p")
                    c_dt = IST.localize(c_dt) if c_dt.tzinfo is None else c_dt
                    exp_dt = c_dt + timedelta(days=v_days)
                except Exception:
                    pass

            if exp_dt and exp_dt > now_ist:
                active_paid_txns.append(txn)
                total_paid_quota += (txn.get("daily_quota") or 0)
                if max_expiry_dt is None or exp_dt > max_expiry_dt:
                    max_expiry_dt = exp_dt
                latest_txn = txn

        if active_paid_txns and max_expiry_dt:
            expiry_str = max_expiry_dt.strftime("%Y-%m-%d %H:%M:%S IST")
            final_quota = max(DAILY_QUESTION_LIMIT, total_paid_quota)
            payment_id = latest_txn.get("payment_id")
            payment_timestamp = latest_txn.get("created_at")
            plan_name = latest_txn.get("plan_name", "VIP Plan")

            curr_quota = user_curr.get("paid_question_balance", 0)
            curr_expiry = user_curr.get("vip_pass_expiry")
            
            diff_sec = max(0, int((max_expiry_dt - now_ist).total_seconds()))
            remaining_days = max(1, int(diff_sec // 86400))

            if curr_quota == final_quota and curr_expiry == expiry_str:
                return {
                    "updated": False,
                    "has_paid_plan": True,
                    "active_count": len(active_paid_txns),
                    "quota": final_quota,
                    "expiry_str": expiry_str,
                    "remaining_days": remaining_days,
                    "full_name": user_curr.get("full_name", "Student"),
                    "student_id": user_curr.get("student_id", f"USER_{user_id}"),
                    "payment_id": payment_id,
                    "plan_name": plan_name
                }

            cursor.execute("""
                UPDATE users 
                SET paid_question_balance = %s,
                    vip_pass_expiry = %s,
                    payment_id = %s,
                    payment_timestamp = %s,
                    demo_used = 1
                WHERE user_id = %s
            """, (final_quota, expiry_str, payment_id, payment_timestamp, user_id))
            conn.commit()

            return {
                "updated": True,
                "has_paid_plan": True,
                "active_count": len(active_paid_txns),
                "quota": final_quota,
                "expiry_str": expiry_str,
                "remaining_days": remaining_days,
                "full_name": user_curr.get("full_name", "Student"),
                "student_id": user_curr.get("student_id", f"USER_{user_id}"),
                "payment_id": payment_id,
                "plan_name": plan_name
            }
        else:
            return {
                "updated": False,
                "has_paid_plan": False,
                "quota": DAILY_QUESTION_LIMIT,
                "expiry_str": None,
                "remaining_days": 0
            }
    except Exception as e:
        conn.rollback()
        logger.error(f"[PLAN RESTORE ERROR] {e}")
        return {"updated": False, "has_paid_plan": False, "error": str(e)}
    finally:
        cursor.close()
        release_db(conn)

def auto_sync_uncredited_paid_users() -> list:
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    candidate_uids = []
    try:
        cursor.execute("""
            SELECT DISTINCT pt.user_id 
            FROM payment_transactions pt
            INNER JOIN users u ON pt.user_id = u.user_id
            WHERE pt.payment_id LIKE 'pay_%%' 
              AND pt.amount_paid > 0 
              AND pt.plan_key != 'FREE_DEMO'
        """)
        candidate_uids = [r['user_id'] for r in cursor.fetchall()]
    except Exception as e:
        logger.error(f"[AUTO SYNC FETCH CANDIDATES ERROR] {e}")
    finally:
        cursor.close()
        release_db(conn)

    credited_students = []
    for uid in candidate_uids:
        try:
            res = recalculate_and_restore_user_plans(uid)
            if res.get("updated") and res.get("has_paid_plan"):
                sync_user_json_profile(uid)
                res["user_id"] = uid
                credited_students.append(res)
        except Exception as err:
            logger.error(f"[AUTO SYNC USER ERROR] {uid}: {err}")

    return credited_students

def init_db():
    init_pool()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS deleted_blocked_users (
            user_id BIGINT PRIMARY KEY,
            student_id TEXT,
            full_name TEXT,
            phone_number TEXT,
            target_exam TEXT,
            deleted_at TEXT
        )
    ''')
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
            payment_id TEXT,
            payment_timestamp TEXT,
            temporary_bonus_quota INTEGER DEFAULT 0,
            gift_granted_date TEXT,
            created_at TEXT
        )
    ''')

    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS payment_id TEXT;")
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS payment_timestamp TEXT;")
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS bonus_quota INTEGER DEFAULT 0;")
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS temporary_bonus_quota INTEGER DEFAULT 0;")
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS gift_granted_date TEXT;")
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS paid_question_balance INTEGER DEFAULT 0;")
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS vip_pass_expiry TEXT;")
    cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS demo_used INTEGER DEFAULT 0;")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS payment_transactions (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            payment_id TEXT UNIQUE NOT NULL,
            plan_key TEXT,
            plan_name TEXT,
            amount_paid NUMERIC(10, 2) DEFAULT 0.0,
            daily_quota INTEGER DEFAULT 0,
            validity_days INTEGER DEFAULT 0,
            created_at TEXT,
            expiry_at TEXT
        )
    ''')
    cursor.execute("ALTER TABLE payment_transactions ADD COLUMN IF NOT EXISTS expiry_at TEXT;")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admin_security (
            id INT PRIMARY KEY,
            admin_id BIGINT,
            password_hash TEXT,
            dob_recovery TEXT,
            email_recovery TEXT,
            updated_at TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS command_analytics (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            command_name TEXT,
            executed_at TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pdf_generation_logs (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            pdf_type TEXT,
            generated_at TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS quiz_attempts (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            quiz_id TEXT DEFAULT 'computer_awareness_mock',
            quiz_mode TEXT DEFAULT 'PRACTICE',
            mock_number INTEGER DEFAULT 0,
            subject TEXT,
            selected_topics TEXT,
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

    cursor.execute("ALTER TABLE quiz_attempts ADD COLUMN IF NOT EXISTS quiz_mode TEXT DEFAULT 'PRACTICE';")
    cursor.execute("ALTER TABLE quiz_attempts ADD COLUMN IF NOT EXISTS mock_number INTEGER DEFAULT 0;")
    cursor.execute("ALTER TABLE quiz_attempts ADD COLUMN IF NOT EXISTS subject TEXT;")
    cursor.execute("ALTER TABLE quiz_attempts ADD COLUMN IF NOT EXISTS selected_topics TEXT;")

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
        CREATE TABLE IF NOT EXISTS platform_likes (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            quiz_attempt_id BIGINT,
            liked_at TEXT,
            UNIQUE(user_id, quiz_attempt_id)
        )
    ''')
    cursor.execute("ALTER TABLE platform_likes ADD COLUMN IF NOT EXISTS quiz_attempt_id BIGINT;")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS student_queries (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            student_name TEXT,
            query_text TEXT,
            photo_file_id TEXT,
            voice_file_id TEXT,
            video_file_id TEXT,
            audio_file_id TEXT,
            doc_file_id TEXT,
            media_type TEXT DEFAULT 'text',
            admin_reply TEXT,
            status TEXT DEFAULT 'PENDING',
            created_at TEXT,
            replied_at TEXT
        )
    ''')
    cursor.execute("ALTER TABLE student_queries ADD COLUMN IF NOT EXISTS photo_file_id TEXT;")
    cursor.execute("ALTER TABLE student_queries ADD COLUMN IF NOT EXISTS voice_file_id TEXT;")
    cursor.execute("ALTER TABLE student_queries ADD COLUMN IF NOT EXISTS video_file_id TEXT;")
    cursor.execute("ALTER TABLE student_queries ADD COLUMN IF NOT EXISTS audio_file_id TEXT;")
    cursor.execute("ALTER TABLE student_queries ADD COLUMN IF NOT EXISTS doc_file_id TEXT;")
    cursor.execute("ALTER TABLE student_queries ADD COLUMN IF NOT EXISTS media_type TEXT DEFAULT 'text';")

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

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS broadcast_deliveries (
            id SERIAL PRIMARY KEY,
            announcement_id INT,
            user_id BIGINT NOT NULL,
            message_id BIGINT NOT NULL,
            delivered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS blocked_bot_users (
            user_id BIGINT PRIMARY KEY,
            blocked_at TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS flash_sales (
            id SERIAL PRIMARY KEY,
            sale_name TEXT NOT NULL,
            discount_percent NUMERIC(5, 2) NOT NULL,
            valid_from TIMESTAMP NOT NULL,
            valid_until TIMESTAMP NOT NULL,
            is_active BOOLEAN DEFAULT TRUE,
            broadcast_sent BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_by BIGINT
        )
    ''')
    
    cursor.execute("INSERT INTO bot_settings (key, value) VALUES ('maintenance_until', '0') ON CONFLICT (key) DO NOTHING")
    
    conn.commit()
    cursor.close()
    release_db(conn)

def record_quiz_like(user_id: int, quiz_attempt_id: int = 0) -> tuple[bool, int]:
    """
    Records 1 like per unique quiz attempt for a user.
    Returns (is_newly_liked, total_likes_count).
    """
    conn = get_db()
    cursor = conn.cursor()
    try:
        now_str = get_ist_timestamp_str()
        cursor.execute(
            """
            INSERT INTO platform_likes (user_id, quiz_attempt_id, liked_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, quiz_attempt_id) DO NOTHING
            RETURNING id;
            """,
            (user_id, quiz_attempt_id, now_str)
        )
        row = cursor.fetchone()
        conn.commit()
        
        is_newly_liked = (row is not None)

        cursor.execute("SELECT COUNT(*) FROM platform_likes")
        total_likes = cursor.fetchone()[0]
        return is_newly_liked, total_likes
    except Exception as e:
        conn.rollback()
        logger.error(f"[RECORD QUIZ LIKE ERROR] {e}")
        return False, 0
    finally:
        cursor.close()
        release_db(conn)

def get_total_platform_likes() -> int:
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM platform_likes")
        res = cursor.fetchone()
        return res[0] if res else 0
    except Exception as e:
        logger.error(f"[GET TOTAL LIKES ERROR] {e}")
        return 0
    finally:
        cursor.close()
        release_db(conn)

def get_total_registered_users_count() -> int:
    """Returns count of active registered students, excluding banned and blocked users."""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_banned = 0 AND is_verified = 1")
        res = cursor.fetchone()
        return res[0] if res else 0
    except Exception as e:
        logger.error(f"[GET TOTAL REGISTERED USERS ERROR] {e}")
        return 0
    finally:
        cursor.close()
        release_db(conn)

def get_next_mock_number(user_id: int, quiz_mode: str) -> int:
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COALESCE(MAX(mock_number), 0) + 1 FROM quiz_attempts WHERE user_id = %s AND quiz_mode = %s", (user_id, quiz_mode))
        res = cursor.fetchone()
        return res[0] if res else 1
    except Exception as e:
        logger.error(f"[GET NEXT MOCK NUMBER ERROR] {e}")
        return 1
    finally:
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

def get_user_by_phone(phone_number: str):
    if not phone_number:
        return None
    clean_phone = "".join(filter(str.isdigit, str(phone_number)))
    if len(clean_phone) > 10:
        clean_phone = clean_phone[-10:]
    if len(clean_phone) < 8:
        return None

    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute(
        "SELECT * FROM users WHERE REPLACE(REPLACE(REPLACE(phone_number, '+', ''), ' ', ''), '-', '') LIKE %s LIMIT 1",
        (f"%{clean_phone}",)
    )
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

def record_blocked_user(user_id: int):
    """
    Requirement 5: Keeps ALL user data intact (no deletion).
    Changes status to blocked/inactive (is_banned = 2) and records timestamp in blocked_bot_users.
    Removes user from the active registered count.
    """
    conn = get_db()
    cursor = conn.cursor()
    try:
        now_str = get_ist_timestamp_str()
        cursor.execute("UPDATE users SET is_banned = 2, last_active = %s WHERE user_id = %s", (now_str, user_id))
        cursor.execute("""
            INSERT INTO blocked_bot_users (user_id, blocked_at)
            VALUES (%s, %s)
            ON CONFLICT (user_id) DO UPDATE SET blocked_at = EXCLUDED.blocked_at;
        """, (user_id, now_str))
        conn.commit()
        sync_user_json_profile(user_id)
    except Exception as e:
        conn.rollback()
        logger.error(f"[RECORD BLOCKED USER ERROR] {user_id}: {e}")
    finally:
        cursor.close()
        release_db(conn)

def admin_delete_user_account(user_id: int):
    """Permanent manual deletion by master admin."""
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT student_id FROM users WHERE user_id = %s", (user_id,))
        u = cursor.fetchone()

        cursor.execute("DELETE FROM payment_transactions WHERE user_id = %s", (user_id,))
        cursor.execute("DELETE FROM quiz_attempts WHERE user_id = %s", (user_id,))
        cursor.execute("DELETE FROM seen_questions WHERE user_id = %s", (user_id,))
        cursor.execute("DELETE FROM saved_questions WHERE user_id = %s", (user_id,))
        cursor.execute("DELETE FROM student_feedback WHERE user_id = %s", (user_id,))
        cursor.execute("DELETE FROM platform_likes WHERE user_id = %s", (user_id,))
        cursor.execute("DELETE FROM paused_quizzes WHERE user_id = %s", (user_id,))
        cursor.execute("DELETE FROM user_activity_time WHERE user_id = %s", (user_id,))
        cursor.execute("DELETE FROM student_queries WHERE user_id = %s", (user_id,))
        cursor.execute("DELETE FROM blocked_bot_users WHERE user_id = %s", (user_id,))
        cursor.execute("DELETE FROM users WHERE user_id = %s", (user_id,))
        conn.commit()

        if u:
            sid = u.get("student_id") or f"USER_{user_id}"
            json_path = os.path.join(USER_PROFILES_DIR, f"{sid}.json")
            if os.path.exists(json_path):
                try:
                    os.remove(json_path)
                except Exception:
                    pass
    except Exception as e:
        conn.rollback()
        logger.error(f"[ADMIN DELETE ACCOUNT ERROR] {user_id}: {e}")
    finally:
        cursor.close()
        release_db(conn)

def get_paid_users():
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM users WHERE paid_question_balance > 0 AND is_banned != 2 ORDER BY created_at DESC")
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
    mock_mode_counts = {}

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
        mode = ad.get("quiz_mode", "PRACTICE")
        mock_mode_counts[mode] = mock_mode_counts.get(mode, 0) + 1

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
            "mock_mode_breakdown": mock_mode_counts,
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
            is_banned=0,
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

    if not row:
        return None

    u_dict = dict(row)

    if u_dict.get("paid_question_balance", 0) <= DAILY_QUESTION_LIMIT or not u_dict.get("vip_pass_expiry"):
        restored = recalculate_and_restore_user_plans(user_id)
        if restored.get("updated") and restored.get("has_paid_plan"):
            conn = get_db()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
            updated_row = cursor.fetchone()
            cursor.close()
            release_db(conn)
            if updated_row:
                return dict(updated_row)

    return u_dict

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

def record_quiz_result(user_id, quiz_id="computer_awareness_mock", score=0.0, total_questions=0, correct_count=0, wrong_count=0, skipped_count=0, time_taken=0, question_details=None, quiz_mode="PRACTICE", mock_number=0, subject=None, selected_topics=None) -> int:
    conn = get_db()
    cursor = conn.cursor()
    today_date = get_ist_date_str()
    timestamp_str = get_ist_timestamp_str()
    details_str = json.dumps(question_details) if question_details else json.dumps([])
    topics_str = json.dumps(selected_topics) if isinstance(selected_topics, list) else (str(selected_topics) if selected_topics else None)
    
    cursor.execute('''
        INSERT INTO quiz_attempts (user_id, quiz_id, quiz_mode, mock_number, subject, selected_topics, questions_attempted, total_questions, correct_answers, wrong_answers, skipped_count, score, time_taken, attempt_timestamp, attempt_date, details_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id;
    ''', (user_id, quiz_id, quiz_mode, mock_number, subject, topics_str, total_questions, total_questions, correct_count, wrong_count, skipped_count, score, time_taken, timestamp_str, today_date, details_str))
    res = cursor.fetchone()
    attempt_id = res[0] if res else 0
    conn.commit()
    cursor.close()
    release_db(conn)
    sync_user_json_profile(user_id)
    return attempt_id

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
        cursor.execute(
            """
            INSERT INTO seen_questions (user_id, question_id, seen_at) 
            VALUES (%s, %s, %s) 
            ON CONFLICT (user_id, question_id) 
            DO UPDATE SET seen_at = EXCLUDED.seen_at
            """,
            (user_id, str(qid), now_str)
        )
    conn.commit()
    cursor.close()
    release_db(conn)

def reset_user_seen_questions_for_ids(user_id: int, question_ids: list):
    if not user_id or not question_ids:
        return
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM seen_questions WHERE user_id = %s AND question_id = ANY(%s)",
            (user_id, [str(qid) for qid in question_ids])
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"[DATABASE ERROR] Failed resetting seen questions: {e}")
    finally:
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

def get_student_feedbacks_count() -> int:
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM student_feedback")
        res = cursor.fetchone()
        return res[0] if res else 0
    except Exception as e:
        logger.error(f"[GET FEEDBACKS COUNT ERROR] {e}")
        return 0
    finally:
        cursor.close()
        release_db(conn)

def get_paginated_student_feedbacks(page: int = 0, limit: int = 5) -> list:
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    offset = max(0, page * limit)
    try:
        cursor.execute(
            "SELECT full_name, feedback_text, submitted_at FROM student_feedback ORDER BY id DESC LIMIT %s OFFSET %s",
            (limit, offset)
        )
        rows = cursor.fetchall()
        return [dict(r) for r in rows] if rows else []
    except Exception as e:
        logger.error(f"[GET PAGINATED FEEDBACKS ERROR] {e}")
        return []
    finally:
        cursor.close()
        release_db(conn)

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

def create_flash_sale(sale_name: str, discount_percent: float, valid_until: datetime, created_by: int) -> dict:
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        now_dt = datetime.now(IST).replace(tzinfo=None)
        cursor.execute("UPDATE flash_sales SET is_active = FALSE WHERE is_active = TRUE;")
        cursor.execute("""
            INSERT INTO flash_sales (sale_name, discount_percent, valid_from, valid_until, is_active, created_by)
            VALUES (%s, %s, %s, %s, TRUE, %s)
            RETURNING *;
        """, (sale_name.strip(), float(discount_percent), now_dt, valid_until, created_by))
        row = cursor.fetchone()
        conn.commit()
        res = dict(row) if row else {}
        if res.get("discount_percent") is not None:
            res["discount_percent"] = float(res["discount_percent"])
        return res
    except Exception as e:
        conn.rollback()
        logger.error(f"Error creating flash sale: {e}")
        return {"error": str(e)}
    finally:
        cursor.close()
        release_db(conn)

def get_active_flash_sale() -> dict:
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        now_dt = datetime.now(IST).replace(tzinfo=None)
        cursor.execute("""
            SELECT * FROM flash_sales 
            WHERE is_active = TRUE AND valid_until > %s
            ORDER BY id DESC LIMIT 1;
        """, (now_dt,))
        row = cursor.fetchone()
        if not row:
            cursor.execute("UPDATE flash_sales SET is_active = FALSE WHERE is_active = TRUE AND valid_until <= %s;", (now_dt,))
            conn.commit()
            return None
        res = dict(row)
        if res.get("discount_percent") is not None:
            res["discount_percent"] = float(res["discount_percent"])
        return res
    except Exception as e:
        logger.error(f"Error getting active flash sale: {e}")
        return None
    finally:
        cursor.close()
        release_db(conn)

def stop_active_flash_sale() -> bool:
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE flash_sales SET is_active = FALSE WHERE is_active = TRUE;")
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"Error stopping flash sale: {e}")
        return False
    finally:
        cursor.close()
        release_db(conn)

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

def get_blocked_bot_users() -> list:
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT b.user_id, b.blocked_at, u.full_name, u.student_id, u.username, u.phone_number 
            FROM blocked_bot_users b
            LEFT JOIN users u ON b.user_id = u.user_id
            ORDER BY b.blocked_at DESC;
        """)
        return [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Error fetching blocked bot users: {e}")
        return []
    finally:
        cursor.close()
        release_db(conn)