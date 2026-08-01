import time
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from telegram.ext import ContextTypes
from app.config import PRIMARY_ADMIN_ID
from app.database import (
    get_maintenance_until, get_user_profile, 
    is_user_session_expired, touch_user_activity
)

logger = logging.getLogger(__name__)

async def strict_authentication_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    STRICT INTERCEPTION GUARD:
    Guarantees no command or button action can be executed without an active session.
    """
    user = update.effective_user
    if not user:
        return True

    # 1. Maintenance Check
    m_until = get_maintenance_until()
    if int(time.time()) < m_until and user.id != PRIMARY_ADMIN_ID:
        remaining_sec = m_until - int(time.time())
        mins_left = max(1, (remaining_sec + 59) // 60)
        msg = f"🛠 **ADMIN HAS PAUSED THE SERVICE CURRENTLY**\nService will resume in approximately `{mins_left} mins`. Please try again later!"
        if update.callback_query:
            try:
                await update.callback_query.answer("🛠 Service Paused!", show_alert=True)
            except Exception:
                pass
        elif update.message:
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
        return False

    # 2. Inactivity Lock & Verification Check
    profile = get_user_profile(user.id)
    
    # Unregistered User Handling
    if not profile or not profile.get("is_verified"):
        unreg_card = (
            "🔒 **ACCOUNT UNVERIFIED**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Kindly register to access the Learn with HiM Quiz Portal.\n\n"
            "👉 Please type **/start** to set up your profile!"
        )
        if update.callback_query:
            try:
                await update.callback_query.answer("🔒 Please type /start to register!", show_alert=True)
            except Exception:
                pass
            await context.bot.send_message(chat_id=user.id, text=unreg_card, parse_mode="Markdown")
        elif update.message:
            await update.message.reply_text(unreg_card, parse_mode="Markdown")
        return False

    # Registered User Session Expired Check
    if is_user_session_expired(user.id):
        lock_card = (
            "🔒 **SECURITY LOCK: ACCESS DENIED**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Your session has expired due to inactivity.\n\n"
            "👉 **To unlock, reply with your Custom Password below (Case Sensitive):**\n"
            "*(Example: `Pass1234` or `9876543210`)*\n\n"
            "💡 *Forgot credentials? Click below to recover via mobile contact!*"
        )
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔑 Recover Credentials via Phone", callback_data="cmd_forgot_credentials")]
        ])
        
        if update.callback_query:
            try:
                await update.callback_query.answer("🔒 Session Expired! Please enter password.", show_alert=True)
            except Exception:
                pass
            await context.bot.send_message(chat_id=user.id, text=lock_card, reply_markup=buttons, parse_mode="Markdown")
        elif update.message:
            await update.message.reply_text(lock_card, reply_markup=buttons, parse_mode="Markdown")
        return False

    # Session active - touch last active timestamp
    touch_user_activity(user.id)
    return True