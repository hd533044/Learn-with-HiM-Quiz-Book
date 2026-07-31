import time
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from app.config import DAILY_QUESTION_LIMIT
from app.database import (
    get_user_profile, get_today_attempts, save_quiz_attempt,
    save_paused_quiz, get_paused_quiz, clear_paused_quiz
)
from app.pyq_fetcher import get_pyq_questions
from app.pdf_generator import generate_quiz_questions_pdf

logger = logging.getLogger(__name__)

async def launch_quiz_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Original Entry Point for Quiz Launch."""
    user = update.effective_user
    profile = get_user_profile(user.id)

    if not profile:
        msg = "⚠️ Please type /start to create your student profile first!"
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(msg)
        else:
            await update.message.reply_text(msg)
        return

    # Check for active paused quiz
    paused = get_paused_quiz(user.id)
    if paused:
        keyboard = [
            [InlineKeyboardButton("▶️ Resume Paused Quiz (/resume)", callback_data="cmd_resume_quiz")],
            [InlineKeyboardButton("🔄 Start New Quiz", callback_data="cmd_start_fresh_quiz")]
        ]
        text = (
            f"⏸ **PAUSED QUIZ FOUND**\n"
            f"⚡ *Powered by @LearnwithHiM*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Saved Questions: `{len(paused.get('questions', []))}` Qs\n"
            f"Current Score: `{paused.get('score', 0)}`\n\n"
            f"Would you like to resume your quiz or start fresh?"
        )
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        else:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    await show_quiz_count_options(update, context)

async def show_quiz_count_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    profile = get_user_profile(user.id)
    today_used = get_today_attempts(user.id)
    allowed_limit = DAILY_QUESTION_LIMIT + profile.get("bonus_quota", 0)
    remaining = max(0, allowed_limit - today_used)

    if remaining <= 0:
        msg = (
            f"🛑 **DAILY ATTEMPT LIMIT REACHED!**\n"
            f"⚡ *Powered by @LearnwithHiM*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"You have used all `{allowed_limit}` questions for today.\n\n"
            f"🤝 Use **/invite** to invite 4 friends and unlock **+10 extra daily questions**!"
        )
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(msg, parse_mode="Markdown")
        else:
            await update.message.reply_text(msg, parse_mode="Markdown")
        return

    counts = [5, 10, 15, 20]
    valid_counts = [c for c in counts if c <= remaining]
    if not valid_counts and remaining > 0:
        valid_counts = [remaining]

    buttons = []
    row = [InlineKeyboardButton(f"{c} Qs", callback_data=f"qcount_{c}") for c in valid_counts]
    buttons.append(row)
    buttons.append([InlineKeyboardButton("📖 Profile Book (/profilebook)", callback_data="cmd_profilebook")])

    text = (
        f"🎯 **SELECT QUESTION COUNT**\n"
        f"⚡ *Powered by @LearnwithHiM*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• **Remaining Daily Quota:** `{remaining}` / `{allowed_limit}` Qs\n\n"
        f"Select how many questions you want to attempt:"
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

async def quiz_count_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    q_count = int(query.data.replace("qcount_", ""))
    context.user_data['quiz_qcount'] = q_count

    buttons = [
        [InlineKeyboardButton("⚡ 15 Sec", callback_data="qtimer_15"), InlineKeyboardButton("⏱ 30 Sec", callback_data="qtimer_30")],
        [InlineKeyboardButton("⌛ 45 Sec", callback_data="qtimer_45"), InlineKeyboardButton("🐢 60 Sec", callback_data="qtimer_60")]
    ]

    text = (
        f"⏱ **SELECT TIMER PER QUESTION**\n"
        f"⚡ *Powered by @LearnwithHiM*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Selected Questions: `{q_count}` Qs\n\n"
        f"Choose speed per question:"
    )
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

async def quiz_timer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("🚀 Launching Quiz...")
    timer_sec = int(query.data.replace("qtimer_", ""))
    q_count = context.user_data.get('quiz_qcount', 5)

    questions = get_pyq_questions(q_count)
    if not questions:
        await query.edit_message_text("⚠️ Could not load question bank. Please try again.")
        return

    context.user_data['active_quiz'] = {
        'questions': questions,
        'current_idx': 0,
        'score': 0,
        'timer_sec': timer_sec,
        'total_qs': len(questions),
        'start_time': int(time.time()),
        'history': []
    }

    await query.edit_message_text("🚀 **Starting quiz polls now... Get ready!**", parse_mode="Markdown")
    await send_next_quiz_question(update, context)

async def stop_quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    active = context.user_data.get('active_quiz')

    if not active or active['current_idx'] >= active['total_qs']:
        msg = "ℹ️ No active quiz in progress to pause."
        if update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        else:
            await update.message.reply_text(msg)
        return

    remaining_questions = active['questions'][active['current_idx']:]
    paused_state = {
        'user_name': user.full_name if user else 'Student',
        'questions': remaining_questions,
        'score': active['score'],
        'timer_sec': active['timer_sec'],
        'total_qs': len(remaining_questions),
        'history': active.get('history', [])
    }

    save_paused_quiz(user.id, paused_state)
    context.user_data.pop('active_quiz', None)

    msg = (
        f"⏸ **QUIZ PAUSED & SAVED**\n"
        f"⚡ *Powered by @LearnwithHiM*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• **Remaining Questions:** `{len(remaining_questions)}` Qs\n"
        f"• **Current Score:** `{paused_state['score']}`\n\n"
        f"Type **/resume** whenever you are ready to continue!"
    )
    buttons = [
        [InlineKeyboardButton("▶️ Resume Quiz Now (/resume)", callback_data="cmd_resume_quiz")],
        [InlineKeyboardButton("📖 Profile Book (/profilebook)", callback_data="cmd_profilebook")]
    ]

    if update.callback_query:
        await update.callback_query.answer("⏸ Quiz Paused!", show_alert=True)
        await update.callback_query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

async def resume_quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    paused = get_paused_quiz(user.id)

    if not paused:
        msg = "ℹ️ No paused quiz found. Type /quiz to start a new test!"
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(msg)
        else:
            await update.message.reply_text(msg)
        return

    clear_paused_quiz(user.id)
    questions = paused['questions']

    context.user_data['active_quiz'] = {
        'questions': questions,
        'current_idx': 0,
        'score': paused['score'],
        'timer_sec': paused['timer_sec'],
        'total_qs': len(questions),
        'start_time': int(time.time()),
        'history': paused.get('history', [])
    }

    msg = f"▶️ **RESUMING QUIZ...**\n`{len(questions)}` questions remaining."
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, parse_mode="Markdown")

    await send_next_quiz_question(update, context)

async def send_next_quiz_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    active = context.user_data.get('active_quiz')
    if not active:
        return

    idx = active['current_idx']
    total = active['total_qs']

    if idx >= total:
        await finish_quiz(update, context)
        return

    q = active['questions'][idx]
    question_text = f"[{idx+1}/{total}] {q['question_text']}"

    if update.effective_chat:
        chat_id = update.effective_chat.id
    elif update.callback_query and update.callback_query.message:
        chat_id = update.callback_query.message.chat_id
    else:
        chat_id = update.effective_user.id

    try:
        sent_poll = await context.bot.send_poll(
            chat_id=chat_id,
            question=question_text,
            options=q['options'],
            type='quiz',
            correct_option_id=q['correct_option_id'],
            open_period=active['timer_sec'],
            is_anonymous=False,
            explanation=q.get('explanation', 'Learn with HiM Official Answer')
        )
        
        user_id = update.effective_user.id if update.effective_user else (
            update.callback_query.from_user.id if update.callback_query else chat_id
        )

        context.bot_data[f"poll_{sent_poll.poll.id}"] = {
            'user_id': user_id,
            'correct_option_id': q['correct_option_id'],
            'question_text': q['question_text'],
            'options': q['options'],
            'explanation': q.get('explanation', '')
        }
    except Exception as e:
        logger.error(f"Error sending poll: {e}")

async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    poll_answer = update.poll_answer
    poll_id = poll_answer.poll_id
    user_id = poll_answer.user_id

    poll_info = context.bot_data.get(f"poll_{poll_id}")
    if not poll_info or poll_info['user_id'] != user_id:
        return

    selected_option = poll_answer.option_ids[0] if poll_answer.option_ids else -1
    is_correct = (selected_option == poll_info['correct_option_id'])

    active = context.user_data.get('active_quiz')
    if active:
        if is_correct:
            active['score'] += 1
        active['current_idx'] += 1
        active.get('history', []).append({
            'question_text': poll_info['question_text'],
            'options': poll_info['options'],
            'correct_option_id': poll_info['correct_option_id'],
            'explanation': poll_info['explanation']
        })

        class FakeChat:
            id = user_id
        class FakeUser:
            id = user_id
            full_name = 'Student'
        class FakeUpdate:
            effective_chat = FakeChat()
            effective_user = FakeUser()
            callback_query = None
            message = None

        if active['current_idx'] < active['total_qs']:
            time.sleep(1)
            await send_next_quiz_question(FakeUpdate(), context)
        else:
            await finish_quiz(FakeUpdate(), context)

async def finish_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    active = context.user_data.get('active_quiz')

    if not active:
        return

    score = active['score']
    total_qs = active['total_qs']
    history = active.get('history', [])

    chat_id = update.effective_chat.id if update.effective_chat else user.id

    save_quiz_attempt(user.id, float(score), total_qs, score, total_qs - score, int(time.time()))
    context.user_data.pop('active_quiz', None)

    context.user_data['last_completed_quiz'] = {
        'user_name': user.full_name if hasattr(user, 'full_name') else 'Student',
        'score': score,
        'total_qs': total_qs,
        'questions': history
    }

    msg = (
        f"🎉 **QUIZ COMPLETED!**\n"
        f"⚡ *Powered by @LearnwithHiM*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 **Final Score:** `{score}` / `{total_qs}` Qs\n"
        f"🎯 **Accuracy:** `{round((score/max(1, total_qs))*100, 1)}%`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 **Download your quiz question bank PDF below:**"
    )

    buttons = [
        [InlineKeyboardButton("📥 Download Quiz Question Bank (PDF)", callback_data="pdf_recent_quiz")],
        [InlineKeyboardButton("📖 Profile Book (/profilebook)", callback_data="cmd_profilebook"), InlineKeyboardButton("🚀 Start Fresh Quiz (/quiz)", callback_data="cmd_quiz")],
        [InlineKeyboardButton("📊 My Stats (/mywholestate)", callback_data="cmd_wholestate"), InlineKeyboardButton("🏆 Leaderboard (/toppername)", callback_data="cmd_toppers")]
    ]

    await context.bot.send_message(
        chat_id=chat_id,
        text=msg,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown"
    )

async def download_quiz_pdf_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("📄 Generating Quiz Question Bank PDF...")

    quiz_data = context.user_data.get('last_completed_quiz')
    if not quiz_data or not quiz_data.get('questions'):
        await query.message.reply_text("⚠️ No recent quiz session found to generate PDF.")
        return

    pdf_buffer = generate_quiz_questions_pdf(quiz_data)
    file_name = f"Quiz_Question_Bank_{quiz_data['user_name'].replace(' ', '_')}.pdf"

    post_pdf_buttons = [
        [InlineKeyboardButton("🚀 Launch Quiz (/quiz)", callback_data="cmd_quiz"), InlineKeyboardButton("📖 Profile Book (/profilebook)", callback_data="cmd_profilebook")],
        [InlineKeyboardButton("📊 My Stats (/mywholestate)", callback_data="cmd_wholestate"), InlineKeyboardButton("🏆 Leaderboard (/toppername)", callback_data="cmd_toppers")]
    ]

    await context.bot.send_document(
        chat_id=query.message.chat_id,
        document=pdf_buffer,
        filename=file_name,
        caption=(
            f"📝 **RECENT QUIZ QUESTION BANK & SOLUTION SHEET**\n"
            f"👤 **Student:** {quiz_data['user_name']}\n"
            f"📊 **Score:** `{quiz_data['score']}/{quiz_data['total_qs']}`\n\n"
            f"⚡ **Powered by @LearnwithHiM**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👇 **Continue practicing using options below:**"
        ),
        reply_markup=InlineKeyboardMarkup(post_pdf_buttons),
        parse_mode="Markdown"
    )