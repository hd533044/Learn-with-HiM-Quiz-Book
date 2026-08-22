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
from psycopg2.extras import RealDictCursor

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
    PLAN_TIERS, PRIMARY_ADMIN_ID, BASE_DIR
)
from app.database import (
    sync_user_json_profile, get_ist_timestamp_str, get_db, release_db, get_user_profile,
    fetch_pending_announcements, update_announcement_status, get_all_users, record_broadcast_delivery,
    get_active_flash_sale, calculate_discounted_price, get_user_by_phone, infer_plan_key_from_amount,
    auto_sync_uncredited_paid_users, record_quiz_result, save_question_to_db, get_saved_questions,
    get_today_attempts, get_total_registered_users_count, get_total_platform_likes
)
from app.pyq_fetcher import (
    fetch_pyqs_for_quiz, fetch_rc_or_cloze_passage_questions, fetch_english_full_mock_25
)
from app.stats import (
    calculate_user_rank, calculate_user_percentile, get_overall_leaderboard, get_user_performance_summary
)
from app.pdf_generator import generate_single_quiz_attempt_pdf, generate_student_pdf_report

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
    if not plan_key or plan_key not in PLAN_TIERS:
        logging.error(f"[SECURITY BLOCKED] Invalid plan key: '{plan_key}' for user {user_id}")
        return False

    pid_str = str(payment_id).strip()
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

        PROFILE_CACHE.pop(user_id, None)
        sync_user_json_profile(user_id)
        
        logging.info(f"[SUCCESS] Activated plan {plan_key} for User ID: {user_id}")
        return True
    except Exception as e:
        if conn:
            conn.rollback()
            release_db(conn)
        logging.error(f"[DATABASE ERROR] Failed activating plan: {e}")
        return False


async def send_payment_invoice_telegram(user_id: int, plan_key: str, payment_id: str = "OFFICIAL_SUBSCRIBED", amount_paid: float = None):
    if not bot_app_instance:
        return

    if not plan_key or plan_key not in PLAN_TIERS:
        return

    from telegram import InlineKeyboardMarkup, InlineKeyboardButton

    plan_info = PLAN_TIERS[plan_key]
    plan_name = plan_info.get('name', plan_key)

    profile = await asyncio.to_thread(get_user_profile, user_id) or {}
    sid = profile.get("student_id", f"USER_{user_id}")
    orig_payment_time = profile.get("payment_timestamp") or get_ist_timestamp_str()
    total_quota = profile.get("paid_question_balance", 0)
    expiry_date = profile.get("vip_pass_expiry", "N/A")

    base_price = plan_info.get('price', 0)
    final_amount = amount_paid if amount_paid is not None else base_price

    broadcast_msg = (
        f"🥳 **PAYMENT CONFIRMED & PLAN CREDITED!** 🥳\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎉 **Purchased Pack:** `{plan_name}`\n"
        f"⚡ **Daily Quota:** `{total_quota} Questions / Day`\n"
        f"⏳ **New VIP Pass Expiry:** `{expiry_date}`\n\n"
        f"🧾 **OFFICIAL PAYMENT INVOICE**\n"
        f"• **Student ID:** `{sid}`\n"
        f"• **Amount Paid:** ₹{final_amount} INR\n"
        f"• **Txn ID:** `{payment_id}`\n"
        f"• **Timestamp:** `{orig_payment_time}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
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
    except Exception as err:
        logging.error(f"[DELIVERY ERROR] Failed to push invoice notification: {err}")


# ======================================================================
# 🚀 CBT MINI APP (WEB PORTAL) API & ROUTE HANDLERS
# ======================================================================

async def handle_serve_webapp(request):
    """Serves the interactive CBT Single Page Application."""
    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "webapp.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return web.Response(text=f.read(), content_type="text/html")
    return web.Response(text="Web App Template Not Found. Please ensure app/templates/webapp.html exists.", status=404)


async def handle_api_get_dashboard(request):
    """Fetches user profile, rank, today's usage, and active flash sales."""
    try:
        user_id = int(request.query.get("user_id", 0))
        profile = await asyncio.to_thread(get_user_profile, user_id) or {}
        today_used = await asyncio.to_thread(get_today_attempts, user_id)
        rank = await asyncio.to_thread(calculate_user_rank, user_id)
        percentile = await asyncio.to_thread(calculate_user_percentile, user_id)
        total_users = await asyncio.to_thread(get_total_registered_users_count)
        total_likes = await asyncio.to_thread(get_total_platform_likes)
        active_sale = await asyncio.to_thread(get_active_flash_sale)

        paid_bal = profile.get("paid_question_balance", 0) or 0
        allowed_limit = 10000 if user_id == PRIMARY_ADMIN_ID else max(20, paid_bal) + profile.get("bonus_quota", 0)

        return web.json_response({
            "profile": profile,
            "today_used": today_used,
            "allowed_limit": allowed_limit,
            "rank": rank,
            "percentile": percentile,
            "total_users": total_users,
            "total_likes": total_likes,
            "active_sale": active_sale,
            "plan_tiers": PLAN_TIERS
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_api_get_leaderboard(request):
    """Fetches top 10 global leaderboard ranks."""
    try:
        toppers = await asyncio.to_thread(get_overall_leaderboard, 10)
        return web.json_response({"leaderboard": toppers})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_api_get_analytics(request):
    """Fetches full analytics, test counts, accuracy, and today's attempts."""
    try:
        user_id = int(request.query.get("user_id", 0))
        perf = await asyncio.to_thread(get_user_performance_summary, user_id)
        rank = await asyncio.to_thread(calculate_user_rank, user_id)
        percentile = await asyncio.to_thread(calculate_user_percentile, user_id)
        
        # Fetch today's wrong & attempted questions
        today_str = get_ist_timestamp_str().split(" ")[0]
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM quiz_attempts WHERE user_id = %s AND attempt_date = %s ORDER BY id DESC", (user_id, today_str))
        attempts = cursor.fetchall()
        cursor.close()
        release_db(conn)

        today_wrong = []
        today_attempted = []

        for a in attempts:
            ad = dict(a)
            if ad.get("details_json"):
                try:
                    details = json.loads(ad["details_json"])
                    for q in details:
                        if isinstance(q, dict):
                            today_attempted.append(q)
                            if str(q.get("status", "")).upper() == "WRONG":
                                today_wrong.append(q)
                except Exception:
                    pass

        return web.json_response({
            "performance": perf,
            "rank": rank,
            "percentile": percentile,
            "today_wrong": today_wrong,
            "today_attempted": today_attempted
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_api_get_saved_questions(request):
    """Fetches bookmarked questions for the user."""
    try:
        user_id = int(request.query.get("user_id", 0))
        saved = await asyncio.to_thread(get_saved_questions, user_id)
        return web.json_response({"saved_questions": saved})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_api_ask_admin(request):
    """Submits a student query directly into student_queries."""
    try:
        data = await request.json()
        user_id = int(data.get("user_id"))
        student_name = data.get("student_name", "Student")
        query_text = data.get("query_text", "").strip()

        if not query_text:
            return web.json_response({"error": "Empty query"}, status=400)

        now_str = get_ist_timestamp_str()
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO student_queries (user_id, student_name, query_text, media_type, created_at)
            VALUES (%s, %s, %s, 'text', %s)
            """,
            (user_id, student_name, query_text, now_str)
        )
        conn.commit()
        cursor.close()
        release_db(conn)

        # Notify Admin in Telegram
        if bot_app_instance:
            try:
                await bot_app_instance.bot.send_message(
                    chat_id=PRIMARY_ADMIN_ID,
                    text=f"📩 **NEW WEB QUERY RECEIVED!**\n👤 **From:** {student_name} (`{user_id}`)\n❓ **Query:** *\"{query_text}\"*",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_api_start_quiz(request):
    """Fetches non-repeated question sets for the Web App session."""
    try:
        data = await request.json()
        user_id = data.get("user_id")
        subject = data.get("subject", "english")
        topic = data.get("topic", "MIXED")
        count = int(data.get("count", 20))

        if topic == "FULL_MOCK_25":
            questions = await asyncio.to_thread(fetch_english_full_mock_25, "en", user_id)
        elif topic in ["eng_comp_rc", "eng_comp_cloze_test"]:
            questions = await asyncio.to_thread(fetch_rc_or_cloze_passage_questions, topic, user_id)
        else:
            questions = await asyncio.to_thread(fetch_pyqs_for_quiz, count, None, "en", user_id, topic, subject)

        return web.json_response({"questions": questions})
    except Exception as e:
        logging.error(f"[WEBAPP START QUIZ API ERROR] {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_api_submit_quiz(request):
    """Evaluates answers, logs attempt in PostgreSQL, and generates rank/accuracy metrics."""
    try:
        data = await request.json()
        user_id = int(data.get("user_id"))
        questions = data.get("questions", [])
        answers = data.get("answers", {})
        subject = data.get("subject", "General")
        quiz_mode = data.get("quiz_mode", "CBT_MOCK")

        correct = 0
        wrong = 0
        skipped = 0
        detailed_logs = []

        for idx, q in enumerate(questions):
            c_idx = q.get("correct_option", 0)
            opts = q.get("options", [])
            selected = answers.get(str(idx))

            if selected is None:
                skipped += 1
                status = "SKIPPED_TIMEOUT"
            elif int(selected) == c_idx:
                correct += 1
                status = "CORRECT"
            else:
                wrong += 1
                status = "WRONG"

            detailed_logs.append({
                "question_id": q.get("id"),
                "question_text": q.get("question"),
                "options": opts,
                "explanation": q.get("explanation", ""),
                "status": status,
                "selected_option": selected,
                "correct_option": c_idx,
                "correct_answer_text": opts[c_idx] if 0 <= c_idx < len(opts) else "N/A"
            })

        score = round(correct * 1.0 - wrong * 0.25, 2)
        total = len(questions)

        attempt_id = await asyncio.to_thread(
            record_quiz_result,
            user_id=user_id,
            quiz_id=quiz_mode,
            score=score,
            total_questions=total,
            correct_count=correct,
            wrong_count=wrong,
            skipped_count=skipped,
            time_taken=0,
            question_details=detailed_logs,
            quiz_mode=quiz_mode,
            mock_number=1,
            subject=subject
        )

        rank = await asyncio.to_thread(calculate_user_rank, user_id)
        percentile = await asyncio.to_thread(calculate_user_percentile, user_id)
        accuracy = round((correct / total) * 100.0, 2) if total > 0 else 0.0

        return web.json_response({
            "attempt_id": attempt_id,
            "score": score,
            "correct": correct,
            "wrong": wrong,
            "skipped": skipped,
            "accuracy": accuracy,
            "rank": rank,
            "percentile": percentile
        })
    except Exception as e:
        logging.error(f"[WEBAPP SUBMIT QUIZ API ERROR] {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_api_bookmark_question(request):
    """Saves a question to the student's personal bookmarks database."""
    try:
        data = await request.json()
        user_id = int(data.get("user_id"))
        q = data.get("question", {})
        
        q_text = q.get("question", "")
        options = q.get("options", [])
        correct_option = int(q.get("correct_option", 0))
        explanation = q.get("explanation", "")
        
        success = await asyncio.to_thread(
            save_question_to_db,
            user_id, q_text, options, correct_option, explanation
        )
        return web.json_response({"success": success})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_api_download_pdf(request):
    """Direct PDF download endpoint for completed test reviews."""
    try:
        aid = int(request.match_info.get("attempt_id", 0))
        pdf_path = await asyncio.to_thread(generate_single_quiz_attempt_pdf, aid)
        if pdf_path and os.path.exists(pdf_path):
            with open(pdf_path, "rb") as f:
                return web.Response(
                    body=f.read(),
                    content_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename={os.path.basename(pdf_path)}"}
                )
        return web.Response(text="PDF report not found", status=404)
    except Exception as e:
        logging.error(f"[WEBAPP DOWNLOAD PDF API ERROR] {e}")
        return web.Response(text=str(e), status=500)


async def handle_api_generate_custom_pdf(request):
    """Generates and downloads student summary / subject PDFs directly from WebApp."""
    try:
        data = await request.json()
        user_id = int(data.get("user_id"))
        subject = data.get("subject", "all")
        report_type = data.get("report_type", "full")
        timeframe = data.get("timeframe", "all")

        pdf_path = await asyncio.to_thread(generate_student_pdf_report, user_id, None, subject, report_type, timeframe)
        if pdf_path and os.path.exists(pdf_path) and not pdf_path.startswith("ERROR"):
            with open(pdf_path, "rb") as f:
                return web.Response(
                    body=f.read(),
                    content_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename={os.path.basename(pdf_path)}"}
                )
        return web.Response(text=f"PDF generation note: {pdf_path}", status=400)
    except Exception as e:
        return web.Response(text=str(e), status=500)


async def handle_ping(request):
    return web.Response(text="Bot Engine & Payment Gateway Active")


async def handle_razorpay_callback_get(request):
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


async def scheduled_auto_payment_sync_worker():
    while True:
        await asyncio.sleep(30)
        if not bot_app_instance:
            continue

        if razorpay_client:
            try:
                p_res = await asyncio.to_thread(razorpay_client.payment.all, {"count": 30})
                items = p_res.get("items", []) if isinstance(p_res, dict) else []
                for p in items:
                    if p.get("status") != "captured":
                        continue
                    p_id = p.get("id")
                    if not p_id or not str(p_id).startswith("pay_"):
                        continue

                    conn = get_db()
                    cursor = conn.cursor()
                    cursor.execute("SELECT 1 FROM payment_transactions WHERE payment_id = %s", (p_id,))
                    exists = cursor.fetchone()
                    cursor.close()
                    release_db(conn)

                    if not exists:
                        amount_paid = float(p.get("amount", 0)) / 100.0
                        contact = p.get("contact", "")
                        notes = p.get("notes", {}) or {}
                        user_id = notes.get("user_id")
                        plan_key = notes.get("plan_key")

                        uid = None
                        if user_id and str(user_id).isdigit():
                            uid = int(user_id)
                        elif contact:
                            u_match = get_user_by_phone(contact)
                            if u_match:
                                uid = u_match["user_id"]

                        if uid:
                            if not plan_key or plan_key not in PLAN_TIERS:
                                plan_key = infer_plan_key_from_amount(amount_paid)
                            if plan_key in PLAN_TIERS:
                                activated = await activate_user_subscription(uid, plan_key, p_id, amount_paid=amount_paid)
                                if activated:
                                    await send_payment_invoice_telegram(uid, plan_key, p_id, amount_paid=amount_paid)
            except Exception as rzp_err:
                logging.error(f"[30S RAZORPAY SYNC ERROR] {rzp_err}")

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

                PROFILE_CACHE.pop(uid, None)

                student_restore_msg = (
                    f"🛡️ **PAID VIP SUBSCRIPTION AUTOMATICALLY RESTORED!** 🛡️\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Hello **{name}**, your genuine paid subscription has been verified and restored to your account!\n\n"
                    f"⚡ **Active Daily Limit:** `{quota} Questions / Day`\n"
                    f"⏳ **Remaining Validity:** `{rem_days} Days` (Expires: `{exp}`)\n"
                    f"🧾 **Reference Payment ID:** `{pid}`\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🚀 Tap **/quiz** below to start practicing immediately!"
                )
                from telegram import InlineKeyboardMarkup, InlineKeyboardButton
                user_nav = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🚀 Launch Quiz Now", callback_data="cmd_quiz"), InlineKeyboardButton("💳 My Plan", callback_data="cmd_myplan")]
                ])
                try:
                    await bot_app_instance.bot.send_message(
                        chat_id=uid,
                        text=student_restore_msg,
                        reply_markup=user_nav,
                        parse_mode="Markdown",
                        disable_notification=False
                    )
                except Exception as u_err:
                    logging.error(f"[STUDENT RESTORE NOTIFICATION ERROR] {u_err}")

                admin_alert = (
                    f"🔔 **30S AUTO-SYNC: PAID PLAN RESTORED FOR STUDENT!** 🔔\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 **Student Name:** {name}\n"
                    f"🪪 **Student ID:** `{sid}`\n"
                    f"🆔 **Telegram ID:** `{uid}`\n\n"
                    f"⚡ **Restored Quota:** `{quota} Questions / Day`\n"
                    f"⏳ **Remaining Validity:** `{rem_days} Days` (Expires: `{exp}`)\n"
                    f"🧾 **Reference Txn ID:** `{pid}`\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🛡️ *Detected uncredited/revoked active purchase and automatically re-credited it for remaining validity days.*"
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
            logging.error(f"[AUTO-CREDIT 30S WORKER EXCEPTION] {e}")


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
                    voice=media_id if media_type == "voice" else None,
                    audio=media_id if media_type == "audio" else None,
                    document=media_id if media_type == "document" else None,
                    animation=media_id if media_type == "animation" else None,
                    media_type=media_type,
                    annc_id=annc_id
                )
                
                await asyncio.to_thread(update_announcement_status, annc_id, "SENT")
                logging.info(f"[SCHEDULED ANNOUNCEMENT #{annc_id} DELIVERED] Broadcasted to {sent_count}/{len(user_ids)} users in ~3s.")
                
        except Exception as e:
            logging.error(f"[ANNOUNCEMENT WORKER EXCEPTION] {e}")


async def scheduled_flash_sale_worker():
    while True:
        await asyncio.sleep(30)
        try:
            await asyncio.to_thread(get_active_flash_sale)
        except Exception as e:
            logging.error(f"[FLASH SALE WORKER EXCEPTION] {e}")


async def start_web_server():
    app = web.Application()
    
    # 1. Mount Static Assets (Serves official logo.png and local assets)
    assets_dir = os.path.join(BASE_DIR, "assets")
    if os.path.exists(assets_dir):
        app.router.add_static("/assets", assets_dir)

    # 2. Main CBT Web App Interface Routes
    app.router.add_get("/", handle_serve_webapp)
    app.router.add_get("/webapp", handle_serve_webapp)
    app.router.add_get("/ping", handle_ping)
    
    # 3. Categorized Mini App REST Endpoints
    app.router.add_get("/api/webapp/dashboard", handle_api_get_dashboard)
    app.router.add_get("/api/webapp/leaderboard", handle_api_get_leaderboard)
    app.router.add_get("/api/webapp/analytics", handle_api_get_analytics)
    app.router.add_get("/api/webapp/saved-questions", handle_api_get_saved_questions)
    app.router.add_post("/api/webapp/ask-admin", handle_api_ask_admin)
    app.router.add_post("/api/webapp/generate-custom-pdf", handle_api_generate_custom_pdf)
    
    # 4. CBT Exam Engine Endpoints
    app.router.add_post("/api/webapp/start-quiz", handle_api_start_quiz)
    app.router.add_post("/api/webapp/submit-quiz", handle_api_submit_quiz)
    app.router.add_post("/api/webapp/bookmark", handle_api_bookmark_question)
    app.router.add_get("/api/webapp/download-pdf/{attempt_id}", handle_api_download_pdf)
    
    # 5. Razorpay Webhook & Payment Handlers
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
    await app.updater.start_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

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