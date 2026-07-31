import time
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from app.config import PRIMARY_ADMIN_ID
from app.database import get_all_users, set_maintenance_until, get_maintenance_until
from app.stats import get_user_performance_summary

logger = logging.getLogger(__name__)

async def admin_portal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != PRIMARY_ADMIN_ID:
        return

    users = get_all_users()
    m_until = get_maintenance_until()
    now_ts = int(time.time())
    m_status = "🟢 Active (Online)" if now_ts >= m_until else f"🔴 PAUSED (Maintenance Mode)"

    keyboard = [
        [InlineKeyboardButton("⏸ Pause Bot 5 Mins", callback_data="admin_pause_5"), InlineKeyboardButton("⏸ Pause Bot 10 Mins", callback_data="admin_pause_10")],
        [InlineKeyboardButton("▶️ Resume Bot Now", callback_data="admin_resume_now")],
        [InlineKeyboardButton("👥 View User Details (IDs & Passwords)", callback_data="admin_view_users")],
        [InlineKeyboardButton("📢 Global Broadcast", callback_data="admin_broadcast")]
    ]

    await update.message.reply_text(
        f"👑 **MASTER ADMIN PORTAL — Himanshu Sir**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 **Total Registered Students:** `{len(users)}`\n"
        f"⚡ **Bot System Status:** `{m_status}`\n\n"
        f"Select an admin action below:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if user_id != PRIMARY_ADMIN_ID:
        return

    users = get_all_users()

    if data == "admin_pause_5":
        set_maintenance_until(int(time.time()) + 300)
        await query.edit_message_text("🛑 **Bot Service PAUSED for 5 Minutes.**\nBroadcasting service timer notice to all users...")
        for u in users:
            try:
                await context.bot.send_message(
                    chat_id=u['user_id'], 
                    text="📢 **ADMIN HAS PAUSED THE SERVICE FOR 5 MINS**\n\n⏰ Services will automatically resume in 5 minutes."
                )
            except Exception:
                pass

    elif data == "admin_pause_10":
        set_maintenance_until(int(time.time()) + 600)
        await query.edit_message_text("🛑 **Bot Service PAUSED for 10 Minutes.**\nBroadcasting service timer notice to all users...")
        for u in users:
            try:
                await context.bot.send_message(
                    chat_id=u['user_id'], 
                    text="📢 **ADMIN HAS PAUSED THE SERVICE FOR 10 MINS**\n\n⏰ Services will automatically resume in 10 minutes."
                )
            except Exception:
                pass

    elif data == "admin_resume_now":
        set_maintenance_until(0)
        await query.edit_message_text("🟢 **Bot Service RESUMED Immediately.**\nBroadcasting notice to all users...")
        for u in users:
            try:
                await context.bot.send_message(
                    chat_id=u['user_id'], 
                    text="📢 **ADMIN HAS RESUMED THE SERVICES, NOW YOU CAN ATTEMPT !!**"
                )
            except Exception:
                pass

    elif data == "admin_view_users":
        if not users:
            await query.edit_message_text("📁 No registered user files found.")
            return

        report_lines = ["📋 **VIEW USER DETAILS — MASTER DATABASE & CREDENTIAL REPORT**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"]
        for idx, u in enumerate(users[:15], start=1):
            perf = get_user_performance_summary(u['user_id'])
            avg_score = round(perf.get('avg_score', 0.0) or 0.0, 2)
            
            student_card = (
                f"👤 **{idx}. {u['full_name']}** (@{u['username'] or 'N/A'})\n"
                f" • **Student ID:** `{u.get('student_id', 'N/A')}`\n"
                f" • **Login Password:** `{u.get('login_pass', 'N/A')}`\n"
                f" • **Telegram ID:** `{u['user_id']}`\n"
                f" • **Phone:** `{u['phone_number'] or 'Not Provided'}`\n"
                f" • **Target Exam:** `{u['target_exam']}`\n"
                f" • **Location:** `{u.get('state', 'N/A')}, {u.get('country', 'India')}`\n"
                f" • **Avg Score:** `{avg_score}` | **Mocks:** `{perf.get('total_tests', 0)}`"
            )
            report_lines.append(student_card)

        await query.edit_message_text("\n\n".join(report_lines), parse_mode="Markdown")

    elif data == "admin_broadcast":
        context.user_data["awaiting_broadcast"] = True
        await query.edit_message_text("📢 Send the message text you wish to broadcast to all registered users:")