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
    "🐾 What is your pet's name?",
    "🏫 What was the name of your first school?",
    "🏙 Which is your favorite city?",
    "👩 What is your mother's maiden name?"
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
        row = [InlineKeyboardButton(f"📍 {INDIAN_STATES_AND_UTS[i]}", callback_data=f"st_{INDIAN_STATES_AND_UTS[i]}")]
        if i + 1 < len(INDIAN_STATES_AND_UTS):
            row.append(InlineKeyboardButton(f"📍 {INDIAN_STATES_AND_UTS[i+1]}", callback_data=f"st_{INDIAN_STATES_AND_UTS[i+1]}"))
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

def build_year_keyboard(prefix="doby_"):
    current_year = datetime.now().year
    years = [str(y) for y in range(current_year - 45, current_year - 10)]
    keyboard = []
    for i in range(0, len(years), 4):
        row = [InlineKeyboardButton(f"🗓 {y}", callback_data=f"{prefix}{y}") for y in years[i:i+4]]
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

def build_month_keyboard(prefix="dobm_"):
    months = ["Jan ❄️", "Feb 🍫", "Mar 🌸", "Apr 🌧", "May ☀️", "Jun 🌿", "Jul ☔", "Aug 🌴", "Sep 🍂", "Oct 🍁", "Nov 🌾", "Dec ⛄"]
    keyboard = []
    for i in range(0, len(months), 3):
        row = [InlineKeyboardButton(months[idx], callback_data=f"{prefix}{idx+1:02d}") for idx in range(i, min(i+3, len(months)))]
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
        msg = "🛠 **ADMIN HAS PAUSED THE SERVICE CURRENTLY** 🛠\n\n⏰ Please try again shortly when services are resumed!"
        if update.callback_query:
            await update.callback_query.answer("🛠 Service Paused!", show_alert=True)
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
        student_id = profile.get("student_id", "N/A")
        await update.effective_message.reply_text(
            f"⚡ **Welcome back, {profile['full_name']}!** 👋\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🪪 **Student ID:** `{student_id}`\n"
            f"🎯 **Target Exam:** `{profile['target_exam']}`\n"
            f"📍 **Location:** `{profile.get('state', 'N/A')}, India` 🇮🇳\n\n"
            f"🚀 **Select an option below to start practicing:**",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 Launch Quiz", callback_data="cmd_quiz"), InlineKeyboardButton("👤 Profile Card", callback_data="cmd_profile")],
                [InlineKeyboardButton("💳 My Plan", callback_data="cmd_myplan"), InlineKeyboardButton("🥇 Toppers Leaderboard", callback_data="cmd_toppers")],
                [InlineKeyboardButton("📊 My Analytics", callback_data="cmd_wholestate"), InlineKeyboardButton("💳 VIP Plans", callback_data="cmd_plans")]
            ]),
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    await update.effective_message.reply_text(
        f"{WELCOME_CARD_TEXT}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 **STUDENT REGISTRATION (STEP 1/7)** 📝\n\n"
        f"👤 Please reply with your **Full Name** (at least 4 alphabetic letters) to generate your Official Student ID:",
        parse_mode="Markdown"
    )
    return NAME

async def name_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    input_name = update.message.text.strip()
    clean_letters = "".join(filter(str.isalpha, input_name))

    if len(clean_letters) < 4:
        await update.message.reply_text(
            "⚠️ **NAME TOO SHORT!** ⚠️\n\n"
            "Your full name must contain at least **4 alphabetic characters**.\n"
            "✍️ Please enter your complete **Full Name** again:",
            parse_mode="Markdown"
        )
        return NAME

    context.user_data["full_name"] = input_name

    exams = [
        [InlineKeyboardButton("🎯 1. SSC CGL", callback_data="exam_SSC CGL"), InlineKeyboardButton("🎯 2. SSC CHSL", callback_data="exam_SSC CHSL")],
        [InlineKeyboardButton("🎯 3. CAPF HCM", callback_data="exam_CAPF HCM"), InlineKeyboardButton("🎯 4. ASI STENO", callback_data="exam_ASI STENO")],
        [InlineKeyboardButton("🎯 5. DP HCM", callback_data="exam_DP HCM"), InlineKeyboardButton("🎯 6. BSF HCM", callback_data="exam_BSF HCM")],
        [InlineKeyboardButton("🎯 7. CISF HCM", callback_data="exam_CISF HCM"), InlineKeyboardButton("🎯 8. RAILWAY NTPC UG", callback_data="exam_RAILWAY NTPC UG")],
        [InlineKeyboardButton("🎯 9. RAILWAY NTPC GRADUATE", callback_data="exam_RAILWAY NTPC GRADUATE")],
        [InlineKeyboardButton("✍️ 10. Other Target Exam", callback_data="exam_OTHER")]
    ]

    await update.message.reply_text(
        f"✨ Nice to meet you, *{context.user_data['full_name']}*! 👋\n\n"
        f"🎯 **TARGET EXAM SELECTION (STEP 2/7)** 🎯\n\n"
        f"Please tap your targeted examination from the interactive options below:",
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
        await query.edit_message_text("✍️ **CUSTOM EXAM INPUT**\n\nPlease reply with the exact name of your Target Examination:")
        return EXAM

    context.user_data["target_exam"] = selected_exam
    context.user_data["country"] = "India"
    
    await query.edit_message_text(
        f"🎯 **Selected Target Exam:** `{selected_exam}`\n\n"
        f"📍 **INDIAN STATE / UT SELECTION (STEP 3/7)** 📍\n\n"
        f"Please choose your home State or Union Territory from the list below:",
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
            f"🎯 **Selected Target Exam:** `{context.user_data['target_exam']}`\n\n"
            f"📍 **INDIAN STATE / UT SELECTION (STEP 3/7)** 📍\n\n"
            f"Please choose your home State or Union Territory from the list below:",
            reply_markup=build_state_keyboard(),
            parse_mode="Markdown"
        )
        return STATE

    exams = [
        [InlineKeyboardButton("🎯 1. SSC CGL", callback_data="exam_SSC CGL"), InlineKeyboardButton("🎯 2. SSC CHSL", callback_data="exam_SSC CHSL")],
        [InlineKeyboardButton("🎯 3. CAPF HCM", callback_data="exam_CAPF HCM"), InlineKeyboardButton("🎯 4. ASI STENO", callback_data="exam_ASI STENO")],
        [InlineKeyboardButton("🎯 5. DP HCM", callback_data="exam_DP HCM"), InlineKeyboardButton("🎯 6. BSF HCM", callback_data="exam_BSF HCM")],
        [InlineKeyboardButton("🎯 7. CISF HCM", callback_data="exam_CISF HCM"), InlineKeyboardButton("🎯 8. RAILWAY NTPC UG", callback_data="exam_RAILWAY NTPC UG")],
        [InlineKeyboardButton("🎯 9. RAILWAY NTPC GRADUATE", callback_data="exam_RAILWAY NTPC GRADUATE")],
        [InlineKeyboardButton("✍️ 10. Other Target Exam", callback_data="exam_OTHER")]
    ]
    await update.message.reply_text(
        "👇 Please tap one of the exam buttons below, or select **✍️ 10. Other Target Exam** to write custom:",
        reply_markup=InlineKeyboardMarkup(exams)
    )
    return EXAM

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
        text=f"📍 **Selected Location:** `{selected_state}, India` 🇮🇳\n\n"
             f"📱 **MOBILE VERIFICATION (STEP 4/7)** 📱\n\n"
             f"Tap the **📱 Share Verified Mobile Number** button at the bottom of your screen to complete instant verification:",
        reply_markup=markup,
        parse_mode="Markdown"
    )
    return PHONE

async def phone_contact_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.contact:
        contact_btn = KeyboardButton(text="📱 Share Verified Mobile Number", request_contact=True)
        markup = ReplyKeyboardMarkup([[contact_btn]], one_time_keyboard=True, resize_keyboard=True)
        await update.message.reply_text(
            "⚠️ **VERIFICATION REQUIRED!** ⚠️\n\n"
            "To keep account scores genuine, you MUST click the button at the bottom of your screen to share your verified Telegram mobile number:",
            reply_markup=markup,
            parse_mode="Markdown"
        )
        return PHONE

    phone_num = update.message.contact.phone_number
    context.user_data["phone_number"] = phone_num

    gender_buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("👨 Male Student", callback_data="gen_Male"), InlineKeyboardButton("👩 Female Student", callback_data="gen_Female")]
    ])

    await update.message.reply_text(
        f"✅ **Verified Mobile:** `{phone_num}`\n\n"
        f"👤 **SELECT GENDER (STEP 5/7)** 👤",
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
        f"👤 **Gender:** `{selected_gender}`\n\n"
        f"🎂 **SELECT BIRTH YEAR (STEP 6/7)** 🎂\n\n"
        f"Please select your Year of Birth from the options below:",
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
        f"🗓 **SELECT BIRTH MONTH** 🗓\n\n"
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
        f"📅 **Selected Period:** `{selected_month}/{selected_year}`\n\n"
        f"🗓 **SELECT DAY OF BIRTH** 🗓\n\n"
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
        f"🎂 **Registered DOB:** `{dob_str}`\n\n"
        f"🔑 **ACCOUNT SECURITY PIN SETUP (STEP 7/7)** 🔑\n\n"
        f"Please reply with a secret **4-Digit PIN** for your account (e.g., `4321`):",
        parse_mode="Markdown"
    )
    return PIN_SETUP

async def pin_setup_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pin_input = update.message.text.strip()
    if not pin_input.isdigit() or len(pin_input) != 4:
        await update.message.reply_text("⚠️ **INVALID PIN!**\n\nPIN must consist of exactly **4 numeric digits** (e.g. 4321). Please try again:")
        return PIN_SETUP

    context.user_data["pin"] = pin_input

    sec_buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton(q, callback_data=f"secq_{idx}")] for idx, q in enumerate(PRESET_SEC_QUESTIONS)
    ])

    await update.message.reply_text(
        f"🛡 **SELECT SECURITY RECOVERY QUESTION** 🛡\n\n"
        f"Choose a recovery question to assist in resetting your PIN if forgotten:",
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
        f"✍️ Please reply with your secret answer below:",
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
        country="India",
        state=context.user_data.get("state", "N/A"),
        referred_by=context.user_data.get("referred_by")
    )

    demo_msg = (
        f"🎉 **STUDENT REGISTRATION COMPLETE!** 🎉\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🪪 **OFFICIAL STUDENT ID:** `{student_id}`\n"
        f"🔑 **SECRET PIN:** `{context.user_data.get('pin')}`\n"
        f"🎂 **REGISTERED DOB:** `{dob_str}`\n\n"
        f"🎁 **FREE DEMO PLAN ACTIVATED BY DEFAULT!**\n"
        f"• **Quota:** `20 Questions / Day`\n"
        f"• **Validity:** `2 Days Access`\n"
        f"• **Status:** Active & Ready\n\n"
        f"💡 *Need higher daily question limits? Check out our VIP Membership Packs via /plans!*\n\n"
        f"🚀 Tap **Launch Quiz** below to start practicing immediately!"
    )

    await update.message.reply_text(
        demo_msg,
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    await context.bot.send_message(
        chat_id=update.message.chat_id,
        text="👇 **QUICK NAVIGATION** 👇",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Launch Quiz", callback_data="cmd_quiz"), InlineKeyboardButton("👤 Profile Card", callback_data="cmd_profile")],
            [InlineKeyboardButton("💳 My Current Plan", callback_data="cmd_myplan"), InlineKeyboardButton("💳 VIP Plans", callback_data="cmd_plans")]
        ])
    )
    return ConversationHandler.END

async def edit_profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_onboarding_maintenance(update):
        return ConversationHandler.END

    user = update.effective_user
    can_edit, days_left = can_user_edit_profile(user.id)
    
    if not can_edit:
        msg = f"⏳ **PROFILE EDIT LOCKED!** ⏳\n\nProfile updates are permitted once every **30 days**.\nPlease try again in `{days_left} days`."
        if update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        else:
            await update.message.reply_text(msg, parse_mode="Markdown")
        return ConversationHandler.END

    warn_msg = (
        "⚠️ **PROFILE EDIT WARNING** ⚠️\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Please note: Profile details can only be edited **ONCE EVERY 30 DAYS**.\n\n"
        "Are you sure you wish to update your profile details now?"
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
        "✏️ **EDIT PROFILE SESSION STARTED (STEP 1/7)** ✏️\n\n"
        "Please reply with your updated **Full Name** (at least 4 letters):",
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
        await query.edit_message_text("⚠️ Session expired. Please type /start to log in again.")
        return ConversationHandler.END

    context.user_data["login_target_user"] = u

    rec_options = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛡 Security Question Recovery", callback_data="rec_opt_secq")],
        [InlineKeyboardButton("📱 Verified Phone Match", callback_data="rec_opt_phone")],
        [InlineKeyboardButton("🎂 DOB + Name Verification", callback_data="rec_opt_namedob")],
        [InlineKeyboardButton("🗓 DOB Grid Match", callback_data="rec_opt_dob")]
    ])

    await query.edit_message_text(
        f"🛡 **PIN & ACCOUNT RESET PORTAL** 🛡\n"
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
            f"🛡 **SECURITY QUESTION RECOVERY** 🛡\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"❓ **Question:** *{sec_q}*\n\n"
            f"✍️ Please reply with your answer below:",
            parse_mode="Markdown"
        )
        return REC_SEC_ANS

    elif data == "rec_opt_phone":
        contact_btn = KeyboardButton(text="📱 Share Verified Mobile Number", request_contact=True)
        markup = ReplyKeyboardMarkup([[contact_btn]], one_time_keyboard=True, resize_keyboard=True)
        await query.delete_message()
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"📱 **PHONE NUMBER RECOVERY** 📱\n\nTap the button below to share your contact number for match:",
            reply_markup=markup
        )
        return REC_PHONE

    elif data == "rec_opt_namedob":
        await query.edit_message_text(
            f"👤 **NAME & DOB RECOVERY** 👤\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Please reply with your **Registered Full Name** (or DOB formatted as DD-MM-YYYY):",
            parse_mode="Markdown"
        )
        return REC_NAME_DOB

    elif data == "rec_opt_dob":
        await query.edit_message_text(
            f"🎂 **DATE OF BIRTH RECOVERY** 🎂\n\nSelect your registered **Birth Year**:",
            reply_markup=build_year_keyboard(prefix="recdoby_"),
            parse_mode="Markdown"
        )
        return REC_DOB_YEAR

async def rec_sec_ans_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ans_input = update.message.text.strip().lower()
    u = context.user_data.get("login_target_user")
    correct_ans = str(u.get("security_answer", "")).strip().lower()

    if ans_input != correct_ans:
        rec_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 Reset Options", callback_data="login_forgot_pin")]])
        await update.message.reply_text("❌ **INCORRECT SECURITY ANSWER!** ❌\n\nPlease try again or tap below to pick another method:", reply_markup=rec_btn)
        return REC_SEC_ANS

    await update.message.reply_text(
        f"✅ **IDENTITY VERIFIED!** ✅\n\n"
        f"👤 **Student Name:** {u['full_name']}\n"
        f"🪪 **Student ID:** `{u['student_id']}`\n\n"
        f"🔑 Please reply with your **New Secret 4-Digit PIN** below:",
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
        rec_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 Reset Options", callback_data="login_forgot_pin")]])
        await update.message.reply_text(
            f"❌ **PHONE NUMBER MISMATCH!** Shared number does not match registered record for `{u['student_id']}`.",
            reply_markup=rec_btn
        )
        return REC_PHONE

    await update.message.reply_text(
        f"✅ **PHONE VERIFIED SUCCESSFULLY!** ✅\n\n"
        f"👤 **Student Name:** {u['full_name']}\n"
        f"🪪 **Student ID:** `{u['student_id']}`\n\n"
        f"🔑 Please reply with your **New Secret 4-Digit PIN** below:",
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
            f"✅ **NAME / DOB VERIFIED!** ✅\n\n"
            f"👤 **Student Name:** {u['full_name']}\n"
            f"🪪 **Student ID:** `{u['student_id']}`\n\n"
            f"🔑 Please reply with your **New Secret 4-Digit PIN** below:",
            parse_mode="Markdown"
        )
        return RESET_PIN

    rec_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 Reset Options", callback_data="login_forgot_pin")]])
    await update.message.reply_text(
        "❌ **VERIFICATION FAILED!** Input does not match records. Try again or select another option:",
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
        await query.edit_message_text(f"❌ **DOB MISMATCH!** Registered record does not match `{dob_constructed}`.", reply_markup=rec_btn)
        return RECOVERY_MENU

    await query.delete_message()
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=f"✅ **DOB VERIFIED!** ✅\n\n👤 **Student Name:** {u['full_name']}\n🪪 **Student ID:** `{u['student_id']}`\n\n🔑 Please reply with your **New Secret 4-Digit PIN** below:",
        parse_mode="Markdown"
    )
    return RESET_PIN

# Feature 4: Interactive Navigation Menu on Account PIN Unlock
async def reset_pin_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_pin = update.message.text.strip()
    if not new_pin.isdigit() or len(new_pin) != 4:
        await update.message.reply_text("⚠️ **INVALID PIN!**\n\nPIN must be exactly **4 numeric digits** (e.g. 1234). Please try again:")
        return RESET_PIN

    u = context.user_data.get("login_target_user")
    if not u:
        user = update.effective_user
        u = get_user_profile(user.id)

    target_uid = u['user_id']
    update_user_pin(target_uid, new_pin)

    unlocked_menu_btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Launch Quiz Now", callback_data="cmd_quiz"), InlineKeyboardButton("👤 My Profile", callback_data="cmd_profile")],
        [InlineKeyboardButton("💳 My Plan", callback_data="cmd_myplan"), InlineKeyboardButton("❓ Help & Support", callback_data="cmd_help")]
    ])

    await update.message.reply_text(
        f"🔓 **ACCOUNT UNLOCKED SUCCESSFULLY!** 🔓\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎉 **Welcome back, {u['full_name']}!** Your identity has been verified.\n"
        f"🪪 **Student ID:** `{u['student_id']}`\n"
        f"🔑 **New Secret PIN:** `{new_pin}`\n\n"
        f"✨ Select an option below to continue practicing on **Learn with HiM Quiz Book**:",
        reply_markup=unlocked_menu_btn,
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def cancel_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["is_editing_profile"] = False
    await update.message.reply_text("🛑 Setup cancelled. Type /start anytime to begin registration.", reply_markup=ReplyKeyboardRemove())
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