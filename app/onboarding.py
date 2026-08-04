import re
import logging
import warnings
import calendar
from datetime import datetime
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.warnings import PTBUserWarning
from telegram.ext import (
    ConversationHandler, 
    CommandHandler, 
    MessageHandler, 
    CallbackQueryHandler, 
    filters, 
    ContextTypes
)
from app.config import WELCOME_CARD_TEXT, PRIMARY_ADMIN_ID
from app.database import (
    save_user_profile, get_user_profile, can_user_edit_profile, 
    get_maintenance_until, generate_student_id, update_user_pin
)
import time

warnings.filterwarnings("ignore", category=PTBUserWarning)

# Exactly 20 states defined in strict sequential order
(
    NAME, EXAM, STATE, PHONE, GENDER, DOB_YEAR, DOB_MONTH, DOB_DAY, 
    PIN_SETUP, SEC_QUESTION, SEC_ANSWER, RECOVERY_MENU, 
    REC_SEC_ANS, REC_PHONE, REC_DOB_YEAR, REC_DOB_MONTH, REC_DOB_DAY, REC_NAME_DOB, RESET_PIN, EDIT_WARN
) = range(20)

PRESET_SEC_QUESTIONS = [
    "ᴡʜᴀᴛ ɪꜱ ʏᴏᴜʀ ᴘᴇᴛ'ꜱ ɴᴀᴍᴇ?",
    "ᴡʜᴀᴛ ᴡᴀꜱ ᴛʜᴇ ɴᴀᴍᴇ ᴏꜰ ʏᴏᴜʀ ꜰɪʀꜱᴛ ꜱᴄʜᴏᴏʟ?",
    "ᴡʜɪᴄʜ ɪꜱ ʏᴏᴜʀ ꜰᴀᴠᴏʀɪᴛᴇ ᴄɪᴛʏ?",
    "ᴡʜᴀᴛ ɪꜱ ʏᴏᴜʀ ᴍᴏᴛʜᴇʀ'ꜱ ᴍᴀɪᴅᴇɴ ɴᴀᴍᴇ?"
]

INDIAN_STATES_AND_UTS = [
    "ᴀɴᴅʜʀᴀ ᴘʀᴀᴅᴇꜱʜ", "ᴀʀᴜɴᴀᴄʜᴀʟ ᴘʀᴀᴅᴇꜱʜ", "ᴀꜱꜱᴀᴍ", "ʙɪʜᴀʀ", "ᴄʜʜᴀᴛᴛɪꜱɢᴀʀʜ", "ɢᴏᴀ", 
    "ɢᴜᴊᴀʀᴀᴛ", "ʜᴀʀʏᴀɴᴀ", "ʜɪᴍᴀᴄʜᴀʟ ᴘʀᴀᴅᴇꜱʜ", "ᴊʜᴀʀᴋʜᴀɴᴅ", "ᴋᴀʀɴᴀᴛᴀᴋᴀ", "ᴋᴇʀᴀʟᴀ", 
    "ᴍᴀᴅʜʏᴀ ᴘʀᴀᴅᴇꜱʜ", "ᴍᴀʜᴀʀᴀꜱʜᴛʀᴀ", "ᴍᴀɴɪᴘᴜʀ", "ᴍᴇɢʜᴀʟᴀʏᴀ", "ᴍɪᴢᴏʀᴀᴍ", "ɴᴀɢᴀʟᴀɴᴅ", 
    "ᴏᴅɪꜱʜᴀ", "ᴘᴜɴᴊᴀʙ", "ʀᴀᴊᴀꜱᴛʜᴀɴ", "ꜱɪᴋᴋɪᴍ", "ᴛᴀᴍɪʟ ɴᴀᴅᴜ", "ᴛᴇʟᴀɴɢᴀɴᴀ", "ᴛʀɪᴘᴜʀᴀ", 
    "ᴜᴛᴛᴀʀ ᴘʀᴀᴅᴇꜱʜ", "ᴜᴛᴛᴀʀᴀᴋʜᴀɴᴅ", "ᴡᴇꜱᴛ ʙᴇɴɢᴀʟ", "ᴀɴᴅᴀᴍᴀɴ & ɴɪᴄᴏʙᴀʀ ɪꜱʟᴀɴᴅꜱ", 
    "ᴄʜᴀɴᴅɪɢᴀʀʜ", "ᴅᴀᴅʀᴀ & ɴᴀɢᴀʀ ʜᴀᴠᴇʟɪ ᴀɴᴅ ᴅᴀᴍᴀɴ & ᴅɪᴜ", "ᴅᴇʟʜɪ", "ᴊᴀᴍᴍᴜ & ᴋᴀꜱʜᴍɪʀ", 
    "ʟᴀᴅᴀᴋʜ", "ʟᴀᴋꜱʜᴀᴅᴡᴇᴇᴘ", "ᴘᴜᴅᴜᴄʜᴇʀʀʏ"
]

def build_state_keyboard():
    keyboard = []
    for i in range(0, len(INDIAN_STATES_AND_UTS), 2):
        row = [InlineKeyboardButton(INDIAN_STATES_AND_UTS[i], callback_data=f"st_{INDIAN_STATES_AND_UTS[i]}")]
        if i + 1 < len(INDIAN_STATES_AND_UTS):
            row.append(InlineKeyboardButton(INDIAN_STATES_AND_UTS[i+1], callback_data=f"st_{INDIAN_STATES_AND_UTS[i+1]}"))
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

def build_year_keyboard(prefix="doby_"):
    current_year = datetime.now().year
    years = [str(y) for y in range(current_year - 45, current_year - 10)]
    keyboard = []
    for i in range(0, len(years), 4):
        row = [InlineKeyboardButton(y, callback_data=f"{prefix}{y}") for y in years[i:i+4]]
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

def build_month_keyboard(prefix="dobm_"):
    months = ["ᴊᴀɴ", "ꜰᴇʙ", "ᴍᴀʀ", "ᴀᴘʀ", "ᴍᴀʏ", "ᴊᴜɴ", "ᴊᴜʟ", "ᴀᴜɢ", "ꜱᴇᴘ", "ᴏᴄᴛ", "ɴᴏᴠ", "ᴅᴇᴄ"]
    keyboard = []
    for i in range(0, len(months), 3):
        row = [InlineKeyboardButton(m, callback_data=f"{prefix}{idx+1:02d}") for idx, m in enumerate(months[i:i+3], start=i)]
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

def build_day_keyboard(year: int, month: int, prefix="dobd_"):
    num_days = calendar.monthrange(year, month)[1]
    days = [f"{d:02d}" for d in range(1, num_days + 1)]
    keyboard = []
    for i in range(0, len(days), 7):
        row = [InlineKeyboardButton(d, callback_data=f"{prefix}{d}") for d in days[i:i+7]]
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

async def check_onboarding_maintenance(update: Update) -> bool:
    user_id = update.effective_user.id if update.effective_user else 0
    if user_id == PRIMARY_ADMIN_ID:
        return True

    m_until = get_maintenance_until()
    if int(time.time()) < m_until:
        msg = "🛠 **ᴀᴅᴍɪɴ ʜᴀꜱ ᴘᴀᴜꜱᴇᴅ ᴛʜᴇ ꜱᴇʀᴠɪᴄᴇ ᴄᴜʀʀᴇɴᴛʟʏ**\nᴘʟᴇᴀꜱᴇ ᴛʀʏ ᴀɢᴀɪɴ ꜱʜᴏʀᴛʟʏ ᴡʜᴇɴ ꜱᴇʀᴠɪᴄᴇꜱ ᴀʀᴇ ʀᴇꜱᴜᴍᴇᴅ!"
        if update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        elif update.message:
            await update.message.reply_text(msg)
        return False
    return True

async def start_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_onboarding_maintenance(update):
        return ConversationHandler.END

    user = update.effective_user
    args = context.args if context.args else []
    
    if args and args[0].startswith("ref_"):
        try:
            ref_id = int(args[0].replace("ref_", ""))
            context.user_data['referred_by'] = ref_id
        except ValueError:
            pass

    context.user_data["awaiting_other_exam"] = False

    profile = get_user_profile(user.id)
    if profile and profile.get("is_verified") and not context.user_data.get("is_editing_profile"):
        student_id = profile.get("student_id", "ɴ/ᴀ")
        await update.effective_message.reply_text(
            f"⚡ **ᴡᴇʟᴄᴏᴍᴇ ʙᴀᴄᴋ, {profile['full_name']}!**\n"
            f"🪪 **ꜱᴛᴜᴅᴇɴᴛ ɪᴅ:** `{student_id}`\n\n"
            f"🎯 **ᴛᴀʀɢᴇᴛ ᴇxᴀᴍ:** `{profile['target_exam']}`\n"
            f"📍 **ʟᴏᴄᴀᴛɪᴏɴ:** `{profile.get('state', 'ɴ/ᴀ')}, ɪɴᴅɪᴀ`\n\n"
            f"ᴄʟɪᴄᴋ ᴏᴘᴛɪᴏɴꜱ ʙᴇʟᴏᴡ ᴏʀ ᴜꜱᴇ ᴛʜᴇ ᴍᴀɪɴ ᴍᴇɴᴜ ᴛᴏ ꜱᴛᴀʀᴛ ᴘʀᴀᴄᴛɪᴄɪɴɢ!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 ʟᴀᴜɴᴄʜ qᴜɪᴢ", callback_data="cmd_quiz"), InlineKeyboardButton("👤 ᴘʀᴏꜰɪʟᴇ", callback_data="cmd_profile")],
                [InlineKeyboardButton("🥇 ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ", callback_data="cmd_toppers"), InlineKeyboardButton("📊 ᴍʏ ꜱᴛᴀᴛꜱ", callback_data="cmd_wholestate")]
            ]),
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    await update.effective_message.reply_text(
        f"{WELCOME_CARD_TEXT}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 **ꜱᴛᴜᴅᴇɴᴛ ʀᴇɢɪꜱᴛʀᴀᴛɪᴏɴ (ꜱᴛᴇᴘ 1/7)**\n\n"
        f"ᴘʟᴇᴀꜱᴇ ᴇɴᴛᴇʀ ʏᴏᴜʀ **ꜰᴜʟʟ ɴᴀᴍᴇ** (ᴀᴛ ʟᴇᴀꜱᴛ 4 ʟᴇᴛᴛᴇʀꜱ) ᴛᴏ ɪꜱꜱᴜᴇ ʏᴏᴜʀ ᴜɴɪqᴜᴇ ᴏꜰꜰɪᴄɪᴀʟ ꜱᴛᴜᴅᴇɴᴛ ɪᴅ:",
        parse_mode="Markdown"
    )
    return NAME

async def name_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    input_name = update.message.text.strip()
    clean_letters = "".join(filter(str.isalpha, input_name))

    if len(clean_letters) < 4:
        await update.message.reply_text(
            "⚠️ **ɴᴀᴍᴇ ᴛᴏᴏ ꜱʜᴏʀᴛ!**\n\n"
            "ʏᴏᴜʀ ɴᴀᴍᴇ ᴍᴜꜱᴛ ᴄᴏɴᴛᴀɪɴ ᴀᴛ ʟᴇᴀꜱᴛ 4 ᴀʟᴘʜᴀʙᴇᴛɪᴄ ᴄʜᴀʀᴀᴄᴛᴇʀꜱ ᴛᴏ ɪꜱꜱᴜᴇ ʏᴏᴜʀ ꜱᴛᴜᴅᴇɴᴛ ɪᴅ.\n"
            "ᴘʟᴇᴀꜱᴇ ᴇɴᴛᴇʀ ʏᴏᴜʀ ᴄᴏᴍᴘʟᴇᴛᴇ **ꜰᴜʟʟ ɴᴀᴍᴇ** ᴀɢᴀɪɴ:",
            parse_mode="Markdown"
        )
        return NAME

    context.user_data["full_name"] = input_name

    exams = [
        [InlineKeyboardButton("1. ꜱꜱᴄ ᴄɢʟ", callback_data="exam_SSC CGL"), InlineKeyboardButton("2. ꜱꜱᴄ ᴄʜꜱʟ", callback_data="exam_SSC CHSL")],
        [InlineKeyboardButton("3. ᴄᴀᴘꜰ ʜᴄᴍ", callback_data="exam_CAPF HCM"), InlineKeyboardButton("4. ᴀꜱɪ ꜱᴛᴇɴᴏ", callback_data="exam_ASI STENO")],
        [InlineKeyboardButton("5. ᴅᴘ ʜᴄᴍ", callback_data="exam_DP HCM"), InlineKeyboardButton("6. ʙꜱꜰ ʜᴄᴍ", callback_data="exam_BSF HCM")],
        [InlineKeyboardButton("7. ᴄɪꜱꜰ ʜᴄᴍ", callback_data="exam_CISF HCM"), InlineKeyboardButton("8. ʀᴀɪʟᴡᴀʏ ɴᴛᴘᴄ ᴜɢ", callback_data="exam_RAILWAY NTPC UG")],
        [InlineKeyboardButton("9. ʀᴀɪʟᴡᴀʏ ɴᴛᴘᴄ ɢʀᴀᴅᴜᴀᴛᴇ", callback_data="exam_RAILWAY NTPC GRADUATE")],
        [InlineKeyboardButton("10. ᴏᴛʜᴇʀ ᴇxᴀᴍ", callback_data="exam_OTHER")]
    ]

    await update.message.reply_text(
        f"✨ ɴɪᴄᴇ ᴛᴏ ᴍᴇᴇᴛ ʏᴏᴜ, *{context.user_data['full_name']}*!\n\n"
        f"🎯 **ᴛᴀʀɢᴇᴛ ᴇxᴀᴍ ꜱᴇʟᴇᴄᴛɪᴏɴ (ꜱᴛᴇᴘ 2/7):**\n"
        f"ᴘʟᴇᴀꜱᴇ ᴛᴀᴘ ʏᴏᴜʀ ᴛᴀʀɢᴇᴛᴇᴅ ᴇxᴀᴍɪɴᴀᴛɪᴏɴ ꜰʀᴏᴍ ᴛʜᴇ ᴏᴘᴛɪᴏɴꜱ ʙᴇʟᴏᴡ:",
        reply_markup=InlineKeyboardMarkup(exams),
        parse_mode="Markdown"
    )
    return EXAM

async def exam_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    selected_exam = query.data.replace("exam_", "")
    
    if selected_exam == "OTHER":
        context.user_data["awaiting_other_exam"] = True
        await query.edit_message_text("✍️ ᴘʟᴇᴀꜱᴇ ᴛʏᴘᴇ ᴛʜᴇ ᴇxᴀᴄᴛ ɴᴀᴍᴇ ᴏꜰ ʏᴏᴜʀ ᴛᴀʀɢᴇᴛ ᴇxᴀᴍ:")
        return EXAM

    context.user_data["target_exam"] = selected_exam
    context.user_data["country"] = "India"
    
    await query.edit_message_text(
        f"🎯 ꜱᴇʟᴇᴄᴛᴇᴅ ᴛᴀʀɢᴇᴛ: `{selected_exam}`\n\n"
        f"📍 **ɪɴᴅɪᴀɴ ꜱᴛᴀᴛᴇ / ᴜᴛ ꜱᴇʟᴇᴄᴛɪᴏɴ (ꜱᴛᴇᴘ 3/7):**\n"
        f"ᴘʟᴇᴀꜱᴇ ꜱᴇʟᴇᴄᴛ ʏᴏᴜʀ ꜱᴛᴀᴛᴇ ᴏʀ ᴜɴɪᴏɴ ᴛᴇʀʀɪᴛᴏʀʏ ꜰʀᴏᴍ ᴛʜᴇ ɪɴᴛᴇʀᴀᴄᴛɪᴠᴇ ʟɪꜱᴛ ʙᴇʟᴏᴡ:",
        reply_markup=build_state_keyboard(),
        parse_mode="Markdown"
    )
    return STATE

async def custom_exam_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_other_exam"):
        context.user_data["target_exam"] = update.message.text.strip()
        context.user_data["awaiting_other_exam"] = False
        context.user_data["country"] = "India"
        
        await update.message.reply_text(
            f"🎯 ꜱᴇʟᴇᴄᴛᴇᴅ ᴛᴀʀɢᴇᴛ: `{context.user_data['target_exam']}`\n\n"
            f"📍 **ɪɴᴅɪᴀɴ ꜱᴛᴀᴛᴇ / ᴜᴛ ꜱᴇʟᴇᴄᴛɪᴏɴ (ꜱᴛᴇᴘ 3/7):**\n"
            f"ᴘʟᴇᴀꜱᴇ ꜱᴇʟᴇᴄᴛ ʏᴏᴜʀ ꜱᴛᴀᴛᴇ ᴏʀ ᴜɴɪᴏɴ ᴛᴇʀʀɪᴛᴏʀʏ ꜰʀᴏᴍ ᴛʜᴇ ɪɴᴛᴇʀᴀᴄᴛɪᴠᴇ ʟɪꜱᴛ ʙᴇʟᴏᴡ:",
            reply_markup=build_state_keyboard(),
            parse_mode="Markdown"
        )
        return STATE

    # If the user sends regular text without tapping button or choosing Other Exam
    exams = [
        [InlineKeyboardButton("1. ꜱꜱᴄ ᴄɢʟ", callback_data="exam_SSC CGL"), InlineKeyboardButton("2. ꜱꜱᴄ ᴄʜꜱʟ", callback_data="exam_SSC CHSL")],
        [InlineKeyboardButton("3. ᴄᴀᴘꜰ ʜᴄᴍ", callback_data="exam_CAPF HCM"), InlineKeyboardButton("4. ᴀꜱɪ ꜱᴛᴇɴᴏ", callback_data="exam_ASI STENO")],
        [InlineKeyboardButton("5. ᴅᴘ ʜᴄᴍ", callback_data="exam_DP HCM"), InlineKeyboardButton("6. ʙꜱꜰ ʜᴄᴍ", callback_data="exam_BSF HCM")],
        [InlineKeyboardButton("7. ᴄɪꜱꜰ ʜᴄᴍ", callback_data="exam_CISF HCM"), InlineKeyboardButton("8. ʀᴀɪʟᴡᴀʏ ɴᴛᴘᴄ ᴜɢ", callback_data="exam_RAILWAY NTPC UG")],
        [InlineKeyboardButton("9. ʀᴀɪʟᴡᴀʏ ɴᴛᴘᴄ ɢʀᴀᴅᴜᴀᴛᴇ", callback_data="exam_RAILWAY NTPC GRADUATE")],
        [InlineKeyboardButton("10. ᴏᴛʜᴇʀ ᴇxᴀᴍ", callback_data="exam_OTHER")]
    ]
    await update.message.reply_text(
        "👇 ᴘʟᴇᴀꜱᴇ ᴛᴀᴘ ᴏɴᴇ ᴏꜰ ᴛʜᴇ ᴇxᴀᴍ ʙᴜᴛᴛᴏɴꜱ ʙᴇʟᴏᴡ, ᴏʀ ᴄʜᴏᴏꜱᴇ '10. ᴏᴛʜᴇʀ ᴇxᴀᴍ' ᴛᴏ ᴛʏᴘᴇ ᴄᴜꜱᴛᴏᴍ:",
        reply_markup=InlineKeyboardMarkup(exams)
    )
    return EXAM

async def state_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    selected_state = query.data.replace("st_", "")
    context.user_data["country"] = "India"
    context.user_data["state"] = selected_state

    contact_btn = KeyboardButton(text="📱 ꜱʜᴀʀᴇ ᴠᴇʀɪꜰɪᴇᴅ ᴍᴏʙɪʟᴇ ɴᴜᴍʙᴇʀ", request_contact=True)
    markup = ReplyKeyboardMarkup([[contact_btn]], one_time_keyboard=True, resize_keyboard=True)

    await query.delete_message()
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=f"📍 ꜱᴇʟᴇᴄᴛᴇᴅ ʟᴏᴄᴀᴛɪᴏɴ: `{selected_state}, ɪɴᴅɪᴀ`\n\n"
             f"📱 **ᴍᴏʙɪʟᴇ ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴ (ꜱᴛᴇᴘ 4/7):**\n"
             f"ᴛᴀᴘ ᴛʜᴇ **ꜱʜᴀʀᴇ ᴠᴇʀɪꜰɪᴇᴅ ᴍᴏʙɪʟᴇ ɴᴜᴍʙᴇʀ** ʙᴜᴛᴛᴏɴ ʙᴇʟᴏᴡ ᴛᴏ ᴄᴏᴍᴘʟᴇᴛᴇ ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴ:",
        reply_markup=markup,
        parse_mode="Markdown"
    )
    return PHONE

async def phone_contact_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.contact:
        contact_btn = KeyboardButton(text="📱 ꜱʜᴀʀᴇ ᴠᴇʀɪꜰɪᴇᴅ ᴍᴏʙɪʟᴇ ɴᴜᴍʙᴇʀ", request_contact=True)
        markup = ReplyKeyboardMarkup([[contact_btn]], one_time_keyboard=True, resize_keyboard=True)
        await update.message.reply_text(
            "⚠️ **ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴ ʀᴇqᴜɪʀᴇᴅ!**\n\n"
            "ᴛᴏ ᴘʀᴇᴠᴇɴᴛ ꜰᴀᴋᴇ ᴘʀᴏꜰɪʟᴇꜱ, ʏᴏᴜ ᴍᴜꜱᴛ ᴄʟɪᴄᴋ ᴛʜᴇ ʙᴜᴛᴛᴏɴ ʙᴇʟᴏᴡ ᴛᴏ ꜱʜᴀʀᴇ ʏᴏᴜʀ ᴠᴇʀɪꜰɪᴇᴅ ᴛᴇʟᴇɢʀᴀᴍ ᴍᴏʙɪʟᴇ ɴᴜᴍʙᴇʀ:",
            reply_markup=markup,
            parse_mode="Markdown"
        )
        return PHONE

    phone_num = update.message.contact.phone_number
    context.user_data["phone_number"] = phone_num

    gender_buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("ᴍᴀʟᴇ 👨", callback_data="gen_Male"), InlineKeyboardButton("ꜰᴇᴍᴀʟᴇ 👩", callback_data="gen_Female")]
    ])

    await update.message.reply_text(
        f"✅ ᴠᴇʀɪꜰɪᴇᴅ ᴍᴏʙɪʟᴇ: `{phone_num}`\n\n"
        f"👤 **ꜱᴇʟᴇᴄᴛ ɢᴇɴᴅᴇʀ (ꜱᴛᴇᴘ 5/7):**",
        reply_markup=gender_buttons,
        parse_mode="Markdown"
    )
    return GENDER

async def gender_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    selected_gender = query.data.replace("gen_", "")
    context.user_data["gender"] = selected_gender

    await query.edit_message_text(
        f"👤 ɢᴇɴᴅᴇʀ: `{selected_gender}`\n\n"
        f"🎂 **ꜱᴇʟᴇᴄᴛ ʙɪʀᴛʜ ʏᴇᴀʀ (ꜱᴛᴇᴘ 6/7):**\n"
        f"ᴘʟᴇᴀꜱᴇ ᴛᴀᴘ ʏᴏᴜʀ ʙɪʀᴛʜ ʏᴇᴀʀ ꜰʀᴏᴍ ʙᴇʟᴏᴡ ᴛᴏ ɪꜱꜱᴜᴇ ʏᴏᴜʀ ꜱᴛᴜᴅᴇɴᴛ ɪᴅ:",
        reply_markup=build_year_keyboard(),
        parse_mode="Markdown"
    )
    return DOB_YEAR

async def dob_year_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    selected_year = query.data.replace("doby_", "")
    context.user_data["birth_year"] = selected_year

    await query.edit_message_text(
        f"📅 **ꜱᴇʟᴇᴄᴛᴇᴅ ʙɪʀᴛʜ ʏᴇᴀʀ:** `{selected_year}`\n\n"
        f"🗓 **ꜱᴇʟᴇᴄᴛ ʙɪʀᴛʜ ᴍᴏɴᴛʜ:**\n"
        f"ᴘʟᴇᴀꜱᴇ ᴛᴀᴘ ʏᴏᴜʀ ᴍᴏɴᴛʜ ᴏꜰ ʙɪʀᴛʜ ʙᴇʟᴏᴡ:",
        reply_markup=build_month_keyboard(),
        parse_mode="Markdown"
    )
    return DOB_MONTH

async def dob_month_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    selected_month = query.data.replace("dobm_", "")
    context.user_data["birth_month"] = selected_month
    
    selected_year = int(context.user_data.get("birth_year", "2002"))
    selected_month_int = int(selected_month)

    await query.edit_message_text(
        f"📅 **ꜱᴇʟᴇᴄᴛᴇᴅ ʙɪʀᴛʜ ᴘᴇʀɪᴏᴅ:** `{selected_month}/{selected_year}`\n\n"
        f"🗓 **ꜱᴇʟᴇᴄᴛ ᴇxᴀᴄᴛ ʙɪʀᴛʜ ᴅᴀᴛᴇ (ᴅᴀʏ):**\n"
        f"ᴘʟᴇᴀꜱᴇ ᴛᴀᴘ ʏᴏᴜʀ ᴇxᴀᴄᴛ ᴅᴀʏ ᴏꜰ ʙɪʀᴛʜ ꜰʀᴏᴍ ʙᴇʟᴏᴡ:",
        reply_markup=build_day_keyboard(selected_year, selected_month_int),
        parse_mode="Markdown"
    )
    return DOB_DAY

async def dob_day_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    selected_day = query.data.replace("dobd_", "")
    birth_year = context.user_data.get("birth_year", "2002")
    birth_month = context.user_data.get("birth_month", "01")
    
    dob_str = f"{selected_day}-{birth_month}-{birth_year}"
    context.user_data["dob_str"] = dob_str

    await query.edit_message_text(
        f"🎂 **ᴅᴏʙ ꜱᴇʟᴇᴄᴛᴇᴅ:** `{dob_str}`\n\n"
        f"🔑 **ᴀᴄᴄᴏᴜɴᴛ ꜱᴇᴄᴜʀɪᴛʏ (ꜱᴛᴇᴘ 7/7):**\n"
        f"ᴘʟᴇᴀꜱᴇ ꜱᴇᴛ ᴀ ꜱᴇᴄʀᴇᴛ **4-ᴅɪɢɪᴛ ᴘɪɴ** ꜰᴏʀ ʏᴏᴜʀ ᴀᴄᴄᴏᴜɴᴛ (ᴇ.ɢ. `4321`):",
        parse_mode="Markdown"
    )
    return PIN_SETUP

async def pin_setup_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pin_input = update.message.text.strip()
    if not pin_input.isdigit() or len(pin_input) != 4:
        await update.message.reply_text("⚠️ ᴘɪɴ ᴍᴜꜱᴛ ʙᴇ ᴇxᴀᴄᴛʟʏ **4 ɴᴜᴍᴇʀɪᴄ ᴅɪɢɪᴛꜱ** (ᴇ.ɢ. 4321). ᴘʟᴇᴀꜱᴇ ᴛʀʏ ᴀɢᴀɪɴ:")
        return PIN_SETUP

    context.user_data["pin"] = pin_input

    sec_buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton(q, callback_data=f"secq_{idx}")] for idx, q in enumerate(PRESET_SEC_QUESTIONS)
    ])

    await update.message.reply_text(
        f"🛡 **ꜱᴇʟᴇᴄᴛ ꜱᴇᴄᴜʀɪᴛʏ ʀᴇᴄᴏᴠᴇʀʏ qᴜᴇꜱᴛɪᴏɴ:**\n"
        f"ᴄʜᴏᴏꜱᴇ ᴀ qᴜᴇꜱᴛɪᴏɴ ꜰʀᴏᴍ ʙᴇʟᴏᴡ ᴛᴏ ᴜꜱᴇ ꜰᴏʀ ᴘɪɴ ʀᴇᴄᴏᴠᴇʀʏ:",
        reply_markup=sec_buttons,
        parse_mode="Markdown"
    )
    return SEC_QUESTION

async def sec_q_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    q_idx = int(query.data.replace("secq_", ""))
    selected_q = PRESET_SEC_QUESTIONS[q_idx]
    context.user_data["security_question"] = selected_q

    await query.edit_message_text(
        f"🛡 **ꜱᴇᴄᴜʀɪᴛʏ qᴜᴇꜱᴛɪᴏɴ:** *{selected_q}*\n\n"
        f"ᴘʟᴇᴀꜱᴇ ʀᴇᴘʟʏ ᴡɪᴛʜ ʏᴏᴜʀ ꜱᴇᴄʀᴇᴛ ᴀɴꜱᴡᴇʀ ʙᴇʟᴏᴡ:",
        parse_mode="Markdown"
    )
    return SEC_ANSWER

async def sec_ans_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ans_input = update.message.text.strip()
    context.user_data["security_answer"] = ans_input

    user = update.effective_user
    full_name = context.user_data.get("full_name", user.full_name)
    dob_str = context.user_data.get("dob_str", "15-08-2000")
    birth_year = dob_str.split("-")[-1]
    calc_age = datetime.now().year - int(birth_year)

    student_id = generate_student_id(full_name, dob_str)
    context.user_data["is_editing_profile"] = False

    save_user_profile(
        user_id=user.id,
        full_name=full_name,
        username=user.username or "ɴ/ᴀ",
        phone=context.user_data.get("phone_number", "ɴ/ᴀ"),
        target_exam=context.user_data.get("target_exam", "ɢᴇɴᴇʀᴀʟ"),
        dob=dob_str,
        age=calc_age,
        gender=context.user_data.get("gender", "ɴᴏᴛ ꜱᴘᴇᴄɪꜰɪᴇᴅ"),
        pin=context.user_data.get("pin", "1234"),
        sec_q=context.user_data.get("security_question", "ᴅᴇꜰᴀᴜʟᴛ"),
        sec_a=ans_input,
        country="India",
        state=context.user_data.get("state", "ɴ/ᴀ"),
        referred_by=context.user_data.get("referred_by")
    )

    await update.message.reply_text(
        f"🎉 **ꜱᴛᴜᴅᴇɴᴛ ʀᴇɢɪꜱᴛʀᴀᴛɪᴏɴ ᴄᴏᴍᴘʟᴇᴛᴇ!**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🪪 **ᴏꜰꜰɪᴄɪᴀʟ ꜱᴛᴜᴅᴇɴᴛ ɪᴅ ɪꜱꜱᴜᴇᴅ:** `{student_id}`\n"
        f"🔑 **ꜱᴇᴄʀᴇᴛ ᴘɪɴ:** `{context.user_data.get('pin')}`\n"
        f"🎂 **ᴅᴏʙ ʀᴇɢɪꜱᴛᴇʀᴇᴅ:** `{dob_str}`\n\n"
        f"✅ ʏᴏᴜʀ ꜱᴛᴜᴅᴇɴᴛ ᴘʀᴏꜰɪʟᴇ ʜᴀꜱ ʙᴇᴇɴ ᴠᴇʀɪꜰɪᴇᴅ ᴀɴᴅ ꜱᴀᴠᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ! ʏᴏᴜ ᴄᴀɴ ᴠɪᴇᴡ ᴏʀ ᴜᴘᴅᴀᴛᴇ ʏᴏᴜʀ ᴅᴇᴛᴀɪʟꜱ ᴀɴʏᴛɪᴍᴇ ɪɴ ʏᴏᴜʀ **ᴘʀᴏꜰɪʟᴇ ᴄᴀʀᴅ** (/myprofile).\n\n"
        f"👉 ᴛᴀᴘ **ʟᴀᴜɴᴄʜ qᴜɪᴢ** ʙᴇʟᴏᴡ ᴏʀ ᴜꜱᴇ ᴛʜᴇ ᴍᴀɪɴ ᴍᴇɴᴜ ᴛᴏ ʙᴇɢɪɴ ʟᴇᴀʀɴɪɴɢ!",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    await context.bot.send_message(
        chat_id=update.message.chat_id,
        text="👇 **qᴜɪᴄᴋ ɴᴀᴠɪɢᴀᴛɪᴏɴ:**",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 ʟᴀᴜɴᴄʜ qᴜɪᴢ", callback_data="cmd_quiz"), InlineKeyboardButton("👤 ᴘʀᴏꜰɪʟᴇ", callback_data="cmd_profile")]
        ])
    )
    return ConversationHandler.END

async def edit_profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_onboarding_maintenance(update):
        return ConversationHandler.END

    user = update.effective_user
    can_edit, days_left = can_user_edit_profile(user.id)
    
    if not can_edit:
        msg = f"⏳ **ᴘʀᴏꜰɪʟᴇ ᴇᴅɪᴛ ʟᴏᴄᴋᴇᴅ!**\n\nʏᴏᴜ ᴄᴀɴ ᴏɴʟʏ ᴜᴘᴅᴀᴛᴇ ʏᴏᴜʀ ᴘʀᴏꜰɪʟᴇ ᴅᴇᴛᴀɪʟꜱ ᴏɴᴄᴇ ᴇᴠᴇʀʏ 30 ᴅᴀʏꜱ.\nᴘʟᴇᴀꜱᴇ ᴛʀʏ ᴀɢᴀɪɴ ɪɴ `{days_left} ᴅᴀʏꜱ`."
        if update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        else:
            await update.message.reply_text(msg, parse_mode="Markdown")
        return ConversationHandler.END

    warn_msg = (
        "⚠️ **ᴘʀᴏꜰɪʟᴇ ᴇᴅɪᴛ ᴡᴀʀɴɪɴɢ**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "ᴘʟᴇᴀꜱᴇ ɴᴏᴛᴇ: ʏᴏᴜ ᴀʀᴇ ᴀʟʟᴏᴡᴇᴅ ᴛᴏ ᴇᴅɪᴛ ʏᴏᴜʀ ꜱᴛᴜᴅᴇɴᴛ ᴘʀᴏꜰɪʟᴇ ᴅᴇᴛᴀɪʟꜱ **ᴏɴʟʏ ᴏɴᴄᴇ ᴇᴠᴇʀʏ 30 ᴅᴀʏꜱ**.\n\n"
        "ᴀʀᴇ ʏᴏᴜ ꜱᴜʀᴇ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ᴘʀᴏᴄᴇᴇᴅ ᴡɪᴛʜ ᴜᴘᴅᴀᴛɪɴɢ ʏᴏᴜʀ ᴘʀᴏꜰɪʟᴇ ɴᴏᴡ?"
    )
    warn_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ ʏᴇꜱ, ᴘʀᴏᴄᴇᴇᴅ ᴛᴏ ᴇᴅɪᴛ", callback_data="edit_confirm_yes")],
        [InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ ᴇᴅɪᴛ", callback_data="edit_confirm_no")]
    ])

    if update.callback_query:
        await update.callback_query.message.reply_text(warn_msg, reply_markup=warn_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(warn_msg, reply_markup=warn_markup, parse_mode="Markdown")
    
    return EDIT_WARN

async def edit_warn_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "edit_confirm_no":
        await query.edit_message_text("❌ ᴘʀᴏꜰɪʟᴇ ᴜᴘᴅᴀᴛᴇ ᴄᴀɴᴄᴇʟʟᴇᴅ.")
        return ConversationHandler.END

    context.user_data["is_editing_profile"] = True
    await query.edit_message_text(
        "✏️ **ᴇᴅɪᴛ ᴘʀᴏꜰɪʟᴇ ꜱᴇꜱꜱɪᴏɴ ꜱᴛᴀʀᴛᴇᴅ (ꜱᴛᴇᴘ 1/7)**\n\n"
        "ᴘʟᴇᴀꜱᴇ ᴇɴᴛᴇʀ ʏᴏᴜʀ ᴜᴘᴅᴀᴛᴇᴅ **ꜰᴜʟʟ ɴᴀᴍᴇ** (ᴀᴛ ʟᴇᴀꜱᴛ 4 ʟᴇᴛᴛᴇʀꜱ):",
        parse_mode="Markdown"
    )
    return NAME

async def recovery_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    u = context.user_data.get("login_target_user")
    if not u:
        user = update.effective_user
        u = get_user_profile(user.id)

    if not u:
        await query.edit_message_text("⚠️ ꜱᴇꜱꜱɪᴏɴ ᴇxᴘɪʀᴇᴅ. ᴘʟᴇᴀꜱᴇ ᴛʏᴘᴇ /start ᴛᴏ ʟᴏɢ ɪɴ ᴀɢᴀɪɴ.")
        return ConversationHandler.END

    context.user_data["login_target_user"] = u

    rec_options = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛡 ꜱᴇᴄᴜʀɪᴛʏ qᴜᴇꜱᴛɪᴏɴ", callback_data="rec_opt_secq")],
        [InlineKeyboardButton("📱 ᴠᴇʀɪꜰɪᴇᴅ ᴘʜᴏɴᴇ ɴᴜᴍʙᴇʀ", callback_data="rec_opt_phone")],
        [InlineKeyboardButton("🎂 ᴅᴏʙ + ɴᴀᴍᴇ ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴ", callback_data="rec_opt_namedob")],
        [InlineKeyboardButton("🗓 ᴅᴏʙ ɢʀɪᴅ ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴ", callback_data="rec_opt_dob")]
    ])

    await query.edit_message_text(
        f"🛡 **ᴘɪɴ & ᴀᴄᴄᴏᴜɴᴛ ʀᴇꜱᴇᴛ ᴘᴏʀᴛᴀʟ**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"ᴀᴄᴄᴏᴜɴᴛ: `{u['full_name']}` (`{u['student_id']}`)\n\n"
        f"ꜱᴇʟᴇᴄᴛ ᴀɴ ᴀᴜᴛʜᴇɴᴛɪᴄᴀᴛɪᴏɴ ᴍᴇᴛʜᴏᴅ ʙᴇʟᴏᴡ ᴛᴏ ʀᴇꜱᴇᴛ ʏᴏᴜʀ ꜱᴇᴄʀᴇᴛ 4-ᴅɪɢɪᴛ ᴘɪɴ:",
        reply_markup=rec_options,
        parse_mode="Markdown"
    )
    return RECOVERY_MENU

async def recovery_option_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    u = context.user_data.get("login_target_user")

    if data == "rec_opt_secq":
        sec_q = u.get("security_question", "ᴅᴇꜰᴀᴜʟᴛ ꜱᴇᴄᴜʀɪᴛʏ qᴜᴇꜱᴛɪᴏɴ")
        await query.edit_message_text(
            f"🛡 **ꜱᴇᴄᴜʀɪᴛʏ qᴜᴇꜱᴛɪᴏɴ ʀᴇᴄᴏᴠᴇʀʏ**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"❓ **qᴜᴇꜱᴛɪᴏɴ:** *{sec_q}*\n\n"
            f"ᴘʟᴇᴀꜱᴇ ʀᴇᴘʟʏ ᴡɪᴛʜ ʏᴏᴜʀ ᴀɴꜱᴡᴇʀ ʙᴇʟᴏᴡ:",
            parse_mode="Markdown"
        )
        return REC_SEC_ANS

    elif data == "rec_opt_phone":
        contact_btn = KeyboardButton(text="📱 ꜱʜᴀʀᴇ ᴠᴇʀɪꜰɪᴇᴅ ᴍᴏʙɪʟᴇ ɴᴜᴍʙᴇʀ", request_contact=True)
        markup = ReplyKeyboardMarkup([[contact_btn]], one_time_keyboard=True, resize_keyboard=True)
        await query.delete_message()
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"📱 **ᴘʜᴏɴᴇ ɴᴜᴍʙᴇʀ ʀᴇᴄᴏᴠᴇʀʏ**\n\nᴛᴀᴘ ᴛʜᴇ ʙᴜᴛᴛᴏɴ ʙᴇʟᴏᴡ ᴛᴏ ꜱʜᴀʀᴇ ʏᴏᴜʀ ᴠᴇʀɪꜰɪᴇᴅ ᴍᴏʙɪʟᴇ ɴᴜᴍʙᴇʀ ꜰᴏʀ ᴍᴀᴛᴄʜ:",
            reply_markup=markup
        )
        return REC_PHONE

    elif data == "rec_opt_namedob":
        await query.edit_message_text(
            f"👤 **ɴᴀᴍᴇ & ᴅᴏʙ ʀᴇᴄᴏᴠᴇʀʏ**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"ᴘʟᴇᴀꜱᴇ ʀᴇᴘʟʏ ᴡɪᴛʜ ʏᴏᴜʀ **ʀᴇɢɪꜱᴛᴇʀᴇᴅ ꜰᴜʟʟ ɴᴀᴍᴇ** (ᴏʀ ᴅᴏʙ ꜰᴏʀᴍᴀᴛᴛᴇᴅ ᴀꜱ ᴅᴅ-ᴍᴍ-ʏʏʏʏ):",
            parse_mode="Markdown"
        )
        return REC_NAME_DOB

    elif data == "rec_opt_dob":
        await query.edit_message_text(
            f"🎂 **ᴅᴀᴛᴇ ᴏꜰ ʙɪʀᴛʜ ʀᴇᴄᴏᴠᴇʀʏ**\n\nꜱᴇʟᴇᴄᴛ ʏᴏᴜʀ ʀᴇɢɪꜱᴛᴇʀᴇᴅ **ʙɪʀᴛʜ ʏᴇᴀʀ**:",
            reply_markup=build_year_keyboard(prefix="recdoby_"),
            parse_mode="Markdown"
        )
        return REC_DOB_YEAR

async def rec_sec_ans_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ans_input = update.message.text.strip().lower()
    u = context.user_data.get("login_target_user")
    correct_ans = str(u.get("security_answer", "")).strip().lower()

    if ans_input != correct_ans:
        rec_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 ʀᴇꜱᴇᴛ ʏᴏᴜʀ ᴘɪɴ / ᴘᴀꜱꜱᴡᴏʀᴅ", callback_data="login_forgot_pin")]])
        await update.message.reply_text("❌ **ɪɴᴄᴏʀʀᴇᴄᴛ ꜱᴇᴄᴜʀɪᴛʏ ᴀɴꜱᴡᴇʀ!**\n\nᴘʟᴇᴀꜱᴇ ᴛʀʏ ᴀɢᴀɪɴ ᴏʀ ᴛᴀᴘ ʙᴇʟᴏᴡ ᴛᴏ ʀᴇꜱᴇᴛ ᴜꜱɪɴɢ ᴀɴᴏᴛʜᴇʀ ᴍᴇᴛʜᴏᴅ:", reply_markup=rec_btn)
        return REC_SEC_ANS

    await update.message.reply_text(
        f"✅ **ɪᴅᴇɴᴛɪᴛʏ ᴠᴇʀɪꜰɪᴇᴅ!**\n\n"
        f"👤 **ꜱᴛᴜᴅᴇɴᴛ ɴᴀᴍᴇ:** {u['full_name']}\n"
        f"🪪 **ꜱᴛᴜᴅᴇɴᴛ ɪᴅ:** `{u['student_id']}`\n\n"
        f"ᴘʟᴇᴀꜱᴇ ᴇɴᴛᴇʀ ʏᴏᴜʀ **ɴᴇᴡ ꜱᴇᴄʀᴇᴛ 4-ᴅɪɢɪᴛ ᴘɪɴ** ʙᴇʟᴏᴡ:",
        parse_mode="Markdown"
    )
    return RESET_PIN

async def rec_phone_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.contact:
        contact_btn = KeyboardButton(text="📱 ꜱʜᴀʀᴇ ᴠᴇʀɪꜰɪᴇᴅ ᴍᴏʙɪʟᴇ ɴᴜᴍʙᴇʀ", request_contact=True)
        markup = ReplyKeyboardMarkup([[contact_btn]], one_time_keyboard=True, resize_keyboard=True)
        await update.message.reply_text("⚠️ ʏᴏᴜ ᴍᴜꜱᴛ ᴛᴀᴘ ᴛʜᴇ ʙᴜᴛᴛᴏɴ ʙᴇʟᴏᴡ ᴛᴏ ꜱʜᴀʀᴇ ʏᴏᴜʀ ᴄᴏɴᴛᴀᴄᴛ ɴᴜᴍʙᴇʀ:", reply_markup=markup)
        return REC_PHONE

    shared_phone = update.message.contact.phone_number.replace("+", "").strip()
    u = context.user_data.get("login_target_user")
    user_phone = str(u.get("phone_number", "")).replace("+", "").strip()

    if shared_phone != user_phone:
        rec_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 ʀᴇꜱᴇᴛ ʏᴏᴜʀ ᴘɪɴ / ᴘᴀꜱꜱᴡᴏʀᴅ", callback_data="login_forgot_pin")]])
        await update.message.reply_text(
            f"❌ **ᴘʜᴏɴᴇ ɴᴜᴍʙᴇʀ ᴍɪꜱᴍᴀᴛᴄʜ!** ꜱʜᴀʀᴇᴅ ɴᴜᴍʙᴇʀ ᴅᴏᴇꜱ ɴᴏᴛ ᴍᴀᴛᴄʜ ʀᴇɢɪꜱᴛᴇʀᴇᴅ ɴᴜᴍʙᴇʀ ꜰᴏʀ `{u['student_id']}`.",
            reply_markup=rec_btn
        )
        return REC_PHONE

    await update.message.reply_text(
        f"✅ **ᴘʜᴏɴᴇ ᴠᴇʀɪꜰɪᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ!**\n\n"
        f"👤 **ꜱᴛᴜᴅᴇɴᴛ ɴᴀᴍᴇ:** {u['full_name']}\n"
        f"🪪 **ꜱᴛᴜᴅᴇɴᴛ ɪᴅ:** `{u['student_id']}`\n\n"
        f"ᴘʟᴇᴀꜱᴇ ᴇɴᴛᴇʀ ʏᴏᴜʀ **ɴᴇᴡ ꜱᴇᴄʀᴇᴛ 4-ᴅɪɢɪᴛ ᴘɪɴ** ʙᴇʟᴏᴡ:",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    return RESET_PIN

async def rec_name_dob_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text_input = update.message.text.strip().lower()
    u = context.user_data.get("login_target_user")
    
    reg_name = str(u.get("full_name", "")).strip().lower()
    reg_dob = str(u.get("dob", "")).strip().lower()

    if text_input in reg_name or text_input == reg_dob:
        await update.message.reply_text(
            f"✅ **ɴᴀᴍᴇ / ᴅᴏʙ ᴠᴇʀɪꜰɪᴇᴅ!**\n\n"
            f"👤 **ꜱᴛᴜᴅᴇɴᴛ ɴᴀᴍᴇ:** {u['full_name']}\n"
            f"🪪 **ꜱᴛᴜᴅᴇɴᴛ ɪᴅ:** `{u['student_id']}`\n\n"
            f"ᴘʟᴇᴀꜱᴇ ᴇɴᴛᴇʀ ʏᴏᴜʀ **ɴᴇᴡ ꜱᴇᴄʀᴇᴛ 4-ᴅɪɢɪᴛ ᴘɪɴ** ʙᴇʟᴏᴡ:",
            parse_mode="Markdown"
        )
        return RESET_PIN

    rec_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 ʀᴇꜱᴇᴛ ʏᴏᴜʀ ᴘɪɴ / ᴘᴀꜱꜱᴡᴏʀᴅ", callback_data="login_forgot_pin")]])
    await update.message.reply_text(
        "❌ **ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴ ꜰᴀɪʟᴇᴅ!** ɪɴᴘᴜᴛ ᴅᴏᴇꜱ ɴᴏᴛ ᴍᴀᴛᴄʜ ʀᴇɢɪꜱᴛᴇʀᴇᴅ ʀᴇᴄᴏʀᴅꜱ. ᴛʀʏ ᴀɢᴀɪɴ ᴏʀ ᴘɪᴄᴋ ᴀɴᴏᴛʜᴇʀ ᴍᴇᴛʜᴏᴅ:",
        reply_markup=rec_btn
    )
    return REC_NAME_DOB

async def rec_dob_year_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    selected_year = query.data.replace("recdoby_", "")
    context.user_data["rec_birth_year"] = selected_year

    await query.edit_message_text(
        f"📅 **ʏᴇᴀʀ ꜱᴇʟᴇᴄᴛᴇᴅ:** `{selected_year}`\n\nꜱᴇʟᴇᴄᴛ ʏᴏᴜʀ ʀᴇɢɪꜱᴛᴇʀᴇᴅ **ʙɪʀᴛʜ ᴍᴏɴᴛʜ**:",
        reply_markup=build_month_keyboard(prefix="recdobm_"),
        parse_mode="Markdown"
    )
    return REC_DOB_MONTH

async def rec_dob_month_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    selected_month = query.data.replace("recdobm_", "")
    context.user_data["rec_birth_month"] = selected_month
    
    y = int(context.user_data.get("rec_birth_year", "2002"))
    m = int(selected_month)

    await query.edit_message_text(
        f"📅 **ᴘᴇʀɪᴏᴅ ꜱᴇʟᴇᴄᴛᴇᴅ:** `{selected_month}/{y}`\n\nꜱᴇʟᴇᴄᴛ ʏᴏᴜʀ ʀᴇɢɪꜱᴛᴇʀᴇᴅ **ʙɪʀᴛʜ ᴅᴀʏ**:",
        reply_markup=build_day_keyboard(y, m, prefix="recdobd_"),
        parse_mode="Markdown"
    )
    return REC_DOB_DAY

async def rec_dob_day_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    selected_day = query.data.replace("recdobd_", "")
    
    y = context.user_data.get("rec_birth_year")
    m = context.user_data.get("rec_birth_month")
    dob_constructed = f"{selected_day}-{m}-{y}"

    u = context.user_data.get("login_target_user")
    if dob_constructed != u.get("dob"):
        rec_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 ʀᴇꜱᴇᴛ ᴏᴘᴛɪᴏɴꜱ", callback_data="login_forgot_pin")]])
        await query.edit_message_text(f"❌ **ᴅᴏʙ ᴍɪꜱᴍᴀᴛᴄʜ!** ʀᴇɢɪꜱᴛᴇʀᴇᴅ ᴅᴏʙ ᴅᴏᴇꜱ ɴᴏᴛ ᴍᴀᴛᴄʜ `{dob_constructed}`.", reply_markup=rec_btn)
        return RECOVERY_MENU

    await query.delete_message()
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=f"✅ **ᴅᴏʙ ᴠᴇʀɪꜰɪᴇᴅ!**\n\n👤 **ꜱᴛᴜᴅᴇɴᴛ ɴᴀᴍᴇ:** {u['full_name']}\n🪪 **ꜱᴛᴜᴅᴇɴᴛ ɪᴅ:** `{u['student_id']}`\n\nᴘʟᴇᴀꜱᴇ ᴇɴᴛᴇʀ ʏᴏᴜʀ **ɴᴇᴡ ꜱᴇᴄʀᴇᴛ 4-ᴅɪɢɪᴛ ᴘɪɴ** ʙᴇʟᴏᴡ:",
        parse_mode="Markdown"
    )
    return RESET_PIN

async def reset_pin_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_pin = update.message.text.strip()
    if not new_pin.isdigit() or len(new_pin) != 4:
        await update.message.reply_text("⚠️ ᴘɪɴ ᴍᴜꜱᴛ ʙᴇ ᴇxᴀᴄᴛʟʏ **4 ɴᴜᴍᴇʀɪᴄ ᴅɪɢɪᴛꜱ** (ᴇ.ɢ. 1234). ᴘʟᴇᴀꜱᴇ ᴛʀʏ ᴀɢᴀɪɴ:")
        return RESET_PIN

    u = context.user_data.get("login_target_user")
    if not u:
        user = update.effective_user
        u = get_user_profile(user.id)

    target_uid = u['user_id']
    update_user_pin(target_uid, new_pin)

    await update.message.reply_text(
        f"🎉 **ᴘɪɴ ʀᴇꜱᴇᴛ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟ!**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 **ꜱᴛᴜᴅᴇɴᴛ ɴᴀᴍᴇ:** {u['full_name']}\n"
        f"🪪 **ꜱᴛᴜᴅᴇɴᴛ ɪᴅ:** `{u['student_id']}`\n"
        f"🔑 **ʏᴏᴜʀ ɴᴇᴡ ꜱᴇᴄʀᴇᴛ ᴘɪɴ:** `{new_pin}`\n\n"
        f"ʏᴏᴜʀ ᴏʀɪɢɪɴᴀʟ ᴀᴄᴄᴏᴜɴᴛ ʀᴇᴍᴀɪɴꜱ 100% ᴀᴄᴛɪᴠᴇ ᴡɪᴛʜ ᴀʟʟ ꜱᴄᴏʀᴇꜱ, ꜱᴀᴠᴇᴅ qᴜᴇꜱᴛɪᴏɴꜱ, ᴀɴᴅ ʟɪᴍɪᴛꜱ ꜰᴜʟʟʏ ɪɴᴛᴀᴄᴛ.\n\n"
        f"👉 ᴛᴀᴘ **ʟᴀᴜɴᴄʜ qᴜɪᴢ** ʙᴇʟᴏᴡ ᴏʀ ᴜꜱᴇ /quiz ᴛᴏ ᴄᴏɴᴛɪɴᴜᴇ ᴘʀᴀᴄᴛɪᴄɪɴɢ!",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 ʟᴀᴜɴᴄʜ qᴜɪᴢ", callback_data="cmd_quiz"), InlineKeyboardButton("👤 ᴘʀᴏꜰɪʟᴇ", callback_data="cmd_profile")]
        ]),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def cancel_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["is_editing_profile"] = False
    await update.message.reply_text("ꜱᴇᴛᴜᴘ ᴄᴀɴᴄᴇʟʟᴇᴅ. ᴛʏᴘᴇ /start ᴀɴʏᴛɪᴍᴇ ᴛᴏ ʙᴇɢɪɴ ʀᴇɢɪꜱᴛʀᴀᴛɪᴏɴ.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

def get_onboarding_handler():
    return ConversationHandler(
        entry_points=[
            CommandHandler("start", start_onboarding),
            CommandHandler("editprofile", edit_profile_command),
            CallbackQueryHandler(edit_profile_command, pattern="^cmd_editprofile$"),
            CallbackQueryHandler(start_onboarding, pattern="^trigger_start$"),
            CallbackQueryHandler(recovery_menu_callback, pattern="^login_forgot_pin$")
        ],
        states={
            RECOVERY_MENU: [
                CallbackQueryHandler(recovery_option_router, pattern="^rec_opt_")
            ],
            REC_SEC_ANS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, rec_sec_ans_step),
                CallbackQueryHandler(recovery_menu_callback, pattern="^login_forgot_pin$")
            ],
            REC_PHONE: [
                MessageHandler(filters.CONTACT | (filters.TEXT & ~filters.COMMAND), rec_phone_step),
                CallbackQueryHandler(recovery_menu_callback, pattern="^login_forgot_pin$")
            ],
            REC_NAME_DOB: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, rec_name_dob_step),
                CallbackQueryHandler(recovery_menu_callback, pattern="^login_forgot_pin$")
            ],
            REC_DOB_YEAR: [
                CallbackQueryHandler(rec_dob_year_callback, pattern="^recdoby_"),
                CallbackQueryHandler(recovery_menu_callback, pattern="^login_forgot_pin$")
            ],
            REC_DOB_MONTH: [
                CallbackQueryHandler(rec_dob_month_callback, pattern="^recdobm_"),
                CallbackQueryHandler(recovery_menu_callback, pattern="^login_forgot_pin$")
            ],
            REC_DOB_DAY: [
                CallbackQueryHandler(rec_dob_day_callback, pattern="^recdobd_"),
                CallbackQueryHandler(recovery_menu_callback, pattern="^login_forgot_pin$")
            ],
            RESET_PIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, reset_pin_step)],

            EDIT_WARN: [CallbackQueryHandler(edit_warn_callback, pattern="^edit_confirm_")],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, name_step)],
            EXAM: [
                CallbackQueryHandler(exam_callback, pattern="^exam_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, custom_exam_text)
            ],
            STATE: [CallbackQueryHandler(state_callback, pattern="^st_")],
            PHONE: [MessageHandler(filters.CONTACT | (filters.TEXT & ~filters.COMMAND), phone_contact_step)],
            GENDER: [CallbackQueryHandler(gender_callback, pattern="^gen_")],
            DOB_YEAR: [CallbackQueryHandler(dob_year_callback, pattern="^doby_")],
            DOB_MONTH: [CallbackQueryHandler(dob_month_callback, pattern="^dobm_")],
            DOB_DAY: [CallbackQueryHandler(dob_day_callback, pattern="^dobd_")],
            PIN_SETUP: [MessageHandler(filters.TEXT & ~filters.COMMAND, pin_setup_step)],
            SEC_QUESTION: [CallbackQueryHandler(sec_q_callback, pattern="^secq_")],
            SEC_ANSWER: [MessageHandler(filters.TEXT & ~filters.COMMAND, sec_ans_step)]
        },
        fallbacks=[
            CommandHandler("start", start_onboarding),
            CommandHandler("cancel", cancel_onboarding),
            CallbackQueryHandler(recovery_menu_callback, pattern="^login_forgot_pin$")
        ],
        allow_reentry=True,
        per_chat=True,
        per_user=True,
        per_message=False
    )