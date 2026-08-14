import json
import os
import random
import logging
from datetime import datetime, timedelta
from app.config import DATA_DIR, QUESTION_BANK_DIR
from app.database import get_user_question_intel

logger = logging.getLogger(__name__)

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
        "language": q.get("language", detected_lang)
    }

def fetch_pyqs_for_quiz(needed_count: int = 20, seen_ids: set = None, language: str = "en", user_id: int = None) -> list:
    """
    Intelligent Tiered Spaced-Repetition Quiz Retrieval:
    - Tier 1: Fresh, unseen questions
    - Tier 2: Previously wrong & unattempted/skipped questions
    - Tier 3: Questions attempted > 10–15 days ago
    - Tier 4: Total pool recycling with randomized option shuffling
    Ensures quizzes never break or stop prematurely while user has quota.
    """
    if seen_ids is None:
        seen_ids = set()

    all_raw_questions = []

    search_dirs = [
        QUESTION_BANK_DIR,
        DATA_DIR,
        os.path.join(QUESTION_BANK_DIR, "hindi"),
        os.path.join(DATA_DIR, "question_bank", "hindi")
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

    # 1. Filter by language barrier
    verified_bank = []
    for q in all_raw_questions:
        verified_q = verify_and_correct_question(q)
        if verified_q:
            q_lang = verified_q.get("language", "en")
            q_is_hindi = is_hindi_text(verified_q["question"])

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

    # 2. Gather User Telemetry for Multi-Tier Spaced Repetition
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

    # 3. Assemble Question Pool Sequentially
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