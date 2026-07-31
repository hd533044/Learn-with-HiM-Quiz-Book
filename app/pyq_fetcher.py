import json
import os
import random
import logging
from app.config import DATA_DIR, QUESTION_BANK_DIR

logger = logging.getLogger(__name__)

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

    return {
        "id": q.get("id") if q.get("id") is not None else hash(str(q_text)),
        "question": str(q_text).strip(),
        "options": clean_opts,
        "correct_option": correct_opt,
        "explanation": str(expl).strip()
    }

def fetch_pyqs_for_quiz(needed_count: int = 20, seen_ids: set = None) -> list:
    if seen_ids is None:
        seen_ids = set()

    all_raw_questions = []
    search_dirs = [QUESTION_BANK_DIR, DATA_DIR]

    for search_dir in search_dirs:
        if not os.path.exists(search_dir):
            continue
        for root, _, files in os.walk(search_dir):
            for file in files:
                if file.endswith(".json") and not file.startswith("."):
                    file_path = os.path.join(root, file)
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            if isinstance(data, list):
                                all_raw_questions.extend(data)
                    except Exception as e:
                        logger.error(f"Error reading JSON file {file_path}: {e}")

    formatted_pool = []
    for q in all_raw_questions:
        q_id = q.get("id")
        if q_id is not None and str(q_id) in seen_ids:
            continue

        verified_q = verify_and_correct_question(q)
        if verified_q:
            formatted_pool.append(verified_q)

    random.shuffle(formatted_pool)
    return formatted_pool[:needed_count]