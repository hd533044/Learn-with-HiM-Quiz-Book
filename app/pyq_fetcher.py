import json
import os
import random
import re
import hashlib
import logging
from datetime import datetime
from app.config import DATA_DIR, QUESTION_BANK_DIR, TOPICS_DIR, SHORTCUT_KEYS_DIR, BASE_DIR
from app.database import get_db, release_db, reset_user_seen_questions_for_ids

logger = logging.getLogger(__name__)

COMPUTER_TOPIC_METADATA = {
    "Computer_Hardware_Architecture": {"en": "🖥️ Computer Basics & Architecture", "hi": "🖥️ कंप्यूटर की मूल बातें और आर्किटेक्चर"},
    "Memory_Storage": {"en": "💾 Memory & Storage Devices", "hi": "💾 मेमोरी और स्टोरेज डिवाइस"},
    "Operating_Systems_CLI": {"en": "⚙️ Operating Systems & CLI", "hi": "⚙️ ऑपरेटिंग सिस्टम और CLI"},
    "MS_Word": {"en": "📝 Microsoft Word", "hi": "📝 माइक्रोसॉफ्ट वर्ड"},
    "MS_Excel": {"en": "📊 Microsoft Excel", "hi": "📊 माइक्रोसॉफ्ट एक्सेल"},
    "MS_PowerPoint_365": {"en": "📽️ PowerPoint & OneNote", "hi": "📽️ पावरपॉइंट और वननोट"},
    "Networking_Internet": {"en": "🌐 Networking & Internet", "hi": "🌐 नेटवर्किंग और इंटरनेट"},
    "Cybersecurity_Malware": {"en": "🛡️ Cybersecurity & Malware", "hi": "🛡️ साइबर सुरक्षा और मैलवेयर"},
    "Number_Systems": {"en": "🔢 Number Systems & Codes", "hi": "🔢 संख्या प्रणाली और कंप्यूटर कोड्स"},
    "General_Computer_Awareness": {"en": "💡 General Computer Awareness", "hi": "💡 सामान्य कंप्यूटर जागरूकता"},
    "SHORTCUTS": {"en": "⌨️ Computer Shortcut Keys", "hi": "⌨️ कंप्यूटर शॉर्टकट कुंजियाँ"}
}

GK_TOPIC_METADATA = {
    "Indian History - Ancient & Medieval": {"en": "🏛️ Indian History - Ancient & Medieval", "hi": "🏛️ भारतीय इतिहास - प्राचीन एवं मध्यकालीन"},
    "Indian History - Modern & Freedom Struggle": {"en": "⚔️ Indian History - Modern & Freedom Struggle", "hi": "⚔️ भारतीय इतिहास - आधुनिक एवं स्वतंत्रता संग्राम"},
    "Indian Polity & Constitution": {"en": "⚖️ Indian Polity & Constitution", "hi": "⚖️ भारतीय राजव्यवस्था एवं संविधान"},
    "Indian & World Geography": {"en": "🌍 Indian & World Geography", "hi": "🌍 भारतीय एवं विश्व भूगोल"},
    "Indian Economy & General Awareness": {"en": "📈 Indian Economy & General Awareness", "hi": "📈 भारतीय अर्थव्यवस्था एवं सामान्य जागरूकता"},
    "General Science - Physics & Chemistry": {"en": "🧪 General Science - Physics & Chemistry", "hi": "🧪 सामान्य विज्ञान - भौतिकी एवं रसायन"},
    "General Science - Biology & Environment": {"en": "🌿 General Science - Biology & Environment", "hi": "🌿 सामान्य विज्ञान - जीव विज्ञान एवं पर्यावरण"},
    "Static GK - Art, Culture & Heritage": {"en": "🎨 Static GK - Art, Culture & Heritage", "hi": "🎨 स्टैटिक जीके - कला, संस्कृति एवं धरोवर"},
    "Static GK - Festivals, Fairs & Temples": {"en": "🛕 Static GK - Festivals, Fairs & Temples", "hi": "🛕 स्टैटिक जीके - त्यौहार, मेले एवं मंदिर"},
    "Static GK - Sports & Awards": {"en": "🏆 Static GK - Sports & Awards", "hi": "🏆 स्टैटिक जीके - खेल एवं पुरस्कार"}
}

ENGLISH_TOPIC_METADATA = {
    "eng_comp_cloze_test": {"en": "📖 Cloze Test", "section": "comprehension"},
    "eng_comp_para_jumbles": {"en": "🔀 Para Jumbles / Sentence Rearrangement", "section": "comprehension"},
    "eng_comp_rc": {"en": "📑 Reading Comprehension (RC)", "section": "comprehension"},
    "eng_vocab_homonyms": {"en": "🔡 Homonyms", "section": "vocab"},
    "eng_vocab_idioms": {"en": "💬 Idioms & Phrases", "section": "vocab"},
    "eng_vocab_ows": {"en": "💡 One Word Substitution (OWS)", "section": "vocab"},
    "eng_vocab_phrasal_verbs": {"en": "🎯 Phrasal Verbs", "section": "vocab"},
    "eng_vocab_spellings": {"en": "✍️ Correct Spellings", "section": "vocab"},
    "eng_vocab_syn_ant": {"en": "🔠 Synonyms & Antonyms", "section": "vocab"},
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


def generate_deterministic_qid(q_text: str, category: str = "General", raw_id=None) -> str:
    """Generates an immutable deterministic ID that never changes across server restarts."""
    if raw_id is not None and str(raw_id).strip() != "":
        return f"{category}_{raw_id}"
    norm = re.sub(r'\W+', '', str(q_text).lower().strip())
    hash_str = hashlib.sha256(norm.encode('utf-8')).hexdigest()[:16]
    return f"{category}_{hash_str}"


def verify_and_correct_question(q: dict, force_lang: str = None) -> dict:
    if not isinstance(q, dict):
        return None

    passage = q.get("passage") or q.get("passage_text") or q.get("context") or q.get("para") or ""
    q_text = q.get("question") or q.get("question_text") or q.get("q_text") or q.get("q") or q.get("title") or ""
    
    if not q_text and ("sentences" in q or "jumble" in q or "statements" in q):
        sentences = q.get("sentences") or q.get("jumble") or q.get("statements")
        if isinstance(sentences, list):
            labels = ["P", "Q", "R", "S", "T", "U"]
            rendered_jumble = "\n".join([f"{labels[i]}. {s}" for i, s in enumerate(sentences) if i < len(labels)])
            q_text = f"Rearrange the following parts to form a meaningful sentence/paragraph:\n\n{rendered_jumble}"

    raw_opts = q.get("options") or q.get("choices") or q.get("answers") or q.get("opts")
    raw_correct = (
        q.get("correct_option") if q.get("correct_option") is not None 
        else q.get("correct_answer") if q.get("correct_answer") is not None
        else q.get("answer") if q.get("answer") is not None
        else q.get("ans")
    )
    expl = q.get("explanation") or q.get("exp") or ""
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

    unique_id = generate_deterministic_qid(q_text, category, q.get("id"))

    return {
        "id": unique_id,
        "question": str(q_text).strip(),
        "passage": str(passage).strip() if passage else "",
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
    base_eng = get_english_base_dir()
    if not os.path.exists(base_eng):
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

    if topic_key == "eng_comp_cloze_test":
        target_files = scan_dir(os.path.join(base_eng, "comprehension", "CLOZE TEST"))
    elif topic_key == "eng_comp_para_jumbles":
        target_files = scan_dir(os.path.join(base_eng, "comprehension", "PARA JUMBLES"))
    elif topic_key == "eng_comp_rc":
        target_files = scan_dir(os.path.join(base_eng, "comprehension", "READING COMPREHENSION"))
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
                            sub_qs = item.get("questions") or item.get("data") or item.get("items")
                            passage_text = item.get("passage") or item.get("passage_text") or item.get("context") or ""
                            if isinstance(sub_qs, list):
                                for sub in sub_qs:
                                    if isinstance(sub, dict):
                                        if passage_text and not sub.get("passage"):
                                            sub["passage"] = passage_text
                                        sub.setdefault("category", category_name)
                                        all_loaded.append(sub)
                            else:
                                item.setdefault("category", category_name)
                                all_loaded.append(item)
                elif isinstance(data, dict):
                    passage_text = data.get("passage") or data.get("passage_text") or data.get("context") or ""
                    sub_qs = data.get("questions") or data.get("data") or data.get("items")
                    if isinstance(sub_qs, list):
                        for item in sub_qs:
                            if isinstance(item, dict):
                                if passage_text and not item.get("passage"):
                                    item["passage"] = passage_text
                                item.setdefault("category", category_name)
                                all_loaded.append(item)
                    else:
                        data.setdefault("category", category_name)
                        all_loaded.append(data)
        except Exception:
            pass
    return all_loaded


def fetch_rc_or_cloze_passage_questions(topic_key: str, user_id: int = None) -> list:
    """Bulletproof loader for RC & Cloze Test passage sets, prioritizing unseen sets."""
    raw_items = load_english_questions(topic_key)
    if not raw_items:
        return []

    verified = []
    for item in raw_items:
        v = verify_and_correct_question(item)
        if v:
            verified.append(v)

    if not verified:
        return []

    user_seen_ids = get_user_seen_identifiers(user_id)

    passages_map = {}
    for q in verified:
        p_text = q.get("passage") or q.get("question")[:40]
        passages_map.setdefault(p_text, []).append(q)

    groups = [g for g in passages_map.values() if len(g) >= 3]
    if not groups:
        groups = list(passages_map.values())

    unseen_groups = []
    for grp in groups:
        grp_ids = {str(q["id"]) for q in grp}
        if not grp_ids.intersection(user_seen_ids):
            unseen_groups.append(grp)

    if unseen_groups:
        chosen_group = random.choice(unseen_groups)
    else:
        chosen_group = random.choice(groups)

    return chosen_group[:5]


def fetch_pyqs_for_quiz(needed_count: int = 20, seen_ids: set = None, language: str = "en", user_id: int = None, topic: str = "MIXED", subject: str = "computer") -> list:
    if subject == "english" and topic in ["eng_comp_rc", "eng_comp_cloze_test"]:
        passage_qs = fetch_rc_or_cloze_passage_questions(topic, user_id=user_id)
        if passage_qs:
            return passage_qs

    all_raw_questions = []
    lang_sub = "hi" if language == "hi" else "en"
    loaded_from_specific_file = False

    if subject == "english":
        all_raw_questions = load_english_questions(topic)
        loaded_from_specific_file = True
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
                except Exception:
                    pass
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
                except Exception:
                    pass
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
                except Exception:
                    pass

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
                except Exception:
                    pass

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
        return []

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
    elif len(unseen_pool) > 0:
        selected_questions.extend(unseen_pool)
        deficit = needed_count - len(selected_questions)
        random.shuffle(seen_pool)
        selected_questions.extend(seen_pool[:deficit])
    else:
        if user_id and all_current_qids:
            reset_user_seen_questions_for_ids(user_id, all_current_qids)
        random.shuffle(verified_bank)
        selected_questions = verified_bank[:needed_count]

    random.shuffle(selected_questions)
    return selected_questions


def fetch_multi_topic_questions(needed_count: int, topic_keys: list, subject: str = "computer", language: str = "en", user_id: int = None) -> list:
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


def fetch_english_full_mock_25(language: str = "en", user_id: int = None) -> list:
    mock_qs = []
    seen_ids = get_user_seen_identifiers(user_id)

    comp_qs = fetch_rc_or_cloze_passage_questions("eng_comp_rc", user_id=user_id)
    if not comp_qs:
        comp_qs = fetch_rc_or_cloze_passage_questions("eng_comp_cloze_test", user_id=user_id)
    for q in comp_qs: seen_ids.add(str(q.get("id")))
    mock_qs.extend(comp_qs[:5])

    pj_count = random.choice([2, 3])
    pj_qs = fetch_pyqs_for_quiz(needed_count=pj_count, seen_ids=seen_ids, language=language, user_id=user_id, topic="eng_comp_para_jumbles", subject="english")
    for q in pj_qs: seen_ids.add(str(q.get("id")))
    mock_qs.extend(pj_qs[:pj_count])

    homo_count = random.choice([2, 3])
    homo_qs = fetch_pyqs_for_quiz(needed_count=homo_count, seen_ids=seen_ids, language=language, user_id=user_id, topic="eng_vocab_homonyms", subject="english")
    for q in homo_qs: seen_ids.add(str(q.get("id")))
    mock_qs.extend(homo_qs[:homo_count])

    pv_count = random.choice([2, 3])
    pv_qs = fetch_pyqs_for_quiz(needed_count=pv_count, seen_ids=seen_ids, language=language, user_id=user_id, topic="eng_vocab_phrasal_verbs", subject="english")
    for q in pv_qs: seen_ids.add(str(q.get("id")))
    mock_qs.extend(pv_qs[:pv_count])

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

    remaining_needed = max(0, 25 - len(mock_qs))
    if remaining_needed > 0:
        grammar_keys = [k for k in ENGLISH_TOPIC_METADATA.keys() if k.startswith("eng_gram_")]
        random.shuffle(grammar_keys)
        gram_qs = fetch_multi_topic_questions(needed_count=remaining_needed, topic_keys=grammar_keys[:5], subject="english", language=language, user_id=user_id)
        mock_qs.extend(gram_qs[:remaining_needed])

    random.shuffle(mock_qs)
    return mock_qs[:25]


def fetch_full_mock_questions(needed_count: int = 20, language: str = "en", user_id: int = None) -> list:
    comp_count = needed_count // 2
    gk_count = needed_count - comp_count

    seen_ids = get_user_seen_identifiers(user_id)
    comp_qs = fetch_pyqs_for_quiz(needed_count=comp_count, seen_ids=seen_ids, language=language, user_id=user_id, topic="MIXED", subject="computer")
    for q in comp_qs: seen_ids.add(str(q.get("id")))
    gk_qs = fetch_pyqs_for_quiz(needed_count=gk_count, seen_ids=seen_ids, language=language, user_id=user_id, topic="MIXED", subject="gk")

    mock_pool = comp_qs + gk_qs
    random.shuffle(mock_pool)
    return mock_pool