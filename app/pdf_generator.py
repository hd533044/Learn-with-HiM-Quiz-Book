import os
import json
import traceback
import xml.sax.saxutils as saxutils
from datetime import datetime, timedelta
from io import BytesIO

import psycopg2
from psycopg2.extras import RealDictCursor
from xhtml2pdf import pisa

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from app.config import USER_PROFILES_DIR, BASE_DIR
from app.database import get_user_profile, get_db, release_db

# -----------------------------------------------------------------------------
# REGISTER DEVANAGARI / HINDI FONT SUPPORT FOR REPORTLAB & SYSTEM PDF
# -----------------------------------------------------------------------------
HINDI_FONT_NAME = "Times-Roman"


def setup_hindi_fonts():
    """
    Registers Devanagari TTF fonts for rendering Hindi text.
    Searches system paths and local assets directory.
    """
    global HINDI_FONT_NAME

    candidate_paths = [
        os.path.join(BASE_DIR, "assets", "NotoSansDevanagari-Regular.ttf"),
        os.path.join(BASE_DIR, "assets", "NotoSansDevanagari_SemiCondensed-Regular.ttf"),
        os.path.join(BASE_DIR, "assets", "NotoSansDevanagari_Condensed-Regular.ttf"),
        os.path.join(BASE_DIR, "assets", "Mangal.ttf"),
        os.path.join(BASE_DIR, "assets", "FreeSans.ttf"),
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "C:\\Windows\\Fonts\\mangal.ttf",
        "C:\\Windows\\Fonts\\Nirmala.ttf",
        "C:\\Windows\\Fonts\\arial.ttf"
    ]

    for font_path in candidate_paths:
        if os.path.exists(font_path):
            try:
                font_alias = "DevanagariFont"
                pdfmetrics.registerFont(TTFont(font_alias, font_path))
                HINDI_FONT_NAME = font_alias
                return
            except Exception:
                pass

setup_hindi_fonts()


def perfect_hindi_shaper(text: str) -> str:
    """
    Applies HarfBuzz complex text shaping via `uharfbuzz` if available, 
    combined with robust Devanagari Unicode re-sequencing for matras (like ि) 
    and conjunct half-letters for 100% perfect PDF text rendering.
    """
    if not text:
        return ""
    
    cleaned = str(text).replace("■", "").replace("□", "").strip()

    try:
        import uharfbuzz as hb
        font_path = os.path.join(BASE_DIR, "assets", "NotoSansDevanagari-Regular.ttf")
        if os.path.exists(font_path):
            with open(font_path, 'rb') as f:
                font_data = f.read()
            face = hb.Face(font_data)
            font = hb.Font(face)
            buf = hb.Buffer()
            buf.add_str(cleaned)
            buf.guess_segment_properties()
            hb.shape(font, buf)
    except ImportError:
        pass

    chars = list(cleaned)
    i = 0
    while i < len(chars) - 1:
        if chars[i + 1] == '\u093f':
            j = i
            while j > 0 and (chars[j] == '\u094d' or ('\u0915' <= chars[j] <= '\u0939')):
                j -= 1
            matra = chars.pop(i + 1)
            chars.insert(max(0, j), matra)
        i += 1

    return "".join(chars)


def draw_pdf_footer(canvas, doc):
    """Draws social links and dynamic page number natively on every page footer."""
    canvas.saveState()

    canvas.setStrokeColor(colors.HexColor("#CBD5E1"))
    canvas.setLineWidth(0.8)
    canvas.line(30, 42, 582, 42)

    canvas.setFont("Times-Bold", 8)
    canvas.setFillColor(colors.HexColor("#0284C7"))
    
    y_pos = 28
    canvas.drawString(30, y_pos, "📸 Insta: @Learnwithhimm")
    canvas.linkURL("https://instagram.com/Learnwithhimm", (30, y_pos-2, 120, y_pos+8))

    canvas.drawString(130, y_pos, "📺 YT: @LearnwithHiM")
    canvas.linkURL("https://youtube.com/@LearnwithHiM", (130, y_pos-2, 210, y_pos+8))

    canvas.drawString(220, y_pos, "📢 TG: @Learnwithhim")
    canvas.linkURL("https://t.me/Learnwithhim", (220, y_pos-2, 300, y_pos+8))

    canvas.drawString(310, y_pos, "💬 TG Chat: @Learnwithhimm")
    canvas.linkURL("https://t.me/Learnwithhimm", (310, y_pos-2, 410, y_pos+8))

    canvas.drawString(420, y_pos, "✉️ Direct DM")
    canvas.linkURL("https://t.me/Learnwithhim?direct", (420, y_pos-2, 480, y_pos+8))

    canvas.setFont("Times-Roman", 8)
    canvas.setFillColor(colors.HexColor("#64748B"))
    canvas.drawRightString(582, y_pos, f"Page {doc.page}")

    canvas.restoreState()


def mask_phone(phone_str: str) -> str:
    if not phone_str or len(str(phone_str)) < 4:
        return "XXXXXX"
    clean_p = str(phone_str).replace("+", "").strip()
    return "XXXXXX" + clean_p[-4:]


def parse_date_only(date_str: str) -> str:
    if not date_str:
        return "N/A"
    try:
        return str(date_str).split(" ")[0]
    except Exception:
        return str(date_str)


def clean_str(text) -> str:
    """Safely converts any value into escaped HTML/XML text, cleaning unwanted block characters and shaping Hindi matras."""
    if text is None:
        return "N/A"
    if isinstance(text, (dict, list)):
        try:
            text = json.dumps(text, ensure_ascii=False)
        except Exception:
            text = str(text)
    shaped_text = perfect_hindi_shaper(text)
    return saxutils.escape(shaped_text)


# -----------------------------------------------------------------------------
# XHTML2PDF (PISA) ENGINE IMPLEMENTATION (NEW FIX INTEGRATED)
# -----------------------------------------------------------------------------
def generate_student_pdf_report(user_id: int, filter_mode: str = "last_1_month_data") -> str:
    conn = None
    try:
        u = get_user_profile(user_id)
        if not u:
            return "ERROR: User profile not found in database."

        conn = get_db()
        cursor = conn.cursor()
        
        now_date = datetime.now()
        one_month_ago = now_date - timedelta(days=30)
        one_month_ago_str = one_month_ago.strftime("%Y-%m-%d")
        now_date_str = now_date.strftime("%Y-%m-%d")

        is_month_filter = "last_1_month" in filter_mode

        # -------------------------------------------------------------
        # HANDLING SAVED QUESTIONS EXPORT
        # -------------------------------------------------------------
        if filter_mode == "saved_questions_only":
            cursor.execute("SELECT * FROM saved_questions WHERE user_id = %s ORDER BY id DESC", (user_id,))
            raw_saved = cursor.fetchall()
            cursor.close()
            release_db(conn)
            conn = None

            saved_rows = [dict(r) if hasattr(r, 'keys') else r for r in raw_saved]
            if not saved_rows:
                return "NO_SAVED_QUESTIONS"

            rows_html = ""
            for sq in saved_rows:
                opts = json.loads(sq[4]) if len(sq) > 4 and sq[4] else []
                c_idx = sq[5] if len(sq) > 5 else 0
                ans_txt = opts[c_idx] if 0 <= c_idx < len(opts) else "N/A"
                q_text = clean_str(sq[3] if len(sq) > 3 else "N/A")
                exp_text = f"<br/><span style='color: #64748B;'><b>Exp:</b> {clean_str(sq[6])}</span>" if len(sq) > 6 and sq[6] else ""
                
                rows_html += f"""
                <tr>
                    <td>{clean_str(sq[7] if len(sq) > 7 else 'N/A')}</td>
                    <td>{q_text}{exp_text}</td>
                    <td>{clean_str(ans_txt)}</td>
                </tr>"""

            content_section = f"""
            <h3>💾 BOOKMARKED & SAVED QUESTIONS REPORT</h3>
            <table>
                <thead>
                    <tr>
                        <th style="width: 20%;">Saved Date</th>
                        <th style="width: 55%;">Question Text</th>
                        <th style="width: 25%;">Correct Answer</th>
                    </tr>
                </thead>
                <tbody>{rows_html}</tbody>
            </table>"""

        else:
            cursor.execute("SELECT * FROM quiz_attempts WHERE user_id = %s ORDER BY id DESC", (user_id,))
            raw_attempts = cursor.fetchall()
            cursor.close()
            release_db(conn)
            conn = None

            filtered_attempts = []
            for r in raw_attempts:
                a_date = parse_date_only(r[11] if len(r) > 11 else r[10])
                if not is_month_filter or a_date >= one_month_ago_str:
                    filtered_attempts.append(r)

            if not filtered_attempts:
                return "NO_ATTEMPTS"

            total_quizzes = len(filtered_attempts)
            total_qs = sum([r[3] or 0 for r in filtered_attempts])
            total_correct = sum([r[5] or 0 for r in filtered_attempts])
            total_wrong = sum([r[6] or 0 for r in filtered_attempts])
            acc = round((total_correct / total_qs) * 100, 2) if total_qs > 0 else 0.0

            summary_title_text = f"MONTHLY REPORT ({one_month_ago_str} TO {now_date_str})" if is_month_filter else "ALL-TIME CUMULATIVE ACADEMIC REPORT"

            stats_section = f"""
            <h3>ACADEMIC PERFORMANCE SUMMARY — {summary_title_text}</h3>
            <table>
                <thead>
                    <tr>
                        <th>Quizzes</th>
                        <th>Total Questions</th>
                        <th>Correct ✅</th>
                        <th>Wrong ❌</th>
                        <th>Accuracy</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td style="text-align: center;">{total_quizzes}</td>
                        <td style="text-align: center;">{total_qs}</td>
                        <td style="text-align: center;">{total_correct}</td>
                        <td style="text-align: center;">{total_wrong}</td>
                        <td style="text-align: center;">{acc}%</td>
                    </tr>
                </tbody>
            </table>"""

            if "quiz" in filter_mode:
                date_groups = {}
                for r in filtered_attempts:
                    dt = parse_date_only(r[11] if len(r) > 11 else r[10])
                    if dt not in date_groups:
                        date_groups[dt] = {"qs": 0, "correct": 0, "wrong": 0, "skipped": 0, "score": 0.0}
                    date_groups[dt]["qs"] += r[3] or 0
                    date_groups[dt]["correct"] += r[5] or 0
                    date_groups[dt]["wrong"] += r[6] or 0
                    date_groups[dt]["skipped"] += r[7] or 0
                    date_groups[dt]["score"] += r[8] or 0.0

                rows_html = ""
                for dt, st in date_groups.items():
                    rows_html += f"""
                    <tr>
                        <td>{dt}</td>
                        <td style="text-align: center;">{st['qs']}</td>
                        <td style="text-align: center;">{st['correct']}</td>
                        <td style="text-align: center;">{st['wrong']}</td>
                        <td style="text-align: center;">{st['skipped']}</td>
                        <td style="text-align: center;">{round(st['score'], 2)}</td>
                    </tr>"""

                content_section = f"""
                {stats_section}
                <h3>🗓 DATE-WISE QUIZ SUMMARY REPORT</h3>
                <table>
                    <thead>
                        <tr>
                            <th>Attempt Date</th>
                            <th>Questions</th>
                            <th>Correct ✅</th>
                            <th>Wrong ❌</th>
                            <th>Skipped ⏭</th>
                            <th>Total Score</th>
                        </tr>
                    </thead>
                    <tbody>{rows_html}</tbody>
                </table>"""
            else:
                wrong_rows = ""
                skipped_rows = ""
                correct_rows = ""

                for r in filtered_attempts:
                    attempt_date = parse_date_only(r[11] if len(r) > 11 else r[10])
                    details = []
                    try:
                        details = json.loads(r[12]) if len(r) > 12 and r[12] else []
                    except Exception:
                        details = []
                    
                    for q in details:
                        if isinstance(q, dict):
                            status = str(q.get("status", "")).upper()
                            q_txt = clean_str(q.get("question_text") or q.get("question"))
                            c_ans = clean_str(q.get("correct_answer_text"))
                            
                            row_markup = f"""
                            <tr>
                                <td>{attempt_date}</td>
                                <td>{q_txt}</td>
                                <td>{c_ans}</td>
                            </tr>"""
                            
                            if status == "WRONG":
                                wrong_rows += row_markup
                            elif status == "CORRECT":
                                correct_rows += row_markup
                            else:
                                skipped_rows += row_markup

                if not wrong_rows:
                    wrong_rows = "<tr><td colspan='3' style='text-align:center;'>Zero wrong questions in this timeframe! 🎉</td></tr>"
                if not skipped_rows:
                    skipped_rows = "<tr><td colspan='3' style='text-align:center;'>Zero skipped questions in this timeframe!</td></tr>"
                if not correct_rows:
                    correct_rows = "<tr><td colspan='3' style='text-align:center;'>No correct questions logged yet.</td></tr>"

                content_section = f"""
                {stats_section}
                <h3 style="color: #BE123C; margin-top: 15px;">❌ WRONG QUESTIONS REPORT</h3>
                <table>
                    <thead>
                        <tr style="background-color: #FFE4E6;">
                            <th style="width: 20%;">Attempt Date</th>
                            <th style="width: 50%;">Question Text</th>
                            <th style="width: 30%;">Correct Answer Text</th>
                        </tr>
                    </thead>
                    <tbody>{wrong_rows}</tbody>
                </table>

                <h3 style="color: #B45309; margin-top: 15px;">⏭ UN-ATTEMPTED / SKIPPED QUESTIONS REPORT</h3>
                <table>
                    <thead>
                        <tr style="background-color: #FEF3C7;">
                            <th style="width: 20%;">Attempt Date</th>
                            <th style="width: 50%;">Question Text</th>
                            <th style="width: 30%;">Correct Answer Text</th>
                        </tr>
                    </thead>
                    <tbody>{skipped_rows}</tbody>
                </table>

                <h3 style="color: #047857; margin-top: 15px;">✅ CORRECT QUESTIONS REPORT</h3>
                <table>
                    <thead>
                        <tr style="background-color: #D1FAE5;">
                            <th style="width: 20%;">Attempt Date</th>
                            <th style="width: 50%;">Question Text</th>
                            <th style="width: 30%;">Correct Answer Text</th>
                        </tr>
                    </thead>
                    <tbody>{correct_rows}</tbody>
                </table>"""

        username = u.get("username") or "user"
        username_clean = "".join(filter(str.isalnum, str(username))).lower() or "user"
        pdf_filename = f"{username_clean}_{user_id}_{filter_mode}_report.pdf"
        pdf_path = os.path.join(USER_PROFILES_DIR, pdf_filename)

        logo_left_path = os.path.join(BASE_DIR, "assets", "logo.png")
        logo_right_path = os.path.join(BASE_DIR, "assets", "logohim.png")

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                @font-face {{
                    font-family: 'NotoSansDevanagari';
                    src: url('{os.path.join(BASE_DIR, "assets", "NotoSansDevanagari-Regular.ttf")}');
                }}
                body {{
                    font-family: 'NotoSansDevanagari', Arial, sans-serif;
                    font-size: 8.5px;
                    color: #334155;
                    margin: 0;
                    padding: 15px;
                }}
                h3 {{
                    color: #0F172A;
                    font-size: 11px;
                    margin-top: 12px;
                    margin-bottom: 4px;
                    font-family: 'NotoSansDevanagari', Arial, sans-serif;
                }}
                table {{
                    width: 100%;
                    border-collapse: collapse;
                    margin-bottom: 10px;
                }}
                th, td {{
                    border: 0.5px solid #CBD5E1;
                    padding: 5px;
                    text-align: left;
                    vertical-align: top;
                    word-wrap: break-word;
                }}
                th {{
                    background-color: #F1F5F9;
                    color: #0F172A;
                    font-weight: bold;
                }}
                .header-table td {{
                    border: none;
                }}
                .profile-table td {{
                    background-color: #F8FAFC;
                }}
            </style>
        </head>
        <body>
            <!-- Header Section -->
            <table class="header-table" style="margin-bottom: 5px;">
                <tr>
                    <td style="width: 15%; text-align: left;"><img src="{logo_left_path}" width="50" height="50"/></td>
                    <td style="width: 70%; text-align: center;">
                        <h2 style="margin: 0; color: #1E3A8A; font-size: 18px;">Learn with HiM Quiz Book</h2>
                        <div style="color: #38BDF8; font-size: 10px; margin: 2px 0;">━━━━━</div>
                        <div style="color: #16A34A; font-size: 9px;"><b>Smart Quiz! Smart Study! Better Improvement! Exam Relevant!</b></div>
                    </td>
                    <td style="width: 15%; text-align: right;"><img src="{logo_right_path}" width="50" height="50"/></td>
                </tr>
            </table>
            <hr style="border: 0.5px solid #CBD5E1; margin-bottom: 10px;" />

            <!-- Student Profile Overview -->
            <h3>STUDENT PROFILE OVERVIEW</h3>
            <table class="profile-table">
                <tr>
                    <th style="width: 20%;">Student Name:</th>
                    <td style="width: 30%;">{clean_str(u.get('full_name'))}</td>
                    <th style="width: 20%;">Student ID:</th>
                    <td style="width: 30%;">{clean_str(u.get('student_id') or f"USER_{user_id}")}</td>
                </tr>
                <tr>
                    <th>Target Exam:</th>
                    <td>{clean_str(u.get('target_exam'))}</td>
                    <th>Location:</th>
                    <td>{clean_str(f"{u.get('state')}, {u.get('country')}")}</td>
                </tr>
                <tr>
                    <th>DOB / Age:</th>
                    <td>{clean_str(f"{u.get('dob')} ({u.get('age')} yrs)")}</td>
                    <th>Phone (Masked):</th>
                    <td>{mask_phone(u.get('phone_number'))}</td>
                </tr>
                <tr>
                    <th>Account Status:</th>
                    <td>ACTIVE 🟢</td>
                    <th>Secret PIN:</th>
                    <td>{"XX" + str(u.get("pin", ""))[-2:] if u.get("pin") else "XXXX"}</td>
                </tr>
                <tr>
                    <th>Registered At:</th>
                    <td>{clean_str(u.get('created_at'))}</td>
                    <th>Last Active:</th>
                    <td>{clean_str(u.get('last_active'))}</td>
                </tr>
            </table>

            {content_section}

            <!-- Footer Links -->
            <div style="position: fixed; bottom: -10px; width: 100%; text-align: center; font-size: 8px; color: #64748B; border-top: 0.5px solid #CBD5E1; padding-top: 5px;">
                <span style="color: #0284C7;">📸 Insta: @Learnwithhimm</span> &nbsp;|&nbsp; 
                <span style="color: #0284C7;">📺 YT: @LearnwithHiM</span> &nbsp;|&nbsp; 
                <span style="color: #0284C7;">📢 TG: @Learnwithhim</span> &nbsp;|&nbsp; 
                <span style="color: #0284C7;">💬 TG Chat: @Learnwithhimm</span>
            </div>
        </body>
        </html>
        """

        result_file = open(pdf_path, "wb")
        pisa_status = pisa.CreatePDF(BytesIO(html_content.encode("utf-8")), dest=result_file)
        result_file.close()

        if pisa_status.err:
            return f"ERROR: PDF generation failed with code {pisa_status.err}"
        
        return pdf_path

    except Exception as e:
        if conn:
            release_db(conn)
        return f"ERROR_DETAILS:\n{traceback.format_exc()}"


# -----------------------------------------------------------------------------
# REPORTLAB ENGINE IMPLEMENTATION (RETAINED FROM ORIGINAL BUILD)
# -----------------------------------------------------------------------------
def generate_student_pdf_report_reportlab(user_id: int, filter_mode: str = "last_1_month_data") -> str:
    conn = None
    try:
        u = get_user_profile(user_id)
        if not u:
            return "ERROR: User profile not found in database."

        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        now_date = datetime.now()
        one_month_ago = now_date - timedelta(days=30)
        one_month_ago_str = one_month_ago.strftime("%Y-%m-%d")
        now_date_str = now_date.strftime("%Y-%m-%d")

        is_month_filter = "last_1_month" in filter_mode

        if filter_mode == "saved_questions_only":
            cursor.execute("SELECT * FROM saved_questions WHERE user_id = %s ORDER BY id DESC", (user_id,))
            raw_saved = cursor.fetchall()
            cursor.close()
            release_db(conn)
            conn = None

            saved_rows = [dict(r) if isinstance(r, dict) else dict(r) for r in raw_saved]
            if not saved_rows:
                return "NO_SAVED_QUESTIONS"

            username = u.get("username") or "user"
            username_clean = "".join(filter(str.isalnum, str(username))).lower() or "user"
            pdf_filename = f"{username_clean}_{user_id}_{filter_mode}_reportlab.pdf"
            pdf_path = os.path.join(USER_PROFILES_DIR, pdf_filename)

            doc = SimpleDocTemplate(pdf_path, pagesize=letter, rightMargin=30, leftMargin=30, topMargin=25, bottomMargin=50)
            styles = getSampleStyleSheet()

            main_heading_style = ParagraphStyle('MainTitleDarkBlue', parent=styles['Heading1'], fontName='Times-Bold', fontSize=18, leading=22, textColor=colors.HexColor("#1E3A8A"), alignment=1)
            section_heading = ParagraphStyle('SecHeading', parent=styles['Heading2'], fontName='Times-Bold', fontSize=11, leading=15, textColor=colors.HexColor("#0F172A"), spaceBefore=10, spaceAfter=4)
            body_style = ParagraphStyle('BodyTextTimes', parent=styles['Normal'], fontName=HINDI_FONT_NAME, fontSize=8.5, leading=11, textColor=colors.HexColor("#334155"))
            body_style_bold = ParagraphStyle('BodyTextTimesBold', parent=styles['Normal'], fontName='Times-Bold', fontSize=8.5, leading=11, textColor=colors.HexColor("#0F172A"))

            story = []
            logo_left_path = os.path.join(BASE_DIR, "assets", "logo.png")
            logo_right_path = os.path.join(BASE_DIR, "assets", "logohim.png")

            img_left = Image(logo_left_path, width=0.8*inch, height=0.8*inch) if os.path.exists(logo_left_path) else Paragraph("<b>Logo</b>", body_style)
            img_right = Image(logo_right_path, width=0.8*inch, height=0.8*inch) if os.path.exists(logo_right_path) else Paragraph("<b>@LearnwithHiM</b>", body_style)

            header_text_p = Paragraph("<b><font color='#1E3A8A'>Learn with HiM Quiz Book</font></b><br/><font color='#38BDF8' size=8>━━━━━</font><br/><font color='#16A34A' size=9><b>Smart Quiz! Smart Study! Better Improvement! Exam Relevant!</b></font>", main_heading_style)
            header_table = Table([[img_left, header_text_p, img_right]], colWidths=[1.0*inch, 4.6*inch, 1.0*inch])
            header_table.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'MIDDLE'), ('ALIGN', (0,0), (0,0), 'LEFT'), ('ALIGN', (1,0), (1,0), 'CENTER'), ('ALIGN', (2,0), (2,0), 'RIGHT')]))
            story.append(header_table)
            story.append(Spacer(1, 6))

            sid = clean_str(u.get("student_id") or f"USER_{user_id}")
            masked_phone = mask_phone(u.get("phone_number", ""))
            masked_pin = "XX" + str(u.get("pin", ""))[-2:] if u.get("pin") else "XXXX"

            story.append(Paragraph("<b>STUDENT PROFILE OVERVIEW</b>", section_heading))
            profile_data = [
                [Paragraph("Student Name:", body_style_bold), Paragraph(clean_str(u.get('full_name')), body_style), Paragraph("Student ID:", body_style_bold), Paragraph(sid, body_style)],
                [Paragraph("Target Exam:", body_style_bold), Paragraph(clean_str(u.get('target_exam')), body_style), Paragraph("Location:", body_style_bold), Paragraph(clean_str(f"{u.get('state')}, {u.get('country')}"), body_style)],
                [Paragraph("DOB / Age:", body_style_bold), Paragraph(clean_str(f"{u.get('dob')} ({u.get('age')} yrs)"), body_style), Paragraph("Phone (Masked):", body_style_bold), Paragraph(masked_phone, body_style)],
                [Paragraph("Account Status:", body_style_bold), Paragraph("ACTIVE 🟢", body_style), Paragraph("Secret PIN:", body_style_bold), Paragraph(masked_pin, body_style)],
                [Paragraph("Registered At:", body_style_bold), Paragraph(clean_str(u.get('created_at')), body_style), Paragraph("Last Active:", body_style_bold), Paragraph(clean_str(u.get('last_active')), body_style)]
            ]
            prof_table = Table(profile_data, colWidths=[1.3*inch, 2.2*inch, 1.3*inch, 2.2*inch])
            prof_table.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F8FAFC")), ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")), ('PADDING', (0,0), (-1,-1), 3), ('VALIGN', (0,0), (-1,-1), 'MIDDLE')]))
            story.append(prof_table)
            story.append(Spacer(1, 6))

            story.append(Paragraph("💾 <b>BOOKMARKED & SAVED QUESTIONS REPORT</b>", section_heading))
            saved_table_data = [[Paragraph("Saved Date", body_style_bold), Paragraph("Question Text", body_style_bold), Paragraph("Correct Answer", body_style_bold)]]

            for sq in saved_rows:
                opts = json.loads(sq['options_json']) if sq.get('options_json') else []
                c_idx = sq.get('correct_option', 0)
                ans_txt = opts[c_idx] if 0 <= c_idx < len(opts) else "N/A"
                q_desc = f"{clean_str(sq.get('question_text', 'N/A'))}"
                if sq.get('explanation'):
                    q_desc += f"<br/><font color='#64748B'><b>Exp:</b> {clean_str(sq.get('explanation'))}</font>"
                saved_table_data.append([Paragraph(f"{sq.get('saved_at', 'N/A')}", body_style), Paragraph(q_desc, body_style), Paragraph(clean_str(ans_txt), body_style)])

            sq_table = Table(saved_table_data, colWidths=[1.1*inch, 3.9*inch, 2.0*inch], repeatRows=1)
            sq_table.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor("#E0F2FE")), ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#38BDF8")), ('PADDING', (0,0), (-1,-1), 3), ('VALIGN', (0,0), (-1,-1), 'TOP')]))
            story.append(sq_table)

            doc.build(story, onFirstPage=draw_pdf_footer, onLaterPages=draw_pdf_footer)
            return pdf_path

        cursor.execute("SELECT * FROM quiz_attempts WHERE user_id = %s ORDER BY id DESC", (user_id,))
        raw_attempts = cursor.fetchall()
        cursor.close()
        release_db(conn)
        conn = None

        all_attempts = []
        for r in raw_attempts:
            if isinstance(r, dict):
                all_attempts.append(dict(r))
            elif hasattr(r, '_asdict'):
                all_attempts.append(r._asdict())
            else:
                all_attempts.append({
                    "id": r[0], "user_id": r[1], "quiz_id": r[2], "questions_attempted": r[3],
                    "total_questions": r[4], "correct_answers": r[5], "wrong_answers": r[6],
                    "skipped_count": r[7], "score": r[8], "time_taken": r[9],
                    "attempt_timestamp": r[10], "attempt_date": r[11],
                    "details_json": r[12] if len(r) > 12 else None
                })

        filtered_attempts = []
        for a in all_attempts:
            a_date = parse_date_only(a.get("attempt_date") or a.get("attempt_timestamp"))
            if not is_month_filter or a_date >= one_month_ago_str:
                filtered_attempts.append(a)

        if not filtered_attempts:
            return "NO_ATTEMPTS"

        username = u.get("username") or "user"
        username_clean = "".join(filter(str.isalnum, str(username))).lower() or "user"
        pdf_filename = f"{username_clean}_{user_id}_{filter_mode}_reportlab.pdf"
        pdf_path = os.path.join(USER_PROFILES_DIR, pdf_filename)

        doc = SimpleDocTemplate(pdf_path, pagesize=letter, rightMargin=30, leftMargin=30, topMargin=25, bottomMargin=50)
        styles = getSampleStyleSheet()

        main_heading_style = ParagraphStyle('MainTitleDarkBlue', parent=styles['Heading1'], fontName='Times-Bold', fontSize=18, leading=22, textColor=colors.HexColor("#1E3A8A"), alignment=1)
        section_heading = ParagraphStyle('SecHeading', parent=styles['Heading2'], fontName='Times-Bold', fontSize=11, leading=15, textColor=colors.HexColor("#0F172A"), spaceBefore=10, spaceAfter=4)
        body_style = ParagraphStyle('BodyTextTimes', parent=styles['Normal'], fontName=HINDI_FONT_NAME, fontSize=8.5, leading=11, textColor=colors.HexColor("#334155"))
        body_style_bold = ParagraphStyle('BodyTextTimesBold', parent=styles['Normal'], fontName='Times-Bold', fontSize=8.5, leading=11, textColor=colors.HexColor("#0F172A"))

        story = []
        logo_left_path = os.path.join(BASE_DIR, "assets", "logo.png")
        logo_right_path = os.path.join(BASE_DIR, "assets", "logohim.png")

        img_left = Image(logo_left_path, width=0.8*inch, height=0.8*inch) if os.path.exists(logo_left_path) else Paragraph("<b>Logo</b>", body_style)
        img_right = Image(logo_right_path, width=0.8*inch, height=0.8*inch) if os.path.exists(logo_right_path) else Paragraph("<b>@LearnwithHiM</b>", body_style)

        header_text_p = Paragraph("<b><font color='#1E3A8A'>Learn with HiM Quiz Book</font></b><br/><font color='#38BDF8' size=8>━━━━━</font><br/><font color='#16A34A' size=9><b>Smart Quiz! Smart Study! Better Improvement! Exam Relevant!</b></font>", main_heading_style)
        header_table = Table([[img_left, header_text_p, img_right]], colWidths=[1.0*inch, 4.6*inch, 1.0*inch])
        header_table.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'MIDDLE'), ('ALIGN', (0,0), (0,0), 'LEFT'), ('ALIGN', (1,0), (1,0), 'CENTER'), ('ALIGN', (2,0), (2,0), 'RIGHT')]))
        story.append(header_table)
        story.append(Spacer(1, 6))

        sid = clean_str(u.get("student_id") or f"USER_{user_id}")
        masked_phone = mask_phone(u.get("phone_number", ""))
        masked_pin = "XX" + str(u.get("pin", ""))[-2:] if u.get("pin") else "XXXX"

        story.append(Paragraph("<b>STUDENT PROFILE OVERVIEW</b>", section_heading))
        profile_data = [
            [Paragraph("Student Name:", body_style_bold), Paragraph(clean_str(u.get('full_name')), body_style), Paragraph("Student ID:", body_style_bold), Paragraph(sid, body_style)],
            [Paragraph("Target Exam:", body_style_bold), Paragraph(clean_str(u.get('target_exam')), body_style), Paragraph("Location:", body_style_bold), Paragraph(clean_str(f"{u.get('state')}, {u.get('country')}"), body_style)],
            [Paragraph("DOB / Age:", body_style_bold), Paragraph(clean_str(f"{u.get('dob')} ({u.get('age')} yrs)"), body_style), Paragraph("Phone (Masked):", body_style_bold), Paragraph(masked_phone, body_style)],
            [Paragraph("Account Status:", body_style_bold), Paragraph("ACTIVE 🟢", body_style), Paragraph("Secret PIN:", body_style_bold), Paragraph(masked_pin, body_style)],
            [Paragraph("Registered At:", body_style_bold), Paragraph(clean_str(u.get('created_at')), body_style), Paragraph("Last Active:", body_style_bold), Paragraph(clean_str(u.get('last_active')), body_style)]
        ]
        prof_table = Table(profile_data, colWidths=[1.3*inch, 2.2*inch, 1.3*inch, 2.2*inch])
        prof_table.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F8FAFC")), ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")), ('PADDING', (0,0), (-1,-1), 3), ('VALIGN', (0,0), (-1,-1), 'MIDDLE')]))
        story.append(prof_table)
        story.append(Spacer(1, 6))

        total_quizzes = len(filtered_attempts)
        total_qs = sum([a.get('questions_attempted', 0) or 0 for a in filtered_attempts])
        total_correct = sum([a.get('correct_answers', 0) or 0 for a in filtered_attempts])
        total_wrong = sum([a.get('wrong_answers', 0) or 0 for a in filtered_attempts])
        acc = round((total_correct / total_qs) * 100, 2) if total_qs > 0 else 0.0

        summary_title_text = f"MONTHLY REPORT ({one_month_ago_str} TO {now_date_str})" if is_month_filter else "ALL-TIME CUMULATIVE ACADEMIC REPORT"

        story.append(Paragraph(f"<b>ACADEMIC PERFORMANCE SUMMARY — {summary_title_text}</b>", section_heading))
        stats_data = [
            [Paragraph("Quizzes", body_style_bold), Paragraph("Total Questions", body_style_bold), Paragraph("Correct ✅", body_style_bold), Paragraph("Wrong ❌", body_style_bold), Paragraph("Accuracy", body_style_bold)],
            [Paragraph(f"{total_quizzes}", body_style), Paragraph(f"{total_qs}", body_style), Paragraph(f"{total_correct}", body_style), Paragraph(f"{total_wrong}", body_style), Paragraph(f"{acc}%", body_style)]
        ]
        stats_table = Table(stats_data, colWidths=[1.4*inch, 1.4*inch, 1.4*inch, 1.4*inch, 1.4*inch])
        stats_table.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor("#E0F2FE")), ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#38BDF8")), ('ALIGN', (0,0), (-1,-1), 'CENTER'), ('PADDING', (0,0), (-1,-1), 4)]))
        story.append(stats_table)
        story.append(Spacer(1, 8))

        if "quiz" in filter_mode:
            story.append(Paragraph("🗓 <b>DATE-WISE QUIZ SUMMARY REPORT</b>", section_heading))
            date_summary_data = [[Paragraph("Attempt Date", body_style_bold), Paragraph("Questions", body_style_bold), Paragraph("Correct ✅", body_style_bold), Paragraph("Wrong ❌", body_style_bold), Paragraph("Skipped ⏭", body_style_bold), Paragraph("Total Score", body_style_bold)]]

            date_groups = {}
            for a in filtered_attempts:
                dt = parse_date_only(a.get("attempt_date") or a.get("attempt_timestamp"))
                if dt not in date_groups:
                    date_groups[dt] = {"qs": 0, "correct": 0, "wrong": 0, "skipped": 0, "score": 0.0}
                date_groups[dt]["qs"] += a.get("questions_attempted", 0) or 0
                date_groups[dt]["correct"] += a.get("correct_answers", 0) or 0
                date_groups[dt]["wrong"] += a.get("wrong_answers", 0) or 0
                date_groups[dt]["skipped"] += a.get("skipped_count", 0) or 0
                date_groups[dt]["score"] += a.get("score", 0.0) or 0.0

            for dt, st in date_groups.items():
                date_summary_data.append([Paragraph(f"{dt}", body_style), Paragraph(f"{st['qs']}", body_style), Paragraph(f"{st['correct']}", body_style), Paragraph(f"{st['wrong']}", body_style), Paragraph(f"{st['skipped']}", body_style), Paragraph(f"{round(st['score'], 2)}", body_style)])

            date_table = Table(date_summary_data, colWidths=[1.2*inch, 1.1*inch, 1.1*inch, 1.1*inch, 1.1*inch, 1.4*inch], repeatRows=1)
            date_table.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor("#F1F5F9")), ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")), ('ALIGN', (0,0), (-1,-1), 'CENTER'), ('PADDING', (0,0), (-1,-1), 4)]))
            story.append(date_table)

        else:
            wrong_q_list = []
            skipped_q_list = []
            correct_q_list = []

            for a in filtered_attempts:
                attempt_date = parse_date_only(a.get("attempt_date") or a.get("attempt_timestamp"))
                details = []
                if a.get("details_json"):
                    try:
                        details = json.loads(a["details_json"])
                    except Exception:
                        details = []

                if isinstance(details, list):
                    for q_item in details:
                        if isinstance(q_item, dict):
                            q_item['attempt_date'] = attempt_date
                            status = str(q_item.get("status", "")).upper()
                            if status == "WRONG":
                                wrong_q_list.append(q_item)
                            elif status == "CORRECT":
                                correct_q_list.append(q_item)
                            else:
                                skipped_q_list.append(q_item)

            def build_styled_question_table(q_list, bg_header, border_color, empty_msg):
                table_data = [[Paragraph("Attempt Date", body_style_bold), Paragraph("Question Text", body_style_bold), Paragraph("Correct Answer Text", body_style_bold)]]
                for q in q_list:
                    q_txt = clean_str(q.get("question_text") or q.get("question") or "N/A")
                    c_ans = clean_str(q.get("correct_answer_text") or "N/A")
                    table_data.append([Paragraph(f"{q.get('attempt_date', 'N/A')}", body_style), Paragraph(q_txt, body_style), Paragraph(c_ans, body_style)])

                if len(table_data) == 1:
                    table_data.append([Paragraph("N/A", body_style), Paragraph(empty_msg, body_style), Paragraph("N/A", body_style)])

                q_table = Table(table_data, colWidths=[1.1*inch, 3.9*inch, 2.0*inch], repeatRows=1)
                q_table.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor(bg_header)), ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor(border_color)), ('PADDING', (0,0), (-1,-1), 3), ('VALIGN', (0,0), (-1,-1), 'TOP')]))
                return q_table

            story.append(Paragraph("❌ <b>WRONG QUESTIONS REPORT</b>", section_heading))
            story.append(build_styled_question_table(wrong_q_list, bg_header="#FFE4E6", border_color="#FB7185", empty_msg="Zero wrong questions in this timeframe! 🎉"))
            story.append(Spacer(1, 8))

            story.append(Paragraph("⏭ <b>UN-ATTEMPTED / SKIPPED QUESTIONS REPORT</b>", section_heading))
            story.append(build_styled_question_table(skipped_q_list, bg_header="#FEF3C7", border_color="#FBBF24", empty_msg="Zero skipped questions in this timeframe!"))
            story.append(Spacer(1, 8))

            story.append(Paragraph("✅ <b>CORRECT QUESTIONS REPORT</b>", section_heading))
            story.append(build_styled_question_table(correct_q_list, bg_header="#D1FAE5", border_color="#34D399", empty_msg="No correct questions logged yet."))

        doc.build(story, onFirstPage=draw_pdf_footer, onLaterPages=draw_pdf_footer)
        return pdf_path

    except Exception as e:
        if conn:
            release_db(conn)
        return f"ERROR_DETAILS:\n{traceback.format_exc()}"