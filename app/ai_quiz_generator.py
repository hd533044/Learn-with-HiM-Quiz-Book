import json
import logging
import re
import os
import time
import asyncio
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

# Secure environment variable resolution
ACTIVE_API_KEY = (
    os.getenv("GROQ_API_KEY") or 
    os.getenv("XAI_API_KEY") or 
    os.getenv("GROQ_KEY") or 
    ""
).strip()

MODELS_TO_TRY = [
    os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip(),
    "llama-3.1-8b-instant",
    "gemma2-9b-it"
]

PROMPT_TEMPLATE = """
You are a Senior Question Paper Setter and Archivist for Indian Competitive Exams (SSC CGL, CHSL, CPO, MTS, GD, Railway RRB NTPC/Group D, State PSC).
Generate exactly {count} AUTHENTIC, FACTUAL, and REAL exam-standard Multiple Choice Questions (MCQs) strictly based on Previous Year Questions (PYQs) and real facts for:

Subject: {subject}
Topic / Keywords: {topic}

STRICT EXAM QUALITY & FACTUAL RULES:
1. REAL EXAM QUESTIONS ONLY: 
   - Never use placeholder text. 
   - Ask real factual questions testing names, dates, articles, geographical locations, grammar rules, vocabulary, math formulas, or computer facts.
   - Example Good: "Who was the Governor-General of India during the 1857 Revolt?"
   - Example Bad: "What is the primary focus of 1857 revolt?"
2. ABSOLUTELY NO GENERIC PLACEHOLDERS: NEVER use dummy options like 'Core Principle', 'Secondary Application', 'Irrelevant Hypothesis', 'Method A'. All 4 options must be real, plausible exam alternatives.
3. BILINGUAL FORMAT (for GK, Computer, Hindi, Maths & Reasoning):
   - Question format: "Real English Question Text\n\n(हिंदी: शुद्ध और सटीक हिंदी अनुवाद)"
   - Options format: "Real English Option (शुद्ध हिंदी विकल्प)"
   - Keep total question text under 280 characters. Each option under 95 characters.
4. ENGLISH LANGUAGE SECTION:
   - If Subject is English Language, output question, options, and explanation purely in English.
5. EXPLANATION: Provide a factual 1-2 line explanation (max 180 chars) explaining the correct answer fact.
6. JSON ESCAPING: Use single quotes ('like this') instead of raw double quotes inside strings.
7. FORMAT: Return ONLY a valid JSON object containing a "questions" array.

Required JSON Structure:
{{
  "questions": [
    {{
      "id": "AI_01",
      "question": "Question text here...\n\n(हिंदी: सटीक प्रश्न...)",
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
    """Robust multi-level JSON extractor for complex outputs."""
    if not raw_text:
        return []

    clean = re.sub(r'^```json\s*', '', raw_text.strip(), flags=re.IGNORECASE)
    clean = re.sub(r'^```\s*', '', clean)
    clean = re.sub(r'\s*```$', '', clean)

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

    match = re.search(r'\{[\s\S]*\}', clean)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict) and "questions" in data:
                return data["questions"]
        except Exception:
            pass

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


def _call_groq_api_sync(model_name: str, prompt: str) -> str:
    """Performs direct HTTP call to Groq API using native urllib (No httpx needed)."""
    if not ACTIVE_API_KEY:
        logger.error("[GROQ CONFIG ERROR] API Key is missing.")
        return ""

    url = "[https://api.groq.com/openai/v1/chat/completions](https://api.groq.com/openai/v1/chat/completions)"
    headers = {
        "Authorization": f"Bearer {ACTIVE_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "QuizWithHiM/1.0"
    }
    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "system",
                "content": "You are a professional SSC question setter. You generate authentic, real competitive exam PYQ questions in valid JSON. Never output dummy questions."
            },
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"}
    }

    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            res_body = response.read().decode("utf-8")
            res_json = json.loads(res_body)
            return res_json["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"[GROQ HTTP ERROR] Model {model_name} failed: {e}")
        return ""


async def generate_live_exam_quiz(subject: str, topic: str, count: int = 10) -> list:
    """Generates authentic on-demand questions with model failover."""
    count = min(max(count, 5), 20)
    prompt = PROMPT_TEMPLATE.format(subject=subject, topic=topic, count=count)

    raw_text = ""
    for model in MODELS_TO_TRY:
        try:
            logger.info(f"[GROQ ATTEMPT] Requesting {count} PYQ questions for '{topic}' using {model}...")
            raw_text = await asyncio.to_thread(_call_groq_api_sync, model, prompt)
            if raw_text:
                break
        except Exception as e:
            logger.warning(f"[GROQ FAILOVER] {model} failed: {e}. Trying next model...")

    questions_raw = extract_json_safely(raw_text)

    if not questions_raw:
        logger.error(f"[GROQ GENERATION FAILED] Could not fetch valid questions for topic: {topic}")
        # Will correctly return an empty list causing the bot to show the "Try Again" error, 
        # instead of delivering fake/garbage questions.
        return []

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