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
from app.config import (
    BOT_TOKEN, PRIMARY_ADMIN_ID, DAILY_QUESTION_LIMIT, PLAN_TIERS,
    RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RENDER_EXTERNAL_URL
)
from app.database import (
    init_db, get_maintenance_until, get_user_profile, 
    get_all_users, get_today_attempts, save_student_feedback, get_all_student_feedbacks,
    clear_paused_quiz_state, get_saved_questions, log_user_activity_time,
    check_and_update_inactivity, refresh_user_activity_epoch, get_db, get_seen_question_ids
)
from app.onboarding import get_onboarding_handler, start_onboarding
from app.quiz_engine import (
    launch_quiz_setup, quiz_count_callback, quiz_timer_callback, handle_poll_answer,
    pause_quiz_command, resume_quiz_command, stop_quiz_command, save_question_callback
)
from app.stats import get_overall_leaderboard, calculate_user_percentile, calculate_user_rank, get_user_performance_summary
from app.admin import admin_portal_command, admin_callback_handler
from app.pdf_generator import generate_student_pdf_report
from app.pyq_fetcher import fetch_pyqs_for_quiz

try:
    import razorpay
    HAS_RAZORPAY = True
except ImportError:
    HAS_RAZORPAY = False

NEGATIVE_WORDS = ["bad", "worst", "useless", "trash", "fake", "hate", "terrible", "waste", "horrible", "fraud", "stupid", "scam"]

def generate_razorpay_link(user_id: int, plan_key: str):
    """Master production-grade Razorpay payment link generator with complete diagnostics."""
    plan = PLAN_TIERS.get(plan_key)
    if not plan:
        logging.error(f"[PAYMENT ERROR] Invalid Plan Key requested: {plan_key}")
        return None

    # Free Demo Trial (₹0) handling
    if plan["price"] == 0:
        return f"https://t.me/{os.getenv('BOT_USERNAME', 'LearnwithHiMBot')}?start=activate_demo_{plan_key}"

    if not HAS_RAZORPAY:
        logging.error("[PAYMENT ERROR] Razorpay Python module is not installed.")
        return None

    # Clean and check keys dynamically from environment or config
    key_id = (os.getenv("RAZORPAY_KEY_ID") or RAZORPAY_KEY_ID or "").strip()
    key_secret = (os.getenv("RAZORPAY_KEY_SECRET") or RAZORPAY_KEY_SECRET or "").strip()

    if not key_id or not key_secret or "your_key" in key_id:
        logging.error(f"[PAYMENT CONFIG ERROR] Razorpay Keys are missing or invalid! Found Key ID length: {len(key_id)}")
        return None

    try:
        client = razorpay.Client(auth=(key_id, key_secret))
        amount_in_paise = int(plan["price"] * 100)
        
        profile = get_user_profile(user_id)
        raw_name = profile.get("full_name", "Student Scholar") if profile else "Student Scholar"
        raw_phone = str(profile.get("phone_number", "9999999999")) if profile else "9999999999"
        
        clean_phone = "".join(filter(str.isdigit, raw_phone))
        if len(clean_phone) >= 10:
            clean_phone = clean_phone[-10:]
        else:
            clean_phone = "9999999999"

        payload = {
            "amount": amount_in_paise,
            "currency": "INR",
            "accept_partial": False,
            "description": f"Learn with HiM Subscription - {plan['name']}",
            "customer": {
                "name": raw_name[:30],
                "contact": clean_phone,
                "email": f"student_{user_id}@learnwithhim.com"
            },
            "notes": {
                "user_id": str(user_id),
                "plan_key": plan_key
            },
            "callback_url": RENDER_EXTERNAL_URL if RENDER_EXTERNAL_URL else "https://learnwithhimquiz.onrender.com",
            "callback_method": "get"
        }

        logging.info(f"[RAZORPAY API CALL] Requesting payment link for User {user_id}, Plan: {plan_key}, Amount: {amount_in_paise} paise")
        response = client.payment_link.create(payload)
        short_url = response.get("short_url")
        
        if not short_url:
            logging.error(f"[RAZORPAY API ERROR] Response received without short_url: {response}")
            return None
            
        return short_url
    except Exception as e:
        logging.error(f"[RAZORPAY CRITICAL EXCEPTION] Failed for User {user_id}: {str(e)}")
        return None

async def send_registration_prompt(update: Update):
    msg = (
        "⚠️ **REGISTRATION REQUIRED!** ⚠️\n\n"
        "To start using the Quiz Book, please tap the registration button below to complete student setup:"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Click Here for Registration", callback_data="trigger_start")]
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
        rec_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 Reset Your PIN / Password", callback_data="login_forgot_pin")]])
        msg = (
            f"🔒 **ACCOUNT LOCKED DUE TO INACTIVITY** 🔒\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"You were inactive for `{diff_sec // 60} mins`.\n\n"
            f"🔑 **Please reply with your 4-Digit Secret PIN to unlock your account:**"
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
        msg = f"🛠 **ADMIN HAS PAUSED THE SERVICE CURRENTLY** 🛠\n\n⏰ Service will resume in approx `{mins_left} mins`. Please try again later!"
        
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
    paid_bal = profile.get("paid_question_balance", 0) or 0
    base_limit = max(DAILY_QUESTION_LIMIT, paid_bal)
    allowed_limit = 10000 if user.id == PRIMARY_ADMIN_ID else base_limit + profile.get("bonus_quota", 0)

    if attempted_today >= allowed_limit:
        limit_msg = (
            f"🛑 **Daily Limit Exhausted!** 🛑\n\n"
            f"You have reached your daily limit of `{allowed_limit}` questions for today (00:00 to 23:59).\n"
            f"The `/quiz` command has been **deactivated** for your account until tomorrow.\n\n"
            f"💡 **Upgrade Limit:** Unlock higher daily questions via **💳 VIP Payment Plans**!"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 View VIP Payment Plans", callback_data="cmd_plans")],
            [InlineKeyboardButton("🤝 Invite Friends (+10 Limit)", callback_data="cmd_referral")]
        ])
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

async def plans_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)

    keyboard = [
        [InlineKeyboardButton("🎁 FREE DEMO TRIAL (2 Days - 20 Qs/Day)", callback_data="buy_plan_FREE_DEMO")],
        [InlineKeyboardButton("📦 BRONZE (₹5 - 3 Days - 80 Qs/Day)", callback_data="buy_plan_BRONZE")],
        [InlineKeyboardButton("📦 SILVER (₹10 - 7 Days - 100 Qs/Day)", callback_data="buy_plan_SILVER")],
        [InlineKeyboardButton("📦 GOLD (₹15 - 12 Days - 120 Qs/Day)", callback_data="buy_plan_GOLD")],
        [InlineKeyboardButton("📦 DIAMOND (₹20 - 18 Days - 150 Qs/Day)", callback_data="buy_plan_DIAMOND")],
        [InlineKeyboardButton("📦 LEARNWITHHIM (₹25 - 30 Days - 250 Qs/Day)", callback_data="buy_plan_LEARNWITHHIM")],
        [InlineKeyboardButton("📦 PLATINUM (₹40 - 60 Days - 300 Qs/Day)", callback_data="buy_plan_PLATINUM")],
        [InlineKeyboardButton("📦 RUBY (₹50 - 90 Days - 400 Qs/Day)", callback_data="buy_plan_RUBY")],
        [InlineKeyboardButton("📦 MEGA PACK (₹80 - 180 Days - 500 Qs/Day)", callback_data="buy_plan_MEGA")],
    ]

    msg = (
        f"👑 **LEARN WITH HIM QUIZ BOOK — VIP MEMBERSHIP PACKS** 👑\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎁 **FREE DEMO TRIAL:** 2 Days Access | 20 Questions / Day Limit\n\n"
        f"📦 **BRONZE:** ₹5 | 3 Days (80 Qs/Day)\n"
        f"📦 **SILVER:** ₹10 | 7 Days (100 Qs/Day)\n"
        f"📦 **GOLD:** ₹15 | 12 Days (120 Qs/Day)\n"
        f"📦 **DIAMOND:** ₹20 | 18 Days (150 Qs/Day)\n"
        f"📦 **LEARNWITHHIM:** ₹25 | 30 Days (250 Qs/Day)\n"
        f"📦 **PLATINUM:** ₹40 | 60 Days (300 Qs/Day)\n"
        f"📦 **RUBY:** ₹50 | 90 Days (400 Qs/Day)\n"
        f"📦 **MEGA PACK:** ₹80 | 180 Days / 6 Months (500 Qs/Day)\n\n"
        f"⚡ **0% Payment Failure Architecture:** Instant Razorpay Auto-Link with Automated VIP Activation!\n\n"
        f"Select a pack below to get started:"
    )

    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_buy_plan_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    plan_key = query.data.replace("buy_plan_", "")
    plan_info = PLAN_TIERS.get(plan_key)

    if not plan_info:
        await query.message.reply_text("⚠️ Invalid plan selected.")
        return

    if plan_info["price"] == 0:
        from main import activate_user_subscription
        await activate_user_subscription(user_id, plan_key)
        await query.edit_message_text(
            f"🎉 **FREE DEMO ACTIVATED SUCCESSFULLY!**\n\n"
            f"🎁 Duration: {plan_info['days']} Days\n"
            f"⚡ Daily Limit: {plan_info['daily_limit']} Questions/Day\n\n"
            f"Start practicing now with /quiz!",
            parse_mode="Markdown"
        )
        return

    payment_url = generate_razorpay_link(user_id, plan_key)

    if payment_url:
        keyboard = [
            [InlineKeyboardButton(f"💳 Pay ₹{plan_info['price']} via Razorpay", url=payment_url)],
            [InlineKeyboardButton("🔙 Back to Plans", callback_data="cmd_plans")]
        ]
        msg = (
            f"🛒 **SELECTED PACK:** {plan_info['name']}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 **Amount Payable:** ₹{plan_info['price']}\n"
            f"📅 **Validity:** {plan_info['days']} Days\n"
            f"⚡ **Daily Limit:** {plan_info['daily_limit']} Questions/Day\n\n"
            f"Tap the **💳 Pay Now** button below to complete payment via UPI, GPay, PhonePe, or Cards. "
            f"Your VIP quota activates automatically upon payment!"
        )
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await query.message.reply_text(
            "⚠️ **Payment Link Generation Failed!**\n\n"
            "Please check your Render Environment Variables:\n"
            "• `RAZORPAY_KEY_ID`\n"
            "• `RAZORPAY_KEY_SECRET`\n\n"
            "Check your Render Console Logs for the exact exception details.",
            parse_mode="Markdown"
        )

async def pdfreport_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 1. Last 1 Month Full Data Report", callback_data="usergenpdf_last_1_month_data")],
        [InlineKeyboardButton("📊 2. Last 1 Month Quiz Summary Report (No Qs)", callback_data="usergenpdf_last_1_month_quiz")],
        [InlineKeyboardButton("📜 3. All Months Full Data Report", callback_data="usergenpdf_all_months_data")],
        [InlineKeyboardButton("📈 4. All Months Quiz Summary Report (No Qs)", callback_data="usergenpdf_all_months_quiz")],
        [InlineKeyboardButton("💾 5. Saved Questions PDF Report", callback_data="usergenpdf_saved_questions_only")],
        [InlineKeyboardButton("👤 Back to Profile", callback_data="cmd_profile")]
    ])

    msg = (
        f"📄 **STUDENT PDF REPORT CENTER** 📄\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✨ Select your preferred report format below to instantly generate and download your personal academic PDF report card:"
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg, reply_markup=buttons, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, reply_markup=buttons, parse_mode="Markdown")

async def wrongquestions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)

    conn = get_db()
    attempts = conn.execute("SELECT * FROM quiz_attempts WHERE user_id = ? ORDER BY id DESC LIMIT 5", (user.id,)).fetchall()
    conn.close()

    lines = [
        f"❌ **YOUR INCORRECT QUESTIONS LOG** ❌",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
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
                lines.append(f" {idx}. ❌ `{q_text}`\n    👉 **Correct Answer:** `{ans_text}`")
            lines.append("")

    if not found_wrong:
        lines.append("🎉 *Zero wrong questions logged in your recent attempts! Excellent job.*")

    msg = "\n".join(lines)
    if len(msg) > 4000:
        msg = msg[:3950] + "\n\n*(Truncated due to length limit)*"

    nav = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Launch Quiz", callback_data="cmd_quiz")]])
    await send_response(update, msg, reply_markup=nav)

async def attemptedquestions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)

    conn = get_db()
    attempts = conn.execute("SELECT * FROM quiz_attempts WHERE user_id = ? ORDER BY id DESC LIMIT 5", (user.id,)).fetchall()
    conn.close()

    lines = [
        f"🎯 **YOUR RECENT ATTEMPTED QUESTIONS** 🎯",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
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
        lines.append("*No question attempt logs found yet. Type /quiz to start!*")

    msg = "\n".join(lines)
    if len(msg) > 4000:
        msg = msg[:3950] + "\n\n*(Truncated due to length limit)*"

    nav = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Launch Quiz", callback_data="cmd_quiz")]])
    await send_response(update, msg, reply_markup=nav)

async def unattemptedquestions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)

    seen_ids = get_seen_question_ids(user.id)
    all_qs = fetch_pyqs_for_quiz(needed_count=1000, seen_ids=[])
    total_bank = len(all_qs)
    seen_count = len(seen_ids)
    remaining_count = max(0, total_bank - seen_count)

    msg = (
        f"⏭️ **UNATTEMPTED QUESTIONS SUMMARY** ⏭️\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📚 **Total Question Bank:** `{total_bank}` Qs\n"
        f"✅ **Questions Attempted:** `{seen_count}` Qs\n"
        f"⏭️ **Remaining Unattempted:** `{remaining_count}` Qs\n\n"
        f"🚀 Tap **Launch Quiz** below to practice new unattempted questions!"
    )
    nav = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Launch Quiz", callback_data="cmd_quiz")]])
    await send_response(update, msg, reply_markup=nav)

async def user_pdf_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    if not data.startswith("usergenpdf_"):
        return

    filter_mode = data.replace("usergenpdf_", "")
    await query.answer()
    await query.edit_message_text("⏳ **Generating Your Custom PDF Report Card...**\nFormatting telemetry, tables, and compiling layout...", parse_mode="Markdown")

    pdf_file = generate_student_pdf_report(user_id, filter_mode)
    profile = get_user_profile(user_id)
    student_name = profile.get("full_name", "Student") if profile else "Student"
    sid = profile.get("student_id", f"USER_{user_id}") if profile else f"USER_{user_id}"

    if pdf_file == "NO_ATTEMPTS":
        nav = InlineKeyboardMarkup([[InlineKeyboardButton("📄 Back to PDF Center", callback_data="cmd_pdfreport")]])
        await query.edit_message_text("ℹ️ **NO QUIZ ATTEMPTS FOUND!**\n\nYou have not attempted any quizzes in the selected timeframe yet.", reply_markup=nav, parse_mode="Markdown")
    elif pdf_file == "NO_SAVED_QUESTIONS":
        nav = InlineKeyboardMarkup([[InlineKeyboardButton("📄 Back to PDF Center", callback_data="cmd_pdfreport")]])
        await query.edit_message_text("ℹ️ **NO SAVED QUESTIONS FOUND!**\n\nYou haven't bookmarked any questions yet during your quizzes.", reply_markup=nav, parse_mode="Markdown")
    elif pdf_file and pdf_file.startswith("ERROR_DETAILS:"):
        nav = InlineKeyboardMarkup([[InlineKeyboardButton("📄 Back to PDF Center", callback_data="cmd_pdfreport")]])
        await query.edit_message_text(f"⚠️ **PDF Generation Error:**\n\n`{pdf_file}`", reply_markup=nav, parse_mode="Markdown")
    elif pdf_file and os.path.exists(pdf_file):
        caption = (
            f"📄 **OFFICIAL STUDENT PDF ACADEMIC REPORT**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 **Student:** {student_name}\n"
            f"🪪 **Student ID:** `{sid}`\n"
            f"📊 **Report Module:** `{filter_mode.replace('_', ' ').title()}`\n"
            f"🏷 **Watermark:** `@LearnwithHiM`"
        )
        with open(pdf_file, "rb") as doc:
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=doc,
                filename=os.path.basename(pdf_file),
                caption=caption,
                parse_mode="Markdown"
            )
        nav = InlineKeyboardMarkup([
            [InlineKeyboardButton("📄 Export Another PDF", callback_data="cmd_pdfreport")],
            [InlineKeyboardButton("👤 My Profile", callback_data="cmd_profile")]
        ])
        await context.bot.send_message(chat_id=query.message.chat_id, text="👇 **Quick Navigation:**", reply_markup=nav, parse_mode="Markdown")
    else:
        nav = InlineKeyboardMarkup([[InlineKeyboardButton("📄 Back to PDF Center", callback_data="cmd_pdfreport")]])
        await query.edit_message_text("⚠️ **Failed to generate PDF file.**", reply_markup=nav, parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return
    
    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)

    msg = (
        "🤖 **LEARN WITH HIM QUIZ BOOK — COMMAND DIRECTORY** 🤖\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Tap any interactive button below or use the blue **[≡ Menu]** button:\n"
    )

    buttons = [
        [InlineKeyboardButton("🚀 Launch Quiz (/quiz)", callback_data="cmd_quiz"), InlineKeyboardButton("📄 PDF Reports (/pdfreport)", callback_data="cmd_pdfreport")],
        [InlineKeyboardButton("💳 VIP Plans (/plans)", callback_data="cmd_plans"), InlineKeyboardButton("💾 Bookmarks (/savedquestions)", callback_data="cmd_savedquestions")],
        [InlineKeyboardButton("❌ Wrong Qs (/wrongquestions)", callback_data="cmd_wrongquestions"), InlineKeyboardButton("🎯 Attempted Qs (/attemptedquestions)", callback_data="cmd_attemptedquestions")],
        [InlineKeyboardButton("⏭️ Unattempted Qs (/unattemptedquestions)", callback_data="cmd_unattemptedquestions"), InlineKeyboardButton("👤 My Profile (/myprofile)", callback_data="cmd_profile")],
        [InlineKeyboardButton("✏️ Edit Profile (/editprofile)", callback_data="cmd_editprofile"), InlineKeyboardButton("📊 My Rank (/mywholestate)", callback_data="cmd_wholestate")],
        [InlineKeyboardButton("🏆 Leaderboard (/toppername)", callback_data="cmd_toppers"), InlineKeyboardButton("💬 Submit Feedback (/feedback)", callback_data="cmd_feedback")],
        [InlineKeyboardButton("📖 Reviews (/reviews)", callback_data="cmd_viewfeedbacks"), InlineKeyboardButton("🤝 Invite Friends (/invite)", callback_data="cmd_referral")]
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
            "📖 **SAVED QUESTIONS BOOKMARKS** 📖\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "You haven't bookmarked any questions yet! Tap **💾 Save Question** during quizzes to save important questions here."
        )
        await send_response(update, msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Launch Quiz", callback_data="cmd_quiz")]]))
        return

    total_count = len(saved)
    lines = [
        f"📖 **SAVED QUESTIONS BOOKMARKS** 📖",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 **Total Bookmarks:** `{total_count}`",
        f"📌 *Showing most recent bookmarks first*\n"
    ]

    for idx, sq in enumerate(saved[:15], start=1):
        opts_list = json.loads(sq['options_json']) if sq['options_json'] else []
        corr_idx = sq['correct_option']
        corr_ans = opts_list[corr_idx] if 0 <= corr_idx < len(opts_list) else 'N/A'
        
        lines.append(
            f"**{idx}. Saved At:** `{sq['saved_at']}`\n"
            f"❓ **Q:** {sq['question_text']}\n"
            f"✅ **Correct Answer:** `{corr_ans}`\n"
            f"💡 **Explanation:** {sq['explanation']}\n"
            f"──────────────────────────────"
        )

    if total_count > 15:
        lines.append(f"\n*(Showing 15 of {total_count} saved questions)*")

    msg = "\n".join(lines)
    buttons = [
        [InlineKeyboardButton("📄 Export Saved Qs to PDF", callback_data="usergenpdf_saved_questions_only")],
        [InlineKeyboardButton("🚀 Launch Quiz", callback_data="cmd_quiz")]
    ]
    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def myprofile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)
    profile = get_user_profile(user.id)

    today_used = get_today_attempts(user.id)
    paid_bal = profile.get("paid_question_balance", 0) or 0
    base_limit = max(DAILY_QUESTION_LIMIT, paid_bal)
    allowed_limit = 10000 if user.id == PRIMARY_ADMIN_ID else base_limit + profile.get("bonus_quota", 0)

    remaining = max(0, allowed_limit - today_used)
    student_id = profile.get("student_id", f"USER_{user.id}")
    expiry = profile.get("vip_pass_expiry") or "N/A"

    msg = (
        f"👤 **STUDENT PROFILE CARD** 👤\n"
        f"📚 **Learn with HiM Quiz Book**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• **Full Name:** {profile['full_name']}\n"
        f"• **Student ID:** `{student_id}` 🪪\n"
        f"• **Telegram ID:** `{profile['user_id']}` 🆔\n"
        f"• **Target Exam:** `{profile['target_exam']}` 🎯\n"
        f"• **DOB / Gender:** `{profile.get('dob', 'N/A')}` / `{profile['gender']}` 🎂\n"
        f"• **Location:** `{profile.get('state', 'N/A')}, India` 📍\n"
        f"• **Phone:** `{profile['phone_number']}` 📱 *(Private)*\n\n"
        f"📊 **DAILY QUOTA & SUBSCRIPTION:**\n"
        f"• **Used Today:** `{today_used}` / `{allowed_limit}` Qs 🖥\n"
        f"• **Remaining Today:** `{remaining}` Qs ⚡\n"
        f"• **VIP Expiry:** `{expiry}`\n"
        f"• **Referrals:** `{profile.get('referral_count', 0)}` / 4 friends 🤝\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    buttons = [
        [InlineKeyboardButton("🚀 Launch Quiz", callback_data="cmd_quiz"), InlineKeyboardButton("📄 PDF Report Center", callback_data="cmd_pdfreport")],
        [InlineKeyboardButton("💳 VIP Plans", callback_data="cmd_plans"), InlineKeyboardButton("💾 Bookmarks", callback_data="cmd_savedquestions")],
        [InlineKeyboardButton("✏️ Edit Profile", callback_data="cmd_editprofile"), InlineKeyboardButton("🤝 Invite Friends", callback_data="cmd_referral")]
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
        f"🎓 **STUDENT ACADEMIC REPORT CARD** 🎓\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 **Name:** {profile['full_name']}\n"
        f"🪪 **Student ID:** `{student_id}`\n"
        f"🎯 **Target Exam:** `{profile['target_exam']}`\n"
        f"📍 **Location:** `{profile.get('state', 'N/A')}, India` 🇮🇳\n\n"
        f"📈 **PERFORMANCE METRICS:**\n"
        f"• **Tests Completed:** `{perf.get('total_tests', 0)}` 📚\n"
        f"• **Questions Attempted:** `{perf.get('total_qs', 0)}` 🖥\n"
        f"• **Global Rank:** `{rank}` 🥇\n"
        f"• **Overall Percentile:** `{percentile}%` 📊 *(Calculated against all scholars)*"
    )

    buttons = [
        [InlineKeyboardButton("🚀 Launch Quiz", callback_data="cmd_quiz"), InlineKeyboardButton("📄 PDF Report Center", callback_data="cmd_pdfreport")],
        [InlineKeyboardButton("🏆 Toppers Leaderboard", callback_data="cmd_toppers")]
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
        badge = " 🥇" if idx == 1 else " 🥈" if idx == 2 else " 🥉" if idx == 3 else " 🎖"
        lines.append(f"{idx}. **{t['full_name']}**{badge} — Avg Score: `{round(t['avg_score'], 2)}` ⭐")

    msg = "🏆 **GLOBAL SCHOLAR LEADERBOARD** 🏆\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n" + "\n".join(lines)
    buttons = [[InlineKeyboardButton("🚀 Attempt Quiz", callback_data="cmd_quiz")]]
    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)

    keyboard = [
        [InlineKeyboardButton("🌟 10/10 Quality Quizzes!", callback_data="fb_p1")],
        [InlineKeyboardButton("✨ Best Exam Prep Portal", callback_data="fb_p2")],
        [InlineKeyboardButton("🔥 Great Daily Limits & Routine!", callback_data="fb_p3")],
        [InlineKeyboardButton("✍️ Write Custom Review", callback_data="fb_custom")],
        [InlineKeyboardButton("📖 View All Student Reviews", callback_data="cmd_viewfeedbacks")]
    ]

    msg = (
        "💬 **STUDENT FEEDBACK PORTAL** 💬\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Your feedback helps Himanshu Sir refine this platform for higher performance!\n"
        "Tap a preset option below or write your own custom feedback:"
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

    lines = ["📖 **STUDENT REVIEWS & FEEDBACK BOARD** 📖\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"]
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
        f"🤝 **INVITE FRIENDS & UNLOCK +10 DAILY LIMIT** 🤝\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Share your personal invite link below with **4 friends**:\n"
        f"`{ref_link}`\n\n"
        f"When 4 friends register using your link, you automatically receive +10 extra questions added to your daily quota!"
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
        
        paid_bal = profile.get("paid_question_balance", 0) or 0 if profile else 0
        base_limit = max(DAILY_QUESTION_LIMIT, paid_bal)
        allowed_limit = 10000 if user.id == PRIMARY_ADMIN_ID else base_limit + (profile.get("bonus_quota", 0) if profile else 0)

        if attempted_today >= allowed_limit:
            await query.answer("🛑 Daily Limit Exhausted!", show_alert=True)
            return
        await launch_quiz_setup(update, context)
    elif data == "cmd_plans":
        await plans_command(update, context)
    elif data.startswith("buy_plan_"):
        await handle_buy_plan_callback(update, context)
    elif data == "cmd_help":
        await help_command(update, context)
    elif data == "cmd_pdfreport":
        await pdfreport_command(update, context)
    elif data == "cmd_wrongquestions":
        await wrongquestions_command(update, context)
    elif data == "cmd_attemptedquestions":
        await attemptedquestions_command(update, context)
    elif data == "cmd_unattemptedquestions":
        await unattemptedquestions_command(update, context)
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
            "fb_p1": "10/10 Quality Quizzes!",
            "fb_p2": "Best Exam Prep Portal!",
            "fb_p3": "Great Daily Limits & Routine!"
        }
        fb_text = presets.get(data, "Great educational bot!")
        profile = get_user_profile(user.id)
        name = profile.get("full_name") if profile else user.full_name
        save_student_feedback(user.id, name, fb_text)
        await query.edit_message_text(f"🎉 **Thank you, {name}!** Your review has been saved:\n\n💬 *\"{fb_text}\"*", parse_mode="Markdown")

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
            await update.message.reply_text("🔓 **ACCOUNT UNLOCKED SUCCESSFULLY!**\nYou may continue learning.", reply_markup=ReplyKeyboardRemove())
        else:
            rec_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 Reset Options", callback_data="login_forgot_pin")]])
            await update.message.reply_text(
                "❌ **INCORRECT PIN!**\n\nPlease enter your correct 4-digit secret PIN, or tap below to reset:",
                reply_markup=rec_btn,
                parse_mode="Markdown"
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
            await update.message.reply_text(f"⚠️ No student record found matching: `{text}`", parse_mode="Markdown")
            return

        keyboard = []
        for m in matches[:10]:
            sid = m.get("student_id") or f"USER_{m['user_id']}"
            keyboard.append([InlineKeyboardButton(f"👤 {m['full_name']} (ID: {sid})", callback_data=f"admin_inspect_u_{m['user_id']}")])
        
        await update.message.reply_text(f"🔍 **Search Results for '{text}':**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    if context.user_data.get("awaiting_custom_feedback"):
        context.user_data["awaiting_custom_feedback"] = False
        
        if any(bad_word in text.lower() for bad_word in NEGATIVE_WORDS):
            await update.message.reply_text("🙏 Thank you for your feedback! We are working hard to improve your learning experience.", reply_markup=ReplyKeyboardRemove())
            return

        profile = get_user_profile(user.id)
        name = profile.get("full_name") if profile else user.full_name
        save_student_feedback(user.id, name, text)
        await update.message.reply_text(f"🎉 **Feedback Received!** Thank you *{name}*:\n\n💬 *\"{text}\"*", reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
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
        BotCommand("plans", "💳 VIP Subscription Plans"),
        BotCommand("pdfreport", "📄 Export Academic PDF Report"),
        BotCommand("wrongquestions", "❌ View Wrong Questions"),
        BotCommand("unattemptedquestions", "⏭️ View Unattempted Questions"),
        BotCommand("attemptedquestions", "🎯 View Attempted Questions"),
        BotCommand("savedquestions", "💾 View Bookmarked Questions"),
        BotCommand("myprofile", "👤 View Student Profile"),
        BotCommand("editprofile", "✏️ Edit Profile Details"),
        BotCommand("mywholestate", "📊 View Performance & Rank"),
        BotCommand("toppername", "🏆 Global Leaderboard"),
        BotCommand("feedback", "💬 Submit Feedback"),
        BotCommand("reviews", "📖 View Student Reviews"),
        BotCommand("invite", "🤝 Invite Friends (+10 Quota)"),
        BotCommand("pause", "⏸️ Pause Running Quiz"),
        BotCommand("resume", "▶️ Resume Paused Quiz"),
        BotCommand("stop", "🛑 Stop Quiz Completely"),
        BotCommand("help", "🤖 Show Command Directory")
    ]
    
    await application.bot.set_my_commands(allowed_commands, scope=BotCommandScopeDefault())
    await application.bot.set_my_commands(allowed_commands, scope=BotCommandScopeAllPrivateChats())

def build_application() -> Application:
    init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(get_onboarding_handler())
    
    app.add_handler(CommandHandler("quiz", strict_quiz_command_guard))
    app.add_handler(CommandHandler("plans", plans_command))
    app.add_handler(CommandHandler("pause", pause_quiz_command))
    app.add_handler(CommandHandler("resume", resume_quiz_command))
    app.add_handler(CommandHandler("stop", stop_quiz_command))
    app.add_handler(CommandHandler("savedquestions", saved_questions_command))
    app.add_handler(CommandHandler("pdfreport", pdfreport_command))
    app.add_handler(CommandHandler("wrongquestions", wrongquestions_command))
    app.add_handler(CommandHandler("attemptedquestions", attemptedquestions_command))
    app.add_handler(CommandHandler("unattemptedquestions", unattemptedquestions_command))
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
    app.add_handler(CallbackQueryHandler(user_pdf_callback_handler, pattern="^usergenpdf_"))
    app.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^(admin_|audit_|genpdf_)"))
    app.add_handler(CallbackQueryHandler(button_router, pattern="^cmd_|^fb_|^trigger_start|^buy_plan_"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
    app.add_handler(PollAnswerHandler(handle_poll_answer))
    app.add_error_handler(global_error_handler)

    return app