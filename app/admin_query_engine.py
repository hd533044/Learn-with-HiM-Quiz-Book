import re
import os
import json
import logging
from datetime import datetime, timedelta
import pytz
from psycopg2.extras import RealDictCursor
from app.database import get_db, release_db, get_ist_now
from app.pdf_generator import generate_admin_query_dataset_pdf

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


def parse_and_execute_admin_query(query_text: str, context_correction: str = None) -> dict:
    """
    OMNISCIENT ADMIN INTELLIGENCE ENGINE (GOOGLE-ASSISTANT LEVEL):
    Performs deep-dive data extraction across all database tables (users, payment_transactions,
    quiz_attempts, student_queries, saved_questions, student_feedback, user_activity_time).
    Handles phone numbers, specific user lookups, custom filters, and self-correction.
    """
    q_lower = query_text.lower().strip()
    if context_correction:
        q_lower += f" (correction note: {context_correction.lower().strip()})"

    now_ist = get_ist_now()

    # -------------------------------------------------------------
    # INTENT 1: PHONE NUMBER & CONTACT LOOKUP
    # -------------------------------------------------------------
    if "phone" in q_lower or "mobile" in q_lower or "contact" in q_lower or "number" in q_lower:
        # Check if looking for a specific user or all phone numbers
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        try:
            # Extract potential name if specified
            search_term = q_lower
            for w in ["phone", "mobile", "number", "contact", "of", "what", "is", "the", "give", "me", "show", "all", "users", "student"]:
                search_term = search_term.replace(w, "")
            search_term = search_term.strip()

            if len(search_term) >= 2:
                cursor.execute("""
                    SELECT user_id, student_id, full_name, phone_number, target_exam, created_at 
                    FROM users 
                    WHERE LOWER(full_name) LIKE LOWER(%s) OR LOWER(student_id) LIKE LOWER(%s) OR phone_number LIKE %s
                    ORDER BY user_id DESC
                """, (f"%{search_term}%", f"%{search_term}%", f"%{search_term}%"))
            else:
                cursor.execute("SELECT user_id, student_id, full_name, phone_number, target_exam, created_at FROM users ORDER BY user_id DESC LIMIT 50")
            
            rows = cursor.fetchall()
        except Exception as e:
            logger.error(f"[AI QUERY PHONE ERROR] {e}")
            rows = []
        finally:
            cursor.close()
            release_db(conn)

        title = "Student Phone Numbers & Contact Directory"
        columns = ["S.No.", "Student ID", "Full Name", "Phone Number", "Target Exam", "Registered At"]
        pdf_rows = []
        tg_lines = [
            "📱 **OMNISCIENT INTEL: PHONE & CONTACT DIRECTORY**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 **Matching Records Found:** `{len(rows)} Students`\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]

        for idx, r in enumerate(rows, start=1):
            sid = r.get("student_id") or f"USER_{r['user_id']}"
            name = r.get("full_name") or "Unknown"
            phone = r.get("phone_number") or "N/A"
            exam = r.get("target_exam") or "N/A"
            reg = str(r.get("created_at", "N/A")).split(" ")[0]

            tg_lines.append(f"**{idx}. {name}** (`{sid}`)\n   📱 Phone: `{phone}` | Exam: `{exam}`")
            pdf_rows.append([str(idx), str(sid), str(name), str(phone), str(exam), str(reg)])

        if not rows:
            tg_lines.append("ℹ️ *No matching phone records found in database.*")

        tg_lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📥 *Download contact ledger as PDF below:*")

        return {
            "title": title,
            "summary_markdown": "\n".join(tg_lines),
            "columns": columns,
            "rows": pdf_rows,
            "kpis": {"Matching Contacts": str(len(rows)), "Query": query_text}
        }

    # -------------------------------------------------------------
    # INTENT 2: SPECIFIC STUDENT DEEP-DIVE DOSSIER
    # -------------------------------------------------------------
    specific_keywords = ["details of", "profile of", "student", "user", "info for", "search user", "all info"]
    is_specific_lookup = any(k in q_lower for k in specific_keywords) or (len(q_lower.split()) <= 4 and not any(w in q_lower for w in ["revenue", "summary", "total", "days data", "all time", "phone", "mobile"]))

    if is_specific_lookup:
        clean_term = q_lower
        for w in ["details", "of", "profile", "student", "user", "info", "for", "search", "tell me", "show", "all"]:
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
                LIMIT 5
            """, (f"%{clean_term}%", f"%{clean_term}%", f"%{clean_term}%", f"%{clean_term}%"))
            matched_users = cursor.fetchall()
        except Exception:
            matched_users = []
        finally:
            cursor.close()
            release_db(conn)

        if matched_users:
            u = matched_users[0]
            uid = u['user_id']
            sid = u.get('student_id', f"USER_{uid}")

            conn = get_db()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT COUNT(*) as test_count, COALESCE(SUM(questions_attempted),0) as tot_qs, COALESCE(SUM(correct_answers),0) as tot_corr FROM quiz_attempts WHERE user_id = %s", (uid,))
            q_stats = cursor.fetchone()

            cursor.execute("SELECT plan_name, amount_paid, payment_id, created_at FROM payment_transactions WHERE user_id = %s ORDER BY id DESC LIMIT 5", (uid,))
            pay_rows = cursor.fetchall()
            cursor.close()
            release_db(conn)

            title = f"Student Dossier: {u['full_name']} ({sid})"
            columns = ["Data Field", "Student Record Value"]
            pdf_rows = [
                ["Full Name", str(u.get('full_name'))],
                ["Student ID", str(sid)],
                ["Telegram ID", str(uid)],
                ["Phone Number", str(u.get('phone_number', 'N/A'))],
                ["Target Exam", str(u.get('target_exam', 'N/A'))],
                ["DOB / Age", f"{u.get('dob', 'N/A')} ({u.get('age', 'N/A')} yrs)"],
                ["State / Country", f"{u.get('state', 'N/A')}, {u.get('country', 'India')}"],
                ["Secret PIN", str(u.get('pin', 'Not Set'))],
                ["Security Q / A", f"{u.get('security_question', 'N/A')} -> {u.get('security_answer', 'N/A')}"],
                ["Paid Question Balance", f"{u.get('paid_question_balance', 20)} Qs/Day"],
                ["VIP Pass Expiry", str(u.get('vip_pass_expiry', 'N/A'))],
                ["Account Status", "BANNED 🛑" if u.get('is_banned') else "ACTIVE 🟢"],
                ["Total Quizzes Solved", str(q_stats.get('test_count', 0))],
                ["Total Questions Attempted", str(q_stats.get('tot_qs', 0))],
                ["Correct Answers", str(q_stats.get('tot_corr', 0))],
                ["Registered At", str(u.get('created_at', 'N/A'))]
            ]

            pay_summary = ", ".join([f"{p['plan_name']} (₹{p['amount_paid']})" for p in pay_rows]) if pay_rows else "None"

            tg_lines = [
                f"👤 **OMNISCIENT DOSSIER: {u['full_name']}** (`{sid}`)\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 **Telegram ID:** `{uid}`\n"
                f"📱 **Phone Number:** `{u.get('phone_number', 'N/A')}`\n"
                f"🎯 **Target Exam:** `{u.get('target_exam', 'N/A')}`\n"
                f"📍 **Location:** `{u.get('state', 'N/A')}, India`\n"
                f"🎂 **DOB / Age:** `{u.get('dob', 'N/A')}` (`{u.get('age', 'N/A')} yrs`)\n"
                f"🔑 **Secret PIN:** `{u.get('pin', 'Not Set')}`\n"
                f"🛡 **Security Q:** *\"{u.get('security_question', 'N/A')}\"*\n"
                f"🛡 **Security Ans:** `{u.get('security_answer', 'N/A')}`\n"
                f"💳 **Active Quota:** `{u.get('paid_question_balance', 20)} Qs/Day`\n"
                f"⏳ **VIP Pass Expiry:** `{u.get('vip_pass_expiry', 'N/A')}`\n"
                f"📦 **Payment History:** `{pay_summary}`\n"
                f"📊 **Quizzes Completed:** `{q_stats.get('test_count', 0)}` | **Correct:** `{q_stats.get('tot_corr', 0)}`\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📥 *Download student dossier as PDF:* "
            ]

            return {
                "title": title,
                "summary_markdown": "\n".join(tg_lines),
                "columns": columns,
                "rows": pdf_rows,
                "kpis": {"Student Name": u['full_name'], "Student ID": sid, "Phone": str(u.get('phone_number', 'N/A'))}
            }

    # -------------------------------------------------------------
    # INTENT 3: NEW REGISTRATIONS + PAID PLANS (Timeframe parsing)
    # -------------------------------------------------------------
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
        columns = ["S.No.", "Student ID", "Full Name", "Phone", "Target Exam", "Pack Name", "Amount", "Registered At"]
        pdf_rows = []
        tg_lines = [
            f"🧠 **OMNISCIENT INTEL: NEW PAID STUDENTS ({days_val} DAYS)**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 **Total New Paid Users:** `{len(filtered)}`\n"
            f"💰 **Total Revenue Generated:** `₹{total_rev} INR`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]

        for idx, r in enumerate(filtered, start=1):
            sid = r.get("student_id") or f"USER_{r['user_id']}"
            p_name = r.get("plan_name") or "VIP Pack"
            amt = r.get("amount_paid", 0)
            reg_date = str(r.get("created_at", "N/A")).split(" ")[0]
            phone = str(r.get("phone_number", "N/A"))

            tg_lines.append(f"**{idx}. {r['full_name']}** (`{sid}`)\n   📱 Phone: `{phone}` | Plan: `{p_name}` (₹{amt})")
            pdf_rows.append([str(idx), str(sid), str(r['full_name']), str(phone), str(r.get('target_exam', 'N/A')), str(p_name), f"Rs. {amt}", str(reg_date)])

        if not filtered:
            tg_lines.append("ℹ️ *No new paid student registrations found in this timeframe.*")

        tg_lines.append("\n📥 *Download PDF:*")
        return {"title": title, "summary_markdown": "\n".join(tg_lines), "columns": columns, "rows": pdf_rows, "kpis": {"Timeframe": f"Last {days_val} Days", "New Paid Users": str(len(filtered)), "Revenue": f"₹{total_rev} INR"}}

    # -------------------------------------------------------------
    # INTENT 4: UPCOMING PASS EXPIRATIONS
    # -------------------------------------------------------------
    elif "expire" in q_lower or "expiring" in q_lower or "expiration" in q_lower:
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
        columns = ["S.No.", "Student ID", "Full Name", "Phone", "Daily Quota", "Hours Left", "Exact Expiry"]
        pdf_rows = []
        tg_lines = [
            f"⏳ **OMNISCIENT INTEL: EXPIRING PASSES ({days_val} DAYS)**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ **Expiring Soon:** `{len(expiring_list)} Aspirants`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]

        for idx, u in enumerate(expiring_list[:20], start=1):
            sid = u.get("student_id") or f"USER_{u['user_id']}"
            tg_lines.append(f"**{idx}. {u['full_name']}** (`{sid}`) — Phone: `{u.get('phone_number')}` | Left: `{u['hours_left']}h`")
            pdf_rows.append([str(idx), str(sid), str(u['full_name']), str(u.get('phone_number', 'N/A')), f"{u['paid_question_balance']} Qs", f"{u['hours_left']}h", str(u['vip_pass_expiry'])])

        if not expiring_list:
            tg_lines.append("🎉 *Zero subscriptions expiring in this window.*")

        tg_lines.append("\n📥 *Download PDF below:*")
        return {"title": title, "summary_markdown": "\n".join(tg_lines), "columns": columns, "rows": pdf_rows, "kpis": {"Expiring Soon": str(len(expiring_list))}}

    # -------------------------------------------------------------
    # INTENT 5: REVENUE & FINANCIAL BREAKDOWN
    # -------------------------------------------------------------
    elif "revenue" in q_lower or "earning" in q_lower or "collection" in q_lower or "sales" in q_lower:
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

        tg_lines.append("\n📥 *Download PDF:*")
        return {"title": title, "summary_markdown": "\n".join(tg_lines), "columns": columns, "rows": pdf_rows, "kpis": {"Gross Revenue": f"₹{grand_total} INR"}}

    # -------------------------------------------------------------
    # INTENT 6: OMNISCIENT PLATFORM FALLBACK
    # -------------------------------------------------------------
    else:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT COUNT(*) as total_users, COUNT(CASE WHEN paid_question_balance > 20 THEN 1 END) as paid_count FROM users")
        u_stats = cursor.fetchone()

        cursor.execute("SELECT SUM(amount_paid) as total_rev FROM payment_transactions WHERE plan_key != 'FREE_DEMO'")
        rev_stats = cursor.fetchone()

        cursor.execute("SELECT target_exam, COUNT(*) as count FROM users GROUP BY target_exam ORDER BY count DESC LIMIT 8")
        top_exams = cursor.fetchall()
        cursor.close()
        release_db(conn)

        total_u = u_stats.get("total_users", 0)
        paid_u = u_stats.get("paid_count", 0)
        tot_rev = float(rev_stats.get("total_rev", 0) or 0)

        title = "Omniscient Platform Database Intelligence Summary"
        columns = ["Rank", "Target Exam Category", "Student Count", "Share"]
        pdf_rows = []
        tg_lines = [
            f"🧠 **OMNISCIENT ADMIN AI SEARCH RESULT**\n"
            f"*(Query: \"{query_text}\")*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 **Total Registered Scholars:** `{total_u}`\n"
            f"💳 **VIP Active Paid Subscribers:** `{paid_u}`\n"
            f"💰 **Gross Lifetime Revenue:** `₹{tot_rev} INR`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 **Top Target Exam Demographics:**\n"
        ]

        for idx, e in enumerate(top_exams, start=1):
            pct = round((e['count'] / max(1, total_u)) * 100, 1)
            tg_lines.append(f"  {idx}. **{e['target_exam']}**: `{e['count']} students` ({pct}%)")
            pdf_rows.append([str(idx), str(e['target_exam']), str(e['count']), f"{pct}%"])

        tg_lines.append("\n📥 *Download complete database report as PDF:*")
        return {"title": title, "summary_markdown": "\n".join(tg_lines), "columns": columns, "rows": pdf_rows, "kpis": {"Total Users": str(total_u), "Revenue": f"₹{tot_rev} INR"}}


def generate_admin_intelligence_pdf(query_result: dict) -> str:
    title = query_result.get("title", "Master Admin Intelligence Report")
    columns = query_result.get("columns", ["S.No.", "Item", "Value"])
    rows = query_result.get("rows", [])
    kpis = query_result.get("kpis", {})
    return generate_admin_query_dataset_pdf(title, columns, rows, kpis)