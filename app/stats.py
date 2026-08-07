import os
import json
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from app.database import get_user_profile, get_saved_questions, get_all_users, get_db, row_to_dict

logger = logging.getLogger(__name__)

def get_overall_leaderboard(limit: int = 10):
    """Retrieves top performers ranked by average quiz score."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT u.user_id, u.full_name, COALESCE(AVG(q.score), 0.0) as avg_score, COUNT(q.id) as total_quizzes
            FROM users u
            JOIN quiz_attempts q ON u.user_id = q.user_id
            GROUP BY u.user_id, u.full_name
            HAVING COUNT(q.id) > 0
            ORDER BY avg_score DESC, total_quizzes DESC
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        conn.close()
        res = []
        for r in rows:
            d = row_to_dict(r)
            if d:
                d['avg_score'] = float(d.get('avg_score') or 0.0)
                res.append(d)
        return res
    except Exception as e:
        logger.error(f"Error fetching leaderboard: {e}")
        return []

def get_user_performance_summary(user_id: int):
    """Returns aggregated academic metrics for a student profile, guaranteed type-safe."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                COUNT(id) as total_tests,
                SUM(questions_attempted) as total_qs,
                SUM(correct_answers) as total_correct,
                SUM(wrong_answers) as total_wrong,
                SUM(skipped_count) as total_skipped,
                AVG(score) as avg_score
            FROM quiz_attempts 
            WHERE user_id = ?
        """, (user_id,))
        row = cursor.fetchone()
        conn.close()
        d = row_to_dict(row) if row else {}
        if d:
            return {
                'total_tests': int(d.get('total_tests') or 0),
                'total_qs': int(d.get('total_qs') or 0),
                'total_correct': int(d.get('total_correct') or 0),
                'total_wrong': int(d.get('total_wrong') or 0),
                'total_skipped': int(d.get('total_skipped') or 0),
                'avg_score': float(d.get('avg_score') or 0.0)
            }
        return {'total_tests': 0, 'total_qs': 0, 'total_correct': 0, 'total_wrong': 0, 'total_skipped': 0, 'avg_score': 0.0}
    except Exception as e:
        logger.error(f"Error getting performance summary for {user_id}: {e}")
        return {'total_tests': 0, 'total_qs': 0, 'total_correct': 0, 'total_wrong': 0, 'total_skipped': 0, 'avg_score': 0.0}

def calculate_user_rank(user_id: int) -> int:
    """Calculates overall student rank based on total score across all quiz attempts."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, SUM(score) as total_score 
            FROM quiz_attempts 
            GROUP BY user_id 
            ORDER BY total_score DESC
        """)
        rows = cursor.fetchall()
        conn.close()
        
        for rank, row in enumerate(rows, start=1):
            r = row_to_dict(row)
            if r and r.get("user_id") == user_id:
                return rank
        return 1
    except Exception as e:
        logger.error(f"Error calculating rank for user {user_id}: {e}")
        return 1

def calculate_user_percentile(user_id: int) -> float:
    """Calculates student performance percentile relative to all registered users."""
    try:
        all_users = get_all_users()
        total_users = len(all_users)
        if total_users <= 1:
            return 100.0

        user_rank = calculate_user_rank(user_id)
        percentile = ((total_users - user_rank) / total_users) * 100
        return round(max(1.0, min(99.9, percentile)), 1)
    except Exception as e:
        logger.error(f"Error calculating percentile for user {user_id}: {e}")
        return 95.0

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /stats command and displays the academic dashboard."""
    user = update.effective_user
    user_id = user.id
    profile = get_user_profile(user_id) or {}

    if not profile or not profile.get("is_verified"):
        await update.message.reply_text("⚠️ You must complete registration first! Tap /start to begin.")
        return

    student_id = profile.get("student_id") or f"USER_{user_id}"
    full_name = profile.get("full_name") or user.full_name
    target_exam = profile.get("target_exam") or "N/A"
    phone_number = profile.get("phone_number") or "N/A"
    paid_balance = int(profile.get("paid_question_balance") or 20)
    vip_expiry = profile.get("vip_pass_expiry") or "Free Tier"
    
    rank = calculate_user_rank(user_id)
    percentile = calculate_user_percentile(user_id)

    text = (
        f"👤 **STUDENT ACADEMIC PROFILE** 👤\n\n"
        f"🪪 **Student ID:** `{student_id}`\n"
        f"📛 **Name:** {full_name}\n"
        f"🎯 **Target Exam:** {target_exam}\n"
        f"📞 **Phone:** `{phone_number}`\n\n"
        f"📊 **PERFORMANCE & SUBSCRIPTION LEDGER**\n"
        f"🏆 **Overall Rank:** `#{rank}`\n"
        f"📈 **Percentile:** `{percentile}%`\n"
        f"💳 **Daily Limit Balance:** `{paid_balance} Questions`\n"
        f"⭐ **VIP Expiry:** `{vip_expiry}`\n"
    )

    keyboard = [
        [InlineKeyboardButton("📚 Saved Questions", callback_data="stats_saved")],
        [InlineKeyboardButton("🔄 Refresh Profile", callback_data="stats_refresh")]
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def stats_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles callback menu buttons for stats/profile view."""
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
            q_text = sq.get("question_text", "Question")
            expl = sq.get("explanation", "N/A")
            msg += f"**{i}. {q_text}**\n💡 *Explanation:* {expl}\n\n"

        await query.message.reply_text(msg, parse_mode="Markdown")

    elif data == "stats_refresh":
        profile = get_user_profile(user_id) or {}
        if profile:
            student_id = profile.get("student_id") or f"USER_{user_id}"
            full_name = profile.get("full_name") or "Student"
            target_exam = profile.get("target_exam") or "N/A"
            phone_number = profile.get("phone_number") or "N/A"
            paid_balance = int(profile.get("paid_question_balance") or 20)
            vip_expiry = profile.get("vip_pass_expiry") or "Free Tier"
            
            rank = calculate_user_rank(user_id)
            percentile = calculate_user_percentile(user_id)

            text = (
                f"👤 **STUDENT ACADEMIC PROFILE** 👤\n\n"
                f"🪪 **Student ID:** `{student_id}`\n"
                f"📛 **Name:** {full_name}\n"
                f"🎯 **Target Exam:** {target_exam}\n"
                f"📞 **Phone:** `{phone_number}`\n\n"
                f"📊 **PERFORMANCE & SUBSCRIPTION LEDGER**\n"
                f"🏆 **Overall Rank:** `#{rank}`\n"
                f"📈 **Percentile:** `{percentile}%`\n"
                f"💳 **Daily Limit Balance:** `{paid_balance} Questions`\n"
                f"⭐ **VIP Expiry:** `{vip_expiry}`\n"
            )
            keyboard = [
                [InlineKeyboardButton("📚 Saved Questions", callback_data="stats_saved")],
                [InlineKeyboardButton("🔄 Refresh Profile", callback_data="stats_refresh")]
            ]
            await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")