import time
import json
import logging
import math
import os
import zipfile
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from app.config import PRIMARY_ADMIN_ID, USER_PROFILES_DIR
from app.database import (
    get_all_users, set_maintenance_until, get_maintenance_until, 
    get_user_profile, get_db, sync_user_json_profile, toggle_user_ban_status
)
from app.pdf_generator import generate_student_pdf_report
from app.stats import get_user_performance_summary, calculate_user_rank, calculate_user_percentile

logger = logging.getLogger(__name__)
USERS_PER_PAGE = 8

async def admin_portal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != PRIMARY_ADMIN_ID:
        return

    users = get_all_users()
    m_until = get_maintenance_until()
    now_ts = int(time.time())
    m_status = "🟢 𝒜𝒸𝓉𝒾𝓋𝑒 (𝒪𝓃𝓁𝒾𝓃𝑒)" if now_ts >= m_until else "🔴 𝒫𝒜𝒰𝒮𝐸𝒟 (𝑀𝒶𝒾𝓃𝓉𝑒𝓃𝒶𝓃𝒸𝑒 𝑀𝑜𝒹𝑒)"

    keyboard = [
        [InlineKeyboardButton("👥 𝐵𝓇𝑜𝓌𝓈𝑒 𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝒟𝒾𝓇𝑒𝒸𝓉𝑜𝓇𝓎 (/user_profiles)", callback_data="admin_users_page_0")],
        [InlineKeyboardButton("🔍 𝒮𝑒𝒶𝓇𝒸𝒽 𝒮𝓉𝓊𝒹𝑒𝓃𝓉 (𝐼𝒟/𝒫𝒽𝑜𝓃𝑒/𝒩𝒶𝓂𝑒)", callback_data="admin_search_prompt")],
        [InlineKeyboardButton("📦 𝐸𝓍𝓅𝑜𝓇𝓉 𝒜𝓁𝓁 𝐿𝑒𝒹𝑔𝑒𝓇𝓈 (.𝓏𝒾𝓅)", callback_data="admin_export_zip")],
        [InlineKeyboardButton("⏸ 𝒫𝒶𝓊𝓈𝑒 𝐵𝑜𝓉 5 𝑀𝒾𝓃𝓈", callback_data="admin_pause_5"), InlineKeyboardButton("⏸ 𝒫𝒶𝓊𝓈𝑒 𝐵𝑜𝓉 10 𝑀𝒾𝓃𝓈", callback_data="admin_pause_10")],
        [InlineKeyboardButton("▶️ 𝑅𝑒𝓈𝓊𝓂𝑒 𝐵𝑜𝓉 𝒩𝑜𝓌", callback_data="admin_resume_now")],
        [InlineKeyboardButton("📢 𝒢𝓁𝑜𝒷𝒶𝓁 𝐵𝓇𝑜𝒶𝒹𝒸𝒶𝓈𝓉", callback_data="admin_broadcast")]
    ]

    msg = (
        f"👑 **𝑀𝒜𝒮𝒯𝐸𝑅 𝒜𝒟𝑀𝐼𝒩 𝒫𝒪𝑅𝒯𝒜𝐿 — 𝐻𝒾𝓂𝒶𝓃𝓈𝒽𝓊 𝒮𝒾𝓇**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 **𝒯𝑜𝓉𝒶𝓁 𝑅𝑒𝑔𝒾𝓈𝓉𝑒𝓇𝑒𝒹 𝒮𝓉𝓊𝒹𝑒𝓃𝓉𝓈:** `{len(users)}`\n"
        f"⚡ **𝐵𝑜𝓉 𝒮𝓎𝓈𝓉𝑒𝓂 𝒮𝓉𝒶𝓉𝓊𝓈:** `{m_status}`\n\n"
        f"𝒮𝑒𝓁𝑒𝒸𝓉 𝒶𝓃 𝒶𝒹𝓂𝒾𝓃𝒾𝓈𝓉𝓇𝒶𝓉𝒾𝓋𝑒 𝒶𝒸𝓉𝒾𝑜𝓃 𝒷𝑒𝓁𝑜𝓌:"
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    if user_id != PRIMARY_ADMIN_ID:
        return

    users = get_all_users()

    # Bulk ZIP Export
    if data == "admin_export_zip":
        await query.answer()
        await query.edit_message_text("⏳ **𝒢𝑒𝓃𝑒𝓇𝒶𝓉𝒾𝓃𝑔 𝐵𝓊𝓁𝓀 𝒵𝒾𝓅 𝒫𝒶𝒸𝓀𝒶𝑔𝑒...**\n𝒵𝒾𝓅𝓅𝒾𝓃𝑔 𝒶𝓁𝓁 𝓈𝓉𝓊𝒹𝑒𝓃𝓉 𝒥𝒮𝒪𝒩 𝓁𝑒𝒹𝑔𝑒𝓇𝓈...")
        
        for u in users:
            sync_user_json_profile(u['user_id'])

        zip_filename = "All_Student_Profiles_Export.zip"
        zip_path = os.path.join(USER_PROFILES_DIR, zip_filename)

        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, _, files in os.walk(USER_PROFILES_DIR):
                    for file in files:
                        if file.endswith('.json'):
                            file_path = os.path.join(root, file)
                            zipf.write(file_path, arcname=file)

            if os.path.exists(zip_path):
                with open(zip_path, "rb") as doc:
                    await context.bot.send_document(
                        chat_id=query.message.chat_id,
                        document=doc,
                        filename=zip_filename,
                        caption=f"📦 **𝑀𝒜𝒮𝒯𝐸𝑅 𝒮𝒯𝒰𝒟𝐸𝒩𝒯 𝒫𝑅𝒪𝐹𝐼𝐿𝐸𝒮 𝐵𝒜𝒞𝒦𝒰𝒫**\n\n𝒯𝑜𝓉𝒶𝓁 𝐹𝒾𝓁𝑒𝓈 𝐼𝓃𝒸𝓁𝓊𝒹𝑒𝒹: `{len(users)} 𝒥𝒮𝒪𝒩 𝓅𝓇𝑜𝒻𝒾𝓁𝑒𝓈`"
                    )
                os.remove(zip_path)
            await admin_portal_command(update, context)
        except Exception as e:
            await query.message.reply_text(f"⚠️ 𝐸𝓇𝓇𝑜𝓇 𝒸𝓇𝑒𝒶𝓉𝒾𝓃𝑔 𝓏𝒾𝓅 𝒶𝓇𝒸𝒽𝒾𝓋𝑒: {e}")

    # Pause 5 Mins
    elif data == "admin_pause_5":
        await query.answer()
        set_maintenance_until(int(time.time()) + 300)
        await query.edit_message_text("🛑 **𝐵𝑜𝓉 𝒮𝑒𝓇𝓋𝒾𝒸𝑒 𝒫𝒜𝒰𝒮𝐸𝒟 𝒻𝑜𝓇 5 𝑀𝒾𝓃𝓊𝓉𝑒𝓈.**\n𝐵𝓇𝑜𝒶𝒹𝒸𝒶𝓈𝓉𝒾𝓃𝑔 𝓃𝑜𝓉𝒾𝒸𝑒 𝓉𝑜 𝒶𝓁𝓁 𝓊𝓈𝑒𝓇𝓈...")
        for u in users:
            try:
                await context.bot.send_message(
                    chat_id=u['user_id'], 
                    text="📢 **𝒜𝒟𝑀𝐼𝒩 𝐻𝒜𝒮 𝒫𝒜𝒰𝒮𝐸𝒟 𝒯𝐻𝐸 𝒮𝐸𝑅𝒱𝐼𝒞𝐸 𝐹𝒪𝑅 5 𝑀𝐼𝒩𝒮**\n\n⏰ 𝒮𝑒𝓇𝓋𝒾𝒸𝑒𝓈 𝓌𝒾𝓁𝓁 𝒶𝓊𝓉𝑜𝓂𝒶𝓉𝒾𝒸𝒶𝓁𝓁𝓎 𝓇𝑒𝓈𝓊me 𝒾𝓃 5 𝓂𝒾𝓃𝓊𝓉𝑒𝓈."
                )
            except Exception:
                pass

    # Pause 10 Mins
    elif data == "admin_pause_10":
        await query.answer()
        set_maintenance_until(int(time.time()) + 600)
        await query.edit_message_text("🛑 **𝐵𝑜𝓉 𝒮𝑒𝓇𝓋𝒾𝒸𝑒 𝒫𝒜𝒰𝒮𝐸𝒟 𝒻𝑜𝓇 10 𝑀𝒾𝓃𝓊𝓉𝑒𝓈.**\n𝐵𝓇𝑜𝒶𝒹𝒸𝒶𝓈𝓉𝒾𝓃𝑔 𝓃𝑜𝓉𝒾𝒸𝑒 𝓉𝑜 𝒶𝓁𝓁 𝓊𝓈𝑒𝓇𝓈...")
        for u in users:
            try:
                await context.bot.send_message(
                    chat_id=u['user_id'], 
                    text="📢 **𝒜𝒟𝑀𝐼𝒩 𝐻𝒜𝒮 𝒫𝒜𝒰𝒮𝐸𝒟 𝒯𝐻𝐸 𝒮𝐸𝑅𝒱𝐼𝒞𝐸 𝐹𝒪𝑅 10 𝑀𝐼𝒩𝒮**\n\n⏰ 𝒮𝑒𝓇𝓋𝒾𝒸𝑒𝓈 𝓌𝒾𝓁𝓁 𝒶𝓊𝓉𝑜𝓂𝒶𝓉𝒾𝒸𝒶𝓁𝓁𝓎 𝓇𝑒𝓈𝓊𝓂𝑒 𝒾𝓃 10 𝓂𝒾𝓃𝓊𝓉𝑒𝓈."
                )
            except Exception:
                pass

    # Resume Bot
    elif data == "admin_resume_now":
        await query.answer()
        set_maintenance_until(0)
        await query.edit_message_text("🟢 **𝐵𝑜𝓉 𝒮𝑒𝓇𝓋𝒾𝒸𝑒 𝑅𝐸𝒮𝒰𝑀𝐸𝒟 𝐼𝓂𝓂𝑒𝒹𝒾𝒶𝓉𝑒𝓁𝓎.**\n𝐵𝓇𝑜𝒶𝒹𝒸𝒶𝓈𝓉𝒾𝓃𝑔 𝓃𝑜𝓉𝒾𝒸𝑒 𝓉𝑜 𝒶𝓁𝓁 𝓊𝓈𝑒𝓇𝓈...")
        for u in users:
            try:
                await context.bot.send_message(
                    chat_id=u['user_id'], 
                    text="📢 **𝒜𝒟𝑀𝐼𝒩 𝐻𝒜𝒮 𝑅𝐸𝒮𝒰𝑀𝐸𝒟 𝒯𝐻𝐸 𝒮𝐸𝑅𝒱𝐼𝒞𝐸𝒮, 𝒩𝒪𝒲 𝒴𝒪𝒰 𝒞𝒜𝒩 𝒜𝒯𝒯𝐸𝑀𝒫𝒯 !!**"
                )
            except Exception:
                pass

    # Search Student Prompt
    elif data == "admin_search_prompt":
        await query.answer()
        context.user_data["awaiting_admin_search"] = True
        await query.edit_message_text("🔍 **𝒮𝒯𝒰𝒟𝐸𝒩𝒯 𝒮𝐸𝒜𝑅𝒞𝐻 𝐸𝒩𝒢𝐼𝒩𝐸**\n\n𝒫𝓁𝑒𝒶𝓈𝑒 𝓇𝑒𝓅𝓁𝓎 𝓌𝒾𝓉𝒽 𝓉𝒽𝑒 𝓈𝓉𝓊𝒹𝑒𝓃𝓉'𝓈 **𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝐼𝒟**, **𝒫𝒽𝑜𝓃𝑒 𝒩𝓊𝓂𝒷𝑒𝓇**, 𝑜𝓇 **𝐹𝓊𝓁𝓁 𝒩𝒶𝓂𝑒**:")

    # Generate and Send PDF Report
    elif data.startswith("genpdf_"):
        await query.answer()
        raw = data.replace("genpdf_", "")
        parts = raw.split("_")
        target_uid = int(parts[0])
        filter_mode = "_".join(parts[1:])

        await query.edit_message_text("⏳ **𝒢𝑒𝓃𝑒𝓇𝒶𝓉𝒾𝓃𝑔 𝒞𝓊𝓈𝓉𝑜𝓂 𝒫𝒟𝐹 𝑅𝑒𝓅𝑜𝓇𝓉 𝒞𝒶𝓇𝒹...**\n𝐵𝓊𝒾𝓁𝒹𝒾𝓃𝑔 𝓈𝓉𝒶𝓉𝓈, 𝒻𝑜𝓇𝓂𝒶𝓉𝓉𝒾𝓃𝑔 𝓉𝒶𝒷𝓁𝑒𝓈, 𝒶𝓃𝒹 𝓇𝑒𝓃𝒹𝑒𝓇𝒾𝓃𝑔 𝒫𝒟𝐹...")
        
        pdf_file = generate_student_pdf_report(target_uid, filter_mode)
        u = get_user_profile(target_uid)
        sid = u.get("student_id") or f"USER_{target_uid}" if u else f"USER_{target_uid}"
        student_name = u.get("full_name", "𝒮𝓉𝓊𝒹𝑒𝓃𝓉") if u else "𝒮𝓉𝓊𝒹𝑒𝓃𝓉"

        if pdf_file == "NO_ATTEMPTS":
            nav_buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("📄 𝐸𝓍𝓅𝑜𝓇𝓉 𝒜𝓃𝑜𝓉𝒽𝑒𝓇 𝒫𝒟𝐹 𝑅𝑒𝓅𝑜𝓇𝓉", callback_data=f"audit_pdfmenu_{target_uid}")],
                [InlineKeyboardButton("🔙 𝐵𝒶𝒸𝓀 𝓉𝑜 𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝒟𝒶𝓈𝒽𝒷𝑜𝒶𝓇𝒹", callback_data=f"admin_inspect_u_{target_uid}")]
            ])
            await query.edit_message_text(
                f"ℹ️ **𝒩𝒪 𝒬𝒰𝐼𝒩 𝒜𝒯𝒯𝐸𝑀𝒫𝒯𝒮 𝐹𝒪𝒰𝒩𝒟!**\n\n"
                f"𝒮𝓉𝓊𝒹𝑒𝓃𝓉 **{student_name}** (`{sid}`) 𝒽𝒶𝓈 𝓃𝑜𝓉 𝒶𝓉𝓉𝑒𝓂𝓅𝓉𝑒𝒹 𝒶𝓃𝓎 𝓆𝓊𝒾𝓏𝓏𝑒𝓈 𝒾𝓃 𝓉𝒽𝑒 𝓈𝑒𝓁𝑒𝒸𝓉𝑒𝒹 𝓉𝒾𝓂𝑒𝒻𝓇𝒶𝓂𝑒.",
                reply_markup=nav_buttons,
                parse_mode="Markdown"
            )
        elif pdf_file and pdf_file.startswith("ERROR_DETAILS:"):
            nav_buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 𝐵𝒶𝒸𝓀 𝓉𝑜 𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝒟𝒶𝓈𝒽𝒷𝑜𝒶𝓇𝒹", callback_data=f"admin_inspect_u_{target_uid}")]
            ])
            err_text = str(pdf_file[:3500])
            await query.edit_message_text(
                f"⚠️ **𝒫𝒟𝐹 𝒢𝑒𝓃𝑒𝓇𝒶𝓉𝒾𝑜𝓃 𝐸𝓇𝓇𝑜𝓇:**\n\n`{err_text}`",
                reply_markup=nav_buttons,
                parse_mode="Markdown"
            )
        elif pdf_file and os.path.exists(pdf_file):
            with open(pdf_file, "rb") as doc:
                await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=doc,
                    filename=os.path.basename(pdf_file),
                    caption=(
                        f"📄 **𝒪𝐹𝐹𝐼𝒞𝐼𝒜𝐿 𝒮𝒯𝒰𝒟𝐸𝒩𝒯 𝒫𝒟𝐹 𝒜𝒞𝒜𝒟𝐸𝑀𝐼𝒞 𝑅𝐸𝒫𝒪𝑅𝒯**\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"👤 **𝒮𝓉𝓊𝒹𝑒𝓃𝓉:** {student_name}\n"
                        f"🪪 **𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝐼𝒟:** `{sid}`\n"
                        f"📊 **𝑅𝑒𝓅𝑜𝓇𝓉 𝑀𝑜𝒹𝓊𝓁𝑒:** `{filter_mode.replace('_', ' ').title()}`\n"
                        f"🏷 **𝒲𝒶𝓉𝑒𝓇𝓂𝒶𝓇𝓀:** `@LearnwithHiM`"
                    )
                )
            
            nav_buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("📄 𝐸𝓍𝓅𝑜𝓇𝓉 𝒜𝓃𝑜𝓉𝒽𝑒𝓇 𝒫𝒟𝐹 𝑅𝑒𝓅𝑜𝓇𝓉", callback_data=f"audit_pdfmenu_{target_uid}")],
                [InlineKeyboardButton("🔙 𝐵𝒶𝒸𝓀 𝓉𝑜 𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝒟𝒶𝓈𝒽𝒷𝑜𝒶𝓇𝒹", callback_data=f"admin_inspect_u_{target_uid}")],
                [InlineKeyboardButton("👑 𝑀𝒶𝒾𝓃 𝒜𝒹𝓂𝒾𝓃 𝒫𝑜𝓇𝓉𝒶𝓁", callback_data="admin_home")]
            ])
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="👇 **𝒬𝓊𝒾𝒸𝓀 𝒜𝒸𝓉𝒾𝑜𝓃𝓈 & 𝒩𝒶𝓋𝒾𝑔𝒶𝓉𝒾𝑜𝓃:**",
                reply_markup=nav_buttons
            )
        else:
            nav_buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 𝐵𝒶𝒸𝓀 𝓉𝑜 𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝒟𝒶𝓈𝒽𝒷𝑜𝒶𝓇𝒹", callback_data=f"admin_inspect_u_{target_uid}")]
            ])
            await query.edit_message_text(
                "⚠️ **𝐹𝒶𝒾𝓁𝑒𝒹 𝓉𝑜 𝑔𝑒𝓃𝑒𝓇𝒶𝓉𝑒 𝒫𝒟𝐹 𝒻𝒾𝓁𝑒.**",
                reply_markup=nav_buttons
            )

    # Paginated Student Directory
    elif data.startswith("admin_users_page_"):
        await query.answer()
        page = int(data.replace("admin_users_page_", ""))
        total_users = len(users)

        if total_users == 0:
            await query.edit_message_text("📁 𝒩𝑜 𝓇𝑒𝑔𝒾𝓈𝓉𝑒𝓇𝑒𝒹 𝓈𝓉𝓊𝒹𝑒𝓃𝓉𝓈 𝒻𝑜𝓊𝓃𝒹 𝒾𝓃 𝒹𝒶𝓉𝒶𝒷𝒶𝓈𝑒.")
            return

        total_pages = math.ceil(total_users / USERS_PER_PAGE)
        page = max(0, min(page, total_pages - 1))

        start_idx = page * USERS_PER_PAGE
        end_idx = start_idx + USERS_PER_PAGE
        page_users = users[start_idx:end_idx]

        keyboard = []
        for u in page_users:
            sid = u.get("student_id") or f"USER_{u['user_id']}"
            ban_flag = " 🛑" if u.get("is_banned") else ""
            btn_text = f"👤 {u['full_name']}{ban_flag} (𝐼𝒟: {sid})"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"admin_inspect_u_{u['user_id']}")])

        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️ 𝒫𝓇𝑒𝓋", callback_data=f"admin_users_page_{page - 1}"))
        nav_row.append(InlineKeyboardButton(f"📄 𝒫𝒶𝑔𝑒 {page + 1}/{total_pages}", callback_data="ignore"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("𝒩𝑒𝓍𝓉 ▶️", callback_data=f"admin_users_page_{page + 1}"))
        
        keyboard.append(nav_row)
        keyboard.append([InlineKeyboardButton("🔙 𝐵𝒶𝒸𝓀 𝓉𝑜 𝒜𝒹𝓂𝒾𝓃 𝒫𝑜𝓇𝓉𝒶𝓁", callback_data="admin_home")])

        msg = (
            f"👥 **𝒮𝒯𝒰𝒟𝐸𝒩𝒯 𝒟𝐼𝑅𝐸𝒞𝒯𝒪𝑅𝒴 𝐿𝐸𝒟𝒢𝐸𝑅**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **𝒯𝑜𝓉𝒶𝓁 𝒮𝓉𝓊𝒹𝑒𝓃𝓉𝓈:** `{total_users}`\n"
            f"• **𝒫𝒶𝑔𝑒:** `{page + 1}` 𝑜𝒻 `{total_pages}`\n\n"
            f"𝒯𝒶𝓅 𝒶𝓃𝓎 𝓈𝓉𝓊𝒹𝑒𝓃𝓉 𝒷𝑒𝓁𝑜𝓌 𝓉𝑜 𝒶𝒸𝒸𝑒𝓈𝓈 𝓉𝒽𝑒𝒾𝓇 𝒻𝓊𝓁𝓁 𝒾𝓃𝓈𝓅𝑒𝒸𝓉𝒾𝑜𝓃 𝒹𝒶𝓈𝒽𝒷𝑜𝒶𝓇𝒹:"
        )
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    # Main Admin Home
    elif data == "admin_home":
        await query.answer()
        await admin_portal_command(update, context)

    # Student Inspection Dashboard
    elif data.startswith("admin_inspect_u_"):
        await query.answer()
        target_uid = int(data.replace("admin_inspect_u_", ""))
        u = get_user_profile(target_uid)

        if not u:
            await query.edit_message_text("⚠️ 𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝓅𝓇𝑜𝒻𝒾𝓁𝑒 𝓃𝑜𝓉 𝒻𝑜𝓊𝓃𝒹.")
            return

        sid = u.get("student_id") or f"USER_{u.get('user_id')}"
        is_banned = u.get("is_banned", 0)
        ban_text = "🟢 𝒜𝒞𝒯𝐼𝒱𝐸" if not is_banned else "🔴 𝐵𝒜𝒩𝒩𝐸𝒟"

        keyboard = [
            [InlineKeyboardButton("📋 𝒫𝑒𝓇𝓈𝑜𝓃𝒶𝓁 𝒟𝑒𝓉𝒶𝒾𝓁𝓈", callback_data=f"audit_personal_{target_uid}"), InlineKeyboardButton("🔑 𝒰𝓈𝑒𝓇 𝒫𝐼𝒩 & 𝒮𝑒𝒸𝓊𝓇𝒾𝓉𝓎 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈", callback_data=f"audit_pinsec_{target_uid}")],
            [InlineKeyboardButton("⏱ 𝒯𝒾𝓂𝑒 & 𝒜𝒸𝓉𝒾𝓋𝒾𝓉𝓎 𝐿𝑜𝑔", callback_data=f"audit_activity_{target_uid}"), InlineKeyboardButton("📊 𝒪𝓋𝑒𝓇𝒶𝓁𝓁 𝒫𝑒𝓇𝒻𝑜𝓇𝓂𝒶𝓃𝒸𝑒", callback_data=f"audit_perf_{target_uid}")],
            [InlineKeyboardButton("📅 𝒟𝒶𝓉𝑒-𝓌𝒾𝓈𝑒 𝒬𝓊𝒾𝓏 𝒮𝓊𝓂𝓂𝒶𝓇𝓎", callback_data=f"audit_datesummary_{target_uid}"), InlineKeyboardButton("🎯 𝒜𝓉𝓉𝑒𝓂𝓅𝓉𝑒𝒹 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈", callback_data=f"audit_attempted_{target_uid}")],
            [InlineKeyboardButton("❌ 𝒲𝓇𝑜𝓃𝑔 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈", callback_data=f"audit_wrong_{target_uid}"), InlineKeyboardButton("💾 𝒮𝒶𝓋𝑒𝒹 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈", callback_data=f"audit_saved_{target_uid}")],
            [InlineKeyboardButton("💬 𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝐹𝑒𝑒𝒹𝒷𝒶𝒸𝓀", callback_data=f"audit_feedback_{target_uid}"), InlineKeyboardButton("🎁 𝒢𝓇𝒶𝓃𝓉 +20 𝐵𝑜𝓃𝓊𝓈 𝒬𝓊𝑜𝓉𝒶", callback_data=f"audit_grant_{target_uid}")],
            [InlineKeyboardButton("📄 𝐸𝓍𝓅𝑜𝓇𝓉 𝒫𝒟𝐹 𝑅𝑒𝓅𝑜𝓇𝓉𝓈 𝒪𝓅𝓉𝒾𝑜𝓃𝓈", callback_data=f"audit_pdfmenu_{target_uid}"), InlineKeyboardButton("📥 𝐸𝓍𝓅𝑜𝓇𝓉 𝑅𝒶𝓌 𝒥𝒮𝒪𝒩 𝐹𝒾𝓁𝑒", callback_data=f"audit_exportjson_{target_uid}")],
            [InlineKeyboardButton("🔙 𝐵𝒶𝒸𝓀 𝓉𝑜 𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝒟𝒾𝓇𝑒𝒸𝓉𝑜𝓇𝓎", callback_data="admin_users_page_0")]
        ]

        msg = (
            f"🪪 **𝒮𝒯𝒰𝒟𝐸𝒩𝒯 𝒜𝒰𝒟𝐼𝒯 𝒞𝒪𝒩𝒯𝑅𝒪𝐿 𝒫𝒜𝒩𝐸𝐿**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝒩𝒶𝓂𝑒:** {u.get('full_name')}\n"
            f"• **𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝐼𝒟:** `{sid}`\n"
            f"• **𝒯𝑒𝓁𝑒𝑔𝓇𝒶𝓂 𝐼𝒟:** `{u.get('user_id')}`\n"
            f"• **𝒯𝒶𝓇𝑔𝑒𝓉 𝐸𝓍𝒶𝓂:** `{u.get('target_exam')}`\n"
            f"• **𝒜𝒸𝒸𝑜𝓊𝓃𝓉 𝒮𝓉𝒶𝓉𝓊𝓈:** `{ban_text}`\n"
            f"• **𝐹𝒾𝓁𝑒 𝐿𝑒𝒹𝑔𝑒𝓇:** `data/user_profiles/{sid}.json`\n\n"
            f"𝒮𝑒𝓁𝑒𝒸𝓉 𝒶𝓃 𝒶𝓊𝒹𝒾𝓉 𝓂𝑜𝒹𝓊𝓁𝑒 𝒷𝑒𝓁𝑜𝓌 𝓉𝑜 𝓋𝒾𝑒𝓌 𝒹𝑒𝓉𝒶𝒾𝓁𝑒𝒹 𝓇𝑒𝓅𝑜𝓇𝓉𝓈:"
        )
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    # User PIN & Security Questions Audit
    elif data.startswith("audit_pinsec_"):
        await query.answer()
        target_uid = int(data.replace("audit_pinsec_", ""))
        u = get_user_profile(target_uid)

        msg = (
            f"🔑 **𝒰𝒮𝐸𝑅 𝒫𝐼𝒩 & 𝒮𝐸𝒞𝒰𝑅𝐼𝒯𝒴 𝒬𝒰𝐸𝒮𝒯𝐼𝒪𝒩𝒮 (𝒜𝒟𝑀𝐼𝒩 𝒜𝒰𝒟𝐼𝒯)**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 **𝒮𝓉𝓊𝒹𝑒𝓃𝓉:** {u.get('full_name')} (`{u.get('student_id')}`)\n\n"
            f"• **𝒮𝑒𝒸𝓇𝑒𝓉 4-𝒟𝒾𝑔𝒾𝓉 𝒫𝐼𝒩:** `{u.get('pin', '𝒩𝑜𝓉 𝒮𝑒𝓉')}`\n"
            f"• **𝒮𝑒𝒸𝓊𝓇𝒾𝓉𝓎 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃:** *\"{u.get('security_question', '𝒩𝑜𝓉 𝒮𝑒𝓉')}\"*\n"
            f"• **𝒮𝑒𝒸𝓊𝓇𝒾𝓉𝓎 𝒜𝓃𝓈𝓌𝑒𝓇:** `{u.get('security_answer', '𝒩𝑜𝓉 𝒮𝑒𝓉')}`\n\n"
            f"⚠️ *𝒞𝑜𝓃𝒻𝒾𝒹𝑒𝓃𝓉𝒾𝒶𝓁: 𝒱𝒾𝓈𝒾𝒷𝓁𝑒 𝓈𝓉𝓇𝒾𝒸𝓉𝓁𝓎 𝓉𝑜 𝒫𝓇𝒾𝓂𝒶𝓇𝓎 𝒜𝒹𝓂𝒾𝓃.*"
        )
        keyboard = [[InlineKeyboardButton("🔙 𝐵𝒶𝒸𝓀 𝓉𝑜 𝒟𝒶𝓈𝒽𝒷𝑜𝒶𝓇𝒹", callback_data=f"admin_inspect_u_{target_uid}")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    # Reorganized PDF Menu Options
    elif data.startswith("audit_pdfmenu_"):
        await query.answer()
        target_uid = int(data.replace("audit_pdfmenu_", ""))
        
        pdf_buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 1. 𝐿𝒶𝓈𝓉 1 𝑀𝑜𝓃𝓉𝒽 𝐹𝓊𝓁𝓁 𝒟𝒶𝓉𝒶 𝑅𝑒𝓅𝑜𝓇𝓉", callback_data=f"genpdf_{target_uid}_last_1_month_data")],
            [InlineKeyboardButton("📊 2. 𝐿𝒶𝓈𝓉 1 𝑀𝑜𝓃𝓉𝒽 𝒬𝓊𝒾𝓏 𝒮𝓊𝓂𝓂𝒶𝓇𝓎 𝑅𝑒𝓅𝑜𝓇𝓉 (𝒩𝑜 𝒬𝓈)", callback_data=f"genpdf_{target_uid}_last_1_month_quiz")],
            [InlineKeyboardButton("📜 3. 𝒜𝓁𝓁 𝑀𝑜𝓃𝓉𝒽𝓈 𝐹𝓊𝓁𝓁 𝒟𝒶𝓉𝒶 𝑅𝑒𝓅𝑜𝓇𝓉", callback_data=f"genpdf_{target_uid}_all_months_data")],
            [InlineKeyboardButton("📈 4. 𝒜𝓁𝓁 𝑀𝑜𝓃𝓉𝒽𝓈 𝒬𝓊𝒾𝓏 𝒮𝓊𝓂𝓂𝒶𝓇𝓎 𝑅𝑒𝓅𝑜𝓇𝓉 (𝒩𝑜 𝒬𝓈)", callback_data=f"genpdf_{target_uid}_all_months_quiz")],
            [InlineKeyboardButton("🔙 𝐵𝒶𝒸𝓀 𝓉𝑜 𝒟𝒶𝓈𝒽𝒷𝑜𝒶𝓇𝒹", callback_data=f"admin_inspect_u_{target_uid}")]
        ])

        await query.edit_message_text(
            f"📄 **𝒫𝒟𝐹 𝑅𝐸𝒫𝒪𝑅𝒯 𝒞𝒜𝑅𝒟 𝒢𝐸𝒩𝐸𝑅𝒜𝒯𝒪𝑅**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"𝒮𝑒𝓁𝑒𝒸𝓉 𝓉𝒽𝑒 𝑒𝓍𝒶𝒸𝓉 𝓇𝑒𝓅𝑜𝓇𝓉 𝓉𝓎𝓅𝑒 𝓉𝑜 𝑔𝑒𝓃𝑒𝓇𝒶𝓉𝑒:",
            reply_markup=pdf_buttons,
            parse_mode="Markdown"
        )

    # Audit Module 1: Personal Details
    elif data.startswith("audit_personal_"):
        await query.answer()
        target_uid = int(data.replace("audit_personal_", ""))
        u = get_user_profile(target_uid)
        
        if not u:
            await query.edit_message_text("⚠️ 𝐸𝓇𝓇𝑜𝓇 𝓇𝑒𝓉𝓇𝒾𝑒𝓋𝒾𝓃𝑔 𝓊𝓈𝑒𝓇 𝓅𝓇𝑜𝒻𝒾𝓁𝑒.")
            return

        sid = u.get("student_id") or f"USER_{u.get('user_id')}"
        ban_status = "𝐵𝒜𝒩𝒩𝐸𝒟 🔴" if u.get("is_banned") else "𝒜𝒞𝒯𝐼𝒱𝐸 🟢"
        
        edit_cnt = u.get("edit_count", 0)
        last_edit = u.get("last_profile_edit", "𝒩𝑒𝓋𝑒𝓇")
        remaining_edits = max(0, 3 - edit_cnt)

        msg = (
            f"📋 **𝒮𝒯𝒰𝒟𝐸𝒩𝒯 𝒫𝐸𝑅𝒮𝒪𝒩𝒜𝐿 𝒟𝐸𝒯𝒜𝐼𝐿𝒮**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **𝐹𝓊𝓁𝓁 𝒩𝒶𝓂𝑒:** {u.get('full_name', '𝒩/𝒜')}\n"
            f"• **𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝐼𝒟:** `{sid}`\n"
            f"• **𝒯𝑒𝓁𝑒𝑔𝓇𝒶𝓂 𝐼𝒟:** `{u.get('user_id')}`\n"
            f"• **𝒜𝒸𝒸𝑜𝓊𝓃𝓉 𝒮𝓉𝒶𝓉𝓊𝓈:** `{ban_status}`\n"
            f"• **𝒰𝓈𝑒𝓇𝓃𝒶𝓂𝑒:** @{u.get('username') or '𝒩/𝒜'}\n"
            f"• **𝒫𝒽𝑜𝓃𝑒 𝒩𝓊𝓂𝒷𝑒𝓇:** `{u.get('phone_number') or '𝒩/𝒜'}`\n"
            f"• **𝒯𝒶𝓇𝑔𝑒𝓉 𝐸𝓍𝒶𝓂:** `{u.get('target_exam', '𝒩/𝒜')}`\n"
            f"• **𝒟𝒶𝓉𝑒 𝑜𝒻 𝐵𝒾𝓇𝓉𝒽:** `{u.get('dob', '𝒩/𝒜')}`\n"
            f"• **𝒞𝒶𝓁𝒸𝓊𝓁𝒶𝓉𝑒𝒹 𝒜𝑔𝑒:** `{u.get('age', '𝒩/𝒜')} 𝓎𝓇𝓈`\n"
            f"• **𝒢𝑒𝓃𝒹𝑒𝓇:** `{u.get('gender', '𝒩/𝒜')}`\n"
            f"• **𝐿𝑜𝒸𝒶𝓉𝒾𝑜𝓃:** `{u.get('state', '𝒩/𝒜')}, {u.get('country', '𝐼𝓃𝒹𝒾𝒶')}`\n"
            f"• **𝒫𝓇𝑜𝒻𝒾𝓁𝑒 𝐸𝒹𝒾𝓉𝓈 𝑀𝒶𝒹𝑒:** `{edit_cnt} / 3 𝓉𝒾𝓂𝑒𝓈` *(𝐿𝒶𝓈𝓉: {last_edit})*\n"
            f"• **𝑅𝑒𝓂𝒶𝒾𝓃𝒾𝓃𝑔 𝐸𝒹𝒾𝓉𝓈:** `{remaining_edits} 𝓁𝑒𝒻𝓉`\n"
            f"• **𝐵𝑜𝓃𝓊𝓈 𝒬𝓊𝑜𝓉𝒶:** `{u.get('bonus_quota', 0)} 𝒬𝓈`\n"
            f"• **𝑅𝑒𝑔𝒾𝓈𝓉𝑒𝓇𝑒𝒹 𝒜𝓉:** `{u.get('created_at', '𝒩/𝒜')}`\n"
            f"• **𝐿𝒶𝓈𝓉 𝒜𝒸𝓉𝒾𝓋𝑒:** `{u.get('last_active', '𝒩/𝒜')}`\n"
            f"• **𝑅𝑒𝒻𝑒𝓇𝓇𝑒𝒹 𝐵𝓎 𝐼𝒟:** `{u.get('referred_by') or '𝒩𝑜𝓃𝑒'}`\n"
            f"• **𝑅𝑒𝒻𝑒𝓇𝓇𝒶𝓁 𝒞𝑜𝓊𝓃𝓉:** `{u.get('referral_count', 0)}` 𝒻𝓇𝒾𝑒𝓃𝒹𝓈"
        )
        keyboard = [[InlineKeyboardButton("🔙 𝐵𝒶𝒸𝓀 𝓉𝑜 𝒟𝒶𝓈𝒽𝒷𝑜𝒶𝓇𝒹", callback_data=f"admin_inspect_u_{target_uid}")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    # Audit Module 2: Time & Activity Log
    elif data.startswith("audit_activity_"):
        await query.answer()
        target_uid = int(data.replace("audit_activity_", ""))
        u = get_user_profile(target_uid)
        
        conn = get_db()
        rows = conn.execute("SELECT date_str, seconds_spent FROM user_activity_time WHERE user_id = ? ORDER BY date_str DESC", (target_uid,)).fetchall()
        conn.close()

        total_sec = sum([r['seconds_spent'] for r in rows])
        
        lines = [
            f"⏱ **𝒮𝒯𝒰𝒟𝐸𝒩𝒯 𝒜𝒞𝒯𝐼𝒱𝐼𝒯𝒴 & 𝒯𝐼𝑀𝐸 𝐿𝒪𝒢**",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"👤 **𝒮𝓉𝓊𝒹𝑒𝓃𝓉:** {u.get('full_name')} (`{u.get('student_id')}`)",
            f"• **𝐿𝒶𝓈𝓉 𝐿𝑜𝑔𝒾𝓃 / 𝒜𝒸𝓉𝒾𝓋𝑒:** `{u.get('last_active', '𝒩/𝒜')}`",
            f"• **𝒯𝑜𝓉𝒶𝓁 𝒯𝒾𝓂𝑒 𝒮𝓅𝑒𝓃𝓉 𝒪𝓋𝑒𝓇𝒶𝓁𝓁:** `{total_sec} 𝓈𝑒𝒸` ({round(total_sec/60, 2)} 𝓂𝒾𝓃𝓈)\n",
            f"📅 **𝒟𝒶𝓉𝑒-𝒲𝒾𝓈𝑒 𝒯𝒾𝓂𝑒 𝒮𝓅𝑒𝓃𝓉 𝐵𝓇𝑒𝒶𝓀𝒹𝑜𝓌𝓃:**"
        ]

        if rows:
            for r in rows:
                mins = round(r['seconds_spent'] / 60, 2)
                lines.append(f" • `{r['date_str']}`: {r['seconds_spent']}𝓈 ({mins} 𝓂𝒾𝓃𝓈)")
        else:
            lines.append(" • *𝒩𝑜 𝒶𝒸𝓉𝒾𝓋𝒾𝓉𝓎 𝓉𝒾𝓂𝑒 𝓇𝑒𝒸𝑜𝓇𝒹𝑒𝒹 𝓎𝑒𝓉.*")

        msg = "\n".join(lines)
        keyboard = [[InlineKeyboardButton("🔙 𝐵𝒶𝒸𝓀 𝓉𝑜 𝒟𝒶𝓈𝒽𝒷𝑜𝒶𝓇𝒹", callback_data=f"admin_inspect_u_{target_uid}")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    # Audit Module 3: Overall Performance
    elif data.startswith("audit_perf_"):
        await query.answer()
        target_uid = int(data.replace("audit_perf_", ""))
        u = get_user_profile(target_uid)
        perf = get_user_performance_summary(target_uid)
        rank = calculate_user_rank(target_uid)
        percentile = calculate_user_percentile(target_uid)

        total_qs = perf.get('total_qs', 0) or 0
        total_correct = perf.get('total_correct', 0) or 0
        acc = round((total_correct / total_qs) * 100, 2) if total_qs > 0 else 0.0

        msg = (
            f"📊 **𝒮𝒯𝒰𝒟𝐸𝒩𝒯 𝒪𝒱𝐸𝑅𝒜𝐿𝐿 𝒫𝐸𝑅𝐹𝒪𝑅𝑀𝒜𝒩𝒞𝐸**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 **𝒮𝓉𝓊𝒹𝑒𝓃𝓉:** {u.get('full_name')} (`{u.get('student_id')}`)\n\n"
            f"• **𝒯𝑜𝓉𝒶𝓁 𝒯𝑒𝓈𝓉𝓈 𝒞𝑜𝓂𝓅𝓁𝑒𝓉𝑒𝒹:** `{perf.get('total_tests', 0)}`\n"
            f"• **𝒯𝑜𝓉𝒶𝓁 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈 𝒜𝓉𝓉𝑒𝓂𝓅𝓉𝑒𝒹:** `{total_qs}`\n"
            f"• **𝒞𝑜𝓇𝓇𝑒𝒸𝓉 𝒜𝓃𝓈𝓌𝑒𝓇𝓈:** `{total_correct}` ✅\n"
            f"• **𝒲𝓇𝑜𝓃𝑔 𝒜𝓃𝓈𝓌𝑒𝓇𝓈:** `{perf.get('total_wrong', 0)}` ❌\n"
            f"• **𝒮𝓀𝒾𝓅𝓅𝑒𝒹 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈:** `{perf.get('total_skipped', 0)}` ⏭\n"
            f"• **𝒜𝒸𝒸𝓊𝓇𝒶𝒸𝓎 𝑅𝒶𝓉𝒾𝓃𝑔:** `{acc}%`\n"
            f"• **𝒜𝓋𝑒𝓇𝒶𝑔𝑒 𝒮𝒸𝑜𝓇𝑒:** `{round(perf.get('avg_score', 0.0) or 0.0, 2)}`\n"
            f"• **𝒢𝓁𝑜𝒷𝒶𝓁 𝑅𝒶𝓃𝓀:** `{rank}`\n"
            f"• **𝒪𝓋𝑒𝓇𝒶𝓁𝓁 𝒫𝑒𝓇𝒸𝑒𝓃𝓉𝒾𝓁𝑒:** `{percentile}%`"
        )
        keyboard = [[InlineKeyboardButton("🔙 𝐵𝒶𝒸𝓀 𝓉𝑜 𝒟𝒶𝓈𝒽𝒷𝑜𝒶𝓇𝒹", callback_data=f"admin_inspect_u_{target_uid}")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    # Audit Module 4: Date-Wise Quiz Summary
    elif data.startswith("audit_datesummary_"):
        await query.answer()
        target_uid = int(data.replace("audit_datesummary_", ""))
        u = get_user_profile(target_uid)

        conn = get_db()
        attempts = conn.execute("SELECT * FROM quiz_attempts WHERE user_id = ? ORDER BY id DESC", (target_uid,)).fetchall()
        conn.close()

        lines = [
            f"📅 **𝒟𝒜𝒯𝐸-𝒲𝐼𝒮𝐸 𝒬𝒰𝐼𝒩 𝒮𝒰𝑀𝑀𝒜𝑅𝒴**",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"👤 **𝒮𝓉𝓊𝒹𝑒𝓃𝓉:** {u.get('full_name')} (`{u.get('student_id')}`)\n"
        ]

        if attempts:
            summary = {}
            for a in attempts:
                ad = dict(a)
                dt = ad.get("attempt_date", "𝒰𝓃𝓀𝓃𝑜𝓌𝓃")
                if dt not in summary:
                    summary[dt] = {"tests": 0, "qs": 0, "correct": 0, "score": 0.0}
                summary[dt]["tests"] += 1
                summary[dt]["qs"] += ad.get("questions_attempted", 0)
                summary[dt]["correct"] += ad.get("correct_answers", 0)
                summary[dt]["score"] += ad.get("score", 0.0)

            for dt, stats in summary.items():
                lines.append(
                    f"🗓 **𝒟𝒶𝓉𝑒:** `{dt}`\n"
                    f" • 𝒬𝓊𝒾𝓏𝓏𝑒𝓈: `{stats['tests']}` | 𝒬𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈: `{stats['qs']}`\n"
                    f" • 𝒞𝑜𝓇𝓇𝑒𝒸𝓉: `{stats['correct']}` | 𝒯𝑜𝓉𝒶𝓁 𝒮𝒸𝑜𝓇𝑒: `{stats['score']}`\n"
                )
        else:
            lines.append("*𝒩𝑜 𝓆𝓊𝒾𝓏 𝒶𝓉𝓉𝑒𝓂𝓅𝓉𝓈 𝒻𝑜𝓊𝓃𝒹 𝒻𝑜𝓇 𝓉𝒽𝒾𝓈 𝓈𝓉𝓊𝒹𝑒𝓃𝓉.*")

        msg = "\n".join(lines)
        keyboard = [[InlineKeyboardButton("🔙 𝐵𝒶𝒸𝓀 𝓉𝑜 𝒟𝒶𝓈𝒽𝒷𝑜𝒶𝓇𝒹", callback_data=f"admin_inspect_u_{target_uid}")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    # Audit Module 5: Attempted Questions
    elif data.startswith("audit_attempted_"):
        await query.answer()
        target_uid = int(data.replace("audit_attempted_", ""))
        u = get_user_profile(target_uid)

        conn = get_db()
        attempts = conn.execute("SELECT * FROM quiz_attempts WHERE user_id = ? ORDER BY id DESC LIMIT 5", (target_uid,)).fetchall()
        conn.close()

        lines = [
            f"🎯 **𝒜𝒯𝒯𝐸𝑀𝒫𝒯𝐸𝒟 𝒬𝒰𝐸𝒮𝒯𝐼𝒪𝒩𝒮 𝐿𝒪𝒢 (𝒪𝓃𝑒-𝐿𝒾𝓃𝑒𝓇 𝐹𝑜𝓇𝓂𝒶𝓉)**",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"👤 **𝒮𝓉𝓊𝒹𝑒𝓃𝓉:** {u.get('full_name')} (`{u.get('student_id')}`)\n"
        ]

        found_any = False
        for a in attempts:
            ad = dict(a)
            dt = ad.get("attempt_timestamp", "𝒩/𝒜")
            details = json.loads(ad["details_json"]) if ad.get("details_json") else []
            if details:
                found_any = True
                lines.append(f"📅 **𝒬𝓊𝒾𝓏 𝒜𝓉:** `{dt}`")
                for idx, q_item in enumerate(details, start=1):
                    q_text = q_item.get("question_text", "𝒩/𝒜")
                    ans_text = q_item.get("correct_answer_text", "𝒩/𝒜")
                    status_icon = "✅" if q_item.get("status") == "CORRECT" else "❌" if q_item.get("status") == "WRONG" else "⏭"
                    lines.append(f" {idx}. {status_icon} `{q_text}`\n    👉 **𝒜𝓃𝓈:** `{ans_text}`")
                lines.append("")

        if not found_any:
            lines.append("*𝒩𝑜 𝓆𝓊𝑒𝓈𝓉𝒾𝑜𝓃 𝒶𝓉𝓉𝑒𝓂𝓅𝓉 𝓁𝑜𝑔𝓈 𝓇𝑒𝒸𝑜𝓇𝒹𝑒𝒹 𝓎𝑒𝓉.*")

        msg = "\n".join(lines)
        if len(msg) > 4000:
            msg = msg[:3950] + "\n\n*(𝒯𝓇𝓊𝓃𝒸𝒶𝓉𝑒𝒹 𝒹𝓊𝑒 𝓉𝑜 𝒯𝑒𝓁𝑒𝑔𝓇𝒶𝓂 𝓁𝑒𝓃𝑔𝓉𝒽 𝓁𝒾𝓂𝒾𝓉)*"

        keyboard = [[InlineKeyboardButton("🔙 𝐵𝒶𝒸𝓀 𝓉𝑜 𝒟𝒶𝓈𝒽𝒷𝑜𝒶𝓇𝒹", callback_data=f"admin_inspect_u_{target_uid}")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    # Audit Module 6: Wrong Questions Log
    elif data.startswith("audit_wrong_"):
        await query.answer()
        target_uid = int(data.replace("audit_wrong_", ""))
        u = get_user_profile(target_uid)

        conn = get_db()
        attempts = conn.execute("SELECT * FROM quiz_attempts WHERE user_id = ? ORDER BY id DESC LIMIT 5", (target_uid,)).fetchall()
        conn.close()

        lines = [
            f"❌ **𝒲𝑅𝒪𝒩𝒢 𝒬𝒰𝐸𝒮𝒯𝐼𝒪𝒩𝒮 𝐿𝒪𝒢 (𝒪𝓃𝑒-𝐿𝒾𝓃𝑒𝓇 𝐹𝑜𝓇𝓂𝒶𝓉)**",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"👤 **𝒮𝓉𝓊𝒹𝑒𝓃𝓉:** {u.get('full_name')} (`{u.get('student_id')}`)\n"
        ]

        found_wrong = False
        for a in attempts:
            ad = dict(a)
            dt = ad.get("attempt_timestamp", "𝒩/𝒜")
            details = json.loads(ad["details_json"]) if ad.get("details_json") else []
            wrong_items = [q for q in details if q.get("status") == "WRONG"]
            if wrong_items:
                found_wrong = True
                lines.append(f"📅 **𝒬𝓊𝒾𝓏 𝒜𝓉:** `{dt}`")
                for idx, q_item in enumerate(wrong_items, start=1):
                    q_text = q_item.get("question_text", "𝒩/𝒜")
                    ans_text = q_item.get("correct_answer_text", "𝒩/𝒜")
                    lines.append(f" {idx}. ❌ `{q_text}`\n    👉 **𝒞𝑜𝓇𝓇𝑒𝒸ᴛ 𝒜𝓃𝓈:** `{ans_text}`")
                lines.append("")

        if not found_wrong:
            lines.append("🎉 *𝒵𝑒𝓇𝑜 𝓌𝓇𝑜𝓃𝑔 𝓆𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈 𝓁𝑜𝑔𝑔𝑒𝒹! 𝐸𝓍𝒸𝑒𝓁𝓁𝑒𝓃𝓉 𝓅𝑒𝓇𝒻𝑜𝓇𝓂𝒶𝓃𝒸𝑒.*")

        msg = "\n".join(lines)
        if len(msg) > 4000:
            msg = msg[:3950] + "\n\n*(𝒯𝓇𝓊𝓃𝒸𝒶𝓉𝑒𝒹 𝒹𝓊𝑒 𝓉𝑜 𝒯𝑒𝓁𝑒𝑔𝓇𝒶𝓂 𝓁𝑒𝓃𝑔𝓉𝒽 𝓁𝒾𝓂𝒾𝓉)*"

        keyboard = [[InlineKeyboardButton("🔙 𝐵𝒶𝒸𝓀 𝓉𝑜 𝒟𝒶𝓈𝒽𝒷𝑜𝒶𝓇𝒹", callback_data=f"admin_inspect_u_{target_uid}")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    # Audit Module 7: Saved Questions
    elif data.startswith("audit_saved_"):
        await query.answer()
        target_uid = int(data.replace("audit_saved_", ""))
        u = get_user_profile(target_uid)

        conn = get_db()
        saved = conn.execute("SELECT * FROM saved_questions WHERE user_id = ? ORDER BY id DESC", (target_uid,)).fetchall()
        conn.close()

        lines = [
            f"💾 **𝒮𝒜𝒱𝐸𝒟 𝒬𝒰𝐸𝒮𝒯𝐼𝒪𝒩𝒮 𝑅𝐸𝒫𝒪𝑅𝒯 (𝒪𝓃𝑒-𝐿𝒾𝓃𝑒𝓇 𝐹𝑜𝓇𝓂𝒶𝓉)**",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"👤 **𝒮𝓉𝓊𝒹𝑒𝓃𝓉:** {u.get('full_name')} (`{u.get('student_id')}`)",
            f"• **𝒯𝑜𝓉𝒶𝓁 𝐵𝑜𝑜𝓀𝓂𝒶𝓇𝓀𝓈:** `{len(saved)}`\n"
        ]

        if saved:
            for idx, sq in enumerate(saved, start=1):
                sq_d = dict(sq)
                opts_list = json.loads(sq_d['options_json']) if sq_d.get('options_json') else []
                c_opt_idx = sq_d.get("correct_option", 0)
                ans_text = opts_list[c_opt_idx] if 0 <= c_opt_idx < len(opts_list) else "𝒩/𝒜"
                s_at = sq_d.get("saved_at", "𝒩/𝒜")
                lines.append(f"**{idx}. [{s_at}]** 📌 `{sq_d['question_text']}`\n    👉 **𝒞𝑜𝓇𝓇𝑒𝒸ᴛ 𝒜𝓃𝓈:** `{ans_text}`")
        else:
            lines.append("*𝒩𝑜 𝓈𝒶𝓋𝑒𝒹 𝓆𝓊𝑒𝓈𝓉𝒾𝑜𝓃𝓈 𝒷𝑜𝑜𝓀𝓂𝒶𝓇𝓀𝑒𝒹 𝒷𝓎 𝓉𝒽ɪ𝓈 𝓈𝓉𝓊𝒹𝑒𝓃𝓉.*")

        msg = "\n".join(lines)
        if len(msg) > 4000:
            msg = msg[:3950] + "\n\n*(𝒯𝓇𝓊𝓃𝒸𝒶𝓉𝑒𝒹 𝒹𝓊𝑒 𝓉𝑜 𝒯𝑒𝓁𝑒𝑔𝓇𝒶𝓂 𝓁𝑒𝓃𝑔𝓉𝒽 𝓁𝒾𝓂𝒾𝓉)*"

        keyboard = [[InlineKeyboardButton("🔙 𝐵𝒶𝒸𝓀 𝓉𝑜 𝒟𝒶𝓈𝒽𝒷𝑜𝒶𝓇𝒹", callback_data=f"admin_inspect_u_{target_uid}")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    # Audit Module 8: Student Feedback
    elif data.startswith("audit_feedback_"):
        await query.answer()
        target_uid = int(data.replace("audit_feedback_", ""))
        u = get_user_profile(target_uid)

        conn = get_db()
        fbs = conn.execute("SELECT * FROM student_feedback WHERE user_id = ? ORDER BY id DESC", (target_uid,)).fetchall()
        conn.close()

        lines = [
            f"💬 **𝒮𝒯𝒰𝒟𝐸𝒩𝒯 𝐹𝐸𝐸𝒟𝐵𝒜𝒞𝒦 & 𝑅𝐸𝒱𝐼𝐸𝒲𝒮**",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"👤 **𝒮𝓉𝓊𝒹𝑒𝓃𝓉:** {u.get('full_name')} (`{u.get('student_id')}`)\n"
        ]

        if fbs:
            for idx, f_item in enumerate(fbs, start=1):
                fd = dict(f_item)
                lines.append(f"**{idx}. 𝒮𝓊𝒷𝓂𝒾𝓉𝓉𝑒𝒹 𝒜𝓉:** `{fd['submitted_at']}`\n 💬 *\"{fd['feedback_text']}\"*\n")
        else:
            lines.append("*𝒩𝑜 𝓇𝑒𝓋𝒾𝑒𝓌𝓈 𝑜𝓇 𝒻𝑒𝑒𝒹𝒷𝒶𝒸𝓀 𝓈𝓊𝒷𝓂𝒾𝓉𝓉𝑒𝒹 𝒷𝓎 𝓉𝒽𝒾𝓈 𝓈𝓉𝓊𝒹𝑒𝓃𝓉 𝓎𝑒𝓉.*")

        msg = "\n".join(lines)
        keyboard = [[InlineKeyboardButton("🔙 𝐵𝒶𝒸𝓀 𝓉𝑜 𝒟𝒶𝓈𝒽𝒷𝑜𝒶𝓇𝒹", callback_data=f"admin_inspect_u_{target_uid}")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    # Audit Module 9: Grant Bonus Quota
    elif data.startswith("audit_grant_"):
        await query.answer()
        target_uid = int(data.replace("audit_grant_", ""))
        conn = get_db()
        conn.execute("UPDATE users SET bonus_quota = bonus_quota + 20 WHERE user_id = ?", (target_uid,))
        conn.commit()
        conn.close()
        sync_user_json_profile(target_uid)

        await query.edit_message_text(
            f"🎉 **𝐵𝑜𝓃𝓊𝓈 𝒬𝓊𝑜𝓉𝒶 𝒢𝓇𝒶𝓃𝓉𝑒𝒹!**\n\n𝒜𝒹𝒹𝑒𝒹 +20 𝒹𝒶𝒾𝓁𝓎 𝓆𝓊𝑒𝓈𝓉𝒾𝑜𝓃 𝓆𝓊𝑜𝓉𝒶 𝓉𝑜 𝓊𝓈𝑒𝓇 `{target_uid}`.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 𝐵𝒶𝒸𝓀 𝓉𝑜 𝒟𝒶𝓈𝒽𝒷𝑜𝒶𝓇𝒹", callback_data=f"admin_inspect_u_{target_uid}")]])
        )

    # Audit Module 10: Export Single JSON File
    elif data.startswith("audit_exportjson_"):
        await query.answer()
        target_uid = int(data.replace("audit_exportjson_", ""))
        u = get_user_profile(target_uid)
        sid = u.get("student_id") or f"USER_{u.get('user_id')}"
        
        sync_user_json_profile(target_uid)
        filepath = os.path.join(USER_PROFILES_DIR, f"{sid}.json")

        if os.path.exists(filepath):
            with open(filepath, "rb") as doc:
                await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=doc,
                    filename=f"{sid}.json",
                    caption=f"📄 **𝑀𝒶𝓈𝓉𝑒𝓇 𝒮𝓉𝓊𝒹𝑒𝓃𝓉 𝒫𝓇𝑜𝒻𝒾𝓁𝑒 𝐹𝒾𝓁𝑒:** `{sid}.json`"
                )
        else:
            await query.message.reply_text("⚠️ 𝒥𝒮𝒪𝒩 𝒻𝒾𝓁𝑒 𝓃𝑜𝓉 𝒻𝑜𝓊𝓃𝒹 𝑜𝓃 𝒹𝒾𝓈𝓀.")

    # Broadcast
    elif data == "admin_broadcast":
        await query.answer()
        context.user_data["awaiting_broadcast"] = True
        await query.edit_message_text("📢 𝒮𝑒𝓃𝒹 𝓉𝒽𝑒 𝓂𝑒𝓈𝓈𝒶𝑔𝑒 𝓉𝑒𝓍𝓉 𝓎𝑜𝓊 𝓌𝒾𝓈𝒽 𝓉𝑜 𝒷𝓇𝑜𝒶𝒹𝒸𝒶𝓈𝓉 𝓉𝑜 𝒶𝓁𝓁 𝓇𝑒𝑔𝒾𝓈𝓉𝑒𝓇𝑒𝒹 𝓊𝓈𝑒𝓇𝓈:")