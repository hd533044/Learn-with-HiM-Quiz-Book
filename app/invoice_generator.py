import os
import pytz
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont
from app.config import USER_PROFILES_DIR, BASE_DIR, PLAN_TIERS
from app.database import get_user_profile, get_db, release_db
from psycopg2.extras import RealDictCursor


def mask_phone_number(phone_str: str) -> str:
    if not phone_str or len(str(phone_str)) < 4:
        return "XXXXXX0000"
    clean_p = "".join(filter(str.isdigit, str(phone_str)))
    if len(clean_p) >= 4:
        return "XXXXXX" + clean_p[-4:]
    return "XXXXXX0000"


def get_latest_user_transaction(user_id: int):
    """Fetches the most recent payment transaction log for the user."""
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "SELECT * FROM payment_transactions WHERE user_id = %s ORDER BY id DESC LIMIT 1",
            (user_id,)
        )
        row = cursor.fetchone()
        cursor.close()
        release_db(conn)
        return dict(row) if row else None
    except Exception:
        if conn:
            release_db(conn)
        return None


def get_ttf_font(size: int):
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


def generate_payment_invoice_card(user_id: int, plan_key: str = None, payment_id: str = None, txn_time_str: str = None) -> str:
    """
    Generates an official academic fee receipt modeled exactly after the classic institutional receipt format.
    Includes logo.png at top-left corner, clean underlined metadata, red amount box, and signature block.
    """
    try:
        profile = get_user_profile(user_id) or {}
        latest_txn = get_latest_user_transaction(user_id)

        full_name = profile.get("full_name", "Student")
        student_id = profile.get("student_id", f"USER_{user_id}")
        masked_phone = mask_phone_number(profile.get("phone_number", ""))

        paid_bal = profile.get("paid_question_balance", 0) or 80
        bonus_q = profile.get("bonus_quota", 0) or 0
        total_daily_quota = paid_bal + bonus_q
        vip_expiry_raw = profile.get("vip_pass_expiry") or "N/A"

        # Determine Real Razorpay Payment / Txn ID
        real_payment_id = payment_id or profile.get("payment_id")
        if latest_txn and (not real_payment_id or real_payment_id == "OFFICIAL_SUBSCRIBED"):
            real_payment_id = latest_txn.get("payment_id")
        if not real_payment_id or real_payment_id == "N/A":
            real_payment_id = "OFFICIAL_SUBSCRIBED"

        # Permanent Deterministic Invoice Number
        invoice_no = f"INV-{student_id}-{user_id}"

        # Resolve Active Plan Info
        if latest_txn:
            p_key = latest_txn.get("plan_key") or plan_key or "BRONZE"
            p_name = latest_txn.get("plan_name")
            p_price = latest_txn.get("amount_paid")
            p_days = latest_txn.get("validity_days")
            payment_date_str = latest_txn.get("created_at")
        else:
            p_key = plan_key or "BRONZE"
            for pk, pv in PLAN_TIERS.items():
                if pv.get("daily_limit") == paid_bal:
                    p_key = pk
                    break
            p_info = PLAN_TIERS.get(p_key, {"name": "BRONZE PACK", "price": 5, "days": 3, "daily_limit": 80})
            p_name = p_info.get("name")
            p_price = p_info.get("price")
            p_days = p_info.get("days")
            payment_date_str = None

        # Synchronize Payment Date
        ist = pytz.timezone("Asia/Kolkata")
        if not payment_date_str or payment_date_str == "N/A":
            payment_date_str = profile.get("payment_timestamp")
            if not payment_date_str and vip_expiry_raw and vip_expiry_raw != "N/A":
                try:
                    clean_exp = vip_expiry_raw.replace(" IST", "").strip()
                    expiry_dt = datetime.strptime(clean_exp, "%Y-%m-%d %H:%M:%S")
                    payment_dt = expiry_dt - timedelta(days=p_days)
                    payment_date_str = payment_dt.strftime("%d %b %Y, %I:%M %p IST")
                except Exception:
                    payment_date_str = datetime.now(ist).strftime("%d %b %Y, %I:%M %p IST")
            elif not payment_date_str:
                payment_date_str = datetime.now(ist).strftime("%d %b %Y, %I:%M %p IST")

        # Format Pass Expiry String
        if vip_expiry_raw and vip_expiry_raw != "N/A":
            try:
                clean_exp = vip_expiry_raw.replace(" IST", "").strip()
                exp_dt = datetime.strptime(clean_exp, "%Y-%m-%d %H:%M:%S")
                expiry_display_str = exp_dt.strftime("%d %b %Y, %I:%M %p IST")
            except Exception:
                expiry_display_str = vip_expiry_raw
        else:
            expiry_display_str = "N/A"

        # Vertical Document Canvas Dimensions (High-Resolution A4 proportions: 1400x1800)
        width, height = 1400, 1800
        image = Image.new("RGBA", (width, height), "#FFFFFF")
        draw = ImageDraw.Draw(image)

        # Fonts
        font_inst_title = get_ttf_font(42)
        font_sub_banner = get_ttf_font(30)
        font_label_bold = get_ttf_font(28)
        font_val = get_ttf_font(28)
        font_amount_box = get_ttf_font(38)
        font_footer = get_ttf_font(22)

        # 1. Top Black Header Bar
        draw.rectangle([0, 0, width, 180], fill="#000000")

        # 2. Main Crimson Red Institution Banner Block
        draw.rectangle([120, 50, width - 120, 210], fill="#B91C1C")
        draw.text((360, 100), "LEARN WITH HIM QUIZ BOOK", fill="#FFFFFF", font=font_inst_title)

        # Logo Placement (Top-Left corner of header)
        logo_path = os.path.join(BASE_DIR, "assets", "logo.png")
        if os.path.exists(logo_path):
            try:
                logo_img = Image.open(logo_path).convert("RGBA")
                logo_img = logo_img.resize((130, 130), Image.Resampling.LANCZOS)
                image.paste(logo_img, (140, 65), logo_img)
            except Exception:
                pass

        # Platform Contact Header Info
        draw.text((120, 250), "Telegram Channel:", fill="#000000", font=font_label_bold)
        draw.line([370, 280, 800, 280], fill="#B91C1C", width=2)
        draw.text((380, 248), "@Learnwithhim", fill="#1E293B", font=font_val)

        draw.text((830, 250), "Support:", fill="#000000", font=font_label_bold)
        draw.line([950, 280, 1280, 280], fill="#B91C1C", width=2)
        draw.text((960, 248), "Online Bot Support", fill="#1E293B", font=font_val)

        draw.text((120, 310), "Masked Phone:", fill="#000000", font=font_label_bold)
        draw.line([330, 340, 800, 340], fill="#B91C1C", width=2)
        draw.text((340, 308), f"{masked_phone}", fill="#1E293B", font=font_val)

        draw.text((830, 310), "Txn ID:", fill="#000000", font=font_label_bold)
        draw.line([940, 340, 1280, 340], fill="#B91C1C", width=2)
        draw.text((950, 308), f"{real_payment_id}", fill="#1E293B", font=font_val)

        # 3. Crimson Red Sub-Header Banner (FEE RECEIPT)
        draw.rectangle([120, 390, width - 120, 460], fill="#B91C1C")
        draw.text((380, 408), "OFFICIAL VIP SUBSCRIPTION FEE RECEIPT", fill="#FFFFFF", font=font_sub_banner)

        # 4. Form Fields with Crimson Red Underlines
        y = 510
        draw.text((120, y), "No:", fill="#000000", font=font_label_bold)
        draw.line([180, y + 32, 600, y + 32], fill="#B91C1C", width=2)
        draw.text((190, y - 2), f"{invoice_no}", fill="#1E3A8A", font=font_val)

        draw.text((650, y), "Date:", fill="#000000", font=font_label_bold)
        draw.line([730, y + 32, 1280, y + 32], fill="#B91C1C", width=2)
        draw.text((740, y - 2), f"{payment_date_str}", fill="#1E293B", font=font_val)

        y += 80
        draw.text((120, y), "Received with thanks from", fill="#000000", font=font_val)
        draw.line([490, y + 32, 1280, y + 32], fill="#B91C1C", width=2)
        draw.text((500, y - 2), f"{full_name}  (Student ID: {student_id})", fill="#1E3A8A", font=font_label_bold)

        y += 80
        draw.text((120, y), "Rupees", fill="#000000", font=font_val)
        draw.line([230, y + 32, 600, y + 32], fill="#B91C1C", width=2)
        draw.text((240, y - 2), f"₹{p_price} INR ONLY", fill="#15803D", font=font_label_bold)

        draw.text((620, y), "towards", fill="#000000", font=font_val)
        draw.line([740, y + 32, 1180, y + 32], fill="#B91C1C", width=2)
        draw.text((750, y - 2), f"{p_name} ({p_days} Days Access)", fill="#1E3A8A", font=font_label_bold)

        draw.text((1190, y), "course", fill="#000000", font=font_val)

        y += 80
        draw.text((120, y), "Daily Question Quota:", fill="#000000", font=font_val)
        draw.line([420, y + 32, 800, y + 32], fill="#B91C1C", width=2)
        draw.text((430, y - 2), f"{total_daily_quota} Questions / Day", fill="#0284C7", font=font_label_bold)

        y += 80
        draw.text((120, y), "Pass Expiry Date:", fill="#000000", font=font_val)
        draw.line([370, y + 32, 800, y + 32], fill="#B91C1C", width=2)
        draw.text((380, y - 2), f"{expiry_display_str}", fill="#D97706", font=font_label_bold)

        y += 80
        draw.text((120, y), "By cash / online gateway:", fill="#000000", font=font_val)
        draw.line([450, y + 32, 1280, y + 32], fill="#B91C1C", width=2)
        draw.text((460, y - 2), "Razorpay Secure Online Gateway (UPI / Cards / NetBanking)", fill="#1E293B", font=font_val)

        # 5. Red Highlighted Amount Box (Bottom Left)
        y += 110
        draw.rectangle([120, y, 550, y + 80], fill="#B91C1C", outline="#7F1D1D", width=2)
        draw.text((150, y + 20), f"Rs: ₹{p_price}/-", fill="#FFFFFF", font=font_amount_box)

        # 6. Receiver Signature Block & Verification Seal (Bottom Right)
        draw.line([850, y + 40, 1280, y + 40], fill="#B91C1C", width=2)
        draw.text((870, y + 50), "Receiver Signature:", fill="#B91C1C", font=font_label_bold)
        draw.text((1120, y + 50), "Himanshu Sir", fill="#000000", font=font_label_bold)

        # Stamp
        draw.rectangle([850, y - 60, 1280, y + 10], outline="#16A34A", width=3)
        draw.text((880, y - 45), "RAZORPAY VERIFIED ✔", fill="#16A34A", font=font_label_bold)
        draw.text((880, y - 10), "PAYMENT RECEIVED", fill="#16A34A", font=font_val)

        # 7. Document Footer Note
        draw.rectangle([120, 1600, width - 120, 1720], fill="#0F172A")
        draw.text((150, 1625), "-> Curated by Himanshu Sir • Telegram Channel: @Learnwithhim", fill="#FFFFFF", font=font_footer)
        draw.text((150, 1665), "-> Thank you for subscribing! Good luck with your preparation!", fill="#38BDF8", font=font_footer)

        filename = f"Invoice_{user_id}_{p_key}.png"
        filepath = os.path.join(USER_PROFILES_DIR, filename)
        image.save(filepath, "PNG", quality=100)

        return filepath
    except Exception as e:
        print(f"Error generating payment invoice card: {e}")
        return None