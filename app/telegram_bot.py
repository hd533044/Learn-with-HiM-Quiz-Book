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
    clear_paused_quiz_state, touch_user_activity, verify_custom_password,
    get_student_credentials_by_phone
)
from app.auth_engine import strict_authentication_guard
from  app.onboarding import get_onboarding_handler
from app.quiz_engine import (
    launch_quiz_setup, quiz_count_callback, quiz_timer_callback, handle_poll_answer,
    pause_quiz_command, resume_quiz_command
)
from app.stats import get_overall_leaderboard, calculate_user_percentile, calculate_user_rank, get_user_performance_summary
from app.admin import admin_portal_command, admin_callback_handler

NEGATIVE_WORDS = ["bad", "worst", "useless", "trash", "fake", "hate", "terrible", "waste", "horrible", "fraud", "stupid", "scam"]

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
    if not await strict_authentication_guard(update, context): return

    msg = (
        "🤖 **LEARN WITH HIM QUIZ BOOK — COMMAND DIRECTORY**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Tap any button below or open the blue **[≡ Menu]** button:\n\n"
        "• 🚀 **/quiz**: Start computer quiz session\n"
        "• ⏸ **/pause**: Pause running quiz\n"
        "• ▶️ **/resume**: Resume paused quiz\n"
        "• 👤 **/myprofile**: View student credentials & ID\n"
        "• ✏️ **/editprofile**: Update profile details (1x / 30 days)\n"
        "• 📊 **/mywholestate**: View detailed rank & percentile\n"
        "• 🏆 **/toppername**: Inspect global scholar leaderboard\n"
        "• 💬 **/feedback**: Submit feedback\n"
        "• 📖 **/reviews**: View student reviews\n"
        "• 🤝 **/invite**: Personal referral link"
    )

    buttons = [
        [InlineKeyboardButton("🚀 /quiz", callback_data="cmd_quiz"), InlineKeyboardButton("👤 /myprofile", callback_data="cmd_profile")],
        [InlineKeyboardButton("✏️ /editprofile", callback_data="cmd_editprofile"), InlineKeyboardButton("📊 /mywholestate", callback_data="cmd_wholestate")],
        [InlineKeyboardButton("🏆 /toppername", callback_data="cmd_toppers"), InlineKeyboardButton("💬 /feedback", callback_data="cmd_feedback")],
        [InlineKeyboardButton("🤝 /invite", callback_data="cmd_referral"), InlineKeyboardButton("📖 /reviews", callback_data="cmd_viewfeedbacks")]
    ]

    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def myprofile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await strict_authentication_guard(update, context): return
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
        f"📅 **Login Tracking Metrics:**\n"
        f"• **Last Login Timestamp:** `{profile.get('last_login_timestamp', 'N/A')}`\n"
        f"• **Last Login Date:** `{profile.get('last_login_date', 'N/A')}`\n"
        f"• **Last Active Epoch:** `{profile.get('last_active_epoch', 'N/A')}`\n\n"
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
    if not await strict_authentication_guard(update, context): return
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
    if not await strict_authentication_guard(update, context): return
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
    if not await strict_authentication_guard(update, context): return

    keyboard = [
        [InlineKeyboardButton("🌟 10/10 Bot! Top quality 🚀", callback_data="fb_p1")],
        [InlineKeyboardButton("✨ Learn with HiM is top class 🎓", callback_data="fb_p2")],
        [InlineKeyboardButton("✍️ Write Custom Feedback", callback_data="fb_custom")],
        [InlineKeyboardButton("📖 View Student Reviews", callback_data="cmd_viewfeedbacks")]
    ]

    msg = "💬 **STUDENT FEEDBACK PORTAL**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nSelect a preset rating or write custom feedback:"
    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(keyboard))

async def viewfeedbacks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await strict_authentication_guard(update, context): return
    feedbacks = get_all_student_feedbacks(limit=15)

    if not feedbacks:
        await send_response(update, "📖 No student reviews submitted yet.")
        return

    lines = ["📖 **STUDENT REVIEWS BOARD**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"]
    for idx, fb in enumerate(feedbacks, start=1):
        lines.append(f"**{idx}. {fb['full_name']}**:\n 💬 *\"{fb['feedback_text']}\"*\n")

    await send_response(update, "\n".join(lines))

async def referral_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await strict_authentication_guard(update, context): return
    user = update.effective_user
    bot_username = context.bot.username
    ref_link = f"https://t.me/{bot_username}?start=ref_{user.id}"

    msg = f"🤝 **INVITE FRIENDS**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nYour invite link:\n`{ref_link}`"
    await send_response(update, msg)

async def handle_forgot_credentials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    contact_btn = KeyboardButton(text="📱 Share Contact to Recover Credentials", request_contact=True)
    markup = ReplyKeyboardMarkup([[contact_btn]], one_time_keyboard=True, resize_keyboard=True)

    await context.bot.send_message(
        chat_id=query.from_user.id,
        text="🔑 **CREDENTIAL RECOVERY**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nClick **Share Contact** to verify mobile and view credentials:",
        reply_markup=markup,
        parse_mode="Markdown"
    )

async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data == "cmd_forgot_credentials":
        await handle_forgot_credentials(update, context)
        return

    if not await strict_authentication_guard(update, context): return

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
        profile = get_user_profile(user.id)
        name = profile.get("full_name") if profile else user.full_name
        save_student_feedback(user.id, name, "Great educational portal!")
        await query.edit_message_text(f"🎉 **Thank you, {name}!** Feedback recorded.", parse_mode="Markdown")
    elif data == "fb_custom":
        context.user_data["awaiting_custom_feedback"] = True
        await query.edit_message_text("✍️ Reply with your custom feedback below:")

async def handle_text_and_contact_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.message
    if not message:
        return

    # 1. Contact Sharing Verification
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
                f"🔑 **Your Custom Password:** `{record['login_pass']}`\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚡ Session unlocked! Tap **/quiz** to continue.",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode="Markdown"
            )
        else:
            await message.reply_text(
                "❌ **RECOVERY FAILED!**\n\nNo registered account matched this phone number.\nType /start to register.",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode="Markdown"
            )
        return

    text = message.text.strip() if message.text else ""
    if text.startswith("/"):
        return

    # 2. Custom Password Verification Entry (Case Sensitive)
    if verify_custom_password(user.id, text):
        await message.reply_text(
            f"🎉 **PASSWORD VERIFIED — SESSION UNLOCKED!**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Welcome back! Login timestamp recorded.\n\n"
            f"👉 Tap **/quiz** or **/myprofile** to access portal features!",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        return

    if not await strict_authentication_guard(update, context): return

    if context.user_data.get("awaiting_custom_feedback"):
        context.user_data["awaiting_custom_feedback"] = False
        if any(bad_word in text.lower() for bad_word in NEGATIVE_WORDS):
            await message.reply_text("🙏 Thank you for your feedback!")
            return

        profile = get_user_profile(user.id)
        name = profile.get("full_name") if profile else user.full_name
        save_student_feedback(user.id, name, text)
        await message.reply_text(f"🎉 **Feedback Received!** Thank you *{name}*!", parse_mode="Markdown")

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

    # 1. Onboarding conversation handler first
    app.add_handler(get_onboarding_handler())
    
    # 2. Text & Contact handler for passwords and phone recovery (BEFORE slash commands)
    app.add_handler(MessageHandler((filters.CONTACT | filters.TEXT) & ~filters.COMMAND, handle_text_and_contact_messages))
    
    # 3. Slash commands
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

    # 4. Callback Query / Button routers
    app.add_handler(CallbackQueryHandler(quiz_count_callback, pattern="^qcount_"))
    app.add_handler(CallbackQueryHandler(quiz_timer_callback, pattern="^qtimer_"))
    app.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^admin_"))
    app.add_handler(CallbackQueryHandler(button_router, pattern="^cmd_|^fb_"))

    # 5. Quiz Poll answer handler & Global Error handler
    app.add_handler(PollAnswerHandler(handle_poll_answer))
    app.add_error_handler(global_error_handler)

    return app