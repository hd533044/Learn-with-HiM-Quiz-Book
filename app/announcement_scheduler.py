import asyncio
from telegram.ext import Application
from app.admin_services import fetch_pending_announcements, update_announcement_status
from app.database import get_db, release_db
from psycopg2.extras import RealDictCursor

def get_all_active_user_ids() -> list:
    """Fetches all registered user IDs from database for broadcasting."""
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT user_id FROM user_profiles;")
        return [r['user_id'] for r in cursor.fetchall()]
    finally:
        cursor.close()
        release_db(conn)


async def run_announcement_broadcast_worker(application: Application):
    """Background task running every 60 seconds to broadcast scheduled announcements."""
    while True:
        try:
            pending_list = fetch_pending_announcements()
            for annc in pending_list:
                annc_id = annc['id']
                text = annc['message_text']
                media_id = annc['media_file_id']
                media_type = annc['media_type']
                
                user_ids = get_all_active_user_ids()
                
                for uid in user_ids:
                    try:
                        if media_type == "photo":
                            await application.bot.send_photo(chat_id=uid, photo=media_id, caption=text, parse_mode="Markdown")
                        elif media_type == "video":
                            await application.bot.send_video(chat_id=uid, video=media_id, caption=text, parse_mode="Markdown")
                        else:
                            await application.bot.send_message(chat_id=uid, text=text, parse_mode="Markdown")
                        
                        await asyncio.sleep(0.05) # Prevent Telegram Flood Limits
                    except Exception:
                        pass # Ignore blocked users
                
                update_announcement_status(annc_id, "SENT")
                
        except Exception as e:
            print(f"[Scheduler Error]: {e}")
            
        await asyncio.sleep(60) # Poll database every minute