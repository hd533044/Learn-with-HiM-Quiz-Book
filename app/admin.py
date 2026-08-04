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
    m_status = "🟢 ᴀᴄᴛɪᴠᴇ (ᴏɴʟɪɴᴇ)" if now_ts >= m_until else "🔴 ᴘᴀᴜꜱᴇᴅ (ᴍᴀɪɴᴛᴇɴᴀɴᴄᴇ ᴍᴏᴅᴇ)"

    keyboard = [
        [InlineKeyboardButton("👥 ʙʀᴏᴡꜱᴇ ꜱᴛᴜᴅᴇɴᴛ ᴅɪʀᴇᴄᴛᴏʀʏ (/user_profiles)", callback_data="admin_users_page_0")],
        [InlineKeyboardButton("🔍 ꜱᴇᴀʀᴄʜ ꜱᴛᴜᴅᴇɴᴛ (ɪᴅ/ᴘʜᴏɴᴇ/ɴᴀᴍᴇ)", callback_data="admin_search_prompt")],
        [InlineKeyboardButton("📦 ᴇxᴘᴏʀᴛ ᴀʟʟ ʟᴇᴅɢᴇʀꜱ (.zip)", callback_data="admin_export_zip")],
        [InlineKeyboardButton("⏸ ᴘᴀᴜꜱᴇ ʙᴏᴛ 5 ᴍɪɴꜱ", callback_data="admin_pause_5"), InlineKeyboardButton("⏸ ᴘᴀᴜꜱᴇ ʙᴏᴛ 10 ᴍɪɴꜱ", callback_data="admin_pause_10")],
        [InlineKeyboardButton("▶️ ʀᴇꜱᴜᴍᴇ ʙᴏᴛ ɴᴏᴡ", callback_data="admin_resume_now")],
        [InlineKeyboardButton("📢 ɢʟᴏʙᴀʟ ʙʀᴏᴀᴅᴄᴀꜱᴛ", callback_data="admin_broadcast")]
    ]

    msg = (
        f"👑 **ᴍᴀꜱᴛᴇʀ ᴀᴅᴍɪɴ ᴘᴏʀᴛᴀʟ — ʜɪᴍᴀɴꜱʜᴜ ꜱɪʀ**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 **ᴛᴏᴛᴀʟ ʀᴇɢɪꜱᴛᴇʀᴇᴅ ꜱᴛᴜᴅᴇɴᴛꜱ:** `{len(users)}`\n"
        f"⚡ **ʙᴏᴛ ꜱʏꜱᴛᴇᴍ ꜱᴛᴀᴛᴜꜱ:** `{m_status}`\n\n"
        f"ꜱᴇʟᴇᴄᴛ ᴀɴ ᴀᴅᴍɪɴɪꜱᴛʀᴀᴛɪᴠᴇ ᴀᴄᴛɪᴏɴ ʙᴇʟᴏᴡ:"
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
        await query.edit_message_text("⏳ **ɢᴇɴᴇʀᴀᴛɪɴɢ ʙᴜʟᴋ ᴢɪᴘ ᴘᴀᴄᴋᴀɢᴇ...**\nᴢɪᴘᴘɪɴɢ ᴀʟʟ ꜱᴛᴜᴅᴇɴᴛ JSON ʟᴇᴅɢᴇʀꜱ...")
        
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
                        caption=f"📦 **ᴍᴀꜱᴛᴇʀ ꜱᴛᴜᴅᴇɴᴛ ᴘʀᴏꜰɪʟᴇꜱ ʙᴀᴄᴋᴜᴘ**\n\nᴛᴏᴛᴀʟ ꜰɪʟᴇꜱ ɪɴᴄʟᴜᴅᴇᴅ: `{len(users)} JSON ᴘʀᴏꜰɪʟᴇꜱ`"
                    )
                os.remove(zip_path)
            await admin_portal_command(update, context)
        except Exception as e:
            logger.error(f"Error zipping profiles: {e}")
            await query.message.reply_text(f"⚠️ Error creating zip archive: {e}")

    # Pause 5 Mins
    elif data == "admin_pause_5":
        await query.answer()
        set_maintenance_until(int(time.time()) + 300)
        await query.edit_message_text("🛑 **ʙᴏᴛ ꜱᴇʀᴠɪᴄᴇ ᴘᴀᴜꜱᴇᴅ ꜰᴏʀ 5 ᴍɪɴᴜᴛᴇꜱ.**\nʙʀᴏᴀᴅᴄᴀꜱᴛɪɴɢ ɴᴏᴛɪᴄᴇ ᴛᴏ ᴀʟʟ ᴜꜱᴇʀꜱ...")
        for u in users:
            try:
                await context.bot.send_message(
                    chat_id=u['user_id'], 
                    text="📢 **ᴀᴅᴍɪɴ ʜᴀꜱ ᴘᴀᴜꜱᴇᴅ ᴛʜᴇ ꜱᴇʀᴠɪᴄᴇ ꜰᴏʀ 5 ᴍɪɴꜱ**\n\n⏰ ꜱᴇʀᴠɪᴄᴇꜱ ᴡɪʟʟ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ ʀᴇꜱᴜᴍᴇ ɪɴ 5 ᴍɪɴᴜᴛᴇꜱ."
                )
            except Exception:
                pass

    # Pause 10 Mins
    elif data == "admin_pause_10":
        await query.answer()
        set_maintenance_until(int(time.time()) + 600)
        await query.edit_message_text("🛑 **ʙᴏᴛ ꜱᴇʀᴠɪᴄᴇ ᴘᴀᴜꜱᴇᴅ ꜰᴏʀ 10 ᴍɪɴᴜᴛᴇꜱ.**\nʙʀᴏᴀᴅᴄᴀꜱᴛɪɴɢ ɴᴏᴛɪᴄᴇ ᴛᴏ ᴀʟʟ ᴜꜱᴇʀꜱ...")
        for u in users:
            try:
                await context.bot.send_message(
                    chat_id=u['user_id'], 
                    text="📢 **ᴀᴅᴍɪɴ ʜᴀꜱ ᴘᴀᴜꜱᴇᴅ ᴛʜᴇ ꜱᴇʀᴠɪᴄᴇ ꜰᴏʀ 10 ᴍɪɴꜱ**\n\n⏰ ꜱᴇʀᴠɪᴄᴇꜱ ᴡɪʟʟ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ ʀᴇꜱᴜᴍᴇ ɪɴ 10 ᴍɪɴᴜᴛᴇꜱ."
                )
            except Exception:
                pass

    # Resume Bot
    elif data == "admin_resume_now":
        await query.answer()
        set_maintenance_until(0)
        await query.edit_message_text("🟢 **ʙᴏᴛ ꜱᴇʀᴠɪᴄᴇ ʀᴇꜱᴜᴍᴇᴅ ɪᴍᴍᴇᴅɪᴀᴛᴇʟʏ.**\nʙʀᴏᴀᴅᴄᴀꜱᴛɪɴɢ ɴᴏᴛɪᴄᴇ ᴛᴏ ᴀʟʟ ᴜꜱᴇʀꜱ...")
        for u in users:
            try:
                await context.bot.send_message(
                    chat_id=u['user_id'], 
                    text="📢 **ᴀᴅᴍɪɴ ʜᴀꜱ ʀᴇꜱᴜᴍᴇᴅ ᴛʜᴇ ꜱᴇʀᴠɪᴄᴇꜱ, ɴᴏᴡ ʏᴏᴜ ᴄᴀɴ ᴀᴛᴛᴇᴍᴘᴛ !!**"
                )
            except Exception:
                pass

    # Search Student Prompt
    elif data == "admin_search_prompt":
        await query.answer()
        context.user_data["awaiting_admin_search"] = True
        await query.edit_message_text("🔍 **ꜱᴛᴜᴅᴇɴᴛ ꜱᴇᴀʀᴄʜ ᴇɴɢɪɴᴇ**\n\nᴘʟᴇᴀꜱᴇ ʀᴇᴘʟʏ ᴡɪᴛʜ ᴛʜᴇ ꜱᴛᴜᴅᴇɴᴛ'ꜱ **ꜱᴛᴜᴅᴇɴᴛ ɪᴅ**, **ᴘʜᴏɴᴇ ɴᴜᴍʙᴇʀ**, ᴏʀ **ꜰᴜʟʟ ɴᴀᴍᴇ**:")

    # Paginated Student Directory
    elif data.startswith("admin_users_page_"):
        await query.answer()
        page = int(data.replace("admin_users_page_", ""))
        total_users = len(users)

        if total_users == 0:
            await query.edit_message_text("📁 No registered students found in database.")
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
            btn_text = f"👤 {u['full_name']}{ban_flag} (ɪᴅ: {sid})"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"admin_inspect_u_{u['user_id']}")])

        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️ ᴘʀᴇᴠ", callback_data=f"admin_users_page_{page - 1}"))
        nav_row.append(InlineKeyboardButton(f"📄 ᴘᴀɢᴇ {page + 1}/{total_pages}", callback_data="ignore"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("ɴᴇxᴛ ▶️", callback_data=f"admin_users_page_{page + 1}"))
        
        keyboard.append(nav_row)
        keyboard.append([InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴀᴅᴍɪɴ ᴘᴏʀᴛᴀʟ", callback_data="admin_home")])

        msg = (
            f"👥 **ꜱᴛᴜᴅᴇɴᴛ ᴅɪʀᴇᴄᴛᴏʀʏ ʟᴇᴅɢᴇʀ**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **ᴛᴏᴛᴀʟ ꜱᴛᴜᴅᴇɴᴛꜱ:** `{total_users}`\n"
            f"• **ᴘᴀɢᴇ:** `{page + 1}` ᴏꜰ `{total_pages}`\n\n"
            f"ᴛᴀᴘ ᴀɴʏ ꜱᴛᴜᴅᴇɴᴛ ʙᴇʟᴏᴡ ᴛᴏ ᴀᴄᴄᴇꜱꜱ ᴛʜᴇɪʀ ꜰᴜʟʟ ɪɴꜱᴘᴇᴄᴛɪᴏɴ ᴅᴀꜱʜʙᴏᴀʀᴅ:"
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
            await query.edit_message_text("⚠️ Student profile not found.")
            return

        sid = u.get("student_id") or f"USER_{u.get('user_id')}"
        is_banned = u.get("is_banned", 0)
        ban_text = "🟢 ᴀᴄᴛɪᴠᴇ" if not is_banned else "🔴 ʙᴀɴɴᴇᴅ"

        keyboard = [
            [InlineKeyboardButton("📋 ᴘᴇʀꜱᴏɴᴀʟ ᴅᴇᴛᴀɪʟꜱ", callback_data=f"audit_personal_{target_uid}"), InlineKeyboardButton("🔑 ᴜꜱᴇʀ ᴘɪɴ & ꜱᴇᴄᴜʀɪᴛʏ qᴜᴇꜱᴛɪᴏɴꜱ", callback_data=f"audit_pinsec_{target_uid}")],
            [InlineKeyboardButton("⏱ ᴛɪᴍᴇ & ᴀᴄᴛɪᴠɪᴛʏ ʟᴏɢ", callback_data=f"audit_activity_{target_uid}"), InlineKeyboardButton("📊 ᴏᴠᴇʀᴀʟʟ ᴘᴇʀꜰᴏʀᴍᴀɴᴄᴇ", callback_data=f"audit_perf_{target_uid}")],
            [InlineKeyboardButton("📅 ᴅᴀᴛᴇ-ᴡɪꜱᴇ qᴜɪᴢ ꜱᴜᴍᴍᴀʀʏ", callback_data=f"audit_datesummary_{target_uid}"), InlineKeyboardButton("🎯 ᴀᴛᴛᴇᴍᴘᴛᴇᴅ qᴜᴇꜱᴛɪᴏɴꜱ", callback_data=f"audit_attempted_{target_uid}")],
            [InlineKeyboardButton("❌ ᴡʀᴏɴɢ qᴜᴇꜱᴛɪᴏɴꜱ", callback_data=f"audit_wrong_{target_uid}"), InlineKeyboardButton("💾 ꜱᴀᴠᴇᴅ qᴜᴇꜱᴛɪᴏɴꜱ", callback_data=f"audit_saved_{target_uid}")],
            [InlineKeyboardButton("💬 ꜱᴛᴜᴅᴇɴᴛ ꜰᴇᴇᴅʙᴀᴄᴋ", callback_data=f"audit_feedback_{target_uid}"), InlineKeyboardButton("🎁 ɢʀᴀɴᴛ +20 ʙᴏɴᴜꜱ qᴜᴏᴛᴀ", callback_data=f"audit_grant_{target_uid}")],
            [InlineKeyboardButton("📄 ᴇxᴘᴏʀᴛ PDF ʀᴇᴘᴏʀᴛꜱ ᴏᴘᴛɪᴏɴꜱ", callback_data=f"audit_pdfmenu_{target_uid}"), InlineKeyboardButton("📥 ᴇxᴘᴏʀᴛ ʀᴀᴡ JSON ꜰɪʟᴇ", callback_data=f"audit_exportjson_{target_uid}")],
            [InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ꜱᴛᴜᴅᴇɴᴛ ᴅɪʀᴇᴄᴛᴏʀʏ", callback_data="admin_users_page_0")]
        ]

        msg = (
            f"🪪 **ꜱᴛᴜᴅᴇɴᴛ ᴀᴜᴅɪᴛ ᴄᴏɴᴛʀᴏʟ ᴘᴀɴᴇʟ**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **ꜱᴛᴜᴅᴇɴᴛ ɴᴀᴍᴇ:** {u.get('full_name')}\n"
            f"• **ꜱᴛᴜᴅᴇɴᴛ ɪᴅ:** `{sid}`\n"
            f"• **ᴛᴇʟᴇɢʀᴀᴍ ɪᴅ:** `{u.get('user_id')}`\n"
            f"• **ᴛᴀʀɢᴇᴛ ᴇxᴀᴍ:** `{u.get('target_exam')}`\n"
            f"• **ᴀᴄᴄᴏᴜɴᴛ ꜱᴛᴀᴛᴜꜱ:** `{ban_text}`\n"
            f"• **ꜰɪʟᴇ ʟᴇᴅɢᴇʀ:** `data/user_profiles/{sid}.json`\n\n"
            f"ꜱᴇʟᴇᴄᴛ ᴀɴ ᴀᴜᴅɪᴛ ᴍᴏᴅᴜʟᴇ ʙᴇʟᴏᴡ ᴛᴏ ᴠɪᴇᴡ ᴅᴇᴛᴀɪʟᴇᴅ ʀᴇᴘᴏʀᴛꜱ:"
        )
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    # User PIN & Security Questions Audit
    elif data.startswith("audit_pinsec_"):
        await query.answer()
        target_uid = int(data.replace("audit_pinsec_", ""))
        u = get_user_profile(target_uid)

        msg = (
            f"🔑 **ᴜꜱᴇʀ ᴘɪɴ & ꜱᴇᴄᴜʀɪᴛʏ qᴜᴇꜱᴛɪᴏɴꜱ (ᴀᴅᴍɪɴ ᴀᴜᴅɪᴛ)**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 **ꜱᴛᴜᴅᴇɴᴛ:** {u.get('full_name')} (`{u.get('student_id')}`)\n\n"
            f"• **ꜱᴇᴄʀᴇᴛ 4-ᴅɪɢɪᴛ ᴘɪɴ:** `{u.get('pin', 'ɴᴏᴛ ꜱᴇᴛ')}`\n"
            f"• **ꜱᴇᴄᴜʀɪᴛʏ qᴜᴇꜱᴛɪᴏɴ:** *\"{u.get('security_question', 'ɴᴏᴛ ꜱᴇᴛ')}\"*\n"
            f"• **ꜱᴇᴄᴜʀɪᴛʏ ᴀɴꜱᴡᴇʀ:** `{u.get('security_answer', 'ɴᴏᴛ ꜱᴇᴛ')}`\n\n"
            f"⚠️ *ᴄᴏɴꜰɪᴅᴇɴᴛɪᴀʟ: ᴠɪꜱɪʙʟᴇ ꜱᴛʀɪᴄᴛʟʏ ᴛᴏ ᴘʀɪᴍᴀʀʏ ᴀᴅᴍɪɴ.*"
        )
        keyboard = [[InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴅᴀꜱʜʙᴏᴀʀᴅ", callback_data=f"admin_inspect_u_{target_uid}")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    # Reorganized PDF Menu Options
    elif data.startswith("audit_pdfmenu_"):
        await query.answer()
        target_uid = int(data.replace("audit_pdfmenu_", ""))
        
        pdf_buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 1. ʟᴀꜱᴛ 1 ᴍᴏɴᴛʜ ꜰᴜʟʟ ᴅᴀᴛᴀ ʀᴇᴘᴏʀᴛ", callback_data=f"genpdf_{target_uid}_last_1_month_data")],
            [InlineKeyboardButton("📊 2. ʟᴀꜱᴛ 1 ᴍᴏɴᴛʜ qᴜɪᴢ ꜱᴜᴍᴍᴀʀʏ ʀᴇᴘᴏʀᴛ (ɴᴏ qꜱ)", callback_data=f"genpdf_{target_uid}_last_1_month_quiz")],
            [InlineKeyboardButton("📜 3. ᴀʟʟ ᴍᴏɴᴛʜꜱ ꜰᴜʟʟ ᴅᴀᴛᴀ ʀᴇᴘᴏʀᴛ", callback_data=f"genpdf_{target_uid}_all_months_data")],
            [InlineKeyboardButton("📈 4. ᴀʟʟ ᴍᴏɴᴛʜꜱ qᴜɪᴢ ꜱᴜᴍᴍᴀʀʏ ʀᴇᴘᴏʀᴛ (ɴᴏ qꜱ)", callback_data=f"genpdf_{target_uid}_all_months_quiz")],
            [InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴅᴀꜱʜʙᴏᴀʀᴅ", callback_data=f"admin_inspect_u_{target_uid}")]
        ])

        await query.edit_message_text(
            f"📄 **PDF ʀᴇᴘᴏʀᴛ ᴄᴀʀᴅ ɢᴇɴᴇʀᴀᴛᴏʀ**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"ꜱᴇʟᴇᴄᴛ ᴛʜᴇ ᴇxᴀᴄᴛ ʀᴇᴘᴏʀᴛ ᴛʏᴘᴇ ᴛᴏ ɢᴇɴᴇʀᴀᴛᴇ:",
            reply_markup=pdf_buttons,
            parse_mode="Markdown"
        )

    # Generate and Send PDF Report
    elif data.startswith("genpdf_"):
        await query.answer()
        parts = data.split("_")
        target_uid = int(parts[1])
        filter_mode = "_".join(parts[2:])

        await query.edit_message_text("⏳ **ɢᴇɴᴇʀᴀᴛɪɴɢ ᴄᴜꜱᴛᴏᴍ PDF ʀᴇᴘᴏʀᴛ ᴄᴀʀᴅ...**\nʙᴜɪʟᴅɪɴɢ ꜱᴛᴀᴛꜱ, ꜰᴏʀᴍᴀᴛᴛɪɴɢ ᴛᴀʙʟᴇꜱ, ᴀɴᴅ ʀᴇɴᴅᴇʀɪɴɢ PDF...")
        
        pdf_file = generate_student_pdf_report(target_uid, filter_mode)
        u = get_user_profile(target_uid)
        sid = u.get("student_id") or f"USER_{target_uid}"

        if pdf_file and os.path.exists(pdf_file):
            with open(pdf_file, "rb") as doc:
                await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=doc,
                    filename=os.path.basename(pdf_file),
                    caption=(
                        f"📄 **ᴏꜰꜰɪᴄɪᴀʟ ꜱᴛᴜᴅᴇɴᴛ PDF ᴀᴄᴀᴅᴇᴍɪᴄ ʀᴇᴘᴏʀᴛ**\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"👤 **ꜱᴛᴜᴅᴇɴᴛ:** {u.get('full_name')}\n"
                        f"🪪 **ꜱᴛᴜᴅᴇɴᴛ ɪᴅ:** `{sid}`\n"
                        f"📊 **ʀᴇᴘᴏʀᴛ ᴍᴏᴅᴜʟᴇ:** `{filter_mode.replace('_', ' ').title()}`\n"
                        f"🏷 **ᴡᴀᴛᴇʀᴍᴀʀᴋ:** `@LearnwithHiM`"
                    )
                )
            
            nav_buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("📄 ᴇxᴘᴏʀᴛ ᴀɴᴏᴛʜᴇʀ PDF ʀᴇᴘᴏʀᴛ", callback_data=f"audit_pdfmenu_{target_uid}")],
                [InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ꜱᴛᴜᴅᴇɴᴛ ᴅᴀꜱʜʙᴏᴀʀᴅ", callback_data=f"admin_inspect_u_{target_uid}")],
                [InlineKeyboardButton("👑 ᴍᴀɪɴ ᴀᴅᴍɪɴ ᴘᴏʀᴛᴀʟ", callback_data="admin_home")]
            ])
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="👇 **qᴜɪᴄᴋ ᴀᴄᴛɪᴏɴꜱ & ɴᴀᴠɪɢᴀᴛɪᴏɴ:**",
                reply_markup=nav_buttons
            )
        else:
            await query.message.reply_text("⚠️ Failed to generate PDF file.")

    # Audit Module 1: Personal Details
    elif data.startswith("audit_personal_"):
        await query.answer()
        target_uid = int(data.replace("audit_personal_", ""))
        u = get_user_profile(target_uid)
        
        if not u:
            await query.edit_message_text("⚠️ Error retrieving user profile.")
            return

        sid = u.get("student_id") or f"USER_{u.get('user_id')}"
        ban_status = "ʙᴀɴɴᴇᴅ 🔴" if u.get("is_banned") else "ᴀᴄᴛɪᴠᴇ 🟢"

        msg = (
            f"📋 **ꜱᴛᴜᴅᴇɴᴛ ᴘᴇʀꜱᴏɴᴀʟ ᴅᴇᴛᴀɪʟꜱ**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **ꜰᴜʟʟ ɴᴀᴍᴇ:** {u.get('full_name', 'ɴ/ᴀ')}\n"
            f"• **ꜱᴛᴜᴅᴇɴᴛ ɪᴅ:** `{sid}`\n"
            f"• **ᴛᴇʟᴇɢʀᴀᴍ ɪᴅ:** `{u.get('user_id')}`\n"
            f"• **ᴀᴄᴄᴏᴜɴᴛ ꜱᴛᴀᴛᴜꜱ:** `{ban_status}`\n"
            f"• **ᴜꜱᴇʀɴᴀᴍᴇ:** @{u.get('username') or 'ɴ/ᴀ'}\n"
            f"• **ᴘʜᴏɴᴇ ɴᴜᴍʙᴇʀ:** `{u.get('phone_number') or 'ɴ/ᴀ'}`\n"
            f"• **ᴛᴀʀɢᴇᴛ ᴇxᴀᴍ:** `{u.get('target_exam', 'ɴ/ᴀ')}`\n"
            f"• **ᴅᴀᴛᴇ ᴏꜰ ʙɪʀᴛʜ:** `{u.get('dob', 'ɴ/ᴀ')}`\n"
            f"• **ᴄᴀʟᴄᴜʟᴀᴛᴇᴅ ᴀɢᴇ:** `{u.get('age', 'ɴ/ᴀ')} ʏʀꜱ`\n"
            f"• **ɢᴇɴᴅᴇʀ:** `{u.get('gender', 'ɴ/ᴀ')}`\n"
            f"• **ʟᴏᴄᴀᴛɪᴏɴ:** `{u.get('state', 'ɴ/ᴀ')}, {u.get('country', 'ɪɴᴅɪᴀ')}`\n"
            f"• **ʙᴏɴᴜꜱ qᴜᴏᴛᴀ:** `{u.get('bonus_quota', 0)} qꜱ`\n"
            f"• **ʀᴇɢɪꜱᴛᴇʀᴇᴅ ᴀᴛ:** `{u.get('created_at', 'ɴ/ᴀ')}`\n"
            f"• **ʟᴀꜱᴛ ᴀᴄᴛɪᴠᴇ:** `{u.get('last_active', 'ɴ/ᴀ')}`\n"
            f"• **ʀᴇꜰᴇʀʀᴇᴅ ʙʏ ɪᴅ:** `{u.get('referred_by') or 'ɴᴏɴᴇ'}`\n"
            f"• **ʀᴇꜰᴇʀʀᴀʟ ᴄᴏᴜɴᴛ:** `{u.get('referral_count', 0)}` ꜰʀɪᴇɴᴅꜱ"
        )
        keyboard = [[InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴅᴀꜱʜʙᴏᴀʀᴅ", callback_data=f"admin_inspect_u_{target_uid}")]]
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
            f"⏱ **ꜱᴛᴜᴅᴇɴᴛ ᴀᴄᴛɪᴠɪᴛʏ & ᴛɪᴍᴇ ʟᴏɢ**",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"👤 **ꜱᴛᴜᴅᴇɴᴛ:** {u.get('full_name')} (`{u.get('student_id')}`)",
            f"• **ʟᴀꜱᴛ ʟᴏɢɪɴ / ᴀᴄᴛɪᴠᴇ:** `{u.get('last_active', 'ɴ/ᴀ')}`",
            f"• **ᴛᴏᴛᴀʟ ᴛɪᴍᴇ ꜱᴘᴇɴᴛ ᴏᴠᴇʀᴀʟʟ:** `{total_sec} ꜱᴇᴄ` ({round(total_sec/60, 2)} ᴍɪɴꜱ)\n",
            f"📅 **ᴅᴀᴛᴇ-ᴡɪꜱᴇ ᴛɪᴍᴇ ꜱᴘᴇɴᴛ ʙʀᴇᴀᴋᴅᴏᴡɴ:**"
        ]

        if rows:
            for r in rows:
                mins = round(r['seconds_spent'] / 60, 2)
                lines.append(f" • `{r['date_str']}`: {r['seconds_spent']}ꜱ ({mins} ᴍɪɴꜱ)")
        else:
            lines.append(" • *ɴᴏ ᴀᴄᴛɪᴠɪᴛʏ ᴛɪᴍᴇ ʀᴇᴄᴏʀᴅᴇᴅ ʏᴇᴛ.*")

        msg = "\n".join(lines)
        keyboard = [[InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴅᴀꜱʜʙᴏᴀʀᴅ", callback_data=f"admin_inspect_u_{target_uid}")]]
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
            f"📊 **ꜱᴛᴜᴅᴇɴᴛ ᴏᴠᴇʀᴀʟʟ ᴘᴇʀꜰᴏʀᴍᴀɴᴄᴇ**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 **ꜱᴛᴜᴅᴇɴᴛ:** {u.get('full_name')} (`{u.get('student_id')}`)\n\n"
            f"• **ᴛᴏᴛᴀʟ ᴛᴇꜱᴛꜱ ᴄᴏᴍᴘʟᴇᴛᴇᴅ:** `{perf.get('total_tests', 0)}`\n"
            f"• **ᴛᴏᴛᴀʟ qᴜᴇꜱᴛɪᴏɴꜱ ᴀᴛᴛᴇᴍᴘᴛᴇᴅ:** `{total_qs}`\n"
            f"• **ᴄᴏʀʀᴇᴄᴛ ᴀɴꜱᴡᴇʀꜱ:** `{total_correct}` ✅\n"
            f"• **ᴡʀᴏɴɢ ᴀɴꜱᴡᴇʀꜱ:** `{perf.get('total_wrong', 0)}` ❌\n"
            f"• **ꜱᴋɪᴘᴘᴇᴅ qᴜᴇꜱᴛɪᴏɴꜱ:** `{perf.get('total_skipped', 0)}` ⏭\n"
            f"• **ᴀᴄᴄᴜʀᴀᴄʏ ʀᴀᴛɪɴɢ:** `{acc}%`\n"
            f"• **ᴀᴠᴇʀᴀɢᴇ ꜱᴄᴏʀᴇ:** `{round(perf.get('avg_score', 0.0) or 0.0, 2)}`\n"
            f"• **ɢʟᴏʙᴀʟ ʀᴀɴᴋ:** `{rank}`\n"
            f"• **ᴏᴠᴇʀᴀʟʟ ᴘᴇʀᴄᴇɴᴛɪʟᴇ:** `{percentile}%`"
        )
        keyboard = [[InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴅᴀꜱʜʙᴏᴀʀᴅ", callback_data=f"admin_inspect_u_{target_uid}")]]
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
            f"📅 **ᴅᴀᴛᴇ-ᴡɪꜱᴇ qᴜɪᴢ ꜱᴜᴍᴍᴀʀʏ**",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"👤 **ꜱᴛᴜᴅᴇɴᴛ:** {u.get('full_name')} (`{u.get('student_id')}`)\n"
        ]

        if attempts:
            summary = {}
            for a in attempts:
                ad = dict(a)
                dt = ad.get("attempt_date", "ᴜɴᴋɴᴏᴡɴ")
                if dt not in summary:
                    summary[dt] = {"tests": 0, "qs": 0, "correct": 0, "score": 0.0}
                summary[dt]["tests"] += 1
                summary[dt]["qs"] += ad.get("questions_attempted", 0)
                summary[dt]["correct"] += ad.get("correct_answers", 0)
                summary[dt]["score"] += ad.get("score", 0.0)

            for dt, stats in summary.items():
                lines.append(
                    f"🗓 **ᴅᴀᴛᴇ:** `{dt}`\n"
                    f" • qᴜɪᴢᴢᴇꜱ: `{stats['tests']}` | qᴜᴇꜱᴛɪᴏɴꜱ: `{stats['qs']}`\n"
                    f" • ᴄᴏʀʀᴇᴄᴛ: `{stats['correct']}` | ᴛᴏᴛᴀʟ ꜱᴄᴏʀᴇ: `{stats['score']}`\n"
                )
        else:
            lines.append("*ɴᴏ qᴜɪᴢ ᴀᴛᴛᴇᴍᴘᴛꜱ ꜰᴏᴜɴᴅ ꜰᴏʀ ᴛʜɪꜱ ꜱᴛᴜᴅᴇɴᴛ.*")

        msg = "\n".join(lines)
        keyboard = [[InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴅᴀꜱʜʙᴏᴀʀᴅ", callback_data=f"admin_inspect_u_{target_uid}")]]
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
            f"🎯 **ᴀᴛᴛᴇᴍᴘᴛᴇᴅ qᴜᴇꜱᴛɪᴏɴꜱ ʟᴏɢ (ᴏɴᴇ-ʟɪɴᴇʀ ꜰᴏʀᴍᴀᴛ)**",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"👤 **ꜱᴛᴜᴅᴇɴᴛ:** {u.get('full_name')} (`{u.get('student_id')}`)\n"
        ]

        found_any = False
        for a in attempts:
            ad = dict(a)
            dt = ad.get("attempt_timestamp", "ɴ/ᴀ")
            details = json.loads(ad["details_json"]) if ad.get("details_json") else []
            if details:
                found_any = True
                lines.append(f"📅 **qᴜɪᴢ ᴀᴛ:** `{dt}`")
                for idx, q_item in enumerate(details, start=1):
                    q_text = q_item.get("question_text", "ɴ/ᴀ")
                    ans_text = q_item.get("correct_answer_text", "ɴ/ᴀ")
                    status_icon = "✅" if q_item.get("status") == "CORRECT" else "❌" if q_item.get("status") == "WRONG" else "⏭"
                    lines.append(f" {idx}. {status_icon} `{q_text}`\n    👉 **ᴀɴꜱ:** `{ans_text}`")
                lines.append("")

        if not found_any:
            lines.append("*ɴᴏ qᴜᴇꜱᴛɪᴏɴ ᴀᴛᴛᴇᴍᴘᴛ ʟᴏɢꜱ ʀᴇᴄᴏʀᴅᴇᴅ ʏᴇᴛ.*")

        msg = "\n".join(lines)
        if len(msg) > 4000:
            msg = msg[:3950] + "\n\n*(ᴛʀᴜɴᴄᴀᴛᴇᴅ ᴅᴜᴇ ᴛᴏ ᴛᴇʟᴇɢʀᴀᴍ ʟᴇɴɢᴛʜ ʟɪᴍɪᴛ)*"

        keyboard = [[InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴅᴀꜱʜʙᴏᴀʀᴅ", callback_data=f"admin_inspect_u_{target_uid}")]]
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
            f"❌ **ᴡʀᴏɴɢ qᴜᴇꜱᴛɪᴏɴꜱ ʟᴏɢ (ᴏɴᴇ-ʟɪɴᴇʀ ꜰᴏʀᴍᴀᴛ)**",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"👤 **ꜱᴛᴜᴅᴇɴᴛ:** {u.get('full_name')} (`{u.get('student_id')}`)\n"
        ]

        found_wrong = False
        for a in attempts:
            ad = dict(a)
            dt = ad.get("attempt_timestamp", "ɴ/ᴀ")
            details = json.loads(ad["details_json"]) if ad.get("details_json") else []
            wrong_items = [q for q in details if q.get("status") == "WRONG"]
            if wrong_items:
                found_wrong = True
                lines.append(f"📅 **qᴜɪᴢ ᴀᴛ:** `{dt}`")
                for idx, q_item in enumerate(wrong_items, start=1):
                    q_text = q_item.get("question_text", "ɴ/ᴀ")
                    ans_text = q_item.get("correct_answer_text", "ɴ/ᴀ")
                    lines.append(f" {idx}. ❌ `{q_text}`\n    👉 **ᴄᴏʀʀᴇᴄᴛ ᴀɴꜱ:** `{ans_text}`")
                lines.append("")

        if not found_wrong:
            lines.append("🎉 *ᴢᴇʀᴏ ᴡʀᴏɴɢ qᴜᴇꜱᴛɪᴏɴꜱ ʟᴏɢɢᴇᴅ! ᴇxᴄᴇʟʟᴇɴᴛ ᴘᴇʀꜰᴏʀᴍᴀɴᴄᴇ.*")

        msg = "\n".join(lines)
        if len(msg) > 4000:
            msg = msg[:3950] + "\n\n*(ᴛʀᴜɴᴄᴀᴛᴇᴅ ᴅᴜᴇ ᴛᴏ ᴛᴇʟᴇɢʀᴀᴍ ʟᴇɴɢᴛʜ ʟɪᴍɪᴛ)*"

        keyboard = [[InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴅᴀꜱʜʙᴏᴀʀᴅ", callback_data=f"admin_inspect_u_{target_uid}")]]
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
            f"💾 **ꜱᴀᴠᴇᴅ qᴜᴇꜱᴛɪᴏɴꜱ ʀᴇᴘᴏʀᴛ (ᴏɴᴇ-ʟɪɴᴇʀ ꜰᴏʀᴍᴀᴛ)**",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"👤 **ꜱᴛᴜᴅᴇɴᴛ:** {u.get('full_name')} (`{u.get('student_id')}`)",
            f"• **ᴛᴏᴛᴀʟ ʙᴏᴏᴋᴍᴀʀᴋꜱ:** `{len(saved)}`\n"
        ]

        if saved:
            for idx, sq in enumerate(saved, start=1):
                sq_d = dict(sq)
                opts_list = json.loads(sq_d['options_json']) if sq_d.get('options_json') else []
                c_opt_idx = sq_d.get("correct_option", 0)
                ans_text = opts_list[c_opt_idx] if 0 <= c_opt_idx < len(opts_list) else "ɴ/ᴀ"
                s_at = sq_d.get("saved_at", "ɴ/ᴀ")
                lines.append(f"**{idx}. [{s_at}]** 📌 `{sq_d['question_text']}`\n    👉 **ᴄᴏʀʀᴇᴄᴛ ᴀɴꜱ:** `{ans_text}`")
        else:
            lines.append("*ɴᴏ ꜱᴀᴠᴇᴅ qᴜᴇꜱᴛɪᴏɴꜱ ʙᴏᴏᴋᴍᴀʀᴋᴇᴅ ʙʏ ᴛʜɪꜱ ꜱᴛᴜᴅᴇɴᴛ.*")

        msg = "\n".join(lines)
        if len(msg) > 4000:
            msg = msg[:3950] + "\n\n*(ᴛʀᴜɴᴄᴀᴛᴇᴅ ᴅᴜᴇ ᴛᴏ ᴛᴇʟᴇɢʀᴀᴍ ʟᴇɴɢᴛʜ ʟɪᴍɪᴛ)*"

        keyboard = [[InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴅᴀꜱʜʙᴏᴀʀᴅ", callback_data=f"admin_inspect_u_{target_uid}")]]
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
            f"💬 **ꜱᴛᴜᴅᴇɴᴛ ꜰᴇᴇᴅʙᴀᴄᴋ & ʀᴇᴠɪᴇᴡꜱ**",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"👤 **ꜱᴛᴜᴅᴇɴᴛ:** {u.get('full_name')} (`{u.get('student_id')}`)\n"
        ]

        if fbs:
            for idx, f_item in enumerate(fbs, start=1):
                fd = dict(f_item)
                lines.append(f"**{idx}. ꜱᴜʙᴍɪᴛᴛᴇᴅ ᴀᴛ:** `{fd['submitted_at']}`\n 💬 *\"{fd['feedback_text']}\"*\n")
        else:
            lines.append("*ɴᴏ ʀᴇᴠɪᴇᴡꜱ ᴏʀ ꜰᴇᴇᴅʙᴀᴄᴋ ꜱᴜʙᴍɪᴛᴛᴇᴅ ʙʏ ᴛʜɪꜱ ꜱᴛᴜᴅᴇɴᴛ ʏᴇᴛ.*")

        msg = "\n".join(lines)
        keyboard = [[InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴅᴀꜱʜʙᴏᴀʀᴅ", callback_data=f"admin_inspect_u_{target_uid}")]]
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
            f"🎉 **ʙᴏɴᴜꜱ qᴜᴏᴛᴀ ɢʀᴀɴᴛᴇᴅ!**\n\nᴀᴅᴅᴇᴅ +20 ᴅᴀɪʟʏ qᴜᴇꜱᴛɪᴏɴ qᴜᴏᴛᴀ ᴛᴏ ᴜꜱᴇʀ `{target_uid}`.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴅᴀꜱʜʙᴏᴀʀᴅ", callback_data=f"admin_inspect_u_{target_uid}")]])
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
                    caption=f"📄 **ᴍᴀꜱᴛᴇʀ ꜱᴛᴜᴅᴇɴᴛ ᴘʀᴏꜰɪʟᴇ ꜰɪʟᴇ:** `{sid}.json`"
                )
        else:
            await query.message.reply_text("⚠️ JSON file not found on disk.")

    # Broadcast
    elif data == "admin_broadcast":
        await query.answer()
        context.user_data["awaiting_broadcast"] = True
        await query.edit_message_text("📢 Send the message text you wish to broadcast to all registered users:")