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


def get_ttf_font(size: int):
    """Safely loads crisp TrueType system fonts for HD text rendering."""
    font_paths = [
        "C:\\Windows\\Fonts\\arial.ttf",
        "C:\\Windows\\Fonts\\seguiemj.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc"
    ]
    for path in font_paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def generate_payment_invoice_card(user_id: int, plan_key: str, payment_id: str = "N/A") -> str:
    """
    Generates a 2K Ultra-HD Branded PNG Payment Invoice Card for the student.
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

        # 2K Canvas Dimensions (Ultra HD 1800x1100)
        width, height = 1800, 1100
        image = Image.new("RGBA", (width, height), "#0B132B")
        draw = ImageDraw.Draw(image)

        # Crisp HD Font Scales
        font_header_title = get_ttf_font(42)
        font_header_sub = get_ttf_font(26)
        font_verified = get_ttf_font(28)
        font_label = get_ttf_font(28)
        font_value_large = get_ttf_font(34)
        font_value_highlight = get_ttf_font(38)
        font_stamp_title = get_ttf_font(32)
        font_stamp_sub = get_ttf_font(28)
        font_footer = get_ttf_font(24)

        # Outer Double HD Border
        draw.rectangle([30, 30, width - 30, height - 30], outline="#38BDF8", width=6)
        draw.rectangle([46, 46, width - 46, height - 46], outline="#1E293B", width=4)

        # Header Container
        draw.rectangle([60, 60, width - 60, 220], fill="#1C2541", outline="#3B82F6", width=2)

        # Brand Logo Placement
        logo_path = os.path.join(BASE_DIR, "assets", "logo.png")
        if os.path.exists(logo_path):
            try:
                logo_img = Image.open(logo_path).convert("RGBA")
                logo_img = logo_img.resize((130, 130), Image.Resampling.LANCZOS)
                image.paste(logo_img, (90, 75), logo_img)
            except Exception:
                pass

        # Header Titles
        draw.text((250, 85), "LEARN WITH HIM QUIZ BOOK", fill="#F8FAFC", font=font_header_title)
        draw.text((250, 145), "OFFICIAL VIP PAYMENT INVOICE RECEIPT", fill="#38BDF8", font=font_header_sub)
        draw.text((1380, 110), "VERIFIED 🟢", fill="#22C55E", font=font_verified)

        # Student Details Panel
        draw.rectangle([60, 250, width - 60, 410], fill="#111827", outline="#374151", width=2)
        
        draw.text((90, 280), f"Student Name:", fill="#9CA3AF", font=font_label)
        draw.text((310, 275), f"{full_name}", fill="#F9FAFB", font=font_value_large)

        draw.text((1000, 280), f"Student ID:", fill="#9CA3AF", font=font_label)
        draw.text((1200, 275), f"{student_id}", fill="#38BDF8", font=font_value_large)

        draw.text((90, 345), f"Masked Phone:", fill="#9CA3AF", font=font_label)
        draw.text((310, 345), f"{masked_phone}", fill="#E5E7EB", font=font_label)

        draw.text((1000, 345), f"Txn / Payment ID:", fill="#9CA3AF", font=font_label)
        draw.text((1280, 345), f"{payment_id}", fill="#E5E7EB", font=font_label)

        # Main Invoice Details Table Box
        draw.rectangle([60, 440, width - 60, 920], fill="#1E293B", outline="#3B82F6", width=2)

        y = 480
        draw.text((100, y), "UNLOCKED PACK:", fill="#94A3B8", font=font_label)
        draw.text((450, y - 5), f"{plan_info.get('name')}", fill="#FACC15", font=font_value_highlight)

        y += 80
        draw.line([100, y, width - 100, y], fill="#334155", width=2)

        y += 30
        draw.text((100, y), "AMOUNT PAID:", fill="#94A3B8", font=font_label)
        draw.text((450, y - 5), f"₹{plan_info.get('price', 0)} INR", fill="#22C55E", font=font_value_highlight)

        y += 80
        draw.line([100, y, width - 100, y], fill="#334155", width=2)

        y += 30
        draw.text((100, y), "DAILY QUOTA:", fill="#94A3B8", font=font_label)
        draw.text((450, y), f"{plan_info.get('daily_limit')} Questions / Day", fill="#38BDF8", font=font_value_large)

        y += 70
        draw.text((100, y), "PACK VALIDITY:", fill="#94A3B8", font=font_label)
        draw.text((450, y), f"{plan_info.get('days')} Days Access", fill="#F8FAFC", font=font_value_large)

        y += 70
        draw.text((100, y), "TXN TIMESTAMP:", fill="#94A3B8", font=font_label)
        draw.text((450, y), f"{txn_time}", fill="#CBD5E1", font=font_value_large)

        # High-Resolution Razorpay Verification Stamp Box
        stamp_box = [1200, 620, 1680, 870]
        draw.rectangle(stamp_box, fill="#0284C7", outline="#38BDF8", width=4)
        draw.text((1260, 650), "RAZORPAY", fill="#FFFFFF", font=font_stamp_title)
        draw.text((1260, 710), "PAID & VERIFIED 🟢", fill="#4ADE80", font=font_stamp_title)
        draw.text((1260, 780), f"₹{plan_info.get('price', 0)} RECEIVED", fill="#FDE047", font=font_stamp_sub)

        # Footer Notes
        draw.text((90, 960), "👑 Curated by Himanshu Sir • Telegram: @Learnwithhim", fill="#94A3B8", font=font_footer)
        draw.text((90, 1010), "⚡ Thank you for subscribing! Good luck with your preparation!", fill="#38BDF8", font=font_footer)

        filename = f"Invoice_{user_id}_{plan_key}.png"
        filepath = os.path.join(USER_PROFILES_DIR, filename)
        image.save(filepath, "PNG", quality=100)

        return filepath
    except Exception as e:
        print(f"Error generating payment invoice card: {e}")
        return None