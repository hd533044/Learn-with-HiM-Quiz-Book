import os
import pytz
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from app.config import USER_PROFILES_DIR, BASE_DIR, PLAN_TIERS
from app.database import get_user_profile, get_db, release_db


def mask_phone_number(phone_str: str) -> str:
    if not phone_str or len(str(phone_str)) < 4:
        return "XXXXXX0000"
    clean_p = "".join(filter(str.isdigit, str(phone_str)))
    if len(clean_p) >= 4:
        return "XXXXXX" + clean_p[-4:]
    return "XXXXXX0000"


def get_next_invoice_number() -> str:
    """Generates sequential invoice numbers starting from 533001."""
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users WHERE paid_question_balance > 0 OR demo_used = 1")
        row = cursor.fetchone()
        cursor.close()
        release_db(conn)
        
        count = row[0] if row else 0
        inv_num = 533001 + count
        return f"INV-{inv_num}"
    except Exception:
        if conn:
            release_db(conn)
        return "INV-533001"


def get_ttf_font(size: int):
    """Loads crisp system fonts for high-definition rendering."""
    font_paths = [
        "C:\\Windows\\Fonts\\arialbd.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
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
    Generates an official, executive-style 2K HD Payment Invoice Card.
    Returns the file path of the generated image.
    """
    try:
        profile = get_user_profile(user_id) or {}
        plan_info = PLAN_TIERS.get(plan_key, {"name": plan_key, "price": 0, "days": 30, "daily_limit": 100})

        full_name = profile.get("full_name", "Student")
        student_id = profile.get("student_id", f"USER_{user_id}")
        masked_phone = mask_phone_number(profile.get("phone_number", ""))
        invoice_no = get_next_invoice_number()
        
        ist = pytz.timezone("Asia/Kolkata")
        txn_time = datetime.now(ist).strftime("%d %b %Y, %I:%M %p IST")

        # 2K Canvas Dimensions (1800x1200)
        width, height = 1800, 1200
        image = Image.new("RGBA", (width, height), "#F8FAFC")
        draw = ImageDraw.Draw(image)

        # Fonts
        font_header_title = get_ttf_font(44)
        font_header_sub = get_ttf_font(24)
        font_sec_title = get_ttf_font(28)
        font_label = get_ttf_font(26)
        font_val = get_ttf_font(28)
        font_val_bold = get_ttf_font(30)
        font_price = get_ttf_font(38)
        font_stamp_title = get_ttf_font(32)
        font_stamp_sub = get_ttf_font(24)
        font_footer = get_ttf_font(22)

        # Outer Frame
        draw.rectangle([30, 30, width - 30, height - 30], outline="#1E3A8A", width=5)
        draw.rectangle([42, 46, width - 42, height - 46], outline="#CBD5E1", width=2)

        # Executive Dark Navy Header Bar
        draw.rectangle([55, 55, width - 55, 230], fill="#0F172A")

        # Brand Logo Placement
        logo_path = os.path.join(BASE_DIR, "assets", "logo.png")
        if os.path.exists(logo_path):
            try:
                logo_img = Image.open(logo_path).convert("RGBA")
                logo_img = logo_img.resize((135, 130), Image.Resampling.LANCZOS)
                image.paste(logo_img, (85, 78), logo_img)
            except Exception:
                pass

        # Header Titles
        draw.text((245, 80), "LEARN WITH HIM QUIZ BOOK", fill="#FFFFFF", font=font_header_title)
        draw.text((245, 142), "OFFICIAL ACADEMIC PAYMENT RECEIPT & INVOICE", fill="#38BDF8", font=font_header_sub)

        # Invoice No & Verified Badge Top Right
        draw.rectangle([1380, 80, 1715, 130], fill="#166534", outline="#22C55E", width=2)
        draw.text((1415, 92), "VERIFIED OFFICIAL", fill="#FFFFFF", font=font_sec_title)
        draw.text((1380, 150), f"INVOICE #: {invoice_no}", fill="#F8FAFC", font=font_sec_title)

        # Section 1: Student Information
        draw.rectangle([55, 255, width - 55, 430], fill="#FFFFFF", outline="#E2E8F0", width=2)
        draw.rectangle([55, 255, width - 55, 300], fill="#F1F5F9")
        draw.text((75, 265), "STUDENT & TRANSACTION INFORMATION", fill="#1E293B", font=font_sec_title)

        draw.text((75, 320), "Student Name:", fill="#64748B", font=font_label)
        draw.text((270, 318), f"{full_name}", fill="#0F172A", font=font_val_bold)

        draw.text((1000, 320), "Student ID:", fill="#64748B", font=font_label)
        draw.text((1200, 318), f"{student_id}", fill="#0284C7", font=font_val_bold)

        draw.text((75, 375), "Masked Phone:", fill="#64748B", font=font_label)
        draw.text((270, 375), f"{masked_phone}", fill="#334155", font=font_val)

        draw.text((1000, 375), "Txn / Payment ID:", fill="#64748B", font=font_label)
        draw.text((1280, 375), f"{payment_id}", fill="#334155", font=font_val)

        # Section 2: Purchased Plan Details
        draw.rectangle([55, 455, 1150, 980], fill="#FFFFFF", outline="#CBD5E1", width=2)
        draw.rectangle([55, 455, 1150, 500], fill="#E0F2FE")
        draw.text((75, 465), "SUBSCRIPTION & PLAN BREAKDOWN", fill="#0369A1", font=font_sec_title)

        y = 525
        draw.text((85, y), "Unlocked Pack:", fill="#64748B", font=font_label)
        draw.text((320, y - 5), f"{plan_info.get('name')}", fill="#1E3A8A", font=font_price)

        y += 75
        draw.line([85, y, 1120, y], fill="#E2E8F0", width=2)

        y += 25
        draw.text((85, y), "Amount Paid:", fill="#64748B", font=font_label)
        draw.text((320, y - 5), f"₹{plan_info.get('price', 0)} INR (Inclusive of taxes)", fill="#15803D", font=font_price)

        y += 75
        draw.line([85, y, 1120, y], fill="#E2E8F0", width=2)

        y += 25
        draw.text((85, y), "Daily Question Quota:", fill="#64748B", font=font_label)
        draw.text((380, y), f"{plan_info.get('daily_limit')} Questions / Day", fill="#0284C7", font=font_val_bold)

        y += 60
        draw.text((85, y), "Subscription Validity:", fill="#64748B", font=font_label)
        draw.text((380, y), f"{plan_info.get('days')} Days Access", fill="#0F172A", font=font_val)

        y += 60
        draw.text((85, y), "Payment Date & Time:", fill="#64748B", font=font_label)
        draw.text((380, y), f"{txn_time}", fill="#334155", font=font_val)

        # Plan Feature Description List
        y += 70
        draw.rectangle([85, y, 1120, y + 100], fill="#F8FAFC", outline="#E2E8F0", width=1)
        draw.text((105, y + 15), "INCLUDED PLAN FEATURES:", fill="#0F172A", font=font_sec_title)
        draw.text((105, y + 55), "• Full Question Explanations  • Custom PDF Export  • State Leaderboards", fill="#475569", font=font_footer)

        # Section 3: Official Verification & Razorpay Seal
        draw.rectangle([1180, 455, width - 55, 980], fill="#FFFFFF", outline="#CBD5E1", width=2)
        draw.rectangle([1180, 455, width - 55, 500], fill="#F1F5F9")
        draw.text((1200, 465), "PAYMENT AUTHENTICATION", fill="#0F172A", font=font_sec_title)

        # Razorpay Payment Stamp
        stamp_box = [1210, 540, 1715, 780]
        draw.rectangle(stamp_box, fill="#0284C7", outline="#0369A1", width=3)
        draw.text((1250, 565), "RAZORPAY SECURE", fill="#FFFFFF", font=font_stamp_title)
        draw.text((1250, 625), "PAID & VERIFIED", fill="#86EFAC", font=font_stamp_title)
        draw.text((1250, 695), f"₹{plan_info.get('price', 0)} RECEIVED", fill="#FEF08A", font=font_stamp_sub)

        # Platform Terms
        draw.text((1200, 820), "• Status: PAYMENT_SUCCESS", fill="#16A34A", font=font_label)
        draw.text((1200, 870), "• Gateway: Razorpay UPI/Cards", fill="#475569", font=font_footer)
        draw.text((1200, 910), "• Support: @Learnwithhim", fill="#475569", font=font_footer)

        # Footer
        draw.rectangle([55, 1005, width - 55, 1145], fill="#0F172A")
        draw.text((85, 1030), "👑 Curated by Himanshu Sir • Telegram Channel: @Learnwithhim", fill="#F8FAFC", font=font_footer)
        draw.text((85, 1080), "⚡ Thank you for your purchase! Start practicing now using the /quiz command in bot.", fill="#38BDF8", font=font_footer)

        filename = f"Invoice_{user_id}_{plan_key}.png"
        filepath = os.path.join(USER_PROFILES_DIR, filename)
        image.save(filepath, "PNG", quality=100)

        return filepath
    except Exception as e:
        print(f"Error generating payment invoice card: {e}")
        return None