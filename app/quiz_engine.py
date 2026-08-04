import asyncio
import logging
import time
import os
from telegram import Update, Poll, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from app.config import DAILY_QUESTION_LIMIT, CHANNEL_USERNAME, YOUTUBE_CHANNEL_URL, PRIMARY_ADMIN_ID
from app.database import (
    get_today_attempts, get_seen_question_ids, 
    mark_questions_as_seen, record_quiz_result, get_ist_timestamp_str, 
    get_user_profile, get_maintenance_until,
    save_paused_quiz_state, get_paused_quiz_state, clear_paused_quiz_state,
    save_question_to_db, log_user_activity_time
)
from app.pyq_fetcher import fetch_pyqs_for_quiz
from app.stats import calculate_user_percentile, calculate_user_rank

logger = logging.getLogger(__name__)

ACTIVE_SESSIONS = {}
POLL_MAP = {}
TIMER_TASKS = {}
QUIZ_SETUP_CACHE = {}

def get_pause_resume_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏸ 𝒫𝒶𝓊𝓈𝑒 (/pause)", callback_data="cmd_pause_quiz"), 
            InlineKeyboardButton("▶️ 𝑅𝑒𝓈𝓊𝓂𝑒 (/resume)", callback_data="cmd_resume_quiz"),
            InlineKeyboardButton("🛑 𝒮𝓉𝑜𝓅 (/stop)", callback_data="cmd_stop_quiz")
        ],
        [
            InlineKeyboardButton("💾 𝒮𝒶𝓋𝑒 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃", callback_data="cmd_save_question")
        ]
    ])

async def check_quiz_maintenance(update: Update) -> bool:
    m_until = get_maintenance_until()
    if int(time.time()) < m_until:
        remaining_sec = m_until - int(time.time())
        mins_left = max(1, (remaining_sec + 59) // 60)
        msg = f"🛠 **𝒜𝒟𝑀𝐼𝒩 𝐻𝒜𝒮 𝒫𝒜𝒰𝒮𝐸𝒟 𝒯𝐻𝐸 𝒮𝐸𝑅𝒱𝐼𝒞𝐸 𝒞𝒰𝑅𝑅𝐸𝒩𝒯𝐿𝒴**\n𝒮𝑒𝓇𝓋𝒾𝒸𝑒 𝓌𝒾𝓁𝓁 𝓇𝑒𝓈𝓊𝓂𝑒 𝒾𝓃 𝒶𝓅𝓅𝓇𝑜𝓍𝒾𝓂𝒶𝓉𝑒𝓁𝓎 `{mins_left} 𝓂𝒾𝓃𝓈`. 𝒫𝓁𝑒𝒶𝓈𝑒 𝓉𝓇𝓎 𝒶𝑔𝒶𝒾𝓃 𝓁𝒶𝓉𝑒𝓇!"
        
        if update.callback_query:
            await update.callback_query.answer(f"🛠 Service Paused! Resuming in ~{mins_left} mins.", show_alert=True)
        elif update.message:
            await update.message.reply_text(msg, parse_mode="Markdown")
        return False
    return True

async def launch_quiz_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_quiz_maintenance(update): return

    user = update.effective_user
    log_user_activity_time(user.id, seconds=10)
    profile = await asyncio.to_thread(get_user_profile, user.id)
    
    if not profile or not profile.get("is_verified"):
        await update.message.reply_text("⚠️ 𝒫𝓁𝑒𝒶𝓈𝑒 𝓉𝓎𝓅𝑒 /start 𝓉𝑜 𝒸𝓇𝑒𝒶𝓉𝑒 𝓎𝑜𝓊𝓇 𝓅𝓇𝑜𝒻𝒾𝓁𝑒 𝒷𝑒𝒻𝑜𝓇𝑒 𝒶𝓉𝓉𝑒𝓂𝓅𝓉𝒾𝓃𝑔 𝓆𝓊𝒾𝓏𝓏𝑒𝓈!")
        return

    attempted_today = await asyncio.to_thread(get_today_attempts, user.id)
    allowed_limit = 10000 if user.id == PRIMARY_ADMIN_ID else DAILY_QUESTION_LIMIT + profile.get("bonus_quota", 0)

    if attempted_today >= allowed_limit:
        exhausted_msg = (
            f"🛑 **𝒲𝒜𝑅𝒩𝐼𝒩𝒢: 𝒟𝒜𝐼𝐿𝒴 𝐹𝑅𝐸𝐸 𝐿𝐼𝑀𝐼𝒯 𝐸𝒳𝐻𝒜𝒰𝒮𝒯𝐸𝒟!**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **𝒯𝑜𝒹𝒶𝓎'𝓈 𝒰𝓈𝒶𝑔𝑒:** `{attempted_today}` / `{allowed_limit}` 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈\n"
            f"• **𝒮𝓉𝒶𝓉𝓊𝓈:** 𝒴𝑜𝓊 𝒽𝒶𝓋𝑒 𝒻𝓊𝓁𝓁𝓎 𝑒𝓍𝒽𝒶𝓊𝓈𝓉𝑒𝒹 𝓎𝑜𝓊𝓇 𝒻𝓇𝑒𝑒 𝒹𝒶𝒾𝓁𝓎 𝓁𝒾𝓂𝒾𝓉 𝒻𝑜𝓇 𝓉𝑜𝒹𝒶𝓎 (00:00 𝓉𝑜 23:59).\n\n"
            f"⚠️ **𝒩𝑜𝓉𝒾𝒸𝑒:** 𝒯𝒽𝑒 `/quiz` 𝒸𝑜𝓂𝓂𝒶𝓃𝒹 𝒾𝓈 𝓃𝑜𝓌 **𝒹𝑒𝒶𝒸𝓉𝒾𝓋𝒶𝓉𝑒𝒹** 𝒻𝑜𝓇 𝓎𝑜𝓊𝓇 𝒶𝒸𝒸𝑜𝓊𝓃𝓉 𝓊𝓃𝓉𝒾𝓁 𝓉𝑜𝓂𝑜𝓇𝓇𝑜𝓌 𝑜𝓇 𝓊𝓃𝓉𝒾𝓁 𝓎𝑜𝓊 𝓊𝓃𝓁𝑜𝒸𝓀 𝑒𝓍𝓉𝓇𝒶 𝓆𝓊𝑜𝓉𝒶 𝓋𝒾𝒶 𝓇𝑒𝒻𝑒𝓇𝓇𝒶𝓁𝓈!\n\n"
            f"💡 **𝒰𝓃𝓁𝑜𝒸𝓀 +10 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈:** 𝒮𝒽𝒶𝓇𝑒 𝓎𝑜𝓊𝓇 𝒾𝓃𝓋𝒾𝓉𝑒 𝓁𝒾𝓃𝓀 𝓌𝒾𝓉𝒽 4 𝒻𝓇𝒾𝑒𝓃𝒹𝓈 𝓊𝓈𝒾𝓃𝑔 `/invite`."
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🤝 𝐼𝓃𝓋𝒾𝓉𝑒 𝐹𝓇𝒾𝑒𝓃𝒹𝓈 (+10 𝐿𝒾𝓂𝒾𝓉)", callback_data="cmd_referral")
        ]])
        if update.callback_query:
            await update.callback_query.answer("🛑 Daily Limit Exhausted! /quiz is deactivated.", show_alert=True)
            await update.callback_query.message.reply_text(exhausted_msg, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await update.message.reply_text(exhausted_msg, reply_markup=keyboard, parse_mode="Markdown")
        return

    paused = await asyncio.to_thread(get_paused_quiz_state, user.id)
    if paused:
        remaining_count = len(paused.get('questions', [])) - paused.get('current_index', 0)
        text = (
            f"⏸ **𝒴𝒪𝒰 𝐻𝒜𝒱𝐸 𝒜 𝒫𝒜𝒰𝒮𝐸𝒟 𝒬𝒰𝐼𝒩 𝒮𝐸𝒮𝒮𝐼𝒪𝒩!**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **𝑅𝑒𝓈𝓊𝓂𝑒 𝒫𝑜𝒾𝓃𝓉:** 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃 `{paused.get('current_index', 0) + 1}` / `{paused.get('total', 0)}`\n"
            f"• **𝑅𝑒𝓂𝒶𝒾𝓃𝒾𝓃𝑔 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈:** `{remaining_count}` 𝒬𝓈\n"
            f"• **𝒞𝓊𝓇𝓇𝑒𝓃𝓉 𝒮𝒸𝑜𝓇𝑒:** `{paused.get('score', 0.0)}` / `{paused.get('total', 0)}`\n\n"
            f"𝒯𝒶𝓅 **𝑅𝑒𝓈𝓊𝓂𝑒** 𝒷𝑒𝓁𝑜𝓌 𝓉𝑜 𝒸𝑜𝓃𝓉𝒾𝓃𝓊𝑒 𝓌𝒽𝑒𝓇𝑒 𝓎𝑜𝓊 𝓁𝑒𝒻𝓉 𝑜𝒻𝒻, 𝑜𝓇 𝓈𝓉𝒶𝓇𝓉 𝒻𝓇𝑒𝓈𝒽:"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ 𝑅𝑒𝓈𝓊𝓂𝑒 𝒫𝒶𝓊𝓈𝑒𝒹 𝒬𝓊𝒾𝓏 (/resume)", callback_data="cmd_resume_quiz")],
            [InlineKeyboardButton("🔄 𝒮𝓉𝒶𝓇𝓉 𝒩𝑒𝓌 𝒬𝓊𝒾𝓏", callback_data="cmd_start_fresh_quiz")]
        ])
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
        return

    remaining_quota = allowed_limit - attempted_today
    counts = [10, 15, 20, 25, 30, 40]
    valid_counts = [c for c in counts if c <= remaining_quota]
    if not valid_counts:
        valid_counts = [max(1, remaining_quota)]

    buttons = [InlineKeyboardButton(f"📝 {c} 𝒬𝓈", callback_data=f"qcount_{c}") for c in valid_counts]
    keyboard = [buttons[:3], buttons[3:]]

    msg_text = (
        f"📚 **𝐿𝑒𝒶𝓇𝓃 𝓌𝒾𝓉𝒽 𝐻𝒾𝑀 𝒬𝓊𝒾𝓏 𝒮𝑒𝓉𝓊𝓅 (𝒮𝓉𝑒𝓅 1/2)**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• **𝒟𝒶𝒾𝓁𝓎 𝒬𝓊𝑜𝓉𝒶 𝒰𝓈𝑒𝒹 𝒯𝑜𝒹𝒶𝓎:** `{attempted_today}` / `{allowed_limit}` 𝒬𝓈\n"
        f"• **𝑅𝑒𝓂𝒶𝒾𝓃𝒾𝓃𝑔 𝒬𝓊𝑜𝓉𝒶:** `{remaining_quota}` 𝒬𝓈 𝒶𝓋𝒶𝒾𝓁𝒶𝒷𝓁𝑒\n\n"
        f"𝒮𝑒𝓁𝑒𝒸𝓉 𝓉𝒽𝑒 𝓃𝓊𝓂𝒷𝑒𝓇 𝑜𝒻 𝓆𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈 𝒻𝑜𝓇 𝓉𝒽𝒾𝓈 𝓈𝑒𝓈𝓈𝒾𝑜𝓃:"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.message.reply_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def quiz_count_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_quiz_maintenance(update): return

    query = update.callback_query
    user_id = query.from_user.id
    log_user_activity_time(user_id, seconds=10)
    
    profile = get_user_profile(user_id)
    attempted_today = get_today_attempts(user_id)
    allowed_limit = 10000 if user_id == PRIMARY_ADMIN_ID else DAILY_QUESTION_LIMIT + (profile.get("bonus_quota", 0) if profile else 0)

    if attempted_today >= allowed_limit:
        await query.answer("🛑 Daily Limit Exhausted! Quiz is locked.", show_alert=True)
        await query.edit_message_text(
            f"🛑 **𝒲𝒜𝑅𝒩𝐼𝒩𝒢: 𝒟𝒜𝐼𝐿𝒴 𝐹𝑅𝐸𝐸 𝐿𝐼𝑀𝐼𝒯 𝐸𝒳𝐻𝒜𝒰𝒮𝒯𝐸𝒟!**\n\n"
            f"𝒴𝑜𝓊 𝒽𝒶𝓋𝑒 𝓇𝑒𝒶𝒸𝒽𝑒𝒹 𝓎𝑜𝓊𝓇 𝒶𝒸𝓉𝓊𝒶𝓁 𝒹𝒶𝒾𝓁𝓎 𝓁𝒾𝓂𝒾𝓉 𝑜𝒻 `{allowed_limit}` 𝓆𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈 𝒻𝑜𝓇 𝓉𝑜𝒹𝒶𝓎 (00:00 𝓉𝑜 23:59). 𝒯𝒽𝑒 `/quiz` 𝒸𝑜𝓂𝓂𝒶𝓃𝒹 𝒾𝓈 𝒹𝑒𝒶𝒸𝓉𝒾𝓋𝒶𝓉𝑒𝒹.",
            parse_mode="Markdown"
        )
        return

    await query.answer()
    count = int(query.data.replace("qcount_", ""))
    
    remaining_quota = allowed_limit - attempted_today
    if count > remaining_quota:
        count = max(1, remaining_quota)

    QUIZ_SETUP_CACHE[user_id] = {"count": count}

    timers = [12, 15, 18, 20, 25, 30]
    buttons = [InlineKeyboardButton(f"⏱ {t}𝓈", callback_data=f"qtimer_{t}") for t in timers]
    keyboard = [buttons[:3], buttons[3:]]

    await query.edit_message_text(
        f"⏱ **𝐿𝑒𝒶𝓇𝓃 𝓌𝒾𝓉𝒽 𝐻𝒾𝑀 𝒬𝓊𝒾𝓏 𝒮𝑒𝓉𝓊𝓅 (𝒮𝓉𝑒𝓅 2/2)**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"𝒮𝑒𝓁𝑒𝒸𝓉𝑒𝒹: `{count} 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈` (𝑅𝑒𝓂𝒶𝒾𝓃𝒾𝓃𝑔 𝒬𝓊𝑜𝓉𝒶: `{remaining_quota}`)\n\n"
        f"𝒞𝒽𝑜𝑜𝓈𝑒 𝓉𝒾𝓂𝑒𝓇 𝒹𝓊𝓇𝒶𝓉𝒾𝑜𝓃 𝓅𝑒𝓇 𝓆𝓊𝑒𝓈𝓉𝒾𝑜𝓃:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def quiz_timer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_quiz_maintenance(update): return

    query = update.callback_query
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    log_user_activity_time(user_id, seconds=15)
    
    profile = get_user_profile(user_id)
    attempted_today = get_today_attempts(user_id)
    allowed_limit = 10000 if user_id == PRIMARY_ADMIN_ID else DAILY_QUESTION_LIMIT + (profile.get("bonus_quota", 0) if profile else 0)

    if attempted_today >= allowed_limit:
        await query.answer("🛑 Daily Limit Exhausted! Quiz is locked.", show_alert=True)
        await query.edit_message_text(
            f"🛑 **𝒲𝒜𝑅𝒩𝐼𝒩𝒢: 𝒟𝒜𝐼𝐿𝒴 𝐹𝑅𝐸𝐸 𝐿𝐼𝑀𝐼𝒯 𝐸𝒳𝐻𝒜𝒰𝒮𝒯𝐸𝒟!**\n\n𝒴𝑜𝓊 𝒽𝒶𝓋𝑒 𝓇𝑒𝒶𝒸𝒽𝑒𝒹 𝓎𝑜𝓊𝓇 𝒶𝒸𝓉𝓊𝒶𝓁 𝒹𝒶𝒾𝓁𝓎 𝓁𝒾𝓂𝒾𝓉 𝑜𝒻 `{allowed_limit}` 𝓆𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈 𝒻𝑜𝓇 𝓉𝑜𝒹𝒶𝓎 (00:00 𝓉𝑜 23:59). 𝒴𝑜𝓊𝓇 𝓆𝓊𝒾𝓏 𝒽𝒶𝓈 𝒷𝑒𝑒𝓃 𝒸𝒶𝓃𝒸𝑒𝓁𝓁𝑒𝒹.",
            parse_mode="Markdown"
        )
        return

    await query.answer()
    timer_sec = int(query.data.replace("qtimer_", ""))
    setup = QUIZ_SETUP_CACHE.pop(user_id, {"count": 20})
    count = setup.get("count", 20)

    remaining_quota = allowed_limit - attempted_today
    if count > remaining_quota:
        count = max(1, remaining_quota)

    seen_ids = get_seen_question_ids(user_id)
    questions = fetch_pyqs_for_quiz(needed_count=count, seen_ids=seen_ids)

    if not questions:
        await query.edit_message_text("🎉 𝒴𝑜𝓊 𝒽𝒶𝓋𝑒 𝒸𝑜𝓂𝓅𝓁𝑒𝓉𝑒𝒹 𝒶𝓁𝓁 𝓆𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈 𝒾𝓃 𝓉𝒽𝑒 𝓆𝓊𝑒𝓈𝓉𝒾𝑜𝓃 𝒷𝒶𝓃𝓀!")
        return

    q_ids = [q["id"] for q in questions if q.get("id") is not None]
    mark_questions_as_seen(user_id, q_ids)

    session = {
        "user_id": user_id,
        "chat_id": chat_id,
        "questions": questions,
        "current_index": 0,
        "score": 0.0,
        "correct": 0,
        "wrong": 0,
        "skipped": 0,
        "total": len(questions),
        "timer_sec": timer_sec,
        "is_paused": False,
        "start_time": get_ist_timestamp_str(),
        "detailed_logs": []
    }
    ACTIVE_SESSIONS[user_id] = session

    await query.edit_message_text(
        f"🚀 **𝒬𝓊𝒾𝓏 𝒮𝑒𝓈𝓈𝒾𝑜𝓃 𝒮𝓉𝒶𝓇𝓉𝑒𝒹!**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 **𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈:** `{len(questions)}` | ⏱ **𝒯𝒾𝓂𝑒𝓇:** `{timer_sec}𝓈/𝓆𝓊𝑒𝓈𝓉𝒾𝑜𝓃`\n"
        f"📅 **𝒜𝓉𝓉𝑒𝓂𝓅𝓉 𝒟𝒶𝓉𝑒:** `{session['start_time']}`\n\n"
        f"𝐿𝑜𝒶𝒹𝒾𝓃𝑔 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃 1/{len(questions)}...",
        parse_mode="Markdown"
    )
    await send_next_question(query.message.chat_id, user_id, context)

async def save_question_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    log_user_activity_time(user_id, seconds=5)
    session = ACTIVE_SESSIONS.get(user_id)
    
    if not session or "current_question" not in session:
        await query.answer("⚠️ No active question found to save right now!", show_alert=True)
        return
    
    q = session["current_question"]
    success = save_question_to_db(
        user_id=user_id,
        q_text=q["question"],
        options=q["options"],
        correct_option=q["correct_option"],
        explanation=q.get("explanation", "")
    )
    if success:
        await query.answer("💾 Question saved successfully! Check result card or type /savedquestions to view.", show_alert=True)
    else:
        await query.answer("ℹ️ This question is already saved in your bookmarks!", show_alert=True)

async def pause_quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    log_user_activity_time(user_id, seconds=5)

    session = ACTIVE_SESSIONS.get(user_id)
    if not session or session.get("is_paused"):
        msg = "ℹ️ 𝒴𝑜𝓊 𝒹𝑜 𝓃𝑜𝓉 𝒽𝒶𝓋𝑒 𝒶𝓃 𝒶𝒸𝓉𝒾𝓋𝑒 𝓇𝓊𝓃𝓃𝒾𝓃𝑔 𝓆𝓊𝒾𝓏 𝓉𝑜 𝓅𝒶𝓊𝓈𝑒."
        if update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        else:
            await update.message.reply_text(msg)
        return

    session["is_paused"] = True
    if user_id in TIMER_TASKS and not TIMER_TASKS[user_id].done():
        TIMER_TASKS[user_id].cancel()

    save_state = {
        "user_id": user_id,
        "questions": session["questions"],
        "current_index": session["current_index"],
        "score": session["score"],
        "correct": session["correct"],
        "wrong": session["wrong"],
        "skipped": session["skipped"],
        "total": session["total"],
        "timer_sec": session["timer_sec"],
        "start_time": session["start_time"],
        "detailed_logs": session.get("detailed_logs", [])
    }
    save_paused_quiz_state(user_id, save_state)
    ACTIVE_SESSIONS.pop(user_id, None)

    remaining_qs = session["total"] - session["current_index"]
    msg = (
        f"⏸ **𝒬𝒰𝐼𝒩 𝒫𝒜𝒰𝒮𝐸𝒟 & 𝒮𝒜𝒱𝐸𝒟**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• **𝒫𝒶𝓊𝓈𝑒𝒹 𝒜𝓉 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃:** `{session['current_index'] + 1}` / `{session['total']}`\n"
        f"• **𝑅𝑒𝓂𝒶𝒾𝓃𝒾𝓃𝑔 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈:** `{remaining_qs}` 𝒬𝓈\n"
        f"• **𝒞𝓊𝓇𝓇𝑒𝓃𝓉 𝒮𝒸𝑜𝓇𝑒:** `{session['score']}` / `{session['total']}`\n\n"
        f"𝒯𝓎𝓅𝑒 `/resume` 𝑜𝓇 𝓉𝒶𝓅 𝒷𝑒𝓁𝑜𝓌 𝓌𝒽𝑒𝓃𝑒𝓋𝑒𝓇 𝓎𝑜𝓊 𝓌𝒾𝓈𝒽 𝓉𝑜 𝒸𝑜𝓃𝓉𝒾𝓃𝓊𝑒!"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ 𝑅𝑒𝓈𝓊𝓂𝑒 𝒬𝓊𝒾𝓏 𝒩𝑜𝓌 (/resume)", callback_data="cmd_resume_quiz")]
    ])

    if update.callback_query:
        await update.callback_query.answer("⏸ Quiz Paused!", show_alert=True)
        await context.bot.send_message(chat_id=chat_id, text=msg, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, reply_markup=keyboard, parse_mode="Markdown")

async def resume_quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    log_user_activity_time(user_id, seconds=5)

    paused = get_paused_quiz_state(user_id)
    if not paused:
        msg = "ℹ️ 𝒩𝑜 𝓅𝒶𝓊𝓈𝑒𝒹 𝓆𝓊𝒾𝓏 𝒻𝑜𝓊𝓃𝒹. 𝒯𝓎𝓅𝑒 /quiz 𝓉𝑜 𝓈𝓉𝒶𝓇𝓉 𝒶 𝓃𝑒𝓌 𝓈𝑒𝓈𝓈𝒾𝑜𝓃!"
        if update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        else:
            await update.message.reply_text(msg)
        return

    clear_paused_quiz_state(user_id)

    session = {
        "user_id": user_id,
        "questions": paused["questions"],
        "current_index": paused.get("current_index", 0),
        "score": paused["score"],
        "correct": paused["correct"],
        "wrong": paused["wrong"],
        "skipped": paused["skipped"],
        "total": paused["total"],
        "timer_sec": paused["timer_sec"],
        "is_paused": False,
        "start_time": paused["start_time"],
        "detailed_logs": paused.get("detailed_logs", [])
    }
    ACTIVE_SESSIONS[user_id] = session

    if update.callback_query:
        await update.callback_query.answer()

    countdown_msg = await context.bot.send_message(
        chat_id=chat_id, 
        text=f"▶️ **𝑅𝐸𝒮𝒰𝑀𝐼𝒩𝒢 𝒬𝒰𝐼𝒩...**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⏳ **3...** 𝒢𝑒𝓉 𝓇𝑒𝒶𝒹𝓎 𝒻𝑜𝓇 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃 `{session['current_index'] + 1}/{session['total']}`!",
        parse_mode="Markdown"
    )
    await asyncio.sleep(1)

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=countdown_msg.message_id,
            text=f"▶️ **𝑅𝐸𝒮𝒰𝑀𝐼𝒩𝒢 𝒬𝒰𝐼𝒩...**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⏳ **2...** 𝒢𝑒𝓉 𝓇𝑒𝒶𝒹𝓎 𝒻𝑜𝓇 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃 `{session['current_index'] + 1}/{session['total']}`!",
            parse_mode="Markdown"
        )
        await asyncio.sleep(1)

        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=countdown_msg.message_id,
            text=f"▶️ **𝑅𝐸𝒮𝒰𝑀𝐼𝒩𝒢 𝒬𝒰𝐼𝒩...**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚡ **1...** 𝐿𝒶𝓊𝓃𝒸𝒽𝒾𝓃𝑔 𝒫𝑜𝓁𝓁 𝓃𝑜𝓌!",
            parse_mode="Markdown"
        )
        await asyncio.sleep(1)
    except Exception:
        pass

    await send_next_question(chat_id, user_id, context)

async def stop_quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    log_user_activity_time(user_id, seconds=5)

    session = ACTIVE_SESSIONS.pop(user_id, None)
    paused = get_paused_quiz_state(user_id)
    
    if not session and not paused:
        msg = "ℹ️ 𝒴𝑜𝓊 𝒹𝑜 𝓃𝑜𝓉 𝒽𝒶𝓋𝑒 𝒶𝓃 𝒶𝒸𝓉𝒾𝓋𝑒 𝑜𝓇 𝓅𝒶𝓊𝓈𝑒𝒹 𝓆𝓊𝒾𝓏 𝓈𝑒𝓈𝓈𝒾𝑜𝓃 𝓉𝑜 𝓈𝓉𝑜𝓅."
        if update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        else:
            await update.message.reply_text(msg)
        return

    clear_paused_quiz_state(user_id)
    if user_id in TIMER_TASKS and not TIMER_TASKS[user_id].done():
        TIMER_TASKS[user_id].cancel()

    if session:
        if session["current_index"] > 0:
            record_quiz_result(
                user_id, 
                score=session["score"], 
                total_questions=session["current_index"], 
                correct_count=session["correct"], 
                wrong_count=session["wrong"], 
                skipped_count=session["skipped"],
                question_details=session.get("detailed_logs", [])
            )
    elif paused:
        if paused.get("current_index", 0) > 0:
            record_quiz_result(
                user_id, 
                score=paused["score"], 
                total_questions=paused["current_index"], 
                correct_count=paused["correct"], 
                wrong_count=paused["wrong"], 
                skipped_count=paused["skipped"],
                question_details=paused.get("detailed_logs", [])
            )

    msg = (
        f"🛑 **𝒬𝒰𝐼𝒩 𝒮𝒯𝒪𝒫𝒫𝐸𝒟 𝒞𝒪𝑀𝒫𝐿𝐸𝒯𝐸𝐿𝒴**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• **𝒴𝑜𝓊𝓇 𝓆𝓊𝒾𝓏 𝓈𝑒𝓈𝓈𝒾𝑜𝓃 𝒽𝒶𝓈 𝒷𝑒𝑒𝓃 𝓉𝑒𝓇𝓂𝒾𝓃𝒶𝓉𝑒𝒹.**\n"
        f"• **𝑅𝑒𝓂𝒶𝒾𝓃𝒾𝓃𝑔 𝓊𝓃𝒶𝓉𝓉𝑒𝓂𝓅𝓉𝑒𝒹 𝓁𝒾𝓂𝒾𝓉 𝒽𝒶𝓈 𝒷𝑒𝑒𝓃 𝓇𝑒𝓈𝓉𝑜𝓇𝑒𝒹 𝓉𝑜 𝓎𝑜𝓊𝓇 𝒹𝒶𝒾𝓁𝓎 𝓆𝓊𝑜𝓉𝒶.**\n\n"
        f"𝒯𝓎𝓅𝑒 `/quiz` 𝓌𝒽𝑒𝓃𝑒𝓋𝑒𝓇 𝓎𝑜𝓊 𝒶𝓇𝑒 𝓇𝑒𝒶𝒹𝓎 𝓉𝑜 𝓈𝓉𝒶𝓇𝓉 𝒶𝑔𝒶𝒾𝓃!"
    )

    if update.callback_query:
        await update.callback_query.answer("🛑 Quiz Stopped Successfully!", show_alert=True)
        await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, parse_mode="Markdown")

async def send_next_question(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    m_until = get_maintenance_until()
    if int(time.time()) < m_until:
        await context.bot.send_message(chat_id=chat_id, text="🛠 **𝒜𝒟𝑀𝐼𝒩 𝐻𝒜𝒮 𝒫𝒜𝒰𝒮𝐸𝒟 𝒯𝐻𝐸 𝒮𝐸𝑅𝒱𝐼𝒞𝐸 𝒞𝒰𝑅𝑅𝐸𝒩𝒯𝐿𝒴**\n𝒬𝓊𝒾𝓏 𝓈𝑒𝓈𝓈𝒾𝑜𝓃 𝓅𝒶𝓊𝓈𝑒𝒹!")
        return

    session = ACTIVE_SESSIONS.get(user_id)
    if not session or session.get("is_paused"):
        return

    if session["current_index"] >= len(session["questions"]):
        await finish_quiz_and_send_report(chat_id, user_id, context)
        return

    q = session["questions"][session["current_index"]]
    session["current_question"] = q
    timer_sec = session["timer_sec"]

    header_text = f"🖥 [𝒬 {session['current_index']+1}/{session['total']}]\n\n{q['question']}"
    if len(header_text) > 300:
        header_text = header_text[:297] + "..."

    clean_opts = [str(opt)[:97] for opt in q["options"]]
    expl_text = q.get("explanation") or "𝐿𝑒𝒶𝓇𝓃 𝓌𝒾𝓉𝒽 𝐻𝒾𝑀 𝒬𝓊𝒾𝓏 𝐵𝑜𝑜𝓀 𝒷𝓎 𝐻𝒾𝓂𝒶𝓃𝓈𝒽𝓊 𝒮𝒾𝓇"
    if len(expl_text) > 200:
        expl_text = expl_text[:197] + "..."

    correct_id = q.get("correct_option", 0)

    try:
        poll_msg = await context.bot.send_poll(
            chat_id=chat_id,
            question=header_text,
            options=clean_opts,
            type=Poll.QUIZ,
            correct_option_id=correct_id,
            explanation=expl_text,
            explanation_parse_mode="Markdown",
            is_anonymous=False,
            open_period=timer_sec
        )
        
        poll_id = poll_msg.poll.id
        POLL_MAP[poll_id] = {
            "user_id": user_id, 
            "chat_id": chat_id, 
            "q_idx": session["current_index"], 
            "correct_id": correct_id,
            "q_data": q
        }

        await context.bot.send_message(
            chat_id=chat_id,
            text="𝒬𝓊𝒾𝓏 𝒞𝑜𝓃𝓉𝓇𝑜𝓁𝓈:",
            reply_markup=get_pause_resume_keyboard()
        )

        if user_id in TIMER_TASKS and not TIMER_TASKS[user_id].done():
            TIMER_TASKS[user_id].cancel()

        TIMER_TASKS[user_id] = asyncio.create_task(auto_skip_task(chat_id, user_id, poll_id, session["current_index"], timer_sec, context))
    except Exception as e:
        logging.error(f"Error sending poll: {e}")
        session["skipped"] += 1
        session["current_index"] += 1
        await send_next_question(chat_id, user_id, context)

async def auto_skip_task(chat_id: int, user_id: int, poll_id: str, expected_idx: int, timer_sec: int, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(timer_sec + 1)
    if poll_id in POLL_MAP:
        data = POLL_MAP.pop(poll_id, None)
        session = ACTIVE_SESSIONS.get(user_id)
        if session and not session.get("is_paused") and session["current_index"] == expected_idx:
            q = data.get("q_data", {})
            opts = q.get("options", [])
            c_idx = data.get("correct_id", 0)
            c_ans_text = opts[c_idx] if 0 <= c_idx < len(opts) else "𝒩/𝒜"

            session.setdefault("detailed_logs", []).append({
                "question_id": q.get("id"),
                "question_text": q.get("question"),
                "status": "SKIPPED_TIMEOUT",
                "selected_option": None,
                "correct_option": c_idx,
                "correct_answer_text": c_ans_text,
                "timestamp": get_ist_timestamp_str()
            })
            session["skipped"] += 1
            session["current_index"] += 1
            await send_next_question(chat_id, user_id, context)

async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    poll_id = answer.poll_id
    if poll_id not in POLL_MAP:
        return

    data = POLL_MAP.pop(poll_id)
    user_id = data["user_id"]
    chat_id = data["chat_id"]
    log_user_activity_time(user_id, seconds=data.get("timer_sec", 15))

    if user_id in TIMER_TASKS and not TIMER_TASKS[user_id].done():
        TIMER_TASKS[user_id].cancel()

    m_until = get_maintenance_until()
    if int(time.time()) < m_until:
        await context.bot.send_message(chat_id=chat_id, text="🛠 **𝒜𝒟𝑀𝐼𝒩 𝐻𝒜𝒮 𝒫𝒜𝒰𝒮𝐸𝒟 𝒯𝐻𝐸 𝒮𝐸𝑅𝒱𝐼𝒞𝐸 𝒞𝒰𝑅𝑅𝐸𝒩𝒯𝐿𝒴**")
        return

    session = ACTIVE_SESSIONS.get(user_id)
    if session and not session.get("is_paused") and session["current_index"] == data["q_idx"]:
        selected = answer.option_ids[0] if answer.option_ids else -1
        correct_id = data["correct_id"]
        q = data.get("q_data", {})
        opts = q.get("options", [])

        c_ans_text = opts[correct_id] if 0 <= correct_id < len(opts) else "𝒩/𝒜"

        is_correct = (selected == correct_id)
        if is_correct:
            session["score"] += 1.0
            session["correct"] += 1
            status = "CORRECT"
        else:
            session["wrong"] += 1
            status = "WRONG"

        session.setdefault("detailed_logs", []).append({
            "question_id": q.get("id"),
            "question_text": q.get("question"),
            "status": status,
            "selected_option": selected,
            "correct_option": correct_id,
            "correct_answer_text": c_ans_text,
            "timestamp": get_ist_timestamp_str()
        })

        session["current_index"] += 1
        await asyncio.sleep(0.8)
        await send_next_question(chat_id, user_id, context)

async def finish_quiz_and_send_report(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    session = ACTIVE_SESSIONS.pop(user_id, None)
    if not session:
        return

    total = session["total"]
    correct = session["correct"]
    wrong = session["wrong"]
    skipped = session["skipped"]
    score = session["score"]
    detailed_logs = session.get("detailed_logs", [])

    record_quiz_result(
        user_id, 
        score=score, 
        total_questions=total, 
        correct_count=correct, 
        wrong_count=wrong, 
        skipped_count=skipped,
        question_details=detailed_logs
    )

    percentile = calculate_user_percentile(user_id)
    rank_str = calculate_user_rank(user_id)

    report_card = (
        f"🏆 **𝒪𝐹𝐹𝐼𝒞𝐼𝒜𝐿 𝒬𝒰𝐼𝒩 𝑅𝐸𝒫𝒪𝑅𝒯 𝒞𝒜𝑅𝒟**\n"
        f"📚 *𝐿𝑒𝒶𝓇𝓃 𝓌𝒾𝓉𝒽 𝐻𝒾𝑀 𝒬𝓊𝒾𝓏 𝐵𝑜𝑜𝓀 𝒷𝓎 𝐻𝒾𝓂𝒶𝓃𝓈𝒽𝓊 𝒮𝒾𝓇*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 **𝒜𝓉𝓉𝑒𝓂𝓅𝓉𝑒𝒹 𝒜𝓉:** `{session['start_time']}`\n\n"
        f"📊 **𝒫𝑒𝓇𝒻𝑜𝓇𝓂𝒶𝓃𝒸𝑒 𝐵𝓇𝑒𝒶𝓀𝒹𝑜𝓌𝓃:**\n"
        f"• **𝒯𝑜𝓉𝒶𝓁 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈:** `{total}`\n"
        f"• **𝒞𝑜𝓇𝓇𝑒𝒸𝓉 𝒜𝓃𝓈𝓌𝑒𝓇𝓈:** `{correct}` ✅\n"
        f"• **𝒲𝓇𝑜𝓃𝑔 𝒜𝓃𝓈𝓌𝑒𝓇𝓈:** `{wrong}` ❌\n"
        f"• **𝒮𝓀𝒾𝓅𝓅𝑒𝒹 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈:** `{skipped}` ⏭\n"
        f"• **𝐹𝒾𝓃𝒶𝓁 𝒮𝒸𝑜𝓇𝑒:** `{score} / {total}`\n\n"
        f"🎖 **𝒪𝓋𝑒𝓇𝒶𝓁𝓁 𝑅𝒶𝓃𝓀 & 𝒫𝑒𝓇𝒸𝑒𝓃𝓉𝒾𝓁𝑒:**\n"
        f"• **𝒢𝓁𝑜𝒷𝒶𝓁 𝑅𝒶𝓃𝓀:** `{rank_str}`\n"
        f"• **𝒫𝑒𝓇𝒸𝑒𝓃𝓉𝒾𝓁𝑒 𝑅𝒶𝓉𝒾𝓃𝑔:** `{percentile}%`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    buttons = [
        [InlineKeyboardButton("📖 𝑅𝑒𝓋𝒾𝑒𝓌 𝒮𝒶𝓋𝑒𝒹 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈", callback_data="cmd_savedquestions")],
        [InlineKeyboardButton("📢 𝒥𝑜𝒾𝓃 𝒯𝑒𝓁𝑒𝑔𝓇𝒶𝓂 𝒞𝒽𝒶𝓃𝓃𝑒𝓁", url="https://t.me/Learnwithhim")],
        [InlineKeyboardButton("📺 𝒥𝑜𝒾𝓃 𝒴𝑜𝓊𝒯𝓊𝒷𝑒 𝒞𝒽𝒶𝓃𝓃𝑒𝓁", url=YOUTUBE_CHANNEL_URL)],
        [InlineKeyboardButton("🚀 𝒜𝓉𝓉𝑒𝓂𝓅𝓉 𝒜𝓃𝑜𝓉𝒽𝑒𝓇 𝒬𝓊𝒾𝓏", callback_data="cmd_quiz")]
    ]

    await context.bot.send_message(chat_id=chat_id, text=report_card, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")