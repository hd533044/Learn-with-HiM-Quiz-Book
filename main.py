import asyncio
import hmac
import hashlib
import json
import logging
import os
import sqlite3
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
    PLAN_TIERS, DB_FILE
)
from app.database import sync_user_json_profile, get_ist_timestamp_str

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


async def activate_user_subscription(user_id: int, plan_key: str):
    """Activates subscription ledger in SQLite DB & syncs JSON profile."""
    plan = PLAN_TIERS.get(plan_key)
    if not plan:
        return False

    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    expiry = now + timedelta(days=plan["days"])
    expiry_str = expiry.strftime("%Y-%m-%d %H:%M:%S IST")

    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET paid_question_balance = ?, vip_pass_expiry = ? WHERE user_id = ?",
            (plan["daily_limit"], expiry_str, user_id)
        )
        conn.commit()
        conn.close()

        sync_user_json_profile(user_id)
        logging.info(f"Successfully activated {plan_key} for User ID: {user_id}")
        return True
    except Exception as e:
        logging.error(f"Error updating subscription for user {user_id}: {e}")
        return False


def create_razorpay_payment_link(user_id: int, plan_key: str):
    """Generates an instant Razorpay payment link for given tier."""
    if not razorpay_client:
        return None

    plan = PLAN_TIERS.get(plan_key)
    if not plan:
        return None

    try:
        response = razorpay_client.payment_link.create({
            "amount": plan["price"] * 100,  # Convert to paise
            "currency": "INR",
            "accept_partial": False,
            "description": f"Learn with HiM Subscription - {plan['name']}",
            "notes": {
                "user_id": str(user_id),
                "plan_key": plan_key
            },
            "callback_url": os.getenv("RENDER_EXTERNAL_URL", "https://learnwithhimquiz.onrender.com"),
            "callback_method": "get"
        })
        return response.get("short_url")
    except Exception as e:
        logging.error(f"Error creating Razorpay payment link: {e}")
        return None


async def handle_ping(request):
    """Render Web Service Healthcheck Endpoint."""
    return web.Response(text="Learn with HiM Quiz Book Bot is Online & Active!")

def verify_razorpay_signature(order_id: str, payment_id: str, razorpay_signature: str, key_secret: str) -> bool:
    """
    Verifies the Razorpay payment signature using HMAC-SHA256.
    Algorithm: HMAC-SHA256(order_id + "|" + payment_id, KEY_SECRET)
    """
    try:
        message = f"{order_id}|{payment_id}"
        generated_signature = hmac.new(
            key_secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(generated_signature, razorpay_signature)
    except Exception as e:
        logging.error(f"Signature verification exception: {e}")
        return False
    
async def handle_razorpay_webhook(request):
    """Webhook Handler for Instant Automated VIP Activation."""
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

        if event == "payment_link.paid":
            payload = data.get("payload", {}).get("payment_link", {}).get("entity", {})
            notes = payload.get("notes", {})
            user_id = notes.get("user_id")
            plan_key = notes.get("plan_key")

            if user_id and plan_key:
                uid = int(user_id)
                success = await activate_user_subscription(uid, plan_key)
                
                if success and bot_app_instance:
                    plan_info = PLAN_TIERS.get(plan_key, {})
                    msg = (
                        f"🎉 **PAYMENT CONFIRMED & ACTIVATED!** 🎉\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"Welcome to **{plan_info.get('name')}**!\n\n"
                        f"📅 **Validity:** {plan_info.get('days')} Days\n"
                        f"⚡ **Daily Quota:** {plan_info.get('daily_limit')} Questions/Day\n\n"
                        f"Your daily question limit is updated. Start practicing now with /quiz!"
                    )
                    try:
                        await bot_app_instance.bot.send_message(
                            chat_id=uid,
                            text=msg,
                            parse_mode="Markdown"
                        )
                    except Exception as err:
                        logging.error(f"Failed to notify user via Telegram: {err}")

        return web.Response(status=200, text="Webhook Processed")
    except Exception as e:
        logging.error(f"Webhook error: {e}")
        return web.Response(status=500, text=str(e))


async def start_web_server():
    """Starts a web server for keep-alive calls & Razorpay Webhooks."""
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/ping", handle_ping)
    app.router.add_post("/razorpay-webhook", handle_razorpay_webhook)

    port = int(os.getenv("PORT", "8080"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"  Keep-Alive & Webhook Server running on port {port}")


async def render_self_ping_loop():
    """Heartbeat loop that pings every 5 minutes to prevent Render sleep."""
    import httpx
    render_url = os.getenv("RENDER_EXTERNAL_URL")
    if not render_url:
        return
    async with httpx.AsyncClient() as client:
        while True:
            await asyncio.sleep(300)
            try:
                await client.get(f"{render_url}/ping")
            except Exception:
                pass


async def run_bot():
    global bot_app_instance
    print("==================================================")
    print("  Learn with HiM Quiz Book Bot Engine")
    print("==================================================")

    await start_web_server()
    asyncio.create_task(render_self_ping_loop())
    
    app = build_application()
    bot_app_instance = app

    await app.initialize()
    await app.start()
    await app.bot.delete_webhook(drop_pending_updates=True)
    await app.updater.start_polling(drop_pending_updates=True)

    print("  Bot is online, synchronized, and listening!")
    print("==================================================")

    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        print("\n  Shutting down bot gracefully...")
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def main():
    try:
        asyncio.run(run_bot())
    except (KeyboardInterrupt, SystemExit):
        print("  Bot offline.")


if __name__ == "__main__":
    main()