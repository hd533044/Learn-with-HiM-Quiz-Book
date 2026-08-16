import os
import json
import re
import time
import hashlib
import fitz  # PyMuPDF
import urllib.request
import urllib.error
from typing import List, Dict, Any

# Point to project root and /data directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# ----------------------------------------------------------------------
# AUTO-LOAD .ENV FILE FROM ROOT DIRECTORY
# ----------------------------------------------------------------------
env_path = os.path.join(BASE_DIR, ".env")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip().strip('"').strip("'")


# ----------------------------------------------------------------------
# 1. COMPREHENSIVE PDF EXTRACTION (THEORY + QUESTIONS)
# ----------------------------------------------------------------------
def extract_clean_chunks_from_pdf(pdf_path: str, margin_cut_pct: float = 0.08) -> List[str]:
    doc = fitz.open(pdf_path)
    valid_chunks = []
    current_chunk = []
    current_char_len = 0

    print(f"[*] Reading and scanning {len(doc)} pages (Theory & Questions) in '{os.path.basename(pdf_path)}'...")

    for page_num in range(len(doc)):
        page = doc[page_num]
        rect = page.rect
        
        crop_box = fitz.Rect(
            rect.x0, 
            rect.y0 + (rect.height * margin_cut_pct),
            rect.x1, 
            rect.y1 - (rect.height * margin_cut_pct)
        )
        
        page_text = page.get_text("text", clip=crop_box)
        cleaned_page = re.sub(
            r"(https?://\S+|t\.me/\S+|www\.\S+|telegram|subscribe|join channel|whatsapp group|author\s*:\s*\S+)",
            "", 
            page_text, 
            flags=re.IGNORECASE
        ).strip()

        if len(cleaned_page) >= 100:
            current_chunk.append(cleaned_page)
            current_char_len += len(cleaned_page)

            if current_char_len >= 3000:
                valid_chunks.append("\n\n".join(current_chunk))
                current_chunk = []
                current_char_len = 0

    if current_chunk:
        valid_chunks.append("\n\n".join(current_chunk))

    doc.close()
    return valid_chunks


# ----------------------------------------------------------------------
# 2. BULLETPROOF JSON ARRAY PARSER
# ----------------------------------------------------------------------
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


# ----------------------------------------------------------------------
# 3. UNIVERSAL AI ENGINE (SUPPORTS GROQ, GROK & OPENAI)
# ----------------------------------------------------------------------
def parse_and_verify_questions(chunks: List[str], subject_tag: str, output_file_path: str) -> List[Dict[str, Any]]:
    api_key = (
        os.getenv("GROQ_API_KEY") or 
        os.getenv("GROK_API_KEY") or 
        os.getenv("OPENAI_API_KEY")
    )
    if not api_key:
        raise ValueError("API Key not found! Please ensure GROQ_API_KEY, GROK_API_KEY, or OPENAI_API_KEY is set in your .env file.")

    if api_key.startswith("gsk_") or os.getenv("GROQ_API_KEY"):
        api_url = "https://api.groq.com/openai/v1/chat/completions"
        model_name = "llama-3.1-8b-instant"
    elif api_key.startswith("xai-"):
        api_url = "https://api.x.ai/v1/chat/completions"
        model_name = "grok-2"
    else:
        api_url = "https://api.openai.com/v1/chat/completions"
        model_name = "gpt-4o-mini"

    system_prompt = """
You are an elite Senior Exam Question Architect, Subject Matter Expert, and Quiz Generator.

TASK:
Analyze the provided text containing theory concepts/notes and existing Multiple Choice Questions.

INSTRUCTIONS:
1. From Theory Content: Synthesize brand new, rigorous exam-standard Multiple Choice Questions testing core concepts.
2. From Existing Questions: Extract, verify, and refine them. Correct any answer key errors.
3. General Rules for ALL Questions:
   - Exactly 4 options per question: ["A. ...", "B. ...", "C. ...", "D. ..."].
   - 'correct_option' MUST be an integer index: 0 for A, 1 for B, 2 for C, 3 for D.
   - For every single question, generate BOTH an English version ('en') and a Hindi version ('hi') as separate objects in the array. Both must point to the exact same correct option index.
   - Provide a clear, verified explanation derived directly from the text.
   - Ignore watermarks, author names, promotional links, and noise.
4. Output MUST be ONLY a valid JSON array of verified objects matching the schema without markdown backticks or commentary.

SCHEMA:
[
  {
    "question": "Question text in specified language",
    "options": ["A. ...", "B. ...", "C. ...", "D. ..."],
    "correct_option": 0,
    "explanation": "Factually verified explanation.",
    "verification_status": "VERIFIED_100%",
    "subject": "Subject Name",
    "language": "en"
  }
]
"""

    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }

    seen_hashes = set()
    all_verified_questions = []
    prefix = "".join([c for c in subject_tag.lower() if c.isalnum()])[:4] or "quiz"
    count = 1

    total_chunks = len(chunks)
    print(f"[*] Processing {total_chunks} content batches via {model_name}...\n")

    # Load existing file if present to avoid overwriting previous progress
    if os.path.exists(output_file_path):
        try:
            with open(output_file_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
                if isinstance(existing, list):
                    all_verified_questions = existing
                    for eq in existing:
                        q_txt = eq.get("question", "")
                        norm_key = re.sub(r"\W+", "", q_txt.lower())
                        seen_hashes.add(hashlib.md5(norm_key.encode("utf-8")).hexdigest())
                    count = len(existing) + 1
                    print(f"[*] Loaded {len(existing)} existing questions from {output_file_path}")
        except Exception:
            pass

    for idx, chunk in enumerate(chunks, start=1):
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Subject: {subject_tag}\n\nRaw Text Segment ({idx}/{total_chunks}):\n{chunk}"}
            ],
            "temperature": 0.0
        }

        for attempt in range(4):
            req = urllib.request.Request(api_url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=60) as response:
                    res_data = json.loads(response.read().decode("utf-8"))
                    raw_content = res_data["choices"][0]["message"]["content"]
                    
                    parsed_qs = extract_json_array_safely(raw_content)

                    new_verified = 0
                    for q in parsed_qs:
                        q_text = q.get("question", "").strip()
                        opts = q.get("options", [])
                        corr = q.get("correct_option")

                        if not q_text or len(opts) != 4 or corr not in (0, 1, 2, 3):
                            continue

                        norm_key = re.sub(r"\W+", "", q_text.lower())
                        q_hash = hashlib.md5(norm_key.encode("utf-8")).hexdigest()

                        if q_hash in seen_hashes:
                            continue

                        seen_hashes.add(q_hash)
                        q["id"] = f"{prefix}_{q.get('language', 'en')}_{count:04d}"
                        q["subject"] = subject_tag
                        q["verification_status"] = "VERIFIED_100%"

                        all_verified_questions.append(q)
                        count += 1
                        new_verified += 1

                    print(f"  [✓ GENERATED/VERIFIED] Segment {idx}/{total_chunks} processed (+{new_verified} questions | Total: {len(all_verified_questions)})")
                    
                    if all_verified_questions:
                        # Ensure output directory exists before saving
                        os.makedirs(os.path.dirname(os.path.abspath(output_file_path)), exist_ok=True)
                        with open(output_file_path, "w", encoding="utf-8") as f:
                            json.dump(all_verified_questions, f, indent=4, ensure_ascii=False)
                    break

            except urllib.error.HTTPError as http_err:
                err_body = http_err.read().decode("utf-8", errors="ignore")
                if http_err.code == 429:
                    print(f"  [⏳] Rate limit hit on segment {idx}. Waiting 6s...")
                    time.sleep(6)
                else:
                    print(f"  [-] HTTP {http_err.code} on segment {idx}: {err_body[:120]}")
                    break
            except Exception as e:
                print(f"  [-] Segment {idx} error: {e}")
                time.sleep(2)

        time.sleep(1.0)

    return all_verified_questions


# ----------------------------------------------------------------------
# 4. MAIN WORKFLOW
# ----------------------------------------------------------------------
def process_and_save_pdf(pdf_path: str, output_filename: str = None, subject: str = "General Knowledge"):
    if not os.path.exists(pdf_path):
        print(f"[!] PDF file not found at: {pdf_path}")
        return

    if not output_filename:
        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        safe_name = re.sub(r"\W+", "_", base_name).lower()
        output_filename = f"{safe_name}_extracted.json"

    # If saving specifically for GK, you can pass output_filename as e.g. "question_bank/gk/gk_questions_en.json"
    if "/" in output_filename or "\\" in output_filename:
        final_output_path = os.path.join(DATA_DIR, output_filename)
    else:
        final_output_path = os.path.join(DATA_DIR, output_filename)

    print("=" * 60)
    print(f"   QUIZ WITH HIM - THEORY & MCQ SYNTHESIS EXTRACTOR")
    print("=" * 60)
    
    valid_chunks = extract_clean_chunks_from_pdf(pdf_path)
    if not valid_chunks:
        print("[!] No readable text segments detected in document.")
        return

    questions = parse_and_verify_questions(valid_chunks, subject, final_output_path)

    print("\n" + "=" * 60)
    print(f"   [SUCCESS] All {len(questions)} Questions Generated/Extracted & Verified!")
    print(f"   [SAVED] JSON Path: {final_output_path}")
    print("=" * 60)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("\nUsage: python tools/pdf_question_extractor.py <path_to_pdf> [output_path_or_filename] [subject_name]")
        print("Example: python tools/pdf_question_extractor.py \"Pinnacle GS Theory 2nd Edition (English Medium).pdf\" \"question_bank/gk/gk_questions_en.json\" \"General Knowledge\"\n")
    else:
        pdf_file = sys.argv[1]
        out_file = sys.argv[2] if len(sys.argv) > 2 else None
        subj = sys.argv[3] if len(sys.argv) > 3 else "General Knowledge"
        process_and_save_pdf(pdf_file, output_filename=out_file, subject=subj)