import json
import os
import random
import re
import logging
from datetime import datetime
from app.config import DATA_DIR, QUESTION_BANK_DIR, TOPICS_DIR, SHORTCUT_KEYS_DIR, BASE_DIR
from app.database import get_db, release_db, reset_user_seen_questions_for_ids

logger = logging.getLogger(__name__)

# Clean Display Topic Names for Computer
COMPUTER_TOPIC_METADATA = {
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
    },
    "SHORTCUTS": {
        "en": "⌨️ Computer Shortcut Keys",
        "hi": "⌨️ कंप्यूटर शॉर्टकट कुंजियाँ"
    }
}

# Clean Display Topic Names for General Knowledge (GK)
GK_TOPIC_METADATA = {
    "Indian History - Ancient & Medieval": {
        "en": "🏛️ Indian History - Ancient & Medieval",
        "hi": "🏛️ भारतीय इतिहास - प्राचीन एवं मध्यकालीन"
    },
    "Indian History - Modern & Freedom Struggle": {
        "en": "⚔️ Indian History - Modern & Freedom Struggle",
        "hi": "⚔️ भारतीय इतिहास - आधुनिक एवं स्वतंत्रता संग्राम"
    },
    "Indian Polity & Constitution": {
        "en": "⚖️ Indian Polity & Constitution",
        "hi": "⚖️ भारतीय राजव्यवस्था एवं संविधान"
    },
    "Indian & World Geography": {
        "en": "🌍 Indian & World Geography",
        "hi": "🌍 भारतीय एवं विश्व भूगोल"
    },
    "Indian Economy & General Awareness": {
        "en": "📈 Indian Economy & General Awareness",
        "hi": "📈 भारतीय अर्थव्यवस्था एवं सामान्य जागरूकता"
    },
    "General Science - Physics & Chemistry": {
        "en": "🧪 General Science - Physics & Chemistry",
        "hi": "🧪 सामान्य विज्ञान - भौतिकी एवं रसायन"
    },
    "General Science - Biology & Environment": {
        "en": "🌿 General Science - Biology & Environment",
        "hi": "🌿 सामान्य विज्ञान - जीव विज्ञान एवं पर्यावरण"
    },
    "Static GK - Art, Culture & Heritage": {
        "en": "🎨 Static GK - Art, Culture & Heritage",
        "hi": "🎨 स्टैटिक जीके - कला, संस्कृति एवं धरोवर"
    },
    "Static GK - Festivals, Fairs & Temples": {
        "en": "🛕 Static GK - Festivals, Fairs & Temples",
        "hi": "🛕 स्टैटिक जीके - त्यौहार, मेले एवं मंदिर"
    },
    "Static GK - Sports & Awards": {
        "en": "🏆 Static GK - Sports & Awards",
        "hi": "🏆 स्टैटिक जीके - खेल एवं पुरस्कार"
    }
}

def get_available_topics(subject: str = "computer", language: str = "en") -> list:
    """Returns list of tuples (topic_key, clean_display_name)."""
    lang_key = "hi" if language == "hi" else "en"
    metadata = GK_TOPIC_METADATA if subject == "gk" else COMPUTER_TOPIC_METADATA
    return [(k, v.get(lang_key, v["en"])) for k, v in metadata.items()]

def is_hindi_text(text: str) -> bool:
    if not text:
        return False
    return any('\u0900' <= char <= '\u097F' for char in str(text))

def clean_option_prefix(opt_text: str) -> str:
    return re.sub(r'^[A-Da-d1-4][\.\)]\s*', '', str(opt_text)).strip()

def randomize_question_options(q: dict) -> dict:
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
    q_text = q.get("question")
    raw_opts = q.get("options")
    raw_correct = q.get("correct_option")
    expl = q.get("explanation", "")
    category = q.get("category", "General")

    if not q_text or not raw_opts:
        return None

    clean_opts = []
    correct_idx = 0

    if isinstance(raw_opts, dict):
        sorted_keys = sorted(raw_opts.keys())
        clean_opts = [str(raw_opts[k]).strip() for k in sorted_keys]
        
        if isinstance(raw_correct, str) and raw_correct.upper() in sorted_keys:
            correct_idx = sorted_keys.index(raw_correct.upper())
        elif isinstance(raw_correct, int) and 0 <= raw_correct < len(clean_opts):
            correct_idx = raw_correct

    elif isinstance(raw_opts, list):
        clean_opts = [str(opt).strip() for opt in raw_opts]
        if isinstance(raw_correct, int) and 0 <= raw_correct < len(clean_opts):
            correct_idx = raw_correct
        elif isinstance(raw_correct, str) and raw_correct.upper() in ("A", "B", "C", "D"):
            mapping = {"A": 0, "B": 1, "C": 2, "D": 3}
            correct_idx = mapping.get(raw_correct.upper(), 0)

    if len(clean_opts) < 2:
        return None

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

def fetch_pyqs_for_quiz(needed_count: int = 20, seen_ids: set = None, language: str = "en", user_id: int = None, topic: str = "MIXED", subject: str = "computer") -> list:
    """STRICT SUBJECT, LANGUAGE & TOPIC ISOLATION ENGINE WITH EXACT FOLDER MAPPING."""
    all_raw_questions = []
    lang_sub = "hi" if language == "hi" else "en"
    loaded_from_specific_file = False

    # 1. GENERAL KNOWLEDGE (GK) MODE
    if subject == "gk":
        gk_file_name = f"gk_questions_{lang_sub}.json"
        potential_gk_paths = [
            os.path.join(QUESTION_BANK_DIR, "gk", gk_file_name),
            os.path.join(DATA_DIR, "question_bank", "gk", gk_file_name),
            os.path.join(BASE_DIR, "data", "question_bank", "gk", gk_file_name)
        ]

        for p in potential_gk_paths:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            all_raw_questions.extend(data)
                            break
                except Exception as e:
                    logger.error(f"Error reading GK file {p}: {e}")

    # 2. SHORTCUT KEYS MODE (COMPUTER)
    elif topic == "SHORTCUTS":
        shortcut_file_name = f"shortcut_{lang_sub}.json"
        potential_shortcut_paths = [
            os.path.join(QUESTION_BANK_DIR, "computer", "shortcut_keys", shortcut_file_name),
            os.path.join(DATA_DIR, "question_bank", "computer", "shortcut_keys", shortcut_file_name),
            os.path.join(BASE_DIR, "data", "question_bank", "computer", "shortcut_keys", shortcut_file_name)
        ]

        for p in potential_shortcut_paths:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            all_raw_questions.extend(data)
                            loaded_from_specific_file = True
                            break
                except Exception as e:
                    logger.error(f"Error reading shortcut keys file {p}: {e}")

    # 3. TOPIC-WISE MODE (COMPUTER)
    elif topic and topic != "MIXED":
        topic_filename = f"{topic}_{lang_sub}.json"
        potential_topic_paths = [
            os.path.join(TOPICS_DIR, "computer", lang_sub, topic_filename),
            os.path.join(DATA_DIR, "topics", "computer", lang_sub, topic_filename),
            os.path.join(BASE_DIR, "data", "topics", "computer", lang_sub, topic_filename)
        ]
        
        for p in potential_topic_paths:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            all_raw_questions.extend(data)
                            loaded_from_specific_file = True
                            break
                except Exception as e:
                    logger.error(f"Error loading topic file {p}: {e}")

    # 4. MIXED PRACTICE MODE & MASTER BANK FALLBACK (COMPUTER)
    if subject == "computer" and not loaded_from_specific_file:
        master_file_name = "all_questions_hindi.json" if language == "hi" else "all_questions_english.json"
        master_candidates = [
            os.path.join(QUESTION_BANK_DIR, "computer", master_file_name),
            os.path.join(DATA_DIR, "question_bank", "computer", master_file_name),
            os.path.join(BASE_DIR, "data", "question_bank", "computer", master_file_name)
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

    # 5. STRICT CATEGORY FILTERING & LANGUAGE VERIFICATION
    verified_bank = []
    seen_unique_texts = set()

    for q in all_raw_questions:
        if not loaded_from_specific_file and topic and topic != "MIXED":
            cat = str(q.get("category", "")).strip()
            cat_clean = cat.replace("_", " ").lower()
            topic_clean = topic.replace("_", " ").lower()
            if cat_clean != topic_clean:
                continue

        verified_q = verify_and_correct_question(q, force_lang=language)
        if verified_q:
            q_is_hindi = is_hindi_text(verified_q["question"])
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
        logger.warning(f"No questions verified for subject={subject}, topic={topic}, lang={language}")
        return []

    # 6. NON-REPETITION & EXHAUSTION CYCLING
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

    if len(unseen_pool) >= needed_count:
        random.shuffle(unseen_pool)
        selected_questions = unseen_pool[:needed_count]
    elif 0 < len(unseen_pool) < needed_count:
        selected_questions.extend(unseen_pool)
        deficit = needed_count - len(selected_questions)

        random.shuffle(seen_pool)
        selected_questions.extend(seen_pool[:deficit])

        if user_id:
            reset_user_seen_questions_for_ids(user_id, all_current_qids)
    else:
        if user_id:
            reset_user_seen_questions_for_ids(user_id, all_current_qids)

        random.shuffle(verified_bank)
        selected_questions = verified_bank[:needed_count]

    random.shuffle(selected_questions)
    return [randomize_question_options(q) for q in selected_questions]

def fetch_multi_topic_questions(needed_count: int, topic_keys: list, subject: str = "computer", language: str = "en", user_id: int = None) -> list:
    """Fetches unique, evenly distributed questions across 2 to 4 selected topics."""
    if not topic_keys:
        return []

    num_topics = len(topic_keys)
    base_per_topic = needed_count // num_topics
    remainder = needed_count % num_topics

    combined_questions = []
    allocated_topics = list(topic_keys)
    random.shuffle(allocated_topics)

    seen_ids = get_user_seen_identifiers(user_id)

    for i, t_key in enumerate(allocated_topics):
        count_for_this = base_per_topic + (1 if i < remainder else 0)
        if count_for_this <= 0:
            continue
        t_qs = fetch_pyqs_for_quiz(needed_count=count_for_this, seen_ids=seen_ids, language=language, user_id=user_id, topic=t_key, subject=subject)
        for q in t_qs:
            seen_ids.add(str(q.get("id")))
        combined_questions.extend(t_qs)

    if len(combined_questions) < needed_count:
        deficit = needed_count - len(combined_questions)
        extra_qs = fetch_pyqs_for_quiz(needed_count=deficit, seen_ids=seen_ids, language=language, user_id=user_id, topic="MIXED", subject=subject)
        combined_questions.extend(extra_qs)

    random.shuffle(combined_questions)
    return combined_questions[:needed_count]

def fetch_full_mock_questions(needed_count: int = 20, language: str = "en", user_id: int = None) -> list:
    """Generates balanced questions split between Computer Awareness and General Knowledge."""
    comp_count = needed_count // 2
    gk_count = needed_count - comp_count

    seen_ids = get_user_seen_identifiers(user_id)
    comp_qs = fetch_pyqs_for_quiz(needed_count=comp_count, seen_ids=seen_ids, language=language, user_id=user_id, topic="MIXED", subject="computer")
    
    for q in comp_qs:
        seen_ids.add(str(q.get("id")))

    gk_qs = fetch_pyqs_for_quiz(needed_count=gk_count, seen_ids=seen_ids, language=language, user_id=user_id, topic="MIXED", subject="gk")

    mock_pool = comp_qs + gk_qs
    random.shuffle(mock_pool)
    return mock_pool