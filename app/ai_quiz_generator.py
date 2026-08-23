import json
import logging
import re
import os
import time
import httpx
from app.config import GROQ_API_KEY, GROQ_MODEL

logger = logging.getLogger(__name__)

ALLOWED_SUBJECTS = ["GK", "English", "Computer", "Maths & Reasoning", "Hindi"]

PROMPT_TEMPLATE = """
You are an expert exam setter for SSC (CGL, CHSL, CPO, MTS), Railways RRB, and State exams in India.
Generate exactly {count} high-yield, exam-standard Multiple Choice Questions (MCQs) on the following parameters:

Subject: {subject}
Topic / Keywords: {topic}

Strict Rules:
1. Question Standard: Strictly relevant to modern SSC TCS pattern PYQ trends.
2. Bilingual Format (for GK, Computer, Hindi, Maths & Reasoning):
   - Question text format: "English Question Text\\n\\n(हिंदी: सटीक हिंदी प्रश्न)"
   - Options format: "English Option (हिंदी विकल्प)"
   - Total question text MUST be under 280 characters.
   - Each option MUST be under 95 characters.
3. English Subject Format:
   - If Subject is English Language, keep questions and options in English only (no Hindi translation).
4. Output format: You MUST return a single JSON object containing a "questions" array. Do not include markdown text outside the JSON object.

Required JSON Structure:
{{
  "questions": [
    {{
      "id": "AI_01",
      "question": "Question text here...",
      "options": ["Option A", "Option B", "Option C", "Option D"],
      "correct_option": 0,
      "explanation": "Concise bilingual explanation under 180 characters."
    }}
  ]
}}
"""

async def generate_live_exam_quiz(subject: str, topic: str, count: int = 10) -> list:
    """Generates on-demand SSC/TCS pattern questions via Groq API."""
    if not GROQ_API_KEY:
        logger.error("[GROQ QUIZ] GROQ_API_KEY is not configured.")
        return []

    count = min(max(count, 5), 20)
    prompt = PROMPT_TEMPLATE.format(subject=subject, topic=topic, count=count)

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": GROQ_MODEL or "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": "You are a professional SSC examination question setter. Always output a valid JSON object containing a 'questions' array."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"}
    }

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            res = await client.post(url, headers=headers, json=payload)
            if res.status_code != 200:
                logger.error(f"[GROQ HTTP ERROR] Status {res.status_code}: {res.text}")
                return []

            data = res.json()
            raw_text = data["choices"][0]["message"]["content"].strip()

            raw_text = re.sub(r'^```json\s*', '', raw_text, flags=re.IGNORECASE)
            raw_text = re.sub(r'^```\s*', '', raw_text)
            raw_text = re.sub(r'\s*```$', '', raw_text)

            parsed_data = json.loads(raw_text)

            questions_list = []
            if isinstance(parsed_data, dict):
                for key in ["questions", "data", "items", "mcqs"]:
                    if key in parsed_data and isinstance(parsed_data[key], list):
                        questions_list = parsed_data[key]
                        break
                if not questions_list:
                    for v in parsed_data.values():
                        if isinstance(v, list) and len(v) > 0:
                            questions_list = v
                            break
            elif isinstance(parsed_data, list):
                questions_list = parsed_data

            if not questions_list:
                logger.error(f"[GROQ PARSE ERROR] No valid list extracted from: {parsed_data}")
                return []

            cleaned_questions = []
            now_epoch = int(time.time())

            for idx, q in enumerate(questions_list, start=1):
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

    except Exception as e:
        logger.error(f"[GROQ AI GENERATION EXCEPTION] {e}")
        return []