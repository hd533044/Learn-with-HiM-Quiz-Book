import json
import os
import random
import re
import logging
from datetime import datetime
from app.config import DATA_DIR, QUESTION_BANK_DIR, TOPICS_DIR, SHORTCUT_KEYS_DIR
from app.database import get_db, release_db, reset_user_seen_questions_for_ids

logger = logging.getLogger(__name__)

# Clean Display Topic Names — No numbers, no total counts
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
    return any('\u0900' <= char <= '\u097F' for char in str(text))

def clean_option_prefix(opt_text: str) -> str:
    """Removes hardcoded prefixes like 'A. ', 'B) ' for clean option shuffling."""
    return re.sub(r'^[A-Da-d1-4][\.\)]\s*', '', str(opt_text)).strip()

def randomize_question_options(q: dict) -> dict:
    """Shuffles options cleanly while maintaining the correct answer mapping."""
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

def verify_and_correct_question(q: dict, force_lang: str = None) -> dict:
    """
    Universal Parser:
    - Parses both Dictionary options ({"A": "...", "B": "..."}) and List options (["..."]).
    - Strictly validates language to eliminate cross-language contamination.
    """
    q_text = q.get("question")
    raw_opts = q.get("options")
    raw_correct = q.get("correct_option")
    expl = q.get("explanation", "")
    category = q.get("category", "General")

    if not q_text or not raw_opts:
        return None

    clean_opts = []
    correct_idx = 0

    # Format 1: Dictionary {"A": "...", "B": "...", "C": "...", "D": "..."}
    if isinstance(raw_opts, dict):
        sorted_keys = sorted(raw_opts.keys())
        clean_opts = [str(raw_opts[k]).strip() for k in sorted_keys]
        
        if isinstance(raw_correct, str) and raw_correct.upper() in sorted_keys:
            correct_idx = sorted_keys.index(raw_correct.upper())
        elif isinstance(raw_correct, int) and 0 <= raw_correct < len(clean_opts):
            correct_idx = raw_correct

    # Format 2: List ["...", "..."]
    elif isinstance(raw_opts, list):
        clean_opts = [str(opt).strip() for opt in raw_opts]
        if isinstance(raw_correct, int) and 0 <= raw_correct < len(clean_opts):
            correct_idx = raw_correct
        elif isinstance(raw_correct, str) and raw_correct.upper() in ("A", "B", "C", "D"):
            mapping = {"A": 0, "B": 1, "C": 2, "D": 3}
            correct_idx = mapping.get(raw_correct.upper(), 0)

    if len(clean_opts) < 2:
        return None

    # Determine Language
    detected_lang = "hi" if is_hindi_text(str(q_text)) else "en"
    if force_lang:
        detected_lang = force_lang

    q_id_val = q.get("id")
    unique_id = f"sc_{category}_{q_id_val}" if q_id_val is not None else str(hash(str(q_text)))

    return {
        "id": unique_id,
        "question": str(q_text).strip(),
        "options": clean_opts,
        "correct_option": correct_idx,
        "explanation": str(expl).strip(),
        "language": detected_lang,
        "chapter": category
    }

def get_user_seen_identifiers(user_id: int) -> set:
    if not user_id:
        return set()

    seen_ids = set()
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

    return seen_ids

def fetch_pyqs_for_quiz(needed_count: int = 20, seen_ids: set = None, language: str = "en", user_id: int = None, topic: str = "MIXED") -> list:
    """
    STRICT LANGUAGE & TOPIC ISOLATION ENGINE:
    - topic == "SHORTCUTS": Loads ONLY shortcut_en.json (for EN) or shortcut_hi.json (for HI).
    - topic == "<TOPIC_NAME>": Loads ONLY that topic's chapter file.
    - topic == "MIXED": Loads from the master question bank.
    """
    all_raw_questions = []
    lang_sub = "hi" if language == "hi" else "en"

    # =========================================================
    # 1. STRICT SHORTCUT KEYS MODE (NO MIXED LEAKAGE)
    # =========================================================
    if topic == "SHORTCUTS":
        shortcut_file_name = f"shortcut_{lang_sub}.json"
        potential_shortcut_paths = [
            os.path.join(SHORTCUT_KEYS_DIR, shortcut_file_name),
            os.path.join(QUESTION_BANK_DIR, "shortcut_keys", shortcut_file_name),
            os.path.join(DATA_DIR, "question_bank", "shortcut_keys", shortcut_file_name)
        ]

        for p in potential_shortcut_paths:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            all_raw_questions.extend(data)
                            break
                except Exception as e:
                    logger.error(f"Error reading shortcut keys file {p}: {e}")

    # =========================================================
    # 2. TOPIC-WISE MODE
    # =========================================================
    elif topic and topic != "MIXED":
        topic_filename = f"{topic}_{lang_sub}.json"
        potential_topic_paths = [
            os.path.join(TOPICS_DIR, lang_sub, topic_filename),
            os.path.join(TOPICS_DIR, topic_filename),
            os.path.join(DATA_DIR, "topics", lang_sub, topic_filename)
        ]
        
        for p in potential_topic_paths:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            all_raw_questions.extend(data)
                            break
                except Exception as e:
                    logger.error(f"Error loading topic file {p}: {e}")

    # =========================================================
    # 3. MIXED PRACTICE MODE (MASTER QUESTION BANK)
    # =========================================================
    else:
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

    # =========================================================
    # 4. STRICT LANGUAGE VERIFICATION & DEDUPLICATION
    # =========================================================
    verified_bank = []
    seen_unique_texts = set()

    for q in all_raw_questions:
        verified_q = verify_and_correct_question(q, force_lang=language)
        if verified_q:
            q_is_hindi = is_hindi_text(verified_q["question"])
            
            # Strict Language Gate
            if language == "hi" and not q_is_hindi:
                continue
            if language == "en" and q_is_hindi:
                continue

            norm_text = re.sub(r'\W+', '', verified_q["question"].lower())
            if norm_text in seen_unique_texts:
                continue
            seen_unique_texts.add(norm_text)
            verified_bank.append(verified_q)

    if not verified_bank:
        logger.warning(f"No questions verified for topic={topic}, lang={language}")
        return []

    # =========================================================
    # 5. STRICT NON-REPETITION & EXHAUSTION CYCLING
    # =========================================================
    user_seen_ids = get_user_seen_identifiers(user_id)
    if seen_ids:
        user_seen_ids.update({str(sid) for sid in seen_ids})

    unseen_pool = []
    seen_pool = []
    all_current_qids = []

    for q in verified_bank:
        qid_str = str(q.get("id"))
        all_current_qids.append(qid_str)

        if qid_str not in user_seen_ids:
            unseen_pool.append(q)
        else:
            seen_pool.append(q)

    selected_questions = []

    # Case A: Sufficient unseen questions available
    if len(unseen_pool) >= needed_count:
        random.shuffle(unseen_pool)
        selected_questions = unseen_pool[:needed_count]

    # Case B: Partially exhausted -> serve remaining unseen + fill from seen, then reset seen history
    elif 0 < len(unseen_pool) < needed_count:
        selected_questions.extend(unseen_pool)
        deficit = needed_count - len(selected_questions)

        random.shuffle(seen_pool)
        selected_questions.extend(seen_pool[:deficit])

        if user_id:
            reset_user_seen_questions_for_ids(user_id, all_current_qids)

    # Case C: 100% of questions in this topic answered -> reset topic history & serve fresh random shuffle
    else:
        if user_id:
            reset_user_seen_questions_for_ids(user_id, all_current_qids)

        random.shuffle(verified_bank)
        selected_questions = verified_bank[:needed_count]

    # Final randomize of questions and option choices
    random.shuffle(selected_questions)
    return [randomize_question_options(q) for q in selected_questions]