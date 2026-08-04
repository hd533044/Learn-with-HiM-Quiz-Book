import time
import json
import logging
import math
import os
import zipfile
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from app.config import PRIMARY_ADMIN_ID, USER_PROFILES_DIR
from app.database import (
    get_all_users, set_maintenance_until, get_maintenance_until, 
    get_user_profile, get_db, sync_user_json_profile, toggle_user_ban_status
)
from app.pdf_generator import generate_student_pdf_report
from app.stats import get_user_performance_summary, calculate_user_rank, calculate_user_percentile

logger = logging.getLogger(__name__)
USERS_PER_PAGE = 8

async def admin_portal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != PRIMARY_ADMIN_ID:
        return

    users = get_all_users()
    m_until = get_maintenance_until()
    now_ts = int(time.time())
    m_status = "🟢 Active (Online)" if now_ts >= m_until else "🔴 PAUSED (Maintenance Mode)"

    keyboard = [
        [InlineKeyboardButton("👥 Student Directory (/user_profiles)", callback_data="admin_users_page_0")],
        [InlineKeyboardButton("🔍 Search Student (ID/Phone/Name)", callback_data="admin_search_prompt")],
        [InlineKeyboardButton("📦 Export All Json Ledgers (.zip)", callback_data="admin_export_zip")],
        [InlineKeyboardButton("⏸ Pause Bot 5 Mins", callback_data="admin_pause_5"), InlineKeyboardButton("⏸ Pause Bot 10 Mins", callback_data="admin_pause_10")],
        [InlineKeyboardButton("▶️ Resume Bot Now", callback_data="admin_resume_now")],
        [InlineKeyboardButton("📢 Global Broadcast", callback_data="admin_broadcast")]
    ]

    msg = (
        f"👑 **MASTER ADMIN PORTAL — Himanshu Sir** 👑\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 **Total Registered Students:** `{len(users)}`\n"
        f"⚡ **Bot System Status:** `{m_status}`\n\n"
        f"Select an administrative action below:"
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    if user_id != PRIMARY_ADMIN_ID:
        return

    users = get_all_users()

    if data == "admin_export_zip":
        await query.answer()
        await query.edit_message_text("⏳ **Generating Bulk Zip Package...**\nZipping all student JSON ledgers...")
        
        for u in users:
            sync_user_json_profile(u['user_id'])

        zip_filename = "All_Student_Profiles_Export.zip"
        zip_path = os.path.join(USER_PROFILES_DIR, zip_filename)

        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, _, files in os.walk(USER_PROFILES_DIR):
                    for file in files:
                        if file.endswith('.json'):
                            file_path = os.path.join(root, file)
                            zipf.write(file_path, arcname=file)

            if os.path.exists(zip_path):
                with open(zip_path, "rb") as doc:
                    await context.bot.send_document(
                        chat_id=query.message.chat_id,
                        document=doc,
                        filename=zip_filename,
                        caption=f"📦 **MASTER STUDENT PROFILES BACKUP**\n\nTotal Files Included: `{len(users)} JSON profiles`"
                    )
                os.remove(zip_path)
            await admin_portal_command(update, context)
        except Exception as e:
            await query.message.reply_text(f"⚠️ Error creating zip archive: {e}")

    elif data == "admin_pause_5":
        await query.answer()
        set_maintenance_until(int(time.time()) + 300)
        await query.edit_message_text("🛑 **Bot Service PAUSED for 5 Minutes.**\nBroadcasting notice to all users...")
        for u in users:
            try:
                await context.bot.send_message(
                    chat_id=u['user_id'], 
                    text="📢 **ADMIN HAS PAUSED SERVICE FOR 5 MINS**\n\n⏰ Services will automatically resume in 5 minutes."
                )
            except Exception:
                pass

    elif data == "admin_pause_10":
        await query.answer()
        set_maintenance_until(int(time.time()) + 600)
        await query.edit_message_text("🛑 **Bot Service PAUSED for 10 Minutes.**\nBroadcasting notice to all users...")
        for u in users:
            try:
                await context.bot.send_message(
                    chat_id=u['user_id'], 
                    text="📢 **ADMIN HAS PAUSED SERVICE FOR 10 MINS**\n\n⏰ Services will automatically resume in 10 minutes."
                )
            except Exception:
                pass

    elif data == "admin_resume_now":
        await query.answer()
        set_maintenance_until(0)
        await query.edit_message_text("🟢 **Bot Service RESUMED Immediately.**\nBroadcasting notice to all users...")
        for u in users:
            try:
                await context.bot.send_message(
                    chat_id=u['user_id'], 
                    text="📢 **ADMIN HAS RESUMED SERVICES! YOU CAN ATTEMPT QUIZZES NOW!**"
                )
            except Exception:
                pass

    elif data == "admin_search_prompt":
        await query.answer()
        context.user_data["awaiting_admin_search"] = True
        await query.edit_message_text("🔍 **STUDENT SEARCH ENGINE**\n\nPlease reply with the student's **Student ID**, **Phone Number**, or **Full Name**:")

    elif data.startswith("genpdf_"):
        await query.answer()
        raw = data.replace("genpdf_", "")
        parts = raw.split("_")
        target_uid = int(parts[0])
        filter_mode = "_".join(parts[1:])

        await query.edit_message_text("⏳ **Generating Custom PDF Report Card...**\nBuilding stats, formatting tables, and rendering PDF...")
        
        pdf_file = generate_student_pdf_report(target_uid, filter_mode)
        u = get_user_profile(target_uid)
        sid = u.get("student_id") or f"USER_{target_uid}" if u else f"USER_{target_uid}"
        student_name = u.get("full_name", "Student") if u else "Student"

        if pdf_file == "NO_ATTEMPTS":
            nav_buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("📄 Export Another PDF Report", callback_data=f"audit_pdfmenu_{target_uid}")],
                [InlineKeyboardButton("🔙 Back to Student Dashboard", callback_data=f"admin_inspect_u_{target_uid}")]
            ])
            await query.edit_message_text(
                f"ℹ️ **NO QUIZ ATTEMPTS FOUND!**\n\n"
                f"Student **{student_name}** (`{sid}`) has not attempted any quizzes in the selected timeframe.",
                reply_markup=nav_buttons,
                parse_mode="Markdown"
            )
        elif pdf_file and pdf_file.startswith("ERROR_DETAILS:"):
            nav_buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back to Student Dashboard", callback_data=f"admin_inspect_u_{target_uid}")]
            ])
            err_text = str(pdf_file[:3500])
            await query.edit_message_text(
                f"⚠️ **PDF Generation Error:**\n\n`{err_text}`",
                reply_markup=nav_buttons,
                parse_mode="Markdown"
            )
        elif pdf_file and os.path.exists(pdf_file):
            with open(pdf_file, "rb") as doc:
                await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=doc,
                    filename=os.path.basename(pdf_file),
                    caption=(
                        f"📄 **OFFICIAL STUDENT PDF ACADEMIC REPORT**\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"👤 **Student:** {student_name}\n"
                        f"🪪 **Student ID:** `{sid}`\n"
                        f"📊 **Report Module:** `{filter_mode.replace('_', ' ').title()}`\n"
                        f"🏷 **Watermark:** `@LearnwithHiM`"
                    )
                )
            
            nav_buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("📄 Export Another PDF Report", callback_data=f"audit_pdfmenu_{target_uid}")],
                [InlineKeyboardButton("🔙 Back to Student Dashboard", callback_data=f"admin_inspect_u_{target_uid}")],
                [InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]
            ])
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="👇 **Quick Actions & Navigation:**",
                reply_markup=nav_buttons
            )
        else:
            nav_buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back to Student Dashboard", callback_data=f"admin_inspect_u_{target_uid}")]
            ])
            await query.edit_message_text(
                "⚠️ **Failed to generate PDF file.**",
                reply_markup=nav_buttons
            )

    elif data.startswith("admin_users_page_"):
        await query.answer()
        page = int(data.replace("admin_users_page_", ""))
        total_users = len(users)

        if total_users == 0:
            await query.edit_message_text("📁 No registered students found in database.")
            return

        total_pages = math.ceil(total_users / USERS_PER_PAGE)
        page = max(0, min(page, total_pages - 1))

        start_idx = page * USERS_PER_PAGE
        end_idx = start_idx + USERS_PER_PAGE
        page_users = users[start_idx:end_idx]

        keyboard = []
        for u in page_users:
            sid = u.get("student_id") or f"USER_{u['user_id']}"
            ban_flag = " 🛑" if u.get("is_banned") else ""
            btn_text = f"👤 {u['full_name']}{ban_flag} (ID: {sid})"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"admin_inspect_u_{u['user_id']}")])

        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"admin_users_page_{page - 1}"))
        nav_row.append(InlineKeyboardButton(f"📄 Page {page + 1}/{total_pages}", callback_data="ignore"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"admin_users_page_{page + 1}"))
        
        keyboard.append(nav_row)
        keyboard.append([InlineKeyboardButton("🔙 Back to Admin Portal", callback_data="admin_home")])

        msg = (
            f"👥 **STUDENT DIRECTORY LEDGER**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **Total Students:** `{total_users}`\n"
            f"• **Page:** `{page + 1}` of `{total_pages}`\n\n"
            f"Tap any student below to access their full inspection dashboard:"
        )
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "admin_home":
        await query.answer()
        await admin_portal_command(update, context)

    elif data.startswith("admin_inspect_u_"):
        await query.answer()
        target_uid = int(data.replace("admin_inspect_u_", ""))
        u = get_user_profile(target_uid)

        if not u:
            await query.edit_message_text("⚠️ Student profile not found.")
            return

        sid = u.get("student_id") or f"USER_{u.get('user_id')}"
        is_banned = u.get("is_banned", 0)
        ban_text = "🟢 ACTIVE" if not is_banned else "🔴 BANNED"

        keyboard = [
            [InlineKeyboardButton("📋 Personal Details", callback_data=f"audit_personal_{target_uid}"), InlineKeyboardButton("🔑 PIN & Security Questions", callback_data=f"audit_pinsec_{target_uid}")],
            [InlineKeyboardButton("⏱ Time & Activity Log", callback_data=f"audit_activity_{target_uid}"), InlineKeyboardButton("📊 Overall Performance", callback_data=f"audit_perf_{target_uid}")],
            [InlineKeyboardButton("📅 Date-wise Quiz Summary", callback_data=f"audit_datesummary_{target_uid}"), InlineKeyboardButton("🎯 Attempted Questions", callback_data=f"audit_attempted_{target_uid}")],
            [InlineKeyboardButton("❌ Wrong Questions Log", callback_data=f"audit_wrong_{target_uid}"), InlineKeyboardButton("💾 Saved Questions", callback_data=f"audit_saved_{target_uid}")],
            [InlineKeyboardButton("💬 Student Feedback", callback_data=f"audit_feedback_{target_uid}"), InlineKeyboardButton("🎁 Grant +20 Quota", callback_data=f"audit_grant_{target_uid}")],
            [InlineKeyboardButton("📄 Export PDF Options", callback_data=f"audit_pdfmenu_{target_uid}"), InlineKeyboardButton("📥 Export Raw JSON File", callback_data=f"audit_exportjson_{target_uid}")],
            [InlineKeyboardButton("🔙 Back to Student Directory", callback_data="admin_users_page_0")]
        ]

        msg = (
            f"🪪 **STUDENT AUDIT CONTROL PANEL** 🪪\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **Student Name:** {u.get('full_name')}\n"
            f"• **Student ID:** `{sid}`\n"
            f"• **Telegram ID:** `{u.get('user_id')}`\n"
            f"• **Target Exam:** `{u.get('target_exam')}`\n"
            f"• **Account Status:** `{ban_text}`\n"
            f"• **File Ledger:** `data/user_profiles/{sid}.json`\n\n"
            f"Select an audit module below to view detailed reports:"
        )
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("audit_pinsec_"):
        await query.answer()
        target_uid = int(data.replace("audit_pinsec_", ""))
        u = get_user_profile(target_uid)

        msg = (
            f"🔑 **USER PIN & SECURITY QUESTIONS AUDIT** 🔑\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 **Student:** {u.get('full_name')} (`{u.get('student_id')}`)\n\n"
            f"• **Secret 4-Digit PIN:** `{u.get('pin', 'Not Set')}`\n"
            f"• **Security Question:** *\"{u.get('security_question', 'Not Set')}\"*\n"
            f"• **Security Answer:** `{u.get('security_answer', 'Not Set')}`\n\n"
            f"⚠️ *Confidential: Visible strictly to Primary Admin.*"
        )
        keyboard = [[InlineKeyboardButton("🔙 Back to Dashboard", callback_data=f"admin_inspect_u_{target_uid}")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("audit_pdfmenu_"):
        await query.answer()
        target_uid = int(data.replace("audit_pdfmenu_", ""))
        
        pdf_buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 1. Last 1 Month Full Data Report", callback_data=f"genpdf_{target_uid}_last_1_month_data")],
            [InlineKeyboardButton("📊 2. Last 1 Month Quiz Summary Report (No Qs)", callback_data=f"genpdf_{target_uid}_last_1_month_quiz")],
            [InlineKeyboardButton("📜 3. All Months Full Data Report", callback_data=f"genpdf_{target_uid}_all_months_data")],
            [InlineKeyboardButton("📈 4. All Months Quiz Summary Report (No Qs)", callback_data=f"genpdf_{target_uid}_all_months_quiz")],
            [InlineKeyboardButton("🔙 Back to Dashboard", callback_data=f"admin_inspect_u_{target_uid}")]
        ])

        await query.edit_message_text(
            f"📄 **PDF REPORT CARD GENERATOR** 📄\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Select the exact report type to generate:",
            reply_markup=pdf_buttons,
            parse_mode="Markdown"
        )

    elif data.startswith("audit_personal_"):
        await query.answer()
        target_uid = int(data.replace("audit_personal_", ""))
        u = get_user_profile(target_uid)
        
        if not u:
            await query.edit_message_text("⚠️ Error retrieving user profile.")
            return

        sid = u.get("student_id") or f"USER_{u.get('user_id')}"
        ban_status = "BANNED 🔴" if u.get("is_banned") else "ACTIVE 🟢"
        
        edit_cnt = u.get("edit_count", 0)
        last_edit = u.get("last_profile_edit", "Never")
        remaining_edits = max(0, 3 - edit_cnt)

        msg = (
            f"📋 **STUDENT PERSONAL DETAILS** 📋\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **Full Name:** {u.get('full_name', 'N/A')}\n"
            f"• **Student ID:** `{sid}`\n"
            f"• **Telegram ID:** `{u.get('user_id')}`\n"
            f"• **Account Status:** `{ban_status}`\n"
            f"• **Username:** @{u.get('username') or 'N/A'}\n"
            f"• **Phone Number:** `{u.get('phone_number') or 'N/A'}`\n"
            f"• **Target Exam:** `{u.get('target_exam', 'N/A')}`\n"
            f"• **Date of Birth:** `{u.get('dob', 'N/A')}`\n"
            f"• **Calculated Age:** `{u.get('age', 'N/A')} yrs`\n"
            f"• **Gender:** `{u.get('gender', 'N/A')}`\n"
            f"• **Location:** `{u.get('state', 'N/A')}, {u.get('country', 'India')}`\n"
            f"• **Profile Edits Made:** `{edit_cnt} / 3 times` *(Last: {last_edit})*\n"
            f"• **Remaining Edits:** `{remaining_edits} left`\n"
            f"• **Bonus Quota:** `{u.get('bonus_quota', 0)} Qs`\n"
            f"• **Registered At:** `{u.get('created_at', 'N/A')}`\n"
            f"• **Last Active:** `{u.get('last_active', 'N/A')}`\n"
            f"• **Referred By ID:** `{u.get('referred_by') or 'None'}`\n"
            f"• **Referral Count:** `{u.get('referral_count', 0)}` friends"
        )
        keyboard = [[InlineKeyboardButton("🔙 Back to Dashboard", callback_data=f"admin_inspect_u_{target_uid}")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("audit_activity_"):
        await query.answer()
        target_uid = int(data.replace("audit_activity_", ""))
        u = get_user_profile(target_uid)
        
        conn = get_db()
        rows = conn.execute("SELECT date_str, seconds_spent FROM user_activity_time WHERE user_id = ? ORDER BY date_str DESC", (target_uid,)).fetchall()
        conn.close()

        total_sec = sum([r['seconds_spent'] for r in rows])
        
        lines = [
            f"⏱ **STUDENT ACTIVITY & TIME LOG** ⏱",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"👤 **Student:** {u.get('full_name')} (`{u.get('student_id')}`)",
            f"• **Last Active:** `{u.get('last_active', 'N/A')}`",
            f"• **Total Time Spent:** `{total_sec} sec` ({round(total_sec/60, 2)} mins)\n",
            f"📅 **Date-Wise Time Spent Breakdown:**"
        ]

        if rows:
            for r in rows:
                mins = round(r['seconds_spent'] / 60, 2)
                lines.append(f" • `{r['date_str']}`: {r['seconds_spent']}s ({mins} mins)")
        else:
            lines.append(" • *No activity time recorded yet.*")

        msg = "\n".join(lines)
        keyboard = [[InlineKeyboardButton("🔙 Back to Dashboard", callback_data=f"admin_inspect_u_{target_uid}")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("audit_perf_"):
        await query.answer()
        target_uid = int(data.replace("audit_perf_", ""))
        u = get_user_profile(target_uid)
        perf = get_user_performance_summary(target_uid)
        rank = calculate_user_rank(target_uid)
        percentile = calculate_user_percentile(target_uid)

        total_qs = perf.get('total_qs', 0) or 0
        total_correct = perf.get('total_correct', 0) or 0
        acc = round((total_correct / total_qs) * 100, 2) if total_qs > 0 else 0.0

        msg = (
            f"📊 **STUDENT OVERALL PERFORMANCE** 📊\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 **Student:** {u.get('full_name')} (`{u.get('student_id')}`)\n\n"
            f"• **Tests Completed:** `{perf.get('total_tests', 0)}` 📚\n"
            f"• **Questions Attempted:** `{total_qs}` 🖥\n"
            f"• **Correct Answers:** `{total_correct}` ✅\n"
            f"• **Wrong Answers:** `{perf.get('total_wrong', 0)}` ❌\n"
            f"• **Skipped Questions:** `{perf.get('total_skipped', 0)}` ⏭\n"
            f"• **Accuracy Rating:** `{acc}%`\n"
            f"• **Average Score:** `{round(perf.get('avg_score', 0.0) or 0.0, 2)}`\n"
            f"• **Global Rank:** `{rank}` 🥇\n"
            f"• **Overall Percentile:** `{percentile}%`"
        )
        keyboard = [[InlineKeyboardButton("🔙 Back to Dashboard", callback_data=f"admin_inspect_u_{target_uid}")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("audit_datesummary_"):
        await query.answer()
        target_uid = int(data.replace("audit_datesummary_", ""))
        u = get_user_profile(target_uid)

        conn = get_db()
        attempts = conn.execute("SELECT * FROM quiz_attempts WHERE user_id = ? ORDER BY id DESC", (target_uid,)).fetchall()
        conn.close()

        lines = [
            f"📅 **DATE-WISE QUIZ SUMMARY** 📅",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"👤 **Student:** {u.get('full_name')} (`{u.get('student_id')}`)\n"
        ]

        if attempts:
            summary = {}
            for a in attempts:
                ad = dict(a)
                dt = ad.get("attempt_date", "Unknown")
                if dt not in summary:
                    summary[dt] = {"tests": 0, "qs": 0, "correct": 0, "score": 0.0}
                summary[dt]["tests"] += 1
                summary[dt]["qs"] += ad.get("questions_attempted", 0)
                summary[dt]["correct"] += ad.get("correct_answers", 0)
                summary[dt]["score"] += ad.get("score", 0.0)

            for dt, stats in summary.items():
                lines.append(
                    f"🗓 **Date:** `{dt}`\n"
                    f" • Quizzes: `{stats['tests']}` | Questions: `{stats['qs']}`\n"
                    f" • Correct: `{stats['correct']}` | Score: `{stats['score']}`\n"
                )
        else:
            lines.append("*No quiz attempts found for this student.*")

        msg = "\n".join(lines)
        keyboard = [[InlineKeyboardButton("🔙 Back to Dashboard", callback_data=f"admin_inspect_u_{target_uid}")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("audit_attempted_"):
        await query.answer()
        target_uid = int(data.replace("audit_attempted_", ""))
        u = get_user_profile(target_uid)

        conn = get_db()
        attempts = conn.execute("SELECT * FROM quiz_attempts WHERE user_id = ? ORDER BY id DESC LIMIT 5", (target_uid,)).fetchall()
        conn.close()

        lines = [
            f"🎯 **ATTEMPTED QUESTIONS LOG** 🎯",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"👤 **Student:** {u.get('full_name')} (`{u.get('student_id')}`)\n"
        ]

        found_any = False
        for a in attempts:
            ad = dict(a)
            dt = ad.get("attempt_timestamp", "N/A")
            details = json.loads(ad["details_json"]) if ad.get("details_json") else []
            if details:
                found_any = True
                lines.append(f"📅 **Quiz At:** `{dt}`")
                for idx, q_item in enumerate(details, start=1):
                    q_text = q_item.get("question_text", "N/A")
                    ans_text = q_item.get("correct_answer_text", "N/A")
                    status_icon = "✅" if q_item.get("status") == "CORRECT" else "❌" if q_item.get("status") == "WRONG" else "⏭"
                    lines.append(f" {idx}. {status_icon} `{q_text}`\n    👉 **Ans:** `{ans_text}`")
                lines.append("")

        if not found_any:
            lines.append("*No question attempt logs recorded yet.*")

        msg = "\n".join(lines)
        if len(msg) > 4000:
            msg = msg[:3950] + "\n\n*(Truncated due to Telegram length limit)*"

        keyboard = [[InlineKeyboardButton("🔙 Back to Dashboard", callback_data=f"admin_inspect_u_{target_uid}")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("audit_wrong_"):
        await query.answer()
        target_uid = int(data.replace("audit_wrong_", ""))
        u = get_user_profile(target_uid)

        conn = get_db()
        attempts = conn.execute("SELECT * FROM quiz_attempts WHERE user_id = ? ORDER BY id DESC LIMIT 5", (target_uid,)).fetchall()
        conn.close()

        lines = [
            f"❌ **WRONG QUESTIONS LOG** ❌",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"👤 **Student:** {u.get('full_name')} (`{u.get('student_id')}`)\n"
        ]

        found_wrong = False
        for a in attempts:
            ad = dict(a)
            dt = ad.get("attempt_timestamp", "N/A")
            details = json.loads(ad["details_json"]) if ad.get("details_json") else []
            wrong_items = [q for q in details if q.get("status") == "WRONG"]
            if wrong_items:
                found_wrong = True
                lines.append(f"📅 **Quiz At:** `{dt}`")
                for idx, q_item in enumerate(wrong_items, start=1):
                    q_text = q_item.get("question_text", "N/A")
                    ans_text = q_item.get("correct_answer_text", "N/A")
                    lines.append(f" {idx}. ❌ `{q_text}`\n    👉 **Correct Ans:** `{ans_text}`")
                lines.append("")

        if not found_wrong:
            lines.append("🎉 *Zero wrong questions logged! Excellent performance.*")

        msg = "\n".join(lines)
        if len(msg) > 4000:
            msg = msg[:3950] + "\n\n*(Truncated due to Telegram length limit)*"

        keyboard = [[InlineKeyboardButton("🔙 Back to Dashboard", callback_data=f"admin_inspect_u_{target_uid}")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("audit_saved_"):
        await query.answer()
        target_uid = int(data.replace("audit_saved_", ""))
        u = get_user_profile(target_uid)

        conn = get_db()
        saved = conn.execute("SELECT * FROM saved_questions WHERE user_id = ? ORDER BY id DESC", (target_uid,)).fetchall()
        conn.close()

        lines = [
            f"💾 **SAVED QUESTIONS REPORT** 💾",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"👤 **Student:** {u.get('full_name')} (`{u.get('student_id')}`)",
            f"• **Total Bookmarks:** `{len(saved)}`\n"
        ]

        if saved:
            for idx, sq in enumerate(saved, start=1):
                sq_d = dict(sq)
                opts_list = json.loads(sq_d['options_json']) if sq_d.get('options_json') else []
                c_opt_idx = sq_d.get("correct_option", 0)
                ans_text = opts_list[c_opt_idx] if 0 <= c_opt_idx < len(opts_list) else "N/A"
                s_at = sq_d.get("saved_at", "N/A")
                lines.append(f"**{idx}. [{s_at}]** 📌 `{sq_d['question_text']}`\n    👉 **Correct Ans:** `{ans_text}`")
        else:
            lines.append("*No saved questions bookmarked by this student.*")

        msg = "\n".join(lines)
        if len(msg) > 4000:
            msg = msg[:3950] + "\n\n*(Truncated due to Telegram length limit)*"

        keyboard = [[InlineKeyboardButton("🔙 Back to Dashboard", callback_data=f"admin_inspect_u_{target_uid}")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("audit_feedback_"):
        await query.answer()
        target_uid = int(data.replace("audit_feedback_", ""))
        u = get_user_profile(target_uid)

        conn = get_db()
        fbs = conn.execute("SELECT * FROM student_feedback WHERE user_id = ? ORDER BY id DESC", (target_uid,)).fetchall()
        conn.close()

        lines = [
            f"💬 **STUDENT FEEDBACK & REVIEWS** 💬",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"👤 **Student:** {u.get('full_name')} (`{u.get('student_id')}`)\n"
        ]

        if fbs:
            for idx, f_item in enumerate(fbs, start=1):
                fd = dict(f_item)
                lines.append(f"**{idx}. Submitted At:** `{fd['submitted_at']}`\n 💬 *\"{fd['feedback_text']}\"*\n")
        else:
            lines.append("*No reviews or feedback submitted by this student yet.*")

        msg = "\n".join(lines)
        keyboard = [[InlineKeyboardButton("🔙 Back to Dashboard", callback_data=f"admin_inspect_u_{target_uid}")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("audit_grant_"):
        await query.answer()
        target_uid = int(data.replace("audit_grant_", ""))
        conn = get_db()
        conn.execute("UPDATE users SET bonus_quota = bonus_quota + 20 WHERE user_id = ?", (target_uid,))
        conn.commit()
        conn.close()
        sync_user_json_profile(target_uid)

        await query.edit_message_text(
            f"🎉 **Bonus Quota Granted!** 🎉\n\nAdded +20 daily question quota to user `{target_uid}`.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Dashboard", callback_data=f"admin_inspect_u_{target_uid}")]])
        )

    elif data.startswith("audit_exportjson_"):
        await query.answer()
        target_uid = int(data.replace("audit_exportjson_", ""))
        u = get_user_profile(target_uid)
        sid = u.get("student_id") or f"USER_{u.get('user_id')}"
        
        sync_user_json_profile(target_uid)
        filepath = os.path.join(USER_PROFILES_DIR, f"{sid}.json")

        if os.path.exists(filepath):
            with open(filepath, "rb") as doc:
                await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=doc,
                    filename=f"{sid}.json",
                    caption=f"📄 **Master Student Profile File:** `{sid}.json`"
                )
        else:
            await query.message.reply_text("⚠️ JSON file not found on disk.")

    elif data == "admin_broadcast":
        await query.answer()
        context.user_data["awaiting_broadcast"] = True
        await query.edit_message_text("📢 Send the message text you wish to broadcast to all registered users:")