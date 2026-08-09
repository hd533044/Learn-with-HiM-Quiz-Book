import os
import re
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


def clean_plan_name(name_str: str) -> str:
    """Strips emoji icons like 📦 or 🎁 to prevent broken [] square symbols on system fonts."""
    if not name_str:
        return "BRONZE PACK"
    clean = re.sub(r'[^\x00-\x7F]+', '', name_str).strip()
    return clean if clean else name_str


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


def get_ttf_font(size: int, bold: bool = False):
    font_paths = [
        "C:\\Windows\\Fonts\\arialbd.ttf" if bold else "C:\\Windows\\Fonts\\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
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
    Generates an executive-grade academic fee receipt card.
    Strictly displays base plan quota without bonus calculations.
    """
    try:
        profile = get_user_profile(user_id) or {}
        latest_txn = get_latest_user_transaction(user_id)

        full_name = profile.get("full_name", "Student")
        student_id = profile.get("student_id", f"USER_{user_id}")
        masked_phone = mask_phone_number(profile.get("phone_number", ""))

        paid_bal = profile.get("paid_question_balance", 0) or 80
        vip_expiry_raw = profile.get("vip_pass_expiry") or "N/A"

        # Determine Real Razorpay Payment / Txn ID
        real_payment_id = payment_id or profile.get("payment_id")
        if latest_txn and (not real_payment_id or real_payment_id == "OFFICIAL_SUBSCRIBED"):
            real_payment_id = latest_txn.get("payment_id")
        if not real_payment_id or real_payment_id == "N/A":
            real_payment_id = "OFFICIAL_SUBSCRIBED"

        # Permanent Deterministic Invoice Number
        invoice_no = f"INV-{student_id}-{user_id}"

        # Resolve Active Plan Info directly from PLAN_TIERS
        if latest_txn:
            p_key = latest_txn.get("plan_key") or plan_key or "BRONZE"
            p_name = clean_plan_name(latest_txn.get("plan_name"))
            p_price = latest_txn.get("amount_paid")
            p_days = latest_txn.get("validity_days")
            base_quota = latest_txn.get("daily_quota") or 80
            payment_date_str = latest_txn.get("created_at")
        else:
            p_key = plan_key or "BRONZE"
            for pk, pv in PLAN_TIERS.items():
                if pv.get("daily_limit") == paid_bal:
                    p_key = pk
                    break
            p_info = PLAN_TIERS.get(p_key, {"name": "BRONZE PACK", "price": 5, "days": 3, "daily_limit": 80})
            p_name = clean_plan_name(p_info.get("name"))
            p_price = p_info.get("price")
            p_days = p_info.get("days")
            base_quota = p_info.get("daily_limit")
            payment_date_str = None

        # STRICTLY DISPLAY PLAN PACK BASE QUOTA
        quota_display_str = f"{base_quota} Questions / Day"

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

        # Canvas Dimensions
        width, height = 1400, 1850
        image = Image.new("RGBA", (width, height), "#FFFFFF")
        draw = ImageDraw.Draw(image)

        # Fonts
        font_title = get_ttf_font(38, bold=True)
        font_sub = get_ttf_font(26, bold=True)
        font_label = get_ttf_font(24, bold=True)
        font_val = get_ttf_font(24, bold=False)
        font_amount = get_ttf_font(36, bold=True)
        font_footer = get_ttf_font(20, bold=False)

        # Palette
        NAVY_DARK = "#0F172A"
        NAVY_LIGHT = "#1E3A8A"
        TEXT_BLUE = "#0284C7"
        TEXT_GREEN = "#15803D"
        TEXT_ORANGE = "#D97706"

        # 1. Top Header Banner
        draw.rectangle([0, 0, width, 180], fill=NAVY_DARK)
        draw.rectangle([100, 45, width - 100, 205], fill=NAVY_LIGHT)
        draw.text((320, 100), "LEARN WITH HIM QUIZ BOOK", fill="#FFFFFF", font=font_title)

        # Logo Placement
        logo_path = os.path.join(BASE_DIR, "assets", "logo.png")
        if os.path.exists(logo_path):
            try:
                logo_img = Image.open(logo_path).convert("RGBA")
                logo_img = logo_img.resize((130, 130), Image.Resampling.LANCZOS)
                image.paste(logo_img, (120, 60), logo_img)
            except Exception:
                pass

        # 2. Metadata Section
        y = 250
        lbl_sname = "Student Name: "
        draw.text((100, y), lbl_sname, fill=NAVY_DARK, font=font_label)
        w_sname = draw.textlength(lbl_sname, font=font_label)
        draw.text((100 + w_sname, y), f"{full_name} ({student_id})", fill=NAVY_LIGHT, font=font_label)

        lbl_tg = "Telegram Channel: "
        draw.text((800, y), lbl_tg, fill=NAVY_DARK, font=font_label)
        w_tg = draw.textlength(lbl_tg, font=font_label)
        draw.text((800 + w_tg, y), "@Learnwithhim", fill=TEXT_BLUE, font=font_val)

        y += 60
        lbl_bot = "Platform Name: "
        draw.text((100, y), lbl_bot, fill=NAVY_DARK, font=font_label)
        w_bot = draw.textlength(lbl_bot, font=font_label)
        draw.text((100 + w_bot, y), "Learn with HiM Quiz Book Bot", fill="#334155", font=font_val)

        lbl_ph = "Masked Phone: "
        draw.text((800, y), lbl_ph, fill=NAVY_DARK, font=font_label)
        w_ph = draw.textlength(lbl_ph, font=font_label)
        draw.text((800 + w_ph, y), f"{masked_phone}", fill="#334155", font=font_val)

        # 3. Sub-Header Banner
        y += 70
        draw.rectangle([100, y, width - 100, y + 65], fill=NAVY_LIGHT)
        draw.text((320, y + 15), "OFFICIAL VIP SUBSCRIPTION FEE RECEIPT", fill="#FFFFFF", font=font_sub)

        # 4. Form Fields
        y += 100
        lbl_no = "No: "
        draw.text((100, y), lbl_no, fill=NAVY_DARK, font=font_label)
        w_no = draw.textlength(lbl_no, font=font_label)
        draw.text((100 + w_no, y), f"{invoice_no}", fill=NAVY_LIGHT, font=font_val)

        lbl_dt = "Date: "
        draw.text((750, y), lbl_dt, fill=NAVY_DARK, font=font_label)
        w_dt = draw.textlength(lbl_dt, font=font_label)
        draw.text((750 + w_dt, y), f"{payment_date_str}", fill="#334155", font=font_val)

        y += 70
        lbl_rec = "Received with thanks from: "
        draw.text((100, y), lbl_rec, fill=NAVY_DARK, font=font_label)
        w_rec = draw.textlength(lbl_rec, font=font_label)
        draw.text((100 + w_rec, y), f"{full_name}  (Student ID: {student_id})", fill=NAVY_LIGHT, font=font_label)

        y += 70
        lbl_rup = "Rupees: "
        draw.text((100, y), lbl_rup, fill=NAVY_DARK, font=font_label)
        w_rup = draw.textlength(lbl_rup, font=font_label)
        draw.text((100 + w_rup, y), f"₹{p_price} INR ONLY", fill=TEXT_GREEN, font=font_label)

        lbl_tow = "towards: "
        draw.text((600, y), lbl_tow, fill=NAVY_DARK, font=font_label)
        w_tow = draw.textlength(lbl_tow, font=font_label)
        draw.text((600 + w_tow, y), f"{p_name} ({p_days} Days Access)", fill=NAVY_LIGHT, font=font_label)

        y += 70
        lbl_q = "Daily Question Quota: "
        draw.text((100, y), lbl_q, fill=NAVY_DARK, font=font_label)
        w_q = draw.textlength(lbl_q, font=font_label)
        draw.text((100 + w_q, y), f"{quota_display_str}", fill=TEXT_BLUE, font=font_label)

        y += 70
        lbl_exp = "Pass Expiry Date: "
        draw.text((100, y), lbl_exp, fill=NAVY_DARK, font=font_label)
        w_exp = draw.textlength(lbl_exp, font=font_label)
        draw.text((100 + w_exp, y), f"{expiry_display_str}", fill=TEXT_ORANGE, font=font_label)

        y += 70
        lbl_gw = "Payment Gateway: "
        draw.text((100, y), lbl_gw, fill=NAVY_DARK, font=font_label)
        w_gw = draw.textlength(lbl_gw, font=font_label)
        draw.text((100 + w_gw, y), "Razorpay Secure (UPI / Cards / NetBanking)", fill="#334155", font=font_val)

        y += 70
        lbl_txn = "Txn / Payment ID: "
        draw.text((100, y), lbl_txn, fill=NAVY_DARK, font=font_label)
        w_txn = draw.textlength(lbl_txn, font=font_label)
        draw.text((100 + w_txn, y), f"{real_payment_id}", fill=NAVY_LIGHT, font=font_val)

        # 5. Highlighted Amount Box
        y += 120
        draw.rectangle([100, y, 480, y + 80], fill=NAVY_LIGHT, outline=NAVY_DARK, width=2)
        draw.text((130, y + 20), f"Rs: ₹{p_price}/-", fill="#FFFFFF", font=font_amount)

        # 6. Verification Seal (ISSUED BY REMOVED)
        draw.rectangle([750, y, 1300, y + 80], outline="#16A34A", width=2)
        draw.text((780, y + 15), "RAZORPAY SECURE VERIFIED ✔", fill="#16A34A", font=font_label)
        draw.text((780, y + 45), "STATUS: PAYMENT_SUCCESS", fill=TEXT_GREEN, font=font_val)

        # 7. Document Footer Note
        draw.rectangle([100, 1680, width - 100, 1780], fill=NAVY_DARK)
        draw.text((130, 1705), "-> Curated by Himanshu Sir • Telegram Channel: @Learnwithhim", fill="#FFFFFF", font=font_footer)
        draw.text((130, 1740), "-> Official Educational Tax Invoice • Terms: Non-refundable academic pass", fill="#38BDF8", font=font_footer)

        filename = f"Invoice_{user_id}_{p_key}.png"
        filepath = os.path.join(USER_PROFILES_DIR, filename)
        image.save(filepath, "PNG", quality=100)

        return filepath
    except Exception as e:
        print(f"Error generating payment invoice card: {e}")
        return None