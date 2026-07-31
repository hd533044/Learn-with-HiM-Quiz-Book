import os
import io
from typing import Dict, Any, List
from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import HexColor, white
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from app.stats import (
    get_user_performance_summary, 
    get_datewise_quiz_history, 
    get_user_badges, 
    calculate_user_rank, 
    calculate_user_percentile
)

def generate_profile_book_pdf(profile: Dict[str, Any]) -> io.BytesIO:
    """Generates an official branded PDF Profile Book with logo integration and readable headers."""
    user_id = profile.get('user_id', 0)
    perf = get_user_performance_summary(user_id)
    history = get_datewise_quiz_history(user_id)
    badges = get_user_badges(user_id)
    rank = calculate_user_rank(user_id)
    percentile = calculate_user_percentile(user_id)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()
    
    # Base Typography - Clean Sans-Serif
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=18,
        textColor=HexColor('#1A2B4C'),
        alignment=0,
        spaceAfter=2
    )
    
    sig_style = ParagraphStyle(
        'SignatureStyle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        textColor=HexColor('#D9534F'),
        alignment=0,
        spaceAfter=4
    )

    h2_style = ParagraphStyle(
        'SectionHeading',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=12,
        textColor=HexColor('#1A2B4C'),
        spaceBefore=10,
        spaceAfter=6
    )

    body_style = ParagraphStyle(
        'BodyTextCustom',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        textColor=HexColor('#222222'),
        leading=13
    )

    header_text_style = ParagraphStyle(
        'TableHeaderCustom',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        textColor=white,
        leading=13,
        alignment=1
    )

    elements: List[Any] = []

    # 1. Header with Official Logo & Title
    logo_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "logo.png")
    
    header_text_block = [
        Paragraph("📖 OFFICIAL STUDENT PROFILE STATS BOOK", title_style),
        Paragraph("⚡ Powered by @LearnwithHiM | YouTube: Learn with HiM", sig_style)
    ]

    if os.path.exists(logo_path):
        logo_img = Image(logo_path, width=50, height=50)
        header_table = Table([[logo_img, header_text_block]], colWidths=[60, 480])
        header_table.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('PADDING', (0,0), (-1,-1), 0),
        ]))
        elements.append(header_table)
    else:
        elements.extend(header_text_block)

    elements.append(Spacer(1, 8))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=HexColor('#1A2B4C'), spaceAfter=12))

    # 2. Student Identity Card Table
    elements.append(Paragraph("👤 Student Personal Card", h2_style))
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
        ('BACKGROUND', (0, 0), (-1, -1), HexColor('#F8F9FA')),
        ('PADDING', (0, 0), (-1, -1), 6),
        ('BOX', (0, 0), (-1, -1), 1, HexColor('#D0D7DE')),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, HexColor('#E1E4E8')),
    ]))
    elements.append(t_student)
    elements.append(Spacer(1, 10))

    # 3. Earned Badges
    elements.append(Paragraph("🏆 Achievement & Scholar Badges", h2_style))
    badge_str = " | ".join(badges) if badges else "🌱 Active Practitioner"
    t_badge = Table([[Paragraph(f"<b>Active Badges:</b> {badge_str}", body_style)]], colWidths=[540])
    t_badge.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), HexColor('#FFF8E7')),
        ('PADDING', (0, 0), (-1, -1), 8),
        ('BOX', (0, 0), (-1, -1), 1, HexColor('#FFE082')),
    ]))
    elements.append(t_badge)
    elements.append(Spacer(1, 10))

    # 4. Overall Academic Metrics (WITH FIXED WHITE HEADERS)
    elements.append(Paragraph("📊 Overall Academic Metrics", h2_style))
    metrics_data = [
        [
            Paragraph("Tests Attempted", header_text_style), 
            Paragraph("Total Questions", header_text_style), 
            Paragraph("Correct Answers", header_text_style), 
            Paragraph("Average Score", header_text_style)
        ],
        [
            Paragraph(str(perf.get('total_tests', 0)), body_style), 
            Paragraph(str(perf.get('total_qs', 0)), body_style), 
            Paragraph(str(perf.get('total_correct', 0)), body_style), 
            Paragraph(f"{round(perf.get('avg_score', 0.0), 2)}", body_style)
        ]
    ]
    t_metrics = Table(metrics_data, colWidths=[135, 135, 135, 135])
    t_metrics.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HexColor('#1A2B4C')),
        ('PADDING', (0, 0), (-1, -1), 6),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('BOX', (0, 0), (-1, -1), 1, HexColor('#1A2B4C')),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, HexColor('#D0D7DE')),
    ]))
    elements.append(t_metrics)
    elements.append(Spacer(1, 12))

    # 5. Date-Wise History Table (WITH FIXED WHITE HEADERS)
    elements.append(Paragraph("📅 Date-Wise Quiz History Summary", h2_style))
    if history:
        hist_table_data = [[
            Paragraph("Date", header_text_style), 
            Paragraph("Quizzes", header_text_style), 
            Paragraph("Questions", header_text_style), 
            Paragraph("Correct", header_text_style), 
            Paragraph("Avg Score", header_text_style)
        ]]
        for h in history[:15]:
            hist_table_data.append([
                Paragraph(str(h.get('date', 'N/A')), body_style),
                Paragraph(str(h.get('tests', 0)), body_style),
                Paragraph(str(h.get('qs', 0)), body_style),
                Paragraph(str(h.get('correct', 0)), body_style),
                Paragraph(str(h.get('avg_score', 0.0)), body_style)
            ])
        t_hist = Table(hist_table_data, colWidths=[120, 100, 105, 105, 110])
        t_hist.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), HexColor('#2C3E50')),
            ('PADDING', (0, 0), (-1, -1), 5),
            ('BOX', (0, 0), (-1, -1), 1, HexColor('#2C3E50')),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, HexColor('#E1E4E8')),
        ]))
        elements.append(t_hist)
    else:
        elements.append(Paragraph("<i>No quiz attempts recorded yet. Start practicing to generate your history!</i>", body_style))

    elements.append(Spacer(1, 20))
    elements.append(Paragraph("🎓 <i>Keep practicing daily on @LearnwithHiM Quiz Book to unlock new badges and raise your global rank!</i>", body_style))

    doc.build(elements)
    buffer.seek(0)
    return buffer