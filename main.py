import asyncio
import hmac
import hashlib
import json
import logging
import os
import warnings
from datetime import datetime, timedelta
import pytz
from aiohttp import web

try:
    import razorpay
    HAS_RAZORPAY = True
except ImportError:
    HAS_RAZORPAY = False

warnings.filterwarnings("ignore")
from app.telegram_bot import build_application, PROFILE_CACHE
from app.config import (
    RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET, 
    PLAN_TIERS
)
from app.database import sync_user_json_profile, get_ist_timestamp_str, get_db, release_db, get_user_profile
from app.quiz_engine import get_random_questions

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

razorpay_client = None
if HAS_RAZORPAY and RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
    try:
        razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
        logging.info("Razorpay Client Initialized Successfully.")
    except Exception as e:
        logging.error(f"Failed to initialize Razorpay Client: {e}")

bot_app_instance = None
SENT_EXPIRY_REMINDERS = set()
LAST_QUIZ_BROADCAST_DATE = ""


async def activate_user_subscription(user_id: int, plan_key: str, payment_id: str = "OFFICIAL_SUBSCRIBED"):
    """
    ACTIVATION ENGINE:
    1. Stacks daily question limit onto current paid_question_balance.
    2. Extends pass validity.
    3. Logs transaction permanently into payment_transactions with unique payment_id constraint.
    4. Invalidates local memory cache for instant synchronization.
    """
    plan = PLAN_TIERS.get(plan_key)
    if not plan:
        logging.error(f"[ACTIVATION ERROR] Invalid plan key received: {plan_key}")
        return False

    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    
    expiry_dt = now + timedelta(days=plan["days"])
    expiry_str = expiry_dt.strftime("%Y-%m-%d %H:%M:%S IST")
    payment_time_str = now.strftime("%d %b %Y, %I:%M %p IST")

    profile = get_user_profile(user_id) or {}
    current_bal = profile.get("paid_question_balance", 0) or 0
    new_bal = current_bal + plan["daily_limit"]

    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # 1. Update user record balance & expiry
        if plan_key == "FREE_DEMO":
            cursor.execute(
                "UPDATE users SET paid_question_balance = %s, vip_pass_expiry = %s, payment_id = %s, payment_timestamp = %s, demo_used = 1 WHERE user_id = %s",
                (new_bal, expiry_str, payment_id, payment_time_str, user_id)
            )
        else:
            cursor.execute(
                "UPDATE users SET paid_question_balance = %s, vip_pass_expiry = %s, payment_id = %s, payment_timestamp = %s WHERE user_id = %s",
                (new_bal, expiry_str, payment_id, payment_time_str, user_id)
            )

        # 2. Record transaction history entry
        try:
            cursor.execute(
                """
                INSERT INTO payment_transactions 
                (user_id, payment_id, plan_key, plan_name, amount_paid, daily_quota, validity_days, created_at, expiry_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (payment_id) DO NOTHING
                """,
                (user_id, payment_id, plan_key, plan["name"], plan["price"], plan["daily_limit"], plan["days"], payment_time_str, expiry_str)
            )
        except Exception as tx_err:
            logging.warning(f"[TX LOG FALLBACK] Fallback insertion without expiry_at: {tx_err}")
            cursor.execute(
                """
                INSERT INTO payment_transactions 
                (user_id, payment_id, plan_key, plan_name, amount_paid, daily_quota, validity_days, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (payment_id) DO NOTHING
                """,
                (user_id, payment_id, plan_key, plan["name"], plan["price"], plan["daily_limit"], plan["days"], payment_time_str)
            )

        conn.commit()
        cursor.close()
        release_db(conn)

        # Flush fast memory cache so /myplan & /myprofile reflect instantly
        PROFILE_CACHE.pop(user_id, None)
        sync_user_json_profile(user_id)
        
        logging.info(f"[SUCCESS] Activated and stacked plan {plan_key} for User ID: {user_id}. New Quota: {new_bal}")
        return True
    except Exception as e:
        if conn:
            release_db(conn)
        logging.error(f"[DATABASE ERROR] Failed activating plan for user {user_id}: {e}")
        return False


async def send_payment_invoice_telegram(user_id: int, plan_key: str, payment_id: str = "OFFICIAL_SUBSCRIBED"):
    """
    Pushes an instant, celebratory text invoice directly into the user's Telegram chat.
    Guaranteed delivery with interactive action buttons.
    """
    if not bot_app_instance:
        logging.error("[TELEGRAM PUSH ERROR] bot_app_instance is uninitialized.")
        return

    from telegram import InlineKeyboardMarkup, InlineKeyboardButton

    plan_info = PLAN_TIERS.get(plan_key, {})
    plan_name = plan_info.get('name', plan_key)

    profile = await asyncio.to_thread(get_user_profile, user_id) or {}
    sid = profile.get("student_id", f"USER_{user_id}")
    orig_payment_time = profile.get("payment_timestamp") or get_ist_timestamp_str()
    total_quota = profile.get("paid_question_balance", 0)
    expiry_date = profile.get("vip_pass_expiry", "N/A")

    is_admin_grant = "ADMIN" in str(payment_id).upper()

    if is_admin_grant:
        broadcast_msg = (
            f"🎁 **SPECIAL ANNOUNCEMENT: VIP PLAN GRANTED!** 🎁\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎉 **Himanshu Sir has granted you the {plan_name}!**\n\n"
            f"⚡ **New Daily Limit:** `{total_quota} Questions / Day`\n"
            f"⏳ **VIP Pass Expiry:** `{expiry_date}`\n"
            f"🧾 **Reference ID:** `{payment_id}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✨ You can now attempt more practice questions every single day!\n"
            f"🚀 Tap **/quiz** below to launch your session now!"
        )
    else:
        broadcast_msg = (
            f"🥳 **PAYMENT CONFIRMED & PLAN CREDITED!** 🥳\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎉 **Purchased Pack:** `{plan_name}`\n"
            f"⚡ **Stacked Daily Quota:** `{total_quota} Questions / Day`\n"
            f"⏳ **New VIP Pass Expiry:** `{expiry_date}`\n\n"
            f"🧾 **OFFICIAL PAYMENT SUCCESS INVOICE**\n"
            f"• **Student ID:** `{sid}`\n"
            f"• **Amount Paid:** ₹{plan_info.get('price', 0)} INR\n"
            f"• **Txn / Payment ID:** `{payment_id}`\n"
            f"• **Payment Timestamp:** `{orig_payment_time}`\n"
            f"• **Added Validity:** `{plan_info.get('days')} Days Access`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Tap **/myplan** anytime to check your active quota breakdown.\n"
            f"🚀 Tap **/quiz** to launch your practice session now!"
        )
    
    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Launch Quiz Now", callback_data="cmd_quiz"), InlineKeyboardButton("💳 My Plan", callback_data="cmd_myplan")]
    ])

    try:
        await bot_app_instance.bot.send_message(
            chat_id=user_id,
            text=broadcast_msg,
            reply_markup=btn,
            parse_mode="Markdown"
        )
        logging.info(f"[INVOICE/BROADCAST DELIVERED] Successfully sent to user {user_id}")
    except Exception as err:
        logging.error(f"[DELIVERY ERROR] Failed to push notification to user {user_id}: {err}")


async def scheduled_expiry_reminder_check():
    """
    BACKGROUND WORKER:
    Checks expiration dates for ALL users (both Paid VIP and FREE DEMO users) and sends reminders:
    - 24 Hours (1 Day) before expiry
    - 6 Hours before expiry
    - 1 Hour before expiry
    """
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    
    ist = pytz.timezone("Asia/Kolkata")
    
    while True:
        await asyncio.sleep(120)  # Check every 2 minutes
        if not bot_app_instance:
            continue

        conn = None
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, full_name, paid_question_balance, vip_pass_expiry, demo_used FROM users WHERE vip_pass_expiry IS NOT NULL AND is_banned = 0")
            users = cursor.fetchall()
            cursor.close()
            release_db(conn)

            now = datetime.now(ist)

            for u in users:
                uid = u['user_id']
                name = u['full_name'] or "Student"
                exp_str = u['vip_pass_expiry']
                is_demo = bool(u.get('demo_used') and u.get('paid_question_balance', 0) <= 20)

                if not exp_str:
                    continue

                try:
                    exp_dt = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S IST")
                    exp_dt = ist.localize(exp_dt) if exp_dt.tzinfo is None else exp_dt
                except Exception:
                    continue

                diff_seconds = (exp_dt - now).total_seconds()

                if diff_seconds <= 0:
                    continue  # Expired

                hours_left = diff_seconds / 3600.0

                # Milestone 1: 24 Hours (1 Day) Reminder
                if 23.0 <= hours_left <= 25.0:
                    rem_key = f"{uid}_24h"
                    if rem_key not in SENT_EXPIRY_REMINDERS:
                        SENT_EXPIRY_REMINDERS.add(rem_key)
                        
                        if is_demo:
                            msg = (
                                f"⏰ **FREE DEMO TRIAL EXPIRING IN 24 HOURS!** ⏰\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"Hello **{name}**, your Free Demo Trial pass will expire in **24 Hours** (1 Day)!\n\n"
                                f"⏳ **Demo Expiry:** `{exp_str}`\n\n"
                                f"💡 **Upgrade to VIP:** Upgrade to a paid VIP Plan now via **/plans** to unlock higher daily questions and keep practicing uninterrupted!"
                            )
                        else:
                            msg = (
                                f"⏰ **VIP PASS EXPIRING IN 24 HOURS (1 DAY)!** ⏰\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"Hello **{name}**, your VIP Subscription Pass will expire in **24 Hours**.\n\n"
                                f"⏳ **Pass Expiry Date:** `{exp_str}`\n"
                                f"⚡ **Current Daily Quota:** `{u['paid_question_balance']} Qs/Day`\n\n"
                                f"💡 **Recharge Now:** Tap **/plans** to renew or upgrade your plan to prevent any interruption in your daily quiz practice!"
                            )
                            
                        btn = InlineKeyboardMarkup([[InlineKeyboardButton("💳 Recharge / Upgrade VIP Plan", callback_data="cmd_plans")]])
                        try:
                            await bot_app_instance.bot.send_message(chat_id=uid, text=msg, reply_markup=btn, parse_mode="Markdown")
                            logging.info(f"[EXPIRY REMINDER 24H SENT] Delivered to user {uid}")
                        except Exception as e:
                            logging.error(f"[EXPIRY REMINDER ERROR] {e}")

                # Milestone 2: 6 Hours Reminder
                elif 5.5 <= hours_left <= 6.5:
                    rem_key = f"{uid}_6h"
                    if rem_key not in SENT_EXPIRY_REMINDERS:
                        SENT_EXPIRY_REMINDERS.add(rem_key)
                        
                        if is_demo:
                            msg = (
                                f"⏳ **FREE DEMO TRIAL EXPIRING IN 6 HOURS!** ⏳\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"Hello **{name}**, your Free Demo Trial pass is expiring in **6 Hours**!\n\n"
                                f"⏳ **Exact Expiry:** `{exp_str}`\n\n"
                                f"🔔 **Recharge to VIP:** Tap **/plans** to upgrade to a VIP plan now and retain your daily quota!"
                            )
                        else:
                            msg = (
                                f"⏳ **VIP PASS EXPIRING IN 6 HOURS!** ⏳\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"Hello **{name}**, your VIP Subscription Pass is expiring in **6 Hours**!\n\n"
                                f"⏳ **Exact Expiry:** `{exp_str}`\n"
                                f"⚡ **Current Quota:** `{u['paid_question_balance']} Qs/Day`\n\n"
                                f"🔔 **Avoid Disruption:** Recharge now to continue practicing uninterrupted!"
                            )

                        btn = InlineKeyboardMarkup([[InlineKeyboardButton("💳 Recharge Plan Now", callback_data="cmd_plans")]])
                        try:
                            await bot_app_instance.bot.send_message(chat_id=uid, text=msg, reply_markup=btn, parse_mode="Markdown")
                            logging.info(f"[EXPIRY REMINDER 6H SENT] Delivered to user {uid}")
                        except Exception as e:
                            logging.error(f"[EXPIRY REMINDER ERROR] {e}")

                # Milestone 3: 1 Hour Urgent Reminder
                elif 0.8 <= hours_left <= 1.2:
                    rem_key = f"{uid}_1h"
                    if rem_key not in SENT_EXPIRY_REMINDERS:
                        SENT_EXPIRY_REMINDERS.add(rem_key)
                        
                        if is_demo:
                            msg = (
                                f"🚨 **FINAL NOTICE: FREE DEMO EXPIRING IN 1 HOUR!** 🚨\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"Attention **{name}**, your Free Demo Trial pass expires in **less than 1 Hour**!\n\n"
                                f"⏳ **Pass Expiry:** `{exp_str}`\n\n"
                                f"⚡ **Upgrade Instantly:** Tap below to upgrade to a VIP Plan and keep practicing!"
                            )
                        else:
                            msg = (
                                f"🚨 **FINAL NOTICE: VIP PASS EXPIRING IN 1 HOUR!** 🚨\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"Attention **{name}**, your VIP Pass will expire in **less than 1 Hour**!\n\n"
                                f"⏳ **Pass Expiry:** `{exp_str}`\n"
                                f"⚡ **Daily Quota:** `{u['paid_question_balance']} Qs/Day`\n\n"
                                f"⚡ **Recharge Immediately:** Tap below to renew your plan and keep your daily limit active!"
                            )

                        btn = InlineKeyboardMarkup([[InlineKeyboardButton("⚡ Instant Recharge Now", callback_data="cmd_plans")]])
                        try:
                            await bot_app_instance.bot.send_message(chat_id=uid, text=msg, reply_markup=btn, parse_mode="Markdown")
                            logging.info(f"[EXPIRY REMINDER 1H SENT] Delivered to user {uid}")
                        except Exception as e:
                            logging.error(f"[EXPIRY REMINDER ERROR] {e}")

        except Exception as err:
            if conn:
                release_db(conn)
            logging.error(f"[SCHEDULED CHECK EXCEPTION] {err}")


async def scheduled_daily_quiz_reminder():
    """
    AUTOMATED 4X DAILY PRACTICE BROADCASTER:
    Broadcasting daily at 09:00 AM, 12:30 PM, 05:00 PM & 09:00 PM IST:
    "IT IS YOUR QUIZZZ TIME GUYZZZ, KINDLY ATTEMPT AND ANALYSIS THE QUIZZ!! 😁💯"
    """
    global LAST_QUIZ_BROADCAST_DATE
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    
    ist = pytz.timezone("Asia/Kolkata")

    while True:
        await asyncio.sleep(60)  # Check every minute
        if not bot_app_instance:
            continue

        now = datetime.now(ist)
        today_date_str = now.strftime("%Y-%m-%d")
        current_hour = now.hour
        current_minute = now.minute

        # 4 Scheduled Times: 09:00 AM (9:00), 12:30 PM (12:30), 05:00 PM (17:00), 09:00 PM (21:00) IST
        is_time_slot = (
            (current_hour == 9 and current_minute == 0) or
            (current_hour == 12 and current_minute == 30) or
            (current_hour == 17 and current_minute == 0) or
            (current_hour == 21 and current_minute == 0)
        )

        if is_time_slot:
            broadcast_key = f"{today_date_str}_{current_hour}_{current_minute}"
            
            if LAST_QUIZ_BROADCAST_DATE != broadcast_key:
                LAST_QUIZ_BROADCAST_DATE = broadcast_key
                
                conn = None
                try:
                    conn = get_db()
                    cursor = conn.cursor()
                    cursor.execute("SELECT user_id FROM users WHERE is_banned = 0 AND is_verified = 1")
                    users = cursor.fetchall()
                    cursor.close()
                    release_db(conn)

                    reminder_text = (
                        f"📢 **DAILY QUIZ PRACTICE REMINDER** 📢\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"IT IS YOUR QUIZZZ TIME GUYZZZ, KINDLY ATTEMPT AND ANALYSIS THE QUIZZ!! 😁💯\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"⚡ Daily consistent practice makes perfect! Tap the button below to launch your session now:"
                    )
                    btn = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Launch Quiz Now", callback_data="cmd_quiz")]])

                    sent_count = 0
                    for u in users:
                        try:
                            await bot_app_instance.bot.send_message(
                                chat_id=u['user_id'], 
                                text=reminder_text, 
                                reply_markup=btn, 
                                parse_mode="Markdown"
                            )
                            sent_count += 1
                        except Exception:
                            pass

                    logging.info(f"[DAILY 4X QUIZ BROADCAST SENT] Delivered to {sent_count} registered users at {now.strftime('%I:%M %p IST')}")
                except Exception as err:
                    if conn:
                        release_db(conn)
                    logging.error(f"[DAILY BROADCAST ERROR] {err}")


async def handle_ping(request):
    return web.Response(text="Bot Engine & Payment Gateway Active")


async def handle_mini_app(request):
    """Serves the Web Mini App HTML Interface"""
    try:
        with open("templates/index.html", "r", encoding="utf-8") as f:
            html = f.read()
        return web.Response(text=html, content_type="text/html")
    except Exception as e:
        return web.Response(text=f"Error loading App UI: {e}", status=500)


async def handle_get_questions(request):
    """API Endpoint for Mini App to fetch practice questions dynamically"""
    user_id = request.query.get("user_id")
    questions = get_random_questions(count=10)
    return web.json_response({"questions": questions})


async def handle_submit_quiz(request):
    """API Endpoint for Mini App to record quiz score and notify student"""
    data = await request.json()
    user_id = data.get("user_id")
    score = data.get("score")
    total = data.get("total")

    if bot_app_instance and user_id:
        msg = (
            f"🎉 **MINI APP SESSION COMPLETED!** 🎉\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 **Score:** `{score} / {total}`\n"
            f"🏆 Great attempt! Tap **/quiz** to launch another session anytime."
        )
        try:
            await bot_app_instance.bot.send_message(chat_id=int(user_id), text=msg, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"[MINI APP SUBMIT NOTIFICATION ERROR] {e}")

    return web.json_response({"status": "success"})


async def handle_razorpay_callback_get(request):
    params = request.query
    razorpay_payment_id = params.get("razorpay_payment_id") or params.get("razorpay_payment_link_id") or "OFFICIAL_SUBSCRIBED"

    raw_user_id = params.get("user_id") or params.get("notes[user_id]")
    plan_key = params.get("plan_key") or params.get("notes[plan_key]")

    logging.info(f"[GET CALLBACK] Captured params: user_id={raw_user_id}, plan_key={plan_key}, payment_id={razorpay_payment_id}")

    if raw_user_id and plan_key:
        try:
            uid = int(raw_user_id)
            activated = await activate_user_subscription(uid, plan_key, razorpay_payment_id)
            if activated:
                await send_payment_invoice_telegram(uid, plan_key, razorpay_payment_id)
        except Exception as e:
            logging.error(f"[GET CALLBACK EXCEPTION] {e}")

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Payment Successful - Learn with HiM Quiz Book</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; background: #0f172a; color: #f8fafc; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; padding: 20px; }}
            .card {{ background: #1e293b; border-radius: 16px; padding: 32px; max-width: 420px; width: 100%; text-align: center; border: 1px solid #334155; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }}
            .icon {{ font-size: 56px; margin-bottom: 16px; }}
            h2 {{ color: #38bdf8; margin-bottom: 8px; }}
            p {{ color: #94a3b8; font-size: 15px; line-height: 1.5; }}
            .id-box {{ background: #0f172a; padding: 12px; border-radius: 8px; font-family: monospace; color: #38bdf8; margin: 16px 0; word-break: break-all; }}
            .btn {{ display: inline-block; background: #2563eb; color: white; text-decoration: none; padding: 12px 24px; border-radius: 8px; font-weight: bold; margin-top: 16px; }}
        </style>
    </head>
    <body>
        <div class="card">
            <div class="icon">🎉</div>
            <h2>Payment Successful!</h2>
            <p>Your plan has been credited and an official invoice has been pushed to your Telegram chat.</p>
            <div class="id-box">Payment ID: {razorpay_payment_id}</div>
            <a href="https://t.me/LearnwithHiMQuizzzbot" class="btn">Return to Telegram Bot</a>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html_content, content_type="text/html")


async def handle_razorpay_webhook(request):
    try:
        body = await request.text()
        signature = request.headers.get("X-Razorpay-Signature", "")

        if RAZORPAY_WEBHOOK_SECRET:
            expected_signature = hmac.new(
                RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
                body.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected_signature, signature):
                return web.Response(status=400, text="Invalid Signature")

        data = json.loads(body)
        event = data.get("event")

        if event in ("payment_link.paid", "payment.captured"):
            payload = data.get("payload", {}).get("payment_link", {}).get("entity", {}) or data.get("payload", {}).get("payment", {}).get("entity", {})
            notes = payload.get("notes", {})
            
            user_id = notes.get("user_id") or payload.get("notes", {}).get("user_id")
            plan_key = notes.get("plan_key") or payload.get("notes", {}).get("plan_key")
            payment_id = payload.get("payment_id") or payload.get("id") or "OFFICIAL_SUBSCRIBED"

            if user_id and plan_key:
                uid = int(user_id)
                success = await activate_user_subscription(uid, plan_key, payment_id)
                if success:
                    await send_payment_invoice_telegram(uid, plan_key, payment_id)

        return web.Response(status=200, text="Webhook Processed")
    except Exception as e:
        logging.error(f"[WEBHOOK EXCEPTION] {e}")
        return web.Response(status=500, text=str(e))


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/ping", handle_ping)
    app.router.add_get("/app", handle_mini_app)
    app.router.add_get("/api/get-questions", handle_get_questions)
    app.router.add_post("/api/submit-quiz", handle_submit_quiz)
    app.router.add_get("/razorpay-webhook", handle_razorpay_callback_get)
    app.router.add_post("/razorpay-webhook", handle_razorpay_webhook)

    port = int(os.getenv("PORT", "8080"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


async def run_bot():
    global bot_app_instance
    await start_web_server()
    app = build_application()
    bot_app_instance = app

    await app.initialize()
    await app.start()
    await app.bot.delete_webhook(drop_pending_updates=True)
    await app.updater.start_polling(drop_pending_updates=True)

    # Launch background automated expiry reminder and daily quiz notification workers
    asyncio.create_task(scheduled_expiry_reminder_check())
    asyncio.create_task(scheduled_daily_quiz_reminder())

    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def main():
    try:
        asyncio.run(run_bot())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()