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
from telegram import Update

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
from app.database import sync_user_json_profile, get_ist_timestamp_str, record_payment_transaction

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

async def activate_user_subscription(user_id: int, plan_key: str, payment_id: str = "N/A"):
    plan = PLAN_TIERS.get(plan_key)
    if not plan:
        return False
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    expiry = now + timedelta(days=plan["days"])
    expiry_str = expiry.strftime("%Y-%m-%d %H:%M:%S IST")
    try:
        from app.database import get_db
        conn = get_db()
        cursor = conn.cursor()
        
        if plan_key == "FREE_DEMO":
            cursor.execute(
                "UPDATE users SET paid_question_balance = CASE WHEN paid_question_balance > ? THEN paid_question_balance ELSE ? END, vip_pass_expiry = ?, demo_used = 1 WHERE user_id = ?",
                (plan["daily_limit"], plan["daily_limit"], expiry_str, user_id)
            )
        else:
            cursor.execute(
                "UPDATE users SET paid_question_balance = paid_question_balance + ?, vip_pass_expiry = ? WHERE user_id = ?",
                (plan["daily_limit"], expiry_str, user_id)
            )
        conn.commit()
        conn.close()
        
        if plan["price"] > 0:
            record_payment_transaction(user_id, plan_key, plan["price"], payment_id)
        sync_user_json_profile(user_id)
        logging.info(f"Successfully activated {plan_key} for User ID: {user_id}")
        return True
    except Exception as e:
        logging.error(f"Error updating subscription for user {user_id}: {e}")
        return False

async def send_payment_invoice_telegram(user_id: int, plan_key: str, payment_id: str = "N/A"):
    if not bot_app_instance:
        return
    plan_info = PLAN_TIERS.get(plan_key, {})
    txn_time = get_ist_timestamp_str()
    plan_name = plan_info.get('name', plan_key)
    
    invoice_msg = (
        f"  **CONGRATULATIONS! PACK ACTIVATED!**\n\n"
        f"  **Your {plan_name} has been successfully activated!**\n"
        f"You can now start your preparation immediately.\n\n"
        f"  **OFFICIAL PAYMENT INVOICE**\n"
        f"  **Unlocked Pack:** `{plan_name}`\n"
        f"  **Amount Paid:**  {plan_info.get('price', 0)}\n"
        f"  **Payment / Txn ID:** `{payment_id}`\n"
        f"  **Date & Time:** `{txn_time}`\n"
        f"  **Validity:** `{plan_info.get('days')} Days`\n"
        f"  **Added Daily Limit:** `+{plan_info.get('daily_limit')} Questions / Day`\n\n"
        f"Tap **/quiz** to launch your Computer Quiz practice session now!"
    )
    try:
        await bot_app_instance.bot.send_message(
            chat_id=user_id,
            text=invoice_msg,
            parse_mode="Markdown"
        )
    except Exception as err:
        logging.error(f"Failed to notify user {user_id} via Telegram: {err}")

async def handle_ping(request):
    return web.Response(text="Learn with HiM Quiz Book Bot is Online & Active!")

async def handle_razorpay_callback_get(request):
    params = request.query
    razorpay_payment_id = params.get("razorpay_payment_id", "N/A")
    user_id = params.get("user_id") or params.get("notes[user_id]")
    plan_key = params.get("plan_key") or params.get("notes[plan_key]")
    if user_id and plan_key:
        try:
            uid = int(user_id)
            activated = await activate_user_subscription(uid, plan_key, razorpay_payment_id)
            if activated:
                await send_payment_invoice_telegram(uid, plan_key, razorpay_payment_id)
        except Exception as e:
            logging.error(f"Error activating from GET callback redirect: {e}")
            
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Payment Successful - Learn with HiM Quiz Book</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #0f172a; color: #f8fafc; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; padding: 20px; }}
            .card {{ background: #1e293b; border-radius: 16px; padding: 32px; max-width: 420px; width: 100%; box-shadow: 0 10px 25px rgba(0, 0, 0, 0.5); text-align: center; border: 1px solid #334155; }}
            .id-box {{ background: #0f172a; padding: 12px; border-radius: 8px; font-family: monospace; color: #38bdf8; margin: 16px 0; word-break: break-all; }}
            .btn {{ display: inline-block; background: #2563eb; color: white; text-decoration: none; padding: 12px 24px; border-radius: 8px; font-weight: bold; margin-top: 16px; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h2>Payment Successful!</h2>
            <p>Congratulations! Your VIP plan has been activated.</p>
            <div class="id-box">Payment ID: {razorpay_payment_id}</div>
            <p>An official invoice and pack receipt have been sent to your Telegram chat.</p>
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
            user_id = notes.get("user_id")
            plan_key = notes.get("plan_key")
            payment_id = payload.get("payment_id") or payload.get("id") or "N/A"
            if user_id and plan_key:
                uid = int(user_id)
                success = await activate_user_subscription(uid, plan_key, payment_id)
                if success:
                    await send_payment_invoice_telegram(uid, plan_key, payment_id)
        return web.Response(status=200, text="Webhook Processed")
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
    logging.info(f"Keep-Alive & Webhook Server running on port {port}")

async def render_self_ping_loop():
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
    logging.info("Starting Learn with HiM Quiz Book Bot Engine...")

    # Start Aiohttp Web Server
    try:
        await start_web_server()
        asyncio.create_task(render_self_ping_loop())
    except Exception as web_err:
        logging.error(f"Web server start warning: {web_err}")

    # Build Application
    global bot_app_instance
    app = build_application()
    bot_app_instance = app

    await app.initialize()
    
    # `Application.run_polling()` calls post_init automatically, but this app
    # uses the lower-level initialize/start/start_polling sequence so it can run
    # the aiohttp keep-alive and webhook server in the same event loop. Command
    # menu setup must never prevent polling from starting if Telegram has a
    # temporary API issue.
    if app.post_init:
        try:
            await app.post_init(app)
        except Exception as command_menu_err:
            logging.warning("Command menu setup skipped: %s", command_menu_err)

    await app.start()
    
    # Safe Webhook Reset
    try:
        await asyncio.wait_for(app.bot.delete_webhook(drop_pending_updates=True), timeout=5.0)
    except Exception as e:
        logging.warning(f"Webhook reset skipped: {e}")

    # Start Polling
    await app.updater.start_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
    logging.info("Bot is online, synchronized, and actively listening!")

    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        logging.info("Shutting down bot gracefully...")
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

def main():
    try:
        asyncio.run(run_bot())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot offline.")

if __name__ == "__main__":
    main()
