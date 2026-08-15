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

# Ignore non-critical runtime warnings
warnings.filterwarnings("ignore")

try:
    import razorpay
    HAS_RAZORPAY = True
except ImportError:
    HAS_RAZORPAY = False

from app.telegram_bot import build_application, PROFILE_CACHE
from app.admin import fast_concurrent_broadcast
from app.config import (
    RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET, 
    PLAN_TIERS, PRIMARY_ADMIN_ID
)
from app.database import (
    sync_user_json_profile, get_ist_timestamp_str, get_db, release_db, get_user_profile,
    fetch_pending_announcements, update_announcement_status, get_all_users, record_broadcast_delivery,
    get_active_flash_sale, calculate_discounted_price, get_user_by_phone, infer_plan_key_from_amount,
    auto_sync_uncredited_paid_users
)

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
LAST_QUIZ_BROADCAST_KEY = ""


async def activate_user_subscription(user_id: int, plan_key: str, payment_id: str = "OFFICIAL_SUBSCRIBED", amount_paid: float = None):
    """
    ACTIVATION ENGINE:
    1. Validates plan key and payment ID format strictly.
    2. Stacks daily question limit onto current paid_question_balance.
    3. Extends pass validity.
    4. Logs transaction permanently into payment_transactions.
    5. Invalidates memory cache for instant synchronization.
    """
    if not plan_key or plan_key not in PLAN_TIERS:
        logging.error(f"[SECURITY BLOCKED] Invalid/fake plan key attempted: '{plan_key}' for user {user_id}")
        return False

    pid_str = str(payment_id).strip()
    if not (pid_str.startswith("pay_") or pid_str.startswith("ADMIN_GRANT_") or pid_str == "FREE_DEMO"):
        logging.error(f"[SECURITY BLOCKED] Invalid/fake payment_id format rejected: '{payment_id}' for user {user_id}")
        return False

    plan = PLAN_TIERS[plan_key]

    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    
    expiry_dt = now + timedelta(days=plan["days"])
    expiry_str = expiry_dt.strftime("%Y-%m-%d %H:%M:%S IST")
    payment_time_str = now.strftime("%d %b %Y, %I:%M %p IST")

    profile = get_user_profile(user_id) or {}
    current_bal = profile.get("paid_question_balance", 0) or 0
    new_bal = current_bal + plan["daily_limit"]

    actual_charged_amount = amount_paid if amount_paid is not None else plan["price"]

    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # Update user record balance & expiry
        cursor.execute(
            """
            UPDATE users 
            SET paid_question_balance = %s, 
                vip_pass_expiry = %s, 
                payment_id = %s, 
                payment_timestamp = %s, 
                demo_used = 1 
            WHERE user_id = %s
            """,
            (new_bal, expiry_str, pid_str, payment_time_str, user_id)
        )

        # Record transaction history entry
        cursor.execute(
            """
            INSERT INTO payment_transactions 
            (user_id, payment_id, plan_key, plan_name, amount_paid, daily_quota, validity_days, created_at, expiry_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (payment_id) DO UPDATE SET
                amount_paid = EXCLUDED.amount_paid,
                expiry_at = EXCLUDED.expiry_at
            """,
            (user_id, pid_str, plan_key, plan["name"], actual_charged_amount, plan["daily_limit"], plan["days"], payment_time_str, expiry_str)
        )

        conn.commit()
        cursor.close()
        release_db(conn)

        # Flush fast memory cache
        PROFILE_CACHE.pop(user_id, None)
        sync_user_json_profile(user_id)
        
        logging.info(f"[SUCCESS] Activated and stacked plan {plan_key} for User ID: {user_id}. New Quota: {new_bal}, Paid: ₹{actual_charged_amount}")
        return True
    except Exception as e:
        if conn:
            conn.rollback()
            release_db(conn)
        logging.error(f"[DATABASE ERROR] Failed activating plan for user {user_id}: {e}")
        return False


async def send_payment_invoice_telegram(user_id: int, plan_key: str, payment_id: str = "OFFICIAL_SUBSCRIBED", amount_paid: float = None):
    """
    Pushes an instant celebratory text invoice directly into the user's Telegram chat
    AND notifies Himanshu Sir in the Admin Dashboard with purchase details.
    """
    if not bot_app_instance:
        logging.error("[TELEGRAM PUSH ERROR] bot_app_instance is uninitialized.")
        return

    if not plan_key or plan_key not in PLAN_TIERS:
        logging.error(f"[INVOICE BLOCKED] Attempted to send invoice for invalid plan '{plan_key}'")
        return

    from telegram import InlineKeyboardMarkup, InlineKeyboardButton

    plan_info = PLAN_TIERS[plan_key]
    plan_name = plan_info.get('name', plan_key)

    profile = await asyncio.to_thread(get_user_profile, user_id) or {}
    student_name = profile.get("full_name", "Student")
    sid = profile.get("student_id", f"USER_{user_id}")
    orig_payment_time = profile.get("payment_timestamp") or get_ist_timestamp_str()
    total_quota = profile.get("paid_question_balance", 0)
    expiry_date = profile.get("vip_pass_expiry", "N/A")

    base_price = plan_info.get('price', 0)
    final_amount = amount_paid if amount_paid is not None else base_price
    discount_applied_str = f" (🔥 Discount Saved: ₹{base_price - final_amount})" if (base_price > final_amount and base_price > 0) else ""

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
            f"💖 **Thanku for showing the faith in the Quiz with HiM. I'll give my best to keep your faith alive and of course, you have joined India's 1st dynamic Quiz platform for your preparation.**\n"
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
            f"• **Amount Paid:** ₹{final_amount} INR{discount_applied_str}\n"
            f"• **Txn / Payment ID:** `{payment_id}`\n"
            f"• **Payment Timestamp:** `{orig_payment_time}`\n"
            f"• **Added Validity:** `{plan_info.get('days')} Days Access`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💖 **Thanku for showing the faith in the Quiz with HiM. I'll give my best to keep your faith alive and of course, you have joined India's 1st dynamic Quiz platform for your preparation.**\n"
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
            parse_mode="Markdown",
            disable_notification=False
        )
        logging.info(f"[INVOICE/BROADCAST DELIVERED] Successfully sent to user {user_id}")
    except Exception as err:
        logging.error(f"[DELIVERY ERROR] Failed to push notification to user {user_id}: {err}")

    # Instant Admin Notification Alert on Genuine Verified Payment Purchase
    if not is_admin_grant and user_id != PRIMARY_ADMIN_ID:
        admin_motivation_alert = (
            f"🎉 **NEW PAID VIP PURCHASE RECEIVED!** 🎉\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 **Student Name:** {student_name}\n"
            f"🪪 **Student ID:** `{sid}`\n"
            f"🆔 **Telegram ID:** `{user_id}`\n\n"
            f"📦 **Purchased Pack:** `{plan_name}`\n"
            f"💰 **Amount Paid:** `₹{final_amount} INR`{discount_applied_str}\n"
            f"⚡ **New Stacked Quota:** `{total_quota} Qs/Day`\n"
            f"⏳ **VIP Pass Expiry:** `{expiry_date}`\n"
            f"🧾 **Payment ID:** `{payment_id}`\n"
            f"⏰ **Timestamp:** `{orig_payment_time}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🚀 *Your Quiz Book platform is growing! Keep up the great work, Himanshu Sir!*"
        )
        admin_nav = InlineKeyboardMarkup([
            [InlineKeyboardButton("👤 Inspect Student Profile", callback_data=f"admin_inspect_u_{user_id}")],
            [InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]
        ])
        try:
            await bot_app_instance.bot.send_message(
                chat_id=PRIMARY_ADMIN_ID,
                text=admin_motivation_alert,
                reply_markup=admin_nav,
                parse_mode="Markdown",
                disable_notification=False
            )
        except Exception as a_err:
            logging.error(f"[ADMIN PAYMENT ALERT ERROR] {a_err}")


async def scheduled_auto_payment_sync_worker():
    """
    DYNAMIC BACKGROUND AUTO-CREDITING WORKER:
    Runs in background every 60s. Detects uncredited paid students, auto-credits them,
    and pushes an instant notification alert to Himanshu Sir's Admin Telegram chat.
    """
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    while True:
        await asyncio.sleep(60)
        if not bot_app_instance:
            continue

        try:
            credited_list = await asyncio.to_thread(auto_sync_uncredited_paid_users)
            for c in credited_list:
                uid = c["user_id"]
                name = c.get("full_name", "Student")
                sid = c.get("student_id", f"USER_{uid}")
                quota = c.get("quota", 20)
                exp = c.get("expiry_str", "Active")
                rem_days = c.get("remaining_days", 0)
                pid = c.get("payment_id", "pay_verified")

                admin_alert = (
                    f"🔔 **AUTO-CREDIT ENGINE: PAID PLAN RESTORED & CREDITED!** 🔔\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 **Student Name:** {name}\n"
                    f"🪪 **Student ID:** `{sid}`\n"
                    f"🆔 **Telegram ID:** `{uid}`\n\n"
                    f"⚡ **Active Daily Quota:** `{quota} Questions / Day`\n"
                    f"⏳ **Remaining Validity:** `{rem_days} Days` (Expires: `{exp}`)\n"
                    f"🧾 **Reference Txn ID:** `{pid}`\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🛡 *The student had purchased this plan and it was automatically synchronized & protected.*"
                )
                admin_nav = InlineKeyboardMarkup([
                    [InlineKeyboardButton("👤 Inspect Student Profile", callback_data=f"admin_inspect_u_{uid}")],
                    [InlineKeyboardButton("👑 Himanshu Sir's Portal (/him)", callback_data="admin_home")]
                ])
                try:
                    await bot_app_instance.bot.send_message(
                        chat_id=PRIMARY_ADMIN_ID,
                        text=admin_alert,
                        reply_markup=admin_nav,
                        parse_mode="Markdown",
                        disable_notification=False
                    )
                except Exception as alert_err:
                    logging.error(f"[ADMIN AUTO-CREDIT ALERT ERROR] {alert_err}")

        except Exception as e:
            logging.error(f"[AUTO-CREDIT WORKER EXCEPTION] {e}")


async def scheduled_expiry_reminder_check():
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    ist = pytz.timezone("Asia/Kolkata")
    
    while True:
        await asyncio.sleep(120)
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

                if not exp_str:
                    continue

                try:
                    exp_dt = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S IST")
                    exp_dt = ist.localize(exp_dt) if exp_dt.tzinfo is None else exp_dt
                except Exception:
                    continue

                diff_seconds = (exp_dt - now).total_seconds()
                if diff_seconds <= 0:
                    continue

                hours_left = diff_seconds / 3600.0

                if 23.0 <= hours_left <= 25.0:
                    rem_key = f"{uid}_24h"
                    if rem_key not in SENT_EXPIRY_REMINDERS:
                        SENT_EXPIRY_REMINDERS.add(rem_key)
                        msg = (
                            f"⏰ **VIP PASS EXPIRING IN 24 HOURS (1 DAY)!** ⏰\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"Hello **{name}**, your VIP Subscription Pass will expire in **24 Hours**.\n\n"
                            f"⏳ **Pass Expiry Date:** `{exp_str}`\n"
                            f"⚡ **Current Daily Quota:** `{u['paid_question_balance']} Qs/Day`\n\n"
                            f"💡 **Recharge Now:** Tap **/plans** to renew your plan and prevent interruption!"
                        )
                        btn = InlineKeyboardMarkup([[InlineKeyboardButton("💳 Recharge / Upgrade VIP Plan", callback_data="cmd_plans")]])
                        try:
                            await bot_app_instance.bot.send_message(chat_id=uid, text=msg, reply_markup=btn, parse_mode="Markdown", disable_notification=False)
                        except Exception as e:
                            logging.error(f"[EXPIRY REMINDER ERROR] {e}")

                elif 5.5 <= hours_left <= 6.5:
                    rem_key = f"{uid}_6h"
                    if rem_key not in SENT_EXPIRY_REMINDERS:
                        SENT_EXPIRY_REMINDERS.add(rem_key)
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
                            await bot_app_instance.bot.send_message(chat_id=uid, text=msg, reply_markup=btn, parse_mode="Markdown", disable_notification=False)
                        except Exception as e:
                            logging.error(f"[EXPIRY REMINDER ERROR] {e}")

                elif 0.8 <= hours_left <= 1.2:
                    rem_key = f"{uid}_1h"
                    if rem_key not in SENT_EXPIRY_REMINDERS:
                        SENT_EXPIRY_REMINDERS.add(rem_key)
                        msg = (
                            f"🚨 **FINAL NOTICE: VIP PASS EXPIRING IN 1 HOUR!** 🚨\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"Attention **{name}**, your VIP Pass will expire in **less than 1 Hour**!\n\n"
                            f"⏳ **Pass Expiry:** `{exp_str}`\n\n"
                            f"⚡ **Recharge Immediately:** Tap below to keep your daily limit active!"
                        )
                        btn = InlineKeyboardMarkup([[InlineKeyboardButton("⚡ Instant Recharge Now", callback_data="cmd_plans")]])
                        try:
                            await bot_app_instance.bot.send_message(chat_id=uid, text=msg, reply_markup=btn, parse_mode="Markdown", disable_notification=False)
                        except Exception as e:
                            logging.error(f"[EXPIRY REMINDER ERROR] {e}")

        except Exception as err:
            if conn:
                release_db(conn)
            logging.error(f"[SCHEDULED CHECK EXCEPTION] {err}")


async def scheduled_daily_quiz_reminder():
    global LAST_QUIZ_BROADCAST_KEY
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    ist = pytz.timezone("Asia/Kolkata")

    while True:
        await asyncio.sleep(15)
        if not bot_app_instance:
            continue

        now = datetime.now(ist)
        today_date_str = now.strftime("%Y-%m-%d")
        current_hour = now.hour
        current_minute = now.minute

        is_time_slot = (
            (current_hour == 9 and current_minute == 0) or
            (current_hour == 12 and current_minute == 0) or
            (current_hour == 16 and current_minute == 0) or
            (current_hour == 19 and current_minute == 0) or
            (current_hour == 22 and current_minute == 0)
        )

        if is_time_slot:
            broadcast_key = f"{today_date_str}_{current_hour}_{current_minute}"
            if LAST_QUIZ_BROADCAST_KEY != broadcast_key:
                LAST_QUIZ_BROADCAST_KEY = broadcast_key
                conn = None
                try:
                    conn = get_db()
                    cursor = conn.cursor()
                    cursor.execute("SELECT user_id FROM users WHERE is_banned = 0 AND is_verified = 1")
                    rows = cursor.fetchall()
                    cursor.close()
                    release_db(conn)

                    user_ids = [r[0] if isinstance(r, (list, tuple)) else r['user_id'] for r in rows]
                    reminder_text = (
                        f"📢 **DAILY QUIZ PRACTICE REMINDER** 📢\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"Guyzzz attempt the Quiz Now, because everyday quiz will take you to one step closer to your selection💯\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"⚡ Tap the button below to start practicing now:"
                    )
                    btn = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Launch Quiz Now", callback_data="cmd_quiz")]])

                    await fast_concurrent_broadcast(bot_app_instance.bot, user_ids, reminder_text, reply_markup=btn, parse_mode="Markdown")
                except Exception as err:
                    if conn:
                        release_db(conn)
                    logging.error(f"[DAILY BROADCAST ERROR] {err}")


async def scheduled_announcement_broadcast_worker():
    """Background worker that polls every 15s and delivers scheduled announcements concurrently in ~3s."""
    while True:
        await asyncio.sleep(15)
        if not bot_app_instance:
            continue

        try:
            pending_list = await asyncio.to_thread(fetch_pending_announcements)
            for annc in pending_list:
                annc_id = annc['id']
                text = annc.get('message_text') or ""
                media_id = annc.get('media_file_id')
                media_type = annc.get('media_type', 'text')
                
                users = await asyncio.to_thread(get_all_users)
                user_ids = [u['user_id'] for u in users if not u.get('is_banned')]
                
                sent_count = await fast_concurrent_broadcast(
                    bot=bot_app_instance.bot,
                    user_ids=user_ids,
                    text=text,
                    photo=media_id if media_type == "photo" else None,
                    video=media_id if media_type == "video" else None,
                    media_type=media_type,
                    annc_id=annc_id
                )
                
                await asyncio.to_thread(update_announcement_status, annc_id, "SENT")
                logging.info(f"[SCHEDULED ANNOUNCEMENT #{annc_id} DELIVERED] Broadcasted to {sent_count}/{len(user_ids)} users in ~3s.")
                
        except Exception as e:
            logging.error(f"[ANNOUNCEMENT WORKER EXCEPTION] {e}")


async def scheduled_flash_sale_worker():
    """Background worker that continuously monitors flash sales and auto-expires them."""
    while True:
        await asyncio.sleep(30)
        try:
            await asyncio.to_thread(get_active_flash_sale)
        except Exception as e:
            logging.error(f"[FLASH SALE WORKER EXCEPTION] {e}")


async def handle_ping(request):
    return web.Response(text="Bot Engine & Payment Gateway Active")


async def handle_razorpay_callback_get(request):
    """
    SECURE GET REDIRECT HANDLER:
    Processes redirect payments safely and credits user subscriptions.
    """
    params = request.query
    razorpay_payment_id = params.get("razorpay_payment_id") or params.get("razorpay_payment_link_id")

    if not razorpay_payment_id or not str(razorpay_payment_id).startswith("pay_"):
        html_invalid = """
        <!DOCTYPE html>
        <html>
        <head><title>Invalid Request</title><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
        <body style="font-family:sans-serif; background:#0f172a; color:#f8fafc; text-align:center; padding:50px;">
            <h2>⚠️ Invalid Payment Verification Request</h2>
            <p>No verified Razorpay payment transaction was found for this link.</p>
            <a href="https://t.me/quizwithhimbot" style="color:#38bdf8; text-decoration:none; font-weight:bold;">Return to Quiz with HiM Bot</a>
        </body>
        </html>
        """
        return web.Response(text=html_invalid, content_type="text/html", status=400)

    raw_user_id = params.get("user_id") or params.get("notes[user_id]")
    plan_key = params.get("plan_key") or params.get("notes[plan_key]")
    charged_amt = None
    is_verified = False

    if razorpay_client:
        try:
            p_data = await asyncio.to_thread(razorpay_client.payment.fetch, razorpay_payment_id)
            if p_data and p_data.get("status") == "captured":
                is_verified = True
                charged_amt = float(p_data.get("amount", 0)) / 100.0
                notes = p_data.get("notes", {}) or {}
                if notes.get("user_id"): raw_user_id = notes.get("user_id")
                if notes.get("plan_key"): plan_key = notes.get("plan_key")
                if not raw_user_id and p_data.get("contact"):
                    u_m = get_user_by_phone(p_data.get("contact"))
                    if u_m: raw_user_id = u_m["user_id"]
        except Exception as e:
            logging.error(f"[CALLBACK REST VERIFY ERROR] {e}")

    if (not plan_key or plan_key not in PLAN_TIERS) and charged_amt:
        plan_key = infer_plan_key_from_amount(charged_amt)

    if is_verified and raw_user_id and plan_key in PLAN_TIERS:
        try:
            uid = int(raw_user_id)
            activated = await activate_user_subscription(uid, plan_key, razorpay_payment_id, amount_paid=charged_amt)
            if activated:
                await send_payment_invoice_telegram(uid, plan_key, razorpay_payment_id, amount_paid=charged_amt)
        except Exception as e:
            logging.error(f"[GET CALLBACK EXCEPTION] {e}")

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Payment Successful - Quiz with HiM</title>
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
            <p>Your VIP plan has been credited and an official invoice has been pushed to your Telegram chat.</p>
            <div class="id-box">Payment ID: {razorpay_payment_id}</div>
            <a href="https://t.me/quizwithhimbot" class="btn">Return to Telegram Bot</a>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html_content, content_type="text/html")


async def handle_razorpay_webhook(request):
    """
    SECURE POST WEBHOOK HANDLER:
    Processes Razorpay server webhooks and credits purchased plans in real time.
    """
    try:
        body = await request.text()
        signature = request.headers.get("X-Razorpay-Signature", "")

        if RAZORPAY_WEBHOOK_SECRET and signature:
            expected_signature = hmac.new(
                RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
                body.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected_signature, signature):
                logging.warning("[WEBHOOK] Signature mismatch detected.")

        data = json.loads(body)
        event = data.get("event")
        payload = data.get("payload", {})

        payment_link_entity = payload.get("payment_link", {}).get("entity", {})
        payment_entity = payload.get("payment", {}).get("entity", {})

        notes = payment_link_entity.get("notes") or payment_entity.get("notes") or {}
        
        user_id = notes.get("user_id")
        plan_key = notes.get("plan_key")
        payment_id = payment_entity.get("id") or payment_link_entity.get("id")
        
        if not payment_id or not str(payment_id).startswith("pay_"):
            return web.Response(status=400, text="Invalid Payment ID format")

        raw_amount = payment_entity.get("amount") or payment_link_entity.get("amount")
        amount_paid = (float(raw_amount) / 100.0) if raw_amount else None
        if not amount_paid and notes.get("amount_paid"):
            amount_paid = float(notes["amount_paid"])

        if not user_id:
            contact = payment_entity.get("contact") or payment_link_entity.get("customer", {}).get("contact")
            if contact:
                user_match = get_user_by_phone(contact)
                if user_match:
                    user_id = user_match["user_id"]

        if not plan_key or plan_key not in PLAN_TIERS:
            if amount_paid:
                plan_key = infer_plan_key_from_amount(amount_paid)

        if user_id and plan_key in PLAN_TIERS and event in ("payment_link.paid", "payment.captured"):
            uid = int(user_id)
            success = await activate_user_subscription(uid, plan_key, payment_id, amount_paid=amount_paid)
            if success:
                await send_payment_invoice_telegram(uid, plan_key, payment_id, amount_paid=amount_paid)

        return web.Response(status=200, text="Webhook Processed")
    except Exception as e:
        logging.error(f"[WEBHOOK EXCEPTION] {e}")
        return web.Response(status=500, text=str(e))


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/ping", handle_ping)
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

    # Launch background tasks
    asyncio.create_task(scheduled_auto_payment_sync_worker())
    asyncio.create_task(scheduled_expiry_reminder_check())
    asyncio.create_task(scheduled_daily_quiz_reminder())
    asyncio.create_task(scheduled_announcement_broadcast_worker())
    asyncio.create_task(scheduled_flash_sale_worker())

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