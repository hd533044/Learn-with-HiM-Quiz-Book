import time
import json
import logging
import math
import os
import zipfile
import asyncio
import pytz
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from psycopg2.extras import RealDictCursor
from app.config import PRIMARY_ADMIN_ID, USER_PROFILES_DIR, PLAN_TIERS
from app.database import (
    get_all_users, set_maintenance_until, get_maintenance_until, 
    get_user_profile, get_db, release_db, sync_user_json_profile, toggle_user_ban_status,
    get_paid_users, admin_update_user_name, admin_delete_user_account, get_ist_date_str,
    get_pending_announcements_list, get_sent_announcements_list, get_announcement_by_id,
    delete_scheduled_announcement, get_broadcast_deliveries, get_blocked_bot_users,
    create_flash_sale, get_active_flash_sale, stop_active_flash_sale, calculate_discounted_price
)
from app.pdf_generator import generate_student_pdf_report
from app.stats import get_user_performance_summary, calculate_user_rank, calculate_user_percentile

logger = logging.getLogger(__name__)
USERS_PER_PAGE = 8
ADMIN_AUTH_SESSIONS = {}

DISCOUNT_OPTIONS = [5, 10, 15, 20, 25, 30, 40]
DURATION_OPTIONS = [
    ("1 Hour", 1, "h"),
    ("6 Hours", 6, "h"),
    ("12 Hours", 12, "h"),
    ("24 Hours (1 Day)", 24, "h"),
    ("3 Days", 3, "d"),
    ("7 Days", 7, "d")
]


def clear_admin_user_data_states(context: ContextTypes.DEFAULT_TYPE):
    keys_to_clear = [
        "awaiting_broadcast",
        "awaiting_admin_direct_msg_uid",
        "awaiting_admin_reply_qid",
        "awaiting_admin_warning_msg_uid",
        "awaiting_admin_search",
        "awaiting_admin_editname",
        "awaiting_admin_password",
        "awaiting_admin_new_pass",
        "awaiting_admin_rec_dob",
        "awaiting_admin_rec_email",
        "awaiting_edit_annc_content",
        "awaiting_edit_annc_time",
        "awaiting_edit_live_broadcast",
        "awaiting_sale_name"
    ]
    for key in keys_to_clear:
        context.user_data.pop(key, None)


def get_admin_nav_buttons(target_uid: int = None):
    row1 = [InlineKeyboardButton("👑 Return to Admin Portal", callback_data="admin_home")]
    if target_uid:
        row1.append(InlineKeyboardButton("🔙 Student Dashboard", callback_data=f"admin_inspect_u_{target_uid}"))
    return InlineKeyboardMarkup([
        row1,
        [InlineKeyboardButton("📩 Student Support Threads", callback_data="admin_view_student_threads_0")]
    ])


async def fast_concurrent_broadcast(bot, user_ids, text, reply_markup=None, parse_mode="Markdown", photo=None, video=None, media_type="text", annc_id=None):
    from app.database import record_blocked_user, record_broadcast_delivery

    async def send_single(uid):
        try:
            m = None
            if media_type == "photo" and photo:
                m = await bot.send_photo(
                    chat_id=uid,
                    photo=photo,
                    caption=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                    disable_notification=False
                )
            elif media_type == "video" and video:
                m = await bot.send_video(
                    chat_id=uid,
                    video=video,
                    caption=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                    disable_notification=False
                )
            else:
                m = await bot.send_message(
                    chat_id=uid,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                    disable_notification=False
                )
            
            if annc_id and m:
                asyncio.create_task(asyncio.to_thread(record_broadcast_delivery, annc_id, uid, m.message_id))
            return True
        except Exception as e:
            err_str = str(e).lower()
            if "forbidden" in err_str or "blocked" in err_str or "deactivated" in err_str or "chat not found" in err_str:
                asyncio.create_task(asyncio.to_thread(record_blocked_user, uid))
            return False

    batch_size = 40
    successful_deliveries = 0
    for i in range(0, len(user_ids), batch_size):
        batch = user_ids[i:i + batch_size]
        results = await asyncio.gather(*(send_single(uid) for uid in batch))
        successful_deliveries += sum(1 for r in results if r)
        await asyncio.sleep(0.03)
    return successful_deliveries


async def fast_concurrent_edit(bot, deliveries, new_text):
    async def edit_single(d):
        try:
            await bot.edit_message_text(
                chat_id=d['user_id'],
                message_id=d['message_id'],
                text=new_text,
                parse_mode="Markdown"
            )
            return True
        except Exception:
            try:
                await bot.edit_message_caption(
                    chat_id=d['user_id'],
                    message_id=d['message_id'],
                    caption=new_text,
                    parse_mode="Markdown"
                )
                return True
            except Exception:
                return False

    batch_size = 40
    success_count = 0
    for i in range(0, len(deliveries), batch_size):
        batch = deliveries[i:i + batch_size]
        results = await asyncio.gather(*(edit_single(d) for d in batch))
        success_count += sum(1 for r in results if r)
        await asyncio.sleep(0.03)
    return success_count


async def fast_concurrent_delete(bot, deliveries):
    async def delete_single(d):
        try:
            await bot.delete_message(chat_id=d['user_id'], message_id=d['message_id'])
            return True
        except Exception:
            return False

    batch_size = 40
    success_count = 0
    for i in range(0, len(deliveries), batch_size):
        batch = deliveries[i:i + batch_size]
        results = await asyncio.gather(*(delete_single(d) for d in batch))
        success_count += sum(1 for r in results if r)
        await asyncio.sleep(0.03)
    return success_count


def get_stored_admin_password() -> str:
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT password_hash FROM admin_security WHERE id = 1")
        row = cursor.fetchone()
        cursor.close()
        release_db(conn)
        if row and row[0]:
            return str(row[0]).strip()
        return "Him@5330"
    except Exception:
        if conn:
            release_db(conn)
        return "Him@5330"


def update_admin_password_db(new_pass: str) -> bool:
    conn = None
    try:
        ist = pytz.timezone("Asia/Kolkata")
        now_str = datetime.now(ist).strftime("%Y-%m-%d %I:%M %p IST")
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO admin_security (id, admin_id, password_hash, dob_recovery, email_recovery, updated_at)
            VALUES (%s, %s, %s, '09081999', 'hd533044@gmail.com', %s)
            ON CONFLICT (id) DO UPDATE SET password_hash = EXCLUDED.password_hash, updated_at = EXCLUDED.updated_at
            """,
            (1, PRIMARY_ADMIN_ID, new_pass, now_str)
        )
        conn.commit()
        cursor.close()
        release_db(conn)
        return True
    except Exception as err:
        if conn:
            release_db(conn)
        logger.error(f"[ADMIN PASS UPDATE DB ERROR] {err}")
        return False


def is_admin_authenticated(user_id: int) -> bool:
    if user_id != PRIMARY_ADMIN_ID:
        return False
    auth_time = ADMIN_AUTH_SESSIONS.get(user_id, 0)
    return (time.time() - auth_time) < 1800


def get_unique_students_with_queries_count() -> int:
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(DISTINCT user_id) FROM student_queries WHERE status = 'PENDING'")
        count = cursor.fetchone()[0]
        cursor.close()
        release_db(conn)
        return count
    except Exception:
        if conn:
            release_db(conn)
        return 0


def get_strict_paid_users():
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            """
            SELECT DISTINCT u.* FROM users u
            INNER JOIN payment_transactions pt ON u.user_id = pt.user_id
            WHERE pt.plan_key != 'FREE_DEMO' AND pt.amount_paid > 0
            ORDER BY u.created_at DESC
            """
        )
        rows = cursor.fetchall()
        cursor.close()
        release_db(conn)
        return [dict(r) for r in rows] if rows else []
    except Exception:
        if conn:
            release_db(conn)
        return []


def get_strict_demo_users():
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            """
            SELECT * FROM users 
            WHERE demo_used = 1 AND (payment_id IS NULL OR payment_id = 'DEMO_PASS' OR payment_id = 'OFFICIAL_SUBSCRIBED')
            AND user_id NOT IN (
                SELECT user_id FROM payment_transactions WHERE plan_key != 'FREE_DEMO' AND amount_paid > 0
            )
            ORDER BY created_at DESC
            """
        )
        rows = cursor.fetchall()
        cursor.close()
        release_db(conn)
        return [dict(r) for r in rows] if rows else []
    except Exception:
        if conn:
            release_db(conn)
        return []


def calculate_financial_revenue():
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=now.weekday())
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT amount_paid, created_at FROM payment_transactions WHERE plan_key != 'FREE_DEMO'")
        rows = cursor.fetchall()
        cursor.close()
        release_db(conn)

        rev_today = 0
        rev_week = 0
        rev_month = 0
        rev_all = 0

        for r in rows:
            amt = r.get("amount_paid", 0) or 0
            rev_all += amt
            dt_str = r.get("created_at", "")
            try:
                dt = datetime.strptime(dt_str, "%d %b %Y, %I:%M %p IST")
                dt = ist.localize(dt) if dt.tzinfo is None else dt
                if dt >= today_start:
                    rev_today += amt
                if dt >= week_start.replace(hour=0, minute=0, second=0, microsecond=0):
                    rev_week += amt
                if dt >= month_start:
                    rev_month += amt
            except Exception:
                pass

        return {
            "today": rev_today,
            "week": rev_week,
            "month": rev_month,
            "all": rev_all
        }
    except Exception:
        if conn:
            release_db(conn)
        return {"today": 0, "week": 0, "month": 0, "all": 0}


def get_currently_online_users(minutes: int = 15):
    conn = None
    try:
        ist = pytz.timezone("Asia/Kolkata")
        now_epoch = int(datetime.now(ist).timestamp())
        threshold_epoch = now_epoch - (minutes * 60)

        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "SELECT user_id, full_name, student_id, last_active FROM users WHERE last_activity_epoch >= %s ORDER BY last_activity_epoch DESC",
            (threshold_epoch,)
        )
        rows = cursor.fetchall()
        cursor.close()
        release_db(conn)
        return [dict(r) for r in rows] if rows else []
    except Exception:
        if conn:
            release_db(conn)
        return []


def get_platform_usage_summary():
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT SUM(seconds_spent) FROM user_activity_time")
        total_sec = cursor.fetchone()[0] or 0
        cursor.close()
        release_db(conn)

        hours = round(total_sec / 3600.0, 2)
        mins = round(total_sec / 60.0, 1)
        return {"seconds": total_sec, "minutes": mins, "hours": hours}
    except Exception:
        if conn:
            release_db(conn)
        return {"seconds": 0, "minutes": 0.0, "hours": 0.0}


def get_pdf_generation_analytics():
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS pdf_generation_logs (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                pdf_type TEXT,
                generated_at TEXT
            )
            """
        )
        cursor.execute(
            """
            SELECT p.user_id, u.full_name, u.student_id, p.pdf_type, p.generated_at 
            FROM pdf_generation_logs p 
            LEFT JOIN users u ON p.user_id = u.user_id 
            ORDER BY p.id DESC LIMIT 30
            """
        )
        rows = cursor.fetchall()
        cursor.close()
        release_db(conn)
        return [dict(r) for r in rows] if rows else []
    except Exception:
        if conn:
            release_db(conn)
        return []


async def admin_portal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_admin_user_data_states(context)
    user_id = update.effective_user.id
    if user_id != PRIMARY_ADMIN_ID:
        reject_msg = "I only listen to Himanshu Sir, sorry you're not Himanshu Sir 😎"
        if update.callback_query:
            await update.callback_query.answer(reject_msg, show_alert=True)
        else:
            await update.message.reply_text(reject_msg)
        return

    if not is_admin_authenticated(user_id):
        context.user_data["awaiting_admin_password"] = True
        msg = (
            "🔒 **HIMANSHU'S ADMIN PORTAL HAS BEEN LOCKED ENTER PASSWORD TO UNLOCK** 🔒\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Your session timed out after 30 minutes of inactivity.\n"
            "🔑 Please reply with the Master Admin Password or tap below to enter password:"
        )
        unlock_btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔑 Enter Password & Unlock Dashboard", callback_data="admin_unlock_prompt")],
            [InlineKeyboardButton("🔑 Forgot Password / Recovery Reset", callback_data="admin_forgot_pass_step1")]
        ])
        if update.callback_query:
            await update.callback_query.answer("🔒 Admin Portal Locked!", show_alert=True)
            await update.callback_query.message.reply_text(msg, reply_markup=unlock_btn, parse_mode="Markdown")
        else:
            await update.message.reply_text(msg, reply_markup=unlock_btn, parse_mode="Markdown")
        return

    users = get_all_users()
    paid_users = get_strict_paid_users()
    demo_users = get_strict_demo_users()
    online_15m = get_currently_online_users(15)
    blocked_users = get_blocked_bot_users()
    pending_students_count = get_unique_students_with_queries_count()
    usage_stats = get_platform_usage_summary()

    m_until = get_maintenance_until()
    now_ts = int(time.time())
    m_status = "🟢 Active (Online)" if now_ts >= m_until else "🔴 PAUSED (Maintenance Mode)"

    active_sale = get_active_flash_sale()
    sale_btn_label = f"🔥 Sale Offers (🟢 {int(float(active_sale['discount_percent']))}% Live)" if active_sale else "⚡ Sale Offers & Discounts"

    keyboard = [
        [InlineKeyboardButton("📊 Power Live Intelligence Overview", callback_data="admin_popup_overview")],
        [InlineKeyboardButton(f"📩 Student Support Threads ({pending_students_count} Unread)", callback_data="admin_view_student_threads_0")],
        [
            InlineKeyboardButton(sale_btn_label, callback_data="admin_sale_dashboard"),
            InlineKeyboardButton("🎟 Create Promo Code", callback_data="admin_create_promo")
        ],
        [
            InlineKeyboardButton("📢 Schedule Announcement", callback_data="admin_schedule_annc"),
            InlineKeyboardButton("📋 Manage Scheduled Posts", callback_data="admin_list_pending_annc_0")
        ],
        [
            InlineKeyboardButton("📢 Live Sent Broadcasts", callback_data="admin_list_sent_annc_0"),
            InlineKeyboardButton(f"🛑 Blocked Users ({len(blocked_users)})", callback_data="admin_list_blocked_users_0")
        ],
        [
            InlineKeyboardButton(f"⚡ Online (15m: {len(online_15m)})", callback_data="admin_live_users_menu"),
            InlineKeyboardButton(f"💳 Paid VIP ({len(paid_users)})", callback_data="admin_paid_users_page_0")
        ],
        [
            InlineKeyboardButton(f"🎁 Free Demo ({len(demo_users)})", callback_data="admin_demo_users_page_0"),
            InlineKeyboardButton("📄 PDF Generation Logs", callback_data="admin_pdf_logs")
        ],
        [
            InlineKeyboardButton("👥 Student Directory", callback_data="admin_users_page_0"),
            InlineKeyboardButton("🔍 Search Student", callback_data="admin_search_prompt")
        ],
        [InlineKeyboardButton("💰 Revenue Dashboard", callback_data="admin_financial_stats"), InlineKeyboardButton("⏱ Platform Time Telemetry", callback_data="admin_total_platform_usage")],
        [InlineKeyboardButton("🎁 Gift Quota Boost to ALL", callback_data="admin_mass_grant_menu"), InlineKeyboardButton("📊 Command Usage Analytics", callback_data="admin_command_stats")],
        [InlineKeyboardButton("📦 Export Ledgers (.zip)", callback_data="admin_export_zip")],
        [
            InlineKeyboardButton("⏸ Pause 5m", callback_data="admin_pause_5"), 
            InlineKeyboardButton("⏸ Pause 10m", callback_data="admin_pause_10"),
            InlineKeyboardButton("⏸ Pause 1h", callback_data="admin_pause_60")
        ],
        [
            InlineKeyboardButton("⏸ Pause 3h", callback_data="admin_pause_180"),
            InlineKeyboardButton("⏸ Pause 6h", callback_data="admin_pause_360"),
            InlineKeyboardButton("⏸ Pause 24h", callback_data="admin_pause_1440")
        ],
        [InlineKeyboardButton("▶️ Resume Bot Services Immediately", callback_data="admin_resume_now")],
        [InlineKeyboardButton("🔑 Change Password", callback_data="admin_change_pass_prompt"), InlineKeyboardButton("🔒 Lock Session", callback_data="admin_lock_session")],
        [InlineKeyboardButton("📢 Global Broadcast to ALL Users", callback_data="admin_broadcast")]
    ]

    sale_summary_line = f"🔥 **Active Flash Sale:** `{active_sale['sale_name']} ({int(float(active_sale['discount_percent']))}% OFF)`" if active_sale else "🔥 **Flash Sale Status:** `🔴 Inactive (Normal Prices)`"

    msg = (
        f"👑 **MASTER ADMIN PORTAL — Himanshu Sir** 👑\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 **Total Registered Students:** `{len(users)}`\n"
        f"💎 **Actual Paid VIP Subscribers:** `{len(paid_users)}`\n"
        f"🎁 **Free Demo Users:** `{len(demo_users)}`\n"
        f"🛑 **Blocked / Inactive Users:** `{len(blocked_users)}`\n"
        f"⚡ **Online Users (15m):** `{len(online_15m)}`\n"
        f"⏱ **Total Platform Practice Time:** `{usage_stats['hours']} Hours`\n"
        f"📩 **Unread Support Queries:** `{pending_students_count}`\n"
        f"⚡ **Bot System Status:** `{m_status}`\n"
        f"{sale_summary_line}\n\n"
        f"Select an administrative action below:"
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def admin_view_user_payments_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PRIMARY_ADMIN_ID:
        await query.answer("Unauthorized!", show_alert=True)
        return

    await query.answer()
    target_uid = int(query.data.replace("admin_view_payments_", ""))

    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT DISTINCT ON (payment_id) * FROM payment_transactions WHERE user_id = %s ORDER BY payment_id, id DESC", (target_uid,))
        rows = cursor.fetchall()
        cursor.close()
        release_db(conn)
    except Exception as e:
        if conn:
            release_db(conn)
        rows = []

    profile = get_user_profile(target_uid) or {}
    sid = profile.get("student_id", f"USER_{target_uid}")
    name = profile.get("full_name", "Student")

    lines = [
        f"💳 **ALL PAYMENTS FOR STUDENT** 💳\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 **Name:** {name} (`{sid}`)\n"
        f"🆔 **Telegram ID:** `{target_uid}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    ]

    if rows:
        for idx, r in enumerate(rows, start=1):
            created_time = r.get('created_at') or 'N/A'
            p_exp = r.get('expiry_at') or 'Active'
            lines.append(
                f"**{idx}. Plan:** `{r.get('plan_name', 'VIP PACK')}` (₹{r.get('amount_paid', 0)})\n"
                f"    🆔 Txn ID: `{r.get('payment_id', 'N/A')}`\n"
                f"    ⚡ Quota: `+{r.get('daily_quota', 0)} Qs` | Days: `{r.get('validity_days', 0)}`\n"
                f"    📅 Date: `{created_time}`\n"
                f"    ⏳ Expiry: `{p_exp}`\n"
                f"    ──────────────────────────"
            )
    else:
        lines.append("ℹ️ *No payment transactions recorded for this user yet.*")

    msg = "\n".join(lines)
    if len(msg) > 4000:
        msg = msg[:3950] + "\n\n*(Truncated due to Telegram length limit)*"

    back_btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("👑 Grant Paid VIP Pack", callback_data=f"admin_grant_menu_{target_uid}")],
        [InlineKeyboardButton("🔙 Back to Student Profile", callback_data=f"admin_inspect_u_{target_uid}")],
        [InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]
    ])
    await query.edit_message_text(msg, reply_markup=back_btn, parse_mode="Markdown")


async def admin_grant_plan_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PRIMARY_ADMIN_ID: return
    await query.answer()

    target_uid = int(query.data.replace("admin_grant_menu_", ""))
    profile = get_user_profile(target_uid) or {}
    name = profile.get("full_name", "Student")

    keyboard = [
        [InlineKeyboardButton("📦 Grant BRONZE (₹5 - 80 Qs)", callback_data=f"admin_exec_grant_{target_uid}_BRONZE")],
        [InlineKeyboardButton("📦 Grant SILVER (₹10 - 100 Qs)", callback_data=f"admin_exec_grant_{target_uid}_SILVER")],
        [InlineKeyboardButton("📦 Grant GOLD (₹15 - 120 Qs)", callback_data=f"admin_exec_grant_{target_uid}_GOLD")],
        [InlineKeyboardButton("📦 Grant DIAMOND (₹20 - 150 Qs)", callback_data=f"admin_exec_grant_{target_uid}_DIAMOND")],
        [InlineKeyboardButton("📦 Grant LEARNWITHHIM (₹25 - 250 Qs)", callback_data=f"admin_exec_grant_{target_uid}_LEARNWITHHIM")],
        [InlineKeyboardButton("📦 Grant PLATINUM (₹40 - 300 Qs)", callback_data=f"admin_exec_grant_{target_uid}_PLATINUM")],
        [InlineKeyboardButton("📦 Grant RUBY (₹50 - 400 Qs)", callback_data=f"admin_exec_grant_{target_uid}_RUBY")],
        [InlineKeyboardButton("📦 Grant MEGA PACK (₹80 - 500 Qs)", callback_data=f"admin_exec_grant_{target_uid}_MEGA")],
        [InlineKeyboardButton("🔙 Back to Student Profile", callback_data=f"admin_inspect_u_{target_uid}")],
        [InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]
    ]

    msg = f"👑 **GRANT PAID PACK TO {name}** 👑\nSelect a VIP plan to credit and stack:"
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def admin_execute_grant_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != PRIMARY_ADMIN_ID: return
    await query.answer()

    parts = query.data.replace("admin_exec_grant_", "").split("_", 1)
    target_uid = int(parts[0])
    plan_key = parts[1]

    plan = PLAN_TIERS.get(plan_key)
    if not plan: return

    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    expiry_dt = now + timedelta(days=plan["days"])
    expiry_str = expiry_dt.strftime("%Y-%m-%d %H:%M:%S IST")
    payment_time_str = now.strftime("%d %b %Y, %I:%M %p IST")
    payment_id = f"ADMIN_GRANT_{int(time.time())}"

    profile = get_user_profile(target_uid) or {}
    current_bal = profile.get("paid_question_balance", 0) or 0
    new_bal = current_bal + plan["daily_limit"]

    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET paid_question_balance = %s, vip_pass_expiry = %s, payment_id = %s, payment_timestamp = %s WHERE user_id = %s",
            (new_bal, expiry_str, payment_id, payment_time_str, target_uid)
        )
        cursor.execute(
            """
            INSERT INTO payment_transactions 
            (user_id, payment_id, plan_key, plan_name, amount_paid, daily_quota, validity_days, created_at, expiry_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (payment_id) DO NOTHING
            """,
            (target_uid, payment_id, plan_key, plan["name"], plan["price"], plan["daily_limit"], plan["days"], payment_time_str, expiry_str)
        )
        conn.commit()
        cursor.close()
        release_db(conn)

        from app.telegram_bot import PROFILE_CACHE
        PROFILE_CACHE.pop(target_uid, None)
        sync_user_json_profile(target_uid)
    except Exception as e:
        if conn: release_db(conn)
        await query.message.reply_text(f"⚠️ Error: {e}")
        return

    user_broadcast_text = (
        f"🎁 **SPECIAL ANNOUNCEMENT: VIP PLAN GRANTED!** 🎁\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎉 **Himanshu Sir has granted you the {plan['name']}!**\n\n"
        f"⚡ **Stacked Daily Limit:** `{new_bal} Questions / Day`\n"
        f"⏳ **VIP Pass Expiry:** `{expiry_str}`\n"
        f"🧾 **Grant Reference ID:** `{payment_id}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🚀 Tap **/quiz** below to launch your practice session now!"
    )

    try:
        user_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Launch Quiz Now", callback_data="cmd_quiz"), InlineKeyboardButton("💳 My Plan", callback_data="cmd_myplan")]])
        await context.bot.send_message(chat_id=target_uid, text=user_broadcast_text, reply_markup=user_btn, parse_mode="Markdown", disable_notification=False)
    except Exception:
        pass

    back_btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Student Profile", callback_data=f"admin_inspect_u_{target_uid}")],
        [InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]
    ])
    await query.edit_message_text(f"✅ **PLAN GRANTED & BROADCASTED!**\nGranted `{plan['name']}` to user `{target_uid}`.", reply_markup=back_btn, parse_mode="Markdown")


async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    if data == "admin_unlock_prompt":
        await query.answer()
        context.user_data["awaiting_admin_password"] = True
        cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("👑 Return to Admin Portal", callback_data="admin_home")]])
        await query.edit_message_text("🔑 **ENTER ADMIN PASSWORD**\n\nPlease reply with your Master Admin Password to unlock the dashboard:", reply_markup=cancel_btn, parse_mode="Markdown")
        return

    if data == "admin_forgot_pass_step1":
        await query.answer()
        context.user_data["awaiting_admin_rec_dob"] = True
        cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("👑 Return to Admin Portal", callback_data="admin_home")]])
        await query.edit_message_text(
            "🔑 **ADMIN PASSWORD RECOVERY (STEP 1/2)**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Please reply with Himanshu Sir's Date of Birth (DDMMYYYY format):",
            reply_markup=cancel_btn,
            parse_mode="Markdown"
        )
        return

    if not is_admin_authenticated(user_id):
        await query.answer("🔒 Session expired or unauthorized! Please type /admin and enter password.", show_alert=True)
        return

    clear_admin_user_data_states(context)
    users = get_all_users()

    # ==============================================================
    # 📊 POWER LIVE INTELLIGENCE OVERVIEW
    # ==============================================================
    if data == "admin_popup_overview":
        await query.answer("📊 Loading Real-Time Power Overview...", show_alert=False)

        ist = pytz.timezone("Asia/Kolkata")
        now = datetime.now(ist)
        today_date_str = get_ist_date_str()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("SELECT COUNT(*) as count FROM users")
        reg_count = cursor.fetchone()['count']

        cursor.execute("""
            SELECT pt.amount_paid, pt.plan_name, pt.created_at, u.full_name, u.student_id, u.user_id
            FROM payment_transactions pt
            LEFT JOIN users u ON pt.user_id = u.user_id
            WHERE pt.plan_key != 'FREE_DEMO'
            ORDER BY pt.id DESC
        """)
        all_txs = cursor.fetchall()

        rev_today = 0
        rev_all = 0
        today_paid_students = []

        for r in all_txs:
            amt = r.get("amount_paid", 0) or 0
            rev_all += amt
            dt_str = r.get("created_at", "")
            try:
                dt = datetime.strptime(dt_str, "%d %b %Y, %I:%M %p IST")
                dt = ist.localize(dt) if dt.tzinfo is None else dt
                if dt >= today_start:
                    rev_today += amt
                    today_paid_students.append({
                        "name": r.get("full_name") or f"User {r.get('user_id')}",
                        "sid": r.get("student_id") or "N/A",
                        "plan": r.get("plan_name") or "VIP Plan",
                        "amt": amt
                    })
            except Exception:
                pass

        cursor.execute("""
            SELECT COUNT(*) as total_attempts_today,
                   COUNT(DISTINCT user_id) as unique_quiz_students,
                   COALESCE(SUM(questions_attempted), 0) as total_qs_today
            FROM quiz_attempts 
            WHERE attempt_date = %s
        """, (today_date_str,))
        quiz_today_stats = cursor.fetchone()

        cursor.execute("SELECT COUNT(*) as count FROM quiz_attempts")
        all_time_attempts = cursor.fetchone()['count']

        cursor.execute("SELECT COUNT(*) as count FROM student_queries WHERE status = 'PENDING'")
        pending_queries = cursor.fetchone()['count']

        cursor.execute("SELECT COUNT(*) as count FROM blocked_bot_users")
        blocked_count = cursor.fetchone()['count']

        cursor.close()
        release_db(conn)

        active_sale = get_active_flash_sale()
        if active_sale:
            pct = int(float(active_sale['discount_percent']))
            now_dt = datetime.now(ist).replace(tzinfo=None)
            valid_until = active_sale['valid_until']
            if hasattr(valid_until, 'tzinfo') and valid_until.tzinfo is not None:
                valid_until = valid_until.astimezone(ist).replace(tzinfo=None)
            diff_sec = max(0, int((valid_until - now_dt).total_seconds()))
            hrs_left = diff_sec // 3600
            mins_left = (diff_sec % 3600) // 60
            sale_line = f"🔥 **Active Flash Sale:** `{active_sale['sale_name']} ({pct}% OFF - {hrs_left}h {mins_left}m left)`"
        else:
            sale_line = "🔥 **Active Flash Sale:** `🔴 Inactive (Normal Base Prices)`"

        if today_paid_students:
            paid_st_lines = []
            for idx, s in enumerate(today_paid_students[:10], start=1):
                paid_st_lines.append(f"  {idx}. **{s['name']}** (`{s['sid']}`) — `{s['plan']}` (₹{s['amt']})")
            paid_st_display = "\n".join(paid_st_lines)
            if len(today_paid_students) > 10:
                paid_st_display += f"\n  *(+ {len(today_paid_students) - 10} more students today)*"
        else:
            paid_st_display = "  ℹ️ *No paid plan purchases recorded today yet.*"

        online_15m = get_currently_online_users(15)

        overview_msg = (
            f"📊 **POWER LIVE INTELLIGENCE DASHBOARD** 📊\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 **Date (IST):** `{today_date_str}`\n"
            f"{sale_line}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 **FINANCIAL REVENUE TELEMETRY:**\n"
            f"• **Today's Revenue:** `₹{rev_today} INR` ({len(today_paid_students)} Purchases)\n"
            f"• **All-Time Gross Revenue:** `₹{rev_all} INR`\n\n"
            f"💳 **TODAY'S PAID VIP STUDENTS ({len(today_paid_students)}):**\n"
            f"{paid_st_display}\n\n"
            f"🎯 **TODAY'S QUIZ ACTIVITY TELEMETRY:**\n"
            f"• **Students Practicing Today:** `{quiz_today_stats['unique_quiz_students']} Students`\n"
            f"• **Quizzes Submitted Today:** `{quiz_today_stats['total_attempts_today']} Quizzes`\n"
            f"• **Total Questions Solved Today:** `{quiz_today_stats['total_qs_today']} Questions`\n\n"
            f"👥 **PLATFORM HEALTH METRICS:**\n"
            f"• **Total Registered Students:** `{reg_count}`\n"
            f"• **Active in Last 15 Mins:** `{len(online_15m)} Students`\n"
            f"• **Unread Support Queries:** `{pending_queries}`\n"
            f"• **Blocked Users:** `{blocked_count}`\n"
            f"• **All-Time Quizzes Attempted:** `{all_time_attempts}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👑 *Master Admin Telemetry — Learn with HiM*"
        )

        keyboard = [
            [InlineKeyboardButton("🔄 Refresh Real-Time Data", callback_data="admin_popup_overview")],
            [InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]
        ]
        await query.edit_message_text(overview_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    # ==============================================================
    # ⚡ FLASH SALE & DISCOUNT OFFER WIZARD AND LIVE CONTROLS
    # ==============================================================
    elif data == "admin_sale_dashboard":
        await query.answer()
        active_sale = get_active_flash_sale()

        if active_sale:
            now_dt = datetime.now(pytz.timezone("Asia/Kolkata")).replace(tzinfo=None)
            valid_until = active_sale['valid_until']
            if hasattr(valid_until, 'tzinfo') and valid_until.tzinfo is not None:
                valid_until = valid_until.astimezone(pytz.timezone("Asia/Kolkata")).replace(tzinfo=None)
            diff_sec = max(0, int((valid_until - now_dt).total_seconds()))
            hrs_left = diff_sec // 3600
            mins_left = (diff_sec % 3600) // 60
            valid_until_str = valid_until.strftime("%d %b %Y, %I:%M %p IST")
            pct = int(float(active_sale['discount_percent']))

            table_lines = ["\n📊 **Calculated Discounted Pricing Matrix:**"]
            for k, p in PLAN_TIERS.items():
                if k == "FREE_DEMO": continue
                disc_p = calculate_discounted_price(p['price'], pct)
                table_lines.append(f"• **{p['name']}:** ~₹{p['price']}~ ➡️ **₹{disc_p}** (-₹{p['price']-disc_p})")

            pricing_summary = "\n".join(table_lines)

            msg = (
                f"🔥 **FLASH SALE & OFFERS CONTROL PANEL** 🔥\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🟢 **Status:** `LIVE & ACTIVE`\n"
                f"🏷 **Sale Name / Purpose:** `{active_sale['sale_name']}`\n"
                f"💸 **Discount:** `{pct}% OFF` (Across all VIP Packs)\n"
                f"⏳ **Time Remaining:** `{hrs_left}h {mins_left}m`\n"
                f"📅 **Valid Until:** `{valid_until_str}`\n"
                f"{pricing_summary}\n\n"
                f"⚡ *Admin can reinstate original plan prices immediately anytime.*"
            )

            keyboard = [
                [InlineKeyboardButton("🛑 Stop Sale & Reinstate Normal Prices", callback_data="admin_sale_stop_confirm")],
                [InlineKeyboardButton("📢 Re-Broadcast Offer Announcement", callback_data="admin_sale_rebroadcast")],
                [InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]
            ]
        else:
            msg = (
                f"⚡ **FLASH SALE & DISCOUNT OFFERS ENGINE** ⚡\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🔴 **Current Status:** `No Active Sale (Normal Base Prices Live)`\n\n"
                f"💡 Launch a pre-calculated flash discount (5% to 40%) with automatic Razorpay price recalculation, expiry timer, and one-click broadcast to all students.\n\n"
                f"Tap **➕ Launch New Sale Offer** below to start the setup wizard:"
            )
            keyboard = [
                [InlineKeyboardButton("➕ Launch New Sale Offer", callback_data="admin_sale_wizard_step1")],
                [InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]
            ]

        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "admin_sale_wizard_step1":
        await query.answer()
        context.user_data["awaiting_sale_name"] = True
        cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_sale_dashboard")]])
        await query.edit_message_text(
            "🏷 **STEP 1/4: NAME OF SALE / PURPOSE OF DISCOUNT**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Please reply with the title/name for this sale offer:\n"
            "*(Examples: `Monsoon Special Offer`, `Independence Day Mega Sale`, `BSF HCM Revision Discount`)*",
            reply_markup=cancel_btn,
            parse_mode="Markdown"
        )

    elif data.startswith("admin_sale_pct_"):
        await query.answer()
        pct = int(data.replace("admin_sale_pct_", ""))
        context.user_data["sale_percent"] = pct
        sale_name = context.user_data.get("sale_name", "Special Discount Offer")

        duration_buttons = []
        row = []
        for label, val, unit in DURATION_OPTIONS:
            cb_val = f"admin_sale_dur_{val}_{unit}"
            row.append(InlineKeyboardButton(f"⏱ {label}", callback_data=cb_val))
            if len(row) == 2:
                duration_buttons.append(row)
                row = []
        if row:
            duration_buttons.append(row)
        duration_buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="admin_sale_dashboard")])

        msg = (
            f"🏷 **Sale Name:** `{sale_name}`\n"
            f"💸 **Discount:** `{pct}% OFF`\n\n"
            f"⏳ **STEP 3/4: CHOOSE SALE DURATION / VALIDITY**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Select how long this discounted pricing should remain active:"
        )
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(duration_buttons), parse_mode="Markdown")

    elif data.startswith("admin_sale_dur_"):
        await query.answer()
        parts = data.replace("admin_sale_dur_", "").split("_")
        val = int(parts[0])
        unit = parts[1]

        ist = pytz.timezone("Asia/Kolkata")
        now_dt = datetime.now(ist).replace(tzinfo=None)

        if unit == "h":
            end_dt = now_dt + timedelta(hours=val)
            dur_label = f"{val} Hour(s)"
        else:
            end_dt = now_dt + timedelta(days=val)
            dur_label = f"{val} Day(s)"

        context.user_data["sale_end_dt"] = end_dt
        context.user_data["sale_dur_label"] = dur_label

        sale_name = context.user_data.get("sale_name", "Special Flash Sale")
        pct = context.user_data.get("sale_percent", 20)

        table_lines = ["\n📊 **Pre-calculated Price Drops for All VIP Packs:**"]
        for k, p in PLAN_TIERS.items():
            if k == "FREE_DEMO": continue
            disc_p = calculate_discounted_price(p['price'], pct)
            table_lines.append(f"• **{p['name']}:** ~₹{p['price']}~ ➡️ **₹{disc_p}** (-₹{p['price']-disc_p})")

        pricing_summary = "\n".join(table_lines)
        end_str = end_dt.strftime("%d %b %Y, %I:%M %p IST")

        msg = (
            f"🚀 **STEP 4/4: CONFIRM & LAUNCH SALE OFFER**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷 **Sale Title:** `{sale_name}`\n"
            f"💸 **Discount Tier:** `{pct}% OFF`\n"
            f"⏳ **Validity Duration:** `{dur_label}`\n"
            f"📅 **Offer Expiry Time:** `{end_str}`\n"
            f"{pricing_summary}\n\n"
            f"Choose how you want to launch this offer:"
        )

        confirm_keyboard = [
            [InlineKeyboardButton("🚀 Launch & Push Announcement to ALL", callback_data="admin_sale_launch_broadcast")],
            [InlineKeyboardButton("🤫 Launch Silently (No Global Broadcast)", callback_data="admin_sale_launch_silent")],
            [InlineKeyboardButton("❌ Cancel", callback_data="admin_sale_dashboard")]
        ]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(confirm_keyboard), parse_mode="Markdown")

    elif data in ("admin_sale_launch_broadcast", "admin_sale_launch_silent"):
        await query.answer()
        do_broadcast = (data == "admin_sale_launch_broadcast")
        
        sale_name = context.user_data.get("sale_name", "Special Flash Sale")
        pct = context.user_data.get("sale_percent", 20)
        end_dt = context.user_data.get("sale_end_dt", datetime.now(pytz.timezone("Asia/Kolkata")).replace(tzinfo=None) + timedelta(hours=24))
        dur_label = context.user_data.get("sale_dur_label", "24 Hours")

        sale_res = create_flash_sale(sale_name, pct, end_dt, user_id)
        if "error" in sale_res:
            await query.edit_message_text(f"❌ **Failed to launch sale:** {sale_res['error']}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_sale_dashboard")]]))
            return

        end_str = end_dt.strftime("%d %b %Y, %I:%M %p IST")

        if do_broadcast:
            b_msg = (
                f"🔥 **SPECIAL SALE ANNOUNCEMENT: {sale_name.upper()}!** 🔥\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🎉 **Himanshu Sir has unlocked a FLAT {pct}% DISCOUNT on ALL VIP Packs!**\n\n"
                f"⚡ All practice question packs are now available at pre-calculated discounted prices.\n"
                f"⏳ **Offer Validity:** `{dur_label}` (Ends: `{end_str}`)\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🚀 Tap **/plans** below to upgrade your daily quota at discounted prices now!"
            )
            btn = InlineKeyboardMarkup([[InlineKeyboardButton("💳 View Discounted Plans", callback_data="cmd_plans"), InlineKeyboardButton("🚀 Launch Quiz", callback_data="cmd_quiz")]])
            
            target_uids = [u['user_id'] for u in users if not u.get('is_banned')]
            sent_count = await fast_concurrent_broadcast(context.bot, target_uids, b_msg, reply_markup=btn)
            broadcast_status = f"✅ Broadcast delivered to `{sent_count}/{len(target_uids)}` students."
        else:
            broadcast_status = "🤫 Launched silently (no mass broadcast sent)."

        msg = (
            f"🎉 **FLASH SALE LAUNCHED SUCCESSFULLY!** 🎉\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷 **Sale Name:** `{sale_name}`\n"
            f"💸 **Discount:** `{pct}% OFF`\n"
            f"⏳ **Expires At:** `{end_str}`\n"
            f"📢 **Broadcast:** {broadcast_status}\n\n"
            f"Prices on **/plans** and Razorpay payment links are now actively discounted!"
        )
        keyboard = [
            [InlineKeyboardButton("⚡ View Sale Control Panel", callback_data="admin_sale_dashboard")],
            [InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]
        ]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "admin_sale_stop_confirm":
        await query.answer()
        active_sale = get_active_flash_sale()
        if not active_sale:
            await query.edit_message_text("ℹ️ No active sale to stop.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_sale_dashboard")]]))
            return

        confirm_btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛑 Yes, Stop Sale & Reset Normal Prices", callback_data="admin_sale_stop_execute")],
            [InlineKeyboardButton("❌ Cancel", callback_data="admin_sale_dashboard")]
        ])
        await query.edit_message_text(
            f"⚠️ **CONFIRM SALE TERMINATION** ⚠️\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Are you sure you want to stop **{active_sale['sale_name']}** ({int(float(active_sale['discount_percent']))}% OFF) right now?\n\n"
            f"This will immediately reinstate original prices for all students on **/plans**.",
            reply_markup=confirm_btn,
            parse_mode="Markdown"
        )

    elif data == "admin_sale_stop_execute":
        await query.answer()
        stop_active_flash_sale()
        nav = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ Flash Sale Panel", callback_data="admin_sale_dashboard")],
            [InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]
        ])
        await query.edit_message_text(
            "🛑 **FLASH SALE STOPPED & REINSTATED!** 🛑\n\n"
            "All plan prices and Razorpay payment links have been returned to their original base prices immediately.",
            reply_markup=nav,
            parse_mode="Markdown"
        )

    elif data == "admin_sale_rebroadcast":
        await query.answer()
        active_sale = get_active_flash_sale()
        if not active_sale:
            await query.edit_message_text("ℹ️ No active sale to broadcast.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_sale_dashboard")]]))
            return

        now_dt = datetime.now(pytz.timezone("Asia/Kolkata")).replace(tzinfo=None)
        valid_until = active_sale['valid_until']
        if hasattr(valid_until, 'tzinfo') and valid_until.tzinfo is not None:
            valid_until = valid_until.astimezone(pytz.timezone("Asia/Kolkata")).replace(tzinfo=None)
        diff_sec = max(0, int((valid_until - now_dt).total_seconds()))
        hrs_left = diff_sec // 3600
        mins_left = (diff_sec % 3600) // 60
        pct = int(float(active_sale['discount_percent']))

        b_msg = (
            f"⏰ **REMINDER: {active_sale['sale_name'].upper()} ENDING SOON!** 🔥\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎉 **FLAT {pct}% DISCOUNT is live on ALL VIP Packs!**\n\n"
            f"⏳ **Hurry, offer ends in:** `{hrs_left} Hours {mins_left} Mins`!\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🚀 Tap **/plans** below to upgrade your daily questions before the sale ends!"
        )
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("💳 Grab Discounted Plan", callback_data="cmd_plans")]])
        target_uids = [u['user_id'] for u in users if not u.get('is_banned')]
        
        await query.edit_message_text("⏳ **Re-broadcasting sale reminder to all students...**")
        sent_count = await fast_concurrent_broadcast(context.bot, target_uids, b_msg, reply_markup=btn)

        nav = InlineKeyboardMarkup([[InlineKeyboardButton("⚡ Return to Sale Panel", callback_data="admin_sale_dashboard")], [InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]])
        await query.edit_message_text(f"✅ **SALE REMINDER BROADCASTED!**\nDelivered to `{sent_count}/{len(target_uids)}` students.", reply_markup=nav, parse_mode="Markdown")

    # ==============================================================
    # 🛑 BLOCKED / INACTIVE USERS AUDIT MODULE
    # ==============================================================
    elif data.startswith("admin_list_blocked_users_"):
        await query.answer()
        page = int(data.replace("admin_list_blocked_users_", ""))
        blocked = get_blocked_bot_users()

        if not blocked:
            nav = InlineKeyboardMarkup([[InlineKeyboardButton("👑 Return to Admin Portal", callback_data="admin_home")]])
            await query.edit_message_text("🛑 **BLOCKED USERS AUDIT**\n\n🎉 No users have blocked the bot yet!", reply_markup=nav, parse_mode="Markdown")
            return

        total_b = len(blocked)
        total_pages = math.ceil(total_b / USERS_PER_PAGE)
        page = max(0, min(page, total_pages - 1))
        page_items = blocked[page * USERS_PER_PAGE:(page + 1) * USERS_PER_PAGE]

        keyboard = []
        for b in page_items:
            name = b.get('full_name') or f"User {b['user_id']}"
            sid = b.get('student_id') or 'N/A'
            b_time = b.get('blocked_at', 'N/A')
            btn_txt = f"🛑 {name} ({sid}) — {b_time}"
            keyboard.append([InlineKeyboardButton(btn_txt, callback_data=f"admin_inspect_u_{b['user_id']}")])

        nav_row = []
        if page > 0: nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"admin_list_blocked_users_{page - 1}"))
        nav_row.append(InlineKeyboardButton(f"📄 Page {page + 1}/{total_pages}", callback_data="ignore"))
        if page < total_pages - 1: nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"admin_list_blocked_users_{page + 1}"))
        keyboard.append(nav_row)
        keyboard.append([InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")])

        await query.edit_message_text(
            f"🛑 **BLOCKED / INACTIVE USERS AUDIT ({total_b} Total)**\n"
            f"These students blocked or deactivated the bot. Tap any student to inspect profile or send a direct message:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    # ==============================================================
    # 📢 SCHEDULED ANNOUNCEMENTS MANAGEMENT
    # ==============================================================
    elif data.startswith("admin_list_pending_annc_"):
        await query.answer()
        page = int(data.replace("admin_list_pending_annc_", ""))
        pending = get_pending_announcements_list()
        
        if not pending:
            nav = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Schedule New Announcement", callback_data="admin_schedule_annc")],
                [InlineKeyboardButton("👑 Return to Admin Portal", callback_data="admin_home")]
            ])
            await query.edit_message_text("📋 **MANAGE SCHEDULED ANNOUNCEMENTS**\n\n🎉 No pending scheduled announcements found in database!", reply_markup=nav, parse_mode="Markdown")
            return

        total_p = len(pending)
        total_pages = math.ceil(total_p / 6)
        page = max(0, min(page, total_pages - 1))
        page_items = pending[page * 6:(page + 1) * 6]

        keyboard = []
        for a in page_items:
            m_dt = a['scheduled_time'].strftime("%d %b, %I:%M %p")
            m_snip = (a['message_text'][:25] + "...") if a['message_text'] else f"[{a['media_type'].upper()}]"
            btn_txt = f"⏰ [{m_dt}] {m_snip}"
            keyboard.append([InlineKeyboardButton(btn_txt, callback_data=f"admin_view_pending_annc_{a['id']}")])

        nav_row = []
        if page > 0: nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"admin_list_pending_annc_{page - 1}"))
        nav_row.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="ignore"))
        if page < total_pages - 1: nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"admin_list_pending_annc_{page + 1}"))
        keyboard.append(nav_row)
        keyboard.append([InlineKeyboardButton("➕ Schedule New Post", callback_data="admin_schedule_annc"), InlineKeyboardButton("👑 Admin Portal", callback_data="admin_home")])

        await query.edit_message_text(f"📋 **PENDING SCHEDULED ANNOUNCEMENTS ({total_p})**\nTap any post to Edit Content, Edit Time, or Delete:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("admin_view_pending_annc_"):
        await query.answer()
        annc_id = int(data.replace("admin_view_pending_annc_", ""))
        a = get_announcement_by_id(annc_id)
        if not a:
            await query.edit_message_text("⚠️ Announcement record not found.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to List", callback_data="admin_list_pending_annc_0")]]))
            return

        scheduled_str = a['scheduled_time'].strftime("%d %b %Y, %I:%M %p IST")
        txt_display = a['message_text'] or "*(No Text / Media Only)*"

        msg = (
            f"📌 **SCHEDULED POST INSPECTION (ID #{a['id']})**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ **Scheduled Time:** `{scheduled_str}`\n"
            f"📝 **Media Type:** `{a['media_type'].upper()}`\n"
            f"🚦 **Current Status:** `{a['status']}`\n\n"
            f"📄 **Post Preview Text:**\n"
            f"{txt_display}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Select an action below:"
        )

        keyboard = [
            [InlineKeyboardButton("✍️ Edit Post Content", callback_data=f"admin_edit_annc_content_prompt_{annc_id}")],
            [InlineKeyboardButton("⏰ Edit Schedule Time", callback_data=f"admin_edit_annc_time_prompt_{annc_id}")],
            [InlineKeyboardButton("🗑 Delete Post Permanently", callback_data=f"admin_del_pending_annc_{annc_id}")],
            [InlineKeyboardButton("🔙 Back to Scheduled List", callback_data="admin_list_pending_annc_0")],
            [InlineKeyboardButton("👑 Return to Admin Portal", callback_data="admin_home")]
        ]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("admin_edit_annc_content_prompt_"):
        await query.answer()
        annc_id = int(data.replace("admin_edit_annc_content_prompt_", ""))
        context.user_data["awaiting_edit_annc_content"] = annc_id
        cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Edit", callback_data=f"admin_view_pending_annc_{annc_id}")]])
        await query.edit_message_text(f"✍️ **EDIT POST CONTENT (ID #{annc_id})**\n\nPlease reply with the new Message text, Photo with caption, or Video:", reply_markup=cancel_btn, parse_mode="Markdown")

    elif data.startswith("admin_edit_annc_time_prompt_"):
        await query.answer()
        annc_id = int(data.replace("admin_edit_annc_time_prompt_", ""))
        context.user_data["awaiting_edit_annc_time"] = annc_id
        cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Edit", callback_data=f"admin_view_pending_annc_{annc_id}")]])
        await query.edit_message_text(
            f"⏰ **EDIT SCHEDULE TIME (ID #{annc_id})**\n\n"
            f"Please reply with the new publishing date and time (IST):\n"
            f"Format: `YYYY-MM-DD HH:MM` (e.g., `2026-08-14 18:30`):",
            reply_markup=cancel_btn,
            parse_mode="Markdown"
        )

    elif data.startswith("admin_del_pending_annc_"):
        await query.answer()
        annc_id = int(data.replace("admin_del_pending_annc_", ""))
        delete_scheduled_announcement(annc_id)
        nav = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Scheduled List", callback_data="admin_list_pending_annc_0")],
            [InlineKeyboardButton("👑 Admin Portal", callback_data="admin_home")]
        ])
        await query.edit_message_text(f"🗑 **SCHEDULED ANNOUNCEMENT #{annc_id} DELETED SUCCESSFULLY!**", reply_markup=nav, parse_mode="Markdown")

    # ==============================================================
    # 📢 LIVE BROADCASTS MANAGEMENT
    # ==============================================================
    elif data.startswith("admin_list_sent_annc_"):
        await query.answer()
        page = int(data.replace("admin_list_sent_annc_", ""))
        sent_posts = get_sent_announcements_list(30)
        
        if not sent_posts:
            nav = InlineKeyboardMarkup([[InlineKeyboardButton("👑 Return to Admin Portal", callback_data="admin_home")]])
            await query.edit_message_text("📢 **LIVE SENT BROADCASTS LOGS**\n\nNo broadcasts recorded in history yet.", reply_markup=nav, parse_mode="Markdown")
            return

        total_p = len(sent_posts)
        total_pages = math.ceil(total_p / 6)
        page = max(0, min(page, total_pages - 1))
        page_items = sent_posts[page * 6:(page + 1) * 6]

        keyboard = []
        for a in page_items:
            m_dt = a['scheduled_time'].strftime("%d %b, %I:%M %p")
            m_snip = (a['message_text'][:20] + "...") if a['message_text'] else f"[{a['media_type'].upper()}]"
            btn_txt = f"📡 [{m_dt}] {m_snip} ({a.get('delivery_count', 0)} users)"
            keyboard.append([InlineKeyboardButton(btn_txt, callback_data=f"admin_view_sent_annc_{a['id']}")])

        nav_row = []
        if page > 0: nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"admin_list_sent_annc_{page - 1}"))
        nav_row.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="ignore"))
        if page < total_pages - 1: nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"admin_list_sent_annc_{page + 1}"))
        keyboard.append(nav_row)
        keyboard.append([InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")])

        await query.edit_message_text(f"📢 **LIVE BROADCAST HISTORY ({total_p} Sent)**\nTap any broadcast to Live Edit in users' chats or Live Delete (Unsend):", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("admin_view_sent_annc_"):
        await query.answer()
        annc_id = int(data.replace("admin_view_sent_annc_", ""))
        a = get_announcement_by_id(annc_id)
        deliveries = get_broadcast_deliveries(annc_id)

        sent_time_str = a['scheduled_time'].strftime("%d %b %Y, %I:%M %p IST") if a else "N/A"
        msg = (
            f"📡 **SENT BROADCAST LOG (ID #{annc_id})**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ **Sent At:** `{sent_time_str}`\n"
            f"👥 **Delivered Chats:** `{len(deliveries)} Students`\n"
            f"📝 **Media:** `{a.get('media_type', 'TEXT').upper()}`\n\n"
            f"📄 **Message Content:**\n"
            f"{a.get('message_text', 'N/A')}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ *Fast concurrent actions will instantly affect messages inside users' personal Telegram chats:*"
        )

        keyboard = [
            [InlineKeyboardButton("✍️ Live Edit in Users' Chats", callback_data=f"admin_edit_sent_broadcast_prompt_{annc_id}")],
            [InlineKeyboardButton("🗑 Live Unsend / Delete for ALL Users", callback_data=f"admin_delete_sent_broadcast_{annc_id}")],
            [InlineKeyboardButton("🔙 Back to Sent History", callback_data="admin_list_sent_annc_0")],
            [InlineKeyboardButton("👑 Return to Admin Portal", callback_data="admin_home")]
        ]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("admin_edit_sent_broadcast_prompt_"):
        await query.answer()
        annc_id = int(data.replace("admin_edit_sent_broadcast_prompt_", ""))
        context.user_data["awaiting_edit_live_broadcast"] = annc_id
        cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"admin_view_sent_annc_{annc_id}")]])
        await query.edit_message_text(f"✍️ **LIVE EDIT BROADCAST #{annc_id}**\n\nPlease reply with the updated text to edit across all recipients' chats in realtime:", reply_markup=cancel_btn, parse_mode="Markdown")

    elif data.startswith("admin_delete_sent_broadcast_"):
        await query.answer()
        annc_id = int(data.replace("admin_delete_sent_broadcast_", ""))
        deliveries = get_broadcast_deliveries(annc_id)
        
        await query.edit_message_text(f"⏳ **Live deleting message from {len(deliveries)} users' chats concurrently (~3s)...**")
        
        deleted_count = await fast_concurrent_delete(context.bot, deliveries)

        delete_scheduled_announcement(annc_id)
        nav = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Sent History", callback_data="admin_list_sent_annc_0")],
            [InlineKeyboardButton("👑 Admin Portal", callback_data="admin_home")]
        ])
        await query.edit_message_text(f"🗑 **LIVE UNSEND COMPLETE!**\nSuccessfully deleted message from `{deleted_count}` out of `{len(deliveries)}` chats and removed from log.", reply_markup=nav, parse_mode="Markdown")

    elif data == "admin_live_users_menu":
        await query.answer()
        online_1m = get_currently_online_users(1)
        online_5m = get_currently_online_users(5)
        online_15m = get_currently_online_users(15)

        keyboard = [
            [InlineKeyboardButton(f"🟢 Active in Last 1 Min ({len(online_1m)})", callback_data="admin_show_online_1")],
            [InlineKeyboardButton(f"🟡 Active in Last 5 Mins ({len(online_5m)})", callback_data="admin_show_online_5")],
            [InlineKeyboardButton(f"🟠 Active in Last 15 Mins ({len(online_15m)})", callback_data="admin_show_online_15")],
            [InlineKeyboardButton("🔙 Back to Admin Portal", callback_data="admin_home")]
        ]

        msg = (
            f"⚡ **LIVE ONLINE STUDENT TELEMETRY** ⚡\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **1-Minute Realtime Active:** `{len(online_1m)} Students` 🟢\n"
            f"• **5-Minutes Active Window:** `{len(online_5m)} Students` 🟡\n"
            f"• **15-Minutes Active Window:** `{len(online_15m)} Students` 🟠\n\n"
            f"Select a time window below to view student details:"
        )
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("admin_show_online_"):
        await query.answer()
        mins = int(data.replace("admin_show_online_", ""))
        online_list = get_currently_online_users(mins)

        lines = [
            f"⚡ **ACTIVE STUDENTS IN LAST {mins} MINUTE(S) ({len(online_list)})** ⚡\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]

        if online_list:
            for idx, u in enumerate(online_list, start=1):
                sid = u.get("student_id") or f"USER_{u['user_id']}"
                lines.append(f"{idx}. **{u['full_name']}** (`{sid}`) — Last Active: `{u.get('last_active', 'Just now')}`")
        else:
            lines.append("ℹ️ *No active students in this time window.*")

        back_btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Online Menu", callback_data="admin_live_users_menu")],
            [InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]
        ])
        await query.edit_message_text("\n".join(lines), reply_markup=back_btn, parse_mode="Markdown")

    elif data == "admin_total_platform_usage":
        await query.answer()
        usage = get_platform_usage_summary()
        msg = (
            f"⏱ **FULL PLATFORM USAGE & PRACTICE TIME TELEMETRY** ⏱\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **Total Hours Spent:** `{usage['hours']} Hours` ⏳\n"
            f"• **Total Minutes Spent:** `{usage['minutes']} Mins` ⏱\n"
            f"• **Total Raw Seconds:** `{usage['seconds']} Sec` 📊\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 Real-time aggregate duration spent across all student practice sessions."
        )
        back_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Admin Portal", callback_data="admin_home")]])
        await query.edit_message_text(msg, reply_markup=back_btn, parse_mode="Markdown")

    elif data == "admin_pdf_logs":
        await query.answer()
        logs = get_pdf_generation_analytics()
        lines = [
            f"📄 **PDF GENERATION LOGS & ANALYTICS** 📄\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]
        if logs:
            for idx, l in enumerate(logs, start=1):
                name = l.get("full_name") or f"User {l['user_id']}"
                sid = l.get("student_id") or f"USER_{l['user_id']}"
                ptype = str(l.get("pdf_type", "")).replace("_", " ").title()
                lines.append(
                    f"**{idx}. {name}** (`{sid}`)\n"
                    f"   👉 Report Type: `{ptype}`\n"
                    f"   📅 Time: `{l['generated_at']}`\n"
                )
        else:
            lines.append("ℹ️ *No PDF generation events logged yet.*")

        msg = "\n".join(lines)
        if len(msg) > 4000:
            msg = msg[:3950] + "\n\n*(Truncated due to length)*"

        back_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Admin Portal", callback_data="admin_home")]])
        await query.edit_message_text(msg, reply_markup=back_btn, parse_mode="Markdown")

    elif data == "admin_change_pass_prompt":
        await query.answer()
        context.user_data["awaiting_admin_new_pass"] = True
        cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("👑 Return to Admin Portal", callback_data="admin_home")]])
        await query.edit_message_text(
            "🔑 **CHANGE ADMIN PASSWORD**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Please reply with your new Master Admin Password:",
            reply_markup=cancel_btn,
            parse_mode="Markdown"
        )

    elif data == "admin_lock_session":
        await query.answer()
        ADMIN_AUTH_SESSIONS.pop(user_id, None)
        await query.edit_message_text("🔒 **ADMIN SESSION LOCKED.**\nType /admin and enter password to log in again.", parse_mode="Markdown")

    elif data == "admin_financial_stats":
        await query.answer()
        rev = calculate_financial_revenue()
        msg = (
            f"💰 **FINANCIAL REVENUE & EARNINGS DASHBOARD** 💰\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 **Today's Revenue:** `₹{rev['today']} INR`\n"
            f"🗓 **This Week's Revenue:** `₹{rev['week']} INR`\n"
            f"📊 **This Month's Revenue:** `₹{rev['month']} INR`\n"
            f"📈 **All-Time Gross Revenue:** `₹{rev['all']} INR`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💳 Real-time totals aggregated from completed payment transactions."
        )
        back_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Admin Portal", callback_data="admin_home")]])
        await query.edit_message_text(msg, reply_markup=back_btn, parse_mode="Markdown")

    elif data == "admin_mass_grant_menu":
        await query.answer()
        keyboard = [
            [InlineKeyboardButton("🎁 Gift +10 Today's Qs to ALL Users", callback_data="admin_exec_mass_10")],
            [InlineKeyboardButton("🎁 Gift +20 Today's Qs to ALL Users", callback_data="admin_exec_mass_20")],
            [InlineKeyboardButton("🎁 Gift +30 Today's Qs to ALL Users", callback_data="admin_exec_mass_30")],
            [InlineKeyboardButton("🎁 Gift +40 Today's Qs to ALL Users", callback_data="admin_exec_mass_40")],
            [InlineKeyboardButton("🔙 Back to Admin Portal", callback_data="admin_home")]
        ]
        msg = (
            "🎁 **SAME-DAY MASS QUOTA BOOST MENU (1 DAY ONLY)**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Select bonus question amount to gift to ALL registered students for **TODAY ONLY**.\n"
            "*(This limit boost will automatically reset tomorrow)*:"
        )
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("admin_exec_mass_"):
        await query.answer()
        amount = int(data.replace("admin_exec_mass_", ""))
        today_date = get_ist_date_str()
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS temporary_bonus_quota INTEGER DEFAULT 0;")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS gift_granted_date TEXT;")
        
        cursor.execute(
            "UPDATE users SET temporary_bonus_quota = %s, gift_granted_date = %s WHERE is_banned = 0 AND is_verified = 1",
            (amount, today_date)
        )
        conn.commit()
        cursor.close()
        release_db(conn)

        broadcast_msg = (
            f"🎁 **SPECIAL SAME-DAY GIFT FROM HIMANSHU SIR!** 🎁\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎉 **Himanshu Sir has gifted ALL students +{amount} Extra Questions for TODAY ONLY!**\n\n"
            f"⚡ Your question limit for today ({today_date}) has been increased by +{amount} Qs!\n"
            f"⏳ *Note: This bonus limit is valid for 1 day only and expires at midnight.*\n\n"
            f"🚀 Tap **/quiz** to start practicing right now!"
        )
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Launch Quiz Now", callback_data="cmd_quiz")]])

        target_uids = [u['user_id'] for u in users if not u.get('is_banned')]
        sent = await fast_concurrent_broadcast(context.bot, target_uids, broadcast_msg, reply_markup=btn)

        back_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Admin Portal", callback_data="admin_home")]])
        await query.edit_message_text(f"✅ **GIFTED +{amount} SAME-DAY QS TO ALL USERS!**\nBroadcasted to {sent} active students.", reply_markup=back_btn, parse_mode="Markdown")

    elif data.startswith("admin_view_student_threads_"):
        await query.answer()
        raw_param = data.replace("admin_view_student_threads_", "")
        show_resolved = "resolved" in raw_param
        
        clean_page_str = raw_param.replace("_resolved", "").strip()
        page = int(clean_page_str) if clean_page_str.isdigit() else 0

        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        if show_resolved:
            cursor.execute(
                """
                SELECT user_id, student_name, MAX(created_at) as last_query_time, 
                       SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END) as pending_count,
                       COUNT(*) as total_queries
                FROM student_queries 
                GROUP BY user_id, student_name 
                HAVING SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END) = 0
                ORDER BY last_query_time DESC
                """
            )
        else:
            cursor.execute(
                """
                SELECT user_id, student_name, MIN(created_at) as oldest_pending_time, 
                       SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END) as pending_count,
                       COUNT(*) as total_queries
                FROM student_queries 
                GROUP BY user_id, student_name 
                HAVING SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END) > 0
                ORDER BY oldest_pending_time ASC
                """
            )

        students_list = cursor.fetchall()
        cursor.close()
        release_db(conn)

        if not students_list:
            keyboard = [
                [InlineKeyboardButton("📂 View Resolved / Old Queries Archive", callback_data="admin_view_student_threads_0_resolved")] if not show_resolved else [InlineKeyboardButton("📩 View Pending Unread Threads", callback_data="admin_view_student_threads_0")],
                [InlineKeyboardButton("👑 Return to Admin Portal", callback_data="admin_home")]
            ]
            empty_lbl = "No unread pending student queries!" if not show_resolved else "No resolved query history found!"
            await query.edit_message_text(f"📩 **STUDENT SUPPORT THREADS**\n\n🎉 {empty_lbl}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            return

        total_s = len(students_list)
        total_pages = math.ceil(total_s / USERS_PER_PAGE)
        page = max(0, min(page, total_pages - 1))
        page_items = students_list[page * USERS_PER_PAGE:(page + 1) * USERS_PER_PAGE]

        keyboard = []
        for s in page_items:
            pend_badge = f"🔴 ({s['pending_count']} unread)" if s['pending_count'] > 0 else "🟢 (Resolved)"
            btn_txt = f"💬 {s['student_name']} — {pend_badge}"
            keyboard.append([InlineKeyboardButton(btn_txt, callback_data=f"admin_student_thread_{s['user_id']}")])

        nav_row = []
        suffix = "_resolved" if show_resolved else ""
        if page > 0: nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"admin_view_student_threads_{page - 1}{suffix}"))
        nav_row.append(InlineKeyboardButton(f"📄 Page {page + 1}/{total_pages}", callback_data="ignore"))
        if page < total_pages - 1: nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"admin_view_student_threads_{page + 1}{suffix}"))
        keyboard.append(nav_row)

        if not show_resolved:
            keyboard.append([InlineKeyboardButton("📂 View Resolved / Old Queries Archive", callback_data="admin_view_student_threads_0_resolved")])
        else:
            keyboard.append([InlineKeyboardButton("📩 View Pending Unread Threads", callback_data="admin_view_student_threads_0")])

        keyboard.append([InlineKeyboardButton("👑 Return to Admin Portal", callback_data="admin_home")])

        title_lbl = "UNREAD PENDING THREADS (Oldest First)" if not show_resolved else "RESOLVED / OLD QUERIES ARCHIVE"
        await query.edit_message_text(f"📩 **STUDENT SUPPORT THREADS — {title_lbl} ({total_s} Students)**\nSelect a student to inspect their questions:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("admin_student_thread_"):
        await query.answer()
        target_uid = int(data.replace("admin_student_thread_", ""))
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM student_queries WHERE user_id = %s ORDER BY id ASC", (target_uid,))
        queries = cursor.fetchall()
        cursor.close()
        release_db(conn)

        if not queries:
            await query.edit_message_text("ℹ️ No queries found for this student.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Support Threads", callback_data="admin_view_student_threads_0")], [InlineKeyboardButton("👑 Return to Admin Portal", callback_data="admin_home")]]))
            return

        st_name = queries[0]["student_name"]
        lines = [
            f"💬 **SUPPORT THREAD: {st_name}** (`{target_uid}`)\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]

        keyboard = []
        for q in queries:
            reply_status = f"`{q['admin_reply']}`" if q["admin_reply"] else "*(Not Replied Yet)*"
            photo_badge = " 📷 [Image Attached]" if q.get("photo_file_id") else ""
            status_flag = "🔴 UNREAD" if q["status"] == "PENDING" else "🟢 RESOLVED"

            lines.append(
                f"🏷 **Query ID #{q['id']}** `[{q['created_at']}]` — {status_flag}{photo_badge}\n"
                f"❓ *\"{q['query_text'] or 'Image Query'}\"*\n"
                f"👨‍🏫 **Admin Reply:** {reply_status}\n"
                f"──────────────────────────────"
            )

            if q["status"] == "PENDING":
                keyboard.append([
                    InlineKeyboardButton(f"✍️ Reply #{q['id']}", callback_data=f"admin_reply_prompt_{q['id']}"),
                    InlineKeyboardButton(f"🙈 Ignore #{q['id']}", callback_data=f"admin_ignore_query_{q['id']}_{target_uid}"),
                    InlineKeyboardButton(f"🗑 Delete #{q['id']}", callback_data=f"admin_delete_query_{q['id']}_{target_uid}")
                ])

        msg = "\n".join(lines)
        if len(msg) > 3900:
            msg = msg[:3850] + "\n\n*(Truncated)*"

        keyboard.append([InlineKeyboardButton("✉️ Direct Message Student (Text/Photo)", callback_data=f"admin_direct_msg_{target_uid}")])
        keyboard.append([InlineKeyboardButton("🔙 Back to Support Threads", callback_data="admin_view_student_threads_0")])
        keyboard.append([InlineKeyboardButton("👑 Return to Admin Portal", callback_data="admin_home")])

        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("admin_ignore_query_"):
        parts = data.replace("admin_ignore_query_", "").split("_")
        qid = int(parts[0])
        target_uid = int(parts[1])

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE student_queries SET status = 'RESOLVED', admin_reply = '(Marked as Ignored/Read by Admin)' WHERE id = %s", (qid,))
        conn.commit()
        cursor.close()
        release_db(conn)

        await query.answer("🙈 Query marked as Read/Ignored!", show_alert=True)
        query.data = f"admin_student_thread_{target_uid}"
        await admin_callback_handler(update, context)

    elif data.startswith("admin_delete_query_"):
        parts = data.replace("admin_delete_query_", "").split("_")
        qid = int(parts[0])
        target_uid = int(parts[1])

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM student_queries WHERE id = %s", (qid,))
        conn.commit()
        cursor.close()
        release_db(conn)

        await query.answer("🗑 Query permanently deleted!", show_alert=True)
        query.data = f"admin_student_thread_{target_uid}"
        await admin_callback_handler(update, context)

    elif data.startswith("admin_reply_prompt_"):
        await query.answer()
        qid = int(data.replace("admin_reply_prompt_", ""))
        context.user_data["awaiting_admin_reply_qid"] = qid
        cancel_btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel Reply & Return to Dashboard", callback_data="admin_home")]
        ])
        await query.edit_message_text(f"✍️ **SECRET REPLY TO QUERY #{qid}**\n\nPlease reply with text or **upload an Image/Photo** to send to this student:", reply_markup=cancel_btn, parse_mode="Markdown")

    elif data.startswith("admin_direct_msg_"):
        await query.answer()
        target_uid = int(data.replace("admin_direct_msg_", ""))
        context.user_data["awaiting_admin_direct_msg_uid"] = target_uid
        cancel_btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel Message & Return to Dashboard", callback_data="admin_home")]
        ])
        msg = (
            f"✉️ **DIRECT MESSAGE TO STUDENT (`{target_uid}`)**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Please reply with text or **upload an Image/Photo** to send directly to this student.\n\n"
            f"🔒 *Will be delivered into the student's personal chat.*"
        )
        await query.edit_message_text(msg, reply_markup=cancel_btn, parse_mode="Markdown")

    elif data.startswith("admin_pause_"):
        await query.answer()
        mins = int(data.replace("admin_pause_", ""))
        set_maintenance_until(int(time.time()) + (mins * 60))
        
        hours_label = f"{mins // 60} Hour(s)" if mins >= 60 else f"{mins} Minutes"
        back_btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ Resume Bot Services Now", callback_data="admin_resume_now")],
            [InlineKeyboardButton("👑 Return to Admin Portal", callback_data="admin_home")]
        ])
        
        await query.edit_message_text(
            f"🛑 **Bot Services PAUSED for {hours_label}.**\n"
            f"Broadcasting pause notification to all registered users...", 
            reply_markup=back_btn,
            parse_mode="Markdown"
        )
        
        target_uids = [u['user_id'] for u in users]
        pause_txt = f"📢 **ADMIN NOTICE:** Bot services have been temporarily paused for {hours_label}."
        await fast_concurrent_broadcast(context.bot, target_uids, pause_txt)

    elif data == "admin_resume_now":
        await query.answer()
        set_maintenance_until(0)
        back_btn = InlineKeyboardMarkup([[InlineKeyboardButton("👑 Return to Admin Portal", callback_data="admin_home")]])
        await query.edit_message_text("🟢 **Bot Service RESUMED Immediately.**\nBroadcasting service status to all users...", reply_markup=back_btn, parse_mode="Markdown")
        
        target_uids = [u['user_id'] for u in users]
        resume_txt = "📢 **ADMIN HAS RESUMED SERVICES! YOU CAN ATTEMPT QUIZZES NOW!**"
        await fast_concurrent_broadcast(context.bot, target_uids, resume_txt)

    elif data == "admin_command_stats":
        await query.answer()
        conn = None
        try:
            conn = get_db()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(
                "SELECT command_name, COUNT(*) as count FROM command_analytics GROUP BY command_name ORDER BY count DESC LIMIT 10"
            )
            rows = cursor.fetchall()
            cursor.close()
            release_db(conn)
        except Exception:
            if conn:
                release_db(conn)
            rows = []

        lines = [
            f"📊 **COMMAND USAGE ANALYTICS** 📊\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ]
        if rows:
            for idx, r in enumerate(rows, start=1):
                lines.append(f"{idx}. `{r['command_name']}` — `{r['count']} uses`")
        else:
            lines.append("ℹ️ *No command execution metrics logged yet.*")

        back_btn = InlineKeyboardMarkup([[InlineKeyboardButton("👑 Return to Admin Portal", callback_data="admin_home")]])
        await query.edit_message_text("\n".join(lines), reply_markup=back_btn, parse_mode="Markdown")

    elif data.startswith("audit_grant_"):
        await query.answer()
        target_uid = int(data.replace("audit_grant_", ""))
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET bonus_quota = bonus_quota + 20 WHERE user_id = %s", (target_uid,))
        conn.commit()
        cursor.close()
        release_db(conn)
        
        from app.telegram_bot import PROFILE_CACHE
        PROFILE_CACHE.pop(target_uid, None)
        sync_user_json_profile(target_uid)

        profile = get_user_profile(target_uid) or {}
        tot_quota = (profile.get("paid_question_balance", 0) or 20) + profile.get("bonus_quota", 0)
        user_announcement = (
            f"🎁 **SPECIAL ANNOUNCEMENT: BONUS QUOTA INCREASED!** 🎁\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎉 **Himanshu Sir has granted you +20 Extra Daily Questions!**\n\n"
            f"⚡ **Your New Daily Limit:** `{tot_quota} Questions / Day`\n"
            f"✨ You can now attempt more practice questions every single day!\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🚀 Tap **/quiz** to launch your daily session now!"
        )
        try:
            btn = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Launch Quiz Now", callback_data="cmd_quiz")]])
            await context.bot.send_message(chat_id=target_uid, text=user_announcement, reply_markup=btn, parse_mode="Markdown", disable_notification=False)
        except Exception as e:
            logger.error(f"Failed broadcasting bonus quota notice: {e}")

        await query.edit_message_text(
            f"🎉 **Bonus Quota Granted & Broadcasted!** 🎉\n\nAdded +20 daily question quota to user `{target_uid}` and pushed notification to their chat.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back to Dashboard", callback_data=f"admin_inspect_u_{target_uid}")],
                [InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]
            ])
        )

    elif data.startswith("admin_issue_warning_prompt_"):
        await query.answer()
        target_uid = int(data.replace("admin_issue_warning_prompt_", ""))
        context.user_data["awaiting_admin_warning_msg_uid"] = target_uid
        cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Warning & Return", callback_data=f"admin_inspect_u_{target_uid}")]])
        msg = (
            f"⚠️ **ISSUE WARNING TO STUDENT (`{target_uid}`)** ⚠️\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Please reply with the exact warning reason/message to send to this student.\n\n"
            f"🔔 *This official warning notice will be delivered instantly to the user's chat.*"
        )
        await query.edit_message_text(msg, reply_markup=cancel_btn, parse_mode="Markdown")

    elif data == "admin_export_zip":
        await query.answer()
        await query.edit_message_text("⏳ **Generating Bulk Zip Package...**\nZipping all student JSON ledgers...", parse_mode="Markdown")
        
        for u in users:
            sync_user_json_profile(u['user_id'])

        zip_filename = "All_Student_Profiles_Export.zip"
        zip_path = os.path.join(USER_PROFILES_DIR, zip_filename)

        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, _, files in os.walk(USER_PROFILES_DIR):
                    for file in files:
                        if file.endswith('.json'):
                            file_path = os.path.join(root, file)
                            zipf.write(file_path, arcname=file)

            if os.path.exists(zip_path):
                with open(zip_path, "rb") as doc:
                    await context.bot.send_document(
                        chat_id=query.message.chat_id,
                        document=doc,
                        filename=zip_filename,
                        caption=f"📦 **MASTER STUDENT PROFILES BACKUP**\n\nTotal Files Included: `{len(users)} JSON profiles`",
                        parse_mode="Markdown"
                    )
                os.remove(zip_path)
            await admin_portal_command(update, context)
        except Exception as e:
            await query.message.reply_text(f"⚠️ Error creating zip archive: {e}")

    elif data == "admin_search_prompt":
        await query.answer()
        context.user_data["awaiting_admin_search"] = True
        cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Search & Return", callback_data="admin_home")]])
        await query.edit_message_text("🔍 **STUDENT SEARCH ENGINE**\n\nPlease reply with the student's **Student ID**, **Phone Number**, or **Full Name**:", reply_markup=cancel_btn, parse_mode="Markdown")

    elif data.startswith("genpdf_"):
        await query.answer()
        raw = data.replace("genpdf_", "")
        parts = raw.split("_")
        target_uid = int(parts[0])
        filter_mode = "_".join(parts[1:])

        await query.edit_message_text("⏳ **Generating Custom PDF Report Card...**\nBuilding stats, formatting tables, and rendering PDF...", parse_mode="Markdown")
        
        pdf_file = generate_student_pdf_report(target_uid, filter_mode)
        u = get_user_profile(target_uid)
        sid = u.get("student_id") or f"USER_{target_uid}" if u else f"USER_{target_uid}"
        student_name = u.get("full_name", "Student") if u else "Student"

        if pdf_file == "NO_ATTEMPTS":
            nav_buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("📄 Export Another PDF Report", callback_data=f"audit_pdfmenu_{target_uid}")],
                [InlineKeyboardButton("🔙 Back to Student Dashboard", callback_data=f"admin_inspect_u_{target_uid}")],
                [InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]
            ])
            await query.edit_message_text(
                f"ℹ️ **NO QUIZ ATTEMPTS FOUND!**\n\n"
                f"Student **{student_name}** (`{sid}`) has not attempted any quizzes in the selected timeframe.",
                reply_markup=nav_buttons,
                parse_mode="Markdown"
            )
        elif pdf_file and pdf_file.startswith("ERROR_DETAILS:"):
            nav_buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back to Student Dashboard", callback_data=f"admin_inspect_u_{target_uid}")],
                [InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]
            ])
            err_text = str(pdf_file[:3500])
            await query.edit_message_text(
                f"⚠️ **PDF Generation Error:**\n\n`{err_text}`",
                reply_markup=nav_buttons,
                parse_mode="Markdown"
            )
        elif pdf_file and os.path.exists(pdf_file):
            caption_text = (
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
                    caption=caption_text,
                    parse_mode="Markdown"
                )
            
            nav_buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("📄 Export Another PDF Report", callback_data=f"audit_pdfmenu_{target_uid}")],
                [InlineKeyboardButton("🔙 Back to Student Dashboard", callback_data=f"admin_inspect_u_{target_uid}")],
                [InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]
            ])
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="👇 **Quick Actions & Navigation:**",
                reply_markup=nav_buttons,
                parse_mode="Markdown"
            )
        else:
            nav_buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back to Student Dashboard", callback_data=f"admin_inspect_u_{target_uid}")],
                [InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]
            ])
            await query.edit_message_text(
                "⚠️ **Failed to generate PDF file.**",
                reply_markup=nav_buttons,
                parse_mode="Markdown"
            )

    elif data.startswith("admin_users_page_"):
        await query.answer()
        page = int(data.replace("admin_users_page_", ""))
        total_users = len(users)

        if total_users == 0:
            await query.edit_message_text("📁 No registered students found in database.", parse_mode="Markdown")
            return

        total_pages = math.ceil(total_users / USERS_PER_PAGE)
        page = max(0, min(page, total_pages - 1))

        start_idx = page * USERS_PER_PAGE
        end_idx = start_idx + USERS_PER_PAGE
        page_users = users[start_idx:end_idx]

        keyboard = []
        for u in page_users:
            sid = u.get("student_id") or f"USER_{u['user_id']}"
            ban_flag = " 🛑" if u.get("is_banned") else ""
            paid_flag = " 💳" if u.get("paid_question_balance", 0) > 0 else ""
            btn_text = f"👤 {u['full_name']}{paid_flag}{ban_flag} (ID: {sid})"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"admin_inspect_u_{u['user_id']}")])

        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"admin_users_page_{page - 1}"))
        nav_row.append(InlineKeyboardButton(f"📄 Page {page + 1}/{total_pages}", callback_data="ignore"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"admin_users_page_{page + 1}"))
        
        keyboard.append(nav_row)
        keyboard.append([InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")])

        msg = (
            f"👥 **STUDENT DIRECTORY LEDGER**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **Total Students:** `{total_users}`\n"
            f"• **Page:** `{page + 1}` of `{total_pages}`\n\n"
            f"Tap any student below to access their full inspection dashboard:"
        )
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("admin_paid_users_page_"):
        await query.answer()
        page = int(data.replace("admin_paid_users_page_", ""))
        paid_users = get_strict_paid_users()
        total_paid = len(paid_users)

        if total_paid == 0:
            keyboard = [[InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]]
            await query.edit_message_text("💳 **PAID VIP STUDENTS**\n\nNo paid users found in the database yet.", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            return

        total_pages = math.ceil(total_paid / USERS_PER_PAGE)
        page = max(0, min(page, total_pages - 1))

        start_idx = page * USERS_PER_PAGE
        end_idx = start_idx + USERS_PER_PAGE
        page_users = paid_users[start_idx:end_idx]

        keyboard = []
        for u in page_users:
            sid = u.get("student_id") or f"USER_{u['user_id']}"
            ban_flag = " 🛑" if u.get("is_banned") else ""
            btn_text = f"💳 {u['full_name']}{ban_flag} ({u.get('paid_question_balance', 0)} Qs/D)"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"admin_inspect_u_{u['user_id']}")])

        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"admin_paid_users_page_{page - 1}"))
        nav_row.append(InlineKeyboardButton(f"📄 Page {page + 1}/{total_pages}", callback_data="ignore"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"admin_paid_users_page_{page + 1}"))
        
        keyboard.append(nav_row)
        keyboard.append([InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")])

        msg = (
            f"💳 **PAID VIP STUDENTS DIRECTORY**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **Total Active VIP Subscribers:** `{total_paid}`\n"
            f"• **Page:** `{page + 1}` of `{total_pages}`\n\n"
            f"Tap any paid student below to inspect profile and subscription ledger:"
        )
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("admin_demo_users_page_"):
        await query.answer()
        page = int(data.replace("admin_demo_users_page_", ""))
        demo_users = get_strict_demo_users()
        total_demo = len(demo_users)

        if total_demo == 0:
            keyboard = [[InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]]
            await query.edit_message_text("🎁 **FREE DEMO USERS**\n\nNo free demo users found in the database.", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            return

        total_pages = math.ceil(total_demo / USERS_PER_PAGE)
        page = max(0, min(page, total_pages - 1))

        start_idx = page * USERS_PER_PAGE
        end_idx = start_idx + USERS_PER_PAGE
        page_users = demo_users[start_idx:end_idx]

        keyboard = []
        for u in page_users:
            sid = u.get("student_id") or f"USER_{u['user_id']}"
            ban_flag = " 🛑" if u.get("is_banned") else ""
            btn_text = f"🎁 {u['full_name']}{ban_flag} (ID: {sid})"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"admin_inspect_u_{u['user_id']}")])

        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"admin_demo_users_page_{page - 1}"))
        nav_row.append(InlineKeyboardButton(f"📄 Page {page + 1}/{total_pages}", callback_data="ignore"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"admin_demo_users_page_{page + 1}"))
        
        keyboard.append(nav_row)
        keyboard.append([InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")])

        msg = (
            f"🎁 **FREE DEMO USERS DIRECTORY**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **Total Free Demo Users:** `{total_demo}`\n"
            f"• **Page:** `{page + 1}` of `{total_pages}`\n\n"
            f"Tap any demo student below to inspect profile and subscription ledger:"
        )
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "admin_home":
        await query.answer()
        await admin_portal_command(update, context)

    elif data.startswith("admin_inspect_u_"):
        await query.answer()
        target_uid = int(data.replace("admin_inspect_u_", ""))
        u = get_user_profile(target_uid)

        if not u:
            await query.edit_message_text("⚠️ Student profile not found.", parse_mode="Markdown")
            return

        sid = u.get("student_id") or f"USER_{u.get('user_id')}"
        is_banned = u.get("is_banned", 0)
        ban_text = "🟢 ACTIVE" if not is_banned else "🔴 BANNED"
        ban_btn_label = "🔴 Ban Student" if not is_banned else "🟢 Unban Student"

        paid_bal = u.get("paid_question_balance", 0)
        is_paid = paid_bal > 20 and u.get("payment_id") and u.get("payment_id") not in ('DEMO_PASS', 'OFFICIAL_SUBSCRIBED')
        paid_text = f"💳 PAID VIP ({paid_bal} Qs/Day)" if is_paid else "🆓 FREE DEMO / TIER"

        keyboard = [
            [InlineKeyboardButton("📩 Direct Message Student", callback_data=f"admin_direct_msg_{target_uid}"), InlineKeyboardButton("⚠️ Issue Warning", callback_data=f"admin_issue_warning_prompt_{target_uid}")],
            [InlineKeyboardButton("💳 View Recent Payments", callback_data=f"admin_view_payments_{target_uid}"), InlineKeyboardButton("👑 Grant Paid Plan", callback_data=f"admin_grant_menu_{target_uid}")],
            [InlineKeyboardButton("📋 Personal Details", callback_data=f"audit_personal_{target_uid}"), InlineKeyboardButton("🔑 PIN & Security Questions", callback_data=f"audit_pinsec_{target_uid}")],
            [InlineKeyboardButton("⏱ Time & Activity Log", callback_data=f"audit_activity_{target_uid}"), InlineKeyboardButton("📊 Overall Performance", callback_data=f"audit_perf_{target_uid}")],
            [InlineKeyboardButton("📅 Date-wise Quiz Summary", callback_data=f"audit_datesummary_{target_uid}"), InlineKeyboardButton("🎯 Attempted Questions", callback_data=f"audit_attempted_{target_uid}")],
            [InlineKeyboardButton("❌ Wrong Questions Log", callback_data=f"audit_wrong_{target_uid}"), InlineKeyboardButton("💾 Saved Questions", callback_data=f"audit_saved_{target_uid}")],
            [InlineKeyboardButton("✏️ Edit Name", callback_data=f"admin_editname_prompt_{target_uid}"), InlineKeyboardButton("🗑 Delete Profile", callback_data=f"admin_deluser_confirm_{target_uid}")],
            [InlineKeyboardButton(ban_btn_label, callback_data=f"admin_toggle_ban_{target_uid}"), InlineKeyboardButton("🎁 Grant +20 Quota", callback_data=f"audit_grant_{target_uid}")],
            [InlineKeyboardButton("💬 Student Feedback", callback_data=f"audit_feedback_{target_uid}"), InlineKeyboardButton("📄 Export PDF Options", callback_data=f"audit_pdfmenu_{target_uid}")],
            [InlineKeyboardButton("📥 Export Raw JSON File", callback_data=f"audit_exportjson_{target_uid}"), InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]
        ]

        msg = (
            f"🪪 **STUDENT AUDIT CONTROL PANEL** 🪪\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **Student Name:** {u.get('full_name')}\n"
            f"• **Student ID:** `{sid}`\n"
            f"• **Telegram ID:** `{u.get('user_id')}`\n"
            f"• **Target Exam:** `{u.get('target_exam')}`\n"
            f"• **Payment Status:** `{paid_text}`\n"
            f"• **VIP Expiry:** `{u.get('vip_pass_expiry') or 'N/A'}`\n"
            f"• **Account Status:** `{ban_text}`\n"
            f"• **File Ledger:** `data/user_profiles/{sid}.json`\n\n"
            f"Select an audit module or management option below:"
        )
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("admin_toggle_ban_"):
        await query.answer()
        target_uid = int(data.replace("admin_toggle_ban_", ""))
        new_ban = toggle_user_ban_status(target_uid)
        status_msg = "🔴 Student Banned successfully!" if new_ban else "🟢 Student Unbanned successfully!"
        await query.message.reply_text(status_msg)
        
        query.data = f"admin_inspect_u_{target_uid}"
        await admin_callback_handler(update, context)

    elif data.startswith("admin_editname_prompt_"):
        await query.answer()
        target_uid = int(data.replace("admin_editname_prompt_", ""))
        context.user_data["awaiting_admin_editname"] = target_uid
        cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Name Edit", callback_data=f"admin_inspect_u_{target_uid}")]])
        await query.edit_message_text(f"✏️ **EDIT STUDENT NAME**\n\nPlease reply with the new Full Name for user ID `{target_uid}`:", reply_markup=cancel_btn, parse_mode="Markdown")

    elif data.startswith("admin_deluser_confirm_"):
        await query.answer()
        target_uid = int(data.replace("admin_deluser_confirm_", ""))
        u = get_user_profile(target_uid)
        
        confirm_btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚠️ Yes, Delete Permanently", callback_data=f"admin_deluser_do_{target_uid}")],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"admin_inspect_u_{target_uid}")]
        ])
        await query.edit_message_text(
            f"⚠️ **CONFIRM PERMANENT DELETION** ⚠️\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Are you sure you want to permanently delete **{u.get('full_name')}** (`{target_uid}`)?\n\n"
            f"This will erase their database entry, quiz attempts, bookmarks, and JSON ledger forever!",
            reply_markup=confirm_btn,
            parse_mode="Markdown"
        )

    elif data.startswith("admin_deluser_do_"):
        await query.answer()
        target_uid = int(data.replace("admin_deluser_do_", ""))
        admin_delete_user_account(target_uid)
        await query.edit_message_text(f"🗑 **STUDENT ACCOUNT DELETED PERMANENTLY.**\nUser ID `{target_uid}` has been removed.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👑 Return to Admin Portal", callback_data="admin_home")]]), parse_mode="Markdown")

    elif data.startswith("audit_pinsec_"):
        await query.answer()
        target_uid = int(data.replace("audit_pinsec_", ""))
        u = get_user_profile(target_uid)

        msg = (
            f"🔑 **USER PIN & SECURITY QUESTIONS AUDIT** 🔑\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 **Student:** {u.get('full_name')} (`{u.get('student_id')}`)\n\n"
            f"• **Secret 4-Digit PIN:** `{u.get('pin', 'Not Set')}`\n"
            f"• **Security Question:** *\"{u.get('security_question', 'Not Set')}\"*\n"
            f"• **Security Answer:** `{u.get('security_answer', 'Not Set')}`\n\n"
            f"⚠️ *Confidential: Visible strictly to Primary Admin.*"
        )
        keyboard = [
            [InlineKeyboardButton("🔙 Back to Dashboard", callback_data=f"admin_inspect_u_{target_uid}")],
            [InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]
        ]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("audit_pdfmenu_"):
        await query.answer()
        target_uid = int(data.replace("audit_pdfmenu_", ""))
        
        pdf_buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 1. Last 1 Month Full Data Report", callback_data=f"genpdf_{target_uid}_last_1_month_data")],
            [InlineKeyboardButton("📊 2. Last 1 Month Quiz Summary Report (No Qs)", callback_data=f"genpdf_{target_uid}_last_1_month_quiz")],
            [InlineKeyboardButton("📜 3. All Months Full Data Report", callback_data=f"genpdf_{target_uid}_all_months_data")],
            [InlineKeyboardButton("📈 4. All Months Quiz Summary Report (No Qs)", callback_data=f"genpdf_{target_uid}_all_months_quiz")],
            [InlineKeyboardButton("🔙 Back to Dashboard", callback_data=f"admin_inspect_u_{target_uid}")],
            [InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]
        ])

        await query.edit_message_text(
            f"📄 **PDF REPORT CARD GENERATOR** 📄\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Select the exact report type to generate:",
            reply_markup=pdf_buttons,
            parse_mode="Markdown"
        )

    elif data.startswith("audit_personal_"):
        await query.answer()
        target_uid = int(data.replace("audit_personal_", ""))
        u = get_user_profile(target_uid)
        
        if not u:
            await query.edit_message_text("⚠️ Error retrieving user profile.", parse_mode="Markdown")
            return

        sid = u.get("student_id") or f"USER_{u.get('user_id')}"
        ban_status = "BANNED 🔴" if u.get("is_banned") else "ACTIVE 🟢"
        
        edit_cnt = u.get("edit_count", 0)
        last_edit = u.get("last_profile_edit", "Never")
        remaining_edits = max(0, 3 - edit_cnt)

        paid_bal = u.get("paid_question_balance", 0)
        is_paid = paid_bal > 20 and u.get("payment_id") and u.get("payment_id") not in ('DEMO_PASS', 'OFFICIAL_SUBSCRIBED')
        paid_str = f"💳 YES ({paid_bal} Qs/Day)" if is_paid else "🆓 NO (Free Demo / Tier)"

        msg = (
            f"📋 **STUDENT PERSONAL DETAILS** 📋\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **Full Name:** {u.get('full_name', 'N/A')}\n"
            f"• **Student ID:** `{sid}`\n"
            f"• **Telegram ID:** `{u.get('user_id')}`\n"
            f"• **Account Status:** `{ban_status}`\n"
            f"• **Paid VIP Subscriber:** `{paid_str}`\n"
            f"• **VIP Pass Expiry:** `{u.get('vip_pass_expiry') or 'N/A'}`\n"
            f"• **Username:** @{u.get('username') or 'N/A'}\n"
            f"• **Phone Number:** `{u.get('phone_number') or 'N/A'}`\n"
            f"• **Target Exam:** `{u.get('target_exam', 'N/A')}`\n"
            f"• **Date of Birth:** `{u.get('dob', 'N/A')}`\n"
            f"• **Calculated Age:** `{u.get('age', 'N/A')} yrs`\n"
            f"• **Gender:** `{u.get('gender', 'N/A')}`\n"
            f"• **Location:** `{u.get('state', 'N/A')}, {u.get('country', 'India')}`\n"
            f"• **Profile Edits Made:** `{edit_cnt} / 3 times` *(Last: {last_edit})*\n"
            f"• **Remaining Edits:** `{remaining_edits} left`\n"
            f"• **Bonus Quota:** `{u.get('bonus_quota', 0)} Qs`\n"
            f"• **Registered At:** `{u.get('created_at', 'N/A')}`\n"
            f"• **Last Active:** `{u.get('last_active', 'N/A')}`\n"
            f"• **Referred By ID:** `{u.get('referred_by') or 'None'}`\n"
            f"• **Referral Count:** `{u.get('referral_count', 0)}` friends"
        )
        keyboard = [
            [InlineKeyboardButton("🔙 Back to Dashboard", callback_data=f"admin_inspect_u_{target_uid}")],
            [InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]
        ]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("audit_activity_"):
        await query.answer()
        target_uid = int(data.replace("audit_activity_", ""))
        u = get_user_profile(target_uid)
        
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT date_str, seconds_spent FROM user_activity_time WHERE user_id = %s ORDER BY date_str DESC", (target_uid,))
        rows = cursor.fetchall()
        cursor.close()
        release_db(conn)

        total_sec = sum([r['seconds_spent'] for r in rows]) if rows else 0
        total_hrs = round(total_sec / 3600.0, 2)
        total_mins = round(total_sec / 60.0, 1)
        
        lines = [
            f"⏱ **STUDENT ACTIVITY & TIME LOG** ⏱",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"👤 **Student:** {u.get('full_name')} (`{u.get('student_id')}`)",
            f"• **Last Active:** `{u.get('last_active', 'N/A')}`",
            f"• **Cumulative Practice Time:** `{total_hrs} Hours` ({total_mins} mins / {total_sec} sec)\n",
            f"📅 **Date-Wise Time Spent Breakdown:**"
        ]

        if rows:
            for r in rows:
                mins = round(r['seconds_spent'] / 60, 2)
                hrs = round(r['seconds_spent'] / 3600.0, 2)
                lines.append(f" • `{r['date_str']}`: {hrs} hrs ({mins} mins / {r['seconds_spent']}s)")
        else:
            lines.append(" • *No activity time recorded yet.*")

        msg = "\n".join(lines)
        keyboard = [
            [InlineKeyboardButton("🔙 Back to Dashboard", callback_data=f"admin_inspect_u_{target_uid}")],
            [InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]
        ]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("audit_perf_"):
        await query.answer()
        target_uid = int(data.replace("audit_perf_", ""))
        u = get_user_profile(target_uid)
        perf = get_user_performance_summary(target_uid)
        rank = calculate_user_rank(target_uid)
        percentile = calculate_user_percentile(target_uid)

        total_qs = perf.get('total_qs', 0) or 0
        total_correct = perf.get('total_correct', 0) or 0
        acc = round((total_correct / total_qs) * 100, 2) if total_qs > 0 else 0.0

        msg = (
            f"📊 **STUDENT OVERALL PERFORMANCE** 📊\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 **Student:** {u.get('full_name')} (`{u.get('student_id')}`)\n\n"
            f"• **Tests Completed:** `{perf.get('total_tests', 0)}` 📚\n"
            f"• **Questions Attempted:** `{total_qs}` 🖥\n"
            f"• **Correct Answers:** `{total_correct}` ✅\n"
            f"• **Wrong Answers:** `{perf.get('total_wrong', 0)}` ❌\n"
            f"• **Skipped Questions:** `{perf.get('total_skipped', 0)}` ⏭\n"
            f"• **Accuracy Rating:** `{acc}%`\n"
            f"• **Normalized Score:** `{round(perf.get('avg_score', 0.0) or 0.0, 2)}%`\n"
            f"• **Global Rank:** `{rank}` 🥇\n"
            f"• **Overall Percentile:** `{percentile}%`"
        )
        keyboard = [
            [InlineKeyboardButton("🔙 Back to Dashboard", callback_data=f"admin_inspect_u_{target_uid}")],
            [InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]
        ]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("audit_datesummary_"):
        await query.answer()
        target_uid = int(data.replace("audit_datesummary_", ""))
        u = get_user_profile(target_uid)

        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM quiz_attempts WHERE user_id = %s ORDER BY id DESC", (target_uid,))
        attempts = cursor.fetchall()
        cursor.close()
        release_db(conn)

        lines = [
            f"📅 **DATE-WISE QUIZ SUMMARY** 📅",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"👤 **Student:** {u.get('full_name')} (`{u.get('student_id')}`)\n"
        ]

        if attempts:
            summary = {}
            for a in attempts:
                ad = dict(a)
                dt = ad.get("attempt_date", "Unknown")
                if dt not in summary:
                    summary[dt] = {"tests": 0, "qs": 0, "correct": 0, "score": 0.0}
                summary[dt]["tests"] += 1
                summary[dt]["qs"] += ad.get("questions_attempted", 0) or 0
                summary[dt]["correct"] += ad.get("correct_answers", 0) or 0
                summary[dt]["score"] += ad.get("score", 0.0) or 0.0

            for dt, stats in summary.items():
                lines.append(
                    f"🗓 **Date:** `{dt}`\n"
                    f" • Quizzes: `{stats['tests']}` | Questions: `{stats['qs']}`\n"
                    f" • Correct: `{stats['correct']}` | Score: `{round(stats['score'], 2)}`\n"
                )
        else:
            lines.append("*No quiz attempts found for this student.*")

        msg = "\n".join(lines)
        if len(msg) > 4000:
            msg = msg[:3950] + "\n\n*(Truncated due to length)*"

        keyboard = [
            [InlineKeyboardButton("🔙 Back to Dashboard", callback_data=f"admin_inspect_u_{target_uid}")],
            [InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]
        ]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("audit_attempted_"):
        await query.answer()
        target_uid = int(data.replace("audit_attempted_", ""))
        u = get_user_profile(target_uid)

        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM quiz_attempts WHERE user_id = %s ORDER BY id DESC LIMIT 5", (target_uid,))
        attempts = cursor.fetchall()
        cursor.close()
        release_db(conn)

        lines = [
            f"🎯 **ATTEMPTED QUESTIONS LOG** 🎯",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"👤 **Student:** {u.get('full_name')} (`{u.get('student_id')}`)\n"
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
                    if isinstance(q_item, dict):
                        q_text = q_item.get("question_text") or q_item.get("question") or "N/A"
                        ans_text = q_item.get("correct_answer_text") or "N/A"
                        status_icon = "✅" if q_item.get("status") == "CORRECT" else "❌" if q_item.get("status") == "WRONG" else "⏭"
                        lines.append(f" {idx}. {status_icon} `{q_text}`\n    👉 **Ans:** `{ans_text}`")
                lines.append("")

        if not found_any:
            lines.append("*No question attempt logs recorded yet.*")

        msg = "\n".join(lines)
        if len(msg) > 4000:
            msg = msg[:3950] + "\n\n*(Truncated due to Telegram length limit)*"

        keyboard = [
            [InlineKeyboardButton("🔙 Back to Dashboard", callback_data=f"admin_inspect_u_{target_uid}")],
            [InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]
        ]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("audit_wrong_"):
        await query.answer()
        target_uid = int(data.replace("audit_wrong_", ""))
        u = get_user_profile(target_uid)

        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM quiz_attempts WHERE user_id = %s ORDER BY id DESC LIMIT 5", (target_uid,))
        attempts = cursor.fetchall()
        cursor.close()
        release_db(conn)

        lines = [
            f"❌ **WRONG QUESTIONS LOG** ❌",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"👤 **Student:** {u.get('full_name')} (`{u.get('student_id')}`)\n"
        ]

        found_wrong = False
        for a in attempts:
            ad = dict(a)
            dt = ad.get("attempt_timestamp", "N/A")
            details = json.loads(ad["details_json"]) if ad.get("details_json") else []
            wrong_items = [q for q in details if isinstance(q, dict) and q.get("status") == "WRONG"]
            if wrong_items:
                found_wrong = True
                lines.append(f"📅 **Quiz At:** `{dt}`")
                for idx, q_item in enumerate(wrong_items, start=1):
                    q_text = q_item.get("question_text") or q_item.get("question") or "N/A"
                    ans_text = q_item.get("correct_answer_text") or "N/A"
                    lines.append(f" {idx}. ❌ `{q_text}`\n    👉 **Correct Ans:** `{ans_text}`")
                lines.append("")

        if not found_wrong:
            lines.append("🎉 *Zero wrong questions logged! Excellent performance.*")

        msg = "\n".join(lines)
        if len(msg) > 4000:
            msg = msg[:3950] + "\n\n*(Truncated due to Telegram length limit)*"

        keyboard = [
            [InlineKeyboardButton("🔙 Back to Dashboard", callback_data=f"admin_inspect_u_{target_uid}")],
            [InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]
        ]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("audit_saved_"):
        await query.answer()
        target_uid = int(data.replace("audit_saved_", ""))
        u = get_user_profile(target_uid)

        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM saved_questions WHERE user_id = %s ORDER BY id DESC LIMIT 15", (target_uid,))
        saved = cursor.fetchall()
        cursor.close()
        release_db(conn)

        lines = [
            f"💾 **SAVED QUESTIONS REPORT** 💾",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"👤 **Student:** {u.get('full_name')} (`{u.get('student_id')}`)",
            f"• **Total Bookmarks:** `{len(saved)}`\n"
        ]

        if saved:
            for idx, sq in enumerate(saved, start=1):
                sq_d = dict(sq)
                opts_list = json.loads(sq_d['options_json']) if sq_d.get('options_json') else []
                c_opt_idx = sq_d.get("correct_option", 0)
                ans_text = opts_list[c_opt_idx] if 0 <= c_opt_idx < len(opts_list) else "N/A"
                s_at = sq_d.get("saved_at", "N/A")
                lines.append(f"**{idx}. [{s_at}]** 📌 `{sq_d['question_text']}`\n    👉 **Correct Ans:** `{ans_text}`")
        else:
            lines.append("*No saved questions bookmarked by this student.*")

        msg = "\n".join(lines)
        if len(msg) > 4000:
            msg = msg[:3950] + "\n\n*(Truncated due to Telegram length limit)*"

        keyboard = [
            [InlineKeyboardButton("🔙 Back to Dashboard", callback_data=f"admin_inspect_u_{target_uid}")],
            [InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]
        ]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("audit_feedback_"):
        await query.answer()
        target_uid = int(data.replace("audit_feedback_", ""))
        u = get_user_profile(target_uid)

        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM student_feedback WHERE user_id = %s ORDER BY id DESC", (target_uid,))
        fbs = cursor.fetchall()
        cursor.close()
        release_db(conn)

        lines = [
            f"💬 **STUDENT FEEDBACK & REVIEWS** 💬",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"👤 **Student:** {u.get('full_name')} (`{u.get('student_id')}`)\n"
        ]

        if fbs:
            for idx, f_item in enumerate(fbs, start=1):
                fd = dict(f_item)
                lines.append(f"**{idx}. Submitted At:** `{fd['submitted_at']}`\n 💬 *\"{fd['feedback_text']}\"*\n")
        else:
            lines.append("*No reviews or feedback submitted by this student yet.*")

        msg = "\n".join(lines)
        keyboard = [
            [InlineKeyboardButton("🔙 Back to Dashboard", callback_data=f"admin_inspect_u_{target_uid}")],
            [InlineKeyboardButton("👑 Main Admin Portal", callback_data="admin_home")]
        ]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("audit_exportjson_"):
        await query.answer()
        target_uid = int(data.replace("audit_exportjson_", ""))
        u = get_user_profile(target_uid)
        sid = u.get("student_id") or f"USER_{u.get('user_id')}"
        
        sync_user_json_profile(target_uid)
        filepath = os.path.join(USER_PROFILES_DIR, f"{sid}.json")

        if os.path.exists(filepath):
            with open(filepath, "rb") as doc:
                await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=doc,
                    filename=f"{sid}.json",
                    caption=f"📄 **Master Student Profile File:** `{sid}.json`",
                    parse_mode="Markdown"
                )
        else:
            await query.message.reply_text("⚠️ JSON file not found on disk.")

    elif data == "admin_broadcast":
        await query.answer()
        context.user_data["awaiting_broadcast"] = True
        cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Broadcast & Return", callback_data="admin_home")]])
        await query.edit_message_text("📢 **GLOBAL BROADCAST CENTER**\n\nSend the message text or photo you wish to broadcast to ALL registered users:", reply_markup=cancel_btn, parse_mode="Markdown")