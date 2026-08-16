import re
import os
import json
import logging
from datetime import datetime, timedelta
import pytz
import urllib.request
from psycopg2.extras import RealDictCursor
from app.database import get_db, release_db, get_ist_now
from app.pdf_generator import generate_admin_query_dataset_pdf

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

# 2026-2027 Indian Festival & Major Exam Calendar Knowledge Base for Sale Planning
INDIAN_FESTIVALS_CALENDAR = [
    {"name": "Maha Shivratri", "date": "2026-02-15", "suggested_sale": "Shivratri Mega Preparation Discount"},
    {"name": "Holi", "date": "2026-03-04", "suggested_sale": "Rangon Ka Tyohar - Holi Revision Sale"},
    {"name": "Eid-ul-Fitr", "date": "2026-03-20", "suggested_sale": "Eid Special VIP Access Sale"},
    {"name": "Ram Navami", "date": "2026-03-27", "suggested_sale": "Ram Navami Success Pass Sale"},
    {"name": "Dr. B.R. Ambedkar Jayanti", "date": "2026-04-14", "suggested_sale": "Ambedkar Jayanti Education Boost"},
    {"name": "Independence Day", "date": "2026-08-15", "suggested_sale": "Azadi Ka Amrit Mahotsav 50% Flash Sale"},
    {"name": "Raksha Bandhan", "date": "2026-08-28", "suggested_sale": "Raksha Bandhan Study Gift Offer"},
    {"name": "Janmashtami", "date": "2026-09-04", "suggested_sale": "Janmashtami Special Sprint Sale"},
    {"name": "Gandhi Jayanti", "date": "2026-10-02", "suggested_sale": "Gandhi Jayanti Prep Pass"},
    {"name": "Dussehra (Vijayadashami)", "date": "2026-10-20", "suggested_sale": "Dussehra Victory Mock Pack Sale"},
    {"name": "Diwali (Deepavali)", "date": "2026-11-08", "suggested_sale": "Grand Diwali Shubh Labh Mega Sale"},
    {"name": "Guru Nanak Jayanti", "date": "2026-11-24", "suggested_sale": "Gurpurab Blessing Flash Pass"},
    {"name": "Christmas & New Year", "date": "2026-12-25", "suggested_sale": "Year-End Mega Discount Sprint"}
]


def execute_llm_nl2sql_fallback(query_text: str) -> str:
    """
    Calls Grok / OpenAI API (if GROK_API_KEY or OPENAI_API_KEY is configured in env)
    to perform deep analytical reasoning and return safe read-only SQL SELECT queries.
    """
    api_key = os.getenv("GROK_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    api_url = "https://api.x.ai/v1/chat/completions" if os.getenv("GROK_API_KEY") else "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    schema_info = """
    PostgreSQL Database Schema (READ-ONLY SELECT QUERIES ONLY):
    - users (user_id BIGINT, student_id TEXT, full_name TEXT, username TEXT, phone_number TEXT, target_exam TEXT, dob TEXT, age INT, gender TEXT, country TEXT, state TEXT, pin TEXT, security_question TEXT, security_answer TEXT, referred_by BIGINT, referral_count INT, bonus_quota INT, paid_question_balance INT, vip_pass_expiry TEXT, demo_used INT, last_profile_edit TEXT, last_active TEXT, last_activity_epoch BIGINT, is_banned INT, is_verified INT, payment_id TEXT, payment_timestamp TEXT, created_at TEXT)
    - payment_transactions (id SERIAL, user_id BIGINT, payment_id TEXT, plan_key TEXT, plan_name TEXT, amount_paid NUMERIC, daily_quota INT, validity_days INT, created_at TEXT, expiry_at TEXT)
    - quiz_attempts (id SERIAL, user_id BIGINT, quiz_id TEXT, questions_attempted INT, total_questions INT, correct_answers INT, wrong_answers INT, skipped_count INT, score REAL, time_taken INT, attempt_timestamp TEXT, attempt_date TEXT, details_json TEXT)
    - student_feedback (id SERIAL, user_id BIGINT, full_name TEXT, feedback_text TEXT, submitted_at TEXT)
    - user_activity_time (id SERIAL, user_id BIGINT, date_str TEXT, seconds_spent INT)
    - flash_sales (id SERIAL, sale_name TEXT, discount_percent NUMERIC, valid_from TIMESTAMP, valid_until TIMESTAMP, is_active BOOLEAN, created_at TIMESTAMP)
    - student_queries (id SERIAL, user_id BIGINT, student_name TEXT, query_text TEXT, photo_file_id TEXT, admin_reply TEXT, status TEXT, created_at TEXT, replied_at TEXT)
    """

    prompt = f"Convert the following admin request into a single, valid PostgreSQL SELECT statement. Output ONLY the raw SQL query without explanation or markdown backticks.\nAdmin Request: {query_text}"

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
        with urllib.request.urlopen(req, timeout=10) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            sql_query = res_data["choices"][0]["message"]["content"].strip()
            sql_query = sql_query.replace("```sql", "").replace("```", "").strip()
            if sql_query.upper().startswith("SELECT"):
                return sql_query
    except Exception as err:
        logger.error(f"[LLM NL2SQL QUERY ERROR] {err}")
    return None


def parse_and_execute_admin_query(query_text: str, context_correction: str = None) -> dict:
    """
    OMNISCIENT MASTER ADMIN INTELLIGENCE ENGINE:
    Crawls, joins, aggregates, and computes across all database tables.
    Returns complete multi-record datasets formatted for Telegram Markdown and PDF compilation.
    """
    q_lower = query_text.lower().strip()
    if context_correction:
        q_lower += f" (correction note: {context_correction.lower().strip()})"

    now_ist = get_ist_now()
    today_date_str = now_ist.strftime("%Y-%m-%d")

    # -------------------------------------------------------------------------
    # 1. SALE CALENDAR & FESTIVAL TIMING INTELLIGENCE
    # -------------------------------------------------------------------------
    if any(k in q_lower for k in ["sale", "offer", "discount", "festival", "calendar", "right time", "when will", "festivals"]):
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

        title = "Omniscient Sales Telemetry & Festival Calendar Intelligence"
        columns = ["S.No.", "Upcoming Festival", "Date", "Countdown", "Recommended Sale Strategy"]
        pdf_rows = []
        tg_lines = [
            "🔥 **OMNISCIENT INTEL: SALE OFFERS & FESTIVAL CALENDAR**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📅 **PAST & RECENT SALE OFFERS IN BOT:**\n"
        ]

        if past_sales:
            for ps in past_sales:
                status_icon = "🟢 LIVE" if ps.get("is_active") else "🔴 EXPIRED"
                pct = int(float(ps.get("discount_percent", 0)))
                tg_lines.append(f"• **{ps['sale_name']}** ({pct}% OFF) — `{status_icon}` | Valid: `{str(ps.get('valid_until', 'N/A'))[:16]}`")
        else:
            tg_lines.append("• *No past sales recorded in system.*")

        tg_lines.append("\n🎉 **UPCOMING FESTIVALS & BEST SALE LAUNCH WINDOWS:**\n")

        for idx, uf in enumerate(upcoming_festivals[:8], start=1):
            tg_lines.append(f"**{idx}. {uf['name']}** (`{uf['date']}` — In `{uf['days_left']} Days`)\n   👉 *Strategy:* `{uf['suggested_sale']}` (Recommended 20%-35% OFF)")
            pdf_rows.append([str(idx), str(uf['name']), str(uf['date']), f"{uf['days_left']} Days Left", str(uf['suggested_sale'])])

        tg_lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📥 *Download complete festival sales guide as PDF below:*")

        return {
            "title": title,
            "total_records": len(upcoming_festivals),
            "summary_markdown": "\n".join(tg_lines),
            "columns": columns,
            "rows": pdf_rows,
            "kpis": {"Past Sales Logged": str(len(past_sales)), "Upcoming Festivals": f"{len(upcoming_festivals)} Events"}
        }

    # -------------------------------------------------------------------------
    # 2. UNIVERSAL MULTI-RECORD PHONE & CONTACT SEARCH
    # -------------------------------------------------------------------------
    if any(k in q_lower for k in ["phone", "mobile", "contact", "number"]):
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        try:
            search_term = q_lower
            for w in ["phone", "mobile", "number", "contact", "of", "what", "is", "the", "give", "me", "show", "all", "users", "student", "contacts"]:
                search_term = search_term.replace(w, "")
            search_term = search_term.strip()

            if len(search_term) >= 2:
                cursor.execute("""
                    SELECT user_id, student_id, full_name, phone_number, target_exam, state, created_at 
                    FROM users 
                    WHERE LOWER(full_name) LIKE LOWER(%s) 
                       OR LOWER(student_id) LIKE LOWER(%s) 
                       OR phone_number LIKE %s
                    ORDER BY user_id DESC
                """, (f"%{search_term}%", f"%{search_term}%", f"%{search_term}%"))
            else:
                cursor.execute("SELECT user_id, student_id, full_name, phone_number, target_exam, state, created_at FROM users ORDER BY user_id DESC")
            
            rows = cursor.fetchall()
        except Exception as e:
            logger.error(f"[OMNISCIENT PHONE QUERY ERROR] {e}")
            rows = []
        finally:
            cursor.close()
            release_db(conn)

        title = "Omniscient Phone & Contact Directory Ledger"
        columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Phone Number", "Target Exam", "State", "Registered At"]
        pdf_rows = []
        tg_lines = [
            "📱 **OMNISCIENT INTEL: CONTACT DIRECTORY**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 **Total Matching Records Found:** `{len(rows)} Students`\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]

        for idx, r in enumerate(rows, start=1):
            uid = r.get("user_id", "N/A")
            sid = r.get("student_id") or f"USER_{uid}"
            name = r.get("full_name") or "Unknown"
            phone = r.get("phone_number") or "N/A"
            exam = r.get("target_exam") or "N/A"
            state = r.get("state") or "N/A"
            reg = str(r.get("created_at", "N/A")).split(" ")[0]

            tg_lines.append(f"**{idx}. {name}** (`{sid}` | ID: `{uid}`)\n   📱 Phone: `{phone}` | Exam: `{exam}` | State: `{state}`")
            pdf_rows.append([str(idx), str(uid), str(sid), str(name), str(phone), str(exam), str(state), str(reg)])

        if not rows:
            tg_lines.append("ℹ️ *No matching phone records found in database.*")

        tg_lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📥 *Download complete contact ledger as PDF below:*")

        return {
            "title": title,
            "total_records": len(rows),
            "summary_markdown": "\n".join(tg_lines),
            "columns": columns,
            "rows": pdf_rows,
            "kpis": {"Total Records": str(len(rows)), "Query Scope": "All Matching Users"}
        }

    # -------------------------------------------------------------------------
    # 3. ONLINE TIME PATTERNS & TELEMETRY SEARCH
    # -------------------------------------------------------------------------
    if any(k in q_lower for k in ["online", "active", "time spent", "practice time", "when comes", "patterns"]):
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT u.user_id, u.student_id, u.full_name, u.last_active, u.target_exam,
                   COALESCE(SUM(uat.seconds_spent), 0) as total_seconds,
                   COUNT(DISTINCT qa.id) as total_attempts
            FROM users u
            LEFT JOIN user_activity_time uat ON u.user_id = uat.user_id
            LEFT JOIN quiz_attempts qa ON u.user_id = qa.user_id
            GROUP BY u.user_id, u.student_id, u.full_name, u.last_active, u.target_exam
            ORDER BY total_seconds DESC
            LIMIT 50
        """)
        rows = cursor.fetchall()
        cursor.close()
        release_db(conn)

        title = "Student Online Activity, Duration & Routine Patterns"
        columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Last Active (IST)", "Total Practice Time", "Quizzes Solved"]
        pdf_rows = []
        tg_lines = [
            "⏱ **OMNISCIENT INTEL: ONLINE PATTERNS & PRACTICE TIME**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 **Top Active Scholars Logged:** `{len(rows)}`\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]

        for idx, r in enumerate(rows, start=1):
            uid = r.get("user_id", "N/A")
            sid = r.get("student_id") or f"USER_{uid}"
            hrs = round(r["total_seconds"] / 3600.0, 2)
            mins = round(r["total_seconds"] / 60.0, 1)
            last_act = r.get("last_active") or "N/A"

            tg_lines.append(f"**{idx}. {r['full_name']}** (`{sid}` | ID: `{uid}`)\n   ⏱ Time: `{hrs} Hours` ({mins}m) | Quizzes: `{r['total_attempts']}`\n   🕒 Last Active: `{last_act}`")
            pdf_rows.append([str(idx), str(uid), str(sid), str(r['full_name']), str(last_act), f"{hrs} hrs ({mins}m)", str(r['total_attempts'])])

        tg_lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📥 *Download complete online analytics as PDF below:*")

        return {
            "title": title,
            "total_records": len(rows),
            "summary_markdown": "\n".join(tg_lines),
            "columns": columns,
            "rows": pdf_rows,
            "kpis": {"Active Records": str(len(rows)), "Analytics Scope": "Activity & Engagement"}
        }

    # -------------------------------------------------------------------------
    # 4. NEW REGISTRATIONS + PAID PURCHASES (e.g. 3 Days Data)
    # -------------------------------------------------------------------------
    days_match = re.search(r"(\d+)\s*day", q_lower)
    days_val = int(days_match.group(1)) if days_match else (3 if "3 day" in q_lower else 7)

    if ("register" in q_lower or "joined" in q_lower or "new" in q_lower) and ("paid" in q_lower or "plan" in q_lower or "bought" in q_lower):
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cutoff_dt = now_ist - timedelta(days=days_val)
        
        cursor.execute("""
            SELECT u.user_id, u.student_id, u.full_name, u.phone_number, u.target_exam, 
                   u.created_at, u.paid_question_balance, u.vip_pass_expiry,
                   pt.plan_name, pt.amount_paid, pt.payment_id, pt.created_at as paid_at
            FROM users u
            INNER JOIN payment_transactions pt ON u.user_id = pt.user_id
            WHERE pt.plan_key != 'FREE_DEMO' AND pt.amount_paid > 0
            ORDER BY pt.id DESC
        """)
        raw_rows = cursor.fetchall()
        cursor.close()
        release_db(conn)

        filtered = []
        for r in raw_rows:
            dt_str = r.get("created_at") or ""
            try:
                dt = datetime.strptime(dt_str.replace(" IST", ""), "%Y-%m-%d %H:%M:%S")
                dt = IST.localize(dt)
                if dt >= cutoff_dt:
                    filtered.append(r)
            except Exception:
                filtered.append(r)

        total_rev = sum([float(r.get("amount_paid", 0) or 0) for r in filtered])
        title = f"Newly Registered Students with Paid Plans (Last {days_val} Days)"
        columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Phone", "Target Exam", "Pack Name", "Amount", "Registered At"]
        pdf_rows = []
        tg_lines = [
            f"🧠 **OMNISCIENT INTEL: NEW PAID STUDENTS ({days_val} DAYS)**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 **Total New Paid Users Found:** `{len(filtered)}`\n"
            f"💰 **Total Revenue Generated:** `₹{total_rev} INR`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]

        for idx, r in enumerate(filtered, start=1):
            uid = r.get("user_id", "N/A")
            sid = r.get("student_id") or f"USER_{uid}"
            p_name = r.get("plan_name") or "VIP Pack"
            amt = r.get("amount_paid", 0)
            reg_date = str(r.get("created_at", "N/A")).split(" ")[0]
            phone = str(r.get("phone_number", "N/A"))

            tg_lines.append(f"**{idx}. {r['full_name']}** (`{sid}` | ID: `{uid}`)\n   📱 Phone: `{phone}` | Plan: `{p_name}` (₹{amt})")
            pdf_rows.append([str(idx), str(uid), str(sid), str(r['full_name']), str(phone), str(r.get('target_exam', 'N/A')), str(p_name), f"Rs. {amt}", str(reg_date)])

        if not filtered:
            tg_lines.append("ℹ️ *No new paid student registrations found in this timeframe.*")

        tg_lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📥 *Download complete dataset as PDF below:*")

        return {
            "title": title,
            "total_records": len(filtered),
            "summary_markdown": "\n".join(tg_lines),
            "columns": columns,
            "rows": pdf_rows,
            "kpis": {"Timeframe": f"Last {days_val} Days", "New Paid Users": str(len(filtered)), "Revenue": f"₹{total_rev} INR"}
        }

    # -------------------------------------------------------------------------
    # 5. UPCOMING PASS EXPIRATIONS
    # -------------------------------------------------------------------------
    if any(k in q_lower for k in ["expire", "expiring", "expiration", "validity"]):
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT user_id, student_id, full_name, phone_number, target_exam, paid_question_balance, vip_pass_expiry
            FROM users
            WHERE vip_pass_expiry IS NOT NULL AND is_banned = 0
            ORDER BY vip_pass_expiry ASC
        """)
        raw_users = cursor.fetchall()
        cursor.close()
        release_db(conn)

        target_cutoff = now_ist + timedelta(days=days_val)
        expiring_list = []

        for u in raw_users:
            exp_str = u.get("vip_pass_expiry", "")
            try:
                exp_dt = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S IST")
                exp_dt = IST.localize(exp_dt) if exp_dt.tzinfo is None else exp_dt
                if now_ist <= exp_dt <= target_cutoff:
                    hours_left = max(0.0, round((exp_dt - now_ist).total_seconds() / 3600.0, 1))
                    u["hours_left"] = hours_left
                    expiring_list.append(u)
            except Exception:
                pass

        title = f"VIP Subscriptions Expiring within Next {days_val} Days"
        columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Phone", "Daily Quota", "Hours Left", "Exact Expiry"]
        pdf_rows = []
        tg_lines = [
            f"⏳ **OMNISCIENT INTEL: EXPIRING PASSES ({days_val} DAYS)**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ **Expiring Soon:** `{len(expiring_list)} Aspirants`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]

        for idx, u in enumerate(expiring_list, start=1):
            uid = u.get("user_id", "N/A")
            sid = u.get("student_id") or f"USER_{uid}"
            tg_lines.append(f"**{idx}. {u['full_name']}** (`{sid}` | ID: `{uid}`)\n   📱 Phone: `{u.get('phone_number')}` | Left: `{u['hours_left']}h` (Expires: `{u['vip_pass_expiry']}`)")
            pdf_rows.append([str(idx), str(uid), str(sid), str(u['full_name']), str(u.get('phone_number', 'N/A')), f"{u['paid_question_balance']} Qs", f"{u['hours_left']}h", str(u['vip_pass_expiry'])])

        if not expiring_list:
            tg_lines.append("🎉 *Zero subscriptions expiring in this window.*")

        tg_lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📥 *Download complete list as PDF below:*")

        return {
            "title": title,
            "total_records": len(expiring_list),
            "summary_markdown": "\n".join(tg_lines),
            "columns": columns,
            "rows": pdf_rows,
            "kpis": {"Expiring Soon": str(len(expiring_list))}
        }

    # -------------------------------------------------------------------------
    # 6. REVENUE & FINANCIAL BREAKDOWN
    # -------------------------------------------------------------------------
    if any(k in q_lower for k in ["revenue", "earning", "collection", "income", "sales stats"]):
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT plan_name, plan_key, COUNT(*) as txn_count, SUM(amount_paid) as total_amount, AVG(amount_paid) as avg_price
            FROM payment_transactions
            WHERE plan_key != 'FREE_DEMO'
            GROUP BY plan_name, plan_key
            ORDER BY total_amount DESC
        """)
        rows = cursor.fetchall()
        cursor.close()
        release_db(conn)

        grand_total = sum([float(r.get("total_amount", 0) or 0) for r in rows])
        title = "Financial Revenue & Plan-wise Sales Performance"
        columns = ["S.No.", "Plan Name", "Plan Code", "Purchases", "Total Revenue (INR)"]
        pdf_rows = []
        tg_lines = [
            "💰 **OMNISCIENT INTEL: FINANCIAL REVENUE LEDGER**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 **Gross Revenue:** `₹{grand_total} INR`\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]

        for idx, r in enumerate(rows, start=1):
            tg_lines.append(f"  {idx}. **{r.get('plan_name')}**: `₹{r.get('total_amount')} INR` ({r.get('txn_count')} orders)")
            pdf_rows.append([str(idx), str(r.get('plan_name')), str(r.get('plan_key')), str(r.get('txn_count')), f"Rs. {r.get('total_amount')}"])

        tg_lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📥 *Download revenue audit as PDF below:*")

        return {
            "title": title,
            "total_records": len(rows),
            "summary_markdown": "\n".join(tg_lines),
            "columns": columns,
            "rows": pdf_rows,
            "kpis": {"Gross Revenue": f"₹{grand_total} INR"}
        }

    # -------------------------------------------------------------------------
    # 7. MULTI-RECORD STUDENT DOSSIER SEARCH (Returns ALL matches instead of 1)
    # -------------------------------------------------------------------------
    clean_term = q_lower
    for w in ["details", "of", "profile", "student", "user", "info", "for", "search", "tell me", "show", "all", "who", "is"]:
        clean_term = clean_term.replace(w, "")
    clean_term = clean_term.strip()

    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT * FROM users 
            WHERE LOWER(full_name) LIKE LOWER(%s) 
               OR LOWER(student_id) LIKE LOWER(%s) 
               OR phone_number LIKE %s 
               OR CAST(user_id AS TEXT) LIKE %s
            ORDER BY user_id DESC
        """, (f"%{clean_term}%", f"%{clean_term}%", f"%{clean_term}%", f"%{clean_term}%"))
        matched_users = cursor.fetchall()
    except Exception:
        matched_users = []
    finally:
        cursor.close()
        release_db(conn)

    if matched_users:
        title = f"Omniscient Student Search Results ({len(matched_users)} Found)"
        columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Phone", "Target Exam", "Quota", "Status"]
        pdf_rows = []
        tg_lines = [
            f"👤 **OMNISCIENT STUDENT SEARCH RESULTS**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 **Matching Scholars:** `{len(matched_users)} Found`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]

        for idx, u in enumerate(matched_users, start=1):
            uid = u['user_id']
            sid = u.get('student_id', f"USER_{uid}")
            name = u.get('full_name', 'Unknown')
            phone = u.get('phone_number', 'N/A')
            exam = u.get('target_exam', 'N/A')
            quota = f"{u.get('paid_question_balance', 20)} Qs/D"
            status = "BANNED 🛑" if u.get('is_banned') else "ACTIVE 🟢"

            tg_lines.append(
                f"**{idx}. {name}** (`{sid}` | ID: `{uid}`)\n"
                f"   📱 Phone: `{phone}` | Exam: `{exam}` | Quota: `{quota}` | Status: `{status}`\n"
                f"   🔑 PIN: `{u.get('pin','N/A')}` | Expiry: `{u.get('vip_pass_expiry','N/A')}`\n"
                f"   ❓ Sec Q: *\"{u.get('security_question','N/A')}\"* | Ans: `{u.get('security_answer','N/A')}`"
            )
            pdf_rows.append([str(idx), str(uid), str(sid), str(name), str(phone), str(exam), str(quota), status])

        tg_lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📥 *Download complete list as PDF below:*")

        return {
            "title": title,
            "total_records": len(matched_users),
            "summary_markdown": "\n".join(tg_lines),
            "columns": columns,
            "rows": pdf_rows,
            "kpis": {"Matched Students": str(len(matched_users))}
        }

    # -------------------------------------------------------------------------
    # 8. OMNISCIENT PLATFORM CRAWLER (Default Multi-Table Fallback)
    # -------------------------------------------------------------------------
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT user_id, student_id, full_name, phone_number, target_exam, state, paid_question_balance, vip_pass_expiry 
        FROM users 
        ORDER BY user_id DESC 
        LIMIT 50
    """)
    all_matched_users = cursor.fetchall()
    
    cursor.execute("SELECT COUNT(*) as total_users, COUNT(CASE WHEN paid_question_balance > 20 THEN 1 END) as paid_count FROM users")
    u_stats = cursor.fetchone()

    cursor.execute("SELECT SUM(amount_paid) as total_rev FROM payment_transactions WHERE plan_key != 'FREE_DEMO'")
    rev_stats = cursor.fetchone()
    cursor.close()
    release_db(conn)

    total_u = u_stats.get("total_users", 0)
    paid_u = u_stats.get("paid_count", 0)
    tot_rev = float(rev_stats.get("total_rev", 0) or 0)

    title = f"Omniscient Database Crawler Results ({len(all_matched_users)} Records)"
    columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Phone", "Target Exam", "State", "Quota"]
    pdf_rows = []
    tg_lines = [
        f"🧠 **OMNISCIENT ADMIN CRAWLER RESULTS**\n"
        f"*(Query: \"{query_text}\" — Showing {len(all_matched_users)} records)*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 **Total Registered Scholars:** `{total_u}` | 💳 **Paid:** `{paid_u}` | 💰 **Rev:** `₹{tot_rev}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    ]

    for idx, u in enumerate(all_matched_users, start=1):
        uid = u.get("user_id")
        sid = u.get("student_id") or f"USER_{uid}"
        name = u.get("full_name") or "Unknown"
        phone = u.get("phone_number") or "N/A"
        exam = u.get("target_exam") or "N/A"
        state = u.get("state") or "N/A"
        quota = f"{u.get('paid_question_balance', 20)} Qs"

        tg_lines.append(f"**{idx}. {name}** (`{sid}` | ID: `{uid}`)\n   📱 Phone: `{phone}` | Exam: `{exam}` | State: `{state}`")
        pdf_rows.append([str(idx), str(uid), str(sid), str(name), str(phone), str(exam), str(state), quota])

    tg_lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📥 *Download complete database report as PDF:*")

    return {
        "title": title,
        "total_records": len(all_matched_users),
        "summary_markdown": "\n".join(tg_lines),
        "columns": columns,
        "rows": pdf_rows,
        "kpis": {"Total Users": str(total_u), "Matched Records": str(len(all_matched_users)), "Revenue": f"₹{tot_rev} INR"}
    }


def generate_admin_intelligence_pdf(query_result: dict) -> str:
    title = query_result.get("title", "Master Admin Intelligence Report")
    columns = query_result.get("columns", ["S.No.", "Item", "Value"])
    rows = query_result.get("rows", [])
    kpis = query_result.get("kpis", {})
    return generate_admin_query_dataset_pdf(title, columns, rows, kpis)