import time
import logging
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
    get_all_users, get_today_attempts, save_student_feedback, get_all_student_feedbacks
)
from app.onboarding import get_onboarding_handler
from app.quiz_engine import launch_quiz_setup, quiz_count_callback, quiz_timer_callback, handle_poll_answer
from app.stats import get_overall_leaderboard, calculate_user_percentile, calculate_user_rank, get_user_performance_summary
from app.admin import admin_portal_command, admin_callback_handler

NEGATIVE_WORDS = ["bad", "worst", "useless", "trash", "fake", "hate", "terrible", "waste", "horrible", "fraud", "stupid", "scam"]

async def maintenance_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    m_until = get_maintenance_until()
    if int(time.time()) < m_until:
        remaining_sec = m_until - int(time.time())
        mins_left = max(1, (remaining_sec + 59) // 60)
        msg = f"🛠 **ADMIN HAS PAUSED THE SERVICE CURRENTLY**\nService will resume in approximately `{mins_left} mins`. Please try again later!"
        
        if update.callback_query:
            await update.callback_query.answer(f"🛠 Service Paused! Resuming in ~{mins_left} mins.", show_alert=True)
        elif update.message:
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
        return False
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
    if not await maintenance_guard(update, context): return

    msg = (
        "🤖 **LEARN WITH HIM QUIZ BOOK — COMMAND DIRECTORY**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Tap any button below or open the blue **[≡ Menu]** button:\n\n"
        "• 🚀 **/quiz**: Start a new custom computer quiz\n"
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
    if not await maintenance_guard(update, context): return
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
    if not await maintenance_guard(update, context): return
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
        f"🎯 **Target Exam:** `{profile['target_exam']}`\n"
        f"📍 **Location:** `{profile.get('state', 'N/A')}, {profile.get('country', 'India')}`\n\n"
        f"📈 **Performance Metrics:**\n"
        f"• **Tests Completed:** `{perf.get('total_tests', 0)}`\n"
        f"• **Total Quizzes Attempted:** `{perf.get('total_tests', 0)}` till date\n"
        f"• **Questions Attempted:** `{perf.get('total_qs', 0)}`\n"
        f"• **Global Rank:** `{rank}`\n"
        f"• **Overall Percentile:** `{percentile}%` *(Calculated against all registered students)*"
    )

    buttons = [[InlineKeyboardButton("🚀 Launch Quiz", callback_data="cmd_quiz"), InlineKeyboardButton("🏆 Leaderboard", callback_data="cmd_toppers")]]
    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def toppers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
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
    if not await maintenance_guard(update, context): return

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
    if not await maintenance_guard(update, context): return
    feedbacks = get_all_student_feedbacks(limit=15)

    if not feedbacks:
        await send_response(update, "📖 **No student reviews submitted yet.**\nBe the first to leave feedback using /feedback!")
        return

    lines = ["📖 **STUDENT REVIEWS & APPRECIATION BOARD**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"]
    for idx, fb in enumerate(feedbacks, start=1):
        lines.append(f"**{idx}. {fb['full_name']}**:\n💬 *\"{fb['feedback_text']}\"*\n")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n💡 *Submit your own review anytime using /feedback!*")
    await send_response(update, "\n".join(lines))

async def referral_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
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

async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return

    query = update.callback_query
    data = query.data
    user = query.from_user

    if data == "cmd_quiz":
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

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return

    user = update.effective_user
    text = update.message.text.strip()

    if context.user_data.get("awaiting_custom_feedback"):
        context.user_data["awaiting_custom_feedback"] = False
        
        if any(bad_word in text.lower() for bad_word in NEGATIVE_WORDS):
            await update.message.reply_text("🙏 Thank you for your feedback! We are constantly working hard to improve your experience.", reply_markup=ReplyKeyboardRemove())
            return

        profile = get_user_profile(user.id)
        name = profile.get("full_name") if profile else user.full_name
        save_student_feedback(user.id, name, text)
        await update.message.reply_text(f"🎉 **Feedback Received!** Thank you *{name}* for your kind words:\n\n💬 *\"{text}\"*", reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
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

    app.add_handler(get_onboarding_handler())
    
    app.add_handler(CommandHandler("quiz", launch_quiz_setup))
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

    app.add_handler(CallbackQueryHandler(quiz_count_callback, pattern="^qcount_"))
    app.add_handler(CallbackQueryHandler(quiz_timer_callback, pattern="^qtimer_"))
    app.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^admin_"))
    app.add_handler(CallbackQueryHandler(button_router, pattern="^cmd_|^fb_"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
    app.add_handler(PollAnswerHandler(handle_poll_answer))
    app.add_error_handler(global_error_handler)

    return app