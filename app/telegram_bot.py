import asyncio
import time
import logging
import json
import os
import urllib.request
import base64
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, 
    BotCommand, BotCommandScopeDefault, BotCommandScopeAllPrivateChats, 
    BotCommandScopeAllGroupChats, ReplyKeyboardRemove
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, PollAnswerHandler, 
    MessageHandler, filters, ContextTypes
)
from app.config import (
    BOT_TOKEN, PRIMARY_ADMIN_ID, DAILY_QUESTION_LIMIT, PLAN_TIERS,
    RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RENDER_EXTERNAL_URL
)
from app.database import (
    init_db, get_maintenance_until, get_user_profile, 
    get_today_attempts, save_student_feedback, get_all_student_feedbacks,
    clear_paused_quiz_state, get_saved_questions, log_user_activity_time,
    check_and_update_inactivity, refresh_user_activity_epoch, get_seen_question_ids
)
from app.onboarding import get_onboarding_handler, start_onboarding, edit_profile_command
from app.quiz_engine import (
    launch_quiz_setup, quiz_count_callback, quiz_timer_callback, handle_poll_answer,
    pause_quiz_command, resume_quiz_command, stop_quiz_command, save_question_callback
)
from app.stats import get_overall_leaderboard, calculate_user_percentile, calculate_user_rank, get_user_performance_summary
from app.admin import admin_portal_command, admin_callback_handler
from app.pdf_generator import generate_student_pdf_report
from app.pyq_fetcher import fetch_pyqs_for_quiz

NEGATIVE_WORDS = ["bad", "worst", "useless", "trash", "fake", "hate", "terrible", "waste", "horrible", "fraud", "stupid", "scam"]

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
    profile = await asyncio.to_thread(get_user_profile, user.id)
    if not profile or not profile.get("is_verified"):
        await send_registration_prompt(update)
        return False

    if profile.get("is_banned"):
        ban_msg = "🛑 **ACCOUNT BANNED!**\n\nYour account has been suspended by the administrator."
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
        await asyncio.to_thread(refresh_user_activity_epoch, user_id)
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
            await update.callback_query.answer("🔒 Account Locked due to inactivity!", show_alert=True)
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
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    await asyncio.to_thread(log_user_activity_time, user.id, 10)
    profile = await asyncio.to_thread(get_user_profile, user.id)
    attempted_today = await asyncio.to_thread(get_today_attempts, user.id)

    paid_bal = profile.get("paid_question_balance", 0) or 0
    base_limit = max(DAILY_QUESTION_LIMIT, paid_bal)
    allowed_limit = 10000 if user.id == PRIMARY_ADMIN_ID else base_limit + profile.get("bonus_quota", 0)

    if attempted_today >= allowed_limit:
        limit_msg = (
            f"🛑 **Daily Limit Exhausted!** 🛑\n\n"
            f"You have reached your daily limit of `{allowed_limit}` questions for today.\n"
            f"Unlock higher daily questions via **💳 VIP Payment Plans**!"
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
    await asyncio.to_thread(log_user_activity_time, user.id, 10)
    profile = await asyncio.to_thread(get_user_profile, user.id)
    today_used = await asyncio.to_thread(get_today_attempts, user.id)

    paid_bal = profile.get("paid_question_balance", 0) or 0
    base_limit = max(DAILY_QUESTION_LIMIT, paid_bal)
    allowed_limit = 10000 if user.id == PRIMARY_ADMIN_ID else base_limit + profile.get("bonus_quota", 0)
    remaining = max(0, allowed_limit - today_used)

    active_plan_name = "🎁 FREE DEMO PLAN"
    for p_key, p_val in PLAN_TIERS.items():
        if p_val.get("daily_limit") == paid_bal:
            active_plan_name = p_val.get("name")
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
    )

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Upgrade / VIP Plans", callback_data="cmd_plans")],
        [InlineKeyboardButton("🚀 Launch Quiz", callback_data="cmd_quiz"), InlineKeyboardButton("👤 Profile Card", callback_data="cmd_profile")]
    ])
    await send_response(update, msg, reply_markup=buttons)

async def plans_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    profile = await asyncio.to_thread(get_user_profile, user.id)

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

    msg = "👑 **VIP MEMBERSHIP PACKS** 👑\n\nSelect a pack below to unlock higher daily limits:"
    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(keyboard))

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return
    await send_response(update, "🤖 **COMMAND DIRECTORY**\n\nUse /quiz, /myprofile, /myplan, /pdfreport, /help")

async def myprofile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return
    profile = await asyncio.to_thread(get_user_profile, update.effective_user.id)
    msg = f"👤 **PROFILE CARD**\n\nName: {profile['full_name']}\nID: `{profile['student_id']}`\nExam: `{profile['target_exam']}`"
    await send_response(update, msg)

async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return

    query = update.callback_query
    data = query.data
    user = query.from_user

    if data == "trigger_start":
        await start_onboarding(update, context)
        return

    if not await check_user_registration(update): return

    if data in ("cmd_quiz", "cmd_start_fresh_quiz"):
        profile = await asyncio.to_thread(get_user_profile, user.id)
        attempted_today = await asyncio.to_thread(get_today_attempts, user.id)
        paid_bal = profile.get("paid_question_balance", 0) or 0 if profile else 0
        base_limit = max(DAILY_QUESTION_LIMIT, paid_bal)
        allowed_limit = 10000 if user.id == PRIMARY_ADMIN_ID else base_limit + (profile.get("bonus_quota", 0) if profile else 0)

        if attempted_today >= allowed_limit:
            await query.answer("🛑 Daily Limit Exhausted!", show_alert=True)
            return
        if data == "cmd_start_fresh_quiz":
            await asyncio.to_thread(clear_paused_quiz_state, user.id)
        await launch_quiz_setup(update, context)
    elif data == "cmd_myplan":
        await myplan_command(update, context)
    elif data == "cmd_plans":
        await plans_command(update, context)
    elif data == "cmd_profile":
        await myprofile_command(update, context)

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    if context.user_data.get("is_account_locked"):
        profile = await asyncio.to_thread(get_user_profile, user.id)
        if profile and profile.get("pin") == text:
            context.user_data["is_account_locked"] = False
            await asyncio.to_thread(refresh_user_activity_epoch, user.id)
            await update.message.reply_text("🔓 **ACCOUNT UNLOCKED SUCCESSFULLY!**", reply_markup=ReplyKeyboardRemove())
        else:
            rec_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 Reset Options", callback_data="login_forgot_pin")]])
            await update.message.reply_text("❌ **INCORRECT PIN!**", reply_markup=rec_btn, parse_mode="Markdown")
        return

    if not await maintenance_guard(update, context): return
    await asyncio.to_thread(log_user_activity_time, user.id, 10)

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.error(f"Exception caught in global error handler: {context.error}")

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
        BotCommand("myprofile", "👤 View Student Profile"),
        BotCommand("help", "🤖 Show Command Directory")
    ]
    await application.bot.set_my_commands(allowed_commands, scope=BotCommandScopeDefault())

def build_application() -> Application:
    init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # The onboarding ConversationHandler MUST be registered first
    app.add_handler(get_onboarding_handler())
    
    app.add_handler(CommandHandler("start", start_onboarding))
    app.add_handler(CommandHandler("quiz", strict_quiz_command_guard))
    app.add_handler(CommandHandler("myplan", myplan_command))
    app.add_handler(CommandHandler("plans", plans_command))
    app.add_handler(CommandHandler("myprofile", myprofile_command))
    app.add_handler(CommandHandler("help", help_command))
    
    app.add_handler(CommandHandler("admin", admin_portal_command))
    app.add_handler(CommandHandler("editprofile", edit_profile_command))

    app.add_handler(CallbackQueryHandler(quiz_count_callback, pattern="^qcount_"))
    app.add_handler(CallbackQueryHandler(quiz_timer_callback, pattern="^qtimer_"))
    app.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^(admin_|audit_|genpdf_)"))
    
    # Specific menu routing
    app.add_handler(CallbackQueryHandler(button_router, pattern="^cmd_|^fb_|^trigger_start|^buy_plan_"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
    app.add_handler(PollAnswerHandler(handle_poll_answer))
    app.add_error_handler(global_error_handler)

    return app