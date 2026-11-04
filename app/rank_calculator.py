import os
import re
import json
import math
import logging
import asyncio
from datetime import datetime
import pytz
import aiohttp
from bs4 import BeautifulSoup
from psycopg2.extras import RealDictCursor

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters

from app.config import BASE_DIR, USER_PROFILES_DIR
from app.database import get_db, release_db, get_user_profile

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

HAS_WEASYPRINT = False
try:
    import weasyprint
    HAS_WEASYPRINT = True
except ImportError:
    HAS_WEASYPRINT = False

# SSC & RRB Exam Schemes and Sectional Configurations
EXAM_CONFIGS = {
    "SSC_CGL_MAINS": {
        "title": "SSC CGL 2026 Mains (Tier-II)",
        "pos_mark": 3.0,
        "neg_mark": 1.0,
        "comp_pos": 3.0,
        "comp_neg": 1.0,
        "max_merit": 390.0,
        "sections": {
            "math": ["Mathematical Abilities", "Quantitative Aptitude", "Mathematics", "Maths"],
            "reas": ["Reasoning and General Intelligence", "General Intelligence", "Reasoning"],
            "eng": ["English Language and Comprehension", "English Comprehension", "English"],
            "ga": ["General Awareness", "General Knowledge", "GA", "GK"]
        },
        "qualifying": {
            "key": "comp",
            "names": ["Computer Knowledge Test", "Computer Knowledge Module", "Computer Awareness"],
            "max": 60.0
        }
    },
    "SSC_CGL_PRE": {
        "title": "SSC CGL Tier-I (Pre)",
        "pos_mark": 2.0,
        "neg_mark": 0.50,
        "max_merit": 200.0,
        "sections": {
            "reas": ["General Intelligence and Reasoning", "General Intelligence", "Reasoning"],
            "ga": ["General Awareness", "GA", "GK"],
            "math": ["Quantitative Aptitude", "Mathematics", "Maths"],
            "eng": ["English Comprehension", "English Language", "English"]
        },
        "qualifying": None
    },
    "SSC_CHSL_MAINS": {
        "title": "SSC CHSL Mains (Tier-II)",
        "pos_mark": 3.0,
        "neg_mark": 1.0,
        "max_merit": 360.0,
        "sections": {
            "math": ["Mathematical Abilities", "Mathematics", "Maths"],
            "reas": ["Reasoning and General Intelligence", "Reasoning"],
            "eng": ["English Language and Comprehension", "English"],
            "ga": ["General Awareness", "GA", "GK"]
        },
        "qualifying": {
            "key": "comp",
            "names": ["Computer Knowledge Module", "Computer Knowledge"],
            "max": 45.0
        }
    },
    "SSC_CHSL_PRE": {
        "title": "SSC CHSL Tier-I (Pre)",
        "pos_mark": 2.0,
        "neg_mark": 0.50,
        "max_merit": 200.0,
        "sections": {
            "reas": ["General Intelligence", "Reasoning"],
            "ga": ["General Awareness", "GA"],
            "math": ["Quantitative Aptitude", "Maths"],
            "eng": ["English Language", "English"]
        },
        "qualifying": None
    },
    "SSC_CPO_PRE": {
        "title": "SSC CPO Paper-I",
        "pos_mark": 1.0,
        "neg_mark": 0.25,
        "max_merit": 200.0,
        "sections": {
            "reas": ["General Intelligence and Reasoning", "Reasoning"],
            "ga": ["General Knowledge and General Awareness", "General Awareness", "GK"],
            "math": ["Quantitative Aptitude", "Maths"],
            "eng": ["English Comprehension", "English"]
        },
        "qualifying": None
    },
    "SSC_GD": {
        "title": "SSC GD Constable",
        "pos_mark": 2.0,
        "neg_mark": 0.25,
        "max_merit": 160.0,
        "sections": {
            "reas": ["General Intelligence and Reasoning", "Reasoning"],
            "ga": ["General Knowledge and General Awareness", "GK"],
            "math": ["Elementary Mathematics", "Maths"],
            "eng": ["English", "Hindi", "Part-D"]
        },
        "qualifying": None
    },
    "RRB_NTPC": {
        "title": "RRB NTPC (CBT)",
        "pos_mark": 1.0,
        "neg_mark": 0.333,
        "max_merit": 100.0,
        "sections": {
            "ga": ["General Awareness", "GA"],
            "math": ["Mathematics", "Maths"],
            "reas": ["General Intelligence and Reasoning", "Reasoning"]
        },
        "qualifying": None
    }
}


def init_rank_calc_db():
    """Initializes PostgreSQL table for real-time ranking and sectional caches."""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rank_calculator_entries (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                roll_number TEXT NOT NULL,
                candidate_name TEXT,
                exam_code VARCHAR(40) NOT NULL,
                exam_date DATE NOT NULL,
                shift_time VARCHAR(60) NOT NULL,
                category VARCHAR(15) DEFAULT 'UR',
                raw_merit_score NUMERIC(6, 2) NOT NULL,
                qualifying_score NUMERIC(6, 2) DEFAULT 0.0,
                total_correct INT DEFAULT 0,
                total_wrong INT DEFAULT 0,
                total_unattempted INT DEFAULT 0,
                sectional_scores JSONB NOT NULL DEFAULT '{}'::jsonb,
                questions_cache JSONB NOT NULL DEFAULT '[]'::jsonb,
                answer_sheet_url TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(exam_code, roll_number)
            );
            CREATE INDEX IF NOT EXISTS idx_rank_calc_merit 
            ON rank_calculator_entries(exam_code, raw_merit_score DESC);
            CREATE INDEX IF NOT EXISTS idx_rank_calc_shift 
            ON rank_calculator_entries(exam_code, exam_date, shift_time);
        """)
        conn.commit()
    finally:
        cursor.close()
        release_db(conn)


async def fetch_answer_key_html(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url.strip(), headers=headers, timeout=aiohttp.ClientTimeout(total=25)) as resp:
            if resp.status == 200:
                return await resp.text()
            raise Exception(f"HTTP Status {resp.status} received from examination server.")


def parse_ssc_response_sheet(html_content: str, default_exam="SSC_CGL_MAINS") -> dict:
    soup = BeautifulSoup(html_content, "html.parser")
    candidate_meta = {}

    # 1. Parse Candidate Header Table (TCS iON & Ediquity patterns)
    header_table = (
        soup.find("table", border="1")
        or soup.find("table", class_=re.compile(r"main_table|table-bordered|cand-info|candidate_details"))
        or soup.find("table")
    )
    if header_table:
        for tr in header_table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) >= 2:
                lbl = tds[0].get_text(strip=True).replace(":", "")
                val = tds[1].get_text(strip=True)
                if "Roll Number" in lbl: candidate_meta["roll_number"] = val
                elif "Candidate Name" in lbl: candidate_meta["candidate_name"] = val
                elif "Venue Name" in lbl or "Center Name" in lbl: candidate_meta["venue_name"] = val
                elif "Exam Date" in lbl or "Test Date" in lbl: candidate_meta["exam_date"] = val
                elif "Exam Time" in lbl or "Test Time" in lbl: candidate_meta["shift_time"] = val
                elif "Subject" in lbl: candidate_meta["subject_raw"] = val

    raw_subject = candidate_meta.get("subject_raw", "").upper()
    exam_code = default_exam
    if "CHSL" in raw_subject:
        exam_code = "SSC_CHSL_MAINS" if any(w in raw_subject for w in ("TIER II", "TIER-II", "MAINS")) else "SSC_CHSL_PRE"
    elif "CGL" in raw_subject:
        exam_code = "SSC_CGL_MAINS" if any(w in raw_subject for w in ("TIER II", "TIER-II", "MAINS")) else "SSC_CGL_PRE"
    elif "CPO" in raw_subject or "SUB-INSPECTOR" in raw_subject:
        exam_code = "SSC_CPO_PRE"
    elif "GD" in raw_subject or "CONSTABLE" in raw_subject:
        exam_code = "SSC_GD"
    elif "NTPC" in raw_subject or "RRB" in raw_subject:
        exam_code = "RRB_NTPC"

    cfg = EXAM_CONFIGS.get(exam_code, EXAM_CONFIGS["SSC_CGL_MAINS"])

    def map_to_section_key(sec_title: str) -> tuple[str, str, bool]:
        s_clean = sec_title.strip()
        if cfg.get("qualifying"):
            q_info = cfg["qualifying"]
            if any(qn.lower() in s_clean.lower() for qn in q_info["names"]):
                return q_info["key"], "Computer Knowledge", True
        for k, aliases in cfg["sections"].items():
            for alias in aliases:
                if alias.lower() in s_clean.lower():
                    clean_display = "Maths" if k == "math" else ("Reasoning" if k == "reas" else ("English" if k == "eng" else "General Awareness"))
                    return k, clean_display, False
        return "ga", "General Awareness", False

    # 2. Locate Question Panels (handles both TCS iON and Ediquity sectional blocks)
    panels = soup.find_all("div", class_=re.compile(r"question-pnl|rw|menu-tbl|question-block"))
    if not panels:
        panels = soup.find_all("table", class_=re.compile(r"questionPnl|menu-tbl|question-table"))

    sectional_data = {}
    merit_score = 0.0
    qual_score = 0.0
    total_correct = 0
    total_wrong = 0
    total_unattempted = 0
    parsed_questions = []

    for idx, p in enumerate(panels, start=1):
        p_text = p.get_text()
        if "Question ID" not in p_text and "Option 1 ID" not in p_text and "Chosen Option" not in p_text and "Selected Option" not in p_text:
            continue

        q_id = f"Q{idx}"
        status = "Not Answered"
        chosen_opt = None

        for row in p.find_all("tr"):
            row_str = row.get_text(strip=True)
            if "Question ID" in row_str:
                cells = row.find_all("td")
                if len(cells) >= 2: q_id = cells[1].get_text(strip=True)
            elif "Status" in row_str:
                cells = row.find_all("td")
                if len(cells) >= 2: status = cells[1].get_text(strip=True)
            elif "Chosen Option" in row_str or "Selected Option" in row_str or "Given Answer" in row_str:
                cells = row.find_all("td")
                if len(cells) >= 2: chosen_opt = cells[1].get_text(strip=True)

        # Detect Right Option Number (1, 2, 3, or 4)
        right_opt = None
        options_table = p.find("table")
        if options_table:
            for opt_idx, opt_row in enumerate(options_table.find_all("tr"), start=1):
                if opt_row.find(class_=re.compile(r"rightAns|correct|tick|right_option")) or opt_row.find("img", src=re.compile(r"correct|right|tick")):
                    right_opt = str(opt_idx)
                    break
        if not right_opt:
            right_opt = "1"

        # Determine Section header
        sec_div = p.find_previous("div", class_=re.compile(r"section-lbl|section-cntnr|sectionName|section_heading|section-title"))
        raw_sec_name = sec_div.get_text(strip=True).replace("Section :", "").strip() if sec_div else "General"
        sec_key, sec_label, is_qual = map_to_section_key(raw_sec_name)

        pos_m = cfg.get("comp_pos", 3.0) if is_qual else cfg["pos_mark"]
        neg_m = cfg.get("comp_neg", 1.0) if is_qual else cfg["neg_mark"]

        is_answered = (status == "Answered" and chosen_opt in ["1", "2", "3", "4"])
        delta = 0.0

        if is_answered:
            if chosen_opt == right_opt:
                q_status = "CORRECT"
                delta = pos_m
                total_correct += 1
            else:
                q_status = "WRONG"
                delta = -neg_m
                total_wrong += 1
        else:
            q_status = "UNATTEMPTED"
            total_unattempted += 1

        if is_qual:
            qual_score += delta
        else:
            merit_score += delta

        if sec_key not in sectional_data:
            sectional_data[sec_key] = {"label": sec_label, "score": 0.0, "correct": 0, "wrong": 0, "unattempted": 0, "is_qual": is_qual}
        sectional_data[sec_key]["score"] = round(sectional_data[sec_key]["score"] + delta, 2)
        if q_status == "CORRECT": sectional_data[sec_key]["correct"] += 1
        elif q_status == "WRONG": sectional_data[sec_key]["wrong"] += 1
        else: sectional_data[sec_key]["unattempted"] += 1

        q_text_el = p.find("td", class_=re.compile(r"bold|question|que_text"))
        q_text_content = q_text_el.get_text(strip=True) if q_text_el else f"Question #{idx}"

        parsed_questions.append({
            "num": idx,
            "q_id": q_id,
            "sec_key": sec_key,
            "sec_label": sec_label,
            "text": q_text_content[:250],
            "status": q_status,
            "chosen": chosen_opt if is_answered else "--",
            "right": right_opt,
            "delta": delta
        })

    return {
        "candidate": candidate_meta,
        "exam_code": exam_code,
        "exam_title": cfg["title"],
        "max_merit": cfg["max_merit"],
        "merit_score": round(merit_score, 2),
        "qual_score": round(qual_score, 2),
        "total_correct": total_correct,
        "total_wrong": total_wrong,
        "total_unattempted": total_unattempted,
        "sectional": sectional_data,
        "questions": parsed_questions
    }


def save_and_calculate_rank_stats(user_id: int, eval_data: dict, sheet_url: str, category: str = "UR") -> dict:
    cand = eval_data["candidate"]
    exam_code = eval_data["exam_code"]
    roll_no = cand.get("roll_number", f"R_{user_id}")
    name = cand.get("candidate_name", "Student")

    raw_date = cand.get("exam_date", "")
    try:
        exam_date = datetime.strptime(raw_date, "%d/%m/%Y").strftime("%Y-%m-%d")
    except Exception:
        exam_date = datetime.now(IST).strftime("%Y-%m-%d")

    shift_time = cand.get("shift_time", "Shift 1")

    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            INSERT INTO rank_calculator_entries (
                user_id, roll_number, candidate_name, exam_code, exam_date, shift_time,
                category, raw_merit_score, qualifying_score, total_correct, total_wrong,
                total_unattempted, sectional_scores, questions_cache, answer_sheet_url
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (exam_code, roll_number) DO UPDATE SET
                raw_merit_score = EXCLUDED.raw_merit_score,
                qualifying_score = EXCLUDED.qualifying_score,
                total_correct = EXCLUDED.total_correct,
                total_wrong = EXCLUDED.total_wrong,
                total_unattempted = EXCLUDED.total_unattempted,
                sectional_scores = EXCLUDED.sectional_scores,
                questions_cache = EXCLUDED.questions_cache,
                answer_sheet_url = EXCLUDED.answer_sheet_url;
        """, (
            user_id, roll_no, name, exam_code, exam_date, shift_time,
            category, eval_data["merit_score"], eval_data["qual_score"],
            eval_data["total_correct"], eval_data["total_wrong"],
            eval_data["total_unattempted"], json.dumps(eval_data["sectional"]),
            json.dumps(eval_data["questions"]), sheet_url
        ))
        conn.commit()

        # Compute Windowed Ranks across Peers
        cursor.execute("""
            WITH ranked_pool AS (
                SELECT 
                    roll_number,
                    raw_merit_score,
                    DENSE_RANK() OVER (ORDER BY raw_merit_score DESC) as overall_rank,
                    DENSE_RANK() OVER (PARTITION BY category ORDER BY raw_merit_score DESC) as category_rank,
                    DENSE_RANK() OVER (PARTITION BY exam_date, shift_time ORDER BY raw_merit_score DESC) as shift_rank,
                    COUNT(*) OVER () as total_students,
                    COUNT(*) OVER (PARTITION BY category) as total_category_students,
                    COUNT(*) OVER (PARTITION BY exam_date, shift_time) as total_shift_students,
                    AVG(raw_merit_score) OVER (PARTITION BY exam_date, shift_time) as shift_avg_score,
                    AVG(raw_merit_score) OVER () as global_avg_score,
                    STDDEV_POP(raw_merit_score) OVER (PARTITION BY exam_date, shift_time) as shift_std,
                    STDDEV_POP(raw_merit_score) OVER () as global_std
                FROM rank_calculator_entries
                WHERE exam_code = %s
            )
            SELECT * FROM ranked_pool WHERE roll_number = %s;
        """, (exam_code, roll_no))
        ranks = cursor.fetchone()
        res = dict(ranks) if ranks else {}

        # 4. SSC Normalization Formula Simulation
        raw_score = float(eval_data["merit_score"])
        shift_avg = float(res.get("shift_avg_score") or raw_score)
        global_avg = float(res.get("global_avg_score") or raw_score)
        shift_std = float(res.get("shift_std") or 15.0) or 15.0
        global_std = float(res.get("global_std") or 15.0) or 15.0

        normalized_score = round(global_avg + (raw_score - shift_avg) * (global_std / shift_std), 2)
        res["normalized_score"] = max(0.0, min(eval_data["max_merit"] * 1.15, normalized_score))
        return res
    finally:
        cursor.close()
        release_db(conn)


# ==============================================================================
# PDF GENERATORS: SCORECARD & SECTION-WISE ANSWER KEYS
# ==============================================================================
def generate_rank_report_card_pdf(user_id: int, eval_data: dict, rank_data: dict) -> str:
    cand = eval_data["candidate"]
    exam_title = eval_data["exam_title"]
    now_str = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")

    sec_rows = ""
    for k, s in eval_data["sectional"].items():
        qual_badge = " *(Qualifying)*" if s.get("is_qual") else ""
        sec_rows += f"""
        <tr>
            <td><b>{s['label']}{qual_badge}</b></td>
            <td style='text-align:center;'>{s['correct']}</td>
            <td style='text-align:center;'>{s['wrong']}</td>
            <td style='text-align:center;'>{s['unattempted']}</td>
            <td style='text-align:center; font-weight:bold; color:#1E3A8A;'>{s['score']}</td>
        </tr>
        """

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8"/>
    <style>
        @page {{ size: letter; margin: 15mm; }}
        body {{ font-family: 'Times New Roman', serif; color: #1E293B; font-size: 11px; }}
        .header {{ text-align: center; border-bottom: 2px solid #1E3A8A; padding-bottom: 6px; margin-bottom: 12px; }}
        .title {{ font-size: 20px; font-weight: bold; color: #1E3A8A; }}
        .sub {{ font-size: 11px; color: #16A34A; font-weight: bold; margin-top: 2px; }}
        table {{ width: 100%; border-collapse: collapse; margin-bottom: 12px; font-size: 10.5px; }}
        th, td {{ border: 0.5px solid #CBD5E1; padding: 6px 8px; text-align: left; }}
        th {{ background-color: #E0F2FE; color: #0F172A; }}
        .kpi {{ text-align: center; font-size: 14px; font-weight: bold; color: #0284C7; }}
        .norm-box {{ background: #F0FDF4; border: 1px solid #86EFAC; padding: 10px; border-radius: 6px; margin-bottom: 12px; }}
    </style>
    </head>
    <body>
    <div class="header">
        <div class="title">Learn with HiM — Official Answer Key & Rank Dossier</div>
        <div class="sub">{exam_title} • Dynamic Shift Normalization • Generated: {now_str}</div>
    </div>

    <table>
        <tr>
            <td><b>Candidate Name:</b> {cand.get('candidate_name', 'Student')}</td>
            <td><b>Roll Number:</b> {cand.get('roll_number', 'N/A')}</td>
            <td><b>Test Date:</b> {cand.get('exam_date', 'N/A')}</td>
            <td><b>Shift:</b> {cand.get('shift_time', 'N/A')}</td>
        </tr>
    </table>

    <table>
        <tr>
            <th class="kpi">Total Raw Score<br/><span style="color:#1E3A8A;">{eval_data['merit_score']} / {int(eval_data['max_merit'])}</span></th>
            <th class="kpi">Normalized Score<br/><span style="color:#16A34A;">{rank_data.get('normalized_score', eval_data['merit_score'])}</span></th>
            <th class="kpi">Overall Rank<br/><span style="color:#0F172A;">#{rank_data.get('overall_rank', 1)} / {rank_data.get('total_students', 1)}</span></th>
            <th class="kpi">Shift Rank<br/><span style="color:#64748B;">#{rank_data.get('shift_rank', 1)} / {rank_data.get('total_shift_students', 1)}</span></th>
        </tr>
    </table>

    <div class="norm-box">
        <b>📊 Shift Telemetry:</b> Shift Average: <b>{round(float(rank_data.get('shift_avg_score') or 0.0), 1)}</b> | Category Rank ({rank_data.get('category', 'UR')}): <b>#{rank_data.get('category_rank', 1)} / {rank_data.get('total_category_students', 1)}</b>.
    </div>

    <h3>📊 SECTIONAL PERFORMANCE BREAKDOWN</h3>
    <table>
        <thead>
            <tr>
                <th>Section Module</th>
                <th style="text-align:center;">Correct</th>
                <th style="text-align:center;">Wrong</th>
                <th style="text-align:center;">Unattempted</th>
                <th style="text-align:center;">Sectional Raw Score</th>
            </tr>
        </thead>
        <tbody>
            {sec_rows}
        </tbody>
    </table>

    <div style="font-size:9.5px; color:#64748B; text-align:center; margin-top:25px;">
        Official Evaluation Dossier • Learn with HiM Platform (@quizwithhimbot) • YouTube: @LearnwithHiM
    </div>
    </body>
    </html>
    """

    filename = f"Scorecard_{cand.get('roll_number', user_id)}_{int(datetime.now(IST).timestamp())}.pdf"
    pdf_path = os.path.join(USER_PROFILES_DIR, filename)
    if HAS_WEASYPRINT:
        weasyprint.HTML(string=html).write_pdf(pdf_path)
        if os.path.exists(pdf_path): return pdf_path
    return ""


def generate_sectional_answer_key_pdf(user_id: int, roll_no: str, target_sec_key: str = "all") -> str:
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT * FROM rank_calculator_entries WHERE roll_number = %s ORDER BY id DESC LIMIT 1;", (roll_no,))
        row = cursor.fetchone()
        if not row: return ""
        row = dict(row)
    finally:
        cursor.close()
        release_db(conn)

    cand_name = row.get("candidate_name", "Student")
    q_cache = row.get("questions_cache") or []
    if isinstance(q_cache, str): q_cache = json.loads(q_cache)

    sec_labels = {"math": "Mathematics", "reas": "Reasoning", "eng": "English", "ga": "General Awareness", "comp": "Computer Knowledge"}
    sec_display = sec_labels.get(target_sec_key, "Complete All-in-One")

    q_filtered = [q for q in q_cache if (target_sec_key == "all" or q["sec_key"] == target_sec_key)]
    if not q_filtered: return ""

    q_rows = ""
    for q in q_filtered:
        status_col = "#16A34A" if q["status"] == "CORRECT" else ("#DC2626" if q["status"] == "WRONG" else "#64748B")
        badge = f"<span style='color:{status_col}; font-weight:bold;'>{q['status']} ({'+' if q['delta']>0 else ''}{q['delta']})</span>"
        q_rows += f"""
        <tr>
            <td style='text-align:center;'><b>#{q['num']}</b></td>
            <td><b>{q['sec_label']}</b><br/>{q['text']}</td>
            <td style='text-align:center;'>Opt {q['chosen']}</td>
            <td style='text-align:center; font-weight:bold; color:#16A34A;'>Opt {q['right']}</td>
            <td style='text-align:center;'>{badge}</td>
        </tr>
        """

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8"/>
    <style>
        @page {{ size: letter; margin: 14mm; }}
        body {{ font-family: 'Times New Roman', serif; font-size: 10.5px; color: #1E293B; }}
        .header {{ text-align: center; border-bottom: 2px solid #1E3A8A; padding-bottom: 5px; margin-bottom: 10px; }}
        table {{ width: 100%; border-collapse: collapse; margin-bottom: 10px; font-size: 10px; }}
        th, td {{ border: 0.5px solid #CBD5E1; padding: 5px 7px; text-align: left; }}
        th {{ background-color: #E0F2FE; color: #0F172A; }}
    </style>
    </head>
    <body>
    <div class="header">
        <h2 style="color:#1E3A8A; margin:0;">Official SSC Sectional Answer Key: {sec_display}</h2>
        <div style="color:#16A34A; font-weight:bold; margin-top:2px;">Candidate: {cand_name} (Roll: {roll_no}) • Learn with HiM</div>
    </div>

    <table>
        <thead>
            <tr>
                <th style="width:6%; text-align:center;">Q#</th>
                <th style="width:58%;">Question Details</th>
                <th style="width:12%; text-align:center;">Chosen Opt</th>
                <th style="width:12%; text-align:center;">Correct Opt</th>
                <th style="width:12%; text-align:center;">Status</th>
            </tr>
        </thead>
        <tbody>
            {q_rows}
        </tbody>
    </table>
    </body>
    </html>
    """

    filename = f"AnsKey_{roll_no}_{target_sec_key}_{int(datetime.now(IST).timestamp())}.pdf"
    pdf_path = os.path.join(USER_PROFILES_DIR, filename)
    if HAS_WEASYPRINT:
        weasyprint.HTML(string=html).write_pdf(pdf_path)
        if os.path.exists(pdf_path): return pdf_path
    return ""


# ==============================================================================
# TELEGRAM BOT HANDLERS & USER INTERACTION
# ==============================================================================
async def rank_calculator_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🎯 **SSC / RRB ANSWER KEY & REAL-TIME RANK CALCULATOR** 🎯\n"
        "• • • ✧ • • •\n"
        "Just paste your official TCS iON / `digialm.com` or `ediquity` response sheet link directly in this chat!\n\n"
        "👉 **Supported SSC & Central Exams:**\n"
        "• **SSC CGL 2026** (Tier-I Pre & Tier-II Mains with Computer Gate)\n"
        "• **SSC CHSL** (Pre & Mains)\n"
        "• **SSC CPO, MTS, GD Constable, Steno**\n"
        "• **RRB NTPC, Group D, ALP**\n\n"
        "⚡ *Paste your link below to get your Normalized Score, Shift Rank, and Download Section-Wise Answer Key PDFs:* "
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, parse_mode="Markdown")


async def handle_response_sheet_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip() if update.message.text else ""
    # Filter for official TCS iON / Digialm / Ediquity answer key domains
    if not any(domain in text for domain in ("digialm.com", "cdn.digialm.com", "ediquity", "AssessmentHTML", "per/g01")):
        return  # Pass to regular message handler

    user_id = update.effective_user.id
    status_msg = await update.message.reply_text("⏳ **Fetching & Evaluating your Sectional Answer Key...**\nPlease wait 3-5 seconds...")

    try:
        html = await fetch_answer_key_html(text)
        profile = get_user_profile(user_id) or {}
        category = profile.get("category", "UR")

        eval_data = await asyncio.to_thread(parse_ssc_response_sheet, html)
        rank_data = await asyncio.to_thread(save_and_calculate_rank_stats, user_id, eval_data, text, category)

        cand = eval_data["candidate"]
        roll_no = cand.get("roll_number", str(user_id))
        context.user_data["last_evaluated_roll"] = roll_no

        sec_lines = []
        for k, s in eval_data["sectional"].items():
            qual_note = " *(Qualifying)*" if s.get("is_qual") else ""
            sec_lines.append(f"• **{s['label']}{qual_note}:** `{s['score']}` (✅ {s['correct']} | ❌ {s['wrong']} | ⏭ {s['unattempted']})")

        qual_info_line = f"🖥️ **Computer Score:** `{eval_data['qual_score']}`\n" if eval_data.get("qual_score") else ""

        result_msg = (
            f"🎉 **EVALUATION COMPLETE — {eval_data['exam_title'].upper()}** 🎉\n"
            f"• • • ✧ • • •\n"
            f"👤 **Candidate:** {cand.get('candidate_name', 'Student')}\n"
            f"🪪 **Roll No:** `{roll_no}` | 🏷 **Category:** `{category}`\n"
            f"📅 **Date & Shift:** `{cand.get('exam_date', 'N/A')} ({cand.get('shift_time', 'N/A')})`\n"
            f"• • • ✧ • • •\n"
            f"🎯 **TOTAL RAW SCORE:** `{eval_data['merit_score']} / {int(eval_data['max_merit'])}`\n"
            f"🚀 **ESTIMATED NORMALIZED SCORE:** `{rank_data.get('normalized_score', eval_data['merit_score'])}`\n"
            f"{qual_info_line}"
            f"🏆 **Overall Rank:** `#{rank_data.get('overall_rank', 1)}` / `{rank_data.get('total_students', 1)}`\n"
            f"⏱ **Shift Rank:** `#{rank_data.get('shift_rank', 1)}` / `{rank_data.get('total_shift_students', 1)}`\n"
            f"📊 **Shift Average:** `{round(float(rank_data.get('shift_avg_score') or 0.0), 1)}`\n"
            f"• • • ✧ • • •\n"
            f"📚 **SECTIONAL BREAKDOWN:**\n" + "\n".join(sec_lines) + "\n\n"
            f"👇 *Download your Official Scorecard or Section-Wise Answer Keys below:*"
        )

        buttons = [
            [InlineKeyboardButton("📄 Download Master Scorecard (PDF)", callback_data=f"rankpdf_scorecard_{roll_no}")],
            [
                InlineKeyboardButton("📐 Maths Key (PDF)", callback_data=f"rankpdf_sec_math_{roll_no}"),
                InlineKeyboardButton("🧠 Reasoning Key (PDF)", callback_data=f"rankpdf_sec_reas_{roll_no}")
            ],
            [
                InlineKeyboardButton("🔤 English Key (PDF)", callback_data=f"rankpdf_sec_eng_{roll_no}"),
                InlineKeyboardButton("🌍 GK Key (PDF)", callback_data=f"rankpdf_sec_ga_{roll_no}")
            ]
        ]
        if "comp" in eval_data["sectional"]:
            buttons.append([InlineKeyboardButton("🖥️ Computer Key (PDF)", callback_data=f"rankpdf_sec_comp_{roll_no}")])
        buttons.append([InlineKeyboardButton("📚 Complete All-in-One Answer Key", callback_data=f"rankpdf_sec_all_{roll_no}")])

        pdf_path = await asyncio.to_thread(generate_rank_report_card_pdf, user_id, eval_data, rank_data)

        await status_msg.delete()
        await update.message.reply_text(result_msg, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

        if pdf_path and os.path.exists(pdf_path):
            with open(pdf_path, "rb") as doc:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=doc,
                    filename=os.path.basename(pdf_path),
                    caption=f"📄 **Official Scorecard & Rank Dossier — {cand.get('candidate_name', 'Student')}**",
                    parse_mode="Markdown"
                )
    except Exception as e:
        logger.error(f"[RANK CALCULATOR ERROR] {e}")
        await status_msg.edit_text(f"❌ **Error evaluating answer key:** {e}\n\nPlease ensure your link is an official TCS iON / Digialm / Ediquity response sheet URL.")


async def rank_pdf_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    await query.answer()

    if data.startswith("rankpdf_sec_"):
        parts = data.replace("rankpdf_sec_", "").split("_")
        sec_key = parts[0]
        roll_no = parts[1]

        wait_msg = await query.message.reply_text(f"⏳ **Building PDF Answer Key for {sec_key.upper()}...**", parse_mode="Markdown")
        pdf_path = await asyncio.to_thread(generate_sectional_answer_key_pdf, user_id, roll_no, sec_key)

        if pdf_path and os.path.exists(pdf_path):
            with open(pdf_path, "rb") as doc:
                await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=doc,
                    filename=os.path.basename(pdf_path),
                    caption=f"📋 **Official Answer Key ({sec_key.upper()}) — Roll No: {roll_no}**",
                    parse_mode="Markdown"
                )
            await wait_msg.delete()
        else:
            await wait_msg.edit_text("⚠️ Failed generating section PDF.")

    elif data.startswith("rankpdf_scorecard_"):
        roll_no = data.replace("rankpdf_scorecard_", "")
        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cursor.execute("SELECT * FROM rank_calculator_entries WHERE roll_number = %s ORDER BY id DESC LIMIT 1;", (roll_no,))
            row = cursor.fetchone()
            if not row:
                await query.message.reply_text("⚠️ Record not found.")
                return
            row = dict(row)
        finally:
            cursor.close()
            release_db(conn)

        wait_msg = await query.message.reply_text("⏳ **Compiling Master Scorecard PDF...**", parse_mode="Markdown")
        eval_data = {
            "candidate": {
                "candidate_name": row["candidate_name"],
                "roll_number": row["roll_number"],
                "exam_date": str(row["exam_date"]),
                "shift_time": row["shift_time"]
            },
            "exam_title": EXAM_CONFIGS.get(row["exam_code"], {}).get("title", row["exam_code"]),
            "max_merit": EXAM_CONFIGS.get(row["exam_code"], {}).get("max_merit", 390.0),
            "merit_score": float(row["raw_merit_score"]),
            "sectional": row.get("sectional_scores") if isinstance(row.get("sectional_scores"), dict) else json.loads(row.get("sectional_scores") or "{}")
        }
        rank_data = {
            "normalized_score": float(row["raw_merit_score"]),
            "overall_rank": 1,
            "total_students": 1,
            "shift_rank": 1,
            "total_shift_students": 1,
            "shift_avg_score": float(row["raw_merit_score"]),
            "category": row.get("category", "UR"),
            "category_rank": 1,
            "total_category_students": 1
        }
        pdf_path = await asyncio.to_thread(generate_rank_report_card_pdf, user_id, eval_data, rank_data)

        if pdf_path and os.path.exists(pdf_path):
            with open(pdf_path, "rb") as doc:
                await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=doc,
                    filename=os.path.basename(pdf_path),
                    caption=f"📄 **Official Scorecard — {row['candidate_name']}**",
                    parse_mode="Markdown"
                )
            await wait_msg.delete()
        else:
            await wait_msg.edit_text("⚠️ Failed generating scorecard PDF.")


def register_rank_calculator_handlers(app):
    init_rank_calc_db()
    app.add_handler(CommandHandler("rank", rank_calculator_command))
    app.add_handler(CommandHandler("anskey", rank_calculator_command))
    app.add_handler(CommandHandler("calculator", rank_calculator_command))
    app.add_handler(CallbackQueryHandler(rank_pdf_callback_router, pattern="^rankpdf_"))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"(digialm\.com|ediquity|AssessmentHTML|per/g01)"), handle_response_sheet_url))