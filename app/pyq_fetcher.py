import json
import os
import random
import re
import logging
from datetime import datetime
from app.config import DATA_DIR, QUESTION_BANK_DIR, TOPICS_DIR
from app.database import get_db, release_db, reset_user_seen_questions_for_ids

logger = logging.getLogger(__name__)

# Clean Display Topic Names (Zero question counts exposed)
TOPIC_METADATA = {
    "Computer_Hardware_Architecture": {
        "en": "🖥️ Computer Basics & Architecture",
        "hi": "🖥️ कंप्यूटर की मूल बातें और आर्किटेक्चर"
    },
    "Memory_Storage": {
        "en": "💾 Memory & Storage Devices",
        "hi": "💾 मेमोरी और स्टोरेज डिवाइस"
    },
    "Operating_Systems_CLI": {
        "en": "⚙️ Operating Systems & CLI",
        "hi": "⚙️ ऑपरेटिंग सिस्टम और CLI"
    },
    "MS_Word": {
        "en": "📝 Microsoft Word",
        "hi": "📝 माइक्रोसॉफ्ट वर्ड"
    },
    "MS_Excel": {
        "en": "📊 Microsoft Excel",
        "hi": "📊 माइक्रोसॉफ्ट एक्सेल"
    },
    "MS_PowerPoint_365": {
        "en": "📽️ PowerPoint & OneNote",
        "hi": "📽️ पावरपॉइंट और वननोट"
    },
    "Networking_Internet": {
        "en": "🌐 Networking & Internet",
        "hi": "🌐 नेटवर्किंग और इंटरनेट"
    },
    "Cybersecurity_Malware": {
        "en": "🛡️ Cybersecurity & Malware",
        "hi": "🛡️ साइबर सुरक्षा और मैलवेयर"
    },
    "Number_Systems": {
        "en": "🔢 Number Systems & Codes",
        "hi": "🔢 संख्या प्रणाली और कंप्यूटर कोड्स"
    },
    "General_Computer_Awareness": {
        "en": "💡 General Computer Awareness",
        "hi": "💡 सामान्य कंप्यूटर जागरूकता"
    }
}

def get_available_topics(language: str = "en") -> list:
    """Returns list of tuples (topic_key, clean_display_name)."""
    lang_key = "hi" if language == "hi" else "en"
    return [(k, v.get(lang_key, v["en"])) for k, v in TOPIC_METADATA.items()]

def is_hindi_text(text: str) -> bool:
    """Detects if text contains Devanagari (Hindi) Unicode characters."""
    if not text:
        return False
    return any('\u0900' <= char <= '\u097F' for char in text)

def clean_option_prefix(opt_text: str) -> str:
    """Removes hardcoded prefixes like 'A. ', 'B) ' so option shuffling looks clean."""
    return re.sub(r'^[A-Da-d1-4][\.\)]\s*', '', str(opt_text)).strip()

def randomize_question_options(q: dict) -> dict:
    """
    Shuffles options cleanly while maintaining the correct answer mapping.
    """
    opts = list(q.get("options", []))
    correct_idx = q.get("correct_option", 0)
    if not opts or correct_idx >= len(opts) or correct_idx < 0:
        return q

    clean_options = [clean_option_prefix(opt) for opt in opts]
    correct_answer_value = clean_options[correct_idx]

    shuffled_opts = list(clean_options)
    random.shuffle(shuffled_opts)

    new_correct_idx = shuffled_opts.index(correct_answer_value)

    new_q = dict(q)
    new_q["options"] = shuffled_opts
    new_q["correct_option"] = new_correct_idx
    return new_q

def verify_and_correct_question(q: dict) -> dict:
    q_text = q.get("question")
    opts = q.get("options")
    correct_opt = q.get("correct_option")
    expl = q.get("explanation", "")

    if not q_text or not isinstance(opts, list) or len(opts) < 2:
        return None

    clean_opts = [str(opt).strip() for opt in opts]

    if not isinstance(correct_opt, int) or correct_opt < 0 or correct_opt >= len(clean_opts):
        correct_opt = 0

    detected_lang = "hi" if is_hindi_text(str(q_text)) else "en"

    return {
        "id": q.get("id") if q.get("id") is not None else str(hash(str(q_text))),
        "question": str(q_text).strip(),
        "options": clean_opts,
        "correct_option": correct_opt,
        "explanation": str(expl).strip(),
        "language": q.get("language", detected_lang),
        "chapter": q.get("chapter", "Computer Awareness")
    }

def get_user_seen_identifiers(user_id: int) -> tuple[set, set]:
    """
    Fetches all seen question IDs and question texts for this user.
    """
    if not user_id:
        return set(), set()

    seen_ids = set()
    seen_texts = set()

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT question_id FROM seen_questions WHERE user_id = %s", (user_id,))
        for row in cursor.fetchall():
            seen_ids.add(str(row[0]))
    except Exception as e:
        logger.error(f"[FETCH SEEN IDS ERROR] {e}")
    finally:
        cursor.close()
        release_db(conn)

    return seen_ids, seen_texts

def fetch_pyqs_for_quiz(needed_count: int = 20, seen_ids: set = None, language: str = "en", user_id: int = None, topic: str = "MIXED") -> list:
    """
    STRICT NON-REPETITION EXHAUSTION CYCLING ENGINE:
    1. Loads all questions belonging to the selected Topic or Mixed Bank.
    2. Separates questions into UNSEEN vs SEEN for this student.
    3. Serves ONLY UNSEEN questions first in randomized order.
    4. If the student has exhausted the entire topic bank (0 unseen left),
       it resets their seen history for that topic and serves a freshly randomized set.
    """
    all_raw_questions = []
    lang_sub = "hi" if language == "hi" else "en"

    # 1. Load Topic-Specific File or Master Question Bank
    if topic and topic != "MIXED":
        topic_filename = f"{topic}_{lang_sub}.json"
        potential_topic_paths = [
            os.path.join(TOPICS_DIR, lang_sub, topic_filename),
            os.path.join(TOPICS_DIR, topic_filename),
            os.path.join(DATA_DIR, "topics", lang_sub, topic_filename)
        ]
        
        loaded_topic = False
        for p in potential_topic_paths:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            all_raw_questions.extend(data)
                            loaded_topic = True
                            break
                except Exception as e:
                    logger.error(f"Error loading topic file {p}: {e}")

        # Fallback to master file if individual topic file is missing
        if not loaded_topic:
            master_file_name = "all_questions_hindi.json" if language == "hi" else "all_questions_english.json"
            master_p = os.path.join(QUESTION_BANK_DIR, "hindi" if language == "hi" else "", master_file_name)
            if os.path.exists(master_p):
                try:
                    with open(master_p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            all_raw_questions.extend(data)
                except Exception:
                    pass
    else:
        # Load Entire 913-Question Master Bank for Mixed Practice
        master_file_name = "all_questions_hindi.json" if language == "hi" else "all_questions_english.json"
        master_candidates = [
            os.path.join(QUESTION_BANK_DIR, "hindi", master_file_name) if language == "hi" else os.path.join(QUESTION_BANK_DIR, master_file_name),
            os.path.join(DATA_DIR, "hindi", master_file_name) if language == "hi" else os.path.join(DATA_DIR, master_file_name)
        ]

        for m_path in master_candidates:
            if os.path.exists(m_path):
                try:
                    with open(m_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, list) and len(data) > 0:
                            all_raw_questions.extend(data)
                            break
                except Exception as e:
                    logger.error(f"Error loading master file {m_path}: {e}")

    # 2. Filter, Verify, and Deduplicate Question Bank
    verified_bank = []
    seen_unique_texts = set()

    for q in all_raw_questions:
        verified_q = verify_and_correct_question(q)
        if verified_q:
            q_lang = verified_q.get("language", "en")
            q_is_hindi = is_hindi_text(verified_q["question"])
            norm_text = re.sub(r'\W+', '', verified_q["question"].lower())

            if norm_text in seen_unique_texts:
                continue
            seen_unique_texts.add(norm_text)

            if language == "hi":
                if q_lang == "hi" or q_is_hindi:
                    verified_bank.append(verified_q)
            else:
                if q_lang != "hi" and not q_is_hindi:
                    verified_bank.append(verified_q)

    if not verified_bank:
        return []

    # 3. Retrieve User History for Non-Repetition
    user_seen_ids, _ = get_user_seen_identifiers(user_id)
    if seen_ids:
        user_seen_ids.update({str(sid) for sid in seen_ids})

    unseen_pool = []
    seen_pool = []
    all_topic_qids = []

    for q in verified_bank:
        qid_str = str(q.get("id"))
        all_topic_qids.append(qid_str)

        if qid_str not in user_seen_ids:
            unseen_pool.append(q)
        else:
            seen_pool.append(q)

    # 4. Strict Selection Logic
    selected_questions = []

    if len(unseen_pool) >= needed_count:
        # Case A: Sufficient unseen questions available
        random.shuffle(unseen_pool)
        selected_questions = unseen_pool[:needed_count]

    elif 0 < len(unseen_pool) < needed_count:
        # Case B: Partially exhausted topic -> serve remaining unseen + fill from seen, then reset seen history
        selected_questions.extend(unseen_pool)
        deficit = needed_count - len(selected_questions)

        random.shuffle(seen_pool)
        selected_questions.extend(seen_pool[:deficit])

        if user_id:
            reset_user_seen_questions_for_ids(user_id, all_topic_qids)

    else:
        # Case C: 100% of questions in this topic answered -> reset topic history & serve fresh random shuffle
        if user_id:
            reset_user_seen_questions_for_ids(user_id, all_topic_qids)

        random.shuffle(verified_bank)
        selected_questions = verified_bank[:needed_count]

    # 5. Final Shuffle of Questions and Options
    random.shuffle(selected_questions)
    return [randomize_question_options(q) for q in selected_questions]