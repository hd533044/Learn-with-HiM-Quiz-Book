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

(
    NAME, EXAM, STATE, PHONE, GENDER, DOB_YEAR, DOB_MONTH, DOB_DAY, 
    PIN_SETUP, SEC_QUESTION, SEC_ANSWER, RECOVERY_MENU, 
    REC_SEC_ANS, REC_PHONE, REC_DOB_YEAR, REC_DOB_MONTH, REC_DOB_DAY, REC_NAME_DOB, RESET_PIN, EDIT_WARN
) = range(20)

PRESET_SEC_QUESTIONS = [
    "𝒲𝒽𝒶𝓉 𝒾𝓈 𝓎𝑜𝓊𝓇 𝓅𝑒𝓉'𝓈 𝓃𝒶𝓂𝑒?",
    "𝒲𝒽𝒶𝓉 𝓌𝒶𝓈 𝓉𝒽𝑒 𝓃𝒶𝓂𝑒 𝑜𝒻 𝓎𝑜𝓊𝓇 𝒻𝒾𝓇𝓈𝓉 𝓈𝒸𝒽𝑜𝑜𝓁?",
    "𝒲𝒽𝒾𝒸𝒽 𝒾𝓈 𝓎𝑜𝓊𝓇 𝒻𝒶𝓋𝑜𝓇𝒾𝓉𝑒 𝒸𝒾𝓉𝓎?",
    "𝒲𝒽𝒶𝓉 𝒾𝓈 𝓎𝑜𝓊𝓇 𝓂𝑜𝓉𝒽𝑒𝓇'𝓈 𝓂𝒶𝒾𝒹𝑒𝓃 𝓃𝒶𝓂𝑒?"
]

INDIAN_STATES_AND_UTS = [
    "𝒜𝓃𝒹𝒽𝓇𝒶 𝒫𝓇𝒶𝒹𝑒𝓈𝒽", "𝒜𝓇𝓊𝓃𝒶𝒸𝒽𝒶𝓁 𝒫𝓇𝒶𝒹𝑒𝓈𝒽", "𝒜𝓈𝓈𝒶𝓂", "𝐵𝒾𝒽𝒶𝓇", "𝒞𝒽𝒽𝒶𝓉𝓉𝒾𝓈𝑔𝒶𝓇𝒽", "𝒢𝑜𝒶", 
    "𝒢𝓊𝒿𝒶𝓇𝒶𝓉", "𝐻𝒶𝓇𝓎𝒶𝓃𝒶", "𝐻𝒾𝓂𝒶𝒸𝒽𝒶𝓁 𝒫𝓇𝒶𝒹𝑒𝓈𝒽", "𝒥𝒽𝒶𝓇𝓀𝒽𝒶𝓃𝒹", "𝒦𝒶𝓇𝓃𝒶𝓉𝒶𝓀𝒶", "𝒦𝑒𝓇𝒶𝓁𝒶", 
    "𝑀𝒶𝒹𝒽𝓎𝒶 𝒫𝓇𝒶𝒹𝑒𝓈𝒽", "𝑀𝒶𝒽𝒶𝓇𝒶𝓈𝒽𝓉𝓇𝒶", "𝑀𝒶𝓃𝒾𝓅𝓊𝓇", "𝑀𝑒𝑔𝒽𝒶𝓁𝒶𝓎𝒶", "𝑀𝒾𝓏𝑜𝓇𝒶𝓂", "𝒩𝒶𝑔𝒶𝓁𝒶𝓃𝒹", 
    "𝒪𝒹𝒾𝓈𝒽𝒶", "𝒫𝓊𝓃𝒿𝒶𝒷", "𝑅𝒶𝒿𝒶𝓈𝓉𝒽𝒶𝓃", "𝒮𝒾𝓀𝓀𝒾𝓂", "𝒯𝒶𝓂𝒾𝓁 𝒩𝒶𝒹𝓊", "𝒯𝑒𝓁𝒶𝓃𝑔𝒶𝓃𝒶", "𝒯𝓇𝒾𝓅𝓊𝓇𝒶", 
    "𝒰𝓉𝓉𝒶𝓇 𝒫𝓇𝒶𝒹𝑒𝓈𝒽", "𝒰𝓉𝓉𝒶𝓇𝒶𝓀𝒽𝒶𝓃𝒹", "𝒲𝑒𝓈𝓉 𝐵𝑒𝓃𝑔𝒶𝓁", "𝒜𝓃𝒹𝒶𝓂𝒶𝓃 & 𝒩𝒾𝒸𝑜𝒷𝒶𝓇 𝐼𝓈𝓁𝒶𝓃𝒹𝓈", 
    "𝒞𝒽𝒶𝓃𝒹𝒾𝑔𝒶𝓇𝒽", "𝒟𝒶𝒹𝓇𝒶 & 𝒩𝒶𝑔𝒶𝓇 𝐻𝒶𝓋𝑒𝓁𝒾 𝒶𝓃𝒹 𝒟𝒶𝓂𝒶𝓃 & 𝒟𝒾𝓊", "𝒟𝑒𝓁𝒽𝒾", "𝒥𝒶𝓂𝓂𝓊 & 𝒦𝒶𝓈𝒽𝓂𝒾𝓇", 
    "𝐿𝒶𝒹𝒶𝓀𝒽", "𝐿𝒶𝓀𝓈𝒽𝒶𝒹𝓌𝑒𝑒𝓅", "𝒫𝓊𝒹𝓊𝒸𝒽𝑒𝓇𝓇𝓎"
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
    months = ["𝒥𝒶𝓃", "𝐹𝑒𝒷", "𝑀𝒶𝓇", "𝒜𝓅𝓇", "𝑀𝒶𝓎", "𝒥𝓊𝓃", "𝒥𝓊𝓁", "𝒜𝓊𝑔", "𝒮𝑒𝓅", "𝒪𝒸𝓉", "𝒩𝑜𝓋", "𝒟𝑒𝒸"]
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
        msg = "🛠 **𝒜𝒹𝓂𝒾𝓃 𝒽𝒶𝓈 𝓅𝒶𝓊𝓈𝑒𝒹 𝓉𝒽𝑒 𝓈𝑒𝓇𝓋𝒾𝒸𝑒 𝒸𝓊𝓇𝓇𝑒𝓃𝓉𝓁𝓎**\n𝒫𝓁𝑒𝒶𝓈𝑒 𝓉𝓇𝓎 𝒶𝑔𝒶𝒾𝓃 𝓈𝒽𝑜𝓇𝓉𝓁𝓎 𝓌𝒽𝑒𝓃 𝓈𝑒𝓇𝓋𝒾𝒸𝑒𝓈 𝒶𝓇𝑒 𝓇𝑒𝓈𝓊𝓂𝑒𝒹!"
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
        student_id = profile.get("student_id", "𝒩/𝒜")
        await update.effective_message.reply_text(
            f"⚡ **𝒲𝑒𝓁𝒸𝑜𝓂𝑒 𝒷𝒶𝒸𝓀, {profile['full_name']}!**\n"
            f"🪪 **𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝐼𝒟:** `{student_id}`\n\n"
            f"🎯 **𝒯𝒶𝓇𝑔𝑒𝓉 𝐸𝓍𝒶𝓂:** `{profile['target_exam']}`\n"
            f"📍 **𝐿𝑜𝒸𝒶𝓉𝒾𝑜𝓃:** `{profile.get('state', '𝒩/𝒜')}, 𝐼𝓃𝒹𝒾𝒶`\n\n"
            f"𝒞𝓁𝒾𝒸𝓀 𝑜𝓅𝓉𝒾𝑜𝓃𝓈 𝒷𝑒𝓁𝑜𝓌 𝑜𝓇 𝓊𝓈𝑒 𝓉𝒽𝑒 𝓂𝒶𝒾𝓃 𝓂𝑒𝓃𝓊 𝓉𝑜 𝓈𝓉𝒶𝓇𝓉 𝓅𝓇𝒶𝒸𝓉𝒾𝒸𝒾𝓃𝑔!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 𝐿𝒶𝓊𝓃𝒸𝒽 𝒬𝓊𝒾𝓏", callback_data="cmd_quiz"), InlineKeyboardButton("👤 𝒫𝓇𝑜𝒻𝒾𝓁𝑒", callback_data="cmd_profile")],
                [InlineKeyboardButton("🥇 𝐿𝑒𝒶𝒹𝑒𝓇𝒷𝑜𝒶𝓇𝒹", callback_data="cmd_toppers"), InlineKeyboardButton("📊 𝑀𝓎 𝒮𝓉𝒶𝓉𝓈", callback_data="cmd_wholestate")]
            ]),
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    await update.effective_message.reply_text(
        f"{WELCOME_CARD_TEXT}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 **𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝑅𝑒𝑔𝒾𝓈𝓉𝓇𝒶𝓉𝒾𝑜𝓃 (𝒮𝓉𝑒𝓅 1/7)**\n\n"
        f"𝒫𝓁𝑒𝒶𝓈𝑒 𝑒𝓃𝓉𝑒𝓇 𝓎𝑜𝓊𝓇 **𝐹𝓊𝓁𝓁 𝒩𝒶𝓂𝑒** (𝒶𝓉 𝓁𝑒𝒶𝓈𝓉 4 𝓁𝑒𝓉𝓉𝑒𝓇𝓈) 𝓉𝑜 𝒾𝓈𝓈𝓊𝑒 𝓎𝑜𝓊𝓇 𝓊𝓃𝒾𝓆𝓊𝑒 𝒪𝒻𝒻𝒾𝒸𝒾𝒶𝓁 𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝐼𝒟:",
        parse_mode="Markdown"
    )
    return NAME

async def name_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    input_name = update.message.text.strip()
    clean_letters = "".join(filter(str.isalpha, input_name))

    if len(clean_letters) < 4:
        await update.message.reply_text(
            "⚠️ **𝒩𝒶𝓂𝑒 𝒯𝑜𝑜 𝒮𝒽𝑜𝓇𝓉!**\n\n"
            "𝒴𝑜𝓊𝓇 𝓃𝒶𝓂𝑒 𝓂𝓊𝓈𝓉 𝒸𝑜𝓃𝓉𝒶𝒾𝓃 𝒶𝓉 𝓁𝑒𝒶𝓈𝓉 4 𝒶𝓁𝓅𝒽𝒶𝒷𝑒𝓉𝒾𝒸 𝒸𝒽𝒶𝓇𝒶𝒸𝓉𝑒𝓇𝓈 𝓉𝑜 𝒾𝓈𝓈𝓊𝑒 𝓎𝑜𝓊𝓇 𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝐼𝒟.\n"
            "𝒫𝓁𝑒𝒶𝓈𝑒 𝑒𝓃𝓉𝑒𝓇 𝓎𝑜𝓊𝓇 𝒸𝑜𝓂𝓅𝓁𝑒𝓉𝑒 **𝐹𝓊𝓁𝓁 𝒩𝒶𝓂𝑒** 𝒶𝑔𝒶𝒾𝓃:",
            parse_mode="Markdown"
        )
        return NAME

    context.user_data["full_name"] = input_name

    exams = [
        [InlineKeyboardButton("1. 𝒮𝒮𝒞 𝒞𝒢𝐿", callback_data="exam_SSC CGL"), InlineKeyboardButton("2. 𝒮𝒮𝒞 𝒞𝐻𝒮𝐿", callback_data="exam_SSC CHSL")],
        [InlineKeyboardButton("3. 𝒞𝒜𝒫𝐹 𝐻𝒞𝑀", callback_data="exam_CAPF HCM"), InlineKeyboardButton("4. 𝒜𝒮𝐼 𝒮𝒯𝐸𝒩𝒪", callback_data="exam_ASI STENO")],
        [InlineKeyboardButton("5. 𝒟𝒫 𝐻𝒞𝑀", callback_data="exam_DP HCM"), InlineKeyboardButton("6. 𝐵𝒮𝐹 𝐻𝒞𝑀", callback_data="exam_BSF HCM")],
        [InlineKeyboardButton("7. 𝒞𝐼𝒮𝐹 𝐻𝒞𝑀", callback_data="exam_CISF HCM"), InlineKeyboardButton("8. 𝑅𝒜𝐼𝐿𝒲𝒜𝒴 𝒩𝒯𝒫𝒞 𝒰𝒢", callback_data="exam_RAILWAY NTPC UG")],
        [InlineKeyboardButton("9. 𝑅𝒜𝐼𝐿𝒲𝒜𝒴 𝒩𝒯𝒫𝒞 𝒢𝑅𝒜𝒟𝒰𝒜𝒯𝐸", callback_data="exam_RAILWAY NTPC GRADUATE")],
        [InlineKeyboardButton("10. 𝒪𝓉𝒽𝑒𝓇 𝐸𝓍𝒶𝓂", callback_data="exam_OTHER")]
    ]

    await update.message.reply_text(
        f"✨ 𝒩𝒾𝒸𝑒 𝓉𝑜 𝓂𝑒𝑒𝓉 𝓎𝑜𝓊, *{context.user_data['full_name']}*!\n\n"
        f"🎯 **𝒯𝒶𝓇𝑔𝑒𝓉 𝐸𝓍𝒶𝓂 𝒮𝑒𝓁𝑒𝒸𝓉𝒾𝑜𝓃 (𝒮𝓉𝑒𝓅 2/7):**\n"
        f"𝒫𝓁𝑒𝒶𝓈𝑒 𝓉𝒶𝓅 𝓎𝑜𝓊𝓇 𝓉𝒶𝓇𝑔𝑒𝓉𝑒𝒹 𝑒𝓍𝒶𝓂𝒾𝓃𝒶𝓉𝒾𝑜𝓃 𝒻𝓇𝑜𝓂 𝓉𝒽𝑒 𝑜𝓅𝓉𝒾𝑜𝓃𝓈 𝒷𝑒𝓁𝑜𝓌:",
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
        await query.edit_message_text("✍️ 𝒫𝓁𝑒𝒶𝓈𝑒 𝓉𝓎𝓅𝑒 𝓉𝒽𝑒 𝑒𝓍𝒶𝒸𝓉 𝓃𝒶𝓂𝑒 𝑜𝒻 𝓎𝑜𝓊𝓇 𝒯𝒶𝓇𝑔𝑒𝓉 𝐸𝓍𝒶𝓂:")
        return EXAM

    context.user_data["target_exam"] = selected_exam
    context.user_data["country"] = "India"
    
    await query.edit_message_text(
        f"🎯 𝒮𝑒𝓁𝑒𝒸𝓉𝑒𝒹 𝒯𝒶𝓇𝑔𝑒𝓉: `{selected_exam}`\n\n"
        f"📍 **𝐼𝓃𝒹𝒾𝒶𝓃 𝒮𝓉𝒶𝓉𝑒 / 𝒰𝒯 𝒮𝑒𝓁𝑒𝒸𝓉𝒾𝑜𝓃 (𝒮𝓉𝑒𝓅 3/7):**\n"
        f"𝒫𝓁𝑒𝒶𝓈𝑒 𝓈𝑒𝓁𝑒𝒸𝓉 𝓎𝑜𝓊𝓇 𝒮𝓉𝒶𝓉𝑒 𝑜𝓇 𝒰𝓃𝒾𝑜𝓃 𝒯𝑒𝓇𝓇𝒾𝓉𝑜𝓇𝓎 𝒻𝓇𝑜𝓂 𝓉𝒽𝑒 𝒾𝓃𝓉𝑒𝓇𝒶𝒸𝓉𝒾𝓋𝑒 𝓁𝒾𝓈𝓉 𝒷𝑒𝓁𝑜𝓌:",
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
            f"🎯 𝒮𝑒𝓁𝑒𝒸𝓉𝑒𝒹 𝒯𝒶𝓇𝑔𝑒𝓉: `{context.user_data['target_exam']}`\n\n"
            f"📍 **𝐼𝓃𝒹𝒾𝒶𝓃 𝒮𝓉𝒶𝓉𝑒 / 𝒰𝒯 𝒮𝑒𝓁𝑒𝒸𝓉𝒾𝑜𝓃 (𝒮𝓉𝑒𝓅 3/7):**\n"
            f"𝒫𝓁𝑒𝒶𝓈𝑒 𝓈𝑒𝓁𝑒𝒸𝓉 𝓎𝑜𝓊𝓇 𝒮𝓉𝒶𝓉𝑒 𝑜𝓇 𝒰𝓃𝒾𝑜𝓃 𝒯𝑒𝓇𝓇𝒾𝓉𝑜𝓇𝓎 𝒻𝓇𝑜𝓂 𝓉𝒽𝑒 𝒾𝓃𝓉𝑒𝓇𝒶𝒸𝓉𝒾𝓋𝑒 𝓁𝒾𝓈𝓉 𝒷𝑒𝓁𝑜𝓌:",
            reply_markup=build_state_keyboard(),
            parse_mode="Markdown"
        )
        return STATE
    else:
        exams = [
            [InlineKeyboardButton("1. 𝒮𝒮𝒞 𝒞𝒢𝐿", callback_data="exam_SSC CGL"), InlineKeyboardButton("2. 𝒮𝒮𝒞 𝒞𝐻𝒮𝐿", callback_data="exam_SSC CHSL")],
            [InlineKeyboardButton("3. 𝒞𝒜𝒫𝐹 𝐻𝒞𝑀", callback_data="exam_CAPF HCM"), InlineKeyboardButton("4. 𝒜𝒮𝐼 𝒮𝒯𝐸𝒩𝒪", callback_data="exam_ASI STENO")],
            [InlineKeyboardButton("5. 𝒟𝒫 𝐻𝒞𝑀", callback_data="exam_DP HCM"), InlineKeyboardButton("6. 𝐵𝒮𝐹 𝐻𝒞𝑀", callback_data="exam_BSF HCM")],
            [InlineKeyboardButton("7. 𝒞𝐼𝒮𝐹 𝐻𝒞𝑀", callback_data="exam_CISF HCM"), InlineKeyboardButton("8. 𝑅𝒜𝐼𝐿𝒲𝒜𝒴 𝒩𝒯𝒫𝒞 𝒰𝒢", callback_data="exam_RAILWAY NTPC UG")],
            [InlineKeyboardButton("9. 𝑅𝒜𝐼𝐿𝒲𝒜𝒴 𝒩𝒯𝒫𝒞 𝒢𝑅𝒜𝒟𝒰𝒜𝒯𝐸", callback_data="exam_RAILWAY NTPC GRADUATE")],
            [InlineKeyboardButton("10. 𝒪𝓉𝒽𝑒𝓇 𝐸𝓍𝒶𝓂", callback_data="exam_OTHER")]
        ]
        await update.message.reply_text(
            "👇 𝒫𝓁𝑒𝒶𝓈𝑒 𝓉𝒶𝓅 𝑜𝓃𝑒 𝑜𝒻 𝓉𝒽𝑒 𝑒𝓍𝒶𝓂 𝒷𝓊𝓉𝓉𝑜𝓃𝓈 𝒷𝑒𝓁𝑜𝓌, 𝑜𝓇 𝒸𝒽𝑜𝑜𝓈𝑒 '10. 𝒪𝓉𝒽𝑒𝓇 𝐸𝓍𝒶𝓂' 𝓉𝑜 𝓉𝓎𝓅𝑒 𝒸𝓊𝓈𝓉𝑜𝓂:",
            reply_markup=InlineKeyboardMarkup(exams)
        )
        return EXAM

async def state_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    selected_state = query.data.replace("st_", "")
    context.user_data["country"] = "India"
    context.user_data["state"] = selected_state

    contact_btn = KeyboardButton(text="📱 𝒮𝒽𝒶𝓇𝑒 𝒱𝑒𝓇𝒾𝒻𝒾𝑒𝒹 𝑀𝑜𝒷𝒾𝓁𝑒 𝒩𝓊𝓂𝒷𝑒𝓇", request_contact=True)
    markup = ReplyKeyboardMarkup([[contact_btn]], one_time_keyboard=True, resize_keyboard=True)

    await query.delete_message()
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=f"📍 𝒮𝑒𝓁𝑒𝒸𝓉𝑒𝒹 𝐿𝑜𝒸𝒶𝓉𝒾𝑜𝓃: `{selected_state}, 𝐼𝓃𝒹𝒾𝒶`\n\n"
             f"📱 **𝑀𝑜𝒷𝒾𝓁𝑒 𝒱𝑒𝓇𝒾𝒻𝒾𝒸𝒶𝓉𝒾𝑜𝓃 (𝒮𝓉𝑒𝓅 4/7):**\n"
             f"𝒯𝒶𝓅 𝓉𝒽𝑒 **𝒮𝒽𝒶𝓇𝑒 𝒱𝑒𝓇𝒾𝒻𝒾𝑒𝒹 𝑀𝑜𝒷𝒾𝓁𝑒 𝒩𝓊𝓂𝒷𝑒𝓇** 𝒷𝓊𝓉𝓉𝑜𝓃 𝒷𝑒𝓁𝑜𝓌 𝓉𝑜 𝒸𝑜𝓂𝓅𝓁𝑒𝓉𝑒 𝓋𝑒𝓇𝒾𝒻𝒾𝒸𝒶𝓉𝒾𝑜𝓃:",
        reply_markup=markup,
        parse_mode="Markdown"
    )
    return PHONE

async def phone_contact_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.contact:
        contact_btn = KeyboardButton(text="📱 𝒮𝒽𝒶𝓇𝑒 𝒱𝑒𝓇𝒾𝒻𝒾𝑒𝒹 𝑀𝑜𝒷𝒾𝓁𝑒 𝒩𝓊𝓂𝒷𝑒𝓇", request_contact=True)
        markup = ReplyKeyboardMarkup([[contact_btn]], one_time_keyboard=True, resize_keyboard=True)
        await update.message.reply_text(
            "⚠️ **𝒱𝑒𝓇𝒾𝒻𝒾𝒸𝒶𝓉𝒾𝑜𝓃 𝑅𝑒𝓆𝓊𝒾𝓇𝑒𝒹!**\n\n"
            "𝒯𝑜 𝓅𝓇𝑒𝓋𝑒𝓃𝓉 𝒻𝒶𝓀𝑒 𝓅𝓇𝑜𝒻𝒾𝓁𝑒𝓈, 𝓎𝑜𝓊 𝓂𝓊𝓈𝓉 𝒸𝓁𝒾𝒸𝓀 𝓉𝒽𝑒 𝒷𝓊𝓉𝓉𝑜𝓃 𝒷𝑒𝓁𝑜𝓌 𝓉𝑜 𝓈𝒽𝒶𝓇𝑒 𝓎𝑜𝓊𝓇 𝓋𝑒𝓇𝒾𝒻𝒾𝑒𝒹 𝒯𝑒𝓁𝑒𝑔𝓇𝒶𝓂 𝓂𝑜𝒷𝒾𝓁𝑒 𝓃𝓊𝓂𝒷𝑒𝓇:",
            reply_markup=markup,
            parse_mode="Markdown"
        )
        return PHONE

    phone_num = update.message.contact.phone_number
    context.user_data["phone_number"] = phone_num

    gender_buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("𝑀𝒶𝓁𝑒 👨", callback_data="gen_Male"), InlineKeyboardButton("𝐹𝑒𝓂𝒶𝓁𝑒 👩", callback_data="gen_Female")]
    ])

    await update.message.reply_text(
        f"✅ 𝒱𝑒𝓇𝒾𝒻𝒾𝑒𝒹 𝑀𝑜𝒷𝒾𝓁𝑒: `{phone_num}`\n\n"
        f"👤 **𝒮𝑒𝓁𝑒𝒸𝓉 𝒢𝑒𝓃𝒹𝑒𝓇 (𝒮𝓉𝑒𝓅 5/7):**",
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
        f"👤 𝒢𝑒𝓃𝒹𝑒𝓇: `{selected_gender}`\n\n"
        f"🎂 **𝒮𝑒𝓁𝑒𝒸𝓉 𝐵𝒾𝓇𝓉𝒽 𝒴𝑒𝒶𝓇 (𝒮𝓉𝑒𝓅 6/7):**\n"
        f"𝒫𝓁𝑒𝒶𝓈𝑒 𝓉𝒶𝓅 𝓎𝑜𝓊𝓇 𝐵𝒾𝓇𝓉𝒽 𝒴𝑒𝒶𝓇 𝒻𝓇𝑜𝓂 𝒷𝑒𝓁𝑜𝓌 𝓉𝑜 𝒾𝓈𝓈𝓊𝑒 𝓎𝑜𝓊𝓇 𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝐼𝒟:",
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
        f"📅 **𝒮𝑒𝓁𝑒𝒸𝓉𝑒𝒹 𝐵𝒾𝓇𝓉𝒽 𝒴𝑒𝒶𝓇:** `{selected_year}`\n\n"
        f"🗓 **𝒮𝑒𝓁𝑒𝒸𝓉 𝐵𝒾𝓇𝓉𝒽 𝑀𝑜𝓃𝓉𝒽:**\n"
        f"𝒫𝓁𝑒𝒶𝓈𝑒 𝓉𝒶𝓅 𝓎𝑜𝓊𝓇 𝑀𝑜𝓃𝓉𝒽 𝑜𝒻 𝐵𝒾𝓇𝓉𝒽 𝒷𝑒𝓁𝑜𝓌:",
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
        f"📅 **𝒮𝑒𝓁𝑒𝒸𝓉𝑒𝒹 𝐵𝒾𝓇𝓉𝒽 𝒫𝑒𝓇𝒾𝑜𝒹:** `{selected_month}/{selected_year}`\n\n"
        f"🗓 **𝒮𝑒𝓁𝑒𝒸𝓉 𝐸𝓍𝒶𝒸𝓉 𝐵𝒾𝓇𝓉𝒽 𝒟𝒶𝓉𝑒 (𝒟𝒶𝓎):**\n"
        f"𝒫𝓁𝑒𝒶𝓈𝑒 𝓉𝒶𝓅 𝓎𝑜𝓊𝓇 𝑒𝓍𝒶𝒸𝓉 𝒟𝒶𝓎 𝑜𝒻 𝐵𝒾𝓇𝓉𝒽 𝒻𝓇𝑜𝓂 𝒷𝑒𝓁𝑜𝓌:",
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
        f"🎂 **𝒟𝒪𝐵 𝒮𝑒𝓁𝑒𝒸𝓉𝑒𝒹:** `{dob_str}`\n\n"
        f"🔑 **𝒜𝒸𝒸𝑜𝓊𝓃𝓉 𝒮𝑒𝒸𝓊𝓇𝒾𝓉𝓎 (𝒮𝓉𝑒𝓅 7/7):**\n"
        f"𝒫𝓁𝑒𝒶𝓈𝑒 𝓈𝑒𝓉 𝒶 𝓈𝑒𝒸𝓇𝑒𝓉 **4-𝒟𝒾𝑔𝒾𝓉 𝒫𝐼𝒩** 𝒻𝑜𝓇 𝓎𝑜𝓊𝓇 𝒶𝒸𝒸𝑜𝓊𝓃𝓉 (𝑒.𝑔. `4321`):",
        parse_mode="Markdown"
    )
    return PIN_SETUP

async def pin_setup_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pin_input = update.message.text.strip()
    if not pin_input.isdigit() or len(pin_input) != 4:
        await update.message.reply_text("⚠️ 𝒫𝐼𝒩 𝓂𝓊𝓈𝓉 𝒷𝑒 𝑒𝓍𝒶𝒸𝓉𝓁𝓎 **4 𝓃𝓊𝓂𝑒𝓇𝒾𝒸 𝒹𝒾𝑔𝒾𝓉𝓈** (𝑒.𝑔. 4321). 𝒫𝓁𝑒𝒶𝓈𝑒 𝓉𝓇𝓎 𝒶𝑔𝒶𝒾𝓃:")
        return PIN_SETUP

    context.user_data["pin"] = pin_input

    sec_buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton(q, callback_data=f"secq_{idx}")] for idx, q in enumerate(PRESET_SEC_QUESTIONS)
    ])

    await update.message.reply_text(
        f"🛡 **𝒮𝑒𝓁𝑒𝒸𝓉 𝒮𝑒𝒸𝓊𝓇𝒾𝓉𝓎 𝑅𝑒𝒸𝑜𝓋𝑒𝓇𝓎 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃:**\n"
        f"𝒞𝒽𝑜𝑜𝓈𝑒 𝒶 𝓆𝓊𝑒𝓈𝓉𝒾𝑜𝓃 𝒻𝓇𝑜𝓂 𝒷𝑒𝓁𝑜𝓌 𝓉𝑜 𝓊𝓈𝑒 𝒻𝑜𝓇 𝒫𝐼𝒩 𝓇𝑒𝒸𝑜𝓋𝑒𝓇𝓎:",
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
        f"🛡 **𝒮𝑒𝒸𝓊𝓇𝒾𝓉𝓎 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃:** *{selected_q}*\n\n"
        f"𝒫𝓁𝑒𝒶𝓈𝑒 𝓇𝑒𝓅𝓁𝓎 𝓌𝒾𝓉𝒽 𝓎𝑜𝓊𝓇 𝓈𝑒𝒸𝓇𝑒𝓉 𝒜𝓃𝓈𝓌𝑒𝓇 𝒷𝑒𝓁𝑜𝓌:",
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
        username=user.username or "𝒩/𝒜",
        phone=context.user_data.get("phone_number", "𝒩/𝒜"),
        target_exam=context.user_data.get("target_exam", "𝒢𝑒𝓃𝑒𝓇𝒶𝓁"),
        dob=dob_str,
        age=calc_age,
        gender=context.user_data.get("gender", "𝒩𝑜𝓉 𝒮𝓅𝑒𝒸𝒾𝒻𝒾𝑒𝒹"),
        pin=context.user_data.get("pin", "1234"),
        sec_q=context.user_data.get("security_question", "𝒟𝑒𝒻𝒶𝓊𝓁𝓉"),
        sec_a=ans_input,
        country="India",
        state=context.user_data.get("state", "𝒩/𝒜"),
        referred_by=context.user_data.get("referred_by")
    )

    await update.message.reply_text(
        f"🎉 **𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝑅𝑒𝑔𝒾𝓈𝓉𝓇𝒶𝓉𝒾𝑜𝓃 𝒞𝑜𝓂𝓅𝓁𝑒𝓉𝑒!**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🪪 **𝒪𝐹𝐹𝐼𝒞𝐼𝒜𝐿 𝒮𝒯𝒰𝒟𝐸𝒩𝒯 𝐼𝒟 𝐼𝒮𝒮𝒰𝐸𝒟:** `{student_id}`\n"
        f"🔑 **𝒮𝑒𝒸𝓇𝑒𝓉 𝒫𝐼𝒩:** `{context.user_data.get('pin')}`\n"
        f"🎂 **𝒟𝒪𝐵 𝑅𝑒𝑔𝒾𝓈𝓉𝑒𝓇𝑒𝒹:** `{dob_str}`\n\n"
        f"✅ 𝒴𝑜𝓊𝓇 𝓈𝓉𝓊𝒹𝑒𝓃𝓉 𝓅𝓇𝑜𝒻𝒾𝓁𝑒 𝒽𝒶𝓈 𝒷𝑒𝑒𝓃 𝓋𝑒𝓇𝒾𝒻𝒾𝑒𝒹 𝒶𝓃𝒹 𝓈𝒶𝓋𝑒𝒹 𝓈𝓊𝒸𝒸𝑒𝓈𝓈𝒻𝓊𝓁𝓁𝓎! 𝒴𝑜𝓊 𝒸𝒶𝓃 𝓋𝒾𝑒𝓌 𝑜𝓇 𝓊𝓅𝒹𝒶𝓉𝑒 𝓎𝑜𝓊𝓇 𝒹𝑒𝓉𝒶𝒾𝓁𝓈 𝒶𝓃𝓎𝓉𝒾𝓂𝑒 𝒾𝓃 𝓎𝑜𝓊𝓇 **𝒫𝓇𝑜𝒻𝒾𝓁𝑒 𝒞𝒶𝓇𝒹** (/myprofile).\n\n"
        f"👉 𝒯𝒶𝓅 **𝐿𝒶𝓊𝓃𝒸𝒽 𝒬𝓊𝒾𝓏** 𝒷𝑒𝓁𝑜𝓌 𝑜𝓇 𝓊𝓈𝑒 𝓉𝒽𝑒 𝓂𝒶𝒾𝓃 𝓂𝑒𝓃𝓊 𝓉𝑜 𝒷𝑒𝑔𝒾𝓃 𝓁𝑒𝒶𝓇𝓃𝒾𝓃𝑔!",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    await context.bot.send_message(
        chat_id=update.message.chat_id,
        text="👇 **𝒬𝓊𝒾𝒸𝓀 𝒩𝒶𝓋𝒾𝑔𝒶𝓉𝒾𝑜𝓃:**",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 𝐿𝒶𝓊𝓃𝒸𝒽 𝒬𝓊𝒾𝓏", callback_data="cmd_quiz"), InlineKeyboardButton("👤 𝒫𝓇𝑜𝒻𝒾𝓁𝑒", callback_data="cmd_profile")]
        ])
    )
    return ConversationHandler.END

async def edit_profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_onboarding_maintenance(update):
        return ConversationHandler.END

    user = update.effective_user
    can_edit, days_left = can_user_edit_profile(user.id)
    
    if not can_edit:
        msg = f"⏳ **𝒫𝓇𝑜𝒻𝒾𝓁𝑒 𝐸𝒹𝒾𝓉 𝐿𝑜𝒸𝓀𝑒𝒹!**\n\n𝒴𝑜𝓊 𝒸𝒶𝓃 𝑜𝓃𝓁𝓎 𝓊𝓅𝒹𝒶𝓉𝑒 𝓎𝑜𝓊𝓇 𝓅𝓇𝑜𝒻𝒾𝓁𝑒 𝒹𝑒𝓉𝒶𝒾𝓁𝓈 𝑜𝓃𝒸𝑒 𝑒𝓋𝑒𝓇𝓎 30 𝒹𝒶𝓎𝓈.\n𝒫𝓁𝑒𝒶𝓈𝑒 𝓉𝓇𝓎 𝒶𝑔𝒶𝒾𝓃 𝒾𝓃 `{days_left} 𝒹𝒶𝓎𝓈`."
        if update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        else:
            await update.message.reply_text(msg, parse_mode="Markdown")
        return ConversationHandler.END

    warn_msg = (
        "⚠️ **𝒫𝑅𝒪𝐹𝐼𝐿𝐸 𝐸𝒟𝐼𝒯 𝒲𝒜𝑅𝒩𝐼𝒩𝒢**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "𝒫𝓁𝑒𝒶𝓈𝑒 𝓃𝑜𝓉𝑒: 𝒴𝑜𝓊 𝒶𝓇𝑒 𝒶𝓁𝓁𝑜𝓌𝑒𝒹 𝓉𝑜 𝑒𝒹𝒾𝓉 𝓎𝑜𝓊𝓇 𝓈𝓉𝓊𝒹𝑒𝓃𝓉 𝓅𝓇𝑜𝒻𝒾𝓁𝑒 𝒹𝑒𝓉𝒶𝒾𝓁𝓈 **𝒪𝒩𝐿𝒴 𝒪𝒩𝒞𝐸 𝐸𝒱𝐸𝑅𝒴 30 𝒟𝒜𝒴𝒮**.\n\n"
        "𝒜𝓇𝑒 𝓎𝑜𝓊 𝓈𝓊𝓇𝑒 𝓎𝑜𝓊 𝓌𝒶𝓃𝓉 𝓉𝑜 𝓅𝓇𝑜𝒸𝑒𝑒𝒹 𝓌𝒾𝓉𝒽 𝓊𝓅𝒹𝒶𝓉𝒾𝓃𝑔 𝓎𝑜𝓊𝓇 𝓅𝓇𝑜𝒻𝒾𝓁𝑒 𝓃𝑜𝓌?"
    )
    warn_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 𝒴𝑒𝓈, 𝒫𝓇𝑜𝒸𝑒𝑒𝒹 𝓉𝑜 𝐸𝒹𝒾𝓉", callback_data="edit_confirm_yes")],
        [InlineKeyboardButton("❌ 𝒞𝒶𝓃𝒸𝑒𝓁 𝐸𝒹𝒾𝓉", callback_data="edit_confirm_no")]
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
        await query.edit_message_text("❌ 𝒫𝓇𝑜𝒻𝒾𝓁𝑒 𝓊𝓅𝒹𝒶𝓉𝑒 𝒸𝒶𝓃𝒸𝑒𝓁𝓁𝑒𝒹.")
        return ConversationHandler.END

    context.user_data["is_editing_profile"] = True
    await query.edit_message_text(
        "✏️ **𝐸𝒹𝒾𝓉 𝒫𝓇𝑜𝒻𝒾𝓁𝑒 𝒮𝑒𝓈𝓈𝒾𝑜𝓃 𝒮𝓉𝒶𝓇𝓉𝑒𝒹 (𝒮𝓉𝑒𝓅 1/7)**\n\n"
        "𝒫𝓁𝑒𝒶𝓈𝑒 𝑒𝓃𝓉𝑒𝓇 𝓎𝑜𝓊𝓇 𝓊𝓅𝒹𝒶𝓉𝑒𝒹 **𝐹𝓊𝓁𝓁 𝒩𝒶𝓂𝑒** (𝒶𝓉 𝓁𝑒𝒶𝓈𝓉 4 𝓁𝑒𝓉𝓉𝑒𝓇𝓈):",
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
        await query.edit_message_text("⚠️ 𝒮𝑒𝓈𝓈𝒾𝑜𝓃 𝑒𝓍𝓅𝒾𝓇𝑒𝒹. 𝒫𝓁𝑒𝒶𝓈𝑒 𝓉𝓎𝓅𝑒 /start 𝓉𝑜 𝓁𝑜𝑔 𝒾𝓃 𝒶𝑔𝒶𝒾𝓃.")
        return ConversationHandler.END

    context.user_data["login_target_user"] = u

    rec_options = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛡 𝒮𝑒𝒸𝓊𝓇𝒾𝓉𝓎 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃", callback_data="rec_opt_secq")],
        [InlineKeyboardButton("📱 𝒱𝑒𝓇𝒾𝒻𝒾𝑒𝒹 𝒫𝒽𝑜𝓃𝑒 𝒩𝓊𝓂𝒷𝑒𝓇", callback_data="rec_opt_phone")],
        [InlineKeyboardButton("🎂 𝒟𝒪𝐵 + 𝒩𝒶𝓂𝑒 𝒱𝑒𝓇𝒾𝒻𝒾𝒸𝒶𝓉𝒾𝑜𝓃", callback_data="rec_opt_namedob")],
        [InlineKeyboardButton("🗓 𝒟𝒪𝐵 𝒢𝓇𝒾𝒹 𝒱𝑒𝓇𝒾𝒻𝒾𝒸𝒶𝓉𝒾𝑜𝓃", callback_data="rec_opt_dob")]
    ])

    await query.edit_message_text(
        f"🛡 **𝒫𝐼𝒩 & 𝒜𝒞𝒞𝒪𝒰𝒩𝒯 𝑅𝐸𝒮𝐸𝒯 𝒫𝒪𝑅𝒯𝒜𝐿**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"𝒜𝒸𝒸𝑜𝓊𝓃𝓉: `{u['full_name']}` (`{u['student_id']}`)\n\n"
        f"𝒮𝑒𝓁𝑒𝒸𝓉 𝒶𝓃 𝒶𝓊𝓉𝒽𝑒𝓃𝓉𝒾𝒸𝒶𝓉𝒾𝑜𝓃 𝓂𝑒𝓉𝒽𝑜𝒹 𝒷𝑒𝓁𝑜𝓌 𝓉𝑜 𝓇𝑒𝓈𝑒𝓉 𝓎𝑜𝓊𝓇 𝓈𝑒𝒸𝓇𝑒𝓉 4-𝒹𝒾𝑔𝒾𝓉 𝒫𝐼𝒩:",
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
        sec_q = u.get("security_question", "𝒟𝑒𝒻𝒶𝓊𝓁𝓉 𝒮𝑒𝒸𝓊𝓇𝒾𝓉𝓎 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃")
        await query.edit_message_text(
            f"🛡 **𝒮𝐸𝒞𝒰𝑅𝐼𝒯𝒴 𝒬𝒰𝐸𝒮𝒯𝐼𝒪𝒩 𝑅𝐸𝒞𝒪𝒱𝐸𝑅𝒴**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"❓ **𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃:** *{sec_q}*\n\n"
            f"𝒫𝓁𝑒𝒶𝓈𝑒 𝓇𝑒𝓅𝓁𝓎 𝓌𝒾𝓉𝒽 𝓎𝑜𝓊𝓇 𝒜𝓃𝓈𝓌𝑒𝓇 𝒷𝑒𝓁𝑜𝓌:",
            parse_mode="Markdown"
        )
        return REC_SEC_ANS

    elif data == "rec_opt_phone":
        contact_btn = KeyboardButton(text="📱 𝒮𝒽𝒶𝓇𝑒 𝒱𝑒𝓇𝒾𝒻𝒾𝑒𝒹 𝑀𝑜𝒷𝒾𝓁𝑒 𝒩𝓊𝓂𝒷𝑒𝓇", request_contact=True)
        markup = ReplyKeyboardMarkup([[contact_btn]], one_time_keyboard=True, resize_keyboard=True)
        await query.delete_message()
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"📱 **𝒫𝐻𝒪𝒩𝐸 𝒩𝒰𝑀𝐵𝐸𝑅 𝑅𝐸𝒞𝒪𝒱𝐸𝑅𝒴**\n\n𝒯𝒶𝓅 𝓉𝒽𝑒 𝒷𝓊𝓉𝓉𝑜𝓃 𝒷𝑒𝓁𝑜𝓌 𝓉𝑜 𝓈𝒽𝒶𝓇𝑒 𝓎𝑜𝓊𝓇 𝓋𝑒𝓇𝒾𝒻𝒾𝑒𝒹 𝓂𝑜𝒷𝒾𝓁𝑒 𝓃𝓊𝓂𝒷𝑒𝓇 𝒻𝑜𝓇 𝓂𝒶𝓉𝒸𝒽:",
            reply_markup=markup
        )
        return REC_PHONE

    elif data == "rec_opt_namedob":
        await query.edit_message_text(
            f"👤 **𝒩𝒜𝑀𝐸 & 𝒟𝒪𝐵 𝑅𝐸𝒞𝒪𝒱𝐸𝑅𝒴**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"𝒫𝓁𝑒𝒶𝓈𝑒 𝓇𝑒𝓅𝓁𝓎 𝓌𝒾𝓉𝒽 𝓎𝑜𝓊𝓇 **𝑅𝑒𝑔𝒾𝓈𝓉𝑒𝓇𝑒𝒹 𝐹𝓊𝓁𝓁 𝒩𝒶𝓂𝑒** (𝑜𝓇 𝒟𝒪𝐵 𝒻𝑜𝓇𝓂𝒶𝓉𝓉𝑒𝒹 𝒶𝓈 𝒟𝒟-𝑀𝑀-𝒴𝒴𝒴𝒴):",
            parse_mode="Markdown"
        )
        return REC_NAME_DOB

    elif data == "rec_opt_dob":
        await query.edit_message_text(
            f"🎂 **𝒟𝒜𝒯𝐸 𝒪𝐹 𝐵𝐼𝑅𝒯𝐻 𝑅𝐸𝒞𝒪𝒱𝐸𝑅𝒴**\n\n𝒮𝑒𝓁𝑒𝒸𝓉 𝓎𝑜𝓊𝓇 𝓇𝑒𝑔𝒾𝓈𝓉𝑒𝓇𝑒𝒹 **𝐵𝒾𝓇𝓉𝒽 𝒴𝑒𝒶𝓇**:",
            reply_markup=build_year_keyboard(prefix="recdoby_"),
            parse_mode="Markdown"
        )
        return REC_DOB_YEAR

async def rec_sec_ans_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ans_input = update.message.text.strip().lower()
    u = context.user_data.get("login_target_user")
    correct_ans = str(u.get("security_answer", "")).strip().lower()

    if ans_input != correct_ans:
        rec_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 𝑅𝑒𝓈𝑒𝓉 𝒴𝑜𝓊𝓇 𝒫𝐼𝒩 / 𝒫𝒶𝓈𝓈𝓌𝑜𝓇𝒹", callback_data="login_forgot_pin")]])
        await update.message.reply_text("❌ **𝐼𝓃𝒸𝑜𝓇𝓇𝑒𝒸𝓉 𝒮𝑒𝒸𝓊𝓇𝒾𝓉𝓎 𝒜𝓃𝓈𝓌𝑒𝓇!**\n\n𝒫𝓁𝑒𝒶𝓈𝑒 𝓉𝓇𝓎 𝒶𝑔𝒶𝒾𝓃 𝑜𝓇 𝓉𝒶𝓅 𝒷𝑒𝓁𝑜𝓌 𝓉𝑜 𝓇𝑒𝓈𝑒𝓉 𝓊𝓈𝒾𝓃𝑔 𝒶𝓃𝑜𝓉𝒽𝑒𝓇 𝓂𝑒𝓉𝒽𝑜𝒹:", reply_markup=rec_btn)
        return REC_SEC_ANS

    await update.message.reply_text(
        f"✅ **𝐼𝒟𝐸𝒩𝒯𝐼𝒯𝒴 𝒱𝐸𝑅𝐼𝐹𝐼𝐸𝒟!**\n\n"
        f"👤 **𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝒩𝒶𝓂𝑒:** {u['full_name']}\n"
        f"🪪 **𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝐼𝒟:** `{u['student_id']}`\n\n"
        f"𝒫𝓁𝑒𝒶𝓈𝑒 𝑒𝓃𝓉𝑒𝓇 𝓎𝑜𝓊𝓇 **𝒩𝑒𝓌 𝒮𝑒𝒸𝓇𝑒𝓉 4-𝒟𝒾𝑔𝒾𝓉 𝒫𝐼𝒩** 𝒷𝑒𝓁𝑜𝓌:",
        parse_mode="Markdown"
    )
    return RESET_PIN

async def rec_phone_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.contact:
        contact_btn = KeyboardButton(text="📱 𝒮𝒽𝒶𝓇𝑒 𝒱𝑒𝓇𝒾𝒻𝒾𝑒𝒹 𝑀𝑜𝒷𝒾𝓁𝑒 𝒩𝓊𝓂𝒷𝑒𝓇", request_contact=True)
        markup = ReplyKeyboardMarkup([[contact_btn]], one_time_keyboard=True, resize_keyboard=True)
        await update.message.reply_text("⚠️ 𝒴𝑜𝓊 𝑀𝒰𝒮𝒯 𝓉𝒶𝓅 𝓉𝒽𝑒 𝒷𝓊𝓉𝓉𝑜𝓃 𝒷𝑒𝓁𝑜𝓌 𝓉𝑜 𝓈𝒽𝒶𝓇𝑒 𝓎𝑜𝓊𝓇 𝒸𝑜𝓃𝓉𝒶𝒸𝓉 𝓃𝓊𝓂𝒷𝑒𝓇:", reply_markup=markup)
        return REC_PHONE

    shared_phone = update.message.contact.phone_number.replace("+", "").strip()
    u = context.user_data.get("login_target_user")
    user_phone = str(u.get("phone_number", "")).replace("+", "").strip()

    if shared_phone != user_phone:
        rec_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 𝑅𝑒𝓈𝑒𝓉 𝒴𝑜𝓊𝓇 𝒫𝐼𝒩 / 𝒫𝒶𝓈𝓈𝓌𝑜𝓇𝒹", callback_data="login_forgot_pin")]])
        await update.message.reply_text(
            f"❌ **𝒫𝒽𝑜𝓃𝑒 𝒩𝓊𝓂𝒷𝑒𝓇 𝑀𝒾𝓈𝓂𝒶𝓉𝒸𝒽!** 𝒮𝒽𝒶𝓇𝑒𝒹 𝓃𝓊𝓂𝒷𝑒𝓇 𝒹𝑜𝑒𝓈 𝓃𝑜𝓉 𝓂𝒶𝓉𝒸𝒽 𝓇𝑒𝑔𝒾𝓈𝓉𝑒𝓇𝑒𝒹 𝓃𝓊𝓂𝒷𝑒𝓇 𝒻𝑜𝓇 `{u['student_id']}`.",
            reply_markup=rec_btn
        )
        return REC_PHONE

    await update.message.reply_text(
        f"✅ **𝒫𝐻𝒪𝒩𝐸 𝒱𝐸𝑅𝐼𝐹𝐼𝐸𝒟 𝒮𝒰𝒞𝒞𝐸𝒮𝒮𝐹𝒰𝐿𝐿𝒴!**\n\n"
        f"👤 **𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝒩𝒶𝓂𝑒:** {u['full_name']}\n"
        f"🪪 **𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝐼𝒟:** `{u['student_id']}`\n\n"
        f"𝒫𝓁𝑒𝒶𝓈𝑒 𝑒𝓃𝓉𝑒𝓇 𝓎𝑜𝓊𝓇 **𝒩𝑒𝓌 𝒮𝑒𝒸𝓇𝑒𝓉 4-𝒟𝒾𝑔𝒾𝓉 𝒫𝐼𝒩** 𝒷𝑒𝓁𝑜𝓌:",
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
            f"✅ **𝒩𝒜𝑀𝐸 / 𝒟𝒪𝐵 𝒱𝐸𝑅𝐼𝐹𝐼𝐸𝒟!**\n\n"
            f"👤 **𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝒩𝒶𝓂𝑒:** {u['full_name']}\n"
            f"🪪 **𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝐼𝒟:** `{u['student_id']}`\n\n"
            f"𝒫𝓁𝑒𝒶𝓈𝑒 𝑒𝓃𝓉𝑒𝓇 𝓎𝑜𝓊𝓇 **𝒩𝑒𝓌 𝒮𝑒𝒸𝓇𝑒𝓉 4-𝒟𝒾𝑔𝒾𝓉 𝒫𝐼𝒩** 𝒷𝑒𝓁𝑜𝓌:",
            parse_mode="Markdown"
        )
        return RESET_PIN

    rec_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 𝑅𝑒𝓈𝑒𝓉 𝒴𝑜𝓊𝓇 𝒫𝐼𝒩 / 𝒫𝒶𝓈𝓈𝓌𝑜𝓇𝒹", callback_data="login_forgot_pin")]])
    await update.message.reply_text(
        "❌ **𝒱𝑒𝓇𝒾𝒻𝒾𝒸𝒶𝓉𝒾𝑜𝓃 𝐹𝒶𝒾𝓁𝑒𝒹!** 𝐼𝓃𝓅𝓊𝓉 𝒹𝑜𝑒𝓈 𝓃𝑜𝓉 𝓂𝒶𝓉𝒸𝒽 𝓇𝑒𝑔𝒾𝓈𝓉𝑒𝓇𝑒𝒹 𝓇𝑒𝒸𝑜𝓇𝒹𝓈. 𝒯𝓇𝓎 𝒶𝑔𝒶𝒾𝓃 𝑜𝓇 𝓅𝒾𝒸𝓀 𝒶𝓃𝑜𝓉𝒽𝑒𝓇 𝓂𝑒𝓉𝒽𝑜𝒹:",
        reply_markup=rec_btn
    )
    return REC_NAME_DOB

async def rec_dob_year_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    selected_year = query.data.replace("recdoby_", "")
    context.user_data["rec_birth_year"] = selected_year

    await query.edit_message_text(
        f"📅 **𝒴𝑒𝒶𝓇 𝒮𝑒𝓁𝑒𝒸𝓉𝑒𝒹:** `{selected_year}`\n\n𝒮𝑒𝓁𝑒𝒸𝓉 𝓎𝑜𝓊𝓇 𝓇𝑒𝑔𝒾𝓈𝓉𝑒𝓇𝑒𝒹 **𝐵𝒾𝓇𝓉𝒽 𝑀𝑜𝓃𝓉𝒽**:",
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
        f"📅 **𝒫𝑒𝓇𝒾𝑜𝒹 𝒮𝑒𝓁𝑒𝒸𝓉𝑒𝒹:** `{selected_month}/{y}`\n\n𝒮𝑒𝓁𝑒𝒸𝓉 𝓎𝑜𝓊𝓇 𝓇𝑒𝑔𝒾𝓈𝓉𝑒𝓇𝑒𝒹 **𝐵𝒾𝓇𝓉𝒽 𝒟𝒶𝓎**:",
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
        rec_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 𝑅𝑒𝓈𝑒𝓉 𝒪𝓅𝓉𝒾𝑜𝓃𝓈", callback_data="login_forgot_pin")]])
        await query.edit_message_text(f"❌ **𝒟𝒪𝐵 𝑀𝒾𝓈𝓂𝒶𝓉𝒸𝒽!** 𝑅𝑒𝑔𝒾𝓈𝓉𝑒𝓇𝑒𝒹 𝒟𝒪𝐵 𝒹𝑜𝑒𝓈 𝓃𝑜𝓉 𝓂𝒶𝓉𝒸𝒽 `{dob_constructed}`.", reply_markup=rec_btn)
        return RECOVERY_MENU

    await query.delete_message()
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=f"✅ **𝒟𝒪𝐵 𝒱𝐸𝑅𝐼𝐹𝐼𝐸𝒟!**\n\n👤 **𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝒩𝒶𝓂𝑒:** {u['full_name']}\n🪪 **𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝐼𝒟:** `{u['student_id']}`\n\n𝒫𝓁𝑒𝒶𝓈𝑒 𝑒𝓃𝓉𝑒𝓇 𝓎𝑜𝓊𝓇 **𝒩𝑒𝓌 𝒮𝑒𝒸𝓇𝑒𝓉 4-𝒟𝒾𝑔𝒾𝓉 𝒫𝐼𝒩** 𝒷𝑒𝓁𝑜𝓌:",
        parse_mode="Markdown"
    )
    return RESET_PIN

async def reset_pin_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_pin = update.message.text.strip()
    if not new_pin.isdigit() or len(new_pin) != 4:
        await update.message.reply_text("⚠️ 𝒫𝐼𝒩 𝓂𝓊𝓈𝓉 𝒷𝑒 𝑒𝓍𝒶𝒸𝓉𝓁𝓎 **4 𝓃𝓊𝓂𝑒𝓇𝒾𝒸 𝒹𝒾𝑔𝒾𝓉𝓈** (𝑒.𝑔. 1234). 𝒫𝓁𝑒𝒶𝓈𝑒 𝓉𝓇𝓎 𝒶𝑔𝒶𝒾𝓃:")
        return RESET_PIN

    u = context.user_data.get("login_target_user")
    if not u:
        user = update.effective_user
        u = get_user_profile(user.id)

    target_uid = u['user_id']
    update_user_pin(target_uid, new_pin)

    await update.message.reply_text(
        f"🎉 **𝒫𝐼𝒩 𝑅𝐸𝒮𝐸𝒯 𝒮𝒰𝒞𝒞𝐸𝒮𝒮𝐹𝒰𝐿𝐿𝒴!**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 **𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝒩𝒶𝓂𝑒:** {u['full_name']}\n"
        f"🪪 **𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝐼𝒟:** `{u['student_id']}`\n"
        f"🔑 **𝒴𝑜𝓊𝓇 𝒩𝑒𝓌 𝒮𝑒𝒸𝓇𝑒𝓉 𝒫𝐼𝒩:** `{new_pin}`\n\n"
        f"𝒴𝑜𝓊𝓇 𝑜𝓇𝒾𝑔𝒾𝓃𝒶𝓁 𝒶𝒸𝒸𝑜𝓊𝓃𝓉 𝓇𝑒𝓂𝒶𝒾𝓃𝓈 100% 𝒶𝒸𝓉𝒾𝓋𝑒 𝓌𝒾𝓉𝒽 𝒶𝓁𝓁 𝓈𝒸𝑜𝓇𝑒𝓈, 𝓈𝒶𝓋𝑒𝒹 𝓆𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈, 𝒶𝓃𝒹 𝓁𝒾𝓂𝒾𝓉𝓈 𝒻𝓊𝓁𝓁𝓎 𝒾𝓃𝓉𝒶𝒸𝓉.\n\n"
        f"👉 𝒯𝒶𝓅 **𝐿𝒶𝓊𝓃𝒸𝒽 𝒬𝓊𝒾𝓏** 𝒷𝑒𝓁𝑜𝓌 𝑜𝓇 𝓊𝓈𝑒 /quiz 𝓉𝑜 𝒸𝑜𝓃𝓉𝒾𝓃𝓊𝑒 𝓅𝓇𝒶𝒸𝓉𝒾𝒸𝒾𝓃𝑔!",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 𝐿𝒶𝓊𝓃𝒸𝒽 𝒬𝓊𝒾𝓏", callback_data="cmd_quiz"), InlineKeyboardButton("👤 𝒫𝓇𝑜𝒻𝒾𝓁𝑒", callback_data="cmd_profile")]
        ]),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def cancel_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["is_editing_profile"] = False
    await update.message.reply_text("𝒮𝑒𝓉𝓊𝓅 𝒸𝒶𝓃𝒸𝑒𝓁𝓁𝑒𝒹. 𝒯𝓎𝓅𝑒 /start 𝒶𝓃𝓎𝓉𝒾𝓂𝑒 𝓉𝑜 𝒷𝑒𝑔𝒾𝓃 𝓇𝑒𝑔𝒾𝓈𝓉𝓇𝒶𝓉𝒾𝑜𝓃.", reply_markup=ReplyKeyboardRemove())
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