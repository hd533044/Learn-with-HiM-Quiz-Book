import time
import logging
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, 
    BotCommand, BotCommandScopeDefault, BotCommandScopeAllPrivateChats, 
    BotCommandScopeAllGroupChats, BotCommandScopeChat,
    MenuButtonCommands, ReplyKeyboardRemove
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
from app.stats import (
    get_overall_leaderboard, calculate_user_percentile, calculate_user_rank, 
    get_user_performance_summary, get_datewise_quiz_history, get_user_badges
)
from app.pdf_generator import generate_profile_book_pdf
from app.admin import admin_portal_command, admin_callback_handler

NEGATIVE_WORDS = ["bad", "worst", "useless", "trash", "fake", "hate", "terrible", "waste", "horrible", "fraud", "stupid", "scam"]

ALLOWED_COMMANDS = [
    BotCommand("quiz", "🚀 Start Computer Quiz"),
    BotCommand("profilebook", "📖 View & Download Profile Stats Book"),
    BotCommand("myprofile", "👤 View Student Profile"),
    BotCommand("editprofile", "✏️ Edit Profile Details"),
    BotCommand("mywholestate", "📊 View Performance & Rank"),
    BotCommand("toppername", "🏆 Global Leaderboard"),
    BotCommand("feedback", "💬 Submit Feedback"),
    BotCommand("reviews", "📖 View Student Reviews"),
    BotCommand("invite", "🤝 Invite Friends (+10 Limit)")
]

async def sync_user_chat_menu(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """FORCES Telegram to sync the left-side [≡ Menu] button for a specific user chat instantly."""
    try:
        await context.bot.set_my_commands(ALLOWED_COMMANDS, scope=BotCommandScopeChat(chat_id=chat_id))
        await context.bot.set_chat_menu_button(chat_id=chat_id, menu_button=MenuButtonCommands())
    except Exception as e:
        logging.warning(f"Note on chat menu sync: {e}")

async def maintenance_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """STRICT MAINTENANCE GUARD: Hard blocks ALL user commands & callbacks if bot is paused."""
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
    if update.effective_chat:
        await sync_user_chat_menu(context, update.effective_chat.id)

    msg = (
        "🤖 **LEARN WITH HIM QUIZ BOOK — DIRECTORY**\n"
        "⚡ *Powered by @LearnwithHiM*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "• 🚀 **/quiz** — Start a new computer quiz\n"
        "• 📖 **/profilebook** — View & download Profile Stats Book\n"
        "• 👤 **/myprofile** — View verified student card\n"
        "• ✏️ **/editprofile** — Update profile details (1x / 30 days)\n"
        "• 📊 **/mywholestate** — Detailed rank & percentile\n"
        "• 🏆 **/toppername** — Global scholar leaderboard\n"
        "• 💬 **/feedback** — Rate bot or leave feedback\n"
        "• 📖 **/reviews** — View student reviews\n"
        "• 🤝 **/invite** — Invite friends to unlock +10 limit"
    )

    buttons = [
        [InlineKeyboardButton("📖 Profile Book (/profilebook)", callback_data="cmd_profilebook"), InlineKeyboardButton("🚀 Start Quiz (/quiz)", callback_data="cmd_quiz")],
        [InlineKeyboardButton("📊 My Stats (/mywholestate)", callback_data="cmd_wholestate"), InlineKeyboardButton("🏆 Leaderboard (/toppername)", callback_data="cmd_toppers")],
        [InlineKeyboardButton("💬 Feedback (/feedback)", callback_data="cmd_feedback"), InlineKeyboardButton("📖 Reviews (/reviews)", callback_data="cmd_viewfeedbacks")],
        [InlineKeyboardButton("✏️ Edit Profile (/editprofile)", callback_data="cmd_editprofile"), InlineKeyboardButton("🤝 Invite Friends (/invite)", callback_data="cmd_referral")]
    ]

    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def profilebook_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if update.effective_chat:
        await sync_user_chat_menu(context, update.effective_chat.id)

    user = update.effective_user
    profile = get_user_profile(user.id)

    if not profile:
        await send_response(update, "⚠️ Please type /start to register first!")
        return

    perf = get_user_performance_summary(user.id)
    history = get_datewise_quiz_history(user.id)
    badges = get_user_badges(user.id)
    rank = calculate_user_rank(user.id)
    percentile = calculate_user_percentile(user.id)

    badge_str = "\n".join([f"  • {b}" for b in badges])
    
    history_lines = []
    if history:
        for h in history[:5]:
            history_lines.append(f"  • `{h['date']}`: `{h['tests']}` Quizzes | Avg Score: `{h['avg_score']}`")
        hist_str = "\n".join(history_lines)
    else:
        hist_str = "  • *No quiz attempts recorded yet.*"

    msg = (
        f"📖 **STUDENT PROFILE STATS BOOK**\n"
        f"⚡ *Powered by @LearnwithHiM*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 **Student Personal Card**\n"
        f"  • **Name:** {profile['full_name']}\n"
        f"  • **Target Exam:** `{profile['target_exam']}`\n"
        f"  • **Gender / Age:** `{profile['gender']}` / `{profile['age']}`\n"
        f"  • **Location:** `{profile.get('state', 'N/A')}, {profile.get('country', 'India')}`\n\n"
        f"🏆 **Earned Scholar Badges**\n"
        f"{badge_str}\n\n"
        f"📊 **Overall Metrics**\n"
        f"  • **Global Rank:** `{rank}` ({percentile}%)\n"
        f"  • **Quizzes Completed:** `{perf['total_tests']}`\n"
        f"  • **Total Questions:** `{perf['total_qs']}`\n"
        f"  • **Average Score:** `{round(perf['avg_score'], 2)}`\n\n"
        f"📅 **Recent Date-Wise Quiz Summary**\n"
        f"{hist_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 **Download your official PDF report card below:**"
    )

    buttons = [
        [InlineKeyboardButton("📥 Download Official PDF Report", callback_data=f"pdf_profilebook_{user.id}")],
        [InlineKeyboardButton("📊 My Stats (/mywholestate)", callback_data="cmd_wholestate"), InlineKeyboardButton("🏆 Leaderboard (/toppername)", callback_data="cmd_toppers")],
        [InlineKeyboardButton("🚀 Launch Quiz (/quiz)", callback_data="cmd_quiz"), InlineKeyboardButton("👤 Profile (/myprofile)", callback_data="cmd_profile")]
    ]

    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def download_pdf_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("📄 Generating your official PDF Stats Book...")

    target_user_id = int(query.data.replace("pdf_profilebook_", ""))
    profile = get_user_profile(target_user_id)

    if not profile:
        await query.message.reply_text("⚠️ Profile not found.")
        return

    pdf_buffer = generate_profile_book_pdf(profile)
    file_name = f"Profile_Book_{profile['full_name'].replace(' ', '_')}.pdf"

    post_pdf_buttons = [
        [InlineKeyboardButton("🚀 Launch Quiz (/quiz)", callback_data="cmd_quiz"), InlineKeyboardButton("📊 My Stats (/mywholestate)", callback_data="cmd_wholestate")],
        [InlineKeyboardButton("🏆 Leaderboard (/toppername)", callback_data="cmd_toppers"), InlineKeyboardButton("👤 Profile (/myprofile)", callback_data="cmd_profile")],
        [InlineKeyboardButton("💬 Feedback (/feedback)", callback_data="cmd_feedback"), InlineKeyboardButton("📖 Reviews (/reviews)", callback_data="cmd_viewfeedbacks")]
    ]

    await context.bot.send_document(
        chat_id=query.message.chat_id,
        document=pdf_buffer,
        filename=file_name,
        caption=(
            f"📄 **OFFICIAL PROFILE STATS REPORT CARD**\n"
            f"👤 **Student:** {profile['full_name']}\n"
            f"🎯 **Target Exam:** `{profile['target_exam']}`\n\n"
            f"⚡ **Powered by @LearnwithHiM**\n"
            f"Keep practicing daily to unlock Gold & Diamond badges!\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👇 **Continue exploring using the buttons below:**"
        ),
        reply_markup=InlineKeyboardMarkup(post_pdf_buttons),
        parse_mode="Markdown"
    )

async def myprofile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if update.effective_chat:
        await sync_user_chat_menu(context, update.effective_chat.id)

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
        f"⚡ *Powered by @LearnwithHiM*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"  • **Full Name:** {profile['full_name']}\n"
        f"  • **Telegram ID:** `{profile['user_id']}`\n"
        f"  • **Target Exam:** `{profile['target_exam']}`\n"
        f"  • **Age / Gender:** `{profile['age']}` / `{profile['gender']}`\n"
        f"  • **Location:** `{profile.get('state', 'N/A')}, {profile.get('country', 'India')}`\n"
        f"  • **Phone:** `{profile['phone_number']}` *(Private)*\n\n"
        f"📊 **Daily Quota Status**\n"
        f"  • **Used Today:** `{today_used}` / `{allowed_limit}` Qs\n"
        f"  • **Remaining Today:** `{remaining}` Qs\n"
        f"  • **Referrals:** `{profile.get('referral_count', 0)}` / 4 friends\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 **Quick Navigation:**"
    )

    buttons = [
        [InlineKeyboardButton("📖 Profile Book (/profilebook)", callback_data="cmd_profilebook"), InlineKeyboardButton("📊 My Stats (/mywholestate)", callback_data="cmd_wholestate")],
        [InlineKeyboardButton("🏆 Leaderboard (/toppername)", callback_data="cmd_toppers"), InlineKeyboardButton("✏️ Edit Profile (/editprofile)", callback_data="cmd_editprofile")],
        [InlineKeyboardButton("🤝 Invite Friends (/invite)", callback_data="cmd_referral"), InlineKeyboardButton("🚀 Launch Quiz (/quiz)", callback_data="cmd_quiz")]
    ]

    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def wholestate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if update.effective_chat:
        await sync_user_chat_menu(context, update.effective_chat.id)

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
        f"⚡ *Powered by @LearnwithHiM*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 **Name:** {profile['full_name']}\n"
        f"🎯 **Target Exam:** `{profile['target_exam']}`\n"
        f"📍 **Location:** `{profile.get('state', 'N/A')}, {profile.get('country', 'India')}`\n\n"
        f"📈 **Performance Metrics**\n"
        f"  • **Tests Completed:** `{perf.get('total_tests', 0)}`\n"
        f"  • **Questions Attempted:** `{perf.get('total_qs', 0)}`\n"
        f"  • **Global Rank:** `{rank}`\n"
        f"  • **Overall Percentile:** `{percentile}%` *(Calculated against all registered students)*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 **Quick Navigation:**"
    )

    buttons = [
        [InlineKeyboardButton("📖 Profile Book (/profilebook)", callback_data="cmd_profilebook"), InlineKeyboardButton("🏆 Leaderboard (/toppername)", callback_data="cmd_toppers")],
        [InlineKeyboardButton("👤 Profile (/myprofile)", callback_data="cmd_profile"), InlineKeyboardButton("💬 Write Review (/feedback)", callback_data="cmd_feedback")],
        [InlineKeyboardButton("🚀 Launch Quiz (/quiz)", callback_data="cmd_quiz")]
    ]
    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def toppers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if update.effective_chat:
        await sync_user_chat_menu(context, update.effective_chat.id)

    toppers = get_overall_leaderboard(limit=10)
    
    if not toppers:
        await send_response(update, "🏆 No leaderboard records available yet. Be the first to attempt a quiz!")
        return

    lines = []
    for idx, t in enumerate(toppers, start=1):
        badge = " 🥇" if idx == 1 else " 🥈" if idx == 2 else " 🥉" if idx == 3 else ""
        lines.append(f"{idx}. **{t['full_name']}**{badge} — Avg Score: `{round(t['avg_score'], 2)}`")

    msg = (
        "🏆 **GLOBAL SCHOLAR LEADERBOARD**\n"
        "⚡ *Powered by @LearnwithHiM*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n" 
        + "\n".join(lines) + 
        "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "👇 **Quick Navigation:**"
    )
    buttons = [
        [InlineKeyboardButton("📖 Profile Book (/profilebook)", callback_data="cmd_profilebook"), InlineKeyboardButton("📊 My Stats (/mywholestate)", callback_data="cmd_wholestate")],
        [InlineKeyboardButton("👤 Profile (/myprofile)", callback_data="cmd_profile"), InlineKeyboardButton("🚀 Launch Quiz (/quiz)", callback_data="cmd_quiz")]
    ]
    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if update.effective_chat:
        await sync_user_chat_menu(context, update.effective_chat.id)

    keyboard = [
        [InlineKeyboardButton("🌟 10/10 Bot! Top quality quizzes 🚀", callback_data="fb_p1")],
        [InlineKeyboardButton("✨ Best preparation portal 🎓", callback_data="fb_p2")],
        [InlineKeyboardButton("🔥 Daily target limits keep me disciplined! 📈", callback_data="fb_p3")],
        [InlineKeyboardButton("✍️ Write Custom Feedback", callback_data="fb_custom")],
        [InlineKeyboardButton("📖 Profile Book (/profilebook)", callback_data="cmd_profilebook"), InlineKeyboardButton("📖 See All Reviews (/reviews)", callback_data="cmd_viewfeedbacks")],
        [InlineKeyboardButton("📊 My Stats (/mywholestate)", callback_data="cmd_wholestate"), InlineKeyboardButton("🚀 Quiz (/quiz)", callback_data="cmd_quiz")]
    ]

    msg = (
        "💬 **STUDENT FEEDBACK PORTAL**\n"
        "⚡ *Powered by @LearnwithHiM*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Your feedback helps Himanshu Sir make this platform even better!\n"
        "Select a quick preset rating below or write your own custom feedback:"
    )
    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(keyboard))

async def viewfeedbacks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if update.effective_chat:
        await sync_user_chat_menu(context, update.effective_chat.id)

    feedbacks = get_all_student_feedbacks(limit=15)

    if not feedbacks:
        await send_response(update, "📖 No student reviews submitted yet. Be the first to leave feedback using /feedback!")
        return

    lines = ["📖 **STUDENT REVIEWS BOARD**\n⚡ *Powered by @LearnwithHiM*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"]
    for idx, fb in enumerate(feedbacks, start=1):
        date_str = fb.get('submitted_at', '').split(' ')[0] if fb.get('submitted_at') else 'Verified Student'
        lines.append(f"**{idx}. {fb['full_name']}** (`{date_str}`):\n💬 *\"{fb['feedback_text']}\"*\n")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n👇 **Quick Navigation:**")

    buttons = [
        [InlineKeyboardButton("💬 Write Review (/feedback)", callback_data="cmd_feedback"), InlineKeyboardButton("📖 Profile Book (/profilebook)", callback_data="cmd_profilebook")],
        [InlineKeyboardButton("🚀 Launch Quiz (/quiz)", callback_data="cmd_quiz"), InlineKeyboardButton("🏆 Leaderboard (/toppername)", callback_data="cmd_toppers")]
    ]

    await send_response(update, "\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))

async def referral_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if update.effective_chat:
        await sync_user_chat_menu(context, update.effective_chat.id)

    user = update.effective_user
    bot_username = context.bot.username
    ref_link = f"https://t.me/{bot_username}?start=ref_{user.id}"

    msg = (
        f"🤝 **INVITE FRIENDS & UNLOCK +10 DAILY LIMIT**\n"
        f"⚡ *Powered by @LearnwithHiM*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Share your personal invite link below with **4 friends**:\n"
        f"`{ref_link}`\n\n"
        f"When 4 friends register using your link, you automatically receive +10 questions added to your daily quota!\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 **Quick Navigation:**"
    )
    buttons = [
        [InlineKeyboardButton("📖 Profile Book (/profilebook)", callback_data="cmd_profilebook"), InlineKeyboardButton("🚀 Launch Quiz (/quiz)", callback_data="cmd_quiz")],
        [InlineKeyboardButton("👤 View Profile (/myprofile)", callback_data="cmd_profile"), InlineKeyboardButton("🏆 Leaderboard (/toppername)", callback_data="cmd_toppers")]
    ]
    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return

    query = update.callback_query
    data = query.data
    user = query.from_user

    if data == "cmd_quiz":
        await launch_quiz_setup(update, context)
    elif data == "cmd_profile":
        await myprofile_command(update, context)
    elif data == "cmd_profilebook":
        await profilebook_command(update, context)
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
    elif data == "cmd_editprofile":
        from app.onboarding import edit_profile_command
        await edit_profile_command(update, context)
    elif data.startswith("pdf_profilebook_"):
        await download_pdf_callback(update, context)
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
        
        post_fb_buttons = [
            [InlineKeyboardButton("📖 Profile Book (/profilebook)", callback_data="cmd_profilebook"), InlineKeyboardButton("📖 See All Reviews (/reviews)", callback_data="cmd_viewfeedbacks")],
            [InlineKeyboardButton("📊 My Stats (/mywholestate)", callback_data="cmd_wholestate"), InlineKeyboardButton("🚀 Start Quiz (/quiz)", callback_data="cmd_quiz")]
        ]
        await query.edit_message_text(
            f"🎉 **Thank you, {name}!** Your feedback has been recorded:\n\n💬 *\"{fb_text}\"*\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n👇 **What would you like to do next?**",
            reply_markup=InlineKeyboardMarkup(post_fb_buttons),
            parse_mode="Markdown"
        )

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

        post_fb_buttons = [
            [InlineKeyboardButton("📖 Profile Book (/profilebook)", callback_data="cmd_profilebook"), InlineKeyboardButton("📖 See All Reviews (/reviews)", callback_data="cmd_viewfeedbacks")],
            [InlineKeyboardButton("📊 My Stats (/mywholestate)", callback_data="cmd_wholestate"), InlineKeyboardButton("🚀 Start Quiz (/quiz)", callback_data="cmd_quiz")]
        ]
        await update.message.reply_text(
            f"🎉 **Feedback Received!** Thank you *{name}* for your kind words:\n\n💬 *\"{text}\"*\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n👇 **What would you like to do next?**",
            reply_markup=InlineKeyboardMarkup(post_fb_buttons),
            parse_mode="Markdown"
        )
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
    """
    PURGES ALL CACHED TELEGRAM COMMAND SCOPES AND REGISTERS THE EXACT PROJECT COMMANDS.
    """
    try:
        await application.bot.delete_my_commands(scope=BotCommandScopeDefault())
        await application.bot.delete_my_commands(scope=BotCommandScopeAllPrivateChats())
        await application.bot.delete_my_commands(scope=BotCommandScopeAllGroupChats())
    except Exception as e:
        logging.warning(f"Note on command purge: {e}")

    await application.bot.set_my_commands(ALLOWED_COMMANDS, scope=BotCommandScopeDefault())
    await application.bot.set_my_commands(ALLOWED_COMMANDS, scope=BotCommandScopeAllPrivateChats())
    await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())

def build_application() -> Application:
    init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(get_onboarding_handler())
    
    # Core Project Commands
    app.add_handler(CommandHandler("quiz", launch_quiz_setup))
    app.add_handler(CommandHandler("profilebook", profilebook_command))
    app.add_handler(CommandHandler("profilecard", profilebook_command))
    app.add_handler(CommandHandler("myprofilebook", profilebook_command))
    app.add_handler(CommandHandler("myprofile", myprofile_command))
    app.add_handler(CommandHandler("mywholestate", wholestate_command))
    app.add_handler(CommandHandler("toppername", toppers_command))
    app.add_handler(CommandHandler("toppersname", toppers_command))
    app.add_handler(CommandHandler("feedback", feedback_command))
    app.add_handler(CommandHandler("reviews", viewfeedbacks_command))
    app.add_handler(CommandHandler("viewfeedbacks", viewfeedbacks_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("invite", referral_command))
    
    # Secret Admin Command
    app.add_handler(CommandHandler("admin", admin_portal_command))
    app.add_handler(CommandHandler("admit", admin_portal_command))

    app.add_handler(CallbackQueryHandler(quiz_count_callback, pattern="^qcount_"))
    app.add_handler(CallbackQueryHandler(quiz_timer_callback, pattern="^qtimer_"))
    app.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^admin_"))
    app.add_handler(CallbackQueryHandler(button_router, pattern="^cmd_|^fb_|^pdf_"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
    app.add_handler(PollAnswerHandler(handle_poll_answer))
    app.add_error_handler(global_error_handler)

    return app