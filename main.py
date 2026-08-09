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
from app.invoice_generator import generate_payment_invoice_card, mask_phone_number

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
    """Activates subscription ledger in PostgreSQL DB & syncs JSON profile."""
    plan = PLAN_TIERS.get(plan_key)
    if not plan:
        return False

    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    expiry = now + timedelta(days=plan["days"])
    expiry_str = expiry.strftime("%Y-%m-%d %H:%M:%S IST")

    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        if plan_key == "FREE_DEMO":
            cursor.execute(
                "UPDATE users SET paid_question_balance = %s, vip_pass_expiry = %s, demo_used = 1 WHERE user_id = %s",
                (plan["daily_limit"], expiry_str, user_id)
            )
        else:
            cursor.execute(
                "UPDATE users SET paid_question_balance = %s, vip_pass_expiry = %s WHERE user_id = %s",
                (plan["daily_limit"], expiry_str, user_id)
            )

        conn.commit()
        cursor.close()
        release_db(conn)

        sync_user_json_profile(user_id)
        logging.info(f"Successfully activated {plan_key} for User ID: {user_id}")
        return True
    except Exception as e:
        if conn:
            release_db(conn)
        logging.error(f"Error updating subscription for user {user_id}: {e}")
        return False


async def send_payment_invoice_telegram(user_id: int, plan_key: str, payment_id: str = "N/A"):
    """Sends official text invoice AND generated Graphic Invoice Receipt Card."""
    if not bot_app_instance:
        return

    plan_info = PLAN_TIERS.get(plan_key, {})
    txn_time = get_ist_timestamp_str()
    plan_name = plan_info.get('name', plan_key)

    profile = await asyncio.to_thread(get_user_profile, user_id) or {}
    sid = profile.get("student_id", f"USER_{user_id}")

    invoice_msg = (
        f"🥳 **CONGRATULATIONS! PACK ACTIVATED!** 🥳\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎉 **Your {plan_name} has been successfully activated!**\n"
        f"✨ You can now start your preparation immediately.\n\n"
        f"🧾 **OFFICIAL PAYMENT RECEIPT**\n"
        f"• **Unlocked Pack:** `{plan_name}`\n"
        f"• **Amount Paid:** ₹{plan_info.get('price', 0)} INR\n"
        f"• **Payment / Txn ID:** `{payment_id}`\n"
        f"• **Date & Time:** `{txn_time}`\n"
        f"• **Validity:** `{plan_info.get('days')} Days`\n"
        f"• **Daily Question Limit:** `{plan_info.get('daily_limit')} Questions / Day`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🚀 Tap **/quiz** to launch your Computer Quiz practice session now!"
    )
    try:
        # Send text invoice
        await bot_app_instance.bot.send_message(
            chat_id=user_id,
            text=invoice_msg,
            parse_mode="Markdown"
        )

        # Generate & send Graphic Receipt Image Card
        img_card_path = await asyncio.to_thread(generate_payment_invoice_card, user_id, plan_key, payment_id)
        if img_card_path and os.path.exists(img_card_path):
            with open(img_card_path, "rb") as card_file:
                await bot_app_instance.bot.send_photo(
                    chat_id=user_id,
                    photo=card_file,
                    caption=f"💳 **OFFICIAL PAYMENT RECEIPT CARD** — `{sid}`\n🏷 Verified by Razorpay & Learn with HiM",
                    parse_mode="Markdown"
                )
            os.remove(img_card_path)

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
            activated = await activate_user_subscription(uid, plan_key)
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
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background: #0f172a;
                color: #f8fafc;
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
                margin: 0;
                padding: 20px;
            }}
            .card {{
                background: #1e293b;
                border-radius: 16px;
                padding: 32px;
                max-width: 420px;
                width: 100%;
                box-shadow: 0 10px 25px rgba(0, 0, 0, 0.5);
                text-align: center;
                border: 1px solid #334155;
            }}
            .icon {{
                font-size: 56px;
                margin-bottom: 16px;
            }}
            h2 {{
                color: #38bdf8;
                margin-bottom: 8px;
            }}
            p {{
                color: #94a3b8;
                font-size: 15px;
                line-height: 1.5;
            }}
            .id-box {{
                background: #0f172a;
                padding: 12px;
                border-radius: 8px;
                font-family: monospace;
                color: #38bdf8;
                margin: 16px 0;
                word-break: break-all;
            }}
            .btn {{
                display: inline-block;
                background: #2563eb;
                color: white;
                text-decoration: none;
                padding: 12px 24px;
                border-radius: 8px;
                font-weight: bold;
                margin-top: 16px;
            }}
        </style>
    </head>
    <body>
        <div class="card">
            <div class="icon">🎉</div>
            <h2>Payment Successful!</h2>
            <p>Congratulations! Your VIP plan has been activated.</p>
            <div class="id-box">Payment ID: {razorpay_payment_id}</div>
            <p>An official invoice and receipt card have been sent to your Telegram chat.</p>
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
                success = await activate_user_subscription(uid, plan_key)
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
    print(f"  Keep-Alive & Webhook Server running on port {port}")


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