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
from app.pdf_generator import generate_instant_quiz_pdf_report

logger = logging.getLogger(__name__)

ACTIVE_SESSIONS = {}
POLL_MAP = {}
TIMER_TASKS = {}
QUIZ_SETUP_CACHE = {}

def get_pause_resume_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏸ Pause (/pause)", callback_data="cmd_pause_quiz"), 
            InlineKeyboardButton("▶️ Resume (/resume)", callback_data="cmd_resume_quiz"),
            InlineKeyboardButton("🛑 Stop (/stop)", callback_data="cmd_stop_quiz")
        ],
        [
            InlineKeyboardButton("💾 Save Question", callback_data="cmd_save_question")
        ]
    ])

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
    log_user_activity_time(user.id, seconds=10)
    profile = await asyncio.to_thread(get_user_profile, user.id)
    
    if not profile or not profile.get("is_verified"):
        await update.message.reply_text("⚠️ Please type /start to create your profile before attempting quizzes!")
        return

    attempted_today = await asyncio.to_thread(get_today_attempts, user.id)
    allowed_limit = 10000 if user.id == PRIMARY_ADMIN_ID else DAILY_QUESTION_LIMIT + profile.get("bonus_quota", 0)

    if attempted_today >= allowed_limit:
        exhausted_msg = (
            f"🛑 **WARNING: DAILY FREE LIMIT EXHAUSTED!**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **Today's Usage:** `{attempted_today}` / `{allowed_limit}` Questions\n"
            f"• **Status:** You have fully exhausted your free daily limit for today (00:00 to 23:59).\n\n"
            f"⚠️ **Notice:** The `/quiz` command is now **deactivated** for your account until tomorrow or until you unlock extra quota via referrals!\n\n"
            f"💡 **Unlock +10 Questions:** Share your invite link with 4 friends using `/invite`."
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🤝 Invite Friends (+10 Limit)", callback_data="cmd_referral")
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

    remaining_quota = allowed_limit - attempted_today
    counts = [10, 15, 20, 25, 30, 40]
    valid_counts = [c for c in counts if c <= remaining_quota]
    if not valid_counts:
        valid_counts = [max(1, remaining_quota)]

    buttons = [InlineKeyboardButton(f"📝 {c} Qs", callback_data=f"qcount_{c}") for c in valid_counts]
    keyboard = [buttons[:3], buttons[3:]]

    msg_text = (
        f"📚 **Learn with HiM Quiz Setup (Step 1/2)**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• **Daily Quota Used Today:** `{attempted_today}` / `{allowed_limit}` Qs\n"
        f"• **Remaining Quota:** `{remaining_quota}` Qs available\n\n"
        f"Select the number of questions for this session:"
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
    
    profile = await asyncio.to_thread(get_user_profile, user_id)
    attempted_today = await asyncio.to_thread(get_today_attempts, user_id)
    allowed_limit = 10000 if user_id == PRIMARY_ADMIN_ID else DAILY_QUESTION_LIMIT + (profile.get("bonus_quota", 0) if profile else 0)

    if attempted_today >= allowed_limit:
        await query.answer("🛑 Daily Limit Exhausted! Quiz is locked.", show_alert=True)
        await query.edit_message_text(
            f"🛑 **WARNING: DAILY FREE LIMIT EXHAUSTED!**\n\nYou have reached your actual daily limit of `{allowed_limit}` questions for today (00:00 to 23:59). The `/quiz` command is deactivated.",
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
    buttons = [InlineKeyboardButton(f"⏱ {t}s", callback_data=f"qtimer_{t}") for t in timers]
    keyboard = [buttons[:3], buttons[3:]]

    await query.edit_message_text(
        f"⏱ **Learn with HiM Quiz Setup (Step 2/2)**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Selected: `{count} Questions` (Remaining Quota: `{remaining_quota}`)\n\n"
        f"Choose timer duration per question:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def quiz_timer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_quiz_maintenance(update): return

    query = update.callback_query
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    log_user_activity_time(user_id, seconds=15)
    
    profile = await asyncio.to_thread(get_user_profile, user_id)
    attempted_today = await asyncio.to_thread(get_today_attempts, user_id)
    allowed_limit = 10000 if user_id == PRIMARY_ADMIN_ID else DAILY_QUESTION_LIMIT + (profile.get("bonus_quota", 0) if profile else 0)

    if attempted_today >= allowed_limit:
        await query.answer("🛑 Daily Limit Exhausted! Quiz is locked.", show_alert=True)
        await query.edit_message_text(
            f"🛑 **WARNING: DAILY FREE LIMIT EXHAUSTED!**\n\nYou have reached your actual daily limit of `{allowed_limit}` questions for today (00:00 to 23:59). Your quiz has been cancelled.",
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

    seen_ids = await asyncio.to_thread(get_seen_question_ids, user_id)
    questions = await asyncio.to_thread(fetch_pyqs_for_quiz, count, seen_ids)

    if not questions:
        await query.edit_message_text("🎉 You have completed all questions in the question bank!")
        return

    q_ids = [q["id"] for q in questions if q.get("id") is not None]
    await asyncio.to_thread(mark_questions_as_seen, user_id, q_ids)

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
        f"🚀 **Quiz Session Started!**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 **Questions:** `{len(questions)}` | ⏱ **Timer:** `{timer_sec}s/question`\n"
        f"📅 **Attempt Date:** `{session['start_time']}`\n\n"
        f"Loading Question 1/{len(questions)}...",
        parse_mode="Markdown"
    )
    await send_next_question(chat_id, user_id, context)

async def save_question_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    log_user_activity_time(user_id, seconds=5)
    session = ACTIVE_SESSIONS.get(user_id)
    
    if not session or "current_question" not in session:
        await query.answer("⚠️ No active question found to save right now!", show_alert=True)
        return
    
    q = session["current_question"]
    success = await asyncio.to_thread(
        save_question_to_db,
        user_id,
        q["question"],
        q["options"],
        q["correct_option"],
        q.get("explanation", "")
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
        "chat_id": chat_id,
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
    await asyncio.to_thread(save_paused_quiz_state, user_id, save_state)
    ACTIVE_SESSIONS.pop(user_id, None)

    remaining_qs = session["total"] - session["current_index"]
    msg = (
        f"⏸ **QUIZ PAUSED & SAVED**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• **Paused At Question:** `{session['current_index'] + 1}` / `{session['total']}`\n"
        f"• **Remaining Questions:** `{remaining_qs}` Qs\n"
        f"• **Current Score:** `{session['score']}` / `{session['total']}`\n\n"
        f"Type `/resume` or tap below whenever you wish to continue!"
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
    log_user_activity_time(user_id, seconds=5)

    paused = await asyncio.to_thread(get_paused_quiz_state, user_id)
    if not paused:
        msg = "ℹ️ No paused quiz found. Type /quiz to start a new session!"
        if update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        else:
            await update.message.reply_text(msg)
        return

    await asyncio.to_thread(clear_paused_quiz_state, user_id)

    session = {
        "user_id": user_id,
        "chat_id": chat_id,
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
        text=f"▶️ **RESUMING QUIZ...**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⏳ **3...** Get ready for Question `{session['current_index'] + 1}/{session['total']}`!",
        parse_mode="Markdown"
    )
    await asyncio.sleep(0.5)

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=countdown_msg.message_id,
            text=f"▶️ **RESUMING QUIZ...**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⏳ **2...** Get ready for Question `{session['current_index'] + 1}/{session['total']}`!",
            parse_mode="Markdown"
        )
        await asyncio.sleep(0.5)

        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=countdown_msg.message_id,
            text=f"▶️ **RESUMING QUIZ...**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚡ **1...** Launching Poll now!",
            parse_mode="Markdown"
        )
        await asyncio.sleep(0.5)
    except Exception:
        pass

    await send_next_question(chat_id, user_id, context)

async def stop_quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    log_user_activity_time(user_id, seconds=5)

    session = ACTIVE_SESSIONS.get(user_id)
    paused = await asyncio.to_thread(get_paused_quiz_state, user_id)
    
    if not session and not paused:
        msg = "ℹ️ You do not have an active or paused quiz session to stop."
        if update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        else:
            await update.message.reply_text(msg)
        return

    await asyncio.to_thread(clear_paused_quiz_state, user_id)
    if user_id in TIMER_TASKS and not TIMER_TASKS[user_id].done():
        TIMER_TASKS[user_id].cancel()

    if session:
        if session["current_index"] > 0:
            await finish_quiz_and_send_report(chat_id, user_id, context, session_override=session)
            return
    elif paused:
        if paused.get("current_index", 0) > 0:
            await finish_quiz_and_send_report(chat_id, user_id, context, session_override=paused)
            return

    msg = (
        f"🛑 **QUIZ STOPPED COMPLETELY**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• Your quiz session has been terminated.\n"
        f"• Remaining unattempted limit has been restored to your daily quota.\n\n"
        f"Type `/quiz` whenever you are ready to start again!"
    )

    if update.callback_query:
        await update.callback_query.answer("🛑 Quiz Stopped Successfully!", show_alert=True)
        await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, parse_mode="Markdown")

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
    session["current_question"] = q
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
        POLL_MAP[poll_id] = {
            "user_id": user_id, 
            "chat_id": chat_id, 
            "q_idx": session["current_index"], 
            "correct_id": correct_id,
            "q_data": q
        }

        await context.bot.send_message(
            chat_id=chat_id,
            text="Quiz Controls:",
            reply_markup=get_pause_resume_keyboard()
        )

        if user_id in TIMER_TASKS and not TIMER_TASKS[user_id].done():
            TIMER_TASKS[user_id].cancel()

        TIMER_TASKS[user_id] = asyncio.create_task(auto_skip_task(chat_id, user_id, poll_id, session["current_index"], timer_sec, context))
    except Exception as e:
        logger.error(f"Error sending poll: {e}")
        session["skipped"] += 1
        session["current_index"] += 1
        await send_next_question(chat_id, user_id, context)

async def auto_skip_task(chat_id: int, user_id: int, poll_id: str, expected_idx: int, timer_sec: int, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(timer_sec)
    if poll_id in POLL_MAP:
        data = POLL_MAP.pop(poll_id, None)
        session = ACTIVE_SESSIONS.get(user_id)
        if session and not session.get("is_paused") and session["current_index"] == expected_idx:
            q = data.get("q_data", {})
            opts = q.get("options", [])
            c_idx = data.get("correct_id", 0)
            c_ans_text = opts[c_idx] if 0 <= c_idx < len(opts) else "N/A"

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
        await context.bot.send_message(chat_id=chat_id, text="🛠 **ADMIN HAS PAUSED THE SERVICE CURRENTLY**")
        return

    session = ACTIVE_SESSIONS.get(user_id)
    if session and not session.get("is_paused") and session["current_index"] == data["q_idx"]:
        selected = answer.option_ids[0] if answer.option_ids else -1
        correct_id = data["correct_id"]
        q = data.get("q_data", {})
        opts = q.get("options", [])

        c_ans_text = opts[correct_id] if 0 <= correct_id < len(opts) else "N/A"

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
        await send_next_question(chat_id, user_id, context)

async def download_instant_pdf_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer("⏳ Generating your instant PDF report card...")
    
    user_data = context.application.user_data.get(user_id, {})
    quiz_result_payload = user_data.get("last_quiz_result")
    
    if not quiz_result_payload:
        await query.message.reply_text("⚠️ No recent quiz session data found to export.")
        return

    profile = await asyncio.to_thread(get_user_profile, user_id)
    pdf_file = await asyncio.to_thread(generate_instant_quiz_pdf_report, user_id, quiz_result_payload)

    if pdf_file and os.path.exists(pdf_file):
        with open(pdf_file, "rb") as doc:
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=doc,
                filename=os.path.basename(pdf_file),
                caption=(
                    f"📄 **OFFICIAL INSTANT QUIZ REPORT CARD**\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 **Student:** {profile['full_name'] if profile else 'Student'}\n"
                    f"🏆 **Final Score:** `{quiz_result_payload['score']} / {quiz_result_payload['total_questions']}.0`\n"
                    f"🏷 **Watermark:** `@LearnwithHiM`"
                )
            )
    else:
        await query.message.reply_text("⚠️ Failed to generate PDF report card.")

async def finish_quiz_and_send_report(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE, session_override=None):
    session = session_override or ACTIVE_SESSIONS.pop(user_id, None)
    if not session:
        return

    total = session["total"]
    correct = session["correct"]
    wrong = session["wrong"]
    skipped = session["skipped"]
    score = session["score"]
    detailed_logs = session.get("detailed_logs", [])

    await asyncio.to_thread(
        record_quiz_result,
        user_id, 
        score, 
        total, 
        correct, 
        wrong, 
        skipped,
        detailed_logs
    )

    percentile = await asyncio.to_thread(calculate_user_percentile, user_id)
    rank_str = await asyncio.to_thread(calculate_user_rank, user_id)

    if user_id not in context.application.user_data:
        context.application.user_data[user_id] = {}
        
    context.application.user_data[user_id]["last_quiz_result"] = {
        "total_questions": total,
        "score": score,
        "correct_count": correct,
        "wrong_count": wrong,
        "skipped_count": skipped,
        "details": detailed_logs
    }

    # Updated Telegram Join Link with @learnwithhim
    buttons = [
        [InlineKeyboardButton("📄 Download Attempt Summary PDF Card", callback_data="cmd_download_instant_pdf")],
        [InlineKeyboardButton("📢 Join Telegram Channel (@learnwithhim)", url="https://t.me/learnwithhim")],
        [InlineKeyboardButton("📖 Review Saved Questions", callback_data="cmd_savedquestions")],
        [InlineKeyboardButton("🚀 Attempt Another Quiz", callback_data="cmd_quiz")]
    ]

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
        f"• **Final Score:** `{score} / {total}.0`\n\n"
        f"🎖 **Overall Rank & Percentile:**\n"
        f"• **Global Rank:** `{rank_str}`\n"
        f"• **Percentile Rating:** `{percentile}%`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 *Tap the PDF button below to download your full itemized report card!*"
    )

    try:
        await context.bot.send_message(
            chat_id=chat_id, 
            text=report_card, 
            reply_markup=InlineKeyboardMarkup(buttons), 
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error sending final quiz report card: {e}")