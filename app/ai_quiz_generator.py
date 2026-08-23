import json
import logging
import re
import os
import time
import httpx
from app.config import GROQ_API_KEY, GROQ_MODEL

logger = logging.getLogger(__name__)

# Secure environment variable resolution (Zero hardcoded secrets)
ACTIVE_API_KEY = (
    os.getenv("GROQ_API_KEY") or 
    os.getenv("XAI_API_KEY") or 
    os.getenv("GROQ_KEY") or 
    (GROQ_API_KEY if GROQ_API_KEY else "")
).strip()

MODELS_TO_TRY = [
    GROQ_MODEL or "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "gemma2-9b-it"
]

PROMPT_TEMPLATE = """
You are a Senior Question Paper Setter and Archivist for Indian Competitive Exams (SSC CGL, CHSL, CPO, MTS, GD, Railway NTPC/Group D, CDS, State PSC).
Generate exactly {count} authentic, specific, and real exam-standard Multiple Choice Questions (MCQs) that strictly follow Previous Year Questions (PYQs) and TCS pattern exam trends for:

Subject: {subject}
Topic / Keywords: {topic}

STRICT EXAM QUALITY & PYQ MANDATES:
1. AUTHENTIC EXAM QUESTIONS: Every question must be a real, factual, and syllabus-centric question (testing real historical dates, places, personalities, constitutional articles, grammar rules, word meanings, mathematical/reasoning concepts, or computer technical facts).
2. NO GENERIC / PLACEHOLDER QUESTIONS: NEVER ask abstract meta-questions (e.g., DO NOT ask 'What is the primary focus of {topic}?' or use dummy options like 'Core Principle', 'Secondary Application', 'Irrelevant Hypothesis', 'Method A', 'Option 1').
3. BILINGUAL SPECIFICATIONS (for GK, Computer, Hindi, Maths & Reasoning):
   - Question format: "Real English Question Text\\n\\n(हिंदी: शुद्ध और सटीक हिंदी अनुवाद)"
   - Options format: 4 authentic options: "English Option (हिंदी विकल्प)"
   - Keep total question text under 280 characters.
   - Keep each option under 95 characters.
4. ENGLISH LANGUAGE SECTION:
   - If Subject is English Language, output question, options, and explanation purely in English (no Hindi).
5. JSON ESCAPING RULE:
   - NEVER use unescaped double quotes inside strings. Use single quotes ('like this') for words in quotes.
6. EXPLANATION:
   - Provide a factual 1-2 line explanation (max 180 characters) stating the exact fact/rule behind the correct answer.
7. Output format: You MUST return ONLY a single valid JSON object containing a "questions" array.

Required JSON Structure:
{{
  "questions": [
    {{
      "id": "AI_01",
      "question": "Question text here...\\n\\n(हिंदी: सटीक प्रश्न...)",
      "options": [
        "Option A (विकल्प A)",
        "Option B (विकल्प B)",
        "Option C (विकल्प C)",
        "Option D (विकल्प D)"
      ],
      "correct_option": 0,
      "explanation": "Concise factual explanation under 180 characters."
    }}
  ]
}}
"""

def extract_json_safely(raw_text: str) -> list:
    """Robust multi-level JSON extractor for complex multi-language LLM outputs."""
    if not raw_text:
        return []

    clean = re.sub(r'^```json\s*', '', raw_text.strip(), flags=re.IGNORECASE)
    clean = re.sub(r'^```\s*', '', clean)
    clean = re.sub(r'\s*```$', '', clean)

    # Attempt 1: Direct JSON load
    try:
        data = json.loads(clean)
        if isinstance(data, dict):
            for k in ["questions", "data", "items", "mcqs"]:
                if k in data and isinstance(data[k], list):
                    return data[k]
            for v in data.values():
                if isinstance(v, list) and len(v) > 0:
                    return v
        elif isinstance(data, list):
            return data
    except Exception:
        pass

    # Attempt 2: Extract outermost JSON object via Regex
    match = re.search(r'\{[\s\S]*\}', clean)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict) and "questions" in data:
                return data["questions"]
        except Exception:
            pass

    # Attempt 3: Regex item-by-item extraction for malformed quotes
    extracted = []
    q_blocks = re.findall(r'\{\s*"id"[^}]*"question"[^}]*"options"[^}]*\}', clean, re.DOTALL)
    for block in q_blocks:
        try:
            sanitized = re.sub(r'(?<!\\)"(?=[^,:{}\[\]]*"(?:,|\s*\}|\s*\]))', r"'", block)
            item = json.loads(sanitized)
            if "question" in item and "options" in item:
                extracted.append(item)
        except Exception:
            continue

    return extracted


async def _fetch_from_groq(model_name: str, prompt: str) -> str:
    if not ACTIVE_API_KEY:
        logger.error("[GROQ CONFIG ERROR] GROQ_API_KEY is empty.")
        return ""

    url = "[https://api.groq.com/openai/v1/chat/completions](https://api.groq.com/openai/v1/chat/completions)"
    headers = {
        "Authorization": f"Bearer {ACTIVE_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "system",
                "content": "You are a professional SSC and Railway exam question paper setter. Always output a valid JSON object containing a 'questions' array with authentic, exam-standard PYQs. Never use raw unescaped double quotes inside text strings."
            },
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"}
    }

    async with httpx.AsyncClient(timeout=25.0) as client:
        res = await client.post(url, headers=headers, json=payload)
        if res.status_code != 200:
            logger.error(f"[GROQ HTTP ERROR {res.status_code}] Model {model_name}: {res.text}")
            return ""
        data = res.json()
        return data["choices"][0]["message"]["content"].strip()


async def generate_live_exam_quiz(subject: str, topic: str, count: int = 10) -> list:
    """Generates on-demand SSC/TCS pattern questions with multi-model failover."""
    count = min(max(count, 5), 20)
    prompt = PROMPT_TEMPLATE.format(subject=subject, topic=topic, count=count)

    raw_text = ""
    for model in MODELS_TO_TRY:
        try:
            logger.info(f"[GROQ ATTEMPT] Requesting {count} PYQ-standard Qs on '{topic}' using {model}...")
            raw_text = await _fetch_from_groq(model, prompt)
            if raw_text:
                break
        except Exception as e:
            logger.warning(f"[GROQ FAILOVER] {model} failed: {e}. Trying next model...")

    questions_raw = extract_json_safely(raw_text)

    cleaned_questions = []
    now_epoch = int(time.time())

    for idx, q in enumerate(questions_raw, start=1):
        if isinstance(q, dict) and len(q.get("options", [])) >= 4:
            q_text = str(q.get("question", "")).strip()[:285]
            opts = [str(opt).strip()[:95] for opt in q["options"][:4]]
            corr_opt = int(q.get("correct_option", 0)) % 4
            expl = str(q.get("explanation", "")).strip()[:185]

            cleaned_questions.append({
                "id": f"ai_gen_{now_epoch}_{idx}",
                "question": q_text,
                "passage": "",
                "options": opts,
                "correct_option": corr_opt,
                "explanation": expl,
                "language": "bilingual" if subject != "English" else "en",
                "chapter": f"Custom AI: {topic[:25]}"
            })

    return cleaned_questions[:count]