import os
import json
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from app.database import get_user_profile, get_saved_questions

logger = logging.getLogger(__name__)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /stats command and displays the user profile dashboard."""
    user = update.effective_user
    user_id = user.id
    profile = get_user_profile(user_id)

    if not profile or not profile.get("is_verified"):
        await update.message.reply_text("⚠️ You must complete registration first! Tap /start to begin.")
        return

    student_id = profile.get("student_id", "N/A")
    full_name = profile.get("full_name", user.full_name)
    target_exam = profile.get("target_exam", "N/A")
    phone_number = profile.get("phone_number", "N/A")
    paid_balance = profile.get("paid_question_balance", 20)
    vip_expiry = profile.get("vip_pass_expiry") or "Free Tier"

    text = (
        f"👤 **STUDENT ACADEMIC PROFILE** 👤\n\n"
        f"🪪 **Student ID:** `{student_id}`\n"
        f"📛 **Name:** {full_name}\n"
        f"🎯 **Target Exam:** {target_exam}\n"
        f"📞 **Phone:** `{phone_number}`\n\n"
        f"📊 **PERFORMANCE & SUBSCRIPTION LEDGER**\n"
        f"💳 **Daily Limit Balance:** `{paid_balance} Questions`\n"
        f"⭐ **VIP Expiry:** `{vip_expiry}`\n"
    )

    keyboard = [
        [InlineKeyboardButton("📚 Saved Questions", callback_data="stats_saved")],
        [InlineKeyboardButton("🔄 Refresh Profile", callback_data="stats_refresh")]
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def stats_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles callback buttons for the stats menu."""
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "stats_saved":
        saved = get_saved_questions(user_id)
        if not saved:
            await query.message.reply_text("📚 You haven't saved any questions yet.")
            return

        msg = "📚 **YOUR SAVED QUESTIONS** 📚\n\n"
        for i, sq in enumerate(saved[:10], 1):
            q_text = sq.get("question_text", sq.get("question", "Question"))
            expl = sq.get("explanation", "N/A")
            msg += f"**{i}. {q_text}**\n💡 *Explanation:* {expl}\n\n"

        await query.message.reply_text(msg, parse_mode="Markdown")

    elif data == "stats_refresh":
        profile = get_user_profile(user_id)
        if profile:
            student_id = profile.get("student_id", "N/A")
            full_name = profile.get("full_name", "Student")
            target_exam = profile.get("target_exam", "N/A")
            phone_number = profile.get("phone_number", "N/A")
            paid_balance = profile.get("paid_question_balance", 20)
            vip_expiry = profile.get("vip_pass_expiry") or "Free Tier"

            text = (
                f"👤 **STUDENT ACADEMIC PROFILE** 👤\n\n"
                f"🪪 **Student ID:** `{student_id}`\n"
                f"📛 **Name:** {full_name}\n"
                f"🎯 **Target Exam:** {target_exam}\n"
                f"📞 **Phone:** `{phone_number}`\n\n"
                f"📊 **PERFORMANCE & SUBSCRIPTION LEDGER**\n"
                f"💳 **Daily Limit Balance:** `{paid_balance} Questions`\n"
                f"⭐ **VIP Expiry:** `{vip_expiry}`\n"
            )
            keyboard = [
                [InlineKeyboardButton("📚 Saved Questions", callback_data="stats_saved")],
                [InlineKeyboardButton("🔄 Refresh Profile", callback_data="stats_refresh")]
            ]
            await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")