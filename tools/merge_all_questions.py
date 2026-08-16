import os
import json
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
HINDI_DIR = os.path.join(DATA_DIR, "hindi")
BANK_DIR = os.path.join(DATA_DIR, "question_bank")

def load_json_safely(file_path):
    """Loads JSON file and returns a list of question dicts."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                # If wrapped in a dictionary key like {"questions": [...]}
                for val in data.values():
                    if isinstance(val, list):
                        return val
    except Exception as e:
        print(f"  [⚠️] Error reading {os.path.basename(file_path)}: {e}")
    return []

def merge_and_build_masters():
    print("=" * 60)
    print("  QUIZ WITH HIM - MERGING ALL 915 QUESTIONS (EN & HI)")
    print("=" * 60)

    # 1. Collect all potential JSON files across data directories
    search_folders = [DATA_DIR, HINDI_DIR, BANK_DIR]
    all_json_files = []
    
    for folder in search_folders:
        if os.path.exists(folder):
            for file in os.listdir(folder):
                if file.endswith(".json") and not file.startswith("master_"):
                    full_path = os.path.join(folder, file)
                    if os.path.isfile(full_path):
                        all_json_files.append(full_path)

    print(f"[*] Discovered {len(all_json_files)} source JSON files to inspect.")

    english_master = []
    hindi_master = []
    seen_eng_hashes = set()
    seen_hi_hashes = set()

    def normalize_text(txt):
        return re.sub(r"\W+", "", str(txt).lower().strip())

    # 2. Iterate and sort questions by language
    for file_path in all_json_files:
        items = load_json_safely(file_path)
        if not items:
            continue
        
        file_name = os.path.basename(file_path)
        count_en, count_hi = 0, 0

        for q in items:
            q_text = q.get("question", "")
            if not q_text:
                continue

            lang = q.get("language")
            # Detect language if tag is missing
            if not lang:
                # Check for Devanagari script (Hindi Unicode range \u0900-\u097F)
                if re.search(r'[\u0900-\u097F]', q_text):
                    lang = "hi"
                else:
                    lang = "en"
                q["language"] = lang

            norm_hash = normalize_text(q_text)

            if lang == "hi":
                if norm_hash not in seen_hi_hashes:
                    seen_hi_hashes.add(norm_hash)
                    q["subject"] = q.get("subject", "Computer Awareness")
                    hindi_master.append(q)
                    count_hi += 1
            else:
                if norm_hash not in seen_eng_hashes:
                    seen_eng_hashes.add(norm_hash)
                    q["subject"] = q.get("subject", "Computer Awareness")
                    english_master.append(q)
                    count_en += 1

        print(f"  [✓ Loaded] {file_name:<40} -> (+{count_en} EN | +{count_hi} HI)")

    # 3. Standardize IDs sequentially
    for idx, q in enumerate(english_master, start=1):
        q["id"] = f"comp_en_{idx:04d}"

    for idx, q in enumerate(hindi_master, start=1):
        q["id"] = f"comp_hi_{idx:04d}"

    # 4. Save clean Master Files
    master_en_path = os.path.join(DATA_DIR, "master_computer_questions_en.json")
    master_hi_path = os.path.join(DATA_DIR, "master_computer_questions_hi.json")

    with open(master_en_path, "w", encoding="utf-8") as f:
        json.dump(english_master, f, indent=4, ensure_ascii=False)

    with open(master_hi_path, "w", encoding="utf-8") as f:
        json.dump(hindi_master, f, indent=4, ensure_ascii=False)

    print("\n" + "=" * 60)
    print(f"  [SUCCESS] All files merged into 2 unified master files!")
    print(f"  📄 English Master : {len(english_master)} Questions -> data/master_computer_questions_en.json")
    print(f"  📄 Hindi Master   : {len(hindi_master)} Questions -> data/master_computer_questions_hi.json")
    print("=" * 60)

if __name__ == "__main__":
    merge_and_build_masters()