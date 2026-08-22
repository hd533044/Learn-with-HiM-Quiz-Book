import asyncio
import logging
import time
import os
import json
from telegram import Update, Poll, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from app.config import DAILY_QUESTION_LIMIT, PRIMARY_ADMIN_ID
from app.database import (
    get_today_attempts, mark_questions_as_seen, record_quiz_result, 
    get_ist_timestamp_str, get_user_profile, get_maintenance_until,
    save_paused_quiz_state, get_paused_quiz_state, clear_paused_quiz_state,
    save_question_to_db, log_user_activity_time, get_next_mock_number,
    record_quiz_like, get_total_platform_likes
)
from app.pyq_fetcher import (
    fetch_pyqs_for_quiz, get_available_topics, COMPUTER_TOPIC_METADATA, GK_TOPIC_METADATA,
    ENGLISH_TOPIC_METADATA, fetch_full_mock_questions, fetch_multi_topic_questions,
    fetch_english_full_mock_25, fetch_rc_or_cloze_passage_questions
)
from app.stats import calculate_user_percentile, calculate_user_rank, get_quiz_performance_trend

logger = logging.getLogger(__name__)

ACTIVE_SESSIONS = {}
POLL_MAP = {}
TIMER_TASKS = {}
QUIZ_SETUP_CACHE = {}

COMBINED_PRESETS = [
    ("10 Qs + 15s", 10, 15),
    ("15 Qs + 20s", 15, 20),
    ("20 Qs + 20s", 20, 20),
    ("25 Qs + 25s", 25, 25),
    ("30 Qs + 25s", 30, 25),
    ("40 Qs + 30s", 40, 30)
]


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
            InlineKeyboardButton("🎯 Attempted Qs", callback_data="cmd_attempted_qs")
        ],
        [
            InlineKeyboardButton("💾 Saved Qs", callback_data="cmd_savedquestions"),
            InlineKeyboardButton("📄 PDF Report", callback_data="cmd_pdfreport")
        ],
        [
            InlineKeyboardButton("🏆 Leaderboard", callback_data="cmd_toppers"),
            InlineKeyboardButton("📊 My Analytics", callback_data="cmd_wholestate")
        ],
        [
            InlineKeyboardButton("💳 VIP Plans", callback_data="cmd_plans"),
            InlineKeyboardButton("🚀 Launch Quiz", callback_data="cmd_quiz")
        ]
    ])


async def check_quiz_maintenance(update: Update) -> bool:
    m_until = await asyncio.to_thread(get_maintenance_until)
    if int(time.time()) < m_until:
        remaining_sec = m_until - int(time.time())
        mins_left = max(1, (remaining_sec + 59) // 60)
        msg = f"🛠 **ADMIN HAS TEMPORARILY PAUSED SERVICES** 🛠\n\n⏰ Resuming in approx `{mins_left} mins`. Please check back shortly!"
        if update.callback_query:
            await update.callback_query.answer(f"🛠 Services Paused (~{mins_left}m left)", show_alert=True)
        elif update.message:
            await update.message.reply_text(msg, parse_mode="Markdown")
        return False
    return True


async def launch_quiz_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /quiz: Clean subject selection with Computer at top, English 2nd, GK 3rd."""
    if not await check_quiz_maintenance(update): return

    user = update.effective_user
    user_id = user.id
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user_id, 10))
    profile = await asyncio.to_thread(get_user_profile, user_id)
    
    if not profile or not profile.get("is_verified"):
        msg = "⚠️ Please type /start to complete your profile before attempting quizzes!"
        if update.callback_query:
            await update.callback_query.message.reply_text(msg)
        else:
            await update.message.reply_text(msg)
        return

    attempted_today = await asyncio.to_thread(get_today_attempts, user_id)
    paid_bal = profile.get("paid_question_balance", 0) or 0
    base_limit = max(DAILY_QUESTION_LIMIT, paid_bal)
    allowed_limit = 10000 if user_id == PRIMARY_ADMIN_ID else base_limit + profile.get("bonus_quota", 0)

    if attempted_today >= allowed_limit:
        exhausted_msg = (
            f"🛑 **DAILY LIMIT REACHED!** 🛑\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 **Used Today:** `{attempted_today}` / `{allowed_limit}` Questions\n\n"
            f"💳 Upgrade to a VIP Pack to unlock higher daily question limits!"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 View VIP Plans", callback_data="cmd_plans")],
            [InlineKeyboardButton("🤝 Invite Friends (+10 Qs)", callback_data="cmd_referral")]
        ])
        if update.callback_query:
            await update.callback_query.answer("🛑 Daily Limit Exhausted!", show_alert=True)
            await update.callback_query.message.reply_text(exhausted_msg, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await update.message.reply_text(exhausted_msg, reply_markup=keyboard, parse_mode="Markdown")
        return

    paused = await asyncio.to_thread(get_paused_quiz_state, user_id)
    if paused:
        remaining_count = len(paused.get('questions', [])) - paused.get('current_index', 0)
        topic_disp = paused.get('topic_name') or "Practice Session"
        text = (
            f"⏸ **PAUSED QUIZ IN PROGRESS** ⏸\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📖 **Subject/Topic:** `{topic_disp}`\n"
            f"📊 **Remaining:** `{remaining_count}` Questions\n"
            f"⭐ **Current Score:** `{paused.get('score', 0.0)}`\n\n"
            f"Would you like to resume your session?"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ Resume Quiz", callback_data="cmd_resume_quiz")],
            [InlineKeyboardButton("🔄 Start New Quiz", callback_data="cmd_start_fresh_quiz")]
        ])
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
        return

    # Clean Subject Selection: Computer top, English 2nd, GK 3rd
    subject_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🖥️ 1. Computer Awareness", callback_data="qsubj_computer")],
        [InlineKeyboardButton("🔤 2. English Language", callback_data="qsubj_english")],
        [InlineKeyboardButton("🌍 3. General Knowledge (GK)", callback_data="qsubj_gk")]
    ])

    msg_text = "🎯 **SELECT YOUR SUBJECT FOR QUIZ:**"

    if update.callback_query:
        await update.callback_query.edit_message_text(msg_text, reply_markup=subject_keyboard, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg_text, reply_markup=subject_keyboard, parse_mode="Markdown")


async def quiz_subject_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_quiz_maintenance(update): return
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    subj = query.data.replace("qsubj_", "")

    QUIZ_SETUP_CACHE[user_id] = {"subject": subj}

    if subj == "english":
        # Specific English Flow
        eng_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📌 Grammar", callback_data="qeng_cat_grammar")],
            [InlineKeyboardButton("💡 Vocabulary", callback_data="qeng_cat_vocab")],
            [InlineKeyboardButton("📖 Comprehension", callback_data="qeng_cat_comprehension")],
            [InlineKeyboardButton("🏆 Full Mock (25 Qs - 10 Min Exam)", callback_data="qeng_full_mock")],
            [InlineKeyboardButton("🔙 Back to Subjects", callback_data="cmd_quiz")]
        ])
        await query.edit_message_text(
            "🔤 **ENGLISH LANGUAGE**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nSelect a practice category below:",
            reply_markup=eng_keyboard,
            parse_mode="Markdown"
        )
        return

    # Standard Subject Flow for Computer / GK: Chapter-wise vs Mixed
    subj_title = "Computer Awareness" if subj == "computer" else "General Knowledge (GK)"
    mode_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Chapter-wise Practice", callback_data=f"qsubopt_chapter_{subj}")],
        [InlineKeyboardButton("🔀 Mixed Practice", callback_data=f"qsubopt_mixed_{subj}")],
        [InlineKeyboardButton("🔙 Back to Subjects", callback_data="cmd_quiz")]
    ])
    await query.edit_message_text(
        f"📖 **{subj_title.upper()}**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nChoose practice type:",
        reply_markup=mode_keyboard,
        parse_mode="Markdown"
    )


async def english_category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_quiz_maintenance(update): return
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    cat = query.data.replace("qeng_cat_", "")

    QUIZ_SETUP_CACHE.setdefault(user_id, {})["subject"] = "english"
    QUIZ_SETUP_CACHE[user_id]["eng_cat"] = cat

    cat_titles = {"grammar": "Grammar", "vocab": "Vocabulary", "comprehension": "Comprehension"}
    mode_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Topic-wise Practice", callback_data=f"qengopt_chapter_{cat}")],
        [InlineKeyboardButton("🔀 Full Practice", callback_data=f"qengopt_mixed_{cat}")],
        [InlineKeyboardButton("🔙 Back", callback_data="qsubj_english")]
    ])
    await query.edit_message_text(
        f"🔤 **ENGLISH — {cat_titles.get(cat, cat).upper()}**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nChoose practice type:",
        reply_markup=mode_keyboard,
        parse_mode="Markdown"
    )


async def english_full_mock_launch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_quiz_maintenance(update): return
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    profile = await asyncio.to_thread(get_user_profile, user_id)
    attempted_today = await asyncio.to_thread(get_today_attempts, user_id)
    paid_bal = profile.get("paid_question_balance", 0) or 0 if profile else 0
    base_limit = max(DAILY_QUESTION_LIMIT, paid_bal)
    allowed_limit = 10000 if user_id == PRIMARY_ADMIN_ID else base_limit + (profile.get("bonus_quota", 0) if profile else 0)
    remaining_quota = allowed_limit - attempted_today

    if remaining_quota < 25:
        await query.edit_message_text(
            f"🛑 You have `{remaining_quota}` questions left today. English Full Mock requires 25 questions.",
            reply_markup=get_quizbook_nav_keyboard(),
            parse_mode="Markdown"
        )
        return

    questions = await asyncio.to_thread(fetch_english_full_mock_25, "en", user_id)
    mock_number = await asyncio.to_thread(get_next_mock_number, user_id, "ENGLISH_FULL_MOCK")
    
    await start_quiz_session(
        query, context, user_id, questions, 
        timer_sec=24, quiz_mode="ENGLISH_FULL_MOCK", 
        mock_number=mock_number, subject="english", 
        topic="FULL_MOCK_25", topic_name=f"English Full Mock #{mock_number}", 
        language="en", total_time_mins=10
    )


async def sub_option_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_quiz_maintenance(update): return
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    parts = query.data.split("_")
    opt_type = parts[1]
    param = parts[2]

    if opt_type == "mixed":
        QUIZ_SETUP_CACHE.setdefault(user_id, {})
        QUIZ_SETUP_CACHE[user_id]["topic"] = "MIXED"
        QUIZ_SETUP_CACHE[user_id]["topic_name"] = "Mixed Practice"
        subj = QUIZ_SETUP_CACHE[user_id].get("subject", param)
        if subj == "english":
            QUIZ_SETUP_CACHE[user_id]["language"] = "en"
            await show_combined_timer_selection(query, user_id)
        else:
            await show_language_selection(query, user_id)
    elif opt_type == "chapter":
        subj = param
        topics_list = get_available_topics(subject=subj, language="en")
        keyboard = []
        for t_key, t_display in topics_list:
            keyboard.append([InlineKeyboardButton(t_display, callback_data=f"qtopic_{t_key}")])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data=f"qsubj_{subj}")])
        await query.edit_message_text("📂 **SELECT TOPIC / CHAPTER:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def english_option_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_quiz_maintenance(update): return
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    parts = query.data.split("_")
    opt_type = parts[1]
    cat = parts[2]

    QUIZ_SETUP_CACHE.setdefault(user_id, {})["subject"] = "english"
    QUIZ_SETUP_CACHE[user_id]["language"] = "en"

    if opt_type == "mixed":
        QUIZ_SETUP_CACHE[user_id]["topic"] = "MIXED"
        QUIZ_SETUP_CACHE[user_id]["topic_name"] = f"{cat.capitalize()} Practice"
        await show_combined_timer_selection(query, user_id)
    elif opt_type == "chapter":
        all_eng_topics = get_available_topics(subject="english", language="en")
        filtered = []
        for t_key, t_disp in all_eng_topics:
            info = ENGLISH_TOPIC_METADATA.get(t_key, {})
            if info.get("section") == cat:
                filtered.append((t_key, t_disp))

        keyboard = []
        for t_key, t_disp in filtered:
            keyboard.append([InlineKeyboardButton(t_disp, callback_data=f"qtopic_{t_key}")])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="qsubj_english")])

        await query.edit_message_text(
            f"📂 **SELECT {cat.upper()} TOPIC:**",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )


async def quiz_topic_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_quiz_maintenance(update): return
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    topic_key = query.data.replace("qtopic_", "")

    current_cache = QUIZ_SETUP_CACHE.get(user_id, {})
    subj = current_cache.get("subject", "computer")

    if subj == "english":
        metadata = ENGLISH_TOPIC_METADATA
    elif subj == "gk":
        metadata = GK_TOPIC_METADATA
    else:
        metadata = COMPUTER_TOPIC_METADATA

    t_info = metadata.get(topic_key, {})
    topic_display = t_info.get("en", topic_key.replace("_", " "))

    current_cache["topic"] = topic_key
    current_cache["topic_name"] = topic_display
    QUIZ_SETUP_CACHE[user_id] = current_cache

    if subj == "english":
        current_cache["language"] = "en"
        await show_combined_timer_selection(query, user_id)
    else:
        await show_language_selection(query, user_id)


async def show_language_selection(query, user_id: int):
    current_cache = QUIZ_SETUP_CACHE.get(user_id, {})
    topic_name = current_cache.get("topic_name", "Practice Session")
    subj = current_cache.get("subject", "computer")

    lang_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 English", callback_data="qlang_en"), InlineKeyboardButton("🇮🇳 हिंदी (Hindi)", callback_data="qlang_hi")],
        [InlineKeyboardButton("🔙 Back", callback_data=f"qsubj_{subj}")]
    ])
    msg_text = (
        f"🌐 **SELECT LANGUAGE**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 **Topic:** `{topic_name}`\n\n"
        f"Choose question language:"
    )
    await query.edit_message_text(msg_text, reply_markup=lang_keyboard, parse_mode="Markdown")


async def quiz_language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_quiz_maintenance(update): return
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    lang = query.data.replace("qlang_", "")

    QUIZ_SETUP_CACHE.setdefault(user_id, {})["language"] = lang
    await show_combined_timer_selection(query, user_id)


async def show_combined_timer_selection(query, user_id: int):
    """Combined Question Number + Timer selection step."""
    current_cache = QUIZ_SETUP_CACHE.get(user_id, {})
    topic_name = current_cache.get("topic_name", "Practice Session")
    profile = await asyncio.to_thread(get_user_profile, user_id)
    attempted_today = await asyncio.to_thread(get_today_attempts, user_id)
    paid_bal = profile.get("paid_question_balance", 0) or 0 if profile else 0
    base_limit = max(DAILY_QUESTION_LIMIT, paid_bal)
    allowed_limit = 10000 if user_id == PRIMARY_ADMIN_ID else base_limit + (profile.get("bonus_quota", 0) if profile else 0)
    remaining_quota = max(1, allowed_limit - attempted_today)

    keyboard = []
    row = []
    for label, q_num, sec in COMBINED_PRESETS:
        if q_num <= remaining_quota:
            row.append(InlineKeyboardButton(f"⚡ {label}", callback_data=f"qcombo_{q_num}_{sec}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    if not keyboard:
        keyboard.append([InlineKeyboardButton(f"⚡ {remaining_quota} Qs + 20s", callback_data=f"qcombo_{remaining_quota}_20")])

    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="cmd_quiz")])

    msg = (
        f"⏱ **SELECT QUESTIONS & TIMER**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 **Target:** `{topic_name}`\n"
        f"⚡ **Available Limit:** `{remaining_quota}` Questions\n\n"
        f"Select your question bundle & timer below:"
    )
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def combined_timer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_quiz_maintenance(update): return
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    parts = query.data.replace("qcombo_", "").split("_")
    count = int(parts[0])
    timer_sec = int(parts[1])

    setup = QUIZ_SETUP_CACHE.pop(user_id, {})
    language = setup.get("language", "en")
    subject = setup.get("subject", "computer")
    topic = setup.get("topic", "MIXED")
    topic_name = setup.get("topic_name", "Practice Session")

    questions = await asyncio.to_thread(fetch_pyqs_for_quiz, count, None, language, user_id, topic, subject)
    await start_quiz_session(query, context, user_id, questions, timer_sec, "PRACTICE", 0, subject, topic, topic_name, language)


async def start_quiz_session(query, context, user_id, questions, timer_sec, quiz_mode, mock_number, subject, topic, topic_name, language, selected_topics=None, total_time_mins=None):
    if not questions:
        await query.edit_message_text("⚠️ No questions found for this topic. Please try another selection.", reply_markup=get_quizbook_nav_keyboard(), parse_mode="Markdown")
        return
    
    chat_id = query.message.chat_id
    q_ids = [q["id"] for q in questions if q.get("id") is not None]
    asyncio.create_task(asyncio.to_thread(mark_questions_as_seen, user_id, q_ids))

    session = {
        "user_id": user_id,
        "chat_id": chat_id,
        "questions": questions,
        "language": language,
        "subject": subject,
        "topic": topic,
        "topic_name": topic_name,
        "current_index": 0,
        "score": 0.0,
        "correct": 0,
        "wrong": 0,
        "skipped": 0,
        "total": len(questions),
        "timer_sec": max(5, int(timer_sec)),
        "is_paused": False,
        "start_time": get_ist_timestamp_str(),
        "detailed_logs": [],
        "quiz_mode": quiz_mode,
        "mock_number": mock_number,
        "selected_topics": selected_topics,
        "global_remaining_sec": (total_time_mins * 60) if total_time_mins else None,
        "question_start_time": time.time(),
        "last_poll_message_id": None
    }
    ACTIVE_SESSIONS[user_id] = session

    lang_str = "🌐 English" if language == "en" else "🇮🇳 हिंदी"
    title = f"{quiz_mode.replace('_', ' ').title()} #{mock_number}" if quiz_mode != "PRACTICE" else topic_name

    early_submit_markup = None
    if quiz_mode == "ENGLISH_FULL_MOCK":
        early_submit_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🏁 Submit Mock Early", callback_data="cmd_stop_quiz")]])

    await query.edit_message_text(
        f"🚀 **PRACTICE SESSION STARTED**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📖 **Title:** `{title}`\n"
        f"🌐 **Language:** `{lang_str}`\n"
        f"⚡ All questions will appear as instant interactive polls.",
        reply_markup=early_submit_markup,
        parse_mode="Markdown"
    )

    if topic in ["eng_comp_rc", "eng_comp_cloze_test"] and questions[0].get("passage"):
        passage_content = questions[0].get("passage")
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"📖 **READING PASSAGE / CONTEXT:**\n\n{passage_content}",
                parse_mode=None
            )
        except Exception:
            pass

    await send_next_question(chat_id, user_id, context)


async def send_next_question(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    m_until = await asyncio.to_thread(get_maintenance_until)
    if int(time.time()) < m_until:
        await context.bot.send_message(chat_id=chat_id, text="🛠 **Services temporarily paused by Admin.**")
        return

    session = ACTIVE_SESSIONS.get(user_id)
    if not session or session.get("is_paused"):
        return

    if session["current_index"] >= len(session["questions"]):
        await finish_quiz_and_send_report(chat_id, user_id, context)
        return

    q = session["questions"][session["current_index"]]
    session["current_question"] = q
    
    global_time_str = ""
    if session.get("global_remaining_sec") is not None:
        rem_sec = int(session["global_remaining_sec"])
        if rem_sec <= 0:
            await context.bot.send_message(chat_id=chat_id, text="⏰ **10-MINUTE TIMER EXPIRED!** Auto-submitting mock...", parse_mode="Markdown")
            await finish_quiz_and_send_report(chat_id, user_id, context)
            return
        m, s = divmod(rem_sec, 60)
        global_time_str = f" | ⏳ {m:02d}:{s:02d} Left"
        poll_timer_sec = min(rem_sec, session["timer_sec"])
        poll_timer_sec = max(5, poll_timer_sec)
    else:
        poll_timer_sec = session["timer_sec"]

    session["question_start_time"] = time.time()
    current_num = session["current_index"] + 1
    total_num = session["total"]
    
    quiz_mode = session.get("quiz_mode", "PRACTICE")
    title = f"{quiz_mode.replace('_', ' ').title()} #{session.get('mock_number', 0)}" if quiz_mode != "PRACTICE" else session.get('topic_name', 'Quiz')
    
    base_header = f"📖 [{title}] — ({current_num}/{total_num}){global_time_str}\n\n"
    avail_len = 300 - len(base_header)
    q_text = q['question']
    if len(q_text) > avail_len:
        q_text = q_text[:max(0, avail_len-3)] + "..."
    header_text = base_header + q_text

    clean_opts = [str(opt)[:97] for opt in q["options"]]
    correct_id = q.get("correct_option", 0)

    try:
        poll_msg = await context.bot.send_poll(
            chat_id=chat_id,
            question=header_text,
            options=clean_opts,
            type=Poll.QUIZ,
            correct_option_id=correct_id,
            explanation=str(q.get("explanation", ""))[:190] if q.get("explanation") else None,
            allows_multiple_answers=False,
            is_anonymous=False,
            open_period=poll_timer_sec
        )
        
        poll_id = poll_msg.poll.id
        session["last_poll_message_id"] = poll_msg.message_id
        
        POLL_MAP[poll_id] = {
            "user_id": user_id, 
            "chat_id": chat_id, 
            "poll_message_id": poll_msg.message_id,
            "q_idx": session["current_index"], 
            "correct_id": correct_id,
            "q_data": q
        }

        await context.bot.send_message(
            chat_id=chat_id,
            text="⚡ *Controls:*",
            reply_markup=get_pause_resume_keyboard(),
            parse_mode="Markdown"
        )

        if user_id in TIMER_TASKS and not TIMER_TASKS[user_id].done():
            TIMER_TASKS[user_id].cancel()

        TIMER_TASKS[user_id] = asyncio.create_task(auto_skip_task(chat_id, user_id, poll_id, session["current_index"], poll_timer_sec, context))
    except Exception as e:
        logger.error(f"Error sending quiz poll: {e}")
        session["skipped"] += 1
        session["current_index"] += 1
        await send_next_question(chat_id, user_id, context)


async def auto_skip_task(chat_id: int, user_id: int, poll_id: str, expected_idx: int, timer_sec: int, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(timer_sec + 1)
    if poll_id in POLL_MAP:
        data = POLL_MAP.pop(poll_id, None)
        session = ACTIVE_SESSIONS.get(user_id)
        if session and not session.get("is_paused") and session["current_index"] == expected_idx:
            if session.get("global_remaining_sec") is not None:
                time_spent = time.time() - session.get("question_start_time", time.time())
                session["global_remaining_sec"] -= time_spent

            q = data.get("q_data", {})
            opts = q.get("options", [])
            c_idx = data.get("correct_id", 0)
            c_ans_text = opts[c_idx] if 0 <= c_idx < len(opts) else "N/A"

            session.setdefault("detailed_logs", []).append({
                "question_id": q.get("id"),
                "question_text": q.get("question"),
                "options": opts,
                "explanation": q.get("explanation", ""),
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
    if not answer: return
        
    poll_id = answer.poll_id
    if poll_id not in POLL_MAP: return

    data = POLL_MAP.pop(poll_id)
    user_id = data["user_id"]
    chat_id = data["chat_id"]
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user_id, 10))

    if user_id in TIMER_TASKS and not TIMER_TASKS[user_id].done():
        TIMER_TASKS[user_id].cancel()

    session = ACTIVE_SESSIONS.get(user_id)
    if session and not session.get("is_paused") and session["current_index"] == data["q_idx"]:
        if session.get("global_remaining_sec") is not None:
            time_spent = time.time() - session.get("question_start_time", time.time())
            session["global_remaining_sec"] -= time_spent

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
            "options": opts,
            "explanation": q.get("explanation", ""),
            "status": status,
            "selected_option": selected,
            "correct_option": correct_id,
            "correct_answer_text": c_ans_text,
            "timestamp": get_ist_timestamp_str()
        })

        session["current_index"] += 1
        await send_next_question(chat_id, user_id, context)


async def save_question_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    session = ACTIVE_SESSIONS.get(user_id)
    
    if not session or "current_question" not in session:
        await query.answer("⚠️ No active question to bookmark!", show_alert=True)
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
        await query.answer("💾 Question bookmarked!", show_alert=True)
    else:
        await query.answer("ℹ️ Question already bookmarked.", show_alert=True)


async def pause_quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

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
        "subject": session.get("subject", "english"),
        "topic": session.get("topic", "MIXED"),
        "topic_name": session.get("topic_name", "Practice Session"),
        "current_index": session["current_index"],
        "score": session["score"],
        "correct": session["correct"],
        "wrong": session["wrong"],
        "skipped": session["skipped"],
        "total": session["total"],
        "timer_sec": session["timer_sec"],
        "start_time": session["start_time"],
        "detailed_logs": session.get("detailed_logs", []),
        "quiz_mode": session.get("quiz_mode", "PRACTICE"),
        "mock_number": session.get("mock_number", 0),
        "global_remaining_sec": session.get("global_remaining_sec")
    }
    asyncio.create_task(asyncio.to_thread(save_paused_quiz_state, user_id, save_state))
    ACTIVE_SESSIONS.pop(user_id, None)

    remaining_qs = session["total"] - session["current_index"]
    msg = (
        f"⏸ **QUIZ PAUSED & SAVED**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 **Remaining:** `{remaining_qs}` Qs | ⭐ **Score:** `{session['score']}`\n\n"
        f"Tap **Resume** below or type `/resume` anytime to continue."
    )
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Resume Quiz", callback_data="cmd_resume_quiz")]])

    if update.callback_query:
        await update.callback_query.answer("⏸ Quiz Paused!", show_alert=True)
        await context.bot.send_message(chat_id=chat_id, text=msg, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, reply_markup=keyboard, parse_mode="Markdown")


async def resume_quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    paused = await asyncio.to_thread(get_paused_quiz_state, user_id)
    if not paused:
        msg = "ℹ️ No paused quiz found. Type /quiz to start fresh!"
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
        "subject": paused.get("subject", "english"),
        "topic": paused.get("topic", "MIXED"),
        "topic_name": paused.get("topic_name", "Practice Session"),
        "current_index": paused.get("current_index", 0),
        "score": paused["score"],
        "correct": paused["correct"],
        "wrong": paused["wrong"],
        "skipped": paused["skipped"],
        "total": paused["total"],
        "timer_sec": paused["timer_sec"],
        "is_paused": False,
        "start_time": paused["start_time"],
        "detailed_logs": paused.get("detailed_logs", []),
        "quiz_mode": paused.get("quiz_mode", "PRACTICE"),
        "mock_number": paused.get("mock_number", 0),
        "global_remaining_sec": paused.get("global_remaining_sec"),
        "question_start_time": time.time(),
        "last_poll_message_id": None
    }
    ACTIVE_SESSIONS[user_id] = session

    if update.callback_query:
        await update.callback_query.answer()

    await context.bot.send_message(chat_id=chat_id, text="▶️ **Resuming quiz session...**", parse_mode="Markdown")
    await send_next_question(chat_id, user_id, context)


async def stop_quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    session = ACTIVE_SESSIONS.pop(user_id, None)
    paused = await asyncio.to_thread(get_paused_quiz_state, user_id)
    
    if not session and not paused:
        msg = "ℹ️ No active quiz session found to stop."
        if update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        else:
            await update.message.reply_text(msg, reply_markup=get_quizbook_nav_keyboard(), parse_mode="Markdown")
        return

    asyncio.create_task(asyncio.to_thread(clear_paused_quiz_state, user_id))
    if user_id in TIMER_TASKS and not TIMER_TASKS[user_id].done():
        TIMER_TASKS[user_id].cancel()

    if session and session["current_index"] > 0:
        asyncio.create_task(asyncio.to_thread(
            record_quiz_result,
            user_id=user_id,
            quiz_id=session.get("topic", "mixed"),
            score=session["score"],
            total_questions=session["total"],
            correct_count=session["correct"],
            wrong_count=session["wrong"],
            skipped_count=session["skipped"],
            time_taken=0,
            question_details=session.get("detailed_logs", []),
            quiz_mode=session.get("quiz_mode", "PRACTICE"),
            mock_number=session.get("mock_number", 0),
            subject=session.get("subject", "english")
        ))

    msg = "🛑 **Quiz session closed.** Unattempted questions remain in your daily balance."

    if update.callback_query:
        await update.callback_query.answer("🛑 Quiz Stopped!", show_alert=True)
        await context.bot.send_message(chat_id=chat_id, text=msg, reply_markup=get_quizbook_nav_keyboard(), parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, reply_markup=get_quizbook_nav_keyboard(), parse_mode="Markdown")


async def finish_quiz_and_send_report(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    session = ACTIVE_SESSIONS.pop(user_id, None)
    if not session: return

    total = session["total"]
    correct = session["correct"]
    wrong = session["wrong"]
    skipped = session["skipped"]
    score = session["score"]
    detailed_logs = session.get("detailed_logs", [])
    lang = session.get("language", "en")
    quiz_mode = session.get("quiz_mode", "PRACTICE")
    title = f"{quiz_mode.replace('_', ' ').title()} #{session.get('mock_number', 0)}" if quiz_mode != "PRACTICE" else session.get('topic_name', 'Quiz')

    attempt_id = await asyncio.to_thread(
        record_quiz_result,
        user_id=user_id,
        quiz_id=session.get("topic", "mixed"),
        score=score,
        total_questions=total,
        correct_count=correct,
        wrong_count=wrong,
        skipped_count=skipped,
        time_taken=0,
        question_details=detailed_logs,
        quiz_mode=quiz_mode,
        mock_number=session.get("mock_number", 0),
        subject=session.get("subject", "english")
    )

    # Award 1 like for completing a quiz attempt
    await asyncio.to_thread(record_quiz_like, user_id, attempt_id)

    current_acc = round((correct / total) * 100.0, 2) if total > 0 else 0.0
    rank = await asyncio.to_thread(calculate_user_rank, user_id)
    percentile = await asyncio.to_thread(calculate_user_percentile, user_id)
    trend_info = await asyncio.to_thread(get_quiz_performance_trend, user_id, current_acc)
    total_likes = await asyncio.to_thread(get_total_platform_likes)

    lang_label = "🌐 English" if lang == "en" else "🇮🇳 हिंदी"

    report_card = (
        f"🏆 **OFFICIAL QUIZ REPORT CARD** 🏆\n"
        f"📚 **Quiz with HiM by Himanshu Sir**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📖 **Title:** `{title}`\n"
        f"🌐 **Language:** `{lang_label}`\n\n"
        f"📊 **PERFORMANCE SUMMARY:**\n"
        f"• **Questions Attempted:** `{total}` Qs\n"
        f"• **Correct:** `{correct}` ✅ | **Wrong:** `{wrong}` ❌ | **Skipped:** `{skipped}` ⏭\n"
        f"• **Accuracy:** `{current_acc}%` ⭐\n\n"
        f"🎖️ **GLOBAL STANDING:**\n"
        f"• **Global Rank:** `{rank}` 🥇 | **Percentile:** `{percentile}%` 📊\n"
        f"• **Platform Likes Earned:** `+1 Like Added!` (❤️ Total: `{total_likes}`)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    end_quiz_buttons = [
        [InlineKeyboardButton("📥 Download Detailed PDF Review", callback_data=f"dl_single_quiz_pdf_{attempt_id}")],
        [InlineKeyboardButton(f"❤️ Platform Likes ({total_likes})", callback_data="cmd_like_info")],
        [InlineKeyboardButton("❌ Review Wrong Qs", callback_data="cmd_wrong_qs"), InlineKeyboardButton("🎯 Attempted Qs", callback_data="cmd_attempted_qs")],
        [InlineKeyboardButton("📄 PDF Reports Center", callback_data="cmd_pdfreport"), InlineKeyboardButton("💾 Bookmarks", callback_data="cmd_savedquestions")],
        [InlineKeyboardButton("🏆 Leaderboard", callback_data="cmd_toppers"), InlineKeyboardButton("📊 Analytics", callback_data="cmd_wholestate")],
        [InlineKeyboardButton("🚀 Launch Next Quiz", callback_data="cmd_quiz"), InlineKeyboardButton("💳 VIP Plans", callback_data="cmd_plans")]
    ]

    await context.bot.send_message(
        chat_id=chat_id, 
        text=report_card, 
        reply_markup=InlineKeyboardMarkup(end_quiz_buttons), 
        parse_mode="Markdown"
    )


async def quiz_extended_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    if data.startswith("qsubj_"):
        await quiz_subject_callback(update, context)
    elif data.startswith("qeng_cat_"):
        await english_category_callback(update, context)
    elif data == "qeng_full_mock":
        await english_full_mock_launch(update, context)
    elif data.startswith("qsubopt_"):
        await sub_option_callback(update, context)
    elif data.startswith("qengopt_"):
        await english_option_callback(update, context)
    elif data.startswith("qtopic_"):
        await quiz_topic_callback(update, context)
    elif data.startswith("qlang_"):
        await quiz_language_callback(update, context)
    elif data.startswith("qcombo_"):
        await combined_timer_callback(update, context)