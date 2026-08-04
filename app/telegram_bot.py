import time
import logging
import json
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, 
    BotCommand, BotCommandScopeDefault, BotCommandScopeAllPrivateChats, 
    BotCommandScopeAllGroupChats, ReplyKeyboardRemove
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, PollAnswerHandler, 
    MessageHandler, filters, ContextTypes
)
from app.config import BOT_TOKEN, PRIMARY_ADMIN_ID, DAILY_QUESTION_LIMIT
from app.database import (
    init_db, get_maintenance_until, get_user_profile, 
    get_all_users, get_today_attempts, save_student_feedback, get_all_student_feedbacks,
    clear_paused_quiz_state, get_saved_questions, log_user_activity_time,
    check_and_update_inactivity, refresh_user_activity_epoch
)
from app.onboarding import get_onboarding_handler, start_onboarding
from app.quiz_engine import (
    launch_quiz_setup, quiz_count_callback, quiz_timer_callback, handle_poll_answer,
    pause_quiz_command, resume_quiz_command, stop_quiz_command, save_question_callback
)
from app.stats import get_overall_leaderboard, calculate_user_percentile, calculate_user_rank, get_user_performance_summary
from app.admin import admin_portal_command, admin_callback_handler

NEGATIVE_WORDS = ["bad", "worst", "useless", "trash", "fake", "hate", "terrible", "waste", "horrible", "fraud", "stupid", "scam"]

async def send_registration_prompt(update: Update):
    msg = (
        "⚠️ **𝒯𝑜 𝓊𝓈𝑒 𝓉𝒽𝒾𝓈 𝒬𝓊𝒾𝓏 𝐵𝑜𝑜𝓀 𝓎𝑜𝓊 𝓂𝓊𝓈𝓉 𝓇𝑒𝑔𝒾𝓈𝓉𝑒𝓇 𝒻𝒾𝓇𝓈𝓉 𝒽𝑒𝓇𝑒!**\n\n"
        "𝒫𝓁𝑒𝒶𝓈𝑒 𝓉𝒶𝓅 𝓉𝒽𝑒 𝓇𝑒𝑔𝒾𝓈𝓉𝓇𝒶𝓉𝒾𝑜𝓃 𝒷𝓊𝓉𝓉𝑜𝓃 𝒷𝑒𝓁𝑜𝓌 𝓉𝑜 𝓈𝑒𝓉 𝓊𝓅 𝓎𝑜𝓊𝓇 𝓈𝓉𝓊𝒹𝑒𝓃𝓉 𝓅𝓇𝑜𝒻𝒾𝓁𝑒:"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 𝒞𝓁𝒾𝒸𝓀 𝐻𝑒𝓇𝑒 𝒻𝑜𝓇 𝑅𝑒𝑔𝒾𝓈𝓉𝓇𝒶𝓉𝒾𝑜𝓃", callback_data="trigger_start")]
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
    profile = get_user_profile(user.id)
    if not profile or not profile.get("is_verified"):
        await send_registration_prompt(update)
        return False
    return True

async def inactivity_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user:
        return True

    user_id = user.id
    if user_id == PRIMARY_ADMIN_ID:
        refresh_user_activity_epoch(user_id)
        return True

    is_locked, diff_sec = check_and_update_inactivity(user_id)
    if is_locked:
        context.user_data["is_account_locked"] = True
        rec_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 𝑅𝑒𝓈𝑒𝓉 𝒴𝑜𝓊𝓇 𝒫𝐼𝒩 / 𝒫𝒶𝓈𝓈𝓌𝑜𝓇𝒹", callback_data="login_forgot_pin")]])
        msg = (
            f"🔒 **𝒜𝒞𝒞𝒪𝒰𝒩𝒯 𝐿𝒪𝒞𝒦𝐸𝒟 𝒟𝒰𝐸 𝒯𝒪 𝐼𝒩𝒜𝒞𝒯𝐼𝒱𝐼𝒯𝒴**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"𝒴𝑜𝓊 𝓌𝑒𝓇𝑒 𝒾𝓃𝒶𝒸𝓉𝒾𝓋𝑒 𝒻𝑜𝓇 `{diff_sec // 60} 𝓂𝒾𝓃𝓈`.\n\n"
            f"🔑 **𝒫𝓁𝑒𝒶𝓈𝑒 𝓇𝑒𝓅𝓁𝓎 𝓌𝒾𝓉𝒽 𝓎𝑜𝓊𝓇 4-𝒟𝒾𝑔𝒾𝓉 𝒮𝑒𝒸𝓇𝑒𝓉 𝒫𝐼𝒩 𝓉𝑜 𝓊𝓃𝓁𝑜𝒸𝓀 𝓎𝑜𝓊𝓇 𝒶𝒸𝒸𝑜𝓊𝓃𝓉:**"
        )
        if update.callback_query:
            await update.callback_query.answer("🔒 Account Locked due to 5 mins of inactivity!", show_alert=True)
            await update.callback_query.message.reply_text(msg, reply_markup=rec_btn, parse_mode="Markdown")
        elif update.message:
            await update.message.reply_text(msg, reply_markup=rec_btn, parse_mode="Markdown")
        return False
    return True

async def maintenance_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not await inactivity_guard(update, context):
        return False

    m_until = get_maintenance_until()
    if int(time.time()) < m_until:
        remaining_sec = m_until - int(time.time())
        mins_left = max(1, (remaining_sec + 59) // 60)
        msg = f"🛠 **𝒜𝒟𝑀𝐼𝒩 𝐻𝒜𝒮 𝒫𝒜𝒰𝒮𝐸𝒟 𝒯𝐻𝐸 𝒮𝐸𝑅𝒱𝐼𝒞𝐸 𝒞𝒰𝑅𝑅𝐸𝒩𝒯𝐿𝒴**\n𝒮𝑒𝓇𝓋𝒾𝒸𝑒 𝓌𝒾𝓁𝓁 𝓇𝑒𝓈𝓊𝓂𝑒 𝒾𝓃 𝒶𝓅𝓅𝓇𝑜𝓍𝒾𝓂𝒶𝓉𝑒𝓁𝓎 `{mins_left} 𝓂𝒾𝓃𝓈`. 𝒫𝓁𝑒𝒶𝓈𝑒 𝓉𝓇𝓎 𝒶𝑔𝒶𝒾𝓃 𝓁𝒶𝓉𝑒𝓇!"
        
        if update.callback_query:
            await update.callback_query.answer(f"🛠 Service Paused! Resuming in ~{mins_left} mins.", show_alert=True)
        elif update.message:
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
        return False
    return True

async def strict_quiz_command_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): 
        return

    if not await check_user_registration(update):
        return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)
    profile = get_user_profile(user.id)

    attempted_today = get_today_attempts(user.id)
    allowed_limit = 10000 if user.id == PRIMARY_ADMIN_ID else DAILY_QUESTION_LIMIT + profile.get("bonus_quota", 0)

    if attempted_today >= allowed_limit:
        limit_msg = (
            f"🛑 **𝒟𝒶𝒾𝓁𝓎 𝐹𝓇𝑒𝑒 𝐿𝒾𝓂𝒾𝓉 𝐸𝓍𝒽𝒶𝓊𝓈𝓉𝑒𝒹!**\n\n"
            f"𝒴𝑜𝓊 𝒽𝒶𝓋𝑒 𝓇𝑒𝒶𝒸𝒽𝑒𝒹 𝓎𝑜𝓊𝓇 𝒶𝒸𝓉𝓊𝒶𝓁 𝒹𝒶𝒾𝓁𝓎 𝓁𝒾𝓂𝒾𝓉 𝑜𝒻 `{allowed_limit}` 𝓆𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈 𝒻𝑜𝓇 𝓉𝑜𝒹𝒶𝓎 (00:00 𝓉𝑜 23:59).\n"
            f"𝒯𝒽𝑒 `/quiz` 𝒸𝑜𝓂𝓂𝒶𝓃𝒹 𝒽𝒶𝓈 𝒷𝑒𝑒𝓃 **𝒹𝑒𝒶𝒸𝓉𝒾𝓋𝒶𝓉𝑒𝒹** 𝒻𝑜𝓇 𝓎𝑜𝓊𝓇 𝒶𝒸𝒸𝑜𝓊𝓃𝓉 𝓊𝓃𝓉𝒾𝓁 𝓉𝑜𝓂𝑜𝓇𝓇𝑜𝓌.\n\n"
            f"💡 **𝒰𝓃𝓁𝑜𝒸𝓀 +10 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈:** 𝒮𝒽𝒶𝓇𝑒 𝓎𝑜𝓊𝓇 𝒾𝓃𝓋𝒾𝓉𝑒 𝓁𝒾𝓃𝓀 𝓌𝒾𝓉𝒽 4 𝒻𝓇𝒾𝑒𝓃𝒹𝓈 𝓉𝑜 𝒾𝓃𝒸𝓇𝑒𝒶𝓈𝑒 𝓎𝑜𝓊𝓇 𝓁𝒾𝓂𝒾𝓉!"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🤝 𝐼𝓃𝓋𝒾𝓉𝑒 𝐹𝓇𝒾𝑒𝓃𝒹𝓈 (+10 𝐿𝒾𝓂𝒾𝓉)", callback_data="cmd_referral")
        ]])
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

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return
    
    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)

    msg = (
        "🤖 **𝐿𝐸𝒜𝑅𝒩 𝒲𝐼𝒯𝐻 𝐻𝐼𝑀 𝒬𝒰𝐼𝒩 𝐵𝒪𝒪𝒦 — 𝒞𝒪𝑀𝑀𝒜𝒩𝒟 𝒟𝐼𝑅𝐸𝒞𝒯𝒪𝑅𝒴**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "𝒯𝒶𝓅 𝒶𝓃𝓎 𝒷𝓊𝓉𝓉𝑜𝓃 𝒷𝑒𝓁𝑜𝓌 𝑜𝓇 𝑜𝓅𝑒𝓃 𝓉𝒽𝑒 𝒷𝓁𝓊𝑒 **[≡ 𝑀𝑒𝓃𝓊]** 𝒷𝓊𝓉𝓉𝑜𝓃:\n\n"
        "• 🚀 **/quiz**: 𝒮𝓉𝒶𝓇𝓉 𝒶 𝓃𝑒𝓌 𝒸𝓊𝓈𝓉𝑜𝓂 𝒸𝑜𝓂𝓅𝓊𝓉𝑒𝓇 𝓆𝓊𝒾𝓏\n"
        "• ⏸ **/pause**: 𝒫𝒶𝓊𝓈𝑒 𝓇𝓊𝓃𝓃𝒾𝓃𝑔 𝓆𝓊𝒾𝓏\n"
        "• ▶️ **/resume**: 𝑅𝑒𝓈𝓊𝓂𝑒 𝓅𝒶𝓊𝓈𝑒𝒹 𝓆𝓊𝒾𝓏\n"
        "• 🛑 **/stop**: 𝒮𝓉𝑜𝓅 𝓆𝓊𝒾𝓏 𝒸𝑜𝓂𝓅𝓁𝑒𝓉𝑒𝓁𝓎 & 𝓇𝑒𝓈𝓉𝑜𝓇𝑒 𝓇𝑒𝓂𝒶𝒾𝓃𝒾𝓃𝑔 𝓁𝒾𝓂𝒾𝓉\n"
        "• 💾 **/savedquestions**: 𝒱𝒾𝑒𝓌 𝓎𝑜𝓊𝓇 𝒷𝑜𝑜𝓀𝓂𝒶𝓇𝓀𝑒𝒹/𝓈𝒶𝓋𝑒𝒹 𝓆𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈\n"
        "• 👤 **/myprofile**: 𝒱𝒾𝑒𝓌 𝓎𝑜𝓊𝓇 𝓋𝑒𝓇𝒾𝒻𝒾𝑒𝒹 𝓈𝓉𝓊𝒹𝑒𝓃𝓉 𝒸𝒶𝓇𝒹\n"
        "• ✏️ **/editprofile**: 𝒰𝓅𝒹𝒶𝓉𝑒 𝓅𝓇𝑜𝒻𝒾𝓁𝑒 𝒹𝑒𝓉𝒶𝒾𝓁𝓈 (1𝓍 / 30 𝒹𝒶𝓎𝓈)\n"
        "• 📊 **/mywholestate**: 𝒱𝒾𝑒𝓌 𝒹𝑒𝓉𝒶𝒾𝓁𝑒𝒹 𝓇𝒶𝓃𝓀 & 𝓅𝑒𝓇𝒸𝑒𝓃𝓉𝒾𝓁𝑒\n"
        "• 🏆 **/toppername**: 𝐼𝓃𝓈𝓅𝑒𝒸𝓉 𝓉𝒽𝑒 𝑔𝓁𝑜𝒷𝒶𝓁 𝓈𝒸𝒽𝑜𝓁𝒶𝓇 𝓁𝑒𝒶𝒹𝑒𝓇𝒷𝑜𝒶𝓇𝒹\n"
        "• 💬 **/feedback**: 𝑅𝒶𝓉𝑒 𝓉𝒽𝑒 𝒷𝑜𝓉 𝑜𝓇 𝓁𝑒𝒶𝓋𝑒 𝒻𝑒𝑒𝒹𝒷𝒶𝒸𝓀\n"
        "• 📖 **/reviews**: 𝒱𝒾𝑒𝓌 𝓈𝓉𝓊𝒹𝑒𝓃𝓉 𝓇𝑒𝓋𝒾𝑒𝓌𝓈\n"
        "• 🤝 **/invite**: 𝒮𝒽𝒶𝓇𝑒 𝓇𝑒𝒻𝑒𝓇𝓇𝒶𝓁 𝓁𝒾𝓃𝓀 𝓉𝑜 𝓊𝓃𝓁𝑜𝒸𝓀 +10 𝓁𝒾𝓂𝒾𝓉"
    )

    buttons = [
        [InlineKeyboardButton("🚀 /quiz", callback_data="cmd_quiz"), InlineKeyboardButton("🛑 /stop", callback_data="cmd_stop_quiz")],
        [InlineKeyboardButton("💾 /savedquestions", callback_data="cmd_savedquestions"), InlineKeyboardButton("👤 /myprofile", callback_data="cmd_profile")],
        [InlineKeyboardButton("📊 /mywholestate", callback_data="cmd_wholestate"), InlineKeyboardButton("🏆 /toppername", callback_data="cmd_toppers")],
        [InlineKeyboardButton("🤝 /invite", callback_data="cmd_referral"), InlineKeyboardButton("📖 /reviews", callback_data="cmd_viewfeedbacks")]
    ]

    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def saved_questions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)
    saved = get_saved_questions(user.id)
    
    if not saved:
        msg = (
            "📖 **𝒮𝒜𝒱𝐸𝒟 𝒬𝒰𝐸𝒮𝒯𝐼𝒪𝒩𝒮 𝐵𝒪𝒪𝒦𝑀𝒜𝑅𝒦**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "𝒴𝑜𝓊 𝒽𝒶𝓋𝑒𝓃'𝓉 𝓈𝒶𝓋𝑒𝒹 𝒶𝓃𝓎 𝓆𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈 𝓎𝑒𝓉! 𝒯𝒶𝓅 𝓉𝒽𝑒 **💾 𝒮𝒶𝓋𝑒 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃** 𝒷𝓊𝓉𝓉𝑜𝓃 𝒹𝓊𝓇𝒾𝓃𝑔 𝓎𝑜𝓊𝓇 𝓆𝓊𝒾𝓏 𝒶𝓉𝓉𝑒𝓂𝓅𝓉𝓈 𝓉𝑜 𝒷𝑜𝑜𝓀𝓂𝒶𝓇𝓀 𝒾𝓂𝓅𝑜𝓇𝓉𝒶𝓃𝓉 𝓆𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈 𝒽𝑒𝓇𝑒."
        )
        await send_response(update, msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚀 𝐿𝒶𝓊𝓃𝒸𝒽 𝒬𝓊𝒾𝓏", callback_data="cmd_quiz")]]))
        return

    total_count = len(saved)
    lines = [
        f"📖 **𝒮𝒜𝒱𝐸𝒟 𝒬𝒰𝐸𝒮𝒯𝐼𝒪𝒩𝒮 𝐵𝒪𝒪𝒦𝑀𝒜𝑅𝒦**",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 **𝒯𝑜𝓉𝒶𝓁 𝒮𝒶𝓋𝑒𝒹 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈:** `{total_count}`",
        f"📌 *𝒮𝒽𝑜𝓌𝒾𝓃𝑔 𝓂𝑜𝓈𝓉 𝓇𝑒𝒸𝑒𝓃𝓉 𝒷𝑜𝑜𝓀𝓂𝒶𝓇𝓀𝓈 𝒻𝒾𝓇𝓈𝓉*\n"
    ]

    for idx, sq in enumerate(saved[:15], start=1):
        opts_list = json.loads(sq['options_json']) if sq['options_json'] else []
        corr_idx = sq['correct_option']
        corr_ans = opts_list[corr_idx] if 0 <= corr_idx < len(opts_list) else '𝒩/𝒜'
        
        lines.append(
            f"**{idx}. 𝒮𝒶𝓋𝑒𝒹 𝒜𝓉:** `{sq['saved_at']}`\n"
            f"❓ **𝒬:** {sq['question_text']}\n"
            f"✅ **𝒞𝑜𝓇𝓇𝑒𝒸𝓉 𝒜𝓃𝓈𝓌𝑒𝓇:** `{corr_ans}`\n"
            f"💡 **𝐸𝓍𝓅𝓁𝒶𝓃𝒶𝓉𝒾𝑜𝓃:** {sq['explanation']}\n"
            f"──────────────────────────────"
        )

    if total_count > 15:
        lines.append(f"\n*(𝒮𝒽𝑜𝓌𝒾𝓃𝑔 15 𝑜𝒻 {total_count} 𝓈𝒶𝓋𝑒𝒹 𝓆𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈)*")

    msg = "\n".join(lines)
    buttons = [[InlineKeyboardButton("🚀 𝐿𝒶𝓊𝓃𝒸𝒽 𝒬𝓊𝒾𝓏", callback_data="cmd_quiz")]]
    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def myprofile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)
    profile = get_user_profile(user.id)

    today_used = get_today_attempts(user.id)
    allowed_limit = 10000 if user.id == PRIMARY_ADMIN_ID else DAILY_QUESTION_LIMIT + profile.get("bonus_quota", 0)

    remaining = max(0, allowed_limit - today_used)
    student_id = profile.get("student_id", f"USER_{user.id}")

    msg = (
        f"👤 **𝒮𝒯𝒰𝒟𝐸𝒩𝒯 𝒫𝑅𝒪𝐹𝐼𝐿𝐸 𝒞𝒜𝑅𝒟**\n"
        f"📚 *𝐿𝑒𝒶𝓇𝓃 𝓌𝒾𝓉𝒽 𝐻𝒾𝑀 𝒬𝓊𝒾𝓏 𝐵𝑜𝑜𝓀*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• **𝐹𝓊𝓁𝓁 𝒩𝒶𝓂𝑒:** {profile['full_name']}\n"
        f"• **𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝐼𝒟:** `{student_id}`\n"
        f"• **𝒯𝑒𝓁𝑒𝑔𝓇𝒶𝓂 𝐼𝒟:** `{profile['user_id']}`\n"
        f"• **𝒯𝒶𝓇𝑔𝑒𝓉 𝐸𝓍𝒶𝓂:** `{profile['target_exam']}`\n"
        f"• **𝒟𝒪𝐵 / 𝒢𝑒𝓃𝒹𝑒𝓇:** `{profile.get('dob', '𝒩/𝒜')}` / `{profile['gender']}`\n"
        f"• **𝐿𝑜𝒸𝒶𝓉𝒾𝑜𝓃:** `{profile.get('state', '𝒩/𝒜')}, {profile.get('country', '𝐼𝓃𝒹𝒾𝒶')}`\n"
        f"• **𝒫𝒽𝑜𝓃𝑒:** `{profile['phone_number']}` *(𝒫𝓇𝒾𝓋𝒶𝓉𝑒)*\n\n"
        f"📊 **𝒟𝒶𝒾𝓁𝓎 𝒬𝓊𝑜𝓉𝒶 𝒮𝓉𝒶𝓉𝓊𝓈 (00:00 𝓉𝑜 23:59):**\n"
        f"• **𝒰𝓈𝑒𝒹 𝒯𝑜𝒹𝒶𝓎:** `{today_used}` / `{allowed_limit}` 𝒬𝓈\n"
        f"• **𝑅𝑒𝓂𝒶𝒾𝓃𝒾𝓃𝑔 𝒯𝑜𝒹𝒶𝓎:** `{remaining}` 𝒬𝓈\n"
        f"• **𝑅𝑒𝒻𝑒𝓇𝓇𝒶𝓁𝓈:** `{profile.get('referral_count', 0)}` / 4 𝒻𝓇𝒾𝑒𝓃𝒹𝓈\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    buttons = [
        [InlineKeyboardButton("🚀 𝐿𝒶𝓊𝓃𝒸𝒽 𝒬𝓊𝒾𝓏", callback_data="cmd_quiz"), InlineKeyboardButton("💾 𝒮𝒶𝓋𝑒𝒹 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈", callback_data="cmd_savedquestions")],
        [InlineKeyboardButton("✏️ 𝐸𝒹𝒾𝓉 𝒫𝓇𝑜𝒻𝒾𝓁𝑒", callback_data="cmd_editprofile"), InlineKeyboardButton("🤝 𝐼𝓃𝓋𝒾𝓉𝑒 (+10 𝒬𝓊𝑜𝓉𝒶)", callback_data="cmd_referral")]
    ]

    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def wholestate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)
    profile = get_user_profile(user.id)

    perf = get_user_performance_summary(user.id)
    rank = calculate_user_rank(user.id)
    percentile = calculate_user_percentile(user.id)
    student_id = profile.get("student_id", f"USER_{user.id}")

    msg = (
        f"🎓 **𝒮𝒯𝒰𝒟𝐸𝒩𝒯 𝒜𝒞𝒜𝒟𝐸𝑀𝐼𝒞 𝑅𝐸𝒫𝒪𝑅𝒯 𝒞𝒜𝑅𝒟**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 **𝒩𝒶𝓂𝑒:** {profile['full_name']}\n"
        f"🪪 **𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝐼𝒟:** `{student_id}`\n"
        f"🎯 **𝒯𝒶𝓇𝑔𝑒𝓉 𝐸𝓍𝒶𝓂:** `{profile['target_exam']}`\n"
        f"📍 **𝐿𝑜𝒸𝒶𝓉𝒾𝑜𝓃:** `{profile.get('state', '𝒩/𝒜')}, {profile.get('country', '𝐼𝓃𝒹𝒾𝒶')}`\n\n"
        f"📈 **𝒫𝑒𝓇𝒻𝑜𝓇𝓂𝒶𝓃𝒸𝑒 𝑀𝑒𝓉𝓇𝒾𝒸𝓈:**\n"
        f"• **𝒯𝑒𝓈𝓉𝓈 𝒞𝑜𝓂𝓅𝓁𝑒𝓉𝑒𝒹:** `{perf.get('total_tests', 0)}`\n"
        f"• **𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈 𝒜𝓉𝓉𝑒𝓂𝓅𝓉𝑒𝒹:** `{perf.get('total_qs', 0)}`\n"
        f"• **𝒢𝓁𝑜𝒷𝒶𝓁 𝑅𝒶𝓃𝓀:** `{rank}`\n"
        f"• **𝒪𝓋𝑒𝓇𝒶𝓁𝓁 𝒫𝑒𝓇𝒸𝑒𝓃𝓉𝒾𝓁𝑒:** `{percentile}%` *(𝒞𝒶𝓁𝒸𝓊𝓁𝒶𝓉𝑒𝒹 𝒶𝑔𝒶𝒾𝓃𝓈𝓉 𝒶𝓁𝓁 𝓇𝑒𝑔𝒾𝓈𝓉𝑒𝓇𝑒𝒹 𝓈𝓉𝓊𝒹𝑒𝓃𝓉𝓈)*"
    )

    buttons = [[InlineKeyboardButton("🚀 𝐿𝒶𝓊𝓃𝒸𝒽 𝒬𝓊𝒾𝓏", callback_data="cmd_quiz"), InlineKeyboardButton("🏆 𝐿𝑒𝒶𝒹𝑒𝓇𝒷𝑜𝒶𝓇𝒹", callback_data="cmd_toppers")]]
    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def toppers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)
    toppers = get_overall_leaderboard(limit=10)
    
    if not toppers:
        await send_response(update, "🏆 No leaderboard records available yet. Be the first to attempt a quiz!")
        return

    lines = []
    for idx, t in enumerate(toppers, start=1):
        badge = " 🥇" if idx == 1 else " 🥈" if idx == 2 else " 🥉" if idx == 3 else ""
        lines.append(f"{idx}. **{t['full_name']}**{badge} — 𝒜𝓋𝑔 𝒮𝒸𝑜𝓇𝑒: `{round(t['avg_score'], 2)}`")

    msg = "🏆 **𝒢𝐿𝒪𝐵𝒜𝐿 𝒮𝒞𝐻𝒪𝐿𝒜𝑅 𝐿𝐸𝒜𝒟𝐸𝑅𝐵𝒪𝒜𝑅𝒟**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n" + "\n".join(lines)
    buttons = [[InlineKeyboardButton("🚀 𝒜𝓉𝓉𝑒𝓂𝓅𝓉 𝒬𝓊𝒾𝓏", callback_data="cmd_quiz")]]
    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(buttons))

async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)

    keyboard = [
        [InlineKeyboardButton("🌟 10/10 𝐵𝑜𝓉! 𝒯𝒽𝑒 𝓆𝓊𝒾𝓏𝓏𝑒𝓈 𝒶𝓇𝑒 𝓉𝑜𝓅 𝓆𝓊𝒶𝓁𝒾𝓉𝓎 🚀", callback_data="fb_p1")],
        [InlineKeyboardButton("✨ 𝐿𝑒𝒶𝓇𝓃 𝓌𝒾𝓉𝒽 𝐻𝒾𝑀 𝒾𝓈 𝓉𝒽𝑒 𝒷𝑒𝓈𝓉 𝓅𝓇𝑒𝓅𝒶𝓇𝒶𝓉𝒾𝑜𝓃 𝓅𝑜𝓇𝓉𝒶𝓁 🎓", callback_data="fb_p2")],
        [InlineKeyboardButton("🔥 𝒟𝒶𝒾𝓁𝓎 𝓉𝒶𝓇𝑔𝑒𝓉 𝓁𝒾𝓂𝒾𝓉𝓈 𝓀𝑒𝑒𝓅 𝓂𝑒 𝒹𝒾𝓈𝒸𝒾𝓅𝓁𝒾𝓃𝑒𝒹! 📈", callback_data="fb_p3")],
        [InlineKeyboardButton("✍️ 𝒲𝓇𝒾𝓉𝑒 𝒞𝓊𝓈𝓉𝑜𝓂 𝐹𝑒𝑒𝒹𝒷𝒶𝒸𝓀", callback_data="fb_custom")],
        [InlineKeyboardButton("📖 𝒱𝒾𝑒𝓌 𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝑅𝑒𝓋𝒾𝑒𝓌𝓈", callback_data="cmd_viewfeedbacks")]
    ]

    msg = (
        "💬 **𝒮𝒯𝒰𝒟𝐸𝒩𝒯 𝐹𝐸𝐸𝒟𝐵𝒜𝒞𝒦 𝒫𝒪𝑅𝒯𝒜𝐿**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "𝒴𝑜𝓊𝓇 𝒻𝑒𝑒𝒹𝒷𝒶𝒸𝓀 𝒽𝑒𝓁𝓅𝓈 𝐻𝒾𝓂𝒶𝓃𝓈𝒽𝓊 𝒮𝒾𝓇 𝓂𝒶𝓀𝑒 𝓉𝒽𝒾𝓈 𝓅𝓁𝒶𝓉𝒻𝑜𝓇𝓂 𝑒𝓋𝑒𝓃 𝒷𝑒𝓉𝓉𝑒𝓇!\n"
        "𝒮𝑒𝓁𝑒𝒸𝓉 𝒶 𝓆𝓊𝒾𝒸𝓀 𝓅𝓇𝑒𝓈𝑒𝓉 𝓇𝒶𝓉𝒾𝓃𝑔 𝒷𝑒𝓁𝑜𝓌 𝑜𝓇 𝓌𝓇𝒾𝓉𝑒 𝓎𝑜𝓊𝓇 𝑜𝓌𝓃 𝒸𝓊𝓈𝓉𝑜𝓂 𝒻𝑒𝑒𝒹𝒷𝒶𝒸𝓀:"
    )
    await send_response(update, msg, reply_markup=InlineKeyboardMarkup(keyboard))

async def viewfeedbacks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)
    feedbacks = get_all_student_feedbacks(limit=15)

    if not feedbacks:
        await send_response(update, "📖 No student reviews submitted yet. Be the first to leave feedback using /feedback!")
        return

    lines = ["📖 **𝒮𝒯𝒰𝒟𝐸𝒩𝒯 𝑅𝐸𝒱𝐼𝐸𝒲𝒮 & 𝐹𝐸𝐸𝒟𝐵𝒜𝒞𝒦 𝐵𝒪𝒜𝑅𝒟**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"]
    for idx, fb in enumerate(feedbacks, start=1):
        lines.append(f"**{idx}. {fb['full_name']}**:\n 💬 *\"{fb['feedback_text']}\"*\n")

    await send_response(update, "\n".join(lines))

async def referral_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return
    if not await check_user_registration(update): return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)
    bot_username = context.bot.username
    ref_link = f"https://t.me/{bot_username}?start=ref_{user.id}"

    msg = (
        f"🤝 **𝐼𝒩𝒱𝐼𝒯𝐸 𝐹𝑅𝐼𝐸𝒩𝒟𝒮 & 𝒰𝒩𝐿𝒪𝒞𝒦 +10 𝒟𝒜𝐼𝐿𝒴 𝐿𝐼𝑀𝐼𝒯**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"𝒮𝒽𝒶𝓇𝑒 𝓎𝑜𝓊𝓇 𝓅𝑒𝓇𝓈𝑜𝓃𝒶𝓁 𝒾𝓃𝓋𝒾𝓉𝑒 𝓁𝒾𝓃𝓀 𝒷𝑒𝓁𝑜𝓌 𝓌𝒾𝓉𝒽 **4 𝒻𝓇𝒾𝑒𝓃𝒹𝓈**:\n"
        f"`{ref_link}`\n\n"
        f"𝒲𝒽𝑒𝓃 4 𝒻𝓇𝒾𝑒𝓃𝒹𝓈 𝓇𝑒𝑔𝒾𝓈𝓉𝑒𝓇 𝓊𝓈𝒾𝓃𝑔 𝓎𝑜𝓊𝓇 𝓁𝒾𝓃𝓀, 𝓎𝑜𝓊 𝒶𝓊𝓉𝑜𝓂𝒶𝓉𝒾𝒸𝒶𝓁𝓁𝓎 𝓇𝑒𝒸𝑒𝒾𝓋𝑒 +10 𝓆𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈 𝒶𝒹𝒹𝑒𝒹 𝓉𝑜 𝓎𝑜𝓊𝓇 𝒹𝒶𝒾𝓁𝓎 𝓆𝓊𝑜𝓉𝒶!"
    )
    await send_response(update, msg)

async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_guard(update, context): return

    query = update.callback_query
    data = query.data
    user = query.from_user
    log_user_activity_time(user.id, seconds=5)

    if data == "trigger_start":
        await start_onboarding(update, context)
        return

    if not await check_user_registration(update): return

    if data == "cmd_quiz":
        profile = get_user_profile(user.id)
        attempted_today = get_today_attempts(user.id)
        allowed_limit = 10000 if user.id == PRIMARY_ADMIN_ID else DAILY_QUESTION_LIMIT + (profile.get("bonus_quota", 0) if profile else 0)

        if attempted_today >= allowed_limit:
            await query.answer("🛑 Daily Limit Exhausted! /quiz is deactivated.", show_alert=True)
            return
        await launch_quiz_setup(update, context)
    elif data == "cmd_pause_quiz":
        await pause_quiz_command(update, context)
    elif data == "cmd_resume_quiz":
        await resume_quiz_command(update, context)
    elif data == "cmd_stop_quiz":
        await stop_quiz_command(update, context)
    elif data == "cmd_savedquestions":
        await saved_questions_command(update, context)
    elif data == "cmd_save_question":
        await save_question_callback(update, context)
    elif data == "cmd_start_fresh_quiz":
        clear_paused_quiz_state(user.id)
        await launch_quiz_setup(update, context)
    elif data == "cmd_profile":
        await myprofile_command(update, context)
    elif data == "cmd_toppers":
        await toppers_command(update, context)
    elif data == "cmd_wholestate":
        await wholestate_command(update, context)
    elif data == "cmd_referral":
        await referral_command(update, context)
    elif data == "cmd_feedback":
        await feedback_command(update, context)
    elif data == "cmd_viewfeedbacks":
        await viewfeedbacks_command(update, context)
    elif data.startswith("fb_p"):
        presets = {
            "fb_p1": "10/10 𝐵𝑜𝓉! 𝒯𝒽𝑒 𝓆𝓊𝒾𝓏𝓏𝑒𝓈 𝒶𝓇𝑒 𝓉𝑜𝓅 𝓆𝓊𝒶𝓁𝒾𝓉𝓎 🚀",
            "fb_p2": "𝐿𝑒𝒶𝓇𝓃 𝓌𝒾𝓉𝒽 𝐻𝒾𝑀 𝒾𝓈 𝓉𝒽𝑒 𝒷𝑒𝓈𝓉 𝓅𝓇𝑒𝓅𝒶𝓇𝒶𝓉𝒾𝑜𝓃 𝓅𝑜𝓇𝓉𝒶𝓁 🎓",
            "fb_p3": "𝒟𝒶𝒾𝓁𝓎 𝓉𝒶𝓇𝑔𝑒𝓉 𝓁𝒾𝓂𝒾𝓉𝓈 𝓀𝑒𝑒𝓅 𝓂𝑒 𝒹𝒾𝓈𝒸𝒾𝓅𝓁𝒾𝓃𝑒𝒹! 📈"
        }
        fb_text = presets.get(data, "𝒢𝓇𝑒𝒶𝓉 𝑒𝒹𝓊𝒸𝒶𝓉𝒾𝑜𝓃𝒶𝓁 𝒷𝑜𝓉!")
        profile = get_user_profile(user.id)
        name = profile.get("full_name") if profile else user.full_name
        save_student_feedback(user.id, name, fb_text)
        await query.edit_message_text(f"🎉 **𝒯𝒽𝒶𝓃𝓀 𝓎𝑜𝓊, {name}!** 𝒴𝑜𝓊𝓇 𝒻𝑒𝑒𝒹𝒷𝒶𝒸𝓀 𝒽𝒶𝓈 𝒷𝑒𝑒𝓃 𝓇𝑒𝒸𝑜𝓇𝒹𝑒𝒹:\n\n💬 *\"{fb_text}\"*", parse_mode="Markdown")

    elif data == "fb_custom":
        context.user_data["awaiting_custom_feedback"] = True
        await query.edit_message_text("✍️ 𝒫𝓁𝑒𝒶𝓈𝑒 𝓇𝑒𝓅𝓁𝓎 𝓌𝒾𝓉𝒽 𝓎𝑜𝓊𝓇 𝒸𝓊𝓈𝓉𝑜𝓂 𝒻𝑒𝑒𝒹𝒷𝒶𝒸𝓀/𝓇𝑒𝓋𝒾𝑒𝓌 𝒷𝑒𝓁𝑜𝓌:")

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    # Unlock Locked Account via Inactivity PIN
    if context.user_data.get("is_account_locked"):
        profile = get_user_profile(user.id)
        if profile and profile.get("pin") == text:
            context.user_data["is_account_locked"] = False
            refresh_user_activity_epoch(user.id)
            await update.message.reply_text("🔓 **𝒜𝒞𝒞𝒪𝒰𝒩𝒯 𝒰𝒩𝐿𝒪𝒞𝒦𝐸𝒟 𝒮𝒰𝒞𝒞𝐸𝒮𝒮𝐹𝒰𝐿𝐿𝒴!**\n𝒴𝑜𝓊 𝓂𝒶𝓎 𝒸𝑜𝓃𝓉𝒾𝓃𝓊𝑒 𝓁𝑒𝒶𝓇𝓃𝒾𝓃𝑔.", reply_markup=ReplyKeyboardRemove())
        else:
            rec_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 𝑅𝑒𝓈𝑒𝓉 𝒴𝑜𝓊𝓇 𝒫𝐼𝒩 / 𝒫𝒶𝓈𝓈𝓌𝑜𝓇𝒹", callback_data="login_forgot_pin")]])
            await update.message.reply_text(
                "❌ **𝐼𝓃𝒸𝑜𝓇𝓇𝑒𝒸𝓉 𝒫𝐼𝒩!**\n\n𝒫𝓁𝑒𝒶𝓈𝑒 𝓉𝓇𝓎 𝑒𝓃𝓉𝑒𝓇𝒾𝓃𝑔 𝓎𝑜𝓊𝓇 𝒸𝑜𝓇𝓇𝑒𝒸𝓉 4-𝒹𝒾𝑔𝒾𝓉 𝒫𝐼𝒩 𝓉𝑜 𝓊𝓃𝓁𝑜𝒸𝓀, 𝑜𝓇 𝓉𝒶𝓅 𝒷𝑒𝓁𝑜𝓌 𝓉𝑜 𝓇𝑒𝓈𝑒𝓉 𝓎𝑜𝓊𝓇 𝒫𝐼𝒩:",
                reply_markup=rec_btn,
                parse_mode="Markdown"
            )
        return

    if not await maintenance_guard(update, context): return
    log_user_activity_time(user.id, seconds=10)

    # Admin Search Handler
    if user.id == PRIMARY_ADMIN_ID and context.user_data.get("awaiting_admin_search"):
        context.user_data["awaiting_admin_search"] = False
        all_u = get_all_users()
        matches = [
            u for u in all_u if text.lower() in str(u.get("student_id", "")).lower() 
            or text.lower() in str(u.get("phone_number", "")).lower() 
            or text.lower() in str(u.get("full_name", "")).lower()
        ]

        if not matches:
            await update.message.reply_text(f"⚠️ No student found matching query: `{text}`", parse_mode="Markdown")
            return

        keyboard = []
        for m in matches[:10]:
            sid = m.get("student_id") or f"USER_{m['user_id']}"
            keyboard.append([InlineKeyboardButton(f"👤 {m['full_name']} (𝐼𝒟: {sid})", callback_data=f"admin_inspect_u_{m['user_id']}")])
        
        await update.message.reply_text(f"🔍 **𝒮𝑒𝒶𝓇𝒸𝒽 𝑅𝑒𝓈𝓊𝓁𝓉𝓈 𝒻𝑜𝓇 '{text}':**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    if context.user_data.get("awaiting_custom_feedback"):
        context.user_data["awaiting_custom_feedback"] = False
        
        if any(bad_word in text.lower() for bad_word in NEGATIVE_WORDS):
            await update.message.reply_text("🙏 Thank you for your feedback! We are constantly working hard to improve your experience.", reply_markup=ReplyKeyboardRemove())
            return

        profile = get_user_profile(user.id)
        name = profile.get("full_name") if profile else user.full_name
        save_student_feedback(user.id, name, text)
        await update.message.reply_text(f"🎉 **𝐹𝑒𝑒𝒹𝒷𝒶𝒸𝓀 𝑅𝑒𝒸𝑒𝒾𝓋𝑒𝒹!** 𝒯𝒽𝒶𝓃𝓀 𝓎𝑜𝓊 *{name}* 𝒻𝑜𝓇 𝓎𝑜𝓊𝓇 𝓀𝒾𝓃𝒹 𝓌𝑜𝓇𝒹𝓈:\n\n💬 *\"{text}\"*", reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
        return

    if context.user_data.get("awaiting_broadcast"):
        context.user_data["awaiting_broadcast"] = False
        users = get_all_users()
        sent = 0
        for u in users:
            try:
                await context.bot.send_message(chat_id=u['user_id'], text=f"📢 **𝒜𝒩𝒩𝒪𝒰𝒩𝒞𝐸𝑀𝐸𝒩𝒯 𝐹𝑅𝒪𝑀 𝐻𝐼𝑀𝒜𝒩𝒮𝐻𝒰 𝒮𝐼𝑅**\n\n{text}", parse_mode="Markdown")
                sent += 1
            except Exception:
                pass
        await update.message.reply_text(f"✅ Announcement sent to {sent} users!", reply_markup=ReplyKeyboardRemove())

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.debug(f"Exception caught in global error handler: {context.error}")

async def post_init(application: Application):
    try:
        await application.bot.delete_my_commands(scope=BotCommandScopeDefault())
        await application.bot.delete_my_commands(scope=BotCommandScopeAllPrivateChats())
        await application.bot.delete_my_commands(scope=BotCommandScopeAllGroupChats())
    except Exception as e:
        logging.warning(f"Note on command purge: {e}")

    allowed_commands = [
        BotCommand("quiz", "🚀 𝒮𝓉𝒶𝓇𝓉 𝒞𝑜𝓂𝓅𝓊𝓉𝑒𝓇 𝒬𝓊𝒾𝓏"),
        BotCommand("pause", "⏸ 𝒫𝒶𝓊𝓈𝑒 𝑅𝓊𝓃𝓃𝒾𝓃𝑔 𝒬𝓊𝒾𝓏"),
        BotCommand("resume", "▶️ 𝑅𝑒𝓈𝓊𝓂𝑒 𝒫𝒶𝓊𝓈𝑒𝒹 𝒬𝓊𝒾𝓏"),
        BotCommand("stop", "🛑 𝒮𝓉𝑜𝓅 𝒬𝓊𝒾𝓏 𝒞𝑜𝓂𝓅𝓁𝑒𝓉𝑒𝓁𝓎"),
        BotCommand("savedquestions", "💾 𝒱𝒾𝑒𝓌 𝒮𝒶𝓋𝑒𝒹 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈"),
        BotCommand("myprofile", "👤 𝒱𝒾𝑒𝓌 𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝒫𝓇𝑜𝒻𝒾𝓁𝑒"),
        BotCommand("editprofile", "✏️ 𝐸𝒹𝒾𝓉 𝒫𝓇𝑜𝒻𝒾𝓁𝑒 𝒟𝑒𝓉𝒶𝒾𝓁𝓈"),
        BotCommand("mywholestate", "📊 𝒱𝒾𝑒𝓌 𝒫𝑒𝓇𝒻𝑜𝓇𝓂𝒶𝓃𝒸𝑒 & 𝑅𝒶𝓃𝓀"),
        BotCommand("toppername", "🏆 𝒢𝓁𝑜𝒷𝒶𝓁 𝐿𝑒𝒶𝒹𝑒𝓇𝒷𝑜𝒶𝓇𝒹"),
        BotCommand("feedback", "💬 𝒮𝓊𝒷𝓂𝒾𝓉 𝐹𝑒𝑒𝒹𝒷𝒶𝒸𝓀"),
        BotCommand("reviews", "📖 𝒱𝒾𝑒𝓌 𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝑅𝑒𝓋𝒾𝑒𝓌𝓈"),
        BotCommand("invite", "🤝 𝐼𝓃𝓋𝒾𝓉𝑒 𝐹𝓇𝒾𝑒𝓃𝒹𝓈 (+10 𝐿𝒾𝓂𝒾𝓉)")
    ]
    
    await application.bot.set_my_commands(allowed_commands, scope=BotCommandScopeDefault())
    await application.bot.set_my_commands(allowed_commands, scope=BotCommandScopeAllPrivateChats())

def build_application() -> Application:
    init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(get_onboarding_handler())
    
    app.add_handler(CommandHandler("quiz", strict_quiz_command_guard))
    app.add_handler(CommandHandler("pause", pause_quiz_command))
    app.add_handler(CommandHandler("resume", resume_quiz_command))
    app.add_handler(CommandHandler("stop", stop_quiz_command))
    app.add_handler(CommandHandler("savedquestions", saved_questions_command))
    app.add_handler(CommandHandler("myprofile", myprofile_command))
    app.add_handler(CommandHandler("mywholestate", wholestate_command))
    app.add_handler(CommandHandler("toppername", toppers_command))
    app.add_handler(CommandHandler("toppersname", toppers_command))
    app.add_handler(CommandHandler("feedback", feedback_command))
    app.add_handler(CommandHandler("reviews", viewfeedbacks_command))
    app.add_handler(CommandHandler("viewfeedbacks", viewfeedbacks_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("invite", referral_command))
    
    # Master Admin Portal Shortcuts
    app.add_handler(CommandHandler("admin", admin_portal_command))
    app.add_handler(CommandHandler("admit", admin_portal_command))
    app.add_handler(CommandHandler("user_profiles", admin_portal_command))

    app.add_handler(CallbackQueryHandler(quiz_count_callback, pattern="^qcount_"))
    app.add_handler(CallbackQueryHandler(quiz_timer_callback, pattern="^qtimer_"))
    app.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^(admin_|audit_|genpdf_)"))
    app.add_handler(CallbackQueryHandler(button_router, pattern="^cmd_|^fb_|^trigger_start"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
    app.add_handler(PollAnswerHandler(handle_poll_answer))
    app.add_error_handler(global_error_handler)

    return app