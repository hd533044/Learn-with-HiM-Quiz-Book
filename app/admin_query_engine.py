import os
import re
import json
import logging
from datetime import datetime, timedelta
import pytz
import xml.sax.saxutils as saxutils
import unicodedata
from psycopg2.extras import RealDictCursor

from app.config import USER_PROFILES_DIR, BASE_DIR, PLAN_TIERS
from app.database import get_db, release_db

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

HAS_WEASYPRINT = False
try:
    import weasyprint
    HAS_WEASYPRINT = True
except ImportError:
    HAS_WEASYPRINT = False


def clean_html_val(text) -> str:
    """Sanitizes text for safe HTML & PDF embedding."""
    if text is None:
        return "N/A"
    val_str = str(text).strip()
    if not val_str:
        return "N/A"
    normalized_str = unicodedata.normalize('NFC', val_str)
    cleaned_spacing = " ".join(normalized_str.split())
    return saxutils.escape(cleaned_spacing)


def parse_and_execute_admin_query(query_text: str) -> dict:
    """
    Parses natural language requests from Himanshu Sir and executes safe,
    real-time multi-table SQL queries to provide accurate platform intelligence.
    """
    clean_q = query_text.lower().strip()
    now_ist = datetime.now(IST)
    today_date_str = now_ist.strftime("%Y-%m-%d")
    
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # -------------------------------------------------------------
        # 1. NEW REGISTRATIONS WHO PURCHASED PAID PLANS (e.g. 3 Days / Custom Days)
        # -------------------------------------------------------------
        days_match = re.search(r'(\d+)\s*(?:day|days)', clean_q)
        days_count = int(days_match.group(1)) if days_match else 3

        if ("register" in clean_q or "new" in clean_q) and ("paid" in clean_q or "bought" in clean_q or "plan" in clean_q or "subscrib" in clean_q):
            cutoff_dt = now_ist - timedelta(days=days_count)
            cutoff_date_str = cutoff_dt.strftime("%Y-%m-%d")

            cursor.execute("""
                SELECT 
                    u.user_id,
                    u.student_id,
                    u.full_name,
                    u.phone_number,
                    u.target_exam,
                    u.state,
                    u.created_at as registered_at,
                    pt.payment_id,
                    pt.plan_name,
                    pt.amount_paid,
                    pt.created_at as paid_at,
                    u.paid_question_balance,
                    u.vip_pass_expiry
                FROM users u
                INNER JOIN payment_transactions pt ON u.user_id = pt.user_id
                WHERE pt.plan_key != 'FREE_DEMO' 
                  AND pt.amount_paid > 0
                  AND u.created_at >= %s
                ORDER BY pt.id DESC;
            """, (cutoff_date_str,))
            rows = cursor.fetchall()
            row_list = [dict(r) for r in rows] if rows else []

            total_rev = sum([float(r.get("amount_paid", 0)) for r in row_list])
            unique_students = len(set([r["user_id"] for r in row_list]))

            summary_lines = [
                f"🧠 **QUERY RESULTS: NEW REGISTRATIONS WITH PAID PLANS**",
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"⏱ **Timeframe:** Last `{days_count} Days` (Since `{cutoff_date_str}`)",
                f"👥 **New Paid Students Found:** `{unique_students}` Students",
                f"🧾 **Total Transactions:** `{len(row_list)}`",
                f"💰 **Revenue Generated:** `₹{total_rev} INR`\n",
                f"📋 **STUDENT DATA BREAKDOWN:**"
            ]

            if row_list:
                for idx, r in enumerate(row_list[:12], start=1):
                    summary_lines.append(
                        f"**{idx}. {r['full_name']}** (`{r['student_id']}`)\n"
                        f"   📦 Plan: `{r['plan_name']}` (₹{r['amount_paid']}) | 🎯 `{r['target_exam']}`\n"
                        f"   📅 Reg: `{r['registered_at']}` | Txn: `{r['payment_id']}`\n"
                        f"   ⏳ Expiry: `{r['vip_pass_expiry'] or 'N/A'}`\n"
                    )
                if len(row_list) > 12:
                    summary_lines.append(f"*(+ {len(row_list) - 12} more students included in the official PDF report)*")
            else:
                summary_lines.append("ℹ️ *No new users registered and purchased paid plans in the selected timeframe.*")

            return {
                "title": f"New Registrations with Paid Plans ({days_count} Days)",
                "summary_markdown": "\n".join(summary_lines),
                "columns": ["S.No.", "Student Name", "Student ID", "Target Exam", "Plan Name", "Amount Paid", "Registered At", "Txn ID"],
                "rows": [
                    [
                        str(i),
                        r["full_name"],
                        r["student_id"] or f"USER_{r['user_id']}",
                        r["target_exam"] or "N/A",
                        r["plan_name"] or "VIP Plan",
                        f"₹{r['amount_paid']}",
                        str(r["registered_at"]).split(" ")[0],
                        r["payment_id"]
                    ] for i, r in enumerate(row_list, start=1)
                ],
                "total_records": len(row_list),
                "total_revenue": total_rev
            }

        # -------------------------------------------------------------
        # 2. UPCOMING / EXPIRING SUBSCRIPTION PASSES
        # -------------------------------------------------------------
        elif "expir" in clean_q or "upcoming" in clean_q or "renewal" in clean_q:
            cursor.execute("""
                SELECT user_id, student_id, full_name, phone_number, target_exam, paid_question_balance, vip_pass_expiry
                FROM users 
                WHERE vip_pass_expiry IS NOT NULL 
                  AND is_banned = 0
                ORDER BY vip_pass_expiry ASC;
            """)
            all_exp = cursor.fetchall()
            upcoming_list = []
            
            for u in all_exp:
                exp_str = u.get("vip_pass_expiry")
                if not exp_str:
                    continue
                try:
                    exp_dt = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S IST")
                    exp_dt = IST.localize(exp_dt) if exp_dt.tzinfo is None else exp_dt
                    diff = (exp_dt - now_ist).total_seconds()
                    if 0 < diff <= (days_count * 86400):
                        hours_left = round(diff / 3600.0, 1)
                        u_dict = dict(u)
                        u_dict["hours_left"] = hours_left
                        upcoming_list.append(u_dict)
                except Exception:
                    pass

            summary_lines = [
                f"🧠 **QUERY RESULTS: UPCOMING VIP PASS EXPIRATIONS**",
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"⏳ **Horizon Window:** Next `{days_count} Days`",
                f"👥 **Students Expiring Soon:** `{len(upcoming_list)}` Students\n",
                f"📋 **EXPIRING STUDENT LEDGER:**"
            ]

            if upcoming_list:
                for idx, r in enumerate(upcoming_list[:12], start=1):
                    summary_lines.append(
                        f"**{idx}. {r['full_name']}** (`{r['student_id']}`)\n"
                        f"   ⏳ Expiry: `{r['vip_pass_expiry']}` ({r['hours_left']} hrs left)\n"
                        f"   ⚡ Active Limit: `{r['paid_question_balance']} Qs/D` | 🎯 `{r['target_exam']}`\n"
                    )
                if len(upcoming_list) > 12:
                    summary_lines.append(f"*(+ {len(upcoming_list) - 12} more students in PDF export)*")
            else:
                summary_lines.append("🎉 *No active VIP passes are scheduled to expire in this window.*")

            return {
                "title": f"Upcoming VIP Subscriptions Expiring (Next {days_count} Days)",
                "summary_markdown": "\n".join(summary_lines),
                "columns": ["S.No.", "Student Name", "Student ID", "Phone", "Target Exam", "Quota / Day", "Expiry Date (IST)", "Hours Left"],
                "rows": [
                    [
                        str(i),
                        r["full_name"],
                        r["student_id"] or f"USER_{r['user_id']}",
                        r.get("phone_number") or "N/A",
                        r["target_exam"] or "N/A",
                        f"{r['paid_question_balance']} Qs",
                        r["vip_pass_expiry"],
                        f"{r['hours_left']} hrs"
                    ] for i, r in enumerate(upcoming_list, start=1)
                ],
                "total_records": len(upcoming_list)
            }

        # -------------------------------------------------------------
        # 3. REVENUE & FINANCIAL BREAKDOWN BY PLAN TIERS
        # -------------------------------------------------------------
        elif "revenue" in clean_q or "earning" in clean_q or "collection" in clean_q or "payment" in clean_q:
            cursor.execute("""
                SELECT 
                    plan_key,
                    plan_name,
                    COUNT(*) as purchase_count,
                    COALESCE(SUM(amount_paid), 0) as total_collected,
                    COALESCE(AVG(amount_paid), 0) as avg_price
                FROM payment_transactions
                WHERE plan_key != 'FREE_DEMO'
                GROUP BY plan_key, plan_name
                ORDER BY total_collected DESC;
            """)
            rev_rows = cursor.fetchall()
            rev_list = [dict(r) for r in rev_rows] if rev_rows else []
            gross_total = sum([float(r["total_collected"]) for r in rev_list])
            total_purchases = sum([int(r["purchase_count"]) for r in rev_list])

            summary_lines = [
                f"🧠 **QUERY RESULTS: PLAN-WISE REVENUE BREAKDOWN**",
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"💰 **Gross Platform Revenue:** `₹{gross_total} INR`",
                f"🧾 **Total Subscriptions Sold:** `{total_purchases}` Purchases\n",
                f"📊 **PLAN TIER ANALYTICS:**"
            ]

            for idx, r in enumerate(rev_list, start=1):
                summary_lines.append(
                    f"**{idx}. {r['plan_name']}** (`{r['plan_key']}`)\n"
                    f"   • Total Revenue: `₹{r['total_collected']} INR`\n"
                    f"   • Units Sold: `{r['purchase_count']}` | Avg Price: `₹{round(float(r['avg_price']), 2)}`\n"
                )

            return {
                "title": "Platform Financial Revenue & Plan Tier Analysis",
                "summary_markdown": "\n".join(summary_lines),
                "columns": ["S.No.", "Plan Name", "Plan Code", "Purchases Count", "Total Revenue (INR)", "Avg Unit Price"],
                "rows": [
                    [
                        str(i),
                        r["plan_name"],
                        r["plan_key"],
                        str(r["purchase_count"]),
                        f"₹{r['total_collected']}",
                        f"₹{round(float(r['avg_price']), 2)}"
                    ] for i, r in enumerate(rev_list, start=1)
                ],
                "total_records": len(rev_list),
                "total_revenue": gross_total
            }

        # -------------------------------------------------------------
        # 4. TARGET EXAM POPULATION & ACTIVE RATIO
        # -------------------------------------------------------------
        elif "exam" in clean_q or "target" in clean_q or "category" in clean_q:
            cursor.execute("""
                SELECT 
                    COALESCE(target_exam, 'Unspecified') as exam_name,
                    COUNT(*) as total_students,
                    SUM(CASE WHEN paid_question_balance > 20 THEN 1 ELSE 0 END) as paid_students,
                    SUM(CASE WHEN is_banned = 1 THEN 1 ELSE 0 END) as banned_students
                FROM users
                GROUP BY target_exam
                ORDER BY total_students DESC;
            """)
            exam_rows = cursor.fetchall()
            exam_list = [dict(r) for r in exam_rows] if exam_rows else []

            summary_lines = [
                f"🧠 **QUERY RESULTS: TARGET EXAM DISTRIBUTION**",
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            ]

            for idx, r in enumerate(exam_list, start=1):
                pct_paid = round((r['paid_students'] / r['total_students']) * 100, 1) if r['total_students'] > 0 else 0.0
                summary_lines.append(
                    f"**{idx}. {r['exam_name']}**\n"
                    f"   • Total Students: `{r['total_students']}`\n"
                    f"   • VIP Paid: `{r['paid_students']}` ({pct_paid}% Conversion)\n"
                )

            return {
                "title": "Target Exam Distribution & Conversion Rates",
                "summary_markdown": "\n".join(summary_lines),
                "columns": ["S.No.", "Target Exam", "Total Aspirants", "Paid VIP Users", "Conversion %"],
                "rows": [
                    [
                        str(i),
                        r["exam_name"],
                        str(r["total_students"]),
                        str(r["paid_students"]),
                        f"{round((r['paid_students'] / r['total_students']) * 100, 1) if r['total_students'] > 0 else 0.0}%"
                    ] for i, r in enumerate(exam_list, start=1)
                ],
                "total_records": len(exam_list)
            }

        # -------------------------------------------------------------
        # 5. INACTIVE STUDENTS (0 Quizzes Attempted)
        # -------------------------------------------------------------
        elif "inactive" in clean_q or "zero" in clean_q or "unactive" in clean_q:
            cursor.execute("""
                SELECT u.user_id, u.student_id, u.full_name, u.phone_number, u.target_exam, u.created_at, u.last_active
                FROM users u
                LEFT JOIN quiz_attempts q ON u.user_id = q.user_id
                WHERE q.id IS NULL AND u.is_banned = 0
                ORDER BY u.created_at DESC;
            """)
            inact_rows = cursor.fetchall()
            inact_list = [dict(r) for r in inact_rows] if inact_rows else []

            summary_lines = [
                f"🧠 **QUERY RESULTS: REGISTERED BUT INACTIVE STUDENTS**",
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"👥 **Total Inactive Students (0 Quizzes):** `{len(inact_list)}`\n",
                f"📋 **STUDENT SAMPLING:**"
            ]

            for idx, r in enumerate(inact_list[:12], start=1):
                summary_lines.append(
                    f"**{idx}. {r['full_name']}** (`{r['student_id']}`)\n"
                    f"   🎯 `{r['target_exam']}` | 📅 Registered: `{r['created_at']}`\n"
                )

            return {
                "title": "Registered Inactive Students (Zero Quizzes)",
                "summary_markdown": "\n".join(summary_lines),
                "columns": ["S.No.", "Student Name", "Student ID", "Phone", "Target Exam", "Registered At", "Last Active"],
                "rows": [
                    [
                        str(i),
                        r["full_name"],
                        r["student_id"] or f"USER_{r['user_id']}",
                        r.get("phone_number") or "N/A",
                        r["target_exam"] or "N/A",
                        r["created_at"],
                        r.get("last_active") or "Never"
                    ] for i, r in enumerate(inact_list, start=1)
                ],
                "total_records": len(inact_list)
            }

        # -------------------------------------------------------------
        # 6. UNIVERSAL FALLBACK: RECENT PLATFORM AUDIT & SUMMARY
        # -------------------------------------------------------------
        else:
            cursor.execute("""
                SELECT 
                    u.user_id, u.student_id, u.full_name, u.target_exam, u.state,
                    u.paid_question_balance, u.vip_pass_expiry, u.created_at
                FROM users u
                ORDER BY u.created_at DESC
                LIMIT 50;
            """)
            gen_rows = cursor.fetchall()
            gen_list = [dict(r) for r in gen_rows] if gen_rows else []

            summary_lines = [
                f"🧠 **QUERY RESULTS: RECENT PLATFORM AUDIT**",
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"🔍 **Matched Context:** `{query_text}`",
                f"👥 **Total Sampled Records:** `{len(gen_list)}`\n",
                f"📋 **RECENT SCHOLARS:**"
            ]

            for idx, r in enumerate(gen_list[:10], start=1):
                p_status = "💳 VIP" if r.get("paid_question_balance", 0) > 20 else "🎁 Free"
                summary_lines.append(f"**{idx}. {r['full_name']}** (`{r['student_id']}`) — `{p_status}` | 🎯 `{r['target_exam']}`")

            return {
                "title": f"Platform Scholars Audit — {query_text[:30]}",
                "summary_markdown": "\n".join(summary_lines),
                "columns": ["S.No.", "Student Name", "Student ID", "Target Exam", "Location", "Quota / Day", "Registered At"],
                "rows": [
                    [
                        str(i),
                        r["full_name"],
                        r["student_id"] or f"USER_{r['user_id']}",
                        r["target_exam"] or "N/A",
                        f"{r.get('state', 'N/A')}",
                        f"{r['paid_question_balance']} Qs",
                        r["created_at"]
                    ] for i, r in enumerate(gen_list, start=1)
                ],
                "total_records": len(gen_list)
            }

    except Exception as e:
        logger.error(f"[ADMIN QUERY ENGINE EXCEPTION] {e}")
        return {
            "title": "Error Processing Query",
            "summary_markdown": f"⚠️ **Error Executing Database Query:** `{str(e)}`",
            "columns": ["Status"],
            "rows": [["Error"]],
            "total_records": 0
        }
    finally:
        cursor.close()
        release_db(conn)


def generate_admin_intelligence_pdf(data_dict: dict) -> str:
    """Generates a styled, branded PDF report document for the query results."""
    title = clean_html_val(data_dict.get("title", "Admin Intelligence Report"))
    columns = data_dict.get("columns", [])
    rows = data_dict.get("rows", [])
    total_records = data_dict.get("total_records", len(rows))
    
    now_str = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")
    filename = f"Admin_Report_{int(datetime.now().timestamp())}.pdf"
    pdf_path = os.path.join(USER_PROFILES_DIR, filename)

    logo_left_path = os.path.abspath(os.path.join(BASE_DIR, "assets", "logo.png"))
    logo_right_path = os.path.abspath(os.path.join(BASE_DIR, "assets", "logohim.png"))
    target_link = "https://t.me/learnwithhim"

    left_logo = f'<a href="{target_link}"><img src="file://{logo_left_path}" style="width:55px; height:55px; object-fit:contain; border:none;"/></a>' if os.path.exists(logo_left_path) else '<b>Logo</b>'
    right_logo = f'<a href="{target_link}"><img src="file://{logo_right_path}" style="width:55px; height:55px; object-fit:contain; border:none;"/></a>' if os.path.exists(logo_right_path) else '<b>@LearnwithHiM</b>'

    headers_html = "".join([f"<th style='padding:6px 8px; border:0.5px solid #CBD5E1; background-color:#E0F2FE; color:#0F172A; font-weight:bold; font-size:10px;'>{clean_html_val(col)}</th>" for col in columns])
    
    rows_html_list = []
    for r in rows:
        cells = "".join([f"<td style='padding:5px 7px; border:0.5px solid #CBD5E1; font-size:9.5px; word-break:break-word;'>{clean_html_val(c)}</td>" for c in r])
        rows_html_list.append(f"<tr>{cells}</tr>")
    
    table_body = "".join(rows_html_list)

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset='utf-8'/>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+Devanagari:wght@400;600;700&display=swap');
            @page {{ size: letter landscape; margin: 15mm 12mm 15mm 12mm; @bottom-right {{ content: 'Page ' counter(page); font-size: 8pt; font-family: 'Times New Roman', serif; color: #64748B; }} }}
            body {{ font-family: 'Noto Sans Devanagari', 'Times New Roman', Helvetica, Arial, sans-serif; margin: 0; padding: 0; color: #334155; }}
            .header-table {{ width: 100%; border-collapse: collapse; margin-bottom: 12px; }}
            .title {{ text-align: center; color: #1E3A8A; font-size: 18px; font-weight: bold; font-family: 'Times New Roman', serif; }}
            .sub-title {{ text-align: center; color: #16A34A; font-size: 11px; font-weight: bold; }}
            .meta-box {{ background-color: #F8FAFC; border: 0.5px solid #CBD5E1; padding: 8px 12px; border-radius: 6px; margin-bottom: 12px; font-size: 10.5px; }}
            .data-table {{ width: 100%; border-collapse: collapse; page-break-inside: auto; }}
            .data-table thead {{ display: table-header-group; }}
            .data-table tr {{ page-break-inside: avoid; }}
            .watermark-container {{ position: fixed; top: 0; left: 0; width: 100%; height: 100%; z-index: -1000; overflow: hidden; pointer-events: none; }}
            .wm-text {{ position: absolute; font-family: 'Times New Roman', serif; font-weight: bold; font-size: 24px; color: #94A3B8; opacity: 0.12; transform: rotate(25deg); }}
        </style>
    </head>
    <body>
        <div class='watermark-container'>
            <div class='wm-text' style='top: 15%; left: 20%;'>Learn with HiM — Master Admin Intelligence</div>
            <div class='wm-text' style='top: 45%; left: 40%;'>Learn with HiM — Master Admin Intelligence</div>
            <div class='wm-text' style='top: 75%; left: 20%;'>Learn with HiM — Master Admin Intelligence</div>
        </div>

        <table class='header-table'>
            <tr>
                <td style='width: 12%; text-align: left;'>{left_logo}</td>
                <td>
                    <div class='title'>Learn with HiM Quiz Book — Master Intelligence Portal</div>
                    <div class='sub-title'>Official Administrative Intelligence Data Sheet</div>
                </td>
                <td style='width: 12%; text-align: right;'>{right_logo}</td>
            </tr>
        </table>

        <div class='meta-box'>
            <b>Report Query:</b> {title} &nbsp;|&nbsp; 
            <b>Generated At:</b> {now_str} &nbsp;|&nbsp; 
            <b>Total Records Matched:</b> {total_records}
        </div>

        <table class='data-table'>
            <thead>
                <tr>{headers_html}</tr>
            </thead>
            <tbody>
                {table_body}
            </tbody>
        </table>
    </body>
    </html>
    """

    if HAS_WEASYPRINT:
        try:
            weasyprint.HTML(string=html_content).write_pdf(pdf_path)
            if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 500:
                return pdf_path
        except Exception as e:
            logger.error(f"[WEASYPRINT ADMIN PDF ERROR] {e}")

    return None