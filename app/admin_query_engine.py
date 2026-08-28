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

# Optional Grok / xAI API Integration
XAI_API_KEY = os.getenv("XAI_API_KEY", os.getenv("GROK_API_KEY", "")).strip()
XAI_API_URL = "https://api.x.ai/v1/chat/completions"

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
    """Escapes markdown formatting characters to prevent Telegram parse errors."""
    if text is None:
        return "N/A"
    s = str(text)
    for c in ["*", "_", "`", "[", "]"]:
        s = s.replace(c, " ")
    return " ".join(s.split()).strip()


def parse_date_safely(date_str: str) -> datetime:
    """Extracts timezone-aware datetime objects from various timestamp formats."""
    if not date_str or str(date_str).strip().lower() in ('', 'none', 'active', 'n/a', 'null'):
        return None
    clean_d = str(date_str).replace(" IST", "").strip()
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d %b %Y, %I:%M %p",
        "%d %B %Y, %I:%M %p",
        "%d %b %Y",
        "%d %B %Y",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y"
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(clean_d, fmt)
            return IST.localize(dt) if dt.tzinfo is None else dt
        except Exception:
            continue
    return None


def is_timestamp_matching_target(raw_timestamp_str: str, target_date: datetime.date) -> bool:
    """Robust date-matching helper that handles all string variations and date formats."""
    if not raw_timestamp_str:
        return False
    clean_s = str(raw_timestamp_str).strip()
    iso_date_str = target_date.strftime("%Y-%m-%d")
    alt_date_str = target_date.strftime("%d %b %Y")
    alt_date_str_full = target_date.strftime("%d %B %Y")
    
    if (iso_date_str in clean_s) or (alt_date_str in clean_s) or (alt_date_str_full in clean_s):
        return True
        
    parsed_dt = parse_date_safely(clean_s)
    if parsed_dt:
        return parsed_dt.astimezone(IST).date() == target_date
    return False


def call_grok_sql_synthesizer(query_text: str, schema_context: str) -> str:
    """Invokes Grok LLM to synthesize safe, read-only SQL queries from natural language."""
    if not XAI_API_KEY:
        return None

    system_prompt = (
        "You are an expert read-only PostgreSQL data analyst assistant for an education Telegram bot. "
        "Given the database schema, convert the admin's natural question into a single, safe SELECT SQL query. "
        "Rules: ONLY return a valid JSON object: {\"sql\": \"SELECT ...\", \"title\": \"...\"}. "
        "Do NOT write any DROP, UPDATE, INSERT, or DELETE statements. All dates are Asia/Kolkata (IST)."
    )

    payload = {
        "model": "grok-beta",
        "messages": [
            {"role": "system", "content": f"{system_prompt}\nSchema:\n{schema_context}"},
            {"role": "user", "content": query_text}
        ],
        "temperature": 0.1
    }

    try:
        req = urllib.request.Request(
            XAI_API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {XAI_API_KEY}"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"].strip()
            
            json_match = re.search(r"\{.*\}", content, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group(0))
                sql = parsed.get("sql", "").strip()
                if sql.upper().startswith("SELECT"):
                    return sql
    except Exception as e:
        logger.warning(f"[GROK API SYNTHESIZER NOTE] {e}")
    return None


def parse_and_execute_admin_query(query_text: str, context_correction: str = None) -> dict:
    """
    OMNISCIENT MASTER ADMIN INTELLIGENCE ENGINE:
    Two-Tier Intelligent Parser with Grok LLM Autolearning & Deterministic Deep-Thinking Engine.
    """
    q_lower = query_text.lower().strip()
    if context_correction:
        q_lower += f" {context_correction.lower().strip()}"

    now_ist = get_ist_now()
    today_date = now_ist.date()
    today_date_str = get_ist_date_str()
    today_start = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_date = (now_ist - timedelta(days=1)).date()
    yesterday_date_str = yesterday_date.strftime("%Y-%m-%d")
    yesterday_start = today_start - timedelta(days=1)

    try:
        # =========================================================================
        # 1. ADMIN IDENTITY, CREATOR INFO & OWNER DETAILS
        # =========================================================================
        if any(k in q_lower for k in [
            "who is admin", "who is the admin", "admin name", "creator", "owner", 
            "who created", "who made", "admin info", "about admin", "about himanshu", 
            "himanshu sir", "himanshu details", "admin bio"
        ]):
            conn = get_db()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT password_hash, updated_at FROM admin_security WHERE id = 1")
            sec_row = cursor.fetchone()
            cursor.close()
            release_db(conn)

            admin_pin = sec_row["password_hash"] if sec_row and sec_row.get("password_hash") else "5330"
            
            tg_lines = [
                "👑 **OMNISCIENT INTEL: MASTER ADMIN DOSSIER**",
                "• • • ✧ • • •",
                "👤 **Platform Creator & Lead Educator:** Himanshu Sir",
                "🏆 **Credentials:** AIR #65 | 96.7/100 Marks in BSF HCM",
                "🎖 **Examinations Qualified:** SSC CGL (3x), SSC CHSL (3x), SSC CPO (3x), DP HCM",
                "• • • ✧ • • •",
                "📲 **Official Community & Channels:**",
                "• Telegram Channel: @LEARNWITHHIM",
                "• YouTube Channel: https://youtube.com/@learnwithhim",
                "• Instagram: @learnwithhimm",
                "• Master Admin PIN: `" + str(admin_pin) + "`",
                "• • • ✧ • • •"
            ]

            return {
                "title": "Master Admin & Creator Dossier",
                "total_records": 1,
                "summary_markdown": "\n".join(tg_lines),
                "columns": ["Field", "Information"],
                "rows": [
                    ["Admin Name", "Himanshu Sir"],
                    ["Designation", "Platform Creator & Lead Educator"],
                    ["Rank / Record", "AIR #65 (96.7/100 Marks in BSF HCM)"],
                    ["Exams Qualified", "SSC CGL (3x), CHSL (3x), CPO (3x), DP HCM"],
                    ["Telegram", "@LEARNWITHHIM"],
                    ["Master PIN", str(admin_pin)]
                ],
                "kpis": {"Lead Admin": "Himanshu Sir", "AIR Rank": "#65"}
            }

        # =========================================================================
        # 2. PDF GENERATION LOGS & REPORTS TELEMETRY
        # =========================================================================
        if any(k in q_lower for k in [
            "pdf report", "pdf reports", "pdf generation", "generated pdf", 
            "downloaded pdf", "pdf log", "pdf logs", "pdf history"
        ]):
            conn = get_db()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT p.id, p.user_id, p.pdf_type, p.generated_at,
                       u.student_id, u.full_name, u.phone_number, u.target_exam
                FROM pdf_generation_logs p
                LEFT JOIN users u ON p.user_id = u.user_id
                ORDER BY p.id DESC
            """)
            pdf_logs = cursor.fetchall()
            cursor.close()
            release_db(conn)

            is_today = "today" in q_lower
            is_yesterday = "yesterday" in q_lower

            matched_logs = []
            for l in pdf_logs:
                g_str = str(l.get("generated_at", ""))
                if is_today:
                    if is_timestamp_matching_target(g_str, today_date):
                        matched_logs.append(l)
                elif is_yesterday:
                    if is_timestamp_matching_target(g_str, yesterday_date):
                        matched_logs.append(l)
                else:
                    matched_logs.append(l)

            time_scope = "Today" if is_today else ("Yesterday" if is_yesterday else "All-Time")
            title = f"PDF Reports Generation Telemetry ({time_scope})"
            columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Report Type", "Generated At (IST)"]
            pdf_rows = []

            tg_lines = [
                f"📄 **OMNISCIENT INTEL: PDF REPORT LOGS ({time_scope.upper()})**",
                "• • • ✧ • • •",
                f"📊 **Total PDF Reports Generated:** `{len(matched_logs)}`",
                "• • • ✧ • • •\n"
            ]

            for idx, pl in enumerate(matched_logs, start=1):
                uid = pl.get("user_id", "N/A")
                sid = clean_text(pl.get("student_id") or f"USER_{uid}")
                name = clean_text(pl.get("full_name") or "Student")
                ptype = clean_text(str(pl.get("pdf_type", "")).replace("_", " ").title())
                g_at = clean_text(pl.get("generated_at") or "N/A")

                if idx <= 20:
                    tg_lines.append(f"**{idx}. {name}** (`{sid}` | ID: `{uid}`)\n   📄 Type: `{ptype}`\n   ⏰ Generated At: `{g_at}`\n")
                pdf_rows.append([str(idx), str(uid), str(sid), name, ptype, g_at])

            if len(matched_logs) > 20:
                tg_lines.append(f"*(+ {len(matched_logs) - 20} more PDF downloads in attached PDF ledger)*")
            if not matched_logs:
                tg_lines.append(f"ℹ️ *Zero PDF reports generated for {time_scope}.*")

            return {
                "title": title,
                "total_records": len(matched_logs),
                "summary_markdown": "\n".join(tg_lines),
                "columns": columns,
                "rows": pdf_rows,
                "kpis": {"Timeframe": time_scope, "PDF Downloads": str(len(matched_logs))}
            }

        # =========================================================================
        # 3. ADMIN MASTER PIN / PASSWORD QUERY
        # =========================================================================
        if any(k in q_lower for k in ["admin password", "admin pin", "master pin", "master password", "admin pass"]):
            conn = get_db()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT password_hash, updated_at FROM admin_security WHERE id = 1")
            row = cursor.fetchone()
            cursor.close()
            release_db(conn)

            admin_pin = row["password_hash"] if row and row.get("password_hash") else "5330"
            updated_at = row["updated_at"] if row and row.get("updated_at") else "Default"

            tg_lines = [
                "🔑 **ADMIN SECURITY INTELLIGENCE**",
                "• • • ✧ • • •",
                f"👑 **Current Admin Master PIN:** `{admin_pin}`",
                f"⏰ **Last Updated:** `{updated_at}`",
                "• • • ✧ • • •",
                "⚠️ *Confidential: Master Admin PIN.*"
            ]

            return {
                "title": "Admin Master PIN Security Report",
                "total_records": 1,
                "summary_markdown": "\n".join(tg_lines),
                "columns": ["Key", "Value"],
                "rows": [["Admin PIN", str(admin_pin)], ["Last Updated", str(updated_at)]],
                "kpis": {"Master PIN": str(admin_pin)}
            }

        # =========================================================================
        # 4. BLOCKED / INACTIVE USERS QUERY
        # =========================================================================
        if "block" in q_lower or "blocked" in q_lower or "inactive user" in q_lower or "inactive student" in q_lower:
            conn = get_db()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT u.user_id, u.student_id, u.full_name, u.phone_number, u.target_exam, u.state,
                       b.blocked_at, u.paid_question_balance, u.created_at
                FROM blocked_bot_users b
                LEFT JOIN users u ON b.user_id = u.user_id
                ORDER BY b.blocked_at DESC
            """)
            blocked_users = cursor.fetchall()
            cursor.close()
            release_db(conn)

            is_today = "today" in q_lower
            matched_blocked = []
            for b in blocked_users:
                b_time = str(b.get("blocked_at", ""))
                if is_today:
                    if is_timestamp_matching_target(b_time, today_date):
                        matched_blocked.append(b)
                else:
                    matched_blocked.append(b)

            time_scope = "Today" if is_today else "All-Time"
            title = f"Blocked / Inactive Users Report ({time_scope})"
            columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Phone", "Target Exam", "State", "Blocked Timestamp"]
            pdf_rows = []
            tg_lines = [
                f"🛑 **OMNISCIENT INTEL: BLOCKED USERS ({time_scope.upper()})**",
                "• • • ✧ • • •",
                f"📊 **Total Blocked Users Found:** `{len(matched_blocked)}`",
                "• • • ✧ • • •\n"
            ]

            for idx, b in enumerate(matched_blocked, start=1):
                uid = b.get("user_id", "N/A")
                sid = clean_text(b.get("student_id") or f"USER_{uid}")
                name = clean_text(b.get("full_name") or "User")
                phone = clean_text(b.get("phone_number") or "N/A")
                exam = clean_text(b.get("target_exam") or "N/A")
                state = clean_text(b.get("state") or "N/A")
                b_at = clean_text(b.get("blocked_at") or "N/A")

                if idx <= 20:
                    tg_lines.append(f"**{idx}. {name}** (`{sid}` | ID: `{uid}`)\n   📱 Phone: `{phone}` | 🎯 Exam: `{exam}`\n   ⏰ Blocked At: `{b_at}`\n")
                pdf_rows.append([str(idx), str(uid), str(sid), name, str(phone), str(exam), str(state), str(b_at)])

            if len(matched_blocked) > 20:
                tg_lines.append(f"*(+ {len(matched_blocked) - 20} more records in attached PDF report)*")
            if not matched_blocked:
                tg_lines.append(f"🎉 *Zero blocked users recorded for {time_scope}.*")

            return {
                "title": title,
                "total_records": len(matched_blocked),
                "summary_markdown": "\n".join(tg_lines),
                "columns": columns,
                "rows": pdf_rows,
                "kpis": {"Scope": time_scope, "Blocked Users": str(len(matched_blocked))}
            }

        # =========================================================================
        # 5. QUIZ ATTEMPTS & REPORTS (TODAY, YESTERDAY, DATE-SPECIFIC & OVERALL)
        # =========================================================================
        is_quiz_query = any(k in q_lower for k in ["quiz", "quizzes", "attempt", "attempts", "test report", "quiz report", "quiz summary"])
        is_today_quiz = "today" in q_lower and is_quiz_query
        is_yesterday_quiz = "yesterday" in q_lower and is_quiz_query

        if is_today_quiz or is_yesterday_quiz:
            target_scope_date = today_date if is_today_quiz else yesterday_date
            scope_str = today_date_str if is_today_quiz else yesterday_date_str
            scope_label = "Today" if is_today_quiz else "Yesterday"

            conn = get_db()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT qa.id, qa.user_id, qa.subject, qa.quiz_mode, qa.questions_attempted, 
                       qa.correct_answers, qa.wrong_answers, qa.score, qa.attempt_timestamp, qa.attempt_date,
                       u.student_id, u.full_name, u.phone_number, u.target_exam
                FROM quiz_attempts qa
                LEFT JOIN users u ON qa.user_id = u.user_id
                ORDER BY qa.id DESC
            """)
            all_attempts = cursor.fetchall()
            cursor.close()
            release_db(conn)

            matched_attempts = []
            for a in all_attempts:
                att_date_val = a.get("attempt_date") or ""
                att_ts_val = a.get("attempt_timestamp") or ""
                if (scope_str == att_date_val) or is_timestamp_matching_target(att_ts_val, target_scope_date):
                    matched_attempts.append(a)

            tot_attempts = len(matched_attempts)
            unique_students = len({r['user_id'] for r in matched_attempts})
            tot_qs = sum(r.get("questions_attempted", 0) for r in matched_attempts)
            tot_corr = sum(r.get("correct_answers", 0) for r in matched_attempts)
            avg_acc = round((tot_corr / tot_qs) * 100.0, 2) if tot_qs > 0 else 0.0

            title = f"Quiz Attempts Telemetry Report ({scope_label} - {scope_str})"
            columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Subject", "Mode", "Questions", "Correct", "Score", "Attempted At"]
            pdf_rows = []

            tg_lines = [
                f"🎯 **OMNISCIENT INTEL: {scope_label.upper()}'S QUIZ ATTEMPTS TELEMETRY**",
                "• • • ✧ • • •",
                f"📅 **Date:** `{scope_str}`",
                f"📚 **Quizzes Submitted:** `{tot_attempts}` Quizzes",
                f"👥 **Students Practicing:** `{unique_students}` Scholars",
                f"🖥 **Total Questions Solved:** `{tot_qs}` Questions",
                f"⭐ **Average Accuracy:** `{avg_acc}%`",
                "• • • ✧ • • •\n"
            ]

            for idx, a in enumerate(matched_attempts, start=1):
                uid = a.get("user_id", "N/A")
                sid = clean_text(a.get("student_id") or f"USER_{uid}")
                name = clean_text(a.get("full_name") or "Student")
                subj = clean_text(a.get("subject") or "Practice")
                qs = a.get("questions_attempted", 0)
                corr = a.get("correct_answers", 0)
                score = round(a.get("score", 0.0), 2)
                t_str = clean_text(a.get("attempt_timestamp") or "N/A")

                if idx <= 20:
                    tg_lines.append(f"**{idx}. {name}** (`{sid}`)\n   📖 Subject: `{subj}` | Solved: `{qs} Qs` (✅ {corr})\n   ⭐ Score: `{score}` | ⏰ Time: `{t_str}`\n")
                pdf_rows.append([str(idx), str(uid), str(sid), name, subj, clean_text(a.get("quiz_mode")), str(qs), str(corr), str(score), str(t_str)])

            if len(matched_attempts) > 20:
                tg_lines.append(f"*(+ {len(matched_attempts) - 20} more quiz sessions in attached PDF report)*")
            if not matched_attempts:
                tg_lines.append(f"ℹ️ *Zero quiz attempts logged for {scope_label} ({scope_str}).*")

            return {
                "title": title,
                "total_records": tot_attempts,
                "summary_markdown": "\n".join(tg_lines),
                "columns": columns,
                "rows": pdf_rows,
                "kpis": {"Date": scope_str, "Quizzes": str(tot_attempts), "Students": str(unique_students), "Questions": str(tot_qs)}
            }

        # Overall Quiz Volume
        if any(k in q_lower for k in ["total quiz", "total quizzes", "quiz attempts", "quizzes attempted", "how many quizzes", "tests completed"]):
            conn = get_db()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT attempt_date, COUNT(*) as total_quizzes, 
                       COUNT(DISTINCT user_id) as unique_students,
                       COALESCE(SUM(questions_attempted), 0) as total_qs,
                       COALESCE(SUM(correct_answers), 0) as total_correct
                FROM quiz_attempts
                GROUP BY attempt_date
                ORDER BY attempt_date DESC
            """)
            date_summary = cursor.fetchall()

            cursor.execute("""
                SELECT COUNT(*) as total_all_quizzes,
                       COUNT(DISTINCT user_id) as total_students,
                       COALESCE(SUM(questions_attempted), 0) as total_qs_all,
                       COALESCE(SUM(correct_answers), 0) as total_correct_all
                FROM quiz_attempts
            """)
            overall = cursor.fetchone()
            cursor.close()
            release_db(conn)

            tot_q = overall["total_all_quizzes"] or 0
            tot_st = overall["total_students"] or 0
            tot_qs = overall["total_qs_all"] or 0
            tot_corr = overall["total_correct_all"] or 0
            acc = round((tot_corr / tot_qs) * 100.0, 2) if tot_qs > 0 else 0.0

            title = "All-Time Quiz Attempts & Date-wise Breakdown"
            columns = ["S.No.", "Date (IST)", "Quizzes Solved", "Unique Students", "Questions Attempted", "Correct Answers"]
            pdf_rows = []
            tg_lines = [
                "📊 **OMNISCIENT INTEL: TOTAL QUIZ ATTEMPTS TELEMETRY**",
                "• • • ✧ • • •",
                f"📚 **All-Time Quizzes Completed:** `{tot_q}` Quizzes",
                f"👥 **Unique Participating Students:** `{tot_st}` Students",
                f"🖥 **Total Questions Solved:** `{tot_qs}` Questions",
                f"⭐ **Global Accuracy:** `{acc}%`",
                "• • • ✧ • • •",
                "📅 **DATE-WISE QUIZ BREAKDOWN (Recent):**\n"
            ]

            for idx, d in enumerate(date_summary, start=1):
                dt = d.get("attempt_date", "N/A")
                quizzes = d.get("total_quizzes", 0)
                students = d.get("unique_students", 0)
                qs = d.get("total_qs", 0)
                corr = d.get("total_correct", 0)

                if idx <= 15:
                    tg_lines.append(f"🗓 **{dt}:** `{quizzes}` Quizzes | `{students}` Students | `{qs}` Questions")
                pdf_rows.append([str(idx), str(dt), str(quizzes), str(students), str(qs), str(corr)])

            if len(date_summary) > 15:
                tg_lines.append(f"\n*(+ {len(date_summary) - 15} more dates in attached PDF report)*")

            return {
                "title": title,
                "total_records": tot_q,
                "summary_markdown": "\n".join(tg_lines),
                "columns": columns,
                "rows": pdf_rows,
                "kpis": {"Total Quizzes": str(tot_q), "Total Students": str(tot_st), "Total Questions": str(tot_qs)}
            }

        # =========================================================================
        # 6. SUMMARY OF DEMO & PAID USERS
        # =========================================================================
        if any(k in q_lower for k in ["summary of demo", "paid vs demo", "demo and paid", "demo & paid", "user summary", "plan breakdown", "users breakdown"]):
            conn = get_db()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT user_id, paid_question_balance, payment_id, is_banned FROM users WHERE is_banned != 2")
            all_users = cursor.fetchall()

            cursor.execute("""
                SELECT plan_name, COUNT(*) as count, SUM(amount_paid) as revenue 
                FROM payment_transactions 
                WHERE plan_key != 'FREE_DEMO' AND amount_paid > 0
                GROUP BY plan_name 
                ORDER BY revenue DESC
            """)
            plan_breakdown = cursor.fetchall()
            cursor.close()
            release_db(conn)

            paid_count = 0
            demo_count = 0
            for u in all_users:
                if (u.get("paid_question_balance", 0) > 20) or (u.get("payment_id") not in (None, 'DEMO_PASS', 'OFFICIAL_SUBSCRIBED')):
                    paid_count += 1
                else:
                    demo_count += 1

            total_active = len(all_users)
            paid_pct = round((paid_count / total_active) * 100.0, 1) if total_active > 0 else 0.0
            demo_pct = round((demo_count / total_active) * 100.0, 1) if total_active > 0 else 0.0

            title = "Summary of Demo vs Paid VIP Users"
            columns = ["S.No.", "Category", "Student Count", "Percentage", "Revenue Generated"]
            pdf_rows = [
                ["1", "Active Paid VIP Users", str(paid_count), f"{paid_pct}%", "Refer to Plans"],
                ["2", "Free Demo Users", str(demo_count), f"{demo_pct}%", "Rs. 0"]
            ]

            tg_lines = [
                "📊 **OMNISCIENT INTEL: DEMO VS PAID USERS SUMMARY**",
                "• • • ✧ • • •",
                f"👥 **Total Active Registered:** `{total_active}` Students",
                f"🟢 **Paid VIP Subscribers:** `{paid_count}` ({paid_pct}%)",
                f"🎁 **Free Demo Users:** `{demo_count}` ({demo_pct}%)",
                "• • • ✧ • • •",
                "📦 **PAID PLAN TIERS BREAKDOWN:**\n"
            ]

            for idx, pb in enumerate(plan_breakdown, start=1):
                p_name = pb.get("plan_name", "Pack")
                cnt = pb.get("count", 0)
                rev = pb.get("revenue", 0)
                tg_lines.append(f"• **{p_name}:** `{cnt}` purchases (₹{rev} INR)")
                pdf_rows.append([str(idx + 2), str(p_name), str(cnt), "-", f"Rs. {rev}"])

            return {
                "title": title,
                "total_records": total_active,
                "summary_markdown": "\n".join(tg_lines),
                "columns": columns,
                "rows": pdf_rows,
                "kpis": {"Total Users": str(total_active), "Paid VIP": str(paid_count), "Free Demo": str(demo_count)}
            }

        # =========================================================================
        # 7. PAID USERS IN LAST X HOURS / DAYS (HOURLY / TIME-WINDOW QUERY)
        # =========================================================================
        hour_match = re.search(r"(\d+)\s*(?:hour|hr)", q_lower)
        days_match = re.search(r"(\d+)\s*day", q_lower)

        if ("paid" in q_lower or "txn" in q_lower or "bought" in q_lower or "purchase" in q_lower) and (hour_match or days_match or "today" in q_lower or "yesterday" in q_lower):
            conn = get_db()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT pt.payment_id, pt.plan_name, pt.amount_paid, pt.daily_quota, pt.created_at,
                       u.user_id, u.student_id, u.full_name, u.phone_number, u.target_exam, u.state
                FROM payment_transactions pt
                LEFT JOIN users u ON pt.user_id = u.user_id
                WHERE pt.plan_key != 'FREE_DEMO' AND pt.amount_paid > 0
                ORDER BY pt.id DESC
            """)
            all_txns = cursor.fetchall()
            cursor.close()
            release_db(conn)

            matched_txns = []
            gross_window_rev = 0.0

            if hour_match:
                hours_val = int(hour_match.group(1))
                time_cutoff = now_ist - timedelta(hours=hours_val)
                time_label = f"Last {hours_val} Hours"
                for t in all_txns:
                    t_dt = parse_date_safely(t.get("created_at", ""))
                    if t_dt and t_dt >= time_cutoff:
                        matched_txns.append(t)
                        gross_window_rev += float(t.get("amount_paid", 0) or 0)
            elif days_match:
                days_val = int(days_match.group(1))
                time_cutoff = now_ist - timedelta(days=days_val)
                time_label = f"Last {days_val} Days"
                for t in all_txns:
                    t_dt = parse_date_safely(t.get("created_at", ""))
                    if t_dt and t_dt >= time_cutoff:
                        matched_txns.append(t)
                        gross_window_rev += float(t.get("amount_paid", 0) or 0)
            elif "today" in q_lower:
                time_label = f"Today ({today_date_str})"
                for t in all_txns:
                    c_str = str(t.get("created_at", ""))
                    if is_timestamp_matching_target(c_str, today_date):
                        matched_txns.append(t)
                        gross_window_rev += float(t.get("amount_paid", 0) or 0)
            elif "yesterday" in q_lower:
                time_label = f"Yesterday ({yesterday_date_str})"
                for t in all_txns:
                    c_str = str(t.get("created_at", ""))
                    if is_timestamp_matching_target(c_str, yesterday_date):
                        matched_txns.append(t)
                        gross_window_rev += float(t.get("amount_paid", 0) or 0)

            title = f"Paid VIP Purchases ({time_label})"
            columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Phone", "Target Exam", "Plan Name", "Amount (INR)", "Txn ID", "Paid Date"]
            pdf_rows = []
            tg_lines = [
                f"💳 **OMNISCIENT INTEL: PAID VIP PURCHASES ({time_label.upper()})**",
                "• • • ✧ • • •",
                f"💵 **Revenue in Window:** `₹{gross_window_rev} INR`",
                f"📦 **Verified Purchases:** `{len(matched_txns)}`",
                "• • • ✧ • • •\n"
            ]

            for idx, t in enumerate(matched_txns, start=1):
                uid = t.get("user_id", "N/A")
                sid = clean_text(t.get("student_id") or f"USER_{uid}")
                name = clean_text(t.get("full_name") or "Student")
                phone = clean_text(t.get("phone_number") or "N/A")
                plan = clean_text(t.get("plan_name") or "VIP Plan")
                amt = t.get("amount_paid", 0)
                pid = clean_text(t.get("payment_id") or "N/A")
                pdate = clean_text(t.get("created_at") or "N/A")

                if idx <= 20:
                    tg_lines.append(
                        f"**{idx}. {name}** (`{sid}` | ID: `{uid}`)\n"
                        f"   💰 `{plan}` (₹{amt}) | 📱 `{phone}`\n"
                        f"   🧾 Txn: `{pid}` | 📅 `{pdate}`\n"
                    )
                pdf_rows.append([str(idx), str(uid), str(sid), name, str(phone), clean_text(t.get("target_exam")), plan, f"Rs. {amt}", str(pid), str(pdate)])

            if len(matched_txns) > 20:
                tg_lines.append(f"*(+ {len(matched_txns) - 20} more transactions in attached PDF report)*")
            if not matched_txns:
                tg_lines.append(f"ℹ️ *No paid plan purchases recorded in {time_label}.*")

            return {
                "title": title,
                "total_records": len(matched_txns),
                "summary_markdown": "\n".join(tg_lines),
                "columns": columns,
                "rows": pdf_rows,
                "kpis": {"Window": time_label, "Revenue": f"₹{gross_window_rev} INR", "Purchases": str(len(matched_txns))}
            }

        # =========================================================================
        # 8. PASS EXPIRATION, VALIDITY & DEMO ENDINGS
        # =========================================================================
        has_expiry_intent = any(k in q_lower for k in ["expire", "expiring", "expiry", "expiration", "validity", "demo ending", "plan expired", "pass expiry"])

        if has_expiry_intent:
            conn = get_db()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT u.user_id, u.student_id, u.full_name, u.phone_number, u.target_exam, 
                       u.paid_question_balance, u.vip_pass_expiry, u.payment_id, u.created_at,
                       pt.plan_name, pt.amount_paid
                FROM users u
                LEFT JOIN (
                    SELECT DISTINCT ON (user_id) user_id, plan_name, amount_paid 
                    FROM payment_transactions 
                    WHERE plan_key != 'FREE_DEMO' AND amount_paid > 0
                    ORDER BY user_id, id DESC
                ) pt ON u.user_id = pt.user_id
                WHERE u.vip_pass_expiry IS NOT NULL AND u.is_banned != 2
                ORDER BY u.vip_pass_expiry ASC
            """)
            raw_users = cursor.fetchall()
            cursor.close()
            release_db(conn)

            days_match_exp = re.search(r"(\d+)\s*day", q_lower)
            is_today = "today" in q_lower
            is_tomorrow = "tomorrow" in q_lower
            is_this_week = "week" in q_lower or "7 day" in q_lower
            is_this_month = "month" in q_lower
            is_already_expired = "already" in q_lower or "expired users" in q_lower or "past" in q_lower

            if days_match_exp:
                days_window = int(days_match_exp.group(1))
                target_cutoff = now_ist + timedelta(days=days_window)
                time_label = f"Next {days_window} Days"
            elif is_today:
                target_cutoff = today_start + timedelta(days=1)
                time_label = f"Today ({today_date_str})"
            elif is_tomorrow:
                target_cutoff = today_start + timedelta(days=2)
                time_label = "Tomorrow"
            elif is_this_week:
                target_cutoff = now_ist + timedelta(days=7)
                time_label = "This Week (Next 7 Days)"
            elif is_this_month:
                target_cutoff = now_ist + timedelta(days=30)
                time_label = "This Month"
            else:
                target_cutoff = now_ist + timedelta(days=7)
                time_label = "Upcoming (Next 7 Days)"

            only_paid = "paid" in q_lower or "vip" in q_lower
            only_demo = "demo" in q_lower or "free" in q_lower

            matched_expirations = []
            for u in raw_users:
                exp_dt = parse_date_safely(u.get("vip_pass_expiry", ""))
                if not exp_dt:
                    continue

                is_user_paid = (u.get("paid_question_balance", 0) > 20) or (u.get("amount_paid") and float(u.get("amount_paid", 0)) > 0)
                if only_paid and not is_user_paid:
                    continue
                if only_demo and is_user_paid:
                    continue

                if is_already_expired:
                    if exp_dt < now_ist:
                        u["hours_left"] = "Expired"
                        matched_expirations.append(u)
                elif is_today:
                    if exp_dt.date() == today_date and exp_dt >= now_ist:
                        hours_left = max(0.0, round((exp_dt - now_ist).total_seconds() / 3600.0, 1))
                        u["hours_left"] = f"{hours_left}h left"
                        matched_expirations.append(u)
                elif is_tomorrow:
                    tomorrow_date = (now_ist + timedelta(days=1)).date()
                    if exp_dt.date() == tomorrow_date:
                        hours_left = max(0.0, round((exp_dt - now_ist).total_seconds() / 3600.0, 1))
                        u["hours_left"] = f"{hours_left}h left"
                        matched_expirations.append(u)
                else:
                    if now_ist <= exp_dt <= target_cutoff:
                        hours_left = max(0.0, round((exp_dt - now_ist).total_seconds() / 3600.0, 1))
                        u["hours_left"] = f"{hours_left}h left"
                        matched_expirations.append(u)

            type_label = "Paid VIP Plan" if only_paid else ("Free Demo" if only_demo else "VIP & Demo")
            title = f"{type_label} Expirations Report ({time_label})"
            columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Phone", "Target Exam", "Active Plan", "Daily Limit", "Time Remaining", "Pass Expiry Date", "Txn ID"]
            pdf_rows = []
            tg_lines = [
                f"⏳ **OMNISCIENT INTEL: {type_label.upper()} EXPIRY TELEMETRY**\n"
                f"• • • ✧ • • •\n"
                f"📅 **Filter Window:** `{time_label}`\n"
                f"⚠️ **Total Expiring Students Found:** `{len(matched_expirations)}`\n"
                f"• • • ✧ • • •\n"
            ]

            for idx, u in enumerate(matched_expirations, start=1):
                uid = u.get("user_id", "N/A")
                sid = clean_text(u.get("student_id") or f"USER_{uid}")
                name = clean_text(u.get('full_name') or "Student")
                phone = clean_text(u.get('phone_number') or "N/A")
                exam = clean_text(u.get('target_exam') or "N/A")
                plan = clean_text(u.get('plan_name') or "VIP Plan")
                quota = u.get('paid_question_balance', 20)
                exp_date = clean_text(u.get('vip_pass_expiry') or "N/A")
                h_left = u.get('hours_left', 'N/A')
                pid = clean_text(u.get('payment_id') or "N/A")

                if idx <= 20:
                    tg_lines.append(
                        f"**{idx}. {name}** (`{sid}` | ID: `{uid}`)\n"
                        f"   📦 Plan: `{plan}` | ⚡ Quota: `{quota} Qs/D`\n"
                        f"   📱 Phone: `{phone}` | ⏳ Expiry: `{exp_date}`\n"
                        f"   ⏱ Status: `{h_left}` | 🧾 Txn ID: `{pid}`\n"
                    )
                pdf_rows.append([str(idx), str(uid), str(sid), name, str(phone), str(exam), str(plan), f"{quota} Qs/D", str(h_left), str(exp_date), str(pid)])

            if len(matched_expirations) > 20:
                tg_lines.append(f"*(+ {len(matched_expirations) - 20} more expiring accounts in attached PDF ledger)*")

            if not matched_expirations:
                tg_lines.append(f"🎉 *Zero {type_label.lower()} accounts found expiring in {time_label}.*")

            return {
                "title": title,
                "total_records": len(matched_expirations),
                "summary_markdown": "\n".join(tg_lines),
                "columns": columns,
                "rows": pdf_rows,
                "kpis": {"Timeframe": time_label, "Expiring Students": str(len(matched_expirations)), "Category": type_label}
            }

        # =========================================================================
        # 9. FESTIVALS & SALE STRATEGY CALENDAR
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
                "• • • ✧ • • •\n"
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

            return {
                "title": title,
                "total_records": len(upcoming_festivals),
                "summary_markdown": "\n".join(tg_lines),
                "columns": columns,
                "rows": pdf_rows,
                "kpis": {"Logged Promotions": str(len(past_sales)), "Upcoming Opportunities": f"{len(upcoming_festivals)} Events"}
            }

        # =========================================================================
        # 10. FINANCIAL REVENUE & PAYMENT COLLECTIONS
        # =========================================================================
        if any(k in q_lower for k in ["revenue", "earning", "income", "collection", "money earned", "sales stats", "transactions", "payment history", "txns", "total sales"]):
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
                c_str = str(t.get("created_at", ""))
                t_dt = parse_date_safely(c_str)

                if is_today:
                    timeframe_label = f"Today ({today_date_str})"
                    if is_timestamp_matching_target(c_str, today_date) or (t_dt and t_dt >= today_start):
                        filtered_txns.append(t)
                        gross_rev += amt
                elif is_yesterday:
                    timeframe_label = f"Yesterday ({yesterday_date_str})"
                    if is_timestamp_matching_target(c_str, yesterday_date) or (t_dt and yesterday_start <= t_dt < today_start):
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
                f"• • • ✧ • • •\n"
                f"📅 **Scope:** `{timeframe_label}`\n"
                f"💵 **Total Revenue:** `₹{gross_rev} INR`\n"
                f"🧾 **Total Verified Orders:** `{len(filtered_txns)}`\n"
                f"• • • ✧ • • •\n"
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

            return {
                "title": title,
                "total_records": len(filtered_txns),
                "summary_markdown": "\n".join(tg_lines),
                "columns": columns,
                "rows": pdf_rows,
                "kpis": {"Timeframe": timeframe_label, "Gross Revenue": f"₹{gross_rev} INR", "Total Orders": str(len(filtered_txns))}
            }

        # =========================================================================
        # 11. STUDENT REGISTRATIONS & USER LISTINGS
        # =========================================================================
        is_registration_query = any(k in q_lower for k in [
            "register", "registered", "joined", "new user", "new users", 
            "new student", "new students", "signup", "onboarded", "users list", 
            "students list", "user list", "total registered"
        ])
        is_today = "today" in q_lower
        is_yesterday = "yesterday" in q_lower
        is_this_week = "this week" in q_lower or "past week" in q_lower
        is_this_month = "this month" in q_lower

        if is_registration_query:
            conn = get_db()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT * FROM users WHERE is_banned != 2 ORDER BY user_id DESC")
            all_users = cursor.fetchall()
            cursor.close()
            release_db(conn)

            matched_date_users = []
            if is_today:
                date_label = f"Today ({today_date_str})"
                for u in all_users:
                    c_str = str(u.get("created_at", ""))
                    if is_timestamp_matching_target(c_str, today_date):
                        matched_date_users.append(u)
            elif is_yesterday:
                date_label = f"Yesterday ({yesterday_date_str})"
                for u in all_users:
                    c_str = str(u.get("created_at", ""))
                    if is_timestamp_matching_target(c_str, yesterday_date):
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
            else:
                date_label = "All Registered Students"
                matched_date_users = all_users

            title = f"Student Registrations Report ({date_label})"
            columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Phone", "Target Exam", "State", "PIN", "Registered At"]
            pdf_rows = []
            tg_lines = [
                f"👥 **OMNISCIENT INTEL: STUDENT REGISTRATIONS ({date_label.upper()})**\n"
                f"• • • ✧ • • •\n"
                f"📊 **Total Matching Students:** `{len(matched_date_users)}`\n"
                f"• • • ✧ • • •\n"
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

            return {
                "title": title,
                "total_records": len(matched_date_users),
                "summary_markdown": "\n".join(tg_lines),
                "columns": columns,
                "rows": pdf_rows,
                "kpis": {"Timeframe": date_label, "New Registrations": f"{len(matched_date_users)} Students"}
            }

        # =========================================================================
        # 12. TOTAL PAID VIP USERS DIRECTORY
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
                WHERE pt.plan_key != 'FREE_DEMO' AND pt.amount_paid > 0 AND u.is_banned != 2
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
                "• • • ✧ • • •\n"
                f"👑 **Total Verified Paid Students:** `{len(paid_students)}`\n"
                f"💰 **Total Gross Revenue Collected:** `₹{total_rev} INR`\n"
                "• • • ✧ • • •\n"
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

            return {
                "title": title,
                "total_records": len(paid_students),
                "summary_markdown": "\n".join(tg_lines),
                "columns": columns,
                "rows": pdf_rows,
                "kpis": {"Total Paid Scholars": str(len(paid_students)), "Gross Revenue": f"₹{total_rev} INR"}
            }

        # =========================================================================
        # 13. ONLINE PRACTICE PATTERNS & TELEMETRY
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
                WHERE u.is_banned != 2
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
                "• • • ✧ • • •\n"
                f"📊 **Scholars Analyzed:** `{len(rows)}`\n"
                "• • • ✧ • • •\n"
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

            return {
                "title": title,
                "total_records": len(rows),
                "summary_markdown": "\n".join(tg_lines),
                "columns": columns,
                "rows": pdf_rows,
                "kpis": {"Scholars Tracked": str(len(rows))}
            }

        # =========================================================================
        # 14. STUDENT FEEDBACK & REVIEWS
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
                "• • • ✧ • • •\n"
                f"📊 **Total Reviews Logged:** `{len(feedbacks)}`\n"
                "• • • ✧ • • •\n"
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
        # 15. UNIVERSAL MULTI-DIMENSIONAL SEARCH & DIRECT STUDENT DOSSIERS
        # =========================================================================
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        matched_state = None
        for st in INDIAN_STATES:
            if st in q_lower:
                matched_state = st
                break

        matched_exam = None
        for ek, ev in EXAM_KEYWORDS.items():
            if ek in q_lower:
                matched_exam = ev
                break

        matched_plan = None
        for pk, pv in PLAN_TIER_KEYWORDS.items():
            if pk in q_lower:
                matched_plan = pv
                break

        filter_banned = None
        if "banned" in q_lower:
            filter_banned = 1
        elif "active" in q_lower or "unbanned" in q_lower:
            filter_banned = 0

        filter_paid_only = None
        if any(k in q_lower for k in ["paid users", "vip users", "subscribers", "paid students", "who bought"]):
            filter_paid_only = True
        elif any(k in q_lower for k in ["free users", "demo users", "unpaid"]):
            filter_paid_only = False

        conditions = ["u.is_banned != 2"]
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

        search_keywords = q_lower
        for stopw in [
            "give", "me", "show", "tell", "details", "of", "list", "all", "the", "total", 
            "users", "students", "who", "is", "about", "student", "user", "info", "find", 
            "search", "pin", "password", "security", "registered", "yesterday", "today", 
            "tomorrow", "phone", "number", "profile", "please", "can", "you", "name"
        ]:
            search_keywords = re.sub(r'\b' + stopw + r'\b', '', search_keywords)
        search_keywords = search_keywords.strip().replace("?", "").replace("!", "")

        if len(search_keywords) >= 2 and not (matched_state or matched_exam or matched_plan or filter_paid_only is not None):
            conditions.append("(LOWER(u.full_name) LIKE %s OR LOWER(u.student_id) LIKE %s OR u.phone_number LIKE %s OR CAST(u.user_id AS TEXT) LIKE %s)")
            p_term = f"%{search_keywords}%"
            params.extend([p_term, p_term, p_term, p_term])

        query_sql = f"""
            SELECT DISTINCT ON (u.user_id)
                   u.user_id, u.student_id, u.full_name, u.phone_number, u.target_exam, u.state,
                   u.paid_question_balance, u.vip_pass_expiry, u.pin, u.security_question, u.security_answer,
                   u.payment_id, u.created_at, u.last_active, u.is_banned,
                   COALESCE(qa.total_quizzes, 0) as total_quizzes_done
            FROM users u
            LEFT JOIN (
                SELECT user_id, COUNT(*) as total_quizzes FROM quiz_attempts GROUP BY user_id
            ) qa ON u.user_id = qa.user_id
            WHERE {' AND '.join(conditions)}
            ORDER BY u.user_id DESC
            LIMIT 100
        """

        cursor.execute(query_sql, tuple(params))
        matched_users = cursor.fetchall()
        cursor.close()
        release_db(conn)

        title = f"Student Search Dossier ({len(matched_users)} Records)"
        columns = ["S.No.", "Telegram ID", "Student ID", "Full Name", "Phone", "Target Exam", "Daily Quota", "Total Quizzes Done", "Pass Expiry", "PIN"]
        pdf_rows = []
        
        tg_lines = [
            f"🔍 **OMNISCIENT INTEL: QUERY RESULTS ({len(matched_users)} Records)**",
            "• • • ✧ • • •\n"
        ]

        for idx, u in enumerate(matched_users, start=1):
            uid = u['user_id']
            sid = clean_text(u.get('student_id') or f"USER_{uid}")
            name = clean_text(u.get('full_name') or "Student")
            phone = clean_text(u.get('phone_number') or "N/A")
            exam = clean_text(u.get('target_exam') or "N/A")
            state = clean_text(u.get('state') or "N/A")
            quota = f"{u.get('paid_question_balance', 20)} Qs/D"
            quizzes_done = u.get("total_quizzes_done", 0)
            exp = clean_text(u.get('vip_pass_expiry') or "Active")
            pin = clean_text(u.get('pin') or "N/A")
            last_act = clean_text(u.get('last_active') or "N/A")

            if idx <= 20:
                tg_lines.append(
                    f"**{idx}. {name}** (`{sid}` | ID: `{uid}`)\n"
                    f"   📱 Phone: `{phone}` | 🎯 Exam: `{exam}` | 📍 `{state}`\n"
                    f"   📚 Total Quizzes Attempted: `{quizzes_done}` Quizzes\n"
                    f"   ⚡ Quota: `{quota}` | ⏳ Expiry: `{exp}` | 🔑 PIN: `{pin}`\n"
                    f"   🕒 Last Active: `{last_act}`\n"
                )
            pdf_rows.append([str(idx), str(uid), str(sid), name, str(phone), exam, quota, str(quizzes_done), exp, pin])

        if len(matched_users) > 20:
            tg_lines.append(f"*(+ {len(matched_users) - 20} more records in attached PDF report)*")

        if not matched_users:
            tg_lines.append(f"ℹ️ *Zero records found matching \"{clean_text(query_text)}\".*")

        return {
            "title": title,
            "total_records": len(matched_users),
            "summary_markdown": "\n".join(tg_lines),
            "columns": columns,
            "rows": pdf_rows,
            "kpis": {"Matched Records": str(len(matched_users))}
        }

    except Exception as general_err:
        logger.error(f"[PARSE ADMIN QUERY EXCEPTION] {general_err}")
        return {
            "title": "Query Result",
            "total_records": 0,
            "summary_markdown": f"⚠️ **Query error:** `{clean_text(str(general_err))}`",
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