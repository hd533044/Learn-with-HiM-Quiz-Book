import asyncio
import logging
import time
from telegram import Update, Poll, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from app.config import DAILY_QUESTION_LIMIT, CHANNEL_USERNAME, YOUTUBE_CHANNEL_URL, PRIMARY_ADMIN_ID
from app.database import (
    get_today_attempts, get_seen_question_ids, 
    mark_questions_as_seen, record_quiz_result, get_ist_timestamp_str, 
    get_user_profile, get_maintenance_until,
    save_paused_quiz_state, get_paused_quiz_state, clear_paused_quiz_state
)
from app.pyq_fetcher import fetch_pyqs_for_quiz
from app.stats import calculate_user_percentile, calculate_user_rank

ACTIVE_SESSIONS = {}
POLL_MAP = {}
TIMER_TASKS = {}
QUIZ_SETUP_CACHE = {}

async def check_quiz_maintenance(update: Update) -> bool:
    m_until = get_maintenance_until()
    if int(time.time()) < m_until:
        remaining_sec = m_until - int(time.time())
        mins_left = max(1, (remaining_sec + 59) // 60)
        msg = f"🛠 **ADMIN HAS PAUSED THE SERVICE CURRENTLY**\nService will resume in approximately `{mins_left} mins`. Please try again later!"
        
        if update.callback_query:
            await update.callback_query.answer(f"🛠 Service Paused! Resuming in ~{mins_left} mins.", show_alert=True)
        elif update.message:
            await update.message.reply_text(msg, parse_mode="Markdown")
        return False
    return True

async def launch_quiz_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_quiz_maintenance(update): return

    user = update.effective_user
    profile = get_user_profile(user.id)
    
    if not profile or not profile.get("is_verified"):
        await update.message.reply_text("⚠️ Please type /start to create your profile before attempting quizzes!")
        return

    paused = get_paused_quiz_state(user.id)
    if paused:
        remaining_count = len(paused.get('questions', [])) - paused.get('current_index', 0)
        text = (
            f"⏸ **YOU HAVE A PAUSED QUIZ SESSION!**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **Resume Point:** Question `{paused.get('current_index', 0) + 1}` / `{paused.get('total', 0)}`\n"
            f"• **Remaining Questions:** `{remaining_count}` Qs\n"
            f"• **Current Score:** `{paused.get('score', 0.0)}` / `{paused.get('total', 0)}`\n\n"
            f"Tap **Resume** below to continue where you left off, or start fresh:"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ Resume Paused Quiz (/resume)", callback_data="cmd_resume_quiz")],
            [InlineKeyboardButton("🔄 Start New Quiz", callback_data="cmd_start_fresh_quiz")]
        ])
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
        return

    attempted_today = get_today_attempts(user.id)
    allowed_limit = DAILY_QUESTION_LIMIT + profile.get("bonus_quota", 0)

    if attempted_today >= allowed_limit and user.id != PRIMARY_ADMIN_ID:
        await update.message.reply_text(
            f"🛑 **Daily Limit Reached!**\n\n"
            f"You have used `{attempted_today}` / `{allowed_limit}` questions today.\n\n"
            f"💡 **Unlock +10 Questions:** Share your invite link with 4 friends to increase your limit!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🤝 Invite Friends (+10 Limit)", callback_data="cmd_referral")
            ]]),
            parse_mode="Markdown"
        )
        return

    counts = [10, 15, 20, 25, 30, 40]
    buttons = [InlineKeyboardButton(f"📝 {c} Qs", callback_data=f"qcount_{c}") for c in counts]
    keyboard = [buttons[:3], buttons[3:]]

    msg_text = (
        "📚 **Learn with HiM Quiz Setup (Step 1/2)**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Select the number of questions for this session:"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.message.reply_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def quiz_count_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_quiz_maintenance(update): return

    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    count = int(query.data.replace("qcount_", ""))
    QUIZ_SETUP_CACHE[user_id] = {"count": count}

    timers = [12, 15, 18, 20, 25, 30]
    buttons = [InlineKeyboardButton(f"⏱ {t}s", callback_data=f"qtimer_{t}") for t in timers]
    keyboard = [buttons[:3], buttons[3:]]

    await query.edit_message_text(
        f"⏱ **Learn with HiM Quiz Setup (Step 2/2)**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Selected: `{count} Questions`\n\n"
        f"Choose timer duration per question:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def quiz_timer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_quiz_maintenance(update): return

    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    timer_sec = int(query.data.replace("qtimer_", ""))
    setup = QUIZ_SETUP_CACHE.pop(user_id, {"count": 20})
    count = setup.get("count", 20)

    seen_ids = get_seen_question_ids(user_id)
    questions = fetch_pyqs_for_quiz(needed_count=count, seen_ids=seen_ids)

    if not questions:
        await query.edit_message_text("🎉 You have completed all questions in the question bank!")
        return

    q_ids = [q["id"] for q in questions if q.get("id") is not None]
    mark_questions_as_seen(user_id, q_ids)

    session = {
        "user_id": user_id,
        "questions": questions,
        "current_index": 0,
        "score": 0.0,
        "correct": 0,
        "wrong": 0,
        "skipped": 0,
        "total": len(questions),
        "timer_sec": timer_sec,
        "is_paused": False,
        "start_time": get_ist_timestamp_str()
    }
    ACTIVE_SESSIONS[user_id] = session

    await query.edit_message_text(
        f"🚀 **Quiz Session Started!**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 **Questions:** `{len(questions)}` | ⏱ **Timer:** `{timer_sec}s/question`\n"
        f"📅 **Attempt Date:** `{session['start_time']}`\n\n"
        f"Loading Question 1/{len(questions)}...",
        parse_mode="Markdown"
    )
    await send_next_question(query.message.chat_id, user_id, context)

async def pause_quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    session = ACTIVE_SESSIONS.get(user_id)
    if not session or session.get("is_paused"):
        msg = "ℹ️ You do not have an active running quiz to pause."
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
        "start_time": session["start_time"]
    }
    save_paused_quiz_state(user_id, save_state)
    ACTIVE_SESSIONS.pop(user_id, None)

    remaining_qs = session["total"] - session["current_index"]
    msg = (
        f"⏸ **QUIZ PAUSED & SAVED**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• **Paused At Question:** `{session['current_index'] + 1}` / `{session['total']}`\n"
        f"• **Remaining Questions:** `{remaining_qs}` Qs\n"
        f"• **Current Score:** `{session['score']}` / `{session['total']}`\n\n"
        f"Type **/resume** or tap below whenever you wish to continue!"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Resume Quiz Now (/resume)", callback_data="cmd_resume_quiz")]
    ])

    if update.callback_query:
        await update.callback_query.answer("⏸ Quiz Paused!", show_alert=True)
        await context.bot.send_message(chat_id=chat_id, text=msg, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, reply_markup=keyboard, parse_mode="Markdown")

async def resume_quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    paused = get_paused_quiz_state(user_id)
    if not paused:
        msg = "ℹ️ No paused quiz found. Type /quiz to start a new session!"
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
        "start_time": paused["start_time"]
    }
    ACTIVE_SESSIONS[user_id] = session

    if update.callback_query:
        await update.callback_query.answer()

    countdown_msg = await context.bot.send_message(
        chat_id=chat_id, 
        text=f"▶️ **RESUMING QUIZ...**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⏳ **3...** Get ready for Question `{session['current_index'] + 1}/{session['total']}`!",
        parse_mode="Markdown"
    )
    await asyncio.sleep(1)

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=countdown_msg.message_id,
            text=f"▶️ **RESUMING QUIZ...**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⏳ **2...** Get ready for Question `{session['current_index'] + 1}/{session['total']}`!",
            parse_mode="Markdown"
        )
        await asyncio.sleep(1)

        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=countdown_msg.message_id,
            text=f"▶️ **RESUMING QUIZ...**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚡ **1...** Launching Poll now!",
            parse_mode="Markdown"
        )
        await asyncio.sleep(1)
    except Exception:
        pass

    await send_next_question(chat_id, user_id, context)

async def send_next_question(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    m_until = get_maintenance_until()
    if int(time.time()) < m_until:
        await context.bot.send_message(chat_id=chat_id, text="🛠 **ADMIN HAS PAUSED THE SERVICE CURRENTLY**\nQuiz session paused!")
        return

    session = ACTIVE_SESSIONS.get(user_id)
    if not session or session.get("is_paused"):
        return

    if session["current_index"] >= len(session["questions"]):
        await finish_quiz_and_send_report(chat_id, user_id, context)
        return

    q = session["questions"][session["current_index"]]
    timer_sec = session["timer_sec"]

    header_text = f"🖥 [Q {session['current_index']+1}/{session['total']}]\n\n{q['question']}"
    if len(header_text) > 300:
        header_text = header_text[:297] + "..."

    clean_opts = [str(opt)[:97] for opt in q["options"]]
    expl_text = q.get("explanation") or "Learn with HiM Quiz Book by Himanshu Sir"
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
        POLL_MAP[poll_id] = {"user_id": user_id, "chat_id": chat_id, "q_idx": session["current_index"], "correct_id": correct_id}

        if user_id in TIMER_TASKS and not TIMER_TASKS[user_id].done():
            TIMER_TASKS[user_id].cancel()

        TIMER_TASKS[user_id] = asyncio.create_task(auto_skip_task(chat_id, user_id, poll_id, session["current_index"], timer_sec, context))
    except Exception as e:
        logging.error(f"Error sending poll: {e}")
        session["skipped"] += 1
        session["current_index"] += 1
        if session["current_index"] >= session["total"]:
            await finish_quiz_and_send_report(chat_id, user_id, context)
        else:
            await send_next_question(chat_id, user_id, context)

async def auto_skip_task(chat_id: int, user_id: int, poll_id: str, expected_idx: int, timer_sec: int, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(timer_sec + 1)
    if poll_id in POLL_MAP:
        POLL_MAP.pop(poll_id, None)
        session = ACTIVE_SESSIONS.get(user_id)
        if session and not session.get("is_paused") and session["current_index"] == expected_idx:
            session["skipped"] += 1
            session["current_index"] += 1
            if session["current_index"] >= session["total"]:
                await finish_quiz_and_send_report(chat_id, user_id, context)
            else:
                await send_next_question(chat_id, user_id, context)

async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    poll_id = answer.poll_id
    if poll_id not in POLL_MAP:
        return

    data = POLL_MAP.pop(poll_id)
    user_id = data["user_id"]
    chat_id = data["chat_id"]

    if user_id in TIMER_TASKS and not TIMER_TASKS[user_id].done():
        TIMER_TASKS[user_id].cancel()

    m_until = get_maintenance_until()
    if int(time.time()) < m_until:
        await context.bot.send_message(chat_id=chat_id, text="🛠 **ADMIN HAS PAUSED THE SERVICE CURRENTLY**")
        return

    session = ACTIVE_SESSIONS.get(user_id)
    if session and not session.get("is_paused") and session["current_index"] == data["q_idx"]:
        selected = answer.option_ids[0] if answer.option_ids else -1
        if selected == data["correct_id"]:
            session["score"] += 1.0
            session["correct"] += 1
        else:
            session["wrong"] += 1

        session["current_index"] += 1
        
        if session["current_index"] >= session["total"]:
            await finish_quiz_and_send_report(chat_id, user_id, context)
        else:
            await send_next_question(chat_id, user_id, context)

async def finish_quiz_and_send_report(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """GENERATES AND SENDS COMPLETE DETAILED REPORT CARD AT QUIZ END INSTANTLY."""
    session = ACTIVE_SESSIONS.pop(user_id, None)
    if not session:
        return

    total = session["total"]
    correct = session["correct"]
    wrong = session["wrong"]
    skipped = session["skipped"]
    score = session["score"]
    attempted = correct + wrong

    record_quiz_result(
        user_id=user_id, 
        score=score, 
        total_questions=total, 
        correct_count=correct, 
        wrong_count=wrong, 
        skipped_count=skipped
    )

    percentile = calculate_user_percentile(user_id)
    rank_str = calculate_user_rank(user_id)
    profile = get_user_profile(user_id)
    student_name = profile.get("full_name", "Student") if profile else "Student"

    accuracy = round((correct / max(1, attempted)) * 100, 1) if attempted > 0 else 0.0

    if accuracy >= 80:
        motivation_quote = "🌟 *\"Success is not final, failure is not fatal: it is the courage to continue that counts.\"*\n— Winston Churchill"
    elif accuracy >= 50:
        motivation_quote = "📈 *\"Quality is not an act, it is a habit. Consistent effort produces consistent results!\"*\n— Aristotle"
    else:
        motivation_quote = "💪 *\"Our greatest glory is not in never falling, but in rising every time we fall.\"*\n— Confucius"

    report_card = (
        f"🏆 **OFFICIAL QUIZ REPORT CARD**\n"
        f"📚 *Learn with HiM Quiz Book by Himanshu Sir*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 **Student Name:** {student_name}\n"
        f"📅 **Attempted At:** `{session['start_time']}`\n\n"
        f"📊 **DETAILED SCORE ANALYSIS:**\n"
        f"• **Total Questions:** `{total}`\n"
        f"• **Total Attempted:** `{attempted}` / `{total}` Qs\n"
        f"• **Total Correct:** `{correct}` ✅\n"
        f"• **Total Wrong:** `{wrong}` ❌\n"
        f"• **Total Skipped:** `{skipped}` ⏭\n"
        f"• **Accuracy Rate:** `{accuracy}%` 🎯\n"
        f"• **Final Score:** `{score} / {total}` Marks\n\n"
        f"🎖 **GLOBAL RANK & PERCENTILE:**\n"
        f"• **Global Rank:** `{rank_str}`\n"
        f"• **Overall Percentile:** `{percentile}%`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔥 **MOTIVATION FOR YOU:**\n"
        f"{motivation_quote}\n\n"
        f"👇 **Share your score & challenge your friends:**"
    )

    clean_channel = CHANNEL_USERNAME.replace('@', '')
    buttons = [
        [InlineKeyboardButton("📢 Join Telegram Channel", url=f"https://t.me/{clean_channel}")],
        [InlineKeyboardButton("📺 Subscribe YouTube Channel", url=YOUTUBE_CHANNEL_URL)],
        [InlineKeyboardButton("🚀 Start Fresh Quiz (/quiz)", callback_data="cmd_quiz")]
    ]

    try:
        await context.bot.send_message(
            chat_id=chat_id, 
            text=report_card, 
            reply_markup=InlineKeyboardMarkup(buttons), 
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Failed to deliver final score card to chat {chat_id}: {e}")