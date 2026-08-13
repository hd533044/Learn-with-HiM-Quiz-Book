import os
import json
import traceback
import xml.sax.saxutils as saxutils
import urllib.request
import unicodedata
from datetime import datetime, timedelta
import asyncio
import concurrent.futures
import psycopg2
from psycopg2.extras import RealDictCursor

from app.config import USER_PROFILES_DIR, BASE_DIR
from app.database import get_user_profile, get_db, release_db
from app.stats import calculate_user_rank, calculate_user_percentile

# Attempt WeasyPrint Import
HAS_WEASYPRINT = False
try:
    import weasyprint
    HAS_WEASYPRINT = True
except ImportError:
    HAS_WEASYPRINT = False

# Attempt Playwright Import
HAS_PLAYWRIGHT = False
try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

# ReportLab Fallback Imports for Guaranteed PDF Generation
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


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
    if text is None:
        return "N/A"
    if isinstance(text, (dict, list)):
        try:
            text = json.dumps(text, ensure_ascii=False)
        except Exception:
            text = str(text)
    
    val_str = str(text).strip()
    if not val_str:
        return "N/A"
    
    normalized_str = unicodedata.normalize('NFC', val_str)
    cleaned_spacing = " ".join(normalized_str.split())
    return saxutils.escape(cleaned_spacing)


def generate_html_report(user_profile: dict, attempts: list, saved_qs: list, rank: str, percentile: float, filter_mode: str) -> str:
    now_date = datetime.now()
    one_month_ago = now_date - timedelta(days=30)
    one_month_ago_str = one_month_ago.strftime("%Y-%m-%d")
    now_date_str = now_date.strftime("%Y-%m-%d")
    is_month_filter = "last_1_month" in filter_mode

    sid = clean_str(user_profile.get("student_id") or f"USER_{user_profile.get('user_id')}")
    masked_phone = mask_phone(user_profile.get("phone_number", ""))
    
    raw_pin = user_profile.get("pin")
    masked_pin = "XX" + str(raw_pin)[-2:] if raw_pin else "XXXX"
    
    full_name_clean = clean_str(user_profile.get('full_name'))
    target_exam_clean = clean_str(user_profile.get('target_exam'))
    
    state_val = user_profile.get('state', '')
    country_val = user_profile.get('country', '')
    location_clean = clean_str(f"{state_val}, {country_val}")
    
    dob_val = user_profile.get('dob', '')
    age_val = user_profile.get('age', '')
    dob_age_clean = clean_str(f"{dob_val} ({age_val} yrs)")
    
    logo_left_path = os.path.abspath(os.path.join(BASE_DIR, "assets", "logo.png"))
    logo_right_path = os.path.abspath(os.path.join(BASE_DIR, "assets", "logohim.png"))

    left_logo_html = f'<img src="file://{logo_left_path}" style="width: 55px; height: 55px; object-fit: contain;" />' if os.path.exists(logo_left_path) else '<b>Logo</b>'
    right_logo_html = f'<img src="file://{logo_right_path}" style="width: 55px; height: 55px; object-fit: contain;" />' if os.path.exists(logo_right_path) else '<b>@LearnwithHiM</b>'

    filtered_attempts = []
    for a in attempts:
        a_date = parse_date_only(a.get("attempt_date") or a.get("attempt_timestamp"))
        if is_month_filter:
            if a_date >= one_month_ago_str:
                filtered_attempts.append(a)
        else:
            filtered_attempts.append(a)

    total_quizzes = len(filtered_attempts)
    total_qs = sum([a.get('questions_attempted', 0) or 0 for a in filtered_attempts])
    total_correct = sum([a.get('correct_answers', 0) or 0 for a in filtered_attempts])
    total_wrong = sum([a.get('wrong_answers', 0) or 0 for a in filtered_attempts])
    acc = round((total_correct / total_qs) * 100, 2) if total_qs > 0 else 0.0

    summary_title = f"MONTHLY REPORT ({one_month_ago_str} TO {now_date_str})" if is_month_filter else "ALL-TIME CUMULATIVE ACADEMIC REPORT"

    html_lines = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        "<meta charset='utf-8'/>",
        "<style>",
        "@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+Devanagari:wght@400;600;700&display=swap');",
        "@page { size: letter; margin: 20mm 15mm 22mm 15mm; @bottom-right { content: 'Page ' counter(page); font-size: 8.5px; font-family: 'Times New Roman', serif; color: #64748B; } @bottom-left { content: '📸 Insta: @Learnwithhimm  |  📺 YT: @LearnwithHiM  |  📢 TG: @Learnwithhim  |  💬 TG Chat: @Learnwithhimm'; font-size: 8px; font-family: 'Times New Roman', serif; color: #0284C7; font-weight: bold; } }",
        "body { font-family: 'Noto Sans Devanagari', 'Times New Roman', Helvetica, Arial, sans-serif; margin: 0; padding: 0; color: #334155; font-size: 11.5px; line-height: 1.45; direction: ltr; background-color: #ffffff; }",
        ".header-table { width: 100%; border-collapse: collapse; margin-bottom: 12px; }",
        ".header-title { text-align: center; color: #1E3A8A; font-size: 20px; font-weight: bold; font-family: 'Times New Roman', serif; }",
        ".sub-title { color: #16A34A; font-size: 11px; text-align: center; font-weight: bold; margin-top: 3px; font-family: 'Times New Roman', serif; }",
        "h3 { font-size: 11px; color: #0F172A; text-transform: uppercase; margin-top: 15px; margin-bottom: 6px; font-weight: bold; font-family: 'Times New Roman', serif; page-break-after: avoid; }",
        ".data-table { width: 100%; border-collapse: collapse; margin-bottom: 12px; page-break-inside: auto; }",
        ".data-table tr { page-break-inside: avoid; page-break-after: auto; }",
        ".data-table th, .data-table td { border: 0.5px solid #CBD5E1; padding: 5px 7px; text-align: left; vertical-align: top; font-size: 10px; word-break: break-word; }",
        ".data-table th { background-color: #E0F2FE; color: #0F172A; font-weight: bold; font-family: 'Times New Roman', serif; }",
        ".prof-table td { background-color: #F8FAFC; }",
        ".prof-label { font-weight: bold; color: #0F172A; width: 18%; font-family: 'Times New Roman', serif; }",
        ".wrong-header { background-color: #FFE4E6 !important; color: #9F1239 !important; border-color: #FB7185 !important; }",
        ".skipped-header { background-color: #FEF3C7 !important; color: #92400E !important; border-color: #FBBF24 !important; }",
        ".correct-header { background-color: #D1FAE5 !important; color: #065F46 !important; border-color: #34D399 !important; }",
        ".watermark-container { position: fixed; top: 0; left: 0; width: 100%; height: 100%; z-index: -1000; overflow: hidden; pointer-events: none; }",
        ".wm-text { position: absolute; font-family: 'Times New Roman', serif; font-weight: bold; font-size: 24px; color: #94A3B8; opacity: 0.14; transform: rotate(30deg); white-space: nowrap; }",
        "</style>",
        "</head>",
        "<body>",
        "<div class='watermark-container'>",
        "<div class='wm-text' style='top: 10%; left: 10%;'>Learn with HiM</div>",
        "<div class='wm-text' style='top: 12%; left: 60%;'>Quiz with HiM</div>",
        "<div class='wm-text' style='top: 30%; left: 20%;'>Quiz with HiM</div>",
        "<div class='wm-text' style='top: 35%; left: 65%;'>Learn with HiM</div>",
        "<div class='wm-text' style='top: 52%; left: 10%;'>Learn with HiM</div>",
        "<div class='wm-text' style='top: 55%; left: 58%;'>Quiz with HiM</div>",
        "<div class='wm-text' style='top: 72%; left: 25%;'>Quiz with HiM</div>",
        "<div class='wm-text' style='top: 76%; left: 70%;'>Learn with HiM</div>",
        "<div class='wm-text' style='top: 90%; left: 15%;'>Learn with HiM</div>",
        "<div class='wm-text' style='top: 92%; left: 60%;'>Quiz with HiM</div>",
        "</div>",
        
        "<table class='header-table'>",
        "<tr>",
        f"<td style='width: 15%; text-align: left;'>{left_logo_html}</td>",
        "<td class='header-title'>Learn with HiM Quiz Book<div class='sub-title'>Smart Quiz! Smart Study! Better Improvement! Exam Relevant!</div></td>",
        f"<td style='width: 15%; text-align: right;'>{right_logo_html}</td>",
        "</tr>",
        "</table>",

        "<h3>STUDENT PROFILE OVERVIEW</h3>",
        "<table class='data-table prof-table'>",
        f"<tr><td class='prof-label'>Student Name:</td><td>{full_name_clean}</td><td class='prof-label'>Student ID:</td><td>{sid}</td></tr>",
        f"<tr><td class='prof-label'>Target Exam:</td><td>{target_exam_clean}</td><td class='prof-label'>Location:</td><td>{location_clean}</td></tr>",
        f"<tr><td class='prof-label'>DOB / Age:</td><td>{dob_age_clean}</td><td class='prof-label'>Phone (Masked):</td><td>{masked_phone}</td></tr>",
        f"<tr><td class='prof-label'>Account Status:</td><td>ACTIVE 🟢</td><td class='prof-label'>Secret PIN:</td><td>{masked_pin}</td></tr>",
        f"<tr><td class='prof-label'>Global Rank:</td><td>{clean_str(rank)}</td><td class='prof-label'>Overall Percentile:</td><td>{percentile}%</td></tr>",
        "</table>"
    ]

    if filter_mode == "saved_questions_only":
        html_lines.append("<h3>💾 BOOKMARKED & SAVED QUESTIONS REPORT</h3>")
        html_lines.append("<table class='data-table'>")
        html_lines.append("<tr><th style='width: 18%;'>Saved Date</th><th style='width: 54%;'>Question Text</th><th style='width: 28%;'>Correct Answer</th></tr>")
        
        for sq in saved_qs:
            opts = json.loads(sq['options_json']) if sq.get('options_json') else []
            c_idx = sq.get('correct_option', 0)
            ans_txt = opts[c_idx] if 0 <= c_idx < len(opts) else "N/A"
            q_desc = clean_str(sq.get('question_text', 'N/A'))
            if sq.get('explanation'):
                q_desc += f"<br/><small style='color: #64748B;'><b>Exp:</b> {clean_str(sq.get('explanation'))}</small>"
            
            html_lines.append(f"<tr><td>{sq.get('saved_at', 'N/A')}</td><td>{q_desc}</td><td>{clean_str(ans_txt)}</td></tr>")
        
        html_lines.append("</table>")
    else:
        html_lines.append(f"<h3>ACADEMIC PERFORMANCE SUMMARY — {summary_title}</h3>")
        html_lines.append("<table class='data-table'>")
        html_lines.append("<tr><th style='text-align:center;'>Quizzes</th><th style='text-align:center;'>Total Questions</th><th style='text-align:center;'>Correct ✅</th><th style='text-align:center;'>Wrong ❌</th><th style='text-align:center;'>Accuracy</th></tr>")
        html_lines.append(f"<tr><td style='text-align:center;'>{total_quizzes}</td><td style='text-align:center;'>{total_qs}</td><td style='text-align:center;'>{total_correct}</td><td style='text-align:center;'>{total_wrong}</td><td style='text-align:center;'>{acc}%</td></tr>")
        html_lines.append("</table>")

        if "quiz" in filter_mode:
            html_lines.append("<h3>🗓 DATE-WISE QUIZ SUMMARY REPORT</h3>")
            html_lines.append("<table class='data-table'>")
            html_lines.append("<tr><th>Attempt Date</th><th style='text-align:center;'>Questions</th><th style='text-align:center;'>Correct ✅</th><th style='text-align:center;'>Wrong ❌</th><th style='text-align:center;'>Skipped ⏭</th><th style='text-align:center;'>Total Score</th></tr>")
            
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
                html_lines.append(f"<tr><td>{dt}</td><td style='text-align:center;'>{st['qs']}</td><td style='text-align:center;'>{st['correct']}</td><td style='text-align:center;'>{st['wrong']}</td><td style='text-align:center;'>{st['skipped']}</td><td style='text-align:center;'>{round(st['score'], 2)}</td></tr>")
            
            html_lines.append("</table>")
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

            def build_html_q_table(title, q_list, header_class, empty_msg):
                res = [f"<h3>{title}</h3>", "<table class='data-table'>"]
                res.append(f"<tr><th class='{header_class}' style='width: 18%;'>Attempt Date</th><th class='{header_class}' style='width: 54%;'>Question Text</th><th class='{header_class}' style='width: 28%;'>Correct Answer Text</th></tr>")
                
                if q_list:
                    for q in q_list:
                        raw_q_text = q.get("question_text") or q.get("question")
                        raw_c_ans = q.get("correct_answer_text") or q.get("correct_answer")
                        q_txt = clean_str(raw_q_text) if raw_q_text else f"Question #{q.get('question_id', 'N/A')}"
                        c_ans = clean_str(raw_c_ans) if raw_c_ans else "N/A"
                        res.append(f"<tr><td>{q.get('attempt_date', 'N/A')}</td><td>{q_txt}</td><td>{c_ans}</td></tr>")
                else:
                    res.append(f"<tr><td>N/A</td><td>{empty_msg}</td><td>N/A</td></tr>")
                
                res.append("</table>")
                return "".join(res)

            html_lines.append(build_html_q_table("❌ WRONG QUESTIONS REPORT", wrong_q_list, "wrong-header", "Zero wrong questions in this timeframe! 🎉"))
            html_lines.append(build_html_q_table("⏭ UN-ATTEMPTED / SKIPPED QUESTIONS REPORT", skipped_q_list, "skipped-header", "Zero skipped questions in this timeframe!"))
            html_lines.append(build_html_q_table("✅ CORRECT QUESTIONS REPORT", correct_q_list, "correct-header", "No correct questions logged yet."))

    html_lines.append("</body></html>")
    return "\n".join(html_lines)


def generate_student_pdf_report(user_id: int, filter_mode: str = "last_1_month_data") -> str:
    conn = None
    try:
        u = get_user_profile(user_id)
        if not u:
            return "ERROR: User profile not found in database."

        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        rank = calculate_user_rank(user_id)
        percentile = calculate_user_percentile(user_id)

        saved_qs = []
        if filter_mode == "saved_questions_only":
            cursor.execute("SELECT * FROM saved_questions WHERE user_id = %s ORDER BY id DESC", (user_id,))
            raw_saved = cursor.fetchall()
            cursor.close()
            release_db(conn)
            conn = None
            saved_qs = [dict(r) for r in raw_saved]
            if not saved_qs:
                return "NO_SAVED_QUESTIONS"
            attempts = []
        else:
            cursor.execute("SELECT * FROM quiz_attempts WHERE user_id = %s ORDER BY id DESC", (user_id,))
            raw_attempts = cursor.fetchall()
            cursor.close()
            release_db(conn)
            conn = None
            attempts = [dict(r) for r in raw_attempts]
            if not attempts:
                return "NO_ATTEMPTS"

        username = u.get("username") or "user"
        username_clean = "".join(filter(str.isalnum, str(username))).lower() or "user"
        pdf_filename = f"{username_clean}_{user_id}_{filter_mode}_report.pdf"
        pdf_path = os.path.join(USER_PROFILES_DIR, pdf_filename)

        html_content = generate_html_report(u, attempts, saved_qs, str(rank), percentile, filter_mode)

        # 1. Primary Engine: WeasyPrint
        if HAS_WEASYPRINT:
            try:
                weasyprint.HTML(string=html_content).write_pdf(pdf_path)
                if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 500:
                    return pdf_path
            except Exception as wp_err:
                pass

        # 2. Secondary Engine: Playwright Chromium
        if HAS_PLAYWRIGHT:
            try:
                async def render_pw():
                    async with async_playwright() as p:
                        browser = await p.chromium.launch(headless=True)
                        page = await browser.new_page()
                        await page.set_content(html_content, wait_until="networkidle")
                        await page.pdf(path=pdf_path, format="Letter", print_background=True, prefer_css_page_size=True)
                        await browser.close()
                
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None

                if loop and loop.is_running():
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        pool.submit(asyncio.run, render_pw()).result(timeout=30)
                else:
                    asyncio.run(render_pw())

                if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 500:
                    return pdf_path
            except Exception as pw_err:
                pass

        # 3. Guaranteed ReportLab Fallback (Fully scoped variables to prevent undefined name errors)
        try:
            doc = SimpleDocTemplate(pdf_path, pagesize=letter, rightMargin=30, leftMargin=30, topMargin=25, bottomMargin=50)
            story = []
            
            font_dir = os.path.join(BASE_DIR, "assets")
            os.makedirs(font_dir, exist_ok=True)
            font_path = os.path.join(font_dir, "NotoSansDevanagari-Regular.ttf")
            fallback_font = "Helvetica"
            if os.path.exists(font_path):
                try:
                    pdfmetrics.registerFont(TTFont("FallbackDevanagari", font_path))
                    fallback_font = "FallbackDevanagari"
                except Exception:
                    pass

            styles = getSampleStyleSheet()
            title_style = ParagraphStyle('TitleStyle', parent=styles['Heading1'], fontName='Times-Bold', fontSize=16, leading=20, textColor=colors.HexColor("#1E3A8A"), alignment=1)
            body_style = ParagraphStyle('BodyStyle', parent=styles['Normal'], fontName=fallback_font, fontSize=9.5, leading=14, textColor=colors.HexColor("#334155"))
            bold_style = ParagraphStyle('BoldStyle', parent=styles['Normal'], fontName='Times-Bold', fontSize=9.5, leading=14, textColor=colors.HexColor("#0F172A"))

            logo_l = os.path.join(BASE_DIR, "assets", "logo.png")
            logo_r = os.path.join(BASE_DIR, "assets", "logohim.png")
            img_l = RLImage(logo_l, width=0.7*inch, height=0.7*inch) if os.path.exists(logo_l) else Paragraph("<b>Logo</b>", bold_style)
            img_r = RLImage(logo_r, width=0.7*inch, height=0.7*inch) if os.path.exists(logo_r) else Paragraph("<b>@LearnwithHiM</b>", bold_style)

            header_p = Paragraph("<b><font color='#1E3A8A'>Learn with HiM Quiz Book</font></b><br/><font color='#16A34A' size=9><b>Smart Quiz! Smart Study! Better Improvement! Exam Relevant!</b></font>", title_style)
            header_tbl = Table([[img_l, header_p, img_r]], colWidths=[1.0*inch, 4.6*inch, 1.0*inch])
            header_tbl.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'MIDDLE'), ('ALIGN', (1,0), (1,0), 'CENTER')]))
            story.append(header_tbl)
            story.append(Spacer(1, 10))

            story.append(Paragraph("<b>STUDENT PROFILE OVERVIEW</b>", ParagraphStyle('Sec', parent=styles['Heading2'], fontName='Times-Bold', fontSize=11, textColor=colors.HexColor("#0F172A"))))
            
            # Scoped variables calculation for fallback
            now_date = datetime.now()
            one_month_ago = now_date - timedelta(days=30)
            one_month_ago_str = one_month_ago.strftime("%Y-%m-%d")
            now_date_str = now_date.strftime("%Y-%m-%d")
            is_month_filter = "last_1_month" in filter_mode
            summary_title = f"MONTHLY REPORT ({one_month_ago_str} TO {now_date_str})" if is_month_filter else "ALL-TIME CUMULATIVE ACADEMIC REPORT"

            filtered_attempts = []
            for a in attempts:
                a_date = parse_date_only(a.get("attempt_date") or a.get("attempt_timestamp"))
                if is_month_filter:
                    if a_date >= one_month_ago_str:
                        filtered_attempts.append(a)
                else:
                    filtered_attempts.append(a)

            total_quizzes = len(filtered_attempts)
            total_qs = sum([a.get('questions_attempted', 0) or 0 for a in filtered_attempts])
            total_correct = sum([a.get('correct_answers', 0) or 0 for a in filtered_attempts])
            total_wrong = sum([a.get('wrong_answers', 0) or 0 for a in filtered_attempts])
            acc = round((total_correct / total_qs) * 100, 2) if total_qs > 0 else 0.0

            prof_data = [
                [Paragraph("Student Name:", bold_style), Paragraph(clean_str(u.get('full_name')), body_style), Paragraph("Student ID:", bold_style), Paragraph(clean_str(u.get('student_id')), body_style)],
                [Paragraph("Target Exam:", bold_style), Paragraph(clean_str(u.get('target_exam')), body_style), Paragraph("Location:", bold_style), Paragraph(clean_str(f"{u.get('state')}, {u.get('country')}"), body_style)],
                [Paragraph("Global Rank:", bold_style), Paragraph(str(rank), body_style), Paragraph("Overall Percentile:", bold_style), Paragraph(f"{percentile}%", body_style)]
            ]
            prof_tbl = Table(prof_data, colWidths=[1.3*inch, 2.2*inch, 1.3*inch, 2.2*inch])
            prof_tbl.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F8FAFC")), ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")), ('PADDING', (0,0), (-1,-1), 4)]))
            story.append(prof_tbl)
            story.append(Spacer(1, 10))

            story.append(Paragraph(f"<b>ACADEMIC SUMMARY — {summary_title}</b>", ParagraphStyle('Sec2', parent=styles['Heading2'], fontName='Times-Bold', fontSize=11, textColor=colors.HexColor("#0F172A"))))
            stats_data = [
                [Paragraph("Quizzes", bold_style), Paragraph("Total Questions", bold_style), Paragraph("Correct ✅", bold_style), Paragraph("Wrong ❌", bold_style), Paragraph("Accuracy", bold_style)],
                [Paragraph(str(total_quizzes), body_style), Paragraph(str(total_qs), body_style), Paragraph(str(total_correct), body_style), Paragraph(str(total_wrong), body_style), Paragraph(f"{acc}%", body_style)]
            ]
            stats_tbl = Table(stats_data, colWidths=[1.4*inch, 1.4*inch, 1.4*inch, 1.4*inch, 1.4*inch])
            stats_tbl.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor("#E0F2FE")), ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#38BDF8")), ('ALIGN', (0,0), (-1,-1), 'CENTER'), ('PADDING', (0,0), (-1,-1), 4)]))
            story.append(stats_tbl)
            story.append(Spacer(1, 10))

            if filter_mode != "saved_questions_only" and not "quiz" in filter_mode:
                story.append(Paragraph("❌ <b>WRONG QUESTIONS REPORT</b>", ParagraphStyle('Sec3', parent=styles['Heading2'], fontName='Times-Bold', fontSize=11, textColor=colors.HexColor("#0F172A"))))
                wrong_tbl_data = [[Paragraph("Attempt Date", bold_style), Paragraph("Question Text", bold_style), Paragraph("Correct Answer", bold_style)]]
                
                for a in filtered_attempts:
                    details = json.loads(a["details_json"]) if a.get("details_json") else []
                    if isinstance(details, list):
                        for q in details:
                            if isinstance(q, dict) and str(q.get("status", "")).upper() == "WRONG":
                                wrong_tbl_data.append([
                                    Paragraph(parse_date_only(a.get("attempt_date")), body_style),
                                    Paragraph(clean_str(q.get("question_text") or q.get("question")), body_style),
                                    Paragraph(clean_str(q.get("correct_answer_text")), body_style)
                                ])
                if len(wrong_tbl_data) == 1:
                    wrong_tbl_data.append([Paragraph("N/A", body_style), Paragraph("Zero wrong questions in this timeframe! 🎉", body_style), Paragraph("N/A", body_style)])

                w_tbl = Table(wrong_tbl_data, colWidths=[1.1*inch, 3.9*inch, 2.0*inch], repeatRows=1)
                w_tbl.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor("#FFE4E6")), ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#FB7185")), ('PADDING', (0,0), (-1,-1), 4)]))
                story.append(w_tbl)

            doc.build(story, onFirstPage=lambda c, d: None, onLaterPages=lambda c, d: None)
            if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 500:
                return pdf_path
        except Exception as fb_err:
            pass

        return "ERROR: Failed to generate PDF file. Please check logs."

    except Exception as e:
        if conn:
            release_db(conn)
        return f"ERROR_DETAILS:\n{traceback.format_exc()}"