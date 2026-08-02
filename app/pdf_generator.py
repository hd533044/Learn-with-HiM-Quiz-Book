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
            self.draw_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_decorations(self, page_count):
        self.saveState()
        
        # 1. Soft Sky Tint Line / Frame
        self.setStrokeColor(colors.HexColor("#38BDF8"))
        self.setLineWidth(1)
        self.line(30, 755, 582, 755)

        # 2. Light, Small Diagonal Watermark (@LearnwithHiM)
        self.setFont("Helvetica-Bold", 18)
        self.setFillColor(colors.HexColor("#E0F2FE"))
        self.saveState()
        self.rotate(25)
        self.drawString(250, 250, "@LearnwithHiM")
        self.restoreState()

        # 3. Footer Separator Line
        self.setStrokeColor(colors.HexColor("#CBD5E1"))
        self.setLineWidth(0.8)
        self.line(30, 42, 582, 42)

        # 4. Clickable Social Media Links Footer
        self.setFont("Helvetica-Bold", 7)
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

        self.setFont("Helvetica", 7)
        self.setFillColor(colors.HexColor("#64748B"))
        self.drawRightString(582, y_pos, f"Page {self._pageNumber} of {page_count}")

        self.restoreState()

def mask_phone(phone_str: str) -> str:
    if not phone_str or len(phone_str) < 4:
        return "XXXXXX"
    clean_p = str(phone_str).replace("+", "").strip()
    return "XXXXXX" + clean_p[-4:]

def generate_student_pdf_report(user_id: int, filter_mode: str = "all") -> str:
    u = get_user_profile(user_id)
    if not u:
        return ""

    username = u.get("username") or "user"
    username_clean = "".join(filter(str.isalnum, username)).lower() or "user"
    
    # Filename format: Username_userid_1-monthreport.pdf
    timeframe_str = "1-monthreport" if filter_mode == "last_1_month" else "allmonthsreport" if filter_mode == "all_months_stats" else "alltimereport"
    pdf_filename = f"{username_clean}_{user_id}_{timeframe_str}.pdf"
    pdf_path = os.path.join(USER_PROFILES_DIR, pdf_filename)

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=letter,
        rightMargin=30,
        leftMargin=30,
        topMargin=30,
        bottomMargin=50
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=15,
        leading=18,
        textColor=colors.HexColor("#0284C7")
    )

    section_heading = ParagraphStyle(
        'SecHeading',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#0F172A"),
        spaceBefore=8,
        spaceAfter=4
    )

    body_style = ParagraphStyle(
        'BodyTextCustom',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#334155")
    )

    story = []

    # 1. Header with Uncrushed Logos
    logo_left_path = os.path.join(BASE_DIR, "assets", "logo.png")
    logo_right_path = os.path.join(BASE_DIR, "assets", "logohim.png")

    img_left = Image(logo_left_path, width=1.1*inch, height=0.45*inch) if os.path.exists(logo_left_path) else Paragraph("<b>Logo</b>", body_style)
    img_right = Image(logo_right_path, width=1.1*inch, height=0.45*inch) if os.path.exists(logo_right_path) else Paragraph("<b>@LearnwithHiM</b>", body_style)

    if hasattr(img_left, 'preserveAspectRatio'):
        img_left.preserveAspectRatio = True
    if hasattr(img_right, 'preserveAspectRatio'):
        img_right.preserveAspectRatio = True

    header_text_p = Paragraph(
        "<b>LEARN WITH HIM QUIZ BOOK</b><br/>"
        "<font size=8 color='#0369A1'>Official Academic Student Performance Ledger</font>",
        title_style
    )

    header_table = Table([[img_left, header_text_p, img_right]], colWidths=[1.2*inch, 4.2*inch, 1.2*inch])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ALIGN', (0,0), (0,0), 'LEFT'),
        ('ALIGN', (1,0), (1,0), 'CENTER'),
        ('ALIGN', (2,0), (2,0), 'RIGHT'),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 6))

    # 2. Student Profile Overview Table
    sid = u.get("student_id") or f"USER_{user_id}"
    masked_phone = mask_phone(u.get("phone_number", ""))
    masked_pin = "XX" + str(u.get("pin", ""))[-2:] if u.get("pin") else "XXXX"

    story.append(Paragraph("📋 <b>STUDENT PROFILE OVERVIEW</b>", section_heading))

    profile_data = [
        [Paragraph("<b>Student Name:</b>", body_style), Paragraph(f"{u.get('full_name')}", body_style), Paragraph("<b>Student ID:</b>", body_style), Paragraph(f"<b>{sid}</b>", body_style)],
        [Paragraph("<b>Target Exam:</b>", body_style), Paragraph(f"{u.get('target_exam')}", body_style), Paragraph("<b>Location:</b>", body_style), Paragraph(f"{u.get('state')}, {u.get('country')}", body_style)],
        [Paragraph("<b>DOB / Age:</b>", body_style), Paragraph(f"{u.get('dob')} ({u.get('age')} yrs)", body_style), Paragraph("<b>Phone (Masked):</b>", body_style), Paragraph(f"{masked_phone}", body_style)],
        [Paragraph("<b>Account Status:</b>", body_style), Paragraph("ACTIVE 🟢", body_style), Paragraph("<b>Secret PIN:</b>", body_style), Paragraph(f"{masked_pin}", body_style)],
        [Paragraph("<b>Registered At:</b>", body_style), Paragraph(f"{u.get('created_at')}", body_style), Paragraph("<b>Last Active:</b>", body_style), Paragraph(f"{u.get('last_active')}", body_style)]
    ]

    prof_table = Table(profile_data, colWidths=[1.3*inch, 2.2*inch, 1.3*inch, 2.2*inch])
    prof_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F8FAFC")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
        ('PADDING', (0,0), (-1,-1), 4),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE')
    ]))
    story.append(prof_table)
    story.append(Spacer(1, 6))

    # 3. Database Attempt Fetching
    conn = get_db()
    cursor = conn.cursor()
    one_month_ago_str = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    if filter_mode == "last_1_month":
        cursor.execute("SELECT * FROM quiz_attempts WHERE user_id = ? AND attempt_date >= ? ORDER BY id DESC", (user_id, one_month_ago_str))
    else:
        cursor.execute("SELECT * FROM quiz_attempts WHERE user_id = ? ORDER BY id DESC", (user_id,))
    
    attempts = cursor.fetchall()
    conn.close()

    total_quizzes = len(attempts)
    total_qs = sum([a['questions_attempted'] for a in attempts])
    total_correct = sum([a['correct_answers'] for a in attempts])
    total_wrong = sum([a['wrong_answers'] for a in attempts])
    acc = round((total_correct / total_qs) * 100, 2) if total_qs > 0 else 0.0

    story.append(Paragraph(f"📊 <b>ACADEMIC PERFORMANCE SUMMARY ({filter_mode.replace('_', ' ').title()})</b>", section_heading))

    stats_data = [
        [Paragraph("<b>Quizzes</b>", body_style), Paragraph("<b>Total Questions</b>", body_style), Paragraph("<b>Correct ✅</b>", body_style), Paragraph("<b>Wrong ❌</b>", body_style), Paragraph("<b>Accuracy</b>", body_style)],
        [Paragraph(f"{total_quizzes}", body_style), Paragraph(f"{total_qs}", body_style), Paragraph(f"{total_correct}", body_style), Paragraph(f"{total_wrong}", body_style), Paragraph(f"<b>{acc}%</b>", body_style)]
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

    # Categorize questions into 3 lists: Wrong, Skipped, Correct
    wrong_q_list = []
    skipped_q_list = []
    correct_q_list = []

    for a in attempts:
        ad = dict(a)
        attempt_date = ad.get("attempt_date") or (ad.get("attempt_timestamp", "").split(" ")[0] if ad.get("attempt_timestamp") else "N/A")
        details = json.loads(ad["details_json"]) if ad.get("details_json") else []

        for q_item in details:
            q_item['attempt_date'] = attempt_date
            status = q_item.get("status")
            if status == "WRONG":
                wrong_q_list.append(q_item)
            elif status == "CORRECT":
                correct_q_list.append(q_item)
            else:
                skipped_q_list.append(q_item)

    # 4a. WRONG QUESTIONS TABLE
    story.append(Paragraph("❌ <b>WRONG QUESTIONS REPORT</b>", section_heading))
    w_table_data = [[Paragraph("<b>Attempt Date</b>", body_style), Paragraph("<b>Question Text</b>", body_style), Paragraph("<b>Correct Answer Text</b>", body_style)]]
    
    for q in wrong_q_list[:20]:
        q_txt = q.get("question_text", "N/A")
        c_ans = q.get("correct_answer_text", "N/A")
        w_table_data.append([
            Paragraph(f"{q['attempt_date']}", body_style),
            Paragraph(f"{q_txt}", body_style),
            Paragraph(f"{c_ans}", body_style)
        ])

    if len(w_table_data) == 1:
        w_table_data.append([Paragraph("N/A", body_style), Paragraph("Zero wrong questions in this timeframe! 🎉", body_style), Paragraph("N/A", body_style)])

    w_table = Table(w_table_data, colWidths=[1.2*inch, 3.8*inch, 2.0*inch])
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
    s_table_data = [[Paragraph("<b>Attempt Date</b>", body_style), Paragraph("<b>Question Text</b>", body_style), Paragraph("<b>Correct Answer Text</b>", body_style)]]
    
    for q in skipped_q_list[:20]:
        q_txt = q.get("question_text", "N/A")
        c_ans = q.get("correct_answer_text", "N/A")
        s_table_data.append([
            Paragraph(f"{q['attempt_date']}", body_style),
            Paragraph(f"{q_txt}", body_style),
            Paragraph(f"{c_ans}", body_style)
        ])

    if len(s_table_data) == 1:
        s_table_data.append([Paragraph("N/A", body_style), Paragraph("Zero skipped questions in this timeframe!", body_style), Paragraph("N/A", body_style)])

    s_table = Table(s_table_data, colWidths=[1.2*inch, 3.8*inch, 2.0*inch])
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
    c_table_data = [[Paragraph("<b>Attempt Date</b>", body_style), Paragraph("<b>Question Text</b>", body_style), Paragraph("<b>Correct Answer Text</b>", body_style)]]
    
    for q in correct_q_list[:20]:
        q_txt = q.get("question_text", "N/A")
        c_ans = q.get("correct_answer_text", "N/A")
        c_table_data.append([
            Paragraph(f"{q['attempt_date']}", body_style),
            Paragraph(f"{q_txt}", body_style),
            Paragraph(f"{c_ans}", body_style)
        ])

    if len(c_table_data) == 1:
        c_table_data.append([Paragraph("N/A", body_style), Paragraph("No correct questions logged yet.", body_style), Paragraph("N/A", body_style)])

    c_table = Table(c_table_data, colWidths=[1.2*inch, 3.8*inch, 2.0*inch])
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