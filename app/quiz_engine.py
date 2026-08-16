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
    save_question_to_db, log_user_activity_time, get_next_mock_number
)
from app.pyq_fetcher import (
    fetch_pyqs_for_quiz, get_available_topics, COMPUTER_TOPIC_METADATA, GK_TOPIC_METADATA,
    fetch_full_mock_questions, fetch_multi_topic_questions
)
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
    """Step 1: Main Game Mode Selection Panel."""
    if not await check_quiz_maintenance(update): return

    user = update.effective_user
    user_id = user.id
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user_id, 10))
    profile = await asyncio.to_thread(get_user_profile, user_id)
    
    if not profile or not profile.get("is_verified"):
        await update.message.reply_text("⚠️ Please type /start to complete registration before attempting quizzes!")
        return

    attempted_today = await asyncio.to_thread(get_today_attempts, user_id)
    
    paid_bal = profile.get("paid_question_balance", 0) or 0
    base_limit = max(DAILY_QUESTION_LIMIT, paid_bal)
    allowed_limit = 10000 if user_id == PRIMARY_ADMIN_ID else base_limit + profile.get("bonus_quota", 0)

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

    paused = await asyncio.to_thread(get_paused_quiz_state, user_id)
    if paused:
        remaining_count = len(paused.get('questions', [])) - paused.get('current_index', 0)
        topic_disp = paused.get('topic_name') or "Practice Session"
        text = (
            f"⏸ **PAUSED QUIZ SESSION FOUND!** ⏸\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📖 **Title:** `{topic_disp}`\n"
            f"📊 **Remaining Questions:** `{remaining_count}` Qs\n"
            f"⭐ **Current Score:** `{paused.get('score', 0.0)}`\n\n"
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

    mode_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔀 Normal Quiz Mode", callback_data="qflow_NORMAL")],
        [InlineKeyboardButton("🏆 Full Mock Mode", callback_data="qflow_MOCK")],
        [InlineKeyboardButton("📚 Sectional Mode", callback_data="qflow_SECT")],
        [InlineKeyboardButton("📑 Topic-Wise Sectional", callback_data="qflow_TOPSECT")]
    ])

    msg_text = (
        f"📚 **QUIZ WITH HIM — SELECT MODE** 📚\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Please select your Quiz Mode to begin:"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(msg_text, reply_markup=mode_keyboard, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg_text, reply_markup=mode_keyboard, parse_mode="Markdown")


# ==========================================
# FLOW ROUTING FOR ALL MODES
# ==========================================

async def quiz_flow_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    flow = query.data.replace("qflow_", "")

    QUIZ_SETUP_CACHE[user_id] = {"flow": flow}

    if flow == "NORMAL":
        subject_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🖥️ Computer Awareness", callback_data="qsubj_computer")],
            [InlineKeyboardButton("🌍 General Knowledge (GK)", callback_data="qsubj_gk")],
            [InlineKeyboardButton("🔙 Back", callback_data="cmd_quiz")]
        ])
        await query.edit_message_text("📚 **NORMAL QUIZ MODE**\nSelect Subject:", reply_markup=subject_keyboard, parse_mode="Markdown")

    elif flow == "MOCK":
        profile = await asyncio.to_thread(get_user_profile, user_id)
        paid_bal = profile.get("paid_question_balance", 0) or 0
        if paid_bal <= 20 and user_id != PRIMARY_ADMIN_ID:
            await query.edit_message_text(
                "🛑 **PREMIUM FEATURE ONLY** 🛑\n\nFull Mock Mode is highly intensive and strictly reserved for VIP members. Free/Demo limits do not apply here.\n\nPlease upgrade your plan to unlock.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💳 View VIP Plans", callback_data="cmd_plans"), InlineKeyboardButton("🔙 Back", callback_data="cmd_quiz")]])
            )
            return
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 20 of Each (40 Total)", callback_data="qmockcount_40")],
            [InlineKeyboardButton("📝 25 of Each (50 Total)", callback_data="qmockcount_50")],
            [InlineKeyboardButton("📝 30 of Each (60 Total)", callback_data="qmockcount_60")],
            [InlineKeyboardButton("🔙 Back", callback_data="cmd_quiz")]
        ])
        await query.edit_message_text("🏆 **FULL MOCK MODE**\nIncludes equal mix of Computer & GK.\nSelect total number of questions:", reply_markup=keyboard)

    elif flow in ["SECT", "TOPSECT"]:
        subject_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🖥️ Computer Awareness", callback_data="qsectsubj_computer")],
            [InlineKeyboardButton("🌍 General Knowledge (GK)", callback_data="qsectsubj_gk")],
            [InlineKeyboardButton("🔙 Back", callback_data="cmd_quiz")]
        ])
        title = "SECTIONAL MODE" if flow == "SECT" else "TOPIC-WISE SECTIONAL"
        await query.edit_message_text(f"📚 **{title}**\nSelect Subject:", reply_markup=subject_keyboard, parse_mode="Markdown")


# ==========================================
# FULL MOCK MODE HANDLERS
# ==========================================

async def mock_count_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    count = int(query.data.replace("qmockcount_", ""))
    QUIZ_SETUP_CACHE[user_id]["count"] = count
    
    # HARDCODED: Automatically set global timer to 10 minutes
    QUIZ_SETUP_CACHE[user_id]["total_time_mins"] = 10

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 English", callback_data="qmocklang_en"), InlineKeyboardButton("🇮🇳 हिंदी", callback_data="qmocklang_hi")],
        [InlineKeyboardButton("🔙 Back", callback_data="qflow_MOCK")]
    ])
    await query.edit_message_text("🌐 **Select Language**:", reply_markup=keyboard, parse_mode="Markdown")

async def mock_lang_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    lang = query.data.replace("qmocklang_", "")
    setup = QUIZ_SETUP_CACHE.pop(user_id, {})
    
    count = setup.get("count", 40)
    total_time_mins = setup.get("total_time_mins", 10)
    timer_sec = (total_time_mins * 60) // count # Fallback parameter

    profile = await asyncio.to_thread(get_user_profile, user_id)
    attempted_today = await asyncio.to_thread(get_today_attempts, user_id)
    paid_bal = profile.get("paid_question_balance", 0) or 0 if profile else 0
    allowed_limit = 10000 if user_id == PRIMARY_ADMIN_ID else max(DAILY_QUESTION_LIMIT, paid_bal) + (profile.get("bonus_quota", 0) if profile else 0)
    remaining_quota = allowed_limit - attempted_today

    if count > remaining_quota:
        await query.edit_message_text(f"🛑 Insufficient limit! You only have {remaining_quota} questions left today.", reply_markup=get_quizbook_nav_keyboard(), parse_mode="Markdown")
        return

    questions = await asyncio.to_thread(fetch_full_mock_questions, count, lang, user_id)
    mock_number = await asyncio.to_thread(get_next_mock_number, user_id, "MOCK")
    
    await start_quiz_session(query, context, user_id, questions, timer_sec, "MOCK", mock_number, "Mixed", "MOCK", f"Full Mock #{mock_number}", lang, total_time_mins=total_time_mins)


# ==========================================
# SECTIONAL & TOPIC-WISE SECTIONAL HANDLERS
# ==========================================

async def sect_subj_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    subj = query.data.replace("qsectsubj_", "")
    QUIZ_SETUP_CACHE[user_id]["subject"] = subj

    flow = QUIZ_SETUP_CACHE[user_id].get("flow")
    if flow == "TOPSECT":
        QUIZ_SETUP_CACHE[user_id]["selected_topics"] = []
        await show_multi_topic_selection(query, user_id, subj)
    else:
        await show_sect_count_selection(query)

async def show_multi_topic_selection(query, user_id, subj):
    topics = get_available_topics(subj, "en")
    selected = QUIZ_SETUP_CACHE[user_id].get("selected_topics", [])
    
    keyboard = []
    for t_key, t_name in topics:
        prefix = "✅ " if t_key in selected else "⬜ "
        keyboard.append([InlineKeyboardButton(f"{prefix}{t_name}", callback_data=f"qtop_toggle_{t_key}")])
    
    if 2 <= len(selected) <= 4:
        keyboard.append([InlineKeyboardButton("🚀 Proceed with Selection", callback_data="qtop_proceed")])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="qflow_TOPSECT")])
    
    await query.edit_message_text(
        "📑 **TOPIC-WISE SECTIONAL**\nSelect at least 2 and max 4 topics:\n",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def top_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    t_key = query.data.replace("qtop_toggle_", "")
    
    selected = QUIZ_SETUP_CACHE[user_id].get("selected_topics", [])
    if t_key in selected:
        selected.remove(t_key)
    else:
        if len(selected) < 4:
            selected.append(t_key)
        else:
            await query.answer("Maximum 4 topics allowed!", show_alert=True)
            return
    
    QUIZ_SETUP_CACHE[user_id]["selected_topics"] = selected
    subj = QUIZ_SETUP_CACHE[user_id].get("subject", "computer")
    await show_multi_topic_selection(query, user_id, subj)

async def top_proceed_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await show_sect_count_selection(query)

async def show_sect_count_selection(query):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 20 Qs", callback_data="qsectcount_20"), InlineKeyboardButton("📝 25 Qs", callback_data="qsectcount_25")],
        [InlineKeyboardButton("📝 30 Qs", callback_data="qsectcount_30")],
        [InlineKeyboardButton("🔙 Back", callback_data="cmd_quiz")]
    ])
    await query.edit_message_text("📚 **SECTIONAL CONFIG**\nSelect number of questions:", reply_markup=keyboard, parse_mode="Markdown")

async def sect_count_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    count = int(query.data.replace("qsectcount_", ""))
    QUIZ_SETUP_CACHE[user_id]["count"] = count
    
    # HARDCODED: Automatically set global timer to 10 minutes
    QUIZ_SETUP_CACHE[user_id]["total_time_mins"] = 10

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 English", callback_data="qsectlang_en"), InlineKeyboardButton("🇮🇳 हिंदी", callback_data="qsectlang_hi")],
        [InlineKeyboardButton("🔙 Back", callback_data="cmd_quiz")]
    ])
    await query.edit_message_text("🌐 **Select Language**:", reply_markup=keyboard, parse_mode="Markdown")

async def sect_lang_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    lang = query.data.replace("qsectlang_", "")
    
    setup = QUIZ_SETUP_CACHE.pop(user_id, {})
    flow = setup.get("flow")
    count = setup.get("count", 20)
    total_time_mins = setup.get("total_time_mins", 10)
    subj = setup.get("subject", "computer")
    timer_sec = (total_time_mins * 60) // count # Fallback parameter

    profile = await asyncio.to_thread(get_user_profile, user_id)
    attempted_today = await asyncio.to_thread(get_today_attempts, user_id)
    paid_bal = profile.get("paid_question_balance", 0) or 0 if profile else 0
    allowed_limit = 10000 if user_id == PRIMARY_ADMIN_ID else max(DAILY_QUESTION_LIMIT, paid_bal) + (profile.get("bonus_quota", 0) if profile else 0)
    remaining_quota = allowed_limit - attempted_today

    if count > remaining_quota:
        await query.edit_message_text(f"🛑 Insufficient limit! You only have {remaining_quota} questions left today.", reply_markup=get_quizbook_nav_keyboard(), parse_mode="Markdown")
        return

    if flow == "TOPSECT":
        selected_topics = setup.get("selected_topics", [])
        questions = await asyncio.to_thread(fetch_multi_topic_questions, count, selected_topics, subj, lang, user_id)
        quiz_mode = "TOPIC_SECTIONAL"
        mock_number = await asyncio.to_thread(get_next_mock_number, user_id, "TOPIC_SECTIONAL")
        topic_name = f"Topic-Wise Sectional #{mock_number}"
        await start_quiz_session(query, context, user_id, questions, timer_sec, quiz_mode, mock_number, subj, "MULTI", topic_name, lang, selected_topics, total_time_mins=total_time_mins)
    else:
        questions = await asyncio.to_thread(fetch_pyqs_for_quiz, count, None, lang, user_id, "MIXED", subj)
        quiz_mode = "SECTIONAL"
        mock_number = await asyncio.to_thread(get_next_mock_number, user_id, "SECTIONAL")
        topic_name = f"Sectional #{mock_number} ({subj.upper()})"
        await start_quiz_session(query, context, user_id, questions, timer_sec, quiz_mode, mock_number, subj, "MIXED", topic_name, lang, total_time_mins=total_time_mins)


# ==========================================
# NORMAL FLOW HANDLERS
# ==========================================

async def quiz_subject_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_quiz_maintenance(update): return
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    subj = query.data.replace("qsubj_", "")
    
    if user_id not in QUIZ_SETUP_CACHE:
        QUIZ_SETUP_CACHE[user_id] = {}
    QUIZ_SETUP_CACHE[user_id]["subject"] = subj

    subj_label = "General Knowledge (GK)" if subj == "gk" else "Computer Awareness"
    mode_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Subject-Wise / Topic-Wise Practice", callback_data="qmode_topic")],
        [InlineKeyboardButton("🔀 Mixed Practice Mock Test", callback_data="qmode_mixed")],
        [InlineKeyboardButton("🔙 Back to Subjects", callback_data="qflow_NORMAL")]
    ])

    msg_text = (
        f"📚 **NORMAL QUIZ SETUP** 📚\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📖 **Subject:** `{subj_label}`\n\n"
        f"Please select your practice mode:"
    )
    await query.edit_message_text(msg_text, reply_markup=mode_keyboard, parse_mode="Markdown")

async def quiz_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_quiz_maintenance(update): return
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    mode = query.data.replace("qmode_", "")
    
    current_cache = QUIZ_SETUP_CACHE.get(user_id, {"subject": "computer"})
    current_cache["mode"] = mode
    subj = current_cache.get("subject", "computer")

    if mode == "mixed":
        current_cache["topic"] = "MIXED"
        current_cache["topic_name"] = "Mixed Practice"
        QUIZ_SETUP_CACHE[user_id] = current_cache
        await show_language_selection(query, user_id)
    elif mode == "topic":
        QUIZ_SETUP_CACHE[user_id] = current_cache
        topics_list = get_available_topics(subject=subj, language="en")
        keyboard = []
        for t_key, t_display in topics_list:
            keyboard.append([InlineKeyboardButton(t_display, callback_data=f"qtopic_{t_key}")])

        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data=f"qsubj_{subj}")])

        msg_text = (
            f"📂 **SELECT TOPIC / CATEGORY** 📂\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📖 **Subject:** `{'General Knowledge (GK)' if subj=='gk' else 'Computer Awareness'}`\n\n"
            f"Tap a specific topic below:"
        )
        await query.edit_message_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def quiz_topic_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_quiz_maintenance(update): return
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    topic_key = query.data.replace("qtopic_", "")
    
    current_cache = QUIZ_SETUP_CACHE.get(user_id, {"subject": "computer"})
    subj = current_cache.get("subject", "computer")
    metadata = GK_TOPIC_METADATA if subj == "gk" else COMPUTER_TOPIC_METADATA
    t_info = metadata.get(topic_key, {})
    topic_display = t_info.get("en", topic_key.replace("_", " "))

    current_cache["topic"] = topic_key
    current_cache["topic_name"] = topic_display
    QUIZ_SETUP_CACHE[user_id] = current_cache

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
        f"🌐 **SELECT LANGUAGE** 🌐\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 **Topic:** `{topic_name}`\n\n"
        f"Please select your preferred language for the questions:"
    )
    await query.edit_message_text(msg_text, reply_markup=lang_keyboard, parse_mode="Markdown")

async def quiz_language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_quiz_maintenance(update): return
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    lang = query.data.replace("qlang_", "")
    
    current_cache = QUIZ_SETUP_CACHE.get(user_id, {})
    current_cache["language"] = lang
    QUIZ_SETUP_CACHE[user_id] = current_cache

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
    topic_name = current_cache.get("topic_name", "Practice Session")
    subj_label = "General Knowledge (GK)" if current_cache.get("subject") == "gk" else "Computer Awareness"

    msg_text = (
        f"📚 **NORMAL QUIZ SETUP** 📚\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 **Language:** `{lang_label}`\n"
        f"📖 **Subject:** `{subj_label}`\n"
        f"📌 **Topic:** `{topic_name}`\n"
        f"⚡ **Available Daily Quota:** `{remaining_quota}` Qs\n\n"
        f"📝 Select the number of questions for this session:"
    )

    await query.edit_message_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def quiz_count_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_quiz_maintenance(update): return
    query = update.callback_query
    user_id = query.from_user.id
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user_id, 10))
    
    profile = await asyncio.to_thread(get_user_profile, user_id)
    attempted_today = await asyncio.to_thread(get_today_attempts, user_id)
    paid_bal = profile.get("paid_question_balance", 0) or 0 if profile else 0
    allowed_limit = 10000 if user_id == PRIMARY_ADMIN_ID else max(DAILY_QUESTION_LIMIT, paid_bal) + (profile.get("bonus_quota", 0) if profile else 0)

    if attempted_today >= allowed_limit:
        await query.answer("🛑 Daily Limit Exhausted!", show_alert=True)
        await query.edit_message_text("🛑 **DAILY FREE LIMIT EXHAUSTED!** 🛑", parse_mode="Markdown")
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
    topic_name = current_cache.get("topic_name", "Practice Session")

    await query.edit_message_text(
        f"⏱ **NORMAL QUIZ SETUP** ⏱\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 **Language:** `{lang_label}`\n"
        f"📖 **Topic:** `{topic_name}`\n"
        f"📝 **Selected:** `{count} Questions`\n\n"
        f"⏱ Choose timer duration per question:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def quiz_timer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_quiz_maintenance(update): return
    query = update.callback_query
    user_id = query.from_user.id
    asyncio.create_task(asyncio.to_thread(log_user_activity_time, user_id, 15))
    
    profile = await asyncio.to_thread(get_user_profile, user_id)
    attempted_today = await asyncio.to_thread(get_today_attempts, user_id)
    paid_bal = profile.get("paid_question_balance", 0) or 0 if profile else 0
    allowed_limit = 10000 if user_id == PRIMARY_ADMIN_ID else max(DAILY_QUESTION_LIMIT, paid_bal) + (profile.get("bonus_quota", 0) if profile else 0)

    if attempted_today >= allowed_limit:
        await query.answer("🛑 Daily Limit Exhausted!", show_alert=True)
        await query.edit_message_text("🛑 Daily limit exhausted for today.", parse_mode="Markdown")
        return

    await query.answer()
    timer_sec = int(query.data.replace("qtimer_", ""))
    setup = QUIZ_SETUP_CACHE.pop(user_id, {})
    
    count = setup.get("count", 20)
    language = setup.get("language", "en")
    subject = setup.get("subject", "computer")
    topic = setup.get("topic", "MIXED")
    topic_name = setup.get("topic_name", "Mixed Practice")

    remaining_quota = allowed_limit - attempted_today
    if count > remaining_quota:
        count = max(1, remaining_quota)

    questions = await asyncio.to_thread(fetch_pyqs_for_quiz, count, None, language, user_id, topic, subject)
    await start_quiz_session(query, context, user_id, questions, timer_sec, "PRACTICE", 0, subject, topic, topic_name, language)


# ==========================================
# SHARED LAUNCH & EXECUTION FUNCTIONS
# ==========================================

async def start_quiz_session(query, context, user_id, questions, timer_sec, quiz_mode, mock_number, subject, topic, topic_name, language, selected_topics=None, total_time_mins=None):
    if not questions:
        await query.edit_message_text("⚠️ No questions found. Try again or contact admin.", reply_markup=get_quizbook_nav_keyboard(), parse_mode="Markdown")
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
        "timer_sec": timer_sec,
        "is_paused": False,
        "start_time": get_ist_timestamp_str(),
        "detailed_logs": [],
        "quiz_mode": quiz_mode,
        "mock_number": mock_number,
        "selected_topics": selected_topics,
        "global_remaining_sec": (total_time_mins * 60) if total_time_mins else None,
        "question_start_time": time.time()
    }
    ACTIVE_SESSIONS[user_id] = session

    lang_str = "🌐 English" if language == "en" else "🇮🇳 हिंदी"
    title = f"{quiz_mode.replace('_', ' ').title()} #{mock_number}" if quiz_mode in ["MOCK", "SECTIONAL", "TOPIC_SECTIONAL"] else topic_name

    await query.edit_message_text(
        f"🚀 **QUIZ SESSION STARTED!** 🚀\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 **Language:** `{lang_str}`\n"
        f"📖 **Mode:** `{quiz_mode.replace('_', ' ')}`\n"
        f"📌 **Title:** `{title}`\n"
        f"⏱ **Timer Config:** `{'Global 10 Mins Timer' if total_time_mins else str(timer_sec) + 's per question'}`\n\n"
        f"⚡ Loading questions...",
        parse_mode="Markdown"
    )
    await send_next_question(chat_id, user_id, context)

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

    # Deduct the time spent on the current question if using Global Timer
    if session.get("global_remaining_sec") is not None:
        time_spent = time.time() - session.get("question_start_time", time.time())
        session["global_remaining_sec"] = max(0, session["global_remaining_sec"] - time_spent)

    save_state = {
        "user_id": user_id,
        "questions": session["questions"],
        "language": session.get("language", "en"),
        "subject": session.get("subject", "computer"),
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
        "selected_topics": session.get("selected_topics"),
        "global_remaining_sec": session.get("global_remaining_sec")
    }
    asyncio.create_task(asyncio.to_thread(save_paused_quiz_state, user_id, save_state))
    ACTIVE_SESSIONS.pop(user_id, None)

    remaining_qs = session["total"] - session["current_index"]
    msg = (
        f"⏸ **QUIZ PAUSED & SAVED** ⏸\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📖 **Title:** `{session.get('topic_name', 'Practice Session')}`\n"
        f"📊 **Remaining Questions:** `{remaining_qs}` Qs\n"
        f"⭐ **Current Score:** `{session['score']}`\n\n"
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
        "subject": paused.get("subject", "computer"),
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
        "selected_topics": paused.get("selected_topics"),
        "global_remaining_sec": paused.get("global_remaining_sec"),
        "question_start_time": time.time()
    }
    ACTIVE_SESSIONS[user_id] = session

    if update.callback_query:
        await update.callback_query.answer()

    countdown_msg = await context.bot.send_message(
        chat_id=chat_id, 
        text=f"▶️ **RESUMING QUIZ...** ▶️\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⏳ **3...** Get ready!",
        parse_mode="Markdown"
    )
    await asyncio.sleep(1)

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=countdown_msg.message_id,
            text=f"▶️ **RESUMING QUIZ...** ▶️\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⏳ **2...** Get ready!",
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
            subject=session.get("subject", "computer"),
            selected_topics=session.get("selected_topics")
        ))
    elif paused and paused.get("current_index", 0) > 0:
        asyncio.create_task(asyncio.to_thread(
            record_quiz_result,
            user_id=user_id,
            quiz_id=paused.get("topic", "mixed"),
            score=paused["score"],
            total_questions=paused["total"],
            correct_count=paused["correct"],
            wrong_count=paused["wrong"],
            skipped_count=paused["skipped"],
            time_taken=0,
            question_details=paused.get("detailed_logs", []),
            quiz_mode=paused.get("quiz_mode", "PRACTICE"),
            mock_number=paused.get("mock_number", 0),
            subject=paused.get("subject", "computer"),
            selected_topics=paused.get("selected_topics")
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
    
    # Global Timer Logic Calculation
    global_time_str = ""
    if session.get("global_remaining_sec") is not None:
        rem_sec = int(session["global_remaining_sec"])
        if rem_sec <= 0:
            await context.bot.send_message(chat_id=chat_id, text="⏰ **TIME'S UP!** Your global 10-minute quiz timer has expired.", parse_mode="Markdown")
            await finish_quiz_and_send_report(chat_id, user_id, context)
            return
        
        m, s = divmod(rem_sec, 60)
        global_time_str = f" | ⏳ {m:02d}:{s:02d} Left"
        
        # Max out open_period to remaining time, capped at 600s due to Telegram API limits
        poll_timer_sec = min(rem_sec, 600)
        poll_timer_sec = max(5, poll_timer_sec) # Telegram minimum is 5s
    else:
        poll_timer_sec = session["timer_sec"]

    session["question_start_time"] = time.time()

    current_num = session["current_index"] + 1
    total_num = session["total"]
    
    quiz_mode = session.get("quiz_mode", "PRACTICE")
    title = f"{quiz_mode.replace('_', ' ').title()} #{session.get('mock_number', 0)}" if quiz_mode != "PRACTICE" else session.get('topic_name', 'Quiz')
    
    # Safe 300 character truncation preserving the Global Time string
    base_header = f"📖 [{title}] — ({current_num}/{total_num}){global_time_str}\n\n"
    avail_len = 300 - len(base_header)
    q_text = q['question']
    if len(q_text) > avail_len:
        q_text = q_text[:max(0, avail_len-3)] + "..."
    header_text = base_header + q_text

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
            open_period=poll_timer_sec
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

        TIMER_TASKS[user_id] = asyncio.create_task(auto_skip_task(chat_id, user_id, poll_id, session["current_index"], poll_timer_sec, context))
    except Exception as e:
        logger.error(f"Error sending poll: {e}")
        session["skipped"] += 1
        session["current_index"] += 1
        await send_next_question(chat_id, user_id, context)

async def auto_skip_task(chat_id: int, user_id: int, poll_id: str, expected_idx: int, timer_sec: int, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(timer_sec + 1)
    if poll_id in POLL_MAP:
        data = POLL_MAP.pop(poll_id, None)
        session = ACTIVE_SESSIONS.get(user_id)
        if session and not session.get("is_paused") and session["current_index"] == expected_idx:
            
            # Global Timer Deduction on Skip/Timeout
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
        
        # Global Timer Deduction on Answer
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
    
    quiz_mode = session.get("quiz_mode", "PRACTICE")
    title = f"{quiz_mode.replace('_', ' ').title()} #{session.get('mock_number', 0)}" if quiz_mode != "PRACTICE" else session.get('topic_name', 'Quiz')

    rank = await asyncio.to_thread(calculate_user_rank, user_id)
    percentile = await asyncio.to_thread(calculate_user_percentile, user_id)
    lang_label = "🌐 English" if lang == "en" else "🇮🇳 हिंदी"

    report_card = (
        f"🏆 **OFFICIAL QUIZ REPORT CARD** 🏆\n"
        f"📚 **Quiz with HiM by Himanshu Sir**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📖 **Title:** `{title}`\n"
        f"🌐 **Language:** `{lang_label}`\n"
        f"📅 **Attempted At:** `{session['start_time']}`\n\n"
        f"📊 **PERFORMANCE BREAKDOWN:**\n"
        f"• **Correct Answers:** `{correct}` ✅\n"
        f"• **Wrong Answers:** `{wrong}` ❌\n"
        f"• **Skipped Questions:** `{skipped}` ⏭\n"
        f"• **Final Score:** `{score}` ⭐\n\n"
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
        subject=session.get("subject", "computer"),
        selected_topics=session.get("selected_topics")
    ))

async def quiz_extended_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Central Router for all quiz modes."""
    data = update.callback_query.data
    if data.startswith("qflow_"):
        await quiz_flow_callback(update, context)
    elif data.startswith("qmockcount_"):
        await mock_count_callback(update, context)
    elif data.startswith("qmocklang_"):
        await mock_lang_callback(update, context)
    elif data.startswith("qsectsubj_"):
        await sect_subj_callback(update, context)
    elif data.startswith("qtop_toggle_"):
        await top_toggle_callback(update, context)
    elif data == "qtop_proceed":
        await top_proceed_callback(update, context)
    elif data.startswith("qsectcount_"):
        await sect_count_callback(update, context)
    elif data.startswith("qsectlang_"):
        await sect_lang_callback(update, context)
    elif data.startswith("qsubj_"):
        await quiz_subject_callback(update, context)
    elif data.startswith("qmode_"):
        await quiz_mode_callback(update, context)
    elif data.startswith("qtopic_"):
        await quiz_topic_callback(update, context)
    elif data.startswith("qlang_"):
        await quiz_language_callback(update, context)
    elif data.startswith("qcount_"):
        await quiz_count_callback(update, context)
    elif data.startswith("qtimer_"):
        await quiz_timer_callback(update, context)