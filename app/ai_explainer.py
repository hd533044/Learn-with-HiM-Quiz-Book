import os
import logging
from groq import Groq

logger = logging.getLogger(__name__)

# Safely fetch GROQ_API_KEY from environment variables
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()

groq_client = None
if GROQ_API_KEY:
    try:
        groq_client = Groq(api_key=GROQ_API_KEY)
        logger.info("[GROQ AI] Client Initialized Successfully.")
    except Exception as e:
        logger.error(f"[GROQ AI ERROR] Failed to initialize Groq client: {e}")
else:
    logger.warning("[GROQ AI WARNING] GROQ_API_KEY not set in environment variables.")


def get_ai_question_explanation(question_text: str, correct_ans: str, user_ans: str = None) -> str:
    """
    Calls Groq LLM API to generate a clear, highly structured academic breakdown
    of a quiz question formatted for Telegram Markdown.
    """
    if not groq_client:
        return "⚠️ **AI Explainer Unavailable:** GROQ_API_KEY is missing in Render Environment Variables."

    user_context = f"\n- **Student's Selection:** `{user_ans}`" if user_ans else ""

    prompt = (
        f"You are an expert academic tutor for Indian competitive exams (SSC, Railway, Banking).\n"
        f"Provide a clear, concise, and educational explanation for this question:\n\n"
        f"📌 **Question:** {question_text}\n"
        f"✅ **Correct Answer:** {correct_ans}"
        f"{user_context}\n\n"
        f"Structure your response exactly like this:\n"
        f"1. **Core Concept:** (1-2 bullet points explaining why the answer is correct)\n"
        f"2. **Why Other Options Are Incorrect:** (Brief 1-liner explanation)\n"
        f"3. **Key Exam Takeaway:** (1-liner memory trick or tip for revision)\n\n"
        f"Keep the total response under 180 words. Use clear formatting with Markdown bolding."
    )

    try:
        response = groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": "You are a helpful, concise AI exam tutor."},
                {"role": "user", "content": prompt}
            ],
            model="llama-3.3-70b-versatile",
            temperature=0.3,
            max_tokens=350,
        )
        return response.choices[0].message.content
    except Exception as err:
        logger.error(f"[GROQ AI API EXCEPTION] {err}")
        return "⚠️ **AI Explanation Error:** Unable to generate response. Please try again in a few seconds."