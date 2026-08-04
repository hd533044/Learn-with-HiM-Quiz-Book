import time
import logging
import json
import os
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
from app.pdf_generator import generate_student_pdf_report

NEGATIVE_WORDS = ["bad", "worst", "useless", "trash", "fake", "hate", "terrible", "waste", "horrible", "fraud", "stupid", "scam"]

async def send_registration_prompt(update: Update):
    msg = (
        "⚠️ REGISTRATION REQUIRED\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "To use Learn with HiM Quiz Book, please create your profile first:"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 START REGISTRATION", callback_data="trigger_start")]
    ])
    if update.callback_query:
        await update.callback_query.answer("⚠️ Registration Required!", show_alert=True)
        await update.callback_query.message.reply_text(msg, reply_markup=keyboard)
    elif update.message:
        await update.message.reply_text(msg, reply_markup=keyboard)

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
        rec_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 RESET PIN", callback_data="login_forgot_pin")]])
        msg = (
            f"🔒 ACCOUNT LOCKED (INACTIVITY)\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Inactive for {diff_sec // 60} mins.\n\n"
            f"Please reply with your 4-Digit Security PIN to unlock:"
        )
        if update.callback_query:
            await update.callback_query.answer("🔒 Account Locked due to 5 mins inactivity!", show_alert=True)
            await update.callback_query.message.reply_text(msg, reply_markup=rec_btn)
        elif update.message:
            await update.message.reply_text(msg, reply_markup=rec_btn)
        return False
    return True

async def maintenance_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not await inactivity_guard(update, context):
        return False

    m_until = get_maintenance_until()
    if int(time.time()) < m_until:
        remaining_sec = m_until - int(time.time())
        mins_left = max(1, (remaining_sec + 59) // 60)
        msg = f"🛠 SERVICE PAUSED\n\nAdmin has paused the bot. Service will resume in ~{mins_left} mins."
        
        if update.callback_query:
            await update.callback_query.answer(f"🛠 Service Paused! Resuming in ~{mins_left} mins.", show_alert=True)
        elif update.message:
            await update.message.reply_text(msg, reply_markup=ReplyKeyboardRemove())
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
            f"🛑 DAILY FREE LIMIT EXHAUSTED\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Daily free quota of {allowed_limit} questions reached for today.\n"
            f"The /quiz command is locked until tomorrow.\n\n"
            f"💡 Share your link with 4 friends to get +10 bonus questions!"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🤝 INVITE FRIENDS (+10 QUOTA)", callback_data="cmd_referral")
        ]])
        await update.message.reply_text(limit_msg, reply_markup=keyboard)
        return

    await launch_quiz_setup(update, context)

async def send_response(update: Update, text: str, reply_markup=None):
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
        except Exception:
            await update.callback_query.message.reply_text(text, reply_markup=reply_markup)
    elif update.message:
        markup = reply_markup if reply_markup else ReplyKeyboardRemove()
        await update.message.reply_text(text, reply_markup=markup)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return
    
    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)

    msg = (
        "🤖 COMMAND DIRECTORY\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Tap a button below or use Menu:\n\n"
        "• 🚀 /quiz: Start Computer Quiz\n"
        "• ⏸ /pause: Pause Running Quiz\n"
        "• ▶️ /resume: Resume Paused Quiz\n"
        "• 🛑 /stop: Stop Quiz Session\n"
        "• 📄 /pdfreport: Export Academic PDF Report\n"
        "• 💾 /savedquestions: View Saved Questions\n"
        "• 👤 /myprofile: View Student Profile Card\n"
        "• ✏️ /editprofile: Edit Profile Details\n"
        "• 📊 /mywholestate: View Rank & Performance\n"
        "• 🏆 /toppername: Scholar Leaderboard\n"
        "• 💬 /feedback: Submit Feedback\n"
        "• 📖 /reviews: Student Reviews\n"
        "• 🤝 /invite: Invite Friends (+10 Limit)"
    )

    buttons = [
        [InlineKeyboardButton("🚀 /quiz", callback_data="cmd_quiz"), InlineKeyboardButton("🛑 /stop", callback_data="cmd_stop_quiz")],
        [InlineKeyboardButton("📄 /pdfreport", callback_data="cmd_pdfreport"), InlineKeyboardButton("💾 /savedquestions", callback_data="cmd_savedquestions")],
        [InlineKeyboardButton("👤 /myprofile", callback_data="cmd_profile"), InlineKeyboardButton("📊 /mywholestate", callback_data="cmd_wholestate")],
        [InlineKeyboardButton("🏆 /toppername", callback_data="cmd_toppers"), InlineKeyboardButton("🤝 /invite", callback_data="cmd_referral")]
    ]

    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def pdfreport_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)

    pdf_buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 1. Last 1 Month Full Data Report", callback_data="studentpdf_last_1_month_data")],
        [InlineKeyboardButton("📊 2. Last 1 Month Quiz Summary (No Qs)", callback_data="studentpdf_last_1_month_quiz")],
        [InlineKeyboardButton("📜 3. All Months Full Data Report", callback_data="studentpdf_all_months_data")],
        [InlineKeyboardButton("📈 4. All Months Quiz Summary (No Qs)", callback_data="studentpdf_all_months_quiz")],
        [InlineKeyboardButton("📌 5. Saved / Bookmarked Questions Report", callback_data="studentpdf_saved_qs")],
        [InlineKeyboardButton("👤 BACK TO MY PROFILE", callback_data="cmd_profile")]
    ])

    msg = (
        f"📄 ACADEMIC PDF EXPORT PORTAL\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Select report format to download your PDF:"
    )
    await send_response(update, msg, reply_markup=pdf_buttons)

async def student_pdf_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return

    query = update.callback_query
    user = query.from_user
    filter_mode = query.data.replace("studentpdf_", "")

    await query.answer()
    await query.edit_message_text("⏳ Generating Official PDF Report...\nCompiling statistics and rendering PDF document...")

    pdf_file = generate_student_pdf_report(user.id, filter_mode)
    u = get_user_profile(user.id)
    sid = u.get("student_id", f"USER_{user.id}") if u else f"USER_{user.id}"

    nav_buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 EXPORT ANOTHER PDF", callback_data="cmd_pdfreport")],
        [InlineKeyboardButton("👤 BACK TO PROFILE", callback_data="cmd_profile")]
    ])

    if pdf_file == "NO_ATTEMPTS":
        await query.edit_message_text(
            f"ℹ️ NO QUIZ ATTEMPTS FOUND\n\n"
            f"No quiz attempts recorded in this selected timeframe.",
            reply_markup=nav_buttons
        )
    elif pdf_file == "NO_SAVED_QS":
        await query.edit_message_text(
            f"ℹ️ NO SAVED QUESTIONS FOUND\n\n"
            f"You have not bookmarked any questions yet. Tap Save Question during quizzes!",
            reply_markup=nav_buttons
        )
    elif pdf_file and pdf_file.startswith("ERROR_DETAILS:"):
        await query.edit_message_text(
            f"⚠️ PDF Generation Error:\n\n{pdf_file[:3500]}",
            reply_markup=nav_buttons
        )
    elif pdf_file and os.path.exists(pdf_file):
        with open(pdf_file, "rb") as doc:
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=doc,
                filename=os.path.basename(pdf_file),
                caption=(
                    f"📄 OFFICIAL ACADEMIC PDF REPORT\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 Student Name: {u.get('full_name')}\n"
                    f"🪪 Student ID: {sid}\n"
                    f"📊 Module: {filter_mode.replace('_', ' ').title()}\n"
                    f"🏷 Official Watermark: @LearnwithHiM"
                )
            )
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="👇 QUICK ACTIONS:",
            reply_markup=nav_buttons
        )
    else:
        await query.edit_message_text("⚠️ Failed to generate PDF file.", reply_markup=nav_buttons)

async def saved_questions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)
    saved = get_saved_questions(user.id)
    
    if not saved:
        msg = (
            "📖 SAVED QUESTIONS BOOKMARK\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "No saved questions found! Tap 💾 Save Question during quizzes to bookmark items here."
        )
        await send_response(update, msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚀 LAUNCH QUIZ", callback_data="cmd_quiz")]]))
        return

    total_count = len(saved)
    lines = [
        f"📖 SAVED QUESTIONS BOOKMARK",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 Total Saved Questions: {total_count}",
        f"📌 Showing most recent bookmarks first\n"
    ]

    for idx, sq in enumerate(saved[:15], start=1):
        opts_list = json.loads(sq['options_json']) if sq['options_json'] else []
        corr_idx = sq['correct_option']
        corr_ans = opts_list[corr_idx] if 0 <= corr_idx < len(opts_list) else 'N/A'
        
        lines.append(
            f"{idx}. Saved At: {sq['saved_at']}\n"
            f"❓ Q: {sq['question_text']}\n"
            f"✅ Correct Answer: {corr_ans}\n"
            f"💡 Explanation: {sq['explanation']}\n"
            f"──────────────────────────────"
        )

    if total_count > 15:
        lines.append(f"\n(Showing 15 of {total_count} saved questions)")

    msg = "\n".join(lines)
    buttons = [
        [InlineKeyboardButton("📌 EXPORT SAVED QUESTIONS PDF", callback_data="studentpdf_saved_qs")],
        [InlineKeyboardButton("🚀 LAUNCH QUIZ", callback_data="cmd_quiz")]
    ]
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
        f"👤 STUDENT PROFILE CARD\n"
        f"📚 Learn with HiM Quiz Book\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• Full Name: {profile['full_name']}\n"
        f"• Student ID: {student_id}\n"
        f"• Telegram ID: {profile['user_id']}\n"
        f"• Target Exam: {profile['target_exam']}\n"
        f"• DOB / Gender: {profile.get('dob', 'N/A')} / {profile['gender']}\n"
        f"• Location: {profile.get('state', 'N/A')}, India\n"
        f"• Phone: {profile['phone_number']} (Private)\n\n"
        f"📊 DAILY QUOTA STATUS (00:00 - 23:59):\n"
        f"• Used Today: {today_used} / {allowed_limit} Qs\n"
        f"• Remaining Today: {remaining} Qs\n"
        f"• Referrals: {profile.get('referral_count', 0)} / 4 friends\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    buttons = [
        [InlineKeyboardButton("📄 EXPORT PDF REPORTS", callback_data="cmd_pdfreport"), InlineKeyboardButton("💾 SAVED QUESTIONS", callback_data="cmd_savedquestions")],
        [InlineKeyboardButton("🚀 LAUNCH QUIZ", callback_data="cmd_quiz"), InlineKeyboardButton("✏️ EDIT PROFILE", callback_data="cmd_editprofile")],
        [InlineKeyboardButton("🤝 INVITE (+10 QUOTA)", callback_data="cmd_referral")]
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
        f"🎓 ACADEMIC REPORT CARD\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Name: {profile['full_name']}\n"
        f"🪪 Student ID: {student_id}\n"
        f"🎯 Target Exam: {profile['target_exam']}\n"
        f"📍 Location: {profile.get('state', 'N/A')}, India\n\n"
        f"📈 PERFORMANCE METRICS:\n"
        f"• Tests Completed: {perf.get('total_tests', 0)}\n"
        f"• Questions Attempted: {perf.get('total_qs', 0)}\n"
        f"• Global Rank: {rank}\n"
        f"• Overall Percentile: {percentile}%"
    )

    buttons = [
        [InlineKeyboardButton("📄 EXPORT PDF REPORT", callback_data="cmd_pdfreport")],
        [InlineKeyboardButton("🚀 LAUNCH QUIZ", callback_data="cmd_quiz"), InlineKeyboardButton("🏆 LEADERBOARD", callback_data="cmd_toppers")]
    ]
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
        lines.append(f"{idx}. {t['full_name']}{badge} — Avg Score: {round(t['avg_score'], 2)}")

    msg = "🏆 GLOBAL SCHOLAR LEADERBOARD\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n" + "\n".join(lines)
    buttons = [[InlineKeyboardButton("🚀 ATTEMPT QUIZ", callback_data="cmd_quiz")]]
    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)

    keyboard = [
        [InlineKeyboardButton("🌟 10/10 Bot! The quizzes are top quality 🚀", callback_data="fb_p1")],
        [InlineKeyboardButton("✨ Learn with HiM is the best preparation portal 🎓", callback_data="fb_p2")],
        [InlineKeyboardButton("🔥 Daily target limits keep me disciplined! 📈", callback_data="fb_p3")],
        [InlineKeyboardButton("✍️ Write Custom Feedback", callback_data="fb_custom")],
        [InlineKeyboardButton("📖 View Student Reviews", callback_data="cmd_viewfeedbacks")]
    ]

    msg = (
        "💬 STUDENT FEEDBACK PORTAL\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Your review helps improve Learn with HiM Quiz Book!\n"
        "Select a quick rating below or reply with custom feedback:"
    )
    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(keyboard))

async def viewfeedbacks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)
    feedbacks = get_all_student_feedbacks(limit=15)

    if not feedbacks:
        await send_response(update, "📖 No student reviews submitted yet. Use /feedback to submit a review!")
        return

    lines = ["📖 STUDENT REVIEWS & FEEDBACK BOARD\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"]
    for idx, fb in enumerate(feedbacks, start=1):
        lines.append(f"{idx}. {fb['full_name']}:\n 💬 \"{fb['feedback_text']}\"\n")

    await send_response(update, "\n".join(lines))

async def referral_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)
    bot_username = context.bot.username
    ref_link = f"https://t.me/{bot_username}?start=ref_{user.id}"

    msg = (
        f"🤝 INVITE FRIENDS & UNLOCK +10 QUOTA\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Share your personal invite link with 4 friends:\n"
        f"{ref_link}\n\n"
        f"When 4 friends complete registration, +10 bonus quota is added automatically!"
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
            await query.answer("🛑 Daily Limit Exhausted! /quiz is locked.", show_alert=True)
            return
        await launch_quiz_setup(update, context)
    elif data == "cmd_pause_quiz":
        await pause_quiz_command(update, context)
    elif data == "cmd_resume_quiz":
        await resume_quiz_command(update, context)
    elif data == "cmd_stop_quiz":
        await stop_quiz_command(update, context)
    elif data == "cmd_pdfreport":
        await pdfreport_command(update, context)
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
            "fb_p1": "10/10 Bot! The quizzes are top quality 🚀",
            "fb_p2": "Learn with HiM is the best preparation portal 🎓",
            "fb_p3": "Daily target limits keep me disciplined! 📈"
        }
        fb_text = presets.get(data, "Great educational bot!")
        profile = get_user_profile(user.id)
        name = profile.get("full_name") if profile else user.full_name
        save_student_feedback(user.id, name, fb_text)
        await query.edit_message_text(f"🎉 Thank you, {name}! Your feedback has been recorded:\n\n💬 \"{fb_text}\"")

    elif data == "fb_custom":
        context.user_data["awaiting_custom_feedback"] = True
        await query.edit_message_text("✍️ Please reply with your custom feedback below:")

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    if context.user_data.get("is_account_locked"):
        profile = get_user_profile(user.id)
        if profile and profile.get("pin") == text:
            context.user_data["is_account_locked"] = False
            refresh_user_activity_epoch(user.id)
            await update.message.reply_text("🔓 ACCOUNT UNLOCKED\n\nYou can continue practicing!", reply_markup=ReplyKeyboardRemove())
        else:
            rec_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 RESET PIN", callback_data="login_forgot_pin")]])
            await update.message.reply_text(
                "❌ Incorrect PIN! Try again or reset your PIN below:",
                reply_markup=rec_btn
            )
        return

    if not await maintenance_guard(update, context): return
    log_user_activity_time(user.id, seconds=10)

    if user.id == PRIMARY_ADMIN_ID and context.user_data.get("awaiting_admin_search"):
        context.user_data["awaiting_admin_search"] = False
        all_u = get_all_users()
        matches = [
            u for u in all_u if text.lower() in str(u.get("student_id", "")).lower() 
            or text.lower() in str(u.get("phone_number", "")).lower() 
            or text.lower() in str(u.get("full_name", "")).lower()
        ]

        if not matches:
            await update.message.reply_text(f"⚠️ No student found matching query: {text}")
            return

        keyboard = []
        for m in matches[:10]:
            sid = m.get("student_id") or f"USER_{m['user_id']}"
            keyboard.append([InlineKeyboardButton(f"👤 {m['full_name']} (ID: {sid})", callback_data=f"admin_inspect_u_{m['user_id']}")])
        
        await update.message.reply_text(f"🔍 Search Results for '{text}':", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if context.user_data.get("awaiting_custom_feedback"):
        context.user_data["awaiting_custom_feedback"] = False
        
        if any(bad_word in text.lower() for bad_word in NEGATIVE_WORDS):
            await update.message.reply_text("🙏 Thank you for your feedback! We are constantly improving.", reply_markup=ReplyKeyboardRemove())
            return

        profile = get_user_profile(user.id)
        name = profile.get("full_name") if profile else user.full_name
        save_student_feedback(user.id, name, text)
        await update.message.reply_text(f"🎉 Feedback Received! Thank you {name}:\n\n💬 \"{text}\"", reply_markup=ReplyKeyboardRemove())
        return

    if context.user_data.get("awaiting_broadcast"):
        context.user_data["awaiting_broadcast"] = False
        users = get_all_users()
        sent = 0
        for u in users:
            try:
                await context.bot.send_message(chat_id=u['user_id'], text=f"📢 ANNOUNCEMENT FROM HIMANSHU SIR\n\n{text}")
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
        BotCommand("quiz", "🚀 Start Computer Quiz"),
        BotCommand("pause", "⏸ Pause Running Quiz"),
        BotCommand("resume", "▶️ Resume Paused Quiz"),
        BotCommand("stop", "🛑 Stop Quiz Session"),
        BotCommand("pdfreport", "📄 Export Academic PDF Report"),
        BotCommand("savedquestions", "💾 View Saved Questions"),
        BotCommand("myprofile", "👤 View Student Profile"),
        BotCommand("editprofile", "✏️ Edit Profile Details"),
        BotCommand("mywholestate", "📊 View Performance & Rank"),
        BotCommand("toppername", "🏆 Scholar Leaderboard"),
        BotCommand("feedback", "💬 Submit Feedback"),
        BotCommand("reviews", "📖 View Student Reviews"),
        BotCommand("invite", "🤝 Invite Friends (+10 Limit)")
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
    app.add_handler(CommandHandler("pdfreport", pdfreport_command))
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
    
    app.add_handler(CommandHandler("admin", admin_portal_command))
    app.add_handler(CommandHandler("admit", admin_portal_command))
    app.add_handler(CommandHandler("user_profiles", admin_portal_command))

    app.add_handler(CallbackQueryHandler(quiz_count_callback, pattern="^qcount_"))
    app.add_handler(CallbackQueryHandler(quiz_timer_callback, pattern="^qtimer_"))
    app.add_handler(CallbackQueryHandler(student_pdf_callback, pattern="^studentpdf_"))
    app.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^(admin_|audit_|genpdf_)"))
    app.add_handler(CallbackQueryHandler(button_router, pattern="^cmd_|^fb_|^trigger_start"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
    app.add_handler(PollAnswerHandler(handle_poll_answer))
    app.add_error_handler(global_error_handler)

    return app