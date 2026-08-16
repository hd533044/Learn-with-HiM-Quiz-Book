import json
import os
import random
import logging
from datetime import datetime, timedelta
from app.config import DATA_DIR, QUESTION_BANK_DIR, TOPICS_DIR
from app.database import get_user_question_intel

logger = logging.getLogger(__name__)

# Official Clean Display Topic Names (NO question counts exposed to users)
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

def randomize_question_options(q: dict) -> dict:
    """
    Shuffles question options in a purely randomized way while maintaining
    the exact mapping of the correct answer index.
    """
    opts = list(q.get("options", []))
    correct_idx = q.get("correct_option", 0)
    if not opts or correct_idx >= len(opts) or correct_idx < 0:
        return q

    correct_answer_value = opts[correct_idx]
    
    # Randomize option order
    shuffled_opts = list(opts)
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
        "id": q.get("id") if q.get("id") is not None else hash(str(q_text)),
        "question": str(q_text).strip(),
        "options": clean_opts,
        "correct_option": correct_opt,
        "explanation": str(expl).strip(),
        "language": q.get("language", detected_lang),
        "chapter": q.get("chapter", "Computer Awareness")
    }

def fetch_pyqs_for_quiz(needed_count: int = 20, seen_ids: set = None, language: str = "en", user_id: int = None, topic: str = "MIXED") -> list:
    """
    Intelligent Tiered Spaced-Repetition Quiz Retrieval:
    - Supports Specific Topic File loading or Mixed (All) loading.
    - Tier 1: Fresh, unseen questions
    - Tier 2: Previously wrong & unattempted/skipped questions
    - Tier 3: Questions attempted > 10–15 days ago
    - Tier 4: Total pool recycling with randomized option shuffling
    Ensures quizzes never break or stop prematurely while user has quota.
    """
    if seen_ids is None:
        seen_ids = set()

    all_raw_questions = []
    lang_sub = "hi" if language == "hi" else "en"

    # 1. Targeted Topic File Load
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

        # Fallback if topic file not found: read general question bank and filter
        if not loaded_topic:
            search_dirs = [
                os.path.join(QUESTION_BANK_DIR, "hindi") if language == "hi" else QUESTION_BANK_DIR,
                os.path.join(DATA_DIR, "hindi") if language == "hi" else DATA_DIR
            ]
            for search_dir in search_dirs:
                if os.path.exists(search_dir):
                    for root, _, files in os.walk(search_dir):
                        for file in files:
                            if file.endswith(".json") and not file.startswith("."):
                                file_path = os.path.join(root, file)
                                try:
                                    with open(file_path, "r", encoding="utf-8") as f:
                                        data = json.load(f)
                                        if isinstance(data, list):
                                            all_raw_questions.extend(data)
                                except Exception:
                                    pass
    else:
        # 2. Mixed Practice Load (All Master Files / Question Banks)
        master_file_name = "all_questions_hindi.json" if language == "hi" else "all_questions_english.json"
        alt_master_name = "master_computer_questions_hi.json" if language == "hi" else "master_computer_questions_en.json"
        
        master_candidates = [
            os.path.join(QUESTION_BANK_DIR, "hindi", master_file_name) if language == "hi" else os.path.join(QUESTION_BANK_DIR, master_file_name),
            os.path.join(DATA_DIR, "hindi", master_file_name) if language == "hi" else os.path.join(DATA_DIR, master_file_name),
            os.path.join(DATA_DIR, alt_master_name)
        ]

        loaded_master = False
        for m_path in master_candidates:
            if os.path.exists(m_path):
                try:
                    with open(m_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, list) and len(data) > 0:
                            all_raw_questions.extend(data)
                            loaded_master = True
                            break
                except Exception as e:
                    logger.error(f"Error loading master file {m_path}: {e}")

        if not loaded_master:
            # Recursive directory scan fallback
            search_dirs = [
                TOPICS_DIR,
                QUESTION_BANK_DIR,
                DATA_DIR
            ]
            seen_paths = set()
            for search_dir in search_dirs:
                if not os.path.exists(search_dir):
                    continue
                for root, _, files in os.walk(search_dir):
                    for file in files:
                        if file.endswith(".json") and not file.startswith("."):
                            file_path = os.path.abspath(os.path.join(root, file))
                            if file_path in seen_paths:
                                continue
                            seen_paths.add(file_path)
                            try:
                                with open(file_path, "r", encoding="utf-8") as f:
                                    data = json.load(f)
                                    if isinstance(data, list):
                                        all_raw_questions.extend(data)
                            except Exception as e:
                                logger.error(f"Error reading JSON file {file_path}: {e}")

    # 3. Filter by language barrier
    verified_bank = []
    seen_q_texts = set()

    for q in all_raw_questions:
        verified_q = verify_and_correct_question(q)
        if verified_q:
            q_lang = verified_q.get("language", "en")
            q_is_hindi = is_hindi_text(verified_q["question"])
            norm_text = verified_q["question"].strip().lower()

            if norm_text in seen_q_texts:
                continue
            seen_q_texts.add(norm_text)

            if language == "hi":
                if q_lang == "hi" or q_is_hindi:
                    verified_bank.append(verified_q)
            else:
                if q_lang != "hi" and not q_is_hindi:
                    verified_bank.append(verified_q)

    if not verified_bank:
        return []

    # If no specific user telemetry available, return basic shuffled selection
    if not user_id and not seen_ids:
        random.shuffle(verified_bank)
        return [randomize_question_options(q) for q in verified_bank[:needed_count]]

    # 4. Gather User Telemetry for Multi-Tier Spaced Repetition
    user_intel = get_user_question_intel(user_id) if user_id else {
        "seen_timestamps": {str(sid): datetime.now() for sid in seen_ids},
        "wrong_or_skipped_ids": set(),
        "wrong_or_skipped_texts": set(),
        "cutoff_days": 12
    }

    seen_timestamps = user_intel.get("seen_timestamps", {})
    wrong_or_skipped_ids = user_intel.get("wrong_or_skipped_ids", set())
    wrong_or_skipped_texts = user_intel.get("wrong_or_skipped_texts", set())
    cutoff_days = user_intel.get("cutoff_days", 12)
    now = datetime.now()

    tier1_unseen = []
    tier2_wrong_skipped = []
    tier3_mature_repetition = []
    tier4_recent_all = []

    for q in verified_bank:
        qid_str = str(q.get("id"))
        q_text_clean = str(q.get("question", "")).strip().lower()

        is_seen = (qid_str in seen_timestamps) or (qid_str in seen_ids)
        is_wrong_or_skipped = (qid_str in wrong_or_skipped_ids) or (q_text_clean in wrong_or_skipped_texts)

        if not is_seen:
            tier1_unseen.append(q)
        elif is_wrong_or_skipped:
            tier2_wrong_skipped.append(q)
        else:
            seen_dt = seen_timestamps.get(qid_str)
            if seen_dt and (now - seen_dt).days >= cutoff_days:
                tier3_mature_repetition.append(q)
            else:
                tier4_recent_all.append(q)

    random.shuffle(tier1_unseen)
    random.shuffle(tier2_wrong_skipped)
    random.shuffle(tier3_mature_repetition)
    random.shuffle(tier4_recent_all)

    # 5. Assemble Question Pool Sequentially
    selected_pool = []

    # Fill from Tier 1 (Never seen)
    selected_pool.extend(tier1_unseen[:needed_count])

    # Fill from Tier 2 (Wrong / Skipped Reinforcement)
    if len(selected_pool) < needed_count:
        deficit = needed_count - len(selected_pool)
        selected_pool.extend([randomize_question_options(q) for q in tier2_wrong_skipped[:deficit]])

    # Fill from Tier 3 (10–15 Days Spaced Repetition)
    if len(selected_pool) < needed_count:
        deficit = needed_count - len(selected_pool)
        selected_pool.extend([randomize_question_options(q) for q in tier3_mature_repetition[:deficit]])

    # Fill from Tier 4 (Full Pool Recycling with Randomized Options)
    if len(selected_pool) < needed_count:
        deficit = needed_count - len(selected_pool)
        selected_pool.extend([randomize_question_options(q) for q in tier4_recent_all[:deficit]])

    # If still below needed_count, cycle from the entire bank with randomized options
    while len(selected_pool) < needed_count and len(selected_pool) < len(verified_bank):
        rem = [q for q in verified_bank if q not in selected_pool]
        if not rem:
            break
        selected_pool.append(randomize_question_options(random.choice(rem)))

    # Final overall shuffle of questions
    random.shuffle(selected_pool)
    return selected_pool[:needed_count]