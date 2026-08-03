import os
import json
from datetime import datetime, timedelta
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas
from app.config import USER_PROFILES_DIR, BASE_DIR
from app.database import get_user_profile, get_db

class CleanReportCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_footer(num_pages)
            super().showPage()
        super().save()

    def draw_footer(self, page_count):
        self.saveState()

        # Footer Separator Line
        self.setStrokeColor(colors.HexColor("#CBD5E1"))
        self.setLineWidth(0.8)
        self.line(30, 42, 582, 42)

        # Clickable Social Media Links
        self.setFont("Times-Bold", 8)
        self.setFillColor(colors.HexColor("#0284C7"))
        
        y_pos = 28
        self.drawString(30, y_pos, "📸 Insta: @Learnwithhimm")
        self.linkURL("https://instagram.com/Learnwithhimm", (30, y_pos-2, 120, y_pos+8))

        self.drawString(130, y_pos, "📺 YT: @LearnwithHiM")
        self.linkURL("https://youtube.com/@LearnwithHiM", (130, y_pos-2, 210, y_pos+8))

        self.drawString(220, y_pos, "📢 TG: @Learnwithhim")
        self.linkURL("https://t.me/Learnwithhim", (220, y_pos-2, 300, y_pos+8))

        self.drawString(310, y_pos, "💬 TG Chat: @Learnwithhimm")
        self.linkURL("https://t.me/Learnwithhimm", (310, y_pos-2, 410, y_pos+8))

        self.drawString(420, y_pos, "✉️ Direct DM")
        self.linkURL("https://t.me/Learnwithhim?direct", (420, y_pos-2, 480, y_pos+8))

        self.setFont("Times-Roman", 8)
        self.setFillColor(colors.HexColor("#64748B"))
        self.drawRightString(582, y_pos, f"Page {self._pageNumber} of {page_count}")

        self.restoreState()

def mask_phone(phone_str: str) -> str:
    if not phone_str or len(phone_str) < 4:
        return "XXXXXX"
    clean_p = str(phone_str).replace("+", "").strip()
    return "XXXXXX" + clean_p[-4:]

def parse_date_only(date_str: str) -> str:
    if not date_str:
        return "N/A"
    try:
        return date_str.split(" ")[0]
    except Exception:
        return date_str

def generate_student_pdf_report(user_id: int, filter_mode: str = "last_1_month_data") -> str:
    u = get_user_profile(user_id)
    if not u:
        return ""

    username = u.get("username") or "user"
    username_clean = "".join(filter(str.isalnum, username)).lower() or "user"
    
    pdf_filename = f"{username_clean}_{user_id}_{filter_mode}_report.pdf"
    pdf_path = os.path.join(USER_PROFILES_DIR, pdf_filename)

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=letter,
        rightMargin=30,
        leftMargin=30,
        topMargin=25,
        bottomMargin=50
    )

    styles = getSampleStyleSheet()

    main_heading_style = ParagraphStyle(
        'MainTitleDarkBlue',
        parent=styles['Heading1'],
        fontName='Times-Bold',
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#1E3A8A"),
        alignment=1
    )

    section_heading = ParagraphStyle(
        'SecHeading',
        parent=styles['Heading2'],
        fontName='Times-Bold',
        fontSize=11,
        leading=15,
        textColor=colors.HexColor("#0F172A"),
        spaceBefore=8,
        spaceAfter=4
    )

    body_style = ParagraphStyle(
        'BodyTextTimes',
        parent=styles['Normal'],
        fontName='Times-Roman',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#334155")
    )

    body_style_bold = ParagraphStyle(
        'BodyTextTimesBold',
        parent=styles['Normal'],
        fontName='Times-Bold',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#0F172A")
    )

    story = []

    # 1. Header with Uncrushed Logos
    logo_left_path = os.path.join(BASE_DIR, "assets", "logo.png")
    logo_right_path = os.path.join(BASE_DIR, "assets", "logohim.png")

    img_left = Image(logo_left_path, width=0.8*inch, height=0.8*inch) if os.path.exists(logo_left_path) else Paragraph("<b>Logo</b>", body_style)
    img_right = Image(logo_right_path, width=0.8*inch, height=0.8*inch) if os.path.exists(logo_right_path) else Paragraph("<b>@LearnwithHiM</b>", body_style)

    if hasattr(img_left, 'preserveAspectRatio'):
        img_left.preserveAspectRatio = True
    if hasattr(img_right, 'preserveAspectRatio'):
        img_right.preserveAspectRatio = True

    header_text_p = Paragraph(
        "<b><font color='#1E3A8A'>Learn with HiM Quiz Book</font></b><br/>"
        "<font color='#38BDF8' size=8>━━━━━</font><br/>"
        "<font color='#16A34A' size=9><b>Smart Quiz! Smart Study! Better Improvement! Exam Relevant!</b></font>",
        main_heading_style
    )

    header_table = Table([[img_left, header_text_p, img_right]], colWidths=[1.0*inch, 4.6*inch, 1.0*inch])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ALIGN', (0,0), (0,0), 'LEFT'),
        ('ALIGN', (1,0), (1,0), 'CENTER'),
        ('ALIGN', (2,0), (2,0), 'RIGHT'),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 8))

    # 2. Student Profile Overview
    sid = u.get("student_id") or f"USER_{user_id}"
    masked_phone = mask_phone(u.get("phone_number", ""))
    masked_pin = "XX" + str(u.get("pin", ""))[-2:] if u.get("pin") else "XXXX"

    story.append(Paragraph("<b>STUDENT PROFILE OVERVIEW</b>", section_heading))

    profile_data = [
        [Paragraph("Student Name:", body_style_bold), Paragraph(f"{u.get('full_name')}", body_style), Paragraph("Student ID:", body_style_bold), Paragraph(f"{sid}", body_style)],
        [Paragraph("Target Exam:", body_style_bold), Paragraph(f"{u.get('target_exam')}", body_style), Paragraph("Location:", body_style_bold), Paragraph(f"{u.get('state')}, {u.get('country')}", body_style)],
        [Paragraph("DOB / Age:", body_style_bold), Paragraph(f"{u.get('dob')} ({u.get('age')} yrs)", body_style), Paragraph("Phone (Masked):", body_style_bold), Paragraph(f"{masked_phone}", body_style)],
        [Paragraph("Account Status:", body_style_bold), Paragraph("ACTIVE 🟢", body_style), Paragraph("Secret PIN:", body_style_bold), Paragraph(f"{masked_pin}", body_style)],
        [Paragraph("Registered At:", body_style_bold), Paragraph(f"{u.get('created_at')}", body_style), Paragraph("Last Active:", body_style_bold), Paragraph(f"{u.get('last_active')}", body_style)]
    ]

    prof_table = Table(profile_data, colWidths=[1.3*inch, 2.2*inch, 1.3*inch, 2.2*inch])
    prof_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F8FAFC")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
        ('PADDING', (0,0), (-1,-1), 4),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE')
    ]))
    story.append(prof_table)
    story.append(Spacer(1, 8))

    # 3. Database Attempt Fetching
    conn = get_db()
    cursor = conn.cursor()
    
    now_date = datetime.now()
    one_month_ago = now_date - timedelta(days=30)
    one_month_ago_str = one_month_ago.strftime("%Y-%m-%d")
    now_date_str = now_date.strftime("%Y-%m-%d")

    is_month_filter = "last_1_month" in filter_mode

    cursor.execute("SELECT * FROM quiz_attempts WHERE user_id = ? ORDER BY id DESC", (user_id,))
    all_attempts = [dict(r) for r in cursor.fetchall()]
    conn.close()

    filtered_attempts = []
    for a in all_attempts:
        a_date = parse_date_only(a.get("attempt_date") or a.get("attempt_timestamp"))
        if is_month_filter:
            if a_date >= one_month_ago_str:
                filtered_attempts.append(a)
        else:
            filtered_attempts.append(a)

    total_quizzes = len(filtered_attempts)
    total_qs = sum([a.get('questions_attempted', 0) for a in filtered_attempts])
    total_correct = sum([a.get('correct_answers', 0) for a in filtered_attempts])
    total_wrong = sum([a.get('wrong_answers', 0) for a in filtered_attempts])
    acc = round((total_correct / total_qs) * 100, 2) if total_qs > 0 else 0.0

    summary_title_text = f"MONTHLY REPORT ({one_month_ago_str} TO {now_date_str})" if is_month_filter else "ALL-TIME CUMULATIVE ACADEMIC REPORT"

    story.append(Paragraph(f"<b>ACADEMIC PERFORMANCE SUMMARY — {summary_title_text}</b>", section_heading))

    stats_data = [
        [Paragraph("Quizzes", body_style_bold), Paragraph("Total Questions", body_style_bold), Paragraph("Correct ✅", body_style_bold), Paragraph("Wrong ❌", body_style_bold), Paragraph("Accuracy", body_style_bold)],
        [Paragraph(f"{total_quizzes}", body_style), Paragraph(f"{total_qs}", body_style), Paragraph(f"{total_correct}", body_style), Paragraph(f"{total_wrong}", body_style), Paragraph(f"{acc}%", body_style)]
    ]
    stats_table = Table(stats_data, colWidths=[1.4*inch, 1.4*inch, 1.4*inch, 1.4*inch, 1.4*inch])
    stats_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#E0F2FE")),
        ('BACKGROUND', (0,1), (-1,1), colors.HexColor("#F8FAFC")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#38BDF8")),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('PADDING', (0,0), (-1,-1), 5)
    ]))
    story.append(stats_table)
    story.append(Spacer(1, 8))

    # QUIZ SUMMARY MODE
    if "quiz" in filter_mode:
        story.append(Paragraph("🗓 <b>DATE-WISE QUIZ SUMMARY REPORT</b>", section_heading))
        
        date_summary_data = [[
            Paragraph("Attempt Date", body_style_bold),
            Paragraph("Questions", body_style_bold),
            Paragraph("Correct ✅", body_style_bold),
            Paragraph("Wrong ❌", body_style_bold),
            Paragraph("Skipped ⏭", body_style_bold),
            Paragraph("Total Score", body_style_bold)
        ]]

        date_groups = {}
        for a in filtered_attempts:
            dt = parse_date_only(a.get("attempt_date") or a.get("attempt_timestamp"))
            if dt not in date_groups:
                date_groups[dt] = {"qs": 0, "correct": 0, "wrong": 0, "skipped": 0, "score": 0.0}
            date_groups[dt]["qs"] += a.get("questions_attempted", 0)
            date_groups[dt]["correct"] += a.get("correct_answers", 0)
            date_groups[dt]["wrong"] += a.get("wrong_answers", 0)
            date_groups[dt]["skipped"] += a.get("skipped_count", 0)
            date_groups[dt]["score"] += a.get("score", 0.0)

        for dt, st in date_groups.items():
            date_summary_data.append([
                Paragraph(f"{dt}", body_style),
                Paragraph(f"{st['qs']}", body_style),
                Paragraph(f"{st['correct']}", body_style),
                Paragraph(f"{st['wrong']}", body_style),
                Paragraph(f"{st['skipped']}", body_style),
                Paragraph(f"{round(st['score'], 2)}", body_style)
            ])

        if len(date_summary_data) == 1:
            date_summary_data.append([Paragraph("N/A", body_style), Paragraph("0", body_style), Paragraph("0", body_style), Paragraph("0", body_style), Paragraph("0", body_style), Paragraph("0.0", body_style)])

        date_table = Table(date_summary_data, colWidths=[1.2*inch, 1.1*inch, 1.1*inch, 1.1*inch, 1.1*inch, 1.4*inch], repeatRows=1)
        date_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#F1F5F9")),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('PADDING', (0,0), (-1,-1), 5)
        ]))
        story.append(date_table)

    # FULL DATA MODE (Wrong -> Skipped -> Correct)
    else:
        wrong_q_list = []
        skipped_q_list = []
        correct_q_list = []

        for a in filtered_attempts:
            attempt_date = parse_date_only(a.get("attempt_date") or a.get("attempt_timestamp"))
            details = json.loads(a["details_json"]) if a.get("details_json") else []

            for q_item in details:
                q_item['attempt_date'] = attempt_date
                status = q_item.get("status")
                if status == "WRONG":
                    wrong_q_list.append(q_item)
                elif status == "CORRECT":
                    correct_q_list.append(q_item)
                else:
                    skipped_q_list.append(q_item)

        max_rows = 50 if is_month_filter else 100

        # 4a. WRONG QUESTIONS TABLE
        story.append(Paragraph("❌ <b>WRONG QUESTIONS REPORT</b>", section_heading))
        w_table_data = [[Paragraph("Attempt Date", body_style_bold), Paragraph("Question Text", body_style_bold), Paragraph("Correct Answer Text", body_style_bold)]]
        
        for q in wrong_q_list[:max_rows]:
            q_txt = q.get("question_text", "N/A")
            c_ans = q.get("correct_answer_text", "N/A")
            w_table_data.append([
                Paragraph(f"{q['attempt_date']}", body_style),
                Paragraph(f"{q_txt}", body_style),
                Paragraph(f"{c_ans}", body_style)
            ])

        if len(w_table_data) == 1:
            w_table_data.append([Paragraph("N/A", body_style), Paragraph("Zero wrong questions in this timeframe! 🎉", body_style), Paragraph("N/A", body_style)])

        w_table = Table(w_table_data, colWidths=[1.2*inch, 3.8*inch, 2.0*inch], repeatRows=1)
        w_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#FFE4E6")),
            ('BACKGROUND', (0,1), (-1,-1), colors.HexColor("#FFFFFF")),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#FB7185")),
            ('PADDING', (0,0), (-1,-1), 4),
            ('VALIGN', (0,0), (-1,-1), 'TOP')
        ]))
        story.append(w_table)
        story.append(Spacer(1, 8))

        # 4b. UN-ATTEMPTED / SKIPPED QUESTIONS TABLE
        story.append(Paragraph("⏭ <b>UN-ATTEMPTED / SKIPPED QUESTIONS REPORT</b>", section_heading))
        s_table_data = [[Paragraph("Attempt Date", body_style_bold), Paragraph("Question Text", body_style_bold), Paragraph("Correct Answer Text", body_style_bold)]]
        
        for q in skipped_q_list[:max_rows]:
            q_txt = q.get("question_text", "N/A")
            c_ans = q.get("correct_answer_text", "N/A")
            s_table_data.append([
                Paragraph(f"{q['attempt_date']}", body_style),
                Paragraph(f"{q_txt}", body_style),
                Paragraph(f"{c_ans}", body_style)
            ])

        if len(s_table_data) == 1:
            s_table_data.append([Paragraph("N/A", body_style), Paragraph("Zero skipped questions in this timeframe!", body_style), Paragraph("N/A", body_style)])

        s_table = Table(s_table_data, colWidths=[1.2*inch, 3.8*inch, 2.0*inch], repeatRows=1)
        s_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#FEF3C7")),
            ('BACKGROUND', (0,1), (-1,-1), colors.HexColor("#FFFFFF")),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#FBBF24")),
            ('PADDING', (0,0), (-1,-1), 4),
            ('VALIGN', (0,0), (-1,-1), 'TOP')
        ]))
        story.append(s_table)
        story.append(Spacer(1, 8))

        # 4c. CORRECT QUESTIONS TABLE
        story.append(Paragraph("✅ <b>CORRECT QUESTIONS REPORT</b>", section_heading))
        c_table_data = [[Paragraph("Attempt Date", body_style_bold), Paragraph("Question Text", body_style_bold), Paragraph("Correct Answer Text", body_style_bold)]]
        
        for q in correct_q_list[:max_rows]:
            q_txt = q.get("question_text", "N/A")
            c_ans = q.get("correct_answer_text", "N/A")
            c_table_data.append([
                Paragraph(f"{q['attempt_date']}", body_style),
                Paragraph(f"{q_txt}", body_style),
                Paragraph(f"{c_ans}", body_style)
            ])

        if len(c_table_data) == 1:
            c_table_data.append([Paragraph("N/A", body_style), Paragraph("No correct questions logged yet.", body_style), Paragraph("N/A", body_style)])

        c_table = Table(c_table_data, colWidths=[1.2*inch, 3.8*inch, 2.0*inch], repeatRows=1)
        c_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#D1FAE5")),
            ('BACKGROUND', (0,1), (-1,-1), colors.HexColor("#FFFFFF")),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#34D399")),
            ('PADDING', (0,0), (-1,-1), 4),
            ('VALIGN', (0,0), (-1,-1), 'TOP')
        ]))
        story.append(c_table)

    doc.build(story, canvasmaker=CleanReportCanvas)
    return pdf_path

def generate_instant_quiz_pdf_report(user_id: int, quiz_result: dict) -> str:
    """Generates an instant PDF report card for the current attempted quiz."""
    u = get_user_profile(user_id)
    if not u:
        return ""

    username = u.get("username") or "user"
    username_clean = "".join(filter(str.isalnum, username)).lower() or "user"
    
    timestamp_slug = datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_filename = f"{username_clean}_{user_id}_instant_quiz_{timestamp_slug}.pdf"
    pdf_path = os.path.join(USER_PROFILES_DIR, pdf_filename)

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=letter,
        rightMargin=30,
        leftMargin=30,
        topMargin=25,
        bottomMargin=50
    )

    styles = getSampleStyleSheet()

    main_heading_style = ParagraphStyle(
        'MainTitleDarkBlue',
        parent=styles['Heading1'],
        fontName='Times-Bold',
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#1E3A8A"),
        alignment=1
    )

    score_red_style = ParagraphStyle(
        'ScoreRedStyle',
        parent=styles['Normal'],
        fontName='Times-Bold',
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#DC2626"), # Red color score
        alignment=1
    )

    section_heading = ParagraphStyle(
        'SecHeading',
        parent=styles['Heading2'],
        fontName='Times-Bold',
        fontSize=11,
        leading=15,
        textColor=colors.HexColor("#0F172A"),
        spaceBefore=8,
        spaceAfter=4
    )

    body_style = ParagraphStyle(
        'BodyTextTimes',
        parent=styles['Normal'],
        fontName='Times-Roman',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#334155")
    )

    body_style_bold = ParagraphStyle(
        'BodyTextTimesBold',
        parent=styles['Normal'],
        fontName='Times-Bold',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#0F172A")
    )

    story = []

    # 1. Header with Logos
    logo_left_path = os.path.join(BASE_DIR, "assets", "logo.png")
    logo_right_path = os.path.join(BASE_DIR, "assets", "logohim.png")

    img_left = Image(logo_left_path, width=0.8*inch, height=0.8*inch) if os.path.exists(logo_left_path) else Paragraph("<b>Logo</b>", body_style)
    img_right = Image(logo_right_path, width=0.8*inch, height=0.8*inch) if os.path.exists(logo_right_path) else Paragraph("<b>@LearnwithHiM</b>", body_style)

    if hasattr(img_left, 'preserveAspectRatio'):
        img_left.preserveAspectRatio = True
    if hasattr(img_right, 'preserveAspectRatio'):
        img_right.preserveAspectRatio = True

    header_text_p = Paragraph(
        "<b><font color='#1E3A8A'>Learn with HiM Quiz Book</font></b><br/>"
        "<font color='#38BDF8' size=8>━━━━━</font><br/>"
        "<font color='#16A34A' size=9><b>Smart Quiz! Smart Study! Better Improvement! Exam Relevant!</b></font>",
        main_heading_style
    )

    header_table = Table([[img_left, header_text_p, img_right]], colWidths=[1.0*inch, 4.6*inch, 1.0*inch])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ALIGN', (0,0), (0,0), 'LEFT'),
        ('ALIGN', (1,0), (1,0), 'CENTER'),
        ('ALIGN', (2,0), (2,0), 'RIGHT'),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 8))

    # Prominent Red Total Score Display Right Below Main Heading
    total_q = quiz_result.get('total_questions', 0)
    score_val = quiz_result.get('score', 0.0)
    max_score = total_q * 1.0
    story.append(Paragraph(f"🏆 TOTAL SCORE: {score_val} / {max_score}", score_red_style))
    story.append(Spacer(1, 8))

    # 2. Student Profile Overview
    sid = u.get("student_id") or f"USER_{user_id}"
    masked_phone = mask_phone(u.get("phone_number", ""))
    masked_pin = "XX" + str(u.get("pin", ""))[-2:] if u.get("pin") else "XXXX"

    story.append(Paragraph("<b>STUDENT PROFILE OVERVIEW</b>", section_heading))

    profile_data = [
        [Paragraph("Student Name:", body_style_bold), Paragraph(f"{u.get('full_name')}", body_style), Paragraph("Student ID:", body_style_bold), Paragraph(f"{sid}", body_style)],
        [Paragraph("Target Exam:", body_style_bold), Paragraph(f"{u.get('target_exam')}", body_style), Paragraph("Location:", body_style_bold), Paragraph(f"{u.get('state')}, {u.get('country')}", body_style)],
        [Paragraph("DOB / Age:", body_style_bold), Paragraph(f"{u.get('dob')} ({u.get('age')} yrs)", body_style), Paragraph("Phone (Masked):", body_style_bold), Paragraph(f"{masked_phone}", body_style)],
        [Paragraph("Account Status:", body_style_bold), Paragraph("ACTIVE 🟢", body_style), Paragraph("Secret PIN:", body_style_bold), Paragraph(f"{masked_pin}", body_style)],
        [Paragraph("Registered At:", body_style_bold), Paragraph(f"{u.get('created_at')}", body_style), Paragraph("Last Active:", body_style_bold), Paragraph(f"{u.get('last_active')}", body_style)]
    ]

    prof_table = Table(profile_data, colWidths=[1.3*inch, 2.2*inch, 1.3*inch, 2.2*inch])
    prof_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F8FAFC")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
        ('PADDING', (0,0), (-1,-1), 4),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE')
    ]))
    story.append(prof_table)
    story.append(Spacer(1, 8))

    # 3. Current Attempt Performance Summary Table
    correct_cnt = quiz_result.get('correct_count', 0)
    wrong_cnt = quiz_result.get('wrong_count', 0)
    skipped_cnt = quiz_result.get('skipped_count', 0)
    acc = round((correct_cnt / total_q) * 100, 2) if total_q > 0 else 0.0

    story.append(Paragraph("📊 <b>ATTEMPTED QUIZ SUMMARY</b>", section_heading))

    stats_data = [
        [Paragraph("Total Questions", body_style_bold), Paragraph("Correct ✅", body_style_bold), Paragraph("Wrong ❌", body_style_bold), Paragraph("Skipped ⏭", body_style_bold), Paragraph("Accuracy", body_style_bold)],
        [Paragraph(f"{total_q}", body_style), Paragraph(f"{correct_cnt}", body_style), Paragraph(f"{wrong_cnt}", body_style), Paragraph(f"{skipped_cnt}", body_style), Paragraph(f"{acc}%", body_style)]
    ]
    stats_table = Table(stats_data, colWidths=[1.4*inch, 1.4*inch, 1.4*inch, 1.4*inch, 1.4*inch])
    stats_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#E0F2FE")),
        ('BACKGROUND', (0,1), (-1,1), colors.HexColor("#F8FAFC")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#38BDF8")),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('PADDING', (0,0), (-1,-1), 5)
    ]))
    story.append(stats_table)
    story.append(Spacer(1, 8))

    # 4. Itemized Question Tables
    details = quiz_result.get("details", [])
    attempt_date_str = datetime.now().strftime("%Y-%m-%d")

    wrong_q_list = []
    skipped_q_list = []
    correct_q_list = []

    for q_item in details:
        q_item['attempt_date'] = attempt_date_str
        status = q_item.get("status")
        if status == "WRONG":
            wrong_q_list.append(q_item)
        elif status == "CORRECT":
            correct_q_list.append(q_item)
        else:
            skipped_q_list.append(q_item)

    # 4a. WRONG QUESTIONS TABLE
    story.append(Paragraph("❌ <b>WRONG QUESTIONS REPORT</b>", section_heading))
    w_table_data = [[Paragraph("Attempt Date", body_style_bold), Paragraph("Question Text", body_style_bold), Paragraph("Correct Answer Text", body_style_bold)]]
    
    for q in wrong_q_list:
        q_txt = q.get("question_text", "N/A")
        c_ans = q.get("correct_answer_text", "N/A")
        w_table_data.append([
            Paragraph(f"{q['attempt_date']}", body_style),
            Paragraph(f"{q_txt}", body_style),
            Paragraph(f"{c_ans}", body_style)
        ])

    if len(w_table_data) == 1:
        w_table_data.append([Paragraph("N/A", body_style), Paragraph("Zero wrong questions in this quiz attempt! 🎉", body_style), Paragraph("N/A", body_style)])

    w_table = Table(w_table_data, colWidths=[1.2*inch, 3.8*inch, 2.0*inch], repeatRows=1)
    w_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#FFE4E6")),
        ('BACKGROUND', (0,1), (-1,-1), colors.HexColor("#FFFFFF")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#FB7185")),
        ('PADDING', (0,0), (-1,-1), 4),
        ('VALIGN', (0,0), (-1,-1), 'TOP')
    ]))
    story.append(w_table)
    story.append(Spacer(1, 8))

    # 4b. UN-ATTEMPTED / SKIPPED QUESTIONS TABLE
    story.append(Paragraph("⏭ <b>UN-ATTEMPTED / SKIPPED QUESTIONS REPORT</b>", section_heading))
    s_table_data = [[Paragraph("Attempt Date", body_style_bold), Paragraph("Question Text", body_style_bold), Paragraph("Correct Answer Text", body_style_bold)]]
    
    for q in skipped_q_list:
        q_txt = q.get("question_text", "N/A")
        c_ans = q.get("correct_answer_text", "N/A")
        s_table_data.append([
            Paragraph(f"{q['attempt_date']}", body_style),
            Paragraph(f"{q_txt}", body_style),
            Paragraph(f"{c_ans}", body_style)
        ])

    if len(s_table_data) == 1:
        s_table_data.append([Paragraph("N/A", body_style), Paragraph("Zero skipped questions in this quiz attempt!", body_style), Paragraph("N/A", body_style)])

    s_table = Table(s_table_data, colWidths=[1.2*inch, 3.8*inch, 2.0*inch], repeatRows=1)
    s_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#FEF3C7")),
        ('BACKGROUND', (0,1), (-1,-1), colors.HexColor("#FFFFFF")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#FBBF24")),
        ('PADDING', (0,0), (-1,-1), 4),
        ('VALIGN', (0,0), (-1,-1), 'TOP')
    ]))
    story.append(s_table)
    story.append(Spacer(1, 8))

    # 4c. CORRECT QUESTIONS TABLE
    story.append(Paragraph("✅ <b>CORRECT QUESTIONS REPORT</b>", section_heading))
    c_table_data = [[Paragraph("Attempt Date", body_style_bold), Paragraph("Question Text", body_style_bold), Paragraph("Correct Answer Text", body_style_bold)]]
    
    for q in correct_q_list:
        q_txt = q.get("question_text", "N/A")
        c_ans = q.get("correct_answer_text", "N/A")
        c_table_data.append([
            Paragraph(f"{q['attempt_date']}", body_style),
            Paragraph(f"{q_txt}", body_style),
            Paragraph(f"{c_ans}", body_style)
        ])

    if len(c_table_data) == 1:
        c_table_data.append([Paragraph("N/A", body_style), Paragraph("No correct questions logged for this attempt.", body_style), Paragraph("N/A", body_style)])

    c_table = Table(c_table_data, colWidths=[1.2*inch, 3.8*inch, 2.0*inch], repeatRows=1)
    c_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#D1FAE5")),
        ('BACKGROUND', (0,1), (-1,-1), colors.HexColor("#FFFFFF")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#34D399")),
        ('PADDING', (0,0), (-1,-1), 4),
        ('VALIGN', (0,0), (-1,-1), 'TOP')
    ]))
    story.append(c_table)

    doc.build(story, canvasmaker=CleanReportCanvas)
    return pdf_path