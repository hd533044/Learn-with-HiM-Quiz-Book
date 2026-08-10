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


async def activate_user_subscription(user_id: int, plan_key: str, payment_id: str = "OFFICIAL_SUBSCRIBED"):
    """
    CRITICAL FIX:
    1. PostgreSQL (%s) parameterized query execution.
    2. Stacks daily question limit onto current paid_question_balance.
    3. Extends pass validity.
    4. Logs transaction in payment_transactions table for /myplan.
    5. Flushes local memory cache so /myplan and /myprofile update instantly.
    """
    plan = PLAN_TIERS.get(plan_key)
    if not plan:
        logging.error(f"[ACTIVATION CRITICAL ERROR] Invalid plan key: '{plan_key}'")
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
                """,
                (user_id, payment_id, plan_key, plan["name"], plan["price"], plan["daily_limit"], plan["days"], payment_time_str, expiry_str)
            )
        except Exception as tx_err:
            logging.warning(f"[TX LOG FALLBACK] Fallback query without expiry_at: {tx_err}")
            cursor.execute(
                """
                INSERT INTO payment_transactions 
                (user_id, payment_id, plan_key, plan_name, amount_paid, daily_quota, validity_days, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (user_id, payment_id, plan_key, plan["name"], plan["price"], plan["daily_limit"], plan["days"], payment_time_str)
            )

        conn.commit()
        cursor.close()
        release_db(conn)

        # Flush fast memory cache so /myplan & /myprofile reflect instantly
        PROFILE_CACHE.pop(user_id, None)
        sync_user_json_profile(user_id)
        
        logging.info(f"[ACTIVATION SUCCESS] Activated and stacked {plan_key} for User ID: {user_id}. New Quota: {new_bal}")
        return True
    except Exception as e:
        if conn:
            release_db(conn)
        logging.error(f"[ACTIVATION DB EXCEPTION] Failed activating plan for user {user_id}: {e}")
        return False


async def send_payment_invoice_telegram(user_id: int, plan_key: str, payment_id: str = "OFFICIAL_SUBSCRIBED"):
    """
    CRITICAL FIX: Pushes a robust text success invoice directly into the user's Telegram chat.
    """
    if not bot_app_instance:
        logging.error("[TELEGRAM PUSH ERROR] bot_app_instance is uninitialized.")
        return

    plan_info = PLAN_TIERS.get(plan_key, {})
    plan_name = plan_info.get('name', plan_key)

    profile = await asyncio.to_thread(get_user_profile, user_id) or {}
    sid = profile.get("student_id", f"USER_{user_id}")
    orig_payment_time = profile.get("payment_timestamp") or get_ist_timestamp_str()
    total_quota = profile.get("paid_question_balance", 0)

    success_text_invoice = (
        f"🥳 **PAYMENT CONFIRMED & PLAN CREDITED!** 🥳\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎉 **Purchased Pack:** `{plan_name}`\n"
        f"⚡ **Stacked Daily Quota:** `{total_quota} Questions / Day`\n"
        f"⏳ **New VIP Pass Expiry:** `{profile.get('vip_pass_expiry')}`\n\n"
        f"🧾 **OFFICIAL PAYMENT SUCCESS INVOICE**\n"
        f"• **Student ID:** `{sid}`\n"
        f"• **Amount Paid:** ₹{plan_info.get('price', 0)} INR\n"
        f"• **Txn / Payment ID:** `{payment_id}`\n"
        f"• **Payment Timestamp:** `{orig_payment_time}`\n"
        f"• **Added Validity:** `{plan_info.get('days')} Days Access`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Tap **/myplan** or **/myprofile** anytime to check your active quota and plan details.\n"
        f"🚀 Tap **/quiz** to launch your practice session now!"
    )
    
    try:
        await bot_app_instance.bot.send_message(
            chat_id=user_id,
            text=success_text_invoice,
            parse_mode="Markdown"
        )
        logging.info(f"[INVOICE DELIVERED] Pushed text success invoice to user {user_id}")
    except Exception as err:
        logging.error(f"[INVOICE DELIVERY ERROR] Could not notify user {user_id}: {err}")


async def handle_ping(request):
    return web.Response(text="Bot Engine & Payment Gateway Active")


async def handle_razorpay_callback_get(request):
    params = request.query
    razorpay_payment_id = params.get("razorpay_payment_id") or params.get("razorpay_payment_link_id") or "OFFICIAL_SUBSCRIBED"

    raw_user_id = params.get("user_id") or params.get("notes[user_id]")
    plan_key = params.get("plan_key") or params.get("notes[plan_key]")

    logging.info(f"[GET CALLBACK] user_id={raw_user_id}, plan_key={plan_key}, payment_id={razorpay_payment_id}")

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