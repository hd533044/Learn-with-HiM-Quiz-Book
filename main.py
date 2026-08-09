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
from app.telegram_bot import build_application
from app.config import (
    RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET, 
    PLAN_TIERS
)
from app.database import sync_user_json_profile, get_ist_timestamp_str, get_db, release_db, get_user_profile
from app.invoice_generator import generate_payment_invoice_card

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
    Activates subscription, records entry in database, and updates user profile limits.
    Calculates exact dynamic expiry from the second code is run.
    """
    plan = PLAN_TIERS.get(plan_key)
    if not plan:
        return False

    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    
    # Calculate exact dynamic expiry timestamp
    expiry_dt = now + timedelta(days=plan["days"])
    expiry_str = expiry_dt.strftime("%Y-%m-%d %H:%M:%S IST")
    payment_time_str = now.strftime("%d %b %Y, %I:%M %p IST")

    profile = get_user_profile(user_id) or {}
    current_bal = profile.get("paid_question_balance", 0) or 0

    # Stack daily quota accurately
    new_bal = current_bal + plan["daily_limit"]

    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # 1. Update user record
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

        # 2. Log payment transaction
        cursor.execute(
            """
            INSERT INTO payment_transactions 
            (user_id, payment_id, plan_key, plan_name, amount_paid, daily_quota, validity_days, created_at, expiry_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (user_id, payment_id, plan_key, plan["name"], plan["price"], plan["daily_limit"], plan["days"], payment_time_str, expiry_str)
        )

        conn.commit()
        cursor.close()
        release_db(conn)

        sync_user_json_profile(user_id)
        logging.info(f"Activated plan {plan_key} for user {user_id}. Quota: {new_bal}, Expiry: {expiry_str}")
        return True
    except Exception as e:
        if conn:
            release_db(conn)
        logging.error(f"Error activating subscription: {e}")
        return False


async def send_payment_invoice_telegram(user_id: int, plan_key: str, payment_id: str = "OFFICIAL_SUBSCRIBED"):
    """
    Delivers prompted text invoice AND HD receipt image card to user on payment completion.
    """
    if not bot_app_instance:
        return

    plan_info = PLAN_TIERS.get(plan_key, {})
    plan_name = plan_info.get('name', plan_key)

    profile = await asyncio.to_thread(get_user_profile, user_id) or {}
    sid = profile.get("student_id", f"USER_{user_id}")
    orig_payment_time = profile.get("payment_timestamp") or get_ist_timestamp_str()
    total_quota = profile.get("paid_question_balance", 0)

    prompted_invoice_msg = (
        f"🥳 **PAYMENT CONFIRMED & PACK ACTIVATED!** 🥳\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎉 **Purchased Plan:** `{plan_name}`\n"
        f"⚡ **New Daily Limit:** `{total_quota} Questions / Day`\n"
        f"⏳ **Pass Expiry Date:** `{profile.get('vip_pass_expiry')}`\n\n"
        f"🧾 **OFFICIAL PAYMENT RECEIPT**\n"
        f"• **Amount Paid:** ₹{plan_info.get('price', 0)} INR\n"
        f"• **Txn / Payment ID:** `{payment_id}`\n"
        f"• **Payment Date:** `{orig_payment_time}`\n"
        f"• **Pack Validity:** `{plan_info.get('days')} Days`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🚀 Use **/quiz** to start practicing!"
    )
    try:
        # Send prompted text invoice
        await bot_app_instance.bot.send_message(
            chat_id=user_id,
            text=prompted_invoice_msg,
            parse_mode="Markdown"
        )

        # Generate and send graphic image card invoice
        img_card_path = await asyncio.to_thread(
            generate_payment_invoice_card, 
            user_id, 
            plan_key, 
            payment_id, 
            orig_payment_time
        )
        if img_card_path and os.path.exists(img_card_path):
            with open(img_card_path, "rb") as card_file:
                await bot_app_instance.bot.send_photo(
                    chat_id=user_id,
                    photo=card_file,
                    caption=f"💳 **OFFICIAL ULTRA-HD RECEIPT CARD** — `{sid}`\n🏷 Verified by Razorpay & Learn with HiM",
                    parse_mode="Markdown"
                )
            if os.path.exists(img_card_path):
                os.remove(img_card_path)

    except Exception as err:
        logging.error(f"Failed to send Telegram invoice notification: {err}")


async def handle_ping(request):
    return web.Response(text="Bot Engine is Active")


async def handle_razorpay_callback_get(request):
    params = request.query
    razorpay_payment_id = params.get("razorpay_payment_id", "OFFICIAL_SUBSCRIBED")

    user_id = params.get("user_id") or params.get("notes[user_id]")
    plan_key = params.get("plan_key") or params.get("notes[plan_key]")

    if user_id and plan_key:
        try:
            uid = int(user_id)
            activated = await activate_user_subscription(uid, plan_key, razorpay_payment_id)
            if activated:
                await send_payment_invoice_telegram(uid, plan_key, razorpay_payment_id)
        except Exception as e:
            logging.error(f"Callback error: {e}")

    return web.Response(text="Payment Processed Successfully", content_type="text/html")


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

        return web.Response(status=200, text="OK")
    except Exception as e:
        logging.error(f"Webhook error: {e}")
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