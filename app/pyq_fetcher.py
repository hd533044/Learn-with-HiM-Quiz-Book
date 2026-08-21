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

# Comprehensive English Topics Metadata
ENGLISH_TOPIC_METADATA = {
    # Comprehension Section
    "eng_comp_cloze_test": {"en": "📖 Cloze Test", "section": "comprehension"},
    "eng_comp_para_jumbles": {"en": "🔀 Para Jumbles / Sentence Rearrangement", "section": "comprehension"},
    "eng_comp_rc": {"en": "📑 Reading Comprehension (RC)", "section": "comprehension"},
    
    # Vocab Section
    "eng_vocab_homonyms": {"en": "🔡 Homonyms", "section": "vocab"},
    "eng_vocab_idioms": {"en": "💬 Idioms & Phrases", "section": "vocab"},
    "eng_vocab_ows": {"en": "💡 One Word Substitution (OWS)", "section": "vocab"},
    "eng_vocab_phrasal_verbs": {"en": "🎯 Phrasal Verbs", "section": "vocab"},
    "eng_vocab_spellings": {"en": "✍️ Correct Spellings", "section": "vocab"},
    "eng_vocab_syn_ant": {"en": "🔠 Synonyms & Antonyms", "section": "vocab"},
    
    # Grammar Section
    "eng_gram_adjective": {"en": "📌 Adjectives", "section": "grammar"},
    "eng_gram_adverb": {"en": "📌 Adverbs", "section": "grammar"},
    "eng_gram_articles": {"en": "📌 Articles (A, An, The)", "section": "grammar"},
    "eng_gram_clause_test": {"en": "📌 Clause Test", "section": "grammar"},
    "eng_gram_conditionals": {"en": "📌 Conditionals", "section": "grammar"},
    "eng_gram_conjunction": {"en": "📌 Conjunctions", "section": "grammar"},
    "eng_gram_infinitive": {"en": "📌 Infinitives, Gerunds & Participles", "section": "grammar"},
    "eng_gram_modals": {"en": "📌 Modals", "section": "grammar"},
    "eng_gram_narration": {"en": "📌 Direct & Indirect Narration", "section": "grammar"},
    "eng_gram_noun": {"en": "📌 Nouns", "section": "grammar"},
    "eng_gram_preposition": {"en": "📌 Prepositions", "section": "grammar"},
    "eng_gram_pronoun": {"en": "📌 Pronouns", "section": "grammar"},
    "eng_gram_question_tag": {"en": "📌 Question Tags", "section": "grammar"},
    "eng_gram_tense": {"en": "📌 Tenses", "section": "grammar"},
    "eng_gram_verb": {"en": "📌 Verbs & Subject-Verb Agreement", "section": "grammar"},
    "eng_gram_voice": {"en": "📌 Active & Passive Voice", "section": "grammar"},
}


def get_available_topics(subject: str = "computer", language: str = "en") -> list:
    """Returns list of tuples (topic_key, clean_display_name)."""
    lang_key = "hi" if language == "hi" else "en"
    if subject == "gk":
        metadata = GK_TOPIC_METADATA
    elif subject == "english":
        metadata = ENGLISH_TOPIC_METADATA
    else:
        metadata = COMPUTER_TOPIC_METADATA
    return [(k, v.get(lang_key, v["en"])) for k, v in metadata.items()]


def is_hindi_text(text: str) -> bool:
    if not text:
        return False
    return any('\u0900' <= char <= '\u097F' for char in str(text))


def clean_option_prefix(opt_text: str) -> str:
    return re.sub(r'^[A-Da-d1-4][\.\)\:\-]\s*', '', str(opt_text)).strip()


REFERENTIAL_PATTERNS = [
    r'\bboth\s+[a-d1-4]\s+(?:and|&)\s+[a-d1-4]\b',
    r'\bonly\s+[a-d1-4]\s+(?:and|&)\s+[a-d1-4]\b',
    r'\ball\s+of\s+the\s+above\b',
    r'\bnone\s+of\s+the\s+above\b',
    r'\ball\s+the\s+above\b',
    r'\bnone\s+of\s+these\b',
    r'\bneither\s+[a-d1-4]\s+nor\s+[a-d1-4]\b',
    r'\bउपरोक्त\s+सभी\b',
    r'\bउपर्युक्त\s+सभी\b',
    r'\bइनमें\s+से\s+कोई\s+नहीं\b',
    r'\bउपर्युक्त\s+दोनों\b',
    r'\bदोनों\s+[A-Da-d1-4]\s+और\s+[A-Da-d1-4]\b'
]


def relabel_option_references(option_text: str, old_to_new_letter_map: dict) -> str:
    """Dynamically replaces old letter references (e.g. 'Both A and B') with new shuffled letters."""
    def replace_letter_match(match):
        prefix = match.group(1)
        letter1 = match.group(2).upper()
        connector = match.group(3)
        letter2 = match.group(4).upper()
        suffix = match.group(5) or ""

        new1 = old_to_new_letter_map.get(letter1, letter1)
        new2 = old_to_new_letter_map.get(letter2, letter2)
        first, second = sorted([new1, new2])
        return f"{prefix} {first} {connector} {second}{suffix}"

    pattern = r'\b(Both|both|Only|only|Either|either|Neither|neither)\s+([A-Da-d])\s+(and|&|or|nor)\s+([A-Da-d])(\s+are\s+correct|\s+is\s+correct|\s+correct)?\b'
    return re.sub(pattern, replace_letter_match, option_text)


def randomize_question_options(q: dict) -> dict:
    """
    Shuffles options while preserving referential and positional accuracy:
    - If positional phrases ('All of the above', 'None of the above', 'उपरोक्त सभी') exist, skips shuffling.
    - If letter references (e.g., 'Both A and B are correct') exist, dynamically updates referenced letters.
    """
    opts = list(q.get("options", []))
    correct_idx = q.get("correct_option", 0)
    if not opts or correct_idx >= len(opts) or correct_idx < 0:
        return q

    clean_options = [clean_option_prefix(opt) for opt in opts]

    # 1. Skip shuffling if options contain absolute positional phrases like 'All of the above'
    lower_opts = [o.lower() for o in clean_options]
    if any("of the above" in o or "the above" in o or "of these" in o or "उपरोक्त" in o or "उपर्युक्त" in o or "इनमें से कोई" in o for o in lower_opts):
        new_q = dict(q)
        new_q["options"] = clean_options
        new_q["correct_option"] = correct_idx
        return new_q

    # 2. Track original positions
    letters = ["A", "B", "C", "D"][:len(clean_options)]
    indexed_options = list(enumerate(clean_options))
    correct_original_tuple = indexed_options[correct_idx]

    # 3. Shuffle options
    shuffled_indexed = list(indexed_options)
    random.shuffle(shuffled_indexed)

    # 4. Build old-to-new letter mapping
    old_to_new_map = {}
    for new_idx, (old_idx, _) in enumerate(shuffled_indexed):
        old_letter = letters[old_idx]
        new_letter = letters[new_idx]
        old_to_new_map[old_letter] = new_letter

    # 5. Dynamically relabel any relative 'Both X and Y' option texts
    final_shuffled_opts = []
    for _, opt_text in shuffled_indexed:
        updated_text = relabel_option_references(opt_text, old_to_new_map)
        final_shuffled_opts.append(updated_text)

    new_correct_idx = shuffled_indexed.index(correct_original_tuple)

    new_q = dict(q)
    new_q["options"] = final_shuffled_opts
    new_q["correct_option"] = new_correct_idx
    return new_q


def verify_and_correct_question(q: dict, force_lang: str = None) -> dict:
    q_text = q.get("question") or q.get("question_text")
    raw_opts = q.get("options")
    raw_correct = q.get("correct_option") if q.get("correct_option") is not None else q.get("correct_answer")
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
        elif isinstance(raw_correct, str):
            clean_ans = clean_option_prefix(raw_correct).lower()
            for idx, opt in enumerate(clean_opts):
                if clean_option_prefix(opt).lower() == clean_ans:
                    correct_idx = idx
                    break

    if len(clean_opts) < 2:
        return None

    detected_lang = "hi" if is_hindi_text(str(q_text)) else "en"
    if force_lang:
        detected_lang = force_lang

    q_id_val = q.get("id")
    unique_id = f"{category}_{q_id_val}" if q_id_val is not None else str(hash(str(q_text)))

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


def get_english_base_dir() -> str:
    candidates = [
        os.path.join(QUESTION_BANK_DIR, "english"),
        os.path.join(DATA_DIR, "question_bank", "english"),
        os.path.join(BASE_DIR, "data", "question_bank", "english")
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return candidates[0]


def load_english_questions(topic_key: str = "MIXED") -> list:
    """Loads English questions from corresponding batch JSON files."""
    base_eng = get_english_base_dir()
    if not os.path.exists(base_eng):
        logger.warning(f"English question bank path not found: {base_eng}")
        return []

    target_files = []

    def scan_dir(folder_path, prefix_filter=None):
        out = []
        if os.path.exists(folder_path):
            for root, _, files in os.walk(folder_path):
                for f in files:
                    if f.endswith(".json"):
                        if prefix_filter:
                            if f.lower().startswith(prefix_filter.lower()):
                                out.append(os.path.join(root, f))
                        else:
                            out.append(os.path.join(root, f))
        return out

    # COMPREHENSION
    if topic_key == "eng_comp_cloze_test":
        target_files = scan_dir(os.path.join(base_eng, "comprehension", "CLOZE TEST"))
    elif topic_key == "eng_comp_para_jumbles":
        target_files = scan_dir(os.path.join(base_eng, "comprehension", "PARA JUMBLES"))
    elif topic_key == "eng_comp_rc":
        target_files = scan_dir(os.path.join(base_eng, "comprehension", "READING COMPREHENSION"))

    # VOCAB
    elif topic_key == "eng_vocab_homonyms":
        target_files = scan_dir(os.path.join(base_eng, "vocab", "HOMONYMS"))
    elif topic_key == "eng_vocab_idioms":
        target_files = scan_dir(os.path.join(base_eng, "vocab", "IDIOMS"))
    elif topic_key == "eng_vocab_ows":
        target_files = scan_dir(os.path.join(base_eng, "vocab", "OWS"))
    elif topic_key == "eng_vocab_phrasal_verbs":
        target_files = scan_dir(os.path.join(base_eng, "vocab", "PHRASEL VERBS"))
    elif topic_key == "eng_vocab_spellings":
        target_files = scan_dir(os.path.join(base_eng, "vocab", "SPELLINGS"))
    elif topic_key == "eng_vocab_syn_ant":
        target_files = scan_dir(os.path.join(base_eng, "vocab", "SYN-ANT"))

    # GRAMMAR (Flat directory or prefix based)
    elif topic_key.startswith("eng_gram_"):
        gram_sub = topic_key.replace("eng_gram_", "").upper()
        gram_dir = os.path.join(base_eng, "grammar")
        target_files = scan_dir(gram_dir, prefix_filter=f"GRAMMAR {gram_sub}")
        if not target_files:
            target_files = scan_dir(os.path.join(gram_dir, gram_sub))

    elif topic_key == "MIXED":
        target_files = scan_dir(base_eng)

    all_loaded = []
    for fp in target_files:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
                category_name = os.path.splitext(os.path.basename(fp))[0]
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            item.setdefault("category", category_name)
                            all_loaded.append(item)
                elif isinstance(data, dict):
                    # In case the json wraps questions under a key
                    qs = data.get("questions") or data.get("data")
                    if isinstance(qs, list):
                        for item in qs:
                            if isinstance(item, dict):
                                item.setdefault("category", category_name)
                                all_loaded.append(item)
                    else:
                        data.setdefault("category", category_name)
                        all_loaded.append(data)
        except Exception as e:
            logger.error(f"Error loading English JSON file {fp}: {e}")

    return all_loaded


def fetch_pyqs_for_quiz(needed_count: int = 20, seen_ids: set = None, language: str = "en", user_id: int = None, topic: str = "MIXED", subject: str = "computer") -> list:
    """STRICT SUBJECT, LANGUAGE & TOPIC ISOLATION ENGINE WITH FULL ENGLISH INTEGRATION."""
    all_raw_questions = []
    lang_sub = "hi" if language == "hi" else "en"
    loaded_from_specific_file = False

    # 1. ENGLISH SUBJECT
    if subject == "english":
        all_raw_questions = load_english_questions(topic)
        loaded_from_specific_file = True

    # 2. GENERAL KNOWLEDGE (GK) MODE
    elif subject == "gk":
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

    # 3. SHORTCUT KEYS MODE (COMPUTER)
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

    # 4. TOPIC-WISE MODE (COMPUTER)
    elif topic and topic != "MIXED" and subject == "computer":
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

    # 5. MIXED PRACTICE MODE & MASTER BANK FALLBACK (COMPUTER)
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

    # 6. STRICT VERIFICATION & PARSING
    verified_bank = []
    seen_unique_texts = set()

    for q in all_raw_questions:
        verified_q = verify_and_correct_question(q, force_lang=language)
        if verified_q:
            if subject != "english":
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

    # 7. EXHAUSTION CYCLING & RESHUFFLING
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
    # Strictly randomize options for each question so answers are uniformly distributed
    return [randomize_question_options(q) for q in selected_questions]


def fetch_multi_topic_questions(needed_count: int, topic_keys: list, subject: str = "computer", language: str = "en", user_id: int = None) -> list:
    """Fetches unique, evenly distributed questions across 2 to 5 selected topics."""
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
    return [randomize_question_options(q) for q in combined_questions[:needed_count]]


def fetch_english_full_mock_25(language: str = "en", user_id: int = None) -> list:
    """
    Constructs an authentic 25-Question English Competitive Exam Mock:
    - 5 RC or Cloze Test (only one per mock, all questions from single passage batch)
    - 2-3 Para Jumbles
    - 2-3 Homonyms
    - 2-3 Phrasal Verbs
    - 5-7 Mixed Vocab (2 OWS, 2 Idioms, 2-3 Syn-Ant)
    - Remaining questions (approx. 5-7) from Grammar
    """
    mock_qs = []
    seen_ids = get_user_seen_identifiers(user_id)

    # 1. 5 Questions: RC or Cloze Test (choose one randomly)
    comp_topic = random.choice(["eng_comp_rc", "eng_comp_cloze_test"])
    comp_qs = fetch_pyqs_for_quiz(needed_count=5, seen_ids=seen_ids, language=language, user_id=user_id, topic=comp_topic, subject="english")
    for q in comp_qs:
        seen_ids.add(str(q.get("id")))
    mock_qs.extend(comp_qs[:5])

    # 2. 2-3 Questions: Para Jumbles
    pj_count = random.choice([2, 3])
    pj_qs = fetch_pyqs_for_quiz(needed_count=pj_count, seen_ids=seen_ids, language=language, user_id=user_id, topic="eng_comp_para_jumbles", subject="english")
    for q in pj_qs:
        seen_ids.add(str(q.get("id")))
    mock_qs.extend(pj_qs[:pj_count])

    # 3. 2-3 Questions: Homonyms
    homo_count = random.choice([2, 3])
    homo_qs = fetch_pyqs_for_quiz(needed_count=homo_count, seen_ids=seen_ids, language=language, user_id=user_id, topic="eng_vocab_homonyms", subject="english")
    for q in homo_qs:
        seen_ids.add(str(q.get("id")))
    mock_qs.extend(homo_qs[:homo_count])

    # 4. 2-3 Questions: Phrasal Verbs
    pv_count = random.choice([2, 3])
    pv_qs = fetch_pyqs_for_quiz(needed_count=pv_count, seen_ids=seen_ids, language=language, user_id=user_id, topic="eng_vocab_phrasal_verbs", subject="english")
    for q in pv_qs:
        seen_ids.add(str(q.get("id")))
    mock_qs.extend(pv_qs[:pv_count])

    # 5. Mixed Vocab: 2 OWS, 2 Idioms, 2-3 Syn-Ant
    ows_qs = fetch_pyqs_for_quiz(needed_count=2, seen_ids=seen_ids, language=language, user_id=user_id, topic="eng_vocab_ows", subject="english")
    for q in ows_qs: seen_ids.add(str(q.get("id")))
    mock_qs.extend(ows_qs[:2])

    idiom_qs = fetch_pyqs_for_quiz(needed_count=2, seen_ids=seen_ids, language=language, user_id=user_id, topic="eng_vocab_idioms", subject="english")
    for q in idiom_qs: seen_ids.add(str(q.get("id")))
    mock_qs.extend(idiom_qs[:2])

    syn_count = random.choice([2, 3])
    syn_qs = fetch_pyqs_for_quiz(needed_count=syn_count, seen_ids=seen_ids, language=language, user_id=user_id, topic="eng_vocab_syn_ant", subject="english")
    for q in syn_qs: seen_ids.add(str(q.get("id")))
    mock_qs.extend(syn_qs[:syn_count])

    # 6. Rest of Questions from Grammar to complete exactly 25
    remaining_needed = max(0, 25 - len(mock_qs))
    if remaining_needed > 0:
        grammar_keys = [k for k in ENGLISH_TOPIC_METADATA.keys() if k.startswith("eng_gram_")]
        random.shuffle(grammar_keys)
        gram_qs = fetch_multi_topic_questions(needed_count=remaining_needed, topic_keys=grammar_keys[:5], subject="english", language=language, user_id=user_id)
        mock_qs.extend(gram_qs[:remaining_needed])

    # Fallback to general english if bank has fewer questions
    if len(mock_qs) < 25:
        deficit = 25 - len(mock_qs)
        extra = fetch_pyqs_for_quiz(needed_count=deficit, seen_ids=seen_ids, language=language, user_id=user_id, topic="MIXED", subject="english")
        mock_qs.extend(extra[:deficit])

    random.shuffle(mock_qs)
    return [randomize_question_options(q) for q in mock_qs[:25]]


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
    return [randomize_question_options(q) for q in mock_pool]