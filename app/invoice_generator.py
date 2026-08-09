import os
import pytz
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from app.config import USER_PROFILES_DIR, BASE_DIR, PLAN_TIERS
from app.database import get_user_profile


def mask_phone_number(phone_str: str) -> str:
    if not phone_str or len(str(phone_str)) < 4:
        return "XXXXXX0000"
    clean_p = "".join(filter(str.isdigit, str(phone_str)))
    if len(clean_p) >= 4:
        return "XXXXXX" + clean_p[-4:]
    return "XXXXXX0000"


def generate_payment_invoice_card(user_id: int, plan_key: str, payment_id: str = "N/A") -> str:
    """
    Generates a branded PNG Payment Invoice Card for the student.
    Returns the file path of the generated image.
    """
    try:
        profile = get_user_profile(user_id) or {}
        plan_info = PLAN_TIERS.get(plan_key, {"name": plan_key, "price": 0, "days": 30, "daily_limit": 100})

        full_name = profile.get("full_name", "Student")
        student_id = profile.get("student_id", f"USER_{user_id}")
        masked_phone = mask_phone_number(profile.get("phone_number", ""))
        
        ist = pytz.timezone("Asia/Kolkata")
        txn_time = datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S IST")

        # Card Dimensions
        width, height = 900, 550
        image = Image.new("RGBA", (width, height), "#0F172A")
        draw = ImageDraw.Draw(image)

        # Draw Outer Border
        draw.rectangle([15, 15, width - 15, height - 15], outline="#38BDF8", width=3)
        draw.rectangle([25, 25, width - 25, height - 25], outline="#1E293B", width=2)

        # Draw Header Bar
        draw.rectangle([30, 30, width - 30, 110], fill="#1E293B")

        # Try loading brand logo
        logo_path = os.path.join(BASE_DIR, "assets", "logo.png")
        if os.path.exists(logo_path):
            try:
                logo_img = Image.open(logo_path).convert("RGBA")
                logo_img = logo_img.resize((70, 70), Image.Resampling.LANCZOS)
                image.paste(logo_img, (45, 35), logo_img)
            except Exception:
                pass

        # Use Default Font
        font_title = ImageFont.load_default()
        font_large = ImageFont.load_default()
        font_body = ImageFont.load_default()

        # Header Titles
        draw.text((130, 42), "LEARN WITH HIM QUIZ BOOK", fill="#F8FAFC", font=font_title)
        draw.text((130, 70), "OFFICIAL VIP PAYMENT INVOICE RECEIPT", fill="#38BDF8", font=font_body)
        draw.text((680, 50), "VERIFIED 🟢", fill="#22C55E", font=font_body)

        # Invoice Content Box
        y = 130
        draw.text((45, y), f"Student Name: {full_name}", fill="#FFFFFF", font=font_large)
        draw.text((500, y), f"Student ID: {student_id}", fill="#38BDF8", font=font_large)

        y += 40
        draw.text((45, y), f"Masked Phone: {masked_phone}", fill="#CBD5E1", font=font_body)
        draw.text((500, y), f"Txn ID: {payment_id}", fill="#CBD5E1", font=font_body)

        y += 35
        draw.line([45, y, width - 45, y], fill="#334155", width=1)

        y += 20
        draw.text((45, y), "UNLOCKED PACK:", fill="#94A3B8", font=font_body)
        draw.text((220, y), f"{plan_info.get('name')}", fill="#FACC15", font=font_large)

        y += 35
        draw.text((45, y), "AMOUNT PAID:", fill="#94A3B8", font=font_body)
        draw.text((220, y), f"₹{plan_info.get('price', 0)} INR", fill="#22C55E", font=font_large)

        y += 35
        draw.text((45, y), "DAILY QUOTA:", fill="#94A3B8", font=font_body)
        draw.text((220, y), f"{plan_info.get('daily_limit')} Questions / Day", fill="#38BDF8", font=font_body)

        y += 35
        draw.text((45, y), "PACK VALIDITY:", fill="#94A3B8", font=font_body)
        draw.text((220, y), f"{plan_info.get('days')} Days Access", fill="#F8FAFC", font=font_body)

        y += 35
        draw.text((45, y), "TXN TIMESTAMP:", fill="#94A3B8", font=font_body)
        draw.text((220, y), f"{txn_time}", fill="#CBD5E1", font=font_body)

        y += 45
        draw.line([45, y, width - 45, y], fill="#334155", width=1)

        # Razorpay Stamp Badge
        stamp_box = [620, 310, 850, 430]
        draw.rectangle(stamp_box, fill="#0284C7", outline="#38BDF8", width=2)
        draw.text((645, 330), "RAZORPAY", fill="#FFFFFF", font=font_title)
        draw.text((645, 360), "PAID & VERIFIED", fill="#22C55E", font=font_title)
        draw.text((645, 390), f"₹{plan_info.get('price', 0)} RECEIVED", fill="#FACC15", font=font_body)

        # Footer
        draw.text((45, 480), "👑 Curated by Himanshu Sir • Telegram: @Learnwithhim", fill="#64748B", font=font_body)
        draw.text((45, 505), "⚡ Thank you for subscribing! Good luck with your preparation!", fill="#38BDF8", font=font_body)

        filename = f"Invoice_{user_id}_{plan_key}.png"
        filepath = os.path.join(USER_PROFILES_DIR, filename)
        image.save(filepath)

        return filepath
    except Exception as e:
        print(f"Error generating payment invoice card: {e}")
        return None