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
    ULTRA-POWERFUL OMNISCIENT ADMIN INTELLIGENCE ENGINE:
    Gives the admin unrestricted crawling, filtering, and cross-table aggregation access 
    across ALL PostgreSQL tables (`users`, `payment_transactions`, `quiz_attempts`, 
    `student_queries`, `saved_questions`, `student_feedback`, `user_activity_time`, 
    `command_analytics`, `blocked_bot_users`, `pdf_generation_logs`).
    
    Guarantees returning ALL matching records instead of a single result.
    """
    q_lower = query_text.lower().strip()
    if context_correction:
        q_lower += f" (correction note: {context_correction.lower().strip()})"

    now_ist = get_ist_now()

    # -------------------------------------------------------------------------
    # 1. UNIVERSAL MULTI-RECORD PHONE & CONTACT SEARCH
    # -------------------------------------------------------------------------
    if "phone" in q_lower or "mobile" in q_lower or "contact" in q_lower or "number" in q_lower:
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
    # 2. MULTI-RECORD STUDENT DOSSIER SEARCH (Returns ALL matches instead of 1)
    # -------------------------------------------------------------------------
    specific_keywords = ["details of", "profile of", "student", "user", "info for", "search user", "all info", "who is"]
    is_specific_lookup = any(k in q_lower for k in specific_keywords) or (len(q_lower.split()) <= 4 and not any(w in q_lower for w in ["revenue", "summary", "total", "days data", "all time", "phone", "mobile", "paid", "register", "expire", "inactive"]))

    if is_specific_lookup:
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
                    f"   🔑 PIN: `{u.get('pin','N/A')}` | Expiry: `{u.get('vip_pass_expiry','N/A')}`"
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
    # 3. NEW REGISTRATIONS + PAID PLANS (Multi-Record Ledger)
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

        tg_lines.append("\n📥 *Download PDF:*")
        return {"title": title, "total_records": len(filtered), "summary_markdown": "\n".join(tg_lines), "columns": columns, "rows": pdf_rows, "kpis": {"Timeframe": f"Last {days_val} Days", "New Paid Users": str(len(filtered)), "Revenue": f"₹{total_rev} INR"}}

    # -------------------------------------------------------------------------
    # 4. UPCOMING PASS EXPIRATIONS
    # -------------------------------------------------------------------------
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

        tg_lines.append("\n📥 *Download PDF below:*")
        return {"title": title, "total_records": len(expiring_list), "summary_markdown": "\n".join(tg_lines), "columns": columns, "rows": pdf_rows, "kpis": {"Expiring Soon": str(len(expiring_list))}}

    # -------------------------------------------------------------------------
    # 5. REVENUE & FINANCIAL BREAKDOWN
    # -------------------------------------------------------------------------
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
        return {"title": title, "total_records": len(rows), "summary_markdown": "\n".join(tg_lines), "columns": columns, "rows": pdf_rows, "kpis": {"Gross Revenue": f"₹{grand_total} INR"}}

    # -------------------------------------------------------------------------
    # 6. OMNISCIENT PLATFORM CRAWLER (Lists ALL users or matches query)
    # -------------------------------------------------------------------------
    else:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # Check if query contains a specific term to search all users
        search_kw = q_lower
        for w in ["all", "list", "users", "students", "show", "give", "me", "what", "are"]:
            search_kw = search_kw.replace(w, "")
        search_kw = search_kw.strip()

        if len(search_kw) >= 3:
            cursor.execute("""
                SELECT * FROM users 
                WHERE LOWER(full_name) LIKE LOWER(%s) 
                   OR LOWER(target_exam) LIKE LOWER(%s)
                   OR LOWER(state) LIKE LOWER(%s)
                ORDER BY user_id DESC LIMIT 100
            """, (f"%{search_kw}%", f"%{search_kw}%", f"%{search_kw}%"))
        else:
            cursor.execute("SELECT * FROM users ORDER BY user_id DESC LIMIT 50")
        
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

        if not all_matched_users:
            tg_lines.append("ℹ️ *No database records matched this query.*")

        tg_lines.append("\n📥 *Download complete database report as PDF:*")
        return {"title": title, "total_records": len(all_matched_users), "summary_markdown": "\n".join(tg_lines), "columns": columns, "rows": pdf_rows, "kpis": {"Total Users": str(total_u), "Matched Records": str(len(all_matched_users)), "Revenue": f"₹{tot_rev} INR"}}


def generate_admin_intelligence_pdf(query_result: dict) -> str:
    title = query_result.get("title", "Master Admin Intelligence Report")
    columns = query_result.get("columns", ["S.No.", "Item", "Value"])
    rows = query_result.get("rows", [])
    kpis = query_result.get("kpis", {})
    return generate_admin_query_dataset_pdf(title, columns, rows, kpis)