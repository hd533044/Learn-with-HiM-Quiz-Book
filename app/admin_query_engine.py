import re
import os
import json
import logging
from datetime import datetime, timedelta
import pytz
import urllib.request
from psycopg2.extras import RealDictCursor
from app.database import get_db, release_db, get_ist_now, get_ist_date_str
from app.pdf_generator import generate_admin_query_dataset_pdf

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

INDIAN_STATES = [
    "andhra pradesh", "arunachal pradesh", "assam", "bihar", "chhattisgarh", "goa",
    "gujarat", "haryana", "himachal pradesh", "jharkhand", "karnataka", "kerala",
    "madhya pradesh", "maharashtra", "manipur", "meghalaya", "mizoram", "nagaland",
    "odisha", "punjab", "rajasthan", "sikkim", "tamil nadu", "telangana", "tripura",
    "uttar pradesh", "uttarakhand", "west bengal", "andaman", "chandigarh", "delhi",
    "jammu", "kashmir", "ladakh", "puducherry", "up", "mp"
]

EXAM_KEYWORDS = {
    "cgl": "SSC CGL", "ssc cgl": "SSC CGL",
    "chsl": "SSC CHSL", "ssc chsl": "SSC CHSL",
    "bsf": "BSF HCM", "bsf hcm": "BSF HCM",
    "capf": "CAPF HCM", "capf hcm": "CAPF HCM",
    "asi": "ASI STENO", "asi steno": "ASI STENO", "steno": "ASI STENO",
    "dp": "DP HCM", "dp hcm": "DP HCM", "delhi police": "DP HCM",
    "cisf": "CISF HCM", "cisf hcm": "CISF HCM",
    "ntpc": "RAILWAY NTPC", "railway": "RAILWAY NTPC", "railway ntpc": "RAILWAY NTPC"
}

PLAN_TIER_KEYWORDS = {
    "bronze": "BRONZE", "silver": "SILVER", "gold": "GOLD",
    "diamond": "DIAMOND", "learnwithhim": "LEARNWITHHIM", "platinum": "PLATINUM",
    "ruby": "RUBY", "mega": "MEGA", "free demo": "FREE_DEMO", "demo": "FREE_DEMO"
}

INDIAN_FESTIVALS_CALENDAR = [
    {"name": "Maha Shivratri", "date": "2026-02-15", "suggested_sale": "Shivratri Mega Preparation Discount (25% OFF)"},
    {"name": "Holi", "date": "2026-03-04", "suggested_sale": "Rangon Ka Tyohar - Holi Revision Sale (30% OFF)"},
    {"name": "Eid-ul-Fitr", "date": "2026-03-20", "suggested_sale": "Eid Special VIP Access Sale (25% OFF)"},
    {"name": "Ram Navami", "date": "2026-03-27", "suggested_sale": "Ram Navami Success Pass Sale (20% OFF)"},
    {"name": "Dr. B.R. Ambedkar Jayanti", "date": "2026-04-14", "suggested_sale": "Ambedkar Jayanti Education Boost (20% OFF)"},
    {"name": "Independence Day", "date": "2026-08-15", "suggested_sale": "Azadi Ka Amrit Mahotsav Flash Sale (40% OFF)"},
    {"name": "Raksha Bandhan", "date": "2026-08-28", "suggested_sale": "Raksha Bandhan Study Gift Offer (20% OFF)"},
    {"name": "Janmashtami", "date": "2026-09-04", "suggested_sale": "Janmashtami Special Sprint Sale (25% OFF)"},
    {"name": "Gandhi Jayanti", "date": "2026-10-02", "suggested_sale": "Gandhi Jayanti Prep Pass (20% OFF)"},
    {"name": "Dussehra (Vijayadashami)", "date": "2026-10-20", "suggested_sale": "Dussehra Victory Mock Pack Sale (30% OFF)"},
    {"name": "Diwali (Deepavali)", "date": "2026-11-08", "suggested_sale": "Grand Diwali Shubh Labh Mega Sale (35% OFF)"},
    {"name": "Guru Nanak Jayanti", "date": "2026-11-24", "suggested_sale": "Gurpurab Blessing Flash Pass (20% OFF)"},
    {"name": "Christmas & New Year", "date": "2026-12-25", "suggested_sale": "Year-End Mega Discount Sprint (35% OFF)"}
]


def clean_text(text) -> str:
    """Escapes Markdown formatting characters to prevent Telegram formatting errors."""
    if text is None:
        return "N/A"
    s = str(text)
    for c in ["*", "_", "`", "[", "]"]:
        s = s.replace(c, " ")
    return " ".join(s.split()).strip()


def parse_date_safely(date_str: str) -> datetime:
    """Extracts date objects from timestamps across database formats."""
    if not date_str:
        return None
    clean_d = str(date_str).replace(" IST", "").strip()
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d %b %Y, %I:%M %p",
        "%d-%m-%Y",
        "%d/%m/%Y"
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(clean_d, fmt)
            return IST.localize(dt) if dt.tzinfo is None else dt
        except Exception:
            continue
    return None


def execute_llm_nl2sql_fallback(query_text: str) -> str:
    """Calls Grok or OpenAI API with read-only SELECT validation."""
    api_key = os.getenv("GROK_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    api_url = "https://api.x.ai/v1/chat/completions" if os.getenv("GROK_API_KEY") else "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    schema_info = """
    PostgreSQL Database Schema (READ-ONLY SELECT STATEMENTS ONLY):
    - users (user_id BIGINT, student_id TEXT, full_name TEXT, username TEXT, phone_number TEXT, target_exam TEXT, dob TEXT, age INT, gender TEXT, country TEXT, state TEXT, pin TEXT, security_question TEXT, security_answer TEXT, referred_by BIGINT, referral_count INT, bonus_quota INT, paid_question_balance INT, vip_pass_expiry TEXT, demo_used INT, last_profile_edit TEXT, last_active TEXT, last_activity_epoch BIGINT, is_banned INT, is_verified INT, payment_id TEXT, payment_timestamp TEXT, created_at TEXT)
    - payment_transactions (id SERIAL, user_id BIGINT, payment_id TEXT, plan_key TEXT, plan_name TEXT, amount_paid NUMERIC, daily_quota INT, validity_days INT, created_at TEXT, expiry_at TEXT)
    - quiz_attempts (id SERIAL, user_id BIGINT, quiz_id TEXT, questions_attempted INT, total_questions INT, correct_answers INT, wrong_answers INT, skipped_count INT, score REAL, time_taken INT, attempt_timestamp TEXT, attempt_date TEXT, details_json TEXT)
    - student_feedback (id SERIAL, user_id BIGINT, full_name TEXT, feedback_text TEXT, submitted_at TEXT)
    - user_activity_time (id SERIAL, user_id BIGINT, date_str TEXT, seconds_spent INT)
    - flash_sales (id SERIAL, sale_name TEXT, discount_percent NUMERIC, valid_from TIMESTAMP, valid_until TIMESTAMP, is_active BOOLEAN, created_at TIMESTAMP)
    - student_queries (id SERIAL, user_id BIGINT, student_name TEXT, query_text TEXT, photo_file_id TEXT, admin_reply TEXT, status TEXT, created_at TEXT, replied_at TEXT)
    """

    prompt = f"Convert this query into a single valid PostgreSQL SELECT statement. Output ONLY the raw SQL query without explanation or markdown backticks.\nQuery: {query_text}"

    payload = {
        "model": "grok-beta" if os.getenv("GROK_API_KEY") else "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": f"You are a strict read-only PostgreSQL expert.\n{schema_info}"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.0
    }

    try:
        req = urllib.request.Request(api_url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=6) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            sql_query = res_data["choices"][0]["message"]["content"].strip()
            sql_query = sql_query.replace("```sql", "").replace("```", "").strip()
            
            forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "GRANT", "REVOKE"]
            if sql_query.upper().startswith("SELECT") and not any(w in sql_query.upper() for w in forbidden):
                return sql_query
    except Exception as err:
        logger.error(f"[LLM NL2SQL ERROR] {err}")
    return None


def parse_and_execute_admin_query(query_text: str, context_correction: str = None) -> dict:
    """
    OMNISCIENT MASTER ADMIN INTELLIGENCE ENGINE:
    Parses intent across all database tables (users, payments, quizzes, telemetry, feedback).
    """
    q_lower = query_text.lower().strip()
    if context_correction:
        q_lower += f" {context_correction.lower().strip()}"

    now_ist = get_ist_now()
    today_date_str = get_ist_date_str()
    today_start = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_date_str = (now_ist - timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday_start = today_start - timedelta(days=1)

    try:
        # =========================================================================
        # 1. FESTIVAL & SALES PROMOTION STRATEGY FORECASTER
        # =========================================================================
        if any(k in q_lower for k in ["festival", "festivals", "sale offer", "sale timing", "when sale", "right time for sale", "calendar", "promo offer"]):
            conn = get_db()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT * FROM flash_sales ORDER BY id DESC LIMIT 5")
            past_sales = cursor.fetchall()
            cursor.close()
            release_db(conn)

            upcoming_festivals = []
            for fest in INDIAN_FESTIVALS_CALENDAR:
                fest_dt = datetime.strptime(fest["date"], "%Y-%m-%d")
                fest_dt = IST.localize(fest_dt)
                if fest_dt >= now_ist:
                    days_left = (fest_dt.date() - now_ist.date()).days
                    upcoming_festivals.append({
                        "name": fest["name"],
                        "date": fest["date"],
                        "days_left": days_left,
                        "suggested_sale": fest["suggested_sale"]
                    })

            title = "Omniscient Sales & Festival Strategy Intelligence"
            columns = ["S.No.", "Upcoming Festival", "Date", "Countdown", "Recommended Strategy"]
            pdf_rows = []
            tg_lines = [
                "🔥 **OMNISCIENT INTEL: SALES & FESTIVAL FORECASTER**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "📅 **RECENT BOT PROMOTIONS:**\n"
            ]

            if past_sales:
                for ps in past_sales:
                    st_icon = "🟢 LIVE" if ps.get("is_active") else "🔴 EXPIRED"
                    pct = int(float(ps.get("discount_percent", 0)))
                    tg_lines.append(f"• **{clean_text(ps['sale_name'])}** ({pct}% OFF) — `{st_icon}` | Valid: `{str(ps.get('valid_until', 'N/A'))[:16]}`")
            else:
                tg_lines.append("• *No past promotions recorded.*")

            tg_lines.append("\n🎉 **UPCOMING FESTIVAL LAUNCH WINDOWS (2026–2027):**\n")
            for idx, uf in enumerate(upcoming_festivals[:8], start=1):
                tg_lines.append(f"**{idx}. {uf['name']}** (`{uf['date']}` — In `{uf['days_left']} Days`)\n   👉 *Strategy:* `{uf['suggested_sale']}`\n")
                pdf_rows.append([str(idx), str(uf['name']), str(uf['date']), f"{uf['days_left']} Days Left", str(uf['suggested_sale'])])

            tg_lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📥 *Download complete festival promotion calendar as PDF below:*")

            return {
                "title": title,
                "total_records": len(upcoming_festivals),
                "summary_markdown": "\n".join(tg_lines),
                "columns": columns,
                "rows": pdf_rows,
                "kpis": {"Logged Promotions": str(len(past_sales)), "Upcoming Opportunities": f"{len(upcoming_festivals)} Events"}
            }

        # =========================================================================
        # 2. FINANCIAL REVENUE, TRANSACTIONS & PAYMENT COLLECTIONS
        # =========================================================================
        if any(k in q_lower for k in ["revenue", "earning", "income", "collection", "money earned", "sales stats", "transactions", "payment history", "txns"]):
            conn = get_db()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            is_today = "today" in q_lower
            is_yesterday = "yesterday" in q_lower
            is_week = "week" in q_lower
            is_month = "month" in q_lower

            cursor.execute("""
                SELECT pt.payment_id, pt.plan_name, pt.amount_paid, pt.daily_quota, pt.validity_days, pt.created_at, pt.expiry_at,
                       u.user_id, u.student_id, u.full_name, u.phone_number, u.target_exam, u.state
                FROM payment_transactions pt
                LEFT JOIN users u ON pt.user_id = u.user_id
                WHERE pt.plan_key != 'FREE_DEMO' AND pt.amount_paid > 0
                ORDER BY pt.id DESC
            """)
            all_txns = cursor.fetchall()
            cursor.close()
            release_db(conn)

            filtered_txns = []
            gross_rev = 0.0
            timeframe_label = "All-Time"

            for t in all_txns:
                amt = float(t.get("amount_paid", 0) or 0)
                c_str = t.get("created_at", "")
                t_dt = parse_date_safely(c_str)

                if is_today:
                    timeframe_label = f"Today ({today_date_str})"
                    if (today_date_str in c_str) or (t_dt and t_dt >= today_start):
                        filtered_txns.append(t)
                        gross_rev += amt
                elif is_yesterday:
                    timeframe_label = f"Yesterday ({yesterday_date_str})"
                    if (yesterday_date_str in c_str) or (t_dt and yesterday_start <= t_dt < today_start):
                        filtered_txns.append(t)
                        gross_rev += amt
                elif is_week:
                    timeframe_label = "This Week"
                    week_start = today_start - timedelta(days=today_start.weekday())
                    if t_dt and t_dt >= week_start:
                        filtered_txns.append(t)
                        gross_rev += amt
                elif is_month:
                    timeframe_label = "This Month"
                    month_start = today_start.replace(day=1)
                    if t_dt and t_dt >= month_start:
                        filtered_txns.append(t)
                        gross_rev += amt
                else:
                    filtered_txns.append(t)
                    gross_rev += amt

            title = f"Financial Revenue & Transactions Ledger ({timeframe_label})"
            columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Phone", "Target Exam", "Plan Name", "Amount (INR)", "Daily Quota", "Txn ID", "Paid Date"]
            pdf_rows = []
            tg_lines = [
                f"💰 **OMNISCIENT INTEL: FINANCIAL REVENUE & TRANSACTIONS**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📅 **Scope:** `{timeframe_label}`\n"
                f"💵 **Total Revenue:** `₹{gross_rev} INR`\n"
                f"🧾 **Total Verified Orders:** `{len(filtered_txns)}`\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            ]

            for idx, t in enumerate(filtered_txns, start=1):
                uid = t.get("user_id", "N/A")
                sid = clean_text(t.get("student_id") or f"USER_{uid}")
                name = clean_text(t.get("full_name") or "Unknown")
                phone = clean_text(t.get("phone_number") or "N/A")
                plan = clean_text(t.get("plan_name") or "VIP Pack")
                amt = t.get("amount_paid", 0)
                pid = clean_text(t.get("payment_id") or "N/A")
                pdate = clean_text(t.get("created_at") or "N/A")

                if idx <= 20:
                    tg_lines.append(
                        f"**{idx}. {name}** (`{sid}` | ID: `{uid}`)\n"
                        f"   💰 Plan: `{plan}` (₹{amt}) | 📱 Phone: `{phone}`\n"
                        f"   🧾 Txn ID: `{pid}` | 📅 Date: `{pdate}`\n"
                    )
                pdf_rows.append([str(idx), str(uid), str(sid), name, str(phone), clean_text(t.get("target_exam")), plan, f"Rs. {amt}", str(t.get("daily_quota", 20)), str(pid), str(pdate)])

            if len(filtered_txns) > 20:
                tg_lines.append(f"*(+ {len(filtered_txns) - 20} more transactions in attached PDF report)*")

            if not filtered_txns:
                tg_lines.append(f"ℹ️ *No payment transactions recorded for {timeframe_label}.*")

            tg_lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📥 *Download full revenue report as PDF below:*")

            return {
                "title": title,
                "total_records": len(filtered_txns),
                "summary_markdown": "\n".join(tg_lines),
                "columns": columns,
                "rows": pdf_rows,
                "kpis": {"Timeframe": timeframe_label, "Gross Revenue": f"₹{gross_rev} INR", "Total Orders": str(len(filtered_txns))}
            }

        # =========================================================================
        # 3. DATE-SPECIFIC REGISTRATIONS (TODAY, YESTERDAY, THIS WEEK, THIS MONTH)
        # =========================================================================
        is_registration_query = any(k in q_lower for k in ["register", "registered", "joined", "new user", "new student", "signup", "onboarded", "users list", "students list", "user list"])
        is_today = "today" in q_lower
        is_yesterday = "yesterday" in q_lower
        is_this_week = "this week" in q_lower or "past week" in q_lower
        is_this_month = "this month" in q_lower

        if is_registration_query and (is_today or is_yesterday or is_this_week or is_this_month):
            conn = get_db()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT * FROM users ORDER BY user_id DESC")
            all_users = cursor.fetchall()
            cursor.close()
            release_db(conn)

            matched_date_users = []
            if is_today:
                date_label = f"Today ({today_date_str})"
                for u in all_users:
                    c_str = str(u.get("created_at", ""))
                    u_dt = parse_date_safely(c_str)
                    if (today_date_str in c_str) or (u_dt and u_dt >= today_start):
                        matched_date_users.append(u)
            elif is_yesterday:
                date_label = f"Yesterday ({yesterday_date_str})"
                for u in all_users:
                    c_str = str(u.get("created_at", ""))
                    u_dt = parse_date_safely(c_str)
                    if (yesterday_date_str in c_str) or (u_dt and yesterday_start <= u_dt < today_start):
                        matched_date_users.append(u)
            elif is_this_week:
                date_label = "This Week"
                week_start = today_start - timedelta(days=today_start.weekday())
                for u in all_users:
                    u_dt = parse_date_safely(u.get("created_at", ""))
                    if u_dt and u_dt >= week_start:
                        matched_date_users.append(u)
            elif is_this_month:
                date_label = "This Month"
                month_start = today_start.replace(day=1)
                for u in all_users:
                    u_dt = parse_date_safely(u.get("created_at", ""))
                    if u_dt and u_dt >= month_start:
                        matched_date_users.append(u)

            title = f"Student Registrations Report ({date_label})"
            columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Phone", "Target Exam", "State", "PIN", "Registered At"]
            pdf_rows = []
            tg_lines = [
                f"👥 **OMNISCIENT INTEL: STUDENT REGISTRATIONS ({date_label.upper()})**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 **Total New Students Registered:** `{len(matched_date_users)}`\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            ]

            for idx, u in enumerate(matched_date_users, start=1):
                uid = u['user_id']
                sid = clean_text(u.get('student_id') or f"USER_{uid}")
                name = clean_text(u.get('full_name') or "Unknown")
                phone = clean_text(u.get('phone_number') or "N/A")
                exam = clean_text(u.get('target_exam') or "N/A")
                state = clean_text(u.get('state') or "N/A")
                pin = clean_text(u.get('pin') or "N/A")
                ctime = clean_text(u.get('created_at') or "N/A")

                if idx <= 20:
                    tg_lines.append(
                        f"**{idx}. {name}** (`{sid}` | ID: `{uid}`)\n"
                        f"   📱 Phone: `{phone}` | 🎯 Exam: `{exam}` | 📍 State: `{state}`\n"
                        f"   🔑 PIN: `{pin}` | ⏰ Joined: `{ctime}`\n"
                    )
                pdf_rows.append([str(idx), str(uid), str(sid), name, str(phone), str(exam), str(state), str(pin), str(ctime)])

            if len(matched_date_users) > 20:
                tg_lines.append(f"*(+ {len(matched_date_users) - 20} more students in attached PDF report)*")

            if not matched_date_users:
                tg_lines.append(f"ℹ️ *Zero student registrations recorded for {date_label}.*")

            tg_lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📥 *Download complete registration report as PDF below:*")

            return {
                "title": title,
                "total_records": len(matched_date_users),
                "summary_markdown": "\n".join(tg_lines),
                "columns": columns,
                "rows": pdf_rows,
                "kpis": {"Timeframe": date_label, "New Registrations": f"{len(matched_date_users)} Students"}
            }

        # =========================================================================
        # 4. TOTAL PAID VIP USERS & SUBSCRIBERS
        # =========================================================================
        if ("paid" in q_lower or "subscriber" in q_lower or "bought" in q_lower or "vip" in q_lower) and any(k in q_lower for k in ["total", "all", "list", "users", "students", "show", "give", "tell"]):
            conn = get_db()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT DISTINCT ON (u.user_id)
                       u.user_id, u.student_id, u.full_name, u.phone_number, u.target_exam, u.state,
                       u.paid_question_balance, u.vip_pass_expiry, u.pin, u.payment_id,
                       pt.plan_name, pt.amount_paid, pt.created_at as purchase_date
                FROM users u
                INNER JOIN payment_transactions pt ON u.user_id = pt.user_id
                WHERE pt.plan_key != 'FREE_DEMO' AND pt.amount_paid > 0
                ORDER BY u.user_id, pt.id DESC
            """)
            paid_students = cursor.fetchall()

            cursor.execute("SELECT SUM(amount_paid) as total_rev FROM payment_transactions WHERE plan_key != 'FREE_DEMO' AND amount_paid > 0")
            rev_data = cursor.fetchone()
            cursor.close()
            release_db(conn)

            total_rev = float(rev_data['total_rev'] or 0)
            title = f"Total Paid VIP Subscribers Directory ({len(paid_students)} Students)"
            columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Phone", "Target Exam", "Active Plan", "Amount (INR)", "Daily Quota", "Pass Expiry", "Txn ID"]
            pdf_rows = []
            tg_lines = [
                "💳 **OMNISCIENT INTEL: TOTAL PAID VIP SUBSCRIBERS**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"👑 **Total Verified Paid Students:** `{len(paid_students)}`\n"
                f"💰 **Total Gross Revenue Collected:** `₹{total_rev} INR`\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            ]

            for idx, s in enumerate(paid_students, start=1):
                uid = s['user_id']
                sid = clean_text(s.get('student_id') or f"USER_{uid}")
                name = clean_text(s.get('full_name') or "Student")
                phone = clean_text(s.get('phone_number') or "N/A")
                plan = clean_text(s.get('plan_name') or "VIP Plan")
                amt = s.get('amount_paid', 0)
                quota = s.get('paid_question_balance', 20)
                exp = clean_text(s.get('vip_pass_expiry') or "Active")
                pid = clean_text(s.get('payment_id') or "N/A")
                exam = clean_text(s.get('target_exam') or "N/A")

                if idx <= 20:
                    tg_lines.append(
                        f"**{idx}. {name}** (`{sid}` | ID: `{uid}`)\n"
                        f"   💳 Plan: `{plan}` (₹{amt}) | ⚡ Quota: `{quota} Qs/D`\n"
                        f"   📱 Phone: `{phone}` | ⏳ Expiry: `{exp}`\n"
                        f"   🧾 Txn ID: `{pid}`\n"
                    )
                pdf_rows.append([str(idx), str(uid), str(sid), name, str(phone), str(exam), str(plan), f"Rs. {amt}", f"{quota} Qs/D", str(exp), str(pid)])

            if len(paid_students) > 20:
                tg_lines.append(f"*(+ {len(paid_students) - 20} more paid students in attached PDF report)*")

            if not paid_students:
                tg_lines.append("ℹ️ *No paid VIP subscribers found in the database.*")

            tg_lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📥 *Download complete paid subscriber ledger as PDF below:*")

            return {
                "title": title,
                "total_records": len(paid_students),
                "summary_markdown": "\n".join(tg_lines),
                "columns": columns,
                "rows": pdf_rows,
                "kpis": {"Total Paid Scholars": str(len(paid_students)), "Gross Revenue": f"₹{total_rev} INR"}
            }

        # =========================================================================
        # 5. PASS EXPIRATIONS, VALIDITY & DEMO ENDINGS
        # =========================================================================
        if any(k in q_lower for k in ["expire", "expiring", "expiration", "validity", "demo ending", "plan expired"]):
            days_match = re.search(r"(\d+)\s*day", q_lower)
            days_window = int(days_match.group(1)) if days_match else 3

            conn = get_db()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT user_id, student_id, full_name, phone_number, target_exam, paid_question_balance, vip_pass_expiry, payment_id
                FROM users
                WHERE vip_pass_expiry IS NOT NULL AND is_banned = 0
                ORDER BY vip_pass_expiry ASC
            """)
            raw_users = cursor.fetchall()
            cursor.close()
            release_db(conn)

            target_cutoff = now_ist + timedelta(days=days_window)
            expiring_list = []
            already_expired = []

            for u in raw_users:
                exp_str = u.get("vip_pass_expiry", "")
                exp_dt = parse_date_safely(exp_str)
                if exp_dt:
                    if exp_dt < now_ist:
                        already_expired.append(u)
                    elif now_ist <= exp_dt <= target_cutoff:
                        hours_left = max(0.0, round((exp_dt - now_ist).total_seconds() / 3600.0, 1))
                        u["hours_left"] = hours_left
                        expiring_list.append(u)

            if "already" in q_lower or "total plan expired" in q_lower or "expired users" in q_lower:
                selected_records = already_expired
                header_lbl = f"VIP Plans Already Expired ({len(already_expired)} Total)"
            else:
                selected_records = expiring_list
                header_lbl = f"VIP Plans Expiring in Next {days_window} Days ({len(expiring_list)} Total)"

            title = header_lbl
            columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Phone", "Target Exam", "Daily Quota", "Pass Expiry Date", "Txn ID"]
            pdf_rows = []
            tg_lines = [
                f"⏳ **OMNISCIENT INTEL: VIP PASS EXPIRY AUDIT**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ **{header_lbl}**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            ]

            for idx, u in enumerate(selected_records, start=1):
                uid = u.get("user_id", "N/A")
                sid = clean_text(u.get("student_id") or f"USER_{uid}")
                name = clean_text(u.get('full_name') or "Student")
                phone = clean_text(u.get('phone_number') or "N/A")
                exp_date = clean_text(u.get('vip_pass_expiry') or "N/A")
                h_info = f"Left: `{u.get('hours_left')}h` | " if 'hours_left' in u else ""

                if idx <= 20:
                    tg_lines.append(f"**{idx}. {name}** (`{sid}` | ID: `{uid}`)\n   📱 Phone: `{phone}` | {h_info}Expires: `{exp_date}`\n   ⚡ Quota: `{u.get('paid_question_balance', 20)} Qs/Day`\n")
                pdf_rows.append([str(idx), str(uid), str(sid), name, phone, clean_text(u.get('target_exam')), f"{u.get('paid_question_balance', 20)} Qs", exp_date, clean_text(u.get('payment_id', 'N/A'))])

            if not selected_records:
                tg_lines.append("🎉 *Zero subscriptions found matching this criteria.*")

            tg_lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📥 *Download complete expiration audit as PDF below:*")

            return {
                "title": title,
                "total_records": len(selected_records),
                "summary_markdown": "\n".join(tg_lines),
                "columns": columns,
                "rows": pdf_rows,
                "kpis": {"Expiring Records": str(len(selected_records))}
            }

        # =========================================================================
        # 6. ONLINE PATTERNS, HABITS & PRACTICE TIME TELEMETRY
        # =========================================================================
        if any(k in q_lower for k in ["online", "active users", "time spent", "practice time", "when comes", "patterns", "habits", "telemetry"]):
            conn = get_db()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT u.user_id, u.student_id, u.full_name, u.phone_number, u.last_active, u.target_exam, u.state,
                       COALESCE(SUM(uat.seconds_spent), 0) as total_seconds,
                       COUNT(DISTINCT qa.id) as total_attempts
                FROM users u
                LEFT JOIN user_activity_time uat ON u.user_id = uat.user_id
                LEFT JOIN quiz_attempts qa ON u.user_id = qa.user_id
                GROUP BY u.user_id, u.student_id, u.full_name, u.phone_number, u.last_active, u.target_exam, u.state
                ORDER BY total_seconds DESC
                LIMIT 50
            """)
            rows = cursor.fetchall()
            cursor.close()
            release_db(conn)

            title = "Student Online Activity, Duration & Practice Time Patterns"
            columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Phone", "Location", "Last Active (IST)", "Total Practice Time", "Quizzes Solved"]
            pdf_rows = []
            tg_lines = [
                "⏱ **OMNISCIENT INTEL: ONLINE PATTERNS & TIME TELEMETRY**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 **Scholars Analyzed:** `{len(rows)}`\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            ]

            for idx, r in enumerate(rows, start=1):
                uid = r.get("user_id", "N/A")
                sid = clean_text(r.get("student_id") or f"USER_{uid}")
                name = clean_text(r.get('full_name') or "Student")
                hrs = round(r["total_seconds"] / 3600.0, 2)
                mins = round(r["total_seconds"] / 60.0, 1)
                last_act = clean_text(r.get("last_active") or "N/A")
                phone = clean_text(r.get("phone_number") or "N/A")

                if idx <= 20:
                    tg_lines.append(f"**{idx}. {name}** (`{sid}` | ID: `{uid}`)\n   ⏱ Practice Time: `{hrs} Hours` ({mins}m) | Quizzes: `{r['total_attempts']}`\n   🕒 Last Active: `{last_act}` | 📱 Phone: `{phone}`\n")
                pdf_rows.append([str(idx), str(uid), str(sid), name, str(phone), clean_text(r.get('state')), str(last_act), f"{hrs} hrs ({mins}m)", str(r['total_attempts'])])

            tg_lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📥 *Download complete online analytics as PDF below:*")

            return {
                "title": title,
                "total_records": len(rows),
                "summary_markdown": "\n".join(tg_lines),
                "columns": columns,
                "rows": pdf_rows,
                "kpis": {"Scholars Tracked": str(len(rows))}
            }

        # =========================================================================
        # 7. STUDENT FEEDBACK & REVIEWS
        # =========================================================================
        if any(k in q_lower for k in ["feedback", "review", "ratings", "reviews given", "what reviews"]):
            conn = get_db()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT * FROM student_feedback ORDER BY id DESC LIMIT 50")
            feedbacks = cursor.fetchall()
            cursor.close()
            release_db(conn)

            title = "Student Feedback & Reviews Ledger"
            columns = ["S.No.", "Telegram ID", "Student Name", "Feedback Text", "Submitted At"]
            pdf_rows = []
            tg_lines = [
                "💬 **OMNISCIENT INTEL: STUDENT REVIEWS & FEEDBACK**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 **Total Reviews Logged:** `{len(feedbacks)}`\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            ]

            for idx, fb in enumerate(feedbacks, start=1):
                uid = fb.get("user_id", "N/A")
                name = clean_text(fb.get("full_name") or f"User {uid}")
                txt = clean_text(fb.get("feedback_text") or "N/A")
                sub_at = clean_text(fb.get("submitted_at") or "N/A")

                if idx <= 15:
                    tg_lines.append(f"**{idx}. {name}** (ID: `{uid}`)\n   📅 Date: `{sub_at}`\n   💬 *\"{txt}\"*\n")
                pdf_rows.append([str(idx), str(uid), name, txt, sub_at])

            if not feedbacks:
                tg_lines.append("ℹ️ *No reviews found in database.*")

            return {
                "title": title,
                "total_records": len(feedbacks),
                "summary_markdown": "\n".join(tg_lines),
                "columns": columns,
                "rows": pdf_rows,
                "kpis": {"Total Feedback": str(len(feedbacks))}
            }

        # =========================================================================
        # 8. UNIVERSAL MULTI-DIMENSIONAL USER DATABASE SEARCH
        # (Matches by State, Exam, Plan Tier, Status, Phone, Name, PIN, Dates)
        # =========================================================================
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # 1. State Filter
        matched_state = None
        for st in INDIAN_STATES:
            if st in q_lower:
                matched_state = st
                break

        # 2. Exam Filter
        matched_exam = None
        for ek, ev in EXAM_KEYWORDS.items():
            if ek in q_lower:
                matched_exam = ev
                break

        # 3. Plan Filter
        matched_plan = None
        for pk, pv in PLAN_TIER_KEYWORDS.items():
            if pk in q_lower:
                matched_plan = pv
                break

        # 4. Status Filter
        filter_banned = None
        if "banned" in q_lower or "blocked" in q_lower:
            filter_banned = 1
        elif "active" in q_lower or "unbanned" in q_lower:
            filter_banned = 0

        # 5. Paid vs Free Filter
        filter_paid_only = None
        if any(k in q_lower for k in ["paid users", "vip users", "subscribers", "paid students", "who bought"]):
            filter_paid_only = True
        elif any(k in q_lower for k in ["free users", "demo users", "unpaid"]):
            filter_paid_only = False

        conditions = ["1=1"]
        params = []

        if matched_state:
            conditions.append("LOWER(u.state) LIKE %s")
            params.append(f"%{matched_state}%")

        if matched_exam:
            conditions.append("LOWER(u.target_exam) LIKE %s")
            params.append(f"%{matched_exam.lower()}%")

        if filter_banned is not None:
            conditions.append("u.is_banned = %s")
            params.append(filter_banned)

        if filter_paid_only is True:
            conditions.append("(u.paid_question_balance > 20 OR u.user_id IN (SELECT user_id FROM payment_transactions WHERE plan_key != 'FREE_DEMO' AND amount_paid > 0))")
        elif filter_paid_only is False:
            conditions.append("(u.paid_question_balance <= 20 AND u.user_id NOT IN (SELECT user_id FROM payment_transactions WHERE plan_key != 'FREE_DEMO' AND amount_paid > 0))")

        if matched_plan:
            conditions.append("u.user_id IN (SELECT user_id FROM payment_transactions WHERE UPPER(plan_key) LIKE %s)")
            params.append(f"%{matched_plan}%")

        # Specific Search Term Extraction (clean stop words)
        search_keywords = q_lower
        for stopw in ["give", "me", "show", "tell", "details", "of", "list", "all", "the", "total", "users", "students", "who", "is", "about", "student", "user", "info", "find", "search", "pin", "password", "security", "registered", "yesterday", "today", "tomorrow"]:
            search_keywords = re.sub(r'\b' + stopw + r'\b', '', search_keywords)
        search_keywords = search_keywords.strip()

        # If a specific person/phone/ID term remains, add to WHERE clause
        if len(search_keywords) >= 2 and not (matched_state or matched_exam or matched_plan or filter_paid_only is not None):
            conditions.append("(LOWER(u.full_name) LIKE %s OR LOWER(u.student_id) LIKE %s OR u.phone_number LIKE %s OR CAST(u.user_id AS TEXT) LIKE %s)")
            p_term = f"%{search_keywords}%"
            params.extend([p_term, p_term, p_term, p_term])

        query_sql = f"""
            SELECT DISTINCT ON (u.user_id)
                   u.user_id, u.student_id, u.full_name, u.phone_number, u.target_exam, u.state,
                   u.paid_question_balance, u.vip_pass_expiry, u.pin, u.security_question, u.security_answer,
                   u.payment_id, u.created_at, u.last_active, u.is_banned
            FROM users u
            WHERE {' AND '.join(conditions)}
            ORDER BY u.user_id DESC
            LIMIT 100
        """

        cursor.execute(query_sql, tuple(params))
        matched_users = cursor.fetchall()

        cursor.execute("SELECT SUM(amount_paid) as total_rev FROM payment_transactions WHERE plan_key != 'FREE_DEMO'")
        rev_data = cursor.fetchone()
        cursor.close()
        release_db(conn)

        total_rev = float(rev_data['total_rev'] or 0)
        title = f"Omniscient Database Search Ledger ({len(matched_users)} Records)"
        columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Phone", "Target Exam", "State", "Daily Quota", "Pass Expiry", "PIN", "Sec Q", "Sec Ans", "Txn ID"]
        pdf_rows = []
        
        tg_lines = [
            "🔍 **OMNISCIENT INTEL: QUERY EXECUTION RESULTS**\n"
            f"*(Matched: {len(matched_users)} Records)*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]

        for idx, u in enumerate(matched_users, start=1):
            uid = u['user_id']
            sid = clean_text(u.get('student_id') or f"USER_{uid}")
            name = clean_text(u.get('full_name') or "Student")
            phone = clean_text(u.get('phone_number') or "N/A")
            exam = clean_text(u.get('target_exam') or "N/A")
            state = clean_text(u.get('state') or "N/A")
            quota = f"{u.get('paid_question_balance', 20)} Qs/D"
            exp = clean_text(u.get('vip_pass_expiry') or "Active")
            pin = clean_text(u.get('pin') or "N/A")
            sec_q = clean_text(u.get('security_question') or "N/A")
            sec_a = clean_text(u.get('security_answer') or "N/A")
            pid = clean_text(u.get('payment_id') or "N/A")
            last_act = clean_text(u.get('last_active') or "N/A")
            st_badge = "🔴 BANNED" if u.get('is_banned') else "🟢 ACTIVE"

            if idx <= 20:
                tg_lines.append(
                    f"**{idx}. {name}** (`{sid}` | ID: `{uid}`)\n"
                    f"   📱 Phone: `{phone}` | 🎯 Exam: `{exam}` | 📍 State: `{state}`\n"
                    f"   ⚡ Quota: `{quota}` | ⏳ Expiry: `{exp}` | 🚦 Status: `{st_badge}`\n"
                    f"   🔑 PIN: `{pin}` | 🕒 Last Active: `{last_act}`\n"
                    f"   ❓ Sec Q: *\"{sec_q}\"* | Ans: `{sec_a}`\n"
                    f"   🧾 Txn ID: `{pid}`\n"
                )
            pdf_rows.append([str(idx), str(uid), str(sid), name, phone, exam, state, quota, exp, pin, sec_q, sec_a, pid])

        if len(matched_users) > 20:
            tg_lines.append(f"*(+ {len(matched_users) - 20} more records in attached PDF report)*")

        if not matched_users:
            tg_lines.append(f"ℹ️ *Zero matching records found in database for query: \"{clean_text(query_text)}\".*")

        tg_lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📥 *Download complete database results as PDF below:*")

        return {
            "title": title,
            "total_records": len(matched_users),
            "summary_markdown": "\n".join(tg_lines),
            "columns": columns,
            "rows": pdf_rows,
            "kpis": {"Matched Records": str(len(matched_users)), "Gross Revenue": f"₹{total_rev} INR"}
        }

    except Exception as general_err:
        logger.error(f"[PARSE ADMIN QUERY EXCEPTION] {general_err}")
        return {
            "title": "Query Error",
            "total_records": 0,
            "summary_markdown": f"⚠️ **Error executing query:** `{clean_text(str(general_err))}`\nPlease try refining your search keywords.",
            "columns": ["Status"],
            "rows": [["Error"]],
            "kpis": {}
        }


def generate_admin_intelligence_pdf(query_result: dict) -> str:
    title = query_result.get("title", "Master Admin Intelligence Report")
    columns = query_result.get("columns", ["S.No.", "Item", "Value"])
    rows = query_result.get("rows", [])
    kpis = query_result.get("kpis", {})
    return generate_admin_query_dataset_pdf(title, columns, rows, kpis)