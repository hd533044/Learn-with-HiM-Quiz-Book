import os
import io
from typing import Dict, Any, List
from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import HexColor, white
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from app.stats import (
    get_user_performance_summary, 
    get_datewise_quiz_history, 
    get_user_badges, 
    calculate_user_rank, 
    calculate_user_percentile
)

class WatermarkCanvas(canvas.Canvas):
    """Custom canvas that draws logo & diagonal watermark on every single page."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pages = []

    def showPage(self):
        self.pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self.pages)
        for page in self.pages:
            self.__dict__.update(page)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, total_pages):
        self.saveState()
        
        # 1. Diagonal Background Watermark
        self.setFont("Helvetica-Bold", 32)
        self.setFillColor(HexColor("#E2E8F0"), alpha=0.35)
        self.rotate(35)
        self.drawString(180, 100, "⚡ Learn with HiM — Official Quiz Bank")
        self.restoreState()

        # 2. Header Logo & Border on every page
        self.saveState()
        logo_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "logo.png")
        if os.path.exists(logo_path):
            try:
                self.drawImage(logo_path, 36, 740, width=35, height=35, preserveAspectRatio=True, mask='auto')
            except Exception:
                pass

        self.setFont("Helvetica-Bold", 8)
        self.setFillColor(HexColor("#718096"))
        self.drawString(78, 752, "LEARN WITH HIM QUIZ BOOK — OFFICIAL STUDY MATERIAL")
        self.drawRightString(576, 752, f"Page {self._pageNumber} of {total_pages}")
        self.setStrokeColor(HexColor("#CBD5E0"))
        self.setLineWidth(0.5)
        self.line(36, 736, 576, 736)

        # 3. Footer Branding
        self.line(36, 36, 576, 36)
        self.setFont("Helvetica", 8)
        self.drawString(36, 24, "⚡ Powered by @LearnwithHiM | Telegram Channel & YouTube Portal")
        self.drawRightString(576, 24, "Strictly For Personal Educational Practice")
        self.restoreState()

def generate_profile_book_pdf(profile: Dict[str, Any]) -> io.BytesIO:
    """Generates an official branded PDF Profile Book."""
    user_id = profile.get('user_id', 0)
    perf = get_user_performance_summary(user_id)
    history = get_datewise_quiz_history(user_id)
    badges = get_user_badges(user_id)
    rank = calculate_user_rank(user_id)
    percentile = calculate_user_percentile(user_id)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        rightMargin=36, leftMargin=36, topMargin=54, bottomMargin=54
    )

    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'DocTitle', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=18, textColor=HexColor('#1A2B4C'), spaceAfter=2
    )
    sig_style = ParagraphStyle(
        'SignatureStyle', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=10, textColor=HexColor('#D9534F'), spaceAfter=10
    )
    h2_style = ParagraphStyle(
        'SectionHeading', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=11, textColor=HexColor('#1A2B4C'), spaceBefore=8, spaceAfter=4
    )
    body_style = ParagraphStyle(
        'BodyTextCustom', parent=styles['Normal'], fontName='Helvetica', fontSize=9, textColor=HexColor('#222222'), leading=13
    )
    header_text_style = ParagraphStyle(
        'TableHeaderCustom', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9, textColor=white, leading=13, alignment=1
    )

    elements: List[Any] = []

    elements.append(Paragraph("📖 OFFICIAL STUDENT PROFILE STATS BOOK", title_style))
    elements.append(Paragraph("⚡ Powered by @LearnwithHiM | YouTube: Learn with HiM", sig_style))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=HexColor('#1A2B4C'), spaceAfter=10))

    full_name = profile.get('full_name', 'Student')
    target_exam = profile.get('target_exam', 'N/A')
    gender = profile.get('gender', 'N/A')
    age = profile.get('age', 'N/A')
    state = profile.get('state', 'N/A')
    country = profile.get('country', 'India')

    student_data = [
        [Paragraph(f"<b>Full Name:</b> {full_name}", body_style), Paragraph(f"<b>Telegram ID:</b> {user_id}", body_style)],
        [Paragraph(f"<b>Target Exam:</b> {target_exam}", body_style), Paragraph(f"<b>Gender / Age:</b> {gender} / {age}", body_style)],
        [Paragraph(f"<b>Location:</b> {state}, {country}", body_style), Paragraph(f"<b>Global Rank:</b> {rank} ({percentile}%)", body_style)]
    ]
    t_student = Table(student_data, colWidths=[270, 270])
    t_student.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), HexColor('#F8F9FA')),
        ('PADDING', (0,0), (-1,-1), 5),
        ('BOX', (0,0), (-1,-1), 1, HexColor('#D0D7DE')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, HexColor('#E1E4E8')),
    ]))
    elements.append(t_student)
    elements.append(Spacer(1, 8))

    elements.append(Paragraph("🏆 Achievement & Scholar Badges", h2_style))
    badge_str = " | ".join(badges) if badges else "🌱 Active Practitioner"
    t_badge = Table([[Paragraph(f"<b>Active Badges:</b> {badge_str}", body_style)]], colWidths=[540])
    t_badge.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), HexColor('#FFF8E7')),
        ('PADDING', (0,0), (-1,-1), 6),
        ('BOX', (0,0), (-1,-1), 1, HexColor('#FFE082')),
    ]))
    elements.append(t_badge)
    elements.append(Spacer(1, 8))

    elements.append(Paragraph("📊 Overall Academic Metrics", h2_style))
    metrics_data = [
        [Paragraph("Tests Attempted", header_text_style), Paragraph("Total Questions", header_text_style), Paragraph("Correct Answers", header_text_style), Paragraph("Average Score", header_text_style)],
        [Paragraph(str(perf.get('total_tests', 0)), body_style), Paragraph(str(perf.get('total_qs', 0)), body_style), Paragraph(str(perf.get('total_correct', 0)), body_style), Paragraph(f"{round(perf.get('avg_score', 0.0), 2)}", body_style)]
    ]
    t_metrics = Table(metrics_data, colWidths=[135, 135, 135, 135])
    t_metrics.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), HexColor('#1A2B4C')),
        ('PADDING', (0,0), (-1,-1), 5),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('BOX', (0,0), (-1,-1), 1, HexColor('#1A2B4C')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, HexColor('#D0D7DE')),
    ]))
    elements.append(t_metrics)
    elements.append(Spacer(1, 10))

    elements.append(Paragraph("📅 Date-Wise Quiz History Summary", h2_style))
    if history:
        hist_table_data = [[
            Paragraph("Date", header_text_style), Paragraph("Quizzes", header_text_style), Paragraph("Questions", header_text_style), Paragraph("Correct", header_text_style), Paragraph("Avg Score", header_text_style)
        ]]
        for h in history[:12]:
            hist_table_data.append([
                Paragraph(str(h.get('date', 'N/A')), body_style),
                Paragraph(str(h.get('tests', 0)), body_style),
                Paragraph(str(h.get('qs', 0)), body_style),
                Paragraph(str(h.get('correct', 0)), body_style),
                Paragraph(str(h.get('avg_score', 0.0)), body_style)
            ])
        t_hist = Table(hist_table_data, colWidths=[120, 100, 105, 105, 110])
        t_hist.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), HexColor('#2C3E50')),
            ('PADDING', (0,0), (-1,-1), 4),
            ('BOX', (0,0), (-1,-1), 1, HexColor('#2C3E50')),
            ('INNERGRID', (0,0), (-1,-1), 0.5, HexColor('#E1E4E8')),
        ]))
        elements.append(t_hist)

    doc.build(elements, canvasmaker=WatermarkCanvas)
    buffer.seek(0)
    return buffer

def generate_quiz_questions_pdf(quiz_data: dict) -> io.BytesIO:
    """Generates a compact PDF containing recent quiz questions, correct answers, and explanations."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        rightMargin=36, leftMargin=36, topMargin=54, bottomMargin=54
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        'QTitle', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=16, textColor=HexColor('#1A2B4C'), spaceAfter=2
    )
    sig_style = ParagraphStyle(
        'QSig', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=10, textColor=HexColor('#D9534F'), spaceAfter=8
    )
    q_num_style = ParagraphStyle(
        'QNum', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=10, textColor=HexColor('#1A2B4C'), spaceBefore=6, spaceAfter=2
    )
    option_style = ParagraphStyle(
        'OptStyle', parent=styles['Normal'], fontName='Helvetica', fontSize=9, textColor=HexColor('#2D3748'), leading=12
    )
    correct_opt_style = ParagraphStyle(
        'CorrStyle', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9, textColor=HexColor('#276749'), leading=12
    )
    exp_style = ParagraphStyle(
        'ExpStyle', parent=styles['Normal'], fontName='Helvetica-Oblique', fontSize=8.5, textColor=HexColor('#4A5568'), leading=11
    )

    elements: List[Any] = []

    user_name = quiz_data.get('user_name', 'Student')
    score = quiz_data.get('score', 0)
    total_qs = quiz_data.get('total_qs', 0)
    questions = quiz_data.get('questions', [])

    elements.append(Paragraph("📝 RECENT QUIZ QUESTION BANK & SOLUTION SHEET", title_style))
    elements.append(Paragraph(f"👤 Student: <b>{user_name}</b> | Score: <b>{score}/{total_qs}</b> | ⚡ Powered by @LearnwithHiM", sig_style))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=HexColor('#1A2B4C'), spaceAfter=10))

    for idx, q in enumerate(questions, start=1):
        q_text = q.get('question_text', 'Question text missing')
        opts = q.get('options', [])
        corr_idx = q.get('correct_option_id', 0)
        exp = q.get('explanation', 'No detailed explanation provided.')

        elements.append(Paragraph(f"<b>Q{idx}. {q_text}</b>", q_num_style))
        
        opt_rows = []
        for o_idx, opt in enumerate(opts):
            is_correct = (o_idx == corr_idx)
            prefix = "✅ " if is_correct else "• "
            style = correct_opt_style if is_correct else option_style
            opt_rows.append([Paragraph(f"{prefix} <b>({chr(65+o_idx)})</b> {opt}", style)])

        t_opts = Table(opt_rows, colWidths=[540])
        t_opts.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), HexColor('#F7FAFC')),
            ('PADDING', (0,0), (-1,-1), 3),
            ('BOX', (0,0), (-1,-1), 0.5, HexColor('#E2E8F0')),
        ]))
        elements.append(t_opts)
        elements.append(Spacer(1, 2))
        elements.append(Paragraph(f"💡 <b>Explanation:</b> {exp}", exp_style))
        elements.append(Spacer(1, 6))

    doc.build(elements, canvasmaker=WatermarkCanvas)
    buffer.seek(0)
    return buffer