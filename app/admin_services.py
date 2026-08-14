import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
import pytz
from app.database import get_db, release_db

IST = pytz.timezone("Asia/Kolkata")

# ==========================================
# 🎟️ PROMO CODE ENGINE
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
    except psycopg2.IntegrityError:
        conn.rollback()
        return {"error": "CODE_EXISTS"}
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
# 📢 SCHEDULED ANNOUNCEMENT ENGINE (FIXED TIMEZONES)
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
        
        announcement = cursor.fetchone()
        conn.commit()
        return dict(announcement)
    finally:
        cursor.close()
        release_db(conn)


def fetch_pending_announcements() -> list:
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT * FROM scheduled_announcements 
            WHERE status = 'PENDING' AND scheduled_time <= (NOW() AT TIME ZONE 'Asia/Kolkata')
            ORDER BY scheduled_time ASC;
        """)
        return [dict(r) for r in cursor.fetchall()]
    finally:
        cursor.close()
        release_db(conn)


def update_announcement_status(announcement_id: int, status: str):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE scheduled_announcements SET status = %s WHERE id = %s", (status, announcement_id))
        conn.commit()
    finally:
        cursor.close()
        release_db(conn)