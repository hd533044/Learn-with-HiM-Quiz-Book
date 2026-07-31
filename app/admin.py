import time
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from app.config import PRIMARY_ADMIN_ID
from app.database import (
    set_maintenance_until, get_maintenance_until, 
    get_all_users_full, get_all_student_feedbacks
)

async def is_primary_admin(user_id: int) -> bool:
    return user_id == PRIMARY_ADMIN_ID

async def admin_portal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await is_primary_admin(user.id):
        await update.message.reply_text("⛔ Unauthorized! This command is reserved for Himanshu Sir.")
        return

    m_until = get_maintenance_until()
    now = int(time.time())
    status_str = "🟢 LIVE (Active)" if now >= m_until else f"🔴 PAUSED (~{(m_until - now + 59)//60} mins left)"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏸ Pause 5 Mins", callback_data="admin_pause_5"), InlineKeyboardButton("⏸ Pause 10 Mins", callback_data="admin_pause_10")],
        [InlineKeyboardButton("▶️ Resume Service Immediately", callback_data="admin_resume")],
        [InlineKeyboardButton("👥 View All Users (Full Details)", callback_data="admin_view_users")],
        [InlineKeyboardButton("📢 Broadcast Announcement", callback_data="admin_broadcast")]
    ])

    msg = (
        f"👑 **LEARN WITH HIM QUIZ BOOK — ADMIN PORTAL**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• **Admin:** Himanshu Sir (`{user.id}`)\n"
        f"• **Current Bot Service Status:** {status_str}\n\n"
        f"Select an administrative command below:"
    )
    await update.message.reply_text(msg, reply_markup=keyboard, parse_mode="Markdown")

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user

    if not await is_primary_admin(user.id):
        await query.answer("⛔ Unauthorized!", show_alert=True)
        return

    data = query.data
    now = int(time.time())

    if data == "admin_pause_5":
        until = now + (5 * 60)
        set_maintenance_until(until)
        await query.answer("⏸ Service paused for 5 minutes!", show_alert=True)
        await query.edit_message_text("🛠 **Bot Service PAUSED for 5 Minutes.**\nAll user commands and callbacks are currently blocked.", parse_mode="Markdown")

    elif data == "admin_pause_10":
        until = now + (10 * 60)
        set_maintenance_until(until)
        await query.answer("⏸ Service paused for 10 minutes!", show_alert=True)
        await query.edit_message_text("🛠 **Bot Service PAUSED for 10 Minutes.**\nAll user commands and callbacks are currently blocked.", parse_mode="Markdown")

    elif data == "admin_resume":
        set_maintenance_until(0)
        await query.answer("🟢 Service resumed!", show_alert=True)
        await query.edit_message_text("🟢 **Bot Service RESUMED Immediately.**\nBroadcasting notice to all users...", parse_mode="Markdown")
        
        users = get_all_users_full()
        for u in users:
            try:
                await context.bot.send_message(
                    chat_id=u['user_id'],
                    text="📢 **ADMIN HAS RESUMED THE SERVICES, NOW YOU CAN ATTEMPT !!** 🚀",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

    elif data == "admin_view_users":
        await query.answer()
        await show_all_users_admin(update, context)

    elif data == "admin_broadcast":
        await query.answer()
        context.user_data["awaiting_broadcast"] = True
        await query.edit_message_text("📢 **Broadcast Mode Activated!**\nReply to this message with the announcement text to send to all users:")

async def show_all_users_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = get_all_users_full()
    if not users:
        await update.callback_query.edit_message_text("👥 No registered users found in database.")
        return

    total_count = len(users)
    header = f"👥 **REGISTERED USER DIRECTORY ({total_count} Total Users)**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    chunks = []
    current_chunk = header
    
    for idx, u in enumerate(users, start=1):
        entry = (
            f"**{idx}. {u['full_name']}** (`{u['user_id']}`)\n"
            f"• **Exam:** `{u['target_exam']}` | **Phone:** `{u['phone_number']}`\n"
            f"• **Location:** `{u['state']}, {u['country']}`\n"
            f"• **Age/Gender:** `{u['age']}` / `{u['gender']}`\n"
            f"• **Joined:** `{u['registration_date']}` | **Referrals:** `{u['referral_count']}`\n\n"
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