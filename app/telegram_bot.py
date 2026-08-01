import time
import logging
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, 
    BotCommand, BotCommandScopeDefault, BotCommandScopeAllPrivateChats, 
    BotCommandScopeAllGroupChats, ReplyKeyboardRemove, ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, PollAnswerHandler, 
    MessageHandler, filters, ContextTypes
)
from app.config import BOT_TOKEN, PRIMARY_ADMIN_ID, DAILY_QUESTION_LIMIT
from app.database import (
    init_db, get_maintenance_until, get_user_profile, 
    get_all_users, get_today_attempts, save_student_feedback, get_all_student_feedbacks,
    clear_paused_quiz_state, is_user_session_expired, touch_user_activity, verify_password_only,
    get_student_credentials_by_phone
)
from app.onboarding import get_onboarding_handler
from app.quiz_engine import (
    launch_quiz_setup, quiz_count_callback, quiz_timer_callback, handle_poll_answer,
    pause_quiz_command, resume_quiz_command
)
from app.stats import get_overall_leaderboard, calculate_user_percentile, calculate_user_rank, get_user_performance_summary
from app.admin import admin_portal_command, admin_callback_handler

NEGATIVE_WORDS = ["bad", "worst", "useless", "trash", "fake", "hate", "terrible", "waste", "horrible", "fraud", "stupid", "scam"]

async def session_and_maintenance_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user:
        return True

    # 1. Maintenance Check
    m_until = get_maintenance_until()
    if int(time.time()) < m_until and user.id != PRIMARY_ADMIN_ID:
        remaining_sec = m_until - int(time.time())
        mins_left = max(1, (remaining_sec + 59) // 60)
        msg = f"🛠 **ADMIN HAS PAUSED THE SERVICE CURRENTLY**\nService will resume in approximately `{mins_left} mins`. Please try again later!"
        if update.callback_query:
            await update.callback_query.answer(f"🛠 Service Paused! Resuming in ~{mins_left} mins.", show_alert=True)
        elif update.message:
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
        return False

    # 2. Inactivity Session Guard (1 Minute Timeout)
    profile = get_user_profile(user.id)
    if profile and profile.get("is_verified"):
        if is_user_session_expired(user.id):
            login_msg = (
                "🔒 **SESSION EXPIRED (1+ Minute Inactive)**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "For account security, enter your **4-Character Password** to unlock:\n\n"
                "👉 **Reply with your 4-Character Password below:**\n"
                "*(Example: `A9K2`)*\n\n"
                "💡 *Forgot password? Tap below to recover via mobile!*"
            )
            buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔑 Recover Password via Phone", callback_data="cmd_forgot_credentials")]
            ])
            
            if update.callback_query:
                await update.callback_query.answer("🔒 Session Expired! Enter Password.", show_alert=True)
                await context.bot.send_message(chat_id=user.id, text=login_msg, reply_markup=buttons, parse_mode="Markdown")
            elif update.message:
                await update.message.reply_text(login_msg, reply_markup=buttons, parse_mode="Markdown")
            return False

        touch_user_activity(user.id)

    return True

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
    if not await session_and_maintenance_guard(update, context): return

    msg = (
        "🤖 **LEARN WITH HIM QUIZ BOOK — COMMAND DIRECTORY**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Tap any button below or open the blue **[≡ Menu]** button:\n\n"
        "• 🚀 **/quiz**: Start a new custom computer quiz\n"
        "• ⏸ **/pause**: Pause running quiz\n"
        "• ▶️ **/resume**: Resume paused quiz\n"
        "• 👤 **/myprofile**: View your verified student card\n"
        "• ✏️ **/editprofile**: Update profile details (1x / 30 days)\n"
        "• 📊 **/mywholestate**: View detailed rank & percentile\n"
        "• 🏆 **/toppername**: Inspect the global scholar leaderboard\n"
        "• 💬 **/feedback**: Rate the bot or leave feedback\n"
        "• 📖 **/reviews**: View student reviews\n"
        "• 🤝 **/invite**: Share referral link to unlock +10 limit"
    )

    buttons = [
        [InlineKeyboardButton("🚀 /quiz", callback_data="cmd_quiz"), InlineKeyboardButton("👤 /myprofile", callback_data="cmd_profile")],
        [InlineKeyboardButton("✏️ /editprofile", callback_data="cmd_editprofile"), InlineKeyboardButton("📊 /mywholestate", callback_data="cmd_wholestate")],
        [InlineKeyboardButton("🏆 /toppername", callback_data="cmd_toppers"), InlineKeyboardButton("💬 /feedback", callback_data="cmd_feedback")],
        [InlineKeyboardButton("🤝 /invite", callback_data="cmd_referral"), InlineKeyboardButton("📖 /reviews", callback_data="cmd_viewfeedbacks")]
    ]

    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def myprofile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await session_and_maintenance_guard(update, context): return
    user = update.effective_user
    profile = get_user_profile(user.id)

    if not profile:
        await send_response(update, "⚠️ Please type /start to create your student profile first!")
        return

    today_used = get_today_attempts(user.id)
    allowed_limit = DAILY_QUESTION_LIMIT + profile.get("bonus_quota", 0)
    remaining = max(0, allowed_limit - today_used)

    msg = (
        f"👤 **STUDENT PROFILE CARD**\n"
        f"📚 *Learn with HiM Quiz Book*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 **Student ID:** `{profile.get('student_id', 'N/A')}`\n"
        f"🔑 **Your Password:** `{profile.get('login_pass', 'N/A')}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• **Full Name:** {profile['full_name']}\n"
        f"• **Telegram ID:** `{profile['user_id']}`\n"
        f"• **Target Exam:** `{profile['target_exam']}`\n"
        f"• **Age / Gender:** `{profile['age']}` / `{profile['gender']}`\n"
        f"• **Location:** `{profile.get('state', 'N/A')}, {profile.get('country', 'India')}`\n"
        f"• **Phone:** `{profile['phone_number']}` *(Private)*\n\n"
        f"📊 **Daily Quota Status:**\n"
        f"• **Used Today:** `{today_used}` / `{allowed_limit}` Qs\n"
        f"• **Remaining Today:** `{remaining}` Qs\n"
        f"• **Referrals:** `{profile.get('referral_count', 0)}` / 4 friends\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    buttons = [
        [InlineKeyboardButton("🚀 Launch Quiz", callback_data="cmd_quiz"), InlineKeyboardButton("📊 Whole State", callback_data="cmd_wholestate")],
        [InlineKeyboardButton("✏️ Edit Profile", callback_data="cmd_editprofile"), InlineKeyboardButton("🤝 Invite (+10 Quota)", callback_data="cmd_referral")]
    ]

    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def wholestate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await session_and_maintenance_guard(update, context): return
    user = update.effective_user
    profile = get_user_profile(user.id)
    
    if not profile:
        await send_response(update, "⚠️ Please type /start to register first!")
        return

    perf = get_user_performance_summary(user.id)
    rank = calculate_user_rank(user.id)
    percentile = calculate_user_percentile(user.id)

    msg = (
        f"🎓 **STUDENT ACADEMIC REPORT CARD**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 **Name:** {profile['full_name']}\n"
        f"🆔 **Student ID:** `{profile.get('student_id', 'N/A')}`\n"
        f"🎯 **Target Exam:** `{profile['target_exam']}`\n"
        f"📍 **Location:** `{profile.get('state', 'N/A')}, {profile.get('country', 'India')}`\n\n"
        f"📈 **Performance Metrics:**\n"
        f"• **Tests Completed:** `{perf.get('total_tests', 0)}`\n"
        f"• **Questions Attempted:** `{perf.get('total_qs', 0)}`\n"
        f"• **Global Rank:** `{rank}`\n"
        f"• **Overall Percentile:** `{percentile}%`"
    )

    buttons = [[InlineKeyboardButton("🚀 Launch Quiz", callback_data="cmd_quiz"), InlineKeyboardButton("🏆 Leaderboard", callback_data="cmd_toppers")]]
    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def toppers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await session_and_maintenance_guard(update, context): return
    toppers = get_overall_leaderboard(limit=10)
    
    if not toppers:
        await send_response(update, "🏆 No leaderboard records available yet. Be the first to attempt a quiz!")
        return

    lines = []
    for idx, t in enumerate(toppers, start=1):
        badge = " 🥇" if idx == 1 else " 🥈" if idx == 2 else " 🥉" if idx == 3 else ""
        lines.append(f"{idx}. **{t['full_name']}**{badge} — Avg Score: `{round(t['avg_score'], 2)}`")

    msg = "🏆 **GLOBAL SCHOLAR LEADERBOARD**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n" + "\n".join(lines)
    buttons = [[InlineKeyboardButton("🚀 Attempt Quiz", callback_data="cmd_quiz")]]
    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await session_and_maintenance_guard(update, context): return

    keyboard = [
        [InlineKeyboardButton("🌟 10/10 Bot! The quizzes are top quality 🚀", callback_data="fb_p1")],
        [InlineKeyboardButton("✨ Learn with HiM is the best preparation portal 🎓", callback_data="fb_p2")],
        [InlineKeyboardButton("🔥 Daily target limits keep me disciplined! 📈", callback_data="fb_p3")],
        [InlineKeyboardButton("✍️ Write Custom Feedback", callback_data="fb_custom")],
        [InlineKeyboardButton("📖 View Student Reviews", callback_data="cmd_viewfeedbacks")]
    ]

    msg = (
        "💬 **STUDENT FEEDBACK PORTAL**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Your feedback helps Himanshu Sir make this platform even better!\n"
        "Select a quick preset rating below or write your own custom feedback:"
    )
    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(keyboard))

async def viewfeedbacks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await session_and_maintenance_guard(update, context): return
    feedbacks = get_all_student_feedbacks(limit=15)

    if not feedbacks:
        await send_response(update, "📖 No student reviews submitted yet. Be the first to leave feedback using /feedback!")
        return

    lines = ["📖 **STUDENT REVIEWS & FEEDBACK BOARD**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"]
    for idx, fb in enumerate(feedbacks, start=1):
        lines.append(f"**{idx}. {fb['full_name']}**:\n 💬 *\"{fb['feedback_text']}\"*\n")

    await send_response(update, "\n".join(lines))

async def referral_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await session_and_maintenance_guard(update, context): return
    user = update.effective_user
    bot_username = context.bot.username
    ref_link = f"https://t.me/{bot_username}?start=ref_{user.id}"

    msg = (
        f"🤝 **INVITE FRIENDS & UNLOCK +10 DAILY LIMIT**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Share your personal invite link below with **4 friends**:\n"
        f"`{ref_link}`\n\n"
        f"When 4 friends register using your link, you automatically receive +10 questions added to your daily quota!"
    )
    await send_response(update, msg)

async def handle_forgot_credentials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    contact_btn = KeyboardButton(text="📱 Share Contact to Recover Password", request_contact=True)
    markup = ReplyKeyboardMarkup([[contact_btn]], one_time_keyboard=True, resize_keyboard=True)

    await context.bot.send_message(
        chat_id=query.from_user.id,
        text="🔑 **CREDENTIAL RECOVERY**\n"
             "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
             "Please click the **Share Contact** button below to verify your phone number and view your Password:",
        reply_markup=markup,
        parse_mode="Markdown"
    )

async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data == "cmd_forgot_credentials":
        await handle_forgot_credentials(update, context)
        return

    if not await session_and_maintenance_guard(update, context): return

    user = query.from_user
    if data == "cmd_quiz":
        await launch_quiz_setup(update, context)
    elif data == "cmd_pause_quiz":
        await pause_quiz_command(update, context)
    elif data == "cmd_resume_quiz":
        await resume_quiz_command(update, context)
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
        await query.edit_message_text(f"🎉 **Thank you, {name}!** Your feedback has been recorded:\n\n💬 *\"{fb_text}\"*", parse_mode="Markdown")
    elif data == "fb_custom":
        context.user_data["awaiting_custom_feedback"] = True
        await query.edit_message_text("✍️ Please reply with your custom feedback/review below:")

async def handle_text_and_contact_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.message
    if not message:
        return

    # 1. OPTION 1 RECOVERY: Handle Contact Verification
    if message.contact:
        phone = message.contact.phone_number
        record = get_student_credentials_by_phone(phone)
        
        if record:
            touch_user_activity(user.id)
            await message.reply_text(
                f"✅ **IDENTITY VERIFIED SUCCESSFULLY!**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 **Student Name:** {record['full_name']}\n"
                f"🆔 **Student ID:** `{record['student_id']}`\n"
                f"🔑 **Your Password:** `{record['login_pass']}`\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚡ Your session is unlocked! Tap **/quiz** to resume.",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode="Markdown"
            )
        else:
            await message.reply_text(
                "❌ **RECOVERY FAILED!**\n\n"
                "No registered student account matched this phone number.\n"
                "Type /start to register.",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode="Markdown"
            )
        return

    # 2. PASSWORD-ONLY UNLOCK CHECK (4-Character String)
    text = message.text.strip() if message.text else ""
    
    # Ignore slash commands so they route directly to CommandHandlers
    if text.startswith("/"):
        return

    if len(text) == 4 and text.isalnum():
        if verify_password_only(user.id, text):
            await message.reply_text(
                f"🎉 **PASSWORD VERIFIED — UNLOCKED!**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Welcome back! Your session is renewed.\n\n"
                f"👉 Tap **/quiz** to start practicing now!",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode="Markdown"
            )
            return
        else:
            await message.reply_text(
                "❌ **INCORRECT PASSWORD!**\n\n"
                "The 4-character password entered is invalid.\n"
                "Please try again or tap below to recover it via phone:",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔑 Recover Password via Phone", callback_data="cmd_forgot_credentials")
                ]]),
                parse_mode="Markdown"
            )
            return

    if not await session_and_maintenance_guard(update, context): return

    if context.user_data.get("awaiting_custom_feedback"):
        context.user_data["awaiting_custom_feedback"] = False
        if any(bad_word in text.lower() for bad_word in NEGATIVE_WORDS):
            await message.reply_text("🙏 Thank you for your feedback! We are constantly working hard to improve your experience.", reply_markup=ReplyKeyboardRemove())
            return

        profile = get_user_profile(user.id)
        name = profile.get("full_name") if profile else user.full_name
        save_student_feedback(user.id, name, text)
        await message.reply_text(f"🎉 **Feedback Received!** Thank you *{name}* for your kind words:\n\n💬 *\"{text}\"*", reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
        return

    if context.user_data.get("awaiting_broadcast"):
        context.user_data["awaiting_broadcast"] = False
        users = get_all_users()
        sent = 0
        for u in users:
            try:
                await context.bot.send_message(chat_id=u['user_id'], text=f"📢 **ANNOUNCEMENT FROM HIMANSHU SIR**\n\n{text}", parse_mode="Markdown")
                sent += 1
            except Exception:
                pass
        await message.reply_text(f"✅ Announcement sent to {sent} users!", reply_markup=ReplyKeyboardRemove())

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
        BotCommand("myprofile", "👤 View Student Profile"),
        BotCommand("editprofile", "✏️ Edit Profile Details"),
        BotCommand("mywholestate", "📊 View Performance & Rank"),
        BotCommand("toppername", "🏆 Global Leaderboard"),
        BotCommand("feedback", "💬 Submit Feedback"),
        BotCommand("reviews", "📖 View Student Reviews"),
        BotCommand("invite", "🤝 Invite Friends (+10 Limit)")
    ]
    
    await application.bot.set_my_commands(allowed_commands, scope=BotCommandScopeDefault())
    await application.bot.set_my_commands(allowed_commands, scope=BotCommandScopeAllPrivateChats())

def build_application() -> Application:
    init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # 1. Onboarding Conversation Handler
    app.add_handler(get_onboarding_handler())
    
    # 2. Command Handlers (Explicitly registered BEFORE generic message handlers)
    app.add_handler(CommandHandler("quiz", launch_quiz_setup))
    app.add_handler(CommandHandler("pause", pause_quiz_command))
    app.add_handler(CommandHandler("resume", resume_quiz_command))
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

    # 3. Callback Queries & Buttons
    app.add_handler(CallbackQueryHandler(quiz_count_callback, pattern="^qcount_"))
    app.add_handler(CallbackQueryHandler(quiz_timer_callback, pattern="^qtimer_"))
    app.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^admin_"))
    app.add_handler(CallbackQueryHandler(button_router, pattern="^cmd_|^fb_"))

    # 4. Generic Text / Contact Message Handler (Filter out command strings using ~filters.COMMAND)
    app.add_handler(MessageHandler((filters.CONTACT | filters.TEXT) & ~filters.COMMAND, handle_text_and_contact_messages))
    
    # 5. Quiz Poll Answers
    app.add_handler(PollAnswerHandler(handle_poll_answer))
    
    app.add_error_handler(global_error_handler)

    return app