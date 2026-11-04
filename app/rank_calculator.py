import os
import re
import json
import math
import logging
import asyncio
import urllib.parse
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

# SSC & Central Exams Marking Schemes
EXAM_CONFIGS = {
    "SSC_CGL_MAINS": {
        "title": "SSC CGL 2026 Mains (Tier-II)",
        "pos_mark": 3.0,
        "neg_mark": 1.0,
        "comp_pos": 3.0,
        "comp_neg": 1.0,
        "max_merit": 390.0,
        "sections": {
            "math": ["Mathematical Abilities", "Quantitative Aptitude", "Mathematics", "Maths", "PART-A", "Section-I Module-I"],
            "reas": ["Reasoning and General Intelligence", "General Intelligence", "Reasoning", "PART-B", "Section-I Module-II"],
            "eng": ["English Language and Comprehension", "English Comprehension", "English", "PART-C", "Section-II Module-I"],
            "ga": ["General Awareness", "General Knowledge", "GA", "GK", "PART-D", "Section-II Module-II"]
        },
        "qualifying": {
            "key": "comp",
            "names": ["Computer Knowledge Test", "Computer Knowledge Module", "Computer Awareness", "Module-I of Section-III"],
            "max": 60.0
        }
    },
    "SSC_CGL_PRE": {
        "title": "SSC CGL Tier-I (Pre)",
        "pos_mark": 2.0,
        "neg_mark": 0.50,
        "max_merit": 200.0,
        "sections": {
            "reas": ["General Intelligence and Reasoning", "General Intelligence", "Reasoning", "PART-A"],
            "ga": ["General Awareness", "GA", "GK", "PART-B"],
            "math": ["Quantitative Aptitude", "Mathematics", "Maths", "PART-C"],
            "eng": ["English Comprehension", "English Language", "English", "PART-D"]
        },
        "qualifying": None
    },
    "SSC_CHSL_MAINS": {
        "title": "SSC CHSL Mains (Tier-II)",
        "pos_mark": 3.0,
        "neg_mark": 1.0,
        "max_merit": 360.0,
        "sections": {
            "math": ["Mathematical Abilities", "Mathematics", "Maths", "PART-A"],
            "reas": ["Reasoning and General Intelligence", "Reasoning", "PART-B"],
            "eng": ["English Language and Comprehension", "English", "PART-C"],
            "ga": ["General Awareness", "GA", "GK", "PART-D"]
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
            "eng": ["English Language", "English", "PART-A"],
            "reas": ["General Intelligence", "Reasoning", "PART-B"],
            "math": ["Quantitative Aptitude", "Maths", "PART-C"],
            "ga": ["General Awareness", "GA", "PART-D"]
        },
        "qualifying": None
    },
    "SSC_CPO_PRE": {
        "title": "SSC CPO Paper-I",
        "pos_mark": 1.0,
        "neg_mark": 0.25,
        "max_merit": 200.0,
        "sections": {
            "reas": ["General Intelligence and Reasoning", "Reasoning", "PART-A"],
            "ga": ["General Knowledge and General Awareness", "General Awareness", "GK", "PART-B"],
            "math": ["Quantitative Aptitude", "Maths", "PART-C"],
            "eng": ["English Comprehension", "English", "PART-D"]
        },
        "qualifying": None
    },
    "SSC_GD": {
        "title": "SSC GD Constable",
        "pos_mark": 2.0,
        "neg_mark": 0.25,
        "max_merit": 160.0,
        "sections": {
            "reas": ["General Intelligence and Reasoning", "Reasoning", "PART-A"],
            "ga": ["General Knowledge and General Awareness", "GK", "PART-B"],
            "math": ["Elementary Mathematics", "Maths", "PART-C"],
            "eng": ["English", "Hindi", "PART-D"]
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
    """
    Multi-tier resilient fetcher that bypasses Cloudflare/IIS 403 Forbidden blocks
    by routing through headless browser reader networks and proxy decoders.
    """
    clean_target_url = url.strip()
    encoded_url = urllib.parse.quote(clean_target_url, safe="")

    # Tier 1: Jina AI Headless Chromium Engine (Bypasses Cloudflare & IIS WAF 403s)
    jina_endpoint = f"https://r.jina.ai/{clean_target_url}"
    jina_headers = {
        "Accept": "text/html,application/xhtml+xml",
        "X-Return-Format": "html",
        "X-No-Cache": "true",
        "X-Timeout": "18"
    }
    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(jina_endpoint, headers=jina_headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status == 200:
                    html_text = await resp.text()
                    if len(html_text) > 800 and any(w in html_text for w in ("Roll", "Question", "Option", "table", "PART")):
                        logger.info("[RANK CALC] Successfully bypassed 403 using Jina Engine.")
                        return html_text
    except Exception as e:
        logger.warning(f"[JINA BYPASS NOTICE] {e}")

    # Tier 2: AllOrigins Scraper JSON Gateway
    allorigins_endpoint = f"https://api.allorigins.win/get?url={encoded_url}"
    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(allorigins_endpoint, timeout=aiohttp.ClientTimeout(total=18)) as resp:
                if resp.status == 200:
                    payload = await resp.json()
                    html_text = payload.get("contents", "")
                    if len(html_text) > 800 and any(w in html_text for w in ("Roll", "Question", "Option", "table", "PART")):
                        logger.info("[RANK CALC] Successfully fetched via AllOrigins Gateway.")
                        return html_text
    except Exception as e:
        logger.warning(f"[ALLORIGINS NOTICE] {e}")

    # Tier 3: Direct Browser Impersonation with ASP.NET Session Pre-warm
    browser_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
        "Referer": "https://sscexams.cbexams.com/",
        "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site",
        "Upgrade-Insecure-Requests": "1"
    }
    try:
        cookie_jar = aiohttp.CookieJar()
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector, cookie_jar=cookie_jar, headers=browser_headers) as session:
            parsed_u = urllib.parse.urlparse(clean_target_url)
            base_url = f"{parsed_u.scheme}://{parsed_u.netloc}/"
            try:
                await session.get(base_url, timeout=aiohttp.ClientTimeout(total=6))
            except Exception:
                pass

            async with session.get(clean_target_url, timeout=aiohttp.ClientTimeout(total=14)) as resp:
                if resp.status == 200:
                    html_text = await resp.text()
                    if len(html_text) > 800:
                        return html_text
    except Exception as e:
        logger.warning(f"[DIRECT BROWSER FETCH NOTICE] {e}")

    raise Exception(
        "The SSC server is actively geo-blocking cloud IPs or this challenge key has expired.\n\n"
        "💡 **Instant Fix:** Open the link on your phone or PC in Chrome, press `Ctrl + S` "
        "(or tap 3 dots $\rightarrow$ Share $\rightarrow$ Download page), and send the downloaded `.html` file directly to this bot!"
    )


def parse_universal_ssc_answer_key(html_content: str, source_url: str = "") -> dict:
    soup = BeautifulSoup(html_content, "html.parser")
    candidate_meta = {}

    # 1. Candidate Info Table Parsing (Compatible with Ediquity & TCS iON)
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) >= 2:
            lbl = cells[0].get_text(strip=True).replace(":", "")
            val = cells[1].get_text(strip=True)
            if "Roll No" in lbl or "Roll Number" in lbl: candidate_meta["roll_number"] = val
            elif "Candidate Name" in lbl: candidate_meta["candidate_name"] = val
            elif "Exam Level" in lbl or "Subject" in lbl: candidate_meta["subject_raw"] = val
            elif "Test Date" in lbl or "Exam Date" in lbl: candidate_meta["exam_date"] = val
            elif "Test Time" in lbl or "Exam Time" in lbl or "Shift" in lbl: candidate_meta["shift_time"] = val
            elif "Centre Name" in lbl or "Venue Name" in lbl: candidate_meta["venue_name"] = val

    full_text = soup.get_text()
    if not candidate_meta.get("roll_number"):
        m = re.search(r"Roll\s*No\.?\s*:\s*([A-Za-z0-9]+)", full_text, re.IGNORECASE)
        if m: candidate_meta["roll_number"] = m.group(1).strip()
    if not candidate_meta.get("candidate_name"):
        m = re.search(r"Candidate\s*Name\s*:\s*([A-Za-z\s]+)", full_text, re.IGNORECASE)
        if m: candidate_meta["candidate_name"] = m.group(1).split("\n")[0].strip()
    if not candidate_meta.get("exam_date"):
        m = re.search(r"(?:Test|Exam)\s*Date\s*:\s*([0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4}|[0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})", full_text, re.IGNORECASE)
        if m: candidate_meta["exam_date"] = m.group(1).strip()
    if not candidate_meta.get("shift_time"):
        m = re.search(r"(?:Test|Exam)\s*Time\s*(?:and\s*Shift)?\s*:\s*([^\n\r]+)", full_text, re.IGNORECASE)
        if m: candidate_meta["shift_time"] = m.group(1).split("Centre")[0].strip()

    full_check = (source_url + " " + candidate_meta.get("subject_raw", "") + " " + full_text[:1200]).upper()
    exam_code = "SSC_CGL_PRE"
    if "CGL" in full_check or "CGLE" in full_check:
        exam_code = "SSC_CGL_MAINS" if any(w in full_check for w in ("MAINS", "TIER II", "TIER-II", "TIER 2")) else "SSC_CGL_PRE"
    elif "CHSL" in full_check:
        exam_code = "SSC_CHSL_MAINS" if any(w in full_check for w in ("MAINS", "TIER II", "TIER-II", "TIER 2")) else "SSC_CHSL_PRE"
    elif "CPO" in full_check:
        exam_code = "SSC_CPO_PRE"
    elif "GD" in full_check:
        exam_code = "SSC_GD"
    elif "NTPC" in full_check or "RRB" in full_check:
        exam_code = "RRB_NTPC"

    cfg = EXAM_CONFIGS.get(exam_code, EXAM_CONFIGS["SSC_CGL_PRE"])

    def resolve_section(sec_name_raw: str) -> tuple[str, str, bool]:
        s = sec_name_raw.strip()
        if cfg.get("qualifying"):
            q_info = cfg["qualifying"]
            if any(qn.lower() in s.lower() for qn in q_info["names"]):
                return q_info["key"], "Computer Knowledge", True
        for k, aliases in cfg["sections"].items():
            for alias in aliases:
                if alias.lower() in s.lower():
                    disp = "Maths" if k == "math" else ("Reasoning" if k == "reas" else ("English" if k == "eng" else "General Awareness"))
                    return k, disp, False
        return "ga", "General Awareness", False

    sectional_data = {}
    merit_score = 0.0
    qual_score = 0.0
    total_correct = 0
    total_wrong = 0
    total_unattempted = 0
    parsed_questions = []

    # ==========================================================
    # BRANCH A: SSC EDIQUITY PATTERN (cbexams.com / ViewCandResponse.aspx)
    # ==========================================================
    is_ediquity = ("cbexams" in source_url.lower()) or ("Correct Option selected" in full_text) or ("Click Here to Challenge" in full_text)

    if is_ediquity:
        current_sec_key = "reas"
        current_sec_label = "Reasoning"
        current_is_qual = False

        q_elements = soup.find_all(text=re.compile(r"(?:Click Here to Challenge Question No\.?:?|Q\.No\s*:)\s*(\d+)", re.IGNORECASE))
        seen_q_nums = set()
        question_counter = 0

        for q_el in q_elements:
            parent_block = q_el.find_parent("table") or q_el.find_parent("div")
            if not parent_block:
                continue

            block_text = parent_block.get_text()
            m_num = re.search(r"(?:Question No\.?:?|Q\.No\s*:)\s*(\d+)", block_text, re.IGNORECASE)
            q_num_str = m_num.group(1) if m_num else str(question_counter + 1)
            if q_num_str in seen_q_nums:
                continue
            seen_q_nums.add(q_num_str)
            question_counter += 1

            sec_header = parent_block.find_previous(text=re.compile(r"PART\s*[-–]\s*[A-D]|Module|Section", re.IGNORECASE))
            if sec_header:
                current_sec_key, current_sec_label, current_is_qual = resolve_section(str(sec_header))

            q_html = str(parent_block)
            is_answered_correct = ("Correct Option selected" in block_text) or ("Correct Option selected" in q_html)
            is_answered_wrong = ("Wrong Option selected" in block_text) or ("Wrong Option selected" in q_html)

            pos_m = cfg.get("comp_pos", 3.0) if current_is_qual else cfg["pos_mark"]
            neg_m = cfg.get("comp_neg", 1.0) if current_is_qual else cfg["neg_mark"]

            chosen_opt = "--"
            right_opt = "1"
            delta = 0.0

            if is_answered_correct and not is_answered_wrong:
                q_status = "CORRECT"
                delta = pos_m
                total_correct += 1
                chosen_opt = "Correct"
                right_opt = "Correct"
            elif is_answered_wrong:
                q_status = "WRONG"
                delta = -neg_m
                total_wrong += 1
                chosen_opt = "Wrong"
                right_opt = "Key"
            else:
                q_status = "UNATTEMPTED"
                total_unattempted += 1

            if current_is_qual:
                qual_score += delta
            else:
                merit_score += delta

            if current_sec_key not in sectional_data:
                sectional_data[current_sec_key] = {"label": current_sec_label, "score": 0.0, "correct": 0, "wrong": 0, "unattempted": 0, "is_qual": current_is_qual}
            sectional_data[current_sec_key]["score"] = round(sectional_data[current_sec_key]["score"] + delta, 2)
            if q_status == "CORRECT": sectional_data[current_sec_key]["correct"] += 1
            elif q_status == "WRONG": sectional_data[current_sec_key]["wrong"] += 1
            else: sectional_data[current_sec_key]["unattempted"] += 1

            q_clean_txt = re.sub(r"Click Here to Challenge.*", "", block_text, flags=re.DOTALL).strip()
            lines = [l.strip() for l in q_clean_txt.splitlines() if l.strip() and "Q.No" not in l and "Option" not in l]
            body_preview = " ".join(lines[:3])[:220] or f"Question #{q_num_str}"

            parsed_questions.append({
                "num": int(q_num_str) if q_num_str.isdigit() else question_counter,
                "q_id": f"Q{q_num_str}",
                "sec_key": current_sec_key,
                "sec_label": current_sec_label,
                "text": body_preview,
                "status": q_status,
                "chosen": chosen_opt,
                "right": right_opt,
                "delta": delta
            })

    # ==========================================================
    # BRANCH B: TCS iON PATTERN (digialm.com)
    # ==========================================================
    else:
        panels = soup.find_all("div", class_=re.compile(r"question-pnl|rw|menu-tbl")) or soup.find_all("table", class_=re.compile(r"questionPnl|menu-tbl"))
        for idx, p in enumerate(panels, start=1):
            p_text = p.get_text()
            if "Question ID" not in p_text and "Chosen Option" not in p_text and "Option 1 ID" not in p_text:
                continue

            q_id = f"Q{idx}"
            status = "Not Answered"
            chosen_opt = None

            for row in p.find_all("tr"):
                row_str = row.get_text(strip=True)
                if "Question ID" in row_str:
                    tds = row.find_all("td")
                    if len(tds) >= 2: q_id = tds[1].get_text(strip=True)
                elif "Status" in row_str:
                    tds = row.find_all("td")
                    if len(tds) >= 2: status = tds[1].get_text(strip=True)
                elif "Chosen Option" in row_str:
                    tds = row.find_all("td")
                    if len(tds) >= 2: chosen_opt = tds[1].get_text(strip=True)

            right_opt = "1"
            opts_tbl = p.find("table")
            if opts_tbl:
                for opt_idx, opt_row in enumerate(opts_tbl.find_all("tr"), start=1):
                    if opt_row.find(class_=re.compile(r"rightAns|correct|tick")) or opt_row.find("img", src=re.compile(r"correct|right")):
                        right_opt = str(opt_idx)
                        break

            sec_div = p.find_previous("div", class_=re.compile(r"section-lbl|section-cntnr|sectionName"))
            raw_sec = sec_div.get_text(strip=True).replace("Section :", "").strip() if sec_div else "General"
            sec_key, sec_label, is_qual = resolve_section(raw_sec)

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

            q_el = p.find("td", class_=re.compile(r"bold|question"))
            body_preview = q_el.get_text(strip=True)[:220] if q_el else f"Question #{idx}"

            parsed_questions.append({
                "num": idx,
                "q_id": q_id,
                "sec_key": sec_key,
                "sec_label": sec_label,
                "text": body_preview,
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
# PDF GENERATORS
# ==============================================================================
def generate_rank_report_card_pdf(user_id: int, eval_data: dict, rank_data: dict) -> str:
    cand = eval_data["candidate"]
    exam_title = eval_data["exam_title"]
    now_str = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")

    sec_rows = ""
    for k, s in eval_data["sectional"].items():
        qual_badge = " *(Qualifying Gate)*" if s.get("is_qual") else ""
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
        <b>📊 Shift Performance:</b> Shift Average Score: <b>{round(float(rank_data.get('shift_avg_score') or 0.0), 1)}</b> | Category Rank: <b>#{rank_data.get('category_rank', 1)}</b>.
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
                <th style="width:12%; text-align:center;">Candidate Choice</th>
                <th style="width:12%; text-align:center;">Official Key</th>
                <th style="width:12%; text-align:center;">Evaluation</th>
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
# PIPELINE EXECUTION HELPER
# ==============================================================================
async def process_and_reply_answer_key(html_content: str, source_identifier: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    status_msg = await update.message.reply_text("⏳ **Evaluating Sectional Answer Key & Computing Rank...**\nPlease wait 3-5 seconds...")

    try:
        profile = get_user_profile(user_id) or {}
        category = profile.get("category", "UR")

        eval_data = await asyncio.to_thread(parse_universal_ssc_answer_key, html_content, source_identifier)
        rank_data = await asyncio.to_thread(save_and_calculate_rank_stats, user_id, eval_data, source_identifier, category)

        cand = eval_data["candidate"]
        roll_no = cand.get("roll_number", str(user_id))
        context.user_data["last_evaluated_roll"] = roll_no

        sec_lines = []
        for k, s in eval_data["sectional"].items():
            qual_note = " *(Qualifying Gate)*" if s.get("is_qual") else ""
            sec_lines.append(f"• **{s['label']}{qual_note}:** `{s['score']}` (✅ {s['correct']} | ❌ {s['wrong']} | ⏭ {s['unattempted']})")

        qual_info_line = f"🖥️ **Computer Gate Score:** `{eval_data['qual_score']}`\n" if eval_data.get("qual_score") else ""

        result_msg = (
            f"🎉 **EVALUATION COMPLETE — {eval_data['exam_title'].upper()}** 🎉\n"
            f"• • • ✧ • • •\n"
            f"👤 **Candidate:** {cand.get('candidate_name', 'Student')}\n"
            f"🪪 **Roll No:** `{roll_no}` | 🏷 **Category:** `{category}`\n"
            f"📅 **Date & Shift:** `{cand.get('exam_date', 'N/A')} ({cand.get('shift_time', 'N/A')})`\n"
            f"• • • ✧ • • •\n"
            f"🎯 **TOTAL RAW MERIT SCORE:** `{eval_data['merit_score']} / {int(eval_data['max_merit'])}`\n"
            f"🚀 **ESTIMATED NORMALIZED SCORE:** `{rank_data.get('normalized_score', eval_data['merit_score'])}`\n"
            f"{qual_info_line}"
            f"🏆 **Overall Rank:** `#{rank_data.get('overall_rank', 1)}` / `{rank_data.get('total_students', 1)}`\n"
            f"⏱ **Shift Rank:** `#{rank_data.get('shift_rank', 1)}` / `{rank_data.get('total_shift_students', 1)}`\n"
            f"📊 **Shift Average:** `{round(float(rank_data.get('shift_avg_score') or 0.0), 1)}`\n"
            f"• • • ✧ • • •\n"
            f"📚 **SECTIONAL BREAKDOWN:**\n" + "\n".join(sec_lines) + "\n\n"
            f"👇 *Download your Official Master Scorecard or Section-Wise Answer Keys below:*"
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
        await status_msg.edit_text(f"❌ **Error evaluating answer key:** {e}")


# ==============================================================================
# TELEGRAM BOT HANDLERS & URL/FILE INTERCEPTION
# ==============================================================================
async def rank_calculator_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🎯 **SSC / RRB ANSWER KEY & REAL-TIME RANK CALCULATOR** 🎯\n"
        "• • • ✧ • • •\n"
        "Send your official response sheet by **either method**:\n\n"
        "1️⃣ **Paste Link:** Paste your official `cbexams.com` or `digialm.com` URL directly in this chat.\n"
        "2️⃣ **Send Saved HTML:** If your server link is firewalled or expired, open it in Chrome, click `Share / Save as HTML`, and upload the `.html` file here!\n\n"
        "👉 **Supported SSC & Central Exams:**\n"
        "• **SSC CGL 2026** (Tier-I Pre & Tier-II Mains with Computer Gate)\n"
        "• **SSC CHSL** (Pre & Mains)\n"
        "• **SSC CPO, MTS, GD Constable, Steno**\n"
        "• **RRB NTPC, Group D, ALP**\n\n"
        "⚡ *Paste your link or send your .html file below:* "
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, parse_mode="Markdown")


async def handle_response_sheet_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip() if update.message.text else ""
    is_valid_url = any(domain in text.lower() for domain in (
        "cbexams", "sscexams", "digialm", "ediquity", "assessmenthtml", "per/g01", "viewcandresponse"
    ))
    if not is_valid_url:
        return

    fetch_msg = await update.message.reply_text("⏳ **Connecting to examination server and fetching answer key...**")
    try:
        html = await fetch_answer_key_html(text)
        await fetch_msg.delete()
        await process_and_reply_answer_key(html, text, update, context)
    except Exception as e:
        logger.error(f"[FETCH ERROR] {e}")
        await fetch_msg.edit_text(
            f"❌ **{e}**\n\n"
            f"📁 **Instant Fix:** Open your response sheet in Chrome, press `Ctrl + S` (or Mobile: 3 dots $\rightarrow$ Share $\rightarrow$ Download), "
            f"and send the downloaded `.html` file directly to this bot!"
        )


async def handle_html_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles saved .html / .htm answer key files uploaded directly by students."""
    doc = update.message.document
    if not doc or not doc.file_name:
        return
    
    fname = doc.file_name.lower()
    if not (fname.endswith(".html") or fname.endswith(".htm") or fname.endswith(".txt")):
        return

    wait_msg = await update.message.reply_text("📥 **Received HTML Response Sheet! Reading file...**")
    try:
        tg_file = await context.bot.get_file(doc.file_id)
        file_bytes = await tg_file.download_as_bytearray()
        html_content = file_bytes.decode("utf-8", errors="ignore")
        await wait_msg.delete()
        await process_and_reply_answer_key(html_content, doc.file_name, update, context)
    except Exception as e:
        logger.error(f"[HTML FILE READ ERROR] {e}")
        await wait_msg.edit_text(f"❌ Failed to parse HTML file: {e}")


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
    
    # 1. URL Handler: Intercepts all SSC Ediquity, cbexams, and TCS iON links
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r"(cbexams|sscexams|digialm|ediquity|assessmenthtml|per/g01|viewcandresponse)"),
        handle_response_sheet_url
    ))
    
    # 2. Document Handler: Processes downloaded .html response sheets directly
    app.add_handler(MessageHandler(
        filters.Document.ALL & ~filters.COMMAND,
        handle_html_document
    ))