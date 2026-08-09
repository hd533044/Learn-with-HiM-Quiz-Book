import time
import logging
import json
import os
import urllib.request
import base64
import asyncio
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, 
    BotCommand, BotCommandScopeDefault, BotCommandScopeAllPrivateChats, 
    BotCommandScopeAllGroupChats, ReplyKeyboardRemove
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, PollAnswerHandler, 
    MessageHandler, filters, ContextTypes
)
from psycopg2.extras import RealDictCursor
from app.config import (
    BOT_TOKEN, PRIMARY_ADMIN_ID, DAILY_QUESTION_LIMIT, PLAN_TIERS,
    RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RENDER_EXTERNAL_URL
)
from app.database import (
    init_db, get_maintenance_until, get_user_profile, 
    get_all_users, get_today_attempts, save_student_feedback, get_all_student_feedbacks,
    clear_paused_quiz_state, get_saved_questions, log_user_activity_time,
    check_and_update_inactivity, refresh_user_activity_epoch, get_db, release_db,
    get_seen_question_ids, admin_update_user_name, get_ist_date_str
)
from app.onboarding import get_onboarding_handler, start_onboarding
from app.quiz_engine import (
    launch_quiz_setup, quiz_count_callback, quiz_timer_callback, handle_poll_answer,
    pause_quiz_command, resume_quiz_command, stop_quiz_command, save_question_callback
)
from app.stats import get_overall_leaderboard, calculate_user_percentile, calculate_user_rank, get_user_performance_summary
from app.admin import admin_portal_command, admin_callback_handler
from app.pdf_generator import generate_student_pdf_report
from app.invoice_generator import generate_payment_invoice_card
from app.pyq_fetcher import fetch_pyqs_for_quiz

NEGATIVE_WORDS = ["bad", "worst", "useless", "trash", "fake", "hate", "terrible", "waste", "horrible", "fraud", "stupid", "scam"]

# In-Memory Speed Caches (TTLs)
PROFILE_CACHE = {}
CACHE_TTL = 30  # seconds

def get_cached_profile(user_id):
    now = time.time()
    if user_id in PROFILE_CACHE:
        prof, timestamp = PROFILE_CACHE[user_id]
        if now - timestamp < CACHE_TTL:
            return prof
    return None

def set_cached_profile(user_id, profile):
    PROFILE_CACHE[user_id] = (profile, time.time())

async def fetch_user_profile_fast(user_id):
    cached = get_cached_profile(user_id)
    if cached is not None:
        return cached
    prof = await asyncio.to_thread(get_user_profile, user_id)
    if prof:
        set_cached_profile(user_id, prof)
    return prof

def generate_razorpay_link_sync(user_id: int, plan_key: str) -> str:
    plan = PLAN_TIERS.get(plan_key)
    if not plan or plan["price"] == 0:
        return None

    key_id = (os.getenv("RAZORPAY_KEY_ID") or RAZORPAY_KEY_ID or "").strip()
    key_secret = (os.getenv("RAZORPAY_KEY_SECRET") or RAZORPAY_KEY_SECRET or "").strip()

    if not key_id or not key_secret:
        logging.error("[PAYMENT ERROR] Razorpay API keys are missing or unconfigured.")
        return None

    url = "https://api.razorpay.com/v1/payment_links"
    auth_str = f"{key_id}:{key_secret}"
    encoded_auth = base64.b64encode(auth_str.encode("ascii")).decode("ascii")

    profile = get_user_profile(user_id)
    raw_phone = str(profile.get("phone_number", "9123456789")) if profile else "9123456789"
    
    clean_phone = "".join(filter(str.isdigit, raw_phone))
    if len(clean_phone) > 10:
        clean_phone = clean_phone[-10:]
    elif len(clean_phone) < 10:
        clean_phone = "9123456789"

    base_render_url = (os.getenv("RENDER_EXTERNAL_URL") or "https://learn-with-him-quiz-book.onrender.com").rstrip("/")
    callback_uri = f"{base_render_url}/razorpay-webhook?user_id={user_id}&plan_key={plan_key}"

    payload = {
        "amount": int(plan["price"] * 100),
        "currency": "INR",
        "accept_partial": False,
        "description": f"Subscription - {plan_key}",
        "customer": {
            "name": "Student",
            "contact": clean_phone,
            "email": f"user{user_id}@gmail.com"
        },
        "notes": {
            "user_id": str(user_id),
            "plan_key": str(plan_key)
        },
        "callback_url": callback_uri,
        "callback_method": "get"
    }

    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=req_data, method="POST")
    req.add_header("Authorization", f"Basic {encoded_auth}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            res_body = response.read().decode("utf-8")
            res_json = json.loads(res_body)
            if response.status in (200, 201) and "short_url" in res_json:
                return res_json["short_url"]
            else:
                logging.error(f"[RAZORPAY API FAIL RESPONSE] Status: {response.status}, Body: {res_body}")
                return None
    except urllib.error.HTTPError as http_err:
        err_body = http_err.read().decode("utf-8") if http_err.fp else ""
        logging.error(f"[RAZORPAY HTTP ERROR] Code: {http_err.code}, Reason: {http_err.reason}, Details: {err_body}")
        return None
    except Exception as e:
        logging.error(f"[RAZORPAY EXCEPTION] {e}")
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
    profile = await fetch_user_profile_fast(user.id)
    if not profile or not profile.get("is_verified"):
        await send_registration_prompt(update)
        return False

    if profile.get("is_banned"):
        ban_msg = "🛑 **ACCOUNT BANNED!**\n\nYour account has been suspended by the administrator. Access to quizzes and services is restricted."
        if update.callback_query:
            await update.callback_query.answer("🛑 Account Banned!", show_alert=True)
            await update.callback_query.message.reply_text(ban_msg, parse_mode="Markdown")
        elif update.message:
            await update.message.reply_text(ban_msg, parse_mode="Markdown")
        return False

    return True

async def inactivity_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user:
        return True

    user_id = user.id
    if user_id == PRIMARY_ADMIN_ID:
        asyncio.create_task(asyncio.to_thread(refresh_user_activity_epoch, user_id))
        return True

    is_locked, diff_sec = await asyncio.to_thread(check_and_update_inactivity, user_id)
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

    m_until = await asyncio.to_thread(get_maintenance_until)
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
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user.id, 10))
    profile = await fetch_user_profile_fast(user.id)

    attempted_today = await asyncio.to_thread(get_today_attempts, user.id)
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

async def myplan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user.id, 10))
    profile = await fetch_user_profile_fast(user.id)

    today_used = await asyncio.to_thread(get_today_attempts, user.id)
    paid_bal = profile.get("paid_question_balance", 0) or 0
    base_limit = max(DAILY_QUESTION_LIMIT, paid_bal)
    allowed_limit = 10000 if user.id == PRIMARY_ADMIN_ID else base_limit + profile.get("bonus_quota", 0)
    remaining = max(0, allowed_limit - today_used)

    active_plan_name = "🎁 FREE DEMO PLAN"
    active_plan_key = "FREE_DEMO"
    for p_key, p_val in PLAN_TIERS.items():
        if p_val.get("daily_limit") == paid_bal:
            active_plan_name = p_val.get("name")
            active_plan_key = p_key
            break

    expiry = profile.get("vip_pass_expiry") or "N/A"

    msg = (
        f"💳 **YOUR CURRENT SUBSCRIPTION PLAN** 💳\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👑 **Active Plan:** `{active_plan_name}`\n"
        f"⚡ **Daily Question Limit:** `{allowed_limit} Questions / Day`\n"
        f"📊 **Used Today:** `{today_used}` / `{allowed_limit}` Qs\n"
        f"🟢 **Remaining Today:** `{remaining}` Qs Available\n"
        f"⏳ **Pass Expiry Date:** `{expiry}`\n"
        f"🎁 **Bonus Quota:** `+{profile.get('bonus_quota', 0)} Qs`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 *Upgrade your daily limit anytime by choosing a VIP Pack below:*"
    )

    btn_list = [
        [InlineKeyboardButton("💳 Upgrade / VIP Plans", callback_data="cmd_plans")]
    ]

    # Show HD Invoice Download option if user has active paid subscription
    if paid_bal > 0 or active_plan_key != "FREE_DEMO":
        btn_list.append([InlineKeyboardButton("🧾 Download HD Invoice Card", callback_data=f"download_invoice_{active_plan_key}")])

    btn_list.append([
        InlineKeyboardButton("🚀 Launch Quiz", callback_data="cmd_quiz"), 
        InlineKeyboardButton("👤 Profile Card", callback_data="cmd_profile")
    ])

    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(btn_list))

async def handle_download_invoice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    plan_key = data.replace("download_invoice_", "")
    await query.answer("⏳ Generating 2K Ultra-HD Payment Invoice Card...", show_alert=True)

    profile = await fetch_user_profile_fast(user_id)
    sid = profile.get("student_id", f"USER_{user_id}") if profile else f"USER_{user_id}"

    img_card_path = await asyncio.to_thread(generate_payment_invoice_card, user_id, plan_key, "OFFICIAL_SUBSCRIBED")

    if img_card_path and os.path.exists(img_card_path):
        with open(img_card_path, "rb") as card_file:
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=card_file,
                caption=f"💳 **OFFICIAL ULTRA-HD PAYMENT INVOICE RECEIPT** — `{sid}`\n🏷 Verified by Razorpay & Learn with HiM",
                parse_mode="Markdown"
            )
        try:
            os.remove(img_card_path)
        except Exception:
            pass
    else:
        await query.message.reply_text("⚠️ Unable to generate invoice card at the moment. Please try again later.")

async def plans_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user.id, 10))
    profile = await fetch_user_profile_fast(user.id)

    keyboard = []
    if not profile.get("demo_used"):
        keyboard.append([InlineKeyboardButton("🎁 FREE DEMO TRIAL (2 Days - 20 Qs/Day)", callback_data="buy_plan_FREE_DEMO")])

    keyboard.extend([
        [InlineKeyboardButton("📦 BRONZE (₹5 - 3 Days - 80 Qs/Day)", callback_data="buy_plan_BRONZE")],
        [InlineKeyboardButton("📦 SILVER (₹10 - 7 Days - 100 Qs/Day)", callback_data="buy_plan_SILVER")],
        [InlineKeyboardButton("📦 GOLD (₹15 - 12 Days - 120 Qs/Day)", callback_data="buy_plan_GOLD")],
        [InlineKeyboardButton("📦 DIAMOND (₹20 - 18 Days - 150 Qs/Day)", callback_data="buy_plan_DIAMOND")],
        [InlineKeyboardButton("📦 LEARNWITHHIM (₹25 - 30 Days - 250 Qs/Day)", callback_data="buy_plan_LEARNWITHHIM")],
        [InlineKeyboardButton("📦 PLATINUM (₹40 - 60 Days - 300 Qs/Day)", callback_data="buy_plan_PLATINUM")],
        [InlineKeyboardButton("📦 RUBY (₹50 - 90 Days - 400 Qs/Day)", callback_data="buy_plan_RUBY")],
        [InlineKeyboardButton("📦 MEGA PACK (₹80 - 180 Days - 500 Qs/Day)", callback_data="buy_plan_MEGA")],
    ])

    msg = (
        f"👑 **LEARN WITH HIM QUIZ BOOK — VIP MEMBERSHIP PACKS** 👑\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Select a pack below to pay securely and instantly unlock daily question limits:"
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

    profile = await fetch_user_profile_fast(user_id)

    if plan_key == "FREE_DEMO":
        if profile and profile.get("demo_used"):
            await query.answer("🛑 Demo trial can only be used ONCE per student!", show_alert=True)
            await plans_command(update, context)
            return

        from main import activate_user_subscription
        await activate_user_subscription(user_id, plan_key)
        await query.edit_message_text(
            f"🎉 **FREE DEMO TRIAL ACTIVATED!** 🎉\n\n"
            f"🎁 **Duration:** 2 Days Access\n"
            f"⚡ **Daily Limit:** 20 Questions / Day\n\n"
            f"Start practicing now with **/quiz**!",
            parse_mode="Markdown"
        )
        return

    payment_url = generate_razorpay_link_sync(user_id, plan_key)

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
        await query.message.reply_text("⚠️ Unable to generate payment link. Please verify Razorpay live keys in Render Environment Variables.")

async def pdfreport_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user.id, 10))

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
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user.id, 10))

    today_str = get_ist_date_str()

    def fetch_today_attempts():
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "SELECT * FROM quiz_attempts WHERE user_id = %s AND attempt_date = %s ORDER BY id DESC", 
            (user.id, today_str)
        )
        rows = cursor.fetchall()
        cursor.close()
        release_db(conn)
        return rows

    attempts = await asyncio.to_thread(fetch_today_attempts)

    lines = [
        f"❌ **YOUR INCORRECT QUESTIONS LOG (TODAY: {today_str})** ❌",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    ]

    found_wrong = False
    wrong_count = 0

    for a in attempts:
        ad = dict(a) if not isinstance(a, dict) else a
        dt = ad.get("attempt_timestamp") or ad.get("attempt_date") or "Today"
        
        raw_details = ad.get("details_json")
        details = []
        if raw_details:
            try:
                details = json.loads(raw_details) if isinstance(raw_details, str) else raw_details
            except Exception:
                details = []

        if isinstance(details, list):
            wrong_items = [q for q in details if isinstance(q, dict) and str(q.get("status", "")).upper() == "WRONG"]
            if wrong_items:
                found_wrong = True
                lines.append(f"📅 **Quiz Session at:** `{dt}`")
                for q_item in wrong_items:
                    wrong_count += 1
                    q_text = q_item.get("question_text") or q_item.get("question") or "N/A"
                    ans_text = q_item.get("correct_answer_text") or q_item.get("correct_answer") or "N/A"
                    lines.append(f" {wrong_count}. ❌ `{q_text}`\n    👉 **Correct Answer:** `{ans_text}`")
                lines.append("")

    if not found_wrong:
        lines.append("🎉 *Zero wrong questions logged for today! Excellent performance.*")

    msg = "\n".join(lines)
    if len(msg) > 4000:
        msg = msg[:3950] + "\n\n*(Truncated due to Telegram message length limit)*"

    nav = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Launch Quiz", callback_data="cmd_quiz"), InlineKeyboardButton("👤 Profile", callback_data="cmd_profile")]
    ])
    await send_response(update, msg, reply_markup=nav)

async def attemptedquestions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user.id, 10))

    today_str = get_ist_date_str()

    def fetch_today_attempts():
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "SELECT * FROM quiz_attempts WHERE user_id = %s AND attempt_date = %s ORDER BY id DESC", 
            (user.id, today_str)
        )
        rows = cursor.fetchall()
        cursor.close()
        release_db(conn)
        return rows

    attempts = await asyncio.to_thread(fetch_today_attempts)

    lines = [
        f"🎯 **YOUR TODAY'S ATTEMPTED QUESTIONS LOG ({today_str})** 🎯",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    ]

    found_any = False
    question_counter = 0

    for a in attempts:
        ad = dict(a) if not isinstance(a, dict) else a
        dt = ad.get("attempt_timestamp") or ad.get("attempt_date") or "Today"
        
        raw_details = ad.get("details_json")
        details = []
        if raw_details:
            try:
                details = json.loads(raw_details) if isinstance(raw_details, str) else raw_details
            except Exception:
                details = []

        if isinstance(details, list) and details:
            found_any = True
            lines.append(f"📅 **Quiz Session at:** `{dt}`")
            for q_item in details:
                if isinstance(q_item, dict):
                    question_counter += 1
                    q_text = q_item.get("question_text") or q_item.get("question") or "N/A"
                    ans_text = q_item.get("correct_answer_text") or q_item.get("correct_answer") or "N/A"
                    st = str(q_item.get("status", "")).upper()
                    
                    status_icon = "✅" if st == "CORRECT" else "❌" if st == "WRONG" else "⏭"
                    lines.append(f" {question_counter}. {status_icon} `{q_text}`\n    👉 **Correct Ans:** `{ans_text}`")
            lines.append("")

    if not found_any:
        lines.append("*No question attempt logs found for today. Type /quiz to start practicing!*")

    msg = "\n".join(lines)
    if len(msg) > 4000:
        msg = msg[:3950] + "\n\n*(Truncated due to Telegram message length limit)*"

    nav = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Launch Quiz", callback_data="cmd_quiz"), InlineKeyboardButton("👤 Profile", callback_data="cmd_profile")]
    ])
    await send_response(update, msg, reply_markup=nav)

async def unattemptedquestions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user.id, 10))

    seen_ids = await asyncio.to_thread(get_seen_question_ids, user.id)
    all_qs = await asyncio.to_thread(fetch_pyqs_for_quiz, 1000, set())
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

    pdf_file = await asyncio.to_thread(generate_student_pdf_report, user_id, filter_mode)
    profile = await fetch_user_profile_fast(user_id)
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
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user.id, 10))

    msg = (
        "🤖 **LEARN WITH HIM QUIZ BOOK — COMMAND DIRECTORY** 🤖\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Tap any interactive button below or use the blue **[≡ Menu]** button:\n"
    )

    buttons = [
        [InlineKeyboardButton("🚀 Launch Quiz (/quiz)", callback_data="cmd_quiz"), InlineKeyboardButton("💳 My Current Plan (/myplan)", callback_data="cmd_myplan")],
        [InlineKeyboardButton("💳 VIP Plans (/plans)", callback_data="cmd_plans"), InlineKeyboardButton("📄 PDF Reports (/pdfreport)", callback_data="cmd_pdfreport")],
        [InlineKeyboardButton("💾 Bookmarks (/savedquestions)", callback_data="cmd_savedquestions"), InlineKeyboardButton("❌ Wrong Qs (/wrongquestions)", callback_data="cmd_wrongquestions")],
        [InlineKeyboardButton("🎯 Attempted Qs (/attemptedquestions)", callback_data="cmd_attemptedquestions"), InlineKeyboardButton("⏭️ Unattempted Qs (/unattemptedquestions)", callback_data="cmd_unattemptedquestions")],
        [InlineKeyboardButton("👤 My Profile (/myprofile)", callback_data="cmd_profile"), InlineKeyboardButton("✏️ Edit Profile (/editprofile)", callback_data="cmd_editprofile")],
        [InlineKeyboardButton("📊 My Rank (/mywholestate)", callback_data="cmd_wholestate"), InlineKeyboardButton("🏆 Leaderboard (/toppername)", callback_data="cmd_toppers")],
        [InlineKeyboardButton("💬 Submit Feedback (/feedback)", callback_data="cmd_feedback"), InlineKeyboardButton("📖 Reviews (/reviews)", callback_data="cmd_viewfeedbacks")],
        [InlineKeyboardButton("🤝 Invite Friends (/invite)", callback_data="cmd_referral")]
    ]

    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def saved_questions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user.id, 10))
    saved = await asyncio.to_thread(get_saved_questions, user.id)
    
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
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user.id, 10))
    profile = await fetch_user_profile_fast(user.id)

    today_used = await asyncio.to_thread(get_today_attempts, user.id)
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
        [InlineKeyboardButton("💳 My Plan", callback_data="cmd_myplan"), InlineKeyboardButton("💳 VIP Plans", callback_data="cmd_plans")],
        [InlineKeyboardButton("💾 Bookmarks", callback_data="cmd_savedquestions"), InlineKeyboardButton("✏️ Edit Profile", callback_data="cmd_editprofile")],
        [InlineKeyboardButton("🤝 Invite Friends", callback_data="cmd_referral")]
    ]

    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def wholestate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user.id, 10))
    profile = await fetch_user_profile_fast(user.id)

    perf = await asyncio.to_thread(get_user_performance_summary, user.id)
    rank = await asyncio.to_thread(calculate_user_rank, user.id)
    percentile = await asyncio.to_thread(calculate_user_percentile, user.id)
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
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user.id, 10))
    toppers = await asyncio.to_thread(get_overall_leaderboard, 10)
    
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
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user.id, 10))

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
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user.id, 10))
    feedbacks = await asyncio.to_thread(get_all_student_feedbacks, 15)

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
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user.id, 10))
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
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user.id, 5))

    if data == "trigger_start":
        await start_onboarding(update, context)
        return

    if not await check_user_registration(update): return

    if data == "cmd_quiz":
        profile = await fetch_user_profile_fast(user.id)
        attempted_today = await asyncio.to_thread(get_today_attempts, user.id)
        
        paid_bal = profile.get("paid_question_balance", 0) or 0 if profile else 0
        base_limit = max(DAILY_QUESTION_LIMIT, paid_bal)
        allowed_limit = 10000 if user.id == PRIMARY_ADMIN_ID else base_limit + (profile.get("bonus_quota", 0) if profile else 0)

        if attempted_today >= allowed_limit:
            await query.answer("🛑 Daily Limit Exhausted!", show_alert=True)
            return
        await launch_quiz_setup(update, context)
    elif data == "cmd_myplan":
        await myplan_command(update, context)
    elif data == "cmd_plans":
        await plans_command(update, context)
    elif data.startswith("buy_plan_"):
        await handle_buy_plan_callback(update, context)
    elif data.startswith("download_invoice_"):
        await handle_download_invoice_callback(update, context)
    elif data == "cmd_help":
        await help_command(update, context)
    elif data == "cmd_pdfreport":
        await pdfreport_command(update, context)
    elif data == "cmd_wrongquestions" or data == "cmd_wrong_qs":
        await wrongquestions_command(update, context)
    elif data == "cmd_attemptedquestions" or data == "cmd_attempted_qs":
        await attemptedquestions_command(update, context)
    elif data == "cmd_unattemptedquestions" or data == "cmd_unattempted_qs":
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
        await asyncio.to_thread(clear_paused_quiz_state, user.id)
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
        profile = await fetch_user_profile_fast(user.id)
        name = profile.get("full_name") if profile else user.full_name
        asyncio.create_task(asyncio.to_thread(save_student_feedback, user.id, name, fb_text))
        await query.edit_message_text(f"🎉 **Thank you, {name}!** Your review has been saved:\n\n💬 *\"{fb_text}\"*", parse_mode="Markdown")

    elif data == "fb_custom":
        context.user_data["awaiting_custom_feedback"] = True
        await query.edit_message_text("✍️ Please reply with your custom feedback below:")

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    if context.user_data.get("is_account_locked"):
        profile = await fetch_user_profile_fast(user.id)
        if profile and profile.get("pin") == text:
            context.user_data["is_account_locked"] = False
            asyncio.create_task(asyncio.to_thread(refresh_user_activity_epoch, user.id))
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
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user.id, 10))

    if user.id == PRIMARY_ADMIN_ID and context.user_data.get("awaiting_admin_editname"):
        target_uid = context.user_data.pop("awaiting_admin_editname")
        asyncio.create_task(asyncio.to_thread(admin_update_user_name, target_uid, text))
        await update.message.reply_text(f"✅ **Student name updated to:** `{text}` for user `{target_uid}`!", parse_mode="Markdown")
        return

    if user.id == PRIMARY_ADMIN_ID and context.user_data.get("awaiting_admin_search"):
        context.user_data["awaiting_admin_search"] = False
        all_u = await asyncio.to_thread(get_all_users)
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

        profile = await fetch_user_profile_fast(user.id)
        name = profile.get("full_name") if profile else user.full_name
        asyncio.create_task(asyncio.to_thread(save_student_feedback, user.id, name, text))
        await update.message.reply_text(f"🎉 **Feedback Received!** Thank you *{name}*:\n\n💬 *\"{text}\"*", reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
        return

    if context.user_data.get("awaiting_broadcast"):
        context.user_data["awaiting_broadcast"] = False
        users = await asyncio.to_thread(get_all_users)
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
        BotCommand("myplan", "💵 Subscriptions"),
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
    app.add_handler(CommandHandler("myplan", myplan_command))
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

    app.add_handler(CallbackQueryHandler(quiz_count_callback, pattern="^qcount_"))
    app.add_handler(CallbackQueryHandler(quiz_timer_callback, pattern="^qtimer_"))
    app.add_handler(CallbackQueryHandler(user_pdf_callback_handler, pattern="^usergenpdf_"))
    app.add_handler(CallbackQueryHandler(handle_download_invoice_callback, pattern="^download_invoice_"))
    app.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^(admin_|audit_|genpdf_)"))
    app.add_handler(CallbackQueryHandler(button_router, pattern="^cmd_|^fb_|^trigger_start|^buy_plan_"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
    app.add_handler(PollAnswerHandler(handle_poll_answer))
    app.add_error_handler(global_error_handler)

    return app