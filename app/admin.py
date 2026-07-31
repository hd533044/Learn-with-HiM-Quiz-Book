import time
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from app.config import PRIMARY_ADMIN_ID
from app.database import get_all_users, set_maintenance_until, get_maintenance_until, get_user_profile
from app.stats import get_user_performance_summary, get_user_badges, calculate_user_rank

logger = logging.getLogger(__name__)

async def is_primary_admin(user_id: int) -> bool:
    return user_id == PRIMARY_ADMIN_ID

async def admin_portal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != PRIMARY_ADMIN_ID:
        return

    users = get_all_users()
    m_until = get_maintenance_until()
    now_ts = int(time.time())
    m_status = "🟢 Active (Online)" if now_ts >= m_until else f"🔴 PAUSED (Maintenance Mode)"

    keyboard = [
        [InlineKeyboardButton("⏸ Pause Bot 5 Mins", callback_data="admin_pause_5"), InlineKeyboardButton("⏸ Pause Bot 10 Mins", callback_data="admin_pause_10")],
        [InlineKeyboardButton("▶️ Resume Bot Now", callback_data="admin_resume_now")],
        [InlineKeyboardButton("👥 View All Users (Full Details & PDF Books)", callback_data="admin_view_users")],
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
        await show_all_users_admin(update, context)

    elif data == "admin_broadcast":
        context.user_data["awaiting_broadcast"] = True
        await query.edit_message_text("📢 Send the message text you wish to broadcast to all registered users:")

async def show_all_users_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = get_all_users()
    if not users:
        await update.callback_query.edit_message_text("👥 No registered users found in database.")
        return

    total_count = len(users)
    header = f"👥 **REGISTERED STUDENT DIRECTORY ({total_count} Total Users)**\n⚡ Powered by @LearnwithHiM\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    chunks = []
    current_chunk = header
    
    for idx, u in enumerate(users, start=1):
        perf = get_user_performance_summary(u['user_id'])
        avg_score = round(perf.get('avg_score', 0.0) or 0.0, 2)
        badges = get_user_badges(u['user_id'])
        badge_short = badges[0] if badges else "Active Student"

        entry = (
            f"**{idx}. {u['full_name']}** (@{u['username'] or 'N/A'})\n"
            f"• **Telegram ID:** `{u['user_id']}` | **Phone:** `{u['phone_number'] or 'N/A'}`\n"
            f"• **Target Exam:** `{u['target_exam']}` | **Gender/Age:** `{u['gender']}`/`{u['age']}`\n"
            f"• **Location:** `{u.get('state', 'N/A')}, {profile_country(u)}`\n"
            f"• **Badge:** `{badge_short}` | **Avg Score:** `{avg_score}`\n"
            f"• **Joined:** `{u.get('created_at', 'N/A')}`\n\n"
        )
        
        if len(current_chunk) + len(entry) > 3800:
            chunks.append(current_chunk)
            current_chunk = entry
        else:
            current_chunk += entry

    if current_chunk:
        chunks.append(current_chunk)

    for i, chunk in enumerate(chunks):
        if i == 0 and update.callback_query:
            await update.callback_query.edit_message_text(chunk, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=chunk, parse_mode="Markdown")

def profile_country(u):
    return u.get('country', 'India')