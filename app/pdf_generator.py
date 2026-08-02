import os
import json
from datetime import datetime, timedelta
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, KeepTogether, PageBreak
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas
from app.config import USER_PROFILES_DIR, BASE_DIR
from app.database import get_user_profile, get_db

class NumberedCanvas(canvas.Canvas):
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
            self.draw_page_number(num_pages)
            super().showPage()
        super().save()

    def draw_page_number(self, page_count):
        self.saveState()
        self.setFont("Helvetica-Bold", 8)
        self.setFillColor(colors.HexColor("#64748B"))
        
        # 1. Official Diagonal Watermark
        self.saveState()
        self.setFont("Helvetica-Bold", 42)
        self.setFillColor(colors.HexColor("#F1F5F9"))
        self.rotate(35)
        self.drawString(180, 100, "@LearnwithHiM")
        self.restoreState()

        # 2. Footer Line
        self.setStrokeColor(colors.HexColor("#CBD5E1"))
        self.setLineWidth(0.8)
        self.line(36, 36, 576, 36)

        # 3. Footer Text
        footer_text = f"Learn with HiM Quiz Book — Official Student Academic Ledger | Page {self._pageNumber} of {page_count}"
        self.drawString(36, 22, footer_text)
        self.restoreState()

def mask_phone(phone_str: str) -> str:
    if not phone_str or len(phone_str) < 4:
        return "XXXXXX"
    return "XXXXXX" + phone_str[-4:]

def generate_student_pdf_report(user_id: int, filter_mode: str = "all") -> str:
    """
    Generates an interactive, colorful PDF report.
    filter_mode: 'last_1_month', 'all_months_stats', 'all_time'
    """
    u = get_user_profile(user_id)
    if not u:
        return ""

    sid = u.get("student_id") or f"USER_{user_id}"
    pdf_filename = f"{sid}_Report_{filter_mode}.pdf"
    pdf_path = os.path.join(USER_PROFILES_DIR, pdf_filename)

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=54
    )

    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#1E3A8A")
    )

    subtitle_style = ParagraphStyle(
        'DocSubTitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#475569")
    )

    section_heading = ParagraphStyle(
        'SecHeading',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#0F172A"),
        spaceBefore=10,
        spaceAfter=6
    )

    body_style = ParagraphStyle(
        'BodyTextCustom',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#334155")
    )

    story = []

    # 1. Header with Logos
    logo_left_path = os.path.join(BASE_DIR, "assets", "logo.png")
    logo_right_path = os.path.join(BASE_DIR, "assets", "logohim.png")

    img_left = Image(logo_left_path, width=1.1*inch, height=0.5*inch) if os.path.exists(logo_left_path) else Paragraph("<b>HiM Logo</b>", body_style)
    img_right = Image(logo_right_path, width=1.1*inch, height=0.5*inch) if os.path.exists(logo_right_path) else Paragraph("<b>@LearnwithHiM</b>", body_style)

    header_text_p = Paragraph(
        "<b>LEARN WITH HIM QUIZ BOOK</b><br/>"
        "<font size=8 color='#64748B'>Official Academic Student Performance Ledger</font>",
        title_style
    )

    header_table = Table([[img_left, header_text_p, img_right]], colWidths=[1.2*inch, 4.0*inch, 1.2*inch])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ALIGN', (0,0), (0,0), 'LEFT'),
        ('ALIGN', (1,0), (1,0), 'CENTER'),
        ('ALIGN', (2,0), (2,0), 'RIGHT'),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 10))

    # 2. Personal Registration Details Table (Masked)
    story.append(Paragraph("👤 <b>STUDENT PROFILE OVERVIEW</b>", section_heading))

    masked_phone = mask_phone(u.get("phone_number", ""))
    masked_pin = "XX" + str(u.get("pin", ""))[-2:] if u.get("pin") else "XXXX"

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
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#E2E8F0")),
        ('PADDING', (0,0), (-1,-1), 5),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE')
    ]))
    story.append(prof_table)
    story.append(Spacer(1, 12))

    # 3. Query Database for Attempts & Filters
    conn = get_db()
    cursor = conn.cursor()

    one_month_ago_str = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    if filter_mode == "last_1_month":
        cursor.execute("SELECT * FROM quiz_attempts WHERE user_id = ? AND attempt_date >= ? ORDER BY id DESC", (user_id, one_month_ago_str))
    else:
        cursor.execute("SELECT * FROM quiz_attempts WHERE user_id = ? ORDER BY id DESC", (user_id,))
    
    attempts = cursor.fetchall()

    cursor.execute("SELECT * FROM saved_questions WHERE user_id = ? ORDER BY id DESC", (user_id,))
    saved_qs = cursor.fetchall()
    conn.close()

    # Performance Stats
    total_quizzes = len(attempts)
    total_qs = sum([a['questions_attempted'] for a in attempts])
    total_correct = sum([a['correct_answers'] for a in attempts])
    total_wrong = sum([a['wrong_answers'] for a in attempts])
    total_score = sum([a['score'] for a in attempts])
    acc = round((total_correct / total_qs) * 100, 2) if total_qs > 0 else 0.0

    story.append(Paragraph(f"📊 <b>ACADEMIC PERFORMANCE SUMMARY ({filter_mode.replace('_', ' ').title()})</b>", section_heading))

    stats_data = [
        [Paragraph("<b>Quizzes Attempted</b>", body_style), Paragraph("<b>Total Questions</b>", body_style), Paragraph("<b>Correct ✅</b>", body_style), Paragraph("<b>Wrong ❌</b>", body_style), Paragraph("<b>Accuracy</b>", body_style)],
        [Paragraph(f"{total_quizzes}", body_style), Paragraph(f"{total_qs}", body_style), Paragraph(f"{total_correct}", body_style), Paragraph(f"{total_wrong}", body_style), Paragraph(f"<b>{acc}%</b>", body_style)]
    ]
    stats_table = Table(stats_data, colWidths=[1.4*inch, 1.4*inch, 1.4*inch, 1.4*inch, 1.4*inch])
    stats_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#DBEAFE")),
        ('BACKGROUND', (0,1), (-1,1), colors.HexColor("#EFF6FF")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#93C5FD")),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('PADDING', (0,0), (-1,-1), 6)
    ]))
    story.append(stats_table)
    story.append(Spacer(1, 14))

    # 4. Attempted / Wrong Questions Detailed Table
    story.append(Paragraph("🎯 <b>DETAILED QUESTION LOGS (One-Liner Format)</b>", section_heading))

    q_table_data = [[Paragraph("<b>Date / Time</b>", body_style), Paragraph("<b>Question Text</b>", body_style), Paragraph("<b>Status</b>", body_style), Paragraph("<b>Correct Answer Text</b>", body_style)]]

    for a in attempts[:15]:
        ad = dict(a)
        dt = ad.get("attempt_timestamp", "N/A")
        details = json.loads(ad["details_json"]) if ad.get("details_json") else []
        
        for q_item in details:
            q_txt = q_item.get("question_text", "N/A")
            c_ans = q_item.get("correct_answer_text", "N/A")
            status = q_item.get("status", "SKIPPED")
            status_str = "CORRECT ✅" if status == "CORRECT" else "WRONG ❌" if status == "WRONG" else "SKIPPED ⏭"

            q_table_data.append([
                Paragraph(f"{dt}", body_style),
                Paragraph(f"{q_txt[:60]}...", body_style) if len(q_txt) > 60 else Paragraph(f"{q_txt}", body_style),
                Paragraph(f"{status_str}", body_style),
                Paragraph(f"{c_ans}", body_style)
            ])

    if len(q_table_data) == 1:
        q_table_data.append([Paragraph("N/A", body_style), Paragraph("No attempted questions recorded for this period.", body_style), Paragraph("-", body_style), Paragraph("N/A", body_style)])

    q_table = Table(q_table_data, colWidths=[1.5*inch, 2.7*inch, 1.1*inch, 1.7*inch])
    q_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#F1F5F9")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
        ('PADDING', (0,0), (-1,-1), 5),
        ('VALIGN', (0,0), (-1,-1), 'TOP')
    ]))
    story.append(q_table)

    doc.build(story, canvasmaker=NumberedCanvas)
    return pdf_path