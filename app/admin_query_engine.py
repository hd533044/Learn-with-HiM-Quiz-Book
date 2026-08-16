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

# 2026-2027 Indian Festival & Major Exam Calendar Knowledge Base for Sale Planning
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


def execute_llm_nl2sql_fallback(query_text: str) -> str:
    """
    Calls Grok API or OpenAI API (if GROK_API_KEY or OPENAI_API_KEY is configured in env)
    to perform deep analytical reasoning and return safe, read-only SQL SELECT queries.
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
    - saved_questions (id SERIAL, user_id BIGINT, question_text TEXT, options_json TEXT, correct_option INT, explanation TEXT, saved_at TEXT)
    """

    prompt = f"Convert the following admin natural language request into a single, valid PostgreSQL SELECT statement. Output ONLY the raw SQL query without any explanation, markdown formatting, or backticks.\nAdmin Request: {query_text}"

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
            
            # Security Sanity Check: ONLY allow SELECT
            forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "GRANT", "REVOKE"]
            if sql_query.upper().startswith("SELECT") and not any(w in sql_query.upper() for w in forbidden):
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
    today_date_str = get_ist_date_str()
    today_start = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)

    # -------------------------------------------------------------------------
    # 1. TODAY'S REVENUE, PAYMENTS & TRANSACTIONS
    # -------------------------------------------------------------------------
    if ("today" in q_lower and any(k in q_lower for k in ["revenue", "payment", "paid", "collection", "bought", "earn", "income", "money", "txn"])):
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT pt.payment_id, pt.plan_name, pt.amount_paid, pt.daily_quota, pt.validity_days, pt.created_at,
                   u.user_id, u.student_id, u.full_name, u.phone_number, u.target_exam
            FROM payment_transactions pt
            LEFT JOIN users u ON pt.user_id = u.user_id
            WHERE pt.plan_key != 'FREE_DEMO' AND pt.amount_paid > 0
            ORDER BY pt.id DESC
        """)
        rows = cursor.fetchall()
        cursor.close()
        release_db(conn)

        today_txns = []
        tot_today_rev = 0.0
        for r in rows:
            dt_str = r.get("created_at", "")
            try:
                dt = datetime.strptime(dt_str, "%d %b %Y, %I:%M %p IST")
                dt = IST.localize(dt) if dt.tzinfo is None else dt
                if dt >= today_start:
                    amt = float(r.get("amount_paid", 0) or 0)
                    tot_today_rev += amt
                    today_txns.append(r)
            except Exception:
                pass

        title = f"Today's Live Revenue & Payment Transactions ({today_date_str})"
        columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Phone", "Plan Name", "Amount (INR)", "Txn ID", "Paid Time"]
        pdf_rows = []
        tg_lines = [
            f"💰 **OMNISCIENT INTEL: TODAY'S REVENUE & PURCHASES**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 **Date:** `{today_date_str}`\n"
            f"💵 **Total Revenue Today:** `₹{tot_today_rev} INR`\n"
            f"🧾 **Total Paid Purchases Today:** `{len(today_txns)} Orders`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]

        for idx, t in enumerate(today_txns, start=1):
            uid = t.get("user_id", "N/A")
            sid = t.get("student_id") or f"USER_{uid}"
            name = t.get("full_name") or "Unknown"
            phone = t.get("phone_number") or "N/A"
            plan = t.get("plan_name") or "VIP Plan"
            amt = t.get("amount_paid", 0)
            pid = t.get("payment_id") or "N/A"
            ctime = t.get("created_at") or "Today"

            tg_lines.append(
                f"**{idx}. {name}** (`{sid}` | ID: `{uid}`)\n"
                f"   💰 Plan: `{plan}` (₹{amt}) | 📱 Phone: `{phone}`\n"
                f"   🧾 Txn ID: `{pid}`\n"
                f"   ⏰ Time: `{ctime}`"
            )
            pdf_rows.append([str(idx), str(uid), str(sid), str(name), str(phone), str(plan), f"Rs. {amt}", str(pid), str(ctime)])

        if not today_txns:
            tg_lines.append("ℹ️ *No paid plan purchases recorded today yet.*")

        tg_lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📥 *Download today's complete revenue ledger as PDF below:*")

        return {
            "title": title,
            "total_records": len(today_txns),
            "summary_markdown": "\n".join(tg_lines),
            "columns": columns,
            "rows": pdf_rows,
            "kpis": {"Date": today_date_str, "Today Revenue": f"₹{tot_today_rev} INR", "Paid Purchases": f"{len(today_txns)} Users"}
        }

    # -------------------------------------------------------------------------
    # 2. TODAY'S REGISTERED USERS LIST
    # -------------------------------------------------------------------------
    if "today" in q_lower and any(k in q_lower for k in ["register", "registered", "joined", "new user", "new student", "signup"]):
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM users ORDER BY user_id DESC")
        all_u = cursor.fetchall()
        cursor.close()
        release_db(conn)

        today_registered = []
        for u in all_u:
            c_str = u.get("created_at", "")
            if today_date_str in c_str:
                today_registered.append(u)

        title = f"Today's Newly Registered Students ({today_date_str})"
        columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Phone", "Target Exam", "State", "PIN", "Registered At"]
        pdf_rows = []
        tg_lines = [
            f"👥 **OMNISCIENT INTEL: TODAY'S NEW REGISTRATIONS**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 **Date:** `{today_date_str}`\n"
            f"📊 **New Students Joined Today:** `{len(today_registered)}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]

        for idx, u in enumerate(today_registered, start=1):
            uid = u['user_id']
            sid = u.get('student_id') or f"USER_{uid}"
            name = u.get('full_name') or "Unknown"
            phone = u.get('phone_number') or "N/A"
            exam = u.get('target_exam') or "N/A"
            state = u.get('state') or "N/A"
            pin = u.get('pin') or "N/A"
            ctime = u.get('created_at') or "Today"

            tg_lines.append(
                f"**{idx}. {name}** (`{sid}` | ID: `{uid}`)\n"
                f"   📱 Phone: `{phone}` | 🎯 Exam: `{exam}` | 📍 State: `{state}`\n"
                f"   🔑 PIN: `{pin}` | ⏰ Joined: `{ctime}`"
            )
            pdf_rows.append([str(idx), str(uid), str(sid), str(name), str(phone), str(exam), str(state), str(pin), str(ctime)])

        if not today_registered:
            tg_lines.append("ℹ️ *No new student registrations recorded today yet.*")

        tg_lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📥 *Download today's registrations report as PDF below:*")

        return {
            "title": title,
            "total_records": len(today_registered),
            "summary_markdown": "\n".join(tg_lines),
            "columns": columns,
            "rows": pdf_rows,
            "kpis": {"Date": today_date_str, "New Registrations": f"{len(today_registered)} Students"}
        }

    # -------------------------------------------------------------------------
    # 3. SALE CALENDAR & FESTIVAL TIMING INTELLIGENCE
    # -------------------------------------------------------------------------
    if any(k in q_lower for k in ["sale", "offer", "discount", "festival", "calendar", "right time", "when will", "festivals", "promo"]):
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
            tg_lines.append(f"**{idx}. {uf['name']}** (`{uf['date']}` — In `{uf['days_left']} Days`)\n   👉 *Strategy:* `{uf['suggested_sale']}`")
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
    # 4. QUIZ ATTEMPTS & PERFORMANCE ON SPECIFIC DATES OR INACTIVE USERS
    # -------------------------------------------------------------------------
    if any(k in q_lower for k in ["quiz attempt", "quizzes attempt", "quiz analysis", "performance", "score", "inactive", "accuracy", "attempts"]):
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        if "inactive" in q_lower or "zero" in q_lower or "0 quiz" in q_lower:
            cursor.execute("""
                SELECT u.user_id, u.student_id, u.full_name, u.phone_number, u.target_exam, u.state, u.created_at, u.last_active
                FROM users u
                WHERE u.user_id NOT IN (SELECT DISTINCT user_id FROM quiz_attempts)
                ORDER BY u.user_id DESC LIMIT 50
            """)
            rows = cursor.fetchall()
            cursor.close()
            release_db(conn)

            title = "Inactive Students Ledger (0 Quizzes Attempted)"
            columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Phone", "Target Exam", "State", "Registered At"]
            pdf_rows = []
            tg_lines = [
                "📉 **OMNISCIENT INTEL: INACTIVE STUDENTS (0 QUIZZES)**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 **Found Inactive Students:** `{len(rows)}`\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            ]
            for idx, r in enumerate(rows, start=1):
                uid = r['user_id']
                sid = r.get('student_id') or f"USER_{uid}"
                name = r.get('full_name') or "Unknown"
                phone = r.get('phone_number') or "N/A"
                exam = r.get('target_exam') or "N/A"
                state = r.get('state') or "N/A"
                reg = str(r.get('created_at', 'N/A')).split(" ")[0]

                tg_lines.append(f"**{idx}. {name}** (`{sid}` | ID: `{uid}`)\n   📱 Phone: `{phone}` | Exam: `{exam}` | State: `{state}`")
                pdf_rows.append([str(idx), str(uid), str(sid), str(name), str(phone), str(exam), str(state), str(reg)])

            return {
                "title": title,
                "total_records": len(rows),
                "summary_markdown": "\n".join(tg_lines),
                "columns": columns,
                "rows": pdf_rows,
                "kpis": {"Inactive Students": str(len(rows))}
            }

        # Date-wise or Top Performers Analysis
        cursor.execute("""
            SELECT u.user_id, u.student_id, u.full_name, u.phone_number, u.target_exam,
                   COUNT(qa.id) as total_quizzes,
                   COALESCE(SUM(qa.questions_attempted), 0) as total_qs,
                   COALESCE(SUM(qa.correct_answers), 0) as total_correct,
                   COALESCE(SUM(qa.wrong_answers), 0) as total_wrong,
                   COALESCE(AVG(qa.score), 0.0) as avg_score
            FROM users u
            INNER JOIN quiz_attempts qa ON u.user_id = qa.user_id
            GROUP BY u.user_id, u.student_id, u.full_name, u.phone_number, u.target_exam
            ORDER BY total_qs DESC LIMIT 50
        """)
        rows = cursor.fetchall()
        cursor.close()
        release_db(conn)

        title = "Student Quiz Performance & Academic Analysis Ledger"
        columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Phone", "Target Exam", "Quizzes", "Total Qs", "Correct", "Wrong", "Avg Score"]
        pdf_rows = []
        tg_lines = [
            "🎯 **OMNISCIENT INTEL: QUIZ ATTEMPTS & PERFORMANCE**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 **Scholars Analyzed:** `{len(rows)}`\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]

        for idx, r in enumerate(rows, start=1):
            uid = r['user_id']
            sid = r.get('student_id') or f"USER_{uid}"
            name = r.get('full_name') or "Unknown"
            phone = r.get('phone_number') or "N/A"
            exam = r.get('target_exam') or "N/A"
            qs = r.get('total_qs', 0)
            corr = r.get('total_correct', 0)
            wrong = r.get('total_wrong', 0)
            score = round(float(r.get('avg_score', 0.0)), 2)

            tg_lines.append(
                f"**{idx}. {name}** (`{sid}` | ID: `{uid}`)\n"
                f"   📚 Quizzes: `{r['total_quizzes']}` | Qs: `{qs}` | ✅ Corr: `{corr}` | ❌ Wrong: `{wrong}` | ⭐ Avg: `{score}`"
            )
            pdf_rows.append([str(idx), str(uid), str(sid), str(name), str(phone), str(exam), str(r['total_quizzes']), str(qs), str(corr), str(wrong), str(score)])

        tg_lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📥 *Download complete quiz analysis as PDF below:*")

        return {
            "title": title,
            "total_records": len(rows),
            "summary_markdown": "\n".join(tg_lines),
            "columns": columns,
            "rows": pdf_rows,
            "kpis": {"Active Scholars": str(len(rows))}
        }

    # -------------------------------------------------------------------------
    # 5. ONLINE TIME PATTERNS, HABITS & ENGAGEMENT TELEMETRY
    # -------------------------------------------------------------------------
    if any(k in q_lower for k in ["online", "active", "time spent", "practice time", "when comes", "patterns", "habits"]):
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT u.user_id, u.student_id, u.full_name, u.phone_number, u.last_active, u.target_exam,
                   COALESCE(SUM(uat.seconds_spent), 0) as total_seconds,
                   COUNT(DISTINCT qa.id) as total_attempts
            FROM users u
            LEFT JOIN user_activity_time uat ON u.user_id = uat.user_id
            LEFT JOIN quiz_attempts qa ON u.user_id = qa.user_id
            GROUP BY u.user_id, u.student_id, u.full_name, u.phone_number, u.last_active, u.target_exam
            ORDER BY total_seconds DESC
            LIMIT 50
        """)
        rows = cursor.fetchall()
        cursor.close()
        release_db(conn)

        title = "Student Online Activity, Duration & Routine Patterns"
        columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Phone", "Last Active (IST)", "Total Practice Time", "Quizzes Solved"]
        pdf_rows = []
        tg_lines = [
            "⏱ **OMNISCIENT INTEL: ONLINE PATTERNS & PRACTICE TIME**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 **Scholars Logged:** `{len(rows)}`\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]

        for idx, r in enumerate(rows, start=1):
            uid = r.get("user_id", "N/A")
            sid = r.get("student_id") or f"USER_{uid}"
            hrs = round(r["total_seconds"] / 3600.0, 2)
            mins = round(r["total_seconds"] / 60.0, 1)
            last_act = r.get("last_active") or "N/A"
            phone = r.get("phone_number") or "N/A"

            tg_lines.append(f"**{idx}. {r['full_name']}** (`{sid}` | ID: `{uid}`)\n   ⏱ Time: `{hrs} Hours` ({mins}m) | Quizzes: `{r['total_attempts']}`\n   🕒 Last Active: `{last_act}` | 📱 Phone: `{phone}`")
            pdf_rows.append([str(idx), str(uid), str(sid), str(r['full_name']), str(phone), str(last_act), f"{hrs} hrs ({mins}m)", str(r['total_attempts'])])

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
    # 6. NEW REGISTRATIONS + PAID PURCHASES (e.g. 3 Days Data, 7 Days Data)
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
        columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Phone", "Target Exam", "Pack Name", "Amount", "Txn ID", "Registered At"]
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
            pid = r.get("payment_id", "N/A")
            reg_date = str(r.get("created_at", "N/A")).split(" ")[0]
            phone = str(r.get("phone_number", "N/A"))

            tg_lines.append(f"**{idx}. {r['full_name']}** (`{sid}` | ID: `{uid}`)\n   📱 Phone: `{phone}` | Plan: `{p_name}` (₹{amt})\n   🧾 Txn ID: `{pid}`")
            pdf_rows.append([str(idx), str(uid), str(sid), str(r['full_name']), str(phone), str(r.get('target_exam', 'N/A')), str(p_name), f"Rs. {amt}", str(pid), str(reg_date)])

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
    # 7. UPCOMING / RECENT PASS EXPIRATIONS & DEMO ENDING
    # -------------------------------------------------------------------------
    if any(k in q_lower for k in ["expire", "expiring", "expiration", "validity", "demo ending", "plan expired"]):
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT user_id, student_id, full_name, phone_number, target_exam, paid_question_balance, vip_pass_expiry, demo_used, payment_id
            FROM users
            WHERE vip_pass_expiry IS NOT NULL AND is_banned = 0
            ORDER BY vip_pass_expiry ASC
        """)
        raw_users = cursor.fetchall()
        cursor.close()
        release_db(conn)

        target_cutoff = now_ist + timedelta(days=days_val)
        expiring_list = []
        expired_already = []

        for u in raw_users:
            exp_str = u.get("vip_pass_expiry", "")
            try:
                exp_dt = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S IST")
                exp_dt = IST.localize(exp_dt) if exp_dt.tzinfo is None else exp_dt
                if exp_dt < now_ist:
                    expired_already.append(u)
                elif now_ist <= exp_dt <= target_cutoff:
                    hours_left = max(0.0, round((exp_dt - now_ist).total_seconds() / 3600.0, 1))
                    u["hours_left"] = hours_left
                    expiring_list.append(u)
            except Exception:
                pass

        if "already" in q_lower or "total plan expired" in q_lower or "expired users" in q_lower:
            display_list = expired_already
            header_title = f"VIP Plans Already Expired ({len(expired_already)} Total)"
        else:
            display_list = expiring_list
            header_title = f"VIP Subscriptions Expiring within Next {days_val} Days ({len(expiring_list)} Total)"

        title = header_title
        columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Phone", "Daily Quota", "Pass Expiry Date", "Txn ID"]
        pdf_rows = []
        tg_lines = [
            f"⏳ **OMNISCIENT INTEL: VIP EXPIRY TELEMETRY**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ **{header_title}**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]

        for idx, u in enumerate(display_list, start=1):
            uid = u.get("user_id", "N/A")
            sid = u.get("student_id") or f"USER_{uid}"
            h_info = f"Left: `{u.get('hours_left', 'Expired')}h` | " if 'hours_left' in u else ""
            tg_lines.append(f"**{idx}. {u['full_name']}** (`{sid}` | ID: `{uid}`)\n   📱 Phone: `{u.get('phone_number')}` | {h_info}Expires: `{u['vip_pass_expiry']}`")
            pdf_rows.append([str(idx), str(uid), str(sid), str(u['full_name']), str(u.get('phone_number', 'N/A')), f"{u['paid_question_balance']} Qs", str(u['vip_pass_expiry']), str(u.get('payment_id', 'N/A'))])

        if not display_list:
            tg_lines.append("🎉 *Zero subscriptions found matching this condition.*")

        tg_lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📥 *Download complete list as PDF below:*")

        return {
            "title": title,
            "total_records": len(display_list),
            "summary_markdown": "\n".join(tg_lines),
            "columns": columns,
            "rows": pdf_rows,
            "kpis": {"Count": str(len(display_list))}
        }

    # -------------------------------------------------------------------------
    # 8. REVIEWS & STUDENT FEEDBACK SEARCH
    # -------------------------------------------------------------------------
    if any(k in q_lower for k in ["feedback", "review", "ratings", "what reviews"]):
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM student_feedback ORDER BY id DESC LIMIT 50")
        fbs = cursor.fetchall()
        cursor.close()
        release_db(conn)

        title = "Student Feedback & Platform Reviews Ledger"
        columns = ["S.No.", "Telegram ID", "Student Name", "Feedback Text", "Submitted At"]
        pdf_rows = []
        tg_lines = [
            "💬 **OMNISCIENT INTEL: STUDENT FEEDBACK & REVIEWS**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 **Total Reviews Found:** `{len(fbs)}`\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]

        for idx, f in enumerate(fbs, start=1):
            uid = f.get("user_id", "N/A")
            name = f.get("full_name") or f"User {uid}"
            fb_txt = f.get("feedback_text") or "N/A"
            sub_at = f.get("submitted_at") or "N/A"

            tg_lines.append(f"**{idx}. {name}** (ID: `{uid}`)\n   📅 Date: `{sub_at}`\n   💬 *\"{fb_txt}\"*\n")
            pdf_rows.append([str(idx), str(uid), str(name), str(fb_txt), str(sub_at)])

        if not fbs:
            tg_lines.append("ℹ️ *No reviews found in database.*")

        return {
            "title": title,
            "total_records": len(fbs),
            "summary_markdown": "\n".join(tg_lines),
            "columns": columns,
            "rows": pdf_rows,
            "kpis": {"Total Reviews": str(len(fbs))}
        }

    # -------------------------------------------------------------------------
    # 9. STUDENT DOSSIER SEARCH (By Name, Student ID, Telegram ID, Phone Number)
    # -------------------------------------------------------------------------
    clean_term = q_lower
    for w in ["details", "of", "profile", "student", "user", "info", "for", "search", "tell me", "show", "all", "who", "is", "about", "everything"]:
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
        title = f"Omniscient Student Dossier Search ({len(matched_users)} Found)"
        columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Phone", "Target Exam", "Daily Quota", "Pass Expiry", "PIN", "Sec Question", "Sec Answer", "Payment ID"]
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
            exp = u.get('vip_pass_expiry', 'N/A')
            pin = u.get('pin', 'N/A')
            sec_q = u.get('security_question', 'N/A')
            sec_a = u.get('security_answer', 'N/A')
            pid = u.get('payment_id', 'N/A')
            last_act = u.get('last_active', 'N/A')

            tg_lines.append(
                f"**{idx}. {name}** (`{sid}` | ID: `{uid}`)\n"
                f"   📱 Phone: `{phone}` | 🎯 Exam: `{exam}` | ⚡ Quota: `{quota}`\n"
                f"   ⏳ Expiry: `{exp}` | 🚦 Status: `{status}` | 🕒 Last Active: `{last_act}`\n"
                f"   🔑 PIN: `{pin}` | 🧾 Txn ID: `{pid}`\n"
                f"   ❓ Sec Q: *\"{sec_q}\"* | Ans: `{sec_a}`"
            )
            pdf_rows.append([str(idx), str(uid), str(sid), str(name), str(phone), str(exam), str(quota), str(exp), str(pin), str(sec_q), str(sec_a), str(pid)])

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
    # 10. LLM NL2SQL FALLBACK (For open-ended questions)
    # -------------------------------------------------------------------------
    llm_sql = execute_llm_nl2sql_fallback(query_text)
    if llm_sql:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cursor.execute(llm_sql)
            dynamic_rows = cursor.fetchall()
            cursor.close()
            release_db(conn)

            if dynamic_rows:
                sample = dict(dynamic_rows[0])
                columns = ["S.No."] + list(sample.keys())
                pdf_rows = []
                tg_lines = [
                    f"🧠 **OMNISCIENT INTEL: CUSTOM QUERY RESULTS**\n"
                    f"*(Query: \"{query_text}\")*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 **Total Matching Records:** `{len(dynamic_rows)}`\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                ]
                for idx, dr in enumerate(dynamic_rows[:30], start=1):
                    row_vals = [str(v) for v in dr.values()]
                    pdf_rows.append([str(idx)] + row_vals)
                    summary_item = " | ".join([f"**{k}:** `{v}`" for k, v in dr.items() if v is not None][:4])
                    tg_lines.append(f"**{idx}.** {summary_item}")

                if len(dynamic_rows) > 30:
                    tg_lines.append(f"\n*(+ {len(dynamic_rows) - 30} more records in full PDF ledger)*")

                tg_lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📥 *Download complete dynamic report as PDF below:*")

                return {
                    "title": f"Custom Query: {query_text[:35]}",
                    "total_records": len(dynamic_rows),
                    "summary_markdown": "\n".join(tg_lines),
                    "columns": columns,
                    "rows": pdf_rows,
                    "kpis": {"Query Records": str(len(dynamic_rows))}
                }
        except Exception as e:
            if conn:
                release_db(conn)
            logger.error(f"[DYNAMIC SQL EXEC ERROR] {e}")

    # -------------------------------------------------------------------------
    # 11. OMNISCIENT PLATFORM CRAWLER (General Multi-Table Overview)
    # -------------------------------------------------------------------------
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT user_id, student_id, full_name, phone_number, target_exam, state, paid_question_balance, vip_pass_expiry, payment_id
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

    title = f"Omniscient Database Crawler Overview ({len(all_matched_users)} Records)"
    columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Phone", "Target Exam", "State", "Daily Quota", "Txn ID"]
    pdf_rows = []
    tg_lines = [
        f"🧠 **OMNISCIENT ADMIN CRAWLER RESULTS**\n"
        f"*(Query: \"{query_text}\" — Showing {len(all_matched_users)} records)*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 **Total Registered Scholars:** `{total_u}` | 💳 **Paid:** `{paid_u}` | 💰 **Gross Revenue:** `₹{tot_rev} INR`\n"
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
        pid = u.get("payment_id", "N/A")

        tg_lines.append(f"**{idx}. {name}** (`{sid}` | ID: `{uid}`)\n   📱 Phone: `{phone}` | Exam: `{exam}` | State: `{state}` | Quota: `{quota}`")
        pdf_rows.append([str(idx), str(uid), str(sid), str(name), str(phone), str(exam), str(state), quota, str(pid)])

    tg_lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📥 *Download complete database report as PDF below:*")

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