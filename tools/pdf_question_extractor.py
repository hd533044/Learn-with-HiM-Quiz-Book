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
# 1. COMPREHENSIVE PDF EXTRACTION (NOISE & PROMO STRIPPING)
# ----------------------------------------------------------------------
def extract_clean_chunks_from_pdf(pdf_path: str, margin_cut_pct: float = 0.08) -> List[str]:
    print(f"[*] Opening PDF document at: {pdf_path}")
    doc = fitz.open(pdf_path)
    valid_chunks = []
    current_chunk = []
    current_char_len = 0

    print(f"[*] Reading and scanning {len(doc)} pages in '{os.path.basename(pdf_path)}'...")

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
        
        # Strip out all promotional text, author names, brands, watermarks
        cleaned_page = re.sub(
            r"(https?://\S+|t\.me/\S+|www\.\S+|telegram|subscribe|join channel|whatsapp group|nikhil gupta|sp bakshi|arihant|gupta edutech|blackbook|all rights reserved)",
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
    print(f"[*] Extracted {len(valid_chunks)} valid text chunks from PDF.")
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
# 3. AI GENERATION ENGINE WITH ACTIVE MODEL FALLBACKS
# ----------------------------------------------------------------------
def parse_and_verify_questions(chunks: List[str], subject_tag: str, output_file_path: str) -> List[Dict[str, Any]]:
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    
    if groq_key and not groq_key.startswith("sk-"):
        api_key = groq_key
        api_url = "https://api.groq.com/openai/v1/chat/completions"
        candidate_models = [
            "meta-llama/llama-4-scout-17b-16e-instruct",
            "openai/gpt-oss-20b",
            "openai/gpt-oss-120b",
            "qwen/qwen3-32b"
        ]
        print("[*] Using Groq AI Engine with active models")
    elif openai_key or (groq_key and groq_key.startswith("sk-")):
        api_key = openai_key if openai_key else groq_key
        api_url = "https://api.openai.com/v1/chat/completions"
        candidate_models = ["gpt-4o-mini"]
        print("[*] Using OpenAI AI Engine (gpt-4o-mini)")
    else:
        raise ValueError("API Key not found! Please ensure a valid GROQ_API_KEY or OPENAI_API_KEY is set in your .env file.")

    system_prompt = """
You are an elite English Quiz Bank Architect and Exam Content Creator.

TASK:
Analyze the provided text fragment from competitive English exam books and generate multiple-choice questions (MCQs).

STRICT RULES:
1. NO promotional tags, brand names, watermarks, or author names anywhere. Ensure 100% factual accuracy.
2. Exactly 4 options per question: ["A. ...", "B. ...", "C. ...", "D. ..."].
3. 'correct_option' MUST be an integer index: 0 for A, 1 for B, 2 for C, 3 for D.
4. DETAILED EXPLANATIONS ARE MANDATORY: The explanation column must explicitly explain why the correct option is right AND briefly state why the other options are wrong or mean something else.
5. TAGGING SYSTEM: Include a robust tag array (e.g., ["vocab", "synonym", "pyq", "ssc_cgl", "hard"]).
6. Output MUST be ONLY a valid JSON array matching the schema below without markdown backticks or commentary.

SCHEMA:
[
  {
    "question": "Question or word text",
    "options": ["A. ...", "B. ...", "C. ...", "D. ..."],
    "correct_option": 0,
    "explanation": "Option X is correct because... B means..., C means..., D means...",
    "tags": ["vocab", "pyq", "ssc_cgl"],
    "difficulty": "medium",
    "subject": "English"
  }
]
"""

    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0"
    }

    seen_hashes = set()
    all_verified_questions = []
    count = 1

    total_chunks = len(chunks)
    print(f"[*] Processing {total_chunks} content batches...\n")

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
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Subject: {subject_tag}\n\nRaw Text Segment ({idx}/{total_chunks}):\n{chunk}"}
            ],
            "temperature": 0.1
        }

        success = False
        for model_name in candidate_models:
            payload["model"] = model_name
            
            for attempt in range(2):
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
                            q["id"] = f"eng_{count:05d}"

                            all_verified_questions.append(q)
                            count += 1
                            new_verified += 1

                        print(f"  [✓ MODEL: {model_name}] Segment {idx}/{total_chunks} processed (+{new_verified} questions | Total: {len(all_verified_questions)})")
                        
                        if all_verified_questions:
                            os.makedirs(os.path.dirname(os.path.abspath(output_file_path)), exist_ok=True)
                            with open(output_file_path, "w", encoding="utf-8") as f:
                                json.dump(all_verified_questions, f, indent=4, ensure_ascii=False)
                        success = True
                        break

                except urllib.error.HTTPError as http_err:
                    err_body = http_err.read().decode("utf-8", errors="ignore")
                    if http_err.code == 404 and "model_not_found" in err_body:
                        break  # Try next model in fallback list
                    elif http_err.code == 429:
                        print("  [⏳] Rate limit hit. Waiting 8 seconds...")
                        time.sleep(8)
                    else:
                        print(f"  [-] HTTP Error {http_err.code} on segment {idx}: {err_body[:100]}")
                        time.sleep(2)
                except Exception as e:
                    print(f"  [-] Segment {idx} error: {e}")
                    time.sleep(2)

            if success:
                break

        if not success:
            print(f"  [!] Skipping segment {idx} after trying all active models.")
        time.sleep(1.0)

    return all_verified_questions


# ----------------------------------------------------------------------
# 4. MAIN WORKFLOW
# ----------------------------------------------------------------------
def process_and_save_pdf(pdf_path: str, output_filename: str = "english_questions.json", subject: str = "English Vocabulary & Grammar"):
    print(f"[*] Checking path: {pdf_path}")
    if not os.path.exists(pdf_path):
        print(f"[!] ERROR: PDF file not found at absolute path: {os.path.abspath(pdf_path)}")
        return

    final_output_path = os.path.join(DATA_DIR, "english", output_filename)

    print("=" * 60)
    print(f"   ENGLISH EXAM MASTER QUESTION BANK GENERATOR")
    print("=" * 60)
    
    valid_chunks = extract_clean_chunks_from_pdf(pdf_path)
    if not valid_chunks:
        print("[!] No readable text segments detected in document.")
        return

    questions = parse_and_verify_questions(valid_chunks, subject, final_output_path)

    print("\n" + "=" * 60)
    print(f"   [SUCCESS] All {len(questions)} Questions Generated & Saved!")
    print(f"   [SAVED] JSON Path: {final_output_path}")
    print("=" * 60)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("\nUsage: python tools/pdf_question_extractor.py <pdf_filename_or_path> [output_json_filename] [subject]")
        print("Example: python tools/pdf_question_extractor.py \"data/PDF 1.pdf\" \"english_questions.json\" \"English Grammar\"\n")
    else:
        pdf_arg = sys.argv[1]
        
        if os.path.exists(pdf_arg):
            pdf_file_path = pdf_arg
        elif os.path.exists(os.path.join(DATA_DIR, pdf_arg)):
            pdf_file_path = os.path.join(DATA_DIR, pdf_arg)
        elif os.path.exists(os.path.join(BASE_DIR, pdf_arg)):
            pdf_file_path = os.path.join(BASE_DIR, pdf_arg)
        else:
            pdf_file_path = pdf_arg

        out_file = sys.argv[2] if len(sys.argv) > 2 else "english_questions.json"
        subj = sys.argv[3] if len(sys.argv) > 3 else "English Vocabulary & Grammar"
        
        process_and_save_pdf(pdf_file_path, output_filename=out_file, subject=subj)