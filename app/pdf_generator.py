import os
import json
import traceback
import xml.sax.saxutils as saxutils
from datetime import datetime, timedelta
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from app.config import USER_PROFILES_DIR, BASE_DIR
from app.database import get_user_profile, get_db


def draw_pdf_footer(canvas, doc):
    """Draws social links and dynamic page number natively on every page footer."""
    canvas.saveState()

    # Footer Separator Line
    canvas.setStrokeColor(colors.HexColor("#CBD5E1"))
    canvas.setLineWidth(0.8)
    canvas.line(30, 42, 582, 42)

    # Clickable Social Media Links
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
    """Safely converts any value into escaped XML text for ReportLab."""
    if text is None:
        return "N/A"
    if isinstance(text, (dict, list)):
        try:
            text = json.dumps(text)
        except Exception:
            text = str(text)
    return saxutils.escape(str(text))


def generate_student_pdf_report(user_id: int, filter_mode: str = "last_1_month_data") -> str:
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
        # HANDLING 5TH OPTION: SAVED QUESTIONS EXPORT
        # -------------------------------------------------------------
        if filter_mode == "saved_questions_only":
            cursor.execute("SELECT * FROM saved_questions WHERE user_id = ? ORDER BY id DESC", (user_id,))
            saved_rows = [dict(r) for r in cursor.fetchall()]
            conn.close()

            if not saved_rows:
                return "NO_SAVED_QUESTIONS"

            username = u.get("username") or "user"
            username_clean = "".join(filter(str.isalnum, str(username))).lower() or "user"
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
                spaceBefore=10,
                spaceAfter=4
            )

            body_style = ParagraphStyle(
                'BodyTextTimes',
                parent=styles['Normal'],
                fontName='Times-Roman',
                fontSize=8.5,
                leading=11,
                textColor=colors.HexColor("#334155")
            )

            body_style_bold = ParagraphStyle(
                'BodyTextTimesBold',
                parent=styles['Normal'],
                fontName='Times-Bold',
                fontSize=8.5,
                leading=11,
                textColor=colors.HexColor("#0F172A")
            )

            story = []

            # Header with Logos
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
            story.append(Spacer(1, 6))

            # Student Profile Overview
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
            prof_table.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F8FAFC")),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
                ('PADDING', (0,0), (-1,-1), 3),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE')
            ]))
            story.append(prof_table)
            story.append(Spacer(1, 6))

            # Saved Questions Table
            story.append(Paragraph("💾 <b>BOOKMARKED & SAVED QUESTIONS REPORT</b>", section_heading))

            saved_table_data = [[
                Paragraph("Saved Date", body_style_bold),
                Paragraph("Question Text", body_style_bold),
                Paragraph("Correct Answer", body_style_bold)
            ]]

            for sq in saved_rows:
                opts = json.loads(sq['options_json']) if sq.get('options_json') else []
                c_idx = sq.get('correct_option', 0)
                ans_txt = opts[c_idx] if 0 <= c_idx < len(opts) else "N/A"

                q_desc = f"{clean_str(sq.get('question_text', 'N/A'))}"
                if sq.get('explanation'):
                    q_desc += f"<br/><font color='#64748B'><b>Exp:</b> {clean_str(sq.get('explanation'))}</font>"

                saved_table_data.append([
                    Paragraph(f"{sq.get('saved_at', 'N/A')}", body_style),
                    Paragraph(q_desc, body_style),
                    Paragraph(clean_str(ans_txt), body_style)
                ])

            sq_table = Table(saved_table_data, colWidths=[1.1*inch, 3.9*inch, 2.0*inch], repeatRows=1)
            sq_table.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#E0F2FE")),
                ('BACKGROUND', (0,1), (-1,-1), colors.HexColor("#FFFFFF")),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#38BDF8")),
                ('PADDING', (0,0), (-1,-1), 3),
                ('VALIGN', (0,0), (-1,-1), 'TOP')
            ]))
            story.append(sq_table)

            doc.build(story, onFirstPage=draw_pdf_footer, onLaterPages=draw_pdf_footer)
            return pdf_path

        # -------------------------------------------------------------
        # STANDARD ATTEMPT LOGS & QUIZ SUMMARY PROCESSING
        # -------------------------------------------------------------
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

        if not filtered_attempts:
            return "NO_ATTEMPTS"

        username = u.get("username") or "user"
        username_clean = "".join(filter(str.isalnum, str(username))).lower() or "user"
        
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
            spaceBefore=10,
            spaceAfter=4
        )

        body_style = ParagraphStyle(
            'BodyTextTimes',
            parent=styles['Normal'],
            fontName='Times-Roman',
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor("#334155")
        )

        body_style_bold = ParagraphStyle(
            'BodyTextTimesBold',
            parent=styles['Normal'],
            fontName='Times-Bold',
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor("#0F172A")
        )

        story = []

        # Header with Both Logos
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
        story.append(Spacer(1, 6))

        # Student Profile Overview
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
        prof_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F8FAFC")),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
            ('PADDING', (0,0), (-1,-1), 3),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE')
        ]))
        story.append(prof_table)
        story.append(Spacer(1, 6))

        # Academic Performance Summary
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
        stats_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#E0F2FE")),
            ('BACKGROUND', (0,1), (-1,1), colors.HexColor("#F8FAFC")),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#38BDF8")),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('PADDING', (0,0), (-1,-1), 4)
        ]))
        story.append(stats_table)
        story.append(Spacer(1, 8))

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
                date_groups[dt]["qs"] += a.get("questions_attempted", 0) or 0
                date_groups[dt]["correct"] += a.get("correct_answers", 0) or 0
                date_groups[dt]["wrong"] += a.get("wrong_answers", 0) or 0
                date_groups[dt]["skipped"] += a.get("skipped_count", 0) or 0
                date_groups[dt]["score"] += a.get("score", 0.0) or 0.0

            for dt, st in date_groups.items():
                date_summary_data.append([
                    Paragraph(f"{dt}", body_style),
                    Paragraph(f"{st['qs']}", body_style),
                    Paragraph(f"{st['correct']}", body_style),
                    Paragraph(f"{st['wrong']}", body_style),
                    Paragraph(f"{st['skipped']}", body_style),
                    Paragraph(f"{round(st['score'], 2)}", body_style)
                ])

            date_table = Table(date_summary_data, colWidths=[1.2*inch, 1.1*inch, 1.1*inch, 1.1*inch, 1.1*inch, 1.4*inch], repeatRows=1)
            date_table.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#F1F5F9")),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
                ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                ('PADDING', (0,0), (-1,-1), 4)
            ]))
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
                table_data = [[
                    Paragraph("Attempt Date", body_style_bold), 
                    Paragraph("Question Text", body_style_bold), 
                    Paragraph("Correct Answer Text", body_style_bold)
                ]]
                
                for q in q_list:
                    q_txt = clean_str(q.get("question_text") or q.get("question") or "N/A")
                    c_ans = clean_str(q.get("correct_answer_text") or "N/A")
                    table_data.append([
                        Paragraph(f"{q.get('attempt_date', 'N/A')}", body_style),
                        Paragraph(q_txt, body_style),
                        Paragraph(c_ans, body_style)
                    ])

                if len(table_data) == 1:
                    table_data.append([
                        Paragraph("N/A", body_style), 
                        Paragraph(empty_msg, body_style), 
                        Paragraph("N/A", body_style)
                    ])

                q_table = Table(table_data, colWidths=[1.1*inch, 3.9*inch, 2.0*inch], repeatRows=1)
                q_table.setStyle(TableStyle([
                    ('BACKGROUND', (0,0), (-1,0), colors.HexColor(bg_header)),
                    ('BACKGROUND', (0,1), (-1,-1), colors.HexColor("#FFFFFF")),
                    ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor(border_color)),
                    ('PADDING', (0,0), (-1,-1), 3),
                    ('VALIGN', (0,0), (-1,-1), 'TOP')
                ]))
                return q_table

            story.append(Paragraph("❌ <b>WRONG QUESTIONS REPORT</b>", section_heading))
            story.append(build_styled_question_table(
                wrong_q_list, 
                bg_header="#FFE4E6", 
                border_color="#FB7185", 
                empty_msg="Zero wrong questions in this timeframe! 🎉"
            ))
            story.append(Spacer(1, 8))

            story.append(Paragraph("⏭ <b>UN-ATTEMPTED / SKIPPED QUESTIONS REPORT</b>", section_heading))
            story.append(build_styled_question_table(
                skipped_q_list, 
                bg_header="#FEF3C7", 
                border_color="#FBBF24", 
                empty_msg="Zero skipped questions in this timeframe!"
            ))
            story.append(Spacer(1, 8))

            story.append(Paragraph("✅ <b>CORRECT QUESTIONS REPORT</b>", section_heading))
            story.append(build_styled_question_table(
                correct_q_list, 
                bg_header="#D1FAE5", 
                border_color="#34D399", 
                empty_msg="No correct questions logged yet."
            ))

        doc.build(story, onFirstPage=draw_pdf_footer, onLaterPages=draw_pdf_footer)
        return pdf_path

    except Exception as e:
        err_msg = f"ERROR_DETAILS:\n{traceback.format_exc()}"
        return err_msg