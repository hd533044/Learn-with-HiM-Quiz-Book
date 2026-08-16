import os
import json
import re
import time
import urllib.request
import urllib.error
from typing import List, Dict, Any

# Directories
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
HINDI_DIR = os.path.join(DATA_DIR, "hindi")
os.makedirs(HINDI_DIR, exist_ok=True)

# Auto-load .env
env_path = os.path.join(BASE_DIR, ".env")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip().strip('"').strip("'")


def extract_json_array_safely(text: str) -> List[Dict[str, Any]]:
    if not text or not text.strip():
        return []
    clean_str = text.replace("```json", "").replace("```", "").strip()
    try:
        data = json.loads(clean_str)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    match = re.search(r"\[\s*\{.*\}\s*\]", clean_str, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def translate_batch_with_retry(mini_batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    api_key = os.getenv("GROQ_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("API Key not found! Please check GROQ_API_KEY in your .env file.")

    api_url = "https://api.groq.com/openai/v1/chat/completions" if api_key.startswith("gsk_") else "https://api.openai.com/v1/chat/completions"
    model_name = "llama-3.1-8b-instant" if api_key.startswith("gsk_") else "gpt-4o-mini"

    system_prompt = """You are an expert bilingual exam translator.
Translate the questions, options, and explanations into accurate academic Hindi.
Keep option prefixes (A., B., C., D.) as A., B., C., D.
Keep the 'idx' key matching the input.
Return ONLY a valid JSON array matching this schema:
[{"idx": 0, "question": "हिन्दी प्रश्न", "options": ["A. ...", "B. ...", "C. ...", "D. ..."], "explanation": "हिन्दी व्याख्या"}]"""

    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0"
    }

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(mini_batch, ensure_ascii=False)}
        ],
        "temperature": 0.0
    }

    while True:
        req = urllib.request.Request(api_url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                raw_content = res_data["choices"][0]["message"]["content"]
                translated = extract_json_array_safely(raw_content)
                if translated and len(translated) == len(mini_batch):
                    return translated
                elif translated:
                    return translated
                else:
                    print("  [⚠️] Malformed JSON response. Retrying batch in 2s...")
                    time.sleep(2)
        except urllib.error.HTTPError as http_err:
            err_body = http_err.read().decode("utf-8", errors="ignore")
            if http_err.code == 429:
                wait_match = re.search(r"try again in ([0-9\.]+)s", err_body)
                sleep_sec = float(wait_match.group(1)) + 1.5 if wait_match else 12.0
                print(f"  [⏳ Rate-Limit] Groq cooling down for {sleep_sec:.1f}s...")
                time.sleep(sleep_sec)
            else:
                print(f"  [-] HTTP {http_err.code}: {err_body[:100]}. Retrying in 4s...")
                time.sleep(4)
        except Exception as e:
            print(f"  [-] Network error: {e}. Retrying in 3s...")
            time.sleep(3)


def convert_json_to_hindi(input_filename: str):
    input_path = os.path.join(DATA_DIR, input_filename)
    if not os.path.exists(input_path):
        print(f"[!] Input file not found: {input_path}")
        return

    with open(input_path, "r", encoding="utf-8") as f:
        english_questions = json.load(f)

    base_name = os.path.splitext(input_filename)[0]
    output_filename = f"{base_name}_hindi.json"
    output_path = os.path.join(HINDI_DIR, output_filename)

    # Smart Resume: Load existing translations if any
    translated_hindi_questions = []
    translated_ids = set()

    if os.path.exists(output_path):
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                translated_hindi_questions = json.load(f)
                for q in translated_hindi_questions:
                    # Map back to corresponding English ID
                    eng_id = q["id"].replace("_hi_", "_en_").replace("hi_", "en_")
                    translated_ids.add(eng_id)
            print(f"[*] Resuming! Found {len(translated_hindi_questions)} already translated questions.")
        except Exception:
            translated_hindi_questions = []
            translated_ids = set()

    # Filter out questions that are already translated
    pending_questions = [q for q in english_questions if q.get("id") not in translated_ids]

    print("=" * 60)
    print(f"  QUIZ WITH HIM - RESILIENT HINDI TRANSLATION PIPELINE")
    print("=" * 60)
    print(f"[*] Total Questions: {len(english_questions)}")
    print(f"[*] Already Done: {len(translated_hindi_questions)}")
    print(f"[*] Remaining to Translate: {len(pending_questions)}\n")

    if not pending_questions:
        print("[SUCCESS] All questions are already translated into Hindi!")
        return

    # Process 2 questions per batch to avoid rate limits
    batch_size = 2
    total_batches = (len(pending_questions) + batch_size - 1) // batch_size

    for i in range(0, len(pending_questions), batch_size):
        raw_batch = pending_questions[i:i + batch_size]
        batch_num = (i // batch_size) + 1

        # Build lightweight payload to save tokens
        mini_batch = []
        for idx, q in enumerate(raw_batch):
            mini_batch.append({
                "idx": idx,
                "question": q.get("question", ""),
                "options": q.get("options", []),
                "explanation": q.get("explanation", "")
            })

        print(f"[*] Translating batch {batch_num}/{total_batches} ({len(raw_batch)} questions)...")
        translated_results = translate_batch_with_retry(mini_batch)

        # Merge translations back with full original metadata
        for idx, orig_q in enumerate(raw_batch):
            matching_trans = next((t for t in translated_results if t.get("idx") == idx), None)
            
            # Fallback if idx matching fails
            if not matching_trans and idx < len(translated_results):
                matching_trans = translated_results[idx]

            if matching_trans:
                hindi_q = {
                    "id": orig_q.get("id", f"comp_hi_{len(translated_hindi_questions)+1:04d}").replace("_en_", "_hi_").replace("en_", "hi_"),
                    "question": matching_trans.get("question", orig_q.get("question")),
                    "options": matching_trans.get("options", orig_q.get("options")),
                    "correct_option": orig_q.get("correct_option", 0),
                    "explanation": matching_trans.get("explanation", orig_q.get("explanation", "")),
                    "verification_status": orig_q.get("verification_status", "VERIFIED_100%"),
                    "subject": orig_q.get("subject", "Computer Awareness"),
                    "language": "hi"
                }
                translated_hindi_questions.append(hindi_q)

        # Incremental save after every single batch
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(translated_hindi_questions, f, indent=4, ensure_ascii=False)

        print(f"  [✓ SAVED] Progress: {len(translated_hindi_questions)}/{len(english_questions)} total translated")
        time.sleep(1.0)

    print("\n" + "=" * 60)
    print(f"  [SUCCESS] Completed! All {len(translated_hindi_questions)} questions translated.")
    print(f"  [FILE SAVED] {output_path}")
    print("=" * 60)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("\nUsage: python tools/hindi_translator.py <input_json_filename_in_data>")
        print("Example: python tools/hindi_translator.py pinnacle_computer_pyqs_extracted.json\n")
    else:
        convert_json_to_hindi(sys.argv[1])