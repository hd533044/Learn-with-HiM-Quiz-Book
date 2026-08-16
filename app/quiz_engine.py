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
from app.pyq_fetcher import fetch_pyqs_for_quiz, get_available_topics, TOPIC_METADATA
from app.stats import calculate_user_percentile, calculate_user_rank

logger = logging.getLogger(__name__)

ACTIVE_SESSIONS = {}
POLL_MAP = {}
TIMER_TASKS = {}
QUIZ_SETUP_CACHE = {}

def get_pause_resume_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏸ Pause", callback_data="cmd_pause_quiz"), 
            InlineKeyboardButton("▶️ Resume", callback_data="cmd_resume_quiz"),
            InlineKeyboardButton("🛑 Stop", callback_data="cmd_stop_quiz")
        ],
        [
            InlineKeyboardButton("💾 Save Question", callback_data="cmd_save_question")
        ]
    ])

def get_quizbook_nav_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("❌ Wrong Qs", callback_data="cmd_wrong_qs"),
            InlineKeyboardButton("⏭ Skipped Qs", callback_data="cmd_unattempted_qs")
        ],
        [
            InlineKeyboardButton("🎯 Attempted Qs", callback_data="cmd_attempted_qs"),
            InlineKeyboardButton("💾 Saved Qs", callback_data="cmd_savedquestions")
        ],
        [
            InlineKeyboardButton("📄 PDF Report", callback_data="cmd_pdfreport"),
            InlineKeyboardButton("🏆 Leaderboard", callback_data="cmd_toppers")
        ],
        [
            InlineKeyboardButton("📊 My Analytics", callback_data="cmd_wholestate"),
            InlineKeyboardButton("💳 VIP Plans", callback_data="cmd_plans")
        ],
        [
            InlineKeyboardButton("🚀 Launch New Quiz", callback_data="cmd_quiz")
        ]
    ])

async def check_quiz_maintenance(update: Update) -> bool:
    m_until = await asyncio.to_thread(get_maintenance_until)
    if int(time.time()) < m_until:
        remaining_sec = m_until - int(time.time())
        mins_left = max(1, (remaining_sec + 59) // 60)
        msg = f"🛠 **ADMIN HAS PAUSED THE SERVICE CURRENTLY** 🛠\n\n⏰ Service will resume in approx `{mins_left} mins`. Please try again later!"
        
        if update.callback_query:
            await update.callback_query.answer(f"🛠 Service Paused! Resuming in ~{mins_left} mins.", show_alert=True)
        elif update.message:
            await update.message.reply_text(msg, parse_mode="Markdown")
        return False
    return True

async def launch_quiz_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 1: Select Language (English or Hindi)."""
    if not await check_quiz_maintenance(update): return

    user = update.effective_user
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user.id, 10))
    profile = await asyncio.to_thread(get_user_profile, user.id)
    
    if not profile or not profile.get("is_verified"):
        await update.message.reply_text("⚠️ Please type /start to complete registration before attempting quizzes!")
        return

    attempted_today = await asyncio.to_thread(get_today_attempts, user.id)
    
    paid_bal = profile.get("paid_question_balance", 0) or 0
    base_limit = max(DAILY_QUESTION_LIMIT, paid_bal)
    allowed_limit = 10000 if user.id == PRIMARY_ADMIN_ID else base_limit + profile.get("bonus_quota", 0)

    if attempted_today >= allowed_limit:
        exhausted_msg = (
            f"🛑 **DAILY LIMIT EXHAUSTED!** 🛑\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 **Today's Usage:** `{attempted_today}` / `{allowed_limit}` Questions\n"
            f"🔒 **Status:** Daily question quota fully used for today.\n\n"
            f"💳 **Upgrade Your Limit:** Tap **💳 View VIP Payment Plans** to unlock higher daily question limits!"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 View VIP Payment Plans", callback_data="cmd_plans")],
            [InlineKeyboardButton("🤝 Invite Friends (+10 Limit)", callback_data="cmd_referral")]
        ])
        if update.callback_query:
            await update.callback_query.answer("🛑 Daily Limit Exhausted!", show_alert=True)
            await update.callback_query.message.reply_text(exhausted_msg, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await update.message.reply_text(exhausted_msg, reply_markup=keyboard, parse_mode="Markdown")
        return

    paused = await asyncio.to_thread(get_paused_quiz_state, user.id)
    if paused:
        remaining_count = len(paused.get('questions', [])) - paused.get('current_index', 0)
        topic_disp = paused.get('topic_name') or "Mixed Practice"
        text = (
            f"⏸ **PAUSED QUIZ SESSION FOUND!** ⏸\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📖 **Topic:** `{topic_disp}`\n"
            f"📌 **Resume Point:** Question `{paused.get('current_index', 0) + 1}` / `{paused.get('total', 0)}`\n"
            f"📊 **Remaining Questions:** `{remaining_count}` Qs\n"
            f"⭐ **Current Score:** `{paused.get('score', 0.0)}` / `{paused.get('total', 0)}`\n\n"
            f"Tap **▶️ Resume Paused Quiz** below to continue:"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ Resume Paused Quiz", callback_data="cmd_resume_quiz")],
            [InlineKeyboardButton("🔄 Start Fresh Quiz", callback_data="cmd_start_fresh_quiz")]
        ])
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
        return

    # Language Selection Buttons
    lang_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 English", callback_data="qlang_en"), InlineKeyboardButton("🇮🇳 हिंदी (Hindi)", callback_data="qlang_hi")]
    ])

    msg_text = (
        f"🌐 **QUIZ WITH HIM SETUP (STEP 1/4)** 🌐\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Please select your preferred language for this quiz session:"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(msg_text, reply_markup=lang_keyboard, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg_text, reply_markup=lang_keyboard, parse_mode="Markdown")

async def quiz_language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 2: Select Mode (Chapter-Wise vs Mixed Practice)."""
    if not await check_quiz_maintenance(update): return

    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    lang = query.data.replace("qlang_", "")
    if user_id not in QUIZ_SETUP_CACHE:
        QUIZ_SETUP_CACHE[user_id] = {}
    QUIZ_SETUP_CACHE[user_id]["language"] = lang

    lang_label = "🌐 English" if lang == "en" else "🇮🇳 हिंदी"

    if lang == "hi":
        mode_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📂 1. विषयवार / अध्यायवार अभ्यास (Chapter-Wise)", callback_data="qmode_topic")],
            [InlineKeyboardButton("🔀 2. मिश्रित अभ्यास मॉक टेस्ट (Mixed Practice)", callback_data="qmode_mixed")]
        ])
        msg_text = (
            f"📚 **QUIZ WITH HIM सेटअप (चरण 2/4)** 📚\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🌐 **चुनी गई भाषा:** `{lang_label}`\n\n"
            f"कृपया अपना अभ्यास मोड चुनें:"
        )
    else:
        mode_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📂 1. Chapter-Wise / Topic-Wise Practice", callback_data="qmode_topic")],
            [InlineKeyboardButton("🔀 2. Mixed Practice Mock Test", callback_data="qmode_mixed")]
        ])
        msg_text = (
            f"📚 **QUIZ WITH HIM SETUP (STEP 2/4)** 📚\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🌐 **Language:** `{lang_label}`\n\n"
            f"Please select your practice mode:"
        )

    await query.edit_message_text(msg_text, reply_markup=mode_keyboard, parse_mode="Markdown")

async def quiz_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles Mode Selection: Shows clean topic list OR proceeds to count for mixed."""
    if not await check_quiz_maintenance(update): return

    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    mode = query.data.replace("qmode_", "")
    current_cache = QUIZ_SETUP_CACHE.get(user_id, {"language": "en"})
    lang = current_cache.get("language", "en")
    lang_label = "🌐 English" if lang == "en" else "🇮🇳 हिंदी"

    if mode == "topic":
        # Render clean topic selection without question counts
        topics_list = get_available_topics(lang)
        keyboard = []
        for t_key, t_display in topics_list:
            keyboard.append([InlineKeyboardButton(t_display, callback_data=f"qtopic_{t_key}")])

        back_lbl = "🔙 वापस (Back)" if lang == "hi" else "🔙 Back"
        keyboard.append([InlineKeyboardButton(back_lbl, callback_data="cmd_quiz")])

        header = "📂 **विषय / अध्याय चुनें (TOPIC SELECTION)** 📂" if lang == "hi" else "📂 **SELECT CHAPTER / TOPIC** 📂"
        sub = "अभ्यास शुरू करने के लिए नीचे दिए गए विषय पर टैप करें:" if lang == "hi" else "Tap a topic below to start focused practice:"

        msg_text = (
            f"{header}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🌐 **Language:** `{lang_label}`\n\n"
            f"{sub}"
        )
        await query.edit_message_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif mode == "mixed":
        current_cache["topic"] = "MIXED"
        current_cache["topic_name"] = "Mixed Practice (All Topics)" if lang == "en" else "मिश्रित अभ्यास (सभी विषय)"
        QUIZ_SETUP_CACHE[user_id] = current_cache
        await show_count_selection(query, user_id, lang)

async def quiz_topic_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles Specific Topic Selection and moves to Count Selection."""
    if not await check_quiz_maintenance(update): return

    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    topic_key = query.data.replace("qtopic_", "")
    current_cache = QUIZ_SETUP_CACHE.get(user_id, {"language": "en"})
    lang = current_cache.get("language", "en")
    
    t_info = TOPIC_METADATA.get(topic_key, {})
    topic_display = t_info.get("hi" if lang == "hi" else "en", topic_key.replace("_", " "))

    current_cache["topic"] = topic_key
    current_cache["topic_name"] = topic_display
    QUIZ_SETUP_CACHE[user_id] = current_cache

    await show_count_selection(query, user_id, lang)

async def show_count_selection(query, user_id: int, lang: str):
    """Step 3: Select Question Count."""
    profile = await asyncio.to_thread(get_user_profile, user_id)
    attempted_today = await asyncio.to_thread(get_today_attempts, user_id)
    
    paid_bal = profile.get("paid_question_balance", 0) or 0 if profile else 0
    base_limit = max(DAILY_QUESTION_LIMIT, paid_bal)
    allowed_limit = 10000 if user_id == PRIMARY_ADMIN_ID else base_limit + (profile.get("bonus_quota", 0) if profile else 0)

    remaining_quota = allowed_limit - attempted_today
    counts = [10, 15, 20, 25, 30, 40, 50, 80, 100]
    valid_counts = [c for c in counts if c <= remaining_quota]
    if not valid_counts:
        valid_counts = [max(1, remaining_quota)]

    buttons = [InlineKeyboardButton(f"📝 {c} Qs", callback_data=f"qcount_{c}") for c in valid_counts[:6]]
    keyboard = [buttons[:3], buttons[3:]]

    lang_label = "🌐 English" if lang == "en" else "🇮🇳 हिंदी"
    topic_name = QUIZ_SETUP_CACHE.get(user_id, {}).get("topic_name", "Computer Awareness")

    msg_text = (
        f"📚 **QUIZ WITH HIM SETUP (STEP 3/4)** 📚\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 **Language:** `{lang_label}`\n"
        f"📖 **Topic:** `{topic_name}`\n"
        f"📊 **Daily Quota Used:** `{attempted_today}` / `{allowed_limit}` Qs\n"
        f"⚡ **Available Quota:** `{remaining_quota}` Qs\n\n"
        f"📝 Select the number of questions for this session:"
    )

    await query.edit_message_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def quiz_count_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 4: Select Timer per Question."""
    if not await check_quiz_maintenance(update): return

    query = update.callback_query
    user_id = query.from_user.id
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user_id, 10))
    
    profile = await asyncio.to_thread(get_user_profile, user_id)
    attempted_today = await asyncio.to_thread(get_today_attempts, user_id)
    
    paid_bal = profile.get("paid_question_balance", 0) or 0 if profile else 0
    base_limit = max(DAILY_QUESTION_LIMIT, paid_bal)
    allowed_limit = 10000 if user_id == PRIMARY_ADMIN_ID else base_limit + (profile.get("bonus_quota", 0) if profile else 0)

    if attempted_today >= allowed_limit:
        await query.answer("🛑 Daily Limit Exhausted!", show_alert=True)
        await query.edit_message_text(
            f"🛑 **DAILY FREE LIMIT EXHAUSTED!** 🛑\n\n"
            f"You have reached your daily limit of `{allowed_limit}` questions for today.",
            parse_mode="Markdown"
        )
        return

    await query.answer()
    count = int(query.data.replace("qcount_", ""))
    
    remaining_quota = allowed_limit - attempted_today
    if count > remaining_quota:
        count = max(1, remaining_quota)

    current_cache = QUIZ_SETUP_CACHE.get(user_id, {})
    current_cache["count"] = count
    QUIZ_SETUP_CACHE[user_id] = current_cache

    timers = [12, 15, 18, 20, 25, 30]
    buttons = [InlineKeyboardButton(f"⏱ {t}s", callback_data=f"qtimer_{t}") for t in timers]
    keyboard = [buttons[:3], buttons[3:]]

    lang_label = "🌐 English" if current_cache.get("language") == "en" else "🇮🇳 हिंदी"
    topic_name = current_cache.get("topic_name", "Computer Awareness")

    await query.edit_message_text(
        f"⏱ **QUIZ WITH HIM SETUP (STEP 4/4)** ⏱\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 **Language:** `{lang_label}`\n"
        f"📖 **Topic:** `{topic_name}`\n"
        f"📝 **Selected:** `{count} Questions` (Available: `{remaining_quota}`)\n\n"
        f"⏱ Choose timer duration per question:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def quiz_timer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Launches Quiz Session with selected Topic and Spaced Repetition."""
    if not await check_quiz_maintenance(update): return

    query = update.callback_query
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user_id, 15))
    
    profile = await asyncio.to_thread(get_user_profile, user_id)
    attempted_today = await asyncio.to_thread(get_today_attempts, user_id)
    
    paid_bal = profile.get("paid_question_balance", 0) or 0 if profile else 0
    base_limit = max(DAILY_QUESTION_LIMIT, paid_bal)
    allowed_limit = 10000 if user_id == PRIMARY_ADMIN_ID else base_limit + (profile.get("bonus_quota", 0) if profile else 0)

    if attempted_today >= allowed_limit:
        await query.answer("🛑 Daily Limit Exhausted!", show_alert=True)
        await query.edit_message_text("🛑 Daily limit exhausted for today.", parse_mode="Markdown")
        return

    await query.answer()
    timer_sec = int(query.data.replace("qtimer_", ""))
    setup = QUIZ_SETUP_CACHE.pop(user_id, {"count": 20, "language": "en", "topic": "MIXED", "topic_name": "Mixed Practice"})
    count = setup.get("count", 20)
    language = setup.get("language", "en")
    topic = setup.get("topic", "MIXED")
    topic_name = setup.get("topic_name", "Mixed Practice")

    remaining_quota = allowed_limit - attempted_today
    if count > remaining_quota:
        count = max(1, remaining_quota)

    # Intelligent tiered fetching with targeted Topic
    questions = await asyncio.to_thread(fetch_pyqs_for_quiz, count, None, language, user_id, topic)

    if not questions:
        await query.edit_message_text("⚠️ No questions found for this topic. Please contact administrator.", reply_markup=get_quizbook_nav_keyboard())
        return

    q_ids = [q["id"] for q in questions if q.get("id") is not None]
    asyncio.create_task(asyncio.to_thread(mark_questions_as_seen, user_id, q_ids))

    session = {
        "user_id": user_id,
        "chat_id": chat_id,
        "questions": questions,
        "language": language,
        "topic": topic,
        "topic_name": topic_name,
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

    lang_str = "🌐 English" if language == "en" else "🇮🇳 हिंदी"

    await query.edit_message_text(
        f"🚀 **QUIZ SESSION STARTED!** 🚀\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 **Language:** `{lang_str}`\n"
        f"📖 **Topic:** `{topic_name}`\n"
        f"📝 **Questions:** `{len(questions)}` | ⏱ **Timer:** `{timer_sec}s/question`\n"
        f"📅 **Attempt Date:** `{session['start_time']}`\n\n"
        f"⚡ Loading Question 1/{len(questions)}...",
        parse_mode="Markdown"
    )
    await send_next_question(query.message.chat_id, user_id, context)

async def save_question_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user_id, 5))
    session = ACTIVE_SESSIONS.get(user_id)
    
    if not session or "current_question" not in session:
        await query.answer("⚠️ No active question found to save!", show_alert=True)
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
        await query.answer("💾 Question bookmarked successfully!", show_alert=True)
    else:
        await query.answer("ℹ️ Question already bookmarked!", show_alert=True)

async def pause_quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user_id, 5))

    session = ACTIVE_SESSIONS.get(user_id)
    if not session or session.get("is_paused"):
        msg = "ℹ️ No running quiz session found to pause."
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
        "language": session.get("language", "en"),
        "topic": session.get("topic", "MIXED"),
        "topic_name": session.get("topic_name", "Mixed Practice"),
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
    asyncio.create_task(asyncio.to_thread(save_paused_quiz_state, user_id, save_state))
    ACTIVE_SESSIONS.pop(user_id, None)

    remaining_qs = session["total"] - session["current_index"]
    msg = (
        f"⏸ **QUIZ PAUSED & SAVED** ⏸\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📖 **Topic:** `{session.get('topic_name', 'Mixed Practice')}`\n"
        f"📌 **Paused At:** Question `{session['current_index'] + 1}` / `{session['total']}`\n"
        f"📊 **Remaining Questions:** `{remaining_qs}` Qs\n"
        f"⭐ **Current Score:** `{session['score']}` / `{session['total']}`\n\n"
        f"▶️ Tap **Resume Quiz Now** or type `/resume` anytime to continue!"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Resume Quiz Now", callback_data="cmd_resume_quiz")]
    ])

    if update.callback_query:
        await update.callback_query.answer("⏸ Quiz Paused!", show_alert=True)
        await context.bot.send_message(chat_id=chat_id, text=msg, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, reply_markup=keyboard, parse_mode="Markdown")

async def resume_quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user_id, 5))

    paused = await asyncio.to_thread(get_paused_quiz_state, user_id)
    if not paused:
        msg = "ℹ️ No paused quiz found. Type /quiz to launch a new session!"
        if update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        else:
            await update.message.reply_text(msg)
        return

    asyncio.create_task(asyncio.to_thread(clear_paused_quiz_state, user_id))

    session = {
        "user_id": user_id,
        "questions": paused["questions"],
        "language": paused.get("language", "en"),
        "topic": paused.get("topic", "MIXED"),
        "topic_name": paused.get("topic_name", "Mixed Practice"),
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
        text=f"▶️ **RESUMING QUIZ ({session['topic_name']})...** ▶️\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⏳ **3...** Get ready for Question `{session['current_index'] + 1}/{session['total']}`!",
        parse_mode="Markdown"
    )
    await asyncio.sleep(1)

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=countdown_msg.message_id,
            text=f"▶️ **RESUMING QUIZ ({session['topic_name']})...** ▶️\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⏳ **2...** Get ready for Question `{session['current_index'] + 1}/{session['total']}`!",
            parse_mode="Markdown"
        )
        await asyncio.sleep(1)

        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=countdown_msg.message_id,
            text=f"▶️ **RESUMING QUIZ...** ▶️\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚡ **1...** Launching Poll now!",
            parse_mode="Markdown"
        )
        await asyncio.sleep(1)
    except Exception:
        pass

    await send_next_question(chat_id, user_id, context)

async def stop_quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user_id, 5))

    session = ACTIVE_SESSIONS.pop(user_id, None)
    paused = await asyncio.to_thread(get_paused_quiz_state, user_id)
    
    if not session and not paused:
        msg = "ℹ️ No active or paused quiz session found to stop."
        if update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        else:
            await update.message.reply_text(msg, reply_markup=get_quizbook_nav_keyboard())
        return

    asyncio.create_task(asyncio.to_thread(clear_paused_quiz_state, user_id))
    if user_id in TIMER_TASKS and not TIMER_TASKS[user_id].done():
        TIMER_TASKS[user_id].cancel()

    if session:
        if session["current_index"] > 0:
            asyncio.create_task(asyncio.to_thread(
                record_quiz_result,
                user_id,
                session.get("topic", "computer_awareness_mock"),
                session["score"],
                session["current_index"],
                session["correct"],
                session["wrong"],
                session["skipped"],
                0,
                session.get("detailed_logs", [])
            ))
    elif paused:
        if paused.get("current_index", 0) > 0:
            asyncio.create_task(asyncio.to_thread(
                record_quiz_result,
                user_id,
                paused.get("topic", "computer_awareness_mock"),
                paused["score"],
                paused["current_index"],
                paused["correct"],
                paused["wrong"],
                paused["skipped"],
                0,
                paused.get("detailed_logs", [])
            ))

    msg = (
        f"🛑 **QUIZ STOPPED** 🛑\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• Your session has been safely closed.\n"
        f"• Remaining unattempted limit restored to your daily quota.\n\n"
        f"🚀 Select an option below to continue learning:"
    )

    if update.callback_query:
        await update.callback_query.answer("🛑 Quiz Stopped!", show_alert=True)
        await context.bot.send_message(chat_id=chat_id, text=msg, reply_markup=get_quizbook_nav_keyboard(), parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, reply_markup=get_quizbook_nav_keyboard(), parse_mode="Markdown")

async def send_next_question(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    m_until = await asyncio.to_thread(get_maintenance_until)
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

    header_text = f"🖥 [{session.get('topic_name', 'Quiz')} • Q{session['current_index']+1}/{session['total']}]\n\n{q['question']}"
    if len(header_text) > 300:
        header_text = header_text[:297] + "..."

    clean_opts = [str(opt)[:97] for opt in q["options"]]
    expl_text = q.get("explanation") or "Quiz with HiM by Himanshu Sir"
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
            text="⚡ ***Quiz Controls:***",
            reply_markup=get_pause_resume_keyboard(),
            parse_mode="Markdown"
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
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user_id, data.get("timer_sec", 15)))

    if user_id in TIMER_TASKS and not TIMER_TASKS[user_id].done():
        TIMER_TASKS[user_id].cancel()

    m_until = await asyncio.to_thread(get_maintenance_until)
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
    lang = session.get("language", "en")
    topic_name = session.get("topic_name", "Computer Awareness")

    rank = await asyncio.to_thread(calculate_user_rank, user_id)
    percentile = await asyncio.to_thread(calculate_user_percentile, user_id)
    lang_label = "🌐 English" if lang == "en" else "🇮🇳 हिंदी"

    report_card = (
        f"🏆 **OFFICIAL QUIZ REPORT CARD** 🏆\n"
        f"📚 **Quiz with HiM by Himanshu Sir**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📖 **Topic:** `{topic_name}`\n"
        f"🌐 **Language:** `{lang_label}`\n"
        f"📅 **Attempted At:** `{session['start_time']}`\n\n"
        f"📊 **PERFORMANCE BREAKDOWN:**\n"
        f"• **Total Questions:** `{total}` 🖥\n"
        f"• **Correct Answers:** `{correct}` ✅\n"
        f"• **Wrong Answers:** `{wrong}` ❌\n"
        f"• **Skipped Questions:** `{skipped}` ⏭\n"
        f"• **Final Score:** `{score} / {total}` ⭐\n\n"
        f"🏆 **RANKING & PERCENTILE:**\n"
        f"• **Global Rank:** `{rank}` 🥇\n"
        f"• **Overall Percentile:** `{percentile}%` 📊\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 **INLINE QUIZ BOOK NAVIGATION:**"
    )

    end_quiz_buttons = [
        [
            InlineKeyboardButton("📄 Export PDF Report", callback_data="cmd_pdfreport"),
            InlineKeyboardButton("💾 Saved Questions", callback_data="cmd_savedquestions")
        ],
        [
            InlineKeyboardButton("❌ Wrong Questions", callback_data="cmd_wrong_qs"),
            InlineKeyboardButton("⏭ Skipped Questions", callback_data="cmd_unattempted_qs")
        ],
        [
            InlineKeyboardButton("🎯 Attempted Questions", callback_data="cmd_attempted_qs"),
            InlineKeyboardButton("🏆 Leaderboard (/toppername)", callback_data="cmd_toppers")
        ],
        [
            InlineKeyboardButton("📊 Analytics (/mywholestate)", callback_data="cmd_wholestate"),
            InlineKeyboardButton("💬 Leave Feedback", callback_data="cmd_feedback")
        ],
        [
            InlineKeyboardButton("💳 VIP Plans", callback_data="cmd_plans"),
            InlineKeyboardButton("📢 Telegram Channel", url="https://t.me/Learnwithhim")
        ],
        [
            InlineKeyboardButton("🚀 Attempt Another Quiz", callback_data="cmd_quiz")
        ]
    ]

    await context.bot.send_message(
        chat_id=chat_id, 
        text=report_card, 
        reply_markup=InlineKeyboardMarkup(end_quiz_buttons), 
        parse_mode="Markdown"
    )

    asyncio.create_task(asyncio.to_thread(
        record_quiz_result,
        user_id,
        session.get("topic", "computer_awareness_mock"),
        score,
        total,
        correct,
        wrong,
        skipped,
        0,
        detailed_logs
    ))