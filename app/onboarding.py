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
    get_maintenance_until, generate_student_id, get_user_by_student_id, update_user_pin
)
import time

warnings.filterwarnings("ignore", category=PTBUserWarning)

(
    START_CHOICE, NAME, EXAM, COUNTRY, STATE, PHONE, GENDER, DOB_YEAR, DOB_MONTH, DOB_DAY, 
    PIN_SETUP, SEC_QUESTION, SEC_ANSWER, LOGIN_SID, LOGIN_PIN, RECOVERY_MENU, 
    REC_SEC_ANS, REC_PHONE, REC_DOB_YEAR, REC_DOB_MONTH, REC_DOB_DAY, REC_NAME_DOB, RESET_PIN, EDIT_WARN
) = range(24)

PRESET_SEC_QUESTIONS = [
    "What is your pet's name?",
    "What was the name of your first school?",
    "Which is your favorite city?",
    "What is your mother's maiden name?"
]

INDIAN_STATES_AND_UTS = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", 
    "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", 
    "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", 
    "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", 
    "Uttar Pradesh", "Uttarakhand", "West Bengal", "Andaman & Nicobar Islands", 
    "Chandigarh", "Dadra & Nagar Haveli and Daman & Diu", "Delhi", "Jammu & Kashmir", 
    "Ladakh", "Lakshadweep", "Puducherry"
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
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
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
        msg = "🛠 **ADMIN HAS PAUSED THE SERVICE CURRENTLY**\nPlease try again shortly when services are resumed!"
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
    args = context.args
    
    if args and args[0].startswith("ref_"):
        try:
            ref_id = int(args[0].replace("ref_", ""))
            context.user_data['referred_by'] = ref_id
        except ValueError:
            pass

    profile = get_user_profile(user.id)
    if profile and profile.get("is_verified") and not context.user_data.get("is_editing_profile"):
        student_id = profile.get("student_id", "N/A")
        await update.effective_message.reply_text(
            f"⚡ **Welcome back, {profile['full_name']}!**\n"
            f"🪪 **Student ID:** `{student_id}`\n\n"
            f"🎯 **Target Exam:** `{profile['target_exam']}`\n"
            f"📍 **Location:** `{profile.get('state', 'N/A')}, {profile.get('country', 'India')}`\n\n"
            f"Click options below or use the main menu to start practicing!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 Launch Quiz", callback_data="cmd_quiz"), InlineKeyboardButton("👤 Profile", callback_data="cmd_profile")],
                [InlineKeyboardButton("🥇 Leaderboard", callback_data="cmd_toppers"), InlineKeyboardButton("📊 My Stats", callback_data="cmd_wholestate")]
            ]),
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    welcome_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕 Create New Student Account", callback_data="start_create")],
        [InlineKeyboardButton("🔑 Existing Student Login", callback_data="start_login")]
    ])

    await update.effective_message.reply_text(
        f"{WELCOME_CARD_TEXT}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👋 **WELCOME TO LEARN WITH HIM QUIZ BOOK!**\n\n"
        f"Please select an option below to proceed:",
        reply_markup=welcome_markup,
        parse_mode="Markdown"
    )
    return START_CHOICE

async def start_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "start_create":
        await query.edit_message_text(
            f"📝 **Student Registration (Step 1/8)**\n\n"
            f"Please enter your **Full Name** (at least 4 letters) to issue your unique Official Student ID:",
            parse_mode="Markdown"
        )
        return NAME
    elif query.data == "start_login":
        await query.edit_message_text(
            f"🔑 **EXISTING STUDENT LOGIN**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Please enter your **Official Student ID** (e.g., `Hi090800`):",
            parse_mode="Markdown"
        )
        return LOGIN_SID

async def login_sid_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid = update.message.text.strip()
    u = get_user_by_student_id(sid)

    if not u:
        rec_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🆕 Create New Account", callback_data="start_create")]
        ])
        await update.message.reply_text(
            f"⚠️ **Student ID Not Found!**\n\n"
            f"No account exists with Student ID `{sid}`. Please check for typos or tap below to create a new profile:",
            reply_markup=rec_markup,
            parse_mode="Markdown"
        )
        return LOGIN_SID

    context.user_data["login_target_user"] = u
    rec_btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Reset Your PIN / Password", callback_data="login_forgot_pin")]
    ])

    await update.message.reply_text(
        f"🔑 **Student Account Found:** `{u['full_name']}` (`{u['student_id']}`)\n\n"
        f"Please enter your secret **4-Digit PIN**:",
        reply_markup=rec_btn,
        parse_mode="Markdown"
    )
    return LOGIN_PIN

async def login_pin_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pin_input = update.message.text.strip()
    u = context.user_data.get("login_target_user")

    if not u or u.get("pin") != pin_input:
        rec_btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔑 Reset Your PIN / Password", callback_data="login_forgot_pin")]
        ])
        await update.message.reply_text(
            f"❌ **Incorrect PIN!**\n\n"
            f"The PIN entered does not match your account. Please try entering your PIN again, or tap below to reset your PIN:",
            reply_markup=rec_btn,
            parse_mode="Markdown"
        )
        return LOGIN_PIN

    user = update.effective_user
    await update.message.reply_text(
        f"🎉 **LOGIN SUCCESSFUL!**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ Welcome back, *{u['full_name']}*!\n"
        f"🪪 **Student ID:** `{u['student_id']}`\n\n"
        f"Your scores, saved questions, and quotas have been loaded successfully!",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Launch Quiz", callback_data="cmd_quiz"), InlineKeyboardButton("👤 Profile", callback_data="cmd_profile")]
        ]),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def recovery_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    u = context.user_data.get("login_target_user")
    if not u:
        user = update.effective_user
        u = get_user_profile(user.id)

    if not u:
        await query.edit_message_text("⚠️ Session expired. Please type /start to log in again.")
        return ConversationHandler.END

    context.user_data["login_target_user"] = u

    rec_options = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛡 Security Question", callback_data="rec_opt_secq")],
        [InlineKeyboardButton("📱 Verify via Phone Number", callback_data="rec_opt_phone")],
        [InlineKeyboardButton("🎂 DOB + Name Verification", callback_data="rec_opt_namedob")],
        [InlineKeyboardButton("🗓 DOB Grid Verification", callback_data="rec_opt_dob")]
    ])

    await query.edit_message_text(
        f"🛡 **PIN & ACCOUNT RESET PORTAL**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Account: `{u['full_name']}` (`{u['student_id']}`)\n\n"
        f"Select an authentication method below to reset your secret 4-digit PIN:",
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
        sec_q = u.get("security_question", "Default Security Question")
        await query.edit_message_text(
            f"🛡 **SECURITY QUESTION RECOVERY**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"❓ **Question:** *{sec_q}*\n\n"
            f"Please reply with your Answer below:",
            parse_mode="Markdown"
        )
        return REC_SEC_ANS

    elif data == "rec_opt_phone":
        contact_btn = KeyboardButton(text="📱 Share Verified Mobile Number", request_contact=True)
        markup = ReplyKeyboardMarkup([[contact_btn]], one_time_keyboard=True, resize_keyboard=True)
        await query.delete_message()
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"📱 **PHONE NUMBER RECOVERY**\n\nTap the button below to share your verified mobile number for match:",
            reply_markup=markup
        )
        return REC_PHONE

    elif data == "rec_opt_namedob":
        await query.edit_message_text(
            f"👤 **NAME & DOB RECOVERY**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Please reply with your **Registered Full Name** (or DOB formatted as DD-MM-YYYY):",
            parse_mode="Markdown"
        )
        return REC_NAME_DOB

    elif data == "rec_opt_dob":
        await query.edit_message_text(
            f"🎂 **DATE OF BIRTH RECOVERY**\n\nSelect your registered **Birth Year**:",
            reply_markup=build_year_keyboard(prefix="recdoby_"),
            parse_mode="Markdown"
        )
        return REC_DOB_YEAR

async def rec_sec_ans_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ans_input = update.message.text.strip().lower()
    u = context.user_data.get("login_target_user")
    correct_ans = str(u.get("security_answer", "")).strip().lower()

    if ans_input != correct_ans:
        rec_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 Reset Your PIN / Password", callback_data="login_forgot_pin")]])
        await update.message.reply_text("❌ **Incorrect Security Answer!**\n\nPlease try again or tap below to reset using another method:", reply_markup=rec_btn)
        return REC_SEC_ANS

    await update.message.reply_text(
        f"✅ **IDENTITY VERIFIED!**\n\n"
        f"👤 **Student Name:** {u['full_name']}\n"
        f"🪪 **Student ID:** `{u['student_id']}`\n\n"
        f"Please enter your **New Secret 4-Digit PIN** below:",
        parse_mode="Markdown"
    )
    return RESET_PIN

async def rec_phone_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.contact:
        contact_btn = KeyboardButton(text="📱 Share Verified Mobile Number", request_contact=True)
        markup = ReplyKeyboardMarkup([[contact_btn]], one_time_keyboard=True, resize_keyboard=True)
        await update.message.reply_text("⚠️ You MUST tap the button below to share your contact number:", reply_markup=markup)
        return REC_PHONE

    shared_phone = update.message.contact.phone_number.replace("+", "").strip()
    u = context.user_data.get("login_target_user")
    user_phone = str(u.get("phone_number", "")).replace("+", "").strip()

    if shared_phone != user_phone:
        rec_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 Reset Your PIN / Password", callback_data="login_forgot_pin")]])
        await update.message.reply_text(
            f"❌ **Phone Number Mismatch!** Shared number does not match registered number for `{u['student_id']}`.",
            reply_markup=rec_btn
        )
        return REC_PHONE

    await update.message.reply_text(
        f"✅ **PHONE VERIFIED SUCCESSFULLY!**\n\n"
        f"👤 **Student Name:** {u['full_name']}\n"
        f"🪪 **Student ID:** `{u['student_id']}`\n\n"
        f"Please enter your **New Secret 4-Digit PIN** below:",
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
            f"✅ **NAME / DOB VERIFIED!**\n\n"
            f"👤 **Student Name:** {u['full_name']}\n"
            f"🪪 **Student ID:** `{u['student_id']}`\n\n"
            f"Please enter your **New Secret 4-Digit PIN** below:",
            parse_mode="Markdown"
        )
        return RESET_PIN

    rec_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 Reset Your PIN / Password", callback_data="login_forgot_pin")]])
    await update.message.reply_text(
        "❌ **Verification Failed!** Input does not match registered records. Try again or pick another method:",
        reply_markup=rec_btn
    )
    return REC_NAME_DOB

async def rec_dob_year_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    selected_year = query.data.replace("recdoby_", "")
    context.user_data["rec_birth_year"] = selected_year

    await query.edit_message_text(
        f"📅 **Year Selected:** `{selected_year}`\n\nSelect your registered **Birth Month**:",
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
        f"📅 **Period Selected:** `{selected_month}/{y}`\n\nSelect your registered **Birth Day**:",
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
        rec_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 Reset Options", callback_data="login_forgot_pin")]])
        await query.edit_message_text(f"❌ **DOB Mismatch!** Registered DOB does not match `{dob_constructed}`.", reply_markup=rec_btn)
        return RECOVERY_MENU

    await query.delete_message()
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=f"✅ **DOB VERIFIED!**\n\n👤 **Student Name:** {u['full_name']}\n🪪 **Student ID:** `{u['student_id']}`\n\nPlease enter your **New Secret 4-Digit PIN** below:",
        parse_mode="Markdown"
    )
    return RESET_PIN

async def reset_pin_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_pin = update.message.text.strip()
    if not new_pin.isdigit() or len(new_pin) != 4:
        await update.message.reply_text("⚠️ PIN must be exactly **4 numeric digits** (e.g. 1234). Please try again:")
        return RESET_PIN

    u = context.user_data.get("login_target_user")
    if not u:
        user = update.effective_user
        u = get_user_profile(user.id)

    target_uid = u['user_id']
    update_user_pin(target_uid, new_pin)

    await update.message.reply_text(
        f"🎉 **PIN RESET SUCCESSFUL!**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 **Student Name:** {u['full_name']}\n"
        f"🪪 **Student ID:** `{u['student_id']}`\n"
        f"🔑 **Your New Secret PIN:** `{new_pin}`\n\n"
        f"Your original account remains 100% active with all scores, saved questions, and limits fully intact.\n\n"
        f"👉 Tap **Launch Quiz** below or use /quiz to continue practicing!",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Launch Quiz", callback_data="cmd_quiz"), InlineKeyboardButton("👤 Profile", callback_data="cmd_profile")]
        ]),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def edit_profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_onboarding_maintenance(update):
        return ConversationHandler.END

    user = update.effective_user
    can_edit, days_left = can_user_edit_profile(user.id)
    
    if not can_edit:
        msg = f"⏳ **Profile Edit Locked!**\n\nYou can only update your profile details once every 30 days.\nPlease try again in `{days_left} days`."
        if update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        else:
            await update.message.reply_text(msg, parse_mode="Markdown")
        return ConversationHandler.END

    warn_msg = (
        "⚠️ **PROFILE EDIT WARNING**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Please note: You are allowed to edit your student profile details **ONLY ONCE EVERY 30 DAYS**.\n\n"
        "Are you sure you want to proceed with updating your profile now?"
    )
    warn_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Yes, Proceed to Edit", callback_data="edit_confirm_yes")],
        [InlineKeyboardButton("❌ Cancel Edit", callback_data="edit_confirm_no")]
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
        await query.edit_message_text("❌ Profile update cancelled.")
        return ConversationHandler.END

    context.user_data["is_editing_profile"] = True
    await query.edit_message_text(
        "✏️ **Edit Profile Session Started (Step 1/8)**\n\n"
        "Please enter your updated **Full Name** (at least 4 letters):",
        parse_mode="Markdown"
    )
    return NAME

async def name_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    input_name = update.message.text.strip()
    clean_letters = "".join(filter(str.isalpha, input_name))

    if len(clean_letters) < 4:
        await update.message.reply_text(
            "⚠️ **Name Too Short!**\n\n"
            "Your name must contain at least 4 alphabetic characters to issue your Student ID.\n"
            "Please enter your complete **Full Name** again:",
            parse_mode="Markdown"
        )
        return NAME

    context.user_data["full_name"] = input_name

    exams = [
        [InlineKeyboardButton("1. SSC CGL", callback_data="exam_SSC CGL"), InlineKeyboardButton("2. SSC CHSL", callback_data="exam_SSC CHSL")],
        [InlineKeyboardButton("3. CAPF HCM", callback_data="exam_CAPF HCM"), InlineKeyboardButton("4. ASI STENO", callback_data="exam_ASI STENO")],
        [InlineKeyboardButton("5. DP HCM", callback_data="exam_DP HCM"), InlineKeyboardButton("6. BSF HCM", callback_data="exam_BSF HCM")],
        [InlineKeyboardButton("7. CISF HCM", callback_data="exam_CISF HCM"), InlineKeyboardButton("8. RAILWAY NTPC UG", callback_data="exam_RAILWAY NTPC UG")],
        [InlineKeyboardButton("9. RAILWAY NTPC GRADUATE", callback_data="exam_RAILWAY NTPC GRADUATE")],
        [InlineKeyboardButton("10. Other Exam", callback_data="exam_OTHER")]
    ]

    await update.message.reply_text(
        f"✨ Nice to meet you, *{context.user_data['full_name']}*!\n\n"
        f"🎯 **Target Exam Selection (Step 2/8):**\n"
        f"Please tap your targeted examination from the options below:",
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
        await query.edit_message_text("✍️ Please type the exact name of your Target Exam:")
        return EXAM

    context.user_data["target_exam"] = selected_exam
    
    country_buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🇮🇳 India", callback_data="country_India"), InlineKeyboardButton("🌎 Other Country", callback_data="country_OTHER")]
    ])
    await query.edit_message_text(
        f"🎯 Selected Target: `{selected_exam}`\n\n"
        f"🌍 **Country Selection (Step 3/8):**\n"
        f"Please choose your country from below:",
        reply_markup=country_buttons,
        parse_mode="Markdown"
    )
    return COUNTRY

async def custom_exam_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_other_exam"):
        context.user_data["target_exam"] = update.message.text.strip()
        context.user_data["awaiting_other_exam"] = False
        
        country_buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("🇮🇳 India", callback_data="country_India"), InlineKeyboardButton("🌎 Other Country", callback_data="country_OTHER")]
        ])
        await update.message.reply_text(
            f"🎯 Selected Target: `{context.user_data['target_exam']}`\n\n"
            f"🌍 **Country Selection (Step 3/8):**\n"
            f"Please choose your country from below:",
            reply_markup=country_buttons,
            parse_mode="Markdown"
        )
        return COUNTRY

async def country_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    choice = query.data.replace("country_", "")
    if choice == "OTHER":
        context.user_data["awaiting_other_country"] = True
        await query.edit_message_text("✍️ Please type the name of your Country:")
        return COUNTRY

    context.user_data["country"] = "India"
    await query.edit_message_text(
        "📍 **Indian State / UT Selection (Step 4/8):**\n\n"
        "Please select your State or Union Territory from the interactive list below:",
        reply_markup=build_state_keyboard(),
        parse_mode="Markdown"
    )
    return STATE

async def custom_country_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_other_country"):
        context.user_data["country"] = update.message.text.strip()
        context.user_data["state"] = "Foreign"
        context.user_data["awaiting_other_country"] = False
        
        contact_btn = KeyboardButton(text="📱 Share Verified Mobile Number", request_contact=True)
        markup = ReplyKeyboardMarkup([[contact_btn]], one_time_keyboard=True, resize_keyboard=True)
        
        await update.message.reply_text(
            f"🌍 Country: `{context.user_data['country']}`\n\n"
            f"📱 **Mobile Verification (Step 5/8):**\n"
            f"Tap the **Share Verified Mobile Number** button below to complete verification:",
            reply_markup=markup,
            parse_mode="Markdown"
        )
        return PHONE

async def state_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    selected_state = query.data.replace("st_", "")
    context.user_data["country"] = "India"
    context.user_data["state"] = selected_state

    contact_btn = KeyboardButton(text="📱 Share Verified Mobile Number", request_contact=True)
    markup = ReplyKeyboardMarkup([[contact_btn]], one_time_keyboard=True, resize_keyboard=True)

    await query.delete_message()
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=f"📍 Selected Location: `{selected_state}, India`\n\n"
             f"📱 **Mobile Verification (Step 5/8):**\n"
             f"Tap the **Share Verified Mobile Number** button below to complete verification:",
        reply_markup=markup,
        parse_mode="Markdown"
    )
    return PHONE

async def phone_contact_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.contact:
        contact_btn = KeyboardButton(text="📱 Share Verified Mobile Number", request_contact=True)
        markup = ReplyKeyboardMarkup([[contact_btn]], one_time_keyboard=True, resize_keyboard=True)
        await update.message.reply_text(
            "⚠️ **Verification Required!**\n\n"
            "To prevent fake profiles, you MUST click the button below to share your verified Telegram mobile number:",
            reply_markup=markup,
            parse_mode="Markdown"
        )
        return PHONE

    phone_num = update.message.contact.phone_number
    context.user_data["phone_number"] = phone_num

    gender_buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("Male 👨", callback_data="gen_Male"), InlineKeyboardButton("Female 👩", callback_data="gen_Female")]
    ])

    await update.message.reply_text(
        f"✅ Verified Mobile: `{phone_num}`\n\n"
        f"👤 **Select Gender (Step 6/8):**",
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
        f"👤 Gender: `{selected_gender}`\n\n"
        f"🎂 **Select Birth Year (Step 7/8):**\n"
        f"Please tap your Birth Year from below to issue your Student ID:",
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
        f"📅 **Selected Birth Year:** `{selected_year}`\n\n"
        f"🗓 **Select Birth Month:**\n"
        f"Please tap your Month of Birth below:",
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
        f"📅 **Selected Birth Period:** `{selected_month}/{selected_year}`\n\n"
        f"🗓 **Select Exact Birth Date (Day):**\n"
        f"Please tap your exact Day of Birth from below:",
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
        f"🎂 **DOB Selected:** `{dob_str}`\n\n"
        f"🔑 **Account Security (Step 8/8):**\n"
        f"Please set a secret **4-Digit PIN** for your account (e.g. `4321`):",
        parse_mode="Markdown"
    )
    return PIN_SETUP

async def pin_setup_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pin_input = update.message.text.strip()
    if not pin_input.isdigit() or len(pin_input) != 4:
        await update.message.reply_text("⚠️ PIN must be exactly **4 numeric digits** (e.g. 4321). Please try again:")
        return PIN_SETUP

    context.user_data["pin"] = pin_input

    sec_buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton(q, callback_data=f"secq_{idx}")] for idx, q in enumerate(PRESET_SEC_QUESTIONS)
    ])

    await update.message.reply_text(
        f"🛡 **Select Security Recovery Question:**\n"
        f"Choose a question from below to use for PIN recovery:",
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
        f"🛡 **Security Question:** *{selected_q}*\n\n"
        f"Please reply with your secret Answer below:",
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
        username=user.username or "N/A",
        phone=context.user_data.get("phone_number", "N/A"),
        target_exam=context.user_data.get("target_exam", "General"),
        dob=dob_str,
        age=calc_age,
        gender=context.user_data.get("gender", "Not Specified"),
        pin=context.user_data.get("pin", "1234"),
        sec_q=context.user_data.get("security_question", "Default"),
        sec_a=ans_input,
        country=context.user_data.get("country", "India"),
        state=context.user_data.get("state", "N/A"),
        referred_by=context.user_data.get("referred_by")
    )

    await update.message.reply_text(
        f"🎉 **Student Registration Complete!**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🪪 **OFFICIAL STUDENT ID ISSUED:** `{student_id}`\n"
        f"🔑 **Secret PIN:** `{context.user_data.get('pin')}`\n"
        f"🎂 **DOB Registered:** `{dob_str}`\n\n"
        f"✅ Your student profile has been verified and saved successfully! You can view or update your details anytime in your **Profile Card** (/myprofile).\n\n"
        f"👉 Tap **Launch Quiz** below or use the main menu to begin learning!",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    await context.bot.send_message(
        chat_id=update.message.chat_id,
        text="👇 **Quick Navigation:**",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Launch Quiz", callback_data="cmd_quiz"), InlineKeyboardButton("👤 Profile", callback_data="cmd_profile")]
        ])
    )
    return ConversationHandler.END

async def cancel_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["is_editing_profile"] = False
    await update.message.reply_text("Setup cancelled. Type /start anytime to begin registration.", reply_markup=ReplyKeyboardRemove())
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
            START_CHOICE: [
                CallbackQueryHandler(start_choice_callback, pattern="^start_")
            ],
            LOGIN_SID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, login_sid_step),
                CallbackQueryHandler(start_choice_callback, pattern="^start_create$")
            ],
            LOGIN_PIN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, login_pin_step),
                CallbackQueryHandler(recovery_menu_callback, pattern="^login_forgot_pin$")
            ],
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
            COUNTRY: [
                CallbackQueryHandler(country_callback, pattern="^country_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, custom_country_text)
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