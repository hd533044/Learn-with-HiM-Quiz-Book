import os
import json
import traceback
import xml.sax.saxutils as saxutils
import urllib.request
import unicodedata
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor

from app.config import USER_PROFILES_DIR, BASE_DIR
from app.database import get_user_profile, get_db, release_db
from app.stats import calculate_user_rank, calculate_user_percentile

# Attempt WeasyPrint Import for Native Unicode Shaping & Perfect Hindi Rendering
HAS_WEASYPRINT = False
try:
    import weasyprint
    HAS_WEASYPRINT = True
except ImportError:
    HAS_WEASYPRINT = False


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
    """
    Safely normalizes Devanagari Unicode characters (NFC form) and consolidates whitespace
    so matras, halants, and vowel signs bind correctly without separation or spacing bugs.
    """
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
    """
    Builds a pixel-perfect HTML document with Google Noto Sans Devanagari font CSS, clickable logos (slightly larger size), 
    bold & italic personal detail values on a dedicated introduction cover page, and clickable footer links.
    """
    now_date = datetime.now()
    one_month_ago = now_date - timedelta(days=30)
    one_month_ago_str = one_month_ago.strftime("%Y-%m-%d")
    now_date_str = now_date.strftime("%Y-%m-%d")
    is_month_filter = "last_1_month" in filter_mode

    sid = clean_str(user_profile.get("student_id") or f"USER_{user_profile.get('user_id')}")
    masked_phone = mask_phone(user_profile.get("phone_number", ""))
    
    raw_pin = user_profile.get("pin")
    masked_pin = "XX" + str(raw_pin)[-2:] if raw_pin else "XXXX"
    
    # Personal detail values formatted in bold & italic as requested
    full_name_clean = f"<b><i>{clean_str(user_profile.get('full_name'))}</i></b>"
    target_exam_clean = f"<b><i>{clean_str(user_profile.get('target_exam'))}</i></b>"
    
    state_val = user_profile.get('state', '')
    country_val = user_profile.get('country', '')
    location_clean = f"<b><i>{clean_str(f'{state_val}, {country_val}')}</i></b>"
    
    dob_val = user_profile.get('dob', '')
    age_val = user_profile.get('age', '')
    dob_age_clean = f"<b><i>{clean_str(f'{dob_val} ({age_val} yrs)')}</i></b>"
    
    sid_val = f"<b><i>{sid}</i></b>"
    masked_phone_val = f"<b><i>{masked_phone}</i></b>"
    account_status_val = "<b><i>ACTIVE 🟢</i></b>"
    masked_pin_val = f"<b><i>{masked_pin}</i></b>"
    rank_val = f"<b><i>{clean_str(rank)}</i></b>"
    percentile_val = f"<b><i>{percentile}%</i></b>"

    # Absolute paths for logos with slightly increased dimensions (~2mm larger all around -> ~65px width/height)
    logo_left_path = os.path.abspath(os.path.join(BASE_DIR, "assets", "logo.png"))
    logo_right_path = os.path.abspath(os.path.join(BASE_DIR, "assets", "logohim.png"))

    target_link = "https://t.me/@learnwithhim"

    left_logo_html = f'<a href="{target_link}" target="_blank"><img src="file://{logo_left_path}" style="width: 65px; height: 65px; object-fit: contain; border: none;" /></a>' if os.path.exists(logo_left_path) else f'<a href="{target_link}"><b>Logo</b></a>'
    right_logo_html = f'<a href="{target_link}" target="_blank"><img src="file://{logo_right_path}" style="width: 65px; height: 65px; object-fit: contain; border: none;" /></a>' if os.path.exists(logo_right_path) else f'<a href="{target_link}"><b>@LearnwithHiM</b></a>'

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
        "@page { size: letter; margin: 20mm 15mm 22mm 15mm; @bottom-right { content: 'Page ' counter(page); font-size: 8.5px; font-family: 'Times New Roman', serif; color: #64748B; } }",
        "body { font-family: 'Noto Sans Devanagari', 'Times New Roman', Helvetica, Arial, sans-serif; margin: 0; padding: 0; color: #334155; font-size: 11.5px; line-height: 1.45; direction: ltr; background-color: #ffffff; }",
        "a { color: inherit; text-decoration: none; }",
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
        ".footer-links { margin-top: 25px; padding-top: 8px; border-top: 0.5px solid #CBD5E1; font-size: 8.5px; font-family: 'Times New Roman', serif; text-align: center; }",
        ".footer-links a { color: #0284C7; font-weight: bold; margin: 0 6px; text-decoration: underline; }",
        ".cover-page { page-break-after: always; height: 100%; display: flex; flex-direction: column; justify-content: center; }",
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
        
        # --- INTRODUCTORY COVER PAGE ---
        "<div class='cover-page'>",
        f"<table class='header-table'><tr>"
        f"<td style='width: 15%; text-align: left;'>{left_logo_html}</td>"
        f"<td class='header-title'>Learn with HiM Quiz Book<div class='sub-title'>Smart Quiz! Smart Study! Better Improvement! Exam Relevant!</div></td>"
        f"<td style='width: 15%; text-align: right;'>{right_logo_html}</td>"
        f"</tr></table>",
        "<br/><br/>",
        "<h2 style='text-align: center; color: #1E3A8A; font-family: \"Times New Roman\", serif; font-size: 22px; margin-bottom: 25px;'>OFFICIAL STUDENT INTRODUCTION & PROFILE REPORT</h2>",
        "<table class='data-table prof-table' style='font-size: 12px;'>",
        f"<tr><td class='prof-label' style='padding: 10px;'>Student Name:</td><td style='padding: 10px;'>{full_name_clean}</td><td class='prof-label' style='padding: 10px;'>Student ID:</td><td style='padding: 10px;'>{sid_val}</td></tr>",
        f"<tr><td class='prof-label' style='padding: 10px;'>Target Exam:</td><td style='padding: 10px;'>{target_exam_clean}</td><td class='prof-label' style='padding: 10px;'>Location:</td><td style='padding: 10px;'>{location_clean}</td></tr>",
        f"<tr><td class='prof-label' style='padding: 10px;'>DOB / Age:</td><td style='padding: 10px;'>{dob_age_clean}</td><td class='prof-label' style='padding: 10px;'>Phone (Masked):</td><td style='padding: 10px;'>{masked_phone_val}</td></tr>",
        f"<tr><td class='prof-label' style='padding: 10px;'>Account Status:</td><td style='padding: 10px;'>{account_status_val}</td><td class='prof-label' style='padding: 10px;'>Secret PIN:</td><td style='padding: 10px;'>{masked_pin_val}</td></tr>",
        f"<tr><td class='prof-label' style='padding: 10px;'>Global Rank:</td><td style='padding: 10px;'>{rank_val}</td><td class='prof-label' style='padding: 10px;'>Overall Percentile:</td><td style='padding: 10px;'>{percentile_val}</td></tr>",
        "</table>",
        "<div style='text-align: center; margin-top: 40px; color: #64748B; font-style: italic; font-size: 11px;'>This document certifies the official academic performance metrics recorded on Learn with HiM Platform.</div>",
        "</div>", # End Cover Page

        # --- MAIN REPORT PAGES HEADER ---
        f"<table class='header-table'><tr>"
        f"<td style='width: 15%; text-align: left;'>{left_logo_html}</td>"
        f"<td class='header-title'>Learn with HiM Quiz Book<div class='sub-title'>Smart Quiz! Smart Study! Better Improvement! Exam Relevant!</div></td>"
        f"<td style='width: 15%; text-align: right;'>{right_logo_html}</td>"
        f"</tr></table>"
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

    # Clickable Footer Links Section
    html_lines.append(
        "<div class='footer-links'>"
        "<a href='https://instagram.com/Learnwithhimm' target='_blank'>📸 Insta: @Learnwithhimm</a> | "
        "<a href='https://youtube.com/@LearnwithHiM' target='_blank'>📺 YT: @LearnwithHiM</a> | "
        "<a href='https://t.me/Learnwithhim' target='_blank'>📢 TG: @Learnwithhim</a> | "
        "<a href='https://t.me/Learnwithhimm' target='_blank'>💬 TG Chat: @Learnwithhimm</a> | "
        "<a href='https://t.me/Learnwithhim?direct' target='_blank'>✉️ Direct DM</a>"
        "</div>"
    )

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

        if HAS_WEASYPRINT:
            try:
                weasyprint.HTML(string=html_content).write_pdf(pdf_path)
                if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 500:
                    return pdf_path
            except Exception as wp_err:
                pass

        return f"ERROR: WeasyPrint compilation failed. Please verify that your Aptfile and system packages are properly built on your server."

    except Exception as e:
        if conn:
            release_db(conn)
        return f"ERROR_DETAILS:\n{traceback.format_exc()}"