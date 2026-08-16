import os
import json

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
BANK_DIR = os.path.join(DATA_DIR, "question_bank")
HINDI_DIR = os.path.join(DATA_DIR, "hindi")

os.makedirs(BANK_DIR, exist_ok=True)

eng_src = os.path.join(DATA_DIR, "pinnacle_computer_pyqs_extracted.json")
hi_src = os.path.join(HINDI_DIR, "pinnacle_computer_pyqs_extracted_hindi.json")

eng_target = os.path.join(BANK_DIR, "computer_awareness_en.json")
hi_target = os.path.join(BANK_DIR, "computer_awareness_hi.json")

def merge_and_save(src_file, target_file, lang="en"):
    if not os.path.exists(src_file):
        print(f"[-] Source file not found: {src_file}")
        return
    
    with open(src_file, "r", encoding="utf-8") as f:
        new_qs = json.load(f)

    existing_qs = []
    if os.path.exists(target_file):
        try:
            with open(target_file, "r", encoding="utf-8") as f:
                existing_qs = json.load(f)
        except Exception:
            existing_qs = []

    seen_texts = {q.get("question", "").strip().lower() for q in existing_qs}
    added_count = 0

    for q in new_qs:
        norm = q.get("question", "").strip().lower()
        if norm not in seen_texts:
            seen_texts.add(norm)
            existing_qs.append(q)
            added_count += 1

    with open(target_file, "w", encoding="utf-8") as f:
        json.dump(existing_qs, f, indent=4, ensure_ascii=False)

    print(f"[✓] {lang.upper()} Bank: +{added_count} questions merged (Total: {len(existing_qs)})")

print("=" * 60)
print("  UPDATING 'QUIZ WITH HIM' MASTER QUESTION BANK")
print("=" * 60)
merge_and_save(eng_src, eng_target, "en")
merge_and_save(hi_src, hi_target, "hi")
print("=" * 60)