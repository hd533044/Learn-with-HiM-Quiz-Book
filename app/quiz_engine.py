import time
import random
import asyncio
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from telegram.ext import ContextTypes
from app.config import DAILY_QUESTION_LIMIT, CHANNEL_USERNAME, YOUTUBE_CHANNEL_URL
from app.database import (
    get_user_profile, get_today_attempts, record_quiz_result, 
    get_questions_by_count, get_maintenance_until
)
from app.stats import calculate_user_percentile, calculate_user_rank

ACTIVE_SESSIONS = {}

async def check_quiz_maintenance(update: Update) -> bool:
    m_until = get_maintenance_until()
    if int(time.time()) < m_until:
        mins_left = max(1, (m_until - int(time.time()) + 59) // 60)
        msg = f"🛠 **ADMIN HAS PAUSED THE SERVICE CURRENTLY**\nService will resume in approximately `{mins_left} mins`. Please try again later!"
        if update.callback_query:
            await update.callback_query.answer(f"🛠 Service Paused! Resuming in ~{mins_left} mins.", show_alert=True)
        elif update.message:
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
        return False
    return True

async def launch_quiz_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_quiz_maintenance(update): return

    user = update.effective_user
    profile = get_user_profile(user.id)

    if not profile or not profile.get("is_verified"):
        msg = "⚠️ Please type /start to create your profile before attempting quizzes!"
        if update.callback_query:
            await update.callback_query.answer("⚠️ Profile required! Type /start first.", show_alert=True)
        else:
            await update.message.reply_text(msg, reply_markup=ReplyKeyboardRemove())
        return

    today_used = get_today_attempts(user.id)
    allowed_limit = DAILY_QUESTION_LIMIT + profile.get("bonus_quota", 0)

    if today_used >= allowed_limit:
        msg = (
            f"🚫 **DAILY QUESTION LIMIT EXHAUSTED!**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **Used Today:** `{today_used}` / `{allowed_limit}` Qs\n\n"
            f"🤝 **Want +10 Extra Questions?**\n"
            f"Use `/invite` to invite 4 friends and instantly expand your daily quota limit!"
        )
        if update.callback_query:
            await update.callback_query.edit_message_text(msg, parse_mode="Markdown")
        else:
            await update.message.reply_text(msg, parse_mode="Markdown")
        return

    buttons = [
        [InlineKeyboardButton("📝 10 Qs", callback_data="qcount_10"), InlineKeyboardButton("📝 15 Qs", callback_data="qcount_15"), InlineKeyboardButton("📝 20 Qs", callback_data="qcount_20")],
        [InlineKeyboardButton("📝 25 Qs", callback_data="qcount_25"), InlineKeyboardButton("📝 30 Qs", callback_data="qcount_30"), InlineKeyboardButton("📝 40 Qs", callback_data="qcount_40")]
    ]
    
    text = (
        "📚 **Learn with HiM Quiz Setup (Step 1/2)**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Select the number of questions for this session:"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

async def quiz_count_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_quiz_maintenance(update): return
    query = update.callback_query
    await query.answer()

    count = int(query.data.replace("qcount_", ""))
    context.user_data["selected_q_count"] = count

    timer_buttons = [
        [InlineKeyboardButton("⏱ 15 Sec / Q", callback_data="qtimer_15"), InlineKeyboardButton("⏱ 30 Sec / Q", callback_data="qtimer_30")],
        [InlineKeyboardButton("⏱ 45 Sec / Q", callback_data="qtimer_45"), InlineKeyboardButton("⏱ 60 Sec / Q", callback_data="qtimer_60")]
    ]

    text = (
        f"📝 Selected Questions: `{count} Qs`\n\n"
        f"⏱ **Timer Selection (Step 2/2)**\n"
        f"Choose time limit per question:"
    )
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(timer_buttons), parse_mode="Markdown")

async def quiz_timer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_quiz_maintenance(update): return
    query = update.callback_query
    await query.answer()

    timer_sec = int(query.data.replace("qtimer_", ""))
    q_count = context.user_data.get("selected_q_count", 10)
    user_id = query.from_user.id

    questions = get_questions_by_count(q_count)
    if not questions:
        await query.edit_message_text("⚠️ Question bank is empty. Please contact admin.")
        return

    ACTIVE_SESSIONS[user_id] = {
        "questions": questions,
        "current_index": 0,
        "total": len(questions),
        "timer_sec": timer_sec,
        "correct": 0,
        "wrong": 0,
        "skipped": 0,
        "score": 0,
        "poll_map": {},
        "start_time": time.strftime("%Y-%m-%d %H:%M:%S")
    }

    await query.edit_message_text(f"🚀 **Quiz Starting!**\nPreparing {len(questions)} questions ({timer_sec}s per question)...", parse_mode="Markdown")
    await send_next_question(query.message.chat_id, user_id, context)

async def send_next_question(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    session = ACTIVE_SESSIONS.get(user_id)
    if not session:
        return

    idx = session["current_index"]
    if idx >= session["total"]:
        await finish_quiz_and_send_report(chat_id, user_id, context)
        return

    q = session["questions"][idx]
    poll_msg = await context.bot.send_poll(
        chat_id=chat_id,
        question=f"Q{idx + 1}/{session['total']}: {q['question']}",
        options=q["options"],
        type="quiz",
        correct_option_id=q["correct_option_id"],
        explanation=q.get("explanation", "Learn with HiM Quiz Book"),
        is_anonymous=False,
        open_period=session["timer_sec"]
    )

    session["poll_map"][poll_msg.poll.id] = {
        "q_index": idx,
        "correct_id": q["correct_option_id"]
    }

async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    poll_answer = update.poll_answer
    user_id = poll_answer.user.id
    poll_id = poll_answer.poll_id

    session = ACTIVE_SESSIONS.get(user_id)
    if not session:
        return

    poll_info = session["poll_map"].get(poll_id)
    if not poll_info:
        return

    selected_option = poll_answer.option_ids[0] if poll_answer.option_ids else None
    if selected_option is None:
        session["skipped"] += 1
    elif selected_option == poll_info["correct_id"]:
        session["correct"] += 1
        session["score"] += 1
    else:
        session["wrong"] += 1

    session["current_index"] += 1
    await asyncio.sleep(1.5)
    await send_next_question(poll_answer.user.id, user_id, context)

async def finish_quiz_and_send_report(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    session = ACTIVE_SESSIONS.pop(user_id, None)
    if not session:
        return

    total = session["total"]
    correct = session["correct"]
    wrong = session["wrong"]
    skipped = session["skipped"]
    score = session["score"]

    record_quiz_result(user_id, score=score, total_questions=total, correct_count=correct, wrong_count=wrong, skipped_count=skipped)

    percentile = calculate_user_percentile(user_id)
    rank_str = calculate_user_rank(user_id)

    report_card = (
        f"🏆 **OFFICIAL QUIZ REPORT CARD**\n"
        f"📚 *Learn with HiM Quiz Book by Himanshu Sir*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 **Attempted At:** `{session['start_time']}`\n\n"
        f"📊 **Performance Breakdown:**\n"
        f"• **Total Questions:** `{total}`\n"
        f"• **Correct Answers:** `{correct}` ✅\n"
        f"• **Wrong Answers:** `{wrong}` ❌\n"
        f"• **Skipped Questions:** `{skipped}` ⏭\n"
        f"• **Final Score:** `{score} / {total}`\n\n"
        f"🎖 **Overall Rank & Percentile:**\n"
        f"• **Global Rank:** `{rank_str}`\n"
        f"• **Percentile Rating:** `{percentile}%` \n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 **What would you like to do next?**\n"
        f"• `/mywholestate` — View detailed academic stats\n"
        f"• `/toppername` — Check overall leaderboards\n"
        f"• `/feedback` — Rate & review this quiz session\n"
        f"• `/quiz` — Start another practice quiz"
    )

    buttons = [
        [
            InlineKeyboardButton("📊 My Stats (/mywholestate)", callback_data="cmd_wholestate"),
            InlineKeyboardButton("🏆 Leaderboard (/toppername)", callback_data="cmd_toppers")
        ],
        [
            InlineKeyboardButton("💬 Write Review (/feedback)", callback_data="cmd_feedback"),
            InlineKeyboardButton("🚀 Attempt Another Quiz", callback_data="cmd_quiz")
        ],
        [
            InlineKeyboardButton("📢 Telegram Channel", url=f"https://t.me/{CHANNEL_USERNAME.replace('@', '')}"),
            InlineKeyboardButton("📺 YouTube Channel", url=YOUTUBE_CHANNEL_URL)
        ]
    ]

    await context.bot.send_message(chat_id=chat_id, text=report_card, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")