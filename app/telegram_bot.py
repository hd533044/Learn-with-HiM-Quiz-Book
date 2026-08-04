import time
import logging
import json
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, 
    BotCommand, BotCommandScopeDefault, BotCommandScopeAllPrivateChats, 
    BotCommandScopeAllGroupChats, ReplyKeyboardRemove
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, PollAnswerHandler, 
    MessageHandler, filters, ContextTypes
)
from app.config import BOT_TOKEN, PRIMARY_ADMIN_ID, DAILY_QUESTION_LIMIT
from app.database import (
    init_db, get_maintenance_until, get_user_profile, 
    get_all_users, get_today_attempts, save_student_feedback, get_all_student_feedbacks,
    clear_paused_quiz_state, get_saved_questions, log_user_activity_time,
    check_and_update_inactivity, refresh_user_activity_epoch
)
from app.onboarding import get_onboarding_handler, start_onboarding
from app.quiz_engine import (
    launch_quiz_setup, quiz_count_callback, quiz_timer_callback, handle_poll_answer,
    pause_quiz_command, resume_quiz_command, stop_quiz_command, save_question_callback
)
from app.stats import get_overall_leaderboard, calculate_user_percentile, calculate_user_rank, get_user_performance_summary
from app.admin import admin_portal_command, admin_callback_handler

NEGATIVE_WORDS = ["bad", "worst", "useless", "trash", "fake", "hate", "terrible", "waste", "horrible", "fraud", "stupid", "scam"]

async def send_registration_prompt(update: Update):
    msg = (
        "⚠️ **ᴛᴏ ᴜꜱᴇ ᴛʜɪꜱ qᴜɪᴢ ʙᴏᴏᴋ ʏᴏᴜ ᴍᴜꜱᴛ ʀᴇɢɪꜱᴛᴇʀ ꜰɪʀꜱᴛ ʜᴇʀᴇ!**\n\n"
        "ᴘʟᴇᴀꜱᴇ ᴛᴀᴘ ᴛʜᴇ ʀᴇɢɪꜱᴛʀᴀᴛɪᴏɴ ʙᴜᴛᴛᴏɴ ʙᴇʟᴏᴡ ᴛᴏ ꜱᴇᴛ ᴜᴘ ʏᴏᴜʀ ꜱᴛᴜᴅᴇɴᴛ ᴘʀᴏꜰɪʟᴇ:"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 ᴄʟɪᴄᴋ ʜᴇʀᴇ ꜰᴏʀ ʀᴇɢɪꜱᴛʀᴀᴛɪᴏɴ", callback_data="trigger_start")]
    ])
    if update.callback_query:
        await update.callback_query.answer("⚠️ Registration Required!", show_alert=True)
        await update.callback_query.message.reply_text(msg, reply_markup=keyboard, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(msg, reply_markup=keyboard, parse_mode="Markdown")

async def check_user_registration(update: Update) -> bool:
    user = update.effective_user
    if not user:
        return False
    profile = get_user_profile(user.id)
    if not profile or not profile.get("is_verified"):
        await send_registration_prompt(update)
        return False
    return True

async def inactivity_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user:
        return True

    user_id = user.id
    if user_id == PRIMARY_ADMIN_ID:
        refresh_user_activity_epoch(user_id)
        return True

    is_locked, diff_sec = check_and_update_inactivity(user_id)
    if is_locked:
        context.user_data["is_account_locked"] = True
        rec_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 ʀᴇꜱᴇᴛ ʏᴏᴜʀ ᴘɪɴ / ᴘᴀꜱꜱᴡᴏʀᴅ", callback_data="login_forgot_pin")]])
        msg = (
            f"🔒 **ᴀᴄᴄᴏᴜɴᴛ ʟᴏᴄᴋᴇᴅ ᴅᴜᴇ ᴛᴏ ɪɴᴀᴄᴛɪᴠɪᴛʏ**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"ʏᴏᴜ ᴡᴇʀᴇ ɪɴᴀᴄᴛɪᴠᴇ ꜰᴏʀ `{diff_sec // 60} ᴍɪɴꜱ`.\n\n"
            f"🔑 **ᴘʟᴇᴀꜱᴇ ʀᴇᴘʟʏ ᴡɪᴛʜ ʏᴏᴜʀ 4-ᴅɪɢɪᴛ ꜱᴇᴄʀᴇᴛ ᴘɪɴ ᴛᴏ ᴜɴʟᴏᴄᴋ ʏᴏᴜʀ ᴀᴄᴄᴏᴜɴᴛ:**"
        )
        if update.callback_query:
            await update.callback_query.answer("🔒 Account Locked due to 5 mins of inactivity!", show_alert=True)
            await update.callback_query.message.reply_text(msg, reply_markup=rec_btn, parse_mode="Markdown")
        elif update.message:
            await update.message.reply_text(msg, reply_markup=rec_btn, parse_mode="Markdown")
        return False
    return True

async def maintenance_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not await inactivity_guard(update, context):
        return False

    m_until = get_maintenance_until()
    if int(time.time()) < m_until:
        remaining_sec = m_until - int(time.time())
        mins_left = max(1, (remaining_sec + 59) // 60)
        msg = f"🛠 **ᴀᴅᴍɪɴ ʜᴀꜱ ᴘᴀᴜꜱᴇᴅ ᴛʜᴇ ꜱᴇʀᴠɪᴄᴇ ᴄᴜʀʀᴇɴᴛʟʏ**\nꜱᴇʀᴠɪᴄᴇ ᴡɪʟʟ ʀᴇꜱᴜᴍᴇ ɪɴ ᴀᴘᴘʀᴏxɪᴍᴀᴛᴇʟʏ `{mins_left} ᴍɪɴꜱ`. ᴘʟᴇᴀꜱᴇ ᴛʀʏ ᴀɢᴀɪɴ ʟᴀᴛᴇʀ!"
        
        if update.callback_query:
            await update.callback_query.answer(f"🛠 Service Paused! Resuming in ~{mins_left} mins.", show_alert=True)
        elif update.message:
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
        return False
    return True

async def strict_quiz_command_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): 
        return

    if not await check_user_registration(update):
        return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)
    profile = get_user_profile(user.id)

    attempted_today = get_today_attempts(user.id)
    allowed_limit = 10000 if user.id == PRIMARY_ADMIN_ID else DAILY_QUESTION_LIMIT + profile.get("bonus_quota", 0)

    if attempted_today >= allowed_limit:
        limit_msg = (
            f"🛑 **ᴅᴀɪʟʏ ꜰʀᴇᴇ ʟɪᴍɪᴛ ᴇxʜᴀᴜꜱᴛᴇᴅ!**\n\n"
            f"ʏᴏᴜ ʜᴀᴠᴇ ʀᴇᴀᴄʜᴇᴅ ʏᴏᴜʀ ᴀᴄᴛᴜᴀʟ ᴅᴀɪʟʏ ʟɪᴍɪᴛ ᴏꜰ `{allowed_limit}` qᴜᴇꜱᴛɪᴏɴꜱ ꜰᴏʀ ᴛᴏᴅᴀʏ (00:00 ᴛᴏ 23:59).\n"
            f"ᴛʜᴇ `/quiz` ᴄᴏᴍᴍᴀɴᴅ ʜᴀꜱ ʙᴇᴇɴ **ᴅᴇᴀᴄᴛɪᴠᴀᴛᴇᴅ** ꜰᴏʀ ʏᴏᴜʀ ᴀᴄᴄᴏᴜɴᴛ ᴜɴᴛɪʟ ᴛᴏᴍᴏʀʀᴏᴡ.\n\n"
            f"💡 **ᴜɴʟᴏᴄᴋ +10 qᴜᴇꜱᴛɪᴏɴꜱ:** ꜱʜᴀʀᴇ ʏᴏᴜʀ ɪɴᴠɪᴛᴇ ʟɪɴᴋ ᴡɪᴛʜ 4 ꜰʀɪᴇɴᴅꜱ ᴛᴏ ɪɴᴄʀᴇᴀꜱᴇ ʏᴏᴜʀ ʟɪᴍɪᴛ!"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🤝 ɪɴᴠɪᴛᴇ ꜰʀɪᴇɴᴅꜱ (+10 ʟɪᴍɪᴛ)", callback_data="cmd_referral")
        ]])
        await update.message.reply_text(limit_msg, reply_markup=keyboard, parse_mode="Markdown")
        return

    await launch_quiz_setup(update, context)

async def send_response(update: Update, text: str, reply_markup=None):
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception:
            await update.callback_query.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    elif update.message:
        markup = reply_markup if reply_markup else ReplyKeyboardRemove()
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return
    
    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)

    msg = (
        "🤖 **ʟᴇᴀʀɴ ᴡɪᴛʜ ʜɪᴍ qᴜɪᴢ ʙᴏᴏᴋ — ᴄᴏᴍᴍᴀɴᴅ ᴅɪʀᴇᴄᴛᴏʀʏ**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "ᴛᴀᴘ ᴀɴʏ ʙᴜᴛᴛᴏɴ ʙᴇʟᴏᴡ ᴏʀ ᴏᴘᴇɴ ᴛʜᴇ ʙʟᴜᴇ **[≡ ᴍᴇɴᴜ]** ʙᴜᴛᴛᴏɴ:\n\n"
        "• 🚀 **/quiz**: ꜱᴛᴀʀᴛ ᴀ ɴᴇᴡ ᴄᴜꜱᴛᴏᴍ ᴄᴏᴍᴘᴜᴛᴇʀ qᴜɪᴢ\n"
        "• ⏸ **/pause**: ᴘᴀᴜꜱᴇ ʀᴜɴɴɪɴɢ qᴜɪᴢ\n"
        "• ▶️ **/resume**: ʀᴇꜱᴜᴍᴇ ᴘᴀᴜꜱᴇᴅ qᴜɪᴢ\n"
        "• 🛑 **/stop**: ꜱᴛᴏᴘ qᴜɪᴢ ᴄᴏᴍᴘʟᴇᴛᴇʟʏ & ʀᴇꜱᴛᴏʀᴇ ʀᴇᴍᴀɪɴɪɴɢ ʟɪᴍɪᴛ\n"
        "• 💾 **/savedquestions**: ᴠɪᴇᴡ ʏᴏᴜʀ ʙᴏᴏᴋᴍᴀʀᴋᴇᴅ/ꜱᴀᴠᴇᴅ qᴜᴇꜱᴛɪᴏɴꜱ\n"
        "• 👤 **/myprofile**: ᴠɪᴇᴡ ʏᴏᴜʀ ᴠᴇʀɪꜰɪᴇᴅ ꜱᴛᴜᴅᴇɴᴛ ᴄᴀʀᴅ\n"
        "• ✏️ **/editprofile**: ᴜᴘᴅᴀᴛᴇ ᴘʀᴏꜰɪʟᴇ ᴅᴇᴛᴀɪʟꜱ (1x / 30 ᴅᴀʏꜱ)\n"
        "• 📊 **/mywholestate**: ᴠɪᴇᴡ ᴅᴇᴛᴀɪʟᴇᴅ ʀᴀɴᴋ & ᴘᴇʀᴄᴇɴᴛɪʟᴇ\n"
        "• 🏆 **/toppername**: ɪɴꜱᴘᴇᴄᴛ ᴛʜᴇ ɢʟᴏʙᴀʟ ꜱᴄʜᴏʟᴀʀ ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ\n"
        "• 💬 **/feedback**: ʀᴀᴛᴇ ᴛʜᴇ ʙᴏᴛ ᴏʀ ʟᴇᴀᴠᴇ ꜰᴇᴇᴅʙᴀᴄᴋ\n"
        "• 📖 **/reviews**: ᴠɪᴇᴡ ꜱᴛᴜᴅᴇɴᴛ ʀᴇᴠɪᴇᴡꜱ\n"
        "• 🤝 **/invite**: ꜱʜᴀʀᴇ ʀᴇꜰᴇʀʀᴀʟ ʟɪɴᴋ ᴛᴏ ᴜɴʟᴏᴄᴋ +10 ʟɪᴍɪᴛ"
    )

    buttons = [
        [InlineKeyboardButton("🚀 /quiz", callback_data="cmd_quiz"), InlineKeyboardButton("🛑 /stop", callback_data="cmd_stop_quiz")],
        [InlineKeyboardButton("💾 /savedquestions", callback_data="cmd_savedquestions"), InlineKeyboardButton("👤 /myprofile", callback_data="cmd_profile")],
        [InlineKeyboardButton("📊 /mywholestate", callback_data="cmd_wholestate"), InlineKeyboardButton("🏆 /toppername", callback_data="cmd_toppers")],
        [InlineKeyboardButton("🤝 /invite", callback_data="cmd_referral"), InlineKeyboardButton("📖 /reviews", callback_data="cmd_viewfeedbacks")]
    ]

    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def saved_questions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)
    saved = get_saved_questions(user.id)
    
    if not saved:
        msg = (
            "📖 **ꜱᴀᴠᴇᴅ qᴜᴇꜱᴛɪᴏɴꜱ ʙᴏᴏᴋᴍᴀʀᴋ**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "ʏᴏᴜ ʜᴀᴠᴇɴ'ᴛ ꜱᴀᴠᴇᴅ ᴀɴʏ qᴜᴇꜱᴛɪᴏɴꜱ ʏᴇᴛ! ᴛᴀᴘ ᴛʜᴇ **💾 ꜱᴀᴠᴇ qᴜᴇꜱᴛɪᴏɴ** ʙᴜᴛᴛᴏɴ ᴅᴜʀɪɴɢ ʏᴏᴜʀ qᴜɪᴢ ᴀᴛᴛᴇᴍᴘᴛꜱ ᴛᴏ ʙᴏᴏᴋᴍᴀʀᴋ ɪᴍᴘᴏʀᴛᴀɴᴛ qᴜᴇꜱᴛɪᴏɴꜱ ʜᴇʀᴇ."
        )
        await send_response(update, msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚀 ʟᴀᴜɴᴄʜ qᴜɪᴢ", callback_data="cmd_quiz")]]))
        return

    total_count = len(saved)
    lines = [
        f"📖 **ꜱᴀᴠᴇᴅ qᴜᴇꜱᴛɪᴏɴꜱ ʙᴏᴏᴋᴍᴀʀᴋ**",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 **ᴛᴏᴛᴀʟ ꜱᴀᴠᴇᴅ qᴜᴇꜱᴛɪᴏɴꜱ:** `{total_count}`",
        f"📌 *ꜱʜᴏᴡɪɴɢ ᴍᴏꜱᴛ ʀᴇᴄᴇɴᴛ ʙᴏᴏᴋᴍᴀʀᴋꜱ ꜰɪʀꜱᴛ*\n"
    ]

    for idx, sq in enumerate(saved[:15], start=1):
        opts_list = json.loads(sq['options_json']) if sq['options_json'] else []
        corr_idx = sq['correct_option']
        corr_ans = opts_list[corr_idx] if 0 <= corr_idx < len(opts_list) else 'ɴ/ᴀ'
        
        lines.append(
            f"**{idx}. ꜱᴀᴠᴇᴅ ᴀᴛ:** `{sq['saved_at']}`\n"
            f"❓ **q:** {sq['question_text']}\n"
            f"✅ **ᴄᴏʀʀᴇᴄᴛ ᴀɴꜱᴡᴇʀ:** `{corr_ans}`\n"
            f"💡 **ᴇxᴘʟᴀɴᴀᴛɪᴏɴ:** {sq['explanation']}\n"
            f"──────────────────────────────"
        )

    if total_count > 15:
        lines.append(f"\n*(ꜱʜᴏᴡɪɴɢ 15 ᴏꜰ {total_count} ꜱᴀᴠᴇᴅ qᴜᴇꜱᴛɪᴏɴꜱ)*")

    msg = "\n".join(lines)
    buttons = [[InlineKeyboardButton("🚀 ʟᴀᴜɴᴄʜ qᴜɪᴢ", callback_data="cmd_quiz")]]
    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def myprofile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)
    profile = get_user_profile(user.id)

    today_used = get_today_attempts(user.id)
    allowed_limit = 10000 if user.id == PRIMARY_ADMIN_ID else DAILY_QUESTION_LIMIT + profile.get("bonus_quota", 0)

    remaining = max(0, allowed_limit - today_used)
    student_id = profile.get("student_id", f"USER_{user.id}")

    msg = (
        f"👤 **ꜱᴛᴜᴅᴇɴᴛ ᴘʀᴏꜰɪʟᴇ ᴄᴀʀᴅ**\n"
        f"📚 *ʟᴇᴀʀɴ ᴡɪᴛʜ ʜɪᴍ qᴜɪᴢ ʙᴏᴏᴋ*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• **ꜰᴜʟʟ ɴᴀᴍᴇ:** {profile['full_name']}\n"
        f"• **ꜱᴛᴜᴅᴇɴᴛ ɪᴅ:** `{student_id}`\n"
        f"• **ᴛᴇʟᴇɢʀᴀᴍ ɪᴅ:** `{profile['user_id']}`\n"
        f"• **ᴛᴀʀɢᴇᴛ ᴇxᴀᴍ:** `{profile['target_exam']}`\n"
        f"• **ᴅᴏʙ / ɢᴇɴᴅᴇʀ:** `{profile.get('dob', 'ɴ/ᴀ')}` / `{profile['gender']}`\n"
        f"• **ʟᴏᴄᴀᴛɪᴏɴ:** `{profile.get('state', 'ɴ/ᴀ')}, {profile.get('country', 'ɪɴᴅɪᴀ')}`\n"
        f"• **ᴘʜᴏɴᴇ:** `{profile['phone_number']}` *(ᴘʀɪᴠᴀᴛᴇ)*\n\n"
        f"📊 **ᴅᴀɪʟʏ qᴜᴏᴛᴀ ꜱᴛᴀᴛᴜꜱ (00:00 ᴛᴏ 23:59):**\n"
        f"• **ᴜꜱᴇᴅ ᴛᴏᴅᴀʏ:** `{today_used}` / `{allowed_limit}` qꜱ\n"
        f"• **ʀᴇᴍᴀɪɴɪɴɢ ᴛᴏᴅᴀʏ:** `{remaining}` qꜱ\n"
        f"• **ʀᴇꜰᴇʀʀᴀʟꜱ:** `{profile.get('referral_count', 0)}` / 4 ꜰʀɪᴇɴᴅꜱ\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    buttons = [
        [InlineKeyboardButton("🚀 ʟᴀᴜɴᴄʜ qᴜɪᴢ", callback_data="cmd_quiz"), InlineKeyboardButton("💾 ꜱᴀᴠᴇᴅ qᴜᴇꜱᴛɪᴏɴꜱ", callback_data="cmd_savedquestions")],
        [InlineKeyboardButton("✏️ ᴇᴅɪᴛ ᴘʀᴏꜰɪʟᴇ", callback_data="cmd_editprofile"), InlineKeyboardButton("🤝 ɪɴᴠɪᴛᴇ (+10 qᴜᴏᴛᴀ)", callback_data="cmd_referral")]
    ]

    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def wholestate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)
    profile = get_user_profile(user.id)

    perf = get_user_performance_summary(user.id)
    rank = calculate_user_rank(user.id)
    percentile = calculate_user_percentile(user.id)
    student_id = profile.get("student_id", f"USER_{user.id}")

    msg = (
        f"🎓 **ꜱᴛᴜᴅᴇɴᴛ ᴀᴄᴀᴅᴇᴍɪᴄ ʀᴇᴘᴏʀᴛ ᴄᴀʀᴅ**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 **ɴᴀᴍᴇ:** {profile['full_name']}\n"
        f"🪪 **ꜱᴛᴜᴅᴇɴᴛ ɪᴅ:** `{student_id}`\n"
        f"🎯 **ᴛᴀʀɢᴇᴛ ᴇxᴀᴍ:** `{profile['target_exam']}`\n"
        f"📍 **ʟᴏᴄᴀᴛɪᴏɴ:** `{profile.get('state', 'ɴ/ᴀ')}, {profile.get('country', 'ɪɴᴅɪᴀ')}`\n\n"
        f"📈 **ᴘᴇʀꜰᴏʀᴍᴀɴᴄᴇ ᴍᴇᴛʀɪᴄꜱ:**\n"
        f"• **ᴛᴇꜱᴛꜱ ᴄᴏᴍᴘʟᴇᴛᴇᴅ:** `{perf.get('total_tests', 0)}`\n"
        f"• **qᴜᴇꜱᴛɪᴏɴꜱ ᴀᴛᴛᴇᴍᴘᴛᴇᴅ:** `{perf.get('total_qs', 0)}`\n"
        f"• **ɢʟᴏʙᴀʟ ʀᴀɴᴋ:** `{rank}`\n"
        f"• **ᴏᴠᴇʀᴀʟʟ ᴘᴇʀᴄᴇɴᴛɪʟᴇ:** `{percentile}%` *(ᴄᴀʟᴄᴜʟᴀᴛᴇᴅ ᴀɢᴀɪɴꜱᴛ ᴀʟʟ ʀᴇɢɪꜱᴛᴇʀᴇᴅ ꜱᴛᴜᴅᴇɴᴛꜱ)*"
    )

    buttons = [[InlineKeyboardButton("🚀 ʟᴀᴜɴᴄʜ qᴜɪᴢ", callback_data="cmd_quiz"), InlineKeyboardButton("🏆 ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ", callback_data="cmd_toppers")]]
    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def toppers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)
    toppers = get_overall_leaderboard(limit=10)
    
    if not toppers:
        await send_response(update, "🏆 No leaderboard records available yet. Be the first to attempt a quiz!")
        return

    lines = []
    for idx, t in enumerate(toppers, start=1):
        badge = " 🥇" if idx == 1 else " 🥈" if idx == 2 else " 🥉" if idx == 3 else ""
        lines.append(f"{idx}. **{t['full_name']}**{badge} — ᴀᴠɢ ꜱᴄᴏʀᴇ: `{round(t['avg_score'], 2)}`")

    msg = "🏆 **ɢʟᴏʙᴀʟ ꜱᴄʜᴏʟᴀʀ ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n" + "\n".join(lines)
    buttons = [[InlineKeyboardButton("🚀 ᴀᴛᴛᴇᴍᴘᴛ qᴜɪᴢ", callback_data="cmd_quiz")]]
    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)

    keyboard = [
        [InlineKeyboardButton("🌟 10/10 ʙᴏᴛ! ᴛʜᴇ qᴜɪᴢᴢᴇꜱ ᴀʀᴇ ᴛᴏᴘ qᴜᴀʟɪᴛʏ 🚀", callback_data="fb_p1")],
        [InlineKeyboardButton("✨ ʟᴇᴀʀɴ ᴡɪᴛʜ ʜɪᴍ ɪꜱ ᴛʜᴇ ʙᴇꜱᴛ ᴘʀᴇᴘᴀʀᴀᴛɪᴏɴ ᴘᴏʀᴛᴀʟ 🎓", callback_data="fb_p2")],
        [InlineKeyboardButton("🔥 ᴅᴀɪʟʏ ᴛᴀʀɢᴇᴛ ʟɪᴍɪᴛꜱ ᴋᴇᴇᴘ ᴍᴇ ᴅɪꜱᴄɪᴘʟɪɴᴇᴅ! 📈", callback_data="fb_p3")],
        [InlineKeyboardButton("✍️ ᴡʀɪᴛᴇ ᴄᴜꜱᴛᴏᴍ ꜰᴇᴇᴅʙᴀᴄᴋ", callback_data="fb_custom")],
        [InlineKeyboardButton("📖 ᴠɪᴇᴡ ꜱᴛᴜᴅᴇɴᴛ ʀᴇᴠɪᴇᴡꜱ", callback_data="cmd_viewfeedbacks")]
    ]

    msg = (
        "💬 **ꜱᴛᴜᴅᴇɴᴛ ꜰᴇᴇᴅʙᴀᴄᴋ ᴘᴏʀᴛᴀʟ**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "ʏᴏᴜʀ ꜰᴇᴇᴅʙᴀᴄᴋ ʜᴇʟᴘꜱ ʜɪᴍᴀɴꜱʜᴜ ꜱɪʀ ᴍᴀᴋᴇ ᴛʜɪꜱ ᴘʟᴀᴛꜰᴏʀᴍ ᴇᴠᴇɴ ʙᴇᴛᴛᴇʀ!\n"
        "ꜱᴇʟᴇᴄᴛ ᴀ qᴜɪᴄᴋ ᴘʀᴇꜱᴇᴛ ʀᴀᴛɪɴɢ ʙᴇʟᴏᴡ ᴏʀ ᴡʀɪᴛᴇ ʏᴏᴜʀ ᴏᴡɴ ᴄᴜꜱᴛᴏᴍ ꜰᴇᴇᴅʙᴀᴄᴋ:"
    )
    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(keyboard))

async def viewfeedbacks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)
    feedbacks = get_all_student_feedbacks(limit=15)

    if not feedbacks:
        await send_response(update, "📖 No student reviews submitted yet. Be the first to leave feedback using /feedback!")
        return

    lines = ["📖 **ꜱᴛᴜᴅᴇɴᴛ ʀᴇᴠɪᴇᴡꜱ & ꜰᴇᴇᴅʙᴀᴄᴋ ʙᴏᴀʀᴅ**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"]
    for idx, fb in enumerate(feedbacks, start=1):
        lines.append(f"**{idx}. {fb['full_name']}**:\n 💬 *\"{fb['feedback_text']}\"*\n")

    await send_response(update, "\n".join(lines))

async def referral_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)
    bot_username = context.bot.username
    ref_link = f"https://t.me/{bot_username}?start=ref_{user.id}"

    msg = (
        f"🤝 **ɪɴᴠɪᴛᴇ ꜰʀɪᴇɴᴅꜱ & ᴜɴʟᴏᴄᴋ +10 ᴅᴀɪʟʏ ʟɪᴍɪᴛ**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"ꜱʜᴀʀᴇ ʏᴏᴜʀ ᴘᴇʀꜱᴏɴᴀʟ ɪɴᴠɪᴛᴇ ʟɪɴᴋ ʙᴇʟᴏᴡ ᴡɪᴛʜ **4 ꜰʀɪᴇɴᴅꜱ**:\n"
        f"`{ref_link}`\n\n"
        f"ᴡʜᴇɴ 4 ꜰʀɪᴇɴᴅꜱ ʀᴇɢɪꜱᴛᴇʀ ᴜꜱɪɴɢ ʏᴏᴜʀ ʟɪɴᴋ, ʏᴏᴜ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ ʀᴇᴄᴇɪᴠᴇ +10 qᴜᴇꜱᴛɪᴏɴꜱ ᴀᴅᴅᴇᴅ ᴛᴏ ʏᴏᴜʀ ᴅᴀɪʟʏ qᴜᴏᴛᴀ!"
    )
    await send_response(update, msg)

async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return

    query = update.callback_query
    data = query.data
    user = query.from_user
    log_user_activity_time(user.id, seconds=5)

    if data == "trigger_start":
        await start_onboarding(update, context)
        return

    if not await check_user_registration(update): return

    if data == "cmd_quiz":
        profile = get_user_profile(user.id)
        attempted_today = get_today_attempts(user.id)
        allowed_limit = 10000 if user.id == PRIMARY_ADMIN_ID else DAILY_QUESTION_LIMIT + (profile.get("bonus_quota", 0) if profile else 0)

        if attempted_today >= allowed_limit:
            await query.answer("🛑 Daily Limit Exhausted! /quiz is deactivated.", show_alert=True)
            return
        await launch_quiz_setup(update, context)
    elif data == "cmd_pause_quiz":
        await pause_quiz_command(update, context)
    elif data == "cmd_resume_quiz":
        await resume_quiz_command(update, context)
    elif data == "cmd_stop_quiz":
        await stop_quiz_command(update, context)
    elif data == "cmd_savedquestions":
        await saved_questions_command(update, context)
    elif data == "cmd_save_question":
        await save_question_callback(update, context)
    elif data == "cmd_start_fresh_quiz":
        clear_paused_quiz_state(user.id)
        await launch_quiz_setup(update, context)
    elif data == "cmd_profile":
        await myprofile_command(update, context)
    elif data == "cmd_toppers":
        await toppers_command(update, context)
    elif data == "cmd_wholestate":
        await wholestate_command(update, context)
    elif data == "cmd_referral":
        await referral_command(update, context)
    elif data == "cmd_feedback":
        await feedback_command(update, context)
    elif data == "cmd_viewfeedbacks":
        await viewfeedbacks_command(update, context)
    elif data.startswith("fb_p"):
        presets = {
            "fb_p1": "10/10 ʙᴏᴛ! ᴛʜᴇ qᴜɪᴢᴢᴇꜱ ᴀʀᴇ ᴛᴏᴘ qᴜᴀʟɪᴛʏ 🚀",
            "fb_p2": "ʟᴇᴀʀɴ ᴡɪᴛʜ ʜɪᴍ ɪꜱ ᴛʜᴇ ʙᴇꜱᴛ ᴘʀᴇᴘᴀʀᴀᴛɪᴏɴ ᴘᴏʀᴛᴀʟ 🎓",
            "fb_p3": "ᴅᴀɪʟʏ ᴛᴀʀɢᴇᴛ ʟɪᴍɪᴛꜱ ᴋᴇᴇᴘ ᴍᴇ ᴅɪꜱᴄɪᴘʟɪɴᴇᴅ! 📈"
        }
        fb_text = presets.get(data, "ɢʀᴇᴀᴛ ᴇᴅᴜᴄᴀᴛɪᴏɴᴀʟ ʙᴏᴛ!")
        profile = get_user_profile(user.id)
        name = profile.get("full_name") if profile else user.full_name
        save_student_feedback(user.id, name, fb_text)
        await query.edit_message_text(f"🎉 **ᴛʜᴀɴᴋ ʏᴏᴜ, {name}!** ʏᴏᴜʀ ꜰᴇᴇᴅʙᴀᴄᴋ ʜᴀꜱ ʙᴇᴇɴ ʀᴇᴄᴏʀᴅᴇᴅ:\n\n💬 *\"{fb_text}\"*", parse_mode="Markdown")

    elif data == "fb_custom":
        context.user_data["awaiting_custom_feedback"] = True
        await query.edit_message_text("✍️ ᴘʟᴇᴀꜱᴇ ʀᴇᴘʟʏ ᴡɪᴛʜ ʏᴏᴜʀ ᴄᴜꜱᴛᴏᴍ ꜰᴇᴇᴅʙᴀᴄᴋ/ʀᴇᴠɪᴇᴡ ʙᴇʟᴏᴡ:")

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    # Unlock Locked Account via Inactivity PIN
    if context.user_data.get("is_account_locked"):
        profile = get_user_profile(user.id)
        if profile and profile.get("pin") == text:
            context.user_data["is_account_locked"] = False
            refresh_user_activity_epoch(user.id)
            await update.message.reply_text("🔓 **ᴀᴄᴄᴏᴜɴᴛ ᴜɴʟᴏᴄᴋᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ!**\nʏᴏᴜ ᴍᴀʏ ᴄᴏɴᴛɪɴᴜᴇ ʟᴇᴀʀɴɪɴɢ.", reply_markup=ReplyKeyboardRemove())
        else:
            rec_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 ʀᴇꜱᴇᴛ ʏᴏᴜʀ ᴘɪɴ / ᴘᴀꜱꜱᴡᴏʀᴅ", callback_data="login_forgot_pin")]])
            await update.message.reply_text(
                "❌ **ɪɴᴄᴏʀʀᴇᴄᴛ ᴘɪɴ!**\n\nᴘʟᴇᴀꜱᴇ ᴛʀʏ ᴇɴᴛᴇʀɪɴɢ ʏᴏᴜʀ ᴄᴏʀʀᴇᴄᴛ 4-ᴅɪɢɪᴛ ᴘɪɴ ᴛᴏ ᴜɴʟᴏᴄᴋ, ᴏʀ ᴛᴀᴘ ʙᴇʟᴏᴡ ᴛᴏ ʀᴇꜱᴇᴛ ʏᴏᴜʀ ᴘɪɴ:",
                reply_markup=rec_btn,
                parse_mode="Markdown"
            )
        return

    if not await maintenance_guard(update, context): return
    log_user_activity_time(user.id, seconds=10)

    # Admin Search Handler
    if user.id == PRIMARY_ADMIN_ID and context.user_data.get("awaiting_admin_search"):
        context.user_data["awaiting_admin_search"] = False
        all_u = get_all_users()
        matches = [
            u for u in all_u if text.lower() in str(u.get("student_id", "")).lower() 
            or text.lower() in str(u.get("phone_number", "")).lower() 
            or text.lower() in str(u.get("full_name", "")).lower()
        ]

        if not matches:
            await update.message.reply_text(f"⚠️ No student found matching query: `{text}`", parse_mode="Markdown")
            return

        keyboard = []
        for m in matches[:10]:
            sid = m.get("student_id") or f"USER_{m['user_id']}"
            keyboard.append([InlineKeyboardButton(f"👤 {m['full_name']} (ɪᴅ: {sid})", callback_data=f"admin_inspect_u_{m['user_id']}")])
        
        await update.message.reply_text(f"🔍 **ꜱᴇᴀʀᴄʜ ʀᴇꜱᴜʟᴛꜱ ꜰᴏʀ '{text}':**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    if context.user_data.get("awaiting_custom_feedback"):
        context.user_data["awaiting_custom_feedback"] = False
        
        if any(bad_word in text.lower() for bad_word in NEGATIVE_WORDS):
            await update.message.reply_text("🙏 Thank you for your feedback! We are constantly working hard to improve your experience.", reply_markup=ReplyKeyboardRemove())
            return

        profile = get_user_profile(user.id)
        name = profile.get("full_name") if profile else user.full_name
        save_student_feedback(user.id, name, text)
        await update.message.reply_text(f"🎉 **ꜰᴇᴇᴅʙᴀᴄᴋ ʀᴇᴄᴇɪᴠᴇᴅ!** ᴛʜᴀɴᴋ ʏᴏᴜ *{name}* ꜰᴏʀ ʏᴏᴜʀ ᴋɪɴᴅ ᴡᴏʀᴅꜱ:\n\n💬 *\"{text}\"*", reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
        return

    if context.user_data.get("awaiting_broadcast"):
        context.user_data["awaiting_broadcast"] = False
        users = get_all_users()
        sent = 0
        for u in users:
            try:
                await context.bot.send_message(chat_id=u['user_id'], text=f"📢 **ᴀɴɴᴏᴜɴᴄᴇᴍᴇɴᴛ ꜰʀᴏᴍ ʜɪᴍᴀɴꜱʜᴜ ꜱɪʀ**\n\n{text}", parse_mode="Markdown")
                sent += 1
            except Exception:
                pass
        await update.message.reply_text(f"✅ Announcement sent to {sent} users!", reply_markup=ReplyKeyboardRemove())

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.debug(f"Exception caught in global error handler: {context.error}")

async def post_init(application: Application):
    try:
        await application.bot.delete_my_commands(scope=BotCommandScopeDefault())
        await application.bot.delete_my_commands(scope=BotCommandScopeAllPrivateChats())
        await application.bot.delete_my_commands(scope=BotCommandScopeAllGroupChats())
    except Exception as e:
        logging.warning(f"Note on command purge: {e}")

    allowed_commands = [
        BotCommand("quiz", "🚀 ꜱᴛᴀʀᴛ ᴄᴏᴍᴘᴜᴛᴇʀ qᴜɪᴢ"),
        BotCommand("pause", "⏸ ᴘᴀᴜꜱᴇ ʀᴜɴɴɪɴɢ qᴜɪᴢ"),
        BotCommand("resume", "▶️ ʀᴇꜱᴜᴍᴇ ᴘᴀᴜꜱᴇᴅ qᴜɪᴢ"),
        BotCommand("stop", "🛑 ꜱᴛᴏᴘ qᴜɪᴢ ᴄᴏᴍᴘʟᴇᴛᴇʟʏ"),
        BotCommand("savedquestions", "💾 ᴠɪᴇᴡ ꜱᴀᴠᴇᴅ qᴜᴇꜱᴛɪᴏɴꜱ"),
        BotCommand("myprofile", "👤 ᴠɪᴇᴡ ꜱᴛᴜᴅᴇɴᴛ ᴘʀᴏꜰɪʟᴇ"),
        BotCommand("editprofile", "✏️ ᴇᴅɪᴛ ᴘʀᴏꜰɪʟᴇ ᴅᴇᴛᴀɪʟꜱ"),
        BotCommand("mywholestate", "📊 ᴠɪᴇᴡ ᴘᴇʀꜰᴏʀᴍᴀɴᴄᴇ & ʀᴀɴᴋ"),
        BotCommand("toppername", "🏆 ɢʟᴏʙᴀʟ ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ"),
        BotCommand("feedback", "💬 ꜱᴜʙᴍɪᴛ ꜰᴇᴇᴅʙᴀᴄᴋ"),
        BotCommand("reviews", "📖 ᴠɪᴇᴡ ꜱᴛᴜᴅᴇɴᴛ ʀᴇᴠɪᴇᴡꜱ"),
        BotCommand("invite", "🤝 ɪɴᴠɪᴛᴇ ꜰʀɪᴇɴᴅꜱ (+10 ʟɪᴍɪᴛ)")
    ]
    
    await application.bot.set_my_commands(allowed_commands, scope=BotCommandScopeDefault())
    await application.bot.set_my_commands(allowed_commands, scope=BotCommandScopeAllPrivateChats())

def build_application() -> Application:
    init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(get_onboarding_handler())
    
    app.add_handler(CommandHandler("quiz", strict_quiz_command_guard))
    app.add_handler(CommandHandler("pause", pause_quiz_command))
    app.add_handler(CommandHandler("resume", resume_quiz_command))
    app.add_handler(CommandHandler("stop", stop_quiz_command))
    app.add_handler(CommandHandler("savedquestions", saved_questions_command))
    app.add_handler(CommandHandler("myprofile", myprofile_command))
    app.add_handler(CommandHandler("mywholestate", wholestate_command))
    app.add_handler(CommandHandler("toppername", toppers_command))
    app.add_handler(CommandHandler("toppersname", toppers_command))
    app.add_handler(CommandHandler("feedback", feedback_command))
    app.add_handler(CommandHandler("reviews", viewfeedbacks_command))
    app.add_handler(CommandHandler("viewfeedbacks", viewfeedbacks_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("invite", referral_command))
    
    # Master Admin Portal Shortcuts
    app.add_handler(CommandHandler("admin", admin_portal_command))
    app.add_handler(CommandHandler("admit", admin_portal_command))
    app.add_handler(CommandHandler("user_profiles", admin_portal_command))

    app.add_handler(CallbackQueryHandler(quiz_count_callback, pattern="^qcount_"))
    app.add_handler(CallbackQueryHandler(quiz_timer_callback, pattern="^qtimer_"))
    app.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^(admin_|audit_|genpdf_)"))
    app.add_handler(CallbackQueryHandler(button_router, pattern="^cmd_|^fb_|^trigger_start"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
    app.add_handler(PollAnswerHandler(handle_poll_answer))
    app.add_error_handler(global_error_handler)

    return app